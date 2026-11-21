# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""EODHD interest rates (/rates/reference-rates, /rates/policy-rates).

Reference rates: SOFR, EFFR, OBFR, SONIA and ESTR, one row per day, with the
NY Fed percentiles and volume where the source publishes them. Policy rates:
the ECB's deposit, refinancing and marginal lending rates.

Both endpoints cap a page at 100 rows and take filter[from]/filter[to], so a
query with no start date asks for the last year rather than the whole series.
EODHD sends percents; each standard model says, per field, whether it stores a
percent or a normalized fraction (`_jsonapi.stored_percent`).
"""

from datetime import date as dateType, timedelta
from typing import Any, Literal

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.ecb_interest_rates import (
    EuropeanCentralBankInterestRatesData,
    EuropeanCentralBankInterestRatesParams,
)
from openbb_core.provider.standard_models.euro_short_term_rate import (
    EuroShortTermRateData, EuroShortTermRateQueryParams,
)
from openbb_core.provider.standard_models.federal_funds_rate import (
    FederalFundsRateData, FederalFundsRateQueryParams,
)
from openbb_core.provider.standard_models.overnight_bank_funding_rate import (
    OvernightBankFundingRateData, OvernightBankFundingRateQueryParams,
)
from openbb_core.provider.standard_models.sofr import SOFRData, SOFRQueryParams
from openbb_core.provider.standard_models.sonia_rates import SONIAData, SONIAQueryParams
from openbb_core.provider.utils.errors import EmptyDataError
from pydantic import Field

from openbq_eodhd.models._jsonapi import fetch_rows, stored_percent

_PAGE = 100  # the endpoints' maximum
_MAX_PAGES = 30  # 3,000 rows: about twelve years of one daily rate
_DEFAULT_DAYS = 365
_PERCENTILES = {"p1": "percentile_1", "p25": "percentile_25", "p75": "percentile_75", "p99": "percentile_99"}


def _window(query) -> dict[str, str | None]:
    start = query.start_date or (dateType.today() - timedelta(days=_DEFAULT_DAYS))
    return {
        "filter[from]": str(start),
        "filter[to]": str(query.end_date) if query.end_date else None,
    }


async def _fetch(credentials, endpoint: str, code: str, query, context: str) -> list[dict]:
    from asyncio import to_thread

    return await to_thread(
        fetch_rows, credentials, endpoint,
        {"filter[code]": code, **_window(query)}, context,
        page_limit=_PAGE, max_pages=_MAX_PAGES,
    )


def _rate_rows(model: type, data: list[dict]) -> list[dict]:
    """Standard rows, oldest first, for one reference rate."""
    rows = []
    for it in data:
        if it.get("date") is None or it.get("rate") is None:
            continue
        row: dict[str, Any] = {
            "date": it["date"],
            "rate": stored_percent(model, "rate", it["rate"]),
            "source": it.get("source"),
        }
        for theirs, ours in _PERCENTILES.items():
            value = (it.get("percentiles") or {}).get(theirs)
            if ours in model.model_fields and value is not None:
                row[ours] = stored_percent(model, ours, value)
        if "volume" in model.model_fields and it.get("volume_billion_usd") is not None:
            row["volume"] = float(it["volume_billion_usd"])
        rows.append(row)
    return sorted(rows, key=lambda r: r["date"])


def _reference_rate_fetcher(code: str, params_cls: type, data_cls: type, label: str) -> type:
    """A Fetcher for one reference-rate code; the five differ only by code and model."""

    class _Fetcher(Fetcher[params_cls, list[data_cls]]):  # type: ignore[valid-type]
        @staticmethod
        def transform_query(params: dict[str, Any]):
            return params_cls(**params)

        @staticmethod
        async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
            return await _fetch(credentials, "rates/reference-rates", code, query, label)

        @staticmethod
        def transform_data(query, data: list[dict], **kwargs):  # pylint: disable=unused-argument
            rows = [data_cls.model_validate(r) for r in _rate_rows(data_cls, data)]
            if not rows:
                raise EmptyDataError(f"EODHD returned no usable {label} rows.")
            return rows

    _Fetcher.__name__ = _Fetcher.__qualname__ = f"EODHD{data_cls.__name__.removeprefix('EODHD').removesuffix('Data')}Fetcher"
    _Fetcher.__doc__ = f"EODHD {label} ({code})."
    return _Fetcher


class EODHDSOFRQueryParams(SOFRQueryParams):
    """EODHD SOFR Query. Without a start date, the last year."""


class EODHDSOFRData(SOFRData):
    """EODHD SOFR Data. Volume is in billions of US dollars."""

    source: str | None = Field(default=None, description="Publisher EODHD read the rate from.")


class EODHDFederalFundsRateQueryParams(FederalFundsRateQueryParams):
    """EODHD Federal Funds Rate Query. Without a start date, the last year."""


class EODHDFederalFundsRateData(FederalFundsRateData):
    """EODHD Effective Federal Funds Rate Data. Volume is in billions of US dollars."""

    source: str | None = Field(default=None, description="Publisher EODHD read the rate from.")


class EODHDOvernightBankFundingRateQueryParams(OvernightBankFundingRateQueryParams):
    """EODHD Overnight Bank Funding Rate Query. Without a start date, the last year."""


class EODHDOvernightBankFundingRateData(OvernightBankFundingRateData):
    """EODHD Overnight Bank Funding Rate Data. Volume is in billions of US dollars."""

    source: str | None = Field(default=None, description="Publisher EODHD read the rate from.")


class EODHDSONIAQueryParams(SONIAQueryParams):
    """EODHD SONIA Query. Without a start date, the last year."""


class EODHDSONIAData(SONIAData):
    """EODHD SONIA Data."""

    source: str | None = Field(default=None, description="Publisher EODHD read the rate from.")


class EODHDEuroShortTermRateQueryParams(EuroShortTermRateQueryParams):
    """EODHD Euro Short-Term Rate Query. Without a start date, the last year."""


class EODHDEuroShortTermRateData(EuroShortTermRateData):
    """EODHD Euro Short-Term Rate Data."""

    source: str | None = Field(default=None, description="Publisher EODHD read the rate from.")


EODHDSOFRFetcher = _reference_rate_fetcher("SOFR", EODHDSOFRQueryParams, EODHDSOFRData, "SOFR")
EODHDFederalFundsRateFetcher = _reference_rate_fetcher(
    "EFFR", EODHDFederalFundsRateQueryParams, EODHDFederalFundsRateData, "effective federal funds rate"
)
EODHDOvernightBankFundingRateFetcher = _reference_rate_fetcher(
    "OBFR", EODHDOvernightBankFundingRateQueryParams, EODHDOvernightBankFundingRateData,
    "overnight bank funding rate",
)
EODHDSONIAFetcher = _reference_rate_fetcher("SONIA", EODHDSONIAQueryParams, EODHDSONIAData, "SONIA")
EODHDEuroShortTermRateFetcher = _reference_rate_fetcher(
    "ESTR", EODHDEuroShortTermRateQueryParams, EODHDEuroShortTermRateData, "euro short-term rate"
)

# The standard model's interest_rate_type -> EODHD policy-rate code.
ECB_CODES = {"deposit": "ECB_DFR", "lending": "ECB_MLF", "refinancing": "ECB_MRO"}


class EODHDEuropeanCentralBankInterestRatesParams(EuropeanCentralBankInterestRatesParams):
    """EODHD ECB Interest Rates Query. Without a start date, the last year."""

    interest_rate_type: Literal["deposit", "lending", "refinancing"] = Field(
        default="lending",
        description="deposit (DFR), lending (marginal lending facility) or refinancing (MRO).",
    )


class EODHDEuropeanCentralBankInterestRatesData(EuropeanCentralBankInterestRatesData):
    """EODHD ECB Interest Rates Data."""

    code: str | None = Field(default=None, description="EODHD policy-rate code.")


class EODHDEuropeanCentralBankInterestRatesFetcher(
    Fetcher[
        EODHDEuropeanCentralBankInterestRatesParams,
        list[EODHDEuropeanCentralBankInterestRatesData],
    ]
):
    """EODHD ECB key interest rates."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDEuropeanCentralBankInterestRatesParams:
        return EODHDEuropeanCentralBankInterestRatesParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        return await _fetch(
            credentials, "rates/policy-rates", ECB_CODES[query.interest_rate_type], query,
            "ECB policy rates",
        )

    @staticmethod
    def transform_data(query, data: list[dict], **kwargs):  # pylint: disable=unused-argument
        model = EODHDEuropeanCentralBankInterestRatesData
        rows = [
            model.model_validate({
                "date": it["date"],
                "rate": stored_percent(model, "rate", it["rate"]),
                "code": it.get("code"),
            })
            for it in data
            if it.get("date") and it.get("rate") is not None
        ]
        if not rows:
            raise EmptyDataError("EODHD returned no usable ECB policy-rate rows.")
        return sorted(rows, key=lambda r: r.date)
