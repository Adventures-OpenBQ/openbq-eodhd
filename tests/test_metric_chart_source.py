# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0-only
"""Prove the explicit snapshot shape and exercise legacy upstream payloads through the actual models."""

import json
from copy import deepcopy

import pytest
from openbb_core.provider.utils.errors import EmptyDataError
from pydantic import ValidationError

from openbq_eodhd.models import metrics, quote, screener

NAMES = ["identity_conflict", "unit_conflict", "period_conflict", "basis_conflict", "duplicate_conflict"]
OBS = dict(observation_id="first-report", vintage="2026-11-02T13:00:00Z", origin="eps_ttm",
           unit={"kind": "money-per-share", "currency": "USD"}, as_of=None, as_of_unknown=True,
           period_start="2025-10-01", period_end="2026-09-30", period_kind="TTM",
           adjustment_basis="diluted-as-reported", reporting_basis="US-GAAP", basis_reference="ordinary-2024")


def snapshot():
    return dict(version=1, kind="metric", origin="acquired-snapshot", issuer="EXAMPLE.US", listing="EXMPL.NYSE",
                share_class="Ordinary-A", retrieved_at="2026-11-20T04:16:46Z", raw_count=2, normalized_count=2,
                delivered_count=2, diagnostics=[], unsafe=dict.fromkeys(NAMES, False),
                metrics={"eps_ttm": {**OBS, "additional_observations": [
                    {**OBS, "observation_id": "revised-report", "vintage": "2026-11-12T13:00:00Z"}]}})


def validate(receipt):
    from openbq_eodhd.models._metric_chart_source import MetricSource

    return MetricSource.model_validate_json(json.dumps(receipt))


def test_each_vintage_keeps_its_own_facts_and_no_measure_values():
    receipt = snapshot()
    assert validate(receipt).model_dump(mode="json") == receipt
    receipt["raw_count"] = 3
    receipt["unsafe"]["basis_conflict"] = True
    receipt["diagnostics"] = ["basis_conflict"]
    assert validate(receipt).model_dump(mode="json") == receipt


@pytest.mark.parametrize("change", [
    {"observation_id": "first-report"}, {"observation_id": "76.544"}, {"basis_reference": "76.544"}, {"period_end": None}, {"period_start": "2026-09-30"},
    {"period_kind": "FY"}, {"as_of_unknown": False}, {"as_of": "2026-12-01", "as_of_unknown": False},
    {"as_of": "2026-02-30", "as_of_unknown": False}, {"origin": "revenue_ttm"}, {"unit": {"kind": "count"}},
    {"unit": {"kind": "money-per-share", "currency": "$"}}, {"unit": {"kind": "multiple", "currency": "USD"}},
    {"adjustment_basis": "as-traded"}, {"reporting_basis": None}, {"basis_reference": 5},
    {"vintage": "2026-11-20T04:16:46.000001Z"}, {"value": 8.5}, {"target": "analyst-consensus"},
])
def test_refuses_contradictory_observation_facts(change):
    receipt = snapshot()
    receipt["metrics"]["eps_ttm"]["additional_observations"][0].update(change)
    with pytest.raises(ValidationError):
        validate(receipt)


@pytest.mark.parametrize("change", [
    {"issuer": "EXAMPLE"}, {"listing": ""}, {"share_class": 5}, {"origin": "eodhd-key-metrics"},
    {"raw_count": 3}, {"normalized_count": 1}, {"delivered_count": 1}, {"diagnostics": ["unit_conflict"]},
    {"metrics": {}}, {"range": [0, 100]}, {"retrieved_at": "2026-11-20T04:16:46-01:00"},
])
def test_refuses_inconsistent_receipt(change):
    with pytest.raises(ValidationError):
        validate({**snapshot(), **change})


def test_representative_fundamentals_and_quote_have_no_metric_source_proof():
    # Excerpts from the public DEMO acquisition documented in docs/chart-contracts/kpi-gauges.md.
    bundle = {"General": {"Code": "AAPL", "CurrencyCode": "USD", "UpdatedAt": "2026-11-19"},
              "Highlights": {"MarketCapitalization": 5121867513856, "DividendYield": .003, "PERatio": 39.2483,
                             "MostRecentQuarter": "2026-09-30", "DilutedEpsTTM": 8.98, "RevenueTTM": 478833999872},
              "Valuation": {"TrailingPE": 39.2483, "ForwardPE": 36.7647}}
    for fetcher in [metrics.EODHDKeyMetricsFetcher, metrics.EODHDFinancialRatiosFetcher]:
        for parameters in [{"symbol": "AAPL"}, {"symbol": "AAPL", "chart_source": True}]:
            query = fetcher.transform_query(parameters)
            rows = fetcher.transform_data(query, deepcopy(bundle))
            assert isinstance(rows, list) and rows[0].dividend_yield == .003 and rows[0].pe_ratio == 39.2483
            with pytest.raises(EmptyDataError):
                fetcher.transform_data(query, {})

    rows = quote.EODHDEquityQuoteFetcher.transform_data(quote.EODHDEquityQuoteFetcher.transform_query({"symbol": "AAPL"}),
        [{"code": "AAPL.US", "timestamp": 1795122000, "close": 349.21, "previousClose": 352.45}])
    assert isinstance(rows, list) and rows[0].last_price == 349.21
    query = screener.EODHDEquityScreenerFetcher.transform_query({"chart_source": True})
    result = screener.EODHDEquityScreenerFetcher.transform_data(query, [])
    assert "chart_evidence" in result.metadata and "chart_sources" not in result.metadata

@pytest.mark.parametrize("route,origin", [("metrics", "eodhd-key-metrics"), ("ratios", "eodhd-financial-ratios")])
def test_completed_request_only_attests_reported_headlines_with_unknown_class_basis_asof(monkeypatch, route, origin):
    from unittest.mock import Mock
    from fastapi.testclient import TestClient
    from openbb_core.api.rest_api import app
    from openbb_core.app.model.credentials import Credentials
    from openbb_core.app.model.user_settings import UserSettings
    from openbb_core.app.service.user_service import UserService
    from openbq_eodhd.models import _fundamentals as F

    bundle = {"General": {"Code": "AAPL", "Exchange": "NASDAQ", "PrimaryTicker": "AAPL.US", "Type": "Common Stock",
                          "CurrencyCode": "USD", "UpdatedAt": "2026-11-19"},
              "Highlights": {"DividendYield": 0, "PERatio": 39.2483, "MostRecentQuarter": "2026-09-30",
                             "MarketCapitalization": 5121867513856, "DilutedEpsTTM": 8.98}}
    upstream = Mock(return_value=bundle)
    monkeypatch.setattr(F, "_arctic_library", lambda: None)
    monkeypatch.setattr(F, "_fetch_sync", upstream)
    monkeypatch.setattr(UserService, "read_from_file", staticmethod(lambda: UserSettings.model_construct(
        credentials=Credentials(eodhd_api_key="demo"))))
    F._reset_cache_for_tests()
    try:
        client = TestClient(app, raise_server_exceptions=False)
        for namespace in ["/eodhd", ""]:
            url = f"/api/v1{namespace}/equity/fundamental/{route}"
            params = {"symbol": "AAPL", "provider": "eodhd"}
            legacy = client.get(url, params=params).json()
            assert legacy["results"][0]["symbol"] == "AAPL" and "metric_vintage" not in legacy["results"][0]
            response = client.get(url, params={**params, "chart_source": True})
            assert response.status_code == 200, response.text
            data = response.json()
            row, = data["results"]
            receipt, = data["extra"]["results_metadata"]["chart_sources"]["receipts"]
            assert receipt["origin"] == origin and receipt["share_class"] is None
            assert receipt["issuer"] == receipt["listing"] == row["symbol"] == "AAPL.US"
            assert receipt["retrieved_at"] == row["metric_vintage"]
            assert receipt["normalized_count"] == receipt["delivered_count"] == 1
            assert set(receipt["metrics"]) == {"dividend_yield", "pe_ratio"}
            for name, kind, amount in [("dividend_yield", "fraction", 0), ("pe_ratio", "multiple", 39.2483)]:
                observation = receipt["metrics"][name]
                assert observation["as_of_unknown"] and observation["as_of"] is None
                assert observation["adjustment_basis"] is None and observation["basis_reference"] is None
                assert observation["period_start"] is None and observation["period_end"] is None
                assert observation["origin"] == name and observation["unit"] == {"kind": kind}
                assert observation["vintage"] == row["metric_vintage"] and row[name] == amount
                assert observation["observation_id"] == f'{name}:{row["metric_vintage"]}'
        upstream.assert_called_once()
    finally:
        F._reset_cache_for_tests()

@pytest.mark.parametrize("currency", ["XXX", "XTS", "QQQ"])
def test_unknown_or_test_denomination_cannot_certify_a_monetary_unit(currency):
    receipt = snapshot()
    receipt["metrics"]["eps_ttm"]["unit"] = {"kind": "money-per-share", "currency": currency}
    with pytest.raises(ValidationError):
        validate(receipt)


@pytest.mark.parametrize("metric,kind,basis,currency", [
    ("price", "money-per-share", "as-traded", "GBX"), ("market_cap", "money", "issuer-all-classes", "CAD"),
    ("dividend_yield", "fraction", "trailing-dividend-per-share", None),
    ("pe_ratio", "multiple", "trailing-diluted-earnings", None),
    ("eps_ttm", "money-per-share", "diluted-as-reported", "USD"), ("revenue_ttm", "money", "as-reported", "JPY"),
])
def test_independent_explicit_snapshot_preset(metric, kind, basis, currency):
    is_report = metric in ("eps_ttm", "revenue_ttm")
    unit = {"kind": kind, **({"currency": currency} if currency else {})}
    obs = {**OBS, "origin": metric, "unit": unit, "adjustment_basis": basis, "additional_observations": [],
           "period_kind": None if metric in ("price", "market_cap") else "TTM",
           "period_start": OBS["period_start"] if is_report else None, "period_end": OBS["period_end"] if is_report else None,
           "reporting_basis": "US-GAAP" if is_report else None}
    receipt = {**snapshot(), "metrics": {metric: obs}, "raw_count": 1, "normalized_count": 1, "delivered_count": 1}
    assert validate(receipt).model_dump(mode="json") == receipt


@pytest.mark.parametrize("delta", [{"PrimaryTicker": None}, {"Exchange": "LSE"}, {"Type": "ETF"}, {"Code": "MSFT"}])
def test_mismatching_acquired_identity_or_missing_original_vintage_keeps_legacy_rows(delta):
    from datetime import datetime, timezone
    from openbq_eodhd.models import _fundamentals as F

    general = {"Code": "AAPL", "Type": "Common Stock", "PrimaryTicker": "AAPL.US", "Exchange": "NASDAQ"}
    query = metrics.EODHDKeyMetricsFetcher.transform_query({"symbol": "AAPL", "chart_source": True})
    for acquired, stamp in [({**general, **delta}, datetime.now(timezone.utc)), (general, None)]:
        data = F.FundamentalsAcquisition({"General": acquired, "Highlights": {"DividendYield": 0.003}}, stamp)
        rows = metrics.EODHDKeyMetricsFetcher.transform_data(query, data)
        assert isinstance(rows, list) and rows[0].symbol == "AAPL" and rows[0].dividend_yield == 0.003


@pytest.mark.parametrize("raw", [False, "0.003", float("inf"), 10 ** 500])
def test_raw_type_errors_survive_the_wide_row_normalization(raw):
    from datetime import datetime, timezone
    from openbq_eodhd.models import _fundamentals as F

    data = F.FundamentalsAcquisition({"General": {"Code": "AAPL", "PrimaryTicker": "AAPL.US", "Type": "Common Stock",
        "Exchange": "NASDAQ"}, "Highlights": {"DividendYield": raw, "PERatio": 0}}, datetime.now(timezone.utc))
    query = metrics.EODHDKeyMetricsFetcher.transform_query({"symbol": "AAPL", "chart_source": True})
    result = metrics.EODHDKeyMetricsFetcher.transform_data(query, data)
    assert result.result[0].dividend_yield is None and result.result[0].pe_ratio == 0
    receipt, = result.metadata["chart_sources"]["receipts"]
    assert receipt["unsafe"]["unit_conflict"] and receipt["diagnostics"] == ["unit_conflict"]


def test_missing_headline_field_does_not_fall_back_to_valuation_or_demote_its_sibling():
    from datetime import datetime, timezone
    from openbq_eodhd.models import _fundamentals as F

    general = {"Code": "AAPL", "PrimaryTicker": "AAPL.US", "Type": "Common Stock", "Exchange": "NASDAQ"}
    query = metrics.EODHDFinancialRatiosFetcher.transform_query({"symbol": "AAPL", "chart_source": True})
    bundle = {"General": general, "Highlights": {"DividendYield": None}, "Valuation": {"TrailingPE": 19.2}}
    stamp = datetime.now(timezone.utc)
    result = metrics.EODHDFinancialRatiosFetcher.transform_data(query, F.FundamentalsAcquisition(bundle, stamp))
    receipt, = result.metadata["chart_sources"]["receipts"]
    assert set(receipt["metrics"]) == {"dividend_yield"} and result.result[0].dividend_yield is None
    assert result.result[0].pe_ratio is None and receipt["diagnostics"] == []
    limited = metrics.EODHDFinancialRatiosFetcher.transform_query({"symbol": "AAPL", "chart_source": True, "limit": 2})
    assert isinstance(metrics.EODHDFinancialRatiosFetcher.transform_data(limited, F.FundamentalsAcquisition(bundle, stamp)), list)
