# TuShare Historical Minute Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复历史 campaign 的阻塞点，正式恢复夜间回填，从 2016-01-04 向前推进到 TuShare 实际可提供的最早日期。

**Architecture:** 以现有 1,588 个交易日计划为基础，先处理 stale readiness、canary provider-no-data 例外和 pandas 校验中断，再用 request-first quota ledger、可恢复 partial receipt 和每日状态服务驱动单 lane 夜间任务。所有历史结果留在 staging，直到逐日质量和全区间 coverage receipt 通过后才发布独立历史版本。

**Tech Stack:** 现有 `tushare_minute_replacement_campaign.py`、`tushare_minute_backfill.py`、TuShare `stk_mins`、JSON receipts、SQLite quota ledger、systemd user timers、pytest。

**Spec:** `docs/superpowers/specs/2026-08-31-tushare-minute-authority-design.md`

## Global Constraints

- 历史任务不写入 canonical alias，不与当前 2022-07-15 之后版本混拼。
- 每次请求使用 `TUSHARE_TOKEN_2` 对应 endpoint 和统一 request quota；token 值不得持久化。
- provider 返回 HTTP 成功但 0 bar 的股票必须进入明确 exception 清单，不能静默当作完整。
- 超过连续无进展阈值必须停止并报警；不得通过无限重试掩盖阻塞。

### Task 1: 修复并验证 campaign readiness 与状态恢复

**Files:**
- Modify: `scripts/operations/tushare_minute_replacement_campaign.py`
- Modify: `scripts/operations/_tushare_minute_quota_schedule_support.py`
- Test: `tests/test_tushare_minute_campaign_systemd.py`, `tests/test_tushare_minute_backfill.py`, quota tests

- [ ] 编写测试证明旧 readiness marker、manifest hash 和 endpoint 不匹配时会明确失败且不消耗下载额度。
- [ ] 将 2026-08-31 campaign manifest、provider exception receipt 和当前 endpoint 绑定为新的可验证 readiness。
- [ ] 编写恢复测试：从 partial receipt 继续时只请求缺失 symbol/date，并保持幂等。
- [ ] 验证 no-progress fuse、SIGINT hard-stop 和 status receipt 的状态转换。

### Task 2: 修复下载校验的性能/中断问题

**Files:**
- Modify: `src/market_data_platform/providers/tushare_a_share_mins.py`
- Modify: `src/market_data_platform/tushare_minute_backfill*.py`
- Test: `tests/test_minute_provider_exceptions_runtime.py`, `tests/test_tushare_minute_backfill.py`

- [ ] 为重复时间和分钟网格校验增加小型回归样本，复现当前 `pandas duplicated/floor` 长时间中断路径。
- [ ] 将校验改为分批/向量化且保持同等质量语义，保证 KeyboardInterrupt 后 receipt 可恢复。
- [ ] 运行单日、三日和 canary 级真实配置 dry-run；确认不修改生产 alias。

### Task 3: 小规模重启 canary 并确认最早可用边界

**Files:**
- Modify: campaign metadata under `/home/richard/data/quant/market-data-platform/metadata/minute_backfill/`（运行产物，不提交 Git）
- Docs: `docs/operations/a-share-minutes.md`

- [ ] 重新执行 2016-01-04 至 2016-01-11 canary，记录完整/partial/exception 股票数和 241-grid 覆盖。
- [ ] 对 2015 年及更早代表性日期做低成本探测；把“最早可用日期”与“全市场完整日期”分开记录。
- [ ] canary 通过后冻结新的 manifest hash、exception 清单和 quota policy。

### Task 4: 启用受控夜间回填和监控

**Files:**
- Modify: `scripts/systemd/tushare-minute-replacement-campaign*.timer`
- Modify: `scripts/systemd/tushare-minute-replacement-campaign*.service`
- Modify: `scripts/systemd/README.md`
- Test: systemd rendering and campaign scheduling tests

- [ ] 只启用一个主夜间 lane，保留独立 hard-stop；先用有限日期/行数预算观察两个 quota window。
- [ ] 确认 coordinator hold、release-ready、日志、OnFailure status service 和 no-progress alert 全部联通。
- [ ] 逐晚扩大日期窗口，按 receipt 统计完成交易日、symbol-day、rows、失败和剩余日期。
- [ ] 连续三个稳定窗口后再允许长期运行至 2016 起点，不启用重复旧 campaign。

### Task 5: 全区间验收和独立历史发布

**Files:**
- Modify: `scripts/operations/tushare_minute_candidate.py` 或现有历史组装入口
- Test: coverage/registry/published asset tests
- Docs: `docs/operations/a-share-minutes.md`, `docs/operations/a-share-tushare.md`

- [ ] 合并历史分区为新的不可变版本，逐文件校验 SHA-256、schema、241 grid、唯一键和 exception receipt。
- [ ] 生成 2016 起至当前的全区间 coverage receipt，明确 provider-no-data 日期/股票例外。
- [ ] 与当前 TuShare-native 版本按交易日连接，避免覆盖或重复写入 2022-07-15 之后分区。
- [ ] 历史 receipt 通过后发布独立历史 alias；最后再决定是否把该 alias 纳入正式 registry 的默认范围。
