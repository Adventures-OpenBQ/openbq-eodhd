# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
from datetime import datetime, timedelta, timezone
from typing import Literal

import pytest
from pydantic import ValidationError

from openbq_eodhd.models._chart_sources import (
    ChartSourceEnvelope, ChartSourceFacts, ChartSourceReceipt,
)


class FixtureFacts(ChartSourceFacts):
    missing: bool


class FixtureReceipt(ChartSourceReceipt):
    kind: Literal["metric"]
    diagnostics: list[Literal["missing"]]
    unsafe: FixtureFacts
    origin: Literal["test"]


def receipt(**changes):
    fields = dict(version=1, kind="metric", retrieved_at=datetime.now(timezone.utc),
                  raw_count=3, normalized_count=2, delivered_count=1,
                  diagnostics=["missing"], unsafe={"missing": True}, origin="test")
    return {**fields, **changes}


def test_closed_typed_envelope_preserves_family_fields():
    envelope = ChartSourceEnvelope[FixtureReceipt](version=1, receipts=[receipt()])
    wire = envelope.model_dump(mode="json")
    assert wire["receipts"][0]["origin"] == "test"
    assert wire["receipts"][0]["unsafe"] == {"missing": True}
    assert ChartSourceEnvelope[FixtureReceipt].model_validate_json(envelope.model_dump_json()) == envelope


@pytest.mark.parametrize("changes", [
    {"version": True}, {"raw_count": False}, {"normalized_count": 4}, {"delivered_count": -1},
    {"raw_count": 2**53}, {"retrieved_at": datetime.now()}, {"kind": "alien"},
    {"retrieved_at": datetime.now(timezone(timedelta(hours=1)))}, {"settings_proof": True},
    {"diagnostics": ["missing", "missing"]}, {"diagnostics": ["unverified"]},
    {"unsafe": {"missing": True, "surprise": False}},
])
def test_invalid_family_facts(changes):
    with pytest.raises(ValidationError):
        ChartSourceEnvelope[FixtureReceipt](version=1, receipts=[receipt(**changes)])


def test_unknown_or_duplicate_kind_rejected():
    for fields in [dict(receipts=[]), dict(receipts=[receipt(), receipt()]),
                   dict(receipts=[receipt()], scope="full"), dict(version=True, receipts=[receipt()])]:
        with pytest.raises(ValidationError):
            ChartSourceEnvelope[FixtureReceipt](**{"version": 1, **fields})


def test_base_receipt_cannot_certify_a_source():
    common = receipt()
    del common["origin"]
    common.update(diagnostics=[], unsafe={})
    with pytest.raises(ValidationError):
        ChartSourceReceipt.model_validate(common)
    with pytest.raises(ValidationError):
        ChartSourceEnvelope.model_validate({"version": 1, "receipts": [common]})


def test_untyped_envelope_cannot_erase_validated_family_fields():
    with pytest.raises(ValidationError):
        ChartSourceEnvelope(version=1, receipts=[FixtureReceipt(**receipt())])


def test_each_family_serializes_its_own_closed_facts():
    class OtherReceipt(ChartSourceReceipt):
        kind: Literal["news"]
        diagnostics: list[Literal["missing"]]
        unsafe: FixtureFacts
        day: Literal["test-only"]

    second = receipt(kind="news", day="test-only")
    del second["origin"]
    mixed = ChartSourceEnvelope[FixtureReceipt | OtherReceipt](receipts=[receipt(), second])
    wire = mixed.model_dump(mode="json")
    assert [entry["kind"] for entry in wire["receipts"]] == ["metric", "news"]
    assert wire["receipts"][0]["origin"] == "test"
    assert wire["receipts"][1]["day"] == "test-only"


def test_opt_in_annotation_keeps_legacy_metadata_and_checks_delivered_rows():
    from openbb_core.provider.abstract.annotated_result import AnnotatedResult
    from openbq_eodhd.models._chart_sources import annotate_chart_sources

    existing = AnnotatedResult(result=[{"id": "a"}], metadata={"chart_evidence": {"legacy": True}})
    envelope = ChartSourceEnvelope[FixtureReceipt](receipts=[receipt()])
    annotated = annotate_chart_sources(existing, envelope)
    assert annotated.result == existing.result
    assert annotated.metadata == {**existing.metadata, "chart_sources": envelope.model_dump(mode="json")}
    assert "chart_sources" not in existing.metadata
    with pytest.raises(ValueError, match="delivered"):
        annotate_chart_sources([], envelope)
    with pytest.raises(ValueError, match="already"):
        annotate_chart_sources(annotated, envelope)
