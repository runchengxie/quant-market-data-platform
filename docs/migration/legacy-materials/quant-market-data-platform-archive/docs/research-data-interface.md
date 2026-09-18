# 研究数据读取适配器

`market_data_platform.research_data_interface.ResearchDataInterface` 为研究应用提供统一的数据读取入口。
它负责读取市场数据平台发布的资产，也支持读取已经固定下来的本地研究资产。

## 支持的读取方式

`data.source_mode=platform_assets` 使用市场数据平台的公开数据接口。数据提供方、缓存和数据契约
由市场数据平台管理，适合研究应用读取已发布资产。

`data.provider=local_artifact` 配合 `data.source_mode=fixed_scored_artifact` 使用本地固定文件。
支持 `.parquet`、`.csv`、`.json` 和 `.jsonl`。文件需要包含 `symbol` 与 `trade_date`，也可以使用
`ts_code`、`order_book_id`、`stock_ticker` 作为证券代码列，使用 `date` 作为日期列。

旧的在线服务商直接读取模式已经停用。研究应用应先将数据整理并发布到市场数据平台，或者明确指定
一个固定的本地资产。

## 使用示例

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

适配器不会创建服务商客户端。实际数据访问由市场数据平台的公开接口负责，研究层只需要提供
市场、配置和缓存目录。
