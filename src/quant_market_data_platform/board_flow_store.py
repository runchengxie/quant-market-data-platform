"""SQLite storage for intraday A-share board fund-flow observations.

The store keeps three kinds of rows:

* ``board_catalog`` – one row per board code ever seen.
* ``flow_snapshot`` – accumulated flow for every board at one capture time.
* ``flow_minute`` – archived per-minute cumulative flow for one trading day.

Snapshots are append-only and keyed by capture time, so partial days, retries
and gaps remain visible instead of being silently overwritten.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from quant_market_data_platform.providers.eastmoney_board_flow import (
    BoardRow,
    MinuteFlowRow,
)

SCHEMA_VERSION = "1"
SOURCE_TAG = "eastmoney.board_flow.kline_1m"

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_catalog (
    board_code TEXT PRIMARY KEY,
    board_name TEXT NOT NULL,
    board_type TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flow_snapshot (
    trade_date TEXT NOT NULL,
    board_code TEXT NOT NULL,
    board_type TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    main_net_yuan REAL,
    main_net_ratio_pct REAL,
    change_pct REAL,
    amount_yuan REAL,
    PRIMARY KEY (trade_date, board_code, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_flow_snapshot_time
    ON flow_snapshot (trade_date, captured_at);

CREATE TABLE IF NOT EXISTS flow_minute (
    trade_date TEXT NOT NULL,
    board_code TEXT NOT NULL,
    board_type TEXT NOT NULL,
    trade_time TEXT NOT NULL,
    main_net_yuan REAL NOT NULL,
    small_net_yuan REAL NOT NULL,
    mid_net_yuan REAL NOT NULL,
    large_net_yuan REAL NOT NULL,
    xlarge_net_yuan REAL NOT NULL,
    source TEXT NOT NULL,
    archived_at TEXT NOT NULL,
    PRIMARY KEY (trade_date, board_code, trade_time)
);

CREATE INDEX IF NOT EXISTS idx_flow_minute_board
    ON flow_minute (trade_date, board_code, trade_time);

CREATE TABLE IF NOT EXISTS collection_run (
    run_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    board_count INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    detail TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class RunRecord:
    """Outcome of one collection command."""

    run_id: str
    kind: str
    trade_date: str
    started_at: str
    finished_at: str
    status: str
    board_count: int
    row_count: int
    detail: str


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (and initialise) a board-flow database."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.executescript(_SCHEMA)
    connection.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    connection.commit()
    return connection


def upsert_catalog(
    connection: sqlite3.Connection, rows: Iterable[BoardRow], *, seen_at: str
) -> int:
    """Insert new boards and refresh the names of known boards."""
    payload = [(row.board_code, row.board_name, row.board_type, seen_at, seen_at) for row in rows]
    if not payload:
        return 0
    connection.executemany(
        """
        INSERT INTO board_catalog (board_code, board_name, board_type, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(board_code) DO UPDATE SET
            board_name = excluded.board_name,
            board_type = excluded.board_type,
            last_seen_at = excluded.last_seen_at
        """,
        payload,
    )
    return len(payload)


def append_snapshot(
    connection: sqlite3.Connection,
    rows: Iterable[BoardRow],
    *,
    trade_date: str,
    captured_at: str,
) -> int:
    """Append one capture time's rows for every board."""
    payload = [
        (
            trade_date,
            row.board_code,
            row.board_type,
            captured_at,
            row.main_net_yuan,
            row.main_net_ratio_pct,
            row.change_pct,
            row.amount_yuan,
        )
        for row in rows
    ]
    if not payload:
        return 0
    connection.executemany(
        """
        INSERT OR REPLACE INTO flow_snapshot (
            trade_date, board_code, board_type, captured_at,
            main_net_yuan, main_net_ratio_pct, change_pct, amount_yuan
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _naive_local(moment: datetime) -> datetime:
    """Vendor minutes are Shanghai wall time; store them without an offset."""
    return moment.replace(tzinfo=None)


def upsert_minutes(
    connection: sqlite3.Connection,
    rows: Iterable[MinuteFlowRow],
    *,
    archived_at: str,
) -> int:
    """Store archived minute rows; re-archiving the same day is idempotent."""
    payload = [
        (
            row.trade_time.date().isoformat(),
            row.board_code,
            row.board_type,
            _naive_local(row.trade_time).isoformat(sep=" "),
            row.main_net_yuan,
            row.small_net_yuan,
            row.mid_net_yuan,
            row.large_net_yuan,
            row.xlarge_net_yuan,
            SOURCE_TAG,
            archived_at,
        )
        for row in rows
    ]
    if not payload:
        return 0
    connection.executemany(
        """
        INSERT OR REPLACE INTO flow_minute (
            trade_date, board_code, board_type, trade_time,
            main_net_yuan, small_net_yuan, mid_net_yuan, large_net_yuan,
            xlarge_net_yuan, source, archived_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def archived_boards(
    connection: sqlite3.Connection, *, trade_date: str, board_type: str | None
) -> set[str]:
    """Board codes that already have at least one archived minute for a day."""
    sql = "SELECT DISTINCT board_code FROM flow_minute WHERE trade_date = ?"
    params: list[Any] = [trade_date]
    if board_type:
        sql += " AND board_type = ?"
        params.append(board_type)
    return {str(row["board_code"]) for row in connection.execute(sql, params)}


def completed_boards(connection: sqlite3.Connection, *, trade_date: str) -> set[str]:
    """Boards whose archived minutes already reach the 15:00 close.

    A board archived during the session is intentionally not complete: the next
    archive run fetches it again so the afternoon is filled in.
    """
    rows = connection.execute(
        """
        SELECT board_code FROM flow_minute
        WHERE trade_date = ?
        GROUP BY board_code
        HAVING MAX(substr(trade_time, 12, 5)) >= '15:00'
        """,
        (trade_date,),
    )
    return {str(row["board_code"]) for row in rows}


def latest_capture(connection: sqlite3.Connection, *, trade_date: str) -> str | None:
    row = connection.execute(
        "SELECT MAX(captured_at) AS captured_at FROM flow_snapshot WHERE trade_date = ?",
        (trade_date,),
    ).fetchone()
    value = row["captured_at"] if row else None
    return None if value is None else str(value)


def snapshot_times(connection: sqlite3.Connection, *, trade_date: str) -> list[str]:
    rows = connection.execute(
        "SELECT DISTINCT captured_at FROM flow_snapshot WHERE trade_date = ? ORDER BY captured_at",
        (trade_date,),
    )
    return [str(row["captured_at"]) for row in rows]


def minute_series(
    connection: sqlite3.Connection, *, trade_date: str, board_codes: Sequence[str]
) -> list[sqlite3.Row]:
    """Minute rows for the requested boards, ordered by board and time."""
    if not board_codes:
        return []
    placeholders = ",".join("?" for _ in board_codes)
    return list(
        connection.execute(
            f"""
            SELECT board_code, board_type, trade_time, main_net_yuan
            FROM flow_minute
            WHERE trade_date = ? AND board_code IN ({placeholders})
            ORDER BY board_code, trade_time
            """,
            [trade_date, *board_codes],
        )
    )


def catalog_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            "SELECT board_code, board_name, board_type FROM board_catalog ORDER BY board_code"
        )
    )


def summary(connection: sqlite3.Connection) -> dict[str, Any]:
    """Return coarse table statistics for status output."""
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "tables": {}}
    for table in ("board_catalog", "flow_snapshot", "flow_minute", "collection_run"):
        count = int(connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        result["tables"][table] = count
    result["snapshot_days"] = [
        dict(row)
        for row in connection.execute(
            """
            SELECT trade_date, COUNT(DISTINCT captured_at) AS captures, COUNT(*) AS rows
            FROM flow_snapshot GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10
            """
        )
    ]
    result["minute_days"] = [
        dict(row)
        for row in connection.execute(
            """
            SELECT trade_date, COUNT(DISTINCT board_code) AS boards, COUNT(*) AS rows
            FROM flow_minute GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10
            """
        )
    ]
    return result


def record_run(connection: sqlite3.Connection, run: RunRecord) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO collection_run (
            run_id, kind, trade_date, started_at, finished_at, status,
            board_count, row_count, detail
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.run_id,
            run.kind,
            run.trade_date,
            run.started_at,
            run.finished_at,
            run.status,
            run.board_count,
            run.row_count,
            run.detail,
        ),
    )
    connection.commit()
