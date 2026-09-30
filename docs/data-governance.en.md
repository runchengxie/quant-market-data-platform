# Data layout and lifecycle governance

[中文页面](data-governance.md)

This guide defines gradual governance for the shared data root. Existing paths remain compatible. Governance tools produce auditable inventory declarations and dry-run reports; a person reviews retention candidates before any operational action.

## Inventory and directory layout

The machine-readable inventory is `<artifacts_root>/metadata/lifecycle/inventory.json`, using schema `market_data_platform.lifecycle_inventory.v1`. Each entry records independent dimensions:

- `tier`: `raw`, `derived`, `state`, `run`, or `archive`.
- `role`: an operational role such as `current`, `rollback`, or `pilot`.
- `lifecycle_state`: for example `active`, `published`, or `superseded`.
- `disposition`: `keep`, `retire_candidate`, or `review`.

An entry marked `superseded` may still have disposition `keep`; `retire_candidate` only adds it to a review list.

New write paths are converging on:

```text
raw/<provider>/<dataset>/<version>
derived/<dataset>/<version>
state/<workflow>/<run_id>
runs/<workflow>/<run_id>
archive/<dataset>/<version>
```

Migrations use physical directories and files. After a move, update absolute paths in manifests, receipts, and runtime configuration. `current`, `latest`, and `rollback` are version entry points that still use symlinks in the existing publication flow. Do not bulk-replace them as part of historical-data migration; changing them requires separate updates to publishers, schedulers, and rollback scripts.

## Code lifecycle

```text
provider API -> ingest -> immutable raw asset -> standardize
-> standardized/canonical asset -> quality receipt -> publish -> published asset
```

`market_data_platform.ingest` owns provider access, request reliability, and raw landing. `market_data_platform.standardize` owns field mapping, type conversion, deduplication, sorting, time handling, and source fusion. The quality receipt records availability; the publication layer manages versions, aliases, manifests, and provenance. Model windows, labels, embeddings, and training samples stay outside the platform standardization layer.

The first migrated path moved the TuShare A-share `daily_clean` builder and daily schema to `standardize`; `providers.tushare_a_share_clean` remains as a compatibility entry point. Existing quality validation remains in the quality implementation. `ingest.tushare.daily` is the stable new entry point while lower-level provider runtime moves in stages. Architecture checks prohibit `standardize` from depending on `providers` or `ingest` implementations.

## Current and latest paths

Downstream consumers start from `metadata/current_assets/<market>_current.json`. The contract's `alias_path` is the stable path for the registry and manual operations. New directory publications use immutable version directories; whether the stable entry point is a symlink depends on downstream manifest-reading support. Existing real directories and regular-file forms of `latest` remain compatible and are reported by audits. Missing contract candidates still participate in health checks, while the registry omits entries with `exists=false`.

For date-ranged assets, the end date in a version-directory name should match manifest `query_end_date`. Current path audits cover `daily`, `adj_factor`, `daily_basic`, `daily_clean`, and `limit_status`; they also detect symlink chains that end at mutable `latest` directories and differences between contract paths and resolved targets. Audits report issues without renaming or rebuilding assets.

```bash
marketdata governance audit-current-paths \
  --artifacts-root "$DATA_PLATFORM_ROOT"

marketdata governance audit-current-paths \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --out "$DATA_PLATFORM_ROOT/metadata/lifecycle/current-path-audit.json"
```

## Retention planning

`plan-retention` supports three inventory rules: `explicit_path` protects, flags, or requests review for a path; `retain_newest` keeps the newest N direct child directories by the rightmost date and basename; and `json_status` selects JSON files whose top-level `status` matches an allowlist.

```bash
marketdata governance plan-retention \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --out "$DATA_PLATFORM_ROOT/metadata/retention/retention-dry-run.tsv" \
  --latest-link "$DATA_PLATFORM_ROOT/metadata/retention/governance-latest.tsv"
```

The command scans assets and writes a report. It does not delete, move, or rename data. The existing systemd retention task continues to use its narrower policy, with separate schemas: governance output uses `governance-latest.tsv`, the scheduled task uses `scheduled-latest.tsv`, and `latest.tsv` remains a legacy compatibility entry point that may contain `action=delete`. Lifecycle review uses `governance-latest.tsv` only.

Timestamped reports are not overwritten by default. `--latest-link` atomically replaces an existing symlink and rejects a regular file with the same name. Publish each run under a new timestamped filename.

## Report input snapshots

Historical daily-report replay uses immutable version directories, not the mutable `*_latest` entry. The builder creates a symlink overlay and an `a_share.report_input_snapshot.v1` receipt with source directory, version date, and `manifest.yml` hash.

```bash
uv run python scripts/operations/build_a_share_report_input_snapshot.py \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --target-date YYYYMMDD \
  --output-root /tmp/a-share-report-snapshot-YYYYMMDD \
  --dataset daily --dataset daily_basic --dataset adj_factor \
  --dataset limit_status --dataset moneyflow_ths
```

If any explicitly requested dataset lacks a completed immutable version covering the target date, the command fails without producing a partial snapshot. The receipt proves selected input versions and manifest identity; it does not make missing data available or replace downstream file validation.

## Current-directory versioning

The first batch materialized seven TuShare A-share datasets into dated physical directories: `broker_recommend`, `ths_index`, `stk_holdertrade`, `moneyflow_hsgt`, `limit_step`, `limit_cpt_list`, and `limit_list_ths`. The migration moves directories on the same filesystem and updates their `manifest.yml`; `latest` symlinks remain for older readers until publishers switch to explicit version directories.

The materialization tool supports a dry run:

```bash
uv run python scripts/operations/materialize_current_versions.py \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --fallback-date YYYYMMDD \
  --dry-run \
  --dataset DATASET
```

It rejects directories with no manifest, a non-completed status, or zero rows. `hsgt_top10`, `margin`, and `ths_member` without a manifest are excluded from this batch. A later batch materialized `dc_concept`, `dc_concept_cons`, `flow_ownership_features`, `holder_structure_features`, `hotspot_features`, `kpl_concept_cons`, `kpl_list`, `report_rc`, `stk_surv`, `ths_hot`, and `top_inst_events` using the same rule.

`fund_portfolio_features` had a newer physical version while its current entry still pointed to the older one; the switch was deferred until the data content was confirmed. `margin_detail` and `moneyflow_ths` had no valid rows and were not published at that point.

## Staging and retention safety

`staging/` is for material still being built, validated, or awaiting a publication decision. Archive a completed staging directory by moving the whole directory to `archive/staging/<date>/`; do not leave a symlink at the old location. Before moving it, confirm that:

- its receipt is in a terminal state;
- it contains no lock file and no active job or scheduler references it;
- the formal artifact or research result has been preserved;
- paths in manifests, receipts, and reports point to the new location.

Keep directories that do not meet these conditions in `staging/` and explain why in `staging/README.md`. `scripts/operations/relocate_data_root.py` can rewrite old absolute paths in text metadata; it reports changes by default and writes only with `--apply`. `scripts/operations/archive_staging.py` also checks by default and moves only with `--apply`; it rejects directories containing lock files or symlinks.

The retention TSV reports:

- `logical_bytes`: total logical size of regular-file path names.
- `allocated_bytes`: allocated blocks deduplicated by `(device, inode)`.
- `reclaimable_bytes`: counted only when the path covers every hard link to an inode.
- `external_hardlink_inodes`: inode count with hard links outside the path.

Rules may cover the same inode, so row sizes must not be summed. Rescan immediately before any actual retirement to account for links created or changed after report generation.

The planner collects `alias_path` and `resolved_path` from all current contracts and explicit `disposition=keep` paths from the inventory. A candidate overlapping a protected path by equality or parent/child relationship is forced to `keep`. Both lexical paths and resolved symlink targets must remain under the artifacts root. Explicit rules cannot select the entire artifacts root. The planner does not follow symlinks; a candidate containing a cross-filesystem directory is downgraded to `review`.

## Remaining current assets

Since the earlier versioning review, `fund_portfolio_features` was switched to its newer physical version and `moneyflow_ths` was materialized while retaining a compatibility entry point.

The current manifests for `hsgt_top10`, `margin`, and `margin_detail` report zero rows, so the current contract marks them temporarily unavailable. `ths_member` has no `manifest.yml` and is marked for republication. When generating a current contract, a directory asset without a manifest is marked for republication and an asset with `totals.rows: 0` is marked temporarily unavailable. This keeps empty or unverified directories out of the normal data path.

When a provider returns an empty result, retain the historical directory and mark the current asset unavailable. If the manifest file count differs from the Parquet file count in the directory, mark it for a consistency republication. These checks catch provider fluctuations and mixed outputs caused by reusing an old output directory during refresh.

## A-share minute-data decisions

The current inventory protects:

- the `minute_1m` current alias and current version `minute_1m_v3_20260714`;
- rollback alias `minute_1m_pre_v3_20260714` and previous version `minute_1m_v3_20260711`;
- older `minute_1m_pre_v3_20260711` and v2 versions, pending group archival review;
- raw TuShare minute history and the full-v1 snapshot;
- universe staging audit trails and reports.

`minute_1m_pilot_20260711` payload retirement completed on 2026-07-14. The lifecycle inventory retains its tombstone, and its coverage, build, and retirement receipts remain audit evidence. `minute_1m_pre_v2_20260710` stays in `review` until source lineage is confirmed or it is archived.

The TuShare replacement campaign completed 820 available trading days and published the independent candidate `minute_1m_tushare_candidate_v1_20260727`. Its acceptance receipt rejected canonical cutover, so both the current Guan version and the TuShare candidate remain `keep`; do not mark the Guan version `superseded`. TuShare-native operational promotion uses the separate `minute_1m_tushare` alias and immutable `minute_1m_tushare_v1_YYYYMMDD` versions. This does not change the canonical-cutover decision. Keep the operational alias, resolved version, version receipt, and promotion receipt. Released locks and empty staging/tmp directories are listed by separate rules. Complete deal checkpoints stay in `review` until their manifest and rollback evidence can be archived together. Reports do not process these items automatically.

## Manual retirement gate

Before any cleanup, verify each condition:

1. The action is `retire_candidate`.
2. The path is not referenced by `current`, `latest`, or `rollback`.
3. No related process holds a lock.
4. Successor, manifest, coverage, and cutover evidence are complete.
5. Metadata needed for audit has been archived.
6. Inodes and reclaimable space have been recalculated close to execution.
7. Explicit human approval has been recorded.

The current implementation stops at the dry-run stage.
