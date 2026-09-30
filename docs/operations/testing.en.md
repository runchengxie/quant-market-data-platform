# Test scripts and coverage

[中文页面](testing.md)

This page describes the current coverage provided by `tests/` and `scripts/dev/`. Changes to public CLI commands, data contracts, path rules, or documentation should update the corresponding tests. Some documentation requirements are enforced by tests.

## Local checks

```bash
uv sync --extra dev
uv run --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run --extra dev python -m ruff check .
uv run --extra dev python -m ruff format --check .
uv run --extra dev ty check --error-on-warning
```

GitHub Actions checks pull requests and pushes to `main`. The quality workflow runs public-snapshot boundary checks, shared quality ratchets, Ruff, pytest, and dependency audit. The documentation workflow runs a strict MkDocs build. Required PR checks are merge gates.

When used as a `research-workspace` submodule, the workspace may also configure a shared `.githooks/pre-push` hook. It runs Ruff, formatting, `ty`, pytest, complexity ratchets, maintainability baselines, compatibility governance, and architecture governance. The shared hook is a workspace convention; it is not managed by this repository's `.pre-commit-config.yaml` and does not replace CI. The `ty` configuration combines the former daily and release scopes.

The full suite accumulates memory in one pytest process. `run_pytest_isolated.py` starts one process per eight test files by default and releases memory between batches. A focused module can run directly with `python -m pytest tests/<file>.py`. Coverage is a release diagnostic; scope it to the target module:

```bash
uv run --extra dev python -m pytest tests/<file>.py \
  --cov=market_data_platform --cov-report=term-missing
```

Dependency and static security checks:

```bash
uv run --extra dev pip-audit
uvx deptry .
uvx bandit -q -r src -lll
```

Add DuckDB query support with `uv sync --extra dev --extra duckdb`.

## Optional integrations

The standard `dev` gate does not install `pyqlib`. Default tests check native DataFrame equivalence, delayed imports, and missing-dependency errors. Real DataLoader tests use `pytest.importorskip` and skip clearly when `pyqlib` is absent. Run the real runtime case with:

```bash
uv sync --locked --extra dev --extra qlib
uv run --locked --extra dev --extra qlib python -m pytest \
  tests/test_published_assets.py -k qlib -q
```

This only tests the read-only mapping from published Parquet assets to Qlib DataLoader. DataHandler, Dataset, model training, experiment tracking, and backtest backends are not implemented here. TuShare collection tests can install `uv sync --extra dev --extra tushare`.

Some tests require `pyarrow` during import, so use the `dev` extra for the complete suite. `tests/conftest.py` clears `DATA_PLATFORM_ROOT`, `DATA_PLATFORM_METADATA_DB_PATH`, `DATA_PLATFORM_WAREHOUSE_DB_PATH`, and `HK_DATA_PLATFORM_ROOT` to prevent shell configuration from changing path-resolution tests.

## Test coverage map

| Test file(s) | Coverage |
| --- | --- |
| `tests/test_artifacts.py`, `tests/test_paths.py` | Artifact root and database paths, shared-path relocation, market paths, current-data contracts, dataset registry, and contract reports. |
| `tests/test_published_assets.py` | Current contracts, read-only asset-manifest API, path boundaries, explicit Parquet mappings, PIT/calendar filters, and delayed Qlib loading. |
| `tests/test_cli_dependency_boundaries.py` | CLI parser imports, optional-dependency errors, and stable help output. |
| `tests/test_quality_governance.py` | Governance scripts, public CLI documentation coverage, documentation style, compatibility lifecycle, and architecture boundaries. |
| `tests/test_data_warehouse.py`, `tests/test_backup_data.py` | Catalog refresh, standard-layer materialization, DuckDB queries, `data` commands, local snapshot backup, and CLI behavior. |
| `tests/test_data_providers_cache.py`, `tests/test_tushare_a_share.py` | Provider caches, local-asset priority, A-share code normalization, TuShare token checks, raw mirroring, retries, quotas, historical backfill, and current-data publication. |
| `tests/test_tushare_a_share_clean.py`, `tests/test_tushare_a_share_universe.py` | `daily_clean` building, quality checks, memory guards, and by-date universe generation/validation. |
| `tests/test_tushare_a_share_fundamentals.py`, `tests/test_tushare_a_share_research_assets.py` | Raw-to-PIT fundamentals, publication, historical-industry changes, and research-asset contracts. |
| `tests/test_tushare_a_share_flow_features.py`, `tests/test_tushare_a_share_hotspot_features.py`, `tests/test_tushare_a_share_ownership_features.py`, `tests/test_tushare_a_share_hsgt_features.py` | Flow, ownership, hotspot, mutual-fund holding, shareholder structure, top-institution, holder-trade, and northbound features. |
| `tests/test_a_share_minute_build.py`, `tests/test_a_share_minute_fusion.py`, `tests/test_a_share_minute_coverage.py`, `tests/test_a_share_minute_bj_overlay.py` | Guan annual/deal builds, unit conversion, source fusion, recovery, coverage classification, final audit, full-day replacement, Beijing Stock Exchange overlay, and receipt binding. |
| `tests/test_tushare_a_share_mins.py`, `tests/test_tushare_minute_chunk.py`, `tests/test_tushare_minute_backfill.py`, `tests/test_tushare_minute_replacement_campaign.py`, `tests/test_tushare_minute_replacement_campaign_runner.py` | TuShare mirroring, sidecars, 241-row grids, checkpoint recovery, bounded chunks, immutable backfill plans, staging resume, soft row budgets, and replacement campaigns. |
| `tests/test_minute_candidate.py`, `tests/test_cutover_a_share_minute.py`, `tests/test_detach_a_share_minute_v3.py`, `tests/test_audit_minute_overlap.py` | Candidate inventory, full-range semantic/feature regression, hard-link candidates and detachment, full-A and Shanghai/Shenzhen contracts, atomic cutover/recovery, and read-only Guan/TuShare overlap audits. |
| `tests/test_archive_guan_mobile.py`, `tests/test_guan_mobile_raw.py` | Guan removable-media archive, revalidation, provider-native promotion, and receipts. |
| `tests/test_tushare_platform_assets.py`, `tests/test_symbol_alias.py`, `tests/test_market_specs.py`, `tests/test_parquet_scanning.py` | Local TuShare asset reads, symbol aliases, supported markets/code mappings, and batched Parquet scans. |

## Governance tools

| Script | Purpose |
| --- | --- |
| `scripts/dev/run_pytest_isolated.py` | Run the full pytest suite in batches and release memory between processes. |
| `scripts/dev/quality_debt.py` | Inspect Ruff/`ty` coverage, complexity debt, and baseline/ratchet status. |
| `scripts/dev/maintainability_metrics.py` | Report large files, long functions, parameter counts, and public aggregation entry points. |
| `scripts/dev/compatibility_governance.py` | Check compatibility lifecycle inventory and repository usage. |
| `scripts/dev/architecture_governance.py` | Check core-module and A-share module boundaries. |

## Documentation gates

`tests/test_quality_governance.py` verifies that `docs/README.md` links the maintenance audit and operations guides, active prose avoids contrastive detours, every public leaf command reachable through `market_data_platform.cli.build_parser()` appears in active docs, the industry-membership download command has a clear entry point, and `docs/compatibility.md` covers all six lifecycle categories. When adding or renaming a `marketdata` command, update the public CLI list in `docs/operations.md`, add a topic-page example, and update tests.
