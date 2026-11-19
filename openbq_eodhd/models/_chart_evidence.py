# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""Versioned, coordinate-only evidence for three opt-in financial charts."""

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Annotated, Literal, TypeVar

from openbb_core.provider.abstract.annotated_result import AnnotatedResult
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

_QUALIFIED = r"^[A-Z0-9^][A-Z0-9^_/-]*(?:\.[A-Z0-9^_/-]+)*\.[A-Z0-9][A-Z0-9_-]*$"
_CURRENCY = r"^[A-Z]{3}$"
QualifiedSymbol = Annotated[str, Field(pattern=_QUALIFIED, max_length=128)]
Currency = Annotated[str, Field(pattern=_CURRENCY)]
Count = Annotated[int, Field(ge=0)]
Reference = Annotated[str, Field(min_length=1, max_length=256)]
Diagnostic = Literal[
    "unassignable_identity", "unassignable_date", "unit_conflict",
    "invalid_price_change_5d", "missing_price_change_5d", "unknown_denomination",
    "mixed_denomination", "screener_duplicate_conflict", "screener_exact_duplicate",
    "invalid_ex_date", "invalid_amount", "duplicate_ex_date", "currency_conflict",
    "unknown_payment_currency", "instrument_conflict", "amount_basis_conflict",
    "outside_requested_window", "unknown_coverage", "clipped_coverage",
    "unknown_reporting_currency", "unknown_scale", "scale_conflict",
    "invalid_period_identity", "period_identity_conflict", "field_origin_conflict",
    "unknown_record_coverage",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Receipt(_StrictModel):
    version: Literal[1] = 1
    retrieved_at: datetime
    raw_count: Count
    normalized_count: Count = 0
    delivered_count: Count = 0
    diagnostics: list[Diagnostic]

    @model_validator(mode="after")
    def _check_common(self):
        if self.retrieved_at.utcoffset() != timedelta(0):
            raise ValueError("retrieved_at must be in UTC")
        if not self.delivered_count <= self.normalized_count <= self.raw_count:
            raise ValueError("Acquisition counts must be in delivery order")
        return self


class ScreenerFieldOrigins(_StrictModel):
    symbol: Literal["code+exchange"] = "code+exchange"
    exchange: Literal["exchange"] = "exchange"
    currency_symbol: Literal["currency_symbol"] = "currency_symbol"
    last_day_data_date: Literal["last_day_data_date"] = "last_day_data_date"
    market_cap: Literal["market_capitalization"] = "market_capitalization"
    dividend_yield: Literal["dividend_yield"] = "dividend_yield"
    price_change_5d: Literal["refund_5d_p"] = "refund_5d_p"


class ScreenerUnits(_StrictModel):
    market_cap: Literal["listing-currency"] = "listing-currency"
    dividend_yield: Literal["fraction"] = "fraction"
    price_change_5d: Literal["fraction"] = "fraction"


class ScreenerScope(_StrictModel):
    sort: str
    limit: int
    offset: int
    signals: str | None
    filters: list[tuple[
        Literal[
            "exchange", "sector", "industry", "market_capitalization", "adjusted_close",
            "dividend_yield", "avgvol_1d",
        ],
        Literal["=", ">=", "<="],
        str | FiniteFloat,
    ]]


class ScreenerUnsafe(_StrictModel):
    unassignable_identity: bool
    unassignable_date: bool
    unit_conflict: bool


class ScreenerCohort(_StrictModel):
    price_date: date
    raw_count: Count
    denominations: list[Currency]
    unknown_denomination: bool
    conflicting_duplicates: bool
    exact_duplicate_count: Count


class ScreenerEvidence(_Receipt):
    kind: Literal["screener"] = "screener"
    field_origins: ScreenerFieldOrigins = Field(default_factory=ScreenerFieldOrigins)
    units: ScreenerUnits = Field(default_factory=ScreenerUnits)
    scope: ScreenerScope
    unsafe: ScreenerUnsafe
    cohorts: list[ScreenerCohort]


class DividendFieldOrigins(_StrictModel):
    ex_dividend_date: Literal["date"] = "date"
    amount: Literal["value"] = "value"
    currency: Literal["currency"] = "currency"
    frequency: Literal["period"] = "period"


class DividendUnsafe(_StrictModel):
    invalid_ex_date: bool
    currency_conflict: bool
    unknown_payment_currency: bool
    instrument_conflict: bool
    amount_basis_conflict: bool
    outside_requested_window: bool


class UnknownCoverage(_StrictModel):
    status: Literal["unknown"] = "unknown"


class DividendSnapshotCoverage(_StrictModel):
    """Trusted snapshot adapter input, never taken from a route parameter."""

    qualified_symbol: QualifiedSymbol
    start: date
    end: date
    reference: Reference

    @model_validator(mode="after")
    def _check_interval(self):
        if self.start > self.end:
            raise ValueError("Snapshot coverage requires ordered bounds")
        return self


class _KnownCoverage(_StrictModel):
    authority: Literal["explicit-snapshot"] = "explicit-snapshot"
    reference: Reference
    start: date
    end: date


class VerifiedCoverage(_KnownCoverage):
    status: Literal["verified"] = "verified"


class ClippedCoverage(_KnownCoverage):
    status: Literal["clipped"] = "clipped"


class DividendEvidence(_Receipt):
    kind: Literal["dividends"] = "dividends"
    field_origins: DividendFieldOrigins = Field(default_factory=DividendFieldOrigins)
    qualified_symbol: QualifiedSymbol
    amount_basis: Literal["split-adjusted-current-share"] = "split-adjusted-current-share"
    requested_start: date | None
    requested_end: date | None
    coverage: Annotated[
        VerifiedCoverage | ClippedCoverage | UnknownCoverage, Field(discriminator="status")
    ]
    unsafe: DividendUnsafe
    event_dates: list[date]
    invalid_amount_dates: list[date]
    duplicate_dates: list[date]
    payment_currencies: list[Currency]

    @model_validator(mode="after")
    def _check_coverage(self):
        if isinstance(self.coverage, UnknownCoverage):
            return self
        if not self.requested_start or not self.requested_end:
            raise ValueError("Known coverage requires explicit requested bounds")
        if self.requested_start > self.requested_end or self.coverage.start > self.coverage.end:
            raise ValueError("Dividend coverage requires ordered bounds")
        contains_request = (
            self.coverage.start <= self.requested_start <= self.requested_end <= self.coverage.end
        )
        if contains_request != isinstance(self.coverage, VerifiedCoverage):
            raise ValueError("Coverage status must agree with the requested and acquired bounds")
        return self


IncomeField = Literal[
    "revenue", "cost_of_revenue", "gross_profit", "operating_income",
    "income_before_tax", "net_income", "income_tax_expense",
]
PeriodKind = Literal["FY", "FQ"]
FiscalPeriod = Literal["FY", "Q1", "Q2", "Q3", "Q4"]


class IncomeFieldOrigins(_StrictModel):
    period_ending: Literal["date"] = "date"
    reporting_currency: Literal["Financials.Income_Statement.currency_symbol"] = (
        "Financials.Income_Statement.currency_symbol"
    )
    revenue: Literal["totalRevenue"] = "totalRevenue"
    cost_of_revenue: Literal["costOfRevenue"] = "costOfRevenue"
    gross_profit: Literal["grossProfit"] = "grossProfit"
    operating_income: Literal["operatingIncome"] = "operatingIncome"
    income_before_tax: Literal["incomeBeforeTax"] = "incomeBeforeTax"
    net_income: Literal["netIncome"] = "netIncome"
    income_tax_expense: Literal["incomeTaxExpense"] = "incomeTaxExpense"


class IncomeUnsafe(_StrictModel):
    invalid_period_identity: bool
    scale_conflict: bool


class IncomePeriod(_StrictModel):
    period_end: date
    period_kind: PeriodKind
    fiscal_year: int | None
    fiscal_period: FiscalPeriod | None
    record_coverage: Literal["full-record", "unknown"]
    declared_currency: Currency | None
    currency_conflict: bool
    scale_conflict: bool
    identity_conflict: bool
    field_origin_conflict: bool
    present_fields: list[IncomeField]


class ExplicitSnapshotScale(_StrictModel):
    """Monetary units declared by the acquired snapshot source."""

    qualified_symbol: QualifiedSymbol
    multiplier: Annotated[FiniteFloat, Field(gt=0)]
    reference: Reference


class VerifiedScale(_StrictModel):
    status: Literal["verified"] = "verified"
    authority: Literal["explicit-snapshot"] = "explicit-snapshot"
    multiplier: Annotated[FiniteFloat, Field(gt=0)]
    reference: Reference


class UnknownScale(_StrictModel):
    status: Literal["unknown"] = "unknown"


class IncomeEvidence(_Receipt):
    kind: Literal["income"] = "income"
    field_origins: IncomeFieldOrigins = Field(default_factory=IncomeFieldOrigins)
    qualified_symbol: QualifiedSymbol
    period_kind: PeriodKind
    reporting_currency: Currency | None
    scale: Annotated[VerifiedScale | UnknownScale, Field(discriminator="status")]
    requested_limit: Count | None
    available_period_count: Count
    unsafe: IncomeUnsafe
    periods: list[IncomePeriod]


ChartEvidence = Annotated[
    ScreenerEvidence | DividendEvidence | IncomeEvidence, Field(discriminator="kind")
]
R = TypeVar("R")


def annotate(rows: list[R], evidence: ChartEvidence) -> AnnotatedResult[list[R]]:
    """Add delivery counts to the pre-normalization audit."""
    values = evidence.model_dump()
    values.update(normalized_count=len(rows), delivered_count=len(rows))
    receipt = type(evidence).model_validate(values)
    return AnnotatedResult(result=rows, metadata={"chart_evidence": receipt.model_dump(mode="json")})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_date(value: object) -> date | None:
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return None
        if parsed.isoformat() == value:
            return parsed
    return None


def finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if isfinite(number) else None


def currency_code(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(_CURRENCY, value) else None


def optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def screener_identity(code: object, exchange: object) -> str | None:
    if not isinstance(code, str) or not isinstance(exchange, str) or not code or not exchange:
        return None
    symbol = f"{code}.{exchange}".upper()
    return symbol if len(symbol) <= 128 and re.fullmatch(_QUALIFIED, symbol) else None


def dividend_amount(value: object) -> float | None:
    amount = finite_number(value)
    return amount if amount is not None and amount >= 0 else None


def _screener_observation(value: object) -> Decimal | str | None:
    if value is None or value == "":
        return None
    try:
        dec = Decimal(str(value))
        if dec.is_finite():
            return dec
    except InvalidOperation:
        pass
    return repr(value)


def screener_evidence(raw: list[dict], scope: ScreenerScope) -> ScreenerEvidence:
    """Audit raw date cohorts before a record or field can be discarded."""
    diagnostics: set[Diagnostic] = set()
    date_counts: Counter[date] = Counter()
    denominations: defaultdict[date, set[str]] = defaultdict(set)
    unknown: set[date] = set()
    conflicts: set[date] = set()
    duplicates: Counter[date] = Counter()
    observations: defaultdict[tuple[date, str], set[tuple]] = defaultdict(set)
    expected_units = {
        "refund_5d_p_unit": "percent",
        "dividend_yield_unit": "fraction",
        "market_capitalization_unit": "listing-currency",
    }

    for value in raw:
        row = value if isinstance(value, dict) else {}
        code, exchange = row.get("code"), row.get("exchange")
        identity = screener_identity(code, exchange)
        if identity is None:
            diagnostics.add("unassignable_identity")
        day = iso_date(row.get("last_day_data_date"))
        if day is None:
            diagnostics.add("unassignable_date")
        if any(key in row and row[key] != unit for key, unit in expected_units.items()):
            diagnostics.add("unit_conflict")
        change = row.get("refund_5d_p")
        if change is None or change == "":
            diagnostics.add("missing_price_change_5d")
        elif finite_number(change) is None:
            diagnostics.add("invalid_price_change_5d")
        if day is None:
            continue
        date_counts[day] += 1
        symbol = row.get("currency_symbol")
        denomination = (
            "USD" if isinstance(exchange, str) and exchange.upper() == "US" and symbol == "$"
            else currency_code(symbol)
        )
        if denomination:
            denominations[day].add(denomination)
        else:
            unknown.add(day)
        if not identity:
            continue
        observation = (
            denomination,
            symbol if denomination is None and isinstance(symbol, str) else None,
            *(_screener_observation(row.get(key)) for key in (
                "market_capitalization", "dividend_yield", "refund_5d_p",
            )),
        )
        seen = observations[day, identity]
        if observation in seen:
            duplicates[day] += 1
        elif seen:
            conflicts.add(day)
        seen.add(observation)

    if unknown:
        diagnostics.add("unknown_denomination")
    if any(len(values) > 1 for values in denominations.values()):
        diagnostics.add("mixed_denomination")
    if conflicts:
        diagnostics.add("screener_duplicate_conflict")
    if duplicates:
        diagnostics.add("screener_exact_duplicate")
    return ScreenerEvidence(
        retrieved_at=utc_now(), raw_count=len(raw), diagnostics=sorted(diagnostics),
        scope=scope,
        unsafe=ScreenerUnsafe(
            unassignable_identity="unassignable_identity" in diagnostics,
            unassignable_date="unassignable_date" in diagnostics,
            unit_conflict="unit_conflict" in diagnostics,
        ),
        cohorts=[
            ScreenerCohort(
                price_date=day, raw_count=date_counts[day],
                denominations=sorted(denominations[day]), unknown_denomination=day in unknown,
                conflicting_duplicates=day in conflicts, exact_duplicate_count=duplicates[day],
            )
            for day in sorted(date_counts)
        ],
    )


def dividend_evidence(
    raw: list[dict], symbol: str, start: date | None, end: date | None,
    snapshot: DividendSnapshotCoverage | None = None,
    *, retrieved_at: datetime | None = None,
) -> DividendEvidence:
    dates: Counter[date] = Counter()
    invalid_amount_dates: set[date] = set()
    currencies: set[str] = set()
    diagnostics: set[Diagnostic] = set()
    coverage = UnknownCoverage()
    if snapshot:
        if symbol != snapshot.qualified_symbol:
            raise ValueError("Dividend snapshot does not match the acquired instrument")
        if start and end and start <= end:
            bounds = {"start": snapshot.start, "end": snapshot.end, "reference": snapshot.reference}
            if snapshot.start <= start <= end <= snapshot.end:
                coverage = VerifiedCoverage(**bounds)
            else:
                coverage = ClippedCoverage(**bounds)
                diagnostics.add("clipped_coverage")
    if isinstance(coverage, UnknownCoverage):
        diagnostics.add("unknown_coverage")

    for value in raw:
        row = value if isinstance(value, dict) else {}
        day = iso_date(row.get("date"))
        if day is None:
            diagnostics.add("invalid_ex_date")
        else:
            dates[day] += 1
            if dividend_amount(row.get("value")) is None:
                invalid_amount_dates.add(day)
            if (
                (start and day < start) or (end and day > end)
                or (snapshot and not snapshot.start <= day <= snapshot.end)
            ):
                diagnostics.add("outside_requested_window")
        currency = currency_code(row.get("currency"))
        if currency is None:
            diagnostics.add("unknown_payment_currency")
        else:
            currencies.add(currency)
        if row.get("symbol") is not None and row["symbol"] != symbol:
            diagnostics.add("instrument_conflict")
        if row.get("amount_basis", "split-adjusted-current-share") != "split-adjusted-current-share":
            diagnostics.add("amount_basis_conflict")

    duplicate_dates = sorted(day for day, count in dates.items() if count > 1)
    if duplicate_dates:
        diagnostics.add("duplicate_ex_date")
    if invalid_amount_dates:
        diagnostics.add("invalid_amount")
    if len(currencies) > 1:
        diagnostics.add("currency_conflict")
    return DividendEvidence(
        retrieved_at=retrieved_at or utc_now(), raw_count=len(raw), diagnostics=sorted(diagnostics),
        qualified_symbol=symbol, requested_start=start, requested_end=end, coverage=coverage,
        unsafe=DividendUnsafe(
            invalid_ex_date="invalid_ex_date" in diagnostics,
            currency_conflict="currency_conflict" in diagnostics,
            unknown_payment_currency="unknown_payment_currency" in diagnostics,
            instrument_conflict="instrument_conflict" in diagnostics,
            amount_basis_conflict="amount_basis_conflict" in diagnostics,
            outside_requested_window="outside_requested_window" in diagnostics,
        ),
        event_dates=sorted(dates), invalid_amount_dates=sorted(invalid_amount_dates),
        duplicate_dates=duplicate_dates, payment_currencies=sorted(currencies),
    )


def income_evidence(
    section: dict,
    selected: list[tuple[str, dict]],
    available_dates: list[date | None],
    fiscal_labels: dict[date, tuple[int | None, FiscalPeriod | None]],
    *,
    symbol: str,
    period_kind: PeriodKind,
    limit: int | None,
    retrieved_at: datetime,
    snapshot_scale: ExplicitSnapshotScale | None,
    covered_periods: set[date] | None,
    origin_conflicts: set[date],
) -> IncomeEvidence:
    """Preserve the scope and raw facts of selected whole statement records."""
    diagnostics: set[Diagnostic] = set()
    reporting_currency = currency_code(section.get("currency_symbol"))
    if reporting_currency is None:
        diagnostics.add("unknown_reporting_currency")
    if snapshot_scale is not None and snapshot_scale.qualified_symbol != symbol:
        raise ValueError("Income snapshot does not match the acquired issuer")
    scale = (
        UnknownScale() if snapshot_scale is None else VerifiedScale(
            multiplier=snapshot_scale.multiplier, reference=snapshot_scale.reference,
        )
    )
    if isinstance(scale, UnknownScale):
        diagnostics.add("unknown_scale")
    block_scale_conflict = bool(snapshot_scale and "scale_multiplier" in section and
                                finite_number(section["scale_multiplier"]) != snapshot_scale.multiplier)
    if block_scale_conflict:
        diagnostics.add("scale_conflict")

    source_fields = IncomeFieldOrigins().model_dump(exclude={"period_ending", "reporting_currency"})
    date_counts = Counter(available_dates)
    periods = []
    seen = set()
    for key, raw in selected:
        day = iso_date(raw.get("date"))
        if day is None:
            diagnostics.add("invalid_period_identity")
            continue
        if day in seen:
            continue  # The count of all acquired dates already captures this conflict.
        seen.add(day)
        fiscal_year, fiscal_period = fiscal_labels[day]
        period_currency = raw.get("currency_symbol")
        declared_currency = currency_code(period_currency)
        currency_conflict = period_currency not in (None, "") and (
            declared_currency is None or declared_currency != reporting_currency
        )
        scale_conflict = bool(snapshot_scale and "scale_multiplier" in raw and
                              finite_number(raw["scale_multiplier"]) != snapshot_scale.multiplier)
        identity_conflict = (
            iso_date(key) != day or date_counts[day] > 1
            or bool({"period_ending", "fiscal_year", "fiscal_period"} & raw.keys())
        )
        full_record = (
            not identity_conflict and (covered_periods is None or day in covered_periods)
        )
        if currency_conflict:
            diagnostics.add("currency_conflict")
        if scale_conflict:
            diagnostics.add("scale_conflict")
        if identity_conflict:
            diagnostics.add("period_identity_conflict")
        if day in origin_conflicts:
            diagnostics.add("field_origin_conflict")
        if not full_record:
            diagnostics.add("unknown_record_coverage")
        periods.append(IncomePeriod(
            period_end=day, period_kind=period_kind, fiscal_year=fiscal_year,
            fiscal_period=fiscal_period, record_coverage="full-record" if full_record else "unknown",
            declared_currency=declared_currency, currency_conflict=currency_conflict,
            scale_conflict=scale_conflict, identity_conflict=identity_conflict,
            field_origin_conflict=day in origin_conflicts,
            present_fields=sorted(
                field for field, origin in source_fields.items()
                if finite_number(raw.get(origin)) is not None
            ),
        ))
    return IncomeEvidence(
        retrieved_at=retrieved_at, raw_count=len(selected), diagnostics=sorted(diagnostics),
        qualified_symbol=symbol, period_kind=period_kind, reporting_currency=reporting_currency,
        scale=scale, requested_limit=limit, available_period_count=len(available_dates),
        unsafe=IncomeUnsafe(
            invalid_period_identity="invalid_period_identity" in diagnostics,
            scale_conflict=block_scale_conflict,
        ),
        periods=periods,
    )
