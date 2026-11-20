# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0-only
"""Acquired KPI coordinates and units. Numeric measurements stay exclusively in table rows."""

import re
from datetime import date, datetime
from sys import float_info
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from openbq_eodhd.models import _fundamentals as F
from openbq_eodhd.models._chart_sources import ChartSourceFacts, ChartSourceReceipt

Preset = Literal["price", "market_cap", "dividend_yield", "pe_ratio", "eps_ttm", "revenue_ttm"]
LiveOrigin = Literal["eodhd-key-metrics", "eodhd-financial-ratios"]
Diagnostic = Literal["identity_conflict", "unit_conflict", "period_conflict", "basis_conflict", "duplicate_conflict"]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}$")]
CoordinateId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:+-]{0,127}$")]
Qualified = Annotated[str, Field(max_length=128, pattern=r"^[A-Z0-9^][A-Z0-9^_/-]*(?:\.[A-Z0-9^_/-]+)*\.[A-Z0-9][A-Z0-9_-]*$")]
UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?(?:Z|\+00:00)")
# All origins are physical numeric fields of the supported acquisition schema.
CONTRACT = {
    "price": ("money-per-share", {"as-traded", "split-adjusted"}),
    "market_cap": ("money", {"issuer-all-classes"}),
    "dividend_yield": ("fraction", {"trailing-dividend-per-share"}),
    "pe_ratio": ("multiple", {"trailing-diluted-earnings"}),
    "eps_ttm": ("money-per-share", {"diluted-as-reported", "diluted-split-adjusted"}),
    "revenue_ttm": ("money", {"as-reported"}),
}
HEADLINES = {"dividend_yield": "DividendYield", "pe_ratio": "PERatio"}


# ISO 4217 current and historical denominations, plus EODHD's GBX (British pence).
CURRENCIES = frozenset("""
ADP AED AFA AFN ALK ALL AMD ANG AOA AOK AON AOR ARA ARP ARS ARY ATS AUD AWG AYM AZM AZN BAD BAM BBD BDT BEC
BEF BEL BGJ BGK BGL BGN BHD BIF BMD BND BOB BOP BOV BRB BRC BRE BRL BRN BRR BSD BTN BUK BWP BYB BYN BYR BZD
CAD CDF CHC CHE CHF CHW CLF CLP CNY COP COU CRC CSD CSJ CSK CUC CUP CVE CYP CZK DDM DEM DJF DKK DOP DZD ECS
ECV EEK EGP ERN ESA ESB ESP ETB EUR FIM FJD FKP FRF GBP GBX GEK GEL GHC GHP GHS GIP GMD GNE GNF GNS GQE GRD
GTQ GWE GWP GYD HKD HNL HRD HRK HTG HUF IDR IEP ILP ILR ILS INR IQD IRR ISJ ISK ITL JMD JOD JPY KES KGS KHR
KMF KPW KRW KWD KYD KZT LAJ LAK LBP LKR LRD LSL LSM LTL LTT LUC LUF LUL LVL LVR LYD MAD MDL MGA MGF MKD MLF
MMK MNT MOP MRO MRU MTL MTP MUR MVQ MVR MWK MXN MXP MXV MYR MZE MZM MZN NAD NGN NIC NIO NLG NOK NPR NZD OMR
PAB PEH PEI PEN PES PGK PHP PKR PLN PLZ PTE PYG QAR RHD ROK ROL RON RSD RUB RUR RWF SAR SBD SCR SDD SDG SDP
SEK SGD SHP SIT SKK SLE SLL SOS SRD SRG SSP STD STN SUR SVC SYP SZL THB TJR TJS TMM TMT TND TOP TPE TRL TRY
TTD TWD TZS UAH UAK UGS UGW UGX USD USN USS UYI UYN UYP UYU UYW UZS VEB VED VEF VES VNC VND VUV WST XAD XAF
XCD XCG XDR XEU XFO XFU XOF XPF XRE XSU XUA YDD YER YUD YUM YUN ZAL ZAR ZMK ZMW ZRN ZRZ ZWC ZWD ZWG ZWL ZWN
ZWR
""".split())


class MoneyUnit(ChartSourceFacts):
    kind: Literal["money", "money-per-share"]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]

    @field_validator("currency")
    @classmethod
    def _known_currency(cls, value):
        if value not in CURRENCIES:
            raise ValueError("Expected a known monetary denomination")
        return value


class ScalarUnit(ChartSourceFacts):
    kind: Literal["count", "fraction", "multiple"]


class MetricUnsafe(ChartSourceFacts):
    identity_conflict: bool
    unit_conflict: bool
    period_conflict: bool
    basis_conflict: bool
    duplicate_conflict: bool


def utc(value: str) -> datetime:
    if not UTC.fullmatch(value):
        raise ValueError("Expected a qualified UTC timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def day(value: str) -> date:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Expected a calendar date")
    return date.fromisoformat(value)


class MetricObservation(ChartSourceFacts):
    observation_id: CoordinateId
    vintage: str
    origin: Preset
    unit: MoneyUnit | ScalarUnit
    as_of: str | None
    as_of_unknown: bool
    period_start: str | None
    period_end: str | None
    period_kind: Literal["FY", "FQ", "TTM"] | None
    adjustment_basis: str | None
    reporting_basis: Literal["US-GAAP", "IFRS", "local-GAAP"] | None
    basis_reference: CoordinateId | None

    @model_validator(mode="after")
    def _consistent_measure(self):
        vintage = utc(self.vintage)
        if self.as_of_unknown != (self.as_of is None):
            raise ValueError("Contradictory measurement date")
        if self.as_of is not None:
            if (day(self.as_of) > vintage.date() if len(self.as_of) == 10 else utc(self.as_of) > vintage):
                raise ValueError("Measurement later than vintage")
        kind, bases = CONTRACT[self.origin]
        if self.unit.kind != kind or (self.adjustment_basis is not None and self.adjustment_basis not in bases):
            raise ValueError("Contradictory metric unit or basis")
        if self.basis_reference is not None and self.adjustment_basis is None:
            raise ValueError("Basis reference requires its named basis")
        if self.origin in ("eps_ttm", "revenue_ttm"):
            if (self.period_kind != "TTM" or not self.period_start or not self.period_end
                    or not self.adjustment_basis or not self.reporting_basis):
                raise ValueError("Expected actual TTM reporting period and reporting basis")
            start, end = day(self.period_start), day(self.period_end)
            if ((end - start).days + 1 not in (364, 365, 366, 371)
                    or end > day((self.as_of or self.vintage)[:10])):
                raise ValueError("Contradictory trailing reporting period")
        else:
            period = "TTM" if self.origin in HEADLINES else None
            if (self.period_kind != period or self.period_start is not None or self.period_end is not None
                    or self.reporting_basis is not None):
                raise ValueError("Unexpected period metadata for this preset")
        return self


class MetricAcquisitions(MetricObservation):
    additional_observations: list[MetricObservation]


class MetricSource(ChartSourceReceipt):
    kind: Literal["metric"]
    origin: Literal["acquired-snapshot", "eodhd-key-metrics", "eodhd-financial-ratios"]
    issuer: Qualified
    listing: Qualified
    share_class: Identifier | None
    diagnostics: list[Diagnostic]
    unsafe: MetricUnsafe
    metrics: dict[Preset, MetricAcquisitions] = Field(min_length=1)

    @field_validator("retrieved_at", mode="before")
    @classmethod
    def _qualified_time(cls, value):
        return utc(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _check_acquisitions(self):
        flags = self.unsafe.model_dump()
        if any(flag != (name in self.diagnostics) for name, flag in flags.items()):
            raise ValueError("Metric diagnostics contradict unsafe facts")
        if self.normalized_count != self.delivered_count or (self.raw_count > self.normalized_count and not self.diagnostics):
            raise ValueError("Missing normalized rows or unexplained raw drops")
        live = self.origin != "acquired-snapshot"
        if live and (not self.issuer.endswith(".US") or self.issuer != self.listing or self.share_class is not None
                     or self.raw_count != 1 or self.delivered_count != 1 or set(self.metrics) - HEADLINES.keys()
                     or set(self.diagnostics) - {"unit_conflict"}):
            raise ValueError("Unexpected live metric acquisition facts")
        ids, coordinates = set(), set()
        for metric, first in self.metrics.items():
            for observation in (first, *first.additional_observations):
                vintage = utc(observation.vintage)
                event = observation.as_of
                if event is not None and len(event) > 10:
                    event = utc(event)
                coordinate = (metric, vintage, event, observation.period_start, observation.period_end)
                if (observation.origin != metric or vintage > self.retrieved_at or observation.observation_id in ids
                        or coordinate in coordinates):
                    raise ValueError("Ambiguous or inconsistent metric acquisition")
                ids.add(observation.observation_id)
                coordinates.add(coordinate)
                if live and (first.additional_observations or vintage != self.retrieved_at
                             or observation.observation_id != f"{metric}:{observation.vintage}"
                             or observation.as_of is not None or observation.adjustment_basis is not None):
                    raise ValueError("Unverified live metric measurement or basis")
        if not live and len(ids) != self.delivered_count:
            raise ValueError("Expected one observation per delivered local row")
        return self


def headline_source(query, data, origin: LiveOrigin):
    """Read the two named raw Highlights fields before normalizing the original wide row."""
    if not isinstance(data, F.FundamentalsAcquisition):
        return data, None, {}
    bundle, retrieved_at = data.payload, data.retrieved_at
    listing = F.qualify(query.symbol, query.exchange)
    h = bundle.get("Highlights")
    if (not query.chart_source or getattr(query, "limit", None) is not None or retrieved_at is None
            or not F.primary_us_common_stock(bundle, listing, query.exchange)
            or not isinstance(h, dict) or not any(field in h for field in HEADLINES.values())):
        return bundle, None, {}
    vintage = retrieved_at.isoformat().replace("+00:00", "Z")
    measures, values = {}, {"symbol": listing, "metric_vintage": vintage}
    invalid = False
    for name, field in HEADLINES.items():
        if field not in h:
            continue
        value = h[field]
        if value is not None and (type(value) not in (int, float) or not -float_info.max <= value <= float_info.max):
            invalid = True
            value = None
        values[name] = value
        measures[name] = MetricAcquisitions(
            origin=name, unit=ScalarUnit(kind=CONTRACT[name][0]), vintage=vintage, observation_id=f"{name}:{vintage}",
            as_of=None, as_of_unknown=True, period_start=None, period_end=None, period_kind="TTM",
            adjustment_basis=None, reporting_basis=None, basis_reference=None, additional_observations=[],
        )
    receipt = MetricSource(
        kind="metric", origin=origin, issuer=listing, listing=listing, share_class=None, retrieved_at=retrieved_at,
        raw_count=1, normalized_count=1, delivered_count=1, diagnostics=["unit_conflict"] if invalid else [],
        unsafe=MetricUnsafe(identity_conflict=False, unit_conflict=invalid, period_conflict=False,
                            basis_conflict=False, duplicate_conflict=False), metrics=measures,
    )
    return bundle, receipt, values
