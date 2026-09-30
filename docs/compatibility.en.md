# Compatibility layer and cleanup plan

[中文页面](compatibility.md)

This page tracks compatibility entry points, migration tools, and historical runtime conventions. Each retained item needs a stated purpose, risk, and removal condition.

## Public CLI lifecycle

Every leaf command reachable from the `marketdata` parser is part of the public CLI. Active documentation must show its full `marketdata ...` path.

| Lifecycle | Meaning | Documentation requirements |
| --- | --- | --- |
| `active` | Normal, currently maintained platform workflow | Command path, inputs, outputs, and validation command |
| `compatibility` | Legacy command, package, or console-script entry point | Recommended replacement, risk, tests, and removal condition |
| `migration-only` | Migration, recovery, or historical handoff entry point | Applicable scenario, replacement, and exit condition |
| `deprecated` | Superseded entry point retained during transition | Replacement, audit evidence, and removal condition |
| `archival` | Historical reproduction or release archive entry point | Reproduction value, verification evidence, and archival condition |
| `internal-only` | Maintainer repair, one-off import, or audit script | Keep under `scripts/internal/`; do not expose through the public parser |

New data production, health checks, current-data refreshes, and publication workflows belong in native provider workflows. One-off repair tools normally belong in `scripts/internal/`. If a tool is added to the public CLI, record its lifecycle here or in the relevant operations guide.

## Current compatibility status

The `hkdata` console script, `hk_data_platform.*` package aliases, `rqdata-hk-depth` / `rqdata-tick`, and `rqdata-hk-assets` were removed from active packages on 2026-06-13. Historical reproduction uses the `hk-freeze-20260613` tag or the private archive repository. New scripts should use `marketdata` and `market_data_platform.*`.

Downstream research data-directory commands and local-snapshot wrappers were also removed from active entry points. Standard-layer catalog, materialization, query, and data snapshots use `marketdata data ...` and `marketdata backup-data`; strategy research uses `strategy ...`.

| Compatibility item | Use / risk | Replacement and cleanup | Owner / status |
| --- | --- | --- | --- |
| `marketdata migration freeze-hk` / `marketdata migration hydrate-hk` | Retired Hong Kong cold-storage commands; code and commands have been removed, so old references describe unavailable features. | Use the `hk-freeze-20260613` tag or private archive for historical reproduction. No further removal is needed after the RQData retirement on 2026-07-26. | Removed; migration-only; see `docs/operations/hk-archive-restore.md`. |

## Maintenance rules

1. New code uses `marketdata`, `market_data_platform`, and `market_data_platform.providers.*`.
2. Add each compatibility layer to the inventory with its purpose and removal condition.
3. Migration commands must not carry new business capabilities; add those to native platform workflows.
4. Before removing a compatibility item, audit repository-local references with `rg` and verify downstream callers have migrated.
5. Downstream wrappers may preserve compatibility but must not implement new downloads, health checks, current-data refreshes, registry behavior, or asset publication.
6. Hong Kong research and RQData recovery were retired on 2026-07-26; freeze/hydrate commands are removed.

## One-off scripts and internal legacy

A repository audit on 2026-07-02 covered source, tests, scripts, and adjacent-submodule references. These unused helpers were retired from the active package: `config_utils.py`, `current_assets.py`, `deprecations.py`, `intraday_paths.py`, `pit_feature_stats.py`, `rebalance.py`, and `rqdata_cli_common.py`.

The archived script `scripts/internal/archive/build_a_share_tushare_sw2021_industry_changes_20260603.py` is retained for reference. Its replacement is `marketdata tushare download-a-share-industry-membership`.

## Static-check debt

Ruff covers all source files. `ty` covers the type boundaries listed in `pyproject.toml`, combining the former daily and release scopes. Blocking checks are Ruff, `ty`, and pytest:

```bash
uv run --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run python -m ruff check .
uv run python -m ruff format --check .
uv run ty check --error-on-warning
```

Debt-visibility checks are non-blocking but recommended before and after refactoring:

```bash
uv run --extra dev python scripts/dev/quality_debt.py
uv run --extra dev python scripts/dev/quality_debt.py --complexity
uv run --extra dev python scripts/dev/maintainability_metrics.py
uv run --extra dev python scripts/dev/compatibility_governance.py --check
uv run --extra dev python scripts/dev/architecture_governance.py --check
```

Priorities are to restore Ruff coverage in low-risk files, avoid expanding `extend-exclude`, prefer documented per-file ignores when needed, and keep `ty` coverage on contracts, paths, manifests, registries, and current-data contracts.
