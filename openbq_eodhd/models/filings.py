# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""EODHD SEC filings and XBRL segments (/sec-filings, /sec-companies).

CompanyFilings: 10-K, 10-Q and 8-K filings for a US company. EODHD returns
the accession number but no link, so the report URL is built from the CIK
(one extra call to the filing overview) and the accession number, pointing at
the filing index on SEC EDGAR.

RevenueBusinessLine / RevenueGeographic: XBRL revenue segments by product or
service and by geography (/sec-companies/{symbol}/segments).
"""

from datetime import date as dateType
from typing import Any, Literal

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.company_filings import (
    CompanyFilingsData, CompanyFilingsQueryParams,
)
from openbb_core.provider.standard_models.revenue_business_line import (
    RevenueBusinessLineData, RevenueBusinessLineQueryParams,
)
from openbb_core.provider.standard_models.revenue_geographic import (
    RevenueGeographicData, RevenueGeographicQueryParams,
)
from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.provider.utils.errors import EmptyDataError
from pydantic import Field

from openbq_eodhd.models._jsonapi import fetch_rows

_FORM_PATH = {"10-K": "10k", "10-Q": "10q", "8-K": "8k"}
_PAGE = 100


def _us_symbol(symbol: str | None) -> str:
    if not symbol:
        raise OpenBBError("EODHD SEC data needs a symbol.")
    first = symbol.split(",")[0].strip().upper()
    return first if first.endswith(".US") else f"{first.split('.')[0]}.US"


def edgar_index_url(cik: str | None, accession: str | None) -> str | None:
    """The filing's index page on SEC EDGAR, or None without both parts."""
    if not cik or not accession:
        return None
    digits = str(cik).lstrip("0") or "0"
    return (
        f"https://www.sec.gov/Archives/edgar/data/{digits}/"
        f"{accession.replace('-', '')}/{accession}-index.htm"
    )


class EODHDCompanyFilingsQueryParams(CompanyFilingsQueryParams):
    """EODHD Company Filings Query. US companies only."""

    form_type: Literal["10-K", "10-Q", "8-K"] = Field(
        default="10-K", description="The filing type to list."
    )
    limit: int | None = Field(default=20, description="Number of filings, newest first.", ge=1)


class EODHDCompanyFilingsData(CompanyFilingsData):
    """EODHD Company Filings Data. Financial fields come from the filing's XBRL (10-K, 10-Q)."""

    accession_number: str | None = Field(default=None, description="SEC accession number.")
    period_ending: dateType | None = Field(default=None, description="Period of report.")
    items: str | None = Field(default=None, description="8-K item numbers, comma-joined.")
    revenue: float | None = Field(default=None, description="Revenue reported in the filing.")
    net_income: float | None = Field(default=None, description="Net income reported in the filing.")
    eps_diluted: float | None = Field(default=None, description="Diluted EPS reported in the filing.")
    total_assets: float | None = Field(default=None, description="Total assets reported in the filing.")
    operating_cash_flow: float | None = Field(
        default=None, description="Operating cash flow reported in the filing."
    )


class EODHDCompanyFilingsFetcher(
    Fetcher[EODHDCompanyFilingsQueryParams, list[EODHDCompanyFilingsData]]
):
    """EODHD SEC filings (10-K, 10-Q, 8-K)."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDCompanyFilingsQueryParams:
        return EODHDCompanyFilingsQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> dict:  # pylint: disable=unused-argument
        from asyncio import to_thread

        symbol = _us_symbol(query.symbol)
        limit = query.limit or 20

        def _sync():
            overview = fetch_rows(credentials, f"sec-filings/{symbol}", {}, "SEC filing overview")
            filings = fetch_rows(
                credentials, f"sec-filings/{symbol}/{_FORM_PATH[query.form_type]}", {},
                f"{query.form_type} filings",
                page_limit=min(limit, _PAGE), max_pages=-(-limit // _PAGE),
            )
            return {"cik": (overview[0] or {}).get("cik"), "filings": filings[:limit]}

        return await to_thread(_sync)

    @staticmethod
    def transform_data(query, data: dict, **kwargs) -> list[EODHDCompanyFilingsData]:  # pylint: disable=unused-argument
        rows = []
        for it in data.get("filings") or []:
            url = edgar_index_url(data.get("cik"), it.get("accession_number"))
            if not it.get("filed_at") or not url:  # both are required by the standard model
                continue
            rows.append(EODHDCompanyFilingsData.model_validate({
                "filing_date": it["filed_at"],
                "report_type": query.form_type,
                "report_url": url,
                "accession_number": it.get("accession_number"),
                "period_ending": it.get("period_of_report"),
                "items": ", ".join(it["items"]) if it.get("items") else None,
                "revenue": it.get("revenue"),
                "net_income": it.get("net_income"),
                "eps_diluted": it.get("eps_diluted"),
                "total_assets": it.get("total_assets"),
                "operating_cash_flow": it.get("operating_cash_flow"),
            }))
        if not rows:
            raise EmptyDataError("EODHD returned no usable filings.")
        return rows


async def _fetch_segments(query, credentials) -> list[dict]:
    from asyncio import to_thread

    return await to_thread(
        fetch_rows, credentials, f"sec-companies/{_us_symbol(query.symbol)}/segments", {},
        "revenue segments", page_limit=_PAGE, max_pages=10,
    )


def _segment_rows(data: list[dict], axis: str, label: str) -> list[dict]:
    rows = []
    for it in data:
        if it.get("axis") != axis or it.get("value") is None or not it.get("period_end"):
            continue
        rows.append({
            "period_ending": it["period_end"],
            "fiscal_year": int(str(it["period_end"])[:4]),
            label: it.get("member"),
            "revenue": it["value"],
            "currency": it.get("unit"),
            "concept": it.get("concept"),
        })
    if not rows:
        raise EmptyDataError(f"EODHD returned no {axis} revenue segments.")
    return sorted(rows, key=lambda r: (r["period_ending"], -r["revenue"]))


class EODHDRevenueBusinessLineQueryParams(RevenueBusinessLineQueryParams):
    """EODHD Revenue By Business Line Query. US companies only."""


class EODHDRevenueBusinessLineData(RevenueBusinessLineData):
    """EODHD Revenue By Business Line Data.

    Members are the filing's own XBRL members, so a total (Product) can sit
    beside its parts (IPhone, Mac).
    """

    currency: str | None = Field(default=None, description="Reporting unit, e.g. USD.")
    concept: str | None = Field(default=None, description="XBRL concept the value was tagged with.")


class EODHDRevenueBusinessLineFetcher(
    Fetcher[EODHDRevenueBusinessLineQueryParams, list[EODHDRevenueBusinessLineData]]
):
    """EODHD revenue by product or service."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDRevenueBusinessLineQueryParams:
        return EODHDRevenueBusinessLineQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        return await _fetch_segments(query, credentials)

    @staticmethod
    def transform_data(query, data: list[dict], **kwargs):  # pylint: disable=unused-argument
        return [
            EODHDRevenueBusinessLineData.model_validate(r)
            for r in _segment_rows(data, "ProductOrService", "business_line")
        ]


class EODHDRevenueGeographicQueryParams(RevenueGeographicQueryParams):
    """EODHD Revenue By Geography Query. US companies only."""


class EODHDRevenueGeographicData(RevenueGeographicData):
    """EODHD Revenue By Geography Data."""

    currency: str | None = Field(default=None, description="Reporting unit, e.g. USD.")
    concept: str | None = Field(default=None, description="XBRL concept the value was tagged with.")


class EODHDRevenueGeographicFetcher(
    Fetcher[EODHDRevenueGeographicQueryParams, list[EODHDRevenueGeographicData]]
):
    """EODHD revenue by geography."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDRevenueGeographicQueryParams:
        return EODHDRevenueGeographicQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        return await _fetch_segments(query, credentials)

    @staticmethod
    def transform_data(query, data: list[dict], **kwargs):  # pylint: disable=unused-argument
        return [
            EODHDRevenueGeographicData.model_validate(r)
            for r in _segment_rows(data, "Geographic", "region")
        ]
