# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""EODHD Fundamental (financial statement) models.

Maps EODHD's `/api/fundamentals` payload onto OpenBB's three standard statement
models: income statement, balance sheet, and cash-flow statement. Well-known
line items are mapped to OpenBB's conventional field names; every other EODHD
field is passed through snake_cased (the standard models allow extra fields).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from openbb_core.provider.abstract.annotated_result import AnnotatedResult
from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.balance_sheet import (
    BalanceSheetData,
    BalanceSheetQueryParams,
)
from openbb_core.provider.standard_models.cash_flow import (
    CashFlowStatementData,
    CashFlowStatementQueryParams,
)
from openbb_core.provider.standard_models.income_statement import (
    IncomeStatementData,
    IncomeStatementQueryParams,
)
from openbb_core.provider.utils.errors import EmptyDataError, UnauthorizedError
from pydantic import Field, create_model

from openbq_eodhd.aggregation import attach_widget_hints
from openbq_eodhd.models._chart_evidence import (
    ExplicitSnapshotScale, FiscalPeriod, annotate, finite_number, income_evidence, iso_date,
    utc_now,
)

# EODHD field -> OpenBB conventional field name. Unmapped fields pass through
# snake_cased, so these maps only need the commonly-consumed line items.
INCOME_MAP = {
    "totalRevenue": "revenue",
    "costOfRevenue": "cost_of_revenue",
    "grossProfit": "gross_profit",
    "researchDevelopment": "research_and_development_expense",
    "sellingGeneralAdministrative": "selling_general_and_administrative_expense",
    "totalOperatingExpenses": "total_operating_expenses",
    "operatingIncome": "operating_income",
    "ebit": "ebit",
    "ebitda": "ebitda",
    "depreciationAndAmortization": "depreciation_and_amortization",
    "interestExpense": "interest_expense",
    "interestIncome": "interest_income",
    "incomeBeforeTax": "income_before_tax",
    "incomeTaxExpense": "income_tax_expense",
    "netIncome": "net_income",
    "netIncomeApplicableToCommonShares": "net_income_to_common",
}
BALANCE_MAP = {
    "totalAssets": "total_assets",
    "totalCurrentAssets": "total_current_assets",
    "cashAndEquivalents": "cash_and_cash_equivalents",
    "cashAndShortTermInvestments": "cash_and_short_term_investments",
    "netReceivables": "net_receivables",
    "inventory": "inventory",
    "propertyPlantAndEquipmentNet": "property_plant_equipment_net",
    "goodWill": "goodwill",
    "intangibleAssets": "intangible_assets",
    "totalLiab": "total_liabilities",
    "totalCurrentLiabilities": "total_current_liabilities",
    "accountsPayable": "accounts_payable",
    "shortTermDebt": "short_term_debt",
    "longTermDebt": "long_term_debt",
    "shortLongTermDebtTotal": "total_debt",
    "netDebt": "net_debt",
    "totalStockholderEquity": "total_shareholders_equity",
    "retainedEarnings": "retained_earnings",
    "commonStock": "common_stock",
    "commonStockSharesOutstanding": "common_shares_outstanding",
}
CASHFLOW_MAP = {
    "totalCashFromOperatingActivities": "net_cash_from_operating_activities",
    "totalCashflowsFromInvestingActivities": "net_cash_from_investing_activities",
    "totalCashFromFinancingActivities": "net_cash_from_financing_activities",
    "capitalExpenditures": "capital_expenditure",
    "freeCashFlow": "free_cash_flow",
    "depreciation": "depreciation_and_amortization",
    "stockBasedCompensation": "stock_based_compensation",
    "dividendsPaid": "dividends_paid",
    "netIncome": "net_income",
    "changeInCash": "net_change_in_cash",
    "beginPeriodCashFlow": "cash_at_beginning_of_period",
    "endPeriodCashFlow": "cash_at_end_of_period",
}

# Keys handled explicitly (date/meta) — excluded from numeric passthrough.
_META_KEYS = {"date", "filing_date", "currency_symbol"}
_PERIOD_COORDINATES = {"period_ending", "fiscal_year", "fiscal_period"}
_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(name: str) -> str:
    """camelCase -> snake_case."""
    return _CAMEL.sub("_", name).lower()


def _num(value: Any) -> Any:
    """Coerce EODHD's (often string) numerics to float, or None."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value  # non-numeric passthrough (kept as-is)


_MONTHS = {
    name: number
    for number, name in enumerate(
        ("january", "february", "march", "april", "may", "june",
         "july", "august", "september", "october", "november", "december"),
        start=1,
    )
}


def _fy_end_month(fiscal_year_end: Any) -> int | None:
    """EODHD's ``General.FiscalYearEnd`` ("September") -> 9; None when absent or unrecognised."""
    if not isinstance(fiscal_year_end, str):
        return None
    return _MONTHS.get(fiscal_year_end.strip().lower())


def _fiscal(end: date, period: str, fy_end_month: int | None) -> tuple[int, FiscalPeriod]:
    """(fiscal_year, fiscal_period) for a period-ending date.

    The fiscal year is named for the calendar year it ends in; quarters count
    from the month after the year-end month. An unknown year-end (None) is the
    calendar year -- exactly what every row said before 9.6.2.
    """
    m = fy_end_month or 12
    year = end.year + (1 if end.month > m else 0)
    quarter = ((end.month - m - 1) % 12) // 3 + 1
    return year, "FY" if period == "annual" else ("Q1", "Q2", "Q3", "Q4")[quarter - 1]


def _chart_fiscal(
    end: date, period: str, fy_end_month: int | None,
) -> tuple[int | None, FiscalPeriod | None]:
    if period == "quarter" and fy_end_month is None:
        return None, None
    return _fiscal(end, period, fy_end_month)


def _transform(
    section_data: dict, period: str, limit: int | None, field_map: dict, fiscal_year_end: int | None = None
) -> list[dict]:
    """Turn one EODHD statement section into standard-model row dicts."""
    bucket = section_data.get("yearly" if period == "annual" else "quarterly") or {}
    # EODHD keys the entries by period-ending date; newest first.
    entries = sorted(bucket.values(), key=lambda e: e.get("date", ""), reverse=True)
    if limit:
        entries = entries[:limit]
    rows = _normalize_statement_entries(entries, period, field_map, fiscal_year_end)
    if not rows:
        raise EmptyDataError("The request was returned empty.")
    return rows


def _normalize_statement_entries(
    entries: list[dict], period: str, field_map: dict, fiscal_year_end: int | None,
    *, chart_source: bool = False,
) -> list[dict]:
    from pandas import isna, to_datetime

    mapped_lines = set(field_map.values())
    rows: list[dict] = []
    for entry in entries:
        # Skip entries whose period-ending date is missing/unparseable.
        if chart_source:
            end = iso_date(entry.get("date"))
            if end is None:
                continue
            fiscal_year, fiscal_period = _chart_fiscal(end, period, fiscal_year_end)
        else:
            end_ts = to_datetime(entry.get("date"), errors="coerce")
            if isna(end_ts):
                continue
            end = end_ts.date()
            fiscal_year, fiscal_period = _fiscal(end, period, fiscal_year_end)
        row: dict[str, Any] = {
            "period_ending": end,
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
        }
        for key, value in entry.items():
            if key in _META_KEYS:
                continue
            line_id = field_map.get(key, _snake(key))
            if chart_source:
                if line_id in _PERIOD_COORDINATES or key == "scale_multiplier":
                    continue
                if line_id in mapped_lines:
                    if key in field_map:
                        row[line_id] = finite_number(value)
                    continue  # A passthrough field cannot overwrite a canonical line.
                normalized = _num(value)
                row[line_id] = finite_number(normalized) if isinstance(normalized, float) else normalized
            else:
                row[line_id] = _num(value)
        rows.append(row)
    return rows


async def _extract(section: str, field_map: dict, query, credentials) -> list[dict]:
    """Shared extract: read the section from the shared /fundamentals bundle, then map to standard rows."""
    # pylint: disable=import-outside-toplevel
    from openbq_eodhd.models._fundamentals import general, get_bundle

    bundle = await get_bundle(query.symbol, query.exchange, credentials)
    financials = bundle.get("Financials") or {}
    data = financials.get(section) or {}
    # EODHD's statement entries carry only a period-ending date; the issuer's
    # fiscal calendar is in the same bundle's General section.
    return _transform(data, query.period, query.limit, field_map, _fy_end_month(general(bundle).get("FiscalYearEnd")))


# --- Query params: add period + exchange to each standard statement query ------
class EODHDIncomeStatementQueryParams(IncomeStatementQueryParams):
    """EODHD Income Statement Query."""

    # Core reads this dunder directly for choices; keep it (not model_config).
    __json_schema_extra__ = {"period": {"choices": ["annual", "quarter"]}}
    period: Literal["annual", "quarter"] = Field(default="annual", description="Reporting period.")
    exchange: str = Field(default="US", description="EODHD exchange code for bare symbols (e.g. 'US').")
    chart_source: bool = Field(default=False, description="Include financial chart acquisition evidence.")


class EODHDBalanceSheetQueryParams(BalanceSheetQueryParams):
    """EODHD Balance Sheet Query."""

    # Core reads this dunder directly for choices; keep it (not model_config).
    __json_schema_extra__ = {"period": {"choices": ["annual", "quarter"]}}
    period: Literal["annual", "quarter"] = Field(default="annual", description="Reporting period.")
    exchange: str = Field(default="US", description="EODHD exchange code for bare symbols (e.g. 'US').")


class EODHDCashFlowStatementQueryParams(CashFlowStatementQueryParams):
    """EODHD Cash Flow Statement Query."""

    # Core reads this dunder directly for choices; keep it (not model_config).
    __json_schema_extra__ = {"period": {"choices": ["annual", "quarter"]}}
    period: Literal["annual", "quarter"] = Field(default="annual", description="Reporting period.")
    exchange: str = Field(default="US", description="EODHD exchange code for bare symbols (e.g. 'US').")


# --- Data models: the standard statement plus EODHD's mapped line items --------
#
# OpenBB's standard statement models declare only period_ending, fiscal_period
# and fiscal_year; every line item is a passthrough extra. That leaves
# widgets.json with no columnsDefs for `revenue` and friends, and no column to
# hang an aggregation hint on. These subclasses declare the mapped line items
# -- generated from the same maps the transform uses, so a map entry and its
# column cannot disagree -- as optional floats, and keep `extra="allow"` for
# EODHD's unmapped passthrough fields. Their docstrings start with "EODHD": the
# widget generator scopes a schema to a provider by that prefix.
#
# attach_widget_hints then stamps every field with its dictionary hint (see
# openbq_eodhd.aggregation, which reads this module's maps at call time rather
# than import time, so the two modules can each import the other's names).
# Validation is unchanged: the same rows, the same values, now also typed.


def _statement_model(name: str, base: type, doc: str, field_map: dict[str, str]) -> type:
    line_items = {
        normalized: (float | None, Field(default=None, description=f"EODHD `{raw}`."))
        for raw, normalized in field_map.items()
    }
    return create_model(name, __base__=base, __doc__=doc, **line_items)


EODHDIncomeStatementData = attach_widget_hints(
    _statement_model("EODHDIncomeStatementData", IncomeStatementData, "EODHD Income Statement Data.", INCOME_MAP),
    "IncomeStatement",
)
EODHDBalanceSheetData = attach_widget_hints(
    _statement_model("EODHDBalanceSheetData", BalanceSheetData, "EODHD Balance Sheet Data.", BALANCE_MAP),
    "BalanceSheet",
)
EODHDCashFlowStatementData = attach_widget_hints(
    _statement_model("EODHDCashFlowStatementData", CashFlowStatementData, "EODHD Cash Flow Statement Data.", CASHFLOW_MAP),
    "CashFlowStatement",
)


@dataclass(frozen=True)
class _IncomeAcquisition:
    section: dict
    fiscal_year_end: int | None
    retrieved_at: datetime


def _annotated_income(
    query: EODHDIncomeStatementQueryParams, acquisition: _IncomeAcquisition,
    *, scale: ExplicitSnapshotScale | None = None, covered_periods: set[date] | None = None,
) -> AnnotatedResult[list[EODHDIncomeStatementData]]:
    from openbq_eodhd.models._fundamentals import qualify

    bucket = acquisition.section.get("yearly" if query.period == "annual" else "quarterly")
    if not isinstance(bucket, dict):
        raise UnauthorizedError("EODHD (Income_Statement): expected an object of period records.")
    entries = [
        (iso_date(key), entry if isinstance(entry, dict) else {}) for key, entry in bucket.items()
    ]
    # The bucket keys order the upstream periods. A malformed body must not
    # move a known recent period below the requested limit.
    entries.sort(key=lambda item: (item[0] is not None, item[0] or date.min), reverse=True)
    selected = entries[:query.limit] if query.limit else entries
    fiscal_labels = {
        day: _chart_fiscal(day, query.period, acquisition.fiscal_year_end)
        for _, raw in selected if (day := iso_date(raw.get("date"))) is not None
    }
    mapped_lines = set(INCOME_MAP.values())
    origin_conflicts = {
        day for _, raw in selected
        if (day := iso_date(raw.get("date"))) is not None
        and any(key not in INCOME_MAP and _snake(key) in mapped_lines for key in raw)
    }
    evidence = income_evidence(
        acquisition.section, selected, entries, fiscal_labels,
        symbol=qualify(query.symbol, query.exchange),
        period_kind="FY" if query.period == "annual" else "FQ",
        limit=query.limit, retrieved_at=acquisition.retrieved_at,
        snapshot_scale=scale, covered_periods=covered_periods, origin_conflicts=origin_conflicts,
    )
    rows = _normalize_statement_entries(
        [raw for _, raw in selected], query.period, INCOME_MAP, acquisition.fiscal_year_end,
        chart_source=True,
    )
    return annotate([EODHDIncomeStatementData.model_validate(row) for row in rows], evidence)


def normalize_income_snapshot(
    query: EODHDIncomeStatementQueryParams, section: dict, scale: ExplicitSnapshotScale,
    *, covered_periods: set[date], fiscal_year_end: int | None = None,
    retrieved_at: datetime | None = None,
) -> AnnotatedResult[list[EODHDIncomeStatementData]]:
    """Normalize explicit snapshot periods with independently declared monetary scale."""
    acquisition = _IncomeAcquisition(section, fiscal_year_end, retrieved_at or utc_now())
    return _annotated_income(query, acquisition, scale=scale, covered_periods=covered_periods)


# --- Fetchers ------------------------------------------------------------------
class EODHDIncomeStatementFetcher(
    Fetcher[EODHDIncomeStatementQueryParams, list[EODHDIncomeStatementData]]
):
    """EODHD income statement."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDIncomeStatementQueryParams:  # pylint: disable=unused-argument
        return EODHDIncomeStatementQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict] | _IncomeAcquisition:
        if not query.chart_source:
            return await _extract("Income_Statement", INCOME_MAP, query, credentials)

        from openbq_eodhd.models._fundamentals import get_bundle

        try:
            bundle = await get_bundle(query.symbol, query.exchange, credentials)
        except EmptyDataError:
            # The shared transport uses EmptyDataError for an empty or
            # non-object fundamentals payload. Neither establishes a period bucket.
            raise UnauthorizedError("EODHD (Income_Statement): expected a fundamentals object.") from None
        if not isinstance(bundle, dict):
            raise UnauthorizedError("EODHD (Income_Statement): expected a fundamentals object.")
        if any(bundle.get(key) for key in ("error", "errors", "message")):
            raise UnauthorizedError("EODHD returned a fundamentals error.")
        financials = bundle.get("Financials")
        if not isinstance(financials, dict):
            raise UnauthorizedError("EODHD (Income_Statement): expected a Financials object.")
        section = financials.get("Income_Statement")
        if not isinstance(section, dict):
            raise UnauthorizedError("EODHD (Income_Statement): expected a statement object.")
        general = bundle.get("General")
        if general is not None and not isinstance(general, dict):
            raise UnauthorizedError("EODHD (Income_Statement): expected a General object.")
        return _IncomeAcquisition(
            section, _fy_end_month((general or {}).get("FiscalYearEnd")), utc_now(),
        )

    @staticmethod
    def transform_data(
        query, data: list[dict] | _IncomeAcquisition, **kwargs,
    ) -> list[EODHDIncomeStatementData] | AnnotatedResult[list[EODHDIncomeStatementData]]:
        if isinstance(data, _IncomeAcquisition):
            return _annotated_income(query, data)
        return [EODHDIncomeStatementData.model_validate(r) for r in data]


class EODHDBalanceSheetFetcher(
    Fetcher[EODHDBalanceSheetQueryParams, list[EODHDBalanceSheetData]]
):
    """EODHD balance sheet."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDBalanceSheetQueryParams:  # pylint: disable=unused-argument
        return EODHDBalanceSheetQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        return await _extract("Balance_Sheet", BALANCE_MAP, query, credentials)

    @staticmethod
    def transform_data(query, data: list[dict], **kwargs) -> list[EODHDBalanceSheetData]:  # pylint: disable=unused-argument
        return [EODHDBalanceSheetData.model_validate(r) for r in data]


class EODHDCashFlowStatementFetcher(
    Fetcher[EODHDCashFlowStatementQueryParams, list[EODHDCashFlowStatementData]]
):
    """EODHD cash-flow statement."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDCashFlowStatementQueryParams:  # pylint: disable=unused-argument
        return EODHDCashFlowStatementQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        return await _extract("Cash_Flow", CASHFLOW_MAP, query, credentials)

    @staticmethod
    def transform_data(query, data: list[dict], **kwargs) -> list[EODHDCashFlowStatementData]:  # pylint: disable=unused-argument
        return [EODHDCashFlowStatementData.model_validate(r) for r in data]
