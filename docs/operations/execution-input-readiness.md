# 执行研究输入审计

[English](execution-input-readiness.en.md)

先核对停复牌和公司行动，再解释成交与持仓收益。此入口复用已有本地资产，输出独立研究切片及 `readiness.json`，不发布 `current`。它不下载数据，也不替代逐股票、逐交易日的覆盖检查。

```bash
uv run --extra tushare python scripts/operations/audit_execution_inputs.py \
  --suspension "$SUSPEND_HISTORY" "$SUSPEND_EXTENSION" \
  --dividend "$DIVIDEND_COMPACT" \
  --symbols "$RESEARCH_SYMBOLS" \
  --current-assets "$CURRENT_ASSETS" \
  --window y2025:20250101:20251231 \
  --window q1_2025:20250101:20250331 \
  --out-dir "$TASK_DATA_ROOT/audit-v1"
```

参数引用仓库外的 Parquet、股票代码文本文件和当前资产清单。股票代码文件每行一个 TuShare 代码。不传 `--symbols` 时保留全体源记录。每个 `--window` 指定名称、开始日期和结束日期，可以重复。输出目录必须为空。重跑使用新目录。`--current-assets` 可选，只统计日分区存在性。

每个窗口输出 `suspend_d.parquet`、`dividend_all_stages.parquet`、`dividend_implemented.parquet` 和 `dividend_events_ready.parquet`。公司行动按公告、实施公告、登记、除权、支付或红股上市中任一日期落入窗口选取，保留旧报告期在窗口内发生的事件。实施表只保留 `div_proc` 去除首尾空格后为`实施`的记录，去掉完全相同的重复行。不同金额等版本仍保留，并标记 `event_key_conflict`。消费者应核查冲突，不能直接累加。全阶段表保留原始记录。

`readiness.json` 记录输入与输出 SHA-256、行数、原始重复、实施阶段缺失日期、所选股票缺失以及字段单位。所选股票没有记录无法证明没有公司行动，需进一步检查下载状态与数据源。历史记录是 `reconstructed_pit_not_revision_safe`。以今天下载的实施进度不能证明过去可见的版本。预测可用性应检查实施公告日，现金到账与红股可交易应分别检查支付日和红股上市日。

[TuShare dividend 文档](https://tushare.pro/document/2?doc_id=103) 将现金字段定义为每股分红，将送转字段定义为每股比例。`cash_div` 为税后字段，`cash_div_tax` 为税前字段，实际税负需由下游账户规则处理。[停复牌文档](https://tushare.pro/document/2?doc_id=214) 定义 `S` 为停牌、`R` 为复牌，`suspend_timing` 为日内时间段。该接口没有历史可见时间戳。日期是生效信息，不能证明前一交易日已经可知。分红接口不覆盖配股等全部公司行动。分钟行情也不能提供 L2 排队、盘口深度或真实价差。

补抓使用现有 `download-a-share-constraint-reference` 或 `download-a-share-fundamentals --dataset dividend`，通过项目 `config run` 注入配置，先 canary，输出到任务专属路径。不得默认切换共享别名。

`dividend_events_ready` 按窗口内除权日选取已实施事件，按 `ts_code` 和 `ex_date` 核查经济字段。同一事件仅公告日不同且其他字段相同时可以折叠，保留 `source_ann_dates` 和 `source_versions`。金额、股数、报告期或经济日期冲突的事件留在原始表，写入 `event_resolution.blocked_events`。实施公告日和登记日缺失、现金或送转比例未知、正现金缺支付日、正送转缺上市日也阻断。不会把现金空值推断为零，也不会推断未知日期。`available_from` 等于已提供的实施公告日，历史仍为重建PIT。
