# Linked KPI acquisition contract

## Source readiness by preset

| Preset | Verified source and encoding | Live headline | Acquired-reference comparison |
| --- | --- | --- | --- |
| `price` | Delayed quote v1 `close` has an exchange-qualified code and UTC Unix-seconds event time. | **Blocked-source.** The quote omits currency, per-share adjustment basis and issuer identity. Screener `adjusted_close` is a different price. | Requires the same listed security, class, denomination and adjustment basis on the two observations. |
| `market_cap` | `Highlights.MarketCapitalization` is full-unit total issuer capitalization. `MarketCapitalizationMln` has another scale. | **Blocked-source.** The Highlights amount has no metric-specific monetary denomination or explicit multi-class pricing basis. Screener gives listing-denominated caps but does not qualify the issuer and valuation basis for a selected KPI. | Requires two acquired observations on a common issuer, listed class, currency and capitalization basis. |
| `dividend_yield` | `Highlights.DividendYield` is trailing annual dividends per share divided by the snapshot price, encoded as a fraction. | **Ready** for the primary US common-stock headline on the two routes below. Measurement date, share class, corporate-action basis and precise trailing period are unknown. | **Blocked-source** on live results. One reported observation and no proved class, adjustment basis or valuation date. |
| `pe_ratio` | The existing routes map `Highlights.PERatio` to `pe_ratio`, a trailing earnings multiple. | **Ready** in the same narrow scope. `Valuation.TrailingPE` is a separate upstream field and `ForwardPE` a different definition. | **Blocked-source** on live results. The snapshot omits comparable price/earnings adjustment and class basis, price event and prior observations. |
| `eps_ttm` | `Highlights.DilutedEpsTTM` and `EarningsShare` denote trailing diluted EPS. | **Blocked-source.** Precise trailing period, reporting basis, adjustment and metric-specific currency per diluted share are absent. | Requires different acquired vintages of the identical actual reporting period, reporting standard and per-share basis. |
| `revenue_ttm` | `Highlights.RevenueTTM` is trailing revenue. | **Blocked-source.** The snapshot does not attach the exact reporting period, accounting basis and currency of this amount. | Requires different acquired vintages of the identical actual reporting period, reporting standard, currency and adjustment basis. |

EODHD distinguishes file refresh, the most recent quarter, the quote time and statement-specific reporting currency. Its example reconciles the price underlying valuation metrics and explains why trading currency can differ from reporting currency. Its screener documentation describes listing currencies and duplicated company-wide market capitalizations across share classes. [Common stock glossary](https://eodhd.com/financial-academy/financial-faq/fundamentals-glossary-common-stock), [Statement and valuation example](https://course.eodhd.com/lessons/co-statements-inside-the-payload), [Screener fields](https://eodhd.com/financial-apis/stock-market-screener-api), [Share classes in the screener](https://course.eodhd.com/lessons/nsd-screener-grammar).

### Representative public captures

The public DEMO fundamentals response retrieved on 2026-11-20 at 04:16:46 UTC contained the following excerpt. A second acquisition at approximately 06:10 UTC verified `PrimaryTicker=AAPL.US` and `Type=Common Stock`. The date under General is file freshness. The statement reported a quarterly amount in USD for September 30 with a November 3 filing date. Neither date nor the statement currency qualifies the Highlights TTM amounts. [Public fundamentals capture endpoint](https://eodhd.com/api/fundamentals/AAPL.US?api_token=demo&fmt=json), [Fundamentals contract](https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds).

```json
{
  "General": {"Code":"AAPL","Exchange":"NASDAQ","CurrencyCode":"USD","UpdatedAt":"2026-11-19"},
  "Highlights": {"MarketCapitalization":5121867513856,"DividendYield":0.003,"PERatio":39.2483,"DilutedEpsTTM":8.98,"RevenueTTM":478833999872,"MostRecentQuarter":"2026-09-30"},
  "Valuation": {"TrailingPE":39.2483,"ForwardPE":36.7647}
}
```

The v1 quote retrieved at 04:16:43 UTC contained `{"code":"AAPL.US","timestamp":1795122000,"close":349.21,"previousClose":352.45}`. Its event time was November 19 at 21:00 UTC. P/E multiplied by EPS yields 352.449734 in the separate fundamentals capture. Matching the previous close to cents does not date the valuation or qualify its currency. US quote v2 exposes its own currency and last-trade event on a separate acquisition. [Public v1 capture endpoint](https://eodhd.com/api/real-time/AAPL.US?api_token=demo&fmt=json), [Delayed quote fields](https://eodhd.com/financial-apis/live-ohlcv-stocks-api), [US v2 contract](https://eodhd.com/financial-apis/live-v2-for-us-stocks-extended-quotes-2025).

## Completed acquisition

For KeyMetrics and FinancialRatios, enable `chart_source=true` and select the full `results` array. The standard paths are `/api/v1/equity/fundamental/metrics` and `/api/v1/equity/fundamental/ratios` with `provider=eodhd`. Their `/api/v1/eodhd/equity/fundamental/…` mirrors default to EODHD. The supported request uses a single bare or US-qualified symbol, the US default or explicit US exchange, and no other source parameter. An explicit ratios limit is outside this single-snapshot contract. Raw General must establish the requested primary ticker, code, known US venue and common stock type. Qualification reuses the original timestamp attached to the shared bundle acquisition. A missing qualified timestamp or identity returns the ordinary rows.

The wire receipt is at `extra.results_metadata.chart_sources`. Its origin distinguishes `eodhd-key-metrics` and `eodhd-financial-ratios`. Both emit the original one wide row, with qualified `symbol` and additive `metric_vintage` on the qualified opt-in only. One row can evidence two independent preset observations. A missing Highlights key produces no metric binding. An explicitly null source measurement remains null. Numeric zero survives, including a zero yield. A nonnumeric, boolean or nonfinite source value becomes unavailable and sets the raw `unit_conflict` before model coercion. Original class, measurement date, adjustment basis and basis reference stay explicitly null. Any explicit observation fields on the delivered wide row must agree with every headline they describe, including the unknown-date flag and TTM period kind. Raw `currency`, `period_ending` and `fiscal_period` on the ordinary row confer no currency, class, measurement date or precise period on either headline. Neither the quote, a second fundamentals request nor archived vintages participate.

## Explicit acquired snapshots

A trusted local JSON table can declare `origin: "acquired-snapshot"`. Each row describes exactly one original preset measurement for the common qualified `issuer`, qualified `listing` and `share_class` (null when unknown). Each row carries `metric`, `observation_id`, UTC `vintage`, `unit_kind`, `currency`, `as_of`, `period_kind`, `period_start`, `period_end`, `adjustment_basis`, `reporting_basis` and `basis_reference`. The actual value belongs to its named original preset column. It is a finite number or explicit null. The adapter checks these row facts against the full closed declaration, irrespective of row order. Backend requests cannot import local declarations. Local files cannot claim live acquisition origins.

The receipt's `metrics[preset]` is a full observation plus `additional_observations`, an array of equally ranked full observations. There is no implicit selection. An observation's `origin` is the physical preset column, and `unit` gives the acquired kind and monetary denomination when applicable. Its UTC `vintage` is the acquisition vintage of that observation. Its `as_of` is an actual measurement day or UTC event, or explicit null with `as_of_unknown: true`. Each observation ID must be unique throughout the receipt. Known identifiers and basis references exclude bare amounts and reserved missing-value markers such as unknown, NA and unavailable. Use null for an unknown class or basis reference. Duplicate financial coordinates refuse even under different IDs. Unknown class or adjustment basis never proves reference comparability. Period dates, units, currencies and bases may legitimately differ across observations. Differences block the incompatible pair, while either individually valid observation remains available as a headline.

| Preset | Unit | Explicit adjustment basis when known |
| --- | --- | --- |
| price | money-per-share | as-traded or split-adjusted |
| market_cap | money | issuer-all-classes |
| dividend_yield | fraction | trailing-dividend-per-share |
| pe_ratio | multiple | trailing-diluted-earnings |
| eps_ttm | money-per-share | diluted-as-reported or diluted-split-adjusted |
| revenue_ttm | money | as-reported |

EPS and revenue require the actual start and end of the same TTM interval, covering either 52 or 53 fiscal weeks or a calendar year ending the day before its anniversary (a February 29 start runs through February 28 of the following year), and a named reporting standard (US-GAAP, IFRS or local-GAAP). The producer supplies a stable `basis_reference` identifying the exact reporting, share and adjustment conventions used, including the share basis epoch when applicable. References require this positive matching identity alongside the same qualified issuer, listing, known class, unit, currency and adjustment. A local-GAAP comparison requires the same specific accounting basis identifier. Price/cap/yield/P-E date comparisons require both actual measurement dates. A user range or target belongs to settings and is visibly a user assumption.

The currency wire value preserves a recognized monetary denomination, including the independent GBX quote unit. It never converts amounts. The allowlist uses the ISO 4217 current and historical lists and excludes testing, no-currency, precious-metal and bond-market placeholders. [SIX currency lists](https://www.six-group.com/en/products-services/financial-information/market-reference-data/data-standards.html), [EODHD's GBX example](https://eodhd.com/financial-apis/api-splits-dividends).

Raw counts, diagnostic flags and additive omissions remain through linked projection. Only string coordinates enter omission plans. Numeric measures stay exclusively on delivered rows. A builder must check the decoded source, raw conflicts, the active acquired coordinates, unambiguous original numeric field bindings, row/cell state and projection restrictions before using an observation. These are source interfaces for the separate linked-KPI renderer and worker.
