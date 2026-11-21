# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""Paged reads of EODHD's JSON:API-shaped endpoints ({"data": [...], "meta", "links"}).

The newer EODHD families (rates, credit risk, SEC filings, real estate,
commodities, options) all answer in this envelope and cap a page at 100 to
1,000 rows. `fetch_rows` walks `page[offset]` until a short page or the page
budget is spent, through `_client.rest_json`, so errors map like every other
call in the extension.
"""

from typing import Any

from openbb_core.provider.utils.errors import EmptyDataError, UnauthorizedError

from openbq_eodhd.models._client import rest_json, sdk_call


def fetch_rows(
    credentials: dict[str, str] | None,
    endpoint: str,
    params: dict[str, Any],
    context: str,
    *,
    page_limit: int | None = None,
    max_pages: int = 1,
    allow_empty: bool = False,
) -> list[Any]:
    """Rows of `data` from one endpoint, following pages. Synchronous: call via to_thread."""

    def _call(client):
        rows: list[Any] = []
        for page in range(max_pages):
            paged = dict(params)
            if page_limit:
                paged["page[limit]"] = page_limit
                paged["page[offset]"] = page * page_limit
            resp = rest_json(client, endpoint, paged)
            chunk = resp.get("data") if isinstance(resp, dict) else resp
            if chunk is None and isinstance(resp, dict):
                raise UnauthorizedError(
                    f"EODHD ({context}): {resp.get('message') or resp.get('errors') or resp}"
                )
            if isinstance(chunk, dict):  # a single resource, not a collection
                return [chunk]
            rows.extend(chunk or [])
            if not page_limit or len(chunk or []) < page_limit:
                break
        return rows

    rows = sdk_call(credentials, _call, context)
    if not rows and not allow_empty:
        raise EmptyDataError(f"EODHD returned no {context}.")
    return rows


def stored_percent(model: type, field: str, percent: float | None) -> float | None:
    """A percent EODHD sent (3.92) in the unit the standard model's field stores.

    Standard fields tagged `x-frontend_multiply: 100` hold a normalized
    fraction (0.0392) that the frontend multiplies back; untagged fields hold
    the number as shown. Reading the tag keeps this right if core changes it.
    """
    if percent is None:
        return None
    extra = model.model_fields[field].json_schema_extra or {}
    scale = extra.get("x-frontend_multiply") if isinstance(extra, dict) else None
    return round(float(percent) / scale, 8) if scale else float(percent)
