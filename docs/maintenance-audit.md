# 维护性审计快照

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-25
> source_of_truth: yes
> superseded_by: n/a

审计日期：2026-07-16

本页记录当前维护事实、已接受的技术债和下一轮重构优先级。数据来自本仓库治理脚本和 repo-local 搜索结果。

（2026-07-26 更新：RQData 与港股市场支持已完全退役，相关代码、`freeze-hk` / `hydrate-hk` 命令和 `cold_storage.py` 已删除。下文相关表述已同步。）

## 当前职责

本仓库维护中国大陆市场主线的数据控制面，当前包含：

- 数据契约、路径规范、资产键、manifest、数据集注册表和当前数据契约。
- 中国大陆市场 TuShare 基础镜像入口。
- Guan 与 TuShare A 股分钟数据构建、覆盖审计和原子切换。
- 标准层 catalog、materialize、DuckDB query 和本地 snapshot 备份入口。
- （港股历史资产的冷存储冻结 / 恢复入口已于 2026-07-26 随 RQData 退役。）
- `marketdata` 统一 CLI，以及保留中的归档和迁移面。

港股 provider 生产模块、港股 tick-depth 模块、港股发布预设、过渡调度入口和历史导入 wrapper 已从活跃主线移除。历史复现使用 `hk-freeze-20260613` 标签或私有归档仓库。恢复说明见 `docs/operations/hk-archive-restore.md`（已退役）。

## 生命周期分类

| 范围 | 分类 | 维护决策 |
| --- | --- | --- |
| `src/market_data_platform/contract.py`, `paths.py`, `manifest.py`, `registry.py`, `data_provider_contracts.py` | active | 平台核心边界，保持 Ruff 和 `ty` 覆盖 |
| `src/market_data_platform/providers/*`, `tushare_cli.py` | active | provider adapter 与 CLI parser，继续扩大类型覆盖 |
| `src/market_data_platform/cold_storage.py` | migration-only; removed | 已于 2026-07-26 随 RQData 退役，命令与文件已删除 |
| `scripts/dev/*` | active governance | 本地门禁使用的治理脚本，变更需配套测试 |
| `artifacts/`, `reports/`, `.pytest_cache/`, `.ruff_cache/`, `*.egg-info` | generated/cache | 不属于源码维护面，不提交 Git |

## 质量覆盖

港股 provider 生产目录移除后，Ruff 和 `ty` 的目录级 exclude 已收窄到非源码路径。核心重构面不再保留大文件级
exclude（`data_providers.py` 和 `data_warehouse.py` 已收敛为薄入口 + 细分实现）。

`src/market_data_platform/data_warehouse.py` 与 `src/market_data_platform/data_providers.py` 现在为聚合入口，实际实现位于各自子模块，便于分步增加类型标注。

（RQData 运行时 `rqdata_runtime.py` 等文件已于 2026-07-26 随 RQData 完全退役并删除。）

`src/market_data_platform/artifacts.py` 是 artifacts root 解析的权威实现。
`src/market_data_platform/paths.py` 保留路径契约和旧调用入口。默认 artifacts root 顺序为显式参数、
`DATA_PLATFORM_ROOT`、`artifacts/`。catalog 与 warehouse 数据库可分别通过
`DATA_PLATFORM_METADATA_DB_PATH`、`DATA_PLATFORM_WAREHOUSE_DB_PATH` 覆盖。（`HK_DATA_PLATFORM_ROOT` 等港股兼容变量已随港股支持于 2026-07-26 移除。）

2026-07-02 repo-local 审计显示 `config_utils.py`、`current_assets.py`、
`deprecations.py`、`intraday_paths.py`、`pit_feature_stats.py`、`rebalance.py`
和 `rqdata_cli_common.py` 无活跃调用，已从源码包退役。港股恢复能力已随 RQData 于 2026-07-26 完全移除。

当前 Ruff 和 `ty` 都覆盖 `scripts/dev/quality_baseline.json` 登记的完整源码面。2026-09-05 baseline 记录如下：

| 工具 | 文件 | 行数 | exclude |
| --- | ---: | ---: | --- |
| Ruff | 290/290 | 65708/65708 | 0 files / 0 lines |
| `ty` | 290/290 | 65708/65708 | 0 files / 0 lines |

`ty check --error-on-warning` 当前按 `pyproject.toml` 的 `src`、`scripts` 和 `tests` 范围执行。质量覆盖 baseline 会阻止检查行数下降、排除行数增加或保护路径退出检查面。

现行 Ruff complexity 阻塞 ratchet 尊重已登记的 inline `noqa`，`quality_baseline.json`
当前记录 2 条未豁免的 C901。后续任何未豁免分类计数或总数增长都会阻塞本地推送门禁。

使用 `--ignore-noqa` 盘点完整存量时，本次 `init_rqdatac` 参数聚合把总数从 40 收紧到 39，
PLR0913 从 28 降到 27。当前完整 inventory 为 C901 5、PLR0911 1、PLR0912 3、
PLR0913 27、PLR0915 3。先前记录的总数 27、PLR0913 24 来自较早快照，已经不能代表
当前 HEAD。27 条 PLR0913 主要是为兼容保留显式参数的 provider / coverage 入口。内部服务
优先改为 options/request 对象，并在迁移完成后删除对应 `noqa`。

维护性基线以 `scripts/dev/maintainability_baseline.json` 为准。2026-09-05 baseline 记录：

| 指标 | 当前 |
| --- | ---: |
| Python 文件 | 421 |
| Python 行数 | 105016 |
| 超过 100 行函数 | 38 |
| 超过 250 行函数 | 1 |
| 超过 500 行函数 | 0 |
| 10 个及以上参数函数 | 10 |
| 最大文件 | 2045 行 |
| 最大函数 | 309 行 |
| 最大参数数 | 16 |

当前唯一超过 250 行的函数是 `tests/test_cutover_a_share_minute.py::_fixture`，309 行。生产代码中较长函数仍集中在分钟数据标准化、切换和归档流程。具体热点不要在本文重复维护，直接读取 `scripts/dev/maintainability_baseline.json` 的 `largest_files` 和 `largest_functions`，避免代码拆分后文档继续保留旧路径。

常规检查：

```bash
uv run --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run --extra dev python -m ruff check .
uv run --extra dev python -m ruff format --check .
uv run --extra dev ty check --error-on-warning
```

治理检查：

```bash
uv run --extra dev python scripts/dev/quality_debt.py --skip-ruff --complexity \
  --check-baseline --check-ratchet
uv run --extra dev python scripts/dev/maintainability_metrics.py --check-baseline
uv run --extra dev python scripts/dev/compatibility_governance.py --check
uv run --extra dev python scripts/dev/architecture_governance.py --check
```

## 兼容层决策

`hkdata` console script、`hk_data_platform.*` Python 包名兼容层、`rqdata-hk-depth`、`rqdata-tick`、`rqdata-hk-assets`、`marketdata migration status`、`marketdata migration sync-hk-links` 和 `marketdata migration import-cross-artifacts` 已从活跃包移除。

保留项：

| 兼容项 | 决策 | 证据 |
| --- | --- | --- |
| `marketdata migration freeze-hk` / `marketdata migration hydrate-hk` | 恢复关键入口，已随 RQData 退役（命令与文件已删除） | `docs/operations/hk-archive-restore.md`（已退役） |

下游研究仓库的数据目录与快照 wrapper 已移除。数据操作使用 `marketdata data ...` 和
`marketdata backup-data`，策略操作使用 `strategy ...`。

## 生成文件与数据产物

`.gitignore` 已覆盖 `.venv/`、`.pytest_cache/`、`.ruff_cache/`、`__pycache__/`、`*.py[cod]`、`*.egg-info/`、`artifacts/`、`data/`、`reports/` 和本地凭证。仓库工作区中可能存在未跟踪的本地运行产物，例如 `artifacts/metadata/*.csv`、`artifacts/reports/*.json`、`src/*.egg-info` 和 `__pycache__/`。这些都不进入 Git。

## 文档审计结论

根 README、`AGENTS.md` 和 `docs/*.md` 已同步当前状态：

- 当前入口使用 `marketdata` 和 `market_data_platform`。
- 归档和下游兼容面集中记录在 `docs/compatibility.md`。
- 港股恢复专用操作已随 RQData 退役，历史复现见 `hk-freeze-20260613` 标签或私有归档仓库。
- 本地治理命令与 GitHub Actions 共同构成质量门禁。`quality.yml` 覆盖边界检查、共享维护性 ratchet、Ruff、pytest 和依赖审计，`docs.yml` 负责 strict 文档构建。
- sdist 显式包含 `docs/` 和 `tests/`。wheel 只包含运行包。当前暂不发布 `py.typed`。
- 文档保留少量关键英文术语，如 CLI、provider、release、baseline、cache、artifacts、workflow。可执行流程、目录、校验和指标描述尽量用中文，避免临时翻译造成歧义。

## 下一轮优先级

1. 拆分 `tushare_a_share_mins.py::mirror_minute_bars` 的 daily executor 与
   checkpoint/receipt finalizer，继续降低剩余 C901、PLR0912、PLR0915。
1. 将 cutover 测试的单体 fixture 拆成 raw、full-day 与 BJ receipt builders。
1. 持续缩短 `_build_fused_minute_dataset`、`materialize_standardized`、
   `build_a_share_daily_clean` 和 TuShare provider 长函数。
1. `data_providers_client.py::fetch_daily` 已拆成缓存计划、range 读取、symbol 增量补数、merge/write/slice helper。下一轮继续拆分 provider SDK adapters 与 frame pipeline，逐步恢复更严格类型覆盖。
1. 继续拆 `src/market_data_platform/data_warehouse_*` 子模块中的 pandas-heavy 步骤，评估 per-module strict 覆盖。
1. RQData 与港股恢复控制面已在 2026-07-26 完全退役，无需恢复演练。
