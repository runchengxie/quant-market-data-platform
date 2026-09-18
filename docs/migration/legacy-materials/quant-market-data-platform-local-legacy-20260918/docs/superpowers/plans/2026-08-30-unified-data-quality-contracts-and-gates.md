# 统一数据质量契约与门禁实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：**统一数据集语义和数据质量决策，增加识别交易所序列的 L2 门禁，并将不可变基本面观测频率提高到每日。

**架构：**新增小型纯 Python 契约、回执、排序和门禁模块，并接入现有 PyArrow 分析器和质量 CLI。复用现有可恢复扫描、试点标签、不可变基本面归档和发布边界，不另建平行管道。

**Tech Stack:** Python 3.11+, dataclasses, JSON, PyArrow, pytest, systemd.

**Spec:** `docs/superpowers/specs/2026-08-30-unified-data-quality-contracts-and-gates-design.md`

## Global Constraints

- Raw inputs remain immutable.
- Existing provider manifests and current-asset contracts remain authoritative for publication.
- L2 gate findings do not silently rewrite raw data or move aliases.
- Cross-channel exchange total order is never claimed.
- Historical fundamentals before first observed vintage remain reconstructed PIT.

---

### 任务一：新增数据集契约和数据质量回执封装

**Files:**
- Create: `src/market_data_platform/dataset_contracts.py`
- Create: `src/market_data_platform/dq_receipts.py`
- Test: `tests/test_dataset_contracts.py`
- Test: `tests/test_dq_receipts.py`

- [x] Write tests for stable schema, semantic fields, sequence-claim validation, quality-rule overrides, duration, and eligibility ordering.
- [x] Run tests and verify they fail because modules do not exist / rules are not yet supported.
- [x] Implement the minimal pure-Python modules and atomic JSON writers.
- [x] Run focused tests and verify they pass.

### 任务二：新增 L2 顺序来源记录和序列指标

**Files:**
- Create: `src/market_data_platform/l2_ordering.py`
- Modify: `src/market_data_platform/quality.py`
- Modify: `src/market_data_platform/quality_scan.py`
- Test: `tests/test_l2_ordering.py`
- Modify: `tests/test_raw_l2_mapping.py`

- [x] Write tests for alias detection, gap/backwards/duplicate accounting, timestamp fallback, and raw snapshot aliases.
- [x] Run pure ordering tests and verify the missing module fails first.
- [x] Implement bounded ordering state.
- [x] Wire ordering state into `profile_parquet`, including `TickTime` and snapshot file handling.
- [x] Include sequence findings in resumable scan categories and summary; bump checkpoint schema to prevent stale reuse.
- [ ] Run PyArrow-backed focused tests in an environment with the quality extra. Current execution environment does not provide PyArrow.

### 任务三：新增 L2 准入门禁和 CLI

**Files:**
- Create: `src/market_data_platform/l2_quality_gate.py`
- Modify: `src/market_data_platform/cli_quality.py`
- Test: `tests/test_l2_quality_gate.py`
- Modify: `tests/test_cli_quality.py`
- Create: `docs/l2-ingestion-gate.md`

- [x] Write tests for production, research-only, quarantine, and contract-specific override outcomes.
- [x] Run tests and verify the missing module / policy behavior fails first.
- [x] Implement deterministic check-to-severity mapping, contract overrides, and DQ receipt output.
- [x] Add `marketdata quality gate --root ... --checkpoint ... --output ...` with optional pilot manifest and dataset contract.
- [x] Document the daily/new-partition gate flow.
- [ ] Run PyArrow-backed CLI gate tests. Current execution environment does not provide PyArrow.

### 任务四：提高基本面观测版本频率

**Files:**
- Modify: `scripts/operations/archive_tushare_fundamentals_vintage.py`
- Modify: `scripts/systemd/tushare-fundamentals-vintage-archive.service`
- Modify: `scripts/systemd/tushare-fundamentals-vintage-archive.timer`
- Modify: `tests/test_archive_tushare_fundamentals_vintage.py`
- Create: `tests/test_fundamentals_systemd.py`
- Modify: `docs/a-share-fundamentals.md`
- Modify: `scripts/systemd/README.md`

- [x] Change archive metadata from hard-coded weekly to explicit `--observation-frequency` with daily operational default.
- [x] Change the packaged timer from Saturday-only to daily at 02:30 Asia/Shanghai while preserving unit names.
- [x] Keep the full immutable snapshot, validation, hashing, sealing, and no-publish behavior unchanged.
- [x] Update tests and docs to state daily observation does not imply intraday revision safety.

### 任务五：验证平台改动

- [x] Run pure-Python focused tests: 12 passed.
- [ ] Run PyArrow quality, CLI, and fundamentals archive tests. Tests are present, but PyArrow is unavailable in the current execution environment.
- [ ] Run Ruff/format/ty. Those executables are unavailable in the current execution environment.
- [x] Run `py_compile` on the pure modules and local mirrors of the modified profile/scan/archive scripts.
- [x] Review the Git diff for raw mutation, alias movement, or accidental publication behavior; none is introduced by this branch.
