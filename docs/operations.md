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

晚间融资融券的延迟补齐及 receipt 状态见 [A 股操作说明](operations/a-share-tushare.md#晚间融资融券的延迟补齐)。

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

## 捕获 daily-clean 原始输入

`marketdata governance snapshot-clean-inputs --artifacts-root "$DATA_PLATFORM_ROOT"
--start-date YYYYMMDD --end-date YYYYMMDD --out-dir NEW_PATH` 捕获四类日期过滤后的输入：
`daily`、`adj_factor`、`daily_basic`、`limit_status`。先串行化原始写入、快照捕获和退役操作。
发布脚本应在已有 publication 锁内执行。输出必须是数据根下不存在的新路径。
完成的 `snapshot_receipt.json` 固定源 manifest 散列与每个数据文件的 SHA256。
失败尝试保留现场，不发布 completed receipt。

追加 `--reuse-from PRIOR_SNAPSHOT`，可复用同一 `daily_clean_inputs` 根下已完成、未退役的
带时间戳快照。仅当分片为只读普通文件，完整供体清单、receipt、实际 SHA256/大小均通过验证，
且当前原始分片字节相同时，才创建快照之间的硬链接。原始输入始终与快照保持不同 inode。
新 manifest 与 receipt 独立创建。新增或变更分片照常复制。硬链接跨文件系统时回退复制，
权限等其他错误直接失败。

发布脚本可使用互斥选项 `--reuse-latest-completed`：输出必须直接位于
`assets/tushare/a_share/daily_clean_inputs`，按捕获时间和数字后缀选取最新完成的规范快照。
历史归档符号链接（含失效链接）、退役目录和没有 receipt 的未完成尝试不参与选择。
自动选择不解析或读取归档链接目标。真实已完成候选无效时失败，不静默换用更旧版本。
显式 `--reuse-from` 仍拒绝符号链接，只接受规范的实际快照目录。
没有候选时全部复制。该选项不修改旧目录或 latest 发布形式。
receipt 的 `storage` 记录供体路径及 receipt 指纹、复用/复制文件数与逻辑字节数。
文件记录的 `storage_method` 区分硬链接和复制。这些字节数描述逻辑复用量，实际空间按 inode 与磁盘块另行统计。

所有已发布快照分片必须保持只读，后续修正写到新路径，不能原地改写共享 inode。
创建硬链接会改变供体 link count 与 ctime，但不改变其内容、权限或 mtime。
移除任一快照中的硬链接名称不会使其他快照的数据消失。恢复依赖仍按退役 receipt 保护。
原有读取路径与 manifest 布局保持不变，生产者回退后仍可读取已经生成的快照。
定时 retention `apply` 仍禁用。删除前遵守[数据治理](data-governance.md)的审阅条件。


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


`status` 和 `watch` 的 `pipeline` 将进度分为下载、语义审计、覆盖认证和正式发布四个阶段。
`acquisition.deferred_dates` 读取当前补缺队列，当前数量以该队列为准。
队列缺失或格式无效时数量为 `null`，不能视为零。语义审计按验收范围分别计数，
`coverage.full_market_audited_dates` 仅统计完整市场验收。指定股票池验收和允许北京市场省略
不会升级为完整市场认证，`full_market_ready` 仍受独立发布门禁约束。
前三个阶段的范围是历史 staging，`publication` 则是当前已发布资产的独立日期窗口，
其日期数和行数只读取经过路径及版本校验的 operational receipt。缺少有效回执时为 `null`。
这些窗口不能直接相加或用来计算统一完成率，队列清零也不表示历史数据已正式发布。

下载进程退出后，若 `download-complete.json` 明确记录首轮下载结束且质量验收待完成，
监控会重新核对当前库存：已有完成日期，剩余交易日和补缺队列均为零、隔离日期与回执一致，
且没有活跃写入、审计阻断或证据读取异常时，报告 `download_complete_with_gaps`。
额度账本仍须可只读验证，未来时间或未来额度日期仍会拒绝完成分类。低磁盘保护继续生效。
回执提供补缺队列路径时须匹配当前权威队列，旧回执无路径字段时仍核对当前队列。
此时下载预检过期或跨日不会单独触发故障。其时间戳和新鲜度仍保留在输出中。
缺少完成回执、库存未知或不一致、写入重新启动时，仍执行原有的新鲜度检查。
该状态仅表示首轮采集结束，隔离告警、语义审计缺口和完整市场发布门禁继续独立生效。

`watch` 使用相同参数并增加 `--state-root <monitor-root>`，写入 `status.json` 和去重的本地 `alerts.jsonl`。连续活跃且可运行的采样超过 15 分钟、至少三次没有进展时报告停滞。等待额度、空闲和审计阻断有独立状态。监控无需数据源凭证。

`repair` 只处理 `deferred_missing.json` 中有来源绑定的股票日缺口，每次最多请求一个股票日。默认每日最多 500 次请求、每项最多三次尝试，失败后从 30 分钟开始退避。它与主下载共用写入锁和额度账本。空队列不创建数据源客户端。`--dry-run` 可检查候选项。

补采保留原始分区和回执，验证通过后原子写入新的完整候选分区。语义异常会保留审计阻断，需独立排查后再恢复。候选分区仍须经过后续验收，补采不会切换发布别名。通用定时模板位于 `scripts/systemd/tushare-minute-history-watch.*` 和 `tushare-minute-gap-repair.*`，渲染后应引用稳定生产 release。

### 显式隔离已复现的分钟语义异常

对于供应商重复返回相同矛盾值的股票日，使用独立审计和重新查询证据登记隔离：

```bash
python scripts/operations/tushare_minute_history.py quarantine \
  --artifacts-root <data-root> --runtime-root <runtime-root> \
  --acceptance <failed-acceptance.json> --requery <raw-stock-day.parquet> \
  --trade-date YYYYMMDD --symbol <ts-code> --reason '<investigation conclusion>'
```

登记无需凭证，使用主下载共用的写入锁。它重新验证来源绑定、完整分钟网格和基础语义，要求异常仅涉及指定股票的成交金额硬校验，并要求重新查询的整个股票日与原值相同。其他错误继续阻断。记录位于 `metadata/minute_backfill/reverse_scheduler/semantic_quarantine.json`，绑定原始分区、sidecar、失败审计和重新查询文件的 SHA256。原文件和阻断凭证保持原样。

主下载仅在当前阻断被隔离记录完整覆盖时继续采集。新增异常、证据变化或损坏记录仍会暂停。主机自定义预检必须使用 `quarantine_status(root, marker)["blocked"]`，继续仅检查文件是否存在会保持暂停。普通缺口补采同样遵守此门禁，额外错误保存在 `semantic-audit-additional-blocks/` 并继续阻断。

隔离日期在分钟分区验收、候选发布、运行版本组装和别名切换时被拒绝。监控显示 `semantic_quarantine_dates` 并记录去重的 warning，首轮采集结束仍返回 `download_pass_complete_with_gaps`。该日期的正确分钟值需要另行核实和修复，本命令保留隔离状态，未提供自动解除或发布豁免。监控会流式核对隔离证据哈希，避免将已变化的证据继续视为已隔离。

### 财报模板筛选诊断

`python scripts/operations/audit_fundamentals_templates.py --vintage <vintage> --output <new-directory> [--dataset income]`
从完整 raw v2 输入重放标准化筛选，隔离修订筛选后仍有歧义的观测键。
诊断表、冲突原行和哈希回执保存在新的外部数据目录。
原始抓取时间保持不变，实际生成时间单独记录。
该工具不发起API请求，不发布标准化资产，也不修改封存输入或共享别名。
严格构建器仍拒绝同键冲突，诊断隔离不构成生产豁免。

`--dataset` 可重复指定 `income`、`balancesheet`、`cashflow`。省略时检查全部三张报表。
失败 vintage 可仅选择已有完整 raw v2 的数据集，不要求存在 normalized manifest。
缺失的旧 normalized 哈希在回执中为 `null`。输出不生成标准资产 manifest、PIT 或 vintage 根 seal，
vintage 的不完整状态保持不变。

本次 shared ratchet 的 `python_lines` 基线精确增加 64 行，用于数据集选择和失败 vintage 回归测试。
复杂度及其他阈值保持原值。当诊断入口合并或测试夹具简化时收回相应行数预算。

### 财报归档顺序

定期归档先保存全部所选 raw，再执行严格归一化、PIT 和封存，详见
[a-share-fundamentals.md](a-share-fundamentals.md)。归一化失败保留其他已完成 raw，
不会生成根 seal。此次 shared ratchet 的 `python_lines` 精确增加 137 行，
覆盖两阶段编排和失败、重入回归。复杂度阈值保持原值。
当编排入口整合或回归夹具简化时，收回相应行数预算。

归档目录中的 `archive_state.json` 原子记录当前阶段、数据集、已完成子清单哈希及失败类型。基本面同日修订冲突还会记录业务键。其他异常文本不写入，避免泄露请求信息。此文件仅供进度诊断，`running` 也可能表示进程被中断。完成证明仍以通过校验的 `manifest.yml` 和 `SEALED.json` 为准。重试只在完整身份一致时复用已完成子资产，已封存成功的归档只做完整性校验，不改写状态文件，也不据此认证新请求参数。

新归档在调用供应商前写入 `retry_identity.json`，记录日期、数据集、run ID、report types、端点规范化后的 SHA-256、字段映射、可用延迟、观察年龄、观察频率、生成代码 Git revision、实现文件哈希、实际 Python 版本及 numpy/pandas/pyarrow/PyYAML/TuShare 安装版本。重试逐项比较这些语义参数。变化必须使用新的输出 vintage。缺少必需库或版本时返回 runtime identity unproven。TuShare SDK 为可选网络依赖，离线复用时明确记录 `not_installed`，与未知版本区分。有无 SDK 或任一版本变化均拒绝重试，缺 SDK 时无法发起真实供应商请求。执行参数（重试次数、分页、桶数、批量大小等）另记于每次 attempt。`page_size` 和 `max_pages` 控制请求分页上限，单元截断仍由 raw 完整性检查拒绝。改变这些参数不会把不完整 raw 认证为完成，复用的完成输入仍须有相同查询身份与已校验的 manifest/source 哈希。字段映射保留原始顺序，以匹配重复 source 时后项覆盖前项的 PIT 解析行为。端点必须使用合法主机名，且不含空白、控制字符、用户名、密码、查询串或 fragment。重试证据不保存端点原文或 token。normalized/PIT 复用还要求匹配显式语义身份回执及对应 manifest 哈希，然后校验源 lineage 与 sealed payload。

每次执行创建独立 `attempts/<id>/`，保存 `parameters.json`、前一次状态原文（若有）、当前 attempt 状态和追加式阶段事件。根 `archive_state.json` 仅为最新进度索引。后续执行不会覆盖历史 attempt。缺少完整身份的非空旧目录返回 `identity is unproven`，在修改旧 receipt 或请求供应商前停止。不得用当前默认参数补写旧身份。已有失败 vintage 的原参数未知时保持只读，需另外保全证据并重新制定隔离输出方案。生成源码必须保留 `src/`、`scripts/` 和 `pyproject.toml`。开发检出从自身 Git 元数据确认 revision。`git-archive-v1` 部署从 `releases/<commit>/` 对应的 `manifests/<commit>.txt` 核验 framework、commit 和 release_format。归档脚本显式指定源码目录，非 editable 安装也可从安装记录 `direct_url.json` 解析本地源码来源。实际安装的 Python 源码必须与该目录一致。来源未知、记录格式错误或代码不一致时，在写入或供应商调用前拒绝执行。此命令按单写者运行，同一 vintage 不支持并发写入。

### 公司类别 selector 只读预检

```bash
marketdata tushare validate-a-share-statement-selector --selector <selector.json>
```

该命令只核验显式决定的机械约束并向 stdout 输出 JSON，不请求供应商、不写资产。
使用已有完整 normalized 文件，字段格式见[选择契约](contracts.md#显式公司类别选择只读预检-v1)。
检查成功退出 0。未覆盖、重复覆盖、类别不存在等选择问题退出 1，保留逐事件错误清单。
格式、文件、哈希或时间约束不合格、日期运算溢出、输入候选身份重复，
以及读取中输入发生变化时退出 2。可以先提交 `decisions: []` 的文件，
取得全部冲突清单，再由有证据的决策方填写。命令本身不生成选择。

最小盘点文件如下（路径和哈希必须替换为实际输入。仅供盘点使用）：

```json
{
  "schema_version": "statement-selector.v1",
  "policy_id": "coverage-inventory-only",
  "policy_version": "1",
  "available_delay_days": 1,
  "normalized_inputs": [
    {"dataset": "income", "path": "normalized/income/data/part.parquet", "sha256": "<actual-sha256>"}
  ],
  "decisions": []
}
```

`valid=true` 不证明经济类别选择正确，也不证明 URI 对应的证据内容已核验。
报告固定 `publication_ready=false`、`evidence_content_verified=false`。
保守可用时间仅为审阅预览，历史决定标记 `reconstructed`，不能追认历史 PIT。
预检没有接入构建、发布或旧 seal。现有 flat PIT 的多类别保护继续拒绝构建。
如需保存报告，将 stdout 重定向到独立任务数据目录，不覆盖共享结果或封存资产。


## 亚洲指数运维采集

使用已安装的 owner 环境和既有私有配置：

```bash
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- python -m quant_market_data_platform.overseas_index_capture --root "$DATA_PLATFORM_ROOT/reports/index-recovery/capture" --date 20261021 --symbols HSI,N225,KS11
```

命令输出包含 `receipt` 路径的 JSON，全部成功时返回 0，部分或全部失败时返回 1。
采集沿用 `TUSHARE_TOKEN_2` 的既有路由，每个指数发起一次逻辑 `index_global` 请求，
向前查询 21 天，单次传输超时为 12 秒。传输故障切换可能访问多个已配置路由。
部署调用方必须另行施加 45 秒进程组期限，模块自身不保证总运行时限。
所有输出放在源码仓库外，中断运行保留 `started.json` 供排查。
该入口只采集指数收盘数据，不恢复港股股票生产能力。

本次 vintage 重试保护新增一个独立身份/attempt 模块和 fixture 负面路径覆盖，shared ratchet 只将 Python 文件库存增加 1、行数库存增加 499。复杂度和大文件预算不变。退出条件：若以后将重试身份、attempt 事件和 derived 语义校验整合进统一的资产事务接口，并保留 legacy 拒绝、参数变化拒绝及历史 receipt 保全覆盖，则移除本次增量预算。

日线 clean 同日重跑应生成新计划并使用新的 clean 输出目录，不要将已完成版本或 latest 作为构建输出。保留失败现场的证据，必要时选择新目录。版本边界见[共享数据契约](contracts.md#日线-clean-构建版本)。

### Clean 链接发布的计划与恢复

默认生产 promotion 仍物化 clean latest。以下只读入口验证目标并打印链接发布/迁移计划：

```bash
python -m quant_market_data_platform.clean_publication \
  --root "$DATA_PLATFORM_ROOT" --alias "$CLEAN_LATEST" --target "$CLEAN_VERSION"
```

实体 latest 的计划包含 `requires_explicit_migration=true`、完整 inventory、源和目标摘要及
`equivalent`。仅字节等价时 `apply_supported=true`。计划本身不会移动或删除实体。
首次转换须另行协调停写、消费者固定版本、原实体保留及 rollback，再执行显式迁移命令。

显式 Python API `publish_clean(root=..., alias=..., target=..., metadata=[...], commit=...)`
只支持 POSIX。路径的父目录须为真实目录，不能经目录链接寻址。metadata 的父目录必须存在，callback 须重建真实路径 contract/registry 并完成
health 检查。发生普通异常会还原声明文件与旧 alias。链接权限错误会明确失败，不回退复制。
准备阶段将原文件字节及旧相对链接写入 alias 同目录的
`.<alias>.clean-publication-lock/journal.json`，持锁时拒绝第二次发布。
中断后使用 `recover_clean(root=..., alias=...)` 显式回滚，函数可重复调用。恢复遇到非本次
目标或实体 alias 会停止。alias 和 metadata 的恢复也有窗口，应在停止 writer 后执行。

成功或完成回滚后，检查 journal 状态，再用 `acknowledge_clean(root=..., alias=...)`
把整个终态 journal 目录重命名为唯一 archive，保留备份并允许下一次发布。没有自动过期删除。
如果准备阶段写 journal 之前失败，alias 尚未改动，保留的空锁需人工核对后处理。
这里验证了合成 POSIX fixture 的异常和中断恢复，未验证 Windows、断电恢复或跨文件系统目录交换。

受控迁移先用同一份参数生成完整计划：

```bash
marketdata tushare promote-a-share-current \
  --artifacts-root "$DATA_PLATFORM_ROOT" --start-date "$START_DATE" --end-date "$END_DATE" \
  --daily-clean-dir "$CLEAN_VERSION" \
  --daily-clean-baseline-report "$BASELINE_REPORT" \
  --daily-clean-research-report "$RESEARCH_REPORT" --clean-only-linked
```

检查计划中的 `source_sha256`、`target_sha256`、`equivalent` 与 inventory。原实体和目标必须是
同一版本的完整字节副本。确认后重复相同命令并添加
`--clean-migration-sha256 "$TREE_SHA256" --apply`。apply 会重新计算摘要，再把原实体 rename
到唯一 `.<alias>.physical-rollback.<id>`，切换链接并重建四份 metadata。原实体 inode 保留。
CLI 成功后自动归档本次 journal，release evidence 记录唯一稳定的 journal 路径。
失败或中断的 journal 保持 active，后续发布停止，须显式恢复并检查后 acknowledge。

恢复中断或回滚已成功迁移：

```bash
python -m quant_market_data_platform.clean_publication \
  --root "$DATA_PLATFORM_ROOT" --alias "$CLEAN_LATEST" --recover
python -m quant_market_data_platform.clean_publication \
  --root "$DATA_PLATFORM_ROOT" --alias "$CLEAN_LATEST" --rollback --journal "$JOURNAL"
```

rollback 先验证当前链接仍为该次发布的目标，再验证保留原实体的完整摘要。归档 journal
不会被修改，rollback 使用新的 active journal。恢复后可用同入口 `--acknowledge` 保留并归档
恢复记录。raw/universe 不在 clean 恢复边界内。

正常夜间发布必须在完整 promotion 命令中显式添加 `--clean-linked`，保持 raw/universe 原有
发布逻辑并将 clean 切为相对链接。仅用 `--clean-only-linked` 会跳过当晚 universe staging
发布，不能替代夜间完整 promotion。停写期间须协调生产 bridge 使用新 owner 版本和此开关，
再进行实体迁移。默认物化模式保留，未修改 bridge 时禁止切换真实 latest。

上述命令与回滚只在合成 fixture 验证，包括子进程直接退出后的源目录恢复。实际部署、
真实迁移和 retained physical rollback 目录的退役均须分别执行，没有自动删除。

### 分钟快照的独立归档与恢复

`scripts/operations/archive_minute_snapshot.py` 将显式选定的分钟目录和树外旁证
复制到新归档，逐文件记录相对恢复路径、`role`、`version`、字节数及 SHA256。
计划文件由操作者在仓库外创建，例如：

```json
{
  "version": "minute-v1",
  "entries": [
    {"source": "/data/minute-v1", "path": "dataset", "role": "minute"},
    {"source": "/data/coverage.json", "path": "evidence/coverage.json", "role": "coverage"},
    {"source": "/data/current.json", "path": "evidence/contract.json", "role": "contract"}
  ]
}
```

```bash
python scripts/operations/archive_minute_snapshot.py archive --plan /data/snapshot-plan.json --archive /backup/minute-snapshot
python scripts/operations/archive_minute_snapshot.py verify --archive /backup/minute-snapshot
python scripts/operations/archive_minute_snapshot.py restore --archive /backup/minute-snapshot --destination /restore/new-minute-snapshot
```

归档和恢复目的地必须不存在，父目录须预先建立。输入只支持普通文件与非空目录。
路径链中的软链接、特殊文件、重复或越界的恢复路径会被拒绝。恢复控制文件名
`LOCK`、`STATE.json`、`RESTORED.json` 不可用作顶层 payload 路径。
复制不使用 hardlink，文件同步落盘后检查源 stat、完整文件集合和内容 hash。最后原子写入
`SEALED.json`。封存只证明所选文件集合的字节一致性。它不提供数字签名，也不证明计划包含
全部所需旁证、供应商数据正确、coverage 合格或旧合同可以发布回滚。

`verify` 只读归档，逐文件核对完整集合与 hash，不读取计划或原源，也不更新 receipt。
`restore` 从已封存归档复制到独占新目录，完成后写入 `RESTORED.json`。恢复文件与归档
inode 独立。`STATE.json` 的 `failed` 表示捕获到失败，进程被强制终止时可能仍为
`copying` 或 `restoring`。缺少最终标志的目录不能视为完成。工具保留失败目录供排查，
不自动续传或覆盖，应使用另一个新目录重试。

归档 writer 使用目标独占锁，校验与恢复持有归档共享锁。它们只约束协作工具，不冻结
生产 writer，也不能防止外部进程原位修改文件。操作者须事先选定不会再写的版本或协调
生产锁。跨文件稳定 stat/hash 检查不构成生产事务快照。必须由独立步骤核验介质故障域、
完整版本范围、Parquet/coverage 和实际 reader/rollback 合同。该工具不改写原始数据、
current、latest 或 provider，不授权退役任何副本。

## 独立融资版本 catalog（显式采用）

`marketdata margin prepare --candidate "$CANDIDATE" --lineage "$LINEAGE" --catalog "$CATALOG"`
只扫描和复制离线完整候选，在仓库外的独立 catalog 创建不可覆盖版本与真实 prepared receipt。
候选必须保留原生 `tushare.margin.v1` 或 `tushare.margin_detail.v1` manifest、完整分区、
准确 totals/query 日期范围。source lineage 的每个文件 SHA256 会重新验证。
`LINEAGE` 是 `tushare.margin.sources.v1` JSON，含 `historical_pit_certified: false` 和
非空 `inputs` 列表：每项提供 `path`、`sha256`、`observed_at`（未知必须显式 null）。
每个候选 data 文件都须有匹配 hash 的源输入。prepare 不重新调用 provider，也不更改原观察时间。

返回的 `version` 和 `receipt_sha256` 用于显式采用：

```bash
marketdata margin adopt --catalog "$CATALOG" --dataset margin \
  --target "$VERSION" --target-sha256 "$RECEIPT_SHA256"
# 默认 dry-run。仅在审核后额外传 --apply。
# 已存在 current 时，还必须给出精确旧链接及其 prepared receipt SHA256：
marketdata margin adopt --catalog "$CATALOG" --dataset margin \
  --target "$VERSION" --target-sha256 "$RECEIPT_SHA256" \
  --expected-current "$OLD_VERSION" --expected-current-sha256 "$OLD_RECEIPT_SHA256"
marketdata margin contract --catalog "$CATALOG" > "$CATALOG/explicit-contract.json"
```

`marketdata margin rollback` 使用与 `adopt` 相同的参数和 CAS，只把 target 明确指定为保留的旧版本。
采用只更改独立 catalog 内 `current/{dataset}`，并发使用该 catalog 的非阻塞锁。
失败仅恢复本次仍持有的指针，不覆盖其他执行者已更改的链接。旧版本不删除。
`contract` 导出已有 `PublishedAssetContract.load` 可读取的显式合同。合同文件必须位于
catalog 内。默认 legacy reader 不会自动切换到它。

legacy `*_latest` 仍由现有 mirror 写分区和 manifest。不得把这些目录或旧 writer 输出路径
指向不可变版本。本入口不执行实体目录 bootstrap，也不修改默认 current contract/registry。
锁只协调使用本 catalog 的操作，不声称与 legacy writer 共享锁。

切换前先持久化 intent，随后原子替换指针并 fsync 目录，再记录最终 receipt。
进程中断留下 pending intent 时，prepare/adopt/rollback/contract 都会拒绝继续。先检查
`marketdata margin recover --catalog "$CATALOG"` 的只读恢复计划，再明确加 `--apply`
回滚到 intent 中的旧指针。未知当前指针、被篡改 intent 或版本 inventory 不允许恢复。
恢复只处理 catalog 指针和事务，保留 legacy 目录。版本数据在 prepared receipt 封存前完成文件/目录 fsync。

本CLI只创建并接受 catalog 内的 absolute current 链接，拒绝相对链接。source observed_at
若已知必须有时区且不得超过当前时间5分钟，未知时仍显式保留 null。

## 历史分钟覆盖独立复核

最多 10 日的离线证据扫描与失败关闭规则见[历史分钟覆盖试点](operations/historical-minute-coverage-pilot.md)。输出仅用于审核，不构成验收或发布授权。

正式签发与消费者重放见[历史分钟独立覆盖凭证](operations/historical-minute-coverage-receipts.md)。
该凭证限定回溯覆盖结论，不授权全市场发布，也不替代跨源 0.99 验收。
