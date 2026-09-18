# A 股 TuShare 历史回补交接记录（2026-05-31）

## 完成更新

中断的回补任务于 2026-05-31 恢复。中等时间窗口的原始历史数据和派生清洗快照已经完成并通过验证。

已验证窗口：

```text
20240101 to 20260529
```

最终原始快照汇总：

| Dataset | Status | Rows | Symbols | Partitions | Failed segments |
| --- | --- | ---: | ---: | ---: | ---: |
| `daily` | `completed` | 3,128,352 | 5,611 | 580 | 0 |
| `adj_factor` | `completed` | 3,147,074 | 5,634 | 580 | 0 |
| `daily_basic` | `completed` | 3,128,352 | 5,611 | 580 | 0 |
| `limit_status` | `completed` | 4,115,535 | 7,771 | 580 | 0 |

已完成的清洗快照：

```text
/home/richard/data/quant/market-data-platform/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean
```

清洗构建汇总：

```text
status: completed
rows: 3128352
symbols: 5611
files: 5611
duplicate_rows: 0
missing_tr_close: 0
```

包含估值和涨跌停状态字段的加强版清洗校验已通过：

```bash
.venv/bin/marketdata tushare validate-a-share-daily-clean \
  --daily-clean-dir /home/richard/data/quant/market-data-platform/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean \
  --min-rows 3000000 --min-symbols 5000 \
  --require-valuation --require-limit-status
```

校验输出：

```text
status: passed
rows: 3128352
symbols: 5611
duplicate_rows: 0
```

同时镜像了对应日期的交易日历资产：

```text
/home/richard/data/quant/market-data-platform/assets/tushare/a_share/trade_cal/a_share_trade_cal_20240101_20260529.parquet
rows: 880
open_dates: 580
status: completed
```

## 发布完成

除非发现新的缺口，否则不要重新运行原始历史下载。

仓库现在提供 A 股标准股票池的构建和校验命令：

```bash
.venv/bin/marketdata tushare build-a-share-universe
.venv/bin/marketdata tushare validate-a-share-universe
```

完成的清洗历史已经用于替换五日样例股票池。旧样例文件已归档到：

```text
/home/richard/data/quant/market-data-platform/metadata/archive/universe/a_share_sample_20260109_pre_backfill_20260531/
```

标准股票池输出为：

```text
/home/richard/data/quant/market-data-platform/assets/universe/a_share_all_full_by_date.csv
/home/richard/data/quant/market-data-platform/assets/universe/a_share_all_full_symbols.txt
/home/richard/data/quant/market-data-platform/assets/universe/a_share_all_full_by_date.meta.yml
```

股票池构建汇总：

```text
rows: 151105
symbols_seen: 5611
symbols_selected: 5582
latest_symbols: 5499
trade_dates: 580
rebalance_dates_requested: 29
rebalance_dates: 28
first_rebalance_date: 20240229
last_rebalance_date: 20260529
duplicate_rows: 0
```

首次请求的月末日期被有意排除，因为配置的最少 30 个交易日历史窗口尚未满足。

`daily`、`daily_clean`、`adj_factor`、`daily_basic`、`limit_status` 和 `trade_cal` 的最新 alias 现在均指向 20240101 至 20260529 的已完成快照。

旧样例当前契约已归档到：

```text
/home/richard/data/quant/market-data-platform/metadata/archive/current_assets/a_share_current_20260109_sample_pre_backfill_20260531.json
```

标准当前契约已经重建到：

```text
/home/richard/data/quant/market-data-platform/metadata/current_assets/a_share_current.json
```

当前契约健康门禁已通过，共检查十项资产，缺失资产数为 0，过期资产数为 0，问题数为 0。JSON 报告为：

```text
/home/richard/data/quant/market-data-platform/reports/a_share_current_health_20260529.json
```

## 原始交接状态

本记录供下一次会话使用。当时正在将中等时间窗口的 A 股 TuShare 原始历史数据回补到外部数据根目录：

```text
/home/richard/data/quant/market-data-platform
```

目标窗口为：

```text
20240101 to 20260529
```

使用 2026-05-29，是因为 2026-05-31 为星期日，2026-05-29 是本次会话中最近的完整工作日。

## 不要先启动并行写入任务

交接时后台仍有一个回补进程运行：

```bash
ps -eo pid,ppid,stat,etime,pcpu,pmem,args | rg 'backfill-a-share-history|PID'
```

观察到的进程：

```text
PID 20728
.venv/bin/marketdata tushare backfill-a-share-history \
  --artifacts-root /home/richard/data/quant/market-data-platform \
  --start-date 20240101 --end-date 20260529 \
  --dataset daily --dataset adj_factor --dataset daily_basic --dataset limit_status \
  --segment month --continue-on-error
```

继续操作前，先检查 PID `20728` 或其他 `backfill-a-share-history` 进程是否仍在运行。同一快照目录正在写入时，不得启动另一个写入方。

## 交接时的下载进度

前两次尝试因本地代理读取超时而停止：

```text
daily 20240801-20240831 timeout
daily 20241101-20241130 timeout
```

随后使用 `--continue-on-error` 重启命令，使后续月份和数据集可以继续处理。

交接时观察到的分区数量：

```text
daily:        562 part.parquet files
adj_factor:   495 part.parquet files
daily_basic:  575 part.parquet files
limit_status: 0 files; directory had not appeared yet
```

用于检查的命令：

```bash
find /home/richard/data/quant/market-data-platform/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily/data -name part.parquet | wc -l
find /home/richard/data/quant/market-data-platform/assets/tushare/a_share/adj_factor/a_share_all_20240101_20260529_adj_factor/data -name part.parquet | wc -l
find /home/richard/data/quant/market-data-platform/assets/tushare/a_share/daily_basic/a_share_all_20240101_20260529_daily_basic/data -name part.parquet | wc -l
find /home/richard/data/quant/market-data-platform/assets/tushare/a_share/limit_status/a_share_limit_status_20240101_20260529/data -name part.parquet | wc -l
```

交接时已有的 manifest：

```text
/home/richard/data/quant/market-data-platform/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily/manifest.yml
/home/richard/data/quant/market-data-platform/assets/tushare/a_share/adj_factor/a_share_all_20240101_20260529_adj_factor/manifest.yml
/home/richard/data/quant/market-data-platform/assets/tushare/a_share/daily_basic/a_share_all_20240101_20260529_daily_basic/manifest.yml
```

交接时 `limit_status` 的 manifest 尚未生成。

## 原始建议恢复步骤

1. 检查现有回补进程是否仍在运行。

2. 如果仍在运行，等待它完成，再汇总 manifest。除非用户明确要求，不要中断任务。

3. 如果任务因失败结束，重新运行相同命令。命令默认跳过已有 `trade_date` 分区，因此只会补齐缺口：

```bash
.venv/bin/marketdata tushare backfill-a-share-history \
  --artifacts-root /home/richard/data/quant/market-data-platform \
  --start-date 20240101 --end-date 20260529 \
  --dataset daily --dataset adj_factor --dataset daily_basic --dataset limit_status \
  --segment month --continue-on-error
```

4. 四个原始快照均为 `status: completed` 且 `segments_failed: 0` 后，再构建新的清洗快照。

```bash
.venv/bin/marketdata tushare build-a-share-daily-clean \
  --daily-dir /home/richard/data/quant/market-data-platform/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily \
  --adj-factor-dir /home/richard/data/quant/market-data-platform/assets/tushare/a_share/adj_factor/a_share_all_20240101_20260529_adj_factor \
  --daily-basic-dir /home/richard/data/quant/market-data-platform/assets/tushare/a_share/daily_basic/a_share_all_20240101_20260529_daily_basic \
  --limit-status-dir /home/richard/data/quant/market-data-platform/assets/tushare/a_share/limit_status/a_share_limit_status_20240101_20260529 \
  --instruments-file /home/richard/data/quant/market-data-platform/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet \
  --out-dir /home/richard/data/quant/market-data-platform/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean \
  --min-rows 3000000 --min-symbols 5000
```

5. 在切换 alias 前先完成清洗校验：

```bash
.venv/bin/marketdata tushare validate-a-share-daily-clean \
  --daily-clean-dir /home/richard/data/quant/market-data-platform/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean \
  --min-rows 3000000 --min-symbols 5000 --require-limit-status
```

6. 只有原始数据和清洗数据都通过校验后，才更新最新 alias 并重建当前契约。校验通过前不得切换当前资产。

## 已执行的恢复任务

恢复会话先确认主机上没有 `backfill-a-share-history` 进程。当时的实际分区数量为：

```text
daily:        562
adj_factor:   495
daily_basic:  580
limit_status: 39
```

第一次恢复任务跳过了已经完成的 `daily_basic`：

```bash
.venv/bin/marketdata tushare backfill-a-share-history \
  --artifacts-root /home/richard/data/quant/market-data-platform \
  --start-date 20240101 --end-date 20260529 \
  --dataset daily --dataset adj_factor --dataset limit_status \
  --segment month --continue-on-error
```

任务完成了 `adj_factor`，将 `daily` 推进到 571 个分区，将 `limit_status` 推进到 576 个分区。仍有三个本地代理的临时超时。

第二次恢复任务只重试未完成的数据集：

```bash
.venv/bin/marketdata tushare backfill-a-share-history \
  --artifacts-root /home/richard/data/quant/market-data-platform \
  --start-date 20240101 --end-date 20260529 \
  --dataset daily --dataset limit_status \
  --segment month --continue-on-error
```

这两个数据集最终均以 `segments_failed: 0` 完成。跳过已有分区的行为符合预期，恢复过程没有重写已完成分区。

## 备注

- `market-data-platform/.env.local` 包含 `TUSHARE_TOKEN`。TuShare CLI 现在会在解析 token 前加载 `.env.local`。
- 本次会话中的 token 检查结果为 `TUSHARE_TOKEN configured=true valid=true`。
- 本次会话此前已增加回补 CLI，支持 dry-run、按月、按年或全部分段、跳过已有分区、`--continue-on-error` 和可选的 `--sync-latest`。
- 20260105 至 20260109 的旧小样例当前契约已经修复，并完成健康检查，结果为 `missing_assets=0`、`stale_assets=0`、`issue_count=0`。
