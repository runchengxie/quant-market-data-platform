# Shared Data Contracts

[中文页面](contracts.md)

> - status: active
> - owner: market-data-platform
> - audience: human and agent
> - last_verified: 2026-09-06
> - source_of_truth: yes
> - superseded_by: n/a

## Artifact root

The shared artifact root defines the storage boundary between data tooling and strategy repositories. Configure it with:

```bash
export DATA_PLATFORM_ROOT=/data/market-data-platform
```

`DATA_PLATFORM_ROOT` identifies shared market-data inputs and platform artifacts. Downstream run records, caches, and reports belong to their respective repositories and are not configured through this variable.

Research code can match an input to the current contract with `market_data_platform.contract.match_current_contract_entry`, load the contract with `load_current_contract`, classify path existence with `path_kind`, and persist resolved path/manifest/current-contract associations with `describe_input_path`. These APIs read contract state only; they do not refresh data or publish assets.

Historical Hong Kong runs can still locate `metadata/current_assets/hk_current.json` through `current_contract_path(..., market="hk")`. This is path compatibility only; the platform has not resumed Hong Kong data production.

## Current data contract

The contract is stored at:

```text
<artifacts_root>/metadata/current_assets/<market>_current.json
```

Its top-level JSON includes `contract` metadata and an `assets` mapping. The contract metadata records `name`, `market`, `provider`, `version`, `artifacts_root`, and `target_date`. Each asset entry can carry `alias_path`, `resolved_path`, `manifest_path`, and `as_of`.

Build the contract from standard asset aliases:

```bash
marketdata contract build \
  --market a_share \
  --provider tushare \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --target-date 20260109
```

By default, the command merges the existing current contract and generates `metadata/dataset_registry.csv`. Add `--no-registry` to write only the JSON contract.

Inspect whether the contract exists, whether aliases are missing, and whether each asset's `as_of` is behind the target date:

```bash
marketdata contract inspect \
  --market a_share \
  --provider tushare \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --target-date 20260109 \
  --fail-on-severity error
```

The inspect output includes manifest coverage bounds and row counts. A-share research candidates should pass `--require-start-date YYYYMMDD`; a later asset start produces a warning and can block promotion with `--fail-on-severity warning`.

### Minute asset entry points

The two stable A-share minute aliases are:

```text
<artifacts_root>/assets/derived/a_share/minute_1m
<artifacts_root>/assets/derived/a_share/minute_1m_tushare
```

`minute_1m` is the Guan legacy canonical and currently points to `minute_1m_v3_20260714`. `minute_1m_tushare` is the TuShare-native operational canonical. Both have the same eight-column Parquet schema, but there is no promise of cross-provider feature equivalence. After acceptance, minute assets enter `a_share_current.json` and `dataset_registry.csv`; the TuShare asset uses key `minute_1m_tushare`, while Guan remains the `minute_1m` rollback/comparison asset. Downstream consumers record the selected alias, resolved version, and receipt hash. Guan daily source and market scope come from its coverage receipt; TuShare integrity and lineage come from its operational receipt. See [A-share minute data](operations/a-share-minutes.en.md).

Hong Kong market support was retired with RQData on 2026-07-26. `freeze-hk` and `hydrate-hk` have been removed. Historical reproduction uses tag `hk-freeze-20260613` or the private archive repository.

## Asset keys

For mainland China, `--provider tushare` explicitly selects the TuShare raw-asset layout. The `contract.provider` in `a_share_current.json` is then `tushare`. Common keys include:

| Key | Canonical relative path |
| --- | --- |
| `instruments` | `assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet` |
| `trade_cal` | `assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet` |
| `daily` | `assets/tushare/a_share/daily/a_share_all_daily_latest` |
| `adj_factor` | `assets/tushare/a_share/adj_factor/a_share_all_adj_factor_latest` |
| `daily_basic` | `assets/tushare/a_share/daily_basic/a_share_all_daily_basic_latest` |
| `limit_status` | `assets/tushare/a_share/limit_status/a_share_limit_status_latest` |
| `moneyflow` | `assets/tushare/a_share/moneyflow/a_share_all_moneyflow_latest` |
| `moneyflow_dc` | `assets/tushare/a_share/moneyflow_dc/a_share_all_moneyflow_dc_latest` |
| `moneyflow_hsgt` | `assets/tushare/a_share/moneyflow_hsgt/a_share_all_moneyflow_hsgt_latest` |
| `top_inst` | `assets/tushare/a_share/top_inst/a_share_all_top_inst_latest` |
| `ths_hot` | `assets/tushare/a_share/ths_hot/a_share_all_ths_hot_latest` |
| `dc_concept` | `assets/tushare/a_share/dc_concept/a_share_all_dc_concept_latest` |
| `dc_concept_cons` | `assets/tushare/a_share/dc_concept_cons/a_share_all_dc_concept_cons_latest` |
| `kpl_list` | `assets/tushare/a_share/kpl_list/a_share_all_kpl_list_latest` |
| `kpl_concept_cons` | `assets/tushare/a_share/kpl_concept_cons/a_share_all_kpl_concept_cons_latest` |
| `limit_step` | `assets/tushare/a_share/limit_step/a_share_all_limit_step_latest` |
| `limit_cpt_list` | `assets/tushare/a_share/limit_cpt_list/a_share_all_limit_cpt_list_latest` |
| `report_rc` | `assets/tushare/a_share/report_rc/a_share_all_report_rc_latest` |
| `stk_surv` | `assets/tushare/a_share/stk_surv/a_share_all_stk_surv_latest` |
| `broker_recommend` | `assets/tushare/a_share/broker_recommend/a_share_all_broker_recommend_latest` |
| `fund_portfolio` | `assets/tushare/a_share/fund_portfolio/a_share_all_fund_portfolio_latest` |
| `top10_holders` | `assets/tushare/a_share/top10_holders/a_share_all_top10_holders_latest` |
| `top10_floatholders` | `assets/tushare/a_share/top10_floatholders/a_share_all_top10_floatholders_latest` |
| `stk_holdertrade` | `assets/tushare/a_share/stk_holdertrade/a_share_all_stk_holdertrade_latest` |
| `moneyflow_ths` | `assets/tushare/a_share/moneyflow_ths/a_share_all_moneyflow_ths_latest` |
| `limit_list_ths` | `assets/tushare/a_share/limit_list_ths/a_share_all_limit_list_ths_latest` |
| `margin_detail` | `assets/tushare/a_share/margin_detail/a_share_all_margin_detail_latest` |
| `margin` | `assets/tushare/a_share/margin/a_share_all_margin_latest` |
| `namechange` | `assets/tushare/a_share/namechange/a_share_all_namechange_latest.parquet` |
| `margin_secs` | `assets/tushare/a_share/margin_secs/a_share_all_margin_secs_latest.parquet` |
| `st_history_reconstructed` | `assets/tushare/a_share/st_history_reconstructed/a_share_all_st_history_reconstructed_latest.parquet` |
| `st_intervals_reconstructed` | `assets/tushare/a_share/st_intervals_reconstructed/a_share_all_st_intervals_reconstructed_latest.parquet` |
| `hsgt_top10` | `assets/tushare/a_share/hsgt_top10/a_share_all_hsgt_top10_latest` |
| `ths_index` | `assets/tushare/a_share/ths_index/a_share_all_ths_index_latest` |
| `ths_member` | `assets/tushare/a_share/ths_member/a_share_all_ths_member_latest` |
| `daily_clean` | `assets/tushare/a_share/daily/a_share_all_daily_clean_latest` |
| `flow_ownership_features` | `assets/tushare/a_share/flow_ownership_features/a_share_all_flow_ownership_features_latest` |
| `hotspot_features` | `assets/tushare/a_share/hotspot_features/a_share_all_hotspot_features_latest` |
| `fund_portfolio_features` | `assets/tushare/a_share/fund_portfolio_features/a_share_all_fund_portfolio_features_latest` |
| `holder_structure_features` | `assets/tushare/a_share/holder_structure_features/a_share_all_holder_structure_features_latest` |
| `top_inst_events` | `assets/tushare/a_share/top_inst_events/a_share_all_top_inst_events_latest` |
| `holdertrade_events` | `assets/tushare/a_share/holdertrade_events/a_share_all_holdertrade_events_latest` |
| `hsgt_market_features` | `assets/tushare/a_share/hsgt_market_features/a_share_all_hsgt_market_features_latest` |
| `normalized_fundamentals` | `assets/tushare/a_share/normalized_fundamentals/a_share_all_normalized_fundamentals_latest` |
| `pit_fundamentals` | `assets/tushare/a_share/pit_fundamentals/a_share_all_pit_fundamentals_latest` |
| `industry_changes` | `assets/tushare/a_share/industry_changes/a_share_all_industry_changes_latest` |
| `universe_by_date` | `assets/universe/a_share_all_full_by_date.csv` |
| `universe_symbols` | `assets/universe/a_share_all_full_symbols.txt` |
| `universe_meta` | `assets/universe/a_share_all_full_by_date.meta.yml` |

The complete key inventory, including reconstructed ST history, THS index assets, and all provider fields, remains available in the Chinese companion. Do not infer that a key is present in a published contract merely because it is supported by the layout.

`daily_clean.is_st` has a value only when the build receives a validated, full-range `st_history_reconstructed` asset. Otherwise it is unknown; do not backfill historical ST state from current instrument names. The build manifest records `st_history_file`. Research-quality validation checks its receipt, hash, date coverage, and row-level `is_st` consistency. `revision_safe=false` means the historical source does not prove announcement-time availability.

Reconstructed ST history records effective dates separately from information availability. Its `available_from` field is the first exchange session on or after the effective start when `ann_date` predates that start; when `ann_date` is on or after the effective start, availability begins on the next exchange session after `ann_date`. A missing or malformed `ann_date`, or a missing next session in the supplied calendar, leaves availability unknown. Receipt schema `market-data-platform.reconstructed-st-history.v2` identifies this contract. This conservative date rule does not establish an exact intraday publication time, so `revision_safe` remains false.

`daily_clean` schema `tushare.a_share.daily_clean.v2` carries the matching `st_available_from` value beside `is_st`. It is null when the symbol has no active ST interval or when the source cannot establish availability. A consumer may use a positive ST value for a decision only when `st_available_from` is present and no later than that decision date; unknown availability must fail closed.

The public `load_a_share_research_daily` and `load_daily_watch20_daily` views expose `is_st` and `st_available_from` from each dated daily row. Both require a completed `tushare.a_share.daily_clean.v2` manifest with the `daily_clean.st_available_from.v1` contract and reject older assets. The generic A-share view also exposes `is_suspended` and `list_date`. Consumers must use the dated ST fields for historical decisions and must not substitute current instrument metadata.

`audit-a-share-st-event-timing` reads receipt-backed ST events and reconstructed ST history, auditing rows where `ann_date=trade_date`. It emits `st_event_timing_audit.parquet` and a hash receipt. Status values include `prior_dated_st_event`, `same_day_time_unknown`, `later_event_date_conflict`, and `no_active_prior_event`. Conflict candidates are limited to the same symbol and ST events effective within the following 10 calendar days and require manual review. The audit compares dates only, does not modify published `is_st`, and does not treat the `st` event endpoint as a complete daily state history.

Raw reference downloads from `download-a-share-reference` create a same-directory `*.receipt.json` with query range, row count, quality status, and file SHA-256. This proves download-file integrity, not intraday announcement availability. Publication must revalidate the source hash.

For DailyWatch20, `ths_hot_strict_v2` and `ths_hot_strict_v3` preserve TuShare's raw ranks without rank imputation. Compatibility strategy v2 requires all top 20 ranks. Production strategy v3 requires rank 1 and permits at most two missing other ranks. A degraded receipt records `rank_coverage_status=degraded`, `missing_ranks`, and `max_missing_ranks=2`; a missing rank 1 or more than two missing ranks blocks publication.

Revision-safe fundamentals start from raw v2. Every query unit records request start/completion, content SHA-256, byte size, and exact retrieval time. Completed raw, normalized v2, and PIT v2 snapshots include `integrity.files`, `integrity.aggregate_sha256`, and `manifest.seal.json`, with `immutable_snapshot=true`. Strict reads and publication recompute file hashes and the manifest seal. Completed directories are immutable; new observations use a new dated snapshot. Periods before `revision_safety.revision_safe_from` are `reconstructed_pit`. Vintage archives live under `assets/tushare/a_share/fundamentals_vintages/vintage=YYYYMMDD`; root `SEALED.json` binds all child manifests. These archives do not participate in current-alias selection.

`dc_concept_cons` retains schema `tushare.dc_concept_cons.v1` and adds daily completeness evidence. Consumers must check `completeness.trade_dates[<trade_date>].complete`, not only top-level `status: completed` or the presence of Parquet. Daily receipts include row/page/request counts, distinct theme count, terminal-page status, and `coverage.row_coverage_ratio`. Top-level `complete` fields summarize the batch. Empty results are marked incomplete; the previous Parquet remains as last-known-good but cannot pass the target-date production gate.

Without `--provider`, mainland contracts default to the TuShare layout. One `a_share_current.json` identifies the currently selected provider; it does not aggregate raw snapshots from multiple providers. For a partial migration, downstream consumers should still use canonical `a_share_current.json`, while health reports explicitly identify assets not yet produced, such as `adj_factor`, `limit_status`, or `daily_clean`.

## Dataset registry and manifests

The human-oriented registry is derived from current contracts and asset manifests:

```text
<artifacts_root>/metadata/dataset_registry.csv
```

Rebuild it independently with:

```bash
marketdata registry build --artifacts-root "$DATA_PLATFORM_ROOT"
```

Every published directory asset must contain `manifest.yml`; a single-file asset uses a neighboring `*.manifest.yml`. A manifest records enough dataset context for consumers to read status, row and symbol counts, date coverage, and lineage. The current contract remains authoritative for downstream path selection.

## Read-only Python contract API

Downstream Python projects can load the current contract through `PublishedAssetContract` and retrieve the complete on-disk manifest by asset key:

```python
from market_data_platform import PublishedAssetContract

contract = PublishedAssetContract.load_current(
    "/data/market-data-platform",
    market="a_share",
)
pit = contract.asset("pit_fundamentals")
print(pit.resolved_path)
print(pit.manifest["semantics"])
print(pit.provenance_dict())
```

The current contract selects a published version; the reader reloads the full manifest at `manifest_path` rather than treating a contract summary as the complete schema. `alias_path`, `resolved_path`, `manifest_path`, and explicit relative data paths must stay within `artifacts_root`. External absolute paths, `..` traversal, and escaping symlinks are rejected. `manifest_sha256` hashes exact file bytes; `content_fingerprint` hashes canonicalized manifest content and is stable across YAML formatting changes. `provenance_dict()` provides serializable contract/manifest hashes, schema version, lineage, and asset paths for research records.

Constructing an asset reference does not traverse large Parquet directories. If publication needs per-file verification, it must put the relevant checksums in the manifest; the reader preserves them in the loaded manifest and lineage.
