# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""EODHD corporate actions: historical dividends and splits."""

from datetime import datetime
from typing import Any

from openbb_core.provider.abstract.annotated_result import AnnotatedResult
from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.historical_dividends import (
    HistoricalDividendsData,
    HistoricalDividendsQueryParams,
)
from openbb_core.provider.standard_models.historical_splits import (
    HistoricalSplitsData,
    HistoricalSplitsQueryParams,
)
from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.provider.utils.errors import EmptyDataError, UnauthorizedError
from pydantic import Field

from openbq_eodhd.models._chart_evidence import (
    DividendSnapshotCoverage, annotate, dividend_amount, dividend_evidence, finite_number,
    iso_date, optional_text,
)
from openbq_eodhd.models._client import get_client, raise_sdk_error


def _qualify(symbol: str, exchange: str) -> str:
    """Qualify a single bare symbol with an exchange code (AAPL -> AAPL.US)."""
    s = symbol.strip().upper()
    return s if "." in s else f"{s}.{exchange.upper()}"


async def _fetch_list(
    kind: str, sym: str, credentials: dict[str, str] | None, extra: dict | None = None,
    *, allow_empty: bool = False,
) -> list[dict]:
    """Fetch dividends ('div') or splits via the official SDK; return the raw list."""
    # The official SDK is synchronous (requests); run it off the event loop.
    # pylint: disable=import-outside-toplevel
    from asyncio import to_thread

    return await to_thread(_fetch_list_sync, kind, sym, credentials, extra, allow_empty=allow_empty)


def _fetch_list_sync(
    kind: str, sym: str, credentials: dict[str, str] | None, extra: dict | None,
    *, allow_empty: bool = False,
) -> list[dict]:
    client = get_client(credentials)
    extra = extra or {}
    try:
        with client:
            if kind == "div":
                response = client.get_historical_dividends_data(
                    ticker=sym,
                    date_from=extra.get("from"),
                    date_to=extra.get("to"),
                )
            else:
                response = client.get_historical_splits_data(ticker=sym)
    except (OpenBBError, UnauthorizedError):
        raise
    except Exception as exc:
        raise_sdk_error(exc, f"{kind} for '{sym}'")
    if isinstance(response, dict):
        # A 200 response that isn't a list is an error payload.
        msg = response.get("errors") or response.get("message") or response
        raise UnauthorizedError(f"EODHD ({sym}): {msg}")
    if allow_empty:
        if not isinstance(response, list):
            raise UnauthorizedError(f"EODHD ({sym}): expected a list of {kind} records.")
        return response
    if not response:
        raise EmptyDataError(f"EODHD returned no {kind} for '{sym}'.")
    return response


# --- Dividends -----------------------------------------------------------------
class EODHDHistoricalDividendsQueryParams(HistoricalDividendsQueryParams):
    """EODHD Historical Dividends Query."""

    exchange: str = Field(
        default="US", description="EODHD exchange code for bare symbols (e.g. 'US')."
    )
    chart_source: bool = Field(default=False, description="Include financial chart acquisition evidence.")


class EODHDHistoricalDividendsData(HistoricalDividendsData):
    """EODHD Historical Dividends Data."""

    declaration_date: Any = Field(default=None, description="Dividend declaration date.")
    record_date: Any = Field(default=None, description="Dividend record date.")
    payment_date: Any = Field(default=None, description="Dividend payment date.")
    frequency: str | None = Field(default=None, description="Dividend period/frequency.")
    unadjusted_amount: float | None = Field(
        default=None, description="Unadjusted dividend amount."
    )
    currency: str | None = Field(default=None, description="Dividend currency.")


def _normalize_dividends(data: list[dict], *, chart_source: bool = False) -> list[EODHDHistoricalDividendsData]:
    from pandas import isna, to_datetime

    def legacy_date(value):
        if not value:
            return None
        timestamp = to_datetime(value, errors="coerce")
        return None if isna(timestamp) else timestamp.date()

    parse_date = iso_date if chart_source else legacy_date
    parse_text = optional_text if chart_source else lambda value: value
    rows = []
    for item in data:
        if chart_source and not isinstance(item, dict):
            continue
        ex_date = parse_date(item.get("date"))
        if ex_date is None:
            continue
        amount = item.get("value")
        unadjusted_amount = item.get("unadjustedValue")
        if chart_source:
            amount = dividend_amount(amount)
            if amount is None:
                continue
            unadjusted_amount = finite_number(unadjusted_amount)
        rows.append(EODHDHistoricalDividendsData.model_validate({
            "ex_dividend_date": ex_date,
            "amount": amount,
            "declaration_date": parse_date(item.get("declarationDate")),
            "record_date": parse_date(item.get("recordDate")),
            "payment_date": parse_date(item.get("paymentDate")),
            "frequency": parse_text(item.get("period")),
            "unadjusted_amount": unadjusted_amount,
            "currency": parse_text(item.get("currency")),
        }))
    rows.sort(key=lambda row: row.ex_dividend_date)
    return rows


def _annotated_dividends(
    query: EODHDHistoricalDividendsQueryParams, data: list[dict],
    coverage: DividendSnapshotCoverage | None = None, *, retrieved_at: datetime | None = None,
) -> AnnotatedResult[list[EODHDHistoricalDividendsData]]:
    evidence = dividend_evidence(
        data, _qualify(query.symbol, query.exchange), query.start_date, query.end_date,
        coverage, retrieved_at=retrieved_at,
    )
    return annotate(_normalize_dividends(data, chart_source=True), evidence)


def normalize_dividend_snapshot(
    query: EODHDHistoricalDividendsQueryParams, data: list[dict],
    coverage: DividendSnapshotCoverage, *, retrieved_at: datetime | None = None,
) -> AnnotatedResult[list[EODHDHistoricalDividendsData]]:
    """Normalize a fully acquired snapshot with its independent coverage declaration."""
    return _annotated_dividends(query, data, coverage, retrieved_at=retrieved_at)


class EODHDHistoricalDividendsFetcher(
    Fetcher[EODHDHistoricalDividendsQueryParams, list[EODHDHistoricalDividendsData]]
):
    """EODHD historical dividends (/api/div)."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDHistoricalDividendsQueryParams:
        # pylint: disable=unused-argument
        return EODHDHistoricalDividendsQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        extra: dict[str, Any] = {}
        if query.start_date:
            extra["from"] = str(query.start_date)
        if query.end_date:
            extra["to"] = str(query.end_date)
        sym = _qualify(query.symbol, query.exchange)
        return await _fetch_list("div", sym, credentials, extra, allow_empty=query.chart_source)

    @staticmethod
    def transform_data(
        query, data: list[dict], **kwargs,
    ) -> list[EODHDHistoricalDividendsData] | AnnotatedResult[list[EODHDHistoricalDividendsData]]:
        if query.chart_source:
            return _annotated_dividends(query, data)
        return _normalize_dividends(data)


# --- Splits --------------------------------------------------------------------
class EODHDHistoricalSplitsQueryParams(HistoricalSplitsQueryParams):
    """EODHD Historical Splits Query."""

    exchange: str = Field(
        default="US", description="EODHD exchange code for bare symbols (e.g. 'US')."
    )


class EODHDHistoricalSplitsData(HistoricalSplitsData):
    """EODHD Historical Splits Data."""


class EODHDHistoricalSplitsFetcher(
    Fetcher[EODHDHistoricalSplitsQueryParams, list[EODHDHistoricalSplitsData]]
):
    """EODHD historical splits (/api/splits)."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDHistoricalSplitsQueryParams:
        # pylint: disable=unused-argument
        return EODHDHistoricalSplitsQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        sym = _qualify(query.symbol, query.exchange)
        return await _fetch_list("splits", sym, credentials)

    @staticmethod
    def transform_data(query, data: list[dict], **kwargs) -> list[EODHDHistoricalSplitsData]:  # pylint: disable=unused-argument
        # pylint: disable=import-outside-toplevel
        from pandas import to_datetime

        rows = []
        for item in data:
            # EODHD "split" is a "num/den" string, e.g. "4.000000/1.000000".
            raw = str(item.get("split", "")).split("/")
            try:
                num = float(raw[0])
                den = float(raw[1]) if len(raw) > 1 and float(raw[1]) else 1.0
            except (ValueError, IndexError):
                continue
            raw_date = item.get("date")
            if not raw_date:
                continue
            try:
                parsed = to_datetime(raw_date).date()
            except (ValueError, TypeError):
                continue
            rows.append(
                EODHDHistoricalSplitsData.model_validate(
                    {
                        "date": parsed,
                        "numerator": num,
                        "denominator": den,
                        # standard field is a string, e.g. "4:1".
                        "split_ratio": f"{num:g}:{den:g}",
                    }
                )
            )
        rows.sort(key=lambda r: r.date)
        return rows
