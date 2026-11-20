# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from openbq_eodhd.models._overlay_chart_source import OverlaySource, prepare_overlay_snapshot
from openbq_eodhd.models.estimates import EODHDHistoricalEpsFetcher
from openbq_eodhd.models.macro import EODHDConsumerPriceIndexFetcher
from openbq_eodhd.models.treasury import EODHDTreasuryRatesFetcher

SAMPLES = json.loads((Path(__file__).parent / "fixtures/overlaySnapshots.json").read_text())


def snapshot(recipe):
    sample = deepcopy(SAMPLES[recipe])
    receipt = sample["chart_sources"]["receipts"][0]
    manifest = {k: v for k, v in receipt.items() if k not in {
        "diagnostics", "unsafe", "raw_count", "normalized_count", "delivered_count", "coordinates",
    }}
    return receipt, manifest, sample["rows"]


@pytest.mark.parametrize("recipe", SAMPLES)
def test_explicit_preparation_validates_both_acquired_inputs_without_calendar_join(recipe):
    receipt, manifest, rows = snapshot(recipe)
    result = prepare_overlay_snapshot(manifest, rows)
    assert result.result == rows
    assert result.metadata["chart_sources"] == {"version": 1, "receipts": [receipt]}
    assert OverlaySource.model_validate_json(json.dumps(receipt)).recipe == recipe


@pytest.mark.parametrize(("recipe", "index", "changes", "flag"), [
    ("price-eps", 1, {"issuer": "SEC-CIK-other"}, "identity_conflict"),
    ("price-eps", 1, {"listing": "AAPL.LSE"}, "identity_conflict"),
    ("price-eps", 1, {"currency": "EUR"}, "unit_conflict"),
    ("price-eps", 1, {"share_basis_ref": None}, "basis_conflict"),
    ("price-eps", 1, {"period_kind": "FY"}, "basis_conflict"),
    ("price-eps", 1, {"source_contract": "Earnings.History.epsActual"}, "basis_conflict"),
    ("price-eps", 1, {"restated": False}, "basis_conflict"),
    ("price-eps", 1, {"available_at": None}, "coordinate_conflict"),
    ("price-eps", 1, {"target_start": None}, "coordinate_conflict"),
    ("price-eps", 1, {"release_ref": None}, "coordinate_conflict"),
    ("cpi-ten-year", 0, {"date": "2026-09-02"}, "coordinate_conflict"),
    ("cpi-ten-year", 0, {"basis": "annual-2010=100"}, "basis_conflict"),
    ("cpi-ten-year", 2, {"source_contract": "DFII10"}, "basis_conflict"),
    ("cpi-ten-year", 2, {"value": 5.12}, "unit_conflict"),
    ("cpi-ten-year", 2, {"input_ref": "another/vintage"}, "identity_conflict"),
])
def test_collects_conflicts_from_raw_rows_before_dropping_them(recipe, index, changes, flag):
    receipt, manifest, rows = snapshot(recipe)
    result = prepare_overlay_snapshot(manifest, [*rows, {**rows[index], **changes}])
    actual = result.metadata["chart_sources"]["receipts"][0]
    assert actual["unsafe"][flag]
    assert actual["raw_count"] == len(rows) + 1
    assert actual["delivered_count"] == len(rows)
    assert result.result == rows
    assert actual["coordinates"] == receipt["coordinates"]


def test_multiversion_eps_target_is_unsafe_even_with_a_different_report_coordinate():
    _, manifest, rows = snapshot("price-eps")
    result = prepare_overlay_snapshot(manifest, [*rows, {**rows[1], "available_at": "2026-11-03", "value": 8, "source_value": 8}])
    assert result.metadata["chart_sources"]["receipts"][0]["unsafe"]["coordinate_conflict"]
    assert result.result == rows
    original = {**rows[1], "available_at": "2026-11-02", "session_timing": "after-market"}
    prepared = prepare_overlay_snapshot(manifest, [rows[0], original])
    assert prepared.result[1]["available_at"] == "2026-11-02"
    assert prepared.result[1]["session_timing"] == "after-market"


def test_unknown_row_and_missing_side_are_never_clean():
    _, manifest, rows = snapshot("price-eps")
    annotated = prepare_overlay_snapshot(manifest, [None, *rows])
    assert annotated.metadata["chart_sources"]["receipts"][0]["unsafe"]["untraced_omission"]
    with pytest.raises(ValueError):
        prepare_overlay_snapshot(manifest, rows[:1])


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(origin="eodhd"),
    lambda s: s.update(raw_count=6),
    lambda s: s["coordinates"]["left"].append("1.98765"),
    lambda s: s["preparation"].update(input_refs=["sample/a", "sample/a"]),
    lambda s: s["preparation"].update(reference="unknown"),
    lambda s: s["series"]["right"].update(id="epsActual"),
    lambda s: s["series"]["right"].update(currency="EUR"),
    lambda s: s["identity"].update(listing="AAPL"),
    lambda s: s["vintages"].update(left="2026-11-19T22:00:00.123457Z"),
    lambda s: s.update(unknown=True),
])
def test_closed_receipt_rejects_unproven_or_impossible_claims(mutate):
    receipt, _, _ = snapshot("price-eps")
    mutate(receipt)
    with pytest.raises(ValidationError):
        OverlaySource.model_validate_json(json.dumps(receipt))


def test_existing_single_input_fetchers_remain_legacy_and_never_certify_an_overlay():
    history = {"Earnings": {"History": {"2026-09-30": {"date": "2026-09-30", "reportDate": "2026-11-02",
        "beforeAfterMarket": "AfterMarket", "epsActual": 2.05, "currency": None}}}}
    eps = EODHDHistoricalEpsFetcher.transform_data(EODHDHistoricalEpsFetcher.transform_query({"symbol": "AAPL"}), history)
    assert isinstance(eps, list) and eps[0].eps_actual == 2.05 and str(eps[0].report_date) == "2026-11-02"
    cpi = EODHDConsumerPriceIndexFetcher.transform_data(EODHDConsumerPriceIndexFetcher.transform_query({}),
        [{"Date": "2024-01-01", "CountryCode": "USA", "Value": 145.0}])
    assert isinstance(cpi, list) and cpi[0].value == 145.0
    query = EODHDTreasuryRatesFetcher.transform_query({})
    rates = EODHDTreasuryRatesFetcher.transform_data(query, [{"date": "2026-11-18", "tenor": "10Y", "rate": 5.07}])
    assert isinstance(rates, list) and rates[0].year_10 == 5.07 / 100


def test_matching_unknown_quote_and_eps_codes_cannot_prove_denomination():
    receipt, _, _ = snapshot("price-eps")
    for series in receipt["series"].values():
        series["currency"] = "ZZZ"
    with pytest.raises(ValidationError):
        OverlaySource.model_validate_json(json.dumps(receipt))


def test_accepts_decimal_and_binary_fraction_conversions_without_rounding_the_observation():
    _, manifest, rows = snapshot("cpi-ten-year")
    for value in (0.0411, 4.11 / 100):
        amended = [*rows[:2], {**rows[2], "source_value": 4.11, "value": value}, *rows[3:]]
        assert prepare_overlay_snapshot(manifest, amended).result == amended
