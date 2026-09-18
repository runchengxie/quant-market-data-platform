# `market-data-platform` 中的公共源 ETF 分钟数据

## 状态

这是首个集成切片的已批准方向。独立的 `etf-minute-fetcher` 仓库保持不变，直到输出一致性验证完成。

## 目标

将公共源 ETF 分钟数据获取能力作为可选 provider 纳入 `market-data-platform`，保留现有分钟数据 schema，并让 ETF 分钟资产与全 A 股股票分钟资产分开存储。

## 决策

1. provider 放在 `market_data_platform.providers` 下，因为它负责获取和规范化市场数据，不负责计算指标或提供 dashboard。
2. `akshare` 作为可选依赖。平台核心导入路径和常规质量检查不能依赖它。系统 `curl` 仍作为 Eastmoney、Sina 直接回退路径的运行时前置条件，并在文档中明确说明。
3. 首个发布的数据集使用独立的 ETF 资产族，不得更新包含全 A 股股票覆盖且完整性口径不同的 `assets/derived/a_share/minute_1m`。
4. 数据写入调用方提供的 `DATA_PLATFORM_ROOT`，使用不可变版本目录。首个切片可以不提供兼容 alias。即使提供，也不得替换非符号链接。
5. 每次运行都写入机器可读的 manifest 和 receipt，记录请求的股票和日期、选定数据源、数据源回退路径、行数和分区总数、空日期、各分区哈希以及 schema 身份。
6. parity 测试期间，`etf-min` 命令继续作为参考实现。平台命令通过 fixture 和线上 smoke 检查后，再决定是否提供兼容包装器。

## 数据契约

Parquet 数据列保持为：

```text
ts_code, trade_time, open, close, high, low, vol, amount
```

`trade_time` 是 Asia/Shanghai 时区下的无时区本地时间戳，与平台现有分钟数据契约一致。`amount` 允许为空，因为 Sina 回退路径不提供成交额。资产元数据标记 instrument family 为 ETF。provider 来源记录在 receipt 中，不增加 Parquet 列。

首批支持 1、5、15、30、60 分钟周期。每个周期都是独立的数据集和版本身份，读取方不能无感混用不同周期。公共源 1 分钟数据只覆盖近期窗口，不代表完整历史数据库。

## Provider 边界

provider 分为三层：

- 纯规范化层，将 Eastmoney、AKShare、Sina 数据框映射为稳定的八列 DataFrame
- 数据源选择层，实现 `auto`、`eastmoney`、`sina`，包含重试和直接 curl 回退
- 编排层，解析请求分区，原子写入每个分区，并生成 manifest、receipt

provider 不得导入 dashboard 代码、研究代码或第三方框架对象作为跨仓库契约的一部分。

## CLI 和存储

平台命令是 `marketdata data` 下的子命令，接受显式股票、日期范围、周期、数据源、资产根目录、输出版本和跳过已有分区等参数。命令输出 JSON 兼容的摘要。请求的所有分区都失败，或写入后留下失败 receipt 时，命令必须返回非零状态。

默认存储路径为：

```text
$DATA_PLATFORM_ROOT/assets/derived/a_share/etf_minute_<period>m/<version>/
```

分区路径为：

```text
trade_date=YYYYMMDD/part-00000.parquet
```

manifest 记录准确的版本目录，读取方不得根据约定推导版本目录。每次写入先写入临时文件，再通过原子替换完成。

## 完整性和回执

每个分区的 receipt 至少记录：请求股票、交易日期、周期、数据源、实际行数、空日期、输出路径、输出哈希和 schema 哈希。manifest 记录所有分区 receipt 的汇总以及运行配置。

允许空日期，但必须明确记录为空日期的原因。部分成功的运行可以发布已成功的分区，但 manifest 必须将失败分区和可重试状态写清楚。任何失败都不能覆盖现有版本。

## 兼容和后续工作

平台实现保持 `etf-minute-fetcher` 的输出列和日期语义，parity 验证完成前不修改独立仓库。平台命令稳定后，再单独评估兼容包装器、alias 和历史数据扩展。
