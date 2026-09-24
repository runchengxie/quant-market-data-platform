# A 股 / TuShare 运维

TuShare 是中国大陆市场数据的并存 provider，当前主要用于 A 股基础数据采集。安装可选依赖后，以环境变量或未跟踪的 `.env.local` 提供 token。显式导出的环境变量优先级高于 `.env.local`。

当前分钟数据采用显式分源的双入口。`minute_1m` 保留 Guan legacy canonical。
`minute_1m_tushare` 是采用独立特征、模型和阈值基线的 TuShare operational canonical。
2026-07-15 启动的采集任务已完成 2022-07-15 至 2026-07-08 共 820 个可获得交易日。跨来源
语义、owner-native 特征和策略来源 A/B 拒绝的是无条件替换 Guan，不阻止 TuShare-native
链路独立晋级。本页的 backfill 命令只写 staging 或 TuShare 原始资产。只有 operational
promotion 工具可以移动 `minute_1m_tushare`，并会验证 `minute_1m` 未改变。来源口径、候选
证据和发布步骤见 [A 股分钟数据](a-share-minutes.md)。

```bash
uv sync --extra dev --extra tushare
marketdata tushare verify-token
```

如使用需要自定义 API 地址的高积分 token，在 `.env.local` 中配置与 token 后缀匹配的
`TUSHARE_API_URL_2`，或在命令行显式传入：

```bash
marketdata tushare verify-token \
  --env TUSHARE_TOKEN_2 \
  --api-url https://proxy-a.example.com
```

`TUSHARE_API_URL*` 会写入 TuShare SDK client 的 API 地址，不等同于 `HTTP_PROXY` /
`HTTPS_PROXY`。如果 `--token-env TUSHARE_TOKEN_2` 且未显式传 `--api-url`，程序会先读取
`TUSHARE_API_URL_2`，再回退到 `TUSHARE_API_URL`。

TuShare 命令默认屏蔽 `HTTP_PROXY`、`HTTPS_PROXY` 和 `ALL_PROXY`。如果当前网络依赖 mihomo、
Clash 或其他本机代理环境变量访问 TuShare 或代理域名，校验和下载命令都需要显式加
`--use-proxy`：

```bash
marketdata tushare verify-token --use-proxy
```

## Raw 镜像

TuShare raw、instruments 和 trade calendar 下载默认在 provider 调用期间屏蔽
`HTTP_PROXY`、`HTTPS_PROXY` 和 `ALL_PROXY`，并设置 `NO_PROXY=*`，避免本机代理超时影响长历史
采集。确需走代理时显式加 `--use-proxy`。瞬时超时、连接中断、代理错误和 502/503/504 默认最多
重试 3 次，初始等待 2 秒、指数退避上限 30 秒。频率超限默认冷却 65 秒后重试。可用
`--retry-attempts`、`--retry-sleep-seconds`、`--retry-max-sleep-seconds` 和
`--quota-cooldown-seconds` 调整。

```bash
marketdata tushare export-a-share-instruments \
  --out "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet"

marketdata tushare mirror-a-share-trade-cal \
  --start-date 20260101 --end-date 20260526 \
  --out "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet"

marketdata tushare mirror-a-share-daily \
  --start-date 20260101 --end-date 20260526 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20260101_20260526_daily"

# 分钟 OHLCV（写入 TuShare 原始资产或显式 staging）
marketdata tushare mirror-a-share-mins \
  --start-date 20260701 --end-date 20260709 \
  --token-env TUSHARE_TOKEN_2 --api-url https://proxy-a.example.com \
  --cooldown-seconds 0.3 --batch-size 20

# 只下载当日实际交易的北交所动态股票池
marketdata tushare mirror-a-share-mins \
  --start-date 20260701 --end-date 20260709 \
  --exchange BJ --token-env TUSHARE_TOKEN_2 \
  --out-dir "$DATA_PLATFORM_ROOT/staging/tushare_minute_bj_probe" \
  --cooldown-seconds 1 --batch-size 20

marketdata tushare mirror-a-share-adj-factor \
  --start-date 20260101 --end-date 20260526 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/adj_factor/a_share_all_20260101_20260526_adj_factor"

marketdata tushare mirror-etf-adj-factor --help

marketdata tushare mirror-etf-daily --help

marketdata tushare mirror-a-share-daily-basic \
  --start-date 20260101 --end-date 20260526 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_20260101_20260526_daily_basic"

marketdata tushare mirror-a-share-limit-status \
  --start-date 20260101 --end-date 20260526 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/limit_status/a_share_limit_status_20260101_20260526"

marketdata tushare mirror-a-share-moneyflow \
  --start-date 20260101 --end-date 20260526 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/moneyflow/a_share_all_20260101_20260526_moneyflow" \
  --token-env TUSHARE_TOKEN_2

marketdata tushare mirror-a-share-fund-portfolio \
  --start-date 20141231 --end-date 20260529 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_portfolio/a_share_all_20141231_20260529_fund_portfolio" \
  --token-env TUSHARE_TOKEN_2 \
  --page-size 8000 --max-pages-per-period 300 \
  --skip-existing
```

分钟镜像默认断点恢复。每个日期按 20 只证券批量请求。发生请求异常或可控中断时，会先将此前
成功批次合并写入 partial checkpoint。完整 sidecar 会绑定 Parquet 的 SHA-256、schema、行数和
证券集合。旧分区、损坏文件或与 sidecar 不一致的文件不会被静默信任，中断后只继续缺失证券。
每只证券必须满足已审计的 241 分钟网格，否则该日期保持 partial。显式 `--force` 才会刷新已经
完整的日期。

`--batch-size` 默认仍为 20，允许范围为 1–33。截至 2026-07-15，分钟 endpoint 明确单次响应
上限为 8000 行。完整 1min 证券日固定为 241 行，因此 33 只对应 7953 行，仍在上限内，而
34 只对应 8194 行，会产生被截断的风险。这个 33 的上限只适用于这里的完整 241-bar 1min
请求，不能外推到其他 endpoint、频率或非完整交易日语义。provider 调整行数上限后必须重新验证。

把生产或长历史任务从 20 调到 33 前，先在隔离 staging 对同一批交易日、同一动态证券池做 A/B
验证。两组任务分别使用 `--batch-size 20` 和 `--batch-size 33`，并比较完整 sidecar、证券集合、
241-bar 网格、Parquet 行数和标准化后的键值内容，同时记录实际分钟请求数、耗时、重试及限频冷却。
历史 backfill 的 batch size 属于不可变 plan identity，两组应生成不同 plan/run 目录。只有内容完全
一致且 33 未增加 partial、截断或限频异常时，才把 33 用于后续长任务。否则保留默认 20。

分钟命令会在 token 预检前自动加载仓库或当前目录中未跟踪的 `.env.local` / `.env`，不需要先
手工 `source`。`--exchange BJ` 在每个交易日解析完整 daily traded universe 后按 `.BJ` 后缀
筛选，sidecar 的 universe rule、source 和 hash 都绑定该过滤条件。该机制区别于会因上市或停牌
日期失效的静态 `--symbols` 列表。exchange-filtered mirror 必须显式指定独立 `--out-dir`，避免与默认
全 A raw 分区发生 universe identity 冲突。

少量、离散的 full-day promotion 缺口适合使用 bounded chunk runner。它直接读取既有
`a_share.minute_tushare_full_day_plan.v1`，完整校验 source root 中已有的 sidecar/Parquet 绑定，
每次只选择最早的 N 个未完成日期。先用纯读取 dry-run 查看本次选择：

```bash
marketdata tushare run-a-share-minute-full-day-chunk \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_fusion/tushare_full_production.json" \
  --full-day-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/minute_1m_full" \
  --receipt-dir "$DATA_PLATFORM_ROOT/metadata/minute_backfill/full_day_chunk_receipts" \
  --max-dates 3 --token-env TUSHARE_TOKEN_2 --dry-run
```

确认后去掉 `--dry-run`。默认 `--max-dates 3`、`--batch-size 20`、`--cooldown-seconds 1`。也可
保守改为 1，或在稳定窗口改为 5。一个 invocation 只调用一次 hardened mirror，并通过
`trading_dates` 在进程内逐日串行执行，没有 worker/并行参数。每次 actual invocation 自动创建唯一
JSON receipt，记录 plan SHA-256、选择日期、endpoint 标识、限速/重试参数和每个完成分区的紧凑
SHA-256 绑定。receipt 不记录 token 值。source root 上的 `flock` 会阻止重叠定时任务，即使两次调用
使用不同 receipt 目录也不能并发。

进程中断时，minute mirror 仍先写 daily partial sidecar。runner 会尽力将本次 receipt 标记为
`partial`/`interrupted`。下次使用同一条命令时重新从 sidecar 判定完成状态，只选择仍未完成的日期，
无需改 plan 或拼接 shell 日期。所有 plan 日期完成后，实际调用返回 `noop` 且不要求 token、
不再写空 receipt，定时器可以继续执行完整性复核而不制造文件。dry-run 不加载 token、不联网、
不创建 lock/receipt/目录，适合在安装定时任务前反复审查。
该 runner 不提供 BJ-only 模式，也不修改 derived/current。独立北交所历史仍走专门 backfill 和
promotion 流程。

历史分钟 canonical 当前由 Guan annual、Guan deal 和 TuShare full day 共同组成。少量抽查可以直接使用上面的 mirror 命令。BJ-only 历史补齐、全 A 对照历史和 Guan 替换研究使用下面的不可变计划与 staging runner，禁止逐日改写生产分钟目录。

## 分钟历史 backfill 计划与续跑

planner 只读取本地 trade calendar 和 instruments Parquet，不调用 TuShare。每个计划固定来源文件
SHA-256、scope、日期、月/年分段、限速参数、endpoint 标识和单 worker 约束，并将数据目录固定为：

```text
$DATA_PLATFORM_ROOT/staging/tushare_minute_backfill/runs/<plan_id>/data/
```

先分别生成 BJ-only 与全 A 计划：

```bash
mkdir -p "$DATA_PLATFORM_ROOT/metadata/minute_backfill"

marketdata tushare plan-a-share-minute-backfill \
  --scope bj-only \
  --start-date 20160104 --end-date 20260709 \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --instruments "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --backfill-root "$DATA_PLATFORM_ROOT/staging/tushare_minute_backfill" \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/bj_only_20160104_20260709.plan.json" \
  --segment month --batch-size 20 --cooldown-seconds 1 \
  --token-env TUSHARE_TOKEN_2

marketdata tushare plan-a-share-minute-backfill \
  --scope all-a \
  --start-date 20160104 --end-date 20260709 \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --instruments "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --backfill-root "$DATA_PLATFORM_ROOT/staging/tushare_minute_backfill" \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/all_a_20160104_20260709.plan.json" \
  --segment year --batch-size 20 --cooldown-seconds 1 \
  --token-env TUSHARE_TOKEN_2
```

BJ-only 计划会把有效起始日限制为北交所首个交易日 2021-11-15。全 A 计划保留沪、深、北当日
动态交易股票池。二者有不同 `plan_id` 和 run 目录，不能共用 receipt。

首次试跑可以给 planner 增加 `--max-dates 5` 或 `--request-budget 500`。request budget 是硬上界：
每个日期按该 scope 在完整历史 instruments master 中的 canonical 唯一证券总数除以 batch size
向上取整，不依赖可能受旧代码映射影响的历史上市区间估计。区间估计仍在 summary 中作为诊断，
receipt 则分别记录计划请求上界和实际成功分钟请求数。预算不包含 stock_basic、daily 和
daily_basic 发现请求。这些请求的最小估算会单列。达到任一限制时，planner 只选择日期前缀并在
计划中记录 `truncated_by`。`--dry-run` 校验规划但不写 plan。

非连续缺口使用 `--dates-file`，文件可以是换行/逗号分隔的 `YYYYMMDD` 文本、JSON 日期数组，
也可以是顶层含 `dates` 数组的 full-day promotion plan。planner 会将整个文件 SHA-256 绑定到
plan，并要求每个日期都处于起止范围且在本地 calendar 中开放。例如 28+58 个离散目标日可以
直接写入同一文件，再按月份或年份分段：

```bash
marketdata tushare plan-a-share-minute-backfill \
  --scope all-a --start-date 20260427 --end-date 20260709 \
  --dates-file "$DATA_PLATFORM_ROOT/metadata/minute_backfill/tushare_full_day_production.plan.json" \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --instruments "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --backfill-root "$DATA_PLATFORM_ROOT/staging/tushare_minute_backfill" \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/full_a_gaps.plan.json" \
  --segment month --request-budget 30000 --token-env TUSHARE_TOKEN_2
```

执行前先生成不调用 API 的 dry-run receipt，然后开始单 worker 续跑：

```bash
marketdata tushare run-a-share-minute-backfill \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/bj_only_20160104_20260709.plan.json" \
  --receipt "$DATA_PLATFORM_ROOT/metadata/minute_backfill/bj_only_20160104_20260709.receipt.json" \
  --token-env TUSHARE_TOKEN_2 --dry-run

marketdata tushare run-a-share-minute-backfill \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/bj_only_20160104_20260709.plan.json" \
  --receipt "$DATA_PLATFORM_ROOT/metadata/minute_backfill/bj_only_20160104_20260709.receipt.json" \
  --token-env TUSHARE_TOKEN_2
```

runner 固定 `workers=1`，逐月或逐年调用已有 hardened minute mirror。日期请求异常或可控中断
时由 `_minute_mirror.json` 保存 partial checkpoint。segment 完成或失败后原子更新 receipt。重新
执行会跳过 receipt 中已完成的 segment，并由 daily sidecar 继续未完成证券。receipt 记录 fetch
vintage、经过净化的 endpoint 标识、实际成功请求数和错误，但只记录 token 环境变量名，绝不记录
token 值。失败信息
也会替换当前 token 和原始 endpoint 字符串。如果实际逻辑请求数超过计划硬上界，receipt 标记
`budget_violation` 并阻止原计划续跑，需根据新的 master vintage 重新生成计划。

计划与 receipt 完成后仍不能据此发布。staging 数据必须另行执行完整性、口径和市场 overlay
校验，再进入 promotion。runner 本身没有任何 current alias 或生产目录切换能力。

### 配额受控的分钟替换 campaign

`scripts/operations/tushare_minute_replacement_campaign.py` 用于把一个离散全市场日期集合拆成
可恢复、互不重叠的双 lane 日批次。`prepare` 会扫描所有已知 run root 的 daily sidecar union，
先排除已经通过 full-universe promotion 校验的日期，把 partial 日期登记为原地续传，再把 fresh
日期按默认每日 50 日、奇偶索引 25 + 25 拆分。首日自动拆成每 lane 3 日 canary 和剩余 main
phase。它只生成 staging plan/manifest，不请求 API。

`run-next` 持有 campaign 级 flock，检查正式 refresh/backup 服务均未运行，顺序续完 partial，
然后错峰启动两个 single-worker plan。每个 phase 完成后逐日调用
`validate_complete_minute_partition(..., require_full_universe=True)` 并原子更新 ledger。canary 未
全部通过时不会进入 main。它每次最多完成一个 campaign 日，partial 或 quota 退出会保留原
sidecar，下一次从缺失证券续传。不要同时恢复生成 campaign 前的宽计划，否则不同 run root 会
重复请求相同日期。

需要利用同一个配额窗口连续跨 campaign 日推进时，使用显式的预算模式：

```bash
uv run python scripts/operations/tushare_minute_replacement_campaign.py run-budgeted \
  --manifest "$CAMPAIGN_DIR/manifest.json" \
  --max-new-rows 67000000 \
  --max-runtime-seconds 14400 \
  --poll-seconds 10 \
  --interrupt-grace-seconds 300 \
  --quota-timezone Asia/Shanghai \
  --quota-reset-guard-seconds 1200 \
  --run-window-start 00:45 \
  --run-window-drain 04:15 \
  --run-window-stop 04:20 \
  --heartbeat-seconds 300 \
  --no-progress-limit 2 \
  --single-lane-threshold-rows 5500000
```

`run-budgeted` 在每个本地配额日第一次启动时，以所有 mutable daily sidecar 的
`partition.rows` 建立窗口基线，并把窗口基线与高水位写入 campaign ledger。同一配额日再次运行
时会继承已经消耗的行数。前一配额日完整下载或保存在 partial checkpoint 中的行不会占用新窗口。
完成当前 campaign 日后，只要窗口预算和运行时间仍有余量，就继续下一个日批次。两条 lane 共用
同一个软行数阈值。到达阈值或 monotonic runtime 后，runner 向所有仍在运行的 lane 发送
`SIGINT`，等待 minute mirror 原子保存 partial sidecar，再以成功状态结束本轮。`04:15` 使用
北京时间墙钟开始排水，`04:20` 强制结束仍未退出的 lane。每次 poll 都重新读取墙钟，因此机器
suspend 后恢复不能绕过截止线。启动时间不在 `00:45–04:15` 时不会创建 lane。任一 sidecar
损坏、日期不匹配或行数倒退都会 fail closed。

父 runner 主动发出的 `SIGINT`、对应的 `interrupted/partial` receipt 和退出码 130 是正常
checkpoint。没有内部停止原因的非零 lane 退出属于可重试失败，脚本返回 75。receipt 中的
`budget_violation`、failed immutable-plan receipt、校验或记账错误属于致命失败，返回 70。完整
sidecar 不能掩盖这些 receipt 证据。共享账本的 `MinuteQuotaExceeded` 或
`MinuteQuotaPoolClosed` 会映射为正常的 `shared_quota_exhausted` checkpoint，并避免重试风暴。
连续两轮没有新增持久化行会写入 `stalled` health 并熔断后续自动 tick。排障后仅可显式使用
`--allow-stalled-retry` 做一次恢复尝试。

`ledger.json.active_run` 每 5 分钟更新 run id、PID、heartbeat、运行时长和新增行数。只读状态命令
支持人读格式和稳定 JSON：

```bash
uv run python scripts/operations/tushare_minute_replacement_campaign.py status \
  --manifest "$CAMPAIGN_DIR/manifest.json"

uv run python scripts/operations/tushare_minute_replacement_campaign.py status \
  --manifest "$CAMPAIGN_DIR/manifest.json" --json
```

输出包含 complete/partial/remaining、当前 campaign 软额度、最近停止原因、heartbeat health、下一
安全窗口和基于近期窗口的区间 ETA。这里的 campaign 额度属于本地估算。启用共享账本后应
同时查看其 consumer/pool 状态。

行数预算依据已持久化 sidecar 计算，只是 campaign 工作量软阈值。服务商硬限制按物理请求次数
计算。共享账本会在每次 `stk_mins` 发送前原子预占 1 个 request slot，0 行、小批和满 8,000 行
响应都消耗 1 次，每次外层重试也分别记账。`committed/reserved/uncertain` 会占用请求池，只有能
证明未发送的 `released` 和门禁拒绝的 `rejected` 不占用。生产使用 10,000 次完整保底池。只有
21:15 后的 tail-filler 开启 burst，20,000 上限扣除 500 次探测余量后有效上限为 19,500。

若分钟 provider 已部署共享额度账本，`run-next` 和 `run-budgeted` 可把同一组参数透传到 preflight
和两条 backfill lane：

```bash
  --minute-quota-mode enforce \
  --minute-quota-db "$DATA_PLATFORM_ROOT/metadata/tushare/minute_quota/minute_quota.sqlite3" \
  --minute-quota-consumer replacement_campaign \
  --minute-quota-gate requests \
  --minute-quota-limit-requests 10000 \
  --minute-quota-burst-limit-requests 20000 \
  --minute-quota-safety-requests 500 \
  --no-minute-quota-allow-burst \
  --minute-quota-limit-rows 160000000 \
  --minute-quota-safety-rows 4000000
```

这些参数只形成运行时配置，不修改 immutable manifest。也可用 canonical
`MDP_TUSHARE_MINUTE_QUOTA_*` 环境变量提供配置。consumer 缺省固定为
`replacement_campaign`，两条 lane 不拆成不同 consumer。接近
`--single-lane-threshold-rows` 时，已运行的双 lane 会确定性向名称排序靠后的 lane 发送 SIGINT，
等它原子写 partial 后让另一 lane 继续。被排水 lane 明确记录为 intentional checkpoint。若一次
invocation 启动时已低于阈值，则只启动第一条未完成 lane。请求级 reservation 仍是控制精确在途
额度的主门禁。`--minute-quota-allow-burst` 只能由 tail-filler 使用。普通 campaign、accelerator、
DailyWatch 和 Top200 都必须留在保底池并受 request hold 保护。

共享账本状态可按实际 token 和北京时间配额日读取：

```bash
marketdata tushare minute-quota-status \
  --token-env TUSHARE_TOKEN_2 \
  --minute-quota-db "$DATA_PLATFORM_ROOT/metadata/tushare/minute_quota/minute_quota.sqlite3" \
  --minute-quota-mode enforce \
  --minute-quota-gate requests \
  --minute-quota-limit-requests 10000 \
  --minute-quota-burst-limit-requests 20000 \
  --minute-quota-safety-requests 500 \
  --minute-quota-limit-rows 160000000 \
  --minute-quota-safety-rows 4000000 \
  --json
```

状态输出只包含短 token fingerprint，并列出 committed、reserved、uncertain、residual holds、
available 和各 consumer 明细。该命令不会创建 request reservation。读取状态时会把租约已过期的
reservation 保守结算为 uncertain。数据库旁的隐藏 HMAC key 文件必须与 SQLite 文件一同备份。

完整生产调度为：00:05 coordinator 建立 DailyWatch/Top200 request holds。00:45–04:20 历史
campaign。05:20 DailyWatch 原始数据完成后释放预留。08:00–16:40 accelerator 补足保底区。
21:00 Top200 原始数据完成后释放预留。21:15–23:35 tail-filler 探测浮动区。下载机会 timer 不设
`Persistent=true`，hard-stop 与 coordinator 可 persistent。同一个 service 实例和 campaign flock
会拒绝并发。Top200 的 tail 放行只检查 raw-completeness marker，不依赖后续 factor/walk-forward
结果。完整模板、renderer、marker 契约与启用命令见 `scripts/systemd/README.md`。
`--quota-reset-guard-seconds` 会在本地午夜前预留保护带。同一配额日的手工机会任务与凌晨任务
共享 ledger 高水位。campaign 仍然只是 raw acquisition + 结构 QA。Guan/TuShare 语义差异
审计和 production 切换必须作为独立门禁。

配额策略在同一个上海自然日内不可变。若部署时当天已经存在旧的 rows pool，coordinator 返回
75。该非零状态会让 coordinator unit 保持 failed，并通过有序的 `Requires=` 阻断所有下游下载，
不能把它登记成成功退出。策略在当天无法安全自愈，因此 service 不立即循环重试，后续机会 timer
可以重试 prerequisite，persistent coordinator timer 会在次日 00:05 再次运行。可通过
`systemctl --user status tushare-minute-quota-coordinator.service` 和
`$HERMES_LOGS_DIR/tushare_minute_quota_coordinator.log` 查看失败状态与结构化延后原因。tail 的策略
条件也会在任何 provider 请求前跳过。次日才建立新的 requests pool 和 holds，不得原地改写当天
pool。

全部目标日期完成后，runner 会重新做 full-universe reconciliation，并原子写入
`acquisition-readiness.json`。marker 固定声明 `acquisition_complete=true`、
`structural_ready=true`、`semantic_audit=pending`、`promotion_ready=false`、
`cutover_performed=false`。后续 timer 只校验 marker 后快速退出，绝不修改 production alias。

如果完整 campaign 的 marker 因控制面中断或旧版本写入顺序而与最终 ledger 哈希不一致，使用
只读数据源的 reconciliation 入口重建 marker。该命令不联网、不修改 ledger，也不触碰 production
alias。它会重新校验全部 full-universe 分区、lane receipt 和文件绑定：

```bash
uv run python scripts/operations/tushare_minute_replacement_campaign.py \
  reconcile-readiness --manifest "$CAMPAIGN_DIR/manifest.json"
```

日频类 TuShare 镜像按开放交易日请求全市场，并写入：

```text
data/trade_date=YYYYMMDD/part.parquet
```

长历史下载先生成分段计划，再按月或按年续跑：

```bash
marketdata tushare backfill-a-share-history \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --start-date 20240101 --end-date 20260531 \
  --dataset daily --dataset adj_factor --dataset daily_basic --dataset limit_status \
  --token-env TUSHARE_TOKEN_2 \
  --segment month \
  --dry-run
```

确认计划后去掉 `--dry-run` 执行。全部成功后可用 `--sync-latest` 将 canonical latest alias 指向本次 snapshot。
重复运行同一输出目录时默认跳过已存在的 `trade_date` partition，因此可以用于补齐上次因超时或限额失败的缺口。

补齐 2008–2014 复权因子和涨跌停价格时先写 staging，不切换 `latest`。所有请求显式使用
15000 积分 token 环境变量并经 xiaodefa 转发：

```bash
marketdata tushare backfill-a-share-history \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --start-date 20080101 --end-date 20141231 \
  --dataset adj_factor --dataset limit_status \
  --token-env TUSHARE_TOKEN_2 \
  --api-url https://proxy-a.example.com \
  --segment year \
  --dry-run
```

审阅 dry-run 计划后去掉 `--dry-run`。在覆盖率、逐日唯一键和分区 receipt 全部通过前不要增加
`--sync-latest`。研究端继续把现有长历史 raw 结果与约束版并列。

## Daily Clean 和 Universe

`build-a-share-daily-clean` 默认使用 memory-managed streaming 构建：按 `trade_date` 分批读取 raw
partition，写入临时暂存目录，再用 streaming Parquet writer compact 成兼容下游的
`data/<symbol>.parquet` 布局。默认每 120 个交易日 flush 一次。当 WSL / Linux `MemAvailable`
低于 2048 MB 时提前 flush，低于 1024 MB 时中止并报错。可用 `--batch-trade-dates`、
`--memory-soft-limit-mb`、`--memory-hard-limit-mb` 调整，传 `0` 可关闭对应 memory guard。

`validate-a-share-daily-clean` 使用列投影和 Parquet batch scan，不会逐文件做 pandas 整块读取。
`baseline` profile 是当前数据刷新发布前的阻塞门禁，覆盖 manifest 对账、字段类型、唯一键、
OHLC、成交量和成交额。`research` profile 在此基础上增加估值、涨跌停、停牌、交易日历、上市天数、
板块分类和 ST 来源校验。`--st-history-file` 指向由 `build-a-share-st-history` 生成的
`st_history_reconstructed.parquet` 或已发布的 latest 资产，配套 receipt 必须为 `complete`
且覆盖全部构建日期。
`is_st` 按交易日匹配这份历史表。没有历史表时输出未知值，研究级校验拒绝该资产。
历史表按名称生效日期重建，仍需区分公告实际可用时间。
`daily_basic` 也只是逐日估值 overlay，不能描述成 PIT fundamentals。

```bash
marketdata tushare build-a-share-daily-clean \
  --daily-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily" \
  --adj-factor-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/adj_factor/a_share_all_20240101_20260529_adj_factor" \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_20240101_20260529_daily_basic" \
  --limit-status-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/limit_status/a_share_limit_status_20240101_20260529" \
  --instruments-file "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --st-history-file "$DATA_PLATFORM_ROOT/assets/tushare/a_share/st_history_reconstructed/a_share_all_st_history_reconstructed_latest.parquet" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean" \
  --min-rows 3000000 --min-symbols 5000

marketdata tushare validate-a-share-daily-clean \
  --daily-clean-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean" \
  --require-valuation --require-limit-status \
  --profile baseline \
  --out "$DATA_PLATFORM_ROOT/reports/a_share_daily_clean_validation_20240101_20260529.json"

marketdata tushare validate-a-share-daily-clean \
  --daily-clean-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean" \
  --profile research \
  --trade-cal-file "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --fail-on-severity warning --max-warning-rate 0.001 \
  --out "$DATA_PLATFORM_ROOT/reports/a_share_daily_clean_research_validation_20240101_20260529.json"

marketdata tushare build-a-share-universe \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --daily-clean-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily_clean" \
  --start-date 20240101 --end-date 20260529 \
  --rebalance-frequency M --lookback-days 60 --min-window-days 30 \
  --min-rows 100000 --min-symbols 5000 --min-rebalance-dates 20

marketdata tushare validate-a-share-universe \
  --by-date-file "$DATA_PLATFORM_ROOT/assets/universe/a_share_all_full_by_date.csv" \
  --latest-symbols-file "$DATA_PLATFORM_ROOT/assets/universe/a_share_all_full_symbols.txt" \
  --meta-file "$DATA_PLATFORM_ROOT/assets/universe/a_share_all_full_by_date.meta.yml" \
  --expected-as-of 20260529 \
  --min-rows 100000 --min-symbols 5000 --min-rebalance-dates 20
```

Universe builder 使用前置滚动中位成交额，避免在调仓日使用当日成交额。

## 当前数据发布

```bash
marketdata tushare plan-a-share-current-refresh \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --start-date 20240101 \
  --end-date 20260529
```

`plan-a-share-current-refresh` 只输出计划。它不调用 provider、不写文件，也不更新 latest alias。
计划中的顺序是：raw backfill、daily_clean build、baseline validation、research validation、
universe build、universe validation、当前数据发布流程。

完成计划里的构建和验证后，用 promotion gate 切换当前数据契约：

```bash
marketdata tushare promote-a-share-current \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --start-date 20240101 \
  --end-date 20260529 \
  --apply
```

不加 `--apply` 时只做发布前检查，不更新当前数据契约。加 `--apply` 后会执行以下发布动作：

- 将 TuShare raw 和 `daily_clean` latest alias 指向本次 snapshot。
- 将暂存 universe 三个文件及其 manifest 复制到 canonical universe 路径。
- 生成 `metadata/current_assets/a_share_current.json`。
- 重建 `metadata/dataset_registry.csv`。
- 写入当前数据契约 health report 和发布证据。

默认 evidence 路径：

```text
$DATA_PLATFORM_ROOT/reports/a_share_current_release_<start>_<end>.json
```

默认要求这些验证报告已经存在且 `status=passed`：

```text
$DATA_PLATFORM_ROOT/reports/a_share_daily_clean_baseline_validation_<start>_<end>.json
$DATA_PLATFORM_ROOT/reports/a_share_daily_clean_research_validation_<start>_<end>.json
$DATA_PLATFORM_ROOT/reports/a_share_universe_validation_<start>_<end>.json
```

如果需要手工检查数据契约或注册表，仍可单独运行：

```bash

marketdata contract build --market a_share --provider tushare \
  --artifacts-root "$DATA_PLATFORM_ROOT" --target-date 20260526

marketdata contract inspect --market a_share --provider tushare \
  --artifacts-root "$DATA_PLATFORM_ROOT" --target-date 20260526 \
  --require-start-date 20240101 \
  --fail-on-severity error --format json \
  --out "$DATA_PLATFORM_ROOT/reports/a_share_current_health_20260526.json"

marketdata registry build --artifacts-root "$DATA_PLATFORM_ROOT" --market a_share
```

promotion gate 默认只阻断日频当前数据契约发布必须检查的资产：instruments、trade calendar、raw daily
四项、`daily_clean` 和 universe 三项。PIT fundamentals、行业历史和真实券商门禁仍是研究 readiness
证据，不作为日频当前数据契约发布的默认阻断项。

## Research Asset 和 Fundamentals

研究资产入口：

```bash
marketdata tushare build-a-share-pit-fundamentals --help
marketdata tushare validate-a-share-pit-fundamentals --help
marketdata tushare download-a-share-industry-membership --help
marketdata tushare download-a-share-reference --help
marketdata tushare normalize-a-share-reference --help
marketdata tushare publish-a-share-reference --help
marketdata tushare list-a-share-reference-specs --help
marketdata tushare build-a-share-industry-changes --help
marketdata tushare validate-a-share-industry-changes --help
marketdata tushare mirror-a-share-moneyflow --help
marketdata tushare build-a-share-flow-ownership-features --help
marketdata tushare validate-a-share-flow-ownership-features --help
```

财务报表、业绩预告、业绩快报、分红、财务指标、审计、主营构成和披露日使用单独的 restartable raw-to-PIT 链路。`daily_basic` 估值字段是逐日估值 overlay。财务报表 PIT 仍以 raw-to-PIT 链路为准。

历史行业 membership 按来源语义处理。TuShare 申万 `index_member_all` 和中信
`ci_index_member` 是区间型来源，核心字段是 `ts_code`、`in_date`、`out_date`、`is_new`
以及一级/二级/三级行业代码和名称。维护者可以先把授权环境中的 provider 结果保存为
未跟踪 CSV / Parquet，再用本地 extract builder 生成平台资产。例如申万 2021 三级行业：

```bash
marketdata tushare download-a-share-industry-membership \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/source/index_member_all_sw2021_l3" \
  --src SW2021 \
  --level L3 \
  --is-new Y \
  --is-new N \
  --min-rows 1000 \
  --min-symbols 1000

marketdata tushare build-a-share-industry-changes \
  --source-file /path/to/tushare_index_member_all_sw2021_l3.csv \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/industry_changes/a_share_all_industry_changes_sw2021_l3" \
  --effective-date-col in_date \
  --end-date-col out_date \
  --industry-code-col l3_code \
  --industry-name-col l3_name \
  --industry-system sw2021_l3 \
  --provider tushare \
  --min-rows 1000 --min-symbols 1000

marketdata tushare validate-a-share-industry-changes \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/industry_changes/a_share_all_industry_changes_sw2021_l3" \
  --min-rows 1000 --min-symbols 1000
```

ST、指数成分和上市公司基础信息属于低频参考数据集，走统一的 reference 链路。原始下载
统一写入一个 staging 目录。`stock_st` 与 `share_float` 按日期保存 `_parts/`，遇到限速后可以
用同一命令续跑。先列出可用的数据集规格：

```bash
marketdata tushare list-a-share-reference-specs

REFERENCE_STAGING="$DATA_PLATFORM_ROOT/staging/tushare_reference_20260730"
REFERENCE_DATASETS=(stock_st index_weight stock_company stk_managers share_float)
for dataset in "${REFERENCE_DATASETS[@]}"
do
  marketdata tushare download-a-share-reference \
    --dataset "$dataset" \
    --out-dir "$REFERENCE_STAGING" \
    --start-date 20220101 \
    --end-date 20260730 \
    --token-env TUSHARE_TOKEN_2 \
    --request-interval-seconds 0.2 \
    --retries 5
done

marketdata tushare normalize-a-share-reference \
  --raw-dir "$REFERENCE_STAGING" \
  --out-dir "$REFERENCE_STAGING" \
  --end-date 20260730 \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet"

marketdata tushare publish-a-share-reference \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --raw-dir "$REFERENCE_STAGING" \
  --target-date 20260730
```

未显式传 `--index-code` 时会下载沪深 300、中证 500、中证 1000、创业板指和上证 50。
未传 `--exchange` 时 `stock_company` 覆盖 SSE、SZSE、BSE。`index_weight_daily` 由月度
`index_weight` 按真实交易日展开并提供归一化 `drift_weight`。发布会同时生成日期版本、
原子替换的 `latest.parquet` 和 `.receipt.json`（schema、SHA-256、行数、质量状态）。
`share_float` 按公告日使用 `limit + offset` 分页，转发端单次容量不会被误当成公告日总量。
如果任何数据集达到配置的 `max_pages`，下载会明确失败并要求缩小窗口。跨仓消费方
（如 market-data-platform）只读已发布资产并校验 receipt。

### 历史交易约束与 ST 重建

`namechange` 按年分段并使用 `limit + offset` 分页。`margin_secs`、`margin_detail`、
`suspend_d` 按年分段，`slb_sec_detail` 按月分段，`margin_secs` 和 `margin_detail` 按开放
交易日分段。`st` 做全量分页后按生效日过滤。每个分段都
写不可变 Parquet 和 SHA-256 receipt，重复运行只复用哈希匹配的完整分段。以下命令显式经
xiaodefa 转发，receipt 只记录 token 环境变量对应的 endpoint，不记录 token 值：

```bash
CONSTRAINT_STAGING="$DATA_PLATFORM_ROOT/staging/tushare_constraints_20260731"

marketdata tushare download-a-share-constraint-reference \
  --dataset namechange \
  --out-dir "$CONSTRAINT_STAGING" \
  --start-date 20060101 --end-date 20260731 \
  --token-env TUSHARE_TOKEN_2 \
  --api-url https://proxy-a.example.com

marketdata tushare download-a-share-constraint-reference \
  --dataset margin_secs \
  --out-dir "$CONSTRAINT_STAGING" \
  --start-date 20150101 --end-date 20260731 \
  --token-env TUSHARE_TOKEN_2 \
  --api-url https://proxy-a.example.com

marketdata tushare download-a-share-constraint-reference \
  --dataset margin_detail \
  --out-dir "$CONSTRAINT_STAGING" \
  --start-date 20150101 --end-date 20260731 \
  --token-env TUSHARE_TOKEN_2 \
  --api-url https://proxy-a.example.com

marketdata tushare download-a-share-constraint-reference \
  --dataset suspend_d \
  --out-dir "$CONSTRAINT_STAGING" \
  --start-date 20080101 --end-date 20260731 \
  --token-env TUSHARE_TOKEN_2 \
  --api-url https://proxy-a.example.com

marketdata tushare download-a-share-constraint-reference \
  --dataset st \
  --out-dir "$CONSTRAINT_STAGING" \
  --start-date 20080101 --end-date 20260731 \
  --token-env TUSHARE_TOKEN_2 \
  --api-url https://proxy-a.example.com

marketdata tushare download-a-share-constraint-reference \
  --dataset slb_sec_detail \
  --out-dir "$CONSTRAINT_STAGING" \
  --start-date 20200101 --end-date 20241231 \
  --token-env TUSHARE_TOKEN_2 \
  --api-url https://proxy-a.example.com
```

`margin_secs` 只表示证券进入融资融券标的范围，是做空资格的上界。它不能证明当日有券，也不
包含借券费、可借数量或召回概率。`margin_detail` 和 `slb_sec_detail` 是已发生、已报告的融资融券
或转融通交易，无法证明研究组合当时可借到的库存。`st` 是状态变更事件，只用于交叉验证，不能直接
当作完整逐日 ST 状态。`slb_sec_detail` 的实际历史覆盖可能短于研究窗口，应保留边界空分段，不能
把空结果外推为当日绝对不可借。全历史逐日下载请求量较大，应先用短窗口 canary 验证权限和
endpoint，再续跑同一 staging 目录。

历史 ST 由完整 `namechange` 时间线重建。构建器先用后续更名截断开放区间，再以 instruments
的上市日和退市日前一日限制有效区间，并把北交所旧代码归一到当前 920 代码。只在交易日历开放日
展开正样本。可用 2022 年以后的 `stock_st` 做交叉验证：

```bash
marketdata tushare build-a-share-st-history \
  --namechange "$CONSTRAINT_STAGING/namechange.parquet" \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --instruments "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --stock-st "$DATA_PLATFORM_ROOT/assets/tushare/a_share/stock_st/a_share_all_stock_st_latest.parquet" \
  --out-dir "$CONSTRAINT_STAGING" \
  --start-date 20080101 --end-date 20260731 \
  --min-precision 0.90 --min-recall 0.90

marketdata tushare publish-a-share-constraint-reference \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --source-dir "$CONSTRAINT_STAGING" \
  --target-date 20260731
```

发布默认拒绝缺失资产或未通过阈值的 ST 重建。产物固定标注
`pit_class=reconstructed_pit`、`revision_safe=false`。它可以修复历史 ST 过滤，但不能被描述为
当时实时可见且版本安全的 PIT。`--allow-partial` 只用于诊断性发布，不应进入正式 current。

资金流特征先走 `moneyflow` raw 加日频成交额、市值 overlay。`daily.amount` 是千元口径，构建器会除以
10 转成与 `moneyflow` 金额字段一致的万元口径。`daily_basic.circ_mv` 也是万元口径。

```bash
marketdata tushare build-a-share-flow-ownership-features \
  --moneyflow-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/moneyflow/a_share_all_20240101_20260529_moneyflow" \
  --daily-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_20240101_20260529_daily" \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_20240101_20260529_daily_basic" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/flow_ownership_features/a_share_all_flow_ownership_features_latest" \
  --min-rows 100000 --min-symbols 5000

marketdata tushare validate-a-share-flow-ownership-features \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/flow_ownership_features/a_share_all_flow_ownership_features_latest" \
  --min-rows 100000 --min-symbols 5000
```

热点研究 raw 资产分三类镜像。热榜、题材行情、涨停/炸板榜、连板天梯和最强板块按交易日分区。
券商盈利预测和机构调研按日历事件日分区。券商月度金股按月份分区。使用 15000 分账户时应显式传
`--token-env TUSHARE_TOKEN_2`，并确认本地配置了匹配的 `TUSHARE_API_URL_2`。

```bash
marketdata tushare mirror-a-share-ths-hot \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/ths_hot/a_share_all_ths_hot_latest" \
  --start-date 20240101 --end-date 20260529 \
  --market 热股 --is-new Y \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-dc-concept \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/dc_concept/a_share_all_dc_concept_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-dc-concept-cons \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/dc_concept_cons/a_share_all_dc_concept_cons_latest" \
  --start-date 20260203 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

# dc_concept_cons 数据从 20260203 开始，单次最多返回 3000 行。镜像会自动分页并写逐日完整性 receipt。

marketdata tushare mirror-a-share-kpl-list \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/kpl_list/a_share_all_kpl_list_latest" \
  --start-date 20240101 --end-date 20260529 \
  --tag 涨停 --tag 炸板 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-kpl-concept-cons \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/kpl_concept_cons/a_share_all_kpl_concept_cons_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-limit-step \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/limit_step/a_share_all_limit_step_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-limit-cpt-list \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/limit_cpt_list/a_share_all_limit_cpt_list_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

# 开盘 / 收盘集合竞价接口需要账户单独授权。当前默认只请求 ts_code/trade_date，
# 确认授权和字段后用 --fields 显式传完整字段集合。
marketdata tushare mirror-a-share-stk-auction-open \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/stk_auction_o/a_share_all_stk_auction_o_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-stk-auction-close \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/stk_auction_c/a_share_all_stk_auction_c_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-report-rc \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/report_rc/a_share_all_report_rc_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-stk-surv \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/stk_surv/a_share_all_stk_surv_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-broker-recommend \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/broker_recommend/a_share_all_broker_recommend_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-moneyflow-ths \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/moneyflow_ths/a_share_all_moneyflow_ths_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-limit-list-ths \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/limit_list_ths/a_share_all_limit_list_ths_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-margin-detail \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/margin_detail/a_share_all_margin_detail_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-margin \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/margin/a_share_all_margin_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

marketdata tushare mirror-a-share-hsgt-top10 \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/hsgt_top10/a_share_all_hsgt_top10_latest" \
  --start-date 20240101 --end-date 20260529 \
  --token-env TUSHARE_TOKEN_2 --skip-existing

# 同花顺概念指数目录是单次快照，不需要 start-date / end-date 参数。
marketdata tushare mirror-a-share-ths-index \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/ths_index/a_share_all_ths_index_latest" \
  --src THS \
  --token-env TUSHARE_TOKEN_2

# 指数日线按指数代码逐只拉取，合并为单文件资产。--index-code 可重复。
# 用于 benchmark 收益等需要指数行情的场景。
marketdata tushare mirror-a-share-index-daily \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/index_daily/a_share_all_index_daily_20260807" \
  --index-code 000300.SH --index-code 000905.SH \
  --start-date 20220101 --end-date 20260807 \
  --token-env TUSHARE_TOKEN_2 --api-url https://proxy-a.example.com

# 同花顺概念成分数据先拉取 ths_index 获取全部概念代码，再逐概念下载 ths_member。
# 下载量较大，建议设置 --request-interval-seconds 控制频率。
marketdata tushare mirror-a-share-ths-member \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/ths_member/a_share_all_ths_member_latest" \
  --token-env TUSHARE_TOKEN_2 --request-interval-seconds 0.1

marketdata tushare build-a-share-hotspot-features \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_20150101_20260608_daily_basic" \
  --ths-hot-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/ths_hot/a_share_all_ths_hot_latest" \
  --dc-concept-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/dc_concept/a_share_all_dc_concept_latest" \
  --dc-concept-cons-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/dc_concept_cons/a_share_all_dc_concept_cons_latest" \
  --kpl-list-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/kpl_list/a_share_all_kpl_list_latest" \
  --kpl-concept-cons-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/kpl_concept_cons/a_share_all_kpl_concept_cons_latest" \
  --limit-step-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/limit_step/a_share_all_limit_step_latest" \
  --report-rc-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/report_rc/a_share_all_report_rc_latest" \
  --stk-surv-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/stk_surv/a_share_all_stk_surv_latest" \
  --broker-recommend-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/broker_recommend/a_share_all_broker_recommend_latest" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/hotspot_features/a_share_all_hotspot_features_latest" \
  --start-date 20240101 --end-date 20260529 \
  --min-rows 100000 --min-symbols 3000

marketdata tushare validate-a-share-hotspot-features \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/hotspot_features/a_share_all_hotspot_features_latest" \
  --min-rows 100000 --min-symbols 3000
```

`dc_concept_cons` 镜像按 `limit/offset` 拉到短末页，并校验每一行的 `trade_date` 严格等于
目标日、业务键 `trade_date + theme_code + ts_code` 唯一。接口结果按 `theme_code` 有序。
分页边界可能切在同一题材内部并产生少量跨页重叠，因此镜像会识别相邻页共享的边界题材，按
`theme_code` 单独重拉这些题材后替换边界行。若页内题材顺序倒退、非边界业务键重叠、边界
重拉仍重叠、达到最大页数或未出现短末页，任务会 fail closed，不发布新的 partition 或
manifest。

成功 manifest 在 `completeness.trade_dates[YYYYMMDD]` 记录总数据页数、请求数、offset 页数、
边界修复页数、行数、独立题材数和 `theme_code` 行覆盖率。`page_count` 包含 offset 数据页与
边界修复数据页，`request_count` 还包含用于证明结束的空页请求。只有逐日 `complete: true`
才可进入生产 fallback。瞬时空结果会原子写入 `complete: false`，同时保留 last-known-good
Parquet。生产消费者必须以目标日 receipt 为准，不能因旧文件仍存在而判定可用。

公募基金持仓先按报告期下载 `fund_portfolio` raw，再按披露日后一交易日生成 PIT 持仓特征。
年报和半年报可能超过 100 页，续跑时保留 `--skip-existing`，必要时提高
`--max-pages-per-period`。

```bash
marketdata tushare build-a-share-fund-portfolio-features \
  --fund-portfolio-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_portfolio/a_share_all_20141231_20260529_fund_portfolio" \
  --daily-basic-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily_basic/a_share_all_20150101_20260608_daily_basic" \
  --out-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_portfolio_features/a_share_all_fund_portfolio_features_latest" \
  --available-delay-days 1 \
  --min-rows 100000 --min-symbols 3000

marketdata tushare validate-a-share-fund-portfolio-features \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/fund_portfolio_features/a_share_all_fund_portfolio_features_latest" \
  --min-rows 100000 --min-symbols 3000
```

中信行业同理，将 `--industry-system` 改为 `citic_l3`，并使用
`ci_index_member` extract 中的三级行业字段。TuShare `index_classify` 用于校验申万
2014 / 2021 taxonomy list，不替代 membership interval。

理杏仁行业成分如果在授权环境中以指定日期 snapshot 返回，只能作为 snapshot source 处理。
使用前必须通过本地 entitlement docs 或 authenticated probe 确认 endpoint、字段和覆盖范围。
写入验证记录时应保留 snapshot date、taxonomy、level、source endpoint/extract 和 snapshot
frequency。用 snapshot diff 反推区间时，不得把采样频率之外的变更日写成精确 provider
事实。只有当前行业标签、没有 effective history 或 dated snapshot provenance 的输入，不允许用于
历史 PIT 研究。

```bash
marketdata tushare list-a-share-fundamentals-specs
marketdata tushare plan-a-share-fundamentals --help
marketdata tushare download-a-share-fundamentals --help
marketdata tushare check-a-share-fundamentals-state --help
marketdata tushare list-a-share-fundamentals-failures --help
marketdata tushare compact-a-share-fundamentals-raw --help
marketdata tushare normalize-a-share-fundamentals --help
marketdata tushare validate-a-share-normalized-fundamentals --help
marketdata tushare build-a-share-fundamentals-pit --help
marketdata tushare validate-a-share-fundamentals-pit --help
marketdata tushare publish-a-share-fundamentals --help
marketdata tushare publish-a-share-pit-fundamentals --help
```

完整 raw-to-PIT 运维见 [../a-share-fundamentals.md](../a-share-fundamentals.md)。

## RQData A 股入口（已退役）

RQData A 股在线读取与采集入口已在 2026-07-26 完全退役，相关代码（`rqdata_a_share.py`、`cli_rqdata.py`、`rqdata_runtime*.py`）已从活跃包移除。当前 A 股主线统一使用 TuShare 平台资产，新研究不应依赖 RQData。
