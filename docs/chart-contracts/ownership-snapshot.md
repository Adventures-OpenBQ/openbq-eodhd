# Ownership snapshot acquisition contract

## Readiness

| Mode | Source status | Supported evidence |
| --- | --- | --- |
| Independent bars | **Ready for the US primary common-stock scope below** | EODHD `SharesStats.PercentInsiders` and `PercentInstitutions`, each in percentage points. Field measurement dates, underlying class and denominator are unknown. |
| Free-float bar and count comparison | **Blocked source for live ShareStatistics** | `SharesOutstanding` and `SharesFloat` do not establish matching class and observation date for the denominator. The existing table ratio is outside the chart receipt. |
| Waffle | **Blocked source for live ShareStatistics** | The feed supplies no same-date, same-class, mutually exclusive and exhaustive ownership buckets or rounding declaration on a common outstanding-shares denominator. |

## Primary source and captured evidence

The fundamentals reference defines the two headline percentages as shares held by insiders and institutions and identifies the `General` listing, primary ticker, instrument type and record update fields. The glossary explicitly scopes `SharesOutstanding` to the ticker-specific share class and contrasts it with historical company-wide `outstandingShares`. It gives `SharesFloat` as the available-to-trade count without asserting that its class or measurement date matches `SharesOutstanding`. The same glossary describes `UpdatedAt` as the last refresh of the fundamentals file. [Fundamentals reference](https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds#stocks-and-equities-fundamentals), [Common stock glossary](https://eodhd.com/financial-academy/financial-faq/fundamentals-glossary-common-stock).

A public AAPL DEMO capture on 2026-11-20 at 04:16:46 UTC returned the four headline share-count and ownership fields below. A second capture on 2026-11-20 at approximately 06:10 UTC verified `General.PrimaryTicker` and `General.Type` alongside the same values. Neither supplied field-level measurement dates, ownership basis or a partition. [Public DEMO response](https://eodhd.com/api/fundamentals/AAPL.US?api_token=demo&fmt=json).

```json
{
  "General": {
    "Code": "AAPL", "Exchange": "NASDAQ", "PrimaryTicker": "AAPL.US",
    "Type": "Common Stock", "OpenFigi": "BBG000B9XRY4", "UpdatedAt": "2026-11-19"
  },
  "SharesStats": {
    "SharesOutstanding": 14532182000, "SharesFloat": 14507041325,
    "PercentInsiders": 1.687, "PercentInstitutions": 66.375
  }
}
```

The API reference explains that one qualified ticker identifies the fundamentals response. EODHD documents `US` as a composite exchange code, including NASDAQ, NYSE, NYSE ARCA and BATS. The receipt checks the response's actual `General.PrimaryTicker`, `Code`, `Exchange` and `Type` against the requested US primary common stock. Bare or qualified requests resolve using the existing EODHD `US` default. Unknown venues, non-US market scope, and identities other than the response's primary listing are unavailable for this contract. [Fundamentals request identity](https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds), [EODHD’s US exchange code](https://eodhd.com/financial-apis/exchanges-api-list-of-tickers-and-trading-hours#the-us-exchange-code).

EODHD distinguishes the headline figures from the named holder list. It documents the list as limited to the largest holders and reports dates driven by the individual holder's filing schedule. A headline, a named holder subtotal, or a float count supplies no partition certificate. [EODHD Academy holder data](https://course.eodhd.com/lessons/who-is-in-the-market).

## Completed live request

The supported opt-in uses `chart_source=true` on `/api/v1/equity/ownership/share_statistics` with `provider=eodhd`, or on `/api/v1/eodhd/equity/ownership/share_statistics` with its EODHD default. The response selector is the entire `results` array. The receipt arrives at `extra.results_metadata.chart_sources`.

The typed `origin: "eodhd-share-statistics"` receipt is one row for the qualified issuer and certifies only `insiders → percent_insiders` and `institutions → percent_institutions`. The proved route normalizes the delivered symbol to that qualified issuer. Values are finite numbers between 0 and 100 in the source's percentage-point units. Raw unexpected values are represented as unavailable in the delivered data and recorded as `unit_conflict` before coercion. Numeric 0 is retained. Missing values remain null. The class, each measure's basis and measurement date are explicitly null. The partition is always `{ "status": "unavailable" }`. The legacy table's `date`, `outstanding_shares`, `float_shares` and calculated `free_float` are ordinary table data and carry no ownership chart binding.

The existing shared cache stores the UTC completion timestamp immediately after a successful upstream fundamentals fetch. The original stamped payload travels through L1 and the same ArcticDB record's data and metadata on L2 reads. `get_bundle_acquisition(symbol, exchange, credentials)` returns that payload and UTC timestamp, or an explicit missing timestamp for a legacy entry. Legacy fetchers continue to use `get_bundle` and receive the same payload dict. Cache reuse qualifies the vintage when the observed UTC chronology is consistent from fetch start through fetch completion, cache storage and the read. A legacy cache hit with a missing, malformed or chronologically inconsistent stamp returns ordinary rows without a receipt or an extra acquisition. The persisted timestamp syntax uses `T`, optional fractional seconds and the UTC suffix `Z` or `+00:00`. `fetched_at` remains the L2 write time for legacy TTL. [ArcticDB versioned data and metadata](https://docs.arcticdb.io/6.10.2/api/library/#arcticdb.version_store.library.Library.read).

The public sample establishes the source field contract. Production availability depends on an existing EODHD entitlement and successful acquisition.

## Explicit acquired snapshots

The local table JSON boundary accepts ownership evidence only from a closed `origin: "acquired-snapshot"` variant. An `origin: "eodhd-share-statistics"` receipt must enter through the matching completed backend request.

- Independent bars use one row with the qualified `symbol`, the declared `share_class` (explicit null when unknown), and the measured fields named by `field_origins`. A measure names `percent-points` or `fraction`, `shares-outstanding` or null, and its own calendar `as_of` or null. Values must fit the declared unit range or be explicit null. The partition is unavailable.
- A partition supplies the acquired certificate reference, known class, calendar date, common outstanding-shares denominator, explicit exclusivity and exhaustiveness, declared aggregate percentage-point rounding bound, and unique canonical bucket keys. Every delivered row repeats the exact `symbol`, `share_class`, `as_of` and `denominator`, binds the bucket and share via their two physical origins, and reports a finite share in [0, 100] percentage points. A zero rounding declaration is the no-declared-precision case for the builder's cancellation allowance. The builder checks the total against the rounding rule before allocation.
- Safe coverage requires all normalized rows to be delivered. Raw drops require an acquired unsafe fact. The declared raw faults and counts remain attached after aliasing, filtering and linked projection. Only string identities participate in coordinate omissions. A dropped or filtered row cannot supply an amount through provenance.

The local variants specify how to validate an acquired snapshot. Their example data establishes neither live coverage nor entitlement.
