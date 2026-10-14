# SPDX-License-Identifier: Apache-2.0
"""Required shared capture bridge in managed mode; optional no-op standalone seam.

requests.Response.content has HTTP content decoding applied. It preserves bytes
before JSON parsing but is deliberately never described as literal wire bytes.
"""

from __future__ import annotations

import base64
from hashlib import sha256
import json
import os
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from uuid import UUID, uuid4


class CaptureUnavailable(RuntimeError):
    code = "CAPTURE_UNAVAILABLE"


class NoCapturedValue(CaptureUnavailable):
    code = "NO_CAPTURED_VALUE"


class CapturedBundle(dict):
    """Ordinary provider bundle with provenance available to managed callers."""

    def __init__(self, value, *, capture_id, stale):
        super().__init__(value)
        self.capture_id, self.stale = capture_id, stale


_hook = None


def set_capture_hook(fn):
    global _hook
    _hook = fn


def capture(**event):
    """Standalone host seam. Local spool output is never a durable receipt."""
    if _hook is not None:
        return _hook(**event)
    return None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise CaptureUnavailable("capture redirects are refused")


class HttpCaptureBridge:
    def __init__(
        self, url, token, workspace_id, entitlement, principal, *, trusted_internal_http=False
    ):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (
                parsed.scheme == "http"
                and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                and not trusted_internal_http
            )
        ):
            raise CaptureUnavailable("capture URL must be trusted HTTPS or loopback HTTP")
        if not token or not entitlement or not principal:
            raise CaptureUnavailable("explicit capture credentials and scope policy are required")
        try:
            workspace_id = str(UUID(workspace_id))
        except (ValueError, TypeError, AttributeError):
            raise CaptureUnavailable("explicit capture workspace policy is required") from None
        self.url, self._token = url.rstrip("/"), token
        self.scope = (workspace_id, entitlement, principal)
        self.entitlement = entitlement

    def cache_scope(self):
        # Scope and credential-policy identity; no credential leaves the process.
        return self.scope + (self.url, sha256(self._token.encode()).hexdigest())

    def _call(self, method, *, metadata=None, body=None, query=""):
        headers = {
            "Authorization": "Bearer " + self._token,
            "X-TemporalDB-Entitlement": self.entitlement,
        }
        if metadata is not None:
            headers.update(
                {
                    "X-TemporalDB-Capture": json.dumps(metadata, separators=(",", ":")),
                    "Content-Type": "application/octet-stream",
                }
            )
        request = Request(
            self.url + "/temporal/v1/captures" + query, data=body, headers=headers, method=method
        )
        try:
            with build_opener(_NoRedirect()).open(request, timeout=10) as response:
                return json.loads(response.read(24 * 1024 * 1024))
        except Exception:
            # Transport exceptions may include URLs/headers. Never log or chain them.
            raise CaptureUnavailable("shared capture transport unavailable") from None

    @staticmethod
    def request_key(endpoint, request):
        return sha256(
            json.dumps(["eodhd", endpoint, request], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def retain(self, metadata, body):
        digest = sha256(body).hexdigest()
        for attempt in range(2):
            try:
                ack = self._call("POST", metadata=metadata, body=body)
                if (
                    ack.get("durable") is not True
                    or ack.get("capture_id") != metadata["capture_id"]
                    or ack.get("payload_sha256") != digest
                ):
                    raise CaptureUnavailable("invalid durable capture receipt")
                return ack
            except CaptureUnavailable:
                if attempt:
                    raise

    def latest(self, endpoint, request):
        try:
            result = self._call(
                "GET", query="?" + urlencode({"request_key": self.request_key(endpoint, request)})
            )
            value = json.loads(base64.b64decode(result["body_base64"], validate=True))
            if not isinstance(value, dict) or not value:
                return None
            return CapturedBundle(value, capture_id=str(UUID(result["capture_id"])), stale=True)
        except Exception:
            return None

    def readthrough(self, endpoint, request, fetch):
        try:
            body, status, received_at = fetch()
            metadata = dict(
                capture_id=str(uuid4()),
                provider="eodhd",
                endpoint=endpoint,
                request=request,
                http_status=status,
                received_at=received_at.isoformat(),
                representation="requests-decoded-content",
            )
            ack = self.retain(metadata, body)
            if 200 <= status < 300:
                value = json.loads(body)
                if isinstance(value, dict) and value:
                    return CapturedBundle(value, capture_id=ack["capture_id"], stale=False)
            elif status == 304:
                response = self.latest(endpoint, request)
                if response is not None and response.capture_id == ack["capture_id"]:
                    response.stale = False
                    return response
        except Exception:
            pass
        return self.latest(endpoint, request) or self._missing()

    @staticmethod
    def _missing():
        raise NoCapturedValue("NO_CAPTURED_VALUE: no acknowledged fundamentals available") from None


def configured_bridge():
    if os.getenv("EODHD_CAPTURE_REQUIRED", "0") != "1":
        return None
    try:
        return HttpCaptureBridge(
            *(
                os.environ[name]
                for name in (
                    "EODHD_CAPTURE_URL",
                    "EODHD_CAPTURE_TOKEN",
                    "EODHD_CAPTURE_WORKSPACE_ID",
                    "EODHD_CAPTURE_ENTITLEMENT",
                    "EODHD_CAPTURE_PRINCIPAL",
                )
            ),
            trusted_internal_http=os.getenv("EODHD_CAPTURE_TRUST_INTERNAL_HTTP") == "1",
        )
    except KeyError:
        raise CaptureUnavailable("required shared capture bridge is not configured") from None
