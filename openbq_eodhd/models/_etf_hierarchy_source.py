# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""Acquired holdings from a single ETF_Data.Holdings map, on a fund-assets basis."""

from datetime import date, datetime
from math import fsum, isfinite
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, ValidationInfo, field_validator, model_validator

from openbq_eodhd.models._chart_evidence import QualifiedSymbol
from openbq_eodhd.models._chart_sources import ChartSourceFacts, ChartSourceReceipt

Diagnostic = Literal["fund_conflict", "basis_conflict", "duplicate_conflict", "classification_conflict",
                     "negative_weight", "excess_weight", "exact_duplicate"]


def _reference(value: str) -> str:
    if re.sub(r"[^a-z0-9]", "", value.lower()) in {
        "na", "none", "null", "unknown", "unavailable", "unspecified", "missing", "notavailable", "notprovided",
    }:
        raise ValueError("An acquired authority reference must identify a source")
    return value


Reference = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9._:/-]{0,255}$"), AfterValidator(_reference)]
_SYMBOL = re.compile(r"[A-Z0-9^][A-Z0-9^_/-]*(?:\.[A-Z0-9^_/-]+)*\.[A-Z0-9][A-Z0-9_-]*")
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?(?:Z|\+00:00)")


class EtfUnsafe(ChartSourceFacts):
    fund_conflict: bool
    basis_conflict: bool
    duplicate_conflict: bool
    classification_conflict: bool
    negative_weight: bool
    excess_weight: bool


class MissingUnknown(ChartSourceFacts):
    status: Literal["unknown"]


class MissingVerified(ChartSourceFacts):
    status: Literal["verified"]
    reference: Reference
    fraction: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]


def _symbol(value):
    return (isinstance(value, str) and len(value) <= 128 and _SYMBOL.fullmatch(value)
            and not re.fullmatch(r"[0-9]+\.[0-9]+(?:E[+-]?[0-9]+)?", value))


class _EtfSource(ChartSourceReceipt):
    version: Literal[1]
    kind: Literal["etf-hierarchy"]
    fund: QualifiedSymbol
    snapshot: str
    as_of: date | None
    weight_basis: Literal["fund-assets-fraction"]
    coverage: Literal["complete", "bounded", "partial", "sample", "unknown"]
    coverage_reference: Reference | None
    rounding_bound_fraction: Annotated[float, Field(ge=0, le=0.0001, allow_inf_nan=False)] | None
    rounding_reference: Reference | None
    missing_mass: MissingUnknown | MissingVerified
    diagnostics: list[Diagnostic]
    unsafe: EtfUnsafe
    holding_keys: list[QualifiedSymbol]

    @field_validator("retrieved_at", mode="before")
    @classmethod
    def _utc_stamp(cls, value, info: ValidationInfo):
        if info.mode == "json":
            if not isinstance(value, str) or not _STAMP.fullmatch(value):
                raise ValueError("Expected the UTC acquisition timestamp")
            return datetime.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _consistent(self):
        if (not _symbol(self.fund) or not all(_symbol(k) for k in self.holding_keys)
                or not self.delivered_count or self.normalized_count != self.delivered_count
                or len(set(self.holding_keys)) != self.delivered_count or len(self.holding_keys) != self.delivered_count):
            raise ValueError("ETF hierarchy requires unique delivered, qualified holding coordinates")
        if (any(flag != (key in self.diagnostics) for key, flag in self.unsafe.model_dump().items())
                or (self.raw_count > self.normalized_count and not set(self.diagnostics) - {"excess_weight"})
                or ("exact_duplicate" in self.diagnostics and self.raw_count == self.normalized_count)):
            raise ValueError("Contradictory ETF raw diagnostics or counts")
        if ((self.coverage == "unknown") != (self.coverage_reference is None)
                or (self.rounding_bound_fraction is None) != (self.rounding_reference is None)
                or (isinstance(self.missing_mass, MissingVerified) and self.coverage not in ("bounded", "partial", "sample"))):
            raise ValueError("Contradictory ETF scope, precision or missing-mass evidence")
        if self.as_of and self.as_of > self.retrieved_at.date():
            raise ValueError("Holdings report date exceeds acquisition vintage")
        if self.snapshot != (self.as_of.isoformat() if self.as_of else None):
            if not _STAMP.fullmatch(self.snapshot) or datetime.fromisoformat(self.snapshot) != self.retrieved_at:
                raise ValueError("Snapshot must identify the acquired vintage or report date")
        return self


class EtfLiveSource(_EtfSource):
    origin: Literal["eodhd-etf-holdings"]
    as_of: None
    coverage: Literal["unknown"]
    coverage_reference: None
    rounding_bound_fraction: None
    rounding_reference: None
    missing_mass: MissingUnknown

    @model_validator(mode="after")
    def _live_identity(self):
        if not self.fund.endswith(".US"):
            raise ValueError("The live holdings source requires a qualified US fund")
        return self


class EtfSnapshotSource(_EtfSource):
    origin: Literal["acquired-snapshot"]


EtfHierarchySource = EtfLiveSource | EtfSnapshotSource


def _fund_matches(general, fund, exchange):
    return (isinstance(general, dict) and _symbol(fund) and exchange.upper() == "US" and fund.endswith(".US")
            and general.get("Code") == fund[:-3] and general.get("Type") == "ETF" and general.get("CountryISO") == "US"
            and general.get("Exchange") in ("NASDAQ", "NYSE", "NYSE ARCA", "BATS")
            and general.get("PrimaryTicker") in (None, fund))


def holding_hierarchy(bundle: dict, fund: str, exchange: str, retrieved_at: datetime | None):
    """Inspect every raw identity, classification and amount before normalizing or dropping a row."""
    etf = bundle.get("ETF_Data")
    holdings = etf.get("Holdings") if isinstance(etf, dict) else None
    if (retrieved_at is None or not _fund_matches(bundle.get("General"), fund, exchange)
            or not isinstance(holdings, dict) or not holdings):
        return None
    snapshot = retrieved_at.isoformat().replace("+00:00", "Z")
    flags = dict.fromkeys(EtfUnsafe.model_fields, False)
    seen = {}
    exact_duplicate = False
    positive = []
    rows = []
    for key, holding in holdings.items():
        if not isinstance(holding, dict):
            flags["fund_conflict"] = flags["basis_conflict"] = True
            continue
        symbol = key.upper() if isinstance(key, str) else None
        code, venue = holding.get("Code"), holding.get("Exchange")
        qualified = f"{code}.{venue}".upper() if isinstance(code, str) and isinstance(venue, str) else None
        identity_ok = bool(_symbol(symbol) and symbol == qualified)
        flags["fund_conflict"] |= not identity_ok
        raw = holding.get("Assets_%")
        numeric = type(raw) in (float, int) and -1e308 <= raw <= 1e308 and isfinite(raw)
        weight = raw / 100 if numeric else None
        valid_weight = numeric and (raw == 0 or weight != 0)
        flags["basis_conflict"] |= not valid_weight
        flags["negative_weight"] |= numeric and raw < 0
        sector = holding.get("Sector")
        valid_sector = sector is None or isinstance(sector, str)
        flags["classification_conflict"] |= not valid_sector

        # Track both the qualified map key and the embedded coordinate, including contradictory rows.
        coordinates = {s for s in (symbol, qualified) if _symbol(s)}
        duplicate = False
        for coordinate in coordinates:
            previous = seen.get(coordinate)
            if previous is not None:
                duplicate = True
                if previous != holding or not identity_ok:
                    flags["duplicate_conflict"] = True
                if previous.get("Sector") != sector:
                    flags["classification_conflict"] = True
        if duplicate and identity_ok and seen.get(symbol) == holding:
            exact_duplicate = True
            continue
        for coordinate in coordinates:
            seen.setdefault(coordinate, holding)
        if numeric and raw > 0:
            positive.append(min(weight, 2.0))
        if duplicate or not identity_ok or not valid_weight or not valid_sector or weight < 0:
            continue
        rows.append({"fund_symbol": fund, "snapshot": snapshot, "symbol": symbol, "weight": weight,
                     "weight_basis": "fund-assets", "weight_unit": "fraction", "sector": (sector or "").strip() or None,
                     **{name.lower(): holding.get(name) for name in ("Name", "Industry", "Country", "Region")}})
    flags["excess_weight"] = fsum(positive) > 1 + 1e-12
    if not rows:
        return None
    rows.sort(key=lambda row: row["weight"], reverse=True)
    return rows, EtfLiveSource(
        version=1, kind="etf-hierarchy", origin="eodhd-etf-holdings", fund=fund, retrieved_at=retrieved_at,
        snapshot=snapshot, as_of=None, weight_basis="fund-assets-fraction", coverage="unknown", coverage_reference=None,
        rounding_bound_fraction=None, rounding_reference=None, missing_mass=MissingUnknown(status="unknown"),
        raw_count=len(holdings), normalized_count=len(rows), delivered_count=len(rows),
        diagnostics=[key for key, flag in flags.items() if flag] + (["exact_duplicate"] if exact_duplicate else []),
        unsafe=EtfUnsafe(**flags), holding_keys=[row["symbol"] for row in rows],
    )
