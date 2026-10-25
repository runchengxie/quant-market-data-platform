# Backups and local development

[中文页面](backup-and-dev.md)

## Local snapshot backup

`marketdata backup-data` freezes local caches, universes, and configuration files into a snapshot directory with `manifest.yml`. It does not overwrite an existing snapshot.

```bash
marketdata backup-data --preset a_share_current --name a_share_current_20260526
```

## Standard layer and DuckDB queries

```bash
marketdata data catalog --artifacts-root "$DATA_PLATFORM_ROOT"
marketdata data materialize --help
marketdata data query --artifacts-root "$DATA_PLATFORM_ROOT" --sql "select 1 as value"
```

Install DuckDB support with:

```bash
uv sync --extra dev --extra duckdb
```

## Local development checks

Local development and public CI use the same complete gate:

```bash
uv sync --locked --extra dev
uv run --locked --extra dev bash scripts/dev/check_quality.sh
```

Set `TMPDIR` to an external temporary root with at least 20 GiB available. The gate
creates its own subdirectory, removes it on success, and retains it on failure.
Dependency auditing requires public network access. The individual commands below
are diagnostic tools, and their governance budgets are included in the full gate.

```bash
uv sync --extra dev
uv run --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run --extra dev python -m ruff check .
uv run --extra dev python -m ruff format --check .
uv run --extra dev ty check --error-on-warning
```

Before pushing, run the local governance checks:

```bash
uv run --extra dev python scripts/dev/quality_debt.py --skip-ruff --complexity --check-baseline --check-ratchet
uv run --extra dev python scripts/dev/maintainability_metrics.py --check-baseline
uv run --extra dev python scripts/dev/compatibility_governance.py --check
uv run --extra dev python scripts/dev/architecture_governance.py --check
```
