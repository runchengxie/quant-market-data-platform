# AGENTS.md

## 迁移通知

`research-workspace` 处于 sunset 过渡期。`market-data-platform` 保持独立，继续负责数据接入、生产、质量治理、版本和 published asset。通用回测、组合、风险和执行模拟进入 `quant-platform`，策略专属派生进入 `quant-research`。

## 并行开发流程

多个 agent 同时工作时，每项改动都必须使用独立 worktree 和功能分支。标准流程如下：

1. 从 `origin/main` 创建 worktree 和功能分支。
2. 在该 worktree 中完成修改和本地门禁。
3. 提交并推送功能分支，创建 PR。
4. PR 合并到 `main` 后，删除远端旧分支和本地功能分支。
5. 删除已经完成的 worktree。

不要让多个 agent 直接修改同一个工作树，也不要在 `main` 上直接提交。跨仓库改动先完成 owner 仓库的 PR，再更新上游仓库中的 gitlink 或迁移说明。

本文件说明 `market-data-platform` 的协作边界。

## 仓库职责

本仓库维护共享市场数据控制面，负责：

- 数据资产标识和目录规范
- 数据清单、当前数据契约和数据集注册表
- A 股 TuShare 数据源入口（RQData 已于 2026-07-26 随 A 股入口退役）
- 原始层、清洗层、股票池和当前数据发布
- Guan 与 TuShare 分钟数据构建、覆盖审计和当前别名切换
- 基本面 PIT、行业、资金流、热点和持仓类特征
- 标准层、查询、备份和数据治理工具
- 港股历史资产的冻结与恢复

策略研究、组合回测和券商执行由其他仓库维护。

## 外部框架边界

Qlib 当前只通过 `market_data_platform.integrations.qlib` 提供条件化只读 DataLoader
适配器。常规开发依赖和标准门禁不安装 `pyqlib`。核心数据契约、生产命令和发布链路应保持
可独立导入和运行。

修改 Qlib 适配器时，除常规门禁外还要运行真实运行时定点测试：

```bash
uv sync --locked --extra dev --extra qlib
uv run --locked --extra dev --extra qlib python -m pytest \
  tests/test_published_assets.py -k qlib -q
```

DataHandler、模型训练、实验记录和回测适配器属于下游研究仓库。本仓库只维护已发布数据资产的
只读映射。

## 数据管理

- 不提交 Parquet、压缩分卷、数据源缓存、运行输出、报告或凭证。
- 大体量数据放在共享数据根目录、NAS、对象存储或 Release 附件。
- Git 只保存代码、文档、结构定义、小型测试数据和迁移记录。
- 全市场长周期任务使用分区扫描、分批处理、临时目录和显式内存保护。
- 资源默认值在运行时探测，不把维护者个人机器配置写成项目要求。
- 写入任务应记录输入、解析后的资源配置和产物血缘。
- 分钟数据重建写入新的版本目录。`assets/derived/a_share/minute_1m` 只在覆盖检查凭证通过后切换。
- 分钟 Parquet 不含来源列。需要区分 Guan annual、Guan deal 和 TuShare 时，按交易日连接覆盖检查凭证。

## 当前市场边界

A 股是当前活跃主线。下游系统只读消费本仓发布的数据资产。

港股生产模块与 RQData 已随 2026-07-26 退役。`freeze-hk`、`hydrate-hk` 等迁移命令与
`metadata/current_assets/hk_current.json` 契约均已移除，恢复历史资产使用
`hk-freeze-20260613` 标签或私有归档仓库。新增港股生产逻辑前需要单独评估归档边界和
维护责任。

## 环境和凭证

```bash
uv sync --extra dev
```

真实凭证放在未跟踪的 `.env.local`、本地 `.env` 或个人 secret 文件中。文档只记录变量名和配置方式。

TuShare 凭证与自定义 API 地址的规则见 `docs/operations/credentials.md`。不要读取、打印或提交 token。

## 测试和质量检查

```bash
uv run --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run --extra dev python -m ruff check .
uv run --extra dev python -m ruff format --check .
uv run --extra dev ty check --error-on-warning
uv run --extra dev python scripts/dev/quality_debt.py
uv run --extra dev python scripts/dev/maintainability_metrics.py
uv run --extra dev python scripts/dev/compatibility_governance.py --check
uv run --extra dev python scripts/dev/architecture_governance.py --check
```

`ty` 的配置范围合并了迁移前的日常检查与发布检查文件。发布检查沿用同一套类型配置。

需要 DuckDB 时额外安装：

```bash
uv sync --extra dev --extra duckdb
```

## GitHub Actions 策略

工作区统一采用以下默认规则：

- public 仓库默认启用 GitHub Actions，用于拉取请求的轻量自动检查。
- private 仓库默认关闭 GitHub Actions，避免持续占用私有仓库的 Actions 额度。
- private 仓库如需启用远端 CI，应在仓库文档中记录原因、检查范围和资源成本，并由维护者明确批准。
- 本地完整门禁继续由仓库自身检查和工作区共享 `pre-push` 承担。

本仓库是 public 仓库，默认运行 GitHub Actions。公开 workflow 只运行无 secrets、无生产数据的静态检查、契约检查和 fixture 测试。需要真实凭证或生产数据的检查不得从 pull request workflow 获取。完整本地门禁仍由仓库自身检查和共享 `pre-push` 承担。

公开化后的旧实现、真实部署配置和内部运行记录位于 private 的
`quant-market-data-platform-archive` 与 `quant-market-data-deploy`。本仓库是唯一活跃的业务代码来源，deploy 通过不可变 release/tag 或 commit 使用本仓库，不在 private 仓库维护业务代码副本。

## 修改规则

- 公开 CLI 变更同步更新 `docs/operations.md` 和行为测试。
- 数据契约变更同步更新 `docs/contracts.md`、清单代码和契约测试。
- 数据源行为变更补充重试、分页、限额和异常路径测试。
- 大文件处理补充分批、内存和中断恢复测试。
- 保持公开路径、资产键和命令稳定。
- 已完成的交接材料放入 `docs/archive/`。
- 更新维护性 baseline 时记录原因和退出条件。

## 文档风格

- 中文正文使用自然、直接的表达和中文标点。
- 保留必要的命令、路径、配置键和 API 名称。
- 用户文档聚焦当前能力、输入、命令和输出。
- 历史背景与操作说明分开保存。
- 文档中的命令和字段应由代码或测试支持。

## Git

本仓可能由多个 agent 并行开发。每个改动都必须使用独立 worktree 与功能分支，避免
多个 agent 在同一检出目录竞争同一组文件。

远端常驻分支只有 `main`。功能分支（`feat/*`、`fix/*`、`hotfix/*`、`release/*`）
只用于拉取请求流程、临时存在。每个改动遵循以下顺序：

1. 从 `origin/main` 新建 worktree 与功能分支：

   ```bash
   git fetch origin
   git worktree add <path> -b feat/<主题> origin/main
   ```

2. 在独立 worktree 内完成改动，提交前运行本地门禁。
3. 提交并推送功能分支：

   ```bash
   git push -u origin feat/<主题>
   ```

4. 用 `gh pr create` 开拉取请求，合并到 `main`。
5. 合并完成后删除功能分支并移除 worktree：

   ```bash
   git push origin --delete feat/<主题>
   git branch -d feat/<主题>
   git worktree remove <path>
   ```

本仓提交推送合并完成后，再回到 `research-workspace` 更新 gitlink。
本仓作为 `research-workspace` 子模块工作时，`core.hooksPath` 指向工作区共享的
`.githooks`。共享 pre-push 会校验分支、提交、工作树和仓库本地门禁。同一仓库的多个
worktree 共享主工作树的 hook 配置，不要在独立 worktree 内重装或改写 hook。安装或
修复 hook 使用工作区根目录的安装脚本，不在子模块内复制另一套 hook。新的并行任务
必须新建 worktree，不要直接在主检出目录的 `main` 上提交改动。

## Worktree-first 目录规范

开发代码使用 `.worktrees/` 下的独立 worktree。数据生产和 systemd 任务必须
使用 `<production-checkout>` 或明确的版本化
生产检出，不得引用开发 worktree 或只保留元数据的仓库根目录。数据、receipt、缓存和运行日志
继续放在 `$DATA_PLATFORM_ROOT` 或用户级状态目录。更新生产版本前先完成
本地质量门禁，再刷新服务配置和 systemd daemon。
