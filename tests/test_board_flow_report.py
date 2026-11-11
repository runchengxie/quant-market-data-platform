from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from quant_market_data_platform import board_flow_report, board_flow_store
from quant_market_data_platform.providers.eastmoney_board_flow import BoardRow, MinuteFlowRow


def _seed(connection) -> None:
    boards = [
        BoardRow("BK0001", "人工智能", "concept", 1e8, 1.0, 0.5, 1e10),
        BoardRow("BK0002", "机器人概念", "concept", 2e8, 2.0, 1.0, 1e10),
        BoardRow("BK0003", "芯片", "concept", 5e7, 0.5, 0.1, 1e10),
    ]
    board_flow_store.upsert_catalog(connection, boards, seen_at="2026-11-11T09:00:00")
    for code, base in (("BK0001", 1e8), ("BK0002", 2e8), ("BK0003", 5e7)):
        rows = [
            MinuteFlowRow(
                board_code=code,
                board_type="concept",
                trade_time=datetime(2026, 11, 11, 9, minute),
                main_net_yuan=base * (minute - 30),
                small_net_yuan=0.0,
                mid_net_yuan=0.0,
                large_net_yuan=base * (minute - 30) / 2,
                xlarge_net_yuan=base * (minute - 30) / 2,
            )
            for minute in (31, 32, 33, 34, 35)
        ]
        board_flow_store.upsert_minutes(connection, rows, archived_at="2026-11-11T15:10:00")
    connection.commit()


def test_render_race_report_contains_selected_boards(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    _seed(connection)

    html = board_flow_report.render_race_report(
        connection,
        board_flow_report.RaceReportOptions(trade_date=date(2026, 11, 11), top_n=2),
    )

    assert "<!doctype html>" in html
    assert "机器人概念" in html
    assert "累计主力净流入" in html
    assert "echarts" in html
    assert "人工智能" in html
    assert "芯片" not in html.split("<tbody>")[1]
    assert '"09:31"' in html
    assert "31:00" not in html


def test_render_race_report_filters_by_name(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    _seed(connection)

    html = board_flow_report.render_race_report(
        connection,
        board_flow_report.RaceReportOptions(trade_date=date(2026, 11, 11), names=("BK0003",)),
    )

    assert "芯片" in html
    assert "机器人概念" not in html.split("<tbody>")[1]


def test_render_race_report_requires_archived_rows(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")

    with pytest.raises(board_flow_report.ReportError, match="No archived minute rows"):
        board_flow_report.render_race_report(
            connection, board_flow_report.RaceReportOptions(trade_date=date(2026, 11, 11))
        )


def test_render_race_report_rejects_unknown_names(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    _seed(connection)

    with pytest.raises(board_flow_report.ReportError, match="None of the requested"):
        board_flow_report.render_race_report(
            connection,
            board_flow_report.RaceReportOptions(trade_date=date(2026, 11, 11), names=("不存在",)),
        )


def test_write_csv_exports_selected_boards(tmp_path: Path) -> None:
    connection = board_flow_store.connect(tmp_path / "store.sqlite3")
    _seed(connection)
    target = tmp_path / "out.csv"

    result = board_flow_report.write_csv(
        connection,
        board_flow_report.RaceReportOptions(trade_date=date(2026, 11, 11), top_n=2),
        target,
    )

    assert result["boards"] == 2
    assert result["rows"] == 10
    text = target.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0].startswith("trade_date,board_code,board_name")
    assert "机器人概念" in text
