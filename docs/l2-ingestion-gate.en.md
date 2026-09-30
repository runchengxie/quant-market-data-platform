# L2 ingestion gate and dataset contract

[中文页面](l2-ingestion-gate.md)

Raw Level-2 files are immutable. New partitions must pass profiling, classification, and a machine-readable gate before downstream research treats them as production data.

## Dataset contract

Use `market_data_platform.dataset_contract.v1` to record semantics that cannot be safely inferred from Parquet types: dataset and provider identity, asset schema version, primary key, timezone and event-time meaning, units, null and sentinel meanings, frequency, PIT policy, event ordering, and dataset-specific quality policy.

Exchange event datasets must state their ordering rules. A contract may declare an exchange sequence only when it explicitly names the sequence column. A total order across channels must remain false unless the source protocol guarantees it. Quality overrides may be `ignore`, `research_only`, or `quarantine`. This lets a snapshot tolerate structurally valid empty book levels without weakening sequence or identity checks.

```json
{
  "schema_version": "market_data_platform.dataset_contract.v1",
  "dataset": {
    "id": "cn_a_share_l2_order",
    "provider": "vendor_x",
    "market": "a_share",
    "schema_version": "vendor_x.order.v3",
    "data_version": "20260830"
  },
  "primary_key": ["trading_day", "symbol", "channel", "sequence"],
  "time": {
    "timezone": "Asia/Shanghai",
    "event_time_semantics": "exchange_generated"
  },
  "units": {"price": "fen", "volume": "shares"},
  "null_semantics": {"Price": {"nullable": true}},
  "sentinels": {
    "Price": [{"raw_value": -999999999, "meaning": "protocol_no_value"}]
  },
  "ordering": {
    "exchange_sequence_available": true,
    "channel_column": "ChannelNo",
    "sequence_column": "ApplSeqNum",
    "cross_channel_total_order": false
  },
  "expected_cadence": {"type": "trading_day"},
  "quality_rules": {
    "null_values": "ignore",
    "exchange_sequence_gaps": "research_only",
    "exchange_sequence_duplicate": "quarantine"
  },
  "pit": {"applicable": false},
  "metadata": {}
}
```

## Ordering evidence

`marketdata quality profile` and the resumable scanner recognize common raw-field aliases including `ChannelNo`, `ApplSeqNum`, `BizIndex`, `OrderTime`, `DealTime`, and `TickTime`.

When a sequence exists, reports count duplicate `(channel, sequence)` pairs, sequence regressions, non-numeric values, gaps, and truncation of tracked maxima. Gaps are evidence, not automatically data corruption; a provider partition may intentionally omit other message types. Without a sequence, the report uses `timestamp_fallback`. Timestamp plus source-file row order is a documented fallback and must not be described as exact exchange ordering.

## Daily or partition gate

```bash
marketdata quality gate \
  --root /path/to/raw-l2/day=20260830 \
  --checkpoint /path/to/state/l2-20260830.checkpoint.json \
  --dataset-id cn_a_share_l2 \
  --provider vendor_x \
  --dataset-contract /path/to/contracts/cn_a_share_l2.json \
  --pilot-manifest /path/to/pilot/manifest.json \
  --scan-output /path/to/reports/l2-20260830.scan.json \
  --output /path/to/reports/l2-20260830.dq.json
```

The gate reuses `quality scan` and can reuse unchanged files from its checkpoint. It does not repair, delete, or rewrite raw Parquet files and does not move publication aliases.

Default eligibility policy:

- `quarantine`: required columns missing, trading-day mismatch, duplicate/regressing/non-numeric exchange sequence, or pilot rows marked `exclude`.
- `research_only`: sequence gaps or truncation, timestamp regressions, duplicate event IDs, null/non-positive values, or pilot rows marked `tag`.
- `production`: none of these issues.

A dataset contract can tighten or relax individual severity levels when semantics justify it. The DQ receipt preserves each check's default `severity` and contract-resolved `effective_severity` so exceptions remain auditable.

Output uses `market_data_platform.dq_receipt.v1` and includes input summaries, check results, lineage, status, eligibility, and elapsed time. Downstream systems should use `eligibility` directly instead of reinterpreting the same structural issues.

## Other quality tools

`quality integrity` continues to validate trade references to order IDs. `quality_opening` continues to provide market-independent opening-auction accounting. Exchange-specific truncation, lag selection, snapshot alignment, model-input checks, leakage tests, and alpha validation remain downstream responsibilities.
