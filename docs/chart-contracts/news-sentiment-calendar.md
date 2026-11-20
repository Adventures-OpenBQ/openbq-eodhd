# CompanyNews acquisition for the news sentiment calendar

**Verdict: ready for a delivered EODHD CompanyNews page for one qualified instrument.** Verified on 2026-11-19 US Eastern time. Data access continues to depend on the caller's EODHD credential and plan. A page supplies a delivered sample, with no claim about news volume outside that sample.

## Provider facts

| Source field | Meaning and normalization |
| --- | --- |
| `date` | The source's offset-aware ISO 8601 publication stamp. The normalized `date` retains the timestamp with its offset (UTC can serialize as `Z`). Coordinates use the UTC calendar day after applying the offset. |
| `link` | Original article URL. The provider uses the exact HTTP(S) link as the per-copy identity and emits it unchanged as `url`. Copies from different publishers may have different links. |
| `symbols` | Qualified instruments associated with the article. The queried instrument must occur in the raw array. The delivered row retains the comma-separated associations, including other instruments. |
| `sentiment.polarity` | Per-article compound score in the fixed range [-1, 1]. The opted-in `polarity` column contains the numeric value or explicit null when missing or invalid. Zero is scored. |

The separate daily `/sentiments` endpoint uses an aggregate measure. The calendar acquires per-article fields from the Financial News endpoint. [EODHD Financial News API reference](https://eodhd.com/financial-apis/stock-market-financial-news-api), [compound polarity range](https://course.eodhd.com/lessons/nsd-how-a-sentiment-score-is-made), [link and symbol identity](https://course.eodhd.com/lessons/nsd-news-row), [exact-link deduplication](https://course.eodhd.com/lessons/nsd-story-arrives-five-times), [publication time and date filters](https://course.eodhd.com/lessons/nsd-news-timestamp).

## Representative acquired page

The public demo request to `GET /api/news` with `s=AAPL.US`, `offset=0`, `limit=2`, and JSON output returned HTTP 200. This is a projection of the actual upstream records onto the chart source fields. Retrieval was on 2026-11-20 UTC, during the evening of November 19 in New York.

```json
[
  {
    "date": "2026-11-20T03:40:00+00:00",
    "link": "https://finance.yahoo.com/media-advertising/articles/pixalate-launches-4d-invalid-traffic-034000682.html",
    "symbols": ["0941.HK", "AAPL.US", "CTO.US", "ROKU.US"],
    "sentiment": { "polarity": -0.986, "neg": 0.078, "neu": 0.878, "pos": 0.044 }
  },
  {
    "date": "2026-11-20T03:17:50+00:00",
    "link": "https://finance.yahoo.com/markets/stocks/articles/apple-aapl-stock-could-33-031750121.html",
    "symbols": ["AAPL.US"],
    "sentiment": { "polarity": 0.997, "neg": 0.015, "neu": 0.869, "pos": 0.115 }
  }
]
```

The installed OpenBB app returned the same two articles through both exact paths with `provider=eodhd&symbol=AAPL.US&chart_source=true&limit=2`. The standard path receipt was acquired at `2026-11-20T04:16:08.386127Z` and the mirror receipt at `2026-11-20T04:16:08.836242Z`. Both recorded counts `2/2/2`, no diagnostics, two scored coordinates on `2026-11-20`, and `scope = {kind: "sample", start: null, end: null, limit: 2, offset: 0}`.

## Completed-request contract

- Use `/api/v1/news/company` with explicit `provider=eodhd` or the fixed-provider `/api/v1/eodhd/news/company` mirror. Both must select the complete `results` array and opt in with `chart_source=true` or `1` to recognize proof. The client checks the exact completed request, scope, delivered row count, URLs, UTC days, symbol associations and scored states. A file receipt does not substitute for that request.
- `symbol` is one qualified instrument, or a single bare symbol with `exchange` (default `US`). `start_date` and `end_date` are optional inclusive UTC publication dates. The effective page defaults are `limit=50` and `offset=0`. The limit range is 1–1000, and offset plus limit cannot exceed 10000. One request acquires one page through the existing endpoint.
- The receipt is in `extra.results_metadata.chart_sources`, with `version=1` and one `news` variant. `raw_count` precedes drops. A row with an invalid timestamp or identity sets its unsafe fact, including when the row fails another requirement and is dropped. Duplicate links are compared before drops. Exact repeats collapse and differing raw objects sharing a link set `duplicate_conflict`. Each delivered link has one coordinate with its UTC day and scored flag. Hidden article contents and scores are absent from the receipt.
- A successful empty array produces the same sample receipt with zero counts. An upstream error or malformed response remains an error. A successful legacy request retains its original row fields and sentiment object; it carries no chart source receipt.

The application can also validate an explicitly acquired local JSON snapshot containing `rows`, `completeness`, and `chart_sources`. The closed receipt and row checks still apply. A successful bounded empty snapshot states its acquired dates without asserting anything about news outside that snapshot. Local snapshots do not establish backend availability.
