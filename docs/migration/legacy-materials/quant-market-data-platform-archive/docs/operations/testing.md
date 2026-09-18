# 测试脚本说明

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

本页说明 `tests/` 和 `scripts/dev/` 当前覆盖的项目事实。修改公开 CLI、数据契约、路径规则或文档时，应同步更新对应测试。项目测试把部分文档质量要求写成门禁。

## 本地测试命令

完整开发环境：

```bash
uv sync --extra dev
uv run --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run --extra dev python -m ruff check .
uv run --extra dev python -m ruff format --check .
uv run --extra dev ty check --error-on-warning
```

仓库没有活动 GitHub Actions workflow。作为 `research-workspace` 子模块使用时，Git 会读取工作区共享的 `.githooks/pre-push`。该 hook 在本地运行 Ruff、格式、`ty`、pytest、复杂度 ratchet、维护性 baseline、兼容层和架构治理。`ty` 已合并迁移前的日常与发布检查范围，发布检查沿用同一配置。

完整测试在单个 pytest 进程中会累积较多内存。`run_pytest_isolated.py` 默认每 8 个测试文件启动一个进程，并在批次结束后释放内存。聚焦单个模块时仍可直接运行 `python -m pytest tests/<file>.py`。coverage 属于发布诊断，按目标模块运行：

```bash
uv run --extra dev python -m pytest tests/<file>.py \
  --cov=market_data_platform --cov-report=term-missing
```

依赖审计和静态安全扫描按仓库运行：

```bash
uv run --extra dev pip-audit
uvx deptry .
uvx bandit -q -r src -lll
```

带 DuckDB 查询能力：

```bash
uv sync --extra dev --extra duckdb
```

## Qlib 条件化测试

标准 `dev` 门禁不安装 `pyqlib`。默认测试会检查原生 DataFrame 等价性、延迟导入和缺失
依赖时的错误信息。真实 Qlib DataLoader 用例通过 `pytest.importorskip` 检测运行时，未安装
`pyqlib` 时会明确跳过。

需要验证真实运行时时，额外安装 `qlib` extra 并运行定点测试：

```bash
uv sync --locked --extra dev --extra qlib
uv run --locked --extra dev --extra qlib python -m pytest \
  tests/test_published_assets.py -k qlib -q
```

这组测试只验证已发布 Parquet 资产到 Qlib DataLoader 的只读映射。DataHandler、Dataset、
模型训练、实验记录和回测后端没有在本仓库实现。

带 TuShare 采集能力：

```bash
uv sync --extra dev --extra tushare
```

当前部分测试在导入阶段需要 `pyarrow`，完整测试建议用 `dev` extra 执行。系统 Python 下若无 `pyarrow`，相关 Parquet 测试可能无法收集。`tests/conftest.py` 会清理 `DATA_PLATFORM_ROOT`、`DATA_PLATFORM_METADATA_DB_PATH`、`DATA_PLATFORM_WAREHOUSE_DB_PATH` 和 `HK_DATA_PLATFORM_ROOT`，避免开发机 shell 配置污染路径解析测试。

## 测试范围

| 测试文件 | 主要覆盖范围 |
| --- | --- |
| `tests/test_artifacts.py` | 产物根目录与数据库路径解析、共享路径重定位 |
| `tests/test_paths.py` | 市场路径、当前数据契约、数据集注册表、契约检查报告 |
| `tests/test_published_assets.py` | 当前数据契约、完整资产清单只读 API、路径边界、显式 Parquet 映射、PIT 和日历过滤、Qlib 延迟加载适配器 |
| `tests/test_cli_dependency_boundaries.py` | CLI parser 可导入性、可选依赖报错、帮助信息稳定性 |
| `tests/test_quality_governance.py` | 治理脚本、公开 CLI 文档覆盖、文档风格回归、兼容层和架构边界 |
| `tests/test_data_warehouse.py` | 数据目录刷新、标准层物化、DuckDB 查询、`data` 命令 |
| `tests/test_backup_data.py` | 本地快照备份、CLI 调用 |
| `tests/test_data_providers_cache.py` | 数据源缓存、local asset 优先读取、A 股代码标准化 |
| `tests/test_tushare_a_share.py` | TuShare token 校验、原始层镜像、重试、限额、历史补齐、当前数据发布 |
| `tests/test_tushare_a_share_clean.py` | A 股 `daily_clean` 构建、质量校验和内存保护 |
| `tests/test_tushare_a_share_universe.py` | A 股 by-date 股票池构建与校验 |
| `tests/test_tushare_a_share_fundamentals.py` | 基本面 raw、标准化、PIT、发布链路 |
| `tests/test_tushare_a_share_research_assets.py` | PIT 基本面候选、历史行业变更和研究资产契约 |
| `tests/test_tushare_a_share_flow_features.py` | 资金流与持仓候选特征 |
| `tests/test_tushare_a_share_hotspot_features.py` | 热点研究特征 |
| `tests/test_tushare_a_share_hotspot_mirrors.py` | 热点类原始镜像命令暴露和查询兜底 |
| `tests/test_a_share_minute_build.py`、`tests/test_a_share_minute_fusion.py` | Guan 年度与 deal 构建、单位转换、来源融合和恢复 |
| `tests/test_a_share_minute_coverage.py`、`tests/test_a_share_minute_bj_overlay.py` | 分钟来源分类、覆盖终检、整日替换、北交所 overlay 和回执（receipt）绑定 |
| `tests/test_tushare_a_share_mins.py`、`tests/test_tushare_minute_chunk.py` | TuShare 分钟镜像、sidecar、241 根网格、断点恢复和 bounded chunk |
| `tests/test_tushare_minute_backfill.py`、`tests/test_tushare_minute_replacement_campaign.py`、`tests/test_tushare_minute_replacement_campaign_runner.py` | 不可变 backfill 计划、staging 续跑、软行数预算和替换 campaign |
| `tests/test_minute_candidate.py` | TuShare 分钟候选 inventory、全区间语义/特征回归和 hardlink 候选发布 |
| `tests/test_cutover_a_share_minute.py`、`tests/test_detach_a_share_minute_v3.py` | v3 hardlink 解除、full-A 与沪深合同、原子切换和中断恢复 |
| `tests/test_audit_minute_overlap.py` | Guan 与 TuShare 重叠日只读口径审计 |
| `tests/test_archive_guan_mobile.py`、`tests/test_guan_mobile_raw.py` | Guan 移动盘原始归档、复验、provider-native 提升和回执 |
| `tests/test_tushare_a_share_hsgt_features.py` | 沪深港通市场级资金特征 |
| `tests/test_tushare_a_share_ownership_features.py` | 公募持仓、股东结构、龙虎榜和增减持事件特征 |
| `tests/test_tushare_platform_assets.py` | TuShare 本地平台资产读取 |
| `tests/test_symbol_alias.py` | symbol、ts_code、order_book_id 等别名标准化 |
| `tests/test_market_specs.py` | 支持市场和代码映射规范 |
| `tests/test_parquet_scanning.py` | Parquet 分批扫描工具 |

## 治理脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/dev/run_pytest_isolated.py` | 分批运行全仓 pytest，并在批次之间释放内存 |
| `scripts/dev/quality_debt.py` | Ruff 与 `ty` 覆盖可见性、复杂度债务和 baseline / ratchet 检查 |
| `scripts/dev/maintainability_metrics.py` | 大文件、长函数、参数数量和公开聚合入口统计 |
| `scripts/dev/compatibility_governance.py` | 兼容层生命周期表和仓库级使用审计 |
| `scripts/dev/architecture_governance.py` | 核心模块、A 股模块边界检查 |

## 文档门禁

`tests/test_quality_governance.py` 会检查：

- `docs/README.md` 链接到 `maintenance-audit.md`、`operations.md` 和 `archive/README.md`。
- 文档中不出现绕弯的对比式表达。
- `market_data_platform.cli.build_parser()` 可达的公开叶子命令都在活跃文档中出现。
- `marketdata tushare download-a-share-industry-membership` 有明确文档入口。
- `docs/compatibility.md` 覆盖 active、compatibility、migration-only、deprecated、archival 和 internal-only 生命周期分类。

新增或重命名 `marketdata` 命令时，先更新 `docs/operations.md` 的公开 CLI 清单，再补主题页样例和测试。这样可保持文档与实际 CLI 同步。
