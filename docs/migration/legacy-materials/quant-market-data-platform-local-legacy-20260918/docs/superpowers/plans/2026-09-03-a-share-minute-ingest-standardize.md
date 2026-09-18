# A 股分钟 Ingest / Standardize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 A 股分钟下载、融合和物化从 `providers` 迁移到生命周期包，同时保持旧入口、数据 contract、receipt、checkpoint 和发布行为兼容。

**Architecture:** `ingest.tushare.minute` 拥有 TuShare 请求和 raw 分区；`standardize.fusion.a_share_minute` 只处理已落盘的 Guan/TuShare/BJ 输入；`standardize.materialize.a_share_minute` 编排分区构建和恢复。旧 `providers.tushare_a_share_mins`、`providers.a_share_minute_fusion`、`providers.a_share_minute_build` 保留为 facade。

**Tech Stack:** Python 3.11+, pandas, PyArrow, optional Polars, pytest, Ruff, ty, JSON receipts, Parquet partitions.

**Spec:** `docs/superpowers/specs/2026-09-03-a-share-minute-ingest-standardize-design.md`

## Global Constraints

- 不改变公开函数名、参数默认值、来源优先级、canonical schema、唯一键、排序或生产 alias。
- `standardize` 不得 import `providers` 或 `ingest` 实现。
- 不提交 Parquet、token、真实下载结果、运行报告或凭证。
- 每个迁移单元先写失败测试，再写实现，再运行局部和全量门禁。
- 旧入口的 monkeypatch seam 必须通过 wrapper 或显式注入适配继续工作。
- quality receipt 和 publish manifest 的 schema、路径和字段保持不变。

### Task 1: 建立分钟生命周期边界测试

**Files:**
- Modify: `tests/test_data_lifecycle_architecture.py`
- Modify: `tests/test_a_share_minute_fusion.py`
- Modify: `tests/test_a_share_minute_build.py`
- Modify: `tests/test_tushare_minute_backfill.py`
- Create: `tests/test_a_share_minute_lifecycle_compatibility.py`

**Interfaces:**
- New public import surfaces are `market_data_platform.ingest.tushare.minute`, `market_data_platform.standardize.fusion.a_share_minute`, and `market_data_platform.standardize.materialize.a_share_minute`.
- Existing public functions remain `mirror_minute_bars`, `MinsMirrorOptions`, `fuse_minute_frames`, `fuse_and_write_minute_partition`, `aggregate_guan_deal_file`, `build_fused_minute_dataset`, and `validate_fused_minute_dataset`.

- [ ] **Step 1: Write failing boundary and compatibility tests.** Assert that standardize imports contain no `market_data_platform.providers` or `market_data_platform.ingest`, and assert the old public symbols resolve to the new implementation surfaces.
- [ ] **Step 2: Run the focused tests and verify the expected import/module failures.**

```bash
uv run --locked --extra dev python -m pytest \
  tests/test_data_lifecycle_architecture.py \
  tests/test_a_share_minute_lifecycle_compatibility.py -q
```

- [ ] **Step 3: Keep the tests as the migration gate and commit them.**

```bash
git add tests/test_data_lifecycle_architecture.py tests/test_a_share_minute_lifecycle_compatibility.py \
  tests/test_a_share_minute_fusion.py tests/test_a_share_minute_build.py tests/test_tushare_minute_backfill.py
git commit -m "test: define minute lifecycle compatibility boundaries"
```

### Task 2: Migrate canonical fusion implementation

**Files:**
- Create: `src/market_data_platform/standardize/fusion/__init__.py`
- Create: `src/market_data_platform/standardize/fusion/a_share_minute/__init__.py`
- Create: `src/market_data_platform/standardize/fusion/a_share_minute/schema.py`
- Create: `src/market_data_platform/standardize/fusion/a_share_minute/normalize.py`
- Create: `src/market_data_platform/standardize/fusion/a_share_minute/guan_deals.py`
- Create: `src/market_data_platform/standardize/fusion/a_share_minute/fusion.py`
- Create: `src/market_data_platform/standardize/fusion/a_share_minute/writer.py`
- Modify: `src/market_data_platform/providers/a_share_minute_fusion.py`
- Modify: `src/market_data_platform/providers/a_share_minute_fusion_part01.py`
- Modify: `src/market_data_platform/providers/a_share_minute_fusion_part02.py`
- Modify: `src/market_data_platform/providers/a_share_minute_fusion_part03.py`
- Test: `tests/test_a_share_minute_fusion.py`
- Test: `tests/test_a_share_minute_bj_overlay.py`

**Interfaces:**
- `standardize.fusion.a_share_minute` exports the same public fusion constants, dataclasses, `normalize_legacy_guan_partition`, `normalize_tushare_partition`, `fuse_minute_frames`, `aggregate_guan_deal_file`, `write_canonical_minute_partition`, and `fuse_and_write_minute_partition` currently exported by the provider facade.
- Fusion functions consume `pd.DataFrame | str | Path` or Arrow inputs and return the existing `MinuteFusionResult`, `GuanDealAggregationResult`, or canonical DataFrame types.

- [ ] **Step 1: Move implementation by responsibility.** Place schema/dataclasses in `schema.py`, frame/unit normalization and deduplication in `normalize.py`, Guan deal aggregation in `guan_deals.py`, source precedence and frame fusion in `fusion.py`, and Parquet conversion/writing in `writer.py`.
- [ ] **Step 2: Replace provider part modules with thin compatibility re-exports.** Preserve the provider facade exports and any test-visible module attributes without importing back from standardize.
- [ ] **Step 3: Run fusion, BJ overlay, and compatibility tests.** Verify canonical frame equality and byte/hash equality for existing fixtures.

```bash
uv run --locked --extra dev python -m pytest \
  tests/test_a_share_minute_fusion.py \
  tests/test_a_share_minute_bj_overlay.py \
  tests/test_a_share_minute_lifecycle_compatibility.py -q
```

- [ ] **Step 4: Commit the fusion migration.**

```bash
git add src/market_data_platform/standardize/fusion \
  src/market_data_platform/providers/a_share_minute_fusion*.py \
  tests/test_a_share_minute_fusion.py tests/test_a_share_minute_bj_overlay.py
git commit -m "refactor: move minute fusion into standardize"
```

### Task 3: Migrate minute materialization and validation adapter

**Files:**
- Create: `src/market_data_platform/standardize/materialize/__init__.py`
- Create: `src/market_data_platform/standardize/materialize/a_share_minute/__init__.py`
- Create: `src/market_data_platform/standardize/materialize/a_share_minute/options.py`
- Create: `src/market_data_platform/standardize/materialize/a_share_minute/checkpoint.py`
- Create: `src/market_data_platform/standardize/materialize/a_share_minute/build.py`
- Create: `src/market_data_platform/standardize/materialize/a_share_minute/validation_adapter.py`
- Modify: `src/market_data_platform/providers/a_share_minute_build.py`
- Modify: `src/market_data_platform/providers/a_share_minute_build_part01.py`
- Modify: `src/market_data_platform/providers/a_share_minute_build_part02.py`
- Modify: `src/market_data_platform/providers/a_share_minute_build_part03.py`
- Modify: `src/market_data_platform/cli_data_part02.py`
- Test: `tests/test_a_share_minute_build.py`
- Test: `tests/test_a_share_minute_coverage.py`
- Test: `tests/test_cli_data_minutes.py`

**Interfaces:**
- `standardize.materialize.a_share_minute` exports `MinuteFusionBuildOptions`, `build_fused_minute_dataset`, and `validate_fused_minute_dataset` with their current signatures and payloads.
- `build_fused_minute_dataset` continues to use `minute_dataset_lock`, staging directories, checkpoint signatures, dry-run behavior, and existing manifest assembly fields.

- [ ] **Step 1: Add compatibility assertions for options, manifest, checkpoint, and monkeypatch behavior.** The tests must cover interrupted deal aggregation, resume, stale checkpoint rejection, dry-run, lock behavior, and validation payload equality.
- [ ] **Step 2: Run the focused build tests and confirm the new materialize imports fail before implementation.**

```bash
uv run --locked --extra dev python -m pytest \
  tests/test_a_share_minute_build.py \
  tests/test_a_share_minute_coverage.py \
  tests/test_cli_data_minutes.py -q
```

- [ ] **Step 3: Move build options and source inventory to `options.py`, checkpoint/fingerprint helpers to `checkpoint.py`, build orchestration to `build.py`, and quality calls to `validation_adapter.py`.** Materialize may call standardize fusion and quality public seams, but it must not persist publish aliases.
- [ ] **Step 4: Replace old build parts with compatibility re-exports/wrappers and update CLI imports to the new public materialize surface.**
- [ ] **Step 5: Re-run the focused build tests and compare manifest/checkpoint payloads against the old fixture expectations.**
- [ ] **Step 6: Commit the materialize migration.**

```bash
git add src/market_data_platform/standardize/materialize \
  src/market_data_platform/providers/a_share_minute_build*.py \
  src/market_data_platform/cli_data_part02.py \
  tests/test_a_share_minute_build.py tests/test_a_share_minute_coverage.py tests/test_cli_data_minutes.py
git commit -m "refactor: move minute materialization into standardize"
```

### Task 4: Extract TuShare minute acquisition into ingest

**Files:**
- Create: `src/market_data_platform/ingest/tushare/minute.py`
- Modify: `src/market_data_platform/ingest/tushare/__init__.py`
- Modify: `src/market_data_platform/providers/tushare_a_share_mins.py`
- Modify: `src/market_data_platform/providers/_mins_mirror.py`
- Modify: `src/market_data_platform/providers/_mins_fetch.py`
- Modify: `src/market_data_platform/providers/_a_share_mins_partition.py`
- Modify: `src/market_data_platform/providers/_a_share_mins_universe.py`
- Modify: `src/market_data_platform/cli_data_part02.py`
- Test: `tests/test_tushare_minute_backfill.py`
- Test: `tests/test_tushare_minute_chunk.py`
- Test: `tests/test_tushare_minute_operational.py`
- Test: `tests/test_tushare_minute_quota.py`
- Test: `tests/test_minute_provider_exceptions_runtime.py`

**Interfaces:**
- `ingest.tushare.minute` exports `MinsMirrorOptions`, `MinuteMirrorIncompleteDatesError`, `mirror_minute_bars`, `validate_mins_batch_size`, and the raw partition/sidecar receipt helpers needed by current callers.
- `providers.tushare_a_share_mins` remains the legacy import surface and delegates to ingest without changing `MinsMirrorOptions` construction or result payloads.

- [ ] **Step 1: Add tests asserting the new ingest entry point handles explicit symbols, quota policy, partial dates, no-data exceptions, and resumable sidecars with the existing fixtures.**
- [ ] **Step 2: Run the acquisition-focused tests and verify the new entry point is absent or incomplete before migration.**

```bash
uv run --locked --extra dev python -m pytest \
  tests/test_tushare_minute_backfill.py tests/test_tushare_minute_chunk.py \
  tests/test_tushare_minute_operational.py tests/test_tushare_minute_quota.py \
  tests/test_minute_provider_exceptions_runtime.py -q
```

- [ ] **Step 3: Move only provider-facing acquisition code and raw partition helpers into ingest.** Keep canonical normalization and source fusion in standardize; keep quality decisions in quality.
- [ ] **Step 4: Make the provider module a facade and update CLI/operational callers to use ingest.** Preserve test monkeypatch seams through a deliberate adapter rather than importing provider modules from standardize.
- [ ] **Step 5: Re-run acquisition tests and verify raw file, sidecar, receipt, quota, and partial-date payload equality.**
- [ ] **Step 6: Commit the ingest migration.**

```bash
git add src/market_data_platform/ingest/tushare \
  src/market_data_platform/providers/tushare_a_share_mins.py \
  src/market_data_platform/providers/_mins_*.py \
  src/market_data_platform/providers/_a_share_mins_*.py \
  src/market_data_platform/cli_data_part02.py \
  tests/test_tushare_minute_*.py tests/test_minute_provider_exceptions_runtime.py
git commit -m "refactor: move minute acquisition into ingest"
```

### Task 5: Enforce boundaries and complete verification

**Files:**
- Modify: `tests/test_data_lifecycle_architecture.py`
- Modify: `docs/data-lifecycle-architecture.md`
- Modify: `docs/maintenance-audit.md` only if measured baseline values change

- [ ] **Step 1: Update lifecycle documentation with the actual new public import paths and facade policy.**
- [ ] **Step 2: Run focused compatibility, fusion, build, acquisition, coverage, BJ overlay, and operational tests.**
- [ ] **Step 3: Run the complete local gate.**

```bash
uv run --locked --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run --locked --extra dev python -m ruff check .
uv run --locked --extra dev python -m ruff format --check .
uv run --locked --extra dev ty check --error-on-warning
uv run --locked --extra dev python scripts/dev/quality_debt.py --skip-ruff --complexity --check-baseline --check-ratchet
uv run --locked --extra dev python scripts/dev/maintainability_metrics.py --check-baseline
uv run --locked --extra dev python scripts/dev/compatibility_governance.py --check
uv run --locked --extra dev python scripts/dev/architecture_governance.py --check
```

- [ ] **Step 4: Inspect `git diff --check`, verify no data artifacts or credentials are tracked, and review the final dependency graph.**
- [ ] **Step 5: Commit documentation and boundary changes, then request review before merging.**

```bash
git add tests/test_data_lifecycle_architecture.py docs/data-lifecycle-architecture.md docs/maintenance-audit.md
git commit -m "docs: record minute lifecycle migration"
```
