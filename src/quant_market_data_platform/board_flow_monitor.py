"""Intraday monitoring and daily archiving of A-share board fund flow.

The module separates three operations so each can be scheduled independently:

* ``take_snapshot`` records every board's accumulated flow at one instant.
* ``run_monitor`` repeats snapshots inside the mainland trading sessions.
* ``archive_minutes`` stores the vendor's per-minute cumulative curve for the day.

All timestamps are Asia/Shanghai. Trading days are approximated by weekdays; the
vendor simply returns no rows on a holiday, which is recorded as an empty run.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from typing import Any
from zoneinfo import ZoneInfo

from quant_market_data_platform import board_flow_store
from quant_market_data_platform.providers.eastmoney_board_flow import (
    BOARD_TYPES,
    BoardFlowClient,
    BoardFlowError,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
MORNING = (clock_time(9, 30), clock_time(11, 30))
AFTERNOON = (clock_time(13, 0), clock_time(15, 0))
SESSIONS = (MORNING, AFTERNOON)


def shanghai_now() -> datetime:
    return datetime.now(tz=SHANGHAI)


@dataclass(frozen=True)
class Runtime:
    """Injectable clock, sleeper and progress hook for tests and CLIs."""

    now: Callable[[], datetime] = shanghai_now
    sleep: Callable[[float], None] = time.sleep
    progress: Callable[[int, int], None] | None = None


@dataclass(frozen=True)
class SnapshotOptions:
    """Which board families one snapshot covers."""

    board_types: tuple[str, ...] = BOARD_TYPES

    def __post_init__(self) -> None:
        unknown = [value for value in self.board_types if value not in BOARD_TYPES]
        if unknown:
            raise ValueError(f"Unsupported board types: {', '.join(unknown)}.")


@dataclass(frozen=True)
class MonitorOptions(SnapshotOptions):
    """Snapshot loop settings."""

    interval_seconds: float = 30.0
    wait_for_open: bool = True
    max_minutes: float | None = None


@dataclass(frozen=True)
class ArchiveOptions:
    """Daily minute-curve archive settings."""

    board_types: tuple[str, ...] = BOARD_TYPES
    trade_date: date | None = None
    delay_seconds: float = 0.2
    skip_archived: bool = True
    limit: int | None = None


def is_trading_session(moment: datetime) -> bool:
    """True when ``moment`` falls inside a mainland continuous-trading window."""
    local = moment.astimezone(SHANGHAI)
    if local.weekday() >= 5:
        return False
    current = local.time()
    return any(start <= current <= end for start, end in SESSIONS)


def session_day_finished(moment: datetime) -> bool:
    """True once no further continuous trading window can open that day."""
    local = moment.astimezone(SHANGHAI)
    if local.weekday() >= 5:
        return True
    return local.time() > SESSIONS[-1][1]


def seconds_until_open(moment: datetime) -> float:
    """Seconds from ``moment`` to the next session start (never negative)."""
    local = moment.astimezone(SHANGHAI)
    for day_offset in range(0, 5):
        day = (local + timedelta(days=day_offset)).date()
        if day.weekday() >= 5:
            continue
        candidates = [
            datetime.combine(day, start, tzinfo=SHANGHAI)
            for start, _ in SESSIONS
            if datetime.combine(day, start, tzinfo=SHANGHAI) > local
        ]
        if candidates:
            return max(0.0, (min(candidates) - local).total_seconds())
    return 0.0


def take_snapshot(
    client: BoardFlowClient,
    connection: Any,
    options: SnapshotOptions,
    *,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Capture one snapshot for every requested board type."""
    captured = (moment or shanghai_now()).astimezone(SHANGHAI)
    trade_date = captured.date().isoformat()
    captured_at = captured.isoformat(timespec="seconds")
    rows = 0
    boards = 0
    per_type: dict[str, int] = {}
    for board_type in options.board_types:
        board_rows = client.fetch_boards(board_type)
        board_flow_store.upsert_catalog(connection, board_rows, seen_at=captured_at)
        board_flow_store.snapshot_catalog(
            connection, board_rows, trade_date=trade_date, captured_at=captured_at
        )
        rows += board_flow_store.append_snapshot(
            connection, board_rows, trade_date=trade_date, captured_at=captured_at
        )
        boards += len(board_rows)
        per_type[board_type] = len(board_rows)
    connection.commit()
    return {
        "trade_date": trade_date,
        "captured_at": captured_at,
        "board_count": boards,
        "row_count": rows,
        "boards_by_type": per_type,
    }


@dataclass(frozen=True)
class RunDraft:
    """Identity of one collection run, finished with an outcome."""

    kind: str
    trade_date: str
    started_at: datetime

    def finish(
        self,
        connection: Any,
        *,
        status: str,
        counts: tuple[int, int],
        detail: dict[str, Any],
        now: Callable[[], datetime],
    ) -> str:
        board_count, row_count = counts
        run_id = f"{self.trade_date}-{self.kind}-{uuid.uuid4().hex[:10]}"
        board_flow_store.record_run(
            connection,
            board_flow_store.RunRecord(
                run_id=run_id,
                kind=self.kind,
                trade_date=self.trade_date,
                started_at=self.started_at.isoformat(timespec="seconds"),
                finished_at=now().isoformat(timespec="seconds"),
                status=status,
                board_count=board_count,
                row_count=row_count,
                detail=json.dumps(detail, ensure_ascii=False),
            ),
        )
        return run_id


def run_monitor(
    client: BoardFlowClient,
    connection: Any,
    options: MonitorOptions,
    *,
    runtime: Runtime | None = None,
) -> dict[str, Any]:
    """Snapshot repeatedly until the trading session closes."""
    runtime = runtime or Runtime()
    started_at = runtime.now()
    started_monotonic = time.monotonic()
    snapshots = 0
    rows = 0
    boards = 0
    moment = started_at
    trade_date = started_at.astimezone(SHANGHAI).date().isoformat()
    while True:
        if not is_trading_session(moment):
            if not options.wait_for_open and snapshots == 0:
                break
            if session_day_finished(moment):
                break
            runtime.sleep(min(60.0, max(1.0, seconds_until_open(moment))))
            moment = runtime.now()
            continue
        result = take_snapshot(client, connection, options, moment=moment)
        trade_date = str(result["trade_date"])
        snapshots += 1
        rows += int(result["row_count"])
        boards = int(result["board_count"])
        if options.max_minutes is not None:
            if time.monotonic() - started_monotonic >= options.max_minutes * 60.0:
                break
        runtime.sleep(options.interval_seconds)
        moment = runtime.now()
    detail = {
        "snapshots": snapshots,
        "interval_seconds": options.interval_seconds,
        "board_types": list(options.board_types),
    }
    run_id = RunDraft(kind="monitor", trade_date=trade_date, started_at=started_at).finish(
        connection,
        status="ok" if snapshots else "empty",
        counts=(boards, rows),
        detail=detail,
        now=runtime.now,
    )
    return {"run_id": run_id, "snapshots": snapshots, "row_count": rows, **detail}


def _catalog_boards(connection: Any, *, board_types: tuple[str, ...]) -> list[tuple[str, str]]:
    rows = connection.execute(
        "SELECT board_code, board_type FROM board_catalog ORDER BY board_type, board_code"
    )
    return [
        (str(row["board_code"]), str(row["board_type"]))
        for row in rows
        if str(row["board_type"]) in board_types
    ]


def archive_minutes(
    client: BoardFlowClient,
    connection: Any,
    options: ArchiveOptions,
    *,
    runtime: Runtime | None = None,
) -> dict[str, Any]:
    """Archive the day's minute curve for every catalogued board."""
    runtime = runtime or Runtime()
    started_at = runtime.now()
    trade_date = options.trade_date or started_at.astimezone(SHANGHAI).date()
    wanted = trade_date.isoformat()
    archived = (
        board_flow_store.completed_boards(connection, trade_date=wanted)
        if options.skip_archived
        else set()
    )
    boards = _catalog_boards(connection, board_types=options.board_types)
    pending = [(code, kind) for code, kind in boards if code not in archived]
    if options.limit is not None:
        pending = pending[: max(0, options.limit)]
    stored_rows = 0
    stored_boards = 0
    failures: list[dict[str, str]] = []
    mismatched: list[str] = []
    for index, (board_code, board_type) in enumerate(pending, start=1):
        try:
            curve = client.fetch_minute_curve(board_code, board_type=board_type)
        except BoardFlowError as error:
            failures.append({"board_code": board_code, "error": str(error)})
        else:
            if curve[0].trade_time.date() != trade_date:
                mismatched.append(board_code)
            else:
                stored_rows += board_flow_store.upsert_minutes(
                    connection, curve, archived_at=runtime.now().isoformat(timespec="seconds")
                )
                stored_boards += 1
        if index % 50 == 0:
            connection.commit()
            if runtime.progress is not None:
                runtime.progress(index, len(pending))
        if options.delay_seconds:
            runtime.sleep(options.delay_seconds)
    connection.commit()
    status = "ok" if stored_boards else ("empty" if not pending else "failed")
    detail = {
        "boards_requested": len(pending),
        "boards_stored": stored_boards,
        "failure_count": len(failures),
        "date_mismatch": mismatched[:20],
        "failure_sample": failures[:10],
    }
    run_id = RunDraft(kind="archive_minutes", trade_date=wanted, started_at=started_at).finish(
        connection,
        status=status,
        counts=(stored_boards, stored_rows),
        detail=detail,
        now=runtime.now,
    )
    return {"run_id": run_id, "trade_date": wanted, "row_count": stored_rows, **detail}
