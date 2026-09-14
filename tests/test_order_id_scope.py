from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.quality import profile_parquet


def test_duplicate_order_ids_are_scoped_by_security(tmp_path: Path) -> None:
    path = tmp_path / "order_2026-04-24.parquet"
    pq.write_table(
        pa.table(
            {
                "ticker": ["000001", "000002", "000001"],
                "TradingDay": [20260424] * 3,
                "time_ms": [1, 2, 3],
                "OrderID": [7, 7, 7],
                "Price": [100, 200, 101],
                "Volume": [100, 100, 50],
            }
        ),
        path,
    )

    report = profile_parquet(path)

    assert report["id_scope_columns"] == ["ticker", "OrderID"]
    assert report["distinct_ids_observed"] == 2
    assert report["duplicate_id_rows"] == 1
