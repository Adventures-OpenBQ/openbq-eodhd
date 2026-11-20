# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""Coordinate-only evidence for bounded EODHD daily US listing acquisitions."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from json import dumps
from math import isfinite
from re import fullmatch
from typing import Annotated, Any, Literal

from openbb_core.provider.utils.errors import UnauthorizedError
from pydantic import AfterValidator, Field, model_validator

from openbq_eodhd.models._chart_sources import (
    ChartSourceFacts,
    ChartSourceReceipt,
    Count,
)

_US_LISTING = r"[A-Z0-9][A-Z0-9_/-]*(?:\.[A-Z0-9_/-]+)*\.US"
USListing = Annotated[str, Field(pattern=f"^{_US_LISTING}$", max_length=128)]
DailyPriceDiagnostic = Literal[
    "undatable_loss", "conflicting_duplicates", "identity_conflict",
    "partial_acquisition", "invalid_response", "missing_adjusted_basis",
]
_PARTIAL_KEYS = {
    "partial", "is_partial", "truncated", "incomplete", "paging", "pagination",
    "has_more", "hasMore", "next", "next_page", "nextPage", "cursor", "next_cursor",
    "offset", "page", "page_size", "total_pages",
}


def _iso_day(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _strict_iso_day(value: str) -> str:
    if _iso_day(value) is None:
        raise ValueError("Expected an ISO calendar date")
    return value


IsoDay = Annotated[str, AfterValidator(_strict_iso_day)]


class DailyPriceUnsafe(ChartSourceFacts):
    undatable_loss: bool
    conflicting_duplicates: bool
    identity_conflict: bool
    partial_acquisition: bool
    invalid_response: bool
    missing_adjusted_basis: bool


class DailyPriceListingFacts(ChartSourceFacts):
    symbol: USListing
    raw_count: Count
    normalized_count: Count
    delivered_count: Count
    duplicate_count: Count
    omissions: list[tuple[USListing, IsoDay]]


class DailyPriceReceipt(ChartSourceReceipt):
    kind: Literal["daily-price"]
    interval: Literal["1d"]
    start_date: IsoDay
    end_date: IsoDay
    symbols: Annotated[list[USListing], Field(min_length=1, max_length=12)]
    date_field: Literal["date"]
    price_field: Literal["adjusted_close"]
    symbol_field: Literal["symbol"] | None
    listings: Annotated[list[DailyPriceListingFacts], Field(min_length=1, max_length=12)]
    diagnostics: list[DailyPriceDiagnostic]
    unsafe: DailyPriceUnsafe

    @model_validator(mode="after")
    def _check_listing_facts(self):
        flags = {key for key, value in self.unsafe.model_dump().items() if value}
        if self.diagnostics != sorted(flags):
            raise ValueError("Daily price diagnostics must match the ordered unsafe facts")
        if self.start_date > self.end_date:
            raise ValueError("Expected an ordered daily price window")
        if self.symbols != [facts.symbol for facts in self.listings] or (
            len(set(self.symbols)) != len(self.symbols)
            or self.symbol_field != ("symbol" if len(self.symbols) > 1 else None)
        ):
            raise ValueError("Daily price listings must match the ordered symbol selection")
        for count in ("raw_count", "normalized_count", "delivered_count"):
            if getattr(self, count) != sum(getattr(facts, count) for facts in self.listings):
                raise ValueError("Daily price listing counts must sum to the acquisition counts")

        unaccounted_allowed = bool(flags - {"missing_adjusted_basis"})
        for facts in self.listings:
            accounted = facts.normalized_count + facts.duplicate_count + len(facts.omissions)
            if (
                facts.normalized_count != facts.delivered_count
                or accounted > facts.raw_count
                or (accounted != facts.raw_count and not unaccounted_allowed)
            ):
                raise ValueError("Contradictory daily price listing counts")
            if len(set(facts.omissions)) != len(facts.omissions) or any(
                symbol != facts.symbol or not self.start_date <= day <= self.end_date
                for symbol, day in facts.omissions
            ):
                raise ValueError("Daily price omissions must be distinct in-window listing dates")
        return self


def supports_daily_price(interval: str, symbols: list[str]) -> bool:
    """Match the effective SDK selection for eligible equity and ETF queries."""
    return (
        interval == "1d"
        and 1 <= len(symbols) <= 12
        and len(symbols) == len(set(symbols))
        and all(len(symbol) <= 128 and fullmatch(_US_LISTING, symbol) for symbol in symbols)
    )


def _require_array(symbol: str, response: object) -> list[dict]:
    """Reject wrappers and failed arrays before any listing can be delivered."""
    payloads = response if isinstance(response, list) else [response]
    for payload in payloads:
        if isinstance(payload, dict) and not any(
            payload.get(key) for key in ("error", "errors", "message")
        ):
            if _PARTIAL_KEYS.intersection(payload):
                raise UnauthorizedError(f"EODHD ({symbol}): partial_acquisition")
        else:
            raise UnauthorizedError(f"EODHD ({symbol}): invalid_response")
    if not isinstance(response, list):
        raise UnauthorizedError(f"EODHD ({symbol}): invalid_response")
    return response


def _matches_listing(bar: dict, symbol: str) -> bool:
    base, exchange = symbol.rsplit(".", 1)
    for aliases, accepted in (
        (("symbol", "ticker", "code", "_symbol"), (symbol, base)),
        (("exchange", "exchange_code"), (exchange,)),
    ):
        for alias in aliases:
            value = bar.get(alias)
            if value is not None and (
                not isinstance(value, str) or value.strip().upper() not in accepted
            ):
                return False
    return True


def _valid_adjusted_close(value: Any) -> bool:
    if type(value) not in (int, float) or value <= 0:
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


@dataclass
class _ListingAcquisition:
    raw_count: int
    duplicate_count: int = 0
    dates: set[str] = field(default_factory=set)
    eligible: set[str] = field(default_factory=set)
    flags: set[DailyPriceDiagnostic] = field(default_factory=set)


@dataclass
class DailyPriceAcquisition:
    """Observe SDK output before normalization and reconcile it with final rows."""

    symbols: list[str]
    start_date: str
    end_date: str
    _listings: dict[str, _ListingAcquisition] = field(default_factory=dict, init=False)
    _retrieved_at: datetime | None = field(default=None, init=False)

    @property
    def multiple(self) -> bool:
        return len(self.symbols) > 1

    def observe(self, symbol: str, response: object, retrieved_at: datetime) -> None:
        raw = _require_array(symbol, response)
        facts = _ListingAcquisition(raw_count=len(raw))
        by_date: dict[str, set[str]] = defaultdict(set)
        if raw and not any("adjusted_close" in bar for bar in raw):
            facts.flags.add("missing_adjusted_basis")

        for bar in raw:
            identity_known = _matches_listing(bar, symbol)
            if not identity_known:
                facts.flags.add("identity_conflict")
            day = _iso_day(bar.get("date"))
            if day is None:
                facts.flags.add("undatable_loss")
                continue
            if not self.start_date <= day <= self.end_date:
                facts.flags.add("invalid_response")
                continue
            if identity_known:
                facts.dates.add(day)

            # Check raw repetitions before a bad close or adjusted price can hide a conflict.
            signatures = by_date[day]
            signature = dumps(bar, sort_keys=True)
            if signature in signatures:
                facts.duplicate_count += 1
            elif signatures:
                facts.flags.add("conflicting_duplicates")
                facts.eligible.discard(day)
            elif identity_known and _valid_adjusted_close(bar.get("adjusted_close")):
                facts.eligible.add(day)
            signatures.add(signature)

        self._listings[symbol] = facts
        self._retrieved_at = retrieved_at

    def candidate_bars(self, bars: list[dict]) -> list[dict]:
        """Select one raw bar per eligible coordinate before the shared normalizer."""
        pending = {symbol: facts.eligible.copy() for symbol, facts in self._listings.items()}
        selected = []
        for bar in bars:
            symbol = bar["_symbol"] if self.multiple else self.symbols[0]
            day = bar.get("date")
            if isinstance(day, str) and day in pending[symbol]:
                pending[symbol].remove(day)
                selected.append(bar)
        return selected

    def receipt(self, coordinates: list[tuple[str, str]]) -> DailyPriceReceipt:
        if self._retrieved_at is None or list(self._listings) != self.symbols:
            raise ValueError("Daily price evidence requires every requested acquisition")
        delivered: dict[str, set[str]] = defaultdict(set)
        for symbol, day in coordinates:
            delivered[symbol].add(day)
        listings = []
        flags: set[DailyPriceDiagnostic] = set()
        for symbol, facts in self._listings.items():
            flags.update(facts.flags)
            count = len(delivered[symbol])
            listings.append(DailyPriceListingFacts(
                symbol=symbol, raw_count=facts.raw_count, normalized_count=count,
                delivered_count=count, duplicate_count=facts.duplicate_count,
                omissions=[(symbol, day) for day in sorted(facts.dates - delivered[symbol])],
            ))
        return DailyPriceReceipt(
            kind="daily-price", retrieved_at=self._retrieved_at, interval="1d",
            start_date=self.start_date, end_date=self.end_date, symbols=self.symbols,
            date_field="date", price_field="adjusted_close",
            symbol_field="symbol" if self.multiple else None,
            raw_count=sum(facts.raw_count for facts in listings),
            normalized_count=len(coordinates), delivered_count=len(coordinates), listings=listings,
            diagnostics=sorted(flags),
            unsafe=DailyPriceUnsafe(**{key: key in flags for key in DailyPriceUnsafe.model_fields}),
        )
