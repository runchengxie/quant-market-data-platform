import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from quant_market_data_platform import board_flow_store
from quant_market_data_platform.board_flow_health import inspect_health


def inspect(path: Path, **kwargs):
    return inspect_health(
        path,
        expected_trade_date="2026-11-27",
        now=datetime.fromisoformat("2026-11-27T16:00:00+08:00"),
        deadline=datetime.fromisoformat("2026-11-27T15:30:00+08:00"),
        **kwargs,
    )


def test_missing_database_is_unknown_and_never_created(tmp_path):
    path = tmp_path / "missing" / "data.sqlite3"
    result = inspect(path)
    assert result["status"] == "unknown"
    assert result["catalog"]["status"] == "unknown"
    assert result["minute"]["missing_board_minutes"] is None
    assert not path.parent.exists()


def test_empty_database_is_unknown_without_initialization(tmp_path):
    path = tmp_path / "data.sqlite3"
    sqlite3.connect(path).close()
    before = path.read_bytes()
    assert inspect(path)["status"] == "unknown"
    assert path.read_bytes() == before


def test_target_day_missing_after_deadline_is_failed(tmp_path):
    path = tmp_path / "data.sqlite3"
    board_flow_store.connect(path).close()
    result = inspect(path)
    assert result["status"] == "failed"
    assert result["catalog"]["target_rows"] == 0
    assert result["minute"]["missing_board_minutes"] is None


def test_aware_clock_required(tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        inspect_health(
            tmp_path / "x",
            expected_trade_date="2026-11-27",
            now=datetime(2026, 11, 27),
            deadline=datetime.fromisoformat("2026-11-27T15:30:00+08:00"),
        )


def test_full_grid_and_latest_capture_are_required(tmp_path):
    path = tmp_path / "data.sqlite3"
    connection = board_flow_store.connect(path)
    connection.execute(
        "INSERT INTO board_catalog_daily VALUES "
        "('2026-11-27','BK1','one','concept','2026-11-27T09:00:00+08:00')"
    )
    connection.execute(
        "INSERT INTO flow_snapshot VALUES "
        "('2026-11-27','BK1','concept','2026-11-27T15:00:00+08:00',1,1,1,1)"
    )
    from datetime import timedelta

    for start in ("09:31", "13:01"):
        moment = datetime.fromisoformat(f"2026-11-27T{start}:00")
        for i in range(120):
            connection.execute(
                "INSERT INTO flow_minute VALUES "
                "(?,?,?,?,1,1,1,1,1,'fixture','2026-11-27T15:10:00+08:00')",
                (
                    "2026-11-27",
                    "BK1",
                    "concept",
                    (moment + timedelta(minutes=i)).isoformat(sep=" "),
                ),
            )
    connection.commit()
    assert inspect(path)["status"] == "unknown"
    assert inspect(path)["catalog_minute_ready"] is True
    early = inspect_health(
        path,
        expected_trade_date="2026-11-27",
        now=datetime.fromisoformat("2026-11-27T10:00:00+08:00"),
        deadline=datetime.fromisoformat("2026-11-27T15:30:00+08:00"),
    )
    assert early["catalog_minute_ready"] is False
    assert early["status"] == "pending"
    connection.execute("DELETE FROM flow_minute WHERE trade_time='2026-11-27 10:00:00'")
    connection.commit()
    result = inspect(path)
    assert result["status"] == "failed"
    assert result["minute"]["missing_board_minutes"] == 1
    assert result["snapshot"]["full_day_complete"] is None


def test_cli_health_does_not_use_initializer(tmp_path, monkeypatch, capsys):
    import json

    from quant_market_data_platform import cli

    def forbidden(*args, **kwargs):
        raise AssertionError("initializer called")

    monkeypatch.setattr(board_flow_store, "connect", forbidden)
    assert (
        cli.main(
            [
                "board-flow",
                "health",
                "--db",
                str(tmp_path / "absent"),
                "--expected-trade-date",
                "2026-11-27",
                "--now",
                "2026-11-27T16:00:00+08:00",
                "--deadline",
                "2026-11-27T15:30:00+08:00",
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["status"] == "unknown"


def test_before_deadline_is_pending_and_naive_clock_cli_rejected(tmp_path, capsys):
    from quant_market_data_platform import cli

    path = tmp_path / "data.sqlite3"
    board_flow_store.connect(path).close()
    report = inspect_health(
        path,
        expected_trade_date="2026-11-27",
        now=datetime.fromisoformat("2026-11-27T10:00:00+08:00"),
        deadline=datetime.fromisoformat("2026-11-27T15:30:00+08:00"),
    )
    assert report["status"] == "pending"
    assert report["phase"] == "before_deadline"
    assert (
        cli.main(
            [
                "board-flow",
                "health",
                "--db",
                str(path),
                "--expected-trade-date",
                "2026-11-27",
                "--now",
                "2026-11-27T16:00:00",
                "--deadline",
                "2026-11-27T15:30:00+08:00",
            ]
        )
        == 1
    )
    assert "timezone" in capsys.readouterr().out


def test_deadline_cannot_precede_target_day_close(tmp_path):
    with pytest.raises(ValueError, match="close"):
        inspect_health(
            tmp_path / "x",
            expected_trade_date="2026-11-27",
            now=datetime.fromisoformat("2026-11-27T10:00:00+08:00"),
            deadline=datetime.fromisoformat("2026-11-27T14:00:00+08:00"),
        )


def test_unknown_schema_version_is_not_healthy(tmp_path):
    path = tmp_path / "data.sqlite3"
    connection = board_flow_store.connect(path)
    connection.execute("UPDATE schema_meta SET value='99' WHERE key='schema_version'")
    connection.commit()
    connection.close()
    assert inspect(path)["status"] == "unknown"
