# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""EODHD Equity Historical Price Model."""

from datetime import datetime
from typing import Any, Literal

from openbb_core.provider.abstract.annotated_result import AnnotatedResult
from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.equity_historical import (
    EquityHistoricalData,
    EquityHistoricalQueryParams,
)
from openbb_core.provider.utils.descriptions import QUERY_DESCRIPTIONS
from pydantic import Field, ValidationError, model_validator

from openbq_eodhd.aggregation import attach_widget_hints

from openbq_eodhd.models._bars import INTERVAL_CHOICES, fetch_bars, rows_from_bars
from openbq_eodhd.models._chart_sources import ChartSourceEnvelope, annotate_chart_sources
from openbq_eodhd.models._daily_price_chart_source import (
    DailyPriceAcquisition,
    DailyPriceReceipt,
    supports_daily_price,
)


def _qualify_equity(symbol: str, exchange: str) -> list[str]:
    """Qualify bare symbols with an exchange code (AAPL -> AAPL.US)."""
    out = []
    for s in symbol.split(","):
        s = s.strip().upper()
        if s:
            out.append(s if "." in s else f"{s}.{exchange.upper()}")
    return out


class EODHDEquityHistoricalQueryParams(EquityHistoricalQueryParams):
    """EODHD Equity Historical Price Query.

    Source: https://eodhd.com/financial-apis/api-for-historical-data-and-volumes
    """

    # OpenBB core reads this dunder directly (registry_map / package_builder) for
    # multi-symbol + choices; pydantic's model_config json_schema_extra is a
    # different mechanism core never inspects, so it must stay this attribute.
    __json_schema_extra__ = {
        "symbol": {"multiple_items_allowed": True},
        "interval": {"choices": INTERVAL_CHOICES},
    }

    interval: Literal["1m", "5m", "1h", "1d", "1W", "1M"] = Field(
        default="1d", description=QUERY_DESCRIPTIONS.get("interval", "")
    )
    exchange: str = Field(
        default="US",
        description="EODHD exchange code appended to bare symbols (e.g. 'US', 'LSE',"
        " 'TO'). Ignored when the symbol is already qualified, like 'AAPL.US'.",
    )
    chart_source: bool = Field(
        default=False, description="Include acquisition evidence for bounded daily US prices.",
    )

    @model_validator(mode="after")
    def _check_chart_source_dates(self):
        if self.chart_source:
            if self.start_date is None or self.end_date is None:
                raise ValueError("chart_source requires explicit start_date and end_date")
            if self.start_date > self.end_date:
                raise ValueError("start_date must be on or before end_date")
        return self


class EODHDEquityHistoricalData(EquityHistoricalData):
    """EODHD Equity Historical Price Data."""

    adjusted_close: float | None = Field(
        default=None,
        description="Split/dividend-adjusted closing price (EOD endpoint only).",
    )


# Every column carries its aggregation hint into widgets.json; see
# openbq_eodhd.aggregation.attach_widget_hints. Stamped after the class so the
# class body stays the plain Data declaration it always was.
attach_widget_hints(EODHDEquityHistoricalData, "EquityHistorical")


class EODHDEquityHistoricalFetcher(
    Fetcher[EODHDEquityHistoricalQueryParams, list[EODHDEquityHistoricalData]]
):
    """Transform the query, extract and transform the data from EODHD endpoints."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDEquityHistoricalQueryParams:  # pylint: disable=unused-argument
        """Transform the query params; default to a 1-year window."""
        # pylint: disable=import-outside-toplevel
        from dateutil.relativedelta import relativedelta

        query = EODHDEquityHistoricalQueryParams(**params)
        now = datetime.now().date()
        if query.start_date is None:
            query.start_date = now - relativedelta(years=1)
        if query.end_date is None:
            query.end_date = now
        return query

    @staticmethod
    async def aextract_data(
        query: EODHDEquityHistoricalQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict] | tuple[DailyPriceAcquisition, list[dict]]:
        """Return the raw bars and capture acquisition facts for qualifying requests."""
        symbols = _qualify_equity(query.symbol, query.exchange)
        if query.chart_source and supports_daily_price(query.interval, symbols):
            acquisition = DailyPriceAcquisition(symbols, str(query.start_date), str(query.end_date))
            bars = await fetch_bars(
                query.interval, symbols, query.start_date, query.end_date, credentials,
                raw_observer=acquisition.observe,
            )
            return acquisition, bars
        return await fetch_bars(
            query.interval, symbols, query.start_date, query.end_date, credentials
        )

    @staticmethod
    def transform_data(
        query: EODHDEquityHistoricalQueryParams,
        data: list[dict] | tuple[DailyPriceAcquisition, list[dict]],
        **kwargs: Any,
    ) -> list[EODHDEquityHistoricalData] | AnnotatedResult[list[EODHDEquityHistoricalData]]:
        """Map EODHD bar fields to the standard model."""
        if isinstance(data, tuple):
            acquisition, bars = data
            rows = []
            candidates = acquisition.candidate_bars(bars)
            for row in rows_from_bars(query.interval, acquisition.multiple, candidates):
                try:
                    rows.append(EODHDEquityHistoricalData.model_validate(row))
                except ValidationError:
                    # The receipt retains the original dated observation as a gap.
                    continue
            coordinates = [
                (row.symbol if acquisition.multiple else acquisition.symbols[0], row.date.isoformat())
                for row in rows
            ]
            envelope = ChartSourceEnvelope[DailyPriceReceipt](
                receipts=[acquisition.receipt(coordinates)],
            )
            return annotate_chart_sources(rows, envelope)

        rows = rows_from_bars(query.interval, "," in query.symbol, data)
        return [EODHDEquityHistoricalData.model_validate(r) for r in rows]
