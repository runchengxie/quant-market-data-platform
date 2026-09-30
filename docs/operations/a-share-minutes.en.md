# A-Share Minute Data

[中文页面](a-share-minutes.md)

This page records the production assets, source semantics, rebuild workflow, and cutover rules. Retired intermediate versions and historical experiments are not treated as current production guidance.

## TuShare historical availability probe

A low-cost probe on 2026-08-31 used the current TuShare proxy and `stk_mins` for `000001.SZ` and `600000.SH`. The dates 2016-01-04, 2020-01-02, 2021-07-01, 2022-07-14, and 2022-07-15 each returned all 241 one-minute bars. Thus 2022-07-15 was an earlier campaign's chosen start date, not the provider's lower bound.

This is evidence for representative symbols and dates, not proof that the full market history has been downloaded. Large backfills still require per-session dynamic-universe sidecars, the 241-bar grid, unique keys, and schema validation. Probe the earliest candidate date before planning a backfill.

The first-day canary in the 2026-08-31 to 2026-09-01 campaign requested six sessions from 2016-01-04 through 2016-01-11. A provider-only historical-symbol rule using the `daily`/`daily_basic` intersection fixed the primary-table gaps, but TuShare returned HTTP success with zero bars for `001872.SZ`, `001914.SZ`, and `601360.SH`. The canary remains `partial`: no historical alias was published and these provider-confirmed absences were not silently treated as complete. Historical backfills need an auditable provider-no-data exception list or must retain the full-market completeness gate.

## Production state

As of 2026-07-27, the stable Guan legacy entry is:

```text
$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m
```

It points to `minute_1m_v3_20260714`. Its coverage receipt is `metadata/minute_fusion/a_share_minute_1m_v3_20260714.coverage.json` and has `status=passed` and `quality_status=passed`.

| Measure | Value |
| --- | ---: |
| Date range | 2016-01-04 through 2026-07-14 |
| Sessions | 2,556 |
| Minute rows | 2,587,512,152 |
| Guan annual sessions | 2,430 |
| Guan deal sessions | 37 |
| TuShare full-A sessions | 89 |
| Complete Shanghai/Shenzhen sessions | 2,556 |
| A-share sessions complete under point-in-time market definitions | 1,515 |
| Known Beijing Stock Exchange gaps | 1,041 |

`coverage_status=full_sh_sz` means Shanghai and Shenzhen are complete over the full range. The 89 TuShare sessions include Shanghai, Shenzhen, and Beijing. The remaining 1,041 Guan sessions after the Beijing market began contain Shanghai/Shenzhen only and are recorded as known gaps.

The TuShare asset is published through `minute_1m_tushare`, its operational receipt, the current contract, and the dataset registry. `minute_1m` remains the Guan rollback entry. Downstream defaults to TuShare; reproducing Guan results requires explicitly setting `minute_dataset="legacy"` and retaining the corresponding receipt path or hash.

The full-market Guan-to-TuShare replacement campaign started on 2026-07-15 and collected/revalidated 820 obtainable sessions. On 2026-07-27 it was published as the separate candidate `minute_1m_tushare_candidate_v1_20260727`, with 1,044,752,352 rows from 2022-07-15 through 2026-07-08 and complete Shanghai, Shenzhen, and Beijing coverage per session. Files were hard-linked from frozen staging without changing source values.

Semantic review compared 993,992,266 shared Shanghai/Shenzhen minute keys. Prices, volume/value, and most daily aggregates were highly consistent, but the median daily cross-sectional correlation of intraday-return ranks was about 0.971, below the 0.99 candidate-cutover threshold. Owner-native DailyWatch20 checks found 2025 median rank correlations of about 0.904 for `minute_last_30m_return` and 0.866 for `minute_active_ratio`. A 63-OOS-session production-variant screen around the 2025 source boundary found daily correlations of about 0.685 for IC, 0.710 for Top20 label return, and 0.719 for portfolio gross return. The screen was smaller than the release-grade 504/252 backtest, but sufficient to reject an unconditional replacement.

The candidate acceptance receipt therefore records `quality_status=review_required`, `canonical_cutover_approved=false`, and `current_alias_mutated=false`. It is available for independent review, Beijing/241-grid research, and backup use; it is not the current canonical and must not be mixed into the frozen Guan factor-discovery sample.

### TuShare operational baseline

After rejecting cross-provider equivalence as a cutover basis, TuShare was promoted as an independent native baseline. The stable entries coexist:

```text
$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m
$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m_tushare
```

`minute_1m` remains the frozen Guan canonical. `minute_1m_tushare` points only to a TuShare version that passes full-partition validation. TuShare promotion does not modify the Guan alias and does not claim equivalence in intraday features, models, or selections. TuShare is the downstream default; reproducing historical Guan results requires `minute_dataset="legacy"`. TuShare-native features, models, and risk thresholds need their own baselines.

The full structural audit of `minute_1m_tushare_v1_20260903` completed on 2026-09-04: 946 sessions, 5,027,904 stock-sessions, and 1,211,724,864 bars passed the 241-bar grid, 09:30 start, 15:00 end, unique time keys, and unified schema checks. Evidence is recorded in `metadata/minute_operational/acceptance/minute_1m_tushare_v1_20260903.full_audit.json`. This establishes structural readiness of the data asset, not a new factor/model/backtest baseline.

Assemble an immutable version from the audited 820-session candidate and complete incremental partitions after 2026-07-09, then promote it separately:

```bash
uv run python scripts/operations/tushare_minute_operational.py assemble \
  --base-receipt "$DATA_PLATFORM_ROOT/metadata/minute_candidate/tushare_all_a_20220715_20260708.candidate.json" \
  --incremental-root "$DATA_PLATFORM_ROOT/assets/tushare/a_share/minute_1m_full_v1_20260711" \
  --incremental-root "$INCREMENTAL_DATA_ROOT" \
  --trade-calendar "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --end-date YYYYMMDD \
  --output-dir "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m_tushare_v1_YYYYMMDD" \
  --receipt-json "$DATA_PLATFORM_ROOT/metadata/minute_operational/versions/minute_1m_tushare_v1_YYYYMMDD.json"

uv run python scripts/operations/tushare_minute_operational.py promote \
  --version-receipt "$DATA_PLATFORM_ROOT/metadata/minute_operational/versions/minute_1m_tushare_v1_YYYYMMDD.json" \
  --alias "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m_tushare" \
  --legacy-alias "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m" \
  --receipt-json "$DATA_PLATFORM_ROOT/metadata/minute_operational/promotions/minute_1m_tushare_v1_YYYYMMDD.json"
```

The assembler rechecks file SHA-256. Incremental partitions require `status=complete`, a 241-bar grid, complete dynamic universe, unique keys, and valid schema. Versions are published with same-filesystem hardlinks. The promotion receipt records the resolved Guan alias before and after promotion and fails if it changes.

The daily entry point `scripts/operations/tushare_minute_operational_daily.py` catches up from the current TuShare receipt's `date_max` to the latest session, uses the shared request-quota ledger, publishes a new immutable version, and atomically moves only `minute_1m_tushare`. Partial downloads are not published; a later run resumes missing symbols from sidecars. The 2026-08-31 operational receipt updated this TuShare alias; it did not replace Guan's `minute_1m`.

For a historical gap, inspect existing complete partitions first to avoid spending quota again. Confirm that the repair date is no later than the current version's `date_max` and exists as exactly one complete partition under an incremental root, then repeat `--repair-date YYYYMMDD`. Repair partitions receive the same sidecar, universe, 241-grid, key, row-count, schema, and Parquet-hash checks as ordinary increments. Assembly writes a new immutable staging version and does not alter stable aliases. Promote only after validation. Atomic same-filesystem symlink replacement means concurrent readers see either the complete old version or complete new version. Receipts record both targets for audit and rollback.

Historical Beijing queries use the [official old/new code mapping](https://www.bseinfo.net/service/code_mapping.html). Six pilot securities use 920 codes from 2025-05-06; other existing securities use 920 codes from 2025-10-09. Earlier dates are requested with the code valid at the time and normalized to the current 920 code before partition write. Newly listed securities originally assigned a 920 code are not converted. This preserves `stk_mins` historical query semantics and stable downstream keys.

## File schema and source semantics

Daily partitions use:

```text
assets/derived/a_share/minute_1m_v3_20260714/
└── trade_date=YYYYMMDD/
    └── part-00000.parquet
```

The Parquet schema is exactly `ts_code`, `trade_time`, `open`, `close`, `high`, `low`, `vol`, and `amount`. `trade_time` is mainland China wall-clock time without a timezone. Prices and `amount` are in yuan; `vol` is in shares.

Source labels are not embedded in Parquet. Daily source, coverage tier, market scope, row/security counts, time bounds, and file hashes are recorded in the `daily` array of the coverage receipt. Features that depend on source must join `canonical_source`, `tier`, and `market_scope` by session.

| Period | Canonical source | First minute | Use |
| --- | --- | --- | --- |
| 2016-01-04 through 2025-12-31 | Guan annual | 09:31 | Same-source historical discovery sample |
| 37 sessions in 2026 | Guan deal | 09:30 | Cross-source robustness |
| 89 sessions in 2026 | TuShare full day | 09:30 | Cross-source robustness |

Only one canonical source is retained per session. Whole-day replacement does not splice intraday segments or apply a uniform one-minute shift. Guan annual does not have a fixed 241-row grid; active minute counts may differ between Guan deal and TuShare. Opening/closing concentration, active minutes, and trade intervals must be reviewed by source. Initial factor discovery should use Guan annual from 2016–2025; use 2026 for Guan deal/TuShare robustness. Freeze the coverage receipt SHA-256 when fixing a research sample.

## Install and read-only checks

Install minute fusion dependencies with `uv sync --extra minute-fusion`. Check the alias and receipt rather than hard-coding a version directory:

```bash
readlink "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m"
```

The receipt is JSON under `metadata/minute_fusion/`; inspect its `status`, `quality_status`, `coverage_status`, and `summary` fields.

## Rebuild workflow

Every rebuild writes a new version directory. The `minute_1m` alias moves only after the final receipt passes. The production sequence is:

1. `marketdata data build-guan-annual-minutes` builds Guan annual daily partitions.
2. `marketdata data build-guan-deal-minutes` aggregates and validates Guan removable-drive trade data.
3. `marketdata tushare mirror-a-share-mins` or an immutable backfill plan produces complete TuShare sessions.
4. `marketdata data finalize-a-share-minute-coverage` materializes whole-day replacement and creates the coverage receipt.
5. `scripts/operations/detach_a_share_minute_v3.py` detaches hardlinks inherited from an older version.
6. `scripts/operations/cutover_a_share_minute.py` performs the atomic cutover.

Options depend on the target date and receipts. Check command help before execution. A complete TuShare replacement campaign should create a partition inventory, run the full-range semantic/feature regression, and publish only a separate candidate. These steps do not modify `minute_1m`. The legacy `marketdata data fuse-a-share-minutes` is for migration of old mixed-source assets only.

Guan annual unit rules differ by vintage: 2016–2025 uses `vol = volume * 100` and `amount = amount`; 2026 uses `vol = volume` and `amount = amount / 100`. Builders validate dates, sessions, duplicate keys, finite values, negative volume/value, and OHLC bounds. Guan deal prices are in fen and volume in shares; price is divided by 100 and amount is `Price * Volume / 100`. The true 09:25 opening-auction trade maps to 09:30; continuous auction uses minute-end labels; closing auction maps to 15:00. Builders do not synthesize zero-volume minutes.

Annual files are scanned serially. Default memory budget is derived at runtime from system and cgroup availability; `--memory-limit` can override it. Resume trusts only source fingerprints, successful receipts, and revalidated current partitions.

## TuShare staging and backfill

Long-history retrieval uses an immutable plan and isolated staging, for example:

```bash
marketdata tushare plan-a-share-minute-backfill \
  --scope all-a --start-date YYYYMMDD --end-date YYYYMMDD \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --instruments "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --backfill-root "$DATA_PLATFORM_ROOT/staging/tushare_minute_backfill" \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/<name>.plan.json" \
  --segment month --batch-size 20 --cooldown-seconds 1 \
  --token-env TUSHARE_TOKEN_2

marketdata tushare run-a-share-minute-backfill \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/<name>.plan.json" \
  --receipt "$DATA_PLATFORM_ROOT/metadata/minute_backfill/<name>.receipt.json" \
  --token-env TUSHARE_TOKEN_2 --dry-run
```

Review the plan before removing `--dry-run`. It binds the calendar, instrument list, date set, batch size, endpoint, and output directory. The receipt explicitly records `writes_production=false`. Before promotion, staged data must pass sidecar, 241-grid, universe, key, session, schema, finite-value, and file-hash checks.

Reverse backfills use the same immutable-plan mechanism with `--date-order descending`. The planner enumerates sessions and orders both dates and monthly segments newest to oldest; execution remains serial and resumable in receipt order. The current TuShare operational asset starts on `20220715`; `20220714` is the first theoretical candidate. A local calendar candidate is not proof that download and promotion checks passed.

Reverse historical backfill is exposed by `tushare-minute-reverse-backfill.service`; its production timer is disabled. It shares request quota with canonical A-share updates and could block daily production work. Before manual execution, verify remaining quota and that the operational update completed. It writes only `staging/tushare_minute_backfill_reverse` and never changes a production alias. The default `--max-dates 1` is intended for observing an initial overnight window.

The default `--bj-missing-policy report` accepts a session as `accepted_bj_missing` only when missing securities are Beijing-only and all other grid/partition checks pass. Missing symbols, dates, and receipts are written to `metadata/minute_backfill/reverse_scheduler/bj_missing_report.json`; source partitions and download receipts remain `partial`. Full-market acceptance remains separate. Shanghai/Shenzhen gaps, malformed Beijing data, request errors, and corrupt files remain incomplete. Use `--bj-missing-policy error` for strict mode.

The scheduler skips fully downloaded or accepted sessions. `--max-dates` limits one plan; existing incomplete plans resume first and are not extended by raising the limit. If an old plan mixes accepted and unaccepted sessions, preserve its receipt and create a new plan for remaining candidates rather than requesting accepted Beijing gaps again. Beijing-only completion uses `--scope bj-only`; the current production minute dataset uses `--scope sh-sz-only`. Keep their plans, run directories, and receipts separate.

## Coverage finalization

`marketdata data finalize-a-share-minute-coverage` reconciles sources, performs whole-day replacement, audits partitions, and writes the final receipt. Production calls must explicitly supply the target version, trading calendar, Guan manifests, TuShare plan/receipt, and relevant source audits.

The release contract requires one partition per trading day; exactly one ordinary Parquet file per partition; exact agreement between source-day counts and the receipt; whole-day TuShare replacement; zero duplicate keys, wrong dates/markets, invalid sessions, or fatal volume/value issues; both `status=passed` and `quality_status=passed`; and consistent partition SHA-256 across promotion, coverage, and cutover.

Full A-share release requires Beijing coverage. The current version retains 1,041 known Beijing gaps, so publication must explicitly select the Shanghai/Shenzhen contract. A later full-A release must supply Beijing overlay data, production plan, and passed receipt together. The overlay adds only the dynamic Beijing universe and is reconciled independently against the existing Guan Shanghai/Shenzhen base. All three overlay options are required for `coverage_status=full_a_share`.

Ordinary TuShare VWAP/OHLC deviations are diagnostic. A nominal-value anomaly exceeding the price range by more than 10% plus a one-yuan-per-minute tolerance blocks publication. TuShare `vol=0, amount>0` also blocks. Guan annual price and volume/value bases differ historically; admission relies on vintage-specific unit contracts, source fingerprints, numeric checks, and overlap-day reconciliation.

## Cutover and rollback

Before cutover, confirm no minute-build process is active and run the cutover in dry-run mode:

```bash
.venv/bin/python scripts/operations/cutover_a_share_minute.py \
  --coverage-manifest "$DATA_PLATFORM_ROOT/metadata/minute_fusion/<new-coverage.json>" \
  --current-path "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m" \
  --new-version "$DATA_PLATFORM_ROOT/assets/derived/a_share/<new-version>" \
  --backup-path "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m_pre_<new-version>" \
  --target-market-scope sh-sz --dry-run
```

Replace placeholders and ensure the backup path does not already exist. The default `--target-market-scope` is `full-a-share`; current coverage has known Beijing gaps, so omitting `sh-sz` is rejected. The tool acquires `.a-share-minute-dataset.lock`, rechecks the version tree, partition hashes, receipt bindings, and `st_nlink == 1`, then atomically exchanges relative symlinks with `renameat2(RENAME_EXCHANGE)`. If the filesystem lacks this operation, it stops before changing current.

The current cutover contract is fixed to 2016-01-04 through 2026-07-14 (2,556 sessions, including 89 TuShare full-A sessions). The prior `minute_1m_v3_20260711` has only 2,553 sessions and 86 TuShare full-A sessions, so the current script rejects direct rollback to it. Preserve `minute_1m_pre_v3_20260714` as evidence of the prior location. A general rollback command compatible with the old date contract does not yet exist.

For an incident, stop minute writes/publication and retain the dataset lock, current, pending, and backup state; perform read-only checks of symlink targets, receipts, version directories, and hashes; prefer a repair version satisfying the current 2,556-session contract, then use dry-run and atomic cutover. Restoring an older date contract requires an explicit rollback contract, behavior tests, and rehearsal first. Never delete or manually rewrite `minute_1m`. If a cutover process is interrupted, preserve `.minute_1m.cutover-pending` and rerun with the same arguments so the tool can recover from current/pending/backup state.

Retire data only through the read-only inventory from `marketdata governance plan-retention`. Review current, rollback, receipts, and audit-relevant staging as a group.

## Tests

The minute-data test suite covers build/fusion/coverage, Beijing overlay, TuShare retrieval/backfill/replacement, cutover/detach, Guan archival, raw data, and overlap audit. The exact current pytest module list is maintained in the Chinese companion and should be run with `uv run --extra dev python -m pytest`.

DailyWatch20 TuShare-native features were compared with Guan over 3,716,570 shared rows. Main-feature Pearson correlations ranged from 0.959 to 0.999, with largest differences in `minute_volume_concentration` and `minute_price_volume_corr`. This establishes lineage and feature availability, not numeric equivalence. Results are recorded in `metadata/minute_operational/acceptance/daily_watch20_tushare_native_rebaseline_v1.json`. Model retraining and release-grade backtesting still need to use TuShare-native features independently.
