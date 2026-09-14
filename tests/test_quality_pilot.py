from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.quality_pilot import L2PilotOptions, run_l2_pilot


def test_pilot_writes_canonical_rows_and_sparse_labels(tmp_path: Path) -> None:
    source = tmp_path / "deal_2024-01-02.parquet"
    pq.write_table(
        pa.table(
            {
                "SecuCode": ["000001", "000001", None],
                "TradingDay": [20240102, 20240102, 20240102],
                "DealTime": [1, 2, 3],
                "DealID": [7, 8, 9],
                "Price": [100, 0, 101],
                "Volume": [10, 20, 30],
            }
        ),
        source,
    )

    report = run_l2_pilot(L2PilotOptions([source], tmp_path / "out"))

    assert report["summary"] == {"keep": 1, "tag": 1, "exclude": 1}
    canonical = pq.read_table(tmp_path / "out" / "canonical" / source.name)
    assert canonical.num_rows == 1
    labels = pq.read_table(tmp_path / "out" / "labels" / f"{source.stem}.parquet").to_pydict()
    assert labels["row_number"] == [1, 2]
    assert labels["decision"] == ["tag", "exclude"]
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["files_processed"] == 1


def test_pilot_limits_rows_per_file(tmp_path: Path) -> None:
    source = tmp_path / "order_2024-01-02.parquet"
    pq.write_table(
        pa.table(
            {
                "SecuCode": ["000001", "000001"],
                "TradingDay": [20240102, 20240102],
                "OrderTime": [1, 2],
                "OrderID": [7, 8],
                "Price": [100, 101],
                "Volume": [10, 20],
            }
        ),
        source,
    )

    report = run_l2_pilot(L2PilotOptions([source], tmp_path / "out", max_rows_per_file=1))

    assert report["reports"][0]["rows_read"] == 1
    assert report["summary"] == {"keep": 1, "tag": 0, "exclude": 0}
