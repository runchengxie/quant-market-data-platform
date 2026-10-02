# QuantZone 因子下载

[English page](quantzone.en.md)

QuantZone 是研究因子数据的可选供应商。market-data-platform 负责下载、校验和冻结 Parquet 产物，下游研究通过已有本地产物接口读取。试验产物独立保存，不切换生产资产和 current 别名。

## 配置和环境

将 `config/config.example.json` 复制到仓库外的单一私有 JSON，文件权限设为 `0600`，并用 `DATA_PLATFORM_CONFIG` 指定。在 `environment` 中填写 `QUANTZONE_ACCESS_KEY`、`QUANTZONE_SIGN_SECRET` 和账户确认的 `QUANTZONE_BASE_URL`。`null` 凭证表示尚未配置。

```bash
uv sync --locked --extra quantzone --python 3.13
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG" --dry-run
marketdata quantzone check --config "$DATA_PLATFORM_CONFIG"
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG"
```

可选依赖固定 SDK `0.10.0`。支持的运行环境为 Python 3.11–3.13，平台包括 glibc Linux x86_64/aarch64、Apple Silicon 和 Windows AMD64。当前下载执行还要求 POSIX 文件锁。Python 3.14、musl 和 Intel macOS 不在本集成支持范围内。配置检查和 dry-run 不安装 SDK 也能运行。

## 查询与目录核验

查询必须明确指定包含首尾的 ISO 日期、带 `.XSHE`/`.XSHG` 后缀的股票列表，以及因子名称列表。示例使用 `trend_dominance_factor`。下载前先认证并确认目录覆盖日期。目录名称本身不能证明因子公式、历史可用时间或修订保障。

默认按七个日历日分批，每次最多 100 只股票、20 个因子。可配置的日历窗口上限为 365 天，超时为不超过 60 秒的正整数。每次请求只尝试一次，避免自动重试重复消耗额度。dry-run 完全离线。认证检查核验正额度、因子覆盖日期和明确的股票映射，兼容 SDK 目录的 `.SZ`/`.SH` 标识。下载使用已核验的六位代码，不请求因子观测的检查命令不会消耗因子查询。

## 冻结产物与恢复

每个运行目录包含原生长表 Parquet（`date`、`ukey`、`factor`、`value`）、研究宽表 `research-panel.parquet`（`symbol`、`trade_date` 和因子列），以及 `receipt.json`。凭证 schema 为 `market-data-platform.quantzone-factor-download.v1`，记录 UTC 请求与接收时间、查询标识、SDK 版本、文件 SHA-256、观测行数、空值数、空响应、目录映射和证据状态。

行数只反映实际观测，缺行或空响应不能证明完整交易日覆盖。宽表构建逐个读取股票和日期窗口的因子分块，不一次加载全部历史原生数据。重复观测会被拒绝，空值原样保留，不平均或填充。

```bash
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG" --resume /data/market-data-platform/research/quantzone-pilot/RUN
```

恢复在访问供应商前核对查询标识、已完成原生文件哈希和完整宽表哈希。完整运行可以离线验证且不会改写。未完成运行只请求缺失批次，每批仍只尝试一次。显式恢复可能再次请求接收过程中中断的批次，恢复前需检查额度。写盘中断留下的未登记文件会保留并阻止继续查询，需要人工检查后恢复。普通失败会清理临时文件，进程被强制结束可能留下临时文件。生产别名和已发布契约不变。

研究消费者配置 `data.provider=local_artifact`、`source_mode=fixed_scored_artifact`，将 `panel_file` 指向冻结宽表，同时保留其 receipt。

## 证据与公开发布

产物保留 `pit_availability=unknown`、`revision_safety=unknown` 和 `public_redistribution=not_authorized`。股票目录是当前标识映射，不能证明历史 PIT 股票池。本试验未授权公开 observatory 投影或数据再分发。供应商数据使用权需要独立确认。

官方参考：[QuantZone](https://quantzone.tech/) 与 [SDK 分发](https://pypi.org/project/quantzone/0.10.0/)。
