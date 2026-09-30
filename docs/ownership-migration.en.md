# DailyWatch20 data ownership

[中文页面](ownership-migration.md)

`market_data_platform.research_views` is the authoritative implementation of DailyWatch20 data views. It produces data facts and source audits by:

- building the candidate universe from information available on the research date;
- validating the requested date's partitions and snapshot completeness;
- recording source files, content hashes, and data lineage;
- summarizing minute-data source inventories and input availability.

Readers fail when a Tonghuashun hot-stock snapshot is incomplete, stale, or dated inconsistently. `alpha-research` owns model-availability decisions, `portfolio-backtester` owns portfolio selection, and `research-apps` owns strategy-level experiment portfolios.

The data platform provides reproducible data views. It does not decide whether a strategy selects a security and does not store strategy models or experimental conclusions.

Workspace 2.0 ownership migration is complete. New data-reading, PIT-universe, or source-lineage logic belongs in `market_data_platform.research_views`. `strategy-pipeline` calls the public entry point and records the run receipt.

`resolve_daily_watch20_assets(..., minute_dataset="legacy" | "tushare")` defaults to TuShare and resolves the separate `minute_1m_tushare` alias and operational receipt. Reproduction of existing Guan research must explicitly set `minute_dataset="legacy"`; that path resolves `minute_1m` and remains the rollback and comparison source.
