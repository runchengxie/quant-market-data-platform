# A 股基本面 raw-to-PIT 运维

本页说明 TuShare A 股基本面如何进入平台原生资产链路。`daily_basic` 的 PE、PB、市值和换手率
只提供逐日估值 overlay。财务报表 PIT fundamentals 必须经过披露日语义校验。

## 数据集清单规格

平台声明十类数据集：`income`、`balancesheet`、`cashflow`、`forecast`、`express`、
`dividend`、`fina_indicator`、`fina_audit`、`fina_mainbz`、`disclosure_date`。

```bash
marketdata tushare list-a-share-fundamentals-specs
```

每个规格会记录 API、VIP batch API、安全 non-VIP fallback、查询粒度、日期字段、报告期字段、
披露字段、主键、dedupe key、必需字段、刷新方式和 entitlement policy。VIP batch 不可用时，
只能使用规格已声明的逐标的 fallback。没有安全 fallback 的数据集必须记录 skip/failure，
不能把不完整结果作为当前数据契约发布。

## 原始下载

先 dry-run 查询单元，再按数据集下载。下载器会持久化 `state.json`、`failures.json` 和
`manifest.yml`，并按 query unit 保存 parquet part：

```bash
marketdata tushare plan-a-share-fundamentals \
  --dataset income --dataset balancesheet --dataset cashflow \
  --start-date 20150101 --end-date 20251231 \
  --entitlement-mode vip_batch

marketdata tushare download-a-share-fundamentals \
  --dataset income \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fundamentals_raw/income_2015_2025" \
  --start-date 20150101 --end-date 20251231 \
  --entitlement-mode vip_batch \
  --retry-attempts 3 --stale-after-days 30
```

逐标的 fallback 需要 `--symbol` 或 `--symbols-file`。分页下载会防止重复页、无限页、缺列和
字段漂移。失败 query unit 不会推进 contiguous watermark。中断后重复运行同一输出目录即可
跳过已完成且未过期的 unit。

```bash
marketdata tushare check-a-share-fundamentals-state --state-file <raw-dir>/state.json
marketdata tushare list-a-share-fundamentals-failures --failure-file <raw-dir>/failures.json
marketdata tushare compact-a-share-fundamentals-raw --raw-dir <raw-dir> --out-dir <compact-dir>
```

## Normalized 与 PIT

raw 层保留 provider 返回的 report type。normalized 层才按 dataset policy 选择标准报表类型、
规范 symbol/date、去重并保留 raw provenance：

```bash
marketdata tushare normalize-a-share-fundamentals \
  --dataset income --raw-dir <raw-income-dir> --out-dir <normalized-income-dir>

marketdata tushare validate-a-share-normalized-fundamentals \
  --asset-dir <normalized-income-dir> --target-date 20260529
```

PIT builder 只接受 normalized 输入。缺少可用报告期或披露日的行进入 quarantine，不允许在
研究面板中提前可见：

```bash
marketdata tushare build-a-share-fundamentals-pit \
  --normalized-dir <normalized-income-dir> \
  --normalized-dir <normalized-balancesheet-dir> \
  --normalized-dir <normalized-cashflow-dir> \
  --field-map revenue=revenue \
  --field-map total_assets=total_assets \
  --field-map n_cashflow_act=n_cashflow_act \
  --available-delay-days 1 \
  --max-observation-age-days 3 \
  --out-dir <pit-dir>

marketdata tushare validate-a-share-fundamentals-pit \
  --asset-dir <pit-dir> --target-date 20260529
```

raw manifest 的 `query.start_date` 和 `query.end_date` 只描述报告期查询边界。它们不提供观测
时点或 freshness 证据。normalized v2 把每个 part 的采集时间传播到
`_source_retrieved_at`，并用 `observed_vintage_dates` 记录完整归档包的观测日期。单次 raw 包跨
多个 part 或多个采集日时，整包完成日采用最晚 part retrieval date。

PIT v2 的事件主键是
`symbol + trade_date + report_period + _source_retrieved_at`。同一可用日披露多个报告期时必须
保留多行，禁止跨报告期拼字段。同一事件在不同 retrieval vintage 的记录构成有序修订。同报告期、
同可用日、同来源和同一 retrieval timestamp 出现冲突值时构建器 fail closed。

TuShare 报表可能在同一次观测中同时返回 `update_flag=0` 的初始值和 `update_flag=1` 的更新值。
normalized 会先在完整事件键内保留数值最高的 `update_flag`，并把被替代行计入
`dropped_rows.superseded_update_flag`。
三张报表只接受官方定义的 `comp_type=1..4`，未定义类型计入
`dropped_rows.unsupported_comp_type`。同一 `f_ann_date` 下存在多次公告记录时，
保留较晚 `ann_date` 并计入 `dropped_rows.superseded_ann_date`。最高 flag 和最新公告日内若仍有
财务值冲突，构建器继续 fail closed。

`available_date` 是披露日加配置的日历日延迟。`trade_date` 只是兼容列，不保证是交易所开市日。
as-of view 使用 `available_date <= as_of_date`，所以周末可用的事件会在下一次传入交易日日期时
自然可见，不需要篡改原始可用日。

## 正式 as-of 读取

研究代码不得自行对事件表做全表 `ffill`。单日和多日入口都按字段选择可见的最新报告期，
再选择该报告期的最新修订，并返回字段级 revision provenance：

```python
from market_data_platform.providers.tushare_a_share_fundamentals import (
    load_pit_fundamentals_as_of_panel,
)

panel = load_pit_fundamentals_as_of_panel(
    asset_dir=pit_dir,
    as_of_dates=trade_dates,
    fields=["revenue", "net_profit", "total_assets"],
    provenance_policy="require_observed",
)
frame = panel.frame
audit = panel.audit
assert audit["revision_safe"] is True
latest = audit["observation_by_as_of_date"][audit["as_of_date"]]
assert latest["bundle_available_date"] <= audit["as_of_date"]
assert latest["revision_covered"] is True
assert latest["freshness_verified"] is True
```

`frame` 每个字段同时带有 `__report_period`、`__available_date`、`__disclosure_date`、
`__source_dataset`、`__source_raw_asset`、`__source_run_id`、`__source_retrieved_at` 和
`__revision_id`。多日入口只读取一次经过 `fields` / `symbols` 投影的事件集。

默认的 `require_observed` 同时要求 `available_date <= as_of_date` 和
`_source_retrieved_at <= as_of_date`。未来采集的行会被排除。缺少目标日前归档 vintage 的组件会让
`revision_covered` 失败，严格视图返回空状态。2026 年才抓取的历史回填不能进入 2020 年的
revision-safe 证据。

archive ladder 按逻辑组件聚合。同一 `source_dataset` 的多个 normalized snapshot 形成一条
vintage 序列。不同 dataset 各自选择目标日前最近 vintage，不要求 retrieval date 完全相同。
`bundle_available_date` 取各组件最近 vintage 的最大值，
`oldest_component_retrieval_date` 取最小值，`observation_age_days` 从最老组件计算。

revision coverage 和 freshness 分开记录。生产默认允许 3 个日历日的 observation age，以覆盖
周末和跨 UTC 采集。目标日必须同时满足 `revision_covered` 和 `freshness_verified`。
`allow_unverified` 只用于明确标记为 `legacy_unverified` 的探索，不得晋级生产、参数选择或正式
样本外结论。

## 发布闸门

只有 normalized 和 PIT validation 都通过后，才允许更新 latest alias、重建
`metadata/current_assets/a_share_current.json` 和 `metadata/dataset_registry.csv`。每个
`--normalized-dir` 指向一个不可变的 normalized v2 数据集（如归档快照
`fundamentals_vintages/vintage=YYYYMMDD/normalized/<dataset>`），全部通过校验后才发布：

```bash
marketdata tushare publish-a-share-fundamentals \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --normalized-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fundamentals_vintages/vintage=20260815/normalized/income" \
  --normalized-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fundamentals_vintages/vintage=20260815/normalized/balancesheet" \
  --normalized-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fundamentals_vintages/vintage=20260815/normalized/cashflow" \
  --normalized-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fundamentals_vintages/vintage=20260815/normalized/fina_indicator" \
  --pit-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fundamentals_vintages/vintage=20260815/pit" \
  --target-date 20260817
```

传入多个 `--normalized-dir` 时，发布入口先把各数据集组装成带顶层 manifest 的不可变
`normalized_fundamentals` 快照目录（`assets/tushare/a_share/normalized_fundamentals/
a_share_all_normalized_fundamentals_<vintage>`，各数据集保留在 `components/<dataset>/`），
再对合并资产和 PIT 统一校验，最后才把 latest alias 切换到合并目录。只传一个
`--normalized-dir` 时保持旧行为，alias 直接指向该目录。

如果本次只需要发布 PIT 财务资产，不更新 normalized fundamentals alias，可以使用下列入口。
但 current normalized alias 必须已经存在、通过 v2 provenance 校验并覆盖同一 `target_date`：

```bash
marketdata tushare publish-a-share-pit-fundamentals \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --pit-dir <pit-dir> \
  --target-date 20260529
```

promotion 会同时要求 normalized/PIT 使用 v2 schema，具备目标日前的完整观测 vintage，并且
`observation_age_days <= max_observation_age_days`。报告期 query end 不能替代这组证据。旧 v1、
PIT stale、normalized missing 或 source retrieval provenance 缺失都会在切换 alias 前失败。
发布失败时保留 raw、state、failure、quarantine 和 validation report 排障，不更新当前数据契约。

## Revision-safe vintage 归档

从 2026-08 起，正式 revision-safe 证据必须来自不可变的定期观测快照。raw v2 为每个 query
unit 记录请求开始/完成时间、parquet 内容 SHA-256 和字节数。normalized/PIT v2 继续传播源
manifest hash，并为自己的 parquet 建立 SHA-256 inventory。完成快照不可原位 stale refresh，需
使用新的 dated output directory。校验或严格 as-of 读取发现文件 hash 变化时会 fail closed。

下列入口默认通过 `https://proxy-a.example.com`，使用 `TUSHARE_TOKEN_2`，每个快照覆盖 2015 年起
的 `income`、`balancesheet`、`cashflow` 和 `fina_indicator`，构建 normalized 与核心 PIT 后封存。
它只写 `fundamentals_vintages/vintage=YYYYMMDD`，不会调用 publish，也不会切换 latest：

```bash
.venv/bin/python scripts/operations/archive_tushare_fundamentals_vintage.py \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --snapshot-date 20260802 --start-date 20150101 \
  --token-env TUSHARE_TOKEN_2 \
  --api-url https://proxy-a.example.com \
  --observation-frequency daily

.venv/bin/python scripts/operations/archive_tushare_fundamentals_vintage.py \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --snapshot-date 20260802 --verify-only
```

一次性长历史稳健性研究不得改写已封存的同日快照。应使用独立实验 artifacts root，并显式把
报告期窗口扩到 2008 年。`netprofit_yoy`、`or_yoy`、`q_sales_yoy` 可作为成长字段映射。该快照
仍只从采集日开始 revision-safe，采集日前的报告期仍是 reconstructed PIT：

```bash
.venv/bin/python scripts/operations/archive_tushare_fundamentals_vintage.py \
  --artifacts-root "$DATA_PLATFORM_ROOT/experiments/style-factor-full-history-20260802" \
  --snapshot-date 20260802 --start-date 20080101 \
  --token-env TUSHARE_TOKEN_2 \
  --api-url https://proxy-a.example.com \
  --observation-frequency ad_hoc
```

同日中断后可续跑。`SEALED.json` 出现后，重复运行只做全链路复验。默认 systemd timer 每天
02:30（Asia/Shanghai）归档一次，形成日频 observation ladder。它仍不能证明两次归档之间发生并
消失的日内修订。首个观测日前的所有历史继续标记为 `reconstructed_pit`，不能反向声明为
revision-safe。日频完整快照会提高 provider 请求量和存储占用，因此部署后应继续监控配额与磁盘，
但不通过降低校验、覆盖已有 vintage 或自动 publish 来换取速度。

## 公告事件版本研究资产

如果研究需要利用 TuShare 返回的 `ann_date`、`f_ann_date`、`report_type` 和 `update_flag`，但
又不希望把 provider 的版本行在 normalized 层提前压缩，可以从 immutable raw asset 构建一个
研究专用的 announcement event PIT：

```bash
marketdata tushare build-a-share-announcement-event-pit \
  --dataset income \
  --raw-dir <raw-asset>/data/income \
  --out-dir <research-event-asset> \
  --value-column revenue \
  --value-column n_income
```

该资产按 `f_ann_date`、缺失时再按 `ann_date` 生成 `available_date`，保留每一行的
`report_type`、`update_flag`、`event_id`、`revision_id` 和 `row_hash`，不会把不同报表类型或
provider 版本自动合并。它的 schema 是
`tushare.a_share.fundamentals.announcement_event_pit.v1`，并且明确标记
`research_only: true`、`complete_revision_history: false`。

因此它可以支持 announcement-time PIT 探索，但不能被解释成 TuShare 已承诺的完整历史
revision database，也不能直接用于 production promotion。生产主视图仍使用经过 normalized/PIT
验证的标准报表资产。

### Announcement event as-of panel policy

研究代码在把事件资产转换成某一历史日期的 panel 时，必须显式选择 `report_type` policy：

- `standard`：只使用 `report_type=1`，适用于标准合并报表。
- `diagnostic_including_type5`：分别保留 `report_type=1` 和 `5`，用于敏感性分析，不能直接视为一个标准 panel。
- `all`：保留所有报表类型，仅用于诊断。
- `dataset_native`：仅适用于数据集本身没有 `report_type` 字段的情况，例如 `fina_indicator`。该限制必须记录在审计结果中。

as-of 读取先限制 `available_date <= as_of_date`，再按报告期选择当时可见的最新事件。不能按
`(symbol, report_period)` 直接对原始数据取最后一行。入口为
`load_announcement_event_as_of_panel`，返回 panel 和包含可见事件数、最终 panel 行数、日期及
policy 的 audit。该 panel 仍是 research-only，不能替代完整 vendor revision history。
