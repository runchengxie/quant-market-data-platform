# AFML research features

[中文页面](afml-research-features.md)

This page describes activity bars and low-frequency microstructure features provided by the data platform. The platform publishes versioned assets with lineage and quality evidence; it does not assess strategy Sharpe, CPCV, or promotion status.

## Install

The base control-plane install remains lightweight and does not force pandas to load when importing the root package. Install the optional research features before using this API:

```bash
uv sync --extra research-features
```

Import the explicit module:

```python
from market_data_platform.research_features import build_daily_microstructure_features
```

## Daily OHLCV features

`market_data_platform.research_features.build_daily_microstructure_features` uses only OHLCV and `amount` fields available at the time. It produces Parkinson high-low volatility, Corwin-Schultz effective-spread estimates, Amihud illiquidity, and turnover shock.

These are low-frequency liquidity proxies. They are not measured order-book spread, market impact, or order-flow imbalance.

## Activity bars

`build_activity_bars` supports tick bars, volume bars, and dollar/notional bars. The CLI is `marketdata research-features activity-bars`:

```bash
marketdata research-features activity-bars \
  --input trades.parquet \
  --output volume_bars.parquet \
  --receipt volume_bars.receipt.json \
  --source-contract trades.v1 \
  --asof 2026-07-14 \
  --kind volume \
  --threshold 100000
```

Input must be authorized tick-level trade data with at least:

```text
symbol
timestamp
price
volume
```

Output includes bar start/end, OHLC, volume, notional, VWAP, and trade count. Timestamps are parsed as timezone-aware UTC.

Recommended future asset keys are `trades`, `volume_bars`, `dollar_bars`, and `microstructure_features`. Keep them separate from `daily_clean`; tick-level and daily assets have different volume, revision, timezone, and quality-gate characteristics.

## Data boundaries

Without trade direction, order-book depth, or order messages, do not publish or infer VPIN, Kyle lambda, order-flow imbalance, depth-based price impact, or imbalance bars. If a provider later supplies aggressor side or complete order-book data, add a separate contract with its own builder and validator.

## Feature receipt

`feature_receipt` records the source contract, as-of date, row count, feature columns, feature SHA-256, and per-column null ratio. Downstream research should read published features through the current contract or registry, not a temporary cache directory.

The daily-feature CLI is `marketdata research-features daily`:

```bash
marketdata research-features daily \
  --input daily_ohlcv.parquet \
  --output daily_microstructure.parquet \
  --receipt daily_microstructure.receipt.json \
  --source-contract daily_clean.v1 \
  --asof 2026-07-14
```
