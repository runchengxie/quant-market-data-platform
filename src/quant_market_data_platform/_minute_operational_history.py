"""Private per-invocation evidence independent of resumable minute checkpoints."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from quant_market_data_platform.minute_candidate import _atomic_write_json

SCHEMA_VERSION = "a_share.minute_operational_run.v1"
_STATUSES = {
    "running",
    "pending",
    "complete",
    "partial",
    "interrupted",
    "failed",
    "noop",
    "download_incomplete",
    "operational_canonical_updated",
}
_RESULT_FIELDS = {
    "target_date",
    "date_max",
    "dates",
    "rows",
    "version_receipt",
    "promotion_receipt",
    "backfill_receipt",
    "legacy_canonical_mutated",
}
_TOTAL_FIELDS = {
    "segments_total",
    "segments_complete",
    "dates_complete",
    "minute_request_upper_bound",
    "actual_successful_minute_requests",
    "actual_attempted_minute_requests",
    "fallback_successful_minute_requests",
    "fallback_attempted_minute_requests",
    "dates_fetched",
    "dates_skipped",
    "total_bars",
    "dates_partial",
    "persisted_bars",
}


class _RunOptions(Protocol):
    @property
    def artifacts_root(self) -> Path: ...

    @property
    def target_date(self) -> str | None: ...


_Options = TypeVar("_Options", bound=_RunOptions)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _history_root(root: Path) -> Path:
    history = root / "metadata/minute_operational/history"
    if not history.resolve().is_relative_to(root):
        raise ValueError("Operational history directory escapes artifacts root")
    return history


def _date(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"\d{8}", value) else None


def _status(value: Any) -> str:
    return value if isinstance(value, str) and value in _STATUSES else "unknown"


def _safe_error(error: Any) -> dict[str, Any]:
    if not isinstance(error, dict):
        return {}
    message = str(error.get("message", ""))
    lowered = message.lower()
    category = "unknown"
    if any(value in lowered for value in ("频率超限", "rate limit", "quota", "too many requests")):
        category = "provider_rate_limit"
    elif any(value in lowered for value in ("权限", "permission", "unauthorized", "token")):
        category = "auth_or_permission"
    elif any(value in lowered for value in ("partial", "incomplete", "不完整")):
        category = "incomplete_partition"
    elif any(value in lowered for value in ("timeout", "connection", "timed out")):
        category = "transient_network"
    error_type = str(error.get("type", "unknown"))
    result: dict[str, Any] = {
        "error_type": error_type if re.fullmatch(r"[A-Za-z_]\w*", error_type) else "unknown",
        "category": category,
        "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
    }
    rate = re.search(r"(\d+)\s*次\s*/\s*(小时|分钟|天|日)", message)
    if rate and category == "provider_rate_limit":
        result.update(
            rate_limit_count=int(rate[1]),
            rate_limit_unit={"小时": "hour", "分钟": "minute", "天": "day", "日": "day"}[rate[2]],
        )
    return result


def _partition_failures(payload: dict[str, Any], root: Path) -> dict[str, Any]:
    output = payload.get("output", {})
    if not isinstance(output, dict) or not output.get("data_dir"):
        return {"partition_failures": []}
    data = Path(output["data_dir"]).expanduser().resolve()
    if not data.is_relative_to(root):
        return {"snapshot_error": "data_dir_outside_artifacts_root"}
    failures = []
    for path in sorted(data.glob("trade_date=*/_minute_mirror.json")):
        if not path.resolve().is_relative_to(root):
            return {"snapshot_error": "sidecar_outside_artifacts_root"}
        sidecar = json.loads(path.read_bytes())
        if not isinstance(sidecar, dict):
            raise ValueError("minute sidecar must contain an object")
        if sidecar.get("status") != "complete":
            failures.append(
                {
                    "trade_date": _date(sidecar.get("trade_date")),
                    "status": _status(sidecar.get("status")),
                    "missing_symbol_count": len(sidecar.get("missing_request_symbols", [])),
                    "error": _safe_error(sidecar.get("error")),
                }
            )
    return {"partition_failures": failures}


def _segment_snapshot(segment: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": _status(segment.get("status")),
        "error": _safe_error(segment.get("error")),
    }
    result.update(
        {
            key: _date(segment[key])
            for key in ("start_date", "end_date", "trade_date")
            if key in segment
        }
    )
    return result


def _checkpoint_snapshot(path: Path, root: Path) -> dict[str, Any]:
    if not path.resolve().is_relative_to(root):
        return {"snapshot_error": "receipt_outside_artifacts_root"}
    if not path.exists():
        return {"receipt_status": "missing"}
    raw = path.read_bytes()
    result: dict[str, Any] = {"receipt_sha256": hashlib.sha256(raw).hexdigest()}
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("backfill receipt must contain an object")
        result.update(status=_status(payload.get("status")), receipt_status="readable")
        totals = payload.get("totals", {})
        result["totals"] = {
            k: v for k, v in totals.items() if k in _TOTAL_FIELDS and isinstance(v, int | float)
        }
        result["segments"] = [_segment_snapshot(segment) for segment in payload.get("segments", [])]
        if result["status"] != "complete":
            result.update(_partition_failures(payload, root))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result["snapshot_error"] = type(exc).__name__
    return result


def _safe_result(payload: dict[str, Any], root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"status": _status(payload.get("status"))}
    for key in _RESULT_FIELDS & payload.keys():
        value = payload[key]
        if isinstance(value, int | float):
            result[key] = value
        elif isinstance(value, str) and re.fullmatch(r"\d{8}", value):
            result[key] = value
        elif key.endswith("_receipt") and isinstance(value, str):
            path = Path(value).expanduser().resolve()
            if path.is_relative_to(root):
                result[key] = str(path)
    return result


@dataclass
class _Journal:
    root: Path
    path: Path
    payload: dict[str, Any]

    def write(self) -> None:
        _atomic_write_json(self.path, self.payload)


_ACTIVE: ContextVar[_Journal | None] = ContextVar("minute_operational_run", default=None)


def record_operational_run(
    function: Callable[[_Options], dict[str, Any]],
) -> Callable[[_Options], dict[str, Any]]:
    """Preserve run evidence while returning the original operational payload."""

    @wraps(function)
    def wrapped(options: _Options) -> dict[str, Any]:
        root = options.artifacts_root.expanduser().resolve()
        history = _history_root(root)
        history.mkdir(parents=True, exist_ok=True, mode=0o700)
        started = _now()
        run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid4().hex}"
        journal = _Journal(
            root,
            history / f"{run_id}.json",
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "started_at": started,
                "finished_at": None,
                "status": "running",
                "target_date": _date(options.target_date),
                "fallback_enabled": bool(getattr(options, "fallback_token_env", None)),
                "attempts": [],
            },
        )
        journal.write()
        token = _ACTIVE.set(journal)
        try:
            result = function(options)
            journal.payload.update(
                status=_status(result.get("status")), result=_safe_result(result, root)
            )
            journal.payload["target_date"] = (
                _date(result.get("target_date"))
                or _date(journal.payload.get("target_date"))
                or _date(result.get("date_max"))
                or _date(options.target_date)
            )
            return result
        except BaseException as exc:
            journal.payload.update(
                status="failed" if isinstance(exc, Exception) else "interrupted",
                error_type=type(exc).__name__,
            )
            raise
        finally:
            try:
                journal.payload["finished_at"] = _now()
                journal.write()
            finally:
                _ACTIVE.reset(token)

    return wrapped


def record_backfill_attempt(token_env: str, receipt_path: Path) -> None:
    """Freeze safe failure facts before a subsequent resume can overwrite them."""
    journal = _ACTIVE.get()
    if journal is None:
        return
    try:
        snapshot = _checkpoint_snapshot(receipt_path, journal.root)
    except OSError as exc:
        snapshot = {"snapshot_error": type(exc).__name__}
    journal.payload["attempts"].append(
        {
            "token_env": token_env,
            "recorded_at": _now(),
            **snapshot,
        }
    )
    journal.write()


def set_operational_target(target_date: str) -> None:
    """Retain the resolved trading date even if execution fails before returning."""
    journal = _ACTIVE.get()
    if journal is None:
        return
    resolved = _date(target_date)
    if resolved is None:
        raise ValueError("Operational target must be an eight-digit trade date")
    journal.payload["target_date"] = resolved
    journal.write()


@contextmanager
def capture_backfill_attempt(token_env: str, receipt_path: Path) -> Generator[None]:
    """Freeze the checkpoint on exit while the caller still owns its run lock."""
    try:
        yield
    finally:
        record_backfill_attempt(token_env, receipt_path)


def _normalize_history_times(payload: dict[str, Any]) -> None:
    started = datetime.fromisoformat(payload["started_at"])
    finished_raw = payload.get("finished_at")
    finished = datetime.fromisoformat(finished_raw) if finished_raw is not None else None
    for stamp in (started, finished):
        if stamp is not None and (stamp.tzinfo is None or stamp.utcoffset() is None):
            raise ValueError("Operational history timestamps must be timezone-aware")
    if finished is not None and finished < started:
        raise ValueError("Operational history cannot finish before it started")
    payload["started_at"] = started.astimezone(UTC).isoformat()
    payload["finished_at"] = finished.astimezone(UTC).isoformat() if finished is not None else None


def read_run_history(artifacts_root: Path) -> list[dict[str, Any]]:
    """Read retained runs without changing files or hiding malformed evidence."""
    root = artifacts_root.expanduser().resolve()
    history = _history_root(root)
    records = []
    for path in history.glob("*.json"):
        if not path.resolve().is_relative_to(root):
            raise ValueError("Operational history file escapes artifacts root")
        try:
            payload = json.loads(path.read_bytes())
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != SCHEMA_VERSION
                or not isinstance(payload.get("started_at"), str)
                or not isinstance(payload.get("run_id"), str)
            ):
                raise ValueError("invalid operational history schema")
            _normalize_history_times(payload)
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"Cannot read operational history: {path.name}") from exc
        records.append(payload)
    return sorted(records, key=lambda value: (value["started_at"], value["run_id"]))
