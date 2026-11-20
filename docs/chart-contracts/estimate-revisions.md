# Estimate revision acquisition contract

Reviewed 2026-11-20 against provider commit `816d043ead981716225696f31012149eff6124b5` and the EODHD documentation. The live slope, time-path and bump routes are **blocked-source**. A separately acquired local history can establish the following contract.

## What the existing request supplies

`EODHDAnalystEstimatesFetcher` reads the Fundamentals bundle once for the requested symbol/exchange. Its legacy `date` comes from `Earnings.Trend.*.date`, the fiscal target end. EPS and revenue averages are analyst consensus means. The companion low, high and number-of-analysts fields describe the panel. The period labels `0q`, `+1q`, `0y` and `+1y` are relative references to fiscal targets. The 7/30/60/90-day EPS trend fields are lagged figures in the same response. Up/down revision fields count changes. [EODHD common-stock glossary, Earnings Trend](https://eodhd.com/financial-academy/financial-faq/fundamentals-glossary-common-stock#Trend)

Minimal excerpts from the 2026-11-19 public AAPL.US demo checks, with only relevant keys retained:

```json
{"date":"2017-06-30","period":"0q","earningsEstimateAvg":"1.5700"}
```

The separate v1.1 Earnings Trend quarterly example was keyed by `2027-03-31` and included `type=quarterly` and `fiscalQuarter=Q2`. Both shapes identify a target. Neither excerpt's full source row supplied an independently observed UTC history for that target. The vendor's calendar trends service likewise takes symbols and returns fiscal-period snapshots without a historical time window. [EODHD calendar API](https://eodhd.com/financial-apis/calendar-upcoming-earnings-ipos-and-splits), [EODHD Academy, estimate trends](https://course.eodhd.com/lessons/co-estimate-trends)

Acquisition gaps for the live source:

- Two or more independently recorded observations, their exact UTC instants, distinct batch IDs and original snapshot references for one identical inclusive fiscal interval and kind. A cache read, store time or bundle retrieval timestamp describes one bundle. Subtracting an offset from it cannot reconstruct these observations.
- A stable qualified issuer/listing and verified metric denomination at each observation. The estimates fields alone supply no evidence of one currency, normalized whole currency units for revenue or a shared current split-adjusted class basis for EPS across those batches.
- For ranking, independently declared common universe and members at the baseline and comparison batches, with actual values available and comparable for every ranked member. A paged list provides no such declaration.

Historical access and entitlements remain unverified. The existing fetchers retain their original data, errors and acquisition behavior.

## Explicit local history

`prepare_estimate_vintage_snapshot(manifest, raw_rows)` validates one pre-existing acquisition and returns a typed `AnnotatedResult`. The input supplies the reference and actual recording instant for each independently observed consensus mean. A producer must provide every raw record from those snapshots and its original facts. A current restatement may be recorded at its own new observation time. Backdating the new estimate into an old recording changes its meaning.

The receipt uses the version-1 `estimate-vintages` kind and `acquired-snapshot` origin. Every vintage has an ID, unique UTC instant and unique batch. Its input supplies a distinct acquired reference. The enclosing retrieval must be at or after each observation. Each source row cross-checks observation, batch, input reference and the `independently-recorded-consensus-mean` source contract. The receipt stores coordinate identities only. The normalized estimate lives exclusively in the delivered `value` field. A null estimate and a missing row both leave a gap.

Every issuer declares its qualified listing, unique entity reference, metric unit and acquired currency. EPS also declares the current split-adjusted share basis and source reference identifying that class and basis. Every vintage row must agree on that reference for the issuer. Revenue is in native currency units with no share basis. Both use the existing currency vocabulary. Values are passed through in the acquired units, including a separately denominated subunit when explicitly declared.

Every row must match the acquired fiscal target's full start, end and kind. Supported inclusive intervals are the day before the clamped three-month or annual anniversary, respectively, or exactly 13/14 weeks for FQ and 52/53 weeks for FY. An issuer-specific stub requires additional calendar evidence. A year label or identical period end alone supplies insufficient coverage.

An unknown universe carries no membership assertion. A complete universe binds its ID, full distinct membership and explicit ordered baseline/current batches, with the same independently acquired roster in every input. A complete local table scope is required to carry that declaration. Membership completeness allows a missing estimate. Consumers must check each displayed rank column separately for a present value and a strictly positive common baseline for every member. An absent or ineligible member makes the column unavailable.

The raw scan records conflicting issuer, vintage, target, unit or duplicate facts before exclusions. Exact repeats collapse with an explicit count. `raw_count - exact_repeats - delivered_count` counts excluded conflicting records. Unsafe acquisitions retain all flags after filtering or projection. Conflicting raw records invalidate a financial comparison even when the current view hides them. A local snapshot example in `tests/fixtures/estimateVintageSnapshot.json` is synthetic.
