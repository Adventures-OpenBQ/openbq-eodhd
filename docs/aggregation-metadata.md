# Provider-owned aggregation dictionary

Status: starter contract and opt-in library API. This change does not alter
fetcher results, attach response metadata, execute a pivot, or change a frontend.
EODHD-specific meanings and rules belong in **openbq-eodhd**, alongside its field
mappings, not in BDOBB-V2 or a second dictionary in openbb-docker.

## Ownership and interface

`openbq_eodhd/aggregation.py` owns the semantic allowlists and serializable
metadata contract. `models/fundamental.py` remains the source of truth for
vendor-to-OpenBB field names; the dictionary resolves those names from the
existing maps. `tests/test_aggregation.py` detects unreviewed additions or
removals in those maps and checks coverage of their normalized output fields.
Passthrough fields not in these maps remain explicitly unknown.

```python
from openbq_eodhd.aggregation import get_aggregation_metadata

metadata = get_aggregation_metadata(
    "CashFlowStatement",
    period="quarter",
    fields=["cash_at_beginning_of_period", "cash_at_end_of_period", "net_change_in_cash"],
)
# suggested_time_aggregation: first, last, sum respectively

bars = get_aggregation_metadata(
    "EquityHistorical", interval="1M", fields=["close", "volume"]
)
# suggested_time_aggregation: last, sum respectively
```

Supported models: IncomeStatement, BalanceSheet, CashFlowStatement, and EOD
EquityHistorical, EtfHistorical, CryptoHistorical, CurrencyHistorical.
Statement frequency must be annual or quarter; historical input interval must
be 1d, 1W or 1M. A future target quarter is a consumer grouping choice, not an
EODHD `period=q` request. Intraday and corporate actions need separate review.

The default catalog lists recognized fields, not proof that every field exists
in a particular response. Pass actual normalized columns through `fields` when
building metadata for a result. Field ordering is preserved.

## Contract and safety

- **Versioning:** `schema_version` versions the consumer contract;
  `ruleset_version` versions reviewed semantic rules. Change the latter whenever
  meaning, operator, eligibility or evidence changes.
- **Field identity:** keys are normalized OpenBB output names. `source_field`
  retains the raw vendor key where reviewed; unknown extras have no claimed
  source mapping.
- **Suggestions only:** `suggested_time_aggregation` is conditional;
  `auto_apply` is false throughout this starter. A consumer must verify all
  `conditions`, or ask for an explicit override, before calculating.
- **Separate dimensions:** `entity_aggregation` is unset. Do not reuse a temporal
  rule to combine issuers, accounts, currencies or instruments.
- **Order:** first/last means chronological order using `order_by`, independent
  of pill order, display sorting, or the provider's newest-first row order.
- **Missing data:** `null_policy=propagate` means a missing boundary value stays
  missing, not "last non-null"; missing values are not zero for sums. Missing
  rows require separate completeness checks. Incomplete target periods require
  selection rather than silently presenting a completed quarter.
- **Period identity:** require distinct observations, resolved revisions,
  compatible units/currency, complete coverage and consistent calendars.
  Resolve duplicate/revised records before aggregation; do not sum versions.
- **Provenance:** rules are `provider_curated`, not metadata returned by EODHD.
  Each reviewed rule retains documentation URLs and its review date.
- **Fallback:** unknown fields, unsupported models and unreviewed contexts must
  never fall back to "sum all numeric values".

## Starter rules and evidence

The EODHD [historical-data documentation](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes)
describes period-spanning OHLC and summed volume. The starter maps open to first,
high to max, low to min, close/adjusted_close to last, and volume to sum.
Adjustment metadata distinguishes raw OHLC, split/dividend-adjusted close, and
split-adjusted volume according to that documentation. Verify instrument,
adjustment basis and capture vintage before combining bars. Raw prices across
corporate actions remain raw and must not be presented as adjusted bars.

The [fundamentals glossary](https://eodhd.com/financial-academy/financial-faq/fundamentals-glossary-common-stock)
defines beginning cash, ending cash and change in cash separately. They receive
first, last and conditional sum respectively, not one statement-wide operator.
Explicitly reviewed balance amounts receive last; explicitly reviewed income
and cash-flow amounts receive conditional sum.

The [fundamentals API](https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds)
provides yearly/quarterly statements, but the reviewed documentation does not
establish a universal standalone-versus-YTD flag. `reporting_basis=unverified`
is deliberate. A quarterly label is not evidence of non-overlapping flows.
YTD requires a separate conversion step; annual and quarterly overlapping
records must not be mixed. No inference is made from a numeric dtype.

The glossary describes `commonStockSharesOutstanding` as a weighted diluted
share count. Its normalized `common_shares_outstanding` therefore requires
review instead of inheriting a balance-sheet last rule. EPS, margins, TTM
snapshots, growth rates, technical indicators and unknown extras likewise have
no automatic rule. Recomputed ratios need named numerator/denominator rules;
weighted averages need actual weights. Neither is implemented in this starter.

## Integration boundary and remaining work

Keep the frontend vendor-agnostic: a pill can show `Suggested: Sum` with the
reason/conditions, or `Choose aggregation` for an unknown field. User overrides
belong in card configuration; they do not mutate the provider dictionary.
Numeric fiscal-year fields are dimensions, not default measures.

### Transport: a per-column hint in widgets.json

The dictionary reaches a frontend through `widgets.json`. OpenBB's widget
generator (`openbb_platform_api.utils.openapi.data_schema_to_columns_defs`)
merges a field's `x-widget_config` verbatim into that column's `columnsDefs`
entry, so the seven covered Data models carry, on every field, a minimal
projection of the dictionary entry — computed from `get_aggregation_metadata`
at import time by `attach_widget_hints`, never restated:

```json
"columnsDefs": [
  { "field": "cash_at_end_of_period", "headerName": "Cash At End Of Period",
    "aggregation": { "time": "last", "frequencies": ["annual", "quarter"],
                     "entity": "require_selection", "status": "conditional",
                     "ruleset": "2026-09-20.1" } },
  { "field": "minority_interest", "...": "...",
    "aggregation": { "time": null, "frequencies": ["annual", "quarter"],
                     "entity": "require_selection", "status": "requires_selection",
                     "ruleset": "2026-09-20.1" } }
]
```

- `time` is the suggested operator or `null`. `frequencies` are the reviewed
  source frequencies in the provider's own tokens (statement periods, or EOD
  intervals `1d`/`1W`/`1M`); a consumer that buckets to any other frequency
  must fall back to "choose", not reuse the suggestion.
- `entity` is always `require_selection`: no entry defines a cross-entity
  rule, and a temporal rule must never be reused to combine issuers.
- `status` is `conditional`, `requires_selection` or `dimension`. Every field
  of a covered model carries a hint, so a consumer can tell "reviewed, no rule"
  (`time: null`) from a model the dictionary does not cover at all (no
  `aggregation` key).
- Reasons, conditions and provenance are not on the wire; the dictionary
  answers for them.

The three statement models declare EODHD's mapped line items as typed
optional fields (OpenBB's standard models declare only the period keys and
treat line items as passthrough extras), which is what gives them a
`columnsDefs` entry to carry the hint; unmapped passthrough fields remain
extras with no entry. `widgets.json` is generated once at API start, so a
change here reaches a served backend with an image rebuild.

The OBBject `extra.results_metadata` route remains available for per-request
resolved metadata later (OpenBB's
[AnnotatedResult metadata mechanism](https://docs.openbb.co/platform/developer_guide/metadata));
this starter does not use it. The host backend forwards `widgets.json`
unchanged, and BDOBB consumes the hint rather than importing rules.

The existing financial transform excludes `filing_date` and `currency_symbol`
from rows and does not expose whether the fiscal-year lookup used its December
fallback. Before enabling Auto execution, preserve reporting currency, original
filing date, fiscal-calendar provenance, capture/revision identity and verified
period basis in provider response context. Do not label missing context as
verified; a filing date alone does not establish when a revised value was known.

## V11 data-mastering workstream

This dictionary is part of the v11 mastering design, not a frontend-only
feature. The division of responsibility is:

| Component | Authority |
| --- | --- |
| openbq-eodhd | Vendor definitions, endpoint/field mappings, adjustment basis, reviewed suggestions and their evidence |
| V11 mastering service | Canonical measure identity, versioned bindings to provider rules, validated period basis, quality gates and reproducible transformations |
| OpenBB/ODP distribution | Standard result rows plus resolved aggregation metadata and query receipt |
| BDOBB-V2 pivot | Ordered grouping configuration, visible suggestions and explicit user overrides |

Mastering may record an immutable copy of the provider rule used for a dataset;
that is provenance, not a second editable source of vendor semantics. The v11
canonical registry may have provider-independent measure definitions. Changes
to EODHD-specific interpretations still originate in this repository.

### Bronze, Silver and Gold

- **Bronze:** retain raw response bytes and capture identity, request endpoint
  and parameters, ingestion time, and provider package revision. Never rewrite
  old captures when the rule dictionary changes.
- **Silver:** bind raw vendor fields and normalized ODP fields to stable
  canonical measure IDs. Preserve source date, filing date, currency, units,
  adjustment basis, fiscal-calendar source, frequency, period start/end and
  reporting basis with evidence or an explicit unknown state. Store the rule
  binding's effective interval separately from when the system learned it.
- **Gold:** publish resolved per-column semantics and the exact rule versions
  used for materialization. Build an automatic rollup only when reporting basis,
  identity, boundaries, completeness, units, revision selection and adjustment
  checks pass. A UI override is not permission to mark data as verified.

Do not confuse vendor field identity with canonical measure identity. For
example, weighted diluted shares and point-in-time shares must not be merged
merely because their column names resemble one another. Preserve the raw field
path and provider model as part of the binding.

### Receipt additions

In addition to the v11 data snapshot/capture/model references, a mastered
aggregation should record:

- provider package version and source revision;
- aggregation schema version and ruleset version;
- content hash and immutable location of the exact resolved rules;
- canonical measure ID and semantic-binding version for every value column;
- source and target frequency, calendar, time zone and period boundaries;
- operator, weighting/dependency fields, null and completeness policies;
- rule effective-time and knowledge-time selection;
- validation results, explicit user overrides and their scope;
- aggregation-engine version and transformation/configuration hash.

Pin both the data and its semantics. A new rule must not silently reinterpret
an old Gold artifact or saved query receipt. Recalculation with current rules
must produce a new result/receipt and disclose the changed interpretation.

### V11 acceptance gates

1. A monthly OHLCV fixture rolls up to a calendar quarter using first/max/min/
   last/sum, with separate instruments and consistent adjustment vintage.
2. A cash-flow fixture uses first for beginning cash, last for ending cash and
   sum for verified standalone flows; YTD or unknown basis blocks Auto.
3. Missing final-period observations, mixed currencies, duplicate revisions and
   unknown new vendor fields cannot silently pass the readiness checks.
4. Fiscal and calendar quarters are distinguishable; a calendar fallback is
   visible and does not claim verified issuer-calendar provenance.
5. A changed rule can be compared with its predecessor, while an old receipt
   reproduces the original interpretation using its pinned semantic snapshot.
6. The same returned metadata is consumable by a table, pivot, export or
   backtest without copying an EODHD mapping into those interfaces.

These are integration requirements, not claims that the mastering service,
storage tables, transport or frontend implementation is included in this
starter change. The current change is confined to openbq-eodhd; the v11 service
and release/v11 frontend should implement their own consumer work separately.

## Maintenance checklist

When EODHD or OpenBB changes:

1. Update the SDK/request handling and field mapping in this repository.
2. Review each changed raw field, including passthrough fields and semantics.
3. Update the explicit semantic allowlist and evidence; unknowns stay blocked.
4. Bump the ruleset version, or schema version for a contract change.
5. Run mapping-drift and serialization tests plus the full provider test suite.
6. Validate with dated provider fixtures before enabling automatic execution.
7. Update the vendored provider version in openbb-docker. No duplicated
   EODHD dictionary should need editing in BDOBB-V2.
