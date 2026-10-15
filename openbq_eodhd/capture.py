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
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from uuid import UUID, uuid4


class CaptureUnavailable(RuntimeError):
    code = "CAPTURE_UNAVAILABLE"


class NoCapturedValue(CaptureUnavailable):
    code = "NO_CAPTURED_VALUE"


class CapturedBundle(dict):
    """Ordinary provider bundle with provenance available to managed callers."""

    def __init__(self, value, *, capture_id, stale, received_at=None):
        super().__init__(value)
        self.capture_id, self.stale = capture_id, stale
        self.received_at = received_at


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
        self, url, token, workspace_id, entitlement, principal, *, trusted_internal_http=False, account_id=None
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
        self.account_id = account_id

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

    def acquire(self, endpoint, request):
        metadata = dict(provider='eodhd', endpoint=endpoint, request=request, operation_id=str(uuid4()))
        deadline = time.monotonic() + 60
        while True:
            result = self._call('POST', metadata=metadata, body=b'', query='?acquire=1')
            UUID(result['acquisition_id'])
            if self.account_id is not None and result.get('account') != self.account_id:
                raise CaptureUnavailable('server provider account differs from configured host account')
            if result['state'] in {'owner','complete'}:
                return result
            if result['state'] != 'pending' or time.monotonic() >= deadline:
                raise CaptureUnavailable('shared acquisition unavailable')
            metadata['wait_for'] = result['acquisition_id']
            time.sleep(0.05)

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
            if self.account_id is not None and result.get('account') != self.account_id:
                return None
            value = json.loads(base64.b64decode(result["body_base64"], validate=True))
            if not isinstance(value, dict) or not value:
                return None
            return CapturedBundle(value, capture_id=str(UUID(result["capture_id"])), stale=True, received_at=result["received_at"])
        except Exception:
            return None

    def latest_response(self, endpoint, request):
        """Rebuild an SDK response from server-owned stale receipt and retained bytes."""
        try:
            import requests
            result = self._call("GET", query="?" + urlencode({"request_key":self.request_key(endpoint, request)}))
            if self.account_id is not None and result.get('account') != self.account_id:
                return None
            body = base64.b64decode(result["body_base64"], validate=True)
            value = json.loads(body)
            if not isinstance(value, (dict,list)) or not value:
                return None
            receipt = dict(capture_id=str(UUID(result["capture_id"])),stale=True,
                received_at=result["received_at"],representation="requests-decoded-content")
            response = requests.Response()
            response.status_code, response._content = 200, body
            return response, receipt
        except Exception:
            return None

    def readthrough(self, endpoint, request, fetch, *, predecessor_capture_id=None):
        try:
            acquisition = self.acquire(endpoint, request)
            if acquisition['state']=='complete':
                value = json.loads(base64.b64decode(acquisition['body_base64'], validate=True))
                if not 200 <= acquisition['http_status'] < 300:
                    raise CaptureUnavailable('joined acquisition failed')
                return CapturedBundle(value,capture_id=acquisition['capture_id'],stale=False,received_at=acquisition['received_at'])
            started = datetime.now(timezone.utc).isoformat()
            clock = time.monotonic()
            body, status, received_at = fetch()
            if status == 304 and predecessor_capture_id is None:
                raise CaptureUnavailable("304 requires an explicit revalidated capture")
            metadata = dict(
                capture_id=acquisition['acquisition_id'],
                acquisition_id=acquisition['acquisition_id'],
                provider="eodhd",
                endpoint=endpoint,
                request=request,
                http_status=status,
                received_at=received_at.isoformat(),
                representation="requests-decoded-content",
                request_started_at=started,
                request_duration_ms=(time.monotonic() - clock) * 1000,
                adapter_version="openbq-eodhd-capture:2",
                response_headers=None,
            )
            if status == 304:
                metadata["predecessor_capture_id"] = str(UUID(str(predecessor_capture_id)))
            ack = self.retain(metadata, body)
            if 200 <= status < 300:
                value = json.loads(body)
                if isinstance(value, dict) and value:
                    return CapturedBundle(value, capture_id=ack["capture_id"], stale=False, received_at=received_at.isoformat())
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
    from .transport import require_capability
    require_capability(os.getenv('EODHD_CAPTURE_CAPABILITY','requests-decoded-content'))
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
            account_id=os.environ["EODHD_CAPTURE_ACCOUNT"],
        )
    except KeyError:
        raise CaptureUnavailable("required shared capture bridge is not configured") from None


def managed_credentials(credentials):
    """Required capture hosts use only the operator-bound provider account key."""
    if os.getenv('EODHD_CAPTURE_REQUIRED', '0') != '1':
        return credentials
    import hmac
    configured = os.getenv('EODHD_API_KEY')
    if not configured or not os.getenv('EODHD_CAPTURE_ACCOUNT'):
        raise CaptureUnavailable('managed provider account credentials are not configured')
    supplied = (credentials or {}).get('eodhd_api_key')
    if supplied is not None and not hmac.compare_digest(str(supplied), configured):
        raise CaptureUnavailable('per-call provider credentials differ from managed account')
    return dict(credentials or {}, eodhd_api_key=configured)
