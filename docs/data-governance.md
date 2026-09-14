# 数据目录与生命周期治理

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

本页说明共享数据根目录的渐进治理规则。现有路径继续兼容，治理工具先形成可审计的声明和 dry-run 报告，再由人工确认后处理退役候选。

## 控制面文件

共享数据根中的机器可读清单位于：

```text
<artifacts_root>/metadata/lifecycle/inventory.json
```

当前 schema 为 `market_data_platform.lifecycle_inventory.v1`。每个对象分别记录：

- `tier`：`raw`、`derived`、`state`、`run` 或 `archive`。
- `role`：current、rollback、pilot 等操作角色。
- `lifecycle_state`：active、published、superseded 等生命周期状态。
- `disposition`：`keep`、`retire_candidate` 或 `review`。

这些维度独立表达。`superseded` 仍可保持 `keep`，`retire_candidate` 也只进入检查清单。

新写入链路逐步采用以下逻辑结构：

```text
raw/<provider>/<dataset>/<version>
derived/<dataset>/<version>
state/<workflow>/<run_id>
runs/<workflow>/<run_id>
archive/<dataset>/<version>
```

数据迁移使用实体目录和实体文件。迁移完成后，清单、回执和运行配置中的绝对路径也要同步更新，避免继续指向旧数据根目录。

`current`、`latest` 和 `rollback` 是版本入口。它们目前仍按现有发布链使用软链接，不能和历史数据迁移混在一起批量替换。若要取消这些软链接，需要先完成发布程序、调度任务和回滚脚本的单独改造。

## 代码生命周期分层

平台代码按数据生命周期逐步收敛到以下依赖方向：

```text
provider API
  -> ingest
  -> raw immutable asset
  -> standardize
  -> standardized / canonical asset
  -> quality receipt
  -> publish
  -> published asset
```

`market_data_platform.ingest` 负责 provider 接入、请求可靠性和 raw landing。`market_data_platform.standardize` 负责字段映射、类型转换、去重、排序、时间处理和来源融合。quality receipt 负责可用性判断。发布层负责版本、alias、manifest 和 provenance。模型 window、label、embedding 和训练样本不进入平台标准化层。

首批迁移把 TuShare A 股 `daily_clean` 的 build 实现和 daily schema 移到 `standardize`。旧 `providers.tushare_a_share_clean` 保留兼容入口。quality validation 继续由现有 quality 实现负责。`ingest.tushare.daily` 先提供稳定的新入口，底层 provider runtime 后续分批内迁。

架构门禁禁止 `standardize` 回依赖 `providers` 或 `ingest` 实现。这样 raw 接入和 canonical 转换可以独立演进，也避免 provider 目录继续吸收新的通用清洗逻辑。

## current 与 latest 约定

当前读取顺序如下：

1. 下游从 `metadata/current_assets/<market>_current.json` 获取已发布资产。
1. 数据契约（contract）中的 `alias_path` 是注册表（registry）和人工操作使用的稳定路径。
1. 新目录型发布采用不可变版本目录。稳定入口是否使用软链接，取决于下游程序是否支持直接读取清单。
1. 现有真实目录和普通文件形式的 `latest` 继续兼容，并由审计报告标记。
1. 数据契约（contract）中缺失的候选项继续参与健康检查，注册表（registry）会跳过 `exists=false` 的条目。

版本目录名称中的结束日期应与清单（manifest）的 `query_end_date` 一致。路径审计只对日期范围明确的资产执行该检查，当前覆盖 `daily`、`adj_factor`、`daily_basic`、`daily_clean` 和 `limit_status`。审计也会识别软链接最终落到可变 `latest` 目录的链式别名，以及 contract 记录与实际解析目标的差异。报告只提示问题，不会改名或重建资产。

运行审计：

```bash
marketdata governance audit-current-paths \
  --artifacts-root "$DATA_PLATFORM_ROOT"
```

写入审计文件：

```bash
marketdata governance audit-current-paths \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --out "$DATA_PLATFORM_ROOT/metadata/lifecycle/current-path-audit.json"
```

## inode 感知的退役规划

`plan-retention` 读取清单中的三类规则：

- `explicit_path`：明确保护、候选退役或人工复核一个路径。
- `retain_newest`：按名称中最右侧日期和 basename 排序，保留最新 N 个直接子目录。
- `json_status`：仅把顶层 `status` 命中白名单的 JSON 文件列为候选。

生成 TSV dry-run：

```bash
marketdata governance plan-retention \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --out "$DATA_PLATFORM_ROOT/metadata/retention/retention-dry-run.tsv" \
  --latest-link "$DATA_PLATFORM_ROOT/metadata/retention/governance-latest.tsv"
```

命令只读扫描资产并写报告，不提供删除、移动或重命名动作。现有 systemd retention 定时任务继续运行
窄策略。窄策略脚本现已由本仓库维护，并通过统一 renderer 注入 canonical data root 和本仓库的
`marketdata` CLI。治理规划与窄策略报告分别写入 `governance-latest.tsv` 和
`scheduled-latest.tsv`，不应将两种 schema 混用。

时间戳报告默认不可覆盖，`--latest-link` 也只会原子替换已有软链接。同名普通文件会触发拒绝。每次发布应使用新的时间戳文件名。

`governance-latest.tsv` 指向新版 dry-run。现有定时任务另写 `scheduled-latest.tsv`。`latest.tsv` 继续作为旧 schema 的兼容入口，定时任务完成后可能包含 `action=delete`。生命周期复核只使用 `governance-latest.tsv`。

### retention systemd 定时任务

从仓库模板渲染 retention unit，避免 systemd 继续引用 workspace 外的旧脚本：

```bash
uv run python scripts/operations/render_tushare_minute_campaign_units.py \
  --template-glob 'market-data-platform-retention.*' \
  --home "$HOME" \
  --mdp-dir "$MDP_DIR" \
  --data-platform-root "$DATA_PLATFORM_ROOT" \
  --campaign-manifest /dev/null \
  --logs-dir "$HOME/.hermes/logs" \
  --marketdata-cli "$MDP_DIR/.venv/bin/marketdata" \
  --output-dir "$HOME/.config/systemd/user"

systemctl --user daemon-reload
systemctl --user enable --now market-data-platform-retention.timer
```

正式迁移前应先执行 `market_data_platform_retention.sh dry-run`，并确认已有
`metadata/retention/governance-latest.tsv` 是软链接。普通文件不会被覆盖。应先人工备份并确认
其来源，再重新执行治理规划。

### A 股日报输入 snapshot

历史日报 replay 使用数据平台拥有的 immutable version 目录，不直接把当前
`*_latest` 入口当作历史输入。构建器只创建 symlink overlay，并写入
`a_share.report_input_snapshot.v1` receipt。源目录、版本日期和 `manifest.yml` hash
都会记录在 receipt 中。

```bash
uv run python scripts/operations/build_a_share_report_input_snapshot.py \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --target-date YYYYMMDD \
  --output-root /tmp/a-share-report-snapshot-YYYYMMDD \
  --dataset daily \
  --dataset daily_basic \
  --dataset adj_factor \
  --dataset limit_status \
  --dataset moneyflow_ths
```

如果任一显式要求的数据集没有覆盖目标日期的 completed immutable version，命令整体失败，
不会生成半成品 snapshot。snapshot receipt 只证明输入版本选择和 manifest 身份。它不把
缺失数据伪装成可用，也不替代下游对实际文件的校验。

### 第一批 current 目录版本化

第一批已将以下 7 个 TuShare A 股数据目录固化为带日期的实体目录：`broker_recommend`、`ths_index`、`stk_holdertrade`、`moneyflow_hsgt`、`limit_step`、`limit_cpt_list` 和 `limit_list_ths`。

迁移过程使用同一文件系统内的目录移动，并更新目录内的 `manifest.yml`。现阶段仍保留 `latest` 软链接供旧读取入口使用。发布程序切换到显式版本目录后，再移除这些兼容入口。

操作脚本支持先干运行：

```bash
uv run python scripts/operations/materialize_current_versions.py \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --fallback-date YYYYMMDD \
  --dry-run \
  --dataset DATASET
```

脚本会拒绝缺少清单、状态未完成或行数为零的目录。`hsgt_top10`、`margin` 和缺少清单的 `ths_member` 暂不纳入本批次。

第三批已按同一规则固化 `dc_concept`、`dc_concept_cons`、`flow_ownership_features`、`holder_structure_features`、`hotspot_features`、`kpl_concept_cons`、`kpl_list`、`report_rc`、`stk_surv`、`ths_hot` 和 `top_inst_events`。

`fund_portfolio_features` 已存在更新的实体版本，但当前入口仍指向较旧版本，待确认数据内容后再切换。`margin_detail` 和 `moneyflow_ths` 当前没有有效行，也暂不发布。

## staging 处理

`staging/` 只保存仍在构建、验证或等待发布结论的材料。完成归档时，直接把整个目录移到 `archive/staging/<日期>/`，不在原位置留下软链接。

移动前需要确认：

- 回执已经进入终态
- 目录中没有锁文件
- 没有运行中的任务或调度引用
- 已有正式产物或研究结果保存下来
- 清单、回执和报告中的路径已经改成新位置

不满足条件的目录继续保留在 `staging/`，并在 `staging/README.md` 中说明原因。处理历史数据根目录时，可使用 `scripts/operations/relocate_data_root.py` 批量改写文本元数据中的旧绝对路径。命令默认只输出检查结果，只有加入 `--apply` 才会写入。

已确认的目录可以使用 `scripts/operations/archive_staging.py` 直接归档。命令默认只检查，加入 `--apply` 后才会移动目录。命令会拒绝含有锁文件或软链接的目录。

TSV 记录以下容量口径：

- `logical_bytes`：路径下每个普通文件名的逻辑大小总和。
- `allocated_bytes`：按 `(device, inode)` 去重后的已分配块。
- `reclaimable_bytes`：该路径覆盖 inode 的全部硬链接时才计入。
- `external_hardlink_inodes`：仍有路径外硬链接的 inode 数量。

不同规则可能覆盖相同 inode，各行容量不可直接相加。任何实际退役动作都需要重新扫描，以处理报告生成后的链接变化。

规划器有以下保护边界：

- 从全部 current contract 收集 `alias_path` 和 `resolved_path`。
- 从清单收集 `disposition=keep` 的显式路径。
- 候选与保护路径存在父子或同一路径关系时强制 `keep`。
- 检查词法路径和软链接解析结果均处于 artifacts root 内。
- 禁止显式规则把整个 artifacts root 列为对象。
- 不跟随软链接，遇到候选内部跨文件系统目录时把退役候选降级为 `review`。

## 剩余 current 资产处理

`fund_portfolio_features` 已切换到已有的更新实体版本。`moneyflow_ths` 已完成实体版本化并保留兼容入口。

`hsgt_top10`、`margin` 和 `margin_detail` 当前清单返回零行，current contract 会将它们标记为暂不可用。`ths_member` 缺少 `manifest.yml`，会标记为待重新发布。

后续生成 current contract 时，目录型资产缺少 `manifest.yml` 会标记为待重新发布，清单中的 `totals.rows` 为零会标记为暂不可用，避免空结果或来源不明的目录进入正常数据链路。

供应端返回空结果时，保留历史目录并标记当前资产为暂不可用。若清单记录的文件数与目录中的 Parquet 文件数不一致，也会标记为需要一致性重发布。这样可以识别接口波动和刷新过程复用旧输出目录造成的混合数据。

## 当前分钟数据决策

现行清单保护以下对象：

- `minute_1m` current alias 与 `minute_1m_v3_20260714` 当前版本。
- `minute_1m_pre_v3_20260714` rollback alias 与 `minute_1m_v3_20260711` 上一版本。
- 更早的 `minute_1m_pre_v3_20260711` 与 v2 版本，等待成组归档复核。
- TuShare 原始分钟历史与 full-v1 快照。
- universe staging 审计轨迹和 reports。

`minute_1m_pilot_20260711` 已在 2026-07-14 完成 payload 退役，生命周期清单保留 tombstone，
coverage、构建 receipt 与 retirement receipt 继续作为审计证据。`minute_1m_pre_v2_20260710`
保持 `review`，需先确认来源关系或完成归档。TuShare Guan 替换 campaign 已完成 820 个可获得
交易日，并发布为 `minute_1m_tushare_candidate_v1_20260727` 独立候选。验收回执明确拒绝
canonical cutover，因此 current Guan 版本和 TuShare 候选都保持 `keep`，不得把前者标为
`superseded`。TuShare-native operational 晋级使用独立的 `minute_1m_tushare` alias 和
`minute_1m_tushare_v1_YYYYMMDD` 不可变版本。它不改变上述 canonical cutover 结论。当前
operational alias、解析版本、version receipt 和 promotion receipt 均应保持 `keep`。released
locks 与空 staging/tmp 由独立规则列出。complete deal checkpoints 保持 `review`，直到关联
manifest 与 rollback 证据成组归档。报告不会自动处理这些对象。

## 人工退役门槛

执行任何清理前需逐项确认：

1. action 为 `retire_candidate`。
1. 路径未被 current、latest 或 rollback 引用。
1. 相关进程未持有锁。
1. successor、manifest、coverage 和 cutover 证据完整。
1. 需要保留审计价值的元数据已经归档。
1. 临近执行时重新计算 inode 与可回收空间。
1. 获得明确人工批准。

当前实现停在 dry-run 阶段。
