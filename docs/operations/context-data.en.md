# China Macro and Industry Context Data

[中文页面](context-data.md)

`cn_context` is a data domain separate from the A-share market-data contract. It holds research context such as macroeconomic, rates, credit, prices, industries, and energy series. It does not change the `market=a_share` and `provider=tushare` semantics of `a_share_current.json`.

The current context contract is stored at:

```text
$DATA_PLATFORM_ROOT/metadata/current_assets/cn_context_current.json
```

The first contract uses `market=cn_context` and `provider=composite`. Stable asset keys are `context_catalog`, `context_observations`, `context_pit`, and `context_release_calendar`.

## Point-in-time semantics

Standardized observations retain `published_at`, `observed_at`, `ingested_at`, `source_retrieved_at`, and `available_at`. A value is visible to research only when both conditions hold:

```text
available_at <= as_of
source_retrieved_at <= as_of
```

The retrieval-time condition prevents a historical webpage collected later, a later revision, or subsequently recovered publication-time evidence from leaking into an earlier research date. When a `series_id + period_end` has multiple revisions, the PIT reader selects the latest vintage visible at the requested `as_of` time.

Historical values without reliable publication times or contemporaneous retrieval evidence are marked `reconstructed=true` and `revision_covered=false`. They can support exploration, but must not be presented as revision-safe historical evidence for promotion.

## Initial TuShare coverage

The first adapter set covers `shibor`, `shibor_lpr`, `cn_m`, `sf_month`, `cn_pmi`, `cn_cpi`, `cn_ppi`, `cn_gdp`, and `cn_schedule`. Provider endpoints retrieve raw data; this platform owns stable `series_id` values, PIT semantics, and the current contract.

## National Bureau of Statistics

The adapter targets the current NBS publication library at `https://data.stats.gov.cn/dg/website`. Initial series include year-over-year industrial value added and generation/output series for electricity, thermal, hydro, nuclear, wind, solar, raw coal, crude oil, and natural gas.

Industrial value added uses the verified publication-library `directoryId:indicatorUUID`. Energy series resolve indicators through the official search endpoint and require a unique exact `expected_name` match. Zero or multiple matches fail closed; the adapter does not silently select a similar name.

## National Energy Administration

The NEA adapter and the remaining operational workflow are documented in the Chinese companion. Machine-readable identifiers, CLI options, and contract keys remain unchanged between locales.
