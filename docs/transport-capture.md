# Audited capture

Set `EODHD_CAPTURE_REQUIRED=1` and configure `EODHD_CAPTURE_URL`, `EODHD_CAPTURE_TOKEN`, `EODHD_CAPTURE_WORKSPACE_ID`, `EODHD_CAPTURE_ENTITLEMENT` and `EODHD_CAPTURE_PRINCIPAL`. The TemporalDB server derives authoritative scope from its token policy. Non-loopback internal HTTP additionally requires `EODHD_CAPTURE_TRUST_INTERNAL_HTTP=1`.

The checked `openbq_eodhd/endpoint_manifest.json` enumerates every provider registration and mirror. Audited normalized acquisition supports `/eod/{symbol}`, `/intraday/{symbol}` and `/fundamentals/{symbol}`. Other data models fail `CAPABILITY_UNAVAILABLE` before acquisition. Tick data is not exposed by this canonical checkout; no cached-provider donor implementation is imported.

The per-client SDK session retains each requests response before SDK JSON output is exposed. `requests-decoded-content` explicitly means HTTP content after content decoding; it is not literal wire bytes. Hidden adapter retries, unsupported URLs/query fields, conditional requests without explicit predecessor identity and required wire-byte capability are refused. The B4 bridge's explicit `predecessor_capture_id` path remains available for bound 304 revalidation.

`EODHD_CAPTURE_CAPABILITY` defaults to `requests-decoded-content`; `http_body` and publication-history claims are unsupported. Model output carries `temporaldb.captures` and aggregate `temporaldb.stale` through OpenBB `AnnotatedResult` into `OBBject.extra.results_metadata`. Managed fundamentals bypass local L1/L2 freshness so a local object cannot recertify a receipt as fresh. Standalone caching behavior is unchanged.

Only local acquisition knowledge is available; provider publication and complete correction history are unknown. New endpoints must add reviewed request identities and manifest declarations before they can run in audited mode. All verification uses local fixture responses.

Managed v11 hosts set `EODHD_CAPTURE_ACCOUNT` to the opaque provider account ID
assigned to their private capture token by TemporalDB's server policy, and set
`EODHD_API_KEY` to that account's configured key. The bridge checks the server's
account on acquisition and stale fallback. It rejects a per-call EODHD key that
differs from the configured key before constructing a vendor request. Missing
account configuration fails closed. The unmanaged legacy path is unchanged.
