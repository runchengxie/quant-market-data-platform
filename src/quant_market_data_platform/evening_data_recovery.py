"""Bounded recovery of delayed margin inputs for the owner evening producer."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

DELAYED_DATASETS = frozenset({"margin", "margin_detail"})
LOOKBACK_SESSIONS = 2
ALLOWED_LAG_DAYS = 2
RunMirror = Callable[[str, Path, str, str], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DelayedDatasetRequest:
    dataset: str
    command: str
    out_dir: Path
    target: str
    data_root: Path
    token_env: str


def recovery_dates(data_root: Path, target: str) -> list[str]:
    """Use the authoritative local SSE calendar, never guessed weekdays."""
    calendar = data_root / "assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet"
    with pq.ParquetFile(calendar) as parquet:
        rows = parquet.read(columns=["exchange", "cal_date", "is_open"]).to_pylist()
    for row in rows:
        if row["exchange"] != "SSE":
            continue
        date = str(row["cal_date"])
        if not re.fullmatch(r"\d{8}", date) or str(row["is_open"]) not in {"0", "1"}:
            raise ValueError("malformed local SSE calendar")
        datetime.strptime(date, "%Y%m%d")
    sessions = sorted(
        {
            str(row["cal_date"])
            for row in rows
            if row["exchange"] == "SSE" and str(row["is_open"]) == "1"
        }
    )
    if target not in sessions:
        raise ValueError("target is not an open session in the local SSE calendar")
    index = sessions.index(target)
    if index < LOOKBACK_SESSIONS:
        raise ValueError("local SSE calendar has insufficient preceding sessions")
    return sessions[index - LOOKBACK_SESSIONS : index + 1]


def partition_info(out_dir: Path, date: str, dataset: str) -> dict[str, Any] | None:
    """Validate positive date/key evidence without claiming market completeness."""
    path = out_dir / "data" / f"trade_date={date}" / "part.parquet"
    if not path.exists():
        return None
    key = "exchange_id" if dataset == "margin" else "ts_code"
    with pq.ParquetFile(path) as parquet:
        if parquet.metadata.num_rows <= 0:
            raise ValueError(f"empty partition: {date}")
        rows = parquet.read(columns=["trade_date", key]).to_pylist()
    if not rows or any(str(row["trade_date"]) != date for row in rows):
        raise ValueError(f"partition trade_date mismatch: {date}")
    keys = [str(row[key]) for row in rows]
    valid = (
        all(value in {"SSE", "SZSE", "BSE"} for value in keys)
        if dataset == "margin"
        else all(re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value) for value in keys)
    )
    if not valid or len(set(keys)) != len(keys):
        raise ValueError(f"invalid or duplicate {key}: {date}")
    markets: dict[str, int] = {}
    for value in keys:
        market = value if dataset == "margin" else value[-2:]
        markets[market] = markets.get(market, 0) + 1
    return {"date": date, "rows": len(rows), "path": str(path), "markets": markets}


def latest_available(out_dir: Path, target: str, dataset: str) -> dict[str, Any] | None:
    dates = sorted(
        (
            path.name.split("=", 1)[1]
            for path in (out_dir / "data").glob("trade_date=*")
            if re.fullmatch(r"trade_date=\d{8}", path.name) and path.name.split("=", 1)[1] <= target
        ),
        reverse=True,
    )
    for date in dates:
        try:
            info = partition_info(out_dir, date, dataset)
        except (OSError, ValueError, pa.ArrowException):
            continue
        if info is not None:
            return info
    return None


def attempt_date(request: DelayedDatasetRequest, date: str, run: RunMirror) -> dict[str, Any]:
    before = partition_info(request.out_dir, date, request.dataset)
    if before is not None:
        return {"date": date, "status": "existing", "command_status": None, **before}
    result = run(request.command, request.out_dir, date, request.token_env)
    after = partition_info(request.out_dir, date, request.dataset)
    return {
        "date": date,
        "status": "written" if after and result.returncode == 0 else "empty",
        "command_status": result.returncode,
        "output_tail": (result.stdout + result.stderr)[-1200:],
        **(after or {}),
    }


def prepare_delayed_dataset(request: DelayedDatasetRequest, run: RunMirror) -> dict[str, Any]:
    """Revisit at most two sessions; target-ready and delayed availability differ."""
    dataset, out_dir, target = request.dataset, request.out_dir, request.target
    window_error = None
    try:
        dates = recovery_dates(request.data_root, target)
    except (OSError, ValueError, pa.ArrowException) as exc:
        dates = [target]
        window_error = str(exc)
    attempts: list[dict[str, Any]] = []
    for date in dates:
        try:
            item = attempt_date(request, date, run)
        except (OSError, ValueError, pa.ArrowException) as exc:
            item = {"date": date, "status": "invalid", "error": str(exc), "command_status": None}
        attempts.append(item)
        if item.get("command_status") not in {None, 0}:
            break
    latest = latest_available(out_dir, target, dataset)
    as_of = latest["date"] if latest else None
    lag = (
        (datetime.strptime(target, "%Y%m%d") - datetime.strptime(as_of, "%Y%m%d")).days
        if as_of
        else None
    )
    target_ready = as_of == target
    failed = any(
        item.get("error") or item.get("command_status") not in {None, 0} for item in attempts
    )
    status = "failed"
    if not failed:
        if target_ready:
            status = "ready"
        elif as_of and lag is not None:
            status = "expected_delay" if lag <= ALLOWED_LAG_DAYS else "stale"
    command_status = next(
        (
            item["command_status"]
            for item in attempts
            if item.get("command_status") not in {None, 0}
        ),
        0,
    )
    return {
        "dataset": dataset,
        "required": False,
        "status": status,
        "path": str(out_dir / "data" / f"trade_date={target}" / "part.parquet"),
        "command_status": command_status,
        "output_tail": attempts[-1].get("output_tail", "") if attempts else "",
        "requested_target_date": target,
        "target_ready": target_ready,
        "as_of": as_of,
        "latest_available_rows": latest["rows"] if latest else 0,
        "latest_available_path": latest["path"] if latest else None,
        "market_coverage": latest["markets"] if latest else {},
        "market_completeness": "not_established",
        "lag_calendar_days": lag,
        "allowed_lag_calendar_days": ALLOWED_LAG_DAYS,
        "recovery_dates": dates,
        "recovery_window_error": window_error,
        "recovered_dates": [
            item["date"]
            for item in attempts
            if item["status"] == "written" and item["date"] != target
        ],
        "date_results": attempts,
    }
