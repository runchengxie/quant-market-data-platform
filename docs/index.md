# quant-market-data-platform

[中文页面](index.zh-CN.md)

`quant-market-data-platform` collects market data from providers, normalizes its format, checks completeness and coverage, and publishes versioned datasets for research and reporting programs.

The active market scope is mainland China, with A-share assets led by TuShare and Guan minute data as a supporting source. RQData and the historical Hong Kong production path are retired; archived assets remain separate from the active production boundary.

## Quick start

You need Git, `uv`, and Python 3.11 or newer. Run these commands from this repository root. Installing dependencies needs network access; displaying help needs no provider credentials and downloads no market data.

```bash
uv sync --locked --extra dev
uv run --locked marketdata --help
```

Success displays the `marketdata` usage, subcommands, and `-h, --help` option, then exits with code 0. Next, choose a task in [Operations](operations.md) and follow [Credentials and configuration](operations/credentials.md) to set an external data directory and any required credentials.

## Responsibilities

- Maintain asset identifiers, dataset registration, manifests, and current data contracts.
- Ingest and publish raw, cleaned, standard, universe, and feature assets.
- Provide coverage, quality, lineage, backup, and release checks.
- Provide read-only interfaces for downstream research consumers.

Credentials, raw provider data, caches, large Parquet files, and production outputs stay outside Git. See the navigation for current contracts, operations, source configuration, and governance rules.
