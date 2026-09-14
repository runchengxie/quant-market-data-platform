# A 股研究资产口径

> status: active
> owner: market-data-platform
> last_verified: 2026-07-20
> source_of_truth: yes
> superseded_by: n/a

本页记录 A 股日频基线、PIT 财务报表和历史行业资产的当前发布状态与使用边界。权威状态
来自 `$DATA_PLATFORM_ROOT/metadata/current_assets/a_share_current.json`。防未来函数和下游
研究分工见 [研究完整性](research-integrity.md)。

## 当前发布状态

2026-07-16 核对结果如下：

| 资产 | schema | 当前状态 | 覆盖与规模 |
| --- | --- | --- | --- |
| `daily_clean` | `tushare.a_share.daily_clean.v1` | 已发布 | 2015-01-05 至 2026-07-16，11,498,830 行，5,785 只证券 |
| `pit_fundamentals` | `tushare.a_share.fundamentals.pit.v1` | 已发布 | 快照 `a_share_top800_union_20150227_20260529_three_statement_pit`，manifest 查询区间为 1994-02-19 至 2026-06-15，252,643 行，6,292 只证券，隔离 8 行 |
| `industry_changes` | `licensed.a_share.industry_changes.v1` | 已发布 | 申万 2021 三级行业，数据截至 2026-03-04，7,780 行，5,851 只证券 |
| `normalized_fundamentals` | `tushare.a_share.fundamentals.normalized.v1` | 未发布 | current contract 中 `exists: false` |

`daily_clean` 是当前默认研究入口。`strategy-pipeline` 的 `default` 和 `default_next` 使用
日线价格、日频估值与全市场逐日股票池。财务报表和历史行业特征通过
`configs/presets/a_share_pit.yml` 显式开启。

PIT 财务快照的目录名、manifest 查询区间和研究股票池口径不同。下游应读取 contract 与
manifest，不能仅凭目录名推断覆盖范围。`normalized_fundamentals` 当前没有可消费的 alias，
程序和文档都应按缺失处理。

现有 `pit_fundamentals` 是 legacy v1。manifest 的 2026-06-15 只表示事件查询范围结束日，
没有提供 source observation vintage。current contract 会把这类资产的显式 as-of 视为缺失，
它也不能通过 v2 revision provenance 和 freshness 门禁。2026 年采集的历史记录不能进入更早日期
的 revision-safe PIT 回测。完成 normalized v2 重建、PIT v2 重建和显式 promotion 前，只能作为
`legacy_unverified` 探索资产。

## 使用边界

- `daily_basic` 只提供逐日估值 overlay，不包含财务报表 PIT 数据。
- 财务报表研究使用 `pit_fundamentals`，并通过平台正式 as-of loader 构造字段级状态。
- `available_date` 由披露日和日历日延迟规则生成。研究日期早于该日期时不得读取对应记录。
  同时必须证明 source retrieval 不晚于目标 as-of date，并检查 revision coverage 与 freshness。
- 历史行业连接使用 `effective_date` 和 `end_date`。当前行业标签不能回填历史日期。
- current contract 记录资产是否已发布。manifest 记录查询区间、截止日、行数、证券数和隔离行。
- 数据发布只证明资产可读取。完整 PIT 策略仍需通过研究窗口、基准、成本、容量和样本外门禁。

## 历史行业来源语义

历史行业资产按来源语义分别处理：

| 来源 | 语义 | 用途 |
| --- | --- | --- |
| TuShare 申万 `index_member_all` | 区间型 membership，包含三级行业、`ts_code`、`in_date`、`out_date` 和 `is_new` | 当前 `industry_changes` 的主要来源 |
| TuShare 中信 `ci_index_member` | 区间型 membership，包含三级行业、`ts_code`、`in_date`、`out_date` 和 `is_new` | 可发布为 CITIC 平行行业体系 |
| TuShare `index_classify` | 行业 taxonomy 清单，申万可区分 2014 和 2021 版本 | 校验 taxonomy 与版本，不单独作为 membership |
| 带日期的行业快照 | 指定日期成分股集合，需要记录来源、日期和频率 | 用于抽样校验，或在证据充分时推导区间 |

平台 schema 将 `in_date` 映射为 `effective_date`，`out_date` 映射为 `end_date`，
`ts_code` 映射为 `symbol`。`is_new` 只记录当前状态来源，不能替代历史区间。
`industry_system` 需要明确填写 `sw2014`、`sw2021`、`citic` 或更具体的 taxonomy 名称。

快照数据不能直接声明为精确区间。用于校验时，应记录 snapshot date、来源名称、taxonomy、
level 和频率。缺少生效历史或带日期来源信息的当前标签不能进入历史 PIT 研究。

## 发布与运维

代码库提交规格、构建器、校验器和契约键，数据本体保存在仓库外。基本面 raw-to-PIT
运维见 [A 股基本面运维](a-share-fundamentals.md)。A 股研究资产使用以下 contract 键：

| 资产 | current contract 键 |
| --- | --- |
| 日频清洗资产 | `daily_clean` |
| PIT 财务报表 | `pit_fundamentals` |
| 标准化财务中间层 | `normalized_fundamentals` |
| 历史行业变更 | `industry_changes` |
