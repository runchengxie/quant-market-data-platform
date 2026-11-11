from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from quant_market_data_platform import board_flow_monitor as monitor
from quant_market_data_platform import board_flow_store
from quant_market_data_platform.providers.eastmoney_board_flow import (
    BoardFlowClient,
    BoardFlowDataError,
    BoardRow,
    MinuteFlowRow,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _board(code: str, name: str = "板块") -> BoardRow:
    return BoardRow(
        board_code=code,
        board_name=name,
        board_type="concept",
        main_net_yuan=1e8,
        main_net_ratio_pct=1.0,
        change_pct=0.5,
        amount_yuan=1e10,
    )


def _curve(code: str, day: datetime) -> list[MinuteFlowRow]:
    rows = []
    for minute, net in ((31, 1e8), (32, 2e8)):
        rows.append(
            MinuteFlowRow(
                board_code=code,
                board_type="concept",
                trade_time=day.replace(hour=9, minute=minute, second=0, microsecond=0),
                main_net_yuan=net,
                small_net_yuan=-net,
                mid_net_yuan=0.0,
                large_net_yuan=net / 2,
                xlarge_net_yuan=net / 2,
            )
        )
    return rows


def _close_row(code: str, day: datetime) -> MinuteFlowRow:
    return MinuteFlowRow(
        board_code=code,
        board_type="concept",
        trade_time=day.replace(hour=15, minute=0, second=0, microsecond=0),
        main_net_yuan=9e8,
        small_net_yuan=-9e8,
        mid_net_yuan=0.0,
        large_net_yuan=4.5e8,
        xlarge_net_yuan=4.5e8,
    )


@dataclass
class FakeClient:
    boards: list[BoardRow]
    curves: dict[str, list[MinuteFlowRow]]
    failing: tuple[str, ...] = ()

    def fetch_boards(self, board_type: str) -> list[BoardRow]:
        return [row for row in self.boards if row.board_type == board_type]

    def fetch_minute_curve(self, board_code: str, *, board_type: str) -> list[MinuteFlowRow]:
        if board_code in self.failing:
            raise BoardFlowDataError(f"no data for {board_code}")
        return self.curves[board_code]


def _client(**kwargs: Any) -> BoardFlowClient:
    return cast(BoardFlowClient, FakeClient(**kwargs))


def _at(hour: int, minute: int, *, day: int = 11) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=SHANGHAI)


class Clock:
    """Deterministic clock that repeats its last value once the script runs out."""

    def __init__(self, values: list[datetime]) -> None:
        self._values = values
        self._index = 0

    def __call__(self) -> datetime:
        value = self._values[min(self._index, len(self._values) - 1)]
        self._index += 1
        return value


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (_at(9, 29), False),
        (_at(9, 30), True),
        (_at(10, 0), True),
        (_at(11, 30), True),
        (_at(11, 31), False),
        (_at(12, 59), False),
        (_at(13, 0), True),
        (_at(15, 0), True),
        (_at(15, 1), False),
        (_at(10, 0, day=14), False),  # Saturday
    ],
)
def test_is_trading_session(moment: datetime, expected: bool) -> None:
    assert monitor.is_trading_session(moment) is expected


def test_seconds_until_open_covers_lunch_and_overnight() -> None:
    assert monitor.seconds_until_open(_at(9, 0)) == pytest.approx(30 * 60)
    assert monitor.seconds_until_open(_at(12, 0)) == pytest.approx(60 * 60)
    # Friday after close -> Monday 09:30.
    friday_close = datetime(2026, 11, 13, 15, 30, tzinfo=SHANGHAI)
    assert monitor.seconds_until_open(friday_close) == pytest.approx(
        (datetime(2026, 11, 16, 9, 30, tzinfo=SHANGHAI) - friday_close).total_seconds()
    )


def test_take_snapshot_writes_catalog_and_rows(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    client = _client(boards=[_board("BK0001", "甲"), _board("BK0002", "乙")], curves={})

    result = monitor.take_snapshot(client, connection, monitor.SnapshotOptions(), moment=_at(10, 5))

    assert result["trade_date"] == "2026-11-11"
    assert result["captured_at"] == "2026-11-11T10:05:00+08:00"
    assert result["row_count"] == 2
    assert result["boards_by_type"] == {"concept": 2, "industry": 0, "region": 0}
    assert board_flow_store.latest_capture(connection, trade_date="2026-11-11") == (
        "2026-11-11T10:05:00+08:00"
    )


def test_run_monitor_stops_at_session_close(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    client = _client(boards=[_board("BK0001")], curves={})
    clock = Clock([_at(14, 59), _at(15, 0), _at(15, 1)])
    slept: list[float] = []

    result = monitor.run_monitor(
        client,
        connection,
        monitor.MonitorOptions(interval_seconds=5.0),
        runtime=monitor.Runtime(now=clock, sleep=slept.append),
    )

    assert result["snapshots"] == 2
    assert slept == [5.0, 5.0]
    run = connection.execute("SELECT * FROM collection_run").fetchone()
    assert run["kind"] == "monitor"
    assert run["status"] == "ok"


def test_run_monitor_waits_for_open_then_snapshots(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    client = _client(boards=[_board("BK0001")], curves={})
    clock = Clock([_at(9, 0), _at(9, 30), _at(15, 1)])
    slept: list[float] = []

    result = monitor.run_monitor(
        client,
        connection,
        monitor.MonitorOptions(interval_seconds=30.0),
        runtime=monitor.Runtime(now=clock, sleep=slept.append),
    )

    assert result["snapshots"] == 1
    assert slept[0] == pytest.approx(60.0)


def test_run_monitor_exits_outside_session_without_waiting(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    client = _client(boards=[_board("BK0001")], curves={})

    result = monitor.run_monitor(
        client,
        connection,
        monitor.MonitorOptions(wait_for_open=False),
        runtime=monitor.Runtime(now=lambda: _at(20, 0), sleep=lambda _: None),
    )

    assert result["snapshots"] == 0
    run = connection.execute("SELECT status FROM collection_run").fetchone()
    assert run["status"] == "empty"


def _seed_catalog(connection: Any, codes: list[str]) -> None:
    board_flow_store.upsert_catalog(
        connection, [_board(code) for code in codes], seen_at="2026-11-11T09:00:00"
    )
    connection.commit()


def test_archive_minutes_skips_archived_and_reports_mismatch(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    _seed_catalog(connection, ["BK0001", "BK0002", "BK0003", "BK0004"])
    board_flow_store.upsert_minutes(
        connection,
        [*_curve("BK0001", _at(9, 0)), _close_row("BK0001", _at(9, 0))],
        archived_at="2026-11-11T15:05:00",
    )
    client = _client(
        boards=[],
        curves={
            "BK0002": _curve("BK0002", _at(9, 0)),
            "BK0003": _curve("BK0003", datetime(2026, 11, 10, tzinfo=SHANGHAI)),
        },
        failing=("BK0004",),
    )

    result = monitor.archive_minutes(
        client,
        connection,
        monitor.ArchiveOptions(trade_date=datetime(2026, 11, 11).date(), delay_seconds=0.0),
        runtime=monitor.Runtime(now=lambda: _at(15, 30), sleep=lambda _: None),
    )

    assert result["boards_requested"] == 3
    assert result["boards_stored"] == 1
    assert result["date_mismatch"] == ["BK0003"]
    assert result["failure_count"] == 1
    assert result["row_count"] == 2


def test_archive_minutes_without_pending_boards_is_empty(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    _seed_catalog(connection, ["BK0001"])
    board_flow_store.upsert_minutes(
        connection,
        [*_curve("BK0001", _at(9, 0)), _close_row("BK0001", _at(9, 0))],
        archived_at="2026-11-11T15:05:00",
    )

    result = monitor.archive_minutes(
        _client(boards=[], curves={}),
        connection,
        monitor.ArchiveOptions(trade_date=datetime(2026, 11, 11).date(), delay_seconds=0.0),
        runtime=monitor.Runtime(now=lambda: _at(15, 30), sleep=lambda _: None),
    )

    assert result["boards_requested"] == 0
    assert result["boards_stored"] == 0
    run = connection.execute("SELECT status FROM collection_run").fetchone()
    assert run["status"] == "empty"


def test_snapshot_options_reject_unknown_board_types() -> None:
    with pytest.raises(ValueError, match="Unsupported board types"):
        monitor.SnapshotOptions(board_types=("concept", "future"))


def test_run_monitor_waits_through_lunch_instead_of_exiting(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    client = _client(boards=[_board("BK0001")], curves={})
    clock = Clock([_at(11, 29), _at(11, 30), _at(12, 0), _at(13, 0), _at(15, 1)])
    slept: list[float] = []

    result = monitor.run_monitor(
        client,
        connection,
        monitor.MonitorOptions(interval_seconds=30.0),
        runtime=monitor.Runtime(now=clock, sleep=slept.append),
    )

    assert result["snapshots"] == 3
    assert slept == [30.0, 30.0, 60.0, 30.0]
    run = connection.execute("SELECT trade_date, status FROM collection_run").fetchone()
    assert run["trade_date"] == "2026-11-11"
    assert run["status"] == "ok"


def test_session_day_finished() -> None:
    assert monitor.session_day_finished(_at(15, 1)) is True
    assert monitor.session_day_finished(_at(11, 31)) is False
    assert monitor.session_day_finished(_at(10, 0, day=14)) is True


def test_archive_refetches_board_archived_before_the_close(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    _seed_catalog(connection, ["BK0001"])
    board_flow_store.upsert_minutes(
        connection, _curve("BK0001", _at(9, 0)), archived_at="2026-11-11T10:30:00"
    )
    client = _client(
        boards=[],
        curves={"BK0001": [*_curve("BK0001", _at(9, 0)), _close_row("BK0001", _at(9, 0))]},
    )

    result = monitor.archive_minutes(
        client,
        connection,
        monitor.ArchiveOptions(trade_date=datetime(2026, 11, 11).date(), delay_seconds=0.0),
        runtime=monitor.Runtime(now=lambda: _at(15, 30), sleep=lambda _: None),
    )

    assert result["boards_requested"] == 1
    assert result["boards_stored"] == 1
    latest = connection.execute(
        "SELECT MAX(trade_time) AS t FROM flow_minute WHERE board_code = 'BK0001'"
    ).fetchone()
    assert latest["t"] == "2026-11-11 15:00:00"


def test_archive_limit_restricts_requested_boards(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    _seed_catalog(connection, ["BK0001", "BK0002", "BK0003"])
    client = _client(
        boards=[],
        curves={code: _curve(code, _at(9, 0)) for code in ("BK0001", "BK0002", "BK0003")},
    )

    result = monitor.archive_minutes(
        client,
        connection,
        monitor.ArchiveOptions(
            trade_date=datetime(2026, 11, 11).date(), delay_seconds=0.0, limit=2
        ),
        runtime=monitor.Runtime(now=lambda: _at(15, 30), sleep=lambda _: None),
    )

    assert result["boards_requested"] == 2
    assert result["boards_stored"] == 2
