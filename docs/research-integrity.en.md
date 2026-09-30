# Research data integrity boundaries

[中文页面](research-integrity.md)

This page defines the market-data platform's responsibilities for preventing look-ahead bias, survivorship bias, and research-sample contamination. Model selection, combinatorial purged cross-validation (CPCV), the Deflated Sharpe Ratio (DSR), probability of backtest overfitting (PBO), feature ablation, and candidate promotion belong to downstream research repositories. Execution dry runs, paper/live gates, and order audits belong to the execution repository.

## Platform controls

| Risk | Platform control | Main evidence |
| --- | --- | --- |
| Data-version drift | Current data contract fixes the downstream entry point | `metadata/current_assets/a_share_current.json` |
| Ambiguous asset scope | Manifest and registry record paths, date range, row count, and lineage | `manifest.yml`, `metadata/dataset_registry.csv` |
| Current constituents backfilled into history | By-date universe is the research-universe entry point | `universe_by_date`, `universe_meta` |
| Latest financial snapshot used in historical research | Raw → normalized → PIT layers publish by disclosure and availability dates | `normalized_fundamentals`, `pit_fundamentals` |
| Current industry labels backfilled into history | Historical industry assets require effective intervals or dated-snapshot provenance | `industry_changes` |
| Daily-asset quality gaps | Baseline and research-profile checks record missing/duplicate rows, price limits, suspensions, and calendar issues | `reports/a_share_daily_clean_*_validation_*.json` |
| Insufficient publication evidence | Validation and health checks precede current-contract publication | `reports/a_share_current_release_*.json`, `reports/a_share_current_health_*.json` |

These controls ensure downstream research reads versioned assets with lineage and quality evidence. The platform does not assess strategy overfitting or read a research run's Sharpe, IC, or backtest curve.

## A-share research assets

The authoritative current A-share contract is `metadata/current_assets/a_share_current.json`. Consumers should verify that:

- `daily_clean` passes baseline checks; formal research profiles also require research checks.
- `universe_by_date` comes from the platform-published by-date universe, with research settings `research_universe.mode: pit` and `require_by_date: true`.
- `daily_basic` valuation fields are a daily valuation overlay. PIT financial-statement research uses `pit_fundamentals`.
- `is_st` from the latest instruments snapshot is only a non-PIT label.
- PIT fundamentals v2 `available_date` is generated from disclosure date with a calendar-day delay. Research must use the formal as-of loader and validate `revision_covered`, `freshness_verified`, field-level revision provenance, and `_source_retrieved_at <= as_of_date`. Report-period query ranges do not prove observation availability. Legacy v1 and historical backfills collected in 2026 cannot claim revision-safe PIT semantics.
- Historical industry assets include `effective_date` / `end_date` or explicit dated-snapshot provenance.

See [A-share research profile](a-share-research-profile.md) for the full asset policy and [A-share fundamentals](a-share-fundamentals.md) for raw-to-PIT operations.

## Pre-publication checks

Before publishing or switching the current A-share contract, record these steps:

1. Download raw data or ingest a licensed local extract.
2. Build normalized or clean assets.
3. Run baseline validation.
4. Run research validation for research use.
5. Check the current data contract's health.
6. Generate publication evidence.
7. Update `metadata/current_assets/a_share_current.json` and `metadata/dataset_registry.csv`.

Related references: [contracts](contracts.md) covers current contracts, asset keys, manifests, and the registry; [A-share research profile](a-share-research-profile.md) covers PIT and historical-industry policy; [A-share fundamentals](a-share-fundamentals.md) covers raw-to-PIT ingestion and publication; [TuShare operations](operations/a-share-tushare.md) covers routine refreshes.

## Relationship to downstream overfitting controls

Downstream research controls should check that runs use the current platform contract and a by-date PIT universe; do not treat `daily_basic` as PIT fundamentals or backfill current industry / ST snapshots; and keep final out-of-sample (OOS) evaluation, CPCV, feature evidence, DSR, and promotion gates in the research layer.

The execution repository consumes research-exported `targets.json`. Broker logs, paper-trading evidence, and live audits are not part of the platform's current-data contract.
