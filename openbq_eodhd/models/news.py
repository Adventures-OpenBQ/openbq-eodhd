# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""EODHD news (/news) — WorldNews (general feed or topic tag) and
CompanyNews (symbol-scoped via `s=`), one endpoint behind both.
"""

from typing import Any

from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.provider.abstract.annotated_result import AnnotatedResult
from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.company_news import (
    CompanyNewsData, CompanyNewsQueryParams,
)
from openbb_core.provider.standard_models.world_news import (
    WorldNewsData, WorldNewsQueryParams,
)
from openbb_core.provider.utils.errors import EmptyDataError, UnauthorizedError
from pydantic import Field, ValidationError, model_serializer

from openbq_eodhd.models._chart_sources import ChartSourceEnvelope, annotate_chart_sources
from openbq_eodhd.models._client import rest_json, sdk_call
from openbq_eodhd.models._fundamentals import qualify
from openbq_eodhd.models._news_chart_source import (
    NewsScope, NewsSource, Polarity, normalize_news_page, qualified_news_symbol,
)


def _fetch_news(credentials, params: dict, context: str, *, chart_source: bool = False) -> list[dict]:
    """One /news call via the raw endpoint (the SDK wrapper refuses the
    general feed the API itself serves)."""
    resp = sdk_call(credentials, lambda c: rest_json(c, "news", params), context)
    if isinstance(resp, dict):
        raise UnauthorizedError(
            f"EODHD ({context}): {resp.get('message') or resp.get('error') or resp}"
        )
    if chart_source:
        if not isinstance(resp, list):
            raise UnauthorizedError("EODHD CompanyNews did not return an article array.")
        return resp
    if not resp:
        raise EmptyDataError(f"EODHD returned no {context}.")
    return resp


class EODHDWorldNewsQueryParams(WorldNewsQueryParams):
    """EODHD World News Query."""

    topic: str | None = Field(
        default=None,
        description="EODHD topic tag, e.g. 'technology', 'mergers and acquisitions'."
        " Omitted, the general financial feed is served.",
    )


class EODHDWorldNewsData(WorldNewsData):
    """EODHD World News Data."""

    symbols: list[str] | None = Field(default=None, description="Tickers the article mentions.")
    tags: list[str] | None = Field(default=None, description="EODHD topic tags on the article.")
    sentiment: dict | None = Field(default=None, description="EODHD polarity/pos/neu/neg scores.")


class EODHDWorldNewsFetcher(
    Fetcher[EODHDWorldNewsQueryParams, list[EODHDWorldNewsData]]
):
    """EODHD world news."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDWorldNewsQueryParams:
        return EODHDWorldNewsQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        from asyncio import to_thread

        return await to_thread(
            _fetch_news,
            credentials,
            {
                "t": query.topic,
                "from": str(query.start_date) if query.start_date else None,
                "to": str(query.end_date) if query.end_date else None,
                "limit": query.limit,
            },
            "world news",
        )

    @staticmethod
    def transform_data(query, data: list[dict], **kwargs) -> list[EODHDWorldNewsData]:  # pylint: disable=unused-argument
        rows = []
        for it in data:
            if not it.get("date") or not it.get("title"):
                continue
            content = it.get("content") or ""
            rows.append(EODHDWorldNewsData.model_validate({
                "date": it["date"],
                "title": it["title"],
                "body": content or None,
                "excerpt": (content[:280] or None) if content else None,
                "url": it.get("link"),
                "symbols": it.get("symbols") or None,
                "tags": it.get("tags") or None,
                "sentiment": it.get("sentiment") or None,
            }))
        return rows


class EODHDCompanyNewsQueryParams(CompanyNewsQueryParams):
    """EODHD Company News Query."""

    __json_schema_extra__ = {"symbol": {"multiple_items_allowed": True}}

    exchange: str = Field(default="US", description="EODHD exchange code for bare symbols.")
    chart_source: bool = Field(default=False, description="Include delivered news chart acquisition evidence.")
    offset: int = Field(default=0, description="EODHD article offset for chart_source requests.")

    def chart_page(self) -> tuple[str, NewsScope]:
        symbol = qualify(self.symbol or "", self.exchange)
        if not qualified_news_symbol(symbol):
            raise OpenBBError("EODHD CompanyNews chart source requires one qualified instrument.")
        try:
            scope = NewsScope(
                kind="sample", start=self.start_date, end=self.end_date,
                limit=50 if self.limit is None else self.limit, offset=self.offset,
            )
        except ValidationError as exc:
            raise OpenBBError(
                "EODHD CompanyNews chart source requires ordered dates, limit 1–1000,"
                " and a nonnegative offset with limit + offset at most 10000."
            ) from exc
        return symbol, scope


class EODHDCompanyNewsData(CompanyNewsData):
    """EODHD Company News Data."""

    tags: list[str] | None = Field(default=None, description="EODHD topic tags on the article.")
    sentiment: dict | None = Field(default=None, description="EODHD polarity/pos/neu/neg scores.")
    polarity: Polarity | None = Field(default=None, description="Opt-in article polarity in [-1,1].")

    @model_serializer(mode="wrap")
    def serialize_news(self, handler):
        row = handler(self)
        if "polarity" not in self.model_fields_set:
            row.pop("polarity", None)
        return row


class EODHDCompanyNewsFetcher(
    Fetcher[EODHDCompanyNewsQueryParams, list[EODHDCompanyNewsData]]
):
    """EODHD symbol-scoped news."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDCompanyNewsQueryParams:
        return EODHDCompanyNewsQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        from asyncio import to_thread

        if not query.symbol:
            raise OpenBBError("EODHD company news requires a symbol.")
        if query.chart_source:
            symbols, scope = query.chart_page()
        else:
            symbols = ",".join(
                qualify(s, query.exchange) for s in query.symbol.split(",") if s.strip()
            )
        params = {
            "s": symbols,
            "from": str(query.start_date) if query.start_date else None,
            "to": str(query.end_date) if query.end_date else None,
            "limit": query.limit,
        }
        if query.chart_source:
            params.update(limit=scope.limit, offset=scope.offset)
        return await to_thread(
            _fetch_news, credentials, params, f"news for '{query.symbol}'", chart_source=query.chart_source,
        )

    @staticmethod
    def transform_data(
        query, data: list[dict], **kwargs,
    ) -> list[EODHDCompanyNewsData] | AnnotatedResult[list[EODHDCompanyNewsData]]:
        if query.chart_source:
            instrument, scope = query.chart_page()
            records, receipt = normalize_news_page(data, instrument, scope)
            rows = [EODHDCompanyNewsData.model_validate(record) for record in records]
            return annotate_chart_sources(rows, ChartSourceEnvelope[NewsSource](receipts=[receipt]))
        rows = []
        for it in data:
            # date, title and url are all required by the standard model
            if not it.get("date") or not it.get("title") or not it.get("link"):
                continue
            content = it.get("content") or ""
            rows.append(EODHDCompanyNewsData.model_validate({
                "date": it["date"],
                "title": it["title"],
                "body": content or None,
                "excerpt": (content[:280] or None) if content else None,
                "url": it["link"],
                "symbols": ",".join(it.get("symbols") or []) or None,
                "tags": it.get("tags") or None,
                "sentiment": it.get("sentiment") or None,
            }))
        return rows
