# Public-source ETF minute data

[中文页面](etf-minutes.md)

This guide covers ETF minute data from AKShare, direct Eastmoney access, and Sina historical-minute interfaces. It supports ongoing archiving and recent research; it is not equivalent to the complete A-share minute production asset.

## Install

The public-source provider is optional and is not imported by the platform core:

```bash
uv sync --locked --extra etf-minute-public
```

Direct Eastmoney and Sina fallback also require system `curl`. Network availability, fields, and historical ranges of live market-data sources may change.

## Download

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

Pass multiple symbols as a comma-separated list or repeat `--symbols`:

```bash
marketdata data mirror-public-etf-minute \
  --symbols 510050.SH,512880.SH \
  --symbols 159915.SZ \
  --start-date 20260824 \
  --end-date 20260825
```

Supported periods are `1`, `5`, `15`, `30`, and `60` minutes. Sources are `auto`, `eastmoney`, and `sina`. Sina does not provide usable one-minute historical data.

## Network modes

`--network-mode system` is the default and uses the host's network configuration.

`--network-mode direct` skips AKShare and uses the curl direct-connect adapter. It clears standard proxy environment variables, passes `--noproxy '*'` to curl, and resolves public market-data hostnames before requests. If it detects a `198.18.0.0/15` fake IP, the task stops and asks the operator to disable mihomo fake-IP/TUN or configure a DIRECT route for the target domain.

This option does not change system routes or stop mihomo. The application cannot guarantee bypassing a transparent system TUN.

The default output is:

```text
$DATA_PLATFORM_ROOT/assets/derived/a_share/etf_minute_<period>m/<version>/
```

Use `--out-dir` to select an explicit version directory. `--dry-run` parses options and pending dates without network access. `--no-skip` rewrites existing trading-day partitions.

## Output contract

Each trading day is written as a Hive-style partition:

```text
trade_date=YYYYMMDD/part-00000.parquet
```

Parquet columns are fixed:

```text
ts_code, trade_time, open, close, high, low, vol, amount
```

`trade_time` is a timezone-naive mainland China wall-clock time. Sina does not provide trade amount, so its partitions have null `amount`; do not substitute `vol`.

Each version directory also contains `manifest.yml` and `receipt.json`. The manifest records request parameters, dataset identity, selected source, date/file statistics, and errors. The receipt records the manifest hash and each Parquet partition's path, row count, symbols, and SHA-256. Read both files to verify the version and source; do not hard-code version directory names.

## Coverage limits

- The public Eastmoney one-minute ETF endpoint usually exposes only about the latest five trading days. Expired data cannot be recovered from the live endpoint without continuous archiving.
- Actual history from higher-period Eastmoney and Sina endpoints depends on the upstream response window; neither provider guarantees long-term coverage.
- `source=auto` falls back only after a request fails. Fields and historical coverage differ by source; the receipt records which source was used.
- This is an ETF-specific asset. It does not update `assets/derived/a_share/minute_1m` and is not automatically written to `metadata/current_assets/a_share_current.json`.

## Read-only check

```bash
marketdata data mirror-public-etf-minute \
  --symbols 510050.SH \
  --start-date 20260825 \
  --end-date 20260825 \
  --period 1 \
  --dry-run
```

Before running, confirm `DATA_PLATFORM_ROOT` points to the shared data root and retain the version's `manifest.yml` and `receipt.json`. A public market-data source should not be treated as the sole historical source for reproducible research.
