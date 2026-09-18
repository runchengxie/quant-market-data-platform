# 公共源 ETF 分钟数据

本页说明使用 AKShare、东方财富直连和新浪历史分钟接口获取 ETF 分钟数据的方式。该链路用于持续归档和近期研究，不等同于完整的 A 股分钟生产资产。

## 安装

公共源 provider 是可选依赖，不会进入平台核心导入路径：

```bash
uv sync --locked --extra etf-minute-public
```

东方财富和新浪的直连回退还需要系统 `curl`。真实行情源的网络窗口、字段和历史范围都可能变化。

## 下载

```bash
marketdata data mirror-public-etf-minute \
  --symbols 510050.SH \
  --start-date 20260824 \
  --end-date 20260825 \
  --period 1 \
  --source auto \
  --network-mode system \
  --version etf_minute_1m_20260825
```

多只 ETF 可以逗号分隔，也可以重复 `--symbols`：

```bash
marketdata data mirror-public-etf-minute \
  --symbols 510050.SH,512880.SH \
  --symbols 159915.SZ \
  --start-date 20260824 \
  --end-date 20260825
```

支持的周期为 `1`、`5`、`15`、`30`、`60` 分钟，数据源为 `auto`、`eastmoney`、`sina`。
新浪源不支持可用的 1 分钟历史数据。

## 网络模式

`--network-mode system` 是默认模式。它沿用主机的网络配置。

`--network-mode direct` 会跳过 AKShare，使用 curl 直连适配器，清理标准代理环境变量，并为 curl 设置 `--noproxy '*'`。请求前会解析公共行情域名。检测到 `198.18.0.0/15` fake-IP 时，任务会停止并提示关闭 mihomo fake-IP/TUN 或为目标域名配置 DIRECT 路由。

该选项不会修改系统路由，也不会停止 mihomo。系统存在透明 TUN 时，应用层无法保证绕过它。

默认输出位于：

```text
$DATA_PLATFORM_ROOT/assets/derived/a_share/etf_minute_<period>m/<version>/
```

也可以使用 `--out-dir` 指定一个明确的版本目录。`--dry-run` 只解析参数和待处理日期，不访问网络。`--no-skip` 会重新写入已有交易日分区。

## 产物契约

每个交易日写入一个 Hive 风格分区：

```text
trade_date=YYYYMMDD/part-00000.parquet
```

Parquet 列固定为：

```text
ts_code, trade_time, open, close, high, low, vol, amount
```

`trade_time` 是不带时区的中国大陆市场墙钟时间。新浪没有成交额字段，因此新浪分区的 `amount` 为缺失值，不得用 `vol` 代替。

每个版本目录还包含：

```text
manifest.yml
receipt.json
```

`manifest.yml` 记录请求参数、数据集身份、选用来源、日期统计、文件统计和错误。`receipt.json` 记录 manifest 哈希以及每个 Parquet 分区的路径、行数、证券列表和 SHA-256。下游应读取 manifest/receipt 确认版本和来源，不应硬编码版本目录名。

## 覆盖限制

- 公共东方财富 ETF 1 分钟接口通常只提供最近约 5 个交易日。没有持续归档时，过期数据无法依靠当前接口补回。
- 东方财富较高周期和新浪历史接口的实际历史长度由上游返回窗口决定，服务商没有长期覆盖承诺。
- `source=auto` 在请求失败时才使用回退源。不同来源的字段完整性和历史覆盖不同，receipt 中会保留实际选用来源。
- 该数据集是 ETF 专用资产，不更新 `assets/derived/a_share/minute_1m`，也不自动写入 `metadata/current_assets/a_share_current.json`。

## 只读检查

```bash
marketdata data mirror-public-etf-minute \
  --symbols 510050.SH \
  --start-date 20260825 \
  --end-date 20260825 \
  --period 1 \
  --dry-run
```

运行前确认 `DATA_PLATFORM_ROOT` 指向共享数据根目录，并保存本次版本目录中的 `manifest.yml` 和 `receipt.json`。公共行情源不应被视为可复现研究的唯一历史来源。
