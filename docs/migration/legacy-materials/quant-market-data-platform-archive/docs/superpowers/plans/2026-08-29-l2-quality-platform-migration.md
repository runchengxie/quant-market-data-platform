# L2 质量平台迁移实施计划

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended) to implement this plan task-by-task.

目标：让 `market-data-platform` 成为原始 L2 结构质量检查的可复用维护方，同时保留 `deep-learning` 兼容性。

架构：新增按需加载的 `market_data_platform.quality` 模块，读取明确指定的 Parquet 文件但不修改文件。通过 `marketdata quality profile` 暴露能力，记录维护边界，并在跨仓库比较完成前保留现有深度学习分析器。

技术栈：Python 3.11、PyArrow、NumPy、argparse、pytest、Ruff，以及现有的 `marketdata` CLI。

Spec: `docs/superpowers/specs/2026-08-29-l2-quality-boundary-design.md`

## 全局约束

- Raw input files remain immutable.
- Quality profiling is read-only and accepts explicit file paths.
- The command must not import PyArrow while building unrelated `marketdata` commands.
- Model, label, leakage, alpha, and portfolio logic stay outside this first slice.
- Do not scan the entire multi-terabyte lake as part of tests.

---

### 任务一：新增平台质量分析器

Files:
- Create: `src/market_data_platform/quality.py`
- Create: `tests/test_quality.py`
- Modify: `pyproject.toml`

Interfaces:
- Produces `profile_parquet(path, batch_size=262144, max_tracked_ids=1000000) -> dict[str, object]`.
- The report includes `rows`, `columns`, `id_column`, `id_scope_columns`, `nulls`, `minimum`, `maximum`, `nonpositive`, `trading_days`, `trading_day_mismatch_rows`, `duplicate_id_rows`, `id_tracking_truncated`, and `timestamp_backwards`. Duplicate IDs are scoped by security when that column is available.

- [x] Write tests for nulls, ranges, date mismatches, cross-batch ID repetition, timestamp reversals, and bounded ID tracking.
- [x] Run the red test phase before implementing the module.
- [x] Implement the platform profiler using batch-level Arrow/NumPy operations and bounded state for IDs and per-symbol timestamps.
- [x] Add a `quality` optional dependency extra containing `pyarrow>=25.0.0`.
- [x] Run the focused tests and confirm they pass.

### 任务二：接入公共 CLI

Files:
- Create: `src/market_data_platform/cli_quality.py`
- Modify: `src/market_data_platform/cli.py`
- Create: `tests/test_cli_quality.py`
- Modify: `tests/test_cli_dependency_boundaries.py`
- Modify: `docs/compatibility.md`

Interfaces:
- Adds `marketdata quality profile --file PATH [--file PATH ...] [--output PATH]`.
- The handler lazily imports PyArrow-dependent code so `marketdata --help` remains dependency-light.

- [x] Write CLI parser and missing-dependency tests.
- [x] Run the red test phase before wiring.
- [x] Add `add_quality_parser` and dispatch the profile command.
- [x] Document the command as an active platform workflow, not a migration-only command.
- [x] Run parser/help and focused CLI tests.

### 任务三：补充兼容性文档并完成比较

Files:
- Modify: `/path/to/code/private-tick-data-project/docs/project-status.md`
- Modify: `/path/to/code/private-tick-data-project/docs/research/experiment-log.md`
- Create: `/tmp/deep-learning-platform-quality-comparison.json`

- [x] Document that structural L2 quality is platform-owned while model-specific checks remain in deep-learning.
- [x] Run both profilers on the same representative 2021 and 2025 pre-open files.
- [x] Compare exact fields and record any intentional schema differences.
- [x] Keep the deep-learning implementation as a compatibility path until the comparison is clean.

### 任务四：验证本次迁移范围

- [x] Run focused platform tests, CLI tests, and the real-file sample command.
- [x] Run the existing deep-learning quality and relevant research tests.
- [x] Run Ruff, formatting, and targeted `ty` checks for changed platform files.
- [x] Confirm `git diff --check` and that no raw files changed.

### 第二阶段：开盘账本核算核心

- [x] Add platform dataclasses, accounting, level reconstruction, and order-level tracing.
- [x] Add regression coverage for known-side consumption when the other trade ID is unknown.
- [x] Route the deep-learning public opening-ledger function through the compatibility bridge.
- [x] Document that raw loading and market-specific timing remain downstream.
- [x] Verify both fallback and platform-installed compatibility paths.
