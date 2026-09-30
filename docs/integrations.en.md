# System integrations

[中文页面](integrations.md)

Downstream research, backtest, trading, and reporting systems consume published assets as read-only data. They do not need to know the platform's internal ingestion, cleaning, or publication implementation.

## Consumer boundary

Downstream systems own their strategy research, feature engineering, models, backtests, positions, and reports. This repository owns the shared market-data entry points, currently centered on TuShare and Guan A-share minute data. Hong Kong support was retired with RQData; there is no active read-only consumer entry point.

Set the shared root with:

```bash
export DATA_PLATFORM_ROOT=/data/market-data-platform
```

This keeps downstream run outputs, caches, and reports in their own project directories while resolving market-data inputs under the shared root. A downstream `paths.artifacts_root` should point to the shared root only when its own runtime artifacts are intentionally stored there.

```yaml
paths:
  artifacts_root: "/data/market-data-platform"
```

Read assets through `metadata/current_assets/<market>_current.json` and each asset's manifest. A-share minute data currently uses `assets/derived/a_share/minute_1m` plus a frozen coverage receipt. Minute Parquet has no source column; join the receipt's `daily` records by trade date to inspect source coverage. Public-source ETF minute data uses its separate `assets/derived/a_share/etf_minute_<period>m/<version>` path. Consumers must read that version's `manifest.yml` and `receipt.json`; do not treat its coverage as equivalent to the all-market stock coverage in `minute_1m`.

Do not hard-code another project's working directory or scan full raw tick-level depth snapshots for low-frequency strategies. Research systems should record the boundaries for PIT universes, PIT fundamentals, historical industries, and daily valuation overlays in their own evidence; see [Research data integrity](research-integrity.en.md).

## GitHub Release inventory

GitHub Releases contain only a sanitized current-asset inventory, not production Parquet, credentials, or local storage paths. The inventory at [`data-cn-a-share-current-20260928`](https://github.com/runchengxie/quant-market-data-platform/releases/tag/data-cn-a-share-current-20260928) is for discovering versions, coverage ranges, row counts, and availability.

Consumers must still use `DATA_PLATFORM_ROOT/metadata/current_assets/<market>_current.json` and each asset's `manifest.yml` as data sources. If a Release inventory differs from the local current contract, stop consumption and regenerate the current contract. Do not assemble assets or fetch raw data from the Release inventory.

## Optional Qlib read-only DataLoader

The optional adapter maps published Parquet assets into a Qlib DataLoader. It supports explicit column mapping, a trading calendar, a per-date PIT universe, date and security filters, and serializable lineage. DataHandler, Dataset, model training, experiment tracking, and backtest backends are outside this repository's support boundary.

Install the optional dependency with:

```bash
uv sync --locked --extra qlib
```

The core package, data-production commands, and current-contract publication path do not import Qlib. `pyqlib` is loaded only when `QlibPublishedAssetAdapter.as_data_loader()` is called. A read plan must explicitly declare each Parquet path and column mapping. For example, with a registered `pit_universe` Parquet asset:

```python
from market_data_platform import (
    PITUniverseMapping,
    ParquetFrameMapping,
    PublishedAssetContract,
    PublishedFramePlan,
    TradingCalendarMapping,
)
from market_data_platform.integrations.qlib import QlibPublishedAssetAdapter

contract = PublishedAssetContract.load_current(
    "/data/market-data-platform",
    market="a_share",
)
plan = PublishedFramePlan(
    frames=(
        ParquetFrameMapping(
            asset_key="flow_ownership_features",
            relative_path="data",
            datetime_column="trade_date",
            instrument_column="symbol",
            columns={
                "northbound_net_amount_20d": "northbound_net_amount_20d",
                "margin_balance_change_5d": "margin_balance_change_5d",
            },
            column_group="feature",
        ),
    ),
    calendar=TradingCalendarMapping(
        asset_key="trade_cal",
        datetime_column="cal_date",
        open_column="is_open",
        open_values=(1,),
    ),
    universe=PITUniverseMapping(
        asset_key="pit_universe",
        relative_path="data",
        datetime_column="trade_date",
        instrument_column="symbol",
        membership_column="selected",
        included_values=(True,),
    ),
)

adapter = QlibPublishedAssetAdapter(contract, plan)
native_frame = adapter.load(start_time="2024-01-01", end_time="2024-12-31")
qlib_loader = adapter.as_data_loader()
qlib_frame = qlib_loader.load(start_time="2024-01-01", end_time="2024-12-31")
metadata = qlib_loader.dataset_metadata
```

The native frame and Qlib DataLoader return the same `(datetime, instrument)` MultiIndex frame. Output columns use a `(column_group, output_name)` MultiIndex. The adapter does not infer aliases for `date`, `symbol`, `selected`, or feature columns; schema mismatches fail explicitly.

`load(instruments=...)` accepts an explicit sequence of security codes or a Qlib-style `{instrument: [(valid_from, valid_to), ...]}` mapping. A string market name requires Qlib provider resolution and is rejected by this read-only adapter; callers must pass the resolved security set.

`dataset_metadata` is a plain dictionary containing the adapter name and version, source backend, current-contract SHA-256, each source manifest SHA-256 and normalized content fingerprint, data cutoff and lineage, plus the explicit frame/calendar/universe mappings and their configuration SHA-256. Research artifacts can store this metadata without serializing Qlib objects. The platform continues to own ingestion, PIT semantics, quality checks, asset promotion, and current pointers; the adapter only reads published assets.

The standard `dev` gate does not install `pyqlib`. It checks native DataFrame equivalence, delayed import, and clear missing-dependency errors; real Qlib runtime tests are skipped. To test an installed Qlib runtime:

```bash
uv sync --locked --extra dev --extra qlib
uv run --locked --extra dev --extra qlib python -m pytest \
  tests/test_published_assets.py -k qlib -q
```

## Planned execution-cost model

`execution_cost_model` is a reserved derived-asset key supported by path conventions and the current data contract. The platform does not yet provide a production builder. A future lightweight derived asset should record its calibration window, tick/intraday source dependencies, usable universe, assumptions for spread, book depth, participation rate, market impact and data quality, as-of date, and version. Strategy code should not read raw tick Parquet directly.
