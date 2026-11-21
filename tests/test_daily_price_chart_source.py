# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""Daily price receipts describe the original per-listing SDK acquisitions."""

from datetime import datetime, timezone
from json import dumps
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.provider.abstract.annotated_result import AnnotatedResult
from openbb_core.provider.utils.errors import EmptyDataError, UnauthorizedError
from pydantic import ValidationError

from openbq_eodhd import eodhd_provider
from openbq_eodhd.models._daily_price_chart_source import DailyPriceReceipt
from openbq_eodhd.models.equity_historical import EODHDEquityHistoricalFetcher
from tests.conftest import run_async

PATCH_TARGET = "openbq_eodhd.models._bars.get_client"
START, END = "2026-10-12", "2026-11-19"
TUESDAY, WEDNESDAY, THURSDAY = "2026-10-13", "2026-10-14", "2026-10-15"
UNSAFE_KEYS = {
    "undatable_loss", "conflicting_duplicates", "identity_conflict",
    "partial_acquisition", "invalid_response", "missing_adjusted_basis",
}


def bar(day, adjusted_close=102, **extra):
    return {
        "date": day, "adjusted_close": adjusted_close, "close": 110.0,
        "open": 100.0, "high": 112.0, "low": 99.0, "volume": 1000,
        **extra,
    }


@pytest.fixture
def fetch(mock_eodhd_client, eodhd_credentials):
    def request(response, *, fetcher=EODHDEquityHistoricalFetcher, **overrides):
        params = dict(symbol="AAPL.US", start_date=START, end_date=END, chart_source=True)
        params.update(overrides)
        sdk = mock_eodhd_client(response)
        with patch(PATCH_TARGET, return_value=sdk):
            result = run_async(fetcher.fetch_data, params, eodhd_credentials)
        return result, sdk

    return request


def receipt_of(result):
    assert isinstance(result, AnnotatedResult)
    envelope = result.metadata["chart_sources"]
    assert set(envelope) == {"version", "receipts"} and envelope["version"] == 1
    assert len(envelope["receipts"]) == 1
    receipt = envelope["receipts"][0]
    assert receipt["delivered_count"] == len(result.result)
    assert set(receipt["unsafe"]) == UNSAFE_KEYS
    return receipt


def test_daily_price_opt_in_counts_calls_and_raw_gaps(fetch, monkeypatch):
    from openbq_eodhd.models import _bars, equity_historical

    acquired_at = datetime(2026, 11, 20, 13, 14, 15, tzinfo=timezone.utc)
    later = datetime(2026, 11, 20, 19, tzinfo=timezone.utc)

    monkeypatch.setattr(_bars, "datetime", SimpleNamespace(now=acquired_at.astimezone))
    monkeypatch.setattr(equity_historical, "datetime", SimpleNamespace(now=later.astimezone))
    raw = [bar(TUESDAY, 100), bar(WEDNESDAY, 101, close=None), bar(THURSDAY, 102)]

    result, sdk = fetch(raw)

    sdk.get_eod_historical_stock_market_data.assert_called_once_with(
        symbol="AAPL.US", period="d", from_date=START, to_date=END, order="a",
    )
    assert len(sdk.method_calls) == 1
    receipt = receipt_of(result)
    assert receipt == {
        "version": 1, "kind": "daily-price", "interval": "1d",
        "retrieved_at": "2026-11-20T13:14:15Z",
        "raw_count": 3, "normalized_count": 2, "delivered_count": 2,
        "diagnostics": [], "unsafe": dict.fromkeys(UNSAFE_KEYS, False),
        "start_date": START, "end_date": END, "symbols": ["AAPL.US"],
        "date_field": "date", "price_field": "adjusted_close", "symbol_field": None,
        "listings": [{
            "symbol": "AAPL.US", "raw_count": 3, "normalized_count": 2,
            "delivered_count": 2, "duplicate_count": 0,
            "omissions": [["AAPL.US", WEDNESDAY]],
        }],
    }
    assert [row.adjusted_close for row in result.result] == [100, 102]


@pytest.mark.parametrize("adjusted", [None, 0, -1, float("nan"), float("inf"), "bad", True])
def test_dated_invalid_adjusted_price_is_a_gap(fetch, adjusted):
    result, _ = fetch([bar(TUESDAY, 100), bar(WEDNESDAY, adjusted), bar(THURSDAY, 102)])
    receipt = receipt_of(result)
    assert receipt["diagnostics"] == []
    assert receipt["listings"][0]["omissions"] == [["AAPL.US", WEDNESDAY]]
    assert [row.adjusted_close for row in result.result] == [100, 102]


def test_dated_model_validation_loss_is_a_gap(fetch):
    result, _ = fetch([bar(TUESDAY), bar(WEDNESDAY, open=None), bar(THURSDAY)])
    receipt = receipt_of(result)
    assert receipt["diagnostics"] == []
    assert receipt["normalized_count"] == 2
    assert receipt["listings"][0]["omissions"] == [["AAPL.US", WEDNESDAY]]


def test_raw_close_only_refuses_adjusted_basis(fetch):
    raw = bar(TUESDAY)
    del raw["adjusted_close"]
    result, _ = fetch([raw])
    receipt = receipt_of(result)
    assert receipt["diagnostics"] == ["missing_adjusted_basis"]
    assert receipt["unsafe"]["missing_adjusted_basis"]
    assert receipt["listings"][0]["omissions"] == [["AAPL.US", TUESDAY]]
    assert result.result == []


@pytest.mark.parametrize(
    "raw, diagnostic, omissions",
    [
        (bar(None, close=None), "undatable_loss", []),
        (bar("2026-10-14T12:00:00Z"), "undatable_loss", []),
        (bar("2026-10-40"), "undatable_loss", []),
        (bar("2026-11-20"), "invalid_response", []),
        (bar("2026-10-11"), "invalid_response", []),
        (bar(WEDNESDAY, symbol="AAPL.US", ticker="MCD.US"), "identity_conflict", []),
        (bar(WEDNESDAY, code="AAPL", exchange="LSE"), "identity_conflict", []),
        (bar(WEDNESDAY, Code="MCD", Exchange="US"), "identity_conflict", []),
        (bar(WEDNESDAY, code="AAPL", Code="MCD", close=None), "identity_conflict", []),
        (bar(WEDNESDAY, Symbol="AAPL.US", Ticker="MCD.US"), "identity_conflict", []),
        (bar(WEDNESDAY, Exchange="TO"), "identity_conflict", []),
        (bar(WEDNESDAY, _symbol="MCD.US"), "identity_conflict", []),
    ],
)
def test_unsafe_raw_row_facts_survive_normalization(fetch, raw, diagnostic, omissions):
    result, _ = fetch([bar(TUESDAY), raw, bar(THURSDAY)])
    receipt = receipt_of(result)
    assert receipt["diagnostics"] == [diagnostic]
    assert receipt["unsafe"][diagnostic]
    assert receipt["raw_count"] == 3
    assert receipt["listings"][0]["omissions"] == omissions


def test_agreeing_identity_aliases_are_bound_to_sdk_listing(fetch):
    result, _ = fetch([bar(TUESDAY, code="AAPL", Code=" aapl ", Symbol="aapl.us", Exchange="us")])
    assert receipt_of(result)["diagnostics"] == []
    assert len(result.result) == 1


@pytest.mark.parametrize("adjusted", [101, None])
def test_same_date_exact_repetition_counts_separately_from_omission(fetch, adjusted):
    repeated = bar(WEDNESDAY, adjusted)
    result, _ = fetch([bar(TUESDAY), repeated, dict(repeated), bar(THURSDAY)])
    listing = receipt_of(result)["listings"][0]
    assert listing == {
        "symbol": "AAPL.US", "raw_count": 4,
        "normalized_count": 3 if adjusted else 2, "delivered_count": 3 if adjusted else 2,
        "duplicate_count": 1, "omissions": [] if adjusted else [["AAPL.US", WEDNESDAY]],
    }


@pytest.mark.parametrize("close", [110, None])
def test_same_date_conflicting_prices_refuse_even_if_would_be_dropped(fetch, close):
    result, _ = fetch([bar(TUESDAY), bar(WEDNESDAY, 98), bar(WEDNESDAY, 99, close=close)])
    receipt = receipt_of(result)
    assert receipt["diagnostics"] == ["conflicting_duplicates"]
    assert receipt["unsafe"]["conflicting_duplicates"]
    assert receipt["listings"][0]["duplicate_count"] == 0


@pytest.mark.parametrize(
    "response, reason",
    [
        (None, "invalid_response"),
        ("not a list", "invalid_response"),
        ({"errors": "denied"}, "invalid_response"),
        ({"data": []}, "invalid_response"),
        ([False], "invalid_response"),
        ([{"error": "denied"}], "invalid_response"),
        ({"data": [], "partial": True}, "partial_acquisition"),
        ({"data": [], "next_page": "page2"}, "partial_acquisition"),
        ({"data": [], "truncated": True}, "partial_acquisition"),
        ([{"date": WEDNESDAY, "has_more": True}], "partial_acquisition"),
    ],
)
def test_wrong_upstream_shape_refuses_the_request(fetch, response, reason):
    with pytest.raises(UnauthorizedError, match=reason):
        fetch(response)


@pytest.mark.parametrize("later", [{"data": [], "next_page": "page2"}, [bar(TUESDAY, nextCursor="page2")],
                                   [bar(TUESDAY, isPartial=True)]])
def test_failed_listing_never_returns_successful_subset(fetch, later):
    responses = {
        "MCD.US": [bar(TUESDAY)],
        "AAPL.US": later,
    }
    calls = []

    def response(*, symbol, **kwargs):
        calls.append(symbol)
        return responses[symbol]

    with pytest.raises(UnauthorizedError, match="AAPL.US.*partial_acquisition"):
        fetch(response, symbol="MCD.US,AAPL.US")
    assert calls == ["MCD.US", "AAPL.US"]


def test_multi_listing_counts_follow_selection_order(fetch):
    responses = {
        "MCD.US": [bar(TUESDAY), bar(WEDNESDAY, None)],
        "AAPL.US": [bar(TUESDAY), bar(TUESDAY), bar(THURSDAY)],
    }
    result, sdk = fetch(lambda *, symbol, **kw: responses[symbol], symbol="MCD.US,AAPL.US")
    receipt = receipt_of(result)
    assert receipt["symbols"] == ["MCD.US", "AAPL.US"]
    assert receipt["symbol_field"] == "symbol"
    assert receipt["listings"] == [
        {
            "symbol": "MCD.US", "raw_count": 2, "normalized_count": 1,
            "delivered_count": 1, "duplicate_count": 0, "omissions": [["MCD.US", WEDNESDAY]],
        },
        {
            "symbol": "AAPL.US", "raw_count": 3, "normalized_count": 2,
            "delivered_count": 2, "duplicate_count": 1, "omissions": [],
        },
    ]
    assert [receipt[key] for key in ("raw_count", "normalized_count", "delivered_count")] == [5, 3, 3]
    assert [row.symbol for row in result.result] == ["AAPL.US", "AAPL.US", "MCD.US"]
    assert [c.kwargs["symbol"] for c in sdk.get_eod_historical_stock_market_data.call_args_list] == [
        "MCD.US", "AAPL.US",
    ]


def test_multi_listing_conflicting_raw_alias_is_seen_before_symbol_injection(fetch):
    result, _ = fetch(
        lambda *, symbol, **kw: [bar(TUESDAY, _symbol="TSLA.US" if symbol == "MCD.US" else symbol)],
        symbol="MCD.US,AAPL.US",
    )
    assert receipt_of(result)["diagnostics"] == ["identity_conflict"]


def test_empty_array_can_produce_a_counted_insufficient_result(fetch):
    result, sdk = fetch([])
    receipt = receipt_of(result)
    assert result.result == []
    assert receipt["diagnostics"] == []
    assert [receipt[key] for key in ("raw_count", "normalized_count", "delivered_count")] == [0, 0, 0]
    sdk.get_eod_historical_stock_market_data.assert_called_once()


def test_safe_duplicate_needs_a_delivered_or_omitted_original(fetch):
    result, _ = fetch([])
    receipt = receipt_of(result)
    receipt["raw_count"] = 1
    receipt["listings"][0].update(raw_count=1, duplicate_count=1)
    with pytest.raises(ValidationError, match="Contradictory daily price listing counts"):
        DailyPriceReceipt.model_validate_json(dumps(receipt))


def test_empty_listing_is_counted_alongside_the_other_requested_listing(fetch):
    result, _ = fetch(
        lambda *, symbol, **kw: [] if symbol == "MCD.US" else [bar(TUESDAY)],
        symbol="MCD.US,AAPL.US",
    )
    receipt = receipt_of(result)
    assert receipt["symbols"] == ["MCD.US", "AAPL.US"]
    assert [listing["raw_count"] for listing in receipt["listings"]] == [0, 1]
    assert receipt["diagnostics"] == []


@pytest.mark.parametrize("missing", ["start_date", "end_date"])
def test_explicit_dates_required_before_legacy_defaults(missing, eodhd_credentials):
    params = {"symbol": "AAPL.US", "chart_source": "true", "start_date": START, "end_date": END}
    del params[missing]
    with patch(PATCH_TARGET) as get_client:
        with pytest.raises((ValidationError, OpenBBError), match="explicit.*start_date.*end_date"):
            run_async(EODHDEquityHistoricalFetcher.fetch_data, params, eodhd_credentials)
    get_client.assert_not_called()


def test_reversed_opt_in_bounds_refuse_before_acquisition(fetch):
    with pytest.raises((ValidationError, OpenBBError), match="start_date.*end_date"):
        fetch([bar(TUESDAY)], start_date=END, end_date=START)


@pytest.mark.parametrize(
    "overrides",
    [
        {"interval": "1W"},
        {"interval": "1m"},
        {"symbol": "VOD.LSE"},
        {"symbol": "^GSPC.INDX"},
        {"symbol": "AAPL.US,AAPL"},
        {"symbol": ",".join(f"LIST{i}.US" for i in range(13))},
    ],
)
def test_unsupported_equity_scope_has_ordinary_rows(fetch, overrides):
    result, _ = fetch([bar(TUESDAY, timestamp=1791900000)], **overrides)
    assert isinstance(result, list) and result


@pytest.mark.parametrize("key", ("EquityHistorical", "EtfHistorical", "EodhdEquityHistorical", "EodhdEtfHistorical"))
def test_opt_in_supported_by_registered_equity_and_etf_fetcher(fetch, key):
    fetcher = eodhd_provider.fetcher_dict[key]
    assert fetcher is EODHDEquityHistoricalFetcher
    result, sdk = fetch([bar(TUESDAY)], fetcher=fetcher, symbol="SPY")
    assert receipt_of(result)["symbols"] == ["SPY.US"]
    assert sdk.get_eod_historical_stock_market_data.call_args.kwargs["symbol"] == "SPY.US"


@pytest.mark.parametrize("key, listing", [("CryptoHistorical", "BTC-USD.CC"), ("CurrencyHistorical", "EURUSD.FOREX")])
def test_non_equity_fetchers_ignore_chart_source(fetch, key, listing):
    result, _ = fetch([bar(TUESDAY)], fetcher=eodhd_provider.fetcher_dict[key], symbol=listing)
    assert isinstance(result, list) and len(result) == 1


def test_plain_route_retains_rows_without_receipt_or_duplicate_filter(fetch):
    raw = bar(TUESDAY)
    del raw["adjusted_close"]
    result, sdk = fetch([raw, dict(raw)], chart_source=False)
    assert isinstance(result, list) and len(result) == 2
    assert [row.adjusted_close for row in result] == [None, None]
    sdk.get_eod_historical_stock_market_data.assert_called_once()


def test_plain_route_retains_empty_data_error(fetch):
    with pytest.raises(EmptyDataError):
        fetch([], chart_source=False)


@pytest.mark.parametrize(
    "declaration",
    [{"interval": "1W"}, {"period": "w"}, {"unit": "cents"}, {"currency": "EUR"}],
)
@pytest.mark.parametrize("dropped", [False, True])
def test_contradictory_raw_declarations_deny_daily_proof(fetch, declaration, dropped):
    dates = [TUESDAY, WEDNESDAY, THURSDAY, "2026-10-22", "2026-10-29", "2026-11-05"]
    raw = [bar(day, 100, interval="1d", period="d", currency="USD", unit="dollars") for day in dates]
    raw[-1].update(declaration)
    if dropped:
        raw[-1]["close"] = None
    result, _ = fetch(raw)
    receipt = receipt_of(result)
    assert receipt["delivered_count"] == (5 if dropped else 6)
    assert receipt["unsafe"]["invalid_response"]
    assert receipt["diagnostics"] == ["invalid_response"]


@pytest.mark.parametrize("conflict", [False, True])
def test_sparse_denominations_are_reconciled_within_each_requested_listing(fetch, conflict):
    def observations(*, symbol, **kwargs):
        currency, unit = ("USD", "dollars") if symbol == "MCD.US" else ("GBP", "pounds")
        return [
            bar(TUESDAY, interval="1d", currency=currency, unit=unit),
            bar("2026-10-27"),
            bar("2026-11-10", period="d", currency=currency,
                unit="pence" if conflict and symbol == "AAPL.US" else unit),
        ]

    result, sdk = fetch(observations, symbol="MCD.US,AAPL.US")
    assert receipt_of(result)["diagnostics"] == (["invalid_response"] if conflict else [])
    assert [c.kwargs["symbol"] for c in sdk.get_eod_historical_stock_market_data.call_args_list] == [
        "MCD.US", "AAPL.US",
    ]
