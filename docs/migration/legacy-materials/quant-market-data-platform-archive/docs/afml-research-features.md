# AFML 研究数据特征

本页记录数据平台新增的 activity bars 和低频微观结构特征。数据平台只发布带版本、血缘和质量证据的资产，不评价策略 Sharpe、CPCV 或晋升状态。

## 安装

基础控制面安装继续保持轻量，不会因为可选研究特征而在根包 import 时强制加载 pandas。使用本页 API 时安装：

```bash
uv sync --extra research-features
```

并从显式模块导入：

```python
from market_data_platform.research_features import build_daily_microstructure_features
```

## 日频 OHLCV 可支持的特征

`market_data_platform.research_features.build_daily_microstructure_features` 只依赖当时可见的 OHLCV / amount 字段，生成：

- Parkinson high-low volatility
- Corwin-Schultz effective spread estimate
- Amihud illiquidity
- turnover shock

这些是低频流动性代理。它们不等同于真实盘口 spread、market impact 或 order-flow imbalance。

## Activity bars

`build_activity_bars` 支持：

- tick bars
- volume bars
- dollar/notional bars

命令行入口为 `marketdata research-features activity-bars`。例如：

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

输入必须是授权逐笔成交数据，至少包含：

```text
symbol
timestamp
price
volume
```

输出包含 bar start/end、OHLC、volume、notional、VWAP 和 trade count。时间戳统一解析为 timezone-aware UTC。

建议未来发布独立资产键：

```text
trades
volume_bars
dollar_bars
microstructure_features
```

这些资产不应混入 `daily_clean`。逐笔和日频资产具有不同的体量、修订、时区与质量门禁。

## 数据边界

没有逐笔方向、盘口或订单消息时，不发布或伪造：

- VPIN
- Kyle lambda
- order-flow imbalance
- depth-based price impact
- imbalance bars

当 provider 以后提供 aggressor side 或完整 order-book 数据时，可在独立 contract 中加入对应构建器和验证器。

## 发布 receipt

`feature_receipt` 记录：

- source contract
- as-of
- 行数
- feature columns
- feature SHA-256
- 各列 null ratio

下游研究应通过 current contract 或 registry 读取已发布特征，不能把临时缓存目录当作正式数据来源。

日频特征的命令行入口为 `marketdata research-features daily`。例如：

```bash
marketdata research-features daily \
  --input daily_ohlcv.parquet \
  --output daily_microstructure.parquet \
  --receipt daily_microstructure.receipt.json \
  --source-contract daily_clean.v1 \
  --asof 2026-07-14
```
