# Scoped minute research reads

An explicit published-asset contract can opt into a hash-bound minute quality
registry. Existing contracts without this declaration retain their behavior.
The guard is available through `PublishedParquetFrameReader`; other builders
must adopt the same policy explicitly.

## Contract and registry

Add `minute_quality` to the contract header:

```json
{
  "registry_path": "metadata/minute_quality/research-policy.json",
  "registry_sha256": "<exact registry SHA256>"
}
```

The JSON registry has `schema_version: minute.quality_registry.v1` and an
`assets` mapping keyed by each mapped feature asset. Each entry contains:

- `resolved_path` and `manifest_sha256` binding the exact publication.
- `market_scope`, such as `["SH", "SZ"]`.
- `partition_sha256`, mapping asset-relative Parquet paths to exact SHA256
  values. Every mapped input must have a binding.
- Unique `expected_dates` and `available_dates` lists in `YYYYMMDD` format.
- `blocked_stock_days`, each containing `trade_date`, `symbol`, `issue_id`,
  `evidence_path` and `evidence_sha256`.

All paths must resolve inside the declared artifacts root. The reader verifies
the registry, contract, manifests, mapped Parquet bytes and issue evidence
before and after reading. Directory mappings also reject changed file
inventories and symbolic descendants. Unmapped history is not hashed.
The registry is selected evidence, not an independent numerical data audit.
Diagnostic markers do not become confirmed stock-day exclusions. An explicit
Beijing security request cannot use a registry scoped only to SH/SZ.

## Research decisions

The default `quality_mode="error"` rejects intersecting blocked stock-days.
Use `quality_mode="exclude_stock_days"` only when the research explicitly
accepts removing the entire affected stock-day. Other securities and source
values are preserved. Missing expected sessions require separate, explicit
`acknowledged_missing_dates`; choosing stock-day exclusion does not acknowledge
calendar gaps. Naive times are interpreted in Asia/Shanghai, and aware times
are converted there for date comparisons.

Use explicit request dates and instruments. For bounded exploration, map one
partition through `ParquetFrameMapping.relative_path`; the existing reader
loads its mapped Parquet input before filtering. Do not map an entire minute
history merely to inspect one stock-day.

The returned frame has `attrs["minute_quality"]` with the policy identity,
scope, acknowledged gaps, excluded stock-days and excluded-row count. Reader
metadata incorporates the policy into its content fingerprint. Derived caches
must retain that fingerprint when adopting this reader.

SH/SZ research acceptance does not establish Beijing coverage, historical PIT,
cross-source consistency or full-market publication readiness. The guard does
not automatically retrofit other minute builders or reclassify old data.

## Earlier reverse history

The reverse scheduler accepts `--history-start-date YYYYMMDD`. Without a stored
scope or explicit option it retains the `20160101` lower bound. A real run
records the validated lower bound in
`metadata/minute_backfill/reverse_scheduler/history_scope.json`. Later runs
and read-only monitoring use that boundary. An explicit dry run does not
change it. Incomplete plans below the boundary are not resumed.

```bash
python scripts/operations/tushare_minute_reverse_backfill.py \
  --artifacts-root "$DATA_PLATFORM_ROOT" --history-start-date 20090101 \
  --max-dates 1 --batch-size 33 --cooldown-seconds 1.2 --dry-run
```

Before activation, verify the trade calendar covers the requested interval,
retain the previous completion receipt, preserve operational quota, and use
the existing audit blocker and writer lock. Revalidate the first acquired
session before increasing the bounded batch. An exhausted download pass never
certifies native semantics or authorizes publication.
