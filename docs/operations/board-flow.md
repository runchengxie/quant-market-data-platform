# A 股板块资金流盘中监控

[English page](board-flow.en.md)

本页说明盘中采集、按日归档和查询 A 股行业、概念、地域板块资金流的方式。数据来自东方财富网页行情接口，写入本地 SQLite，不占用 TuShare 积分。

## 数据源事实

命令只使用两个公开行情接口：

| 用途 | 接口 | 行为 |
| --- | --- | --- |
| 板块列表与当前累计净流入 | `/api/qt/clist/get`，`fs=m:90+t:N` | 每页最多 100 条，按返回的 `total` 翻页。`t:2` 行业、`t:3` 概念、`t:1` 地域 |
| 单板块当日分钟累计净流入 | `/api/qt/stock/fflow/kline/get`，`klt=1` | 从 09:31 开始，`lmt=0` 返回当天到当前时刻的全部分钟 |

字段口径：`f62` 是累计主力净流入（元），`f184` 是主力净流入占比（%），`f3` 是涨跌幅（%），`f6` 是成交额（元）。分钟序列依次为时间、主力、小单、中单、大单、超大单，每行都满足 `主力 = 大单 + 超大单`，解析时逐行校验。

社区常提到的 `/api/qt/stock/fflow/KLChart` 在 2026-11-11 实测返回 `404`，本仓库不实现该路径。`klt=101` 的日线参数只返回当天一行，不能用来补历史。历史日度板块资金流仍由 TuShare 的 `moneyflow_ind_dc` 等接口提供。

## 存储

默认数据库是 `$DATA_PLATFORM_ROOT/intraday/board_flow.sqlite3`，可用 `--db` 覆盖。SQLite 使用 WAL 模式，可同时读取和写入。

| 表 | 内容 | 主键 |
| --- | --- | --- |
| `board_catalog` | 板块代码、名称、类型和首次/最近出现时间 | `board_code` |
| `board_catalog_daily` | 每个交易日实际存在的板块清单 | `(trade_date, board_code)` |
| `flow_snapshot` | 某个采集时刻全部板块的累计资金流 | `(trade_date, board_code, captured_at)` |
| `flow_minute` | 归档的分钟累计资金流，含小单/中单/大单/超大单 | `(trade_date, board_code, trade_time)` |
| `collection_run` | 正常监控结束与归档运行的已保留回执 | `run_id` |

快照只在同一 `captured_at` 上覆盖，不做跨时刻补齐，因此中断和缺口会保留下来，便于事后区分真实采集数据与补录数据。

## 命令

### 建立板块目录

```bash
marketdata board-flow catalog
```

按行业、概念、地域拉取全部板块并写入 `board_catalog`。归档分钟曲线依赖这份目录。

### 采集一次快照

```bash
marketdata board-flow snapshot --board-types concept,industry
```

### 盘中持续采集

```bash
marketdata board-flow monitor --interval 30
```

默认只在 09:30–11:30、13:00–15:00 采集，周末自动跳过。开盘前启动会等待到 09:30。`--no-wait` 让非交易时段直接退出。`--max-minutes` 可限制单次运行时长，便于临时试跑。

### 收盘后归档分钟曲线

```bash
marketdata board-flow archive-minutes
```

默认归档当天，跳过已保存到15:00的板块，可以重复运行续采剩余板块。这个既有跳过条件只检查收盘行，不能证明分钟网格完整。

输出的 `status` 与 `collection_run` 回执一致。全部请求失败返回 `failed`，部分写入且仍有失败返回 `partial`，两者都以非零状态退出。`empty` 明确表示没有选定工作。整个目录已归档时返回 `ok`，并单独计数 `boards_already_archived`。

默认连续三次板块获取不成功就停止遍历，成功写入后重新计数。错日曲线也计入失败，不会写到请求日期。错误原因链中的HTTP 4xx，包括耗尽重试后的429限流，会立即停止，不再尝试下一个板块。其他provider错误只计数，归档循环不对该板块追加重试。单请求的既有重试预算保持独立，三次板块失败最多各使用三次请求尝试。API调用方可通过 `ArchiveOptions.max_consecutive_failures` 指定正整数，CLI沿用默认三次。

`boards_requested` 保持 `--limit` 之后的选定数量。新增字段分别记录目录数量 `boards_catalogued`、已归档 `boards_already_archived`、原待采 `boards_pending`、实际尝试 `boards_attempted`、失败 `boards_failed`、提前停止后未尝试 `boards_unattempted`、因限制未选中 `boards_deferred_by_limit`。`date_mismatch_count` 记录全部错日数量，`date_mismatch` 和 `failure_sample` 只保留有限样本。`stop_reason` 区分HTTP拒绝、连续失败和选定范围完成。限定范围成功不能证明整个目录覆盖，缺失板块不能当作零流入。

### 查询、导出与出图

```bash
marketdata board-flow status
marketdata board-flow export --date 2026-11-11 --board-type concept --top 30 --out flow.csv
marketdata board-flow report --date 2026-11-11 --board-type concept --top 20 --out race.html
marketdata board-flow report --date 2026-11-11 --metric delta --window 30 --out heat.html
marketdata board-flow report --date 2026-11-11 --metric rank --out rank.html
```

`report` 生成自包含 HTML，用 ECharts 作图，并附最新值及 5、15、30 分钟增量的表格。`--metric` 选择视图：

| 视图 | 含义 | 适用场景 |
| --- | --- | --- |
| `cumulative`（默认） | 累计主力净流入曲线 | 看收盘谁吸金最多 |
| `delta` | 每个 `--window` 分钟区间新增的净流入，默认热力图，`--style line` 改为多线条 | 看资金在板块之间切换 |
| `rank` | 所选板块的每分钟排名 | 看领先者何时被替换 |

`--top` 默认按收盘累计值选板块，因此 `cumulative` 视图天然偏向赢家。需要观察轮动时用 `delta` 或 `rank`。`--name` 可以指定固定的观察列表。ECharts 从 jsdelivr CDN 加载，离线环境需要自行替换脚本地址。未指定 `--out` 时输出到数据库同级的 `reports/board-flow/` 目录。

## 数据校验

归档完成后可以用 TuShare 的日度板块资金流做交叉校验：

```bash
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- \
  python scripts/operations/board_flow_tushare_check.py \
  --date 20261111 --database "$DATA_PLATFORM_ROOT/intraday/board_flow.sqlite3"
```

脚本按板块代码对齐 `moneyflow_ind_dc.net_amount` 与归档的 15:00 累计值，输出匹配数量、平均/最大绝对差和最差板块，全部在容差内时返回 0。2026-11-11 的实测结果是 531 个概念板块全部匹配，最大绝对差为 0。

## 定时建议

| 时间 | 任务 |
| --- | --- |
| 09:25 | `board-flow catalog`，同日首次运行时建立目录 |
| 09:30–15:00 | `board-flow monitor --interval 30` |
| 15:05 | `board-flow archive-minutes` |

`monitor` 自身会等待开盘并在收盘后退出，所以一个交易日只需要启动一次。生产任务必须从版本化检出运行，数据库和日志放在数据根目录，不要放在开发 worktree。

## 限制

- 分钟曲线只能补当天。错过的交易日无法用这个接口回补，只能在当天采集。
- 交易时段只按工作日判断，不含交易所节假日历。空的选定范围会明确记录，源请求失败或错日仍算失败，不能据此认定为节假日。
- 板块成分高度重叠，人工智能、机器人、芯片等概念的净流入不能相加解释成全市场资金流入。
- 主力净流入是供应商按成交金额和方向估算的统计量，不同供应商口径不同。
- 这些是网页行情接口，没有稳定性承诺，可能限流、改字段或下线。归档结果和失败会写入 `collection_run`。监控异常可能在回执写入前退出，须结合服务结果和实际数据时间戳判断，不能假定定时任务一定成功。
- 服务端直接断开连接时，客户端会在有限次数内重试，默认最多尝试三次。耗尽次数后命令仍会失败。重试不能重建遗漏的快照，也不能归档过去交易日的分钟曲线。

## 在线库无损归档 { #board-flow-store-archive }

归档工具需要 PyArrow。最小锁定安装为 `uv sync --locked --extra quality`，已有 dev、tushare 或 offline-equities 环境也包含该依赖。


只读回放消费遵循 [v1 归档契约](../contracts/board-flow-archive.zh-CN.md)。导出必须使用全新目录：

```bash
marketdata board-flow archive-export --db "$DATA_PLATFORM_ROOT/intraday/board_flow.sqlite3" --out "$DATA_PLATFORM_ROOT/archive/board-flow-SNAPSHOT_ID"
marketdata board-flow archive-verify --archive "$DATA_PLATFORM_ROOT/archive/board-flow-SNAPSHOT_ID"
marketdata board-flow archive-restore --archive "$DATA_PLATFORM_ROOT/archive/board-flow-SNAPSHOT_ID" --out "$DATA_PLATFORM_ROOT/tasks/restore-check/new.sqlite3"
```

原库只读，校验覆盖六表和全部清单文件。恢复拒绝已有目标和归档目录内的路径。导出分批写入并读回 Parquet，完整恢复临时 SQLite 验证后才发布，失败不发布半成品。强杀遗留 staging 需核对归属后处理。导出的是整库快照，日增量去重另行设计，不接定时器、不裁剪在线库、不改索引、不设保留期限，也不更新共享别名。
