# Unified Data Quality Contracts and Gates Design

## Goal

让市场数据质量决策能够机器读取，并可复用于 L2、分钟和基本面流程，同时保留仓库现有的清单、当前资产契约和供应商专项校验。

## Boundaries

- 原始输入保持不可变。
- 现有供应商清单和当前资产契约继续作为资产发布的权威来源。
- 新数据集契约描述身份、单位、时间、空值或哨兵含义、顺序、频率、质量策略和 PIT 策略。
- 新数据质量回执描述一次质量决策，包括输入、检查、血缘、结果、准入资格和时间信息。
- L2 结构扫描继续由 `quality.py` 和 `quality_scan.py` 负责。新门禁消费这些报告，不重复执行扫描。
- L2 规范试点输出继续采用低复制和稀疏标签方式。
- 模型、标签、泄漏、alpha 和组合检查继续放在下游。

## Dataset contract

`market_data_platform.dataset_contract.v1` 是一个轻量 JSON 封装，包含数据集身份和版本、主键、时间和单位、空值或哨兵语义、顺序、频率、质量规则、PIT 策略和供应商元数据。声称具备交易所序列信息的契约必须明确序列列名。契约在写入前完成校验。

质量规则覆盖只接受 `ignore`、`research_only` 或 `quarantine`。默认检查等级与实际生效等级分开记录，确保数据集专项例外仍然可审计。这套机制用于结构上有效的空快照深度等语义例外，不能用来隐藏无法解释的数据损坏。

## DQ receipt

`market_data_platform.dq_receipt.v1` 统一数据集身份和版本、运行 ID、输入摘要、质量检查、血缘、状态、准入资格和时间信息。准入等级单调递减：`quarantine` 高于 `research_only`，`research_only` 高于 `production`。

## L2 ordering provenance

原始 L2 分析会识别常见的通道别名（`ChannelNo`、`Channel` 等）和序列别名（`ApplSeqNum`、`BizIndex`、`SeqNum` 等）。存在序列列时，分析会记录非数字行、重复的 `(channel, sequence)` 行、逆向移动、序列缺口、缺口总跨度和跟踪截断等有界指标。

缺口属于证据，不能自动视为损坏，因为供应商分区可能有意省略其他消息类型。交易所序列重复、逆向或非数字属于隔离等级。只有在数据集契约证明完整性语义后，序列缺口才能改变处理等级。没有序列时，报告明确记录 `timestamp_fallback` 和 `timestamp_then_source_order`。

## L2 ingestion gate

`marketdata quality gate` 运行现有的可恢复目录扫描，并将结果转换为一份数据质量回执。默认策略如下：

- quarantine: missing required columns, trading-day mismatch, exchange-sequence duplicate/backwards/non-numeric, pilot excluded rows;
- research-only: sequence gaps/truncated tracking, timestamp reversals, duplicate event IDs, nulls, non-positive values, pilot tagged rows;
- production: none of the above.

数据集契约可以通过 `quality_rules` 收紧或放宽单项检查等级。门禁不会修改原始输入，也不会移动发布别名。

## Fundamentals observation cadence

不可变的基本面核心归档仍然保存完整的观测版本快照，但打包的 systemd 定时器改为每日运行。归档记录 `observation_frequency=daily`，命令行允许为其他通道显式指定频率。这会提高修订观测粒度，但不会削弱现有的哈希、封存和 PIT 校验流程。

每日观测不代表具备日内修订安全性。首次观测版本之前的历史时期仍属于重建 PIT。

## Downstream ordering

深度学习模拟器继续使用时间戳作为跨通道时间坐标。原始转换保留可选的交易所通道或序列字段时，只在同一时间戳分组内、非快照事件最多属于一个通道且全部具备序列时使用序列排序。多通道分组保留源顺序。模拟器不会声称存在跨通道的交易所全序。

## Testing

- Pure contract/receipt/gate/ordering logic is covered without PyArrow.
- Existing PyArrow quality tests gain raw snapshot aliases and sequence metrics.
- CLI tests cover `quality gate`.
- Fundamentals archive and systemd tests assert daily observation metadata.
- Deep-learning tests cover ordering-column detection, sequence ordering, and cross-channel fallback.
