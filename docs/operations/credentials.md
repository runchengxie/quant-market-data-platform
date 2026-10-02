# 凭证和环境变量

[English page](credentials.en.md)

## 单一私有 JSON 配置

将公开的 [示例](https://github.com/runchengxie/quant-market-data-platform/blob/main/config/config.example.json) 复制到 Git 仓库外的私有位置。文件权限设为 `0600`，仅存放凭证的目录设为 `0700`。填写 `environment` 中的配置，并通过 `DATA_PLATFORM_CONFIG` 指定文件。未指定路径时，只有已存在的 `${XDG_CONFIG_HOME:-$HOME/.config}/quant-market-data-platform/config.json` 会被自动选中。

```bash
export DATA_PLATFORM_CONFIG=/private/path/config.json
marketdata config check --config "$DATA_PLATFORM_CONFIG"
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- python /path/to/job.py
```

`config check` 只报告配置是否已填写，不输出值，也不请求网络。`config run` 按参数列表直接启动并替换进程，保留退出码和信号。凭证只注入子进程环境，不另写凭证文件。

已有进程变量优先，包括显式空字符串。JSON 的 `null` 表示未配置。密钥原样保留。只有 `DATA_PLATFORM_ROOT` 展开 `${HOME}` 和开头的 `~`。被选中的 JSON 缺失、无效、为符号链接、所有者错误或权限错误时立即失败。只有未选择 JSON 时才兼容读取 `.env.local`、`.env` 和旧版默认 `config.env`。保持单一活跃凭证源，回滚备份必须停用并限制权限。

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

TuShare API 地址覆盖与本机 HTTP 代理独立。需要代理域名的 token 可在 JSON 的 `environment` 中设置：

```json
{
  "TUSHARE_TOKEN_2": null,
  "TUSHARE_API_URL_2": "https://proxy-a.example.com"
}
```

在私有文件中填写 `null` 凭证。单次命令也可以传 `--token-env TUSHARE_TOKEN_2 --api-url https://proxy-b.example.com`。依赖 mihomo、Clash 等本机代理的环境还需为支持的 TuShare 命令添加 `--use-proxy`。

## 路径检查

```bash
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- marketdata paths --market a_share --provider tushare --json
```

QuantZone 的可选环境、分批查询、产物和许可证据见 [下载说明](quantzone.md)。
