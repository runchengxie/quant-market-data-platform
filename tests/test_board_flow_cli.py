from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import quant_market_data_platform.cli as cli
import quant_market_data_platform.cli_board_flow as board_flow_cli
from quant_market_data_platform import board_flow_store
from quant_market_data_platform.providers.eastmoney_board_flow import (
    BoardRow,
    MinuteFlowRow,
)


def test_board_flow_parser_defaults() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["board-flow", "monitor"])

    assert args.command == "board-flow"
    assert args.board_flow_command == "monitor"
    assert args.interval == 30.0
    assert args.board_types == "industry,concept,region"
    assert args.no_wait is False


def test_board_flow_report_parser_requires_date() -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["board-flow", "report"])

    args = parser.parse_args(["board-flow", "report", "--date", "2026-11-11", "--top", "5"])
    assert args.board_type == "concept"
    assert args.top == 5


def test_status_command_creates_empty_store(tmp_path: Path, capsys) -> None:
    db = tmp_path / "store.sqlite3"

    exit_code = cli.main(["board-flow", "status", "--db", str(db)])

    assert exit_code == 0
    assert db.exists()
    assert '"flow_minute": 0' in capsys.readouterr().out


def test_catalog_command_uses_provider_and_stores_boards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    boards = [
        BoardRow("BK0001", "人工智能", "concept", 1e8, 1.0, 0.5, 1e10),
        BoardRow("BK0002", "半导体", "concept", 2e8, 1.0, 0.5, 1e10),
    ]

    class FakeClient:
        def fetch_boards(self, board_type: str) -> list[BoardRow]:
            return boards if board_type == "concept" else []

    monkeypatch.setattr(board_flow_cli, "BoardFlowClient", lambda: FakeClient())

    db = tmp_path / "store.sqlite3"
    exit_code = cli.main(["board-flow", "catalog", "--db", str(db), "--board-types", "concept"])

    assert exit_code == 0
    assert '"concept": 2' in capsys.readouterr().out
    connection = board_flow_store.connect(db)
    assert {row["board_code"] for row in board_flow_store.catalog_rows(connection)} == {
        "BK0001",
        "BK0002",
    }


def test_report_command_writes_html(tmp_path: Path, capsys) -> None:
    db = tmp_path / "store.sqlite3"
    connection = board_flow_store.connect(db)
    board_flow_store.upsert_catalog(
        connection,
        [BoardRow("BK0001", "人工智能", "concept", 1e8, 1.0, 0.5, 1e10)],
        seen_at="2026-11-11T09:00:00",
    )
    rows = [
        MinuteFlowRow(
            board_code="BK0001",
            board_type="concept",
            trade_time=datetime(2026, 11, 11, 9, minute),
            main_net_yuan=float(minute),
            small_net_yuan=0.0,
            mid_net_yuan=0.0,
            large_net_yuan=float(minute) / 2,
            xlarge_net_yuan=float(minute) / 2,
        )
        for minute in (31, 32)
    ]
    board_flow_store.upsert_minutes(connection, rows, archived_at="2026-11-11T15:10:00")
    connection.commit()
    output = tmp_path / "race.html"

    exit_code = cli.main(
        ["board-flow", "report", "--db", str(db), "--date", "2026-11-11", "--out", str(output)]
    )

    assert exit_code == 0
    assert output.exists()
    assert "人工智能" in output.read_text(encoding="utf-8")
    assert str(output) in capsys.readouterr().out


def test_report_command_rejects_unsupported_board_types(tmp_path: Path, capsys) -> None:
    db = tmp_path / "store.sqlite3"

    exit_code = cli.main(
        [
            "board-flow",
            "catalog",
            "--db",
            str(db),
            "--board-types",
            "concept,future",
        ]
    )

    assert exit_code == 1
    assert "Unsupported board types" in capsys.readouterr().out


def test_archive_command_reports_missing_store_data(tmp_path: Path, capsys) -> None:
    db = tmp_path / "store.sqlite3"
    cast(Any, board_flow_store.connect(db))

    exit_code = cli.main(
        ["board-flow", "archive-minutes", "--db", str(db), "--trade-date", date.today().isoformat()]
    )

    assert exit_code == 0
    assert '"boards_requested": 0' in capsys.readouterr().out
