# QuantZone factor acquisition

[中文页面](quantzone.md)

QuantZone is an optional supplier for bounded research factor artifacts. The data owner downloads and validates them; research consumers use frozen Parquet runs through the existing local artifact interface. These runs do not update production assets or current aliases.

## Configuration and runtime

Use the version 2 project configuration and existing shared API-key registry described in [credentials](credentials.en.md). Map `QUANTZONE_ACCESS_KEY` and `QUANTZONE_SIGN_SECRET` to registry entries. Set the account-confirmed `QUANTZONE_BASE_URL` in `environment`; SDK version and timeout belong in `providers.quantzone`. Query, batching, retry, output and evidence belong in the separate `jobs.quantzone` JSON. Null registry entries leave acquisition unconfigured.

```bash
uv sync --locked --extra quantzone --python 3.13
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG" --dry-run
marketdata quantzone check --config "$DATA_PLATFORM_CONFIG"
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG"
```

The extra pins QuantZone `0.10.0`. Its supplied wheels support Python 3.11–3.13 on glibc Linux x86_64/aarch64, Apple Silicon and Windows AMD64. Use a supported runtime; Python 3.14, musl and Intel macOS are unsupported by this integration. Configuration and dry-run commands do not require the SDK.

Use `--job /private/path/jobs/quantzone-pilot.json` on either QuantZone command to override the default job. A job cannot override connection settings. Ordinary configuration checks and TuShare startup do not open jobs.

## Requests and catalog checks

Queries require fixed inclusive ISO dates, explicit stock identifiers with `.XSHE`/`.XSHG` suffixes, and explicit factor names. The example uses `trend_dominance_factor`; authenticate and confirm its catalog coverage before acquisition. Catalog names do not establish a factor formula, historical availability or revision guarantees.

The owner batches by seven calendar days by default, at most 100 stocks and 20 factors per request. Calendar windows may be configured up to 365 days. Timeout is a positive integer at most 60 seconds. Only one request attempt is allowed; automatic retries may charge quota twice. Dry-run is offline. The authenticated check verifies positive available quota, factor coverage and unambiguous stock mappings (including SDK `.SZ`/`.SH` catalog identifiers) without requesting factor observations.

## Evidence and publication

Every artifact retains `pit_availability=unknown`, `revision_safety=unknown`, and `public_redistribution=not_authorized`. The stock catalog is a current identifier map and does not establish a historical point-in-time universe. No public observatory projection or redistribution is authorized by this pilot. Supplier data rights require a separate licensing decision.

Official references: [QuantZone](https://quantzone.tech/) and [SDK distribution](https://pypi.org/project/quantzone/0.10.0/).

## Immutable artifacts and recovery

Download execution currently requires POSIX file locking. Each run contains native long-format Parquet batches (`date`, `ukey`, `factor`, `value`), a `research-panel.parquet` projection (`symbol`, `trade_date`, factor columns), `job.json` with the resolved non-secret execution settings, and `receipt.json`. Receipts use `market-data-platform.quantzone-factor-download.v1`, record UTC request/retrieval times, query identity, SDK version, file SHA-256 hashes, observed rows/nulls/empty responses, catalog mapping and evidence flags. Row counts establish observed coverage only; missing rows and empty batches do not prove complete trading-day coverage.

Projection building reads one stock/calendar window and its factor chunks at a time, rather than loading the whole historical native dataset. Duplicate observations are rejected. Null values remain null; no averaging or filling is performed.

```bash
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG" --resume /data/market-data-platform/research/quantzone-pilot/RUN
```

Resume first verifies the archived job snapshot and its receipt hash, query identity, every completed native hash and the completed projection hash before contacting the supplier. A completed run is verified offline and never rewritten. Partial runs request only missing batches, with one attempt each; an explicit resume may repeat a request whose response was interrupted, so review quota before resuming. Unreceipted files after a write interruption are preserved and block acquisition for manual recovery inspection. Atomic temporary files are removed on ordinary failures; a process kill can leave temporary files for inspection. Existing aliases and published contracts remain unchanged.

Research consumers select `data.provider=local_artifact`, `source_mode=fixed_scored_artifact`, and `panel_file` pointing to the frozen projection. Preserve its receipt alongside the file. Public redistribution remains unauthorized.

Use `--no-proxy` on either QuantZone command to connect directly when an inherited HTTP or SOCKS proxy is unsuitable. The option suppresses proxy environment variables only while constructing the SDK client and restores their original values even if construction fails. Other commands keep their inherited proxy policy.
