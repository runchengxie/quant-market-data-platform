# Offline lossless US equity minute layouts

The optional `offline-equities` extra provides a standalone module for mirroring an audited
by-day Parquet dataset into ZSTD6 files partitioned by exact ticker, identity segment and year.
It does not download data, add a supported platform market, register an asset or switch current aliases.
Real data and runtime receipts stay outside Git.

```bash
uv sync --locked --extra offline-equities
uv run --locked --extra offline-equities python -m quant_market_data_platform.offline_equities \
  --source /datasets/equities/by-day \
  --identity-root /datasets/equities/identity-evidence \
  --output /datasets/equities/by-security-v2
```

All paths are explicit. Source and output must be disjoint. At least 20 GiB must remain free;
this lower bound is not an estimate of the total scratch and output space required.
Inputs include a completed `summary.json`, `parquet_manifest.csv`, `file_quality.parquet`
and the manifest's files below `parquet/`. Manifest paths escaping that root are rejected.
The evidence directory contains `wolf_identity_rule.json` and
`reference_inputs/security_identity_intervals.csv`; their hashes pin provenance.

The existing dataset identity rule is retained: use the existing candidate key or
`unresolved:ticker`, with WOLF separated at 2025-09-29. Evidence files pin lineage; they are
not parsed into newly inferred rules. Candidate keys are not certified permanent security IDs.
Ticker and settlement basis remain separate. No adjustment, padding, row filtering or
historical constituent reconstruction is performed.

Original physical fields, types and nulls are preserved; only `ResearchKey` is added.
ASCII lowercase letters and trailing dots are reversibly percent-encoded in symbol directories
so that `BCPC` and `BCpC` do not collide on Windows. DuckDB hive partitioning restores the
original ticker from `symbol=BC%70C`; users must keep exact symbol case.

Monthly stage files use 128 hash buckets and ZSTD1. Final files under
`parquet/symbol=.../security=.../year=.../data.parquet` use ZSTD6. Both stages read back every
row group and compare a logical all-column hash, including types, validity and row order.
Source file hashes, Parquet page checksums and strictly increasing per-segment timestamps
are checked. Duplicate minutes fail validation.

Repeating the command resumes verified checkpoints after checking configuration, the combined
implementation hash, source attributes and evidence. Incomplete task-owned files are retained
in `recovery/` before rebuilding. PID and process creation time protect the output lock.
Only `progress.json` with `status=complete` indicates success. Completion produces
`file_index.csv`, `catalog.duckdb` (`bars_by_security`) and `summary.json`.
Query only final `parquet/`, never scratch, staging, recovery or a mixture of pilot outputs.
Use exact `symbol` and `year` filters. `--dates` and `--symbols` create separate pilots;
changed selection, bucket counts, row groups or implementation require a new output version.

The repository implementation is `security-year-lossless-v2`. Existing external single-file v1
runs retain their frozen code and checkpoints; this module must not take over those output
directories. Synthetic regressions cover losslessness, identities, Windows path collisions,
resume, interrupted publication, ordering and input boundaries. They do not claim a new v2 run
over the real full dataset; historical real-data results belong to frozen v1 and its receipts.

See the [Chinese guide](offline-equities.md) for the same operational contract.
