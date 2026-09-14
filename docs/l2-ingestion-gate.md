# L2 数据接入门禁和数据集契约

Level-2 原始文件保持不可变。新增分区先经过画像分析、分类和机器可读门禁，门禁通过后下游研究才能把它视为生产数据。

## 数据集契约

使用 `market_data_platform.dataset_contract.v1` 记录仅凭 Parquet 类型无法安全推断的语义，包括数据集和供应商身份、资产 schema 版本、主键、时区和时间含义、单位、空值和哨兵值含义、频率、PIT 政策、事件顺序以及数据集专用质量政策。

交易所事件数据集必须明确记录排序规则。例如：

```json
{
  "schema_version": "market_data_platform.dataset_contract.v1",
  "dataset": {
    "id": "cn_a_share_l2_order",
    "provider": "vendor_x",
    "market": "a_share",
    "schema_version": "vendor_x.order.v3",
    "data_version": "20260830"
  },
  "primary_key": ["trading_day", "symbol", "channel", "sequence"],
  "time": {
    "timezone": "Asia/Shanghai",
    "event_time_semantics": "exchange_generated"
  },
  "units": {"price": "fen", "volume": "shares"},
  "null_semantics": {"Price": {"nullable": true}},
  "sentinels": {
    "Price": [{"raw_value": -999999999, "meaning": "protocol_no_value"}]
  },
  "ordering": {
    "exchange_sequence_available": true,
    "channel_column": "ChannelNo",
    "sequence_column": "ApplSeqNum",
    "cross_channel_total_order": false
  },
  "expected_cadence": {"type": "trading_day"},
  "quality_rules": {
    "null_values": "ignore",
    "exchange_sequence_gaps": "research_only",
    "exchange_sequence_duplicate": "quarantine"
  },
  "pit": {"applicable": false},
  "metadata": {}
}
```

契约只有在明确写出序列列名后，才能声明存在交易所序列。除非源协议本身保证跨频道总顺序，否则跨频道全序必须保持为 false。质量规则覆盖值可以使用 `ignore`、`research_only` 或 `quarantine`。这样，快照契约可以容忍结构上有效的空深度档位，同时不会放松序列或身份检查。

## 排序来源

`marketdata quality profile` 和可恢复扫描器会识别常见的原始字段别名，包括 `ChannelNo`、`ApplSeqNum`、`BizIndex`、`OrderTime`、`DealTime` 和 `TickTime`。

存在序列时，报告会统计 `(channel, sequence)` 重复、序列倒退、非数字序列值、缺口和跟踪截断的数量上限。缺口属于证据，不会自动认定为数据损坏，因为供应商分区可能有意省略其他消息类型。没有序列时，报告会标记为 `timestamp_fallback`。此时，时间戳加源文件行顺序只是有文档记录的回退方案，不能宣称与交易所精确排序一致。

## 每日或按分区执行门禁

新增原始文件到达后运行：

```bash
marketdata quality gate \
  --root /path/to/raw-l2/day=20260830 \
  --checkpoint /path/to/state/l2-20260830.checkpoint.json \
  --dataset-id cn_a_share_l2 \
  --provider vendor_x \
  --dataset-contract /path/to/contracts/cn_a_share_l2.json \
  --pilot-manifest /path/to/pilot/manifest.json \
  --scan-output /path/to/reports/l2-20260830.scan.json \
  --output /path/to/reports/l2-20260830.dq.json
```

该命令复用 `quality scan`，未变化的文件可以从 checkpoint 直接复用。命令不会修复、删除或重写 Parquet 原始文件，也不会移动发布别名。

默认准入政策：

- `quarantine`：必需列缺失、交易日不匹配、交易所序列重复、倒退或非数字，以及 pilot 中标记为 `exclude` 的行。
- `research_only`：序列缺口或跟踪截断、时间戳倒退、事件 ID 重复、空值或非正值，以及 pilot 中标记为 `tag` 的行。
- `production`：不存在上述问题。

如果数据语义有充分理由，数据集契约可以收紧或放宽单项检查的严重级别。DQ 回执会同时保留每项检查的默认 `severity` 和由契约解析出的 `effective_severity`，确保例外仍可审计。

输出使用 `market_data_platform.dq_receipt.v1`，包含输入摘要、检查结果、血缘、状态、准入结论和耗时。下游系统应直接使用 `eligibility` 字段，不要对同一组结构性问题另行解释。

## 与其他质量工具的关系

`quality integrity` 继续检查成交记录对订单 ID 的引用。`quality_opening` 继续负责与市场无关的开盘账本核算。交易所专用截断、滞后选择、快照对齐、模型输入检查、数据泄漏测试和 alpha 验证仍由下游负责。
