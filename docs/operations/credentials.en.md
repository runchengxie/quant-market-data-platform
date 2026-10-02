# Credentials and environment variables

[中文页面](credentials.md)

## One private JSON configuration

Copy the public [example](https://github.com/runchengxie/quant-market-data-platform/blob/main/config/config.example.json) to a private location outside Git. Set file permissions to `0600` and credential-only directory permissions to `0700`. Fill its `environment` entries and select it with `DATA_PLATFORM_CONFIG`. The portable default, used only when that file exists and no explicit path is set, is `${XDG_CONFIG_HOME:-$HOME/.config}/quant-market-data-platform/config.json`.

```bash
export DATA_PLATFORM_CONFIG=/private/path/config.json
marketdata config check --config "$DATA_PLATFORM_CONFIG"
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- python /path/to/job.py
```

`config check` reports configured booleans without values or network requests. `config run` directly replaces the process with the provided argv, preserving child exit status and signals. It supplies credentials only in the child process environment, without writing another credential file.

Existing process variables take precedence, including explicitly empty values. Null JSON values are unconfigured. Secrets remain opaque; only `DATA_PLATFORM_ROOT` expands `${HOME}` or a leading `~`. Invalid, missing, symlinked, incorrectly owned or incorrectly permissioned selected JSON fails closed. Legacy `.env.local`, `.env`, and portable `config.env` are compatibility sources only when no JSON is selected. Keep exactly one active credential source; inactive rollback backups remain restricted.

## Variables

| Variable | Purpose |
| --- | --- |
| `DATA_PLATFORM_CONFIG` | Explicit private JSON configuration path. |
| `DATA_PLATFORM_ROOT` | Shared market-data artifact root, outside source repositories. |
| `DATA_PLATFORM_METADATA_DB_PATH` | Optional catalog SQLite path. |
| `DATA_PLATFORM_WAREHOUSE_DB_PATH` | Optional DuckDB warehouse path. |
| `TUSHARE_TOKEN` / `TUSHARE_TOKEN_2` | TuShare provider credentials. |
| `TUSHARE_API_URL` / `TUSHARE_API_URL_2` | SDK API endpoint override; the `_2` address pairs with `TUSHARE_TOKEN_2`. |
| `QUANTZONE_ACCESS_KEY` / `QUANTZONE_SIGN_SECRET` | QuantZone provider credentials. |
| `QUANTZONE_BASE_URL` | Account-confirmed HTTPS service address. |

Set the data root to a stable external location such as `${HOME}/data/quant/quant-market-data-platform`. CI and deployments may inject process variables through their secret manager. Do not store populated examples in Git.

TuShare API overrides select the supplier endpoint, independently of local HTTP proxies. For a token requiring a proxy endpoint, use JSON environment entries such as:

```json
{
  "TUSHARE_TOKEN_2": null,
  "TUSHARE_API_URL_2": "https://proxy-a.example.com"
}
```

This snippet belongs inside `environment`; populate the null credential privately. A single command may select `--token-env TUSHARE_TOKEN_2 --api-url https://proxy-b.example.com`. Machines relying on mihomo, Clash or another local proxy require `--use-proxy` for supported TuShare commands.

## Inspect paths

```bash
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- marketdata paths --market a_share --provider tushare --json
```

See [QuantZone acquisition](quantzone.en.md) for optional runtime installation, bounded queries, artifacts and licensing evidence.
