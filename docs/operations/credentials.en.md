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
${XDG_CONFIG_HOME:-$HOME/.config}/quant-market-data-platform/config.json
```

Copy `config/config.example.json` into a private file, set mode `0600`, and fill its `environment` entries. Set `DATA_PLATFORM_CONFIG` to select a different private location. Null entries are unconfigured; inherited variables take precedence. Do not retain a second active credential env file. CI may inject process variables through its secret manager.

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


## JSON process launcher

```bash
marketdata config check --config "$DATA_PLATFORM_CONFIG"
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- python /path/to/job.py
```

Selected JSON fails closed. Legacy `.env.local`, `.env`, and `config.env` are compatibility sources only when no JSON is selected. Keep credentials in one active private JSON; backup files must remain inactive and restricted. Credential values are opaque and never substituted. Only the declared `DATA_PLATFORM_ROOT` supports `${HOME}` and `~`. Diagnostics report configured booleans only. See the [configuration example](https://github.com/runchengxie/quant-market-data-platform/blob/main/config/config.example.json).
