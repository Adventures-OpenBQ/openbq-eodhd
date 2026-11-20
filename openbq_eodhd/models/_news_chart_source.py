# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""Closed CompanyNews evidence for a single acquired /news page."""

import json
import re
from datetime import date, datetime, timezone
from ipaddress import IPv6Address
from typing import Annotated, Literal

from openbb_core.provider.utils.errors import UnauthorizedError
from pydantic import Field, field_validator, model_validator

from openbq_eodhd.models._chart_sources import ChartSourceFacts, ChartSourceReceipt

_SYMBOL = re.compile(r"[A-Z0-9^][A-Z0-9^_/-]*(?:\.[A-Z0-9^_/-]+)*\.[A-Z0-9][A-Z0-9_-]*", re.ASCII)
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
)
# Match the portable authority rules in BDOBB newsEvidence.ts.
_LINK = re.compile(r"^https?://([a-z0-9.-]+|\[[0-9a-f:]+\])(?::([0-9]+))?(?:[/?#]|$)", re.I | re.ASCII)
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.I | re.ASCII)
_IPV4_OCTET = re.compile(r"0|[1-9][0-9]{0,2}")
_UNSAFE_LINK_CHAR = re.compile(
    r"[\x00-\x20\x7f\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff\ud800-\udfff\\]",
)

Polarity = Annotated[float, Field(strict=True, ge=-1, le=1, allow_inf_nan=False)]
Diagnostic = Literal[
    "invalid_identity", "invalid_timestamp", "duplicate_conflict", "unit_conflict",
    "exact_duplicate", "missing_title", "unscored",
]


def qualified_news_symbol(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 128 and _SYMBOL.fullmatch(value) is not None


def _article_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 4096 or _UNSAFE_LINK_CHAR.search(value):
        return False
    if len(value.encode("utf-16-le")) > 8192:
        return False
    authority = _LINK.match(value)
    if not authority:
        return False
    host, port = authority.groups()
    if port:
        significant_digits = port.lstrip("0")
        if len(significant_digits) > 5 or int(significant_digits or "0") > 65535:
            return False
    if host.startswith("["):
        try:
            IPv6Address(host[1:-1])
        except ValueError:
            return False
        return True
    domain = host.removesuffix(".")
    labels = domain.lower().split(".")
    if re.match(r"[a-z]", labels[-1], re.ASCII):
        return len(domain) <= 253 and all(
            _DNS_LABEL.fullmatch(label) and not label.startswith("xn--") for label in labels
        )
    return host == domain and len(labels) == 4 and all(
        _IPV4_OCTET.fullmatch(label) and int(label) <= 255 for label in labels
    )


def _publication_time(value: object) -> tuple[datetime, date] | None:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp, stamp.astimezone(timezone.utc).date()
    except (ValueError, OverflowError):
        return None


class NewsFieldOrigins(ChartSourceFacts):
    timestamp: Literal["date"]
    articleId: Literal["url"]
    instrument: Literal["symbols"]
    polarity: Literal["polarity"]


class NewsUnsafe(ChartSourceFacts):
    invalid_identity: bool
    invalid_timestamp: bool
    duplicate_conflict: bool
    unit_conflict: bool


class NewsScope(ChartSourceFacts):
    kind: Literal["complete", "bounded", "partial", "sample", "unknown"]
    start: date | None
    end: date | None
    limit: Annotated[int, Field(ge=1, le=1000)] | None
    offset: Annotated[int, Field(ge=0, le=9999)] | None

    @model_validator(mode="after")
    def _scope_coherent(self):
        if self.start and self.end and self.start > self.end:
            raise ValueError("News scope start must precede end")
        if self.kind in ("complete", "bounded") and (not self.start or not self.end or self.limit is not None):
            raise ValueError("Complete and bounded snapshots require dates and cannot be paged")
        if (self.limit is None) != (self.offset is None):
            raise ValueError("A news page requires both limit and offset")
        if self.limit is not None and self.offset is not None and self.limit + self.offset > 10000:
            raise ValueError("News page limit and offset must total at most 10000")
        return self

    def contains(self, day: date) -> bool:
        return (self.start is None or self.start <= day) and (self.end is None or day <= self.end)


class NewsCoordinate(ChartSourceFacts):
    article_id: str
    utc_day: date
    scored: bool

    @field_validator("article_id")
    @classmethod
    def _valid_url(cls, value):
        if not _article_url(value):
            raise ValueError("Expected the original HTTP(S) article URL")
        return value


class NewsSource(ChartSourceReceipt):
    version: Literal[1]
    kind: Literal["news"]
    diagnostics: list[Diagnostic]
    unsafe: NewsUnsafe
    field_origins: NewsFieldOrigins
    instrument: str
    polarity_unit: Literal["polarity[-1,1]"]
    coordinates: list[NewsCoordinate]
    scope: NewsScope

    @field_validator("instrument")
    @classmethod
    def _valid_symbol(cls, value):
        if not qualified_news_symbol(value):
            raise ValueError("Expected one qualified news instrument")
        return value

    @model_validator(mode="after")
    def _consistent(self):
        if self.scope.limit is not None and self.raw_count > self.scope.limit:
            raise ValueError("Raw news page exceeds its limit")
        if self.scope.kind in ("complete", "bounded") and self.normalized_count != self.delivered_count:
            raise ValueError("News snapshot omits normalized articles from its declared coverage")
        if len(self.coordinates) != self.delivered_count or len({c.article_id for c in self.coordinates}) != self.delivered_count:
            raise ValueError("Delivered news requires a unique coordinate for each article")
        if any(not self.scope.contains(c.utc_day) for c in self.coordinates):
            raise ValueError("News coordinate outside acquired scope")
        diagnostics = set(self.diagnostics)
        if any(value != (name in diagnostics) for name, value in self.unsafe.model_dump().items()):
            raise ValueError("News diagnostics contradict unsafe facts")
        if any(not c.scored for c in self.coordinates) != ("unscored" in diagnostics):
            raise ValueError("News diagnostics contradict scored classifications")
        drops = bool(diagnostics & {"invalid_identity", "invalid_timestamp", "duplicate_conflict", "exact_duplicate", "missing_title"})
        if drops != (self.raw_count > self.normalized_count):
            raise ValueError("News diagnostics contradict normalization counts")
        return self


def normalize_news_page(data: list, instrument: str, scope: NewsScope) -> tuple[list[dict], NewsSource]:
    """Record raw facts and compare duplicate payloads before rejecting any row."""
    if scope.limit is not None and len(data) > scope.limit:
        raise UnauthorizedError("EODHD CompanyNews returned more articles than the requested page limit.")
    retrieved_at = datetime.now(timezone.utc)
    rows, coordinates = [], []
    seen_payloads: dict[str, dict] = {}
    delivered_ids: set[str] = set()
    diagnostics: set[Diagnostic] = set()
    for value in data:
        item = value if isinstance(value, dict) else {}
        timestamp, url, symbols = _publication_time(item.get("date")), item.get("link"), item.get("symbols")
        valid_url = _article_url(url)
        associated = isinstance(symbols, list) and instrument in symbols and all(map(qualified_news_symbol, symbols))
        if not timestamp:
            diagnostics.add("invalid_timestamp")
        elif not scope.contains(timestamp[1]):
            raise UnauthorizedError("EODHD CompanyNews returned an article outside the requested UTC publication dates.")
        if not valid_url or not associated:
            diagnostics.add("invalid_identity")
        if valid_url:
            prior = seen_payloads.get(url)
            if prior is None:
                seen_payloads[url] = item
            else:
                # JSON key order is immaterial, but value types and array order remain significant.
                same = json.dumps(prior, sort_keys=True) == json.dumps(item, sort_keys=True)
                diagnostics.add("exact_duplicate" if same else "duplicate_conflict")
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            diagnostics.add("missing_title")
            continue
        if not (timestamp and valid_url and associated) or url in delivered_ids:
            continue
        delivered_ids.add(url)
        content = item.get("content")
        body = content if isinstance(content, str) and content else None
        raw_sentiment = item.get("sentiment")
        sentiment = raw_sentiment if isinstance(raw_sentiment, dict) else None
        score = sentiment.get("polarity") if sentiment else None
        scored = type(score) in (int, float) and -1 <= score <= 1
        if not scored:
            diagnostics.add("unscored")
        tags = item.get("tags")
        rows.append({
            "date": timestamp[0], "title": title, "body": body, "excerpt": body[:280] if body else None,
            "url": url, "symbols": ",".join(symbols),
            "tags": tags if isinstance(tags, list) and all(isinstance(tag, str) for tag in tags) else None,
            "sentiment": sentiment, "polarity": score if scored else None,
        })
        coordinates.append(NewsCoordinate(article_id=url, utc_day=timestamp[1], scored=scored))
    return rows, NewsSource(
        version=1, kind="news", polarity_unit="polarity[-1,1]",
        retrieved_at=retrieved_at, raw_count=len(data), normalized_count=len(rows), delivered_count=len(rows),
        diagnostics=sorted(diagnostics), unsafe=NewsUnsafe(
            invalid_identity="invalid_identity" in diagnostics,
            invalid_timestamp="invalid_timestamp" in diagnostics,
            duplicate_conflict="duplicate_conflict" in diagnostics,
            unit_conflict=False,
        ),
        field_origins=NewsFieldOrigins(timestamp="date", articleId="url", instrument="symbols", polarity="polarity"),
        instrument=instrument, scope=scope, coordinates=coordinates,
    )
