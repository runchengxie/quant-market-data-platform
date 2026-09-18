# 系统集成

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

本页说明下游研究、回测、交易和报表系统如何接入本平台发布的数据资产。下游系统应把本平台视为只读数据源，不需要了解平台内部的采集、清洗和发布实现。

## 下游系统接入边界

推荐定位：
* 下游系统负责自己的策略研究、特征工程、模型构建、回测、持仓管理或报告生成。
* 下游系统仅作为已发布数据资产的只读调用方。
* 中国大陆市场数据的基础采集入口位于本仓库，当前以 TuShare 与 Guan 分钟数据为主。
* 中国香港市场支持已随 RQData 一起退役，不再提供只读消费入口。

环境配置：

```bash
export DATA_PLATFORM_ROOT=/data/market-data-platform
```

这样配置可将下游系统的运行结果、缓存和报告输出保留在自身项目目录，同时将市场数据输入路径指向共享的数据根目录。下游项目如有自己的输出根目录配置，应只在确实需要把运行产物也写入平台根目录时使用。

覆盖默认输出路径：

```yaml
paths:
  artifacts_root: "/data/market-data-platform"
```

数据调用规范：
* 推荐通过 `metadata/current_assets/<market>_current.json` 结合各项资产的 manifest 清单文件来读取数据。
* A 股分钟数据当前从 `assets/derived/a_share/minute_1m` 读取，并同时冻结 coverage receipt。分钟 Parquet 不含来源列，来源分层需要按交易日连接 receipt 的 `daily` 数组。
* ETF 公共源分钟数据从单独的 `assets/derived/a_share/etf_minute_<period>m/<version>` 读取。下游必须读取该版本的 `manifest.yml` 和 `receipt.json`，不能把它和 `minute_1m` 的全市场股票覆盖混用。
* 严禁直接依赖（或硬编码）其他项目的工作目录。
* 运行低频策略时，请勿直接全量扫描原始的 Tick 级深度快照数据。
* 研究系统应把 PIT universe、PIT fundamentals、历史行业和逐日估值 overlay 的边界按
  [research-integrity.md](research-integrity.md) 记录到自己的研究证据中。

---

## Qlib 只读 DataLoader 适配器

### 当前支持范围

当前实现只把已发布的 Parquet 资产映射为 Qlib DataLoader，覆盖显式列映射、交易日历、
逐日 PIT 股票池、日期与证券过滤，以及可序列化的数据血缘。DataHandler、Dataset、
模型训练、实验记录和回测后端不在本仓库的支持范围内。

Qlib 接入为可选依赖：

```bash
uv sync --locked --extra qlib
```

核心包、数据生产命令和当前数据契约发布链路不会导入 Qlib。只有调用
`QlibPublishedAssetAdapter.as_data_loader()` 时才加载 `pyqlib`。

读取计划必须显式声明每个 Parquet 路径和列映射。下面的例子假设当前数据契约已
登记 `pit_universe` Parquet 资产，并同时应用交易日历和逐日 PIT 股票池。股票池只匹配
同一交易日，不做向前填充：

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

`native_frame` 与 Qlib DataLoader 返回同一份 `(datetime, instrument)` MultiIndex frame。
输出列使用 `(column_group, output_name)` MultiIndex。适配器不会推断 `date`、`symbol`、
`selected` 或特征列的别名。schema 不匹配会直接失败。

`load(instruments=...)` 接受显式证券代码序列，或 Qlib 风格的
`{instrument: [(valid_from, valid_to), ...]}` 映射。字符串 market 名称需要 Qlib provider
解析，当前只读适配器会拒绝该输入，调用方应传入已经解析的证券集合。

`dataset_metadata` 使用普通字典，记录：

- Qlib 适配器名称和版本
- 已发布 Parquet 的来源后端
- 当前数据契约的 SHA-256
- 每个源资产清单的 SHA-256、规范化内容指纹、数据截止时间和数据血缘
- 显式 frame、calendar、universe 映射及其配置 SHA-256

上述元数据可写入研究实验产物，无需序列化 Qlib 对象。平台继续维护采集、PIT 语义、
质量校验、资产晋升和当前指针。适配器仅消费已发布数据。

标准 `dev` 门禁不安装 `pyqlib`，因此会覆盖原生 DataFrame 等价性、延迟导入和缺失依赖
报错，真实 Qlib 运行时用例会跳过。需要验证真实 DataLoader 时运行：

```bash
uv sync --locked --extra dev --extra qlib
uv run --locked --extra dev --extra qlib python -m pytest \
  tests/test_published_assets.py -k qlib -q
```

---


## 规划中的交易成本模型

`execution_cost_model` 当前是预留的衍生资产键，路径规范和当前数据契约已支持登记。
正式构建流程尚未在平台内落地。后续交易成本模型应作为轻量级衍生数据资产提供，策略层
不应直接读取底层 Tick Parquet 文件。该模型资产需明确记录以下元数据信息：

* 模型校准窗口期（calibration window）
* 数据源依赖（所依赖的 Tick 级深度数据和日内数据资产）
* 适用的股票池和投资域（usable universe）
* 核心假设条件（包括买卖价差、盘口深度、成交参与率、市场冲击以及数据质量的预设假设）
* 数据截止日期（as-of date）与版本号
