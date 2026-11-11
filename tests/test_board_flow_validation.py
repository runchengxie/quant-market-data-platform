from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from quant_market_data_platform import board_flow_store
from quant_market_data_platform.board_flow_validation import compare, latest_values
from quant_market_data_platform.providers.eastmoney_board_flow import BoardRow, MinuteFlowRow


def _seed(connection) -> None:
    boards = [
        BoardRow("BK0001", "人工智能", "concept", 1e8, 1.0, 0.5, 1e10),
        BoardRow("BK0002", "机器人概念", "concept", 2e8, 1.0, 0.5, 1e10),
    ]
    board_flow_store.upsert_catalog(connection, boards, seen_at="2026-11-11T09:00:00")
    for code, value in (("BK0001", 261.0e8), ("BK0002", -30.0e8)):
        rows = [
            MinuteFlowRow(
                board_code=code,
                board_type="concept",
                trade_time=datetime(2026, 11, 11, 9, 31),
                main_net_yuan=value / 2,
                small_net_yuan=0.0,
                mid_net_yuan=0.0,
                large_net_yuan=value / 4,
                xlarge_net_yuan=value / 4,
            ),
            MinuteFlowRow(
                board_code=code,
                board_type="concept",
                trade_time=datetime(2026, 11, 11, 15, 0),
                main_net_yuan=value,
                small_net_yuan=0.0,
                mid_net_yuan=0.0,
                large_net_yuan=value / 2,
                xlarge_net_yuan=value / 2,
            ),
        ]
        board_flow_store.upsert_minutes(connection, rows, archived_at="2026-11-11T15:10:00")
    connection.commit()


def test_latest_values_uses_the_close_row(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    _seed(connection)

    values = latest_values(connection, trade_date="2026-11-11", board_type="concept")

    assert values["BK0001"] == ("人工智能", pytest.approx(261.0e8))
    assert values["BK0002"][1] == pytest.approx(-30.0e8)


def test_compare_reports_match_and_worst_boards(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    _seed(connection)
    ours = latest_values(connection, trade_date="2026-11-11", board_type="concept")

    result = compare(
        [
            {"code": "BK0001", "name": "人工智能", "net_amount": 261.0e8},
            {"code": "BK0002", "name": "机器人概念", "net_amount": -30.0e8},
        ],
        ours,
        tolerance_yuan=1e6,
    )

    assert result["ok"] is True
    assert result["matched"] == 2
    assert result["max_abs_diff_yi"] == 0.0


def test_compare_flags_mismatch_and_missing_boards(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    _seed(connection)
    ours = latest_values(connection, trade_date="2026-11-11", board_type="concept")

    result = compare(
        [
            {"code": "BK0001", "name": "人工智能", "net_amount": 300.0e8},
            {"code": "BK9999", "name": "缺失板块", "net_amount": 1.0e8},
        ],
        ours,
        tolerance_yuan=1e6,
    )

    assert result["ok"] is False
    assert result["matched"] == 1
    assert result["missing_in_archive"] == ["BK9999"]
    assert result["worst"][0]["code"] == "BK0001"


def test_compare_without_overlap_reports_error() -> None:
    result = compare(
        [{"code": "BK9999", "name": "缺失", "net_amount": 1.0}], {}, tolerance_yuan=1e6
    )

    assert result["ok"] is False
    assert "no matching boards" in result["error"]
