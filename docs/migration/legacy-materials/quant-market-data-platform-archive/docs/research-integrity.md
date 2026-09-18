# 研究数据完整性边界

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

本页说明市场数据平台在防未来函数、幸存者偏差和研究样本污染中的职责。模型选择、组合式带清理交叉验证（CPCV）、修正夏普比（DSR）、过拟合概率（PBO）、特征消融和候选晋升属于下游研究仓库范围。执行 dry-run、paper/live 门禁和订单审计属于执行仓库范围。

## 平台侧承担的防线

| 风险 | 平台机制 | 主要证据 |
| --- | --- | --- |
| 数据版本漂移 | 当前数据契约固定下游读取入口 | `metadata/current_assets/a_share_current.json` |
| 资产口径不清 | 清单（manifest）和注册表（registry）记录路径、日期范围、行数、血缘 | `manifest.yml`、`metadata/dataset_registry.csv` |
| 当前成分回填历史 | by-date universe 作为研究股票池入口 | `universe_by_date`、`universe_meta` |
| 最新财报快照进入历史研究 | raw -> normalized -> PIT 分层，按披露日和可用日发布 | `normalized_fundamentals`、`pit_fundamentals` |
| 当前行业标签回填历史 | 历史行业变更资产必须包含生效区间或 dated snapshot provenance | `industry_changes` |
| 日线资产质量不足 | baseline 与 research profile 检查会记录缺失、重复、涨跌停、停牌和交易日历问题 | `reports/a_share_daily_clean_*_validation_*.json` |
| 发布证据不足 | 当前数据契约发布前会先做 validation、health 检查，并写入发布证据 | `reports/a_share_current_release_*.json`、`reports/a_share_current_health_*.json` |

这些机制的目标是让下游研究只能读取带版本、带血缘、带质量证据的数据资产。平台不会评价某个策略是否过拟合，也不会读取研究 run 的 Sharpe、IC 或回测曲线。

## A 股研究资产口径

A 股当前数据契约的权威入口是：

```text
metadata/current_assets/a_share_current.json
```

下游研究读取该入口时，应同时确认：

- `daily_clean` 已通过 baseline 检查。正式研究 profile 需要额外通过 research 检查。
- `universe_by_date` 来自平台发布的 by-date 股票池，研究侧配置应使用 `research_universe.mode: pit` 和 `require_by_date: true`。
- `daily_basic` 估值字段属于逐日估值 overlay。财务报表 PIT 研究使用 `pit_fundamentals`。
- `is_st` 当前来自 latest instruments snapshot 时，只能作为非 PIT 标记处理。
- PIT fundamentals v2 的 `available_date` 按披露日和日历日延迟生成。研究侧必须通过正式
  as-of loader 读取，并同时验证 `revision_covered`、`freshness_verified`、字段级 revision
  provenance 和 `_source_retrieved_at <= as_of_date`。报告期查询范围不提供观测证据。旧 v1 或
  2026 年采集的历史回填不能声称 revision-safe PIT。
- 历史行业资产需要 `effective_date` / `end_date` 或明确 dated snapshot provenance。

完整 A 股资产政策见 `a-share-research-profile.md`，基本面 raw-to-PIT 运维见 `a-share-fundamentals.md`。

## 平台发布前检查顺序

发布或切换 A 股当前数据契约前，建议按以下顺序留证：

1. 完成 raw 数据下载或 licensed local extract 接入。
2. 构建 normalized 或 clean 资产。
3. 运行 baseline validation。
4. 对研究用途运行 research validation。
5. 运行当前数据契约健康检查。
6. 生成发布证据。
7. 更新 `metadata/current_assets/a_share_current.json` 和 `metadata/dataset_registry.csv`。

对应文档入口：

- `contracts.md`：当前数据契约、资产键、清单和数据集注册表规则。
- `a-share-research-profile.md`：A 股 research profile 与 PIT / 历史行业政策。
- `a-share-fundamentals.md`：基本面 raw-to-PIT 下载、校验和发布。
- `operations/a-share-tushare.md`：TuShare 日常运维和当前数据刷新。

## 与下游防过拟合文档的关系

下游研究仓库的防过拟合文档会检查：

- 是否使用平台当前数据契约。
- 是否启用 by-date PIT universe。
- 是否避免把 `daily_basic` 当成 PIT fundamentals。
- 是否避免把当前行业或当前 ST 快照回填历史。
- 是否把 final 样本外（OOS）、CPCV、feature evidence、DSR 和 promotion gate 留在研究侧执行。

执行仓库只消费研究导出的 `targets.json`。平台不把券商执行日志、模拟盘证据或实盘审计日志纳入数据资产当前数据契约。
