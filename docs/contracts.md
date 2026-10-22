# 共享数据契约

[English page](contracts.en.md)

> status: active
> owner: quant-market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

## 数据产物根目录

沪深历史分钟资产可通过独立契约
`metadata/current_assets/a_share_sh_sz_minute_current.json` 读取。显式调用
`PublishedAssetContract.load(path).asset("minute_1m_tushare_sh_sz_historical")`，
不要通过全市场 `load_current` 隐式选择。该正式资产的 `market_scope` 为 `SH_SZ`，
`full_market_publication_allowed` 为 `false`。全市场入口 `minute_1m`、
`minute_1m_tushare` 和 `a_share_current.json` 沿用各自契约。
版本内 `_publication_receipt.json` 保存覆盖范围、逐日哈希和缺口。
`coverage_exclusions` 明示被排除日期。日期上下界描述首尾日期，
下游必须核对实际分区与缺口。

共享的数据产物根目录用于划分数据工具与策略代码库之间的存储边界。

推荐的环境变量配置：

```bash
export DATA_PLATFORM_ROOT=/data/market-data-platform
```

`DATA_PLATFORM_ROOT` 是跨项目统一使用的环境变量，用于指定共享市场数据输入与平台产物路径。
下游系统的运行记录、缓存和报告由各自仓库管理，不通过市场数据平台的路径变量配置。

研究运行需要核对输入是否属于当前契约时，可使用
`quant_market_data_platform.contract.match_current_contract_entry` 按 alias 路径或最终解析路径匹配资产。
读取契约内容可使用 `quant_market_data_platform.contract.load_current_contract`。这些函数只读取契约，
不刷新数据，也不发布新的平台资产。路径存在性分类可使用
`quant_market_data_platform.contract.path_kind`。
输入路径的解析结果和 manifest、current contract 关联信息可使用
`quant_market_data_platform.contract.describe_input_path`，适合由下游运行记录直接保存。

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
构建清单记录 `st_history_file` 来源。研究级质量检查核验来源回执、文件哈希和日期覆盖，
并逐行比较 `is_st` 与已发布的定日 ST 历史。历史来源的 `revision_safe=false` 仍表示它不能证明原始公告时刻可用。

重建 ST 历史分别记录实际生效日期和信息可用日期。若 `ann_date` 早于生效日期，`available_from`
取生效日当天或之后的首个交易日。若 `ann_date` 等于或晚于生效日，则取公告日之后的首个交易日。
`ann_date` 缺失或格式无效，或提供的交易日历中没有后续交易日时，可用日期保持未知。
回执 schema `market-data-platform.reconstructed-st-history.v2` 标识这一契约。该保守日期规则
无法证明具体的日内公告时刻，因此 `revision_safe` 仍为 false。

`daily_clean` schema `tushare.a_share.daily_clean.v2` 在 `is_st` 旁提供对应的
`st_available_from`。股票没有生效中的 ST 区间，或来源无法确定可用时间时，该字段为空。
消费者只有在 `st_available_from` 存在且不晚于决策日时，才能将正向 ST 状态用于该决策，
可用时间未知时必须按不具备资格处理。

公开读取接口 `load_a_share_research_daily` 和 `load_daily_watch20_daily` 会在每日记录中提供
`is_st` 与 `st_available_from`。通用 A 股研究视图还提供 `is_suspended` 和 `list_date`。
两个接口都要求已完成的 `tushare.a_share.daily_clean.v2` 清单及
`daily_clean.st_available_from.v1` 契约，旧版资产会被拒绝。历史决策必须使用对应日期的
ST 字段，不能用当前 instruments 元数据替代。

`audit-a-share-st-event-timing` 读取带下载回执的 `st` 事件源及重建 ST 历史，审计 `ann_date=trade_date` 的行。输出 `st_event_timing_audit.parquet` 和哈希回执。状态为 `prior_dated_st_event`、`same_day_time_unknown`、`later_event_date_conflict` 或 `no_active_prior_event`。冲突候选仅限同一股票、之后 10 个自然日内生效的 ST 事件，仍需人工核对。该审计只比较日期，不修改已发布的 `is_st`，也不将 `st` 事件视为完整的每日 ST 状态。

`download-a-share-reference` 下载的原始参考数据会在同目录写入同名 `*.receipt.json`。回执包含查询区间、行数、质量状态和文件 SHA-256。原始回执只证明下载文件完整，不证明公告日内的可用时刻。发布流程仍需重新校验来源哈希。

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

财报标准化接受 `comp_type=1,2,3,4,7`，分别为一般工商业、银行、保险、证券和多元金融。该字段描述报表模板，合并范围由 `report_type` 选择。同一次观测中不按模板设置优先级。按供应商更新标记和公告日期筛选后，同键冲突仍由歧义检查拒绝。修补标准化时须写入新目录，保留原始抓取时间和旧版本封印，不能据此认证历史 PIT 可得性。定义见 [TuShare 利润表](https://tushare.pro/document/2?doc_id=33)、[资产负债表](https://tushare.pro/document/2?doc_id=36)和[现金流量表](https://tushare.pro/document/2?doc_id=44)。

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
from quant_market_data_platform import PublishedAssetContract

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

## Daily-clean input snapshot receipt

`a_share.daily_clean_input_snapshot.v1` records a completed, independently copied
input attempt. It contains `status`, `created_at`, `start_date`, `end_date` and
`datasets`. Each of daily, adj_factor, daily_basic and limit_status records its
root-relative `source_path`, `source_manifest_sha256`, observed `rows`, and a
`files` list of dataset-relative `path`, `bytes` and `sha256`. Per-dataset manifests
refer to `../snapshot_receipt.json` and describe captured file/row counts. Consumers
must require a completed receipt before using a new snapshot. The receipt is
published only after copies and source stability checks pass. Raw writers must
be serialized by the caller; unrelated mutable latest directories are not an
immutable lineage contract.

### Statement observations v1

The optional `statement-observations.v1` ledger preserves raw statement fields, `dataset`, `observed_at`, `source_sha256`, `source_manifest_sha256`, and `available_from`. Visibility is the later of the provider disclosure date and actual retrieval date in Asia/Shanghai, plus at least one calendar day. This is a conservative observation boundary, not an exchange execution calendar. Consumers must enforce it before selecting observations and must explicitly choose report semantics; type `4` and `5` are not interchangeable with standard type `1` statements. Historical revision completeness remains false. The ledger does not publish a current alias or authorize frozen holdout evaluation.

The shared quality baseline adds two small Python files and 277 lines for the statement ledger and its contract tests. Structural debt thresholds (large functions, large files, complexity exclusions) are unchanged. This growth is retained while the observation contract is supported; remove the module and tests together if that contract is retired.

Statement observation reads accept an optional `dataset` (`income`, `balancesheet`, or
`cashflow`) and non-empty, unique `columns` projection. Checksums remain mandatory
for every partition, including excluded datasets. Visibility and report-type fields
are loaded internally for filtering; projected results contain only requested fields.
This limits retained columns when consuming multiple observed vintages. It does not
select a financial revision or establish historical revision completeness.

The maintainability baseline accounts for 100 net Python lines added by statement
projection and its schema, visibility and integrity tests. Other debt budgets are
unchanged; future projection growth must pass the existing ratchet or replace code.

TuShare 按交易日镜像的 `totals.rows`、`totals.symbols` 和 `totals.files` 表示资产 data 目录中全部已存储 Parquet 分区，包括本次请求范围外保留的分区。`run_totals` 单独记录本次运行写入的行数、标的数、文件数和请求日期计数。跳过分区不计入本次新写行数。query 日期仍表示请求范围，不由物理盘点推定。物理盘点不证明来源完整性。逐日 receipt 仍为准，保留的 last-known-good 文件可以同时对应不完整回执。

### 龙虎榜机构成交金额单位

TuShare [top_inst](https://tushare.pro/document/2?doc_id=107) 的 `buy`、
`sell` 和 `net_buy` 单位为元。`top_inst_events` 在聚合和滚动计算前将金额
除以 10000，换算为万元，与原单位为千元的 `daily.amount / 10` 一致。
字段名保持稳定。本次修正前构建的资产须重建后再使用金额特征或成交额比率。
事件计数和数据源提供的比例不变。

## 机构调研完整性凭证

`tushare.stk_surv.v1` 保持资产键和事件日期分区目录不变，新增的 `pagination` 节记录 `page_size`、`max_pages`、`complete`、每天的各页行数、实际行数及数据哈希。分区旁的 `pagination.json` 使用 `tushare.stk_surv.pagination.v1`，记录准确查询、末页行数和 SHA256。末页必须不足 400 行，正好 400 行时仍需后续空页。恢复时校验凭证查询、数据哈希、行数及调研日期，资产总量独立统计全部保留的实际分区，`run_totals` 统计本次写入。未验证的保留分区使资产保持 partial，清单不可读时将行数及标的数记录为未知并失败。失败时保留已完成日期，清单标记 partial 并记录失败日期及异常类型，不持久化 provider 异常文本。失败日期不获得完整性凭证。这些凭证不证明历史时点可用性或数据源不可修订。

行业区间清单界定实际观察到的成员日期：`query.start_date` 是最早生效日期，`query.end_date` 是全部生效日期和非空区间结束日期的最大值。较早的已关闭区间结束日期不能排除较晚的开放成员区间。这些范围由数据值决定，不采用请求刷新日期或获取版本日期。

财报观测账本接受 `income`、`balancesheet`、`cashflow` 单证券来源和对应的 `_vip` 批量来源，映射到相同的标准数据集。两种来源均要求内容哈希、带时区的真实观测时间和财报身份字段。可见日期仍不得早于实际观测日和披露日的较晚者加延迟。恢复时补算的哈希不证明原始下载日期的内容，不能据此倒填历史可见日期。

## ETF 有界快照与验收

`marketdata.etf_snapshot.v1` 记录指定 ETF、日期范围、基线哈希、实际采集开始和结束时间、
实际请求次数、相对文件路径和 SHA-256。`daily`、`factors`、`panel`、`calendar` 分别保留
行情、复权因子、一致基准复权价格和交易日历。成功采集的状态为 `unreviewed`。失败为
`incomplete`，不得作为完整输入使用。供应商发布时间未知时为 null，不能从交易日推断。

快照逐代码请求，每次实际网络尝试计入预算（上限 96）。每个逻辑请求至多三次尝试，
退避为 1、2 秒，单次超时不超过 30 秒或截止时间剩余时长。权限和额度错误停止。
分页、完整交易日覆盖、唯一行情与因子键及正数有限价格通过后才能生成待验收快照。
基线文件必须通过哈希验证，按代码和所需列读取。每次增量补充都重新请求该代码的完整历史因子，
与基线逐键核对。发现因子修订时拒绝增量发布，使用新目录重新取得一致历史。
行情分页上限 5000，因子分页上限 2000，均计入实际请求预算。
输出目录不可复用。重试使用新目录，并通过基线身份关联已有输入。

单独的验收器采用 `owner-etf-snapshot-policy-v1`，策略描述的规范 JSON SHA-256 为策略身份。
它验证文件哈希、采集开始晚于请求末日收盘、接收时间、完整交易日覆盖、行情和因子配对、复权价格重算一致性。
`marketdata.etf_snapshot_review.v1` 的 `accepted` 仅表示这批字节在记录的 `observed_by`
时刻已经接收，不能证明历史供应商送达时间。采集过程不能自己设置此状态。

## 海外前向研究回执

`overseas-receipt-v1` 是独立追加档案，不进入现有 published asset 或港股生产契约。每次新请求的 CSV 与开始/完成回执写入 owner 数据目录，按内容 SHA256 供下游核验。[格式和时间判定](operations/overseas-forward.md)描述原始来源日期、假设闭市时间、缺失/失败状态与不合格补跑。研究预测由 QMR 管理。

三张报表的 normalized v2 在供应商 revision 选择前规范化 `comp_type` 的空白，并将其加入 `semantics.dedupe_keys`。`semantics.statement_template_identity` 明确记录模板维度。更新标记和公告日期仅在同一模板内排序，同模板冲突仍拒绝。不同模板的观测全部保留，不建立数值大小、非空数量或抓取时间优先级。PIT v2 新增可选来源列 `_source_comp_type`，as-of 字段溯源增加 `<field>__source_comp_type`。flat PIT 构建、流式验证和查询对同一 symbol、报告期、可用日及来源的多模板记录拒绝，即使值相同、为空或抓取时间不同。需另有明确的模板选择契约才能生成单值。旧资产缺少模板列时按未知模板兼容读取，不推断公司类型，也不补写旧资产。schema 仍为 v2，模板能力由 manifest semantics 和实际列共同标识。

`_source_comp_type` 使用规范 JSON 对象保存来源数据集与模板的对应关系，例如 `{"balancesheet":"7","income":"2"}`。事件合并保留此对应关系，不分别拼接来源集合与模板集合。兼容单来源的旧标量模板。空值仍表示未知身份。多来源且模板非空的旧标量信息无法恢复对应关系，因此拒绝消费。

### 显式公司类别选择：只读预检 v1

`comp_type` 是供应商的公司类别，`report_type` 才是报表类型。现有代码中的
`template identity`用于保留类别身份，不表示供应商定义了另一种报表口径。
`statement-selector.v1` 仅描述人为提供的精确事件决定，验证器不推断类别优先级。
其 `valid` 只表示机械覆盖、输入身份、文件哈希和时间约束合格，不证明经济选择正确，
也不证明证据内容、审批或历史送达时间。报告始终为 `publication_ready=false`、
`evidence_content_verified=false`。该契约尚未接入任何 PIT 构建或发布入口。
现有多类别失败保护保持有效。

顶层必须且只能包含以下字段。未知字段（包括字段投影）拒绝。

| 字段 | v1 约束 |
| --- | --- |
| `schema_version` | 固定 `statement-selector.v1` |
| `policy_id` / `policy_version` | 非空字符串，由决策提供方明确命名及版本化 |
| `available_delay_days` | 至少 1 的整数，按自然日计算，日期运算必须在 YYYYMMDD 可表示范围内 |
| `normalized_inputs` | 非空数组，每个 dataset 一个完整 Parquet 文件 |
| `decisions` | 精确事件决定数组。允许为空，以列出全部未覆盖事件 |

每个 input 必须且只能包含 `dataset`、`path`、`sha256`。dataset 限
`income`、`balancesheet`、`cashflow`。相对路径以 selector 文件所在目录解析。
SHA256 是完整文件实际校验，必须为 64 位小写十六进制。源文件以同一只读文件描述符
完成读取前后哈希及两遍扫描，并核对读前、读后的 inode、路径身份和变更元数据。
原子替换、链接改向或扫描中改写均拒绝，解析失败也执行读后一致性检查。
报告的 selector 哈希直接来自实际解析的同一份字节。文件必须保留
`symbol`、`report_period`、`disclosure_date`、`comp_type`、`report_type`、
`_source_retrieved_at`。身份非空，日期为有效 YYYYMMDD，报告期不得晚于披露日。
全部输入按 symbol、报告期、披露日、comp_type 检查候选身份唯一性，
单类别事件、财务值完全相同的重复行和空 decisions 也拒绝。
本版仅接受 normalized `report_type="1"`，不能把后发 `report_type=4` 修订
解释为原公告时可得数据。`comp_type` 按输入字符串核对，不假定类别全集。

每个 decision 必须且只能包含 `dataset`、`symbol`、`report_period`、
`disclosure_date`、`available_date`、`allowed_comp_types`、`observed_at`、
`evidence`、`reason`。精确主键包含 dataset、symbol、报告期、披露日和可用日。
`available_date` 必须等于披露日加指定延迟。`allowed_comp_types` 必须恰选一个
实际候选，不能按字段补空、值相等或更新标记自动决定。每个多类别事件必须恰好覆盖
一次。漏选、重复覆盖、类别不存在、候选身份重复、无对应冲突事件的规则均拒绝。
字段差异仅供审阅，绝不参与选择，验证器不提供字段投影。

`observed_at` 记录决定的实际观察时间。`evidence` 必须且只能包含 `uri`、
`sha256`、`observed_at`。理由为非空 `reason`。URI 必须有 scheme，证据 SHA256
只验证格式，不抓取 URI，也不核验证据字节。所有观察时间必须含时区且不得在未来。
证据和源观察不能晚于决定。填写时间及证据真实性仍须决策提供方负责。

`statement-selection-report.v1` 在顶层绑定 selector 文件哈希、policy 身份及
所有输入哈希。`decision_ledger` 保留每个冲突事件的候选、选择、拒绝类别、源哈希、
逐字段数值差异（含缺失）、原始 source/decision/evidence 观察时间和理由。
`conservative_available_from_preview` 取披露日及三种实际观察时间在上海的日期最大值，
再加延迟，仅是保守预览，不能替代现有 PIT 可用时点契约。
该预览晚于历史可用日时标记 `reconstructed`。未覆盖事件也按源观察时间标记历史重建。
不产生数据资产、manifest、seal、current 别名或经济选择。

共享清点基线为此独立预检精确增加 2 个 Python 文件、739 行：验证器 361 行、
28 项契约与竞态回归测试 351 行、CLI 接线和帮助 27 行。
`python_files` 为 526，`python_lines` 为 124368。结构债务、复杂度、长行、
超大文件及测试规模阈值均保持原值。预检退役或合并且保留等效覆盖、身份、
时间和源文件一致性保护后，收回对应文件及行数清点额度。


## 亚洲指数运维回执

`overseas-index-operational-v1` 用于运维恢复，与研究采集窗口和已发布市场资产分开。
每次运行使用独立目录，依次写入 `started.json`、各指数原始 CSV，最后写入
`receipt.json`。消费者只接受最终回执。支持 HSI、N225、KS11。
来源记录包含 CSV 文件名、SHA256、行数、交易日期、`tushare-compatible` 来源标识、
路由主机名和带时区的 UTC 请求时间。回执还记录请求日期、运行起止时间和代码哈希。
整体状态为 `complete`、`partial` 或 `failed`。单一来源状态为 `ok`、`empty` 或 `failed`。
失败只记录异常类型和脱敏分类 `permission`、`transport`、`provider`，不保存原始异常消息。
分类依据已知错误特征，无法识别的提供方错误归入 `provider`。

消费者校验准确的交易日、有限正数收盘价、文件哈希、大小上限和观察截止时间。
晚到的回执不能作为较早报告的证据。本协议不改变研究资格或当前资产别名。
