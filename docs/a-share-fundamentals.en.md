# A-Share Fundamentals: Raw-to-PIT Operations

[中文页面](a-share-fundamentals.md)

This page describes the native TuShare A-share fundamentals pipeline. `daily_basic` PE, PB, market capitalization, and turnover are daily valuation overlays. Financial-statement PIT fundamentals require explicit disclosure-time validation.

## Dataset specifications

The platform declares ten datasets: `income`, `balancesheet`, `cashflow`, `forecast`, `express`, `dividend`, `fina_indicator`, `fina_audit`, `fina_mainbz`, and `disclosure_date`.

```bash
marketdata tushare list-a-share-fundamentals-specs
```

Each specification records the API, VIP batch API, safe non-VIP fallback, query grain, date/report-period/disclosure fields, primary and deduplication keys, required fields, refresh method, and entitlement policy. If a VIP batch call is unavailable, only the per-symbol fallback declared in that specification is allowed. A dataset without a safe fallback must record a skip or failure; incomplete output must not be published as the current data contract.

## Raw download

Plan query units before downloading. The downloader persists `state.json`, `failures.json`, `manifest.yml`, and Parquet parts by query unit:

```bash
marketdata tushare plan-a-share-fundamentals \
  --dataset income --dataset balancesheet --dataset cashflow \
  --start-date 20150101 --end-date 20251231 \
  --entitlement-mode vip_batch

marketdata tushare download-a-share-fundamentals \
  --dataset income \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fundamentals_raw/income_2015_2025" \
  --start-date 20150101 --end-date 20251231 \
  --entitlement-mode vip_batch \
  --retry-attempts 3 --stale-after-days 30
```

Per-symbol fallback requires `--symbol` or `--symbols-file`. Pagination guards against repeated or unbounded pages, missing columns, and schema drift. Failed query units do not advance the contiguous watermark. Re-running against the same output directory resumes by skipping completed, unexpired units.

```bash
marketdata tushare check-a-share-fundamentals-state --state-file <raw-dir>/state.json
marketdata tushare list-a-share-fundamentals-failures --failure-file <raw-dir>/failures.json
marketdata tushare compact-a-share-fundamentals-raw --raw-dir <raw-dir> --out-dir <compact-dir>
```

## Normalization and PIT construction

Raw data preserves the provider's report types. The normalized layer selects standard report types according to dataset policy, normalizes symbols and dates, deduplicates records, and retains raw provenance:

```bash
marketdata tushare normalize-a-share-fundamentals \
  --dataset income --raw-dir <raw-income-dir> --out-dir <normalized-income-dir>

marketdata tushare validate-a-share-normalized-fundamentals \
  --asset-dir <normalized-income-dir> --target-date 20260529
```

The PIT builder accepts normalized input only. Rows without an eligible report period or disclosure date go to quarantine and must not become visible early in a research panel:

```bash
marketdata tushare build-a-share-fundamentals-pit \
  --normalized-dir <normalized-income-dir> \
  --normalized-dir <normalized-balancesheet-dir> \
  --normalized-dir <normalized-cashflow-dir> \
  --field-map revenue=revenue \
  --field-map rd_exp=rd_exp \
  --field-map total_assets=total_assets \
  --field-map n_cashflow_act=n_cashflow_act \
  --available-delay-days 1 \
  --max-observation-age-days 3 \
  --out-dir <pit-dir>

marketdata tushare validate-a-share-fundamentals-pit \
  --asset-dir <pit-dir> --target-date 20260529
```

Raw manifest `query.start_date` and `query.end_date` describe report-period query bounds; they are not observation-time or freshness evidence. Normalized v2 propagates each part's retrieval time to `_source_retrieved_at` and records complete archive observation dates in `observed_vintage_dates`. If one raw bundle spans multiple parts or retrieval dates, its completion date is the latest part retrieval date.

PIT v2 event identity is `symbol + trade_date + report_period + _source_retrieved_at`. Multiple report periods disclosed on one available date remain separate rows; fields must not be merged across report periods. Records of the same event across retrieval vintages form an ordered revision history. Conflicting values for the same report period, available date, source, and retrieval timestamp fail closed.

When one TuShare observation contains both `update_flag=0` initial values and `update_flag=1` updates, normalization retains the highest flag within the complete event key and counts replaced rows in `dropped_rows.superseded_update_flag`. The three financial statements accept only provider-defined `comp_type=1..4`; unsupported types are counted in `dropped_rows.unsupported_comp_type`. For repeated announcements with the same `f_ann_date`, the later `ann_date` is retained and counted in `dropped_rows.superseded_ann_date`. Conflicting financial values after these selections still fail closed.

`available_date` is the disclosure date plus the configured calendar-day delay. `trade_date` is a compatibility column and is not guaranteed to be an exchange session. An as-of view uses `available_date <= as_of_date`; a weekend event therefore becomes visible on the next requested trading-date without altering its original availability date.

## Strict as-of reads

Research code must not apply a full-table `ffill` to the event table. The single- and multi-date readers select the latest visible report period per field, then its latest revision, and return field-level revision provenance:

```python
from market_data_platform.providers.tushare_a_share_fundamentals import (
    load_pit_fundamentals_as_of_panel,
)

panel = load_pit_fundamentals_as_of_panel(
    asset_dir=pit_dir,
    as_of_dates=trade_dates,
    fields=["revenue", "net_profit", "total_assets"],
    provenance_policy="require_observed",
)
frame = panel.frame
audit = panel.audit
assert audit["revision_safe"] is True
```

Each field carries `__report_period`, `__available_date`, `__disclosure_date`, `__source_dataset`, `__source_raw_asset`, `__source_run_id`, `__source_retrieved_at`, and `__revision_id`. The multi-date reader loads the field/symbol-projected event set once.

The default `require_observed` policy requires both `available_date <= as_of_date` and `_source_retrieved_at <= as_of_date`; later-collected rows are excluded. A component without a pre-target-date archived vintage fails `revision_covered`, and the strict view returns an empty state. Historical data first collected in 2026 cannot be evidence of revision safety in 2020.

The archive ladder aggregates by logical component: normalized snapshots for one `source_dataset` form one vintage sequence. Each dataset selects its own most recent vintage before the target date; retrieval dates need not match. `bundle_available_date` is the maximum of the selected component vintage dates, `oldest_component_retrieval_date` is the minimum, and `observation_age_days` is computed from the oldest component.

Revision coverage and freshness are recorded separately. Production defaults to an observation age of at most three calendar days to cover weekends and cross-UTC collection. Both `revision_covered` and `freshness_verified` must pass for the target date. `allow_unverified` is for explicitly labelled `legacy_unverified` exploration only, not production promotion, parameter selection, or formal out-of-sample conclusions.

## Publication gates

Only after normalized and PIT validation pass may the platform update the latest alias, `metadata/current_assets/a_share_current.json`, and `metadata/dataset_registry.csv`. Every `--normalized-dir` must point to an immutable normalized v2 dataset. The full multi-dataset publication example is in the Chinese companion; the command and option names are identical.

With multiple `--normalized-dir` options, publication assembles an immutable `normalized_fundamentals` snapshot with a top-level manifest and components under `components/<dataset>/`, validates the combined asset and PIT, and only then switches the latest alias. With one normalized directory, the legacy behavior remains: the alias points directly to that directory.

To publish only PIT financial assets without updating the normalized alias, use `marketdata tushare publish-a-share-pit-fundamentals`. The existing normalized alias must already pass v2 provenance validation and cover the same `target-date`. Promotion requires v2 schemas, a complete pre-target observation vintage, and `observation_age_days <= max_observation_age_days`; report-period query bounds are not a substitute. Legacy v1, stale PIT, missing normalized data, or missing retrieval provenance fail before alias switching. A failed publish retains raw, state, failure, quarantine, and validation reports for diagnosis and does not update the current contract.

## Revision-safe vintage archive

From August 2026, formal revision-safe evidence must come from immutable periodic snapshots. Raw v2 records request start/completion time, Parquet SHA-256, and byte size per query unit. Normalized/PIT v2 propagate source-manifest hashes and maintain SHA-256 inventories for their own Parquet files. A completed snapshot cannot be refreshed in place; use a new dated output directory. Validation or strict as-of reads fail closed if a file hash changes.

`scripts/operations/archive_tushare_fundamentals_vintage.py` archives `income`, `balancesheet`, `cashflow`, and `fina_indicator` from 2015 onward into `fundamentals_vintages/vintage=YYYYMMDD`. It seals the raw, normalized, and core PIT snapshot but does not call publish or switch `latest`. The operational invocations, token environment variable, API URL, and verification-only mode are listed in the Chinese companion.

An ad-hoc long-history study must use a separate experiment artifacts root and a report-period window extended to 2008; it must not rewrite an already sealed snapshot for the same date. Fields such as `netprofit_yoy`, `or_yoy`, and `q_sales_yoy` may be used in field mappings. Such an archive is revision-safe only from its collection date onward; earlier report periods remain reconstructed PIT.

An interrupted same-day archive can be resumed. After `SEALED.json` exists, re-running performs full-chain verification only. The default systemd timer archives daily at 02:30 Asia/Shanghai, building a daily observation ladder. This cannot prove intraday revisions that appear and disappear between snapshots. Historical periods before the first observation remain `reconstructed_pit`. Monitor provider quota and storage after deployment; do not gain speed by weakening validation, overwriting existing vintages, or automatically publishing.

## Announcement-event research asset

For research using TuShare `ann_date`, `f_ann_date`, `report_type`, and `update_flag` without collapsing provider version rows during normalization, build an announcement-event PIT from an immutable raw asset with `marketdata tushare build-a-share-announcement-event-pit`.

The asset sets `available_date` from `f_ann_date`, falling back to `ann_date`, and retains `report_type`, `update_flag`, `event_id`, `revision_id`, and `row_hash`. It does not automatically merge report types or provider versions. Schema: `tushare.a_share.fundamentals.announcement_event_pit.v1`; metadata explicitly sets `research_only: true` and `complete_revision_history: false`.

This supports announcement-time PIT exploration, but is not a complete vendor revision database and cannot be used directly for production promotion. Production continues to use normalized and PIT-validated standard statement assets.

For event-to-panel reads, select a `report_type` policy explicitly:

- `standard`: only `report_type=1`, for standard consolidated statements.
- `diagnostic_including_type5`: preserve types 1 and 5 separately for sensitivity analysis; do not treat them as one standard panel.
- `all`: preserve all report types for diagnostics only.
- `dataset_native`: only for datasets without a `report_type` field, such as `fina_indicator`; record this restriction in the audit.

The as-of reader first restricts events to `available_date <= as_of_date`, then selects the latest event visible at that date for each report period. Do not select the final raw row by `(symbol, report_period)`. `load_announcement_event_as_of_panel` returns the panel and an audit containing visible-event count, final panel row count, dates, and policy. This remains research-only and does not replace complete vendor revision history.
