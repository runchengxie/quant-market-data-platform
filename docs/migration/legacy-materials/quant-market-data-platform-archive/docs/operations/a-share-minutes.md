# A 股分钟数据

本页记录当前生产资产、来源口径、重建流程和切换规则。历史试验和已经退役的中间版本不再展开。

## TuShare 历史可用性探测

2026-08-31 使用当前 TuShare 代理和 `stk_mins` 对 `000001.SZ`、`600000.SH` 做了低成本探测：
2016-01-04、2020-01-02、2021-07-01、2022-07-14 和 2022-07-15 均返回完整 241 根 1 分钟 bar。
因此 2022-07-15 是此前替换 campaign 的人为起点，并非 TuShare 分钟数据的提供方下限。

该结论是代表性股票和代表性日期的可用性证据，不等同于全市场全历史已完成下载。大批量回填仍须
按交易日动态股票池执行完整 sidecar、241 网格、唯一键和 schema 校验。最早日期应由小规模探测
确认后再生成回填计划。

2026-08-31 至 2026-09-01 的历史 campaign 首日 canary 已实际请求 2016-01-04 至 2016-01-11
的六个交易日。主表缺口已通过 `daily` 与 `daily_basic` 交集中的 provider-only 历史代码规则
修复，但 TuShare 对 `001872.SZ`、`001914.SZ`、`601360.SH` 返回 HTTP 成功、0 根分钟 bar。
因此 canary 保持 `partial`，没有发布历史 alias，也没有把这三个供应商确认缺失静默当作完整数据。
后续历史回填需增加可审计的 provider-no-data 例外清单，或继续保持全市场完整性门禁。

## 当前生产状态

截至 2026-07-27，稳定入口为：

```text
$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m
```

该相对符号链接当前指向：

```text
minute_1m_v3_20260714
```

最终 coverage receipt 位于：

```text
$DATA_PLATFORM_ROOT/metadata/minute_fusion/a_share_minute_1m_v3_20260714.coverage.json
```

receipt 已通过 `status=passed` 和 `quality_status=passed`。当前覆盖如下：

| 指标 | 当前值 |
| --- | ---: |
| 日期范围 | 2016-01-04 至 2026-07-14 |
| 交易日 | 2,556 |
| 分钟行数 | 2,587,512,152 |
| Guan 年度日 | 2,430 |
| Guan deal 日 | 37 |
| TuShare 全 A 日 | 89 |
| 沪深完整日 | 2,556 |
| 按点时市场定义完整的 A 股日 | 1,515 |
| 北交所已知缺口日 | 1,041 |

当前 `coverage_status=full_sh_sz`，表示沪深全区间完整。89 个 TuShare 日期同时包含沪、深、北三地。其余北交所成立后的 1,041 个 Guan 日期只含沪深，receipt 将这部分记录为已知缺口。

分钟资产已通过 `minute_1m_tushare` alias、operational receipt 和 current contract/数据集注册表发布。
`minute_1m` 继续保留为 Guan legacy 回滚入口。下游默认读取 TuShare，复现 Guan 结果时应显式选择
`minute_dataset="legacy"`，并保留对应 receipt 路径或哈希。

2026-07-15 启动的 Guan 全市场 TuShare 替换已完成 820 个可获得交易日的采集和结构复验。
2026-07-27 将它发布为独立候选版本：

```text
$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m_tushare_candidate_v1_20260727
```

候选版本包含 2022-07-15 至 2026-07-08 的 1,044,752,352 行，按交易日完整包含沪、深、北三地。
文件通过 hardlink 从已冻结 staging 提升，不复制或改写源值。候选 receipt 位于：

```text
$DATA_PLATFORM_ROOT/metadata/minute_candidate/
  tushare_all_a_20220715_20260708.candidate.json
```

全量 Guan/TuShare 语义审计比较了 993,992,266 个沪深共同分钟键。价格、量额和大部分日频聚合
特征高度一致，但日内收益横截面排名的逐日中位相关性约为 0.971，未通过 0.99 的候选切换门槛。
owner-native DailyWatch20 特征进一步确认来源敏感性：2025 年 `minute_last_30m_return` 的逐日
截面排名中位相关性约为 0.904，`minute_active_ratio` 约为 0.866。针对 2025 年来源断点的
production-variant 诊断筛查中，63 个 OOS 日的 IC、Top20 标签收益和组合毛收益逐日相关性分别
约为 0.685、0.710 和 0.719。该筛查没有采用发布级 504/252 回测规模，但足以拒绝无条件替换。

最终验收回执位于：

```text
$DATA_PLATFORM_ROOT/metadata/minute_candidate/
  tushare_all_a_20220715_20260708.acceptance.json
```

因此 receipt 固定为 `quality_status=review_required`、
`canonical_cutover_approved=false` 和 `current_alias_mutated=false`。这批数据可用于独立复验、
北交所/241 网格研究和备源，但不属于当前 canonical，也不应混入冻结的 Guan 因子发现样本。

### TuShare operational 主线

跨来源等价切换被拒绝后，TuShare 采用独立的 native 基线晋级。两个稳定入口并行存在：

```text
$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m
$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m_tushare
```

`minute_1m` 继续指向冻结的 Guan legacy canonical。`minute_1m_tushare` 只指向通过完整分区校验的
TuShare 版本。TuShare 晋级不改变 Guan alias，也不宣称两种来源的日内特征、模型或选股结果应当
相同。TuShare 现已作为下游默认分钟来源。需要复现历史 Guan 结果时，必须显式选择
`minute_dataset="legacy"`。TuShare 链路仍需重新生成特征、训练模型并设定风控阈值。

2026-09-04 对 `minute_1m_tushare_v1_20260903` 完成全区间结构清点。946 个交易日和
5,027,904 个股票日共 1,211,724,864 根 bar 均通过 241 根网格、09:30 起始、15:00 结束、
唯一时间键和统一 schema 检查。详细结果登记在
`metadata/minute_operational/acceptance/minute_1m_tushare_v1_20260903.full_audit.json`。
这证明数据资产本身达到结构正式使用门槛，不替代 TuShare-native 因子、模型和回测重新基线。

首次组装从已审计的 820 日候选出发，再加入 2026-07-09 之后的完整增量分区：

```bash
uv run python scripts/operations/tushare_minute_operational.py assemble \
  --base-receipt \
    "$DATA_PLATFORM_ROOT/metadata/minute_candidate/tushare_all_a_20220715_20260708.candidate.json" \
  --incremental-root \
    "$DATA_PLATFORM_ROOT/assets/tushare/a_share/minute_1m_full_v1_20260711" \
  --incremental-root "$INCREMENTAL_DATA_ROOT" \
  --trade-calendar \
    "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --end-date YYYYMMDD \
  --output-dir \
    "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m_tushare_v1_YYYYMMDD" \
  --receipt-json \
    "$DATA_PLATFORM_ROOT/metadata/minute_operational/versions/minute_1m_tushare_v1_YYYYMMDD.json"

uv run python scripts/operations/tushare_minute_operational.py promote \
  --version-receipt \
    "$DATA_PLATFORM_ROOT/metadata/minute_operational/versions/minute_1m_tushare_v1_YYYYMMDD.json" \
  --alias "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m_tushare" \
  --legacy-alias "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m" \
  --receipt-json \
    "$DATA_PLATFORM_ROOT/metadata/minute_operational/promotions/minute_1m_tushare_v1_YYYYMMDD.json"
```

组装器逐文件复验 SHA-256，增量分区还必须具有 `status=complete`、241 根网格、完整动态股票池、
唯一主键和有效 schema。版本通过同文件系统 hardlink 发布。promotion receipt 同时记录 Guan
alias 切换前后的解析目标，任何变化都会使晋级失败。

日常入口 `scripts/operations/tushare_minute_operational_daily.py` 会从当前 TuShare receipt 的
`date_max` 追赶到最近交易日，使用共享请求配额账本，发布新的不可变版本后只原子移动
`minute_1m_tushare`。下载 partial 时不发布，下次从 sidecar 记录的缺失股票继续。

2026-08-31 的 operational receipt 已将 `minute_1m_tushare` 更新至 2026-08-31。该 alias
是 TuShare 独立 native 基线，尚未替换 Guan 的 `minute_1m` canonical alias。

历史缺口应先检查已有完整分区，避免重复消耗接口额度。确认缺口日期在当前版本的
`date_max` 以内，并且某个 `--incremental-root` 中恰好存在一份完整分区后，可在 assemble
命令中重复传入：

```bash
  --repair-date YYYYMMDD
```

修复分区必须通过与日常增量相同的 sidecar、股票池、241 分钟网格、唯一键、行数、schema
和 Parquet SHA-256 校验。组装先写入新的不可变版本（staging），不会改变任何稳定入口。
校验成功后再单独执行 promote。promote 使用同文件系统的原子 symlink 替换，因此并发读者
只能看到完整旧版本或完整新版本，不会看到两者混合。promotion receipt 记录切换前后目标，
可用于审计和回滚。

北交所历史分钟查询使用[官方新旧代码对照表](https://www.bseinfo.net/service/code_mapping.html)。
六只试点股票从 2025-05-06 起使用 920 代码，其余存量股票从 2025-10-09 起使用 920
代码。更早日期按当时有效的旧代码请求，返回后统一规范为当前 920 代码再写入分区。直接以
920 代码上市的新股不做转换。这样既符合 `stk_mins` 的历史查询语义，也保持下游主键稳定。

## 文件与来源口径

日分区布局固定为：

```text
assets/derived/a_share/minute_1m_v3_20260714/
└── trade_date=YYYYMMDD/
    └── part-00000.parquet
```

Parquet 只有以下八列：

```text
ts_code, trade_time, open, close, high, low, vol, amount
```

`trade_time` 是中国大陆市场墙钟时间，不带时区。价格和 `amount` 的单位为元，`vol` 的单位为股。

来源标签不写入 Parquet。逐日来源、覆盖层级、市场范围、行数、证券数、时间边界和文件哈希都在 coverage receipt 的 `daily` 数组中。研究特征需要识别来源时，应按交易日连接该数组中的 `canonical_source`、`tier` 和 `market_scope`。

当前来源分段为：

| 时间范围 | canonical 来源 | 起始分钟 | 当前用途 |
| --- | --- | --- | --- |
| 2016-01-04 至 2025-12-31 | Guan annual | 09:31 | 历史发现主样本 |
| 2026 年的 37 个交易日 | Guan deal | 09:30 | 跨来源稳健性检查 |
| 2026 年的 89 个交易日 | TuShare full day | 09:30 | 跨来源稳健性检查 |

同一交易日只保留一个 canonical 来源。整日替换禁止拼接日内片段，也不做统一的一分钟平移。Guan annual 没有固定 241 根网格。Guan deal 和 TuShare 的活跃分钟数也可能不同。开盘占比、尾盘占比、活跃分钟数和成交间隔等特征必须按来源分层检查。

首轮因子发现建议使用 2016 至 2025 年 Guan annual 同源样本。2026 年用于 Guan deal 与 TuShare 的稳健性验证。研究代码需要固定样本时，还应冻结 coverage receipt 的 SHA-256。

## 安装与只读检查

分钟构建依赖通过以下 extra 安装：

```bash
uv sync --extra minute-fusion
```

先确认 alias 和 receipt，不要把版本目录名写死在研究代码中：

```bash
readlink "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m"

python - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["DATA_PLATFORM_ROOT"])
receipt = root / "metadata/minute_fusion/a_share_minute_1m_v3_20260714.coverage.json"
payload = json.loads(receipt.read_text(encoding="utf-8"))
print(payload["status"], payload["quality_status"], payload["coverage_status"])
print(payload["summary"])
PY
```

## 重建流程

所有重建都写入新的版本目录。`minute_1m` 只在最终 receipt 通过后切换。

生产链路依次包含：

1. `marketdata data build-guan-annual-minutes` 构建 Guan 年度日分区。
2. `marketdata data build-guan-deal-minutes` 聚合并校验 Guan 移动盘逐笔数据。
3. `marketdata tushare mirror-a-share-mins` 或不可变 backfill 计划生成 TuShare 完整日。
4. `marketdata data finalize-a-share-minute-coverage` 物化整日替换并生成 coverage receipt。
5. `scripts/operations/detach_a_share_minute_v3.py` 解除从旧版本继承的 hardlink。
6. `scripts/operations/cutover_a_share_minute.py` 做原子切换。

各命令的参数会随目标日期和 receipt 组合变化。执行前先读取帮助：

```bash
marketdata data build-guan-annual-minutes --help
marketdata data build-guan-deal-minutes --help
marketdata data finalize-a-share-minute-coverage --help
marketdata tushare mirror-a-share-mins --help
marketdata tushare plan-a-share-minute-backfill --help
marketdata tushare run-a-share-minute-backfill --help
```

### TuShare 替换候选验收

完整 campaign 结束后，先生成逐分区 inventory，再执行全区间语义与特征回归，最后只发布独立
候选版本。三个步骤都不修改 `minute_1m`：

```bash
uv run python scripts/operations/tushare_minute_candidate.py inventory \
  --campaign-manifest "$CAMPAIGN_DIR/manifest.json" \
  --canonical-coverage "$DATA_PLATFORM_ROOT/metadata/minute_fusion/<coverage.json>" \
  --output-json "$DATA_PLATFORM_ROOT/metadata/minute_candidate/<inventory.json>"

uv run python scripts/operations/tushare_minute_candidate.py audit \
  --inventory "$DATA_PLATFORM_ROOT/metadata/minute_candidate/<inventory.json>" \
  --output-json "$DATA_PLATFORM_ROOT/metadata/minute_candidate/<semantic-audit.json>" \
  --work-dir "$DATA_PLATFORM_ROOT/staging/minute_candidate_audit/<candidate>" \
  --threads 4

uv run python scripts/operations/tushare_minute_candidate.py publish \
  --inventory "$DATA_PLATFORM_ROOT/metadata/minute_candidate/<inventory.json>" \
  --semantic-audit \
    "$DATA_PLATFORM_ROOT/metadata/minute_candidate/<semantic-audit.json>" \
  --output-dir "$DATA_PLATFORM_ROOT/assets/derived/a_share/<candidate-version>" \
  --receipt-json "$DATA_PLATFORM_ROOT/metadata/minute_candidate/<candidate-receipt.json>"
```

审计对 Guan annual 使用共同的 `09:31–15:00` 时段，对 Guan deal 使用完整的
`09:30–15:00` 时段。源值始终保持不变，不做分钟平移、填值、裁剪或日内拼接。逐日 checkpoint
允许中断续跑。候选发布只使用同文件系统 hardlink，并在版本目录内嵌入同一份 receipt。

兼容入口 `marketdata data fuse-a-share-minutes` 只用于旧混合源迁移。新版本沿用年度构建、deal 构建、整日替换、覆盖终检和原子切换流程。

### Guan 构建约束

2016 至 2025 年年度源执行 `vol = volume * 100` 和 `amount = amount`。2026 年年度源执行 `vol = volume` 和 `amount = amount / 100`。构建器会检查日期、交易时段、重复主键、有限数、负量额和 OHLC 边界。

Guan deal 的成交价单位为分，成交量单位为股。成交价除以 100，成交额按 `Price * Volume / 100` 计算。`09:25` 的真实开盘集合竞价成交归入 `09:30`，连续竞价使用分钟结束标签，收盘集合竞价归入 `15:00`。构建器不补造零量分钟。

年度文件应串行扫描。默认内存预算由运行时根据系统和 cgroup 可用内存计算，也可以显式传入 `--memory-limit`。续跑只信任源文件指纹、成功 receipt 和当前分区复验结果。

### TuShare staging 约束

长历史下载使用不可变计划和独立 staging：

```bash
marketdata tushare plan-a-share-minute-backfill \
  --scope all-a \
  --start-date YYYYMMDD --end-date YYYYMMDD \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --instruments "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --backfill-root "$DATA_PLATFORM_ROOT/staging/tushare_minute_backfill" \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/<name>.plan.json" \
  --segment month --batch-size 20 --cooldown-seconds 1 \
  --token-env TUSHARE_TOKEN_2

marketdata tushare run-a-share-minute-backfill \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/<name>.plan.json" \
  --receipt "$DATA_PLATFORM_ROOT/metadata/minute_backfill/<name>.receipt.json" \
  --token-env TUSHARE_TOKEN_2 --dry-run
```

确认计划后再去掉 `--dry-run`。plan 会绑定交易日历、股票表、日期集合、批次大小、endpoint 和输出目录。receipt 会明确记录 `writes_production=false`。staging 数据需要经过完整 sidecar、241 根网格、股票池、主键、时段、schema、有限数和文件哈希检查，才能进入后续 promotion。

历史反向回填使用同一套不可变计划，但显式指定 `--date-order descending`。计划器会先枚举交易日，
再按月份将日期和月份分段都排列为最新到最早。执行器仍按 receipt 顺序串行、可中断续跑：

```bash
marketdata tushare plan-a-share-minute-backfill \
  --scope all-a --start-date 20160104 --end-date 20220714 \
  --trade-cal "$DATA_PLATFORM_ROOT/assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet" \
  --instruments "$DATA_PLATFORM_ROOT/assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet" \
  --backfill-root "$DATA_PLATFORM_ROOT/staging/tushare_minute_backfill_reverse" \
  --plan "$DATA_PLATFORM_ROOT/metadata/minute_backfill/reverse_to_20220714.plan.json" \
  --segment month --date-order descending --batch-size 20 \
  --token-env TUSHARE_TOKEN_2 --api-url https://proxy-a.example.com --dry-run
```

当前 TuShare operational 资产最早为 `20220715`，理论上的第一候选回填日是 `20220714`。当前 pilot
已完成 `20220714、13、12、11、08、07`，因此调度器下一次会选择 `20220706`。候选日只代表本地
交易日历确认该日为开放日。下载完成和晋级检查通过需要单独确认。反向回填的输出始终保持
`writes_production=false`，完成逐日质量验收后才允许生成新的 operational version。

历史反向回填由 `tushare-minute-reverse-backfill.service` 提供手动入口，生产环境不启用对应 timer。
原因是它与全 A operational canonical 更新共享 request quota，自动运行可能耗尽当日额度并阻塞正式
分钟数据更新。需要执行历史回填时，应先确认当日剩余额度和 operational 更新已完成，再单独运行该
服务。它只写入 `staging/tushare_minute_backfill_reverse`，不会自动修改任何生产 alias。

渲染并检查 systemd 单元后，默认保持 timer 禁用。需要临时执行时：

```bash
systemctl --user start tushare-minute-reverse-backfill.service
```

默认 `--max-dates 1` 用于先观察连续夜间窗口。连续稳定后，再考虑增加每日日期数。

历史反向回填默认使用 `--bj-missing-policy report`。仅北交所股票未返回数据，且其他股票的
分钟网格和分区文件校验通过时，任务返回 `accepted_bj_missing` 并正常退出。缺失股票、日期及
数据凭证记录在 `metadata/minute_backfill/reverse_scheduler/bj_missing_report.json`。原始分区
和下载回执仍保留 `partial`，完整市场验收和生产晋级规则保持独立。沪深缺失、北交所格式异常、
请求异常及文件损坏继续导致任务未完成。传入 `--bj-missing-policy error` 可恢复严格模式。

调度器会跳过已完整下载或已按北交所缺失策略接受的日期。`--max-dates` 是单次计划的日期上限，
已有未完成计划优先续跑，调大上限不会向旧计划追加日期。旧计划全部日期尚未验收时，本次
运行可能少于新上限。若旧计划混有已验收日期，则保留旧计划和回执，并为尚未验收的候选日期
生成新计划，避免重复请求已接受的北交所缺失。超过上限的大计划保持原样。


北交所独立补齐使用 `--scope bj-only`。整日替换使用 `--scope all-a`。两类计划使用各自的 plan、run 目录和 receipt。

### Guan 原始数据与重叠审计

移动盘原始文件先归档到 enclosure 的 `_incoming` 目录，再通过已验证的 receipt 提升到 `source_native/guan_mobile`。以下工具分别负责归档、内容复验和 provider-native 目录整理：

```bash
.venv/bin/python scripts/operations/archive_guan_mobile.py --help
.venv/bin/python scripts/operations/promote_guan_mobile.py --help
```

归档保留相对路径、大小、修改时间和 SHA-256。promotion 默认使用同文件系统 hardlink，跨文件系统时需要显式选择 copy。deal 重建后的 coverage receipt 还应绑定 promotion receipt 和 provider root。

Guan 与 TuShare 的重叠日检查使用只读脚本：

```bash
.venv/bin/python scripts/operations/audit_minute_overlap.py --help
```

审计比较时间键、价格、成交量和成交额，并输出诊断 receipt。诊断结果不能自动触发一分钟平移或来源改写。

## 覆盖终检

`marketdata data finalize-a-share-minute-coverage` 负责来源对账、整日替换、分区审计和最终 receipt。生产调用必须显式提供目标版本目录、交易日历、Guan manifests、TuShare plan 与 receipt，以及相关 source audit。

当前发布合同要求：

- 交易日与分区一一对应。
- 每个分区只有一个普通 Parquet 文件。
- 来源日期数与 receipt 完全一致。
- TuShare 替换以整日为单位。
- 重复键、错误日期、错误市场、非法时段和致命量额问题均为零。
- `status=passed` 与 `quality_status=passed` 同时成立。
- 每个分区的 SHA-256 在 promotion、coverage 和切换时保持一致。

完整 A 股发布要求北交所覆盖齐全。当前版本保留 1,041 个北交所已知缺口，因此发布时必须显式选择沪深合同。

若后续发布完整 A 股版本，`finalize-a-share-minute-coverage` 需要同时接收北交所 overlay 的数据目录、production plan 和 passed receipt。overlay 只追加动态北交所股票池，并对现有 Guan 沪深基底做独立对账。三个 overlay 参数需要成组出现，最终 coverage 才能达到 `full_a_share`。

TuShare 的普通 VWAP 与 OHLC 偏差记为诊断。超过价格区间 10% 并计入每分钟 1 元容差的名义金额异常会阻断发布。TuShare 出现 `vol=0, amount>0` 也会阻断。Guan annual 的价格基准与量额基准存在历史差异，其准入依靠逐年单位合同、源指纹、数值检查和重叠日对账。

## 切换

切换前确认没有分钟构建进程，并先运行 dry-run。新版本使用以下参数关系：

```bash
.venv/bin/python scripts/operations/cutover_a_share_minute.py \
  --coverage-manifest "$DATA_PLATFORM_ROOT/metadata/minute_fusion/<new-coverage.json>" \
  --current-path "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m" \
  --new-version "$DATA_PLATFORM_ROOT/assets/derived/a_share/<new-version>" \
  --backup-path "$DATA_PLATFORM_ROOT/assets/derived/a_share/minute_1m_pre_<new-version>" \
  --target-market-scope sh-sz \
  --dry-run
```

把占位符替换为本次新版本，并确认 backup 路径尚未存在。dry-run 通过后再执行实际交换。`--target-market-scope` 默认值为 `full-a-share`。当前 coverage 含北交所已知缺口，漏写 `--target-market-scope sh-sz` 会被拒绝。

工具会获取 `.a-share-minute-dataset.lock`，复核版本树、分区哈希、receipt 绑定和 `st_nlink == 1`。切换使用 `renameat2(RENAME_EXCHANGE)` 原子交换相对链接。文件系统不支持该操作时，工具会在修改 current 前停止。

## 回滚

当前切换脚本把生产合同固定为 2016-01-04 至 2026-07-14，共 2,556 个交易日，其中 TuShare 全 A 日为 89 个。上一版本 `minute_1m_v3_20260711` 只有 2,553 个交易日和 86 个 TuShare 全 A 日，因此当前脚本会拒绝将它直接切回 current。

`minute_1m_pre_v3_20260714` 仍需保留，它记录上一版本位置并为恢复审计提供证据。目前还缺少兼容旧日期合同的通用回滚命令。发生事故时按以下顺序处理：

1. 停止分钟写入和发布任务，保留 dataset lock、current、pending 与 backup 现场。
2. 运行只读检查，确认 current 指向、coverage receipt、版本目录和分区哈希。
3. 优先生成满足当前 2,556 日合同的修复版本，再通过 dry-run 和原子切换发布。
4. 确需恢复旧日期合同，先补充显式 rollback contract、行为测试和演练记录。

禁止删除或手工改写 `minute_1m`。切换进程异常中断时，保留 `.minute_1m.cutover-pending`，使用同一组原切换参数重跑，让脚本根据 current、pending 和 backup 状态恢复。

数据退役只通过 `marketdata governance plan-retention` 生成只读清单。current、rollback、receipt 和仍有审计价值的 staging 证据需要成组复核。

## 相关测试

```bash
uv run --extra dev python -m pytest \
  tests/test_a_share_minute_build.py \
  tests/test_a_share_minute_fusion.py \
  tests/test_a_share_minute_coverage.py \
  tests/test_a_share_minute_bj_overlay.py \
  tests/test_tushare_a_share_mins.py \
  tests/test_tushare_minute_backfill.py \
  tests/test_tushare_minute_replacement_campaign.py \
  tests/test_cutover_a_share_minute.py \
  tests/test_detach_a_share_minute_v3.py \
  tests/test_archive_guan_mobile.py \
  tests/test_guan_mobile_raw.py \
  tests/test_audit_minute_overlap.py
```

TuShare token、分钟 backfill、full-day chunk 和 staging 续跑的详细参数见 [a-share-tushare.md](a-share-tushare.md)。
DailyWatch20 的 TuShare-native 特征已生成并与 Guan 版本完成共同日期和股票的对照。
共同样本为 3,716,570 行。各主要特征的 Pearson 相关为 0.959 至 0.999，差异最大的
是 `minute_volume_concentration` 和 `minute_price_volume_corr`。这确认了来源血缘和
特征可用性，但不把特征数值视为等价。对照结果登记在
`metadata/minute_operational/acceptance/daily_watch20_tushare_native_rebaseline_v1.json`。
模型重训和 release-grade 回测仍需以 TuShare-native 特征为输入后单独完成。
