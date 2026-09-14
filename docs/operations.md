# 操作手册

> status: active
> owner: market-data-platform
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
