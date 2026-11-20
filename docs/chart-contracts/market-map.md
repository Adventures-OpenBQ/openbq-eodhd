# Market-map acquisition contract

Both live modes are **blocked-source** as of 2026-11-20. The registered provider at `58f66697bf41b8ed0ef6a01e615232aebdd42845` has no single acquisition that supplies their required area, identity and completed-session return facts. The local preparation function accepts either of two explicitly acquired snapshot contracts.

## Delivered capitalization sample

The EODHD screener documentation gives this AAPL.US example from 2026-08-12. The latest listing capitalization is 4,411,090,796,544, the currency symbol is `$`, the one-day percentage change is −0.87 and the five-day percentage change is −2.73. Its one-day percentage would convert to a fraction of −0.0087. The documentation describes a trading date and the latest adjusted close. It leaves the precise source close pair, adjustment basis and guarantee of a completed regular session unspecified. A currency glyph cannot certify a common denomination across a cohort. [EODHD screener response and field definitions](https://eodhd.com/financial-apis/stock-market-screener-api)

The registered `EquityScreener` exposes the five-day fraction and preserves the existing opt-in screening-bubble acquisition evidence. The 2026-11-19 public-demo US screen returned HTTP 403. Neither the documented example nor an editable request flag grants live market-map certification.

## Named index

EODHD's current Fundamentals components example reports AIZ.US with weight 0.0002 and PGR.US with weight 0.0019. Those are fractions. The example fields identify the listing and sector but supply no date for which each weight is valid. The historical ticker records give join and leave dates. The separate historical component snapshots record membership on change dates. Current weights cannot be assigned to those dates from that evidence. The 2026-11-19 public-demo GSPC components request returned HTTP 403. [EODHD Fundamentals index sections and examples](https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds#index-constituents)

The separate Marketplace Index Snapshot product advertises dated weighted components for three S&P indices from 2022-11-22. A weekend or holiday request may return an earlier snapshot. The registered provider offers index discovery and index-level price history. It has no integrated Marketplace weighted-snapshot acquisition, verified entitlement or same-session prepared constituent returns. [EODHD Marketplace Index Snapshot](https://eodhd.com/lp/spglobal)

## Prepared local snapshots

`prepare_market_map_snapshot(manifest, raw_rows)` in `openbq_eodhd/models/_market_map_source.py` validates one original acquisition. It accepts a JSON array of rows or an object keyed by qualified listing. Original keys and embedded listings must agree. The caller provides the actual source manifest and the entire original acquisition together. A local file contains the returned rows, the matching top-level `completeness`, and the returned `metadata.chart_sources` under `chart_sources`. No backend route recognizes these snapshot origins.

Both variants bind a single `acquisition_ref`, UTC retrieval instant, two exact ordered trading dates, and a source `session_ref` attesting that these are consecutive completed regular-session closes on the declared price basis. The dates alone do not establish this fact. Every row repeats that lineage and supplies its own qualified listing, acquired sector identity (null when unavailable), area and original normalized price change. `change = close / prior_close - 1` uses the same as-traded or split-adjusted price basis for every listing and excludes dividend reinvestment. Zero is unchanged. Null is unavailable. A session after the acquisition date, an intraday move, a five-day move or a nonfinite observation cannot serve as a completed-session return.

| Fact | Delivered cap | Index weight |
| --- | --- | --- |
| Universe and coverage | Fixed `delivered-sample`, `sample` coverage. The acquisition reference identifies the sample. | Named authority identifier, distinct qualified members and acquired coverage reference. The source declares complete, bounded or partial coverage. |
| Area | Absolute latest listing capitalization, independently identified by `area_ref`, in one verified denomination code. | Original fractional weight and source `area_ref`. |
| Validity | `cap_retrieved_at` equals retrieval of this acquisition. It makes no historical capitalization-at-close claim. | `membership_as_of` dates the common membership and weight snapshot. Both its authority reference `weight_reference` and returned `weight_valid_for` must attest validity for the exact price session. An older as-of date requires that same explicit proof. |
| Precision | Original absolute amounts, with no index-weight cap. | Independent `rounding_reference` accompanies a declared bound of 0 through 0.0001 inclusive. With neither, use 1e-12. |

All raw observations contribute to identity, source basis, session, denomination, membership, duplicate and excess diagnostics before exclusion. Conflicting records can be omitted from delivery while the receipt keeps every applicable unsafe flag. Exact repeats of accepted records collapse. Record comparison includes the original object key and complete JSON field values, including nested boolean versus numeric values. Only the first structurally valid row for each listing participates in area exclusion or delivery.

`raw_count = delivered_count + exact_repeats + rejected_count + excluded_areas.zero + excluded_areas.negative + excluded_areas.invalid`. Normalized and delivered counts agree. A row with contradictory lineage counts once as rejected, even when its area is unusable. A repeated otherwise clean zero or invalid area is counted once as an exclusion and subsequent times as exact repeats. Raw diagnostics survive either outcome. Sector-null rows with positive area remain present. The receipt contains delivered listing coordinates, source scope and counts. Numeric observations stay in the table.

A complete index must cover every declared member and sum to one within its precision bound. A shortfall or missing member sets `membership_conflict`. Any index mode whose observed positive raw weights exceed one plus the bound sets `excess_weight`, including when the unsafe row was rejected. A conflicting duplicate is independently unsafe. The chart refuses every flagged acquisition, including after filtering or aggregation. An explicitly marked unsafe receipt remains available for diagnosis. No normalization, inferred residual or cash weight is added.

The paired `tests/fixtures/marketMapCap.json` and `marketMapIndex.json` files are synthetic contract examples. Each preserves an unchanged observation and an unavailable one. Their source references demonstrate the fields a real acquired local snapshot must supply.
