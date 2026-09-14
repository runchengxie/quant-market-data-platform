from __future__ import annotations

import pandas as pd

from market_data_platform.standardize.parquet import (
    csv_columns,
    hive_partition_columns,
    read_parquet_dataset_compat,
    read_parquet_file_with_partitions,
    select_available_columns,
)


def test_research_parquet_io_preserves_hive_partition_columns(tmp_path) -> None:
    dataset = tmp_path / "features" / "data" / "trade_date=20260418"
    dataset.mkdir(parents=True)
    pd.DataFrame([{"symbol": "600519.SH", "value": 1.0}]).to_parquet(
        dataset / "part.parquet", index=False
    )

    assert hive_partition_columns(dataset.parent) == ["trade_date"]
    frame = read_parquet_file_with_partitions(dataset / "part.parquet")
    assert frame["trade_date"].tolist() == ["20260418"]


def test_research_parquet_io_projects_columns_and_reads_dataset(tmp_path) -> None:
    dataset = tmp_path / "data"
    dataset.mkdir()
    pd.DataFrame([{"symbol": "600519.SH", "value": 1.0, "unused": "drop"}]).to_parquet(
        dataset / "part.parquet", index=False
    )

    frame = read_parquet_dataset_compat(dataset, columns=["symbol", "value"])
    assert frame.columns.tolist() == ["symbol", "value"]
    assert select_available_columns(["value", "missing", "value"], frame.columns) == ["value"]


def test_research_csv_columns_reads_only_header(tmp_path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("symbol,value\n600519.SH,1\n", encoding="utf-8")

    assert csv_columns(path) == ["symbol", "value"]
