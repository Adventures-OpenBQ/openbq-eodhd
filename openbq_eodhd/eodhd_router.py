# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""EODHD namespace router: obb.eodhd.* / /api/v1/eodhd/...

Mirror commands over the eodhd fetchers, GENERATED rather than hand-written
(user ruling 2026-09-30). For every key in eodhd_provider.fetcher_dict that has
a row in MIRRORS, one command is registered at the standard path under the
eodhd prefix, with model "Eodhd" + <standard model>. That model is a second
key for the very same fetcher (see openbq_eodhd/__init__.py), so the command's
only provider is eodhd and its rows, errors and HTTP statuses are identical to
the standard command with provider=eodhd. No data code lives here.

Why a static table and not openbb_core's CommandMap: this module is imported
BY openbb's extension loader (RouterLoader.from_extensions). Calling
CommandMap() here re-enters that lru_cache'd loader from a half-initialised
state and loads every extension a second time -- it works by accident and
doubles startup (probed on core 1.6.13, 2026-09-30). The table has no
import-time dependency on any other extension, and tests/test_eodhd_router.py
pins it to the real CommandMap after load, so a wrong or missing row fails CI.
The standard routes carry no widget_config, so the sub-category has to live
here anyway.

A line whose fetcher_dict lacks a key simply generates no mirror for it, which
is what keeps this file byte-identical across the release lines. A non-standard
key (v12's EquityTicks) has no row and so no mirror.
"""

import json
from pathlib import Path

from openbb_core.app.model.command_context import CommandContext
from openbb_core.app.model.obbject import OBBject
from openbb_core.app.provider_interface import (
    ExtraParams,
    ProviderChoices,
    StandardParams,
)
from openbb_core.app.query import Query as OBBQuery
from openbb_core.app.router import Router

from openbq_eodhd import eodhd_provider

# standard model -> (standard path, widget subCategory)
MIRRORS: dict[str, tuple[str, str]] = {
    "EquityHistorical": ("/equity/price/historical", "Price"),
    "EquityQuote": ("/equity/price/quote", "Price"),
    "EquityInfo": ("/equity/profile", "Profile"),
    "EquitySearch": ("/equity/search", "Search"),
    "EquityScreener": ("/equity/screener", "Screener"),
    "HistoricalMarketCap": ("/equity/historical_market_cap", "Profile"),
    "IncomeStatement": ("/equity/fundamental/income", "Fundamental"),
    "BalanceSheet": ("/equity/fundamental/balance", "Fundamental"),
    "CashFlowStatement": ("/equity/fundamental/cash", "Fundamental"),
    "HistoricalDividends": ("/equity/fundamental/dividends", "Fundamental"),
    "HistoricalSplits": ("/equity/fundamental/historical_splits", "Fundamental"),
    "HistoricalEps": ("/equity/fundamental/historical_eps", "Fundamental"),
    "KeyMetrics": ("/equity/fundamental/metrics", "Fundamental"),
    "FinancialRatios": ("/equity/fundamental/ratios", "Fundamental"),
    "KeyExecutives": ("/equity/fundamental/management", "Fundamental"),
    "EsgScore": ("/equity/fundamental/esg_score", "Fundamental"),
    "TrailingDividendYield": ("/equity/fundamental/trailing_dividend_yield", "Fundamental"),
    "InstitutionalOwnership": ("/equity/ownership/institutional", "Ownership"),
    "EquityOwnership": ("/equity/ownership/major_holders", "Ownership"),
    "InsiderTrading": ("/equity/ownership/insider_trading", "Ownership"),
    "ShareStatistics": ("/equity/ownership/share_statistics", "Ownership"),
    "GovernmentTrades": ("/equity/ownership/government_trades", "Ownership"),
    "AnalystEstimates": ("/equity/estimates/historical", "Estimates"),
    "ForwardEpsEstimates": ("/equity/estimates/forward_eps", "Estimates"),
    "PriceTargetConsensus": ("/equity/estimates/consensus", "Estimates"),
    "CalendarEarnings": ("/equity/calendar/earnings", "Calendar"),
    "CalendarIpo": ("/equity/calendar/ipo", "Calendar"),
    "CalendarSplits": ("/equity/calendar/splits", "Calendar"),
    "EtfHistorical": ("/etf/historical", "Price"),
    "EtfSearch": ("/etf/search", "Search"),
    "EtfInfo": ("/etf/info", "Reference"),
    "EtfHoldings": ("/etf/holdings", "Reference"),
    "EtfSectors": ("/etf/sectors", "Reference"),
    "EtfCountries": ("/etf/countries", "Reference"),
    "CryptoHistorical": ("/crypto/price/historical", "Price"),
    "CryptoSearch": ("/crypto/search", "Search"),
    "CurrencyHistorical": ("/currency/price/historical", "Price"),
    "CurrencyPairs": ("/currency/search", "Search"),
    "CurrencySnapshots": ("/currency/snapshots", "Price"),
    "AvailableIndices": ("/index/available", "Reference"),
    "IndexHistorical": ("/index/price/historical", "Price"),
    "EconomicCalendar": ("/economy/calendar", "Calendar"),
    "EconomicIndicators": ("/economy/indicators", "Macro"),
    "GdpReal": ("/economy/gdp/real", "Macro"),
    "GdpNominal": ("/economy/gdp/nominal", "Macro"),
    "ConsumerPriceIndex": ("/economy/cpi", "Macro"),
    "Unemployment": ("/economy/unemployment", "Macro"),
    "CountryProfile": ("/economy/country_profile", "Macro"),
    "WorldNews": ("/news/world", "News"),
    "CompanyNews": ("/news/company", "News"),
    "TreasuryRates": ("/fixedincome/government/treasury_rates", "Government"),
    "YieldCurve": ("/fixedincome/government/yield_curve", "Government"),
}

router = Router(prefix="", description="EODHD provider namespace (obb.eodhd.*).")


def _register_mirror(std_model: str, path: str, sub_category: str) -> None:
    """Register one generated mirror command for `std_model` at `path`."""

    # The signature is the standard command shape: Router.command's
    # SignatureInspector validates these four annotations and injects the
    # Eodhd<Std> model's provider/params into them. The body must reference NO
    # closure variable: a referenced free variable would show up in locals()
    # and be passed on to Query.
    async def mirror(
        cc: CommandContext,
        provider_choices: ProviderChoices,
        standard_params: StandardParams,
        extra_params: ExtraParams,
    ) -> OBBject:
        return await OBBject.from_query(OBBQuery(**locals()))

    # operationId = module + __name__, and the Python interface keys on the
    # path, so every generated function needs its own name.
    mirror.__name__ = mirror.__qualname__ = path.strip("/").replace("/", "_")
    mirror.__doc__ = (
        f"EODHD {path}: the standard command's EODHD data (provider=eodhd), "
        "in the eodhd namespace."
    )
    router.command(
        mirror,
        model=f"Eodhd{std_model}",
        path=path,
        widget_config={"category": "EODHD", "subCategory": sub_category, "source": ["EODHD"]},
    )


for _std_model in list(eodhd_provider.fetcher_dict):
    if _std_model in MIRRORS:
        _register_mirror(_std_model, *MIRRORS[_std_model])


_APPS_JSON = Path(__file__).parent / "assets" / "apps.json"


@router.api_router.get("/apps.json", include_in_schema=False)
async def eodhd_apps() -> list:
    """Serve the bundled EODHD dashboard template."""
    return json.loads(_APPS_JSON.read_text(encoding="utf-8"))
