# TuShare 分钟数据运维计划

## 数据目录 retention

`market-data-platform-retention.timer` 每日 22:05（带随机延迟）运行仓库内维护的窄策略清理器。
它只处理带日期的 A 股 `daily_clean` 和 `daily_clean_inputs` 目录，默认各保留最新两个版本，
保护仍被 sibling symlink 引用的目录，并在删除前写入 `scheduled-latest.tsv`。同一次运行还会调用
`marketdata governance plan-retention`，生成独立的 `governance-latest.tsv`。治理规划本身仍是只读的。

使用本仓库 renderer 安装 unit。不要让服务回退到 `$HOME/bin` 或
`research-workspace` 下的脚本：

```bash
uv run python scripts/operations/render_tushare_minute_campaign_units.py \
  --template-glob 'market-data-platform-retention.*' \
  --home "$HOME" --mdp-dir "$MDP_DIR" \
  --data-platform-root "$DATA_PLATFORM_ROOT" \
  --campaign-manifest /dev/null --logs-dir "$HOME/.hermes/logs" \
  --marketdata-cli "$MDP_DIR/.venv/bin/marketdata" \
  --output-dir "$HOME/.config/systemd/user"
systemctl --user daemon-reload
systemctl --user enable --now market-data-platform-retention.timer
```

为期四年的替换任务已经完成。夜间、加速、尾部和硬停止 timer 仍作为历史模板保留，
但必须保持禁用。`tushare-minute-quota-coordinator.timer` 继续为共享请求账本提供服务。

`tushare-minute-operational-daily.timer` 在工作日的 Asia/Shanghai 19:10 运行。
它会补齐所有缺失的开市日期，要求 241 根完整 sidecar，发布不可变的
`minute_1m_tushare_v1_YYYYMMDD` 版本，并且只原子切换 `minute_1m_tushare` 别名。
发布回执会验证 Guan 的 `minute_1m` 别名保持不变。

## 当前每日顺序和限额

所有下载路径使用同一套按 token 限定的请求政策：

- 保证额度：10,000 次请求。
- 突发上限：20,000 次请求。
- 仅供突发模式使用的安全储备：500 次请求，因此有效突发上限为 19,500 次。
- 旧的 160,000,000/4,000,000 行设置继续作为一致的遥测指标和软工作量参考，
  `gate=requests` 才是权威门禁。

当前计划如下：

1. `00:05`：协调器在工作日为 `daily_watch20` 获取 300 次请求额度，为
   `top200_factor_observation` 获取 30 次请求额度。重复启动会返回相同的 hold ID，不会重复占用额度。
2. DailyWatch 和 Top200 使用各自登记的额度，并发布原始数据完整性标记。
3. `19:10`：全 A 股分钟数据运维更新使用剩余保证额度，消费者为
   `operational_canonical`，不会启用突发模式。
4. 历史反向回填模板保留为手动工具，但生产环境不启用 `reverse_backfill` timer。它与全 A 运维更新共享
   请求额度，自动运行会挤占当天的 operational canonical 预算。

已完成的替换任务曾使用 `00:45–04:20`、`08:00–16:40` 和 `21:15–23:35` 时间段。
运维别名安装后，不要重新启用这些 timer。

## 原始数据完整性契约

标记文件位于：

```text
$DATA_PLATFORM_ROOT/metadata/tushare/minute_quota/raw_completeness/
  YYYYMMDD/<consumer>.json
```

`YYYYMMDD` 表示 Asia/Shanghai 时区的额度日期。有效的原子 JSON 标记包含
`schema_version: 1`、匹配的 `quota_date` 和 `consumer`、八位数字的 `trade_date`、
带时区的 `completed_at`、`raw_complete: true`，以及 SHA-256 与 `evidence_sha256` 匹配的绝对路径
`evidence_path`。证据缺失、不完整、过期或 hash 不匹配时，依赖该证据的任务保持关闭。
周末不要求这两个生产标记。

生产脚本发布标记后，应立即释放自己的剩余额度，不要等待后续分析：

```bash
python scripts/operations/tushare_minute_quota_schedule.py release-ready \
  --manifest "$CAMPAIGN_MANIFEST" \
  --minute-quota-db "$DATA_PLATFORM_ROOT/metadata/tushare/minute_quota/minute_quota.sqlite3" \
  --state-root "$DATA_PLATFORM_ROOT/metadata/tushare/minute_quota/schedule" \
  --raw-completeness-root "$DATA_PLATFORM_ROOT/metadata/tushare/minute_quota/raw_completeness" \
  --consumer top200_factor_observation --weekdays-only --not-before-local 21:00
```

早间生产器使用 `daily_watch20` 作为 `--consumer`，使用 `05:15` 作为
`--not-before-local`。08:00 和 21:15 的 unit 会重复执行释放操作，作为故障恢复兜底。

## 渲染和启用

统一渲染所有模板，确保各个 unit 使用完全一致的路径：

```bash
.venv/bin/python scripts/operations/render_tushare_minute_campaign_units.py \
  --home "$HOME" \
  --mdp-dir "$PWD" \
  --data-platform-root "$HOME/data/quant/market-data-platform" \
  --campaign-manifest "$CAMPAIGN_MANIFEST" \
  --logs-dir "$HOME/.hermes/logs" \
  --output-dir "$HOME/.config/systemd/user"

systemctl --user daemon-reload
systemctl --user enable --now \
  tushare-minute-quota-coordinator.timer \
  tushare-minute-operational-daily.timer
```

00:05 协调器和 19:10 运维 timer 是持久任务。运维运行器根据当前嵌入的回执推导缺失开市日期范围，
因此漏跑后可以补齐数据，不会创建重叠版本。

同一个 Asia/Shanghai 日期内的额度政策不可变。如果在按行数门控的额度池已经创建后再部署这些 unit，
协调器会返回 75，并有意让 oneshot unit 保持失败。每个下载服务都通过 `Requires=` 和 `After=`
依赖协调器，因此 systemd 会阻止下载，不会把延期误判为成功的前置条件。
服务不会立即重启，因为同一额度日内无法安全改变政策。后续机会 timer 可以重试前置条件，持久运行的
00:05 timer 会在下一个额度日再次重试。失败状态可以通过
`systemctl --user status tushare-minute-quota-coordinator.service` 查看，结构化延期原因会追加到
`$HERMES_LOGS_DIR/tushare_minute_quota_coordinator.log`。尾部政策条件也会在不发布额度、不调用供应商的情况下正常跳过，
不要原地替换或修改同一额度日的额度池。

启用前验证渲染后的 unit：

```bash
systemd-analyze verify "$HOME/.config/systemd/user"/tushare-minute-*.service \
  "$HOME/.config/systemd/user"/tushare-minute-*.timer
```

正常停止运维运行器会发送 `SIGINT`，使其能够保存部分 sidecar。完整分区会被跳过，
部分分区只会在下一次运行时继续处理缺失的证券。

## 每日基本面版本归档

`tushare-fundamentals-vintage-archive.timer` 每天 Asia/Shanghai 02:30 运行。它通过 xiaodefa forwarder
采集从 2015 年开始的四个核心基本面组件，构建标准化资产和 PIT v2 资产，验证所有内容 hash，
并写入 `SEALED.json`。它不会发布或修改 latest 别名。首次手动验证归档后，再单独启用该 timer：

```bash
.venv/bin/python scripts/operations/render_tushare_minute_campaign_units.py \
  --template-glob 'tushare-fundamentals-*' \
  --home "$HOME" \
  --mdp-dir "$PWD" \
  --data-platform-root "$HOME/data/quant/market-data-platform" \
  --campaign-manifest /dev/null \
  --logs-dir "$HOME/.hermes/logs" \
  --output-dir "$HOME/.config/systemd/user"

systemctl --user daemon-reload
systemctl --user enable --now tushare-fundamentals-vintage-archive.timer
```

服务从仓库 `.env.local` 读取 `TUSHARE_TOKEN_2`，不会把 token 值或指纹写入归档。
每日频率会形成每日修订观测阶梯，但无法证明两次采集之间发生又消失的日内修订。
首次观测版本之前的期间仍标记为 `reconstructed_pit`。每日完整快照比原来的每周任务消耗更多供应商请求和存储空间。
该 unit 继续使用现有的不可变、失败即关闭实现，确保提高观测频率不会削弱修订安全契约。
