# 质量治理与维护债务

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-25
> source_of_truth: yes
> superseded_by: n/a

## Raw L2 quality profiling

The platform owns reusable, read-only structural checks for raw market data. Profile explicit
Parquet files without modifying them:

```bash
marketdata quality profile \
  --file /path/to/order_2024-12-13.parquet \
  --output /tmp/l2-quality.json
```

For a large tree, use the resumable CPU scanner. It writes one checkpoint update per file and
keeps the raw Parquet files unchanged:

```bash
marketdata quality scan \
  --root /path/to/raw-l2 \
  --checkpoint /tmp/l2-quality.checkpoint.json \
  --output /tmp/l2-quality-summary.json
```

Before materializing a larger derived dataset, run a low-copy pilot on explicit files. The pilot
writes keep-only canonical samples and sparse labels for rows that need review:

```bash
marketdata quality pilot \
  --file /path/to/deal_2024-12-13.parquet \
  --file /path/to/order_2024-12-13.parquet \
  --file /path/to/snapshot_2024-12-13.parquet \
  --output-dir /tmp/l2-pilot
```

The pilot does not delete or rewrite its inputs. `canonical/` contains rows currently safe for a
downstream sample, while `labels/` contains only `tag` and `exclude` rows with a source row number.

Install the optional dependency with `uv sync --extra quality`. Model-input, label, leakage,
alpha, and portfolio checks remain in their downstream research projects.

For raw L2 files, duplicate identity checks are scoped by the resolved security column and ID
column when a security column is available. For example, `SecuCode=000001, OrderID=7` and
`SecuCode=000002, OrderID=7` are treated as different identities. The report exposes this
contract in `id_scope_columns`.

Check trade order references separately when both source files are available:

```bash
marketdata quality integrity \
  --orders /path/to/order_2024-12-13.parquet \
  --trades /path/to/trades_2024-12-13.parquet \
  --output /tmp/l2-integrity.json
```

The reusable opening-auction accounting core is available as
`market_data_platform.quality_opening`. It consumes explicit orders, trades, and cancels,
reconstructs remaining bid/ask levels, and reports unknown identities and overdrawn volume.
Raw-file discovery, event cutoffs, exchange-specific lag, and snapshot alignment remain in
downstream research projects because those rules are market- and experiment-specific.

本页记录 Ruff、`ty` 覆盖、维护性指标、兼容层生命周期和架构边界的本地治理命令。
这些检查用于暴露历史债务、阻止新增债务扩大，并指导分阶段收紧。

## 常规门禁

常规门禁：

```bash
uv run --extra dev python scripts/dev/run_pytest_isolated.py -- -q
uv run --extra dev python -m ruff check .
uv run --extra dev python -m ruff format --check .
uv run --extra dev ty check --error-on-warning
```

阻塞类型检查是 `ty check --error-on-warning`。`pyproject.toml` 中的
`[tool.ty.src].include` 合并了迁移前的日常检查与发布检查范围，并额外保护 `symbols.py`。工作区的发布类型检查与日常门禁使用同一配置。

## 代码健康与评审准则

本仓库参考 Google Engineering Practices / Code Review Guide 的代码健康取向，以及 Google Python Style Guide
的清晰 Python 写法。执行口径仍以本仓库 Ruff、`ty`、pytest、baseline 和公开数据契约为准。

参考资料：

- Google Engineering Practices / Code Review Guide:
  `https://google.github.io/eng-practices/review/`
- Google Python Style Guide:
  `https://google.github.io/styleguide/pyguide.html`

评审和重构时优先检查：

- 变更是否让被修改系统的整体代码健康变好，并避免只让当前 patch 勉强通过。
- 是否保持 CLI、资产键、数据提供方（provider）API、路径、当前数据契约、清单（manifest）schema 和历史产物兼容。
- 是否把新增逻辑放在正确职责边界内，例如数据提供方调用、分页 / 分区循环、清单 builder、options dataclass
  和纯日期 helper 分开维护。
- 是否避免新增长函数、大文件、10 个及以上参数函数、隐式公开门面（facade）导出和无法解释的兼容层。
- 是否用聚焦测试覆盖数据提供方 adapter、清单内容、CLI 参数和跨仓契约，并减少对快照式大测试的依赖。

Google-style docstring 只用于公共入口和不显然的 helper。仓库内优先用类型标注、清晰命名、小函数和本地测试表达意图。
不要为了风格一致性做全仓 docstring、重命名或格式化 churn。

## 债务可见性

静态检查覆盖和非阻塞 Ruff debt：

```bash
uv run --extra dev python scripts/dev/quality_debt.py
uv run --extra dev python scripts/dev/quality_debt.py --complexity
uv run --extra dev python scripts/dev/quality_debt.py --json --skip-ruff
```

`--complexity` 单独使用时只报告 Ruff 的 `C90,PLR0911,PLR0912,PLR0913,PLR0915`
规则集。本地完整门禁会同时传入 `--check-ratchet`，阻止复杂度债务超过已接受基线。类型诊断由 `ty` 阻塞检查直接处理，不再维护另一套诊断债务。

本地提交前也可以运行 pre-commit：

```bash
uv run --extra dev pre-commit install
uv run --extra dev pre-commit run --all-files
```

当前仓库有两条活动 GitHub Actions 工作流。`.github/workflows/quality.yml` 在 PR 和
`main` 推送时运行公开快照边界检查、共享代码质量 ratchet、Ruff、pytest 和 `pip-audit`。
`.github/workflows/docs.yml` 对文档变更执行 strict MkDocs 构建，并在主分支发布 Pages。
本地 pre-commit 和治理脚本用于更早发现问题，不能替代远端必需检查。发布前按目标模块运行 coverage。

维护性指标：

```bash
uv run --extra dev python scripts/dev/maintainability_metrics.py
uv run --extra dev python scripts/dev/maintainability_metrics.py --markdown
```

兼容层和架构边界：

```bash
uv run --extra dev python scripts/dev/compatibility_governance.py --check
uv run --extra dev python scripts/dev/architecture_governance.py --check
```

## Baseline 更新

只有在变化是有意接受或有意改善时才更新 baseline：

```bash
uv run --extra dev python scripts/dev/quality_debt.py --skip-ruff --write-baseline
uv run --extra dev python scripts/dev/quality_debt.py --skip-ruff --check-baseline
uv run --extra dev python scripts/dev/quality_debt.py --skip-ruff --check-ratchet

uv run --extra dev python scripts/dev/maintainability_metrics.py --write-baseline
uv run --extra dev python scripts/dev/maintainability_metrics.py --check-baseline
```

`--check-baseline` 会阻止以下回退：

- Ruff 或 `ty` 已检查源码行数下降。
- Ruff 或 `ty` 排除的源码行数上升。
- Ruff 或 `ty` 的源码排除列表新增但 baseline 未更新。
- 已纳入治理的边界模块重新进入 Ruff 或 `ty` exclude。
- Ruff complexity 诊断数量超过已接受 ratchet。
- 大文件、长函数、超长参数列表或 public facade export 数量超过已接受 baseline。

更新 baseline 的提交说明应包含：

- `quality_debt.py --json --skip-ruff` 的当前覆盖摘要。
- `maintainability_metrics.py --json --limit 30` 的当前热点摘要。
- 触发前滚的原因：真实重构、计数口径变化、测试覆盖增加，或明确接受的存量 debt。
- 下一步收紧对象和验证命令。

新增 `src/` Python 文件默认进入 Ruff 覆盖。扩大 `ty` 范围时同步更新配置和 baseline。新增 `ty` 排除需要同步
`scripts/dev/quality_baseline.json`、`docs/maintenance-audit.md` 和提交说明。已列入
`quality_debt.PROTECTED_INCLUDED_PATHS_BY_TOOL` 的路径不能重新进入 Ruff 或 `ty` exclude。

长函数 waiver 规则：

- 单函数超过 150 行，提交说明需写出文件、函数、职责边界和后续切片计划。
- 单函数超过 250 行，需补 focused characterization test 或说明已有测试覆盖。
- 单文件超过 800 行，进入 `docs/maintenance-audit.md` 热点表。
- 新增 10 个及以上参数的函数，需要说明调用方稳定性和后续 dataclass / config object 计划。

## 分阶段准入计划

当前优先级：

1. Ruff / `ty`：`data_provider_contracts.py` 和 `symbols.py`
   已纳入保护覆盖，并由 `quality_debt.PROTECTED_INCLUDED_PATHS_BY_TOOL` 防止重新排除。
   2026-07-02 已退役无活跃调用的旧 helper：
   `config_utils.py`、`pit_feature_stats.py`、`rebalance.py` 和 `rqdata_cli_common.py`。
1. Ruff：`data_providers.py`、`data_warehouse.py` 作为聚合入口与主要 A 股 provider helper
   已恢复覆盖。`data_providers_core.py` 已改为薄入口，目前实现位于 `data_providers_client.py`，下一步继续拆分
   `data_providers_client.py` 的 provider adapters 与 frame pipeline，再逐步加入 stricter 覆盖。
1. `ty`：保持已登记范围，新增诊断必须在合入前解决，并按边界模块逐步扩大覆盖。
1. 类型契约：provider contract 优先使用 `Protocol`、`TypedDict` 或 dataclass 稳定接口。
1. 类型承诺：扩大 `ty` 覆盖并完成下游类型契约验证后再发布 `py.typed`。
1. RQData 与港股恢复控制面已在 2026-07-26 完全退役，相关代码与 `freeze-hk` / `hydrate-hk` 命令已移除。

当前覆盖数据和维护热点见 `docs/maintenance-audit.md`。

## 兼容层规则

新增 console script alias、旧 import re-export 或 migration-only command 前，必须先更新
`docs/compatibility.md`，记录用途、风险、推荐替代、清理条件、当前状态和审计证据。
迁移类命令不承载新的平台业务能力。新能力应进入原生 `marketdata tushare ...` 工作流。RQData 入口已完全退役。

### CLI 文档同步规则

`market_data_platform.cli.build_parser()` 可达的所有公开叶子命令都会被治理测试扫描。
新增、移除或重命名 `marketdata` 命令时，必须同步更新 `docs/` 下用户可见文档（包含命令示例或
`--help` 文本片段），否则 `test_public_marketdata_cli_commands_are_documented` 将阻塞本地 full gate。
