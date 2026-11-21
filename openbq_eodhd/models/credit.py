# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""EODHD credit and sovereign risk (/credit-risk/...).

RiskPremium: Damodaran's annual country and equity risk premiums, 157
countries (/credit-risk/sovereign/risk-premium).
HighQualityMarketCorporateBond: the US Treasury HQM corporate yield curve,
monthly, par and spot, tenors 1 to 30 years (/credit-risk/corporate/hqm-yields).

EODHD sends the premiums as fractions (0.0708) and the HQM yields as percents
(5.92). The standard RiskPremium fields are plain percents, so premiums are
multiplied by 100; the HQM rate goes through `stored_percent`.
"""

from datetime import date as dateType, timedelta
from typing import Any, Literal

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.high_quality_market import (
    HighQualityMarketCorporateBondData, HighQualityMarketCorporateBondQueryParams,
)
from openbb_core.provider.standard_models.risk_premium import (
    RiskPremiumData, RiskPremiumQueryParams,
)
from openbb_core.provider.utils.errors import EmptyDataError
from pydantic import Field

from openbq_eodhd.models._jsonapi import fetch_rows, stored_percent

_PAGE = 100


def _pct(fraction) -> float | None:
    return None if fraction is None else round(float(fraction) * 100, 6)


class EODHDRiskPremiumQueryParams(RiskPremiumQueryParams):
    """EODHD Risk Premium Query."""

    country: str | None = Field(
        default=None, description="Country name or ISO alpha-3 code, e.g. Germany or DEU."
    )


class EODHDRiskPremiumData(RiskPremiumData):
    """EODHD Risk Premium Data. Premiums and spreads are percents."""

    country_iso3: str | None = Field(default=None, description="ISO alpha-3 country code.")
    date: dateType | None = Field(default=None, description="As-of date of the annual update.")
    moodys_rating: str | None = Field(default=None, description="Moody's sovereign rating.")
    adjusted_default_spread: float | None = Field(
        default=None, description="Rating-based default spread, in percent."
    )
    sovereign_cds: float | None = Field(
        default=None, description="Sovereign CDS spread, in percent, when one trades."
    )
    corporate_tax_rate: float | None = Field(
        default=None, description="Marginal corporate tax rate, in percent."
    )


class EODHDRiskPremiumFetcher(Fetcher[EODHDRiskPremiumQueryParams, list[EODHDRiskPremiumData]]):
    """EODHD country and equity risk premiums (Damodaran)."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDRiskPremiumQueryParams:
        return EODHDRiskPremiumQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        from asyncio import to_thread

        return await to_thread(
            fetch_rows, credentials, "credit-risk/sovereign/risk-premium",
            {"filter[country]": query.country}, "sovereign risk premiums",
            page_limit=_PAGE, max_pages=5,
        )

    @staticmethod
    def transform_data(query, data: list[dict], **kwargs) -> list[EODHDRiskPremiumData]:  # pylint: disable=unused-argument
        rows = []
        for it in data:
            if not it.get("country_name"):
                continue
            crp = _pct(it.get("country_risk_premium"))
            erp = _pct(it.get("equity_risk_premium"))
            rows.append(EODHDRiskPremiumData.model_validate({
                "country": it["country_name"],
                "continent": it.get("region"),
                # the standard fields are constrained (> 0 and >= 0)
                "total_equity_risk_premium": erp if erp and erp > 0 else None,
                "country_risk_premium": crp if crp is not None and crp >= 0 else None,
                "country_iso3": it.get("country_iso3"),
                "date": (it.get("as_of_date") or "")[:10] or None,
                "moodys_rating": it.get("moodys_rating"),
                "adjusted_default_spread": _pct(it.get("adj_default_spread")),
                "sovereign_cds": _pct(it.get("sovereign_cds")),
                "corporate_tax_rate": _pct(it.get("corporate_tax_rate")),
            }))
        if not rows:
            raise EmptyDataError("EODHD returned no usable risk-premium rows.")
        return sorted(rows, key=lambda r: r.country)


class EODHDHighQualityMarketCorporateBondQueryParams(HighQualityMarketCorporateBondQueryParams):
    """EODHD HQM Corporate Bond Query. `date` is one month or several, comma-joined."""

    yield_curve: Literal["par", "spot"] = Field(default="spot", description="The yield curve type.")


class EODHDHighQualityMarketCorporateBondData(HighQualityMarketCorporateBondData):
    """EODHD HQM Corporate Bond Data."""

    yield_curve: str | None = Field(default=None, description="par or spot.")
    series_id: str | None = Field(default=None, description="FRED series the value comes from.")


class EODHDHighQualityMarketCorporateBondFetcher(
    Fetcher[
        EODHDHighQualityMarketCorporateBondQueryParams,
        list[EODHDHighQualityMarketCorporateBondData],
    ]
):
    """EODHD HQM corporate bond yield curve."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDHighQualityMarketCorporateBondQueryParams:
        return EODHDHighQualityMarketCorporateBondQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        from asyncio import to_thread

        wanted = _wanted_dates(query)
        # The series is monthly, dated the first of the month. Ask from the
        # month before the earliest wanted date so "as of" has a row to land on.
        start = (min(wanted) - timedelta(days=62)) if wanted else dateType.today() - timedelta(days=120)
        return await to_thread(
            fetch_rows, credentials, "credit-risk/corporate/hqm-yields",
            {
                "filter[type]": query.yield_curve,
                "filter[from]": str(start),
                "filter[to]": str(max(wanted)) if wanted else None,
            },
            "HQM corporate yields", page_limit=_PAGE, max_pages=20,
        )

    @staticmethod
    def transform_data(query, data: list[dict], **kwargs):  # pylint: disable=unused-argument
        model = EODHDHighQualityMarketCorporateBondData
        by_date: dict[str, list[dict]] = {}
        for it in data:
            d = (it.get("as_of_date") or "")[:10]
            if d and it.get("yield_value") is not None and it.get("tenor_years") is not None:
                by_date.setdefault(d, []).append(it)
        if not by_date:
            raise EmptyDataError("EODHD returned no usable HQM yields.")
        available = sorted(by_date)
        wanted = [str(d) for d in _wanted_dates(query)] or [available[-1]]
        rows = []
        for want in wanted:
            at_or_before = [d for d in available if d <= want]
            if not at_or_before:
                continue
            chosen = at_or_before[-1]
            for it in sorted(by_date[chosen], key=lambda x: float(x["tenor_years"])):
                tenor = float(it["tenor_years"])
                rows.append(model.model_validate({
                    "date": chosen,
                    "rate": stored_percent(model, "rate", it["yield_value"]),
                    "maturity": f"year_{int(tenor) if tenor.is_integer() else tenor}",
                    "yield_curve": it.get("yield_type"),
                    "series_id": it.get("series_id"),
                }))
        if not rows:
            raise EmptyDataError("No HQM yields at or before the requested date(s).")
        return rows


def _wanted_dates(query) -> list[dateType]:
    if not query.date:
        return []
    parts = [p.strip() for p in str(query.date).split(",") if p.strip()]
    return sorted(dateType.fromisoformat(p) for p in parts)
