# Research data interface

[中文页面](research-data-interface.md)

`market_data_platform.research_data_interface.ResearchDataInterface` provides a common data-reading interface for research applications. It reads platform-published assets and fixed local research assets.

## Supported modes

`data.source_mode=platform_assets` uses the data platform's public reader. The platform manages the provider, cache, and data contract; this mode is intended for published assets.

Use `data.provider=local_artifact` with `data.source_mode=fixed_scored_artifact` to read a fixed local file. Supported formats are `.parquet`, `.csv`, `.json`, and `.jsonl`. The file must contain `symbol` and `trade_date`; accepted security-code columns also include `ts_code`, `order_book_id`, and `stock_ticker`. The date column may be named `date`.

Direct reads from legacy online providers are disabled. Research applications should publish prepared data through the market-data platform or explicitly select a fixed local asset.

## Example

```python
from pathlib import Path

from market_data_platform.research_data_interface import ResearchDataInterface

interface = ResearchDataInterface(
    market="a_share",
    data_cfg={"provider": "tushare", "source_mode": "platform_assets"},
    cache_dir=Path("artifacts/cache"),
)
daily = interface.fetch_daily("600519.SH", "20250101", "20251231")
basic = interface.load_basic(["600519.SH"])
```

The adapter does not create a provider client. Actual access is handled by the platform's public interface; the research layer supplies the market, configuration, and cache directory.
