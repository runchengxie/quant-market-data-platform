# QuantZone factor acquisition

QuantZone is an optional supplier for bounded research factor artifacts. The data owner downloads and validates them; research consumers use frozen Parquet runs through the existing local artifact interface. These runs do not update production assets or current aliases.

## Configuration and runtime

Copy `config/config.example.json` to one private JSON file (mode `0600`) and select it with `DATA_PLATFORM_CONFIG`. Set `QUANTZONE_ACCESS_KEY`, `QUANTZONE_SIGN_SECRET`, and the account-confirmed `QUANTZONE_BASE_URL` in its `environment` object. Never publish credentials. Null credentials leave acquisition unconfigured.

```bash
uv sync --locked --extra quantzone --python 3.13
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG" --dry-run
marketdata quantzone check --config "$DATA_PLATFORM_CONFIG"
marketdata quantzone download-factors --config "$DATA_PLATFORM_CONFIG"
```

The extra pins QuantZone `0.10.0`. Its supplied wheels support Python 3.11–3.13 on glibc Linux x86_64/aarch64, Apple Silicon and Windows AMD64. Use a supported runtime; Python 3.14, musl and Intel macOS are unsupported by this integration. Configuration and dry-run commands do not require the SDK.

## Requests and catalog checks

Queries require fixed inclusive ISO dates, explicit stock identifiers with `.XSHE`/`.XSHG` suffixes, and explicit factor names. The example uses `trend_dominance_factor`; authenticate and confirm its catalog coverage before acquisition. Catalog names do not establish a factor formula, historical availability or revision guarantees.

The owner batches by seven calendar days by default, at most 100 stocks and 20 factors per request. Calendar windows may be configured up to 365 days. Timeout is a positive integer at most 60 seconds. Only one request attempt is allowed; automatic retries may charge quota twice. Dry-run is offline. The authenticated check verifies positive available quota, factor coverage and unambiguous stock mappings without requesting factor observations.

## Evidence and publication

Every artifact retains `pit_availability=unknown`, `revision_safety=unknown`, and `public_redistribution=not_authorized`. The stock catalog is a current identifier map and does not establish a historical point-in-time universe. No public observatory projection or redistribution is authorized by this pilot. Supplier data rights require a separate licensing decision.

Official references: [QuantZone](https://quantzone.tech/) and [SDK distribution](https://pypi.org/project/quantzone/0.10.0/).
