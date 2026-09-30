# Credentials and environment variables

[中文页面](credentials.md)

## Shared data root

Configure the shared data root with:

```bash
export DATA_PLATFORM_ROOT=/data/market-data-platform
```

For local development, the repository's `artifacts/` directory can be used:

```bash
export DATA_PLATFORM_ROOT="$PWD/artifacts"
uv sync --extra dev
```

Store TuShare and other provider credentials in the user-private configuration file:

```text
~/.config/richard/projects/quant/quant-market-data-platform/config.env
```

The file uses `KEY=VALUE` entries, for example `TUSHARE_TOKEN=...`. Restrict its permissions so other users cannot read it. CI and deployment environments should inject credentials through their secret-management facility. `.env.local` and `.env` remain supported for compatibility, but new environments do not need to create them.

## Variables

| Variable | Purpose |
| --- | --- |
| `DATA_PLATFORM_ROOT` | Recommended shared market-data artifact root. |
| `DATA_PLATFORM_METADATA_DB_PATH` | Optional catalog SQLite file path. |
| `DATA_PLATFORM_WAREHOUSE_DB_PATH` | Optional DuckDB warehouse file path. |
| `TUSHARE_TOKEN` / `TUSHARE_TOKEN_2` | TuShare credentials for mainland China market data. |
| `TUSHARE_API_URL` / `TUSHARE_API_URL_2` | Optional TuShare SDK API endpoint; `TUSHARE_API_URL_2` pairs automatically with `TUSHARE_TOKEN_2`. |

`TUSHARE_API_URL*` overrides the SDK request endpoint; it does not configure the local HTTP proxy. For a 15,000-point `TUSHARE_TOKEN_2` that requires a proxy domain, configure the private file:

```bash
TUSHARE_TOKEN_2=...
TUSHARE_API_URL_2=https://proxy-a.example.com
```

Alternatively, pass `--token-env TUSHARE_TOKEN_2 --api-url https://proxy-b.example.com` for one command. If the machine requires mihomo, Clash, or another local proxy environment to access TuShare, validation and download commands also need `--use-proxy`.

## Inspect resolved paths

```bash
marketdata paths --market a_share --provider tushare --json
```
