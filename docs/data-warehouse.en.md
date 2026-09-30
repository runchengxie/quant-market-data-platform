# Catalog, standardized layer, and DuckDB queries

[中文页面](data-warehouse.md)

`marketdata data ...` refreshes the asset catalog, materializes the standardized layer, and runs DuckDB queries. The platform maintains these capabilities.

The default artifact-root resolution order is:

```text
--artifacts-root argument
DATA_PLATFORM_ROOT
artifacts/
```

Override the catalog SQLite path with `DATA_PLATFORM_METADATA_DB_PATH` and the DuckDB warehouse path with `DATA_PLATFORM_WAREHOUSE_DB_PATH`. Without overrides, both are stored under `<artifacts_root>/metadata/`.

## Refresh the catalog

```bash
marketdata data catalog \
  --artifacts-root "$DATA_PLATFORM_ROOT"
```

By default, it writes `<artifacts_root>/metadata/catalog.sqlite` and `<artifacts_root>/metadata/catalog_summary.csv`.

## Materialize the standardized layer

```bash
marketdata data materialize \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --name a_share_daily_panel \
  --market a_share \
  --preset generic \
  --asset-dir "$DATA_PLATFORM_ROOT/assets/tushare/a_share/daily/a_share_all_daily_latest" \
  --frequency D
```

The default output is `<artifacts_root>/standardized/<market>/<dataset>/<name>/`.

## Query standardized data

Install DuckDB support:

```bash
uv sync --extra dev --extra duckdb
```

Then query:

```bash
marketdata data query \
  --artifacts-root "$DATA_PLATFORM_ROOT" \
  --sql "select 1 as value"
```

The query command scans standardized-layer manifests and registers views in DuckDB. Use `--format` and `--out` to write query results.
