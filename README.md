<!-- Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# openbq-eodhd

The EODHD provider extension for the OpenBB Platform, for the *Adventures in OpenBQ* stack (openbq-docker v9 vendors it under `v9/eodhd/source`). It is bdobb-dev/openbb-docker's `openbb-eodhd` (v9.6.3, fixes through v9.6.6), renamed; see NOTICE for provenance. The provider is still `eodhd` and its routes are still `obb.eodhd.*`.

An [EODHD](https://eodhd.com) (EOD Historical Data) provider extension for the
[OpenBB Platform](https://github.com/OpenBB-finance/OpenBB). Adds `provider="eodhd"`
for historical equity/ETF pricing.

All API calls go through the official
[EODHD Python library](https://github.com/EodHistoricalData/EODHD-APIs-Python-Financial-Library)
(`eodhd.APIClient`), vendored at upstream commit f98e69d — the PyPI release (1.0.32)
predates the SDK's typed errors, request timeouts, and the fundamentals
`filter` parameter.

## Coverage

| OpenBB command | EODHD endpoint | Notes |
| --- | --- | --- |
| `obb.equity.price.historical(..., provider="eodhd")` | `/api/eod`, `/api/intraday` | intervals `1d`, `1W`, `1M`, `1m`, `5m`, `1h` |
| `obb.etf.price.historical(..., provider="eodhd")` | same | same |
| `obb.crypto.price.historical(..., provider="eodhd")` | same (`.CC` symbols) | e.g. `BTC-USD`, `ETH-USD` |
| `obb.currency.price.historical(..., provider="eodhd")` | same (`.FOREX` symbols) | e.g. `EURUSD`, `EUR/USD` |
| `obb.equity.fundamental.income(..., provider="eodhd")` | `/api/fundamentals` → `Financials::Income_Statement` | `period="annual"` or `"quarter"`, `limit=N` |
| `obb.equity.fundamental.balance(..., provider="eodhd")` | `Financials::Balance_Sheet` | same |
| `obb.equity.fundamental.cash(..., provider="eodhd")` | `Financials::Cash_Flow` | same |
| `obb.equity.fundamental.dividends(..., provider="eodhd")` | `/api/div` | ex-date, amount, declaration/record/payment dates |
| `obb.equity.fundamental.historical_splits(..., provider="eodhd")` | `/api/splits` | numerator/denominator + `"n:1"` ratio |

### Historical prices (equity / ETF / crypto / forex)
- Intervals `1d/1W/1M` hit the end-of-day endpoint and carry an extra
  `adjusted_close` field (split/dividend adjusted).
- Intervals `1m/5m/1h` hit the intraday endpoint; bars are UTC and tz-aware.
  EODHD limits intraday history per request (roughly 120 days for `1m`, longer
  for `5m/1h`) — narrow the date window for fine intervals.
- Crypto uses EODHD's `BASE-QUOTE.CC` symbols (bare `BTCUSD`/`BTC/USD` are
  normalized to `BTC-USD.CC`); forex uses `EURUSD.FOREX` (`EUR/USD` normalized).

### Fundamentals
- Three statements: income, balance sheet, cash flow. `period` selects EODHD's
  `yearly` vs `quarterly`; `limit` caps the number of most-recent periods.
- Well-known line items map to OpenBB's conventional field names (`revenue`,
  `net_income`, `total_assets`, `free_cash_flow`, …); every other EODHD field is
  passed through snake_cased, so nothing is dropped.
- Fundamentals require a paid EODHD plan for most tickers (the `demo` token
  covers `AAPL.US`).

### Economic calendar
- `obb.economy.calendar(..., provider="eodhd")` rows carry `source` (publisher,
  e.g. "BLS") and `category` (release, e.g. "Producer Prices"), resolved from
  a static three-level taxonomy of EODHD's event names; unrecognized names
  resolve to `None`.
- EODHD's `/economic-events` caps every request at 1,000 rows; a range with
  more events than that is fetched in successive date windows and merged, so
  a busy three-month window returns every row instead of being cut off.

### Rates, credit, filings and the other All-In-One endpoints

Added in 9.1.0. Each row is an OpenBB standard model, so each has an
`obb.eodhd.*` mirror; all but the options chain are included in EODHD's
All-In-One plan.

| OpenBB standard model | Mirror path | EODHD endpoint | Notes |
| --- | --- | --- | --- |
| `SOFR` | `/fixedincome/rate/sofr` | `/rates/reference-rates` (`SOFR`) | NY Fed percentiles, volume in USD billions |
| `FederalFundsRate` | `/fixedincome/rate/effr` | same (`EFFR`) | percentiles and volume; no target range |
| `OvernightBankFundingRate` | `/fixedincome/rate/overnight_bank_funding` | same (`OBFR`) | percentiles and volume |
| `SONIA` | `/fixedincome/rate/sonia` | same (`SONIA`) | rate only |
| `EuroShortTermRate` | `/fixedincome/rate/estr` | same (`ESTR`) | rate only |
| `EuropeanCentralBankInterestRates` | `/fixedincome/rate/ecb` | `/rates/policy-rates` | deposit, lending or refinancing |
| `TipsYields` | `/fixedincome/government/tips_yields` | `/ust/real-yield-rates` | constant-maturity real yields, not single securities |
| `HighQualityMarketCorporateBond` | `/fixedincome/corporate/hqm` | `/credit-risk/corporate/hqm-yields` | par or spot, monthly, as of one or several dates |
| `RiskPremium` | `/economy/risk_premium` | `/credit-risk/sovereign/risk-premium` | Damodaran, 157 countries, in percent |
| `HousePriceIndex` | `/economy/house_price_index` | `/real-estate/{code}` | BIS, quarterly, index or year-over-year, nominal or real |
| `CommoditySpotPrices` | `/commodity/price/spot` | `/commodities/historical/{code}` | twelve spot series; no precious metals, no futures |
| `CompanyFilings` | `/equity/fundamental/filings` | `/sec-filings/{symbol}/10k`, `/10q`, `/8k` | US only; links to the SEC EDGAR filing index |
| `RevenueBusinessLine` | `/equity/fundamental/revenue_per_segment` | `/sec-companies/{symbol}/segments` | XBRL product and service members |
| `RevenueGeographic` | `/equity/fundamental/revenue_per_geography` | same | XBRL geographic members |
| `IndexConstituents` | `/index/constituents` | `/fundamentals/{CODE}.INDX` (`Components`) | current members with sector and weight |
| `OptionsChains` | `/derivatives/options/chains` | `/mp/unicornbay/options/eod` | needs the US Stock Options marketplace add-on (HTTP 403 without it) |

- Rates default to the last year when no start date is given: the endpoints
  return at most 100 rows a page.
- Percent values follow each standard model: fields the model tags as
  normalized hold fractions (0.0392), the rest hold the percent as shown.
- `OptionsChains` is end of day only, one trade date per call, with implied
  volatility and the five Greeks. Its mapping follows EODHD's published OpenAPI
  description and has **not** been run against the live feed, because the
  development account is not subscribed to it.
- EODHD endpoints with no OpenBB standard model are not wired: sovereign
  credit ratings and CDS spreads, the corporate bond distress index, CDS market
  aggregates, funding-stress spreads, sanctions, symbol change history, CBOE
  Europe indices, SEC company ratios and growth, and tick data.

## Aggregation metadata

The provider-owned [aggregation dictionary](docs/aggregation-metadata.md)
offers versioned, conditional time-aggregation guidance for normalized EOD
price and financial-statement fields. Use
`openbq_eodhd.aggregation.get_aggregation_metadata` to inspect it.
Unknown fields require selection, and temporal rules are separate from
cross-entity aggregation. It does not change fetcher responses or
automatically aggregate data. The seven covered Data models carry each
field's hint into `widgets.json` as a per-column `aggregation` entry (see the
[transport section](docs/aggregation-metadata.md#transport-a-per-column-hint-in-widgetsjson)),
which also gives the three statements typed `columnsDefs` for their mapped
line items.
The contract also defines the [v11 data-mastering integration boundary](docs/aggregation-metadata.md#v11-data-mastering-workstream):
provider-owned rules, versioned canonical bindings, Bronze/Silver/Gold
provenance, and semantics pinned in query receipts.

## Symbols

EODHD symbols are `SYMBOL.EXCHANGE` (e.g. `AAPL.US`, `VOD.LSE`). Pass a bare
symbol and it is qualified with the `exchange` parameter (default `US`), or pass
a fully-qualified symbol to override:

```python
obb.equity.price.historical("AAPL", provider="eodhd")                 # -> AAPL.US
obb.equity.price.historical("VOD", provider="eodhd", exchange="LSE")  # -> VOD.LSE
obb.equity.price.historical("AAPL.US,MSFT.US", provider="eodhd")      # multi-symbol
```

## Credentials

One API key, read from the bare uppercase env var (no `OPENBB_` prefix):

```
EODHD_API_KEY=<your eodhd api token>
```

Get a token at https://eodhd.com. The public `demo` token works for a few
symbols (`AAPL.US`, `MCD.US`, `TSLA.US`, `VTI.US`) and is handy for testing.

## Install

```bash
pip install openbq-eodhd        # or: pip install /path/to/openbq-eodhd
python -c "import openbb; openbb.build()"   # regenerate the static package
```

## License

Apache-2.0 — see `LICENSE`.
