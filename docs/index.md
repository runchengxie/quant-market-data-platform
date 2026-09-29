# quant-market-data-platform

[中文页面](index.zh-CN.md)

`quant-market-data-platform` is the shared market-data control plane for quantitative research and reporting systems. It owns data ingestion, normalization, quality governance, versioning, and published assets.

The active market scope is mainland China, with A-share assets led by TuShare and Guan minute data as a supporting source. RQData and the historical Hong Kong production path are retired; archived assets remain separate from the active production boundary.

## Quick start

```bash
uv sync --extra dev
export DATA_PLATFORM_ROOT=/data/market-data-platform
marketdata --help
```

## Responsibilities

- Maintain asset identifiers, dataset registration, manifests, and current data contracts.
- Ingest and publish raw, cleaned, standard, universe, and feature assets.
- Provide coverage, quality, lineage, backup, and release checks.
- Provide read-only interfaces for downstream research consumers.

Credentials, raw provider data, caches, large Parquet files, and production outputs stay outside Git. See the navigation for current contracts, operations, source configuration, and governance rules.
