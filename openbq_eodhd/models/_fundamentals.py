# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""Shared EODHD /fundamentals fetch with a single-flight TTL cache.

OpenBB invokes each widget's fetcher independently, so a dashboard of N
fundamentals widgets for one symbol would otherwise fire N identical
/fundamentals calls (EODHD bills each ~10 credits). Every fundamentals-derived
fetcher reads sections of ONE payload obtained here; concurrent and repeat
requests for the same symbol within the TTL window share a single HTTP call.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from asyncio import Lock, to_thread
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.provider.utils.errors import EmptyDataError, UnauthorizedError

from openbq_eodhd.models._client import get_client, raise_sdk_error, rest_json, sdk_call
from openbq_eodhd import capture as _capture

_TTL_SECONDS = 120.0            # L1 in-process burst-coalescing TTL
_L2_LIBRARY = "eodhd_fundamentals_cache"
_EARNINGS_V11_SUFFIX = "#earnings-v1.1"
_CACHE_UTC_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)",
)


@dataclass(frozen=True)
class FundamentalsAcquisition:
    payload: dict
    # UTC completion of the upstream fetch. Legacy cache records may lack it.
    retrieved_at: datetime | None


@dataclass(frozen=True)
class _FundamentalsCacheEntry:
    payload: dict
    retrieved_at: datetime | None
    stored_at: datetime


_cache: dict[str, tuple[float, _FundamentalsCacheEntry]] = {}
_locks: dict[str, Lock] = {}


def _l2_ttl_seconds() -> float:
    try:
        return float(os.getenv("EODHD_FUNDAMENTALS_TTL_HOURS", "24")) * 3600.0
    except ValueError:
        return 24 * 3600.0


def qualify(symbol: str, exchange: str = "US") -> str:
    s = symbol.strip().upper()
    return s if "." in s else f"{s}.{exchange.upper()}"


def primary_us_common_stock(bundle: dict, listing: str, exchange: str) -> bool:
    """Check the acquired U.S. primary common-stock identity against the requested listing."""
    general = bundle.get("General")
    return bool(
        exchange.upper() == "US" and len(listing) <= 128
        and re.fullmatch(r"[A-Z0-9^][A-Z0-9^_/-]*(?:\.[A-Z0-9^_/-]+)*\.US", listing)
        and isinstance(general, dict) and general.get("Code") == listing.removesuffix(".US")
        and general.get("PrimaryTicker") == listing and general.get("Type") == "Common Stock"
        and general.get("Exchange") in ("NASDAQ", "NYSE", "NYSE ARCA", "BATS")
    )


def _cache_key(sym: str, credentials: dict[str, str] | None) -> str:
    """Credential and canonical policy scoped coalescing; never persist secrets."""
    token = (credentials or {}).get("eodhd_api_key")
    if not isinstance(token, str) or not token.strip():
        raise UnauthorizedError("Missing EODHD credential. Set EODHD_API_KEY.")
    bridge = _capture.configured_bridge()
    scope = bridge.cache_scope() if bridge is not None else ('standalone',)
    identity = json.dumps([scope, token, sym], separators=(",", ":"))
    return 'v3_' + hashlib.sha256(identity.encode()).hexdigest()


def _reset_cache_for_tests() -> None:
    _cache.clear()
    _locks.clear()


def _get_lock(sym: str) -> Lock:
    lock = _locks.get(sym)
    if lock is None:
        lock = _locks[sym] = Lock()
    return lock


def _cached(sym: str) -> _FundamentalsCacheEntry | None:
    hit = _cache.get(sym)
    if hit is None:
        return None
    expiry, payload = hit
    if time.monotonic() >= expiry:
        _cache.pop(sym, None)
        return None
    return payload


def _fetch_sync(sym: str, credentials: dict[str, str] | None) -> dict:
    client = get_client(credentials)
    try:
        with client:
            response = client.get_fundamentals_data(sym)
    except (OpenBBError, UnauthorizedError):
        raise
    except Exception as exc:  # noqa: BLE001 - mapped below
        raise_sdk_error(exc, f"fundamentals for '{sym}'")
    if not isinstance(response, dict) or not response:
        raise EmptyDataError(f"EODHD returned no fundamentals for '{sym}'.")
    return response


# --- L2: ArcticDB read-through (best-effort; never raises) ---------------------
def _arctic_library():
    """Return the ArcticDB cache library, or None if unavailable/unconfigured.

    Soft dependency: openbb-arcticdb is present in the container but not required
    for this extension to work (standalone dev, MinIO down). Any failure -> None.
    """
    try:
        # pylint: disable=import-outside-toplevel
        from openbb_arcticdb.utils import get_library, resolve_config

        uri, library = resolve_config(library=_L2_LIBRARY)
        return get_library(uri, library, create_if_missing=True)
    except Exception:  # noqa: BLE001 - L2 is optional; degrade to L1 + live fetch
        return None


def _l2_get(sym: str) -> _FundamentalsCacheEntry | None:
    """Fresh cached bundle for sym, or None. Never raises."""
    try:
        lib = _arctic_library()
        if lib is None or not lib.has_symbol(sym):
            return None
        # Read the metadata paired with this exact payload version.
        cached = lib.read(sym)
        meta = cached.metadata or {}
        fetched = meta.get("fetched_at")
        if not fetched:
            return None
        stored_at = datetime.fromisoformat(fetched)
        now = datetime.now(timezone.utc)
        if (now - stored_at).total_seconds() > _l2_ttl_seconds():
            return None
        # Keep legacy TTL and payload behavior, including when the wall clock has rolled back.
        raw_stamp = meta.get("retrieved_at")
        retrieved_at = None
        if stored_at <= now and _CACHE_UTC_TIMESTAMP.fullmatch(fetched) and isinstance(raw_stamp, str):
            if _CACHE_UTC_TIMESTAMP.fullmatch(raw_stamp):
                try:
                    candidate = datetime.fromisoformat(raw_stamp)
                    if candidate <= stored_at:
                        retrieved_at = candidate
                except ValueError:
                    pass
        return _FundamentalsCacheEntry(json.loads(cached.data["payload"].iloc[0]), retrieved_at, stored_at)
    except Exception:  # noqa: BLE001
        return None


def _l2_put(sym: str, acquisition: FundamentalsAcquisition) -> datetime | None:
    """Persist a bundle and return the observed write time when available."""
    stored_at = None
    try:
        lib = _arctic_library()
        if lib is None:
            return None
        # pylint: disable=import-outside-toplevel
        from pandas import DataFrame, Timestamp

        stored_at = datetime.now(timezone.utc)
        df = DataFrame({"payload": [json.dumps(acquisition.payload)]}, index=[Timestamp(stored_at)])
        metadata = {"fetched_at": stored_at.isoformat()}
        if acquisition.retrieved_at is not None and acquisition.retrieved_at <= stored_at:
            metadata["retrieved_at"] = acquisition.retrieved_at.isoformat()
        lib.write(sym, df, metadata=metadata, prune_previous_versions=True)
    except Exception:  # noqa: BLE001
        pass
    return stored_at


def _read_cached_through_sync(key: str, fetch) -> _FundamentalsCacheEntry:
    """L2 get -> fetch() -> L2 put. Runs in a worker thread."""
    hit = _l2_get(key)
    if hit is not None:
        return hit
    started_at = datetime.now(timezone.utc)
    payload = fetch()
    completed_at = datetime.now(timezone.utc)
    retrieved_at = completed_at if started_at <= completed_at else None
    stored_at = _l2_put(key, FundamentalsAcquisition(payload, retrieved_at)) or datetime.now(timezone.utc)
    if retrieved_at is not None and retrieved_at > stored_at:
        retrieved_at = None
    return _FundamentalsCacheEntry(payload, retrieved_at, stored_at)


async def _get_cached(key: str, fetch) -> _FundamentalsCacheEntry:
    """L1 -> single-flight lock -> L2/fetch, all under one cache key."""
    cached = _cached(key)
    if cached is not None:
        return cached
    async with _get_lock(key):
        cached = _cached(key)  # another coroutine may have filled it while we waited
        if cached is not None:
            return cached
        entry = await to_thread(_read_cached_through_sync, key, fetch)
        now = time.monotonic()
        for k in [k for k, (exp, _) in list(_cache.items()) if exp <= now]:
            _cache.pop(k, None)
        _cache[key] = (now + _TTL_SECONDS, entry)
        return entry


def _managed_fetch_sync(sym, credentials, *, client=None):
    """Observe response content before the pinned SDK decodes JSON or raises."""
    client = client or get_client(credentials)
    observed = []
    def observe(response, *args, **kwargs):
        observed.append((response.content, response.status_code, datetime.now(timezone.utc)))
        return response
    client._session.hooks.setdefault('response', []).append(observe)
    try:
        with client:
            client.get_fundamentals_data(sym)
    except Exception:
        if not observed:
            raise _capture.CaptureUnavailable('provider transport unavailable') from None
    if len(observed) != 1:
        raise _capture.CaptureUnavailable('expected one observed fundamentals response')
    return observed[0]


def _read_through_sync(sym: str, credentials: dict[str, str] | None) -> dict:
    bridge = _capture.configured_bridge()
    if bridge is not None:
        credentials = _capture.managed_credentials(credentials)
        client = get_client(credentials)
        if getattr(client._session, '_temporaldb_capture', False):
            try:
                with client:
                    value = client.get_fundamentals_data(sym)
            except (OpenBBError, UnauthorizedError, _capture.CaptureUnavailable):
                raise
            except Exception as exc:  # noqa: BLE001 - mapped as in _fetch_sync
                raise_sdk_error(exc, f"fundamentals for '{sym}'")
            if not isinstance(value, dict) or not value:
                raise EmptyDataError(f"EODHD returned no fundamentals for '{sym}'.")
            receipt = client._session._temporaldb_last_receipt
            return _capture.CapturedBundle(value, capture_id=receipt['capture_id'], stale=receipt['stale'], received_at=receipt['received_at'])
        # Optional host seam for injected SDKs: B4 observes bytes before decoding.
        value = bridge.readthrough('fundamentals', {'symbol': sym}, lambda: _managed_fetch_sync(sym, credentials, client=client))
        from openbq_eodhd.transport import record_receipt
        record_receipt(dict(capture_id=value.capture_id,stale=value.stale,received_at=value.received_at,representation='requests-decoded-content'))
        return value
    return _read_cached_through_sync(_cache_key(sym, credentials), lambda: _fetch_sync(sym, credentials)).payload


async def get_bundle_acquisition(
    symbol: str, exchange: str, credentials: dict[str, str] | None,
) -> FundamentalsAcquisition:
    """Return the same cached fundamentals bundle with its original acquisition time."""
    sym = qualify(symbol, exchange)
    if _capture.configured_bridge() is not None:
        payload = await to_thread(_read_through_sync, sym, credentials)
        raw_stamp = getattr(payload, 'received_at', None)
        vintage = None
        if not getattr(payload, 'stale', True) and isinstance(raw_stamp, str) and _CACHE_UTC_TIMESTAMP.fullmatch(raw_stamp):
            candidate = datetime.fromisoformat(raw_stamp)
            if candidate <= datetime.now(timezone.utc):
                vintage = candidate
        return FundamentalsAcquisition(payload, vintage)
    cached = await _get_cached(_cache_key(sym, credentials), lambda: _fetch_sync(sym, credentials))
    # The L1 monotonic TTL outlives wall-clock adjustments. Only the stamp depends on the wall time.
    vintage = cached.retrieved_at
    if vintage is not None and cached.stored_at > datetime.now(timezone.utc):
        vintage = None
    return FundamentalsAcquisition(cached.payload, vintage)


async def get_bundle(
    symbol: str, exchange: str, credentials: dict[str, str] | None,
) -> dict:
    return (await get_bundle_acquisition(symbol, exchange, credentials)).payload


def _fetch_earnings_v11_sync(sym: str, credentials: dict[str, str] | None) -> dict:
    response = sdk_call(
        credentials,
        lambda c: rest_json(c, f"v1.1/fundamentals/{sym}", {"filter": "Earnings"}),
        f"earnings for '{sym}'",
    )
    if not isinstance(response, dict) or not response:
        raise EmptyDataError(f"EODHD returned no earnings for '{sym}'.")
    return response


async def get_earnings_v11(
    symbol: str, exchange: str, credentials: dict[str, str] | None
) -> dict:
    """v1.1 `Earnings` object, cached apart from the bundle (own L1/L2 key)."""
    sym = qualify(symbol, exchange)
    if _capture.configured_bridge() is not None:
        # The managed transport supplies server-owned cache receipts on every
        # acquisition; a local L1 hit would omit that required acknowledgement.
        return await to_thread(_fetch_earnings_v11_sync, sym, _capture.managed_credentials(credentials))
    return (await _get_cached(
        _cache_key(sym + _EARNINGS_V11_SUFFIX, credentials), lambda: _fetch_earnings_v11_sync(sym, credentials)
    )).payload


def _rows(section: Any) -> list[dict]:
    if isinstance(section, dict):
        return [v for v in section.values() if isinstance(v, dict)]
    if isinstance(section, list):
        return [v for v in section if isinstance(v, dict)]
    return []


def general(b: dict) -> dict:
    return b.get("General") or {}


def technicals(b: dict) -> dict:
    return b.get("Technicals") or {}


def highlights(b: dict) -> dict:
    return b.get("Highlights") or {}


def valuation(b: dict) -> dict:
    return b.get("Valuation") or {}


def shares_stats(b: dict) -> dict:
    return b.get("SharesStats") or {}


def analyst_ratings(b: dict) -> dict:
    return b.get("AnalystRatings") or {}


def esg(b: dict) -> dict:
    return b.get("ESGScores") or {}


def etf_data(b: dict) -> dict:
    return b.get("ETF_Data") or {}


def holders_institutions(b: dict) -> list[dict]:
    return _rows((b.get("Holders") or {}).get("Institutions"))


def holders_funds(b: dict) -> list[dict]:
    return _rows((b.get("Holders") or {}).get("Funds"))


def earnings_history(b: dict) -> list[dict]:
    return _rows((b.get("Earnings") or {}).get("History"))


def earnings_trend(b: dict) -> list[dict]:
    return _rows((b.get("Earnings") or {}).get("Trend"))
