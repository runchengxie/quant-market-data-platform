# A-share Minute Lifecycle Boundary Migration

## Goal

Move the source-neutral A-share minute fusion and materialization implementation out
of `market_data_platform.providers` and into explicit lifecycle packages while
preserving all current data contracts and legacy import paths.

This migration is package-internal. It does not split the project into additional
repositories and does not change provider authority, source priority, units,
partition layout, schema, manifest schema, receipt semantics, or CLI behavior.

## Current state

The minute implementation currently mixes several lifecycle responsibilities under
`providers/`:

- `a_share_minute_fusion.py` is a re-export facade over `part01/02/03`;
- fusion part 01 contains the canonical schema, source normalization, deduplication,
  ordering, source fusion, canonical Parquet writing, symbol resolution, and Guan
  deal parsing helpers;
- fusion parts 02/03 contain Guan deal aggregation orchestration and pandas/polars
  execution paths;
- `a_share_minute_build.py` is a public facade over `build_part01/02/03`;
- build parts contain source inventory, build options, checkpoint hashing and
  resume state, materialization workers, dataset validation, manifest assembly,
  manifest persistence, and locking.

The repository already establishes the dependency direction
`provider API -> ingest -> immutable raw -> standardize -> quality -> publish`.
`standardize` is guarded by a test that rejects imports from `providers` and
`ingest`, so a top-level facade move alone is insufficient: the implementation
imports between the current `partNN` modules must migrate as a coherent unit.

## Target package structure

The implementation remains under the existing `src` layout:

```text
src/market_data_platform/
├── standardize/
│   ├── fusion/
│   │   └── a_share_minute/
│   │       ├── __init__.py
│   │       ├── schema.py
│   │       ├── normalize.py
│   │       ├── fusion.py
│   │       └── guan_deals.py
│   └── materialize/
│       └── a_share_minute/
│           ├── __init__.py
│           ├── options.py
│           ├── inventory.py
│           ├── checkpoint.py
│           └── build.py
├── quality/
├── publish/
└── providers/
    ├── a_share_minute_fusion.py
    ├── a_share_minute_fusion_part01.py
    ├── a_share_minute_fusion_part02.py
    ├── a_share_minute_fusion_part03.py
    ├── a_share_minute_build.py
    ├── a_share_minute_build_part01.py
    ├── a_share_minute_build_part02.py
    └── a_share_minute_build_part03.py
```

The semantic filenames are the desired end state. During migration, compatibility
facades may temporarily retain the old `partNN` files so existing private imports in
tests and operations code do not break in one large change.

## Lifecycle ownership

### `standardize.fusion.a_share_minute`

Own source-neutral transformation logic:

- canonical minute columns and Arrow schema;
- source projection and canonical type coercion;
- legacy Guan unit normalization;
- TuShare canonical projection;
- source-local deduplication;
- deterministic ordering;
- Guan/TuShare overlap resolution;
- source-neutral canonical partition writing;
- Guan deal-to-minute transformation needed to produce canonical minute bars.

The package may depend on shared platform utilities and third-party dataframe /
Arrow libraries. It must not import `market_data_platform.providers` or
`market_data_platform.ingest`.

### `standardize.materialize.a_share_minute`

Own orchestration that turns immutable inputs into standardized minute partitions:

- build options and date/runtime validation;
- input discovery and source inventory;
- symbol mapping load needed by the transformation;
- resumable transformation checkpoints;
- partition materialization workers;
- dataset locking around materialization;
- orchestration of legacy, Guan deal, and TuShare standardized inputs.

It depends on `standardize.fusion.a_share_minute`, never on legacy provider
implementations.

### `quality`

Own dataset acceptance decisions. The existing
`validate_fused_minute_dataset()` behavior may remain reachable through a migration
adapter initially, but the authoritative implementation must not stay embedded in
materialization long term.

Materialization may call a quality API and consume its result. It must not redefine
quality policy merely to complete the package move.

### `publish`

Own manifest persistence, publication metadata, aliases, versions, and provenance.
Materialization may assemble build facts needed by a manifest, but durable manifest
I/O should delegate to the publish layer rather than introducing another private
JSON writer in `standardize`.

Checkpoint sidecars used only to resume an in-progress materialization remain owned
by materialization; they are execution state, not published manifests.

## Compatibility strategy

The old provider imports remain valid throughout the migration.

```text
market_data_platform.providers.a_share_minute_fusion
    -> market_data_platform.standardize.fusion.a_share_minute

market_data_platform.providers.a_share_minute_build
    -> market_data_platform.standardize.materialize.a_share_minute
```

Public objects should be direct re-exports wherever practical so identity checks can
assert that old and new imports resolve to the same function/class object.

Current private imports of `providers.a_share_minute_*_partNN` are migrated in two
steps:

1. repository-owned callers move to semantic new modules;
2. old `partNN` modules become thin compatibility facades and are removed only after
   code search proves no supported callers remain.

No compatibility facade may contain transformation, validation, publication, or
checkpoint business logic after its implementation has moved.

## Data and behavior invariants

The migration must preserve:

- canonical columns: `ts_code`, `trade_time`, `open`, `close`, `high`, `low`,
  `vol`, `amount`;
- canonical Arrow types and metadata behavior;
- key: `(ts_code, trade_time)`;
- TuShare-over-Guan priority on overlapping keys;
- stable ordering by the canonical key;
- legacy Guan unit profiles and their date application rules;
- Guan deal aggregation output and pandas/polars equivalence expectations;
- partition path and Parquet compression behavior;
- build date defaults and option validation;
- deal checkpoint schema and contract fingerprint semantics;
- resume behavior;
- existing manifest schema and externally consumed fields;
- existing lock behavior;
- dry-run behavior;
- CLI and operations-script observable behavior.

This is a structural migration. Any desired semantic change discovered while moving
code becomes a separate change unless it is required to preserve current behavior.

## Migration sequence

### Phase 1: boundary tests

Extend lifecycle architecture tests before moving implementation:

- assert `standardize` contains no imports from `providers` or `ingest`;
- assert legacy public minute-fusion exports are identical to new exports;
- assert legacy public minute-build exports are identical to new exports;
- add import smoke tests for the new packages;
- retain existing minute contract/behavior tests as the compatibility oracle.

### Phase 2: fusion implementation

Move fusion implementation as one dependency-consistent unit into
`standardize.fusion.a_share_minute`.

Prefer semantic modules over reproducing `part01/02/03`. Keep the original public
surface in `providers.a_share_minute_fusion` as a thin re-export facade.

Update repository-owned internal imports such as coverage materialization and audit
scripts to use the new standardize path when their lifecycle ownership permits it.

### Phase 3: materialization implementation

Move build options, inventory, checkpointing, workers, and orchestration into
`standardize.materialize.a_share_minute`.

Keep `providers.a_share_minute_build` as a thin compatibility facade. Migrate
repository-owned callers off the old `build_partNN` paths before thinning those
modules into facades.

### Phase 4: quality and publish seams

Route dataset acceptance through an existing or dedicated quality-layer API while
preserving the current validation result contract.

Route manifest writing through `market_data_platform.publish`. Preserve the existing
manifest payload until a separately reviewed publication-contract change.

Do not move resumable deal checkpoints to publish.

### Phase 5: compatibility cleanup

After code search, tests, and operations scripts no longer rely on old private
`partNN` imports, reduce those files to facades and later remove them in a separate
cleanup change. Keep the two documented public provider facades for the migration
window.

## Verification

The implementation must be verified at four levels.

### Import boundary

- lifecycle architecture tests reject reverse dependencies;
- old public imports resolve to the same objects as new imports;
- no new standardize module imports provider or ingest implementations.

### Contract

Run existing minute tests covering fusion, build, coverage, BJ overlay, and TuShare
minute integration. Add focused tests for facade identity and new module imports.

### Output equivalence

For deterministic fixtures, compare old-entry and new-entry outputs using:

- canonical schema equality;
- row count and key uniqueness;
- stable row ordering;
- Parquet/table content hash where serialization is deterministic;
- normalized dataframe hash otherwise;
- build payload / manifest contract equality after removing intentionally volatile
  fields such as timestamps or temporary paths.

### Quality and receipt compatibility

Existing quality receipts and validation contracts remain the acceptance oracle.
The migration passes only when the same fixture/data state produces equivalent
accept/reject results and materially equivalent receipt facts.

## Non-goals

This change does not:

- create separate repositories;
- change TuShare/Guan provider authority;
- redesign minute schemas or units;
- change source priority;
- remove rollback data;
- redesign quality policy;
- redesign publication schemas or aliases;
- migrate fundamentals, flow, hotspot, or ownership features yet;
- remove all provider compatibility imports in one step.

## Acceptance criteria

The migration is complete when:

1. minute fusion implementation lives under `standardize.fusion`;
2. minute materialization implementation lives under `standardize.materialize`;
3. `standardize` has zero implementation imports from `providers` or `ingest`;
4. legacy public provider entry points remain import-compatible;
5. repository-owned callers no longer require old private `partNN` implementation
   paths;
6. quality decisions are owned by `quality` and manifest persistence is owned by
   `publish`;
7. existing minute tests pass without data-semantic changes;
8. old-entry/new-entry contract and deterministic output equivalence checks pass;
9. no independent repository split is introduced.
