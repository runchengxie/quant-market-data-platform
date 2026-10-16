# Explicit research panel inputs

`quant_market_data_platform.research_views.legacy_strategy_inputs.load_strategy_panel`
reads a hash-pinned Parquet panel from an explicit manifest. It does not discover
latest directories, download data, or substitute one asset class for another.

The JSON manifest has exactly these fields:

```json
{
  "schema_version": "research.strategy_input.v1",
  "asset_kind": "etf_daily",
  "path": "panel.parquet",
  "sha256": "replace with the actual file SHA-256",
  "available_at": null
}
```

Supported kinds are `etf_daily`, `a_share_daily`, `ths_hot`, `concept_members`,
and `rotation_signal`. The caller must specify the expected kind and an explicit
`as_of` cutoff. Relative panel paths resolve beside the manifest. Files are limited
to 64 MiB; large assets need an explicitly published bounded view.

A `trade_date` column is required. Date strings, integer YYYYMMDD dates and
persisted duplicate DataFrame indices are supported. Rows after the cutoff are
excluded. A date-only cutoff means midnight UTC; pass a timezone-aware timestamp
when an intraday cutoff is needed. A non-null `available_at` must include a
timezone and must not exceed the cutoff.

The receipt always reports `strict_point_in_time=false` and diagnostic evidence.
An operator-supplied timestamp is not independently validated historical source
visibility. Do not upgrade this receipt into point-in-time or executable evidence.
