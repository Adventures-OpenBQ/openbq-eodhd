# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""Validate a single explicitly acquired long-form snapshot. No fetcher uses this contract."""

import re
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from math import isfinite
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_validator, model_validator

from openbq_eodhd.models._chart_evidence import Currency, QualifiedSymbol
from openbq_eodhd.models._metric_chart_source import CURRENCIES
from openbq_eodhd.models._chart_sources import (
    ChartSourceEnvelope, ChartSourceFacts, ChartSourceReceipt, annotate_chart_sources,
)


_REFERENCE = r"^[A-Za-z][A-Za-z0-9._:/-]{0,255}$"
_MISSING = {"na", "unknown", "none", "null", "missing", "unspecified", "unavailable", "notprovided", "notavailable"}


def _has_reference(value):
    return (isinstance(value, str) and re.fullmatch(_REFERENCE, value) is not None
            and re.sub(r"[^a-z0-9]", "", value.lower()) not in _MISSING)


def _reference(value: str) -> str:
    if not _has_reference(value):
        raise ValueError("Missing acquired reference")
    return value


Reference = Annotated[str, Field(pattern=_REFERENCE), AfterValidator(_reference)]
Diagnostic = Literal["identity_conflict", "unit_conflict", "coordinate_conflict", "basis_conflict", "untraced_omission"]
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?(?:Z|\+00:00)")


def _stamp(value):
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _coordinate(value, vintage, *, allow_time):
    day = _day(value)
    if day:
        return day if day <= vintage.date() else None
    stamp = _stamp(value) if allow_time else None
    return stamp if stamp and stamp <= vintage else None


class OverlayUnsafe(ChartSourceFacts):
    identity_conflict: bool
    unit_conflict: bool
    coordinate_conflict: bool
    basis_conflict: bool
    untraced_omission: bool


class SeriesFact(ChartSourceFacts):
    id: Literal["adjusted_close", "reported_diluted_eps", "CUSR0000SA0", "DGS10"]
    unit: Literal["money-per-share", "index", "yield-fraction"]
    currency: Currency | None
    basis: Literal["splits-and-dividends-adjusted-close", "current-split-adjusted-diluted-fq",
                   "monthly-sa-us-cpi-u-all-items-1982-84=100", "daily-nominal-us-ten-year-constant-maturity"]
    coordinate: Literal["observation-date", "availability-date"]
    period_kind: Literal["FQ"] | None
    restated: bool


class Series(ChartSourceFacts):
    left: SeriesFact
    right: SeriesFact


class Identity(ChartSourceFacts):
    issuer: Reference
    listing: QualifiedSymbol
    share_basis_ref: Reference

    @field_validator("listing")
    @classmethod
    def _qualified_listing(cls, value):
        if re.fullmatch(r"[0-9]+\.[0-9]+(?:E[+-]?[0-9]+)?", value):
            raise ValueError("Expected qualified listing")
        return value


class Preparation(ChartSourceFacts):
    input_refs: Annotated[list[Reference], Field(min_length=2, max_length=2)]
    reference: Reference

    @model_validator(mode="after")
    def _separate_references(self):
        if len(set([*self.input_refs, self.reference])) != 3:
            raise ValueError("Identify the prepared acquisition and its two distinct inputs")
        return self


class Vintages(ChartSourceFacts):
    left: str
    right: str


class Coordinates(ChartSourceFacts):
    left: Annotated[list[str], Field(min_length=1)]
    right: Annotated[list[str], Field(min_length=1)]


# Source ID, normalized unit, basis, plotting coordinate, fiscal kind, acquired input contract.
_DEFINITIONS = {
    "price-eps": (
        ("adjusted_close", "money-per-share", "splits-and-dividends-adjusted-close", "observation-date", None, "eodhd-eod-adjusted-close"),
        ("reported_diluted_eps", "money-per-share", "current-split-adjusted-diluted-fq", "availability-date", "FQ", "issuer-reported-diluted-fq-eps"),
    ),
    "cpi-ten-year": (
        ("CUSR0000SA0", "index", "monthly-sa-us-cpi-u-all-items-1982-84=100", "observation-date", None, "BLS:CUSR0000SA0/FRED:CPIAUCSL"),
        ("DGS10", "yield-fraction", "daily-nominal-us-ten-year-constant-maturity", "observation-date", None, "Fed:H15/FRED:DGS10"),
    ),
}


class OverlayManifest(ChartSourceFacts):
    version: Literal[1]
    kind: Literal["overlay"]
    origin: Literal["acquired-snapshot"]
    retrieved_at: datetime
    recipe: Literal["price-eps", "cpi-ten-year"]
    series: Series
    identity: Identity | None
    preparation: Preparation
    vintages: Vintages

    @field_validator("retrieved_at", mode="before")
    @classmethod
    def _utc(cls, value):
        stamp = value if isinstance(value, datetime) else _stamp(value)
        if stamp is None or stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError("Expected exact UTC snapshot vintage")
        return stamp

    @field_validator("version", mode="before")
    @classmethod
    def _version(cls, value):
        if type(value) is not int:
            raise ValueError("Expected integer version")
        return value

    @model_validator(mode="after")
    def _pair(self):
        equity = self.recipe == "price-eps"
        currency = self.series.left.currency
        if (equity != (self.identity is not None) or equity != (currency is not None)
                or (equity and currency not in CURRENCIES) or self.series.right.currency != currency):
            raise ValueError("Contradictory equity identity or denomination")
        for side, definition in zip(("left", "right"), _DEFINITIONS[self.recipe], strict=True):
            series = getattr(self.series, side)
            vintage = _stamp(getattr(self.vintages, side))
            if (not vintage or vintage > self.retrieved_at or
                    (series.id, series.unit, series.basis, series.coordinate, series.period_kind) != definition[:5]
                    or (series.restated and series.id != "reported_diluted_eps")):
                raise ValueError("Unproven series definition or acquisition vintage")
        return self


class OverlaySource(OverlayManifest, ChartSourceReceipt):
    coordinates: Coordinates
    diagnostics: list[Diagnostic]
    unsafe: OverlayUnsafe

    @model_validator(mode="after")
    def _consistent(self):
        if (self.normalized_count != self.delivered_count or
                self.delivered_count != len(self.coordinates.left) + len(self.coordinates.right)
                or set(self.diagnostics) != {k for k, v in self.unsafe.model_dump().items() if v}
                or (self.raw_count > self.normalized_count) != bool(self.diagnostics)):
            raise ValueError("Contradictory raw, delivered or diagnostic counts")
        for side in ("left", "right"):
            series = getattr(self.series, side)
            coords = getattr(self.coordinates, side)
            vintage = _stamp(getattr(self.vintages, side))
            parsed = {_coordinate(c, vintage, allow_time=series.coordinate == "availability-date") for c in coords}
            if (None in parsed or len(parsed) != len(coords)
                    or (series.id == "CUSR0000SA0" and any(c[8:] != "01" for c in coords))):
                raise ValueError("Invalid, duplicate or post-vintage series coordinate")
        return self


_ROW_FIELDS = set("series date value source_value unit currency basis period_kind restated issuer listing share_basis_ref "
                  "source_contract input_ref input_vintage available_at release_ref session_timing target_start target_end".split())


def _finite(value):
    if type(value) not in (float, int):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def _supported_quarter(start, end):
    if not start or not end:
        return False
    days = (end - start).days + 1
    if days in (13 * 7, 14 * 7):
        return True
    # Clamp the exclusive three-month anniversary to its target month's last day.
    months = range(start.month - 1, start.month + 3)
    lengths = [monthrange(start.year + month // 12, month % 12 + 1)[1] for month in months]
    return days == sum(lengths[:3]) - max(0, start.day - lengths[3])


def _row_diagnostics(manifest, side, index, row):
    facts = getattr(manifest.series, side)
    eps = facts.id == "reported_diluted_eps"
    identity = manifest.identity
    flags = set()
    if (row.get("input_ref") != manifest.preparation.input_refs[index]
            or row.get("input_vintage") != getattr(manifest.vintages, side)
            or row.get("issuer") != (identity.issuer if identity else None)
            or row.get("listing") != (identity.listing if identity else None)):
        flags.add("identity_conflict")
    if (row.get("share_basis_ref") != (identity.share_basis_ref if identity else None)
            or row.get("source_contract") != _DEFINITIONS[manifest.recipe][index][5]
            or row.get("basis") != facts.basis or row.get("period_kind") != facts.period_kind
            or row.get("restated") is not facts.restated):
        flags.add("basis_conflict")
    raw, value = row.get("source_value"), row.get("value")
    valid_value = raw is None and value is None
    if _finite(raw) and _finite(value):
        if facts.id == "DGS10":
            # A prepared decimal 0.0411 and binary 4.11 / 100 are both lossless unit conversions.
            valid_value = value in (raw / 100, float(Decimal(str(raw)).scaleb(-2)))
            valid_value &= raw == 0 or value != 0
        else:
            valid_value = value == raw and (facts.id not in ("CUSR0000SA0", "adjusted_close") or value > 0)
    if row.get("currency") != facts.currency or row.get("unit") != facts.unit or not valid_value:
        flags.add("unit_conflict")
    vintage = _stamp(getattr(manifest.vintages, side))
    day = _coordinate(row.get("date"), vintage, allow_time=False)
    release = row.get("available_at")
    available = _coordinate(release, vintage, allow_time=True)
    release_day = available.date() if isinstance(available, datetime) else available
    if (not day or (release is None and row.get("release_ref") is not None)
            or (release is not None and (not available or not _has_reference(row.get("release_ref")) or
                                         (day and release_day < day)))):
        flags.add("coordinate_conflict")
    if facts.id == "CUSR0000SA0" and day:
        month = (day.year, day.month)
        observed = raw is not None or value is not None
        if (day.day != 1 or (observed and (vintage.year, vintage.month) <= month)
                or (release_day and (release_day.year, release_day.month) <= month)):
            flags.add("coordinate_conflict")
    if eps:
        start, end = _day(row.get("target_start")), _day(row.get("target_end"))
        if (not _supported_quarter(start, end) or end != day or not available
                or row.get("session_timing") not in (None, "before-market", "after-market", "during-market")):
            flags.add("coordinate_conflict")
    elif any(row.get(k) is not None for k in ("target_start", "target_end", "session_timing")):
        flags.add("coordinate_conflict")
    if row.keys() != _ROW_FIELDS:
        flags.add("untraced_omission")
    return flags


def prepare_overlay_snapshot(manifest: dict, raw_rows: list):
    """Validate all rows from this one prepared input before excluding contradictory records.

    The preparer supplies a source manifest and original long-form records together. Hidden
    losses from an earlier preparation must never be passed as a new clean acquisition.
    """
    source = OverlayManifest.model_validate(manifest)
    if not isinstance(raw_rows, list):
        raise ValueError("Expected the complete acquired row array")
    flags = set()
    seen = set()
    coords = {"left": [], "right": []}
    delivered = []
    sides = {source.series.left.id: ("left", 0), source.series.right.id: ("right", 1)}
    for row in raw_rows:
        if not isinstance(row, dict) or not isinstance(row.get("series"), str) or row["series"] not in sides:
            flags.update(("identity_conflict", "untraced_omission"))
            continue
        series = row["series"]
        side, index = sides[series]
        issues = _row_diagnostics(source, side, index, row)
        eps = series == "reported_diluted_eps"
        coordinate = row.get("available_at" if eps else "date")
        # Observe both report identity and fiscal target even on a raw row excluded for unit/basis conflicts.
        keys = {(series, "coordinate", _stamp(coordinate) or _day(coordinate))}
        if eps:
            keys.add((series, "target", _day(row.get("target_end"))))
        keys = {key for key in keys if key[2] is not None}
        if seen.intersection(keys):
            issues.add("coordinate_conflict")
        seen.update(keys)
        flags.update(issues)
        if not issues:
            coords[side].append(coordinate)
            delivered.append(row)

    receipt = OverlaySource(**source.model_dump(), raw_count=len(raw_rows), normalized_count=len(delivered),
        delivered_count=len(delivered), coordinates=Coordinates(**coords),
        diagnostics=sorted(flags), unsafe=OverlayUnsafe(**{k: k in flags for k in OverlayUnsafe.model_fields}))
    return annotate_chart_sources(delivered, ChartSourceEnvelope[OverlaySource](receipts=[receipt]))
