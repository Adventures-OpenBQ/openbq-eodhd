# ETF composition from acquired holdings

## Readiness

| Mode | Verdict | Acquired authority |
| --- | --- | --- |
| Delivered holdings of a qualified US ETF at the original fetch vintage | **ready** for the existing EODHD holdings commands when the checks below pass | One cached fundamentals response supplies the fund, constituent identities, classifications and weights. The original fetch-completion stamp identifies this acquired version. |
| Whole-fund or report-date composition from those commands | **blocked-source** | The holdings map supplies neither an exhaustive fund register nor a holdings report date. A count, a total near one and a file update date cannot establish these facts. |
| Live residual or rounding allowance | **blocked-source** | The response gives no independently measured missing mass or total rounding bound. |
| Non-US or unverified fund identity, cache entry without credible original vintage | **blocked-source** | The implemented binding covers a verified US venue and fund only. Unstamped cache records retain useful legacy data. |
| Explicitly prepared local holdings JSON | **ready** for the acquired facts it actually contains | Its own origin, declared fund, coordinates, units, coverage, precision and optional missing-mass authority must validate. This mode establishes no live availability. |

Deployment, credentials and entitlements are separate integration checks. The opt-in adds no source endpoint, collection job, lookup or join.

## Source evidence

EODHD defines the map key as a qualified holding ticker with separate `Code` and `Exchange` fields. Each holding's `Sector` and `Assets_%` describe that constituent and its percentage of fund assets. The larger `Holdings` map has the same field definitions as `Top_10_Holdings`. This route uses only the larger map. Its sector totals come from these leaves. `Sector_Weights.*.Equity_%` describes a separate equity-sector distribution. [ETF glossary](https://eodhd.com/financial-academy/financial-faq/fundamentals-glossary-etf)

The API documents an available holdings list. The Academy shows examples where `Holdings_Count` equals the number of delivered positions in a truncated list. This supports an unknown whole-fund coverage label even when the map and count agree. [Fundamentals API](https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds) · [Academy coverage examples](https://course.eodhd.com/lessons/co-one-endpoint-many-shapes)

Public demonstration response, `GET /api/fundamentals/VTI.US`, HTTP 200 on 2026-11-20 at 08:30:35 UTC. Minimal excerpt from that acquisition follows. Other original holding fields are omitted from the excerpt.

```json
{
  "General": {
    "Type": "ETF", "Code": "VTI", "CountryISO": "US", "Exchange": "NYSE ARCA",
    "OpenFigi": "BBG000HR9779", "UpdatedAt": "2026-11-19"
  },
  "ETF_Data": {
    "Holdings_Count": 3452,
    "Holdings": {
      "AAPL.US": {"Code":"AAPL","Exchange":"US","Sector":"Technology","Assets_%":6.9985},
      "NVDA.US": {"Code":"NVDA","Exchange":"US","Sector":"Technology","Assets_%":6.1691}
    }
  }
}
```

All 3,452 keys agreed with the holding code and exchange. The weights totaled 99.5617 percentage points, or 0.995617 of fund assets. Of those holdings, 526 had zero weight and 24 had a null or blank sector. Acquired examples included blank sector for `HONAV.US` and null sector for `AAYA.F`. The original response contained mixed-case historic codes, including `ADT_old.US`. The provider preserves the existing uppercase symbol convention and checks duplicates after canonicalizing case. Replaying the captured response through both source boundaries delivered all 3,452 rows with no unsafe flags. Demo snapshots change with the source.

## Provider contract

Both existing OpenBB paths use the `results` array. The standard path is `/api/v1/etf/holdings?provider=eodhd`, and the EODHD mirror is `/api/v1/eodhd/etf/holdings`. Set `chart_source=true` on the completed request. Only `symbol`, `exchange`, `provider` and the opt-in selector are relevant request parameters. The exchange defaults to US. On the mirror only, the omitted provider defaults to EODHD. The requested single qualified fund must match an acquired ETF with the same code, US country and a known US exchange (NYSE ARCA, NYSE, NASDAQ or BATS). An explicitly present primary ticker must agree. No route for sectors, regions or the separate top-ten map can produce a hierarchy receipt.

The accessor pairs the original UTC fetch completion with the precise cached fundamentals payload. A qualified response repeats the fund as `fund_symbol` and the original vintage as `snapshot` on each row. `as_of` on the receipt is null. A live cache read, `General.UpdatedAt` and today's date cannot supply a holdings report date. The opted-in rows also declare `weight_basis="fund-assets"` and `weight_unit="fraction"`. The consumer binds the original `symbol`, `sector` and `weight` fields.

The existing default holdings route on this provider branch already returns `weight = Assets_% / 100` and its frontend multiplier is 100. Opt-in independently checks numeric JSON `Assets_%` and applies this same single conversion. For example, 6.9985 becomes 0.069985. The default path's rows, formatter, sorting and empty-data behavior remain those of the original fetcher. This route previously emitted no ETF `chart_evidence` envelope. Opt-in adds `AnnotatedResult.metadata.chart_sources`, serialized at `extra.results_metadata.chart_sources`, using the shared additive envelope.

The parser checks raw rows before discarding or deduplicating them. The six monotone flags cover identity disagreement between the fund's acquired map key and declared holding coordinate, missing/invalid weight basis, conflicting duplicate, conflicting/invalid sector, negative weight, and raw positive weight above the allowance. Unassignable holdings count toward the raw count. A qualified map key on a malformed entry still witnesses a duplicate, in either row order. Finite raw numeric values contribute their sign and upper-bound facts even if their fractional representation cannot be delivered. Positive weights with contradictory identity or classification still count toward the upper-bound check. Negative weights cannot offset positive overexposure. Exact duplicates require identical original holding records at the same canonical qualified coordinate and count once. The `exact_duplicate` diagnostic records a de-duplication without turning it into a conflict. Any other repeat is unsafe. Blank and null sectors normalize to null. Zero weights are delivered explicitly.

The live source certifies `coverage="unknown"`, `weight_basis="fund-assets-fraction"`, no declared rounding allowance and `missing_mass={"status":"unknown"}`. Only a 1e-12 numerical cancellation tolerance applies to its raw sum. No numeric leaf, hidden subtotal or residual is persisted in the receipt. `holding_keys` names the delivered qualified coordinates. The normalized and delivered counts equal the number of unique delivered rows, and the raw count includes dropped rows. A nonempty usable source is required to attach evidence. A source containing negative facts remains unsafe in every descendant.

## Explicit snapshots and linked tables

The `acquired-snapshot` receipt is local-file only. Each canonical row repeats the qualified fund, qualified holding, snapshot, fund-assets basis, fraction unit, sector and original fractional weight. A local snapshot can name a known report date or the original UTC acquisition vintage. If the acquisition vintage is selected while a report date is claimed, every row must corroborate that report date. The report date cannot exceed the acquisition date. A claimed partial, sample, bounded or complete scope requires a coverage reference. Unknown scope has no such reference. An independent rounding reference permits a total bound from zero through 1e-4 fraction. An absent bound uses 1e-12. An explicitly verified positive missing mass requires a reference and a delivered partial, sample or bounded scope. Its independent measurement plus the delivered mass must reconcile to one within the declared allowance. Complete claims must reconcile their delivered leaves to one.

An ETF-marked sibling legacy receipt can independently declare fund, holdings kind, fund-assets basis, fractional unit, coverage, report date or fetch vintage. Each declared unit, basis, identity or kind alias must agree, including when two aliases appear together. An unrecognized field on an ETF-marked sibling also invalidates hierarchy acquisition. Unrelated legacy metadata retains its own meaning. Both engines project only the acquired tuple `(fund_symbol, snapshot, symbol)`. Hidden coordinates accumulate through links and calculated or aggregate values cannot reestablish them. Financial amounts and hidden sector subtotals are absent from that projection. Filtering and selectors do not change source coverage, denominator or unsafe flags.
