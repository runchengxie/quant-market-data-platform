# Market Data Platform Maintainability Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 降低市场数据平台高复杂度编排代码的维护成本，同时保持 CLI、公开函数、数据契约、receipt 和生产路径兼容。

**Architecture:** 先把长函数中的校验、路径解析和状态转换提取为小型纯函数，再用不可变配置对象承载重复参数。旧入口继续保留为薄 facade，所有行为变化必须由现有回归测试锁定。

**Tech Stack:** Python 3.11+, pytest, ruff, ty, uv, MkDocs。

**Spec:** `docs/architecture/quant-repo-boundaries.md`、`AGENTS.md`。

## Global Constraints

- 不改变公开 CLI 命令、参数默认值、返回 payload、数据 schema、manifest 和 receipt 字段。
- 不复制业务代码到 private deploy。deploy 只维护生产配置、systemd 模板、pin 和回滚工具。
- 每个阶段从 `origin/main` 建独立 worktree，完成本地门禁后提交 PR。
- 生产数据、凭证和运行产物不进入 Git。
- 重构完成前保持现有 `# noqa` 和 `ty: ignore` 行为，新增忽略必须有对应测试与说明。

### Task 1: Cutover orchestration boundaries

**Files:**
- Modify: `scripts/operations/cutover_a_share_minute.py`
- Modify: `tests/test_cutover_a_share_minute.py`

**Interfaces:**
- 保留 `cutover_minute_current(...)` 的现有签名与返回值。
- 新增内部纯函数 `_validate_cutover_inputs(...)`、`_build_cutover_plan(...)`，只接收显式参数并返回结构化结果。

- [ ] Step 1: 为已有成功、缺少 receipt、BJ overlay 不完整和 dry-run 场景补充行为锁定测试。
- [ ] Step 2: 运行 `uv run --locked --extra dev pytest tests/test_cutover_a_share_minute.py -q`，确认基线。
- [ ] Step 3: 提取输入校验和计划构建逻辑，保持文件移动、alias 切换和 rollback 顺序不变。
- [ ] Step 4: 运行该测试文件、`ruff check` 和 `ty check`。
- [ ] Step 5: 提交 `refactor: split minute cutover validation from execution`。

### Task 2: Campaign configuration object

**Files:**
- Modify: `src/market_data_platform/_campaign_run.py`
- Modify: `src/market_data_platform/_campaign_supervisor.py`
- Modify: `src/market_data_platform/_tushare_minute_quota_config.py`
- Test: `tests/test_tushare_minute_replacement_campaign.py`
- Test: `tests/test_tushare_minute_quota.py`

**Interfaces:**
- 新增只读 `CampaignRuntimeConfig`，集中承载 quota、窗口、heartbeat 和 blocker 配置。
- 保留现有 CLI parser 和 `run_budgeted` 调用入口，由 adapter 将 argparse Namespace 转换为配置对象。

- [ ] Step 1: 用现有 CLI fixture 覆盖默认值、显式值、忽略 blocker 和 quota safety 值。
- [ ] Step 2: 运行相关测试确认基线。
- [ ] Step 3: 将 supervisor 与 runner 的重复参数改为读取配置对象。
- [ ] Step 4: 运行相关测试、ruff、ty，检查无新增忽略。
- [ ] Step 5: 提交 `refactor: centralize campaign runtime configuration`。

### Task 3: Complexity and lint debt register

**Files:**
- Modify: `scripts/dev/maintainability_metrics.py`
- Modify: `scripts/dev/quality_debt.py`
- Modify: `docs/quality-governance.md`
- Test: `tests/test_quality_governance.py`

- [ ] Step 1: 为函数行数、参数数量和 noqa 数量输出稳定 JSON 字段并补测试。
- [ ] Step 2: 将 facade re-export 的 F401 与真实 dead code 分开统计。
- [ ] Step 3: 在质量文档记录当前基线和每阶段下降目标，不放宽现有门禁。
- [ ] Step 4: 运行治理测试和全部静态检查。
- [ ] Step 5: 提交 `chore: make maintainability debt measurable`。

### Task 4: Private deploy verification

**Files:**
- Modify: `quant-market-data-deploy/bin/verify_public_pin.sh`
- Modify: `quant-market-data-deploy/README.md`
- Test: shell-level dry-run using `config/production.env.example`

- [ ] Step 1: 增加 public repository、immutable SHA、production path 和 rollback pin 的 fail-closed 检查。
- [ ] Step 2: 用示例配置运行 dry-run，确认不会读取真实 secrets。
- [ ] Step 3: 更新 deploy 文档，说明 public CI、private CI 和本地验证边界。
- [ ] Step 4: 提交独立 deploy PR 并合并。

### Task 5: Final verification and cleanup

- [ ] Step 1: 运行 `uv run --locked --extra dev pytest -q`、ruff、ty、quality debt、architecture governance。
- [ ] Step 2: 检查 public MkDocs `uv run mkdocs build --strict`。
- [ ] Step 3: 检查 public 与 deploy 主 checkout 干净，旧任务 branch/worktree 已删除。
- [ ] Step 4: 记录剩余高复杂度函数和明确不删除的一次性历史脚本。
