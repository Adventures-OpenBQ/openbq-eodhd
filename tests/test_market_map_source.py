# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
from pathlib import Path

import pytest
from openbb_core.provider.utils.errors import EmptyDataError, UnauthorizedError
from pydantic import ValidationError

from openbq_eodhd.models import screener


def fixture(mode="Cap"):
    data = json.loads((Path(__file__).parent / f"fixtures/marketMap{mode}.json").read_text())
    receipt = data["chart_sources"]["receipts"][0]
    manifest = {k: v for k, v in receipt.items() if k not in {
        "listing_keys", "raw_count", "normalized_count", "delivered_count", "exact_repeats",
        "rejected_count", "excluded_areas", "diagnostics", "unsafe"}}
    return manifest, data["rows"], receipt


def prepare(manifest, raw):
    from openbq_eodhd.models._market_map_source import prepare_market_map_snapshot
    result = prepare_market_map_snapshot(manifest, raw)
    return result.result, result.metadata["chart_sources"]["receipts"][0]


@pytest.mark.parametrize("mode", ["Cap", "Index"])
def test_prepared_modes_round_trip_in_original_units(mode):
    m, rows, receipt = fixture(mode)
    assert prepare(m, rows) == (rows, receipt)
    assert rows[0]["change"] == 0 and rows[1]["change"] is None


@pytest.mark.parametrize(("mode", "patch"), [
    ("Cap", {"universe": "GSPC.INDX"}), ("Cap", {"denomination": "$"}), ("Cap", {"denomination": "ZZZ"}),
    ("Cap", {"cap_retrieved_at": "2026-11-12T22:00:00Z"}), ("Cap", {"membership_as_of": "2026-11-12"}),
    ("Cap", {"session": "2026-11-31"}), ("Cap", {"session": "2026-11-14"}), ("Cap", {"version": True}),
    ("Cap", {"prior_session": "2026-11-13"}), ("Cap", {"session_ref": "unknown"}),
    ("Cap", {"return_basis": "intraday"}), ("Cap", {"price_basis": "total-return"}), ("Cap", {"fake": 1}),
    ("Index", {"coverage": "unknown"}), ("Index", {"weight_valid_for": "2026-11-12"}),
    ("Index", {"membership_as_of": "2026-11-14"}), ("Index", {"weight_reference": None}),
    ("Index", {"members": ["ALFA.US", "ALFA.US"]}), ("Index", {"members": ["1.25"]}),
    ("Index", {"rounding_bound_fraction": .00011, "rounding_reference": "archive/rounding"}),
    ("Index", {"rounding_bound_fraction": .0001}), ("Index", {"denomination": "USD"}),
])
def test_unproven_manifests_refuse(mode, patch):
    m, rows, _ = fixture(mode)
    with pytest.raises(ValidationError):
        prepare({**m, **patch}, rows)


def test_raw_conflicts_precede_area_exclusion():
    m, rows, _ = fixture()
    other = dict(rows[0], area=0, denomination="EUR", session="2026-11-12", universe="GSPC.INDX")
    result, receipt = prepare(m, [other, *rows, dict(rows[1])])
    assert result == rows
    assert receipt["exact_repeats"] == 1 and receipt["rejected_count"] == 1
    assert set(receipt["diagnostics"]) == {"denomination_conflict", "session_conflict", "membership_conflict",
                                          "duplicate_conflict"}
    assert "98765.4321" not in json.dumps(receipt)


def test_zero_negative_invalid_area_are_individually_counted_and_not_financial_proof():
    m, rows, _ = fixture()
    raw = [dict(rows[0], listing=f"OTHER{i}.US", area=v) for i, v in enumerate([0, -2, None, True, "5", 10**400])]
    result, receipt = prepare(m, [*rows, *raw])
    assert result == rows and not receipt["diagnostics"]
    assert receipt["excluded_areas"] == {"zero": 1, "negative": 1, "invalid": 4}
    assert (receipt["raw_count"], receipt["normalized_count"], receipt["delivered_count"]) == (8, 2, 2)


def test_malformed_keyed_entry_still_witnesses_both_qualified_coordinates():
    m, rows, _ = fixture()
    raw = {"ALFA.US": None, "OTHER.US": rows[0], "BETA.US": rows[1]}
    result, receipt = prepare(m, raw)
    assert result == rows[1:]
    assert receipt["unsafe"]["identity_conflict"] and receipt["unsafe"]["duplicate_conflict"]


@pytest.mark.parametrize(("a", "b"), [(True, 1), (1, True), ([{"x": True}], [{"x": 1}])])
def test_duplicate_compares_whole_json_including_malformed_categories(a, b):
    m, rows, _ = fixture()
    raw = [dict(rows[0], other=a), dict(rows[0], other=b), rows[1]]
    result, receipt = prepare(m, raw)
    assert result == rows[1:] and receipt["unsafe"]["duplicate_conflict"]
    assert receipt["unsafe"]["identity_conflict"]


@pytest.mark.parametrize(("coverage", "weight", "rounding", "issues"), [
    ("complete", .39995, .0001, []), ("complete", .39995, None, ["membership_conflict"]),
    ("partial", .39995, None, []), ("bounded", .40005, .0001, []),
    ("partial", .40005, None, ["excess_weight"]), ("complete", .40011, .0001, ["excess_weight"]),
])
def test_observed_weight_admission_uses_declared_bounds(coverage, weight, rounding, issues):
    m, rows, _ = fixture("Index")
    m.update(coverage=coverage, rounding_bound_fraction=rounding, rounding_reference="example/rounding" if rounding else None)
    rows[1]["area"] = weight
    result, receipt = prepare(m, rows)
    assert receipt["diagnostics"] == issues and result == rows


@pytest.mark.parametrize("area", [1e308, 10**500])
def test_raw_index_excess_persists_even_if_issue_removes_giant_addend(area):
    m, rows, _ = fixture("Index")
    bad = dict(rows[0], listing="OTHER.US", area=area, session="1999-01-01")
    result, receipt = prepare(m, [*rows, bad])
    assert result == rows
    assert set(receipt["diagnostics"]) == {"session_conflict", "membership_conflict", "excess_weight"}


def test_complete_requires_authoritative_roster_even_if_remaining_weight_is_one():
    m, rows, _ = fixture("Index")
    result, receipt = prepare(m, [dict(rows[0], area=1)])
    assert len(result) == 1 and receipt["unsafe"]["membership_conflict"]


@pytest.mark.parametrize("patch", [{"change": True}, {"change": -1.01}, {"price_basis": "five-day"},
                                   {"session_ref": None}, {"prior_session": "2026-11-01"}])
def test_invalid_return_lineage_on_negative_area_survives(patch):
    m, rows, _ = fixture()
    result, receipt = prepare(m, [*rows, dict(rows[0], listing="OTHER.US", area=-1, **patch)])
    assert result == rows and receipt["unsafe"]["session_conflict"]


def test_existing_screener_preserves_opt_in_bubble_and_legacy_requests(monkeypatch):
    captured = {"code": "AAPL", "exchange": "US", "currency_symbol": "$", "last_day_data_date": "2026-08-12",
                "refund_1d_p": -.87, "refund_5d_p": -2.73, "market_capitalization": 4411090796544}
    fetcher = screener.EODHDEquityScreenerFetcher
    requests = []
    class Client:
        def stock_market_screener(self, **kwargs):
            requests.append(kwargs)
            return {"data": [captured]}
    monkeypatch.setattr(screener, "sdk_call", lambda credentials, call, name: call(Client()))
    for opt_in in (False, True):
        q = fetcher.transform_query({"chart_source": opt_in})
        data = asyncio.run(fetcher.aextract_data(q, {}))
        output = fetcher.transform_data(q, data)
        if opt_in:
            assert "chart_sources" not in output.metadata and "chart_evidence" in output.metadata
            output = output.result
        assert output[0].price_change_5d == -.0273
    assert len(requests) == 2 and requests[0] == requests[1]
    for opt_in, reply, error in [(False, [], EmptyDataError), (True, {"error": "denied"}, UnauthorizedError)]:
        monkeypatch.setattr(screener, "sdk_call", lambda *args: reply)
        with pytest.raises(error):
            asyncio.run(fetcher.aextract_data(fetcher.transform_query({"chart_source": opt_in}), {}))


def test_one_excluded_index_record_retains_every_original_issue():
    m, rows, _ = fixture("Index")
    bad = dict(rows[0], sector=True, session="2025-01-01", weight_valid_for="2025-01-01", denomination="GBP", area=7)
    result, receipt = prepare(m, [*rows, bad])
    assert result == rows and receipt["raw_count"] == 3 and receipt["rejected_count"] == 1
    assert all(receipt["unsafe"].values()) and set(receipt["diagnostics"]) == set(receipt["unsafe"])


def test_same_embedded_row_under_conflicting_acquired_keys_is_not_an_exact_repeat():
    m, rows, _ = fixture()
    result, receipt = prepare(m, {"ALFA.US": rows[0], "OTHER.US": rows[0], "BETA.US": rows[1]})
    assert result == rows and receipt["exact_repeats"] == 0
    assert set(receipt["diagnostics"]) == {"duplicate_conflict", "identity_conflict"}


@pytest.mark.parametrize("case", ["repeat-without-accepted", "single-duplicate", "partial-membership-without-rejection"])
def test_receipt_disallows_impossible_pre_drop_counts(case):
    from openbq_eodhd.models._market_map_source import IndexSource
    _, _, r = fixture("Index")
    r.update(coverage="partial", raw_count=1, delivered_count=0, normalized_count=0, listing_keys=[],
             diagnostics=["membership_conflict"], rejected_count=1)
    r["unsafe"]["membership_conflict"] = True
    if case == "repeat-without-accepted":
        r.update(raw_count=2, exact_repeats=1)
    elif case == "single-duplicate":
        r["unsafe"]["duplicate_conflict"] = True
        r["diagnostics"].append("duplicate_conflict")
    else:
        r.update(raw_count=0, rejected_count=0)
    with pytest.raises(ValidationError):
        IndexSource.model_validate(r)


def test_zero_bound_fixture_round_trips_with_correctly_rounded_total():
    m, rows, receipt = fixture("IndexZeroBound")
    assert receipt["rounding_bound_fraction"] == 0 and receipt["diagnostics"] == []
    assert [r["area"] for r in rows] == [.08, .69, .23]
    assert prepare(m, rows) == (rows, receipt)
    # Half an ulp rounds to even at one. The smallest additional observation breaks the tie.
    for weights, excess in [([1., 2**-54, 2**-54], False), ([1., 2**-53, 5e-324], True)]:
        observed = [dict(r, area=w) for r, w in zip(rows, weights)]
        assert prepare(m, observed)[1]["unsafe"]["excess_weight"] is excess


@pytest.mark.parametrize("other_first", [True, False])
def test_distinct_acquired_keys_always_contribute_to_raw_index_total(other_first):
    m, rows, _ = fixture("Index")
    keyed = [("ALFA.US", rows[0]), ("OTHER.US", rows[0])]
    if other_first:
        keyed.reverse()
    delivered, r = prepare(m, dict([*keyed, ("BETA.US", rows[1])]))
    assert delivered == rows
    assert set(r["diagnostics"]) == {"identity_conflict", "duplicate_conflict", "excess_weight"}
    assert (r["raw_count"], r["normalized_count"], r["rejected_count"], r["exact_repeats"]) == (3, 2, 1, 0)


@pytest.mark.parametrize("reverse", [True, False])
def test_repeated_malformed_weight_is_counted_once_in_either_order(reverse):
    m, rows, _ = fixture("Index")
    m["coverage"] = "partial"
    bad = dict(rows[0], area=.4, sector=False)
    raw = [dict(rows[0], area=.3), bad, bad]
    _, r = prepare(m, list(reversed(raw)) if reverse else raw)
    assert r["rejected_count"] == 2 and r["unsafe"]["duplicate_conflict"] and not r["unsafe"]["excess_weight"]


@pytest.mark.parametrize("zero,negative", [(0, 0), (1, 2)])
def test_index_excess_requires_some_possible_positive_raw_area(zero, negative):
    from openbq_eodhd.models._market_map_source import IndexSource
    _, _, r = fixture("Index")
    r.update(coverage="partial", raw_count=zero + negative, normalized_count=0, delivered_count=0, listing_keys=[],
             diagnostics=["excess_weight"], excluded_areas={"zero": zero, "negative": negative, "invalid": 0})
    r["unsafe"]["excess_weight"] = True
    with pytest.raises(ValidationError):
        IndexSource.model_validate(r)
