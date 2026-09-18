# market-data-platform 文档与文风治理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 核对 `market-data-platform` 当前真实能力，补齐独立数据平台的架构说明，统一文档生命周期标记，并把面向人和 agent 的主要文档润色为自然、易读的中文。

**Architecture:** 保持 `market-data-platform` 作为独立数据平台，继续负责 provider、数据生产、PIT、质量治理、版本和 published asset。`quant-platform` 只消费稳定的数据资产和 contracts，`quant-research` 负责策略、ML 和研究证据。文档改造分为事实盘点、入口文档、专题文档、生命周期标记和自动检查五个边界，避免把历史记录误改成当前结论。

**Tech Stack:** Markdown、Python、pytest、Ruff、现有 `scripts/dev/architecture_governance.py`、`scripts/dev/compatibility_governance.py`、`tests/test_quality_governance.py`。

**Spec:** 本计划同时落实当前讨论的 `market-data-platform` 独立项目边界、`research-workspace` sunset 关系，以及中文文档风格要求。

**Existing Audit Inputs:** 另一条迁移工作流已经生成 `research-workspace` 侧的 `migration_parity_audit.py`、`quant-supersession-parity-2026-09-06.json`、组件审查和 supersession ownership 矩阵，并在 `quant-research` worktree 中补充了 `availability_lag_days` 的代码修复与回归测试。本计划把这些结果作为输入，不重复生成同一份量化仓 parity 审计。

## Global Constraints

- 不删除历史文档、历史命令或历史实验结果。
- 不改变公开 CLI、Python API、数据路径、资产键、schema、receipt 和测试所验证的行为。
- `market-data-platform` 保持独立项目，不迁入 `quant-platform`。
- provider、数据生产、质量治理和 published asset 由本项目负责。
- 策略专属规则、ML 特征和研究证据归 `quant-research`。
- 通用回测、组合、风险和执行模拟归 `quant-platform`。
- 只有已经确认过时且存在明确替代页面的文档才标记为 `status: superseded`。
- 归档文档保留历史语气，并标记为 `historical` 或 `archived`，不强行改写成当前说明。
- 中文正文使用中文标点，保留必要的命令、路径、配置键、包名和 API 名称。
- 默认不使用双引号、粗体、分号和破折号，避免翻译腔、过度术语和“不是……而是……”句式。
- 每一批文档改动都运行对应的文档、架构和测试检查。
- 多 agent 并行时，每项改动使用独立 worktree 和功能分支，经过 PR 合并到 `main` 后删除旧分支和 worktree。

## 文件边界

### 新增

- `docs/superpowers/plans/2026-09-06-market-data-platform-documentation-refresh.md`：本实施计划。
- `docs/architecture/quant-repo-boundaries.md`：`market-data-platform`、`quant-platform`、`quant-research` 和 `research-workspace` 的职责与依赖方向。
- `docs/documentation-style.md`：中文文档风格、状态标记和历史文档处理规则。

### 修改

- `README.md`：新人入口、当前能力、独立项目定位、快速开始和文档阅读路径。
- `AGENTS.md`：agent 的迁移边界、事实核对顺序、文档风格和禁止事项。
- `docs/README.md`：按新人路径、架构、数据契约、操作、治理和历史记录重新组织索引。
- `docs/contracts.md`、`docs/data-lifecycle-architecture.md`、`docs/integrations.md`、`docs/ownership-migration.md`：补充数据平台 owner、发布资产消费方式和下游边界。
- `docs/operations.md`、`docs/operations/testing.md`、`docs/quality-governance.md`、`docs/compatibility.md`：统一当前命令、门禁、兼容层和生命周期用语。
- `docs/*.md` 和 `docs/**/*.md` 中被事实审计确认需要更新的专题文档：按主题分批润色，不一次性重写所有历史正文。
- `tests/test_quality_governance.py` 或新增 `tests/test_documentation_style.py`：增加状态标记、关键链接、中文标点和禁用文风回归检查。
- `scripts/dev/architecture_governance.py`、`scripts/dev/compatibility_governance.py`：仅在现有治理入口无法覆盖新规则时扩展，不复制一套独立检查器。

## 实施任务

### Task 0：接入已有迁移审计结果

- [x] 阅读 `research-workspace/docs/evidence/quant-supersession-component-review-2026-09-06.md`、`quant-supersession-parity-2026-09-06.json`、`docs/governance/documentation-supersession-matrix.md` 和 `docs/governance/supersession-ownership-matrix.md`。
- [x] 阅读 `research-workspace/scripts/migration_parity_audit.py` 及其 3 个测试，确认审计工具是只读 inventory，不把它误当成 `market-data-platform` 的事实来源。
- [x] 核对 `quant-research` worktree 中 `availability_lag_days` 的修复、回归测试和 96 个现金流/CNI 测试结果，文档只记录已经验证的事实，不复制未合入的代码。
- [x] 将已有审计中的组件 owner、文档迁移状态和 34 个待人工确认差异加入本计划的事实输入清单。
- [x] 明确本项目本轮只处理 `market-data-platform` 文档和治理规则，不替代另一条工作流对 `quant-research` 的代码 parity 审查。

验收：本轮文档审查使用同一个迁移事实基线，明确已完成、待审核和本轮不处理的范围。

### Task 1：建立 market-data-platform 事实清单与文档分类

- [ ] 盘点源码、CLI、数据路径、published asset、provider、PIT、质量治理和当前测试覆盖的真实能力，并与已有迁移审计中的 owner 矩阵交叉核对。
- [ ] 对每个 active 文档记录 owner、主题、事实来源、当前状态和替代页面。
- [ ] 将文档分为 `active`、`migration-only`、`historical`、`archived` 和 `superseded`。
- [ ] 找出已经被新目录或新命令替代的页面，确认每个 `superseded` 页面都有唯一的 `superseded_by` 目标。
- [ ] 找出 README、AGENTS、docs 索引、测试和代码之间的事实冲突，形成修改清单。对外部迁移审计中仍待人工确认的差异保留待审状态，不直接改写成结论。

验收：形成一份只在本次工作区使用的事实矩阵，所有计划中的文档修改都有明确原因和代码或测试依据。

### Task 2：固定跨仓库架构边界

- [x] 新增 `docs/architecture/quant-repo-boundaries.md`。
- [ ] 明确 `market-data-platform` 独立负责 provider、raw 到 clean、PIT、DQ、lineage、版本和发布。
- [ ] 明确 `quant-platform` 负责通用回测、组合、风险、成本、容量、执行模拟和消费侧 contracts。
- [ ] 明确 `quant-research` 负责策略、特征、ML、实验和研究证据。
- [ ] 明确 `research-workspace` 进入 sunset，只保留集成、迁移和历史导航职责。
- [ ] 明确依赖方向只能通过 published asset、manifest、receipt、schema 或公开 API 连接，不允许下游导入数据平台内部实现。

验收：架构文档中的每项职责都能在当前源码、测试或现有发布流程中找到事实依据，且没有把 `market-data-platform` 写成 `quant-platform` 的子模块。

### Task 3：重写新人入口文档

- [ ] 重写根目录 `README.md` 的开头和阅读路径，让第一次接触项目的人先理解项目用途、当前市场范围、稳定数据入口和最小检查。
- [ ] 重写 `AGENTS.md` 的迁移通知、上下文阅读顺序、文档规则和验证要求。
- [x] 在 `AGENTS.md` 中明确并行 agent 的 worktree、分支、PR、合并和清理流程。
- [ ] 重写 `docs/README.md` 的推荐阅读顺序和主题索引，区分当前能力、操作指南、架构说明和历史资料。
- [ ] 将复杂技术细节移动到对应 `docs/` 页面，入口文档只保留定位、边界、命令和导航。
- [x] 新增 `docs/documentation-style.md`，规定中文标点、术语、代码引用、状态元数据和历史文档处理方式。

验收：新人只读 `README.md` 和 `docs/README.md` 就能回答项目负责什么、数据从哪里来、如何运行最小检查、下游如何消费和下一步该读什么。

### Task 4：按事实分批润色专题文档

- [ ] 先处理数据契约、生命周期和下游接入文档，确保架构边界一致。
- [ ] 再处理操作、测试、质量治理和兼容层文档，核对每个命令的参数和当前状态。
- [ ] 最后处理研究特征、分钟数据、数据仓库和 provider 专题，保留必要的技术细节。
- [ ] 每篇文档清理翻译腔、过长句、无必要的否定前置、重复解释和中英混杂表达。
- [ ] 将中文正文中的半角括号、逗号、冒号、句号和问号替换为中文标点，代码、路径和命令块保持原样。
- [ ] 对已失效页面添加统一元数据：`status: superseded`、`superseded_by`、`owner`、`last_verified`，正文只保留短迁移指针。
- [ ] 对历史记录添加 `historical` 或 `archived` 状态，保留原始事实和日期，不把历史判断改写成当前结论。

验收：主动文档都能找到当前 owner 和事实来源，失效文档都有可用替代入口，历史材料不会被误读为当前能力。

### Task 5：补齐文档治理测试

- [x] 先在测试中定义 active 文档必须拥有状态元数据、有效 `superseded_by`、必要索引链接和有效文件目标。
- [x] 增加对根 README、AGENTS 和 `docs/README.md` 的入口检查。
- [ ] 增加对明显英文翻译腔、半角中文标点、连续双引号、粗体、分号、破折号和“不是……而是……”模式的报告或阻断规则。
- [ ] 规则对代码块、URL、行内代码、历史归档和引用保留例外，避免误报。
- [ ] 检查文档中的命令和路径是否仍然出现在代码、测试或当前文档索引中。
- [x] 复用现有 `test_quality_governance.py` 的扫描入口，只有在职责明显增加时才拆出 `tests/test_documentation_style.py`。

验收：文档风格回归检查可以稳定发现新增违规，但不会阻断合法的命令、路径、API 名称和历史归档。

### Task 6：完整验证与迁移说明

- [ ] 运行文档相关测试和当前质量治理测试。
- [ ] 运行架构治理、兼容性治理、Ruff、格式检查、类型检查和完整 pytest。
- [ ] 扫描所有 Markdown 链接，修复入口文档和 `superseded_by` 指向的失效路径。
- [ ] 检查 `git diff --check`，确认没有半角标点、空格或误改代码内容。
- [ ] 输出文档变更清单，区分事实修正、结构调整、文风润色和 supersede 标记。
- [ ] 单独记录尚未核实的事实，不用猜测补全项目能力。

验收：所有声明都能由新鲜命令结果、当前代码或测试支持，文档改动不改变运行行为。

## 建议执行顺序

先完成 Task 1 和 Task 2，再处理 Task 3。Task 3 稳定后按 Task 4 分批修改。Task 5 应在第一批入口文档修改前先写出最小回归规则，Task 6 在全部文档修改结束后执行。

第一阶段先接入已有迁移审计结果，再处理 `market-data-platform`。确认边界和文风规则稳定后，再把同一套状态标记和中文文档约定同步到 `quant-research`、`quant-platform` 以及 `research-workspace` 的迁移说明。已有的 `availability_lag_days` 修复和量化仓 parity 结论由原工作流继续负责，本计划只引用其证据。
