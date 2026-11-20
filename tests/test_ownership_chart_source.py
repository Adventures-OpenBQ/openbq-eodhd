# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""Source-bound checks for ownership acquisition evidence."""

import json
from datetime import date, datetime, timedelta, timezone
from itertools import chain, repeat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from openbb_core.api.rest_api import app
from openbb_core.app.model.credentials import Credentials
from openbb_core.app.model.user_settings import UserSettings
from openbb_core.app.service.user_service import UserService
from openbb_core.provider.utils.errors import EmptyDataError
from pydantic import ValidationError

from openbq_eodhd.models import _fundamentals as F
from openbq_eodhd.models._chart_sources import ChartSourceEnvelope, annotate_chart_sources
from openbq_eodhd.models._ownership_chart_source import OwnershipSource
from openbq_eodhd.models.profile import EODHDShareStatisticsFetcher

# Public EODHD DEMO /fundamentals/AAPL.US excerpt. See the chart contract.
DEMO = {
    "General": {
        "Code": "AAPL", "Exchange": "NASDAQ", "OpenFigi": "BBG000B9XRY4",
        "CurrencyCode": "USD", "UpdatedAt": "2026-11-19", "PrimaryTicker": "AAPL.US", "Type": "Common Stock",
    },
    "SharesStats": {
        "SharesOutstanding": 14532182000, "SharesFloat": 14507041325,
        "PercentInsiders": 1.687, "PercentInstitutions": 66.375,
    },
}
BASE = {
    "version": 1, "kind": "ownership", "origin": "acquired-snapshot",
    "issuer": "EXAMPLE.US", "share_class": "Class-A", "retrieved_at": "2026-11-20T04:16:46Z",
    "raw_count": 1, "normalized_count": 1, "delivered_count": 1, "diagnostics": [],
    "unsafe": {"identity_conflict": False, "unit_conflict": False, "date_conflict": False, "partition_conflict": False},
}
BARS = {
    **BASE,
    "measures": {
        "insiders": {"unit": "percent-points", "basis": "shares-outstanding", "as_of": None},
        "institutions": {"unit": "fraction", "basis": None, "as_of": "2026-09-30"},
    },
    "field_origins": {"insiders": "insider_pct", "institutions": "institution_ratio"},
    "partition": {"status": "unavailable"},
}
PARTITION = {
    **BASE, "raw_count": 2, "normalized_count": 2, "delivered_count": 2,
    "measures": {}, "field_origins": {"bucket": "bucket_id", "share": "reported_share"},
    "partition": {
        "status": "verified", "as_of": "2026-09-30", "denominator": "outstanding-shares",
        "exclusive": True, "exhaustive": True, "rounding_bound_percent": 0.002,
        "reference": "example:ownership-register",
    },
    "bucket_keys": ["control", "other"],
}


def receipt(payload):
    return ChartSourceEnvelope[OwnershipSource].model_validate_json(json.dumps({"version": 1, "receipts": [payload]}))


def test_legacy_capture_keeps_values_and_file_update_date():
    fetcher = EODHDShareStatisticsFetcher
    query = fetcher.transform_query({"symbol": "AAPL"})
    rows = fetcher.transform_data(query, DEMO)
    assert isinstance(rows, list)
    row, = rows
    assert row.symbol == "AAPL"
    assert row.date == date(2026, 11, 19)
    assert row.percent_insiders == 1.687
    assert row.percent_institutions == 66.375
    assert row.free_float == pytest.approx(99.827)
    with pytest.raises(EmptyDataError, match="no share statistics for 'AAPL'"):
        fetcher.transform_data(query, {})


@pytest.mark.parametrize("payload", [BARS, PARTITION])
def test_closed_explicit_snapshot_shapes_round_trip(payload):
    envelope = receipt(payload)
    assert envelope.model_dump(mode="json")["receipts"] == [payload]


@pytest.mark.parametrize("payload", [
    {**BARS, "issuer": "EXAMPLE"},
    {**BARS, "share_class": 5},
    {**BARS, "retrieved_at": "2026-11-20T04:16:46-01:00"},
    {**BARS, "partition": PARTITION["partition"]},
    {**BARS, "partition": {"status": "unavailable", "exclusive": True}},
    {**BARS, "measures": {"insiders": {"unit": "%", "basis": "shares-outstanding", "as_of": None}}},
    {**BARS, "measures": {"insiders": {"unit": "fraction", "basis": None}}},
    {**BARS, "delivered_count": 0},
    {**BARS, "diagnostics": ["identity_conflict"]},
    {**BARS, "raw_count": 2},
    {**PARTITION, "normalized_count": 3},
    {**PARTITION, "share_class": None},
    {**PARTITION, "bucket_keys": ["control", "control"]},
    {**PARTITION, "bucket_keys": ["23.456", "other"]},
    {**PARTITION, "partition": {**PARTITION["partition"], "exhaustive": False}},
    {**PARTITION, "partition": {**PARTITION["partition"], "exclusive": 1}},
    {**PARTITION, "partition": {**PARTITION["partition"], "rounding_bound_percent": -0.01}},
])
def test_refuses_contradictory_and_missing_contract_facts(payload):
    with pytest.raises(ValidationError):
        receipt(payload)


def test_retains_raw_contradictions_and_declared_counts():
    payload = {
        **PARTITION, "raw_count": 3, "diagnostics": ["partition_conflict"],
        "unsafe": {**BASE["unsafe"], "partition_conflict": True},
    }
    envelope = receipt(payload)
    result = annotate_chart_sources([{"bucket_id": "control"}, {"bucket_id": "other"}], envelope)
    assert result.metadata["chart_sources"]["receipts"] == [payload]
    with pytest.raises(ValueError, match="counts must match"):
        annotate_chart_sources([{"bucket_id": "control"}], envelope)


class CacheLibrary:
    """Exercise the real read/write and metadata paths against an in-memory table."""

    def __init__(self):
        self.data = {}

    def has_symbol(self, symbol):
        return symbol in self.data

    def read(self, symbol):
        data, metadata = self.data[symbol]
        return SimpleNamespace(data=data, metadata=metadata)

    def read_metadata(self, symbol):
        raise AssertionError("Read data and its acquisition timestamp from the same version")

    def write(self, symbol, data, *, metadata, prune_previous_versions):
        self.data[symbol] = (data, metadata)


@pytest.fixture
def ownership_http(monkeypatch):
    F._reset_cache_for_tests()
    monkeypatch.setattr(
        UserService, "read_from_file",
        staticmethod(lambda: UserSettings.model_construct(
            credentials=Credentials(eodhd_api_key="demo"),
        )),
    )
    upstream = Mock(return_value=DEMO)
    monkeypatch.setattr(F, "_fetch_sync", upstream)
    cache = CacheLibrary()
    monkeypatch.setattr(F, "_arctic_library", lambda: cache)
    yield TestClient(app, raise_server_exceptions=False), upstream, cache
    F._reset_cache_for_tests()


def get_share_statistics(client, prefix="/eodhd", **params):
    response = client.get(f"/api/v1{prefix}/equity/ownership/share_statistics", params={
        "symbol": "aapl", **params,
    })
    assert response.status_code == 200, response.text
    return response.json()


def chart_receipts(body):
    return body.get("extra", {}).get("results_metadata", {}).get("chart_sources", {}).get("receipts", [])


def test_completed_opt_in_has_original_vintage_after_legacy_l1_and_l2_hits(ownership_http):
    client, upstream, cache = ownership_http
    # A legacy request and a chart request share the same actual acquisition.
    legacy = get_share_statistics(client)
    assert chart_receipts(legacy) == [] and legacy["results"][0]["symbol"] == "AAPL"
    original = cache.data["AAPL.US"][1]["retrieved_at"]
    assert datetime.fromisoformat(original).tzinfo == timezone.utc

    first = get_share_statistics(client, chart_source=True)
    chart, = chart_receipts(first)
    assert chart["origin"] == "eodhd-share-statistics"
    assert chart["issuer"] == first["results"][0]["symbol"] == "AAPL.US"
    assert chart["share_class"] is None
    assert chart["retrieved_at"] == original.replace("+00:00", "Z")
    assert first["results"][0]["date"] == "2026-11-19"
    assert first["results"][0]["percent_insiders"] == 1.687
    assert first["results"][0]["percent_institutions"] == 66.375
    assert chart["partition"] == {"status": "unavailable"}
    assert chart["field_origins"] == {"insiders": "percent_insiders", "institutions": "percent_institutions"}
    assert chart["measures"] == {
        role: {"unit": "percent-points", "basis": None, "as_of": None}
        for role in ("insiders", "institutions")
    }
    assert (chart["raw_count"], chart["normalized_count"], chart["delivered_count"]) == (1, 1, 1)
    F._reset_cache_for_tests()
    second = get_share_statistics(client, prefix="", provider="eodhd", chart_source=True, symbol="AAPL.US")
    assert chart_receipts(second) == [chart]
    upstream.assert_called_once()
    assert upstream.call_args.args[0] == "AAPL.US"


@pytest.mark.parametrize("stamp", [
    None,
    "2000-01-01T00:00:00-05:00",
    "invalid",
    "2000-01-01X00:00:00+00:00",
    "2000-01-01T00:00:00+00:00:00.5",
    "2000-01-01T00:00:00",
    "2999-01-01T00:00:00+00:00",
])
def test_old_or_unverified_l2_stamp_preserves_legacy_and_refuses_without_refetch(ownership_http, stamp):
    client, upstream, cache = ownership_http
    seed = get_share_statistics(client)
    entry = cache.data["AAPL.US"][1]
    if stamp is None:
        del entry["retrieved_at"]
    else:
        entry["retrieved_at"] = stamp
    F._reset_cache_for_tests()
    chart = get_share_statistics(client, chart_source=True)
    assert chart["results"] == seed["results"]
    assert chart_receipts(chart) == []
    upstream.assert_called_once()


@pytest.mark.parametrize("delta,params", [
    ({"Code": "SOMETHING"}, {}), ({"Exchange": "LSE"}, {}), ({"PrimaryTicker": "AAPL.LSE"}, {}),
    ({"PrimaryTicker": None}, {}), ({"Type": "Preferred Stock"}, {}), ({}, {"exchange": "LSE", "symbol": "AAPL.US"}),
    ({}, {"exchange": "LSE"}), ({}, {"symbol": "DIFFERENT.US"}),
])
def test_completed_request_requires_acquired_primary_us_common_stock_identity(ownership_http, delta, params):
    client, upstream, _ = ownership_http
    upstream.return_value = {**DEMO, "General": {**DEMO["General"], **delta}}
    body = get_share_statistics(client, chart_source=True, **params)
    assert chart_receipts(body) == []
    upstream.assert_called_once()


@pytest.mark.parametrize("insiders,institutions,delivered,conflict", [
    (0.5, 50, (0.5, 50), False), (0, None, (0, None), False),
    ("0.5", 50, (None, 50), True), (True, 50, (None, 50), True),
    (101, 50, (None, 50), True), (-0.001, 50, (None, 50), True),
])
def test_headline_percentages_are_preserved_and_raw_unit_conflicts_precede_normalization(
    ownership_http, insiders, institutions, delivered, conflict,
):
    client, upstream, _ = ownership_http
    upstream.return_value = {
        **DEMO, "SharesStats": {**DEMO["SharesStats"], "PercentInsiders": insiders, "PercentInstitutions": institutions},
    }
    response = get_share_statistics(client, chart_source=True)
    single, = response["results"]
    assert (single["percent_insiders"], single["percent_institutions"]) == delivered
    source, = chart_receipts(response)
    assert source["unsafe"]["unit_conflict"] is conflict
    assert source["diagnostics"] == (["unit_conflict"] if conflict else [])
    upstream.assert_called_once()


@pytest.fixture
def cache_clock(monkeypatch):
    now = datetime(2026, 11, 20, 4, 17, tzinfo=timezone.utc)
    clock = Mock(return_value=now)

    class WallClock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return clock()

    monkeypatch.setattr(F, "datetime", WallClock)
    return now, clock


@pytest.mark.parametrize("retrieval_offset", [-1, 1])
def test_future_l2_write_and_rollback_refuse_without_refetch(ownership_http, cache_clock, retrieval_offset):
    client, upstream, cache = ownership_http
    now, _ = cache_clock
    seed = get_share_statistics(client)
    entry = cache.data["AAPL.US"][1]
    entry["fetched_at"] = (now + timedelta(hours=1)).isoformat()
    entry["retrieved_at"] = (now + timedelta(seconds=retrieval_offset)).isoformat()
    F._reset_cache_for_tests()
    chart = get_share_statistics(client, chart_source=True)
    assert chart["results"] == seed["results"]
    assert chart_receipts(chart) == []
    upstream.assert_called_once()


def test_invalid_l2_write_format_refuses_only_source_qualification(ownership_http, cache_clock):
    client, upstream, cache = ownership_http
    seed = get_share_statistics(client)
    entry = cache.data["AAPL.US"][1]
    entry["fetched_at"] = entry["fetched_at"].replace("T", "X")
    F._reset_cache_for_tests()
    chart = get_share_statistics(client, chart_source=True)
    assert chart["results"] == seed["results"]
    assert chart_receipts(chart) == []
    upstream.assert_called_once()


@pytest.mark.parametrize("elapsed", [-1, 1])
def test_l1_rollback_before_retrieval_or_write_refuses_until_clock_recovers(ownership_http, cache_clock, elapsed):
    client, upstream, _ = ownership_http
    now, clock = cache_clock
    clock.side_effect = chain((now, now, now + timedelta(seconds=2)), repeat(now + timedelta(seconds=2)))
    seed = get_share_statistics(client)
    valid = get_share_statistics(client, chart_source=True)
    valid_chart, = chart_receipts(valid)
    clock.side_effect = None
    clock.return_value = now + timedelta(seconds=elapsed)
    rolled_back = get_share_statistics(client, chart_source=True)
    assert rolled_back["results"] == seed["results"]
    assert chart_receipts(rolled_back) == []
    clock.return_value = now + timedelta(seconds=3)
    assert chart_receipts(get_share_statistics(client, chart_source=True)) == [valid_chart]
    upstream.assert_called_once()


@pytest.mark.parametrize("retrieval_offset,write_offset", [(-1, 2), (2, 1)])
def test_fresh_inverted_vintage_is_data_only_in_both_tiers(
    ownership_http, cache_clock, retrieval_offset, write_offset,
):
    client, upstream, cache = ownership_http
    now, clock = cache_clock
    clock.side_effect = chain((
        now, now + timedelta(seconds=retrieval_offset), now + timedelta(seconds=write_offset),
    ), repeat(now + timedelta(seconds=3)))
    response = get_share_statistics(client, chart_source=True)
    assert response["results"][0]["symbol"] == "AAPL"
    assert chart_receipts(response) == []
    assert "retrieved_at" not in cache.data["AAPL.US"][1]
    F._reset_cache_for_tests()
    assert chart_receipts(get_share_statistics(client, chart_source=True)) == []
    upstream.assert_called_once()
