# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""EODHD house price index (/real-estate/{code}) — BIS residential property prices.

Quarterly series per country, as an index (2010 = 100) or year-over-year
change, nominal or real. Rows are {period: "2024-Q3", value, type, metric};
the period becomes the quarter's last day. Year-over-year values are percents
and stay percents, as the standard model's `value` carries no unit tag.
"""

from datetime import date as dateType
from typing import Any, Literal

from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.house_price_index import (
    HousePriceIndexData, HousePriceIndexQueryParams,
)
from openbb_core.provider.utils.errors import EmptyDataError
from pydantic import Field

from openbq_eodhd.models._jsonapi import fetch_rows

_PAGE = 500
_QUARTER_END = {"1": (3, 31), "2": (6, 30), "3": (9, 30), "4": (12, 31)}


def quarter_end(period: str | None) -> dateType | None:
    """'2024-Q3' -> 2024-09-30; None for anything else."""
    try:
        year, quarter = str(period).split("-Q")
        month, day = _QUARTER_END[quarter]
        return dateType(int(year), month, day)
    except (ValueError, KeyError):
        return None


def _period(d: dateType | None) -> str | None:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}" if d else None


class EODHDHousePriceIndexQueryParams(HousePriceIndexQueryParams):
    """EODHD House Price Index Query. Quarterly data only."""

    country: str = Field(
        default="united_states",
        description="Country name (united_states) or ISO alpha-2 code (US).",
    )
    frequency: Literal["quarter"] = Field(
        default="quarter", description="The BIS series is quarterly."
    )
    transform: Literal["index", "yoy"] = Field(
        default="index", description="index (2010 = 100) or yoy (year-over-year change, percent)."
    )
    price_basis: Literal["nominal", "real"] = Field(
        default="nominal", description="Nominal prices, or real (inflation-adjusted)."
    )


class EODHDHousePriceIndexData(HousePriceIndexData):
    """EODHD House Price Index Data."""


class EODHDHousePriceIndexFetcher(
    Fetcher[EODHDHousePriceIndexQueryParams, list[EODHDHousePriceIndexData]]
):
    """EODHD house price index (BIS)."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDHousePriceIndexQueryParams:
        return EODHDHousePriceIndexQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> dict:  # pylint: disable=unused-argument
        from asyncio import to_thread

        def _sync():
            wanted = query.country.strip()
            code, name = wanted.upper(), None
            if len(wanted) != 2:
                countries = fetch_rows(
                    credentials, "real-estate/countries", {}, "real-estate countries",
                    page_limit=_PAGE, max_pages=2,
                )
                key = wanted.lower().replace("_", " ")
                match = next(
                    (c for c in countries if str(c.get("name", "")).lower() == key), None
                )
                if match is None:
                    raise OpenBBError(
                        f"EODHD has no house price series for '{query.country}'."
                        " Pass an ISO alpha-2 code or a country name."
                    )
                code, name = match["code"], match.get("name")
            rows = fetch_rows(
                credentials, f"real-estate/{code}",
                {
                    "filter[type]": query.price_basis,
                    "filter[metric]": query.transform,
                    "filter[from]": _period(query.start_date),
                    "filter[to]": _period(query.end_date),
                },
                "house price index", page_limit=_PAGE, max_pages=4,
            )
            return {"country": name or code, "rows": rows}

        return await to_thread(_sync)

    @staticmethod
    def transform_data(query, data: dict, **kwargs):  # pylint: disable=unused-argument
        rows = []
        for it in data.get("rows") or []:
            d = quarter_end(it.get("period"))
            if d is None or it.get("value") is None:
                continue
            rows.append(EODHDHousePriceIndexData.model_validate({
                "date": d, "country": data.get("country"), "value": it["value"],
            }))
        if not rows:
            raise EmptyDataError("EODHD returned no usable house price rows.")
        return sorted(rows, key=lambda r: r.date)
