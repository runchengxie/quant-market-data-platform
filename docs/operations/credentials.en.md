# Credentials and environment variables

[中文页面](credentials.md)

## Credentials, connection settings and jobs

Version 2 of the public [configuration example](https://github.com/runchengxie/quant-market-data-platform/blob/main/config/config.example.json) separates three sources:

- A shared private API-key registry holds credential values. Reuse the existing registry; add only missing supplier entries using [the credential example](https://github.com/runchengxie/quant-market-data-platform/blob/main/config/api_keys.example.json). Preserve other services' entries.
- The project configuration holds non-secret `environment`, `providers`, a `credentials.path` and environment-name-to-registry-key references, and default `jobs` paths. It contains no credential values or download query.
- A separate [QuantZone job example](https://github.com/runchengxie/quant-market-data-platform/blob/main/config/jobs/quantzone-pilot.example.json) defines query, batching, retry, output and evidence settings. Copy it to the configured `jobs/quantzone-pilot.json` path.

Keep editable definitions under the private configuration directory. Execution snapshots, receipts, Parquet and logs belong under the external data root. They are retained run artifacts, not active configuration sources. Shared credential registries, project configuration and selected job files require mode `0600`; credential-only directories require `0700`.

```bash
export DATA_PLATFORM_CONFIG=/private/path/config.json
marketdata config check --config "$DATA_PLATFORM_CONFIG"
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- python /path/to/job.py
```

`config check` reports configured booleans without values or network requests. `config run` directly replaces the process with the provided argv, preserving child exit status and signals. It reads only referenced credential entries and supplies them in the child environment; it does not open job files or write another credential file.

Existing process variables take precedence, including explicitly empty values. Null registry entries are unconfigured; missing referenced keys fail closed. Secrets remain opaque. `DATA_PLATFORM_ROOT` and file references expand `${HOME}` or a leading `~`; relative references resolve from the project configuration directory. Credentials cannot appear both in `environment` and `credentials.keys`. Invalid, missing, duplicate-key, symlinked, incorrectly owned or incorrectly permissioned selected files fail closed.

Version 1 inline `environment`/`downloads` configurations remain compatible for migration. Legacy env files are compatibility sources only when no JSON is selected. Keep one active copy of each credential; inactive rollback backups remain restricted. The portable project default is `${XDG_CONFIG_HOME:-$HOME/.config}/quant-market-data-platform/config.json`, selected only when it exists and no explicit path is set.

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

TuShare API overrides select the supplier endpoint, independently of local HTTP proxies. For a token requiring a proxy endpoint, reference `TUSHARE_TOKEN_2` through `credentials.keys` and set the non-secret `TUSHARE_API_URL_2` in `environment`. A single command may select `--token-env TUSHARE_TOKEN_2 --api-url https://proxy-b.example.com`. Machines relying on mihomo, Clash or another local proxy require `--use-proxy` for supported TuShare commands.

## Inspect paths

```bash
marketdata config run --config "$DATA_PLATFORM_CONFIG" -- marketdata paths --market a_share --provider tushare --json
```

See [QuantZone acquisition](quantzone.en.md) for optional runtime installation, bounded queries, artifacts and licensing evidence.
