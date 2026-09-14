from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.quality_scan import QualityScanOptions, scan_parquet_tree


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_scan_writes_incremental_checkpoint_and_classifies_findings(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    _write(
        root / "order_2024-01-02.parquet",
        [
            {
                "ticker": "000001",
                "TradingDay": 20240102,
                "time_ms": 2,
                "OrderID": 7,
                "Price": 100,
                "Volume": 10,
            },
            {
                "ticker": "000001",
                "TradingDay": 20240101,
                "time_ms": 1,
                "OrderID": 7,
                "Price": 0,
                "Volume": -1,
            },
        ],
    )
    _write(
        root / "order_2024-01-03.parquet",
        [
            {
                "ticker": "000002",
                "TradingDay": 20240103,
                "time_ms": 1,
                "OrderID": 8,
                "Price": 101,
                "Volume": 10,
            }
        ],
    )
    checkpoint = tmp_path / "checkpoint.json"
    output = tmp_path / "summary.json"

    report = scan_parquet_tree(QualityScanOptions(root, checkpoint, output))

    assert report["status"] == "complete"
    assert report["files_scanned"] == 2
    assert report["summary"]["files_with_findings"] == 1
    assert report["summary"]["finding_files_by_category"] == {
        "date_mismatch": 1,
        "duplicate_id": 1,
        "nonpositive": 1,
        "timestamp_order": 1,
    }
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["files_scanned"] == 2
    checkpoint_payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert len(checkpoint_payload["completed"]) == 2


def test_scan_reuses_completed_checkpoint(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    _write(
        root / "order_2024-01-02.parquet",
        [
            {
                "ticker": "000001",
                "TradingDay": 20240102,
                "time_ms": 1,
                "OrderID": 7,
                "Price": 100,
                "Volume": 10,
            }
        ],
    )
    checkpoint = tmp_path / "checkpoint.json"
    options = QualityScanOptions(root, checkpoint)
    first = scan_parquet_tree(options)
    second = scan_parquet_tree(options)

    assert first["files_scanned"] == 1
    assert second["files_scanned"] == 1
    assert second["files_reused_from_checkpoint"] == 1


def test_scan_discards_checkpoint_from_an_older_schema(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    _write(
        root / "order_2024-01-02.parquet",
        [
            {
                "ticker": "000001",
                "TradingDay": 20240102,
                "time_ms": 1,
                "OrderID": 7,
                "Price": 100,
                "Volume": 10,
            }
        ],
    )
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text('{"version": 1, "completed": {"stale": {}}}\n', encoding="utf-8")

    report = scan_parquet_tree(QualityScanOptions(root, checkpoint))

    assert report["files_reused_from_checkpoint"] == 0
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["version"] == 3
