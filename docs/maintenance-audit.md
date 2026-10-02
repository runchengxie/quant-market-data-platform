# 维护性审计快照

[English page](maintenance.en.md)

> status: active
> owner: quant-market-data-platform
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
| `src/quant_market_data_platform/contract.py`, `paths.py`, `manifest.py`, `registry.py`, `data_provider_contracts.py` | active | 平台核心边界，保持 Ruff 和 `ty` 覆盖 |
| `src/quant_market_data_platform/providers/*`, `tushare_cli.py` | active | provider adapter 与 CLI parser，继续扩大类型覆盖 |
| `src/quant_market_data_platform/cold_storage.py` | migration-only; removed | 已于 2026-07-26 随 RQData 退役，命令与文件已删除 |
| `scripts/dev/*` | active governance | 本地门禁使用的治理脚本，变更需配套测试 |
| `artifacts/`, `reports/`, `.pytest_cache/`, `.ruff_cache/`, `*.egg-info` | generated/cache | 不属于源码维护面，不提交 Git |

## 质量覆盖

港股 provider 生产目录移除后，Ruff 和 `ty` 的目录级 exclude 已收窄到非源码路径。核心重构面不再保留大文件级
exclude（`data_providers.py` 和 `data_warehouse.py` 已收敛为薄入口 + 细分实现）。

`src/quant_market_data_platform/data_warehouse.py` 与 `src/quant_market_data_platform/data_providers.py` 现在为聚合入口，实际实现位于各自子模块，便于分步增加类型标注。

（RQData 运行时 `rqdata_runtime.py` 等文件已于 2026-07-26 随 RQData 完全退役并删除。）

`src/quant_market_data_platform/artifacts.py` 是 artifacts root 解析的权威实现。
`src/quant_market_data_platform/paths.py` 保留路径契约和旧调用入口。默认 artifacts root 顺序为显式参数、
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

## 2026-09-27 共享代码质量基线调整

`config/code-quality-baseline.json` 的 `python_files` 从 449 调整到 452，`python_lines` 从
108564 调整到 109101。本次增加的 3 个 Python 文件和 537 行分别是
`src/quant_market_data_platform/knowledge_index.py`、`scripts/dev/knowledge_index.py` 和
`tests/test_knowledge_index.py`。它们提供 Knowledge v2 数据集试点的严格解析、开发校验器和
合成契约测试。额外 24 行用于拆分解析和清单校验函数，保持既有复杂度门禁通过。
这是已批准的试点范围，其他共享指标保持原基线。

调整前，GitHub Actions 使用的 `research-code-quality --ratchet` 命令只报告
`python_files: 452 > baseline 449` 和 `python_lines: 109077 > baseline 108564`。
完整本地门禁随后发现新解析器增加了 2 条 C901 诊断。拆分函数后，复杂度 ratchet 回到
接受基线以内，文件行数增加到 109101。
`quality_debt.py --json --skip-ruff` 报告 Ruff 和 `ty` 均覆盖 293/293 个源码文件、
66549/66549 行，排除 0 个文件和 0 行。
`maintainability_metrics.py --json --limit 30` 报告 452 个 Python 文件、109101 行、
38 个超过 100 行的函数、1 个超过 250 行的函数，最大文件 2045 行、最大函数 309 行。

## 2026-09-30 canonical package rename

将 distribution/import 从 `market-data-platform` / `market_data_platform` 改为
`quant-market-data-platform` / `quant_market_data_platform` 后，较长的绝对导入由 formatter 拆行，
`config/code-quality-baseline.json` 的 `python_lines` 因此从 109565 调整为 109626。文件数和其余
质量指标保持不变。此基线在内部导入改为相对路径或下一次明确的源码布局调整时重新评估。
这些热点指标没有随本次试点增加。

后续收紧时，优先在知识索引的解析、清单加载和 CLI 中移除重复逻辑，保持测试覆盖。
只有文件或行数实际下降并通过完整测试，才降低对应共享基线。验证命令为：

```bash
uv run --locked --extra dev research-code-quality --root . --scope src --scope scripts --scope tests --baseline config/code-quality-baseline.json --ratchet --json
uv run --extra dev python scripts/dev/maintainability_metrics.py --check-baseline
uv run --extra dev python scripts/dev/quality_debt.py --skip-ruff --check-baseline --check-ratchet
```

## 2026-09-28 约束数据定向发布基线调整

共享代码质量基线的 `python_lines` 从 109101 调整到 109151。新增的 50 行用于按数据集
发布约束参考资产，验证所选数据集的来源收据，并覆盖命令行和错误路径。其他指标不变。
这使完整的 `namechange` 资产能够单独发布，避免把尚未重新验证的其他约束资产一起切换。
后续合并发布入口的重复选择逻辑时，应减少实际代码行数，并同步下调该基线。退出条件是
完整测试及 `research-code-quality --ratchet` 均通过。

## 2026-09-28 ST 一致性校验基线调整

共享代码质量基线的 `python_lines` 从 109151 调整到 109311。新增的 160 行用于研究级
`daily_clean` 校验中的 ST 来源回执与哈希核验、逐行 `is_st` 对账和合成回归测试。
`python_files`、长行、复杂函数与大文件数量均未增加。当前已发布清洗资产的
11,775,852 行扫描中，ST 不一致行数为 0，原有 1 行涨跌幅警告保持不变。

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

- 当前入口使用 `marketdata` 和 `quant_market_data_platform`。
- 归档和下游兼容面集中记录在 `docs/compatibility.md`。
- 港股恢复专用操作已随 RQData 退役，历史复现见 `hk-freeze-20260613` 标签或私有归档仓库。
- 本地治理命令与 GitHub Actions 共同构成质量门禁。`quality.yml` 覆盖边界检查、共享维护性 ratchet、Ruff、pytest 和依赖审计，`docs.yml` 负责 strict 文档构建。
- sdist 显式包含 `docs/` 和 `tests/`。wheel 只包含运行包。当前暂不发布 `py.typed`。
- 文档保留少量关键英文术语，如 CLI、provider、release、baseline、cache、artifacts、workflow。可执行流程、目录、校验和指标描述尽量用中文，避免临时翻译造成歧义。

## 2026-09-29 ST 事件日期审计基线

共享代码质量基线的 `python_files` 从 452 调整到 454，`python_lines` 从 109311 调整到 109565。新增的模块与测试为带来源哈希的 ST 事件日期审计和原始参考数据回执提供可重复执行的命令。`functions_over_100`、复杂度与长行基线没有增加。退出条件是将这些审计合并进既有 ST 质量流水线时删除独立入口及对应冗余代码。在公告时刻可用性得到原始证据前，继续保留 `revision_safe=false`。

## 2026-09-30 双语文档回归测试基线

双语页面测试增加 1 个 Python 文件和 148 行，检查 README 与 MkDocs 页面使用英文 canonical、中文 companion 链接互通，以及导航目标保持英文。共享基线的 `python_files` 从 454 调整到 455，`python_lines` 从 109626 调整到 109774。没有提高复杂度或长函数预算。只要页面仍按文件配对维护，该测试就是语言路由契约。未来若迁移到统一 i18n 路由，应将断言并入新的契约测试并重新测量基线。

## 2026-10-01 ST 可用时间契约基线

ST 历史增加保守的交易日可用时间，并将 `st_available_from` 传播到 `daily_clean.v2`。对应校验覆盖缺失公告日期、同日公告、下一交易日、receipt 版本和逐行一致性。共享代码质量基线的 `python_lines` 从 109774 调整到 110058。文件数、复杂度、长函数和长行预算未增加。退出条件是 provider 与消费者完成版本化迁移后，评估将重复的独立校验收敛到既有 ST 质量流水线，并在删减等量冗余代码后收紧行数基线。重建历史仍标记 `revision_safe=false`。

研究视图将每日 `st_available_from` 提供给 A 股和 DailyWatch20 消费者，并在缺少字段时失败，确保使用者不会静默退回旧契约。共享 `python_lines` 基线从 110058 调整至 110081，增加内容为 API 字段投影、契约测试和必要文档。文件数、函数复杂度、长函数和长行预算未增加。消费者完成迁移后，可评估将重复的逐行可用性检查集中到共享数据校验。

两个研究读取接口现在校验 `daily_clean` 清单必须声明 v2 schema 和 ST availability contract，避免 DuckDB 将旧分区缺失字段补成空值后被误当成有效数据。共享基线的 `python_files` 从 455 增至 456，`python_lines` 从 110081 增至 110174，新增内容为共享清单校验器及回归测试。复杂度、长函数和长行预算未增加。消费者完成迁移并确认发布资产都使用 v2 后，可评估是否在更高层合并重复契约校验。

## 下一轮优先级


1. 拆分 `tushare_a_share_mins.py::mirror_minute_bars` 的 daily executor 与
   checkpoint/receipt finalizer，继续降低剩余 C901、PLR0912、PLR0915。
1. 将 cutover 测试的单体 fixture 拆成 raw、full-day 与 BJ receipt builders。
1. 持续缩短 `_build_fused_minute_dataset`、`materialize_standardized`、
   `build_a_share_daily_clean` 和 TuShare provider 长函数。
1. `data_providers_client.py::fetch_daily` 已拆成缓存计划、range 读取、symbol 增量补数、merge/write/slice helper。下一轮继续拆分 provider SDK adapters 与 frame pipeline，逐步恢复更严格类型覆盖。
1. 继续拆 `src/quant_market_data_platform/data_warehouse_*` 子模块中的 pandas-heavy 步骤，评估 per-module strict 覆盖。
1. RQData 与港股恢复控制面已在 2026-07-26 完全退役，无需恢复演练。


## QuantZone configuration size baseline — 2026-10-02

The shared size baseline now records 472 Python files and 112,768 lines after adding the private JSON loader/launcher, optional QuantZone planner/adapter/artifact modules and their tests. This update accounts for the intentional feature addition. Complexity, long-function, large-file and lint-exclusion thresholds remain unchanged. There is no extra size allowance beyond the measured snapshot. Future growth requires an explained feature change or refactoring. Remove the corresponding size allowance if these modules are retired.

### Recovery contract regression inventory (2026-10-02)

The shared Python line inventory increases from 112768 to 112876 for explicit THS cache resume and required nullable ST availability preservation. The increment includes parser, cached-file and full-mirror regressions plus legacy/v2 ST receipt batching coverage. File, long-function, complexity and large-file limits remain unchanged. This is an exact source inventory allowance. Reduce it when these implementations or fixtures are consolidated without losing regression coverage.

### Single-file receipt and ST wrapper inventory (2026-10-02)

Python files increase from 472 to 476, and lines from 112976 to 113231, for hash-bound reference manifests, narrowly recognized ST availability receipts and regressions. Publication integration tests are separated to keep the existing clean test module below 800 lines. Complexity, long-function and large-file limits remain unchanged. This exact inventory allowance can be reduced when receipt helpers or fixtures are consolidated while preserving provenance and failure-path coverage.

### Reference coverage dates (2026-10-02)

Python lines increase from 113231 to 113260 to distinguish immutable version labels from verified source coverage, with a regression. All other limits are unchanged. Reduce this exact allowance if the receipt fixture is consolidated while retaining the different-date case.
