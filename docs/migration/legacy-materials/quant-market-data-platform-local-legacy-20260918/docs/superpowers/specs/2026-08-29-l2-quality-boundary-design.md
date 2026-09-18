# L2 Quality Boundary Design

## Goal

Move reusable raw L2 structural quality checks into `market-data-platform` while keeping model-, label-, and experiment-specific validation in downstream research projects.

## Scope of the first slice

The first slice introduces a native `market_data_platform.quality` module for inspecting explicit Parquet files. It covers structural checks that are independent of any model:

- required columns and observed schema;
- row counts and null counts;
- trading-day consistency with the file name;
- numeric ranges and non-positive prices/volumes;
- bounded ID repetition statistics;
- per-symbol timestamp reversals;
- JSON quality reports suitable for manifests and downstream consumers.

The raw input remains immutable. The quality tool is read-only and does not silently repair or overwrite data.

## Ownership boundary

`market-data-platform` owns ingestion, raw-data quality, deterministic normalization, canonical publication, manifests, lineage, and reusable market-data contracts.

`deep-learning` owns eventstream/model input contracts, label construction, leakage checks, training compatibility, signal generation, and model evaluation.

`research-workspace` owns alpha validation, portfolio backtesting, and research governance.

Opening-auction ledger reconstruction is reusable market-data quality logic. The market-agnostic
accounting core is now a second migration slice in `market_data_platform.quality_opening`:
it consumes explicit orders, trades, and cancels, reconstructs levels, and reports identity and
volume gaps. Raw-file discovery, event cutoffs, exchange-specific timing, lag selection, and
snapshot alignment remain downstream because those rules are market- and experiment-specific.

## Public interface

The first slice exposes:

```python
from market_data_platform.quality import profile_parquet

report = profile_parquet(
    "/path/to/order_2024-12-13.parquet",
    batch_size=262_144,
    max_tracked_ids=1_000_000,
)
```

The command-line entry point is:

```bash
marketdata quality profile \
  --file /path/to/order_2024-12-13.parquet \
  --output /tmp/l2-quality.json
```

If ID tracking reaches its configured bound, the report must state that the duplicate count is bounded/partial rather than presenting it as an exact global count.

## Compatibility strategy

The existing `deep-learning` profiler remains available during migration. Its implementation will be replaced by a thin compatibility adapter only after the platform module has equivalent tests and a real-file report comparison.

No raw-data paths, model configuration, or research protocol dates change in this slice.

## Acceptance criteria

1. Platform unit tests cover nulls, ranges, trading-day mismatch, repeated IDs across batches, timestamp reversals, and bounded tracking.
2. The platform CLI is documented and reachable from `marketdata --help`.
3. A representative 2021 and 2025 pre-open file can be profiled without GPU resources.
4. The platform report agrees with the existing deep-learning profiler for the same file on exact fields.
5. Existing deep-learning tests remain green.
6. Raw files are never modified by the quality command.

## Second-slice acceptance criteria

1. The platform opening core has focused tests for level reconstruction, unknown identities,
   overdrawn volume, known-side consumption, and order-level traces.
2. The deep-learning public opening-ledger entry point can use the platform core when the package
   is installed and falls back to its local implementation otherwise.
3. Deep-learning retains ownership of raw-file loading, cutoff/lag selection, coverage states,
   and snapshot alignment.

## Non-goals

- automatically cleaning or deleting raw data;
- moving model or label validation into the platform;
- moving alpha-research or portfolio-backtester into the platform;
- creating a separate repository for quality and cleaning;
- scanning the entire multi-terabyte lake in the first implementation slice.
