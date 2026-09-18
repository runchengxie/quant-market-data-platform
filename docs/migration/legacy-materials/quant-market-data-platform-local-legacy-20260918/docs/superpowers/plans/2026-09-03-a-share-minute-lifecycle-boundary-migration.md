# A-share Minute Lifecycle Boundary Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move A-share minute fusion and materialization implementation out of `providers` into lifecycle-owned modules while preserving public imports, data contracts, quality decisions, publication format, and runtime behavior.

**Architecture:** `standardize.fusion.a_share_minute` owns canonical minute transformation and Guan-deal aggregation; `standardize.materialize.a_share_minute` owns source discovery, checkpoints, workers, locking, and build orchestration. Existing `providers.a_share_minute_fusion` and `providers.a_share_minute_build` become compatibility facades. Because this repository already has a top-level `market_data_platform/quality.py` module, this migration places minute-dataset acceptance in `market_data_platform.quality_a_share_minute` rather than creating a conflicting `quality/` package; reorganizing all quality code into a package is a separate migration.

**Tech Stack:** Python 3.11+, pandas, PyArrow, optional Polars, dataclasses, JSON, pytest, Ruff, existing `marketdata` CLI and dataset locking.

**Spec:** `docs/superpowers/specs/2026-09-03-a-share-minute-lifecycle-boundary-design.md`

## Global Constraints

- Keep all importable platform code under `src/market_data_platform/`.
- Do not split this project into additional repositories.
- Preserve canonical columns exactly: `ts_code`, `trade_time`, `open`, `close`, `high`, `low`, `vol`, `amount`.
- Preserve canonical Arrow types, metadata behavior, `(ts_code, trade_time)` key semantics, stable ordering, and TuShare-over-Guan overlap priority.
- Preserve legacy Guan unit profiles and date rules, Guan deal pandas/polars behavior, partition paths, Zstandard Parquet writing, build defaults, checkpoint schema/fingerprint semantics, resume behavior, manifest schema, lock behavior, dry-run behavior, CLI behavior, and operations-script behavior.
- `standardize` must not import `market_data_platform.providers` or `market_data_platform.ingest`.
- Public legacy provider entry points remain valid and should resolve to the same function/class objects as the new public entry points wherever practical.
- Existing private `providers.a_share_minute_*_partNN` files may survive only as temporary compatibility facades after repository-owned callers migrate.
- Quality acceptance policy must live outside materialization. For this migration use `market_data_platform.quality_a_share_minute` because `market_data_platform.quality` is currently a module, not a package.
- Durable fused-manifest JSON persistence belongs to `publish`; resumable deal checkpoint JSON remains materialization execution state.
- Do not change source authority, source priority, units, schemas, aliases, receipt semantics, or research feature behavior as part of this structural migration.

---

## File map

### New fusion ownership

- `src/market_data_platform/standardize/fusion/__init__.py`: standardize fusion namespace.
- `src/market_data_platform/standardize/fusion/a_share_minute/__init__.py`: public minute-fusion surface.
- `src/market_data_platform/standardize/fusion/a_share_minute/schema.py`: canonical schema/constants and public result/stat dataclasses.
- `src/market_data_platform/standardize/fusion/a_share_minute/normalize.py`: canonical projection/coercion, legacy Guan normalization, TuShare normalization, source deduplication.
- `src/market_data_platform/standardize/fusion/a_share_minute/fusion.py`: source fusion, canonical Arrow table conversion, atomic canonical Parquet writing.
- `src/market_data_platform/standardize/fusion/a_share_minute/guan_deals.py`: symbol resolution, deal-time/session bucketing, pandas/polars deal aggregation, engine selection.

### New materialization ownership

- `src/market_data_platform/standardize/materialize/__init__.py`: materialization namespace.
- `src/market_data_platform/standardize/materialize/a_share_minute/__init__.py`: public fused-minute build surface.
- `src/market_data_platform/standardize/materialize/a_share_minute/options.py`: `MinuteFusionBuildOptions` and option/date validation.
- `src/market_data_platform/standardize/materialize/a_share_minute/inventory.py`: source inventory, discovery, path helpers, instrument mapping inventory facts.
- `src/market_data_platform/standardize/materialize/a_share_minute/checkpoint.py`: deal checkpoint schema, contract fingerprinting, resume load/persist/finalize execution state.
- `src/market_data_platform/standardize/materialize/a_share_minute/workers.py`: legacy normalization, Guan-deal merge, TuShare merge, partition-level materialization helpers.
- `src/market_data_platform/standardize/materialize/a_share_minute/build.py`: build orchestration, lock acquisition, manifest fact assembly, quality invocation, publish invocation.

### Quality and publish seams

- `src/market_data_platform/quality_a_share_minute.py`: authoritative fused-minute dataset validation and partition acceptance helpers.
- `src/market_data_platform/publish/json_manifest.py`: atomic JSON manifest writer preserving the current JSON representation.

### Compatibility and callers

- Modify `src/market_data_platform/providers/a_share_minute_fusion.py` to direct-re-export the new public fusion surface.
- Modify `src/market_data_platform/providers/a_share_minute_build.py` to direct-re-export the new public materialization and quality surfaces.
- Thin `providers/a_share_minute_fusion_part01.py`, `part02.py`, `part03.py`, `a_share_minute_build_part01.py`, `part02.py`, `part03.py` to compatibility facades only after all repository-owned callers move.
- Update coverage materialization, BJ overlay, audit scripts, CLI helpers, and tests that currently import minute implementation from `providers` private modules.

---

### Task 1: Lock lifecycle imports and public facade identities with failing tests

**Files:**
- Modify: `tests/test_data_lifecycle_architecture.py`
- Create: `src/market_data_platform/standardize/fusion/__init__.py`
- Create: `src/market_data_platform/standardize/fusion/a_share_minute/__init__.py`
- Create: `src/market_data_platform/standardize/materialize/__init__.py`
- Create: `src/market_data_platform/standardize/materialize/a_share_minute/__init__.py`

**Interfaces:**
- Consumes: existing `providers.a_share_minute_fusion` and `providers.a_share_minute_build` public objects.
- Produces: importable new package paths that later tasks populate; lifecycle tests that require old/new public identity.

- [ ] **Step 1: Extend the architecture test before implementation**

Add imports and tests equivalent to:

```python
from market_data_platform.providers.a_share_minute_build import (
    MinuteFusionBuildOptions as legacy_minute_build_options,
    build_fused_minute_dataset as legacy_build_fused_minute_dataset,
    validate_fused_minute_dataset as legacy_validate_fused_minute_dataset,
)
from market_data_platform.providers.a_share_minute_fusion import (
    aggregate_guan_deal_file as legacy_aggregate_guan_deal_file,
    fuse_minute_frames as legacy_fuse_minute_frames,
)
from market_data_platform.standardize.fusion.a_share_minute import (
    aggregate_guan_deal_file,
    fuse_minute_frames,
)
from market_data_platform.standardize.materialize.a_share_minute import (
    MinuteFusionBuildOptions,
    build_fused_minute_dataset,
    validate_fused_minute_dataset,
)


def test_legacy_minute_fusion_is_standardize_facade() -> None:
    assert legacy_fuse_minute_frames is fuse_minute_frames
    assert legacy_aggregate_guan_deal_file is aggregate_guan_deal_file


def test_legacy_minute_build_is_materialize_facade() -> None:
    assert legacy_minute_build_options is MinuteFusionBuildOptions
    assert legacy_build_fused_minute_dataset is build_fused_minute_dataset
    assert legacy_validate_fused_minute_dataset is validate_fused_minute_dataset
```

Keep the existing AST boundary test unchanged so every new standardize file is scanned automatically.

- [ ] **Step 2: Run the architecture test and verify the new imports fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_data_lifecycle_architecture.py -q
```

Expected: collection/import failure because the new minute packages do not yet expose the requested symbols.

- [ ] **Step 3: Add only package skeletons**

Create namespace docstrings and empty `__all__` lists. Do not re-export provider implementations from the new packages because that would violate the standardize dependency rule.

- [ ] **Step 4: Re-run and confirm the failure is now missing public symbols, not a reverse import**

Run the same command. Expected: failure naming missing minute symbols; the AST boundary assertion itself must remain clean.

- [ ] **Step 5: Commit the red test and package skeletons**

```bash
git add tests/test_data_lifecycle_architecture.py src/market_data_platform/standardize/fusion src/market_data_platform/standardize/materialize
git commit -m "test: define minute lifecycle migration boundaries"
```

---

### Task 2: Move canonical schema, normalization, fusion, and writer into standardize

**Files:**
- Create: `src/market_data_platform/standardize/fusion/a_share_minute/schema.py`
- Create: `src/market_data_platform/standardize/fusion/a_share_minute/normalize.py`
- Create: `src/market_data_platform/standardize/fusion/a_share_minute/fusion.py`
- Modify: `src/market_data_platform/standardize/fusion/a_share_minute/__init__.py`
- Modify: `tests/test_a_share_minute_fusion.py`
- Modify: `tests/test_data_lifecycle_architecture.py`

**Interfaces:**
- Produces: `CANONICAL_MINUTE_COLUMNS`, `CANONICAL_MINUTE_SCHEMA`, `MINUTE_KEY_COLUMNS`, `LegacyGuanUnitProfile`, `LEGACY_GUAN_CANONICAL_UNITS`, `LEGACY_GUAN_HUNDRED_X_UNITS`, `LegacyGuanNormalizationResult`, `MinuteFusionResult`, `MinuteFusionStats`, `normalize_legacy_guan_partition`, `normalize_legacy_guan_partition_with_stats`, `normalize_tushare_partition`, `fuse_minute_frames`, `fuse_and_write_minute_partition`, `write_canonical_minute_partition`.
- Consumes: pandas, PyArrow, filesystem only; no provider/ingest imports.

- [ ] **Step 1: Point behavioral tests at the new implementation path while retaining separate facade identity tests**

Change the main imports in `tests/test_a_share_minute_fusion.py` to:

```python
from market_data_platform.standardize.fusion.a_share_minute import (
    CANONICAL_MINUTE_COLUMNS,
    CANONICAL_MINUTE_SCHEMA,
    LEGACY_GUAN_CANONICAL_UNITS,
    LEGACY_GUAN_HUNDRED_X_UNITS,
    MinuteAggregationEngine,
    aggregate_guan_deal_file,
    fuse_and_write_minute_partition,
    normalize_legacy_guan_partition,
    normalize_legacy_guan_partition_with_stats,
    normalize_tushare_partition,
)
```

For the atomic writer monkeypatch, import the owning module:

```python
from market_data_platform.standardize.fusion.a_share_minute import fusion
```

and patch `fusion.pq.write_table`, not the provider facade.

- [ ] **Step 2: Run the normalization/fusion subset and verify it fails**

```bash
uv run --extra dev python -m pytest \
  tests/test_a_share_minute_fusion.py::test_legacy_guan_normalization_requires_audited_unit_profile \
  tests/test_a_share_minute_fusion.py::test_tushare_projection_priority_unique_key_and_atomic_schema \
  tests/test_a_share_minute_fusion.py::test_atomic_write_preserves_existing_partition_on_failure -q
```

Expected: import failures for the unimplemented new surface.

- [ ] **Step 3: Move schema/result types exactly, without semantic edits**

Move canonical constants and dataclasses from `providers/a_share_minute_fusion_part01.py` into `schema.py`. Keep exact field names and defaults. Define `MinuteAggregationEngine = Literal["auto", "pandas", "polars"]` in `schema.py` so both fusion and deal aggregation share one type.

- [ ] **Step 4: Move source projection and normalization exactly**

Move `_empty_canonical_frame`, `_project_source`, `_canonicalize_frame`, `normalize_legacy_guan_partition`, `normalize_legacy_guan_partition_with_stats`, `normalize_tushare_partition`, and `_deduplicate_source` into `normalize.py`, importing schema objects from `.schema`.

- [ ] **Step 5: Move deterministic fusion and atomic Parquet writing exactly**

Move `fuse_minute_frames`, `_canonical_arrow_table`, `write_canonical_minute_partition`, and `fuse_and_write_minute_partition` into `fusion.py`. Preserve `compression="zstd"`, temporary-file placement in the destination directory, `os.replace`, and cleanup in `finally`.

- [ ] **Step 6: Export the new public surface**

Populate `a_share_minute/__init__.py` with direct imports from `.schema`, `.normalize`, and `.fusion`. Do not import anything from `providers`.

- [ ] **Step 7: Run the focused tests**

Run the Step 2 command. Expected: PASS for the normalization, fusion-priority, schema, ordering, and atomic-write tests.

- [ ] **Step 8: Run lifecycle import boundary test**

```bash
uv run --extra dev python -m pytest tests/test_data_lifecycle_architecture.py::test_standardize_does_not_depend_on_provider_or_ingest_implementations -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/market_data_platform/standardize/fusion/a_share_minute tests/test_a_share_minute_fusion.py tests/test_data_lifecycle_architecture.py
git commit -m "refactor: move minute fusion core into standardize"
```

---

### Task 3: Move Guan deal aggregation and make the provider fusion facade direct

**Files:**
- Create: `src/market_data_platform/standardize/fusion/a_share_minute/guan_deals.py`
- Modify: `src/market_data_platform/standardize/fusion/a_share_minute/__init__.py`
- Modify: `src/market_data_platform/providers/a_share_minute_fusion.py`
- Modify: `tests/test_a_share_minute_fusion.py`
- Modify: `tests/test_data_lifecycle_architecture.py`

**Interfaces:**
- Consumes: `.schema` and `.normalize` canonical helpers.
- Produces: `GuanDealAggregationResult`, `GuanDealAggregationStats`, `aggregate_guan_deal_file`; private engine helper `_try_import_polars` remains owned by `guan_deals.py` for tests of engine fallback.

- [ ] **Step 1: Make the engine-fallback test patch the owning implementation module**

Replace provider-facade monkeypatching with:

```python
from market_data_platform.standardize.fusion.a_share_minute import guan_deals

monkeypatch.setattr(guan_deals, "_try_import_polars", lambda: None)
```

Keep all expected error text and pandas/polars equivalence assertions unchanged.

- [ ] **Step 2: Run deal aggregation tests and verify the new module is incomplete**

```bash
uv run --extra dev python -m pytest tests/test_a_share_minute_fusion.py -k "deal or polars" -q
```

Expected: failure until aggregation helpers move.

- [ ] **Step 3: Move all deal-specific helpers as one dependency-consistent unit**

Move from the three legacy fusion parts every helper reachable from `aggregate_guan_deal_file`, including deal-file/date parsing, symbol mapping/fallback, time/session bucket logic, pandas partial aggregation/compaction, Polars partial aggregation/compaction, validation, context/state dataclasses, engine selection, and aggregation result assembly. Update all imports to `.schema` / `.normalize` / local helpers.

Do not rename payload fields, stats fields, error messages relied on by tests, default row-group values, or unit-profile strings.

- [ ] **Step 4: Export the aggregation API**

Add `aggregate_guan_deal_file`, `GuanDealAggregationResult`, and `GuanDealAggregationStats` to `a_share_minute/__init__.py`.

- [ ] **Step 5: Replace the public provider fusion module with a direct compatibility facade**

Its implementation should be structurally equivalent to:

```python
"""Compatibility facade for standardized A-share minute fusion."""

from market_data_platform.standardize.fusion.a_share_minute import (
    CANONICAL_MINUTE_COLUMNS,
    CANONICAL_MINUTE_SCHEMA,
    DEFAULT_BATCH_ROW_GROUPS,
    DEFAULT_COMPACTION_ROW_GROUPS,
    DEFAULT_COMPACTION_ROWS,
    DEFAULT_MAX_AGGREGATED_ROWS,
    DEFAULT_POLARS_BATCH_ROW_GROUPS,
    GuanDealAggregationResult,
    GuanDealAggregationStats,
    LEGACY_GUAN_CANONICAL_UNITS,
    LEGACY_GUAN_HUNDRED_X_UNITS,
    LegacyGuanNormalizationResult,
    LegacyGuanNormalizationStats,
    LegacyGuanUnitProfile,
    MINUTE_KEY_COLUMNS,
    MinuteAggregationEngine,
    MinuteFusionResult,
    MinuteFusionStats,
    aggregate_guan_deal_file,
    fuse_and_write_minute_partition,
    fuse_minute_frames,
    normalize_legacy_guan_partition,
    normalize_legacy_guan_partition_with_stats,
    normalize_tushare_partition,
    write_canonical_minute_partition,
)
```

Set `__all__` to the same supported public names. Do not retain implementation imports from old `partNN` files.

- [ ] **Step 6: Run fusion and identity tests**

```bash
uv run --extra dev python -m pytest tests/test_a_share_minute_fusion.py tests/test_data_lifecycle_architecture.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/market_data_platform/standardize/fusion/a_share_minute src/market_data_platform/providers/a_share_minute_fusion.py tests/test_a_share_minute_fusion.py tests/test_data_lifecycle_architecture.py
git commit -m "refactor: move minute deal aggregation into standardize"
```

---

### Task 4: Extract minute quality validation and JSON manifest persistence before moving build orchestration

**Files:**
- Create: `src/market_data_platform/quality_a_share_minute.py`
- Create: `src/market_data_platform/publish/json_manifest.py`
- Create: `tests/test_a_share_minute_publish.py`
- Modify: `tests/test_a_share_minute_build.py`

**Interfaces:**
- Produces: `validate_fused_minute_dataset(output_dir, *, expected_dates=None, start_date=None, end_date=None, prevalidated_dates=None) -> dict[str, Any]` in `quality_a_share_minute.py`.
- Produces: `write_json_manifest(path: Path, payload: Mapping[str, Any]) -> None` in `publish/json_manifest.py` with atomic UTF-8 JSON write.
- Consumes: canonical schema/key constants from `standardize.fusion.a_share_minute`.

- [ ] **Step 1: Add a publish-layer atomic JSON test**

Create a focused test:

```python
def test_write_json_manifest_is_utf8_json_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / "metadata" / "minute.json"
    write_json_manifest(path, {"schema_version": "x.v1", "status": "passed"})
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": "x.v1",
        "status": "passed",
    }
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))
```

Add a failure-preserves-existing-file test by monkeypatching the module's JSON dump/write step or `os.replace` at the final swap, matching the existing atomic Parquet test pattern.

- [ ] **Step 2: Add a direct quality ownership test**

In `tests/test_a_share_minute_build.py`, import:

```python
from market_data_platform.quality_a_share_minute import validate_fused_minute_dataset
```

Keep existing invalid-partition and expected/missing/orphan assertions unchanged so the new quality implementation must satisfy the old contract.

- [ ] **Step 3: Run the new tests and verify import failures**

```bash
uv run --extra dev python -m pytest tests/test_a_share_minute_publish.py tests/test_a_share_minute_build.py -k "validate_fused or validation or json_manifest" -q
```

Expected: import failures until the new modules exist.

- [ ] **Step 4: Move partition validation to the quality module**

Move `validate_fused_minute_dataset` and the partition validation helpers it calls out of build implementation. Preserve returned keys exactly: `status`, `partition_count`, `rows`, `date_min`, `date_max`, `invalid_dates`, `missing_dates`, `orphan_dates`, `empty_dataset`, `empty_source_inventory`, `output_dates_in_range`, `partitions`.

- [ ] **Step 5: Implement atomic JSON publication**

`write_json_manifest` must create the parent directory, write a temporary file in that directory using `json.dump(..., ensure_ascii=False, indent=2, allow_nan=False)`, append a trailing newline, `os.replace` it into place, and remove leftover temporary files in `finally`.

- [ ] **Step 6: Run focused quality/publish tests**

Run the Step 3 command. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/market_data_platform/quality_a_share_minute.py src/market_data_platform/publish/json_manifest.py tests/test_a_share_minute_publish.py tests/test_a_share_minute_build.py
git commit -m "refactor: separate minute quality and manifest persistence"
```

---

### Task 5: Move minute materialization options, inventory, checkpointing, workers, and build orchestration

**Files:**
- Create: `src/market_data_platform/standardize/materialize/a_share_minute/options.py`
- Create: `src/market_data_platform/standardize/materialize/a_share_minute/inventory.py`
- Create: `src/market_data_platform/standardize/materialize/a_share_minute/checkpoint.py`
- Create: `src/market_data_platform/standardize/materialize/a_share_minute/workers.py`
- Create: `src/market_data_platform/standardize/materialize/a_share_minute/build.py`
- Modify: `src/market_data_platform/standardize/materialize/a_share_minute/__init__.py`
- Modify: `tests/test_a_share_minute_build.py`
- Modify: `tests/test_data_lifecycle_architecture.py`

**Interfaces:**
- Consumes: public fusion API from `standardize.fusion.a_share_minute`, `validate_fused_minute_dataset` from `quality_a_share_minute`, `write_json_manifest` from `publish.json_manifest`, `minute_dataset_lock` from shared platform utilities.
- Produces: `DEFAULT_FUSED_MINUTE_MANIFEST_SUBPATH`, `DEFAULT_FUSED_MINUTE_SUBDIR`, `MinuteFusionBuildOptions`, `build_fused_minute_dataset`, and a compatibility re-export of `validate_fused_minute_dataset`.

- [ ] **Step 1: Point build behavioral tests at the new materialization API**

Use:

```python
from market_data_platform.standardize.materialize.a_share_minute import (
    MinuteFusionBuildOptions,
    build_fused_minute_dataset,
)
from market_data_platform.quality_a_share_minute import validate_fused_minute_dataset
```

Tests that intentionally verify provider facade compatibility stay in `test_data_lifecycle_architecture.py`.

- [ ] **Step 2: Run representative build tests and verify failure**

```bash
uv run --extra dev python -m pytest \
  tests/test_a_share_minute_build.py::test_dry_run_discovers_filtered_sources_without_writing \
  tests/test_a_share_minute_build.py::test_build_applies_date_dependent_legacy_scaling_and_audits_regimes \
  tests/test_a_share_minute_build.py::test_build_orchestrates_guan_then_tushare_with_tushare_priority \
  tests/test_a_share_minute_build.py::test_deal_resume_excludes_output_directory_as_an_input -q
```

Expected: import or missing-implementation failures.

- [ ] **Step 3: Move options and validation**

Move `MinuteFusionBuildOptions`, `_validate_date`, `_validate_build_date_options`, `_validate_build_runtime_options`, `_validate_build_override_options`, `_in_range`, and public default paths into `options.py`. Preserve all defaults and exception text.

- [ ] **Step 4: Move source inventory and discovery**

Move `_MinuteSourceInventory`, `_expanded_path`, input path requirements, partition/deal/TuShare filename patterns, source discovery, inventory serialization, and symbol mapping load/inventory facts into `inventory.py`. Keep previous-output exclusion and protected/override semantics exactly.

- [ ] **Step 5: Move resumable checkpoint state**

Move `_DEAL_CHECKPOINT_SCHEMA_VERSION`, `_DEAL_TRANSFORM_CONTRACT_VERSION`, file inventory/hash/fingerprint helpers needed by checkpointing, checkpoint path/contract/load/current-action checks, persist/finalize execution state into `checkpoint.py`.

Checkpoint writes remain private atomic JSON execution-state writes. They must not call the publish manifest writer because checkpoint payloads are resumable state rather than durable publication metadata.

- [ ] **Step 6: Move materialization workers**

Move symbol mapping transformation, partition validation shortcuts used only for already-written worker outputs, `_normalize_legacy_inputs`, `_merge_deal_inputs`, `_merge_tushare_inputs`, and worker/process helpers into `workers.py`. Replace every provider fusion import with `standardize.fusion.a_share_minute` imports.

Any helper that makes a final dataset acceptance decision belongs in `quality_a_share_minute`, not `workers.py`.

- [ ] **Step 7: Move orchestration and remove dead unreachable return**

Move `_legacy_unit_regimes`, manifest fact assembly, `_build_fused_minute_dataset`, and `build_fused_minute_dataset` into `build.py`.

Replace the old durable manifest call:

```python
_atomic_write_json(payload, manifest_path)
```

with:

```python
write_json_manifest(manifest_path, payload)
```

Call `validate_fused_minute_dataset` from `quality_a_share_minute` for final acceptance. Preserve the dry-run `validation` object exactly. Remove the unreachable duplicate `return _assemble_fused_manifest_payload(...)` after `return payload` because it cannot affect behavior.

- [ ] **Step 8: Export the materialization public surface**

`a_share_minute/__init__.py` directly exports build options/defaults/build and re-exports the quality function for migration compatibility:

```python
from market_data_platform.quality_a_share_minute import validate_fused_minute_dataset
```

- [ ] **Step 9: Run representative build tests and lifecycle boundary**

Run the Step 2 command plus:

```bash
uv run --extra dev python -m pytest tests/test_data_lifecycle_architecture.py -q
```

Expected: PASS and zero standardize imports from providers/ingest.

- [ ] **Step 10: Commit**

```bash
git add src/market_data_platform/standardize/materialize/a_share_minute tests/test_a_share_minute_build.py tests/test_data_lifecycle_architecture.py
git commit -m "refactor: move minute materialization into standardize"
```

---

### Task 6: Convert build provider facade and private part modules; migrate repository-owned callers

**Files:**
- Modify: `src/market_data_platform/providers/a_share_minute_build.py`
- Modify: `src/market_data_platform/providers/a_share_minute_build_part01.py`
- Modify: `src/market_data_platform/providers/a_share_minute_build_part02.py`
- Modify: `src/market_data_platform/providers/a_share_minute_build_part03.py`
- Modify: `src/market_data_platform/providers/a_share_minute_fusion_part01.py`
- Modify: `src/market_data_platform/providers/a_share_minute_fusion_part02.py`
- Modify: `src/market_data_platform/providers/a_share_minute_fusion_part03.py`
- Modify: repository-owned callers returned by code search for `providers.a_share_minute_fusion` and `providers.a_share_minute_build_part`.
- Modify: `tests/test_guan_mobile_raw.py` where it imports `_discover_deal_files` from a build part.

**Interfaces:**
- Produces: legacy provider public modules as direct facades; temporary private part facades for unsupported-but-repository-used private symbols.
- Consumes: new standardize semantic modules and quality API.

- [ ] **Step 1: Inventory every old private caller before changing compatibility files**

Run:

```bash
rg -n "market_data_platform\.providers\.a_share_minute_(fusion|build)(_part0[123])?" src tests scripts
```

Record each repository-owned import and map it to the semantic owner. Expected known examples include coverage materialization, `scripts/operations/audit_minute_overlap.py`, minute build internals, and `tests/test_guan_mobile_raw.py`.

- [ ] **Step 2: Migrate repository-owned callers to semantic paths**

Examples:

```python
from market_data_platform.standardize.fusion.a_share_minute import (
    CANONICAL_MINUTE_COLUMNS,
    CANONICAL_MINUTE_SCHEMA,
    normalize_tushare_partition,
)
```

and, for source discovery needed by tests/operations:

```python
from market_data_platform.standardize.materialize.a_share_minute.inventory import (
    _discover_deal_files,
)
```

Do not make quality modules import materialization just to reuse discovery; move shared read-only path helpers to the lowest appropriate semantic module instead.

- [ ] **Step 3: Replace `providers.a_share_minute_build` with a direct facade**

Use direct imports from `standardize.materialize.a_share_minute` and expose the same existing public `__all__` names: defaults, `MinuteFusionBuildOptions`, `aggregate_guan_deal_file`, `build_fused_minute_dataset`, `validate_fused_minute_dataset`.

- [ ] **Step 4: Thin private part files**

After Step 2 code search has no repository-owned implementation imports, replace each old part file with explicit compatibility re-exports for any remaining symbols that tests or documented external callers still require. For broad private surfaces, use module-level `__getattr__` delegating to the single semantic owner only as a temporary migration seam; do not duplicate implementation bodies.

A facade must contain no pandas/PyArrow transformation code, checkpoint logic, validation policy, or manifest assembly.

- [ ] **Step 5: Assert no standardize reverse imports and no repository-owned private implementation imports**

Run:

```bash
rg -n "market_data_platform\.providers" src/market_data_platform/standardize
rg -n "market_data_platform\.providers\.a_share_minute_(fusion|build)_part0[123]" src tests scripts
```

Expected: first command returns no matches. Second command returns only deliberately retained compatibility tests, or no matches after those tests are moved to public facades.

- [ ] **Step 6: Run compatibility and affected caller tests**

```bash
uv run --extra dev python -m pytest \
  tests/test_data_lifecycle_architecture.py \
  tests/test_a_share_minute_fusion.py \
  tests/test_a_share_minute_build.py \
  tests/test_guan_mobile_raw.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/market_data_platform/providers src/market_data_platform/standardize scripts tests
git commit -m "refactor: make minute providers compatibility facades"
```

---

### Task 7: Add deterministic old-entry/new-entry contract equivalence checks

**Files:**
- Create: `tests/test_a_share_minute_lifecycle_compatibility.py`
- Modify: `tests/test_data_lifecycle_architecture.py` only if identity coverage belongs there instead.

**Interfaces:**
- Consumes: legacy public provider facades and new standardize public APIs.
- Produces: deterministic content/contract compatibility oracle for the migration.

- [ ] **Step 1: Add direct object identity assertions for all supported public symbols**

Build a tuple of old/new objects and assert `old is new`, covering at minimum:

```python
(
    (legacy_fusion.CANONICAL_MINUTE_SCHEMA, new_fusion.CANONICAL_MINUTE_SCHEMA),
    (legacy_fusion.normalize_tushare_partition, new_fusion.normalize_tushare_partition),
    (legacy_fusion.fuse_minute_frames, new_fusion.fuse_minute_frames),
    (legacy_fusion.aggregate_guan_deal_file, new_fusion.aggregate_guan_deal_file),
    (legacy_build.MinuteFusionBuildOptions, new_build.MinuteFusionBuildOptions),
    (legacy_build.build_fused_minute_dataset, new_build.build_fused_minute_dataset),
    (legacy_build.validate_fused_minute_dataset, new_build.validate_fused_minute_dataset),
)
```

- [ ] **Step 2: Add deterministic frame hash helper**

Use canonical row ordering and pandas hashing rather than relying solely on Parquet byte identity:

```python
def _frame_hash(frame: pd.DataFrame) -> str:
    canonical = frame.sort_values(["ts_code", "trade_time"], kind="stable").reset_index(drop=True)
    values = pd.util.hash_pandas_object(canonical, index=False).to_numpy().tobytes()
    return hashlib.sha256(values).hexdigest()
```

- [ ] **Step 3: Verify legacy/new fusion entry points produce equal output and stats**

Use the same deterministic Guan/TuShare fixture through both entry points and assert schema equality, frame hash equality, unique keys, sorted keys, and equal `MinuteFusionStats`.

Because direct facades should have object identity, this test also protects against a future facade accidentally growing adaptation logic.

- [ ] **Step 4: Verify build dry-run contracts through both public paths**

Construct one deterministic dry-run source inventory and compare the entire returned payload after removing no fields if dry-run contains no volatile timestamps; if a generated timestamp is present, remove only `generated_at` before equality comparison.

- [ ] **Step 5: Run compatibility tests**

```bash
uv run --extra dev python -m pytest tests/test_a_share_minute_lifecycle_compatibility.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_a_share_minute_lifecycle_compatibility.py
git commit -m "test: verify minute lifecycle compatibility"
```

---

### Task 8: Run the complete minute regression surface and update lifecycle documentation

**Files:**
- Modify: `docs/data-lifecycle-architecture.md`
- Modify: `docs/operations/a-share-minutes.md` only where implementation import paths are documented.
- Modify: `docs/testing.md` if test ownership descriptions need updating.
- Modify: `pyproject.toml` only if Ruff per-file ignores refer to moved minute implementation paths.

**Interfaces:**
- Consumes: all completed migration tasks.
- Produces: documented lifecycle ownership and a green regression suite.

- [ ] **Step 1: Update lifecycle documentation with the completed minute migration**

State explicitly that:

```text
providers.a_share_minute_fusion
  -> standardize.fusion.a_share_minute

providers.a_share_minute_build
  -> standardize.materialize.a_share_minute

materialize
  -> quality_a_share_minute
  -> publish.json_manifest
```

Document that `quality_a_share_minute.py` is an interim quality-layer module because the repository already contains `quality.py`; a future quality-package migration can consolidate them without blocking minute ownership now.

- [ ] **Step 2: Update Ruff path-specific configuration if moved modules need the same intentional ignores**

Do not add broad ignores. Translate only existing minute-specific ignores to the new exact file paths, and remove obsolete ignores when the semantic split eliminates the condition.

- [ ] **Step 3: Run the documented minute regression set**

```bash
uv run --extra dev python -m pytest \
  tests/test_data_lifecycle_architecture.py \
  tests/test_a_share_minute_lifecycle_compatibility.py \
  tests/test_a_share_minute_fusion.py \
  tests/test_a_share_minute_build.py \
  tests/test_a_share_minute_coverage.py \
  tests/test_a_share_minute_bj_overlay.py \
  tests/test_tushare_a_share_mins.py \
  tests/test_guan_mobile_raw.py -q
```

Expected: PASS.

- [ ] **Step 4: Run formatting/lint/type checks used by the repository**

```bash
uv run --extra dev ruff check src tests scripts
uv run --extra dev ruff format --check src tests scripts
```

If the repository's documented CI also runs `ty`, run its existing documented command without inventing new type-checking scope.

- [ ] **Step 5: Re-run import searches**

```bash
rg -n "market_data_platform\.providers" src/market_data_platform/standardize
rg -n "market_data_platform\.providers\.a_share_minute_(fusion|build)_part0[123]" src tests scripts
```

Expected: zero reverse dependencies; old private part imports limited to explicitly documented compatibility coverage or zero.

- [ ] **Step 6: Commit documentation/config cleanup**

```bash
git add docs pyproject.toml
git commit -m "docs: record minute lifecycle ownership"
```

- [ ] **Step 7: Compare branch to main before PR**

```bash
git diff --stat main...HEAD
git log --oneline main..HEAD
```

Confirm the diff contains only minute lifecycle ownership, compatibility tests/facades, quality/publish seams required by the minute build, and related docs/config. Any unrelated semantic change is removed or split before review.
