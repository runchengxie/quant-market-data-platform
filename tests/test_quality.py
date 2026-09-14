from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.quality import profile_l2_integrity, profile_parquet


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_profile_reports_structural_quality_fields(tmp_path: Path) -> None:
    path = tmp_path / "order_preopen" / "202401" / "order_2024-01-02.parquet"
    _write(
        path,
        [
            {
                "ticker": "000001",
                "TradingDay": 20240102,
                "time_ms": -100,
                "OrderID": 7,
                "Price": 1000,
                "Volume": 200,
                "OrderType": 2,
            },
            {
                "ticker": "000001",
                "TradingDay": 20240102,
                "time_ms": -120,
                "OrderID": 7,
                "Price": 0,
                "Volume": -1,
                "OrderType": -1,
            },
        ],
    )

    report = profile_parquet(path, batch_size=1)

    assert report["rows"] == 2
    assert report["missing_columns"] == []
    assert report["nulls"]["ticker"] == 0
    assert report["trading_days"] == [20240102]
    assert report["duplicate_id_rows"] == 1
    assert report["nonpositive"] == {"Price": 1, "Volume": 1}
    assert report["timestamp_backwards"] == 1


def test_profile_marks_bounded_id_tracking(tmp_path: Path) -> None:
    path = tmp_path / "trades.parquet"
    _write(
        path,
        [
            {"ticker": "000001", "TradingDay": 20240102, "time_ms": 1, "DealID": 1},
            {"ticker": "000001", "TradingDay": 20240102, "time_ms": 2, "DealID": 2},
        ],
    )

    report = profile_parquet(path, max_tracked_ids=1)

    assert report["id_tracking_truncated"] is True
    json.dumps(report, ensure_ascii=False)


def test_profile_reports_missing_required_columns(tmp_path: Path) -> None:
    path = tmp_path / "order_2024-01-02.parquet"
    _write(path, [{"ticker": "000001", "TradingDay": 20240102}])

    report = profile_parquet(path)

    assert report["missing_columns"] == ["time_ms", "Price", "Volume", "OrderID"]


def test_profile_l2_integrity_reports_unknown_trade_references(tmp_path: Path) -> None:
    order_path = tmp_path / "order_2024-01-02.parquet"
    trades_path = tmp_path / "trades_2024-01-02.parquet"
    _write(
        order_path,
        [{"OrderID": 7}, {"OrderID": 8}],
    )
    _write(
        trades_path,
        [
            {"BuyID": 7, "SellID": 8, "Volume": 10},
            {"BuyID": 99, "SellID": 8, "Volume": 5},
        ],
    )

    report = profile_l2_integrity(order_path, trades_path)

    assert report["order_rows"] == 2
    assert report["trade_rows"] == 2
    assert report["unknown_buy_id_rows"] == 1
    assert report["unknown_sell_id_rows"] == 0
    assert report["order_id_tracking_truncated"] is False
