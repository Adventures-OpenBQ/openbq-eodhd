# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import Mock

import pytest
from openbb_core.provider.abstract.annotated_result import AnnotatedResult
from openbb_core.provider.utils.errors import EmptyDataError
from pydantic import TypeAdapter, ValidationError

from openbq_eodhd.models import _fundamentals as F
from openbq_eodhd.models.etf import EODHDEtfHoldingsFetcher as Holdings

STAMP = datetime(2026, 11, 20, 4, 16, 45, tzinfo=timezone.utc)
GENERAL = {"Code": "VTI", "Type": "ETF", "CountryISO": "US", "Exchange": "NYSE ARCA", "UpdatedAt": "2026-11-19"}
APPLE = {"Code": "AAPL", "Exchange": "US", "Name": "Apple Inc.", "Sector": "Technology", "Assets_%": 6.9985}
BLANK = {"Code": "AAYA", "Exchange": "F", "Sector": None, "Assets_%": 0}
BUNDLE = {"General": GENERAL, "ETF_Data": {"Holdings_Count": 2, "Holdings": {"AAPL.US": APPLE, "AAYA.F": BLANK},
                                           "Top_10_Holdings": {"AAPL.US": APPLE},
                                           "Sector_Weights": {"Technology": {"Equity_%": 36.527}}}}


def acquired(bundle=BUNDLE, stamp=STAMP, symbol="VTI"):
    q = Holdings.transform_query({"symbol": symbol, "chart_source": True})
    return Holdings.transform_data(q, F.FundamentalsAcquisition(bundle, stamp))


def receipt(result):
    assert isinstance(result, AnnotatedResult)
    source, = result.metadata["chart_sources"]["receipts"]
    return source


def test_binds_original_vintage_and_fraction_to_delivered_holdings_only():
    result = acquired()
    s = receipt(result)
    assert s["fund"] == "VTI.US" and s["snapshot"] == s["retrieved_at"] == "2026-11-20T04:16:45Z"
    assert s["as_of"] is None and s["weight_basis"] == "fund-assets-fraction"
    assert s["origin"] == "eodhd-etf-holdings" and s["coverage"] == "unknown"
    assert s["missing_mass"] == {"status": "unknown"} and s["rounding_bound_fraction"] is None
    assert s["diagnostics"] == [] and s["holding_keys"] == ["AAPL.US", "AAYA.F"]
    assert s["raw_count"] == s["normalized_count"] == s["delivered_count"] == 2
    assert [(r.symbol, r.weight, r.sector) for r in result.result] == [
        ("AAPL.US", pytest.approx(.069985), "Technology"), ("AAYA.F", 0, None)]
    assert all(r.fund_symbol == s["fund"] and r.snapshot == s["snapshot"]
               and r.weight_basis == "fund-assets" and r.weight_unit == "fraction" for r in result.result)
    assert "0.069985" not in json.dumps(s)


def test_source_counts_negative_conflicting_duplicate_classification_and_raw_excess_before_drops():
    b = deepcopy(BUNDLE)
    b["ETF_Data"]["Holdings"].update({
        "aapl.us": {**APPLE, "Assets_%": -1, "Sector": "Finance"},
        "BAD.US": {**APPLE, "Assets_%": 95, "Sector": 5}, "NULL.US": None,
    })
    s = receipt(acquired(b))
    assert s["raw_count"] == 5 and s["normalized_count"] == s["delivered_count"] == 2
    assert set(s["diagnostics"]) == {"fund_conflict", "negative_weight", "duplicate_conflict",
                                      "classification_conflict", "basis_conflict", "excess_weight"}
    assert all(s["unsafe"].values())
    assert "0.95" not in json.dumps(s)


def test_exact_duplicates_drop_once_and_case_normalization_retains_real_old_code():
    b = deepcopy(BUNDLE)
    b["ETF_Data"]["Holdings"].update({"aapl.us": APPLE, "ADT_old.US": {**APPLE, "Code": "ADT_old"}})
    s = receipt(acquired(b))
    assert s["diagnostics"] == ["exact_duplicate"] and not any(s["unsafe"].values())
    assert s["raw_count"] == 4 and s["delivered_count"] == 3 and "ADT_OLD.US" in s["holding_keys"]


@pytest.mark.parametrize("field,value", [("Code","SPY"), ("CountryISO","CA"), ("Type","Common Stock"),
                                          ("Exchange","LSE"), ("PrimaryTicker","VTI.LSE")])
def test_no_evidence_for_unbound_fund_or_unstamped_cache(field, value):
    b = {**BUNDLE, "General": {**GENERAL, field: value}}
    assert isinstance(acquired(b), list)
    assert isinstance(acquired(stamp=None), list)
    assert isinstance(acquired(symbol="VTI.LSE"), list)


def test_legacy_plain_rows_errors_and_no_proof_from_a_flag():
    for opt in [False, True]:
        q = Holdings.transform_query({"symbol": "VTI", "chart_source": opt})
        rows = Holdings.transform_data(q, BUNDLE)
        assert rows[0].weight == pytest.approx(.069985) and "snapshot" not in rows[0].model_dump()
        with pytest.raises(EmptyDataError):
            Holdings.transform_data(q, {"ETF_Data": {"Sector_Weights": {"Tech": {"Equity_%": 36}}}})


def test_both_completed_http_paths_one_acquisition_no_entitlement_change(monkeypatch):
    from fastapi.testclient import TestClient
    from openbb_core.api.rest_api import app
    from openbb_core.app.model.credentials import Credentials
    from openbb_core.app.model.user_settings import UserSettings
    from openbb_core.app.service.user_service import UserService

    fetch = Mock(return_value=deepcopy(BUNDLE))
    monkeypatch.setattr(F, "_fetch_sync", fetch)
    monkeypatch.setattr(F, "_arctic_library", lambda: None)
    monkeypatch.setattr(UserService, "read_from_file", staticmethod(lambda: UserSettings.model_construct(
        credentials=Credentials(eodhd_api_key="demo"))))
    F._reset_cache_for_tests()
    try:
        client = TestClient(app, raise_server_exceptions=False)
        stamps = set()
        for base in ["/api/v1/etf/holdings", "/api/v1/eodhd/etf/holdings"]:
            params = {"symbol": "VTI", "provider": "eodhd"}
            plain = client.get(base, params=params).json()["results"]
            assert plain[0]["weight"] == pytest.approx(.069985) and "snapshot" not in plain[0]
            response = client.get(base, params={**params, "chart_source": True})
            assert response.status_code == 200, response.text
            body = response.json()
            s, = body["extra"]["results_metadata"]["chart_sources"]["receipts"]
            assert s["holding_keys"] == [r["symbol"] for r in body["results"]]
            assert body["results"][0]["fund_symbol"] == "VTI.US"
            stamps.add(s["snapshot"])
        fetch.assert_called_once()
        assert len(stamps) == 1
    finally:
        F._reset_cache_for_tests()


def test_closed_local_and_live_authority():
    from openbq_eodhd.models._etf_hierarchy_source import EtfHierarchySource
    decode = TypeAdapter(EtfHierarchySource).validate_json
    s = receipt(acquired())
    local = {**s, "origin": "acquired-snapshot", "coverage": "partial", "coverage_reference": "issuer:register",
             "rounding_bound_fraction": 0.0001, "rounding_reference": "issuer:precision",
             "missing_mass": {"status": "verified", "reference": "issuer:omitted", "fraction": .930015}}
    assert decode(json.dumps(local)).model_dump(mode="json") == local
    for delta in [
        {"coverage": "complete"}, {"holding_keys": ["AAPL.US", "AAPL.US"]}, {"snapshot": "2026-11-19"},
        {"rounding_bound_fraction": .01}, {"missing_mass": {"status": "verified", "fraction": .9}},
        {"diagnostics": ["negative_weight"]}, {"raw_count": 3}, {"extra": True},
    ]:
        with pytest.raises(ValidationError):
            decode(json.dumps({**s, **delta}))
    for delta in [{"rounding_reference": None}, {"coverage_reference": None}, {"rounding_bound_fraction": .000101},
                  {"coverage": "complete"}, {"as_of": "2026-12-01"}]:
        with pytest.raises(ValidationError):
            decode(json.dumps({**local, **delta}))


@pytest.mark.parametrize("delta", [
    {"holding_keys": ["2.345E1", "AAYA.F"]}, {"coverage_reference": "unknown"},
    {"rounding_bound_fraction": .00001, "rounding_reference": "NA"},
    {"missing_mass": {"status": "verified", "fraction": .930015, "reference": "Unavailable"}},
    {"raw_count": 3, "diagnostics": ["excess_weight"]},
])
def test_unexplained_drops_and_false_authority(delta):
    from openbq_eodhd.models._etf_hierarchy_source import EtfHierarchySource
    s = receipt(acquired())
    if delta.get("diagnostics"):
        s["unsafe"]["excess_weight"] = True
    with pytest.raises(ValidationError):
        TypeAdapter(EtfHierarchySource).validate_json(json.dumps(
            {**s, "origin": "acquired-snapshot", "coverage": "partial", "coverage_reference": "issuer:report", **delta}))
