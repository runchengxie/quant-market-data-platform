"""Read-only board-flow coverage evidence for an explicitly supplied trading day."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quant_market_data_platform.board_flow_report import (
    LUNCH_MINUTES,
    SESSION_MINUTES,
    SESSION_OPEN_MINUTE,
)
from quant_market_data_platform.board_flow_store import SCHEMA_VERSION


def _minute_grid(day: str) -> set[str]:
    midnight = datetime.fromisoformat(day)
    return {
        (
            midnight
            + timedelta(
                minutes=SESSION_OPEN_MINUTE + i + (LUNCH_MINUTES if i > SESSION_MINUTES // 2 else 0)
            )
        ).isoformat(sep=" ")
        for i in range(1, SESSION_MINUTES + 1)
    }


def inspect_health(
    path: Path, *, expected_trade_date: str, now: datetime, deadline: datetime
) -> dict[str, Any]:
    """Inspect one consistent read transaction; never initialise or repair a store.

    The caller supplies the exchange-calendar decision and aware operational clock.
    Minute completeness uses the 240 continuous-trading minute-end observations;
    additional opening observations are reported but do not substitute missing minutes.
    """
    day = date.fromisoformat(expected_trade_date).isoformat()
    if any(value.tzinfo is None or value.utcoffset() is None for value in (now, deadline)):
        raise ValueError("now and deadline require timezone offsets")
    close = datetime.fromisoformat(f"{day}T15:00:00").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    if deadline < close:
        raise ValueError("deadline must not precede the target trading day close")
    due = now >= deadline
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "unknown",
        "db": str(path),
        "expected_trade_date": day,
        "now": now.isoformat(),
        "deadline": deadline.isoformat(),
        "phase": "after_deadline" if due else "before_deadline",
        "status_scope": "all_assets",
        "target_day_closed": now >= close,
        "catalog_minute_ready": False,
        "catalog": {"status": "unknown", "latest": None, "target_rows": None},
        "snapshot": {
            "status": "unknown",
            "latest": None,
            "target_latest": None,
            "target_rows": None,
            "full_day_complete": None,
            "latest_capture_complete": None,
        },
        "minute": {
            "status": "unknown",
            "latest": None,
            "target_latest": None,
            "target_rows": None,
            "missing_board_minutes": None,
            "complete": None,
        },
        "latest_run": None,
    }
    try:
        connection = sqlite3.connect(
            Path(path).expanduser().resolve().as_uri() + "?mode=ro", uri=True
        )
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            if version is None or version[0] != SCHEMA_VERSION:
                result["error"] = "Unsupported board-flow schema version"
            else:
                result.update(_coverage(connection, day, due, now >= close))
        finally:
            connection.close()
    except sqlite3.Error as error:
        result.update(status="unknown", error=str(error))
    return result


def _coverage(connection: sqlite3.Connection, day: str, due: bool, closed: bool) -> dict[str, Any]:
    catalog = list(
        connection.execute(
            "SELECT board_code, board_type FROM board_catalog_daily WHERE trade_date=?", (day,)
        )
    )
    expected = {tuple(row) for row in catalog}
    catalog_latest = connection.execute(
        "SELECT MAX(captured_at) FROM board_catalog_daily"
    ).fetchone()[0]
    capture = connection.execute(
        "SELECT MAX(captured_at) FROM flow_snapshot WHERE trade_date=?", (day,)
    ).fetchone()[0]
    snapshot = list(
        connection.execute(
            "SELECT board_code, board_type FROM flow_snapshot WHERE trade_date=? AND captured_at=?",
            (day, capture),
        )
    )
    snapshot_rows = connection.execute(
        "SELECT COUNT(*) FROM flow_snapshot WHERE trade_date=?", (day,)
    ).fetchone()[0]
    minute_rows = list(
        connection.execute(
            "SELECT board_code, board_type, trade_time FROM flow_minute WHERE trade_date=?", (day,)
        )
    )
    actual = {(code, kind, moment) for code, kind, moment in minute_rows}
    wanted = {(code, kind, minute) for code, kind in expected for minute in _minute_grid(day)}
    missing = wanted - actual
    latest_run = connection.execute(
        "SELECT kind,status,finished_at FROM collection_run WHERE trade_date=? "
        "ORDER BY finished_at DESC,run_id DESC LIMIT 1",
        (day,),
    ).fetchone()
    failed_run = latest_run is not None and latest_run[1] in {"failed", "partial"}
    snapshot_missing = expected - {tuple(row) for row in snapshot}
    complete = bool(expected) and not missing
    status = (
        "failed"
        if failed_run
        else ("unknown" if complete and closed else ("failed" if due else "pending"))
    )
    return {
        "status": status,
        "catalog_minute_ready": complete and closed and not failed_run,
        "catalog": {
            "status": "fresh" if expected else ("failed" if due else "pending"),
            "latest": catalog_latest,
            "target_rows": len(catalog),
            "expected_boards": len(expected),
            "source": "board_catalog_daily",
        },
        "snapshot": {
            "latest": connection.execute("SELECT MAX(captured_at) FROM flow_snapshot").fetchone()[
                0
            ],
            "target_latest": capture,
            "target_rows": snapshot_rows,
            "latest_capture_boards": len(snapshot),
            "missing_boards": len(snapshot_missing) if expected else None,
            "full_day_complete": None,
            "status": "unknown",
            "latest_capture_complete": bool(expected) and not snapshot_missing,
        },
        "minute": {
            "status": "fresh" if complete else ("failed" if due else "pending"),
            "latest": connection.execute("SELECT MAX(trade_time) FROM flow_minute").fetchone()[0],
            "target_rows": len(minute_rows),
            "target_boards": len({(row[0], row[1]) for row in minute_rows}),
            "expected_minutes_per_board": SESSION_MINUTES,
            "target_latest": max((row[2] for row in minute_rows), default=None),
            "missing_board_minutes": len(missing) if expected else None,
            "complete": bool(expected) and not missing,
        },
        "latest_run": None
        if latest_run is None
        else dict(zip(("kind", "status", "finished_at"), latest_run, strict=True)),
    }
