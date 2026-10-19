# 操作手册

[English page](operations.en.md)

> status: active
> owner: quant-market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

本页是运维入口索引。具体命令按主题拆到 `docs/operations/`，避免把凭证、A 股采集、备份和本地治理混在同一页。

## 推荐入口

| 目标 | 文档 |
| --- | --- |
| 配置共享数据根目录和 provider 凭证 | [operations/credentials.md](operations/credentials.md) |
| 运行 A 股 / TuShare raw、clean、universe、当前数据刷新和 raw-to-PIT 链路 | [operations/a-share-tushare.md](operations/a-share-tushare.md) |
| 抓取、构建、发布和检查中国宏观与产业情境数据 | [operations/context-data.md](operations/context-data.md) |
| 构建公募基金一致口径前十大重仓 PIT 研究特征 | [a-share-fund-top10-ownership-features.md](a-share-fund-top10-ownership-features.md) |
| 融合 Guan 与 TuShare A 股分钟数据 | [operations/a-share-minutes.md](operations/a-share-minutes.md) |
| 归档公共源 ETF 分钟数据 | [operations/etf-minutes.md](operations/etf-minutes.md) |
| 港股归档恢复（已退役） | [operations/hk-archive-restore.md](operations/hk-archive-restore.md) |
| 做本地快照备份、本地开发检查和治理脚本 | [operations/backup-and-dev.md](operations/backup-and-dev.md) |
| 构建可发布 Python 包 | [operations/package-publishing.md](operations/package-publishing.md) |
| 审计 current/latest 路径并生成退役 dry-run | [data-governance.md](data-governance.md) |
| 准备独立停复牌与分红执行研究输入 | [执行研究输入审计](operations/execution-input-readiness.md) |
| 测试脚本与测试范围说明 | [operations/testing.md](operations/testing.md) |

## 常用命令族

- `marketdata paths`
- `marketdata contract build`
- `marketdata contract inspect`
- `marketdata registry build`
- `marketdata data catalog`
- `marketdata data materialize`
- `marketdata data query`
- `marketdata context fetch`
- `marketdata context build`
- `marketdata context publish`
- `marketdata context inspect`
- `marketdata data build-guan-annual-minutes`
- `marketdata data build-guan-deal-minutes`
- `marketdata data finalize-a-share-minute-coverage`
- `marketdata data mirror-public-etf-minute`
- `marketdata tushare backfill-etf-history`
- `marketdata tushare build-etf-daily-forward-adjusted`
- `marketdata tushare validate-etf-daily-pair`
- `marketdata governance audit-current-paths`
- `marketdata governance plan-retention`
- `marketdata tushare plan-a-share-minute-backfill`
- `marketdata tushare run-a-share-minute-backfill`
- `marketdata tushare build-a-share-fund-top10-portfolio-features`
- `marketdata tushare validate-a-share-fund-top10-portfolio-features`
- `marketdata tushare build-a-share-daily-clean --st-history-file ...`
- `marketdata tushare audit-a-share-st-event-timing --st-history <已发布 ST 历史 parquet> --st-events <st 下载 parquet> --out-dir <审计输出目录>`：核对 ST 历史中公告日等于交易日的行，输出逐行日期证据和哈希回执。`prior_dated_st_event` 表示此前已有生效的 ST 事件。`same_day_time_unknown` 不能证明盘前可用。`later_event_date_conflict` 表示事件公布及实施日期均晚于历史行。所有结果均不证明日内公告时刻，`revision_safe=false`。
- `.venv/bin/python scripts/operations/cutover_a_share_minute.py`
- `marketdata backup-data`
- `marketdata tushare ...`

公共 CLI 的文档覆盖由测试从 parser 派生检查。新增命令时，先把命令放入对应主题页，再更新测试。

历史分钟反向回填的北交所缺失策略和续跑上限见 [A 股分钟数据](operations/a-share-minutes.md)。
# 分钟分区恢复

被隔离的分钟分区可以先用 dry-run 检查，再显式应用恢复。工具会校验 sidecar 中的完整证券集合、每个交易日 241 根分钟线、现有分区是否会回退，并在应用前创建备份。

```bash
uv run python scripts/operations/restore_minute_partition_from_quarantine.py \
  --partition-dir /path/to/trade_date=20241128 \
  --quarantine-file /path/to/quarantine/part-00000.parquet \
  --trade-date 20241128 \
  --ledger /path/to/campaign/ledger.json
```

确认输出后追加 `--apply`。已有完整分区默认禁止回退，只有经过人工核对才使用 `--allow-regression`。

## Capture daily-clean raw inputs

`marketdata governance snapshot-clean-inputs --artifacts-root "$DATA_PLATFORM_ROOT"
--start-date YYYYMMDD --end-date YYYYMMDD --out-dir NEW_PATH` creates an independent,
date-filtered snapshot of daily, adjustment, daily-basic and limit-status inputs.
Serialize raw writers first. The output must be a new path under the data root.
A completed receipt pins source manifests and each captured file's SHA-256.
Failed attempts are preserved without a completed receipt. Scheduled retention
`apply` is disabled; use the reviewed lifecycle retirement conditions in
[data governance](data-governance.en.md) before any data removal.


## Private JSON configuration

Use one private configuration copied from `config/config.example.json`. Keep credentials outside Git and set mode `0600`. `DATA_PLATFORM_CONFIG` selects the file; when unset, an existing `${XDG_CONFIG_HOME:-$HOME/.config}/quant-market-data-platform/config.json` is selected. An invalid selected file stops the command. Existing process variables take precedence, including empty values. Null entries are unconfigured. Only `DATA_PLATFORM_ROOT` expands `${HOME}` or `~`; secret strings remain opaque. Legacy env files are read only when no JSON is selected.

```bash
marketdata config check --config "$DATA_PLATFORM_CONFIG"
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- python /path/to/job.py
```

`config check` reports names and configured booleans, without setting values or network requests. `config run` directly replaces the process using argv and preserves child exit status and signals. Services use an immutable installed release and a non-secret configuration path.


## QuantZone research factors

See [QuantZone operations](operations/quantzone.en.md).

```bash
marketdata quantzone check --config "$DATA_PLATFORM_CONFIG"
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG" --dry-run
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG"
```

### Statement observation ledger

`marketdata tushare build-a-share-statement-version-ledger --source-manifest <raw-manifest.yml> --source-manifest <supplement-receipt.json> --out-dir <new-external-directory>` retains verified financial statement observations in separate Parquet partitions. Existing output directories are never replaced. Source manifests must be completed and contain checksummed files with time zone aware retrieval timestamps.

The raw download and archive commands accept repeated `--report-type` options (for example `1`, `4`, `5`). Omitting this option preserves the provider default. Normalized standard statements continue to use type `1`; other report types are retained for version audits.

For multi-vintage research inputs, use `read_statement_observations(...,
dataset="income", columns=[...])` to project required fields. Include identity,
disclosure, observation, availability and source-hash fields when downstream code
selects revisions. The reader verifies all partition checksums before applying filters.
### 同花顺成分续传

`marketdata tushare mirror-a-share-ths-member --out-dir <output> --skip-existing` 显式复用已验证的概念缓存分区，补取剩余概念。默认拒绝非空输出目录。续传必须使用相同字段和数据源配置。概念成分仅代表获取时的快照。

### 单文件参考资产清单

参考资产发布会同步写入与 Parquet 同名的 `.manifest.yml`，从 owner receipt 绑定文件 hash、行数、日期和来源语义。约束资产的部分来源会保留 `partial` 状态。参考文件更新时必须同步更新 receipt 和 manifest。

参考文件的 `version_date` 记录版本标签日期。有来源 `end_date` 时，清单的 `as_of_date` 和查询结束日期使用实际来源覆盖日期。

### 龙虎榜机构成交金额单位

TuShare [top_inst](https://tushare.pro/document/2?doc_id=107) 的 `buy`、
`sell` 和 `net_buy` 单位为元。`top_inst_events` 在聚合和滚动计算前将金额
除以 10000，换算为万元，与原单位为千元的 `daily.amount / 10` 一致。
字段名保持稳定。本次修正前构建的资产须重建后再使用金额特征或成交额比率。
事件计数和数据源提供的比例不变。

本次质量预算增加一个测试文件和 58 行 Python，用于单位转换和独立算术回归。
现有复杂度阈值保持不变。只有在替代测试提供同等覆盖且减少源代码或测试行数时，
才移除此预算。

## 机构调研分页

`marketdata tushare mirror-a-share-stk-surv` 按日使用 `limit=400` 和递增的 `offset` 请求数据，只有获得不足 400 行的末页或空页后才写入当天分区。请求间隔作用于每一页，重试沿用原有 provider 策略。重复页、超限响应、调研日期不符或达到 1000 页上限时，任务失败并写入 partial 清单。其他事件镜像行为不变。

`--skip-existing` 要求存在与数据哈希、准确查询和各页行数一致的 `pagination.json` 凭证。没有凭证的旧数据应保留，并在新目录重新获取。分页完成只证明本次观察到的获取范围，不证明历史观察版本安全。[官方调研接口文档](https://tushare.pro/document/2?doc_id=275) 说明单次最多 400 行，可以循环或分页获取。重复页会触发对忽略分页的代理的拒绝，不额外声称数据源在请求期间保持稳定。

财报观测账本构建器也接受非 VIP 单证券查询的完成清单。恢复旧缓存时保留原始清单和日期声明，校验归档内容，并为恢复输入记录真实恢复时间与哈希。原始分区缺少内容校验凭证时，不要把恢复时计算的哈希与历史日期拼接成已验证旧版本。

两个 QuantZone 命令都支持 `--no-proxy`，用于在现有 HTTP 或 SOCKS 代理不适用时直连。该选项只在创建 SDK 客户端时临时移除代理环境变量，创建失败也会恢复原值。其他命令保留原有代理设置。

### 历史分钟回填容量

反向回填脚本支持 `--batch-size`（默认 20，上限 33）和 `--cooldown-seconds`（默认 1.2）。显式设置 `--minute-quota-allow-burst` 可使用共享账本的弹性区，但仍受供应商实际限额、预留和停止条件约束。默认继续只使用保底池。完整 241 根网格的 33 只股票共 7,953 行，不超过单次 8,000 行上限。扩大运行时段时保持单实例运行、断点续传和逐批内容验收。

本次质量预算增加 110 行 Python，用于显式弹性额度和命令参数，以及真实共享账本的默认拒绝与显式放行回归。文件数和复杂度门槛不增加。仅在等效覆盖能减少源码与测试行数时收回该预算。

### 分钟历史补采的日线名单核对

分钟补采以 `daily` 的实际交易股票作为请求股票池，并用 `daily_basic` 交叉核对。
历史 `daily_basic` 可能保留当天不交易的股票行。只在存在同日 `suspend_d`
全天停牌记录，或发行人公告确认的暂停上市区间时，解释其与 `daily` 的差异。
日内临时停牌、复牌记录、其他日期的记录和空行情本身均不能证明全天不交易。
未被解释的股票仍阻止下载。不会根据两个接口的交集直接丢弃差异，也不会补造分钟线。
解释过的股票与原因写入分区 `universe_source`。本改动不改变实际交易股票的请求集合，
也不把已存在的北交所或供应商缺失标记为完整。

暂停上市区间采用起日包含、恢复上市日不包含的边界。目前核实的三个区间及公告：

| 股票 | 暂停上市起日 | 恢复上市日 | 发行人公告 |
| --- | --- | --- | --- |
| 000670.SZ | 2020-04-07 | 2022-08-22 | [历史沿革](https://static.cninfo.com.cn/finalpage/2026-06-09/1225358835.PDF) |
| 000792.SZ | 2020-05-22 | 2021-08-10 | [暂停](https://static.cninfo.com.cn/finalpage/2020-05-21/1207838318.PDF)、[恢复](https://static.cninfo.com.cn/finalpage/2021-08-03/1210641780.PDF) |
| 000995.SZ | 2019-05-13 | 2020-12-16 | [暂停期间公告](https://static.cninfo.com.cn/finalpage/2020-12-05/1208857867.PDF)、[恢复](https://static.cninfo.com.cn/finalpage/2020-12-09/1208871266.PDF) |

### 指定 ETF 的有界日线快照

先预演请求范围，不读取凭证、不访问网络、不创建输出：

```bash
marketdata tushare snapshot-etf-daily --symbols 510300.SH 511260.SH \
  --start-date 20261016 --end-date 20261016 --output /path/to/new-snapshot --dry-run
```

实际运行使用相同参数去掉 `--dry-run`，通过现有 `marketdata config run` 注入凭证。
可加 `--deadline 2026-10-16T19:00:00+08:00` 限制采集截止时间、
`--max-requests 96` 限制实际尝试次数。`--baseline-manifest` 接受此前的完整快照清单。
保留失败目录并使用新目录重试，禁止覆盖已有目录。成功退出只表示采集完成、尚待验收。

采集完成后，单独运行 `marketdata tushare review-etf-snapshot`。从已安装模块导出策略哈希：

```bash
python -c 'from quant_market_data_platform.etf_snapshot_review import POLICY_SHA256; print(POLICY_SHA256)'
marketdata tushare review-etf-snapshot --manifest /path/to/new-snapshot/manifest.json \
  --reviewer owner-etf-snapshot-policy-v1 --policy-sha256 <installed-policy-sha256> \
  --output /path/to/new-review.json
```

验收器拒绝未知身份或策略哈希，并重新检查输入字节与交易日覆盖。
重复验收同一身份时保留原凭证，输入变化须使用新目录重新采集和验收。

反向回填默认将已落盘、仅有股票无响应的日期写入
`metadata/minute_backfill/reverse_scheduler/deferred_missing.json`，继续下载更早日期。
回执和分区仍保持 `partial`，不会自动发布或豁免完整性检查。
`deferred_missing` 表示本批缺口已挂起。`download_pass_complete_with_gaps` 表示首轮范围已扫描、仍有补采工作，不能解释为历史完整。
网络、额度、存储和数据校验错误不会按无响应缺口跳过。
使用 `--retry-deferred` 显式重试缺口。此模式保留未完成批次的恢复行为。

### 海外日线前向研究回执

[追加式采集与时间边界](operations/overseas-forward.md)提供独立 module 入口，不更新共享数据别名。

### 分钟历史监控与缺口补采

使用 `scripts/operations/tushare_minute_history.py status --artifacts-root <data-root> --runtime-root <runtime-root>` 只读检查进度、额度、存储和审计阻断。统计分别列出元数据完成日期、独立审计日期和北京市场省略日期。历史审计绑定失效会单独记录，下载完成仍须经过发布验收。

`watch` 使用相同参数并增加 `--state-root <monitor-root>`，写入 `status.json` 和去重的本地 `alerts.jsonl`。连续活跃且可运行的采样超过 15 分钟、至少三次没有进展时报告停滞。等待额度、空闲和审计阻断有独立状态。监控无需数据源凭证。

`repair` 只处理 `deferred_missing.json` 中有来源绑定的股票日缺口，每次最多请求一个股票日。默认每日最多 500 次请求、每项最多三次尝试，失败后从 30 分钟开始退避。它与主下载共用写入锁和额度账本。空队列不创建数据源客户端。`--dry-run` 可检查候选项。

补采保留原始分区和回执，验证通过后原子写入新的完整候选分区。语义异常会保留审计阻断，需独立排查后再恢复。候选分区仍须经过后续验收，补采不会切换发布别名。通用定时模板位于 `scripts/systemd/tushare-minute-history-watch.*` 和 `tushare-minute-gap-repair.*`，渲染后应引用稳定生产 release。

### 财报模板筛选诊断

`python scripts/operations/audit_fundamentals_templates.py --vintage <sealed-vintage> --output <new-directory>`
从完整 raw v2 输入重放标准化筛选，隔离修订筛选后仍有歧义的观测键。
诊断表、冲突原行和哈希回执保存在新的外部数据目录。
原始抓取时间保持不变，实际生成时间单独记录。
该工具不发起API请求，不发布标准化资产，也不修改封存输入或共享别名。
严格构建器仍拒绝同键冲突，诊断隔离不构成生产豁免。
