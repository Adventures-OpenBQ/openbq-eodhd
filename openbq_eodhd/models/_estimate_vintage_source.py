# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""Validate an explicitly acquired consensus history. No provider route supplies this manifest."""
import re
from calendar import monthrange
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from openbq_eodhd.models._chart_evidence import Currency, QualifiedSymbol
from openbq_eodhd.models._chart_sources import (
    ChartSourceEnvelope, ChartSourceFacts, ChartSourceReceipt, Count, annotate_chart_sources,
)
from openbq_eodhd.models._metric_chart_source import CURRENCIES
from openbq_eodhd.models._overlay_chart_source import Reference, _day, _finite, _stamp, _supported_quarter

Flag = Literal["identity_conflict", "vintage_conflict", "target_conflict", "unit_conflict", "duplicate_conflict"]
_CONTRACT = "independently-recorded-consensus-mean"


class Unsafe(ChartSourceFacts):
    identity_conflict: bool
    vintage_conflict: bool
    target_conflict: bool
    unit_conflict: bool
    duplicate_conflict: bool


class Target(ChartSourceFacts):
    start: str
    end: str
    kind: Literal["FY", "FQ"]

    @model_validator(mode="after")
    def _interval(self):
        start, end = _day(self.start), _day(self.end)
        if not start or not end:
            raise ValueError("Expected exact fiscal dates")
        days = (end - start).days + 1
        if self.kind == "FQ":
            valid = _supported_quarter(start, end)
        else:
            anniversary_day = min(start.day, monthrange(start.year + 1, start.month)[1])
            calendar_days = 365 + (monthrange(start.year if start.month <= 2 else start.year + 1, 2)[1] == 29)
            valid = days in (364, 371) or days == calendar_days + anniversary_day - start.day
        if not valid:
            raise ValueError("Unsupported inclusive fiscal interval")
        return self


class Vintage(ChartSourceFacts):
    id: Reference
    observed_at: str
    batch: Reference


class BatchInput(ChartSourceFacts):
    batch: Reference
    reference: Reference
    universe_id: Reference | None
    members: list[QualifiedSymbol] | None


class Issuer(ChartSourceFacts):
    issuer: QualifiedSymbol
    entity_ref: Reference
    currency: Currency
    unit: Literal["money", "money-per-share"]
    basis: Literal["current-split-adjusted-share", "currency-units"]
    basis_ref: Reference | None


class UnknownUniverse(ChartSourceFacts):
    status: Literal["unknown"]


class CompleteUniverse(ChartSourceFacts):
    status: Literal["complete"]
    id: Reference
    members: list[QualifiedSymbol]
    baseline_batch: Reference
    current_batch: Reference


class Manifest(ChartSourceFacts):
    version: Literal[1]
    kind: Literal["estimate-vintages"]
    origin: Literal["acquired-snapshot"]
    retrieved_at: datetime
    metric: Literal["eps", "revenue"]
    target: Target
    vintages: Annotated[list[Vintage], Field(min_length=2)]
    inputs: list[BatchInput]
    issuers: Annotated[list[Issuer], Field(min_length=1)]
    universe: UnknownUniverse | CompleteUniverse

    @field_validator("version", mode="before")
    @classmethod
    def _integer_version(cls, v):
        if type(v) is not int:
            raise ValueError("Expected integer version")
        return v

    @field_validator("retrieved_at", mode="before")
    @classmethod
    def _retrieved(cls, value):
        stamp = value if isinstance(value, datetime) else _stamp(value)
        if not stamp or stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError("Expected exact UTC snapshot retrieval")
        return stamp

    @model_validator(mode="after")
    def _lineage(self):
        instants = {v.batch: _stamp(v.observed_at) for v in self.vintages}
        n = len(self.vintages)
        if (len(instants) != n or len({v.id for v in self.vintages}) != n or len(set(instants.values())) != n
                or any(t is None or t > self.retrieved_at for t in instants.values())
                or len(self.inputs) != n or {i.batch for i in self.inputs} != instants.keys()
                or len({i.reference for i in self.inputs}) != n):
            raise ValueError("Each independent observation needs its own UTC instant, batch and acquired reference")
        members = {i.issuer for i in self.issuers}
        if len(members) != len(self.issuers) or len({i.entity_ref for i in self.issuers}) != len(self.issuers):
            raise ValueError("Expected distinct qualified issuers")
        eps = self.metric == "eps"
        for i in self.issuers:
            if (re.fullmatch(r"[0-9]+\.[0-9]+(?:E[+-]?[0-9]+)?", i.issuer) or i.currency not in CURRENCIES
                    or (i.unit, i.basis) != (("money-per-share", "current-split-adjusted-share") if eps else ("money", "currency-units"))
                    or (i.basis_ref is not None) != eps):
                raise ValueError("Unknown issuer, denomination, metric unit or current share basis")
        u = self.universe
        if isinstance(u, CompleteUniverse):
            if (set(u.members) != members or len(u.members) != len(members)
                    or u.baseline_batch not in instants or u.current_batch not in instants
                    or instants[u.baseline_batch] >= instants[u.current_batch]
                    or any(i.universe_id != u.id or i.members is None or set(i.members) != members
                           or len(i.members) != len(members) for i in self.inputs)):
                raise ValueError("Common batches and independently declared stable membership required")
        elif any(i.universe_id is not None or i.members is not None for i in self.inputs):
            raise ValueError("Contradictory unknown-universe declaration")
        return self


class Coordinate(ChartSourceFacts):
    issuer: QualifiedSymbol
    vintage: Reference


class EstimateVintageSource(Manifest, ChartSourceReceipt):
    coordinates: list[Coordinate]
    exact_repeats: Count
    diagnostics: list[Flag]
    unsafe: Unsafe

    @model_validator(mode="after")
    def _counts_and_coordinates(self):
        issues = {name for name, state in self.unsafe.model_dump().items() if state}
        unexplained = self.raw_count - self.exact_repeats - self.delivered_count
        issuers = {i.issuer for i in self.issuers}
        vintages = {v.id for v in self.vintages}
        if (self.normalized_count != self.delivered_count or self.delivered_count != len(self.coordinates)
                or len({(c.issuer, c.vintage) for c in self.coordinates}) != len(self.coordinates)
                or any(c.issuer not in issuers or c.vintage not in vintages for c in self.coordinates)
                or issues != set(self.diagnostics) or unexplained < 0 or (unexplained > 0) != bool(issues)
                or (self.exact_repeats and not self.coordinates)):
            raise ValueError("Contradictory raw counts, diagnostics or delivered coordinates")
        return self


_ROW_FIELDS = set("issuer entity_ref currency unit basis basis_ref vintage observed_at batch input_ref "
                  "source_contract metric target_start target_end target_kind value".split())
_KEY_FIELDS = ("issuer", "target_start", "target_end", "target_kind", "vintage")


def prepare_estimate_vintage_snapshot(manifest: dict, raw_rows: list):
    """Scan every original row with its acquisition manifest before exclusion or exact-repeat collapse."""
    source = Manifest.model_validate(manifest)
    if not isinstance(raw_rows, list):
        raise ValueError("Expected the complete original acquisition")
    issuers = {i.issuer: i for i in source.issuers}
    batches = {i.batch: i for i in source.inputs}
    vintages = {v.id: v for v in source.vintages}
    seen, accepted = {}, {}
    target_fields = {f"target_{k}": v for k, v in source.target.model_dump().items()}
    coordinates, delivered = [], []
    unsafe = set()
    repeats = 0
    for raw in raw_rows:
        row = raw if isinstance(raw, dict) else {}
        issues = set()
        issuer = issuers.get(row["issuer"]) if isinstance(row.get("issuer"), str) else None
        vintage = vintages.get(row["vintage"]) if isinstance(row.get("vintage"), str) else None
        if row.keys() != _ROW_FIELDS or not issuer or row.get("entity_ref") != issuer.entity_ref:
            issues.add("identity_conflict")
        if (not vintage or row.get("batch") != vintage.batch or row.get("observed_at") != vintage.observed_at
                or row.get("input_ref") != batches[vintage.batch].reference or row.get("source_contract") != _CONTRACT):
            issues.add("vintage_conflict")
        if any(row.get(field) != value for field, value in target_fields.items()):
            issues.add("target_conflict")
        if (row.get("metric") != source.metric or not issuer or
                any(row.get(k) != getattr(issuer, k) for k in ("currency", "unit", "basis", "basis_ref"))
                or "value" not in row or (row["value"] is not None and not _finite(row["value"]))):
            issues.add("unit_conflict")
        key = tuple(row.get(field) for field in _KEY_FIELDS)
        if all(isinstance(k, str) for k in key):
            # Even a malformed first row participates in conflict detection. bool is not a JSON number.
            previous = seen.setdefault(key, row)
            different = previous != row or isinstance(previous.get("value"), bool) != isinstance(row.get("value"), bool)
            if different:
                unsafe.add("duplicate_conflict")
                if key in accepted and row != accepted[key]:
                    issues.add("duplicate_conflict")
        if issues:
            unsafe.update(issues)
            continue
        if key in accepted:
            repeats += 1
            continue
        accepted[key] = row
        delivered.append(row)
        coordinates.append(Coordinate(issuer=issuer.issuer, vintage=vintage.id))

    receipt = EstimateVintageSource(**source.model_dump(), raw_count=len(raw_rows), normalized_count=len(delivered),
        delivered_count=len(delivered), exact_repeats=repeats, coordinates=coordinates, diagnostics=sorted(unsafe),
        unsafe=Unsafe(**{k: k in unsafe for k in Unsafe.model_fields}))
    return annotate_chart_sources(delivered, ChartSourceEnvelope[EstimateVintageSource](receipts=[receipt]))
