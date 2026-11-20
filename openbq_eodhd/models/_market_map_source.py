# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""Validate one explicitly acquired local equity market-map table; no live adapter supplies it."""
from datetime import datetime, timedelta
from math import fsum
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, field_validator, model_validator

from openbq_eodhd.models._chart_evidence import Currency, QualifiedSymbol
from openbq_eodhd.models._chart_sources import (
    ChartSourceEnvelope, ChartSourceFacts, ChartSourceReceipt, Count, annotate_chart_sources,
)
from openbq_eodhd.models._estimate_vintage_source import _same_json
from openbq_eodhd.models._etf_hierarchy_source import _symbol
from openbq_eodhd.models._metric_chart_source import CURRENCIES
from openbq_eodhd.models._overlay_chart_source import Reference, _day, _finite, _stamp

Flag = Literal["identity_conflict", "session_conflict", "denomination_conflict", "membership_conflict",
               "duplicate_conflict", "excess_weight"]


class Unsafe(ChartSourceFacts):
    identity_conflict: bool
    session_conflict: bool
    denomination_conflict: bool
    membership_conflict: bool
    duplicate_conflict: bool
    excess_weight: bool


class AreaExclusions(ChartSourceFacts):
    zero: Count
    negative: Count
    invalid: Count


class Manifest(ChartSourceFacts):
    version: Literal[1]
    kind: Literal["market-map"]
    origin: Literal["acquired-snapshot"]
    retrieved_at: datetime
    acquisition_ref: Reference
    session: str
    prior_session: str
    return_basis: Literal["completed-close-to-close-fraction"]
    price_basis: Literal["split-adjusted-price", "as-traded-price"]
    session_ref: Reference
    area_ref: Reference
    coverage_reference: Reference

    @field_validator("version", mode="before")
    @classmethod
    def _integer_version(cls, v):
        if type(v) is not int:
            raise ValueError("Expected integer version")
        return v

    @field_validator("retrieved_at", mode="before")
    @classmethod
    def _retrieval(cls, value):
        stamp = value if isinstance(value, datetime) else _stamp(value)
        if not stamp or stamp.utcoffset() != timedelta(0):
            raise ValueError("Expected exact UTC acquisition time")
        return stamp

    @model_validator(mode="after")
    def _sessions(self):
        prior, close = _day(self.prior_session), _day(self.session)
        if not prior or not close or not prior < close <= self.retrieved_at.date():
            raise ValueError("Expected actual ordered close dates no later than acquisition")
        return self


class CapManifest(Manifest):
    mode: Literal["delivered-cap"]
    universe: Literal["delivered-sample"]
    area_basis: Literal["listing-currency-cap"]
    denomination: Currency
    cap_retrieved_at: str
    membership_as_of: None
    weight_valid_for: None
    weight_reference: None
    members: None
    coverage: Literal["sample"]
    rounding_bound_fraction: None
    rounding_reference: None

    @model_validator(mode="after")
    def _common_denomination(self):
        if self.denomination not in CURRENCIES or _stamp(self.cap_retrieved_at) != self.retrieved_at:
            raise ValueError("Expected same-acquisition latest capitalization in a verified currency")
        return self


class IndexManifest(Manifest):
    mode: Literal["index-weight"]
    universe: Reference
    area_basis: Literal["index-weight-fraction"]
    denomination: None
    cap_retrieved_at: None
    membership_as_of: str
    weight_valid_for: str
    weight_reference: Reference
    members: Annotated[list[QualifiedSymbol], Field(min_length=1)]
    coverage: Literal["complete", "partial", "bounded"]
    rounding_bound_fraction: Annotated[float, Field(ge=0, le=1e-4, allow_inf_nan=False)] | None
    rounding_reference: Reference | None

    @model_validator(mode="after")
    def _membership(self):
        if (self.universe == "delivered-sample" or not _day(self.membership_as_of)
                or self.membership_as_of > self.session or self.weight_valid_for != self.session
                or not all(_symbol(member) for member in self.members) or len(set(self.members)) != len(self.members)
                or (self.rounding_bound_fraction is None) != (self.rounding_reference is None)):
            raise ValueError("Expected authoritative dated membership, weight validity and independent precision")
        return self


class _Accounting(ChartSourceReceipt):
    listing_keys: list[QualifiedSymbol]
    exact_repeats: Count
    rejected_count: Count
    excluded_areas: AreaExclusions
    diagnostics: list[Flag]
    unsafe: Unsafe

    @model_validator(mode="after")
    def _counts(self):
        flags = {name for name, active in self.unsafe.model_dump().items() if active}
        accounted = self.delivered_count + self.exact_repeats + self.rejected_count + sum(self.excluded_areas.model_dump().values())
        if (self.raw_count != accounted or self.normalized_count != self.delivered_count
                or self.delivered_count != len(self.listing_keys) or len(set(self.listing_keys)) != self.delivered_count
                or not all(_symbol(k) for k in self.listing_keys) or flags != set(self.diagnostics)
                or (self.exact_repeats and self.raw_count == self.exact_repeats + self.rejected_count)
                or (self.unsafe.duplicate_conflict and self.raw_count < 2)
                or (self.unsafe.excess_weight and not (self.delivered_count or self.rejected_count or self.excluded_areas.invalid))
                or (self.rejected_count and not flags - {"excess_weight"})
                or (flags - {"membership_conflict", "excess_weight"} and not self.rejected_count)):
            raise ValueError("Contradictory coordinates, raw counts or diagnostics")
        return self


class CapSource(CapManifest, _Accounting):
    @model_validator(mode="after")
    def _cap_flags(self):
        if self.unsafe.excess_weight or (self.unsafe.membership_conflict and not self.rejected_count):
            raise ValueError("Contradictory cap sample diagnostics")
        return self


class IndexSource(IndexManifest, _Accounting):
    @model_validator(mode="after")
    def _roster(self):
        if (not set(self.listing_keys).issubset(self.members)
                or (self.coverage != "complete" and self.unsafe.membership_conflict and not self.rejected_count) or
                (self.coverage == "complete" and len(self.listing_keys) != len(self.members) and not self.unsafe.membership_conflict)):
            raise ValueError("Contradictory index roster")
        return self


MarketMapSource = Annotated[CapSource | IndexSource, Field(discriminator="mode")]
_MANIFEST = TypeAdapter(Annotated[CapManifest | IndexManifest, Field(discriminator="mode")])
_SESSION = ("prior_session", "session", "return_basis", "price_basis", "session_ref")
_MEMBERSHIP = ("universe", "membership_as_of", "weight_valid_for", "weight_reference")
_AREA = ("area_basis", "area_ref", "denomination", "cap_retrieved_at")
_FIELDS = {"listing", "sector", "area", "change", "acquisition_ref", *_SESSION, *_MEMBERSHIP, *_AREA}


def _row_issues(source, row, members, key):
    issues = set()
    listing, sector = row.get("listing"), row.get("sector")
    if (row.keys() != _FIELDS or not _symbol(listing) or (key is not None and key != listing)
            or row.get("acquisition_ref") != source.acquisition_ref
            or sector is not None and (not isinstance(sector, str) or not sector or sector.strip() != sector)):
        issues.add("identity_conflict")
    change = row.get("change")
    if ("change" not in row or (change is not None and (not _finite(change) or change < -1))
            or any(row.get(k) != getattr(source, k) for k in _SESSION)):
        issues.add("session_conflict")
    if (any(k not in row or row[k] != getattr(source, k) for k in _MEMBERSHIP)
            or members is not None and (not _symbol(listing) or listing not in members)):
        issues.add("membership_conflict")
    if "denomination" not in row or row["denomination"] != source.denomination:
        issues.add("denomination_conflict")
    if any(k not in row or row[k] != getattr(source, k) for k in ("area_basis", "area_ref", "cap_retrieved_at")):
        issues.add("denomination_conflict" if members is None else "membership_conflict")
    return issues


def prepare_market_map_snapshot(manifest: dict, raw_rows: list | dict):
    """Inspect the complete array or listing-keyed object before discarding any observations."""
    source = _MANIFEST.validate_python(manifest)
    if not isinstance(raw_rows, (list, dict)):
        raise ValueError("Expected the original acquisition")
    members = set(source.members) if isinstance(source, IndexManifest) else None
    pairs = raw_rows.items() if isinstance(raw_rows, dict) else ((None, row) for row in raw_rows)
    seen, accepted, positive_observations = {}, {}, {}
    flags, raw_positive, delivered = set(), [], []
    exclusions = {"zero": 0, "negative": 0, "invalid": 0}
    rejected = repeats = 0
    for key, raw in pairs:
        row = raw if isinstance(raw, dict) else {}
        issues = _row_issues(source, row, members, key)
        listing = row.get("listing")
        # Both the original object key and embedded identity witness conflicts, even on malformed rows.
        for coord in {s for s in (key, listing) if _symbol(s)}:
            if coord in seen:
                prior_key, prior_raw = seen[coord]
                if prior_key != key or not _same_json(prior_raw, raw):
                    flags.add("duplicate_conflict")
            else:
                seen[coord] = (key, raw)
        previous = accepted.get(listing) if _symbol(listing) else None
        same_accepted = previous is not None and previous[0] == key and _same_json(previous[1], row)
        if previous is not None and not same_accepted:
            issues.add("duplicate_conflict")
        area = row.get("area")
        if members is not None and type(area) in (int, float) and area > 0:
            # A single contribution of 2 proves excess. This also handles unbounded JSON integers.
            # Every key in an original object is distinct. In an array collapse full repeats
            # of a qualified observation, including repeats of a rejected row in any order.
            if key is not None or not _symbol(listing):
                raw_positive.append(min(area, 2))
            else:
                variants = positive_observations.setdefault((listing, area), [])
                if not any(_same_json(previous_row, row) for previous_row in variants):
                    variants.append(row)
                    raw_positive.append(min(area, 2))
        if issues:
            flags.update(issues)
            rejected += 1
            continue
        if same_accepted:
            repeats += 1
            continue
        accepted[listing] = (key, row)
        if not _finite(area):
            exclusions["invalid"] += 1
        elif area < 0:
            exclusions["negative"] += 1
        elif area == 0:
            exclusions["zero"] += 1
        else:
            delivered.append(row)

    keys = [row["listing"] for row in delivered]
    if members is not None:
        bound = source.rounding_bound_fraction if source.rounding_bound_fraction is not None else 1e-12
        if fsum(raw_positive) > 1 + bound:
            flags.add("excess_weight")
        if source.coverage == "complete" and (set(keys) != members or fsum(min(row["area"], 2) for row in delivered) < 1 - bound):
            flags.add("membership_conflict")
    receipt_type = IndexSource if members is not None else CapSource
    receipt = receipt_type(**source.model_dump(), raw_count=len(raw_rows), normalized_count=len(keys), delivered_count=len(keys),
        listing_keys=keys, exact_repeats=repeats, rejected_count=rejected, excluded_areas=AreaExclusions(**exclusions),
        diagnostics=sorted(flags), unsafe=Unsafe(**{k: k in flags for k in Unsafe.model_fields}))
    return annotate_chart_sources(delivered, ChartSourceEnvelope[MarketMapSource](receipts=[receipt]))
