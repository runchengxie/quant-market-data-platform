# Reading research Parquet files

[中文页面](research-parquet-io.md)

`market_data_platform.standardize.parquet` provides shared file-reading helpers for research data. They inspect Parquet/CSV column names, detect fields and values in Hive-partitioned directories, project requested columns, and fall back to reading Parquet files individually while restoring partition columns when dataset-level reads fail.

These helpers handle file formats and dataset layout only. They do not implement strategy, features, stock selection, or backtests. Higher-level code can reuse them through `read_parquet_dataset_compat` and `select_available_columns`.
