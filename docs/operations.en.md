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
