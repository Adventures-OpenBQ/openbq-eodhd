# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""EODHD reported quarters against their analyst estimate range (v1.1 Trend + History)."""

import calendar
from datetime import date
from decimal import Decimal
from typing import Any

from openbb_core.provider.abstract.data import Data
from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.abstract.query_params import QueryParams
from pydantic import Field

from openbq_eodhd.models import _fundamentals as F
from openbq_eodhd.models.estimates import _date, _f, _i

_MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}
_TOLERANCE = Decimal("0.10")
_NO_CONSENSUS = (
    "EODHD recorded no consensus for this quarter, so its estimate range can't be checked; "
    "range withheld."
)


def fiscal_label(period_end: date, fiscal_year_end: str) -> str | None:
    """`FQ3 2026` for a period end, given the fiscal year-end month name."""
    end = _MONTHS.get(str(fiscal_year_end or "").strip().title())
    if end is None:
        return None
    quarter = ((period_end.month - end - 1) % 12) // 3 + 1
    year = period_end.year + (1 if period_end.month > end else 0)
    return f"FQ{quarter} {year}"


def band_check(
    eps_avg: float | None, eps_consensus: float | None, has_trend: bool
) -> tuple[str, str | None]:
    """Whether the Trend range may be shown: its average must sit within 10% of the consensus."""
    if not has_trend or eps_avg is None:
        return "no_estimate", None
    if not eps_consensus:
        return "withheld", _NO_CONSENSUS
    # Compare the reported decimals so a nominal 10% difference stays within the band.
    estimate = Decimal(str(eps_avg))
    consensus = Decimal(str(eps_consensus))
    if (
        estimate.is_finite()
        and consensus.is_finite()
        and abs(estimate - consensus) <= abs(consensus) * _TOLERANCE
    ):
        return "ok", None
    return "withheld", (
        "EODHD's estimate range for this quarter doesn't match its recorded consensus "
        f"({eps_avg:.2f} vs {eps_consensus:.2f}); range withheld."
    )


def _pct(v: float | None) -> float | None:
    return None if v is None else round(v * 100, 6)


def _growth(actual: float | None, prior: float | None) -> float | None:
    if actual is None or not prior:
        return None
    # Divide by the size of the prior so a loss shrinking toward a profit reads as growth.
    return (actual - prior) / abs(prior) * 100


def _by_month(section: dict, field: str) -> dict[tuple[int, int], float | None]:
    """Values keyed by (year, month), so quarter ends on varying days still match a year later."""
    out = {}
    for key, row in section.items():
        d = _date(key)
        if d is not None:
            out[(d.year, d.month)] = _f((row or {}).get(field))
    return out


_PERCENT = {"x-widget_config": {"formatterFn": "percent"}}


class EODHDEarningsVsEstimatesQueryParams(QueryParams):
    """EODHD Earnings vs Estimates Query."""
    symbol: str = Field(description="Symbol to get data for.")
    exchange: str = Field(default="US", description="EODHD exchange code for bare symbols.")


class EODHDEarningsVsEstimatesData(Data):
    """One reported quarter beside EODHD's estimate range for it."""
    symbol: str = Field(description="Symbol.")
    period_ending: date = Field(description="Quarter end date.")
    fiscal_period: str | None = Field(default=None, description="Fiscal quarter label, e.g. FQ3 2026.")
    report_date: date | None = Field(default=None, description="Date the quarter was reported.")
    eps_actual: float | None = Field(
        default=None,
        description="Reported EPS as EODHD records it; the basis is not necessarily GAAP diluted.",
    )
    eps_consensus: float | None = Field(default=None, description="EODHD's recorded EPS consensus at report time.")
    eps_low: float | None = Field(default=None, description="Lowest analyst EPS estimate.")
    eps_avg: float | None = Field(default=None, description="Average analyst EPS estimate.")
    eps_high: float | None = Field(default=None, description="Highest analyst EPS estimate.")
    eps_analysts: int | None = Field(default=None, description="Analysts behind the EPS estimate.")
    revenue_actual: float | None = Field(default=None, description="Reported total revenue.")
    revenue_low: float | None = Field(default=None, description="Lowest analyst revenue estimate.")
    revenue_avg: float | None = Field(default=None, description="Average analyst revenue estimate.")
    revenue_high: float | None = Field(default=None, description="Highest analyst revenue estimate.")
    revenue_analysts: int | None = Field(default=None, description="Analysts behind the revenue estimate.")
    eps_growth_avg: float | None = Field(
        default=None, description="Estimated year-over-year EPS growth, percent.", json_schema_extra=_PERCENT)
    eps_growth_actual: float | None = Field(
        default=None, description="Actual year-over-year EPS growth, percent.", json_schema_extra=_PERCENT)
    revenue_growth_avg: float | None = Field(
        default=None, description="Estimated year-over-year revenue growth, percent.", json_schema_extra=_PERCENT)
    revenue_growth_actual: float | None = Field(
        default=None, description="Actual year-over-year revenue growth, percent.", json_schema_extra=_PERCENT)
    band_status: str = Field(description="ok, withheld, or no_estimate.")
    band_note: str | None = Field(default=None, description="Why the estimate range was withheld.")


class EODHDEarningsVsEstimatesFetcher(
    Fetcher[EODHDEarningsVsEstimatesQueryParams, list[EODHDEarningsVsEstimatesData]]
):
    """EODHD earnings history joined to v1.1 quarterly estimates."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDEarningsVsEstimatesQueryParams:
        return EODHDEarningsVsEstimatesQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> dict:  # pylint: disable=unused-argument
        return {
            "earnings": await F.get_earnings_v11(query.symbol, query.exchange, credentials),
            "bundle": await F.get_bundle(query.symbol, query.exchange, credentials),
        }

    @staticmethod
    def transform_data(query, data: dict, **kwargs) -> list[EODHDEarningsVsEstimatesData]:  # pylint: disable=unused-argument
        earnings, bundle = data["earnings"], data["bundle"]
        history = earnings.get("History") or {}
        trend = (earnings.get("Trend") or {}).get("Quarterly") or {}
        income = ((bundle.get("Financials") or {}).get("Income_Statement") or {}).get("quarterly") or {}
        fye = F.general(bundle).get("FiscalYearEnd")

        eps_by_month = _by_month(history, "epsActual")
        rev_by_month = _by_month(income, "totalRevenue")

        rows = []
        for key, h in history.items():
            d, actual = _date(key), _f(h.get("epsActual"))
            if d is None or actual is None:
                continue
            iso, prior = d.isoformat(), (d.year - 1, d.month)
            t = trend.get(iso) or {}
            consensus = _f(h.get("epsEstimate"))
            status, note = band_check(_f(t.get("earningsEstimateAvg")), consensus, bool(t))
            show = t if status == "ok" else {}
            rev = _f((income.get(iso) or {}).get("totalRevenue"))
            rows.append(EODHDEarningsVsEstimatesData(
                symbol=query.symbol.upper(),
                period_ending=d,
                fiscal_period=fiscal_label(d, fye),
                report_date=_date(h.get("reportDate")),
                eps_actual=actual,
                eps_consensus=consensus,
                eps_low=_f(show.get("earningsEstimateLow")),
                eps_avg=_f(show.get("earningsEstimateAvg")),
                eps_high=_f(show.get("earningsEstimateHigh")),
                eps_analysts=_i(show.get("earningsEstimateNumberOfAnalysts")),
                revenue_actual=rev,
                revenue_low=_f(show.get("revenueEstimateLow")),
                revenue_avg=_f(show.get("revenueEstimateAvg")),
                revenue_high=_f(show.get("revenueEstimateHigh")),
                revenue_analysts=_i(show.get("revenueEstimateNumberOfAnalysts")),
                eps_growth_avg=_pct(_f(show.get("earningsEstimateGrowth"))),
                eps_growth_actual=_growth(actual, eps_by_month.get(prior)),
                revenue_growth_avg=_pct(_f(show.get("revenueEstimateGrowth"))),
                revenue_growth_actual=_growth(rev, rev_by_month.get(prior)),
                band_status=status,
                band_note=note,
            ))
        rows.sort(key=lambda r: r.period_ending)
        return rows
