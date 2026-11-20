# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

import pytest
from openbb_core.provider.utils.errors import EmptyDataError
from pydantic import ValidationError

from openbq_eodhd.models import estimates

FILE = Path(__file__).parent / "fixtures/estimateVintageSnapshot.json"


def prepared():
    data = json.loads(FILE.read_text())
    receipt = data["chart_sources"]["receipts"][0]
    manifest = {k: v for k, v in receipt.items() if k not in {
        "raw_count", "normalized_count", "delivered_count", "diagnostics", "unsafe", "exact_repeats", "coordinates"}}
    return manifest, data["rows"], receipt


def prepare(manifest, rows):
    from openbq_eodhd.models._estimate_vintage_source import prepare_estimate_vintage_snapshot
    return prepare_estimate_vintage_snapshot(manifest, rows)


def test_recorded_batches_round_trip_with_an_explicit_missing_estimate():
    manifest, rows, receipt = prepared()
    result = prepare(manifest, rows)
    assert result.result == rows
    assert result.metadata["chart_sources"]["receipts"] == [receipt]
    assert result.result[2]["value"] is None


def test_raw_conflicts_and_exact_repeats_survive_exclusion():
    manifest, rows, _ = prepared()
    bad = dict(rows[0], value=9, currency="GBP", observed_at="2027-03-31")
    result = prepare(manifest, [bad, *rows, dict(rows[1]), dict(rows[2], target_kind="FY"), dict(rows[3], issuer="BETA")])
    receipt = result.metadata["chart_sources"]["receipts"][0]
    assert set(receipt["diagnostics"]) == set(receipt["unsafe"])
    assert all(receipt["unsafe"].values())
    assert receipt["raw_count"] == 10
    assert receipt["exact_repeats"] == 1
    assert "2.67543" not in json.dumps(receipt)


@pytest.mark.parametrize("patch", [
    {"target_start": "2026-01-01"}, {"target_kind": "FY"}, {"value": True},
    {"observed_at": "2026-11-11"}, {"input_ref": "earnings/trend"}, {"batch": "batch-b"},
    {"source_contract": "restated-current-consensus"}, {"basis_ref": "example/different-class"},
    {"basis": "per-ADR"}, {"currency": "GBX"}, {"unit": "thousands"}, {"entity_ref": "example/other"},
])
def test_single_hidden_raw_contradiction_is_accounted_for(patch):
    manifest, rows, _ = prepared()
    r = prepare(manifest, [*rows, dict(rows[0], **patch)]).metadata["chart_sources"]["receipts"][0]
    assert r["diagnostics"] and r["raw_count"] > r["normalized_count"]


@pytest.mark.parametrize("start,end,kind,valid", [
    ("2026-01-01", "2026-12-31", "FQ", False), ("2000-01-01", "2026-12-31", "FQ", False),
    ("2023-02-01", "2024-01-31", "FY", True), ("2024-02-29", "2025-02-27", "FY", True),
    ("2024-01-01", "2024-12-30", "FY", False), ("2024-01-01", "2024-09-30", "FY", False),
    ("2023-10-01", "2024-09-28", "FY", True), ("2023-10-01", "2024-10-05", "FY", True),
    ("2022-09-25", "2022-12-31", "FQ", True),
])
def test_exact_target_interval(start, end, kind, valid):
    manifest, rows, _ = prepared()
    manifest["target"] = dict(start=start, end=end, kind=kind)
    for row in rows:
        row.update(target_start=start, target_end=end, target_kind=kind)
    if valid:
        assert prepare(manifest, rows).result == rows
    else:
        with pytest.raises(ValidationError):
            prepare(manifest, rows)


@pytest.mark.parametrize("change", ["timestamp", "same-instant", "same-ref", "same-batch", "future", "membership",
                                     "current-before-baseline", "currency", "unqualified", "basis", "extra"])
def test_refuses_unproven_manifest(change):
    m, rows, _ = prepared()
    if change == "timestamp":
        m["vintages"][0]["observed_at"] = m["target"]["end"]
    elif change == "same-instant":
        m["vintages"][0]["observed_at"] = m["vintages"][1]["observed_at"].replace("Z", "+00:00")
    elif change == "same-ref":
        m["inputs"][0]["reference"] = m["inputs"][1]["reference"]
    elif change == "same-batch":
        m["vintages"][0]["batch"] = m["vintages"][1]["batch"]
    elif change == "future":
        m["vintages"][0]["observed_at"] = "2099-01-31T00:00:00Z"
    elif change == "membership":
        m["inputs"][0]["members"].pop()
    elif change == "current-before-baseline":
        m["universe"].update(baseline_batch="batch-c", current_batch="batch-a")
    elif change == "currency":
        m["issuers"][0]["currency"] = "ZZZ"
    elif change == "unqualified":
        m["issuers"][0]["issuer"] = "1.25"
    elif change == "basis":
        m["issuers"][0]["basis_ref"] = "N/A"
    else:
        m["issuers"][0]["estimate"] = 123
    with pytest.raises(ValidationError):
        prepare(m, rows)


def test_revenue_and_missing_members_do_not_generate_values():
    m, rows, _ = prepared()
    m.update(metric="revenue", universe={"status": "unknown"})
    for i in m["inputs"]:
        i.update(universe_id=None, members=None)
    for i in [*m["issuers"], *rows]:
        i.update(unit="money", basis="currency-units", basis_ref=None)
    rows = [dict(row, metric="revenue") for row in rows[:-1]]
    result = prepare(m, rows)
    assert result.result == rows
    assert result.metadata["chart_sources"]["receipts"][0]["delivered_count"] == 5


def test_legacy_trend_dates_and_relative_fields_cannot_certify_observation_history():
    trend = {"date": "2017-06-30", "period": "0q", "earningsEstimateAvg": "1.5700",
             "epsTrend7daysAgo": "1.56", "epsTrend90daysAgo": "1.51"}
    q = estimates.EODHDAnalystEstimatesQueryParams(symbol="AAPL")
    result = estimates.EODHDAnalystEstimatesFetcher.transform_data(q, {
        "General": {"UpdatedAt": "2026-11-19"}, "Earnings": {"Trend": {"0": trend}}}, chart_source=True)
    assert isinstance(result, list) and str(result[0].date) == "2017-06-30"
    assert result[0].estimated_eps_avg == 1.57
    m, _, _ = prepared()
    result = prepare(m, [trend])
    assert not result.result and result.metadata["chart_sources"]["receipts"][0]["unsafe"]["vintage_conflict"]


def test_legacy_extraction_preserves_single_request_and_source_error(monkeypatch):
    import asyncio
    calls = []

    async def unavailable(symbol, exchange, credentials):
        calls.append((symbol, exchange, credentials))
        raise EmptyDataError("original source error")

    monkeypatch.setattr(estimates.F, "get_bundle", unavailable)
    q = estimates.EODHDAnalystEstimatesQueryParams(symbol="AAPL")
    with pytest.raises(EmptyDataError, match="original source error"):
        asyncio.run(estimates.EODHDAnalystEstimatesFetcher.aextract_data(q, {}, chart_source=True))
    assert calls == [("AAPL", "US", {})]



def test_conflicting_malformed_boolean_precedes_number_with_same_coordinate():
    m, rows, _ = prepared()
    result = prepare(m, [dict(rows[0], value=True), *rows])
    receipt = result.metadata["chart_sources"]["receipts"][0]
    assert receipt["unsafe"]["unit_conflict"] and receipt["unsafe"]["duplicate_conflict"]
    assert rows[0] in result.result


def test_exact_repeats_are_disclosed_without_overstating_conflicts():
    m, rows, _ = prepared()
    result = prepare(m, [*rows, dict(rows[0])])
    receipt = result.metadata["chart_sources"]["receipts"][0]
    assert result.result == rows and receipt["exact_repeats"] == 1 and not receipt["diagnostics"]
    assert (receipt["raw_count"], receipt["normalized_count"], receipt["delivered_count"]) == (7, 6, 6)
