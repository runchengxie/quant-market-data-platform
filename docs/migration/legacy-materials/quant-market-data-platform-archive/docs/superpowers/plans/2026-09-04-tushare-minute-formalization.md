# TuShare Minute Formalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 验收并正式发布 2022-07-15 至今的 TuShare-native A 股分钟资产，同时保留 Guan legacy 回滚入口。

**Architecture:** 以现有不可变 TuShare 版本和 promotion receipt 为输入，补齐统一的质量、覆盖和开盘 bar 检查；验收通过后注册正式数据集并原子移动 TuShare canonical alias，不修改 Guan alias。下游分钟特征和回测使用显式 provider 版本完成一次重新基线。

**Tech Stack:** Python、Parquet、JSON receipts、DuckDB/Polars、pytest、现有 marketdata CLI 与 systemd operational daily service。

**Spec:** `docs/superpowers/specs/2026-08-31-tushare-minute-authority-design.md` and `docs/superpowers/specs/2026-09-03-a-share-minute-ingest-standardize-design.md`

## Global Constraints

- 不把 TuShare 与 Guan 宣称为数值等价来源；Guan `minute_1m` 必须保持可回滚且不被原子切换修改。
- 所有正式版本写入不可变版本目录，alias 只在 receipt 通过后原子移动。
- 每日完整性要求动态股票池、唯一键、有效 schema、241 分钟网格和可审计 provider-no-data 例外。
- token 值不得写入代码、receipt 或日志。

### Task 1: 验收现有 TuShare-native 版本

**Files:**
- Modify: `src/market_data_platform/` 中现有分钟质量/receipt 模块（沿用已存在边界）
- Test: `tests/test_tushare_minute_*`, `tests/test_minute_*`
- Verify: `/home/richard/data/quant/market-data-platform/metadata/minute_operational/versions/minute_1m_tushare_v1_20260903.json`

- [ ] 读取当前版本 receipt，验证日期范围、分区数量、市场范围和所有分区 SHA-256。
- [ ] 增加/补齐每个 symbol-day 的 241 grid、09:30 bar、重复主键、OHLCV 和 provider exception 检查。
- [ ] 用代表性日期和股票做 Guan/TuShare 分层差异报告，明确差异而不是将其视为失败。
- [ ] 运行对应 pytest、ruff、ty，并生成可追溯验收 receipt。

### Task 2: 注册正式 TuShare 数据集并完成 alias 晋级

**Files:**
- Modify: `metadata/current_assets/a_share_current.json`、dataset registry 与对应发布模块
- Test: `tests/test_published_assets.py`, `tests/test_dataset_contracts.py`, `tests/test_tushare_platform_assets.py`
- Docs: `docs/operations/a-share-minutes.md`, `docs/contracts.md`

- [ ] 将验收 receipt、版本路径、provider 和 coverage 纳入正式 registry/current contract。
- [ ] 发布新的不可变正式版本；promotion receipt 同时记录 TuShare alias 新目标和 Guan alias 原目标。
- [ ] 原子移动 `minute_1m_tushare`，确认 `minute_1m` 仍解析到 Guan legacy。
- [ ] 用只读查询确认下游能按 provider 显式选择，并完成发布文档。

### Task 3: TuShare 下游重新基线

**Files:**
- Modify: 下游分钟特征/读取配置的 provider 显式选择处
- Test: 对应分钟特征、回测和 PIT/coverage tests
- Docs: `docs/operations/a-share-minutes.md`

- [ ] 用同一冻结样本和 receipt 重建分钟特征。
- [ ] 对比 Guan/TuShare 的 IC、Top20 标签收益、换手、组合收益和缺失率。
- [ ] 若切换 TuShare 为默认研究入口，记录模型重训和风控阈值重校准结果；否则保留双入口。
- [ ] 运行发布前完整质量门禁并保存报告。
