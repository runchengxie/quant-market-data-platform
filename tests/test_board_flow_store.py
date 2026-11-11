from __future__ import annotations

from datetime import datetime
from pathlib import Path

from quant_market_data_platform import board_flow_store
from quant_market_data_platform.providers.eastmoney_board_flow import BoardRow, MinuteFlowRow


def _board(code: str, name: str, net: float) -> BoardRow:
    return BoardRow(
        board_code=code,
        board_name=name,
        board_type="concept",
        main_net_yuan=net,
        main_net_ratio_pct=1.0,
        change_pct=0.5,
        amount_yuan=1e10,
    )


def _minute(code: str, minute: int, net: float) -> MinuteFlowRow:
    return MinuteFlowRow(
        board_code=code,
        board_type="concept",
        trade_time=datetime(2026, 11, 11, 9, minute),
        main_net_yuan=net,
        small_net_yuan=-net,
        mid_net_yuan=0.0,
        large_net_yuan=net / 2,
        xlarge_net_yuan=net / 2,
    )


def test_store_roundtrip_and_idempotent_writes(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    rows = [_board("BK0001", "甲", 1e8), _board("BK0002", "乙", -2e8)]

    assert board_flow_store.upsert_catalog(connection, rows, seen_at="2026-11-11T09:31:00") == 2
    assert (
        board_flow_store.append_snapshot(
            connection, rows, trade_date="2026-11-11", captured_at="2026-11-11T09:31:00"
        )
        == 2
    )
    assert (
        board_flow_store.append_snapshot(
            connection, rows, trade_date="2026-11-11", captured_at="2026-11-11T09:31:00"
        )
        == 2
    )
    connection.commit()

    assert board_flow_store.snapshot_times(connection, trade_date="2026-11-11") == [
        "2026-11-11T09:31:00"
    ]
    assert board_flow_store.latest_capture(connection, trade_date="2026-11-11") == (
        "2026-11-11T09:31:00"
    )
    catalog = {
        row["board_code"]: row["board_name"] for row in board_flow_store.catalog_rows(connection)
    }
    assert catalog == {"BK0001": "甲", "BK0002": "乙"}


def test_minutes_are_keyed_by_day_board_and_minute(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    rows = [_minute("BK0001", 31, 1e8), _minute("BK0001", 32, 1.5e8)]

    assert board_flow_store.upsert_minutes(connection, rows, archived_at="2026-11-11T15:10:00") == 2
    assert board_flow_store.upsert_minutes(connection, rows, archived_at="2026-11-11T15:20:00") == 2
    connection.commit()

    assert board_flow_store.archived_boards(
        connection, trade_date="2026-11-11", board_type="concept"
    ) == {"BK0001"}
    series = board_flow_store.minute_series(
        connection, trade_date="2026-11-11", board_codes=["BK0001"]
    )
    assert [row["trade_time"] for row in series] == [
        "2026-11-11 09:31:00",
        "2026-11-11 09:32:00",
    ]
    summary = board_flow_store.summary(connection)
    assert summary["tables"]["flow_minute"] == 2
    assert summary["minute_days"][0]["boards"] == 1


def test_summary_reports_empty_store(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")

    summary = board_flow_store.summary(connection)

    assert summary["tables"] == {
        "board_catalog": 0,
        "board_catalog_daily": 0,
        "flow_snapshot": 0,
        "flow_minute": 0,
        "collection_run": 0,
    }
    assert summary["snapshot_days"] == []


def test_record_run_is_readable(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    record = board_flow_store.RunRecord(
        run_id="run-1",
        kind="snapshot",
        trade_date="2026-11-11",
        started_at="2026-11-11T09:30:00",
        finished_at="2026-11-11T09:30:05",
        status="ok",
        board_count=3,
        row_count=3,
        detail="{}",
    )

    board_flow_store.record_run(connection, record)

    stored = connection.execute("SELECT * FROM collection_run").fetchone()
    assert stored["run_id"] == "run-1"
    assert stored["status"] == "ok"


def test_catalog_daily_snapshot_is_per_trading_day(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    rows = [_board("BK0001", "甲", 1e8), _board("BK0002", "乙", -2e8)]

    assert (
        board_flow_store.snapshot_catalog(
            connection, rows, trade_date="2026-11-10", captured_at="2026-11-10T09:25:00"
        )
        == 2
    )
    assert (
        board_flow_store.snapshot_catalog(
            connection, rows[:1], trade_date="2026-11-11", captured_at="2026-11-11T09:25:00"
        )
        == 1
    )
    connection.commit()

    rows_10 = connection.execute(
        "SELECT board_code FROM board_catalog_daily WHERE trade_date = '2026-11-10' ORDER BY 1"
    ).fetchall()
    rows_11 = connection.execute(
        "SELECT board_code FROM board_catalog_daily WHERE trade_date = '2026-11-11' ORDER BY 1"
    ).fetchall()
    assert [row["board_code"] for row in rows_10] == ["BK0001", "BK0002"]
    assert [row["board_code"] for row in rows_11] == ["BK0001"]
