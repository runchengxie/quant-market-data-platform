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
