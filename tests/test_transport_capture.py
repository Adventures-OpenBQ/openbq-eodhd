"""Only owned EODHD sessions may expose acknowledged provider responses."""

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import json
from uuid import uuid4

import pytest
import requests
from requests.adapters import BaseAdapter


def implementation():
    try:
        return importlib.import_module("openbq_eodhd.transport")
    except ModuleNotFoundError:
        pytest.fail("audited EODHD transport is missing")


class LocalAdapter(BaseAdapter):
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def send(self, request, **kwargs):
        self.calls.append(request)
        status, body = next(self.responses)
        response = requests.Response()
        response.request, response.url = request, request.url
        response.status_code, response._content = status, body
        return response

    def close(self):
        pass


def bridge():
    from openbq_eodhd.capture import HttpCaptureBridge

    value = HttpCaptureBridge("http://localhost", "fixture-token", str(uuid4()), "paid", "alice")
    value.events = []

    def call(method, *, metadata=None, body=None, query=""):
        if query == '?acquire=1':
            return {'state':'owner','acquisition_id':str(uuid4())}
        if method == "GET":
            raise OSError("fixture has no fallback")
        value.events.append((metadata, body))
        return {
            "capture_id": metadata["capture_id"],
            "payload_sha256": sha256(body).hexdigest(),
            "durable": True,
        }

    value._call = call
    return value


def test_owned_session_captures_each_nested_response_and_retry_without_secrets():
    module = implementation()
    session, shared = requests.Session(), bridge()
    adapter = LocalAdapter(
        [
            (429, b'{"error":"limited"}'),
            (200, b'[{"date":"2026-01-02","close":12}]'),
            (200, b'{"General":{"Code":"TEST"}}'),
        ]
    )
    session.mount("https://", adapter)
    module.attach_session(session, shared)
    bad = session.get("https://eodhd.com/api/eod/TEST.US?api_token=credential&fmt=json&period=d")
    assert bad.status_code == 429
    good = session.get("https://eodhd.com/api/eod/TEST.US?api_token=credential&fmt=json&period=d")
    nested = session.get("https://eodhd.com/api/fundamentals/TEST.US?api_token=credential&fmt=json")
    assert good.json()[0]["close"] == 12 and nested.json()["General"]["Code"] == "TEST"
    assert len(shared.events) == 3
    assert [item[0]["http_status"] for item in shared.events] == [429, 200, 200]
    assert all(item[0]["representation"] == "requests-decoded-content" for item in shared.events)
    assert "credential" not in json.dumps([meta for meta, _ in shared.events])
    assert shared.events[1][0]["request"] == {"symbol": "TEST.US", "period": "d"}
    unrelated = requests.Session()
    assert unrelated.send.__func__ is requests.Session.send


def test_failed_capture_and_unreviewed_request_cannot_expose_new_bytes():
    module = implementation()
    shared = bridge()
    session = requests.Session()
    adapter = LocalAdapter([(200, b'[{"close":12}]')])
    session.mount("https://", adapter)
    module.attach_session(session, shared)
    shared._call = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("storage offline"))
    from openbq_eodhd.capture import CaptureUnavailable

    with pytest.raises(CaptureUnavailable):
        session.get("https://eodhd.com/api/eod/TEST.US?api_token=credential")
    with pytest.raises(module.CapabilityUnavailable):
        session.get("https://eodhd.com/api/unknown?api_token=credential")
    with pytest.raises(module.CapabilityUnavailable):
        session.get("https://eodhd.com/api/eod/TEST.US?opaque=credential")
    assert len(adapter.calls) == 0


def test_endpoint_manifest_covers_provider_models_and_router_mirrors():
    module = implementation()
    from openbq_eodhd import eodhd_provider
    from openbq_eodhd.eodhd_router import MIRRORS

    assert set(eodhd_provider.fetcher_dict) == set(module.ENDPOINT_MANIFEST)
    assert all("Eodhd" + name in module.ENDPOINT_MANIFEST for name in MIRRORS)
    for name, entry in module.ENDPOINT_MANIFEST.items():
        assert entry["mode"] in {"normalized", "unsupported"}
        if entry["mode"] == "normalized":
            assert entry["normalizer"] and entry["endpoints"]
        else:
            assert entry["reason"]


def test_openbb_projection_includes_stale_capture_receipt(monkeypatch):
    module = implementation()
    from openbq_eodhd import eodhd_provider
    from openbq_eodhd.capture import configured_bridge

    assert configured_bridge() is None
    monkeypatch.setattr(module, "configured_bridge", lambda: bridge())
    receipt = {
        "capture_id": str(uuid4()),
        "stale": True,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "representation": "requests-decoded-content",
    }

    async def bars(*args):
        module.record_receipt(receipt)
        return [
            {"date": "2026-01-02", "open": 10, "high": 13, "low": 9, "close": 12, "volume": 100}
        ]

    # Patch at the provider helper boundary: keep query/model/OpenBB projection real.
    import openbq_eodhd.models.equity_historical as prices

    monkeypatch.setattr(prices, "fetch_bars", bars)
    result = asyncio.run(
        eodhd_provider.fetcher_dict["EquityHistorical"].fetch_data(
            {"symbol": "TEST", "start_date": "2026-01-01", "end_date": "2026-01-03"},
            {"eodhd_api_key": "fixture"},
        )
    )
    assert result.result[0].close == 12
    assert result.metadata["temporaldb"]["stale"] is True
    assert result.metadata["temporaldb"]["captures"] == [receipt]
    unsupported = eodhd_provider.fetcher_dict["EquitySearch"]
    with pytest.raises(module.CapabilityUnavailable):
        asyncio.run(unsupported.fetch_data({"query": "TEST"}, {"eodhd_api_key": "fixture"}))


def test_eodhd_sdk_and_obbject_projection_keep_retained_body_and_receipt(monkeypatch):
    module = implementation()
    from openbq_eodhd import eodhd_provider
    from openbq_eodhd.models import _client
    from openbb_core.app.model.obbject import OBBject
    from eodhd import APIClient

    shared = bridge()
    monkeypatch.setattr(module, "configured_bridge", lambda: shared)
    import openbq_eodhd.capture as capture

    monkeypatch.setattr(capture, "configured_bridge", lambda: shared)
    client = APIClient("demo")
    adapter = LocalAdapter(
        [(200, b'[{"date":"2026-01-02","open":10,"high":13,"low":9,"close":12,"volume":100}]')]
    )
    client._session.mount("https://", adapter)
    monkeypatch.setattr(_client, "apply_openbb_http_settings", lambda c: None)
    import eodhd

    monkeypatch.setattr(eodhd, "APIClient", lambda key: client)

    class Query:
        async def execute(self):
            return await eodhd_provider.fetcher_dict["EquityHistorical"].fetch_data(
                {"symbol": "TEST", "start_date": "2026-01-01", "end_date": "2026-01-03"},
                {"eodhd_api_key": "demo"},
            )

    result = asyncio.run(OBBject.from_query(Query()))
    assert result.results[0].close == 12
    receipt = result.extra["results_metadata"]["temporaldb"]["captures"][0]
    assert receipt["capture_id"] == shared.events[0][0]["capture_id"]
    assert not receipt["stale"]
    assert len(shared.events) == 1


def test_hidden_adapter_retries_are_refused_before_http():
    module = implementation()
    from requests.adapters import HTTPAdapter

    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=2))
    with pytest.raises(module.CapabilityUnavailable):
        module.attach_session(session, bridge())


def test_managed_fundamentals_does_not_recertify_l1_freshness(monkeypatch):
    implementation()
    from openbq_eodhd.models import _fundamentals as fundamentals
    from openbq_eodhd import capture

    shared = bridge()
    monkeypatch.setattr(capture, "configured_bridge", lambda: shared)
    old = capture.CapturedBundle({"General": {"Code": "OLD"}}, capture_id=str(uuid4()), stale=False)
    key = fundamentals._cache_key("TEST.US", {"eodhd_api_key": "fixture"})
    import time

    monkeypatch.setitem(fundamentals._cache, key, (time.monotonic() + 100, old))

    def read(sym, credentials):
        return capture.CapturedBundle(
            {"General": {"Code": "ACKNOWLEDGED"}}, capture_id=str(uuid4()), stale=True
        )

    monkeypatch.setattr(fundamentals, "_read_through_sync", read)
    result = asyncio.run(fundamentals.get_bundle("TEST", "US", {"eodhd_api_key": "fixture"}))
    assert result["General"]["Code"] == "ACKNOWLEDGED" and result.stale


def test_pinned_sdk_nested_calls_and_explicit_retry_retain_individual_evidence():
    module = implementation()
    from eodhd import APIClient
    from eodhd.errors import EODHDHTTPError

    shared = bridge()
    client = APIClient("demo")
    adapter = LocalAdapter(
        [
            (429, b'{"message":"limited"}'),
            (200, b'[{"date":"2026-01-02","close":12}]'),
            (200, b'{"General":{"Code":"TEST"}}'),
        ]
    )
    client._session.mount("https://", adapter)
    module.attach_session(client._session, shared)
    with client:
        with pytest.raises(EODHDHTTPError):
            client.get_eod_historical_stock_market_data("TEST.US", period="d")
        bars = client.get_eod_historical_stock_market_data("TEST.US", period="d")
        fundamentals = client.get_fundamentals_data("TEST.US")
    assert bars[0]["close"] == 12 and fundamentals["General"]["Code"] == "TEST"
    assert len(shared.events) == 3
    assert [meta["http_status"] for meta, _ in shared.events] == [429, 200, 200]


def test_requested_wire_capability_fails_before_sdk_acquisition(monkeypatch):
    module = implementation()
    with pytest.raises(module.CapabilityUnavailable):
        module.require_capability("http_body")


def test_http_timing_adapter_and_allowlisted_headers():
    module = implementation()
    shared = bridge()
    session = requests.Session()
    adapter = LocalAdapter([(200, b'[{"date":"2020-01-01","close":12}]')])
    original = adapter.send
    def respond(request, **kwargs):
        response = original(request, **kwargs)
        response.headers.update({'Content-Type':'application/json','Set-Cookie':'secret','ETag':'"x"'})
        return response
    adapter.send = respond
    session.mount('https://', adapter)
    module.attach_session(session, shared)
    session.get('https://eodhd.com/api/eod/TEST.US')
    metadata = shared.events[0][0]
    assert datetime.fromisoformat(metadata['request_started_at']) <= datetime.fromisoformat(metadata['received_at'])
    assert metadata['request_duration_ms'] >= 0
    assert metadata['adapter_version'] == 'openbq-eodhd-capture:2'
    assert metadata['response_headers'] == {'content-type':'application/json','etag':'"x"'}
