# 共享数据契约

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

## 数据产物根目录

共享的数据产物根目录用于划分数据工具与策略代码库之间的存储边界。

推荐的环境变量配置：

```bash
export DATA_PLATFORM_ROOT=/data/market-data-platform
```

`DATA_PLATFORM_ROOT` 是跨项目统一使用的环境变量，用于指定共享市场数据输入与平台产物路径。
下游系统的运行记录、缓存和报告由各自仓库管理，不通过市场数据平台的路径变量配置。

研究运行需要核对输入是否属于当前契约时，可使用
`market_data_platform.contract.match_current_contract_entry` 按 alias 路径或最终解析路径匹配资产。
读取契约内容可使用 `market_data_platform.contract.load_current_contract`。这些函数只读取契约，
不刷新数据，也不发布新的平台资产。路径存在性分类可使用
`market_data_platform.contract.path_kind`。
输入路径的解析结果和 manifest、current contract 关联信息可使用
`market_data_platform.contract.describe_input_path`，适合由下游运行记录直接保存。

历史港股运行记录仍可通过 `current_contract_path(..., market="hk")` 定位
`metadata/current_assets/hk_current.json`。这项支持只保留路径兼容，平台暂未恢复港股数据生产。

## 当前数据契约

```text
<artifacts_root>/metadata/current_assets/<market>_current.json
```

必需的顶层 JSON 结构：

```json
{
  "contract": {
    "name": "a_share_current",
    "market": "a_share",
    "provider": "tushare",
    "version": 1,
    "artifacts_root": "/data/market-data-platform",
    "target_date": "20260109"
  },
  "assets": {
    "daily_clean": {
      "alias_path": ".../a_share_all_daily_clean_latest",
      "resolved_path": ".../a_share_all_20260109_daily_clean_latest",
      "manifest_path": ".../manifest.yml",
      "as_of": "20260109"
    }
  }
}
```

可通过标准的数据资产别名生成该契约文件：

```bash
marketdata contract build \
  --market a_share \
  --provider tushare \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --target-date 20260109
```

默认情况下，该命令会合并已存在的当前数据契约，生成
`metadata/dataset_registry.csv`。如只需写入 JSON 契约，可加 `--no-registry`。

### 分钟资产入口

A 股分钟资产通过以下两个稳定 alias 发布：

```text
<artifacts_root>/assets/derived/a_share/minute_1m
<artifacts_root>/assets/derived/a_share/minute_1m_tushare
```

`minute_1m` 是 Guan legacy canonical，当前指向 `minute_1m_v3_20260714`。
`minute_1m_tushare` 是 TuShare-native operational canonical。两者具有相同的八列 Parquet schema，
但不具有跨来源特征等价承诺。分钟资产会在正式验收后进入 `a_share_current.json` 和
`dataset_registry.csv`，其中 TuShare 资产使用 `minute_1m_tushare` 键，Guan 入口保留为
`minute_1m` 回滚/对照资产。下游需要同时记录所选 alias、最终解析版本和 receipt 哈希。Guan
逐日来源和市场范围从 coverage receipt 读取。TuShare 完整性和 lineage 从 operational receipt
读取。完整口径见 [A 股分钟数据](operations/a-share-minutes.md)。

中国香港市场支持已在 2026-07-26 随 RQData 一起退役，`freeze-hk` / `hydrate-hk` 命令已移除。历史复现使用 `hk-freeze-20260613` 标签或私有归档仓库。

也可以用市场通用检查入口查看当前契约是否存在、资产别名是否缺失、以及各资产
`as_of` 是否落后于目标日期：

```bash
marketdata contract inspect \
  --market a_share \
  --provider tushare \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --target-date 20260109 \
  --fail-on-severity error
```

## 数据资产键名

中国大陆市场契约可通过 `--provider tushare` 显式选择 TuShare raw 资产。此模式下
`a_share_current.json` 的 `contract.provider` 为 `tushare`，当前支持的路径为：

| 资产键名 | TuShare 中国大陆市场默认路径 |
| --- | --- |
| `instruments` | `assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet` |
| `trade_cal` | `assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet` |
| `daily` | `assets/tushare/a_share/daily/a_share_all_daily_latest` |
| `adj_factor` | `assets/tushare/a_share/adj_factor/a_share_all_adj_factor_latest` |
| `daily_basic` | `assets/tushare/a_share/daily_basic/a_share_all_daily_basic_latest` |
| `limit_status` | `assets/tushare/a_share/limit_status/a_share_limit_status_latest` |
| `moneyflow` | `assets/tushare/a_share/moneyflow/a_share_all_moneyflow_latest` |
| `moneyflow_dc` | `assets/tushare/a_share/moneyflow_dc/a_share_all_moneyflow_dc_latest` |
| `moneyflow_hsgt` | `assets/tushare/a_share/moneyflow_hsgt/a_share_all_moneyflow_hsgt_latest` |
| `top_inst` | `assets/tushare/a_share/top_inst/a_share_all_top_inst_latest` |
| `ths_hot` | `assets/tushare/a_share/ths_hot/a_share_all_ths_hot_latest` |
| `dc_concept` | `assets/tushare/a_share/dc_concept/a_share_all_dc_concept_latest` |
| `dc_concept_cons` | `assets/tushare/a_share/dc_concept_cons/a_share_all_dc_concept_cons_latest` |
| `kpl_list` | `assets/tushare/a_share/kpl_list/a_share_all_kpl_list_latest` |
| `kpl_concept_cons` | `assets/tushare/a_share/kpl_concept_cons/a_share_all_kpl_concept_cons_latest` |
| `limit_step` | `assets/tushare/a_share/limit_step/a_share_all_limit_step_latest` |
| `limit_cpt_list` | `assets/tushare/a_share/limit_cpt_list/a_share_all_limit_cpt_list_latest` |
| `report_rc` | `assets/tushare/a_share/report_rc/a_share_all_report_rc_latest` |
| `stk_surv` | `assets/tushare/a_share/stk_surv/a_share_all_stk_surv_latest` |
| `broker_recommend` | `assets/tushare/a_share/broker_recommend/a_share_all_broker_recommend_latest` |
| `fund_portfolio` | `assets/tushare/a_share/fund_portfolio/a_share_all_fund_portfolio_latest` |
| `top10_holders` | `assets/tushare/a_share/top10_holders/a_share_all_top10_holders_latest` |
| `top10_floatholders` | `assets/tushare/a_share/top10_floatholders/a_share_all_top10_floatholders_latest` |
| `stk_holdertrade` | `assets/tushare/a_share/stk_holdertrade/a_share_all_stk_holdertrade_latest` |
| `moneyflow_ths` | `assets/tushare/a_share/moneyflow_ths/a_share_all_moneyflow_ths_latest` |
| `limit_list_ths` | `assets/tushare/a_share/limit_list_ths/a_share_all_limit_list_ths_latest` |
| `margin_detail` | `assets/tushare/a_share/margin_detail/a_share_all_margin_detail_latest` |
| `margin` | `assets/tushare/a_share/margin/a_share_all_margin_latest` |
| `namechange` | `assets/tushare/a_share/namechange/a_share_all_namechange_latest.parquet` |
| `margin_secs` | `assets/tushare/a_share/margin_secs/a_share_all_margin_secs_latest.parquet` |
| `st_history_reconstructed` | `assets/tushare/a_share/st_history_reconstructed/a_share_all_st_history_reconstructed_latest.parquet` |
| `st_intervals_reconstructed` | `assets/tushare/a_share/st_intervals_reconstructed/a_share_all_st_intervals_reconstructed_latest.parquet` |

| `hsgt_top10` | `assets/tushare/a_share/hsgt_top10/a_share_all_hsgt_top10_latest` |
| `ths_index` | `assets/tushare/a_share/ths_index/a_share_all_ths_index_latest` |
| `ths_member` | `assets/tushare/a_share/ths_member/a_share_all_ths_member_latest` |
| `daily_clean` | `assets/tushare/a_share/daily/a_share_all_daily_clean_latest` |

| `flow_ownership_features` | `assets/tushare/a_share/flow_ownership_features/a_share_all_flow_ownership_features_latest` |
| `hotspot_features` | `assets/tushare/a_share/hotspot_features/a_share_all_hotspot_features_latest` |
| `fund_portfolio_features` | `assets/tushare/a_share/fund_portfolio_features/a_share_all_fund_portfolio_features_latest` |
| `holder_structure_features` | `assets/tushare/a_share/holder_structure_features/a_share_all_holder_structure_features_latest` |
| `top_inst_events` | `assets/tushare/a_share/top_inst_events/a_share_all_top_inst_events_latest` |
| `holdertrade_events` | `assets/tushare/a_share/holdertrade_events/a_share_all_holdertrade_events_latest` |
| `hsgt_market_features` | `assets/tushare/a_share/hsgt_market_features/a_share_all_hsgt_market_features_latest` |
| `normalized_fundamentals` | `assets/tushare/a_share/normalized_fundamentals/a_share_all_normalized_fundamentals_latest` |
| `pit_fundamentals` | `assets/tushare/a_share/pit_fundamentals/a_share_all_pit_fundamentals_latest` |
| `industry_changes` | `assets/tushare/a_share/industry_changes/a_share_all_industry_changes_latest` |
| `universe_by_date` | `assets/universe/a_share_all_full_by_date.csv` |
| `universe_symbols` | `assets/universe/a_share_all_full_symbols.txt` |
| `universe_meta` | `assets/universe/a_share_all_full_by_date.meta.yml` |

`daily_clean.is_st` 仅在构建时提供已验证且覆盖全区间的 `st_history_reconstructed`
资产后才有布尔值。未提供时为未知值，不能将最新 instruments 名称回填为历史 ST 状态。
构建清单记录 `st_history_file` 来源，研究级质量检查要求这项来源存在。

DailyWatch20 的 `ths_hot_strict_v2` 与 `ths_hot_strict_v3` 快照都保留 TuShare 原始排名，
不补号。历史兼容策略 v2 要求前 20 名完整，生产策略 v3 要求 rank 1 存在，并允许整张
快照最多缺 2 个其他排名。存在缺口时回执记录 `rank_coverage_status=degraded`、原始
`missing_ranks` 与 `max_missing_ranks=2`。缺少 rank 1 或缺口超过 2 个时仍拒绝发布。

基本面 revision-safe 链路从 raw v2 开始。raw query unit 必须记录请求开始/完成时间、内容
SHA-256、字节数和精确 retrieval timestamp。raw、normalized v2 与 PIT v2 的完成目录都包含
`integrity.files`、`integrity.aggregate_sha256` 和 `manifest.seal.json`，且
`immutable_snapshot=true`。严格读取和发布校验会复算文件 hash 与 manifest seal。已完成目录
只能作为不可变版本读取，新观测写入新的 dated snapshot。`revision_safety.revision_safe_from`
之前的报告期属于 `reconstructed_pit`。周期归档位于
`assets/tushare/a_share/fundamentals_vintages/vintage=YYYYMMDD`，根目录的 `SEALED.json` 绑定
所有子 manifest。该归档目录不参与 current alias 选择。

`dc_concept_cons` 保持 `tushare.dc_concept_cons.v1` 既有 schema，并以新增字段提供逐交易日
完整性证据。下游必须读取
`completeness.trade_dates[<trade_date>].complete`，不能只依据顶层 `status: completed` 或目录中
存在 Parquet。逐日 receipt 同时包含 `row_count`、`page_count`、`request_count`、
`distinct_theme_count`、`terminal_page_reached` 和 `coverage.row_coverage_ratio`。顶层
`complete` 与 `completeness.complete` 只是整批日期的汇总。空结果会写入 `complete: false`，
并保留上一次 Parquet 作为 last-known-good 数据，但该旧分区不得通过目标日生产门禁。

不传 `--provider` 的中国大陆市场 contract 默认使用 `tushare` 布局（当前 A 股主线为 TuShare）。单个
`a_share_current.json` 只表示当前采纳的 provider，不汇总多个 provider 的 raw 快照。
如果只迁入了小样本，仍应使用 canonical `a_share_current.json` 作为下游入口，但 health
报告需要明确列出尚未生产的 `adj_factor`、`limit_status`、`daily_clean` 等资产。

`marketdata contract inspect` 会输出各 manifest 的覆盖起止日期与总量。A 股候选研究
入口应显式传入 `--require-start-date YYYYMMDD`。如果资产起点晚于要求，检查会
生成 warning，并可用 `--fail-on-severity warning` 阻断 promotion。

## 数据集注册表

```text
<artifacts_root>/metadata/dataset_registry.csv
```

该注册表是面向人工查看的精简索引文件，内容由当前数据契约和各项数据资产的数据清单推导生成。当前数据契约才是下游读取路径时的权威入口。

也可以单独重建注册表：

```bash
marketdata registry build \
  --artifacts-root "$DATA_PLATFORM_ROOT"
```

## 数据清单规范

每一个正式发布的数据资产目录中，都必须包含一个 `manifest.yml` 文件。对于单文件形式的数据资产，则应在其同级目录下提供一个对应的 `*.manifest.yml` 文件。数据清单需要给出足够清楚的字段，便于下游代码快速获取数据集概况，包括数据集状态、数据行数、标的代码（Symbol）数量、日期范围以及数据血缘（Lineage）。

## Python 只读契约 API

下游 Python 项目可通过 `PublishedAssetContract` 读取 current contract，并按资产键加载
磁盘上的完整 manifest：

```python
from market_data_platform import PublishedAssetContract

contract = PublishedAssetContract.load_current(
    "/data/market-data-platform",
    market="a_share",
)
pit = contract.asset("pit_fundamentals")

print(pit.resolved_path)
print(pit.manifest["semantics"])
print(pit.provenance_dict())
```

该 API 有以下边界：

* current contract 负责选择发布版本。读取器会重新加载 `manifest_path` 指向的完整
  manifest，不把 current contract 内的摘要当作完整 schema。
* `alias_path`、`resolved_path`、`manifest_path` 和显式数据相对路径都必须位于
  `artifacts_root` 内。指向根目录外的绝对路径、`..` 路径和逃逸软链接会被拒绝。
* `manifest_sha256` 表示 manifest 文件的精确字节摘要。`content_fingerprint` 是完整
  manifest 的规范化内容摘要，调整 YAML 排版不会改变后者。
* `provenance_dict()` 提供可序列化的 contract hash、manifest hash、schema version、
  lineage 和资产路径，供研究产物记录来源。
* 构造资产引用不会遍历大型 Parquet 目录。若 manifest 需要覆盖逐文件数据校验，应由
  发布流程把对应 checksum 写入 manifest，读取器会将这些字段原样保留在完整 manifest
  和 lineage 中。
