# A-share research asset profile

[中文页面](a-share-research-profile.md)

> status: active
> owner: market-data-platform
> last_verified: 2026-07-20
> source_of_truth: yes
> superseded_by: n/a

This page records the publication snapshot and usage boundaries for the A-share daily baseline, PIT financial statements, and historical industry assets. The authoritative state is `DATA_PLATFORM_ROOT/metadata/current_assets/a_share_current.json`. See [Research data integrity](research-integrity.en.md) for look-ahead controls and downstream ownership.

## Publication snapshot

The following snapshot was checked on 2026-07-16. It records the state at that time, not a live query of the current data root.

| Asset | Schema | Status at snapshot | Coverage and size |
| --- | --- | --- | --- |
| `daily_clean` | `tushare.a_share.daily_clean.v1` | Published | 2015-01-05 to 2026-07-16; 11,498,830 rows; 5,785 securities |
| `pit_fundamentals` | `tushare.a_share.fundamentals.pit.v1` | Published | Snapshot `a_share_top800_union_20150227_20260529_three_statement_pit`; manifest query range 1994-02-19 to 2026-06-15; 252,643 rows; 6,292 securities; 8 quarantined rows |
| `industry_changes` | `licensed.a_share.industry_changes.v1` | Published | SW 2021 level-3 industries; data through 2026-03-04; 7,780 rows; 5,851 securities |
| `normalized_fundamentals` | `tushare.a_share.fundamentals.normalized.v1` | Not published | `exists: false` in the current contract at the time of the snapshot |

`daily_clean` is the default research entry point. The `default` and `default_next` presets in `strategy-pipeline` use daily prices, daily valuation data, and the full-market by-date universe. Financial-statement and historical-industry features are enabled explicitly through `configs/presets/a_share_pit.yml`.

The PIT snapshot directory name, manifest query range, and research-universe definition are different concepts. Consumers must inspect the contract and manifest instead of inferring coverage from a directory name. At the recorded snapshot, `normalized_fundamentals` had no consumable alias and must be treated as missing.

The published `pit_fundamentals` asset described here is legacy v1. Its manifest end date of 2026-06-15 is only the end of the event query range; it does not provide a source-observation vintage. The current contract treats an explicit as-of value for this asset as missing, and the asset cannot pass v2 revision-provenance or freshness gates. Historical records collected in 2026 must not enter earlier-date revision-safe PIT backtests. Until normalized v2 and PIT v2 are rebuilt and explicitly promoted, this asset is exploratory and has status `legacy_unverified`.

## Usage boundaries

- `daily_basic` provides a daily valuation overlay; it does not contain PIT financial-statement data.
- Financial-statement research uses `pit_fundamentals` through the platform's formal as-of loader to build field-level state.
- `available_date` is generated from the disclosure date with a calendar-day delay. Do not read a record before that date. Also prove that source retrieval is no later than the target as-of date and check revision coverage and freshness.
- Join historical industry labels using `effective_date` and `end_date`. Do not backfill current industry labels into historical dates.
- The current contract records publication status. The manifest records query range, cutoff, row count, security count, and quarantined rows.
- Data publication only proves that an asset is readable. A complete PIT strategy still needs research-window, benchmark, cost, capacity, and out-of-sample gates.

## Historical-industry source semantics

| Source | Semantics | Use |
| --- | --- | --- |
| TuShare SW `index_member_all` | Interval membership with level-3 industry, `ts_code`, `in_date`, `out_date`, and `is_new` | Primary source for current `industry_changes` |
| TuShare CITIC `ci_index_member` | Interval membership with level-3 industry, `ts_code`, `in_date`, `out_date`, and `is_new` | Can be published as a parallel CITIC industry system |
| TuShare `index_classify` | Industry taxonomy inventory; SW distinguishes the 2014 and 2021 versions | Validate taxonomy/version; not a membership source by itself |
| Dated industry snapshot | Constituent set on a specified date; record source, date, and frequency | Sampling validation or interval derivation when evidence supports it |

The platform maps `in_date` to `effective_date`, `out_date` to `end_date`, and `ts_code` to `symbol`. `is_new` records current-source state and cannot replace historical intervals. Set `industry_system` explicitly to `sw2014`, `sw2021`, `citic`, or a more specific taxonomy name.

A snapshot cannot directly claim exact validity intervals. For validation, record snapshot date, source name, taxonomy, level, and frequency. Current labels without effective history or dated-source provenance must not enter historical PIT research.

## Publication and operations

The repository contains specifications, builders, validators, and contract keys; the data itself stays outside Git. See [A-share fundamentals operations](a-share-fundamentals.md) for the raw-to-PIT workflow.

| Asset | Current-contract key |
| --- | --- |
| Daily cleaned asset | `daily_clean` |
| PIT financial statements | `pit_fundamentals` |
| Normalized financial intermediate layer | `normalized_fundamentals` |
| Historical industry changes | `industry_changes` |
