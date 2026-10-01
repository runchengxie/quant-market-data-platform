# A-Share / TuShare Operations

[中文页面](a-share-tushare.md)

TuShare is one of the supported providers for mainland China market data and is currently used primarily for A-share collection. After installing the optional dependency, provide the token through an environment variable or an untracked `.env.local`. Explicitly exported environment variables take precedence over `.env.local`.

Minute data has two explicitly source-separated entry points. `minute_1m` remains the Guan legacy canonical; `minute_1m_tushare` is the TuShare operational canonical with its own feature, model, and threshold baselines. The campaign started on 2026-07-15 collected 820 obtainable sessions from 2022-07-15 through 2026-07-08. Cross-source semantics, owner-native features, and strategy-source A/B results reject unconditional replacement of Guan, but do not prevent TuShare-native promotion as an independent line. The backfill commands on this page write only staging or TuShare raw assets. Only the operational promotion tool may move `minute_1m_tushare`, and it verifies that `minute_1m` is unchanged. See [A-share minute data](a-share-minutes.en.md) for source semantics, candidate evidence, and publication steps.

## Install and verify credentials

```bash
uv sync --extra dev --extra tushare
marketdata tushare verify-token
```

For a high-point account requiring a custom API URL, set the matching `TUSHARE_API_URL_2` in `.env.local` or pass it explicitly:

```bash
marketdata tushare verify-token \
  --env TUSHARE_TOKEN_2 \
  --api-url https://proxy-a.example.com
```

`TUSHARE_API_URL*` sets the TuShare SDK client's API URL; it is not equivalent to `HTTP_PROXY` or `HTTPS_PROXY`. With `--token-env TUSHARE_TOKEN_2` and no explicit `--api-url`, the program checks `TUSHARE_API_URL_2` and then falls back to `TUSHARE_API_URL`.

TuShare commands mask `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` by default. If the network requires a local mihomo, Clash, or other proxy environment variable to reach TuShare or its proxy domain, add `--use-proxy` to verification and download commands:

```bash
marketdata tushare verify-token --use-proxy
```

## Raw mirrors

TuShare raw, instrument, and trading-calendar downloads mask `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` during provider calls and set `NO_PROXY=*`, preventing local proxy timeouts from interrupting long-history collection. Add `--use-proxy` only when required. Transient timeouts, connection interruptions, proxy errors, and HTTP 502/503/504 are retried up to three times by default, starting at two seconds with exponential backoff capped at 30 seconds. Rate-limit responses cool down for 65 seconds by default. Tune these with `--retry-attempts`, `--retry-sleep-seconds`, `--retry-max-sleep-seconds`, and `--quota-cooldown-seconds`.

```bash
marketdata tushare export-a-share-instruments \
  --out "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet"

marketdata tushare mirror-a-share-trade-cal \
  --start-date 20260101 --end-date 20260526 \
  --out "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet"

marketdata tushare mirror-a-share-daily \
  --start-date 20260101 --end-date 20260526 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20260101_20260526_daily"

# Minute OHLCV; writes a TuShare raw asset or explicit staging directory.
marketdata tushare mirror-a-share-mins \
  --start-date 20260701 --end-date 20260709 \
  --token-env TUSHARE_TOKEN_2 --api-url https://proxy-a.example.com \
  --cooldown-seconds 0.3 --batch-size 20

# Download only the dynamically determined Beijing universe traded on each date.
marketdata tushare mirror-a-share-mins \
  --start-date 20260701 --end-date 20260709 \
  --exchange BJ --token-env TUSHARE_TOKEN_2 \
  --out-dir "$DATA_PLATFORM_ROOT/staging/tushare_minute_bj_probe" \
  --cooldown-seconds 1 --batch-size 20

marketdata tushare mirror-a-share-adj-factor \
  --start-date 20260101 --end-date 20260526 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/adj_factor/a_share_all_20260101_20260526_adj_factor"

marketdata tushare mirror-etf-adj-factor --help
marketdata tushare mirror-etf-daily --help

marketdata tushare mirror-a-share-daily-basic \
  --start-date 20260101 --end-date 20260526 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_20260101_20260526_daily_basic"

marketdata tushare mirror-a-share-limit-status \
  --start-date 20260101 --end-date 20260526 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/limit_status/a_share_limit_status_20260101_20260526"

marketdata tushare mirror-a-share-moneyflow \
  --start-date 20260101 --end-date 20260526 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/moneyflow/a_share_all_20260101_20260526_moneyflow" \
  --token-env TUSHARE_TOKEN_2

marketdata tushare mirror-a-share-fund-portfolio \
  --start-date 20141231 --end-date 20260529 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_portfolio/a_share_all_20141231_20260529_fund_portfolio" \
  --token-env TUSHARE_TOKEN_2 \
  --page-size 8000 --max-pages-per-period 300 \
  --skip-existing
```

Minute mirrors support checkpoint resume by default. Each session is requested in batches of 20 securities. On request failure or controlled interruption, successful prior batches are merged into a partial checkpoint. A complete sidecar binds the Parquet SHA-256, schema, row count, and security set. Old partitions, corrupt files, and files inconsistent with their sidecar are not trusted silently; resumption requests only missing securities. Each security must pass the audited 241-minute grid or the session remains partial. Refreshing an already complete date requires explicit `--force`.

`--batch-size` defaults to 20 and accepts 1–33. As of 2026-07-15, the minute endpoint has a documented single-response cap of 8,000 rows. A complete 241-bar session has 241 rows per security: 33 securities produce 7,953 rows; 34 would produce 8,194 and risk truncation. The cap of 33 applies only to a complete 241-bar 1-minute request and must not be generalized to another endpoint, frequency, or partial-session definition. Revalidate if the provider changes the row limit.

Before changing a production or long-history task from batch size 20 to 33, run an A/B test in isolated staging over identical dates and dynamic security universes. Compare sidecars, security sets, 241-bar grids, Parquet row counts, and normalized key/value contents, and record request counts, elapsed time, retries, and rate-limit cooldowns. Backfill batch size is part of immutable plan identity, so the two runs need separate plan/run directories. Use 33 only if contents match exactly and it introduces no partial data, truncation, or rate-limit errors; otherwise keep 20.

Minute commands automatically load an untracked `.env.local` or `.env` from the repository or current directory before token preflight; manual `source` is unnecessary. `--exchange BJ` resolves the full daily traded universe and filters `.BJ` securities for each date. The sidecar binds this filter's universe rule, source, and hash. This is safer than a static `--symbols` list, which can become invalid across listing or suspension dates. An exchange-filtered mirror must use an explicit, separate `--out-dir` to avoid a universe-identity conflict with the default all-A raw partition.

For a small number of scattered full-day promotion gaps, use the bounded chunk runner. It reads an existing `a_share.minute_tushare_full_day_plan.v1`, validates sidecar/Parquet bindings in the source root, and selects the earliest N incomplete sessions. Review the read-only dry run first:

```bash
marketdata tushare run-a-share-minute-full-day-chunk \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_fusion/tushare_full_production.json" \
  --full-day-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/minute_1m_full" \
  --receipt-dir "$DATA_PLATFORM_ROOT/metadata/minute_backfill/full_day_chunk_receipts" \
  --max-dates 3 --token-env TUSHARE_TOKEN_2 --dry-run
```

After review, remove `--dry-run`. Defaults are `--max-dates 3`, `--batch-size 20`, and `--cooldown-seconds 1`; use 1 conservatively or 5 during a stable window. One invocation calls the hardened mirror once and processes sessions serially in-process through `trading_dates`; it has no worker/concurrency option. Each actual invocation creates a unique JSON receipt with plan SHA-256, selected dates, endpoint identifier, throttle/retry parameters, and compact SHA-256 bindings for completed partitions. It never records token values. A source-root `flock` prevents overlapping scheduled jobs, even if they use different receipt directories.

If interrupted, the minute mirror first writes a daily partial sidecar. The runner attempts to mark its receipt `partial` or `interrupted`. On the next run, it re-evaluates sidecars and selects only incomplete dates; no plan edits or shell date concatenation are needed. Once all plan dates are complete, an actual invocation returns `noop`, requires no token, and writes no empty receipt. A scheduled integrity check can therefore continue without creating files. Dry-run does not load a token, access the network, or create locks, receipts, or directories. The runner has no Beijing-only mode and does not modify derived/current aliases; historical Beijing backfill and promotion use their dedicated workflows.

The historical minute canonical currently combines Guan annual, Guan deal, and TuShare full-day sources. Small spot checks can use the mirror commands above. Beijing-only completion, all-A comparison history, and Guan replacement research must use immutable plans and staging runners; never rewrite the production minute directory day by day.

## Historical minute backfill planning and resume

The planner reads only local trading-calendar and instrument Parquet files; it does not call TuShare. Each immutable plan binds source-file SHA-256 values, scope, dates, month/year segmentation, throttle parameters, endpoint identifier, and the single-worker constraint. Its data path is fixed to:

```text
$DATA_PLATFORM_ROOT/staging/tushare_minute_backfill/runs/<plan_id>/data/
```

Create separate Beijing-only and all-A plans:

```bash
mkdir -p "$DATA_PLATFORM_ROOT/metadata/minute_backfill"

marketdata tushare plan-a-share-minute-backfill \
  --scope bj-only --start-date 20160104 --end-date 20260709 \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --instruments "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --backfill-root "$DATA_PLATFORM_ROOT/staging/tushare_minute_backfill" \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/bj_only_20160104_20260709.plan.json" \
  --segment month --batch-size 20 --cooldown-seconds 1 \
  --token-env TUSHARE_TOKEN_2

marketdata tushare plan-a-share-minute-backfill \
  --scope all-a --start-date 20160104 --end-date 20260709 \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --instruments "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --backfill-root "$DATA_PLATFORM_ROOT/staging/tushare_minute_backfill" \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/all_a_20160104_20260709.plan.json" \
  --segment year --batch-size 20 --cooldown-seconds 1 \
  --token-env TUSHARE_TOKEN_2
```

The Beijing-only plan clips its effective start to the first Beijing Stock Exchange session, 2021-11-15. The all-A plan retains the daily dynamic Shanghai/Shenzhen/Beijing universe. The plans have different `plan_id` and run directories and cannot share a receipt.

For an initial trial, add `--max-dates 5` or `--request-budget 500`. Request budget is a hard upper bound: for each date, the planner divides the canonical unique security count in the complete historical instrument master for that scope by batch size and rounds up. It does not rely on historical listing estimates that could be affected by old code mappings. Estimated request counts remain in the summary for diagnostics; receipts separately record planned upper bounds and actual successful minute requests. Discovery requests for `stock_basic`, `daily`, and `daily_basic` are excluded and their minimum estimate is reported separately. When a limit is reached, the planner selects a date prefix and records `truncated_by`. `--dry-run` validates the plan without writing it.

For non-contiguous gaps, use `--dates-file`: newline/comma-separated `YYYYMMDD` text, a JSON date array, or a full-day promotion plan with a top-level `dates` array. The planner binds the file's SHA-256 and requires every date to be within the start/end range and open in the local calendar. The exact invocation is in the Chinese companion; it uses the same `plan-a-share-minute-backfill` CLI and immutable `--dates-file` option.

Run a no-API dry-run receipt before starting the serial resumable runner:

```bash
marketdata tushare run-a-share-minute-backfill \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/bj_only_20160104_20260709.plan.json" \
  --receipt "$DATA_PLATFORM_ROOT/metadata/minute_backfill/bj_only_20160104_20260709.receipt.json" \
  --token-env TUSHARE_TOKEN_2 --dry-run
```

Remove `--dry-run` only after reviewing the plan. The runner fixes `workers=1` and invokes the hardened minute mirror by month or year. Request errors or controlled interruption are checkpointed in `_minute_mirror.json`. After a segment succeeds or fails, the receipt is updated atomically. Reruns skip completed segments and resume missing securities from daily sidecars. Receipts record fetch vintage, sanitized endpoint identifier, actual successful requests, and errors; they include the token environment-variable name but never the token value. Failure messages also scrub token values and raw endpoint strings. If actual logical requests exceed the planned hard bound, the receipt records `budget_violation` and prevents resume under that plan; regenerate from a new master vintage.

Plan/receipt completion is not publication approval. Validate staging integrity, source semantics, and market overlays separately before promotion. The runner cannot switch a current alias or production directory.

### Quota-controlled replacement campaigns

`scripts/operations/tushare_minute_replacement_campaign.py` splits a non-contiguous all-market date set into resumable, non-overlapping daily batches across two lanes. `prepare` scans the union of daily sidecars under known run roots, excludes sessions already validated for full-universe promotion, registers partial dates for in-place resume, and divides fresh dates into a default 50-session daily batch split 25/25 by alternating index. The first date is further split into a three-session canary per lane and the remaining main phase. Preparation writes staging plans/manifests only and makes no provider requests.

`run-next` holds a campaign-level `flock`, verifies that formal refresh/backup services are not running, resumes partial work first, then starts the two single-worker plans with staggered timing. After each phase, it calls `validate_complete_minute_partition(..., require_full_universe=True)` for every session and atomically updates the ledger. Main work does not start until every canary passes. Each invocation completes at most one campaign date; partial or quota exits preserve sidecars for the next run. Do not resume the wider pre-campaign plan at the same time, or separate run roots may request the same dates twice.

To advance across campaign dates within one quota window, use the explicit budgeted mode:

```bash
uv run python scripts/operations/tushare_minute_replacement_campaign.py run-budgeted \
  --manifest "$CAMPAIGN_DIR/manifest.json" \
  --max-new-rows 67000000 --max-runtime-seconds 14400 \
  --poll-seconds 10 --interrupt-grace-seconds 300 \
  --quota-timezone Asia/Shanghai --quota-reset-guard-seconds 1200 \
  --run-window-start 00:45 --run-window-drain 04:15 --run-window-stop 04:20 \
  --heartbeat-seconds 300 --no-progress-limit 2 \
  --single-lane-threshold-rows 5500000
```

On the first start of a local quota day, `run-budgeted` records a window baseline and high-water mark from `partition.rows` in mutable daily sidecars. Later invocations on the same quota day inherit rows already consumed; rows from a previous day, including partial checkpoints, do not consume the new window. After each campaign date it advances while both row budget and runtime remain. Both lanes share the soft row threshold. At the threshold or monotonic runtime limit, the runner sends `SIGINT` to active lanes, waits for the minute mirror to atomically save partial sidecars, then exits this run successfully. The drain begins at 04:15 Beijing wall time; lanes still running at 04:20 are force-stopped. Wall time is re-read on every poll so machine suspend/resume cannot bypass the deadline. No lane starts outside 00:45–04:15. Corrupt sidecars, mismatched dates, or decreasing row counts fail closed.

Parent-issued `SIGINT`, the matching `interrupted/partial` receipt, and exit code 130 are normal checkpoints. A nonzero lane exit without an internal stop reason is retryable and returns 75. `budget_violation`, a failed immutable-plan receipt, validation failures, or ledger errors are fatal and return 70. A complete sidecar cannot override such receipt evidence. Shared-ledger `MinuteQuotaExceeded` and `MinuteQuotaPoolClosed` map to a normal `shared_quota_exhausted` checkpoint and prevent retry storms. Two consecutive runs with no newly persisted rows mark health `stalled` and trip the automatic-tick circuit breaker. After diagnosis, one recovery attempt requires explicit `--allow-stalled-retry`.

`ledger.json.active_run` updates run ID, PID, heartbeat, runtime, and added-row counts every five minutes. Read-only `status --manifest ...` supports human-readable and stable `--json` output, including complete/partial/remaining counts, campaign soft quota, last stop reason, heartbeat health, next safe window, and an ETA range based on recent windows. This campaign quota is a local work estimate; when the shared ledger is enabled, inspect its consumer/pool state too.

The campaign row budget counts persisted sidecar rows and is only a soft workload threshold. The provider hard quota counts physical requests. The shared ledger atomically reserves one request slot before every `stk_mins` call; empty, small, and full 8,000-row responses all consume one, and each outer retry is charged separately. `committed`, `reserved`, and `uncertain` consume pool capacity; only proven-unsent `released` and gate-rejected `rejected` requests do not. Production has a 10,000-request guaranteed pool. Only the tail-filler after 21:15 may use burst capacity; of a 20,000-request ceiling, 500 are held for probes, leaving an effective burst ceiling of 19,500.

When the shared ledger is deployed, pass the same quota configuration through preflight and both lanes in `run-next`/`run-budgeted`, for example:

```text
--minute-quota-mode enforce
--minute-quota-db <DATA_PLATFORM_ROOT>/metadata/tushare/minute_quota/minute_quota.sqlite3
--minute-quota-consumer replacement_campaign
--minute-quota-gate requests
--minute-quota-limit-requests 10000
--minute-quota-burst-limit-requests 20000
--minute-quota-safety-requests 500
--no-minute-quota-allow-burst
--minute-quota-limit-rows 160000000
--minute-quota-safety-rows 4000000
```

These options configure runtime only and do not change the immutable manifest. Canonical `MDP_TUSHARE_MINUTE_QUOTA_*` environment variables can supply them. Consumer defaults to `replacement_campaign`; the two lanes must not become separate consumers. Near `--single-lane-threshold-rows`, the runner deterministically interrupts the lane with the lexicographically later name, waits for its atomic partial checkpoint, and lets the other lane continue. The drained lane is recorded as an intentional checkpoint. If a run starts below the threshold, it starts only the first incomplete lane. Request reservations remain the precise in-flight quota gate. `--minute-quota-allow-burst` is reserved for the tail-filler; regular campaigns, accelerators, DailyWatch, and Top200 stay in the guaranteed pool under request holds.

Inspect shared usage for the actual token and Beijing quota date with `marketdata tushare minute-quota-status`. It reports only a short token fingerprint and lists committed, reserved, uncertain, residual holds, available capacity, and consumer details. It creates no request reservation. Expired leases are conservatively settled as uncertain during inspection. Back up the hidden HMAC key file next to the SQLite database together with the database.

The production schedule is: a 00:05 coordinator establishes DailyWatch/Top200 request holds; the history campaign runs 00:45–04:20; after DailyWatch raw data completes at 05:20, its hold is released; accelerator backfill runs 08:00–16:40 within the guaranteed pool; after Top200 raw data completes at 21:00, its hold is released; the tail-filler probes burst capacity 21:15–23:35. Opportunity timers do not set `Persistent=true`; hard-stop and coordinator timers may. A single service instance and campaign lock reject concurrent runs. Top200 tail authorization checks only the raw-completeness marker, not later factor/walk-forward results. Exact templates, renderer, marker contract, and enablement instructions are in `scripts/systemd/README.md`.

`--quota-reset-guard-seconds` reserves a safety interval before local midnight. Manual opportunity jobs and the overnight job share the same quota-day ledger high-water mark. The campaign performs raw acquisition and structural QA only; Guan/TuShare semantic review and production cutover remain separate gates.

Quota policy is immutable within a Shanghai calendar day. If a legacy rows pool already exists that day, the coordinator returns 75 and remains failed; ordered `Requires=` dependencies block downstream downloads. Do not mark this as a successful exit. The policy cannot safely self-heal that day, so the service does not hot-loop. Later opportunity timers may retry the prerequisite, and a persistent coordinator timer retries at 00:05 the next day. Check `tushare-minute-quota-coordinator.service` and the structured delay reason in the coordinator log. Tail policy conditions are also checked before any provider request. A new request pool and holds are created the next day; never rewrite the current day's pool.

After all target dates finish, the runner reconciles the full universe again and atomically writes `acquisition-readiness.json`. The marker sets `acquisition_complete=true`, `structural_ready=true`, `semantic_audit=pending`, `promotion_ready=false`, and `cutover_performed=false`. Subsequent timer ticks validate this marker and exit quickly; they never modify production aliases. If an interrupted control plane or older write order leaves the marker's ledger hash inconsistent, `reconcile-readiness --manifest ...` rebuilds it from read-only sources. This command does not access the network, modify the ledger, or touch production aliases; it revalidates every full-universe partition, lane receipt, and file binding.

Daily TuShare mirrors request the full market on open sessions and write `data/trade_date=YYYYMMDD/part.parquet`. For longer history, generate a segmented plan and resume by month or year:

```bash
marketdata tushare backfill-a-share-history \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --start-date 20240101 --end-date 20260531 \
  --dataset daily --dataset adj_factor --dataset daily_basic --dataset limit_status \
  --token-env TUSHARE_TOKEN_2 --segment month --dry-run
```

Review the plan before removing `--dry-run`. After every segment succeeds, `--sync-latest` may point the canonical latest alias at this snapshot. Re-running into the same output directory skips existing `trade_date` partitions by default, so it can fill gaps left by timeouts or quota limits.

For 2008–2014 adjustment factors and limit prices, write to staging and do not move `latest`:

```bash
marketdata tushare backfill-a-share-history \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --start-date 20080101 --end-date 20141231 \
  --dataset adj_factor --dataset limit_status \
  --token-env TUSHARE_TOKEN_2 --api-url https://proxy-a.example.com \
  --segment year --dry-run
```

Remove `--dry-run` only after reviewing the plan. Do not add `--sync-latest` until coverage, per-day unique keys, and partition receipts pass. Research should retain existing long-history raw results alongside the constrained variant.

## Daily clean and universe

`build-a-share-daily-clean` uses memory-managed streaming by default. It reads raw partitions in `trade_date` batches, writes temporary staging, and compacts through a streaming Parquet writer into the downstream-compatible `data/<symbol>.parquet` layout. It flushes every 120 sessions by default. On WSL/Linux it flushes early when `MemAvailable` falls below 2,048 MB and stops with an error below 1,024 MB. Tune with `--batch-trade-dates`, `--memory-soft-limit-mb`, and `--memory-hard-limit-mb`; set a guard to `0` to disable it.

`validate-a-share-daily-clean` uses column projection and Parquet batch scans rather than loading each file as a full pandas frame. The `baseline` profile blocks current-data refresh publication and checks manifest reconciliation, field types, unique keys, OHLC, volume, and amount. The `research` profile adds valuation, limit status, suspensions, trading calendar, listing age, board classification, and ST-source validation. Research validation checks every `is_st` and `st_available_from` value against dated ST history and fails if its receipt, hash, schema, or date coverage is invalid. `--st-history-file` must point to the `st_history_reconstructed.parquet` generated by `build-a-share-st-history` or a published latest asset; its v2 receipt must be `complete` and cover every build date. The daily-clean v2 output carries `st_available_from`; same-day announcements without an intraday time become available on the next exchange session. Without history, `is_st` and its availability remain unknown and research validation rejects the asset. `daily_basic` is a daily valuation overlay, not PIT fundamentals.

```bash
marketdata tushare build-a-share-daily-clean \
  --daily-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily" \
  --adj-factor-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/adj_factor/a_share_all_20240101_20260529_adj_factor" \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_20240101_20260529_daily_basic" \
  --limit-status-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/limit_status/a_share_limit_status_20240101_20260529" \
  --instruments-file "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --st-history-file "$DATA_PLATFORM_ROOT/assets/tushare/a_share/st_history_reconstructed/a_share_all_st_history_reconstructed_latest.parquet" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean" \
  --min-rows 3000000 --min-symbols 5000

marketdata tushare validate-a-share-daily-clean \
  --daily-clean-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean" \
  --require-valuation --require-limit-status --profile baseline \
  --out "$DATA_PLATFORM_ROOT/reports/a_share_daily_clean_validation_20240101_20260529.json"

marketdata tushare validate-a-share-daily-clean \
  --daily-clean-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean" \
  --profile research \
  --trade-cal-file "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --fail-on-severity warning --max-warning-rate 0.001 \
  --out "$DATA_PLATFORM_ROOT/reports/a_share_daily_clean_research_validation_20240101_20260529.json"

marketdata tushare build-a-share-universe \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --daily-clean-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean" \
  --start-date 20240101 --end-date 20260529 \
  --rebalance-frequency M --lookback-days 60 --min-window-days 30 \
  --min-rows 100000 --min-symbols 5000 --min-rebalance-dates 20
```

The universe builder uses a trailing median of trading value, so rebalancing does not use the current session's turnover.

## Current-data publication

`plan-a-share-current-refresh` only produces a plan. It does not call the provider, write files, or update a latest alias. The planned order is raw backfill, daily-clean build, baseline validation, research validation, universe build/validation, then current-data publication.

After the planned build and validations, run the promotion gate:

```bash
marketdata tushare promote-a-share-current \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --start-date 20240101 --end-date 20260529 --apply
```

Without `--apply`, the command performs pre-publication checks only. With it, the command updates TuShare raw and `daily_clean` latest aliases, copies the staged three universe files and manifests to canonical paths, creates `metadata/current_assets/a_share_current.json`, rebuilds `metadata/dataset_registry.csv`, and writes contract health and release evidence. Default evidence is `$DATA_PLATFORM_ROOT/reports/a_share_current_release_<start>_<end>.json`.

These validation reports must already exist with `status=passed`:

```text
$DATA_PLATFORM_ROOT/reports/a_share_daily_clean_baseline_validation_<start>_<end>.json
$DATA_PLATFORM_ROOT/reports/a_share_daily_clean_research_validation_<start>_<end>.json
$DATA_PLATFORM_ROOT/reports/a_share_universe_validation_<start>_<end>.json
```

The gate blocks only assets required by the daily current-data contract: instruments, trading calendar, four raw daily datasets, `daily_clean`, and the three universe files. PIT fundamentals, industry history, and live broker gates are research-readiness evidence, not default blockers for daily-contract publication.

To inspect the current contract or registry separately:

```bash
marketdata contract build --market a_share --provider tushare \
  --artifacts-root "$DATA_PLATFORM_ROOT" --target-date 20260526

marketdata contract inspect --market a_share --provider tushare \
  --artifacts-root "$DATA_PLATFORM_ROOT" --target-date 20260526 \
  --require-start-date 20240101 \
  --fail-on-severity error --format json \
  --out "$DATA_PLATFORM_ROOT/reports/a_share_current_health_20260526.json"

marketdata registry build --artifacts-root "$DATA_PLATFORM_ROOT" --market a_share
```

## Research assets and fundamentals

Research-facing commands cover PIT fundamentals, industry membership and changes, reference-data download/normalization/publication, and money-flow feature build/validation. Financial statements, forecasts, express reports, dividends, financial indicators, audits, business segments, and disclosure dates use a separate restartable raw-to-PIT workflow. `daily_basic` valuation fields are only a daily overlay. See [A-share fundamentals](../a-share-fundamentals.en.md) for the full PIT semantics.

Historical industry membership follows provider semantics. TuShare's Shenwan `index_member_all` and CITIC `ci_index_member` are interval sources. Key fields include `ts_code`, `in_date`, `out_date`, `is_new`, and level-one/two/three industry codes and names. In an authorized environment, save provider output as untracked CSV/Parquet and build the platform asset locally. The Shenwan 2021 third-level workflow uses `download-a-share-industry-membership`, `build-a-share-industry-changes`, and `validate-a-share-industry-changes`:

```bash
marketdata tushare download-a-share-industry-membership \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/source/index_member_all_sw2021_l3" \
  --src SW2021 --level L3 --is-new Y --is-new N \
  --min-rows 1000 --min-symbols 1000

marketdata tushare build-a-share-industry-changes \
  --source-file /path/to/tushare_index_member_all_sw2021_l3.csv \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/industry_changes/a_share_all_industry_changes_sw2021_l3" \
  --effective-date-col in_date --end-date-col out_date \
  --industry-code-col l3_code --industry-name-col l3_name \
  --industry-system sw2021_l3 --provider tushare \
  --min-rows 1000 --min-symbols 1000

marketdata tushare validate-a-share-industry-changes \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/industry_changes/a_share_all_industry_changes_sw2021_l3" \
  --min-rows 1000 --min-symbols 1000
```

ST, index constituents, and listed-company information are low-frequency reference datasets using the shared reference pipeline. Download all datasets to one staging directory. `stock_st` and `share_float` retain date-based `_parts/` and can resume from rate limits with the same command:

```bash
marketdata tushare list-a-share-reference-specs

REFERENCE_STAGING="$DATA_PLATFORM_ROOT/staging/tushare_reference_20260730"
REFERENCE_DATASETS=(stock_st index_weight stock_company stk_managers share_float)
for dataset in "${REFERENCE_DATASETS[@]}"; do
  marketdata tushare download-a-share-reference \
    --dataset "$dataset" --out-dir "$REFERENCE_STAGING" \
    --start-date 20220101 --end-date 20260730 \
    --token-env TUSHARE_TOKEN_2 --request-interval-seconds 0.2 --retries 5
done

marketdata tushare normalize-a-share-reference \
  --raw-dir "$REFERENCE_STAGING" --out-dir "$REFERENCE_STAGING" \
  --end-date 20260730 \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet"

marketdata tushare publish-a-share-reference \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --raw-dir "$REFERENCE_STAGING" --target-date 20260730
```

Without `--index-code`, the downloader requests CSI 300, CSI 500, CSI 1000, ChiNext, and SSE 50. Without `--exchange`, `stock_company` covers SSE, SZSE, and BSE. `index_weight_daily` expands monthly `index_weight` over actual sessions and provides normalized `drift_weight`. Publication writes date-versioned data, atomically replaces `latest.parquet`, and writes `.receipt.json` containing schema, SHA-256, row count, and quality status. `share_float` uses `limit + offset` pagination by announcement date, so a forwarding service's per-response capacity is not mistaken for the full day's total. Reaching configured `max_pages` fails explicitly and requires a narrower window. Cross-repository consumers read published assets and validate receipts.

### Historical trading constraints and ST reconstruction

`namechange` is segmented by year and paginated with `limit + offset`. `margin_secs`, `margin_detail`, and `suspend_d` are segmented by year; `slb_sec_detail` by month. `margin_secs` and `margin_detail` are segmented by open trading day. `st` is fully paginated and filtered by effective date. Every segment writes immutable Parquet and a SHA-256 receipt; reruns reuse only complete segments whose hashes match. Examples use the xiaodefa forwarding endpoint. Receipts record the endpoint associated with the token environment variable, never the token itself.

Download these datasets with `marketdata tushare download-a-share-constraint-reference` and explicit `--dataset`, date range, output directory, `--token-env TUSHARE_TOKEN_2`, and `--api-url https://proxy-a.example.com`. Covered datasets are `namechange`, `margin_secs`, `margin_detail`, `suspend_d`, `st`, and `slb_sec_detail`. Keep results staged until source, coverage, hash, and publication checks pass.

`margin_secs` only says that a security is eligible for margin financing/securities lending; it is an upper bound on short eligibility, not proof of same-day inventory. It does not report borrow fees, available quantity, or recall probability. `margin_detail` and `slb_sec_detail` report transactions that occurred and were reported; they do not prove that a research portfolio could have borrowed the stock at the time. `st` is a state-change event for cross-checks, not a complete daily ST state. `slb_sec_detail` history may be shorter than the research window; retain empty boundary segments and do not interpret them as proof that no stock was borrowable. Full-history daily downloads can be large, so run a short canary to verify permissions and endpoint before resuming into the same staging directory.

Historical ST is reconstructed from the complete `namechange` timeline. The builder truncates open-ended intervals at later name changes, bounds validity using instrument listing dates and the day before delisting, normalizes legacy Beijing codes to current 920 codes, and expands positive states only over open trading sessions. `stock_st` observations after 2022 can be used for cross-validation:

```bash
marketdata tushare build-a-share-st-history \
  --namechange "$CONSTRAINT_STAGING/namechange.parquet" \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --instruments "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --stock-st "$DATA_PLATFORM_ROOT/assets/tushare/a_share/stock_st/a_share_all_stock_st_latest.parquet" \
  --out-dir "$CONSTRAINT_STAGING" \
  --start-date 20080101 --end-date 20260731 \
  --min-precision 0.90 --min-recall 0.90

marketdata tushare publish-a-share-constraint-reference \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --source-dir "$CONSTRAINT_STAGING" --target-date 20260731
```

Publication rejects missing assets or ST reconstruction below its thresholds by default. The output is labelled `pit_class=reconstructed_pit` and `revision_safe=false`: it can support historical ST filters but is not evidence of real-time availability or revision-safe PIT. `--allow-partial` is diagnostic-only and must not enter the formal current contract. A completed, receipted name-change source may be published alone with `--datasets namechange`; that option selects datasets but does not relax quality or source-receipt checks.

Money-flow features combine raw `moneyflow` with daily turnover and market-value overlays. `daily.amount` is in thousands of yuan and is divided by 10 to match the ten-thousand-yuan unit in `moneyflow`; `daily_basic.circ_mv` is also in ten-thousand-yuan units:

```bash
marketdata tushare build-a-share-flow-ownership-features \
  --moneyflow-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/moneyflow/a_share_all_20240101_20260529_moneyflow" \
  --daily-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily" \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_20240101_20260529_daily_basic" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/flow_ownership_features/a_share_all_flow_ownership_features_latest" \
  --min-rows 100000 --min-symbols 5000

marketdata tushare validate-a-share-flow-ownership-features \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/flow_ownership_features/a_share_all_flow_ownership_features_latest" \
  --min-rows 100000 --min-symbols 5000
```

## Hotspot and event assets

Hotspot raw assets fall into three partitioning groups: hot lists, concept quotes, limit-up/broken-limit lists, limit ladders, and leading sectors are partitioned by trading date; broker forecasts and institutional surveys by calendar event date; monthly broker picks by month. With the 15,000-point account, pass `--token-env TUSHARE_TOKEN_2` and ensure its matching `TUSHARE_API_URL_2` is configured.

The mirror commands include `mirror-a-share-ths-hot`, `mirror-a-share-dc-concept`, `mirror-a-share-dc-concept-cons`, `mirror-a-share-kpl-list`, `mirror-a-share-kpl-concept-cons`, `mirror-a-share-limit-step`, `mirror-a-share-limit-cpt-list`, `mirror-a-share-stk-auction-open`, `mirror-a-share-stk-auction-close`, `mirror-a-share-report-rc`, `mirror-a-share-stk-surv`, `mirror-a-share-broker-recommend`, `mirror-a-share-moneyflow-ths`, `mirror-a-share-limit-list-ths`, `mirror-a-share-margin-detail`, `mirror-a-share-margin`, `mirror-a-share-hsgt-top10`, `mirror-a-share-ths-index`, `mirror-a-share-index-daily`, and `mirror-a-share-ths-member`. Use the matching `--out-dir`, date filters where supported, token options, and `--skip-existing` for resumable mirror jobs. `ths_index` is a single snapshot and takes no start/end dates. Index daily data is fetched per code and merged into one file; repeat `--index-code` to select benchmarks. THS members require fetching all concepts from `ths_index` and then retrieving `ths_member` for each, so set `--request-interval-seconds` to control request rate. Auction endpoints require account-specific authorization; by default they request only `ts_code` and `trade_date`. Confirm entitlement and fields before passing a complete `--fields` list.

`dc_concept_cons` starts on 2026-02-03, returns at most 3,000 rows per request, and is automatically paginated with a per-session completeness receipt. It requests `limit/offset` pages through the short final page and verifies that every row's `trade_date` equals the target date and that `(trade_date, theme_code, ts_code)` is unique. Results are ordered by `theme_code`. Page boundaries can split one theme and create small overlaps, so the mirror identifies shared boundary themes, re-fetches them by `theme_code`, and replaces the boundary rows. It fails closed without publishing a new partition/manifest if theme ordering reverses within a page, non-boundary keys overlap, re-fetched boundaries still overlap, `max_pages` is reached, or no short final page appears.

Successful manifests record per-date total data pages, requests, offset pages, boundary-repair pages, row count, distinct themes, and `theme_code` row coverage under `completeness.trade_dates[YYYYMMDD]`. `page_count` includes offset and repair data pages; `request_count` also includes the empty request that proves the end. Only a date with `complete: true` may be used by production fallback. A transient empty result is atomically recorded as `complete: false` while preserving last-known-good Parquet. Production consumers must check the target-date receipt, not infer availability from an old file still being present.

Public-fund holdings are mirrored by report period, then turned into PIT features available on the first trading day after disclosure. Annual and half-year reports can exceed 100 pages; retain `--skip-existing` for resume and raise `--max-pages-per-period` if needed. Build and validate with `build-a-share-fund-portfolio-features` and `validate-a-share-fund-portfolio-features`.

For CITIC industry membership, use `--industry-system citic_l3` with level-three fields from the `ci_index_member` extract. TuShare `index_classify` validates Shenwan 2014/2021 taxonomy lists; it does not replace interval membership history.

If Lixinger industry constituents are returned as a dated snapshot in an authorized environment, treat them only as snapshot data. Verify endpoint, fields, and coverage through local entitlement documentation or an authenticated probe. Record snapshot date, taxonomy, level, source endpoint/extract, and snapshot frequency. Snapshot diffs must not claim a precise effective date at a frequency the snapshots cannot support. Current labels without effective history or dated snapshot provenance are not valid inputs for historical PIT research.

## Fundamentals command entry points

Use the dedicated restartable raw-to-PIT commands for financial statements. The CLI exposes specification listing, planning, download, state/failure inspection, raw compaction, normalization, validation, PIT build/validation, and publication:

```bash
marketdata tushare list-a-share-fundamentals-specs
marketdata tushare plan-a-share-fundamentals --help
marketdata tushare download-a-share-fundamentals --help
marketdata tushare check-a-share-fundamentals-state --help
marketdata tushare list-a-share-fundamentals-failures --help
marketdata tushare compact-a-share-fundamentals-raw --help
marketdata tushare normalize-a-share-fundamentals --help
marketdata tushare validate-a-share-normalized-fundamentals --help
marketdata tushare build-a-share-fundamentals-pit --help
marketdata tushare validate-a-share-fundamentals-pit --help
marketdata tushare publish-a-share-fundamentals --help
marketdata tushare publish-a-share-pit-fundamentals --help
```

See [A-share fundamentals](../a-share-fundamentals.en.md) for the full raw-to-PIT workflow.

## Retired RQData entry point

RQData online reads and A-share collection were fully retired on 2026-07-26. `rqdata_a_share.py`, `cli_rqdata.py`, and `rqdata_runtime*.py` were removed from the active package. The A-share data path now uses TuShare platform assets; new research must not depend on RQData.
