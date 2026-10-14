"""Fail-open capture, wrong receipts, or scope-only symbol keys must fail here."""

import asyncio
import gzip
from hashlib import sha256
import importlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4
import pytest
import requests
from openbq_eodhd.models import _fundamentals as F


def implementation():
    try:
        return importlib.import_module("openbq_eodhd.capture")
    except ModuleNotFoundError:
        pytest.fail("required durable capture bridge is missing")


@pytest.fixture
def bridge_server():
    state = {"body": b'{"General":{"Code":"TEST"}}', "retained": {}, "fail": False, "requests": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/vendor"):
                state["requests"] += 1
                body = gzip.compress(state["body"])
                self.send_response(state.get("status", 200))
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if state["fail"] or not state["retained"]:
                self.send_response(503)
                self.end_headers()
                return
            import base64

            item = next(reversed(state["retained"].values()))
            self.reply(
                dict(
                    capture_id=item["capture_id"],
                    body_base64=base64.b64encode(item["body"]).decode(),
                    stale=True,
                    received_at=item["received_at"],
                )
            )

        def do_POST(self):
            if state.get("redirect"):
                self.send_response(302)
                self.send_header("Location", "/vendor")
                self.end_headers()
                return
            body = self.rfile.read(int(self.headers["Content-Length"]))
            meta = json.loads(self.headers["X-TemporalDB-Capture"])
            state["posted"] = meta, body
            if state["fail"]:
                self.send_response(503)
                self.end_headers()
                return
            state["retained"][meta["capture_id"]] = dict(meta, body=body)
            ack = dict(
                capture_id=meta["capture_id"], payload_sha256=sha256(body).hexdigest(), durable=True
            )
            ack.update(state.get("ack_changes", {}))
            self.reply(ack)

        def reply(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, "http://127.0.0.1:" + str(server.server_port)
    server.shutdown()
    server.server_close()
    thread.join()


def configure(monkeypatch, url):
    env = dict(
        EODHD_CAPTURE_REQUIRED="1",
        EODHD_CAPTURE_URL=url,
        EODHD_CAPTURE_TOKEN="fixture-token",
        EODHD_CAPTURE_WORKSPACE_ID=str(uuid4()),
        EODHD_CAPTURE_ENTITLEMENT="paid",
        EODHD_CAPTURE_PRINCIPAL="alice",
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def client(monkeypatch, url):
    class Client:
        def __init__(self):
            self._session = requests.Session()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._session.close()

        def get_fundamentals_data(self, symbol):
            response = self._session.get(url + "/vendor", timeout=2)
            response.raise_for_status()
            return response.json()

    monkeypatch.setattr(F, "get_client", lambda credentials: Client())


def test_required_bridge_refuses_missing_config(monkeypatch):
    C = implementation()
    monkeypatch.setenv("EODHD_CAPTURE_REQUIRED", "1")
    monkeypatch.delenv("EODHD_CAPTURE_URL", raising=False)
    with pytest.raises(C.CaptureUnavailable):
        C.configured_bridge()


def test_managed_capture_retains_decoded_http_content_before_return(bridge_server, monkeypatch):
    implementation()
    state, url = bridge_server
    configure(monkeypatch, url)
    client(monkeypatch, url)
    F._reset_cache_for_tests()
    result = asyncio.run(F.get_bundle("TEST", "US", {"eodhd_api_key": "secret-provider-key"}))
    assert result["General"]["Code"] == "TEST"
    meta, body = state["posted"]
    assert body == state["body"] and meta["representation"] == "requests-decoded-content"
    assert "secret-provider-key" not in json.dumps(meta) and "scope" not in meta
    assert len(state["retained"]) == 1


@pytest.mark.parametrize(
    "change", [{"durable": False}, {"capture_id": str(uuid4())}, {"payload_sha256": "0" * 64}]
)
def test_invalid_receipt_never_returns_fresh(bridge_server, monkeypatch, change):
    C = implementation()
    state, url = bridge_server
    configure(monkeypatch, url)
    client(monkeypatch, url)
    state["ack_changes"] = change
    state["fail"] = False
    # A corrupt acknowledgment followed by unavailable shared fallback fails closed.
    bridge = C.configured_bridge()
    monkeypatch.setattr(bridge, "latest", lambda *args: None)
    monkeypatch.setattr(C, "configured_bridge", lambda: bridge)
    with pytest.raises(C.NoCapturedValue):
        F._read_through_sync("TEST.US", {"eodhd_api_key": "key"})


def test_scoped_key_separates_credentials_workspace_and_entitlement(bridge_server, monkeypatch):
    implementation()
    _, url = bridge_server
    configure(monkeypatch, url)
    a = F._cache_key("TEST.US", {"eodhd_api_key": "key-a"})
    assert a != F._cache_key("TEST.US", {"eodhd_api_key": "key-b"})
    monkeypatch.setenv("EODHD_CAPTURE_ENTITLEMENT", "other")
    assert a != F._cache_key("TEST.US", {"eodhd_api_key": "key-a"})
    monkeypatch.setenv("EODHD_CAPTURE_WORKSPACE_ID", str(uuid4()))
    assert a != F._cache_key("TEST.US", {"eodhd_api_key": "key-a"})


def test_capture_failure_uses_shared_stale_and_does_not_fill_l1(bridge_server, monkeypatch):
    C = implementation()
    state, url = bridge_server
    configure(monkeypatch, url)
    client(monkeypatch, url)
    bridge = C.configured_bridge()
    F._reset_cache_for_tests()
    assert F._read_through_sync("TEST.US", {"eodhd_api_key": "key"})["General"]["Code"] == "TEST"
    # Fail POST only; GET still returns prior acknowledged bytes.
    monkeypatch.setattr(
        bridge, "retain", lambda *args: (_ for _ in ()).throw(C.CaptureUnavailable("unavailable"))
    )
    monkeypatch.setattr(C, "configured_bridge", lambda: bridge)
    state["body"] = b'{"General":{"Code":"UNRETAINED"}}'
    value = asyncio.run(F.get_bundle("TEST", "US", {"eodhd_api_key": "key"}))
    assert value["General"]["Code"] == "TEST" and value.stale
    assert not F._cache


@pytest.mark.parametrize("body,status", [(b"malformed", 200), (b"rate limited", 429)])
def test_transport_failures_are_retained_without_publishing(
    bridge_server, monkeypatch, body, status
):
    C = implementation()
    state, url = bridge_server
    configure(monkeypatch, url)
    client(monkeypatch, url)
    state["body"], state["status"] = body, status
    with pytest.raises(C.NoCapturedValue):
        F._read_through_sync("TEST.US", {"eodhd_api_key": "key"})
    meta, retained = state["posted"]
    assert retained == body and meta["http_status"] == status


def test_required_startup_rejects_unconfigured_bridge(monkeypatch):
    import os
    import subprocess
    import sys

    implementation()
    env = {key: value for key, value in os.environ.items() if not key.startswith("EODHD_CAPTURE_")}
    env["EODHD_CAPTURE_REQUIRED"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", "import openbq_eodhd"], env=env, capture_output=True, text=True
    )
    assert (
        result.returncode != 0
        and "required shared capture bridge is not configured" in result.stderr
    )


def test_capture_credentials_are_never_forwarded_through_redirects(bridge_server, monkeypatch):
    C = implementation()
    state, url = bridge_server
    configure(monkeypatch, url)
    state["redirect"] = True
    with pytest.raises(C.CaptureUnavailable):
        C.configured_bridge().retain({"capture_id": str(uuid4())}, b"{}")
    assert state["requests"] == 0


@pytest.mark.parametrize(
    "body,status",
    [(b'{"General":{"Code":"SDK"}}', 200), (b"malformed", 200), (b"rate limited", 429)],
)
def test_pinned_sdk_response_hook_observes_bytes_before_json_and_typed_errors(
    bridge_server, monkeypatch, body, status
):
    from eodhd import APIClient

    C = implementation()
    state, url = bridge_server
    configure(monkeypatch, url)
    state["body"], state["status"] = body, status
    sdk = APIClient("demo")
    original = sdk._session.get
    monkeypatch.setattr(
        sdk._session, "get", lambda *args, **kwargs: original(url + "/vendor", timeout=2)
    )
    monkeypatch.setattr(F, "get_client", lambda credentials: sdk)
    if status == 200 and body.startswith(b"{"):
        assert (
            F._read_through_sync("TEST.US", {"eodhd_api_key": "demo"})["General"]["Code"] == "SDK"
        )
    else:
        with pytest.raises(C.NoCapturedValue):
            F._read_through_sync("TEST.US", {"eodhd_api_key": "demo"})
    assert (
        state["posted"][1] == body
        and state["posted"][0]["representation"] == "requests-decoded-content"
    )


def test_internal_http_requires_explicit_trusted_network_opt_in(monkeypatch):
    C = implementation()
    configure(monkeypatch, "http://temporaldb-api:8080")
    monkeypatch.delenv("EODHD_CAPTURE_TRUST_INTERNAL_HTTP", raising=False)
    with pytest.raises(C.CaptureUnavailable):
        C.configured_bridge()
    monkeypatch.setenv("EODHD_CAPTURE_TRUST_INTERNAL_HTTP", "1")
    assert C.configured_bridge().url == "http://temporaldb-api:8080"
    monkeypatch.setenv("EODHD_CAPTURE_URL", "http://credential:secret@temporaldb-api:8080")
    with pytest.raises(C.CaptureUnavailable):
        C.configured_bridge()


def test_304_transport_carries_explicit_revalidated_capture(bridge_server, monkeypatch):
    from datetime import datetime, timezone

    C = implementation()
    state, url = bridge_server
    configure(monkeypatch, url)
    bridge = C.configured_bridge()
    previous = str(uuid4())
    monkeypatch.setattr(
        bridge,
        "latest",
        lambda *args: C.CapturedBundle(
            {"value": "A"}, capture_id=state["posted"][0]["capture_id"], stale=True
        ),
    )
    result = bridge.readthrough(
        "fundamentals",
        {"symbol": "TEST.US"},
        lambda: (b"", 304, datetime.now(timezone.utc)),
        predecessor_capture_id=previous,
    )
    assert state["posted"][0]["predecessor_capture_id"] == previous
    assert not result.stale and result["value"] == "A"


def test_ambiguous_304_only_uses_stale_and_is_not_posted(bridge_server, monkeypatch):
    from datetime import datetime, timezone

    C = implementation()
    state, url = bridge_server
    configure(monkeypatch, url)
    bridge = C.configured_bridge()
    previous = C.CapturedBundle({"value": "A"}, capture_id=str(uuid4()), stale=True)
    monkeypatch.setattr(bridge, "latest", lambda *args: previous)
    result = bridge.readthrough(
        "fundamentals", {"symbol": "TEST.US"}, lambda: (b"", 304, datetime.now(timezone.utc))
    )
    assert result.stale and result.capture_id == previous.capture_id
    assert "posted" not in state
