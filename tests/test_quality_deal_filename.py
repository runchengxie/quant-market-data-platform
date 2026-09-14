from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.quality import profile_parquet


def test_deal_filename_requires_deal_id(tmp_path: Path) -> None:
    path = tmp_path / "deal_2024-01-02.parquet"
    pq.write_table(
        pa.table(
            {
                "SecuCode": ["000001"],
                "TradingDay": [20240102],
                "DealTime": [1],
                "DealID": [7],
                "Price": [100],
                "Volume": [10],
            }
        ),
        path,
    )

    report = profile_parquet(path)

    assert report["missing_columns"] == []
    assert report["id_column"] == "DealID"
