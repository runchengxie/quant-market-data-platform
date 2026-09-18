# 兼容层与清理计划

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

本页记录保留中的兼容入口、迁移入口和历史运行约定。每个兼容项都必须有明确用途、风险和清理条件。

## 公开 CLI 生命周期

`marketdata` parser 中可达的叶子命令属于公开 CLI 面，活跃文档必须写出完整
`marketdata ...` 命令路径。公开命令使用以下生命周期分类：

| 生命周期 | 含义 | 文档要求 |
| --- | --- | --- |
| active | 当前维护的正常平台工作流 | 写明命令路径、输入、输出和验证命令 |
| compatibility | 旧命令名、旧包名或旧 console script 的兼容入口 | 写明推荐替代、风险、测试和清理条件 |
| migration-only | 迁移、恢复或历史交接入口 | 写明适用场景、替代入口和退出条件 |
| deprecated | 已有替代入口，保留给过渡期使用 | 写明替代入口、审计证据和移除条件 |
| archival | 历史复现或发布归档入口 | 写明复现价值、验证证据和归档条件 |
| internal-only | 维护者本地修复、一次性导入或审计脚本 | 放在 `scripts/internal/`，不进入公开 `marketdata` parser |

新增数据生产、健康检查、当前数据刷新或发布流程应进入原生 provider 工作流。
一次性修复工具默认进入 `scripts/internal/`。如进入公开 CLI，必须在本页或对应 operations
文档中记录生命周期状态。

## 当前兼容项

`hkdata` console script、`hk_data_platform.*` Python 包名兼容层、
`rqdata-hk-depth` / `rqdata-tick` 和 `rqdata-hk-assets` 已在
2026-06-13 从活跃包移除。历史复现应使用 `hk-freeze-20260613` 标签或私有归档仓库。
新脚本只应使用 `marketdata` 和 `market_data_platform.*`。

下游研究仓库的数据目录与本地快照 wrapper 也已从活跃入口移除。标准层 catalog、
materialize、query 和数据快照统一使用 `marketdata data ...` 与
`marketdata backup-data`。策略研究入口统一使用 `strategy ...`。

| 兼容项 | 当前用途 | 风险 | 推荐替代 | 清理条件 | Owner | 当前状态 | 审计证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `marketdata migration freeze-hk` / `marketdata migration hydrate-hk` | 历史港股冷存储冻结与恢复入口（已退役） | 代码与命令已移除，旧文档引用会指向不存在的能力 | 历史复现改用 `hk-freeze-20260613` 标签或私有归档仓库 | 已随 RQData 完全退役，无需进一步移除 | removed | migration-only; removed; 2026-07-26 | `docs/operations/hk-archive-restore.md`（已退役） |

## 维护规则

1. 新代码使用 `marketdata`、`market_data_platform` 和
   `market_data_platform.providers.*`。
1. 新增兼容层时必须写入本表，说明用途和清理条件。
1. 迁移类命令不应继续承载新的业务能力。新能力应进入平台原生工作流。
1. 删除兼容项前先做 repo-local `rg` 审计，并确认下游脚本已经切换。
1. 下游研究仓库 wrapper 只用于兼容，不能承载新的下载、健康检查、
   当前数据刷新、registry 或数据资产发布实现。
1. 港股研究与 RQData 恢复能力已在 2026-07-26 完全退役，freeze / hydrate 命令已移除，历史复现改用 `hk-freeze-20260613` 标签或私有归档仓库。

## 一次性脚本与内部遗留

2026-07-02 的仓库内审计覆盖本仓源码、测试、脚本和相邻子模块引用，当时只发现本仓仍保留这些入口。
以下 legacy helper 已无源码、测试、脚本或相邻仓库调用，已从活跃包退役：
`config_utils.py`、`current_assets.py`、`deprecations.py`、`intraday_paths.py`、
`pit_feature_stats.py`、`rebalance.py` 和 `rqdata_cli_common.py`。

| 类型 | 模块 | 当前状态 | 处理策略 |
| --- | --- | --- | --- |
| archived script | `scripts/internal/archive/build_a_share_tushare_sw2021_industry_changes_20260603.py` | archival | 替代产物：`marketdata tushare download-a-share-industry-membership` |

## 静态检查债务

Ruff 覆盖全部源码。`ty` 覆盖 `pyproject.toml` 登记的类型边界，范围已经合并迁移前的日常检查与发布检查文件。常规阻塞检查使用 Ruff、`ty` 和 pytest：

```bash
uv run --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run python -m ruff check .
uv run python -m ruff format --check .
uv run ty check --error-on-warning
```

债务可见性检查不作为阻塞门禁，但每次重构前后都建议跑：

```bash
uv run --extra dev python scripts/dev/quality_debt.py
uv run --extra dev python scripts/dev/quality_debt.py --complexity
uv run --extra dev python scripts/dev/maintainability_metrics.py
uv run --extra dev python scripts/dev/compatibility_governance.py --check
uv run --extra dev python scripts/dev/architecture_governance.py --check
```

治理优先级：

1. 先恢复低风险文件的 Ruff 覆盖，避免继续扩大 `extend-exclude`。
1. 继续避免扩大 `extend-exclude`，新增覆盖优先改为 per-file ignore，并写清楚保留原因。
1. contracts、paths、manifest、registry 和当前数据契约等边界模块持续保持 `ty` 覆盖。
