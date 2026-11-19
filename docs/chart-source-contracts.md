# Financial chart acquisition evidence

The EODHD equity screener, historical dividends and income statement accept
`chart_source=true` on the standard provider route and its generated
`/eodhd` mirror. OpenBB puts the versioned receipt at
`extra.results_metadata.chart_evidence` and leaves the rows at `results`.
Keep the receipt from the same completed response when selecting or projecting
`results`. The default `chart_source=false` returns the regular list and
retains the existing empty-data and error behavior.

The receipt describes acquired records before row normalization. It carries
no credentials or hidden row amounts. Dashboard options cannot grant coverage,
declare units or carry a saved receipt. A trusted local snapshot adapter can
emit an equivalent receipt along with the rows it acquires at runtime.

## Shared wire fields

Every receipt has `version: 1`, `kind`, `retrieved_at` (UTC timestamp),
`raw_count`, `normalized_count`, `delivered_count`, `diagnostics`,
`field_origins` and `unsafe`. All fields in the examples are required except
where a union variant changes the fields. Nullable fields are sent as JSON
null. The receipts use ISO dates and sort diagnostic codes and date/currency
sets deterministically. `retrieved_at` says when the provider acquired the
payload for this response, including a read from the existing fundamentals
cache. It does not date the underlying observations.

Counts refer to the selected acquisition and the normalized and delivered
row lists, in that order. The provider currently has no gate between
normalization and delivery, so the last two counts agree. Subsequent table
projection preserves these three acquisition counts. The diagnostic codes
are closed literals in `openbq_eodhd/models/_chart_evidence.py`; use the
typed safety and coverage fields to decide whether a chart is supported.

## Screener

The upstream `refund_5d_p` is in percentage points and is divided by 100
exactly once. `dividend_yield` is already fractional and
`market_capitalization` uses the listing denomination. The provider preserves
`currency_symbol` and uses the generated widget percent formatter for
`price_change_5d`. [EODHD screener fields and units](https://eodhd.com/financial-apis/stock-market-screener-api)

Each member of `cohorts` is for one raw `price_date`. The raw count includes
rows later omitted from results. `denominations` lists distinct explicit
three-letter codes. A row with exchange US and `$` is treated as USD. Other
unresolved symbols set `unknown_denomination`. Treat multiple denominations,
unknown denomination or `conflicting_duplicates` as unsafe for that date.
Duplicates compare the listing, denomination, and the raw return, yield
and capitalization values. The exact duplicate count tracks repeated copies
of the same observation. A distinct raw value for any of those three measures,
including missing versus present, sets the date-local conflict flag. Invalid
or missing raw identity/date and explicit unit contradictions are global
unsafe flags. The provider receipt sends the effective `sort`, ordered
`filters` tuples, `limit`, `offset` and nullable `signals` as `scope`.

## Dividends

The SDK makes one JSON request to `/div/{qualified_symbol}`. `date` is the
ex-date, `value` is adjusted to today's share count, and `currency` is the
payment currency. For example, a GBP dividend on a GBX quote stays GBP.
`unadjustedValue` is a separate delivered field and cannot fill a missing
`value`. [EODHD dividend contract](https://eodhd.com/financial-apis/api-splits-dividends)

`requested_start` and `requested_end` are the inclusive requested bounds
and may be null. `event_dates`, `invalid_amount_dates` and `duplicate_dates`
are sorted unique ex-dates. An invalid amount drops the numeric row but keeps
its date as a continuity boundary. An undated raw event sets
`unsafe.invalid_ex_date`; conflicting currency, instrument, amount basis or
window also remains visible even if a presentation filter hides a row. A
verified empty snapshot has an empty results list, empty coordinate lists
and all three counts zero.

Coverage is `{"status":"unknown"}` for ordinary EODHD responses, including a
successful empty list. HTTP success, observed dates and requested bounds do
not establish acquired coverage. The free plan's documented history limit
does not identify the credential's entitlement or certify this acquisition.
SDK errors and malformed response envelopes still fail the request.

The positive authority is a deterministic, fully acquired snapshot with a
stable source reference, matching qualified instrument and independently
declared bounds. The trusted adapter `normalize_dividend_snapshot` accepts
that declaration separately from the query. `coverage.status` is `verified`
only if the requested bounds lie within the snapshot bounds. It is `clipped`
if an explicit request extends beyond them, even when usable events exist.
Both known variants carry `authority: "explicit-snapshot"`, `reference`,
`start` and `end`. An unsafe receipt or clipped/unknown coverage must not
certify historical steps. [Provider snapshot tests](../tests/test_chart_evidence.py)

## Income statement

The provider uses the single acquired fundamentals bundle. The
`reporting_currency` belongs to `Financials.Income_Statement.currency_symbol`,
with each supplied per-period declaration checked for disagreement.
`General.CurrencyCode` is the quote currency and gets no say in the
statement's currency. [EODHD statement structure and currency](https://course.eodhd.com/lessons/co-statements-inside-the-payload)

The live route declares `scale: {"status":"unknown"}`. EODHD's fundamentals
documentation supplies example magnitudes and names the fields, without a
monetary field-unit guarantee for this payload. A captured example, number
magnitude or display formatter cannot supply a multiplier. The trusted
`normalize_income_snapshot` adapter can publish an explicit positive
multiplier for the acquired issuer and reference. The verified scale variant
has `status: "verified"`, `authority: "explicit-snapshot"`, `multiplier` and
`reference`. The delivered numbers are unchanged by this multiplier. Any
conflict with a snapshot's block or period declaration is a refusal fact.
[EODHD fundamentals route and sample](https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds)

`period_kind` is `FY` or `FQ`. A `periods` entry has the actual `period_end`
and the kind plus nullable `fiscal_year` and `fiscal_period`. The chart
source leaves numbered fiscal quarter labels null when the issuer fiscal
year end is unknown. `requested_limit` is the limit in the completed query
and may be null. `available_period_count` counts the upstream bucket before
that limit. It does not certify whole-history coverage.

`record_coverage: "full-record"` means the particular selected record was
obtained whole, through the unfiltered fundamentals bundle or the snapshot's
explicit covered-period list. An ambiguous period identity or a partial
snapshot record declares `"unknown"`. `present_fields` contains only finite
values from the canonical chart field origins, even if a lookalike alias is
present. This list carries field identities and no amounts. Required anchors
can be absent from a full record. Downstream chart admission still checks
the presented fields, period identity, currency and scale. Optional missing
components are left for downstream reconciliation.

## Complete serialized examples

### US screener page receipt

The delivered row has `price_change_5d: 0.05` and `dividend_yield: 0.004`.

```json
{
  "version": 1,
  "retrieved_at": "2026-11-19T16:00:00Z",
  "raw_count": 1,
  "normalized_count": 1,
  "delivered_count": 1,
  "diagnostics": [],
  "kind": "screener",
  "field_origins": {
    "symbol": "code+exchange",
    "exchange": "exchange",
    "currency_symbol": "currency_symbol",
    "last_day_data_date": "last_day_data_date",
    "market_cap": "market_capitalization",
    "dividend_yield": "dividend_yield",
    "price_change_5d": "refund_5d_p"
  },
  "units": {
    "market_cap": "listing-currency",
    "dividend_yield": "fraction",
    "price_change_5d": "fraction"
  },
  "scope": {
    "sort": "market_capitalization.desc",
    "limit": 50,
    "offset": 0,
    "signals": null,
    "filters": [
      [
        "exchange",
        "=",
        "us"
      ]
    ]
  },
  "unsafe": {
    "unassignable_identity": false,
    "unassignable_date": false,
    "unit_conflict": false
  },
  "cohorts": [
    {
      "price_date": "2026-08-28",
      "raw_count": 1,
      "denominations": [
        "USD"
      ],
      "unknown_denomination": false,
      "conflicting_duplicates": false,
      "exact_duplicate_count": 0
    }
  ]
}
```

### Covered nonempty dividend snapshot receipt

The fixture delivers the 2026-02-12 Special dividend with `amount: 0.2`, `unadjusted_amount: 0.8` and `currency: "GBP"`.

```json
{
  "version": 1,
  "retrieved_at": "2026-11-19T16:00:00Z",
  "raw_count": 1,
  "normalized_count": 1,
  "delivered_count": 1,
  "diagnostics": [],
  "kind": "dividends",
  "field_origins": {
    "ex_dividend_date": "date",
    "amount": "value",
    "currency": "currency",
    "frequency": "period"
  },
  "qualified_symbol": "SHEL.LSE",
  "amount_basis": "split-adjusted-current-share",
  "requested_start": "2026-01-01",
  "requested_end": "2026-06-30",
  "coverage": {
    "authority": "explicit-snapshot",
    "reference": "fixtures/shel-dividends-2026h1-v1",
    "start": "2026-01-01",
    "end": "2026-06-30",
    "status": "verified"
  },
  "unsafe": {
    "invalid_ex_date": false,
    "currency_conflict": false,
    "unknown_payment_currency": false,
    "instrument_conflict": false,
    "amount_basis_conflict": false,
    "outside_requested_window": false
  },
  "event_dates": [
    "2026-02-12"
  ],
  "invalid_amount_dates": [],
  "duplicate_dates": [],
  "payment_currencies": [
    "GBP"
  ]
}
```

### Covered empty dividend snapshot receipt

This successful snapshot has `results: []`.

```json
{
  "version": 1,
  "retrieved_at": "2026-11-19T16:00:00Z",
  "raw_count": 0,
  "normalized_count": 0,
  "delivered_count": 0,
  "diagnostics": [],
  "kind": "dividends",
  "field_origins": {
    "ex_dividend_date": "date",
    "amount": "value",
    "currency": "currency",
    "frequency": "period"
  },
  "qualified_symbol": "SHEL.LSE",
  "amount_basis": "split-adjusted-current-share",
  "requested_start": "2026-01-01",
  "requested_end": "2026-06-30",
  "coverage": {
    "authority": "explicit-snapshot",
    "reference": "fixtures/shel-dividends-2026h1-v1",
    "start": "2026-01-01",
    "end": "2026-06-30",
    "status": "verified"
  },
  "unsafe": {
    "invalid_ex_date": false,
    "currency_conflict": false,
    "unknown_payment_currency": false,
    "instrument_conflict": false,
    "amount_basis_conflict": false,
    "outside_requested_window": false
  },
  "event_dates": [],
  "invalid_amount_dates": [],
  "duplicate_dates": [],
  "payment_currencies": []
}
```

### Clipped dividend snapshot receipt

The requested start is one day before the acquired window.

```json
{
  "version": 1,
  "retrieved_at": "2026-11-19T16:00:00Z",
  "raw_count": 1,
  "normalized_count": 1,
  "delivered_count": 1,
  "diagnostics": [
    "clipped_coverage"
  ],
  "kind": "dividends",
  "field_origins": {
    "ex_dividend_date": "date",
    "amount": "value",
    "currency": "currency",
    "frequency": "period"
  },
  "qualified_symbol": "SHEL.LSE",
  "amount_basis": "split-adjusted-current-share",
  "requested_start": "2025-12-31",
  "requested_end": "2026-06-30",
  "coverage": {
    "authority": "explicit-snapshot",
    "reference": "fixtures/shel-dividends-2026h1-v1",
    "start": "2026-01-01",
    "end": "2026-06-30",
    "status": "clipped"
  },
  "unsafe": {
    "invalid_ex_date": false,
    "currency_conflict": false,
    "unknown_payment_currency": false,
    "instrument_conflict": false,
    "amount_basis_conflict": false,
    "outside_requested_window": false
  },
  "event_dates": [
    "2026-02-12"
  ],
  "invalid_amount_dates": [],
  "duplicate_dates": [],
  "payment_currencies": [
    "GBP"
  ]
}
```

### Explicit-scale income snapshot receipt

The selected period has `revenue: 100`, `cost_of_revenue: 40`, `gross_profit: 60`, `operating_income: 30`, `income_before_tax: 25`, `net_income: 20` and `income_tax_expense: 5`. These fixture values are in thousands of USD under the snapshot declaration.

```json
{
  "version": 1,
  "retrieved_at": "2026-11-19T16:00:00Z",
  "raw_count": 1,
  "normalized_count": 1,
  "delivered_count": 1,
  "diagnostics": [],
  "kind": "income",
  "field_origins": {
    "period_ending": "date",
    "reporting_currency": "Financials.Income_Statement.currency_symbol",
    "revenue": "totalRevenue",
    "cost_of_revenue": "costOfRevenue",
    "gross_profit": "grossProfit",
    "operating_income": "operatingIncome",
    "income_before_tax": "incomeBeforeTax",
    "net_income": "netIncome",
    "income_tax_expense": "incomeTaxExpense"
  },
  "qualified_symbol": "HSBA.LSE",
  "period_kind": "FY",
  "reporting_currency": "USD",
  "scale": {
    "status": "verified",
    "authority": "explicit-snapshot",
    "multiplier": 1000.0,
    "reference": "fixtures/hsba-2026fy-v1"
  },
  "requested_limit": 1,
  "available_period_count": 2,
  "unsafe": {
    "invalid_period_identity": false,
    "scale_conflict": false
  },
  "periods": [
    {
      "period_end": "2026-06-30",
      "period_kind": "FY",
      "fiscal_year": 2026,
      "fiscal_period": "FY",
      "record_coverage": "full-record",
      "declared_currency": "USD",
      "currency_conflict": false,
      "scale_conflict": false,
      "identity_conflict": false,
      "field_origin_conflict": false,
      "present_fields": [
        "cost_of_revenue",
        "gross_profit",
        "income_before_tax",
        "income_tax_expense",
        "net_income",
        "operating_income",
        "revenue"
      ]
    }
  ]
}
```

An ordinary dividend response uses the same shape with unknown coverage. An ordinary income response uses the same shape with unknown scale. Both receipts describe the completed route and lack the authority these charts require.
