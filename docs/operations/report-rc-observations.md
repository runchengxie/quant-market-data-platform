# Daily immutable broker forecast observations

The Linux observation command captures the configured provider's most recent 270 calendar days into a new timestamped attempt. It reuses bounded monthly pagination and preserves every actual observation. It never updates published assets or historical snapshots.

The [TuShare report_rc documentation](https://tushare.pro/document/2?doc_id=292) describes an evening update followed by the next morning's 09:15 refresh. The deployment owner schedules this separate observation at 10:00 Asia/Shanghai, including calendar days with new weekend reports. Credentials remain in the existing owner configuration.

```bash
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- \
  python -m quant_market_data_platform.providers.report_rc_observation \
  --artifacts-root "$DATA_PLATFORM_ROOT" --source-revision <merged-owner-sha> --dry-run
```

Dry-run validates the resolved query without network collection or creating an observation directory. Remove `--dry-run` only for authorized collection. The rolling window is configurable from one to 366 calendar days. Two acquisition workers are the default; the existing four-worker cap applies. A process lock rejects concurrent observation captures.

Output lives under `assets/tushare/a_share/report_rc_observations/observation_date=YYYYMMDD/attempt=<actual-UTC-time>_<nonce>/`. The actual starting clock determines the local date. Each repeat creates another attempt. Partial failures remain partial. No completion record is published until every monthly payload, acquisition time and code identity agrees with its hash-bound source manifest.

```bash
python -m quant_market_data_platform.providers.report_rc_observation \
  --verify "$OBSERVATION_DIRECTORY"
```

The `tushare.report_rc.observation.v1` receipt includes actual start and finish UTC timestamps, local observation date, query range, source revision, record/month counts and the source-history manifest SHA-256. Monthly acquisition metadata is fixed by that manifest, including query, pagination, retrieval time and acquisition code identity. The verifier checks the full metadata and payload chain. The source history uses the existing `tushare.report_rc.history.v1` contract.

Future research must use only an observation completed before its decision timestamp. The query's report dates and provider `create_time` do not override this local availability clock. A missed daily observation stays missing. Preserving today's retrieval does not recover original provider versions from earlier years.

Deploy only merged, CI-verified code from a stable noneditable release. The private deployment owner owns its narrow release pin, service, timer and rollback. Do not point a scheduler to a task worktree or replace the global data platform release for this separate observation.
