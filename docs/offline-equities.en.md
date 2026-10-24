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

## Recompress a completed mirror

```bash
python -m quant_market_data_platform.offline_equities.repack \
  --source "$SOURCE_SECURITY_MIRROR" --output "$NEW_DELTA_CANDIDATE"
```

The source requires a completed `summary.json`, `file_index.csv`, and `parquet/`.
The direct repacker uses delta encodings for strings/integers, byte-stream split for
floats, 262144-row groups, and ZSTD12. It preserves every physical field, schema
metadata, null, lexical value, and row order in standard Parquet. It processes one
file at a time and rejects input partitions exceeding 2 GiB of uncompressed pages.
Decoded batches of 8192 rows are checked cumulatively against the smaller of 1 GiB
and one sixth of available memory. The source table is released before readback to
leave room for encoding, decoding, and hash buffers.

Each file receives complete all-column logical SHA256 readback before its data and
proof directory are atomically published. An OS file lock prevents concurrent writes.
Resume checks compressed input/output hashes and reuses verified partitions. Changed
source manifests, implementation, or Arrow versions require a separate output.
Unknown partitions are preserved and rejected. Progress is in `progress.json`.
Completion produces an index, summary, and `bars_by_security` DuckDB catalog view.
The source mirror remains intact and the candidate does not become a current alias.
Larger row groups can increase decompression for date-filtered queries.
