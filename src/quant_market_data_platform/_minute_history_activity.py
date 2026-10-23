"""Observe committed minute rows without opening a provider or changing the quota ledger."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def observe_rows(root: Path, day: str, now: datetime, warnings: list[str]) -> dict[str, Any]:
    path = root / "metadata/tushare/minute_quota/minute_quota.sqlite3"
    result: dict[str, Any] = {
        "source": "quota_ledger",
        "ledger_readable": False,
        "returned_rows_today": 0,
        "last_returned_rows_at": None,
        "age_seconds": None,
        "fresh": False,
    }
    if not path.exists():
        return result
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(committed_rows),0), "
                "MAX(CASE WHEN committed_rows>0 THEN updated_at END) "
                "FROM request_attempts WHERE quota_date=? "
                "AND consumer IN ('reverse_backfill','minute_gap_repair') AND state='committed'",
                (day,),
            ).fetchone()
        result["returned_rows_today"] = int(row[0])
        if row[1] is not None:
            at = datetime.fromisoformat(row[1].replace("Z", "+00:00"))
            at = at.replace(tzinfo=UTC) if at.tzinfo is None else at
            age = (now - at).total_seconds()
            result.update(
                last_returned_rows_at=at.isoformat(), age_seconds=age, fresh=-60 <= age <= 600
            )
        result["ledger_readable"] = True
    except (sqlite3.Error, ValueError, TypeError) as exc:
        warnings.append(f"quota ledger activity: {exc}")
    return result
