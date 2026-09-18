# Market Data 公开化三仓迁移设计

## 目标

将现有 private `quant-market-data-platform` 安全迁移为三个职责互斥的仓库：

- `quant-market-data-platform-archive` 保存原始 Git 历史、旧协作记录和内部资料，并在迁移完成后设为只读归档。
- 新的 public `quant-market-data-platform` 从经过审计的 clean-room snapshot 起步，成为业务代码、通用文档和公开 CI 的唯一来源。
- 新的 private `quant-market-data-deploy` 只保存真实环境配置、部署与回滚自动化、机器拓扑和仍有使用价值的内部运行记录。

迁移必须避免形成两套活跃业务代码，并确保任何阶段都能回到原 private 仓库恢复信息。

## 仓库边界

### Public platform

public 仓库拥有 `src/`、`tests/`、通用 CLI、数据契约、provider、无敏感信息的运维模板、示例配置和 GitHub Actions。真实 endpoint、token、个人路径、机器清单、生产数据、运行结果和内部变更记录不得进入该仓库。

自定义 TuShare 地址仅通过环境变量、配置文件或 CLI 参数传入。源码不提供维护者个人服务作为默认值。数据根目录使用显式配置或平台中性的用户级默认值。

### Private deploy

deploy 仓库拥有生产配置模板的实际取值、systemd 实例、部署与回滚脚本、健康检查、机器清单、数据盘与 secret 文件位置，以及仍需持续维护的生产操作说明。

deploy 仓库不得复制 public 仓库的 `src/` 或维护私有业务补丁。部署必须固定到 public 仓库的 release 或不可变 commit。业务修复先进入 public 仓库，再由 deploy 仓库升级固定版本。

### Private archive

archive 仓库保留迁移前的完整 Git 历史、PR、Issues、release 元数据和内部文档。迁移验收后将其设为 GitHub archived。除恢复或审计外，不在该仓库继续开发、部署或记录新的运行状态。

## Clean-room snapshot

public 仓库不继承原仓库的 `.git`、refs、tags、PR、Issues、Actions artifacts 或 release attachments。首个提交由脱敏基线的受控文件清单生成。

快照排除：

- `docs/archive/`
- `docs/superpowers/`
- 原始数据、缓存、报告、日志和运行产物
- 本地环境文件与凭证
- 任何 private deploy 专属配置

生成后在独立临时目录重新扫描文件内容和 Git objects。只有新 public 仓库自身的首个提交及后续提交属于公开历史。

## 脱敏行为

当前代码基线需要完成以下调整：

- 删除源码、脚本、示例、systemd 模板、活跃文档和测试中的个人域名默认值。
- 删除个人 home 路径和 private sibling 项目路径。
- 从公开快照排除内部 archive、plan 和 spec。
- 保留通用 endpoint 配置接口，并为缺少必要配置提供明确错误或安全 fallback。
- 暂不改变与 endpoint 无关的 quota 和调度语义。

行为变更先由测试定义。文档删除和纯文本脱敏通过受控扫描与快照审计验证。

## CI 与凭证边界

public pull request CI 不读取 secrets，仅运行静态检查、单元测试、fixture integration 和公开契约检查。来自 fork 的代码不得通过 `pull_request_target` 获取生产凭证。

需要真实 token 或生产数据的 smoke test、部署、健康检查和回滚属于 private deploy。若未来在 public 仓库运行受信任的 live integration，必须仅从受保护环境向可信分支或人工批准的任务下发 secrets。

## 迁移顺序

1. 在原 private 仓库的独立分支完成脱敏和测试，形成可公开基线。
2. 扫描当前文件、完整旧历史和 GitHub 外围内容，记录需要轮换或排除的项目。
3. 重命名原仓库为 `quant-market-data-platform-archive`，保留 private，并暂停自动任务。
4. 从脱敏基线导出不含 `.git` 的 clean-room snapshot。
5. 用 snapshot 创建 public `quant-market-data-platform`，运行全套无密钥门禁后推送首个提交。
6. 创建 private `quant-market-data-deploy`，只迁移仍在使用的部署内容，并固定 public 版本。
7. 验证 public CI、private 部署、生产健康和回滚路径。
8. 确认没有调用方继续引用旧仓库后，将 archive 仓库设为只读。

仓库重命名与新建之间存在短暂的名称切换窗口。切换前记录旧 remote URL、默认分支 SHA、仓库可见性和自动任务状态。任何关键验证失败时停止后续步骤，保留 archive 仓库和生产 checkout，不通过强推或历史覆盖修复迁移。

## 验证与验收

代码基线必须通过相关行为测试、仓库静态检查和可运行的全量测试。已知且与本任务无关的基线失败需要单独记录，不得伪装为本次验证通过。

public snapshot 必须满足：

- 受控扫描不再发现个人域名、个人路径或 private sibling 项目标识。
- Git 历史只有 clean-room 首个提交及其后提交。
- 默认 CI 不需要 secrets 或生产数据。
- 安装、导入、CLI 帮助和核心测试可在全新 checkout 中运行。

deploy 仓库必须满足：

- 不包含业务源码副本。
- 所有部署引用都固定到不可变 public 版本。
- secrets 仍位于 Git 外部。
- dry-run、健康检查和回滚说明可在目标环境使用。

archive 仓库只在 public 与 deploy 验收通过、生产调用方切换完成后设为只读。

## 非目标

本次迁移不重写原仓库历史，不把 public Actions 用作其他 private 项目的免费计算后端，不重新设计 market data 业务接口，也不顺带调整与脱敏无关的 quota、数据契约或生产调度策略。
