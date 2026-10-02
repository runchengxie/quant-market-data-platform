# 凭证和环境变量

[English page](credentials.en.md)

## 凭证、连接设置与下载任务

新版 [项目配置示例](https://github.com/runchengxie/quant-market-data-platform/blob/main/config/config.example.json) 使用 `schema_version=2`，将三类内容分开：

- 共享私有 API key JSON 保存凭证值。沿用已有注册表，按 [凭证示例](https://github.com/runchengxie/quant-market-data-platform/blob/main/config/api_keys.example.json) 补充缺失的供应商字段，保留其他服务的条目。
- 项目配置保存非敏感的 `environment`、`providers`、`credentials.path` 和环境变量到注册表字段的映射，以及默认 `jobs` 路径。这里不保存凭证值和下载查询。
- 独立的 [QuantZone 任务示例](https://github.com/runchengxie/quant-market-data-platform/blob/main/config/jobs/quantzone-pilot.example.json) 保存查询、分批、重试、输出和证据设置。复制到配置引用的 `jobs/quantzone-pilot.json`。

可编辑的任务定义放在私有配置目录。每次执行的参数快照、回执、Parquet 和日志放在外部数据根目录。它们作为运行归档保留，活跃配置单独管理。凭证注册表、项目配置和任务文件权限均为 `0600`，仅存放凭证的目录权限为 `0700`。

```bash
export DATA_PLATFORM_CONFIG=/private/path/config.json
marketdata config check --config "$DATA_PLATFORM_CONFIG"
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- python /path/to/job.py
```

`config check` 只输出是否已配置，不输出值、不联网。`config run` 按 argv 替换进程并保留退出码和信号，只读取被引用的凭证条目并注入子进程环境。不会打开任务文件或写入另一份凭证文件。

已有进程变量优先，包括显式空值。注册表中的 `null` 表示未配置，缺失的引用字段会失败。凭证保持原样。数据根路径和文件引用可展开 `${HOME}` 或开头的 `~`，相对引用以项目配置目录为基准。同一凭证不能同时放在 `environment` 和 `credentials.keys`。被选中的文件缺失、格式错误、键重复、为符号链接、所有者或权限不符合要求时，直接失败。

版本 1 的内嵌 `environment`/`downloads` 保留迁移兼容。仅在未选择 JSON 时才使用旧 env 兼容入口。每项凭证只保留一份活跃来源，回滚备份保持受限且不参与加载。未显式选择文件时，只有已存在的 `${XDG_CONFIG_HOME:-$HOME/.config}/quant-market-data-platform/config.json` 会被自动选中。

## 主要变量

| 变量 | 用途 |
| --- | --- |
| `DATA_PLATFORM_CONFIG` | 私有 JSON 的显式路径 |
| `DATA_PLATFORM_ROOT` | 仓库外的共享数据根目录 |
| `DATA_PLATFORM_METADATA_DB_PATH` | 可选 catalog SQLite 路径 |
| `DATA_PLATFORM_WAREHOUSE_DB_PATH` | 可选 DuckDB warehouse 路径 |
| `TUSHARE_TOKEN` / `TUSHARE_TOKEN_2` | TuShare 凭证 |
| `TUSHARE_API_URL` / `TUSHARE_API_URL_2` | SDK API 地址覆盖。`_2` 地址匹配 `TUSHARE_TOKEN_2` |
| `QUANTZONE_ACCESS_KEY` / `QUANTZONE_SIGN_SECRET` | QuantZone 凭证 |
| `QUANTZONE_BASE_URL` | 账户确认的 HTTPS 服务地址 |

数据根目录使用稳定的外部位置，例如 `${HOME}/data/quant/quant-market-data-platform`。CI 和部署也可通过 secret 管理器注入进程变量。不要提交已填写凭证的示例。

TuShare API 地址覆盖与本机 HTTP 代理独立。需要代理域名的 token 通过 `credentials.keys` 引用注册表条目，将配套的非敏感 `TUSHARE_API_URL_2` 放在 `environment`。单次命令也可以传 `--token-env TUSHARE_TOKEN_2 --api-url https://proxy-b.example.com`。依赖 mihomo、Clash 等本机代理的环境还需为支持的 TuShare 命令添加 `--use-proxy`。

## 路径检查

```bash
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- marketdata paths --market a_share --provider tushare --json
```

QuantZone 的可选环境、分批查询、产物和许可证据见 [下载说明](quantzone.md)。
