# Operations

[中文页面](operations.md)

This page is the operations index. Detailed procedures are maintained by topic so credentials, A-share ingestion, backups, and local governance remain easy to find.

| Task | Guide |
| --- | --- |
| Configure the shared data root and provider credentials | [Credentials](operations/credentials.en.md) |
| Run A-share / TuShare raw, clean, universe, current-data refresh, and raw-to-PIT workflows | [A-share TuShare](operations/a-share-tushare.md) |
| Fetch, build, publish, and inspect China macro and industrial context data | [Context data](operations/context-data.md) |
| Build consistent PIT features for mutual-fund top-10 holdings | [Fund top-10 ownership features](a-share-fund-top10-ownership-features.md) |
| Fuse Guan and TuShare A-share minute data | [A-share minutes](operations/a-share-minutes.md) |
| Archive public-source ETF minute data | [ETF minutes](operations/etf-minutes.en.md) |
| Restore the retired Hong Kong archive | [Hong Kong archive recovery](operations/hk-archive-restore.en.md) |
| Create local snapshot backups and run development/governance checks | [Backup and development](operations/backup-and-dev.en.md) |
| Build a publishable Python package | [Package publishing](operations/package-publishing.en.md) |
| Audit current/latest paths and plan a retention dry run | [Data governance](data-governance.en.md) |
| Understand test scripts and coverage | [Testing operations](operations/testing.en.md) |

## Common command groups

```text
marketdata paths
marketdata contract build / inspect
marketdata registry build
marketdata data catalog / materialize / query
marketdata context fetch / build / publish / inspect
marketdata data build-guan-annual-minutes
marketdata data build-guan-deal-minutes
marketdata data finalize-a-share-minute-coverage
marketdata data mirror-public-etf-minute
marketdata tushare backfill-etf-history
marketdata tushare build-etf-daily-forward-adjusted
marketdata tushare validate-etf-daily-pair
marketdata governance audit-current-paths / plan-retention
marketdata tushare plan-a-share-minute-backfill
marketdata tushare run-a-share-minute-backfill
marketdata tushare build-a-share-fund-top10-portfolio-features
marketdata tushare validate-a-share-fund-top10-portfolio-features
marketdata backup-data
```

Public CLI documentation tests derive reachable commands from the parser. When adding a command, document it in the relevant topic page and update the tests. Historical minute backfill coverage and resume limits are described in the [A-share minute guide](operations/a-share-minutes.md).

## Capture daily-clean raw inputs

`marketdata governance snapshot-clean-inputs --artifacts-root "$DATA_PLATFORM_ROOT"
--start-date YYYYMMDD --end-date YYYYMMDD --out-dir NEW_PATH` creates an independent,
date-filtered snapshot of daily, adjustment, daily-basic and limit-status inputs.
Serialize raw writers first. The output must be a new path under the data root.
A completed receipt pins source manifests and each captured file's SHA-256.
Failed attempts are preserved without a completed receipt. Scheduled retention
`apply` is disabled; use the reviewed lifecycle retirement conditions in
[data governance](data-governance.en.md) before any data removal.


## Private JSON configuration

Use one private configuration copied from `config/config.example.json`. Keep credentials outside Git and set mode `0600`. `DATA_PLATFORM_CONFIG` selects the file; when unset, an existing `${XDG_CONFIG_HOME:-$HOME/.config}/quant-market-data-platform/config.json` is selected. An invalid selected file stops the command. Existing process variables take precedence, including empty values. Null entries are unconfigured. Only `DATA_PLATFORM_ROOT` expands `${HOME}` or `~`; secret strings remain opaque. Legacy env files are read only when no JSON is selected.

```bash
marketdata config check --config "$DATA_PLATFORM_CONFIG"
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- python /path/to/job.py
```

`config check` reports names and configured booleans, without setting values or network requests. `config run` directly replaces the process using argv and preserves child exit status and signals. Services use an immutable installed release and a non-secret configuration path.


## QuantZone research factors

See [QuantZone operations](operations/quantzone.en.md).

```bash
marketdata quantzone check --config "$DATA_PLATFORM_CONFIG"
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG" --dry-run
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG"
```

For multi-vintage research inputs, use `read_statement_observations(...,
dataset="income", columns=[...])` to project required fields. Include identity,
disclosure, observation, availability and source-hash fields when downstream code
selects revisions. The reader verifies all partition checksums before applying filters.
### Resume THS constituent mirrors

`marketdata tushare mirror-a-share-ths-member --out-dir <output> --skip-existing` explicitly reuses validated concept cache partitions and fetches remaining concepts. Nonempty output directories are rejected by default. Resume with the same fields and provider configuration; constituents represent the acquisition snapshot.

### Single-file reference manifests

Reference publication writes a sibling `.manifest.yml` bound to the owner receipt and payload hash, with counts, dates and source semantics. Partial constraint provenance remains `partial`. Refresh the receipt and manifest whenever the reference payload changes.

Reference `version_date` records the version label date. When a source `end_date` is present, manifest freshness and query end use that actual source coverage date.
