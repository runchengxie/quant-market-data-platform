# TuShare 分钟数据、Doctor 与热点报告上线计划

目标：修复近期 TuShare 分钟数据尾部，支持可恢复的历史隔夜任务，通过 doctor 和 watchdog 暴露运行状态，并让报告热点产物使用 `dc_concept` 数据源，同时保持 DailyWatch20 候选池契约不变。

架构：TuShare 仍然是独立的数据生产方，发布前必须通过回执检查。第一阶段只发布 `minute_1m_tushare`，生产别名 `minute_1m` 等后续策略审批后再切换。market-intel 消费正式的 `topic_summary.json`，报告的主数据源为 `dc_concept`。当前 DailyWatch20 策略仍必须使用 `ths_hot_strict_v3`。

技术栈：Python、Parquet、JSON 回执、systemd 用户单元、pytest、uv。

设计依据：`docs/superpowers/specs/2026-08-31-tushare-minute-authority-design.md`

## 全局约束

- 本次上线不得删除数据，也不得切换 `minute_1m` 别名。
- 下载不完整或失败时，不得推进别名。
- 每个已发布的 TuShare 日期都必须具备完整的 241 根 K 线 sidecar 和完整的预期股票池。
- 热点替换只作用于报告主题产物，DailyWatch20 继续使用 `ths_hot_strict_v3`。
- 不得打印或提交 API token。
- 必须保留现有 worktree 中未提交的改动。

## 任务 1：审计当前分钟数据状态和数据源下限

文件：

- 新建：`scripts/operations/audit_tushare_minute_availability.py`
- 测试：`tests/test_audit_tushare_minute_availability.py`
- 修改：`docs/operations/a-share-minutes.md`

接口要求：

- 生成 JSON 审计结果，包含探测日期、每个股票的行数、确认的最早日期，以及结果属于数据源可用性证据还是仅属于股票级证据。
- 不得写入数据资产或修改别名。

执行项：

- [ ] 为日期探测、241 行校验和空响应无法形成结论的情况编写测试。
- [ ] 先运行目标测试，确认实现前测试失败。
- [ ] 使用配置中的 API URL 和具有代表性的沪、深、北交所股票，实现不泄露 token 的探测器。
- [ ] 在稳定的 MDP 环境中运行探测，只记录状态、行数和日期。
- [ ] 根据观察到的下限证据及其局限，更新运维文档。
- [ ] 运行目标测试和 `git diff --check`。
- [ ] 提交：`feat: audit tushare minute availability`。

## 任务 2：从 20260708 起修复 TuShare 尾部数据

文件：

- 修改：`scripts/operations/tushare_minute_operational_daily.py`
- 修改：`src/market_data_platform/tushare_minute_operational_daily.py`
- 测试：`tests/` 下现有的分钟数据运维测试
- 仅在验证完成后，在 `/home/richard/data/quant/market-data-platform` 下新建不可变版本和回执

接口要求：

- 读取现有的 `minute_1m_tushare` 版本及其回执。
- 生成新的不可变 TuShare 版本，并以原子方式推进 `minute_1m_tushare`。

执行项：

- [ ] 增加 dry-run 或计划测试，证明日期选择来自当前回执，并且只选择 20260708 之后的日期。
- [ ] 先运行测试，确认当前行为确实不足，或确认现有行为已经满足要求。
- [ ] 如果确有问题，只修复最小的日期选择或配置问题，不得绕过完整性检查。
- [ ] 通过用户 systemd 服务注入 `TUSHARE_TOKEN_2` 和稳定的 API URL，过程中不得暴露值。
- [ ] 在运行时间受限的条件下执行尾部更新，并检查生成的回执。
- [ ] 验证日期连续性、241 根 K 线 sidecar、完整股票池和别名目标。
- [ ] 运行相关 pytest 测试，并记录无法获取的日期，禁止发布这些日期。

## 任务 3：恢复一次受控的历史隔夜任务

文件：

- 只有审计确认需要时，才修改 `scripts/operations/tushare_minute_replacement_campaign.py`
- 通过仓库渲染器修改 `~/.config/systemd/user` 下生成的用户 systemd 单元
- 修改：`scripts/systemd/README.md`
- 测试：任务规划器和配额测试

接口要求：

- 读取任务 1 确认的最早日期、交易日历和股票池。
- 生成可恢复的暂存分区和回执，禁止直接写入 `minute_1m` 或 `minute_1m_tushare`。

执行项：

- [ ] 停用互相竞争的任务版本，同时保留它们的清单和回执。
- [ ] 使用现有配额台账、批次大小 33、硬停止时间和可恢复 sidecar，生成一个隔夜单元。
- [ ] 先执行受控的试运行窗口，确认部分结果不会被发布。
- [ ] 试运行通过后再启用任务。
- [ ] 在任务状态回执中记录进度和最早成功日期。
- [ ] 验证 systemd 单元语法和定时器状态。

## 任务 4：让 doctor 和 watchdog 校验分钟数据生产方及报告产物

文件：

- 修改：`market-intel/src/ops_common/business_freshness.py`
- 修改：`market-intel/src/a_share_daily/deploy_check/delivery_checks.py`
- 修改：`market-intel/src/a_share_daily/deploy_check/__init__.py`
- 修改：`market-intel/src/a_share_daily/deploy_check/constants.py`
- 测试：`market-intel/tests/test_a_share_deploy_check.py`
- 测试：`business_freshness.py` 附近的 freshness 和 watchdog 测试

接口要求：

- Doctor 分别报告 TuShare 运维新鲜度、历史任务健康度、六张图报告完整性和主题产物来源。
- 检查结果必须区分生产必需输入和可选的降级报告输入。

执行项：

- [ ] 为 TuShare systemd 服务失败、运维回执过期、主题产物缺失和六张图组合不完整增加失败测试。
- [ ] 实现结构化检查，输出可操作的路径，但不得输出秘密值。
- [ ] 必需生产方失败时返回非零状态，并生成 watchdog 可以读取的输出。
- [ ] 历史回补进行期间保持可选，当前交易日的运维数据缺失或过期仍属于必需错误。
- [ ] 对生产环境运行 doctor，确认结果与实际状态一致。

## 任务 5：将 `dc_concept` 固定为报告热点数据源

文件：

- 修改：`market-intel/src/a_share_daily/topic_summary_fallback.py`
- 修改：`market-intel/src/a_share_daily/pipeline.py`
- 修改：`market-intel/src/a_share_daily/topic_summary.py`
- 修改：`market-intel/src/a_share_daily/deploy_check/delivery_checks.py`
- 测试：主题摘要和 pipeline 测试

接口要求：

- 生成 `source="dc_concept"`、`degraded=false` 并包含明确输入来源的 `topic_summary.json`。
- 报告产物使用 `dc_concept`、`dc_concept_cons`、`limit_list_ths` 和 `moneyflow_ths`。
- 不得修改 `ths_hot_strict_v3` 候选池输入。

执行项：

- [ ] 增加失败测试，要求正式主题产物将 `dc_concept` 标记为主来源。
- [ ] 将该数据源实现为常规报告路径，不再作为 THS 降级路径。
- [ ] 保留来源日期校验，并在产物中记录缺失输入的原因。
- [ ] 只移除主题图表对 `ths_hot` 的依赖，保留策略新鲜度检查。
- [ ] 运行主题、pipeline、交付检查和 doctor 测试。
- [ ] 针对最近可用日期生成不发送的产物，并检查其来源信息。

## 任务 6：冷存储和最终验证

文件：

- 修改：`docs/operations/a-share-minutes.md`
- 修改：`scripts/systemd/README.md`
- 在数据元数据目录下新建归档清单和验证回执

执行项：

- [ ] 清点当前启用的别名、保留的回滚版本、暂存根目录和已停用的单元。
- [ ] 在执行任何保留清理前，确认选定的历史数据已经由 restic 备份。
- [ ] 本次上线不删除旧数据，清理工作另行审批。
- [ ] 运行完整的目标测试、doctor、systemd 校验和报告 dry-run。
- [ ] 汇总当前启用的输入、警告和剩余的策略切换门槛。
