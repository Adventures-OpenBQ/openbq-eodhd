# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""EODHD commodity spot prices (/commodities/historical/{code}).

One series per request: {"meta": {name, unit, interval}, "data": [{date,
value}]}, newest first, 1,000 rows a page. These are spot reference series
(for example WTI at Cushing), not futures contracts, and there are no
precious metals: GOLD, SILVER and PLATINUM answer 404 although EODHD's
OpenAPI description lists them (probed 2026-11-18).

The endpoint ignores `from`/`to` (probed the same day: the total is the same
with and without them), so the window is applied here and paging stops at the
first page that reaches back past the start date.
"""

from datetime import date as dateType, timedelta
from typing import Any, Literal

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.commodity_spot_prices import (
    CommoditySpotPricesData, CommoditySpotPricesQueryParams,
)
from openbb_core.provider.utils.errors import EmptyDataError, UnauthorizedError
from pydantic import Field, field_validator

from openbq_eodhd.models._client import rest_json, sdk_call

CODES = (
    "WTI", "BRENT", "NATURAL_GAS", "COAL_AU", "URANIUM", "COPPER", "ALUMINUM",
    "WHEAT", "CORN", "SUGAR", "COTTON", "ALL_COMMODITIES",
)

_PAGE = 1000
_MAX_PAGES = 15
_DEFAULT_DAYS = 365 * 5


class EODHDCommoditySpotPricesQueryParams(CommoditySpotPricesQueryParams):
    """EODHD Commodity Spot Prices Query. Without a start date, the last five years."""

    commodity: Literal[CODES] = Field(  # type: ignore[valid-type]
        default="WTI",
        description="EODHD commodity code. ALL_COMMODITIES is the global price index.",
    )
    interval: Literal["daily", "weekly", "monthly"] = Field(
        default="daily", description="Sampling interval; not every commodity has daily data."
    )

    @field_validator("commodity", mode="before")
    @classmethod
    def _code(cls, v):
        return str(v).strip().upper().replace(" ", "_")


class EODHDCommoditySpotPricesData(CommoditySpotPricesData):
    """EODHD Commodity Spot Prices Data."""


class EODHDCommoditySpotPricesFetcher(
    Fetcher[EODHDCommoditySpotPricesQueryParams, list[EODHDCommoditySpotPricesData]]
):
    """EODHD commodity spot prices."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDCommoditySpotPricesQueryParams:
        return EODHDCommoditySpotPricesQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> dict:  # pylint: disable=unused-argument
        from asyncio import to_thread

        start = query.start_date or (dateType.today() - timedelta(days=_DEFAULT_DAYS))
        params = {"interval": query.interval}

        def _call(client):
            meta, rows = {}, []
            for page in range(_MAX_PAGES):
                resp = rest_json(client, f"commodities/historical/{query.commodity}", {
                    **params, "page[limit]": _PAGE, "page[offset]": page * _PAGE,
                })
                if not isinstance(resp, dict) or resp.get("data") is None:
                    raise UnauthorizedError(f"EODHD (commodity prices): {resp}")
                meta = resp.get("meta") or meta
                rows.extend(resp["data"])
                oldest = min((r.get("date") or "9999" for r in resp["data"]), default="9999")
                if len(resp["data"]) < _PAGE or oldest < str(start):
                    break
            return {"meta": meta, "rows": rows, "start": str(start)}

        out = await to_thread(sdk_call, credentials, _call, "commodity prices")
        if not out["rows"]:
            raise EmptyDataError(f"EODHD returned no prices for {query.commodity}.")
        return out

    @staticmethod
    def transform_data(query, data: dict, **kwargs):  # pylint: disable=unused-argument
        meta = data.get("meta") or {}
        start = data.get("start") or (str(query.start_date) if query.start_date else None)
        end = str(query.end_date) if query.end_date else None
        rows = []
        for it in data.get("rows") or []:
            d, value = it.get("date"), it.get("value")
            if not d or value is None or (start and d < start) or (end and d > end):
                continue
            rows.append(EODHDCommoditySpotPricesData.model_validate({
                "date": d,
                "symbol": query.commodity,
                "commodity": meta.get("name"),
                "price": value,
                "unit": meta.get("unit"),
            }))
        if not rows:
            raise EmptyDataError(f"No {query.commodity} prices in the requested window.")
        return sorted(rows, key=lambda r: r.date)
