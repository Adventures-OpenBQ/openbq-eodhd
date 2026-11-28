"""New chart observations must retain the capture branch's evidence boundary."""
import asyncio
from datetime import datetime, timezone, timedelta

import pytest
from openbq_eodhd import capture, transport
from openbq_eodhd.models import _fundamentals as F
from openbq_eodhd.models import _bars as B
from types import SimpleNamespace


def test_earnings_v11_request_has_a_distinct_bounded_audited_identity():
    assert transport.identity("https://eodhd.com/api/v1.1/fundamentals/TEST.US?filter=Earnings&api_token=EXAMPLE&fmt=json") == (
        "earnings-v1.1", {"symbol": "TEST.US", "filter": "Earnings"})
    for path in ("?filter=Financials", "?filter=Earnings&filter=Earnings", "?filter=Earnings&unknown=1"):
        with pytest.raises(transport.CapabilityUnavailable):
            transport.identity("https://eodhd.com/api/v1.1/fundamentals/TEST.US" + path)


@pytest.mark.parametrize("stale,future", [(False, False), (True, False), (False, True)])
def test_acquisition_vintage_comes_from_capture_and_never_recertifies_stale_or_future(monkeypatch, stale, future):
    stamp = datetime.now(timezone.utc) + timedelta(days=1 if future else -1)
    bundle = capture.CapturedBundle({"General": {"Code": "TEST"}}, capture_id="example", stale=stale,
                                    received_at=stamp.isoformat())
    monkeypatch.setattr(capture, "configured_bridge", lambda: object())
    monkeypatch.setattr(F, "_read_through_sync", lambda *args: bundle)
    result = asyncio.run(F.get_bundle_acquisition("TEST", "US", {"eodhd_api_key": "EXAMPLE"}))
    assert result.payload is bundle
    assert result.retrieved_at == (stamp if not stale and not future else None)


def test_managed_earnings_acquisitions_each_cross_the_transport_boundary(monkeypatch):
    calls = []
    monkeypatch.setattr(capture, "configured_bridge", lambda: object())
    monkeypatch.setattr(capture, "managed_credentials", lambda value: value)
    monkeypatch.setattr(F, "_fetch_earnings_v11_sync", lambda *args: (calls.append(args) or {"History": {}}))
    for _ in range(2):
        assert asyncio.run(F.get_earnings_v11("TEST", "US", {"eodhd_api_key": "EXAMPLE"})) == {"History": {}}
    assert len(calls) == 2


@pytest.mark.parametrize("stale", [False, True])
def test_daily_observation_uses_capture_time_and_refuses_stale_recertification(monkeypatch, stale):
    stamp = datetime.now(timezone.utc) - timedelta(days=1)
    class Client:
        _session = SimpleNamespace(_temporaldb_capture=True,
            _temporaldb_last_receipt={"received_at": stamp.isoformat(), "stale": stale})
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_eod_historical_stock_market_data(self, **kwargs): return []
    monkeypatch.setattr(B, "get_client", lambda credentials: Client())
    observed = []
    if stale:
        with pytest.raises(capture.CaptureUnavailable):
            B._fetch_bars_sync("1d", ["TEST.US"], "2026-01-01", "2026-01-02", {},
                               raw_observer=lambda *args: observed.append(args))
        assert observed == []
    else:
        B._fetch_bars_sync("1d", ["TEST.US"], "2026-01-01", "2026-01-02", {},
                           raw_observer=lambda *args: observed.append(args))
        assert observed[0][2] == stamp
