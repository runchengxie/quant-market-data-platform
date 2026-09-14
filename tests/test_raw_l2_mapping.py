from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.quality import profile_parquet


def test_profile_accepts_raw_l2_aliases_and_reports_resolution(tmp_path: Path) -> None:
    path = tmp_path / "order_2026-04-24.parquet"
    pq.write_table(
        pa.table(
            {
                "SecuCode": [2202, 2202],
                "OrderTime": [91500000, 91500100],
                "TradingDay": [20260424, 20260424],
                "OrderID": [1, 2],
                "ChannelNo": [3, 3],
                "ApplSeqNum": [10, 12],
                "Price": [2668, 2778],
                "Volume": [100.0, 500.0],
            }
        ),
        path,
    )

    report = profile_parquet(path)

    assert report["missing_columns"] == []
    assert report["resolved_columns"] == {"ticker": "SecuCode", "time_ms": "OrderTime"}
    assert report["distinct_tickers_observed"] == 1
    assert report["timestamp_backwards"] == 0
    assert report["expected_trading_day"] == 20260424
    assert report["ordering"]["ordering_mode"] == "channel_sequence"
    assert report["ordering"]["sequence_quality"]["gap_events"] == 1


def test_profile_accepts_raw_snapshot_ticktime_without_event_id(tmp_path: Path) -> None:
    path = tmp_path / "snapshot_2026-04-24.parquet"
    pq.write_table(
        pa.table(
            {
                "SecuCode": [2202],
                "TickTime": [91500000],
                "TradingDay": [20260424],
                "Price": [2668],
                "Volume": [100.0],
            }
        ),
        path,
    )

    report = profile_parquet(path)

    assert report["missing_columns"] == []
    assert report["resolved_columns"] == {"ticker": "SecuCode", "time_ms": "TickTime"}
    assert report["id_column"] is None
    assert report["expected_trading_day"] == 20260424
