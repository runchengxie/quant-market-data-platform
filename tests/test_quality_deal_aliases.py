from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.quality import profile_parquet


def test_trade_files_accept_deal_time_alias(tmp_path: Path) -> None:
    path = tmp_path / "trades_2024-01-02.parquet"
    pq.write_table(
        pa.table(
            {
                "SecuCode": ["000001", "000001"],
                "TradingDay": [20240102, 20240102],
                "DealTime": [1, 2],
                "DealID": [7, 8],
                "Price": [100, 101],
                "Volume": [10, 20],
            }
        ),
        path,
    )

    report = profile_parquet(path)

    assert report["missing_columns"] == []
    assert report["resolved_columns"] == {"ticker": "SecuCode", "time_ms": "DealTime"}
    assert report["id_column"] == "DealID"
