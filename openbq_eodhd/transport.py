# SPDX-License-Identifier: Apache-2.0
"""Capture only owned SDK sessions at the requests decoded-content boundary.

No global requests patch. Unsupported paths and hidden adapter retries are
refused in audited mode; response content is not compressed/wire HTTP bytes.
"""

from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from importlib.resources import files
import json
import base64
import re
import time
from urllib.parse import parse_qs, unquote, urlsplit

from openbb_core.provider.abstract.annotated_result import AnnotatedResult
from .capture import CaptureUnavailable, configured_bridge


class CapabilityUnavailable(CaptureUnavailable):
    code = "CAPABILITY_UNAVAILABLE"


ENDPOINT_MANIFEST = json.loads(files(__package__).joinpath("endpoint_manifest.json").read_text())
_receipts = ContextVar("eodhd_capture_receipts", default=None)
_ALLOWED = {
    "fundamentals": set(),
    "eod": {"from", "to", "period", "order"},
    "intraday": {"from", "to", "interval"},
}


def require_capability(capability):
    if capability not in {"requests-decoded-content", "local_received", "nested_http_responses"}:
        raise CapabilityUnavailable(
            "EODHD decoded HTTP content cannot provide requested evidence capability"
        )


def record_receipt(receipt):
    current = _receipts.get()
    if current is not None:
        current.append(dict(receipt))


def identity(url):
    parsed = urlsplit(url)
    parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "eodhd.com"
        or len(parts) != 3
        or parts[0] != "api"
        or parts[1] not in _ALLOWED
    ):
        raise CapabilityUnavailable("EODHD path has no audited capture boundary")
    endpoint, symbol = parts[1:]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._^-]{0,127}", symbol):
        raise CapabilityUnavailable("EODHD request symbol is unsupported")
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    if not query.keys() <= _ALLOWED[endpoint] | {"api_token", "fmt"} or any(
        len(v) != 1 for v in query.values()
    ):
        raise CapabilityUnavailable("EODHD request parameters are unsupported")
    request = {"symbol": symbol}
    for key in _ALLOWED[endpoint] & query.keys():
        value = query[key][0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise CapabilityUnavailable("EODHD request identity is unsupported")
        request[key] = value
    return endpoint, request


def attach_session(session, bridge):
    if getattr(session, "_temporaldb_capture", False):
        return
    for adapter in session.adapters.values():
        retries = getattr(adapter, "max_retries", None)
        if retries is not None and (retries.total != 0 or retries.redirect):
            raise CapabilityUnavailable(
                "hidden adapter retries cannot provide individual response evidence"
            )
    original = session.send

    @wraps(original)
    def send(prepared, **kwargs):
        endpoint, request = identity(prepared.url)
        if (
            prepared.method != "GET"
            or prepared.body
            or any(key.lower().startswith("if-") for key in prepared.headers)
        ):
            raise CapabilityUnavailable("only audited GET requests are supported")
        try:
            acquisition = bridge.acquire(endpoint, request)
            if acquisition['state']=='complete':
                import requests
                response = requests.Response()
                response.status_code = acquisition['http_status']
                response._content = base64.b64decode(acquisition['body_base64'], validate=True)
                receipt = dict(capture_id=acquisition['capture_id'],stale=False,received_at=acquisition['received_at'],representation='requests-decoded-content')
                record_receipt(receipt)
                session._temporaldb_last_receipt = receipt
                return response
            started = datetime.now(timezone.utc).isoformat()
            clock = time.monotonic()
            # Redirects are retained and rejected by the SDK, never followed with credentials.
            response = original(prepared, **dict(kwargs, allow_redirects=False))
            if response.status_code == 304:
                # This seam did not issue a conditional request with a bound predecessor.
                raise CaptureUnavailable("304 requires explicitly bound predecessor evidence")
            body = response.content
            received_at = datetime.now(timezone.utc).isoformat()
            metadata = dict(
                capture_id=acquisition["acquisition_id"],
                acquisition_id=acquisition["acquisition_id"],
                provider="eodhd",
                endpoint=endpoint,
                request=request,
                http_status=response.status_code,
                received_at=received_at,
                representation="requests-decoded-content",
                request_started_at=started,
                request_duration_ms=(time.monotonic() - clock) * 1000,
                adapter_version="openbq-eodhd-capture:2",
                response_headers={k.lower(): v for k, v in response.headers.items() if k.lower() in {"content-type", "content-length", "content-encoding", "date", "etag", "last-modified", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"}},
            )
            ack = bridge.retain(metadata, body)
            receipt = dict(
                capture_id=ack["capture_id"],
                stale=False,
                received_at=received_at,
                representation="requests-decoded-content",
            )
            if not 200 <= response.status_code < 300:
                previous = bridge.latest_response(endpoint, request)
                if previous is not None:
                    response, receipt = previous
            record_receipt(receipt)
            session._temporaldb_last_receipt = receipt
            return response
        except CapabilityUnavailable:
            raise
        except Exception:
            previous = bridge.latest_response(endpoint, request)
            if previous is None:
                raise CaptureUnavailable(
                    "NO_CAPTURED_VALUE: no acknowledged provider response"
                ) from None
            response, receipt = previous
            record_receipt(receipt)
            session._temporaldb_last_receipt = receipt
            return response

    session.send = send
    session._temporaldb_capture = True


def instrument_provider(provider):
    if set(provider.fetcher_dict) != set(ENDPOINT_MANIFEST):
        raise CapabilityUnavailable("provider endpoint coverage manifest is incomplete")
    for name, fetcher in list(provider.fetcher_dict.items()):
        original = fetcher.fetch_data
        entry = ENDPOINT_MANIFEST[name]

        async def audited(
            cls, params, credentials=None, _original=original, _entry=entry, **kwargs
        ):
            bridge = configured_bridge()
            if bridge is None:
                return await _original(params, credentials, **kwargs)
            if _entry["mode"] == "unsupported":
                raise CapabilityUnavailable(_entry["reason"])
            receipts = []
            token = _receipts.set(receipts)
            try:
                result = await _original(params, credentials, **kwargs)
                if not receipts:
                    raise CaptureUnavailable("provider projection has no capture receipts")
                metadata = (
                    dict(result.metadata or {}) if isinstance(result, AnnotatedResult) else {}
                )
                metadata["temporaldb"] = dict(
                    stale=any(r["stale"] for r in receipts), captures=receipts
                )
                return AnnotatedResult(
                    result=result.result if isinstance(result, AnnotatedResult) else result,
                    metadata=metadata,
                )
            finally:
                _receipts.reset(token)

        # Per-registration subclass keeps aliases distinct and leaves unrelated providers intact.
        registered = type(name + "AuditedFetcher", (fetcher,), {"fetch_data": classmethod(audited)})
        provider.fetcher_dict[name] = registered
