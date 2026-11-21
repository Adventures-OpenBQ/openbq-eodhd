# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""EODHD US options chains (/mp/unicornbay/options/eod) — OptionsChains.

A marketplace product ("US Stock Options Data API"), bought separately from
the core plans: without it every request is HTTP 403. End of day only, about
6,000 underlyings, two years of history, with implied volatility and the five
Greeks per contract.

NOT VERIFIED AGAINST THE LIVE FEED. The account this was written with is not
subscribed, so the field mapping follows EODHD's published OpenAPI schema
(EodHistoricalData/EODHD-openapi, paths/mp_unicornbay_options_eod.yaml) and
its example row. The tests pin that documented shape.

One chain = every contract of one underlying on one trade date. The endpoint
cannot sort by trade date, so with no `date` the fetcher walks back from today
one day at a time (at most `_LOOKBACK_DAYS`) until a date has rows. Pages are
1,000 rows and the offset stops at 10,000, so a chain larger than 11,000
contracts is cut; narrow it with the expiration, strike or option_type
filters.
"""

from datetime import date as dateType, timedelta
from typing import Any, Literal

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.options_chains import (
    OptionsChainsData, OptionsChainsQueryParams,
)
from openbb_core.provider.utils.errors import EmptyDataError
from pydantic import Field

from openbq_eodhd.models._jsonapi import fetch_rows

_PAGE = 1000
_MAX_PAGES = 11  # page[offset] is capped at 10,000
_LOOKBACK_DAYS = 6

# EODHD attribute -> standard OptionsChains column.
STANDARD_FIELDS = {
    "contract": "contract_symbol",
    "underlying_symbol": "underlying_symbol",
    "tradetime": "eod_date",
    "exp_date": "expiration",
    "dte": "dte",
    "strike": "strike",
    "type": "option_type",
    "open_interest": "open_interest",
    "volume": "volume",
    "theoretical": "theoretical_price",
    "last": "last_trade_price",
    "last_size": "last_trade_size",
    "bid": "bid",
    "bid_size": "bid_size",
    "bid_date": "bid_time",
    "ask": "ask",
    "ask_size": "ask_size",
    "ask_date": "ask_time",
    "midpoint": "mark",
    "open": "open",
    "high": "high",
    "low": "low",
    "previous": "prev_close",
    "change": "change",
    "pctchange": "change_percent",
    "volatility": "implied_volatility",
    "delta": "delta",
    "gamma": "gamma",
    "theta": "theta",
    "vega": "vega",
    "rho": "rho",
}
# EODHD attributes with no standard column, kept under these names.
EXTRA_FIELDS = {
    "exchange": "exchange",
    "currency": "currency",
    "expiration_type": "expiration_type",
    "moneyness": "moneyness",
    "volume_change": "volume_change",
    "open_interest_change": "open_interest_change",
    "volatility_change": "implied_volatility_change",
    "vol_oi_ratio": "volume_open_interest_ratio",
}
_TIMESTAMPS = ("bid_time", "ask_time")
_BLANK_AS_NONE = ("eod_date", "bid_time", "ask_time")


class EODHDOptionsChainsQueryParams(OptionsChainsQueryParams):
    """EODHD Options Chains Query. Needs the US Stock Options marketplace subscription."""

    date: dateType | None = Field(
        default=None,
        description="Trade date of the end-of-day chain. Default: the latest date with data.",
    )
    option_type: Literal["call", "put"] | None = Field(
        default=None, description="Only calls or only puts."
    )
    expiration_gte: dateType | None = Field(default=None, description="Earliest expiration.")
    expiration_lte: dateType | None = Field(default=None, description="Latest expiration.")
    strike_gte: float | None = Field(default=None, description="Lowest strike.")
    strike_lte: float | None = Field(default=None, description="Highest strike.")


class EODHDOptionsChainsData(OptionsChainsData):
    """EODHD Options Chains Data."""

    exchange: list[str | None] = Field(default_factory=list, description="Listing exchange.")
    currency: list[str | None] = Field(default_factory=list, description="Contract currency.")
    expiration_type: list[str | None] = Field(
        default_factory=list, description="monthly, weekly, quarterly and so on."
    )
    moneyness: list[float | None] = Field(
        default_factory=list, description="Moneyness of the strike, as EODHD reports it."
    )
    volume_change: list[float | None] = Field(
        default_factory=list, description="Change in volume from the previous session."
    )
    open_interest_change: list[float | None] = Field(
        default_factory=list, description="Change in open interest from the previous session."
    )
    implied_volatility_change: list[float | None] = Field(
        default_factory=list, description="Change in implied volatility from the previous session."
    )
    volume_open_interest_ratio: list[float | None] = Field(
        default_factory=list, description="Volume divided by open interest."
    )


def _filters(query, trade_date: dateType) -> dict[str, Any]:
    return {
        "filter[underlying_symbol]": query.symbol.split(".")[0],
        "filter[tradetime_eq]": str(trade_date),
        "filter[type]": query.option_type,
        "filter[exp_date_from]": str(query.expiration_gte) if query.expiration_gte else None,
        "filter[exp_date_to]": str(query.expiration_lte) if query.expiration_lte else None,
        "filter[strike_from]": query.strike_gte,
        "filter[strike_to]": query.strike_lte,
        "sort": "exp_date",
    }


class EODHDOptionsChainsFetcher(
    Fetcher[EODHDOptionsChainsQueryParams, EODHDOptionsChainsData]
):
    """EODHD end-of-day US options chains."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDOptionsChainsQueryParams:
        return EODHDOptionsChainsQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        from asyncio import to_thread

        def _sync():
            today = dateType.today()
            dates = [query.date] if query.date else [
                today - timedelta(days=n) for n in range(_LOOKBACK_DAYS + 1)
            ]
            for trade_date in dates:
                rows = fetch_rows(
                    credentials, "mp/unicornbay/options/eod", _filters(query, trade_date),
                    "options chain", page_limit=_PAGE, max_pages=_MAX_PAGES, allow_empty=True,
                )
                if rows:
                    return rows
            raise EmptyDataError(
                f"EODHD returned no options for {query.symbol}"
                + (f" on {query.date}." if query.date else f" in the last {_LOOKBACK_DAYS} days.")
            )

        return await to_thread(_sync)

    @staticmethod
    def transform_data(query, data: list[dict], **kwargs) -> EODHDOptionsChainsData:  # pylint: disable=unused-argument
        model = EODHDOptionsChainsData
        pct_scale = (model.model_fields["change_percent"].json_schema_extra or {}).get(
            "x-frontend_multiply"
        )
        names = {**STANDARD_FIELDS, **EXTRA_FIELDS}
        columns: dict[str, list] = {ours: [] for ours in names.values()}
        for it in data:
            # JSON:API resource objects carry the row under "attributes".
            row = it.get("attributes", it) if isinstance(it, dict) else {}
            if not row.get("contract") or not row.get("exp_date") or row.get("strike") is None:
                continue
            for theirs, ours in names.items():
                value = row.get(theirs)
                if ours in _BLANK_AS_NONE and not value:
                    value = None
                elif ours in _TIMESTAMPS and isinstance(value, str):
                    value = value.replace(" ", "T")
                elif ours == "change_percent" and value is not None and pct_scale:
                    value = float(value) / pct_scale
                columns[ours].append(value)
        if not columns["contract_symbol"]:
            raise EmptyDataError("EODHD returned no usable option contracts.")
        return model.model_validate(columns)
