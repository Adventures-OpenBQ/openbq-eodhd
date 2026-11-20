# Dual-axis overlay sources

Reviewed 2026-11-20. Both live recipes are **blocked-source**. Both accept one explicitly acquired, validated local JSON snapshot.

| Recipe | Verified inputs | Missing live evidence |
| --- | --- | --- |
| Adjusted closing price and reported diluted FQ EPS | EODHD daily adjusted close and a separate historical earnings feed | One prepared acquisition with matching issuer, qualified listing, quote/report currency and current split-adjusted share basis. Historical EPS lacks an explicit diluted basis, proven denomination for the sampled row and start of its fiscal quarter. The adapter also omits market-session timing. |
| US CPI and ten-year Treasury yield | The authoritative monthly CPI and daily nominal rate definitions. The provider independently exposes Treasury rates. | The provider's annual CPI uses a different base. No completed request binds the exact pair, their independent observation coordinates, per-input vintages and actual release evidence. |

## Price and earnings

EODHD `adjusted_close` includes splits and cash dividends. Its EOD date identifies the regular-session observation. Historical values can change after a corporate action. The public AAPL.US demo captured on November 19 returned a 2026-08-12 record with `close=302.25` and `adjusted_close=302.0205`. [EODHD price API](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes) · [Adjustment methodology](https://eodhd.com/help/methodology)

In EODHD `Earnings.History`, `date` identifies period end, `reportDate` identifies the actual report date, and `epsActual` is reported non-GAAP EPS. The AAPL.US demo row for 2026-09-30 reported `reportDate=2026-11-02`, `beforeAfterMarket=AfterMarket`, `epsActual=2.05`, and `currency=null`. The listing had USD currency and the separately acquired financial statement had a November 3 filing date. Neither establishes the earnings row's denomination, diluted/current-share basis or original announcement instant. [EODHD earnings glossary](https://eodhd.com/financial-academy/financial-faq/fundamentals-glossary-common-stock#History)

An eligible local pair binds the same issuer, qualified listing, documented current split ledger and known monetary denomination to both series. It supplies an issuer-reported diluted fiscal-quarter EPS input and an actual announcement reference. EPS keeps the inclusive fiscal start and end, with `date=target_end`. Its plotted coordinate is `available_at`. A date-only after-market report retains day precision and its supplied session qualifier. Session close and publication time cannot be inferred from that date. A restated EPS uses the label **currently restated; not historical as-known** and its retrieval vintage. A current snapshot supplies no point-in-time history.

## CPI and the nominal ten-year rate

BLS series `CUSR0000SA0`, also distributed as FRED `CPIAUCSL`, covers monthly seasonally adjusted all-items prices for all urban consumers in the US city average. The index base is 1982–84=100. FRED showed 337.476 for October 2026 on November 20. The first day of the month encodes that measurement month. An actual BLS release date has its own field and source reference. Seasonal factors and historical index levels can change. [BLS series](https://data.bls.gov/timeseries/CUSR0000SA0) · [FRED CPIAUCSL](https://fred.stlouisfed.org/series/CPIAUCSL) · [BLS release archive](https://www.bls.gov/bls/news-release/cpi.htm) · [Seasonal revisions](https://www.bls.gov/cpi/seasonal-adjustment/using-seasonally-adjusted-data.htm)

Fed H.15's nominal ten-year constant-maturity Treasury rate is FRED `DGS10`. It is a daily percentage on the observation date. The November 19 H.15 release showed 5.07% for November 18, equivalent to the fraction 0.0507. Release schedules alone supply no actual row-level availability timestamp. EODHD's nominal Treasury feed also supplies dated par yields by tenor, and the provider divides `10Y` percent by 100 into `year_10`. A public demo request returned 403 during source research. The provider CPI model exposes an annual index with base 2010=100 or annual inflation percent. [Fed H.15](https://www.federalreserve.gov/releases/h15/) · [FRED DGS10](https://fred.stlouisfed.org/series/DGS10) · [EODHD Treasury fields](https://eodhd.com/financial-apis/us-treasury-ust-interest-rates-api-beta) · [EODHD macro indicators](https://eodhd.com/financial-apis/macroeconomics-data-and-macro-indicators-api)

## Explicit snapshot contract

`prepare_overlay_snapshot(manifest, raw_rows)` validates the original long-form rows from one prepared acquisition. It reads no other source. The manifest names the preparation, two distinct ordered input references (left, right), their UTC acquisition vintages, and the closed recipe and series facts. These reference the preparer's acquired records. The validator checks their consistency with every row. A local issuer fixture exercises the schema and cannot establish the missing live earnings facts.

Each row has physical fields for `series`, `date`, `value`, original `source_value`, `source_contract`, `input_ref`, `input_vintage`, `unit`, `basis`, `currency`, `issuer`, `listing`, `share_basis_ref`, `period_kind`, `restated`, `target_start`, `target_end`, `available_at`, `release_ref` and `session_timing`. Inapplicable fields are explicit nulls. The source contract and row identity, unit, basis and lineage must all agree.

| Series | Input contract | Delivered value | Observation coordinate |
| --- | --- | --- | --- |
| `adjusted_close` | `eodhd-eod-adjusted-close` | Adjusted closing price, quoted currency per current split-adjusted share | Trading date |
| `reported_diluted_eps` | `issuer-reported-diluted-fq-eps` | Reported FQ diluted EPS on the same currency/share basis | Actual availability date or UTC timestamp, retaining up to six fractional digits |
| `CUSR0000SA0` | `BLS:CUSR0000SA0/FRED:CPIAUCSL` | Monthly seasonally adjusted index level | Measurement month |
| `DGS10` | `Fed:H15/FRED:DGS10` | Fraction checked against the original percentage in `source_value` | Independent daily observation date |

The preparer preserves original row order and explicit null observations. It checks all raw rows for contradictions and duplicate series coordinates or fiscal targets before exclusion. A loss sets an unsafe flag. A receipt with a missing side cannot validate. Raw counts and flags travel in `AnnotatedResult.metadata.chart_sources`. Local JSON supplies the same envelope directly as `chart_sources` beside `rows` and `completeness`. The examples under `tests/fixtures/overlaySnapshots.json` are synthetic prepared files indexed by recipe; select a single file object for import.

Acquired coordinates are separate arrays for each side. Projections record omitted coordinate IDs and preserve raw flags. Values and source percentages remain ordinary visible row fields. Provenance contains no hidden amounts. An absent CPI release remains unknown, an absent measurement remains absent, and an explicitly missing observation keeps its null value. No common calendar, interpolation, zero filling or implicit second acquisition is part of this contract.
