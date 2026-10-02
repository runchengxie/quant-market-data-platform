# Quality governance and maintenance debt

[中文页面](quality-governance.md)

This guide describes local governance commands for Ruff and `ty` coverage, maintainability metrics, compatibility lifecycles, and architecture boundaries. These checks expose existing debt, prevent it from growing, and support gradual tightening.

## Raw L2 quality profiling

The platform provides reusable, read-only structural checks for raw market data. Profile explicit Parquet files without changing them:

```bash
marketdata quality profile \
  --file /path/to/order_2024-12-13.parquet \
  --output /tmp/l2-quality.json
```

For a large tree, use the resumable CPU scanner. It writes one checkpoint update per file and leaves raw Parquet unchanged:

```bash
marketdata quality scan \
  --root /path/to/raw-l2 \
  --checkpoint /tmp/l2-quality.checkpoint.json \
  --output /tmp/l2-quality-summary.json
```

Before materializing a larger derived dataset, run a low-copy pilot on explicit files:

```bash
marketdata quality pilot \
  --file /path/to/deal_2024-12-13.parquet \
  --file /path/to/order_2024-12-13.parquet \
  --file /path/to/snapshot_2024-12-13.parquet \
  --output-dir /tmp/l2-pilot
```

The pilot does not delete or rewrite inputs. `canonical/` contains rows currently safe for a downstream sample; `labels/` contains only `tag` and `exclude` rows with a source row number. Install the optional dependency with `uv sync --extra quality`. Model-input, label, leakage, alpha, and portfolio checks remain in downstream research projects.

Duplicate identities in raw L2 files are scoped by the resolved security and ID columns when a security column is available. For example, `SecuCode=000001, OrderID=7` and `SecuCode=000002, OrderID=7` are distinct. The report records this in `id_scope_columns`.

When order and trade files are both available, check trade references separately:

```bash
marketdata quality integrity \
  --orders /path/to/order_2024-12-13.parquet \
  --trades /path/to/trades_2024-12-13.parquet \
  --output /tmp/l2-integrity.json
```

The reusable opening-auction accounting core is `market_data_platform.quality_opening`. It consumes explicit orders, trades, and cancels, reconstructs remaining bid/ask levels, and reports unknown identities and overdrawn volume. Raw-file discovery, event cutoffs, exchange-specific lag, and snapshot alignment remain downstream because those rules depend on the market and experiment.

## Standard quality gates

```bash
uv run --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run --extra dev python -m ruff check .
uv run --extra dev python -m ruff format --check .
uv run --extra dev ty check --error-on-warning
```

The blocking type check is `ty check --error-on-warning`. `[tool.ty.src].include` in `pyproject.toml` combines the former daily and release scopes and additionally protects `symbols.py`.

## Code review and debt visibility

Review for overall code health, compatibility of CLI names and data contracts, correct responsibility boundaries, and focused tests for provider adapters, manifests, CLI arguments, and cross-repository contracts. Avoid growing long functions, oversized files, functions with ten or more arguments, implicit facade exports, and unexplained compatibility layers. Use Google-style docstrings for public entry points and non-obvious helpers; do not reformat or rename the repository for style alone.

Debt visibility commands are non-blocking:

```bash
uv run --extra dev python scripts/dev/quality_debt.py
uv run --extra dev python scripts/dev/quality_debt.py --complexity
uv run --extra dev python scripts/dev/quality_debt.py --json --skip-ruff
uv run --extra dev python scripts/dev/maintainability_metrics.py
uv run --extra dev python scripts/dev/maintainability_metrics.py --markdown
uv run --extra dev python scripts/dev/compatibility_governance.py --check
uv run --extra dev python scripts/dev/architecture_governance.py --check
```

`--complexity` alone reports Ruff rules `C90`, `PLR0911`, `PLR0912`, `PLR0913`, and `PLR0915`. The full local gate also passes `--check-ratchet` to prevent complexity debt from exceeding the accepted baseline. `ty` directly blocks type diagnostics, so there is no separate type-debt inventory.

The public workflows run snapshot-boundary checks, shared quality ratchets, Ruff, pytest, and `pip-audit` on pull requests and `main`. Documentation changes also run strict MkDocs builds and publish Pages from `main`. Pull-request CI is a merge gate. Before a release, run coverage for the affected module.

## Lifecycle capture baseline update (2026-10-02)

The shared ratchet's file/line inventory increased from 456 files / 110,174
lines to 459 files / 110,790 lines for the new reference scanner, independent
input capture module and its behavior tests. Existing long-line, complexity,
function-size and file-size ceilings are unchanged. Acceptance requires the
complete test suite, the unchanged structural budgets and independent review
to pass. Future removal or consolidation of this functionality must lower the
file/line inventory accordingly; this adjustment grants no complexity waiver.

## QuantZone direct connection inventory (2026-10-03)

The explicit CLI proxy option and its restoration tests add 83 Python lines (114,313 to 114,396), with no new files and no changes to complexity, long-line or function/file-size budgets. Removing this option or consolidating its tests must lower the inventory accordingly.
