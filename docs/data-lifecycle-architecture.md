# Data code lifecycle

[中文页面](data-lifecycle-architecture.zh-CN.md)

The platform organizes shared market data around the following dependency direction:

```text
provider API
  -> ingest
  -> raw immutable asset
  -> standardize
  -> standardized / canonical asset
  -> quality receipt
  -> publish
  -> published asset
```

## Layer responsibilities

`quant_market_data_platform.ingest` owns provider access, request reliability, quotas, and raw landing. The first migrated entry point is `ingest.tushare.daily`; lower-level provider runtime is being moved in stages.

`quant_market_data_platform.standardize` owns field mapping, type conversion, deduplication, sorting, time handling, source fusion, and standardized dataset materialization. It consumes persisted raw assets and must not depend back on `providers` or `ingest` implementations.

Quality checks availability and writes receipts. Publish manages versions, aliases, manifests, and provenance. Warehouse provides queries and materialization. Model windows, embeddings, labels, training samples, and research features belong to model or research projects, not the shared standardization layer.

## Migrated paths

The build implementation and daily schema for TuShare A-share `daily_clean` have moved into `standardize`. `providers.tushare_a_share_clean` remains as a compatibility entry point while callers migrate.

A-share minute fusion and fused-dataset materialization are split by lifecycle:

```text
providers.a_share_minute_fusion
  -> standardize.fusion.a_share_minute

providers.a_share_minute_build
  -> standardize.materialize.a_share_minute

standardize.materialize.a_share_minute
  -> quality_a_share_minute
  -> publish.json_manifest
```

`standardize.fusion.a_share_minute` owns the canonical schema, source normalization, deduplication, deterministic ordering, Guan/TuShare fusion, Guan deal aggregation, and canonical Parquet writes.

`standardize.materialize.a_share_minute` owns build options, source inventory, resumable checkpoints, partition workers, dataset locks, and build orchestration. Final acceptance calls the independent minute-quality API; the publish layer persists the durable JSON manifest. Deal checkpoints remain materialization execution state.

The repository already has a top-level `market_data_platform.quality.py` module. Minute quality therefore currently lives in `market_data_platform.quality_a_share_minute` rather than a conflicting `quality/` package. It can converge when the quality package is migrated as a whole.

The old `providers.a_share_minute_fusion` and `providers.a_share_minute_build` paths remain compatibility entry points. Historical `a_share_minute_*_partNN` files only forward calls; they no longer own transformation, validation, checkpoint, or manifest behavior.

## Boundary checks

`tests/test_data_lifecycle_architecture.py` prevents `standardize` from importing `providers` or `ingest`. Daily-clean tests keep old and new function identities aligned. The minute path checks identity compatibility for schema, normalization, fusion, options, and validation that can be forwarded directly.

Thin wrappers remain for minute builds and Guan deal aggregation to preserve dependency injection and monkeypatch behavior for optional aggregation engines. These wrappers do not own transformation or materialization logic. `tests/test_a_share_minute_lifecycle_compatibility.py` compares the old and new canonical contract, fusion content hash, Guan deal pandas aggregation, and build dry-run payload.

## Next migration order

Continue moving provider runtime and research-derived assets along the full lifecycle. The next priority is the remaining TuShare minute raw-ingress runtime, followed by fundamentals, ownership, flow, and hotspot. Preserve old entry points during each migration and verify behavior with contract, quality-receipt, and output-equivalence tests.
