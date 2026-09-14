# 凭证和环境变量

## 共享数据根目录

推荐统一配置共享数据根目录：

```bash
export DATA_PLATFORM_ROOT=/data/market-data-platform
```

本地开发可使用仓库内的默认 `artifacts/`：

```bash
cp .envrc.example .envrc
cp .env.example .env.local
direnv allow
```

真实凭证写入未跟踪的 `.env.local`，或写入：

```text
~/.config/market-data-platform/secrets.env
```

## 主要变量

| 变量 | 用途 |
| --- | --- |
| `DATA_PLATFORM_ROOT` | 推荐的共享市场数据产物根目录 |
| `DATA_PLATFORM_METADATA_DB_PATH` | 可选的 catalog SQLite 文件路径 |
| `DATA_PLATFORM_WAREHOUSE_DB_PATH` | 可选的 DuckDB warehouse 文件路径 |
| `TUSHARE_TOKEN` / `TUSHARE_TOKEN_2` | TuShare 中国大陆市场数据源凭证 token |
| `TUSHARE_API_URL` / `TUSHARE_API_URL_2` | 可选 TuShare SDK API 地址。`TUSHARE_API_URL_2` 会自动匹配 `TUSHARE_TOKEN_2` |

`TUSHARE_API_URL*` 是 SDK 请求地址覆盖，作用范围不涉及本机 HTTP 代理。使用 15000 分 `TUSHARE_TOKEN_2`
这类需要代理域名的 token 时，可在未跟踪的 `.env.local` 写入：

```bash
TUSHARE_TOKEN_2=...
TUSHARE_API_URL_2=https://proxy-a.example.com
```

也可以单次命令传 `--token-env TUSHARE_TOKEN_2 --api-url https://proxy-b.example.com`。
如果当前机器依赖 mihomo、Clash 或其他本机代理环境变量访问 TuShare，校验和下载命令还需要
显式加 `--use-proxy`。

## 路径检查

```bash
marketdata paths --market a_share --provider tushare --json
```
