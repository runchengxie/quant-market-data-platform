"""Read-only history state and local-only monitoring, without provider initialization."""

from __future__ import annotations

import fcntl
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quant_market_data_platform._minute_history_inventory import inventory, read_json

_MIN_FREE = 10 * 1024**3


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result
    except ValueError:
        return None


def _writer_active(root: Path, warnings: list[str]) -> bool:
    path = root / "metadata/minute_backfill/reverse_scheduler/writer.lock"
    if not path.exists():
        return False
    try:
        with path.open("rb") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError as exc:
        warnings.append(f"writer lock: {exc}")
    return False


def _returned_rows(root: Path, day: str, warnings: list[str]) -> int:
    path = root / "metadata/tushare/minute_quota/minute_quota.sqlite3"
    if not path.exists():
        return 0
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(committed_rows),0) FROM request_attempts "
                "WHERE quota_date=? AND consumer IN ('reverse_backfill','minute_gap_repair') "
                "AND state='committed'",
                (day,),
            ).fetchone()
            return int(row[0])
    except (sqlite3.Error, ValueError) as exc:
        warnings.append(f"quota ledger: {exc}")
        return 0


def _quota(preflight: dict[str, Any]) -> dict[str, Any]:
    source = preflight.get("quota", preflight.get("quota_status", preflight))
    if not isinstance(source, dict):
        return {}
    fields = (
        "quota_date",
        "available_requests",
        "available_rows",
        "committed_requests",
        "uncertain_requests",
        "hold_requests",
        "closed_reason",
        "effective_request_ceiling",
    )
    return {key: source[key] for key in fields if key in source}


def _preflight(runtime: Path, now: datetime, warnings: list[str]) -> tuple[dict, dict]:
    path = runtime / "runtime-preflight.json"
    if not path.exists():
        path = runtime / "preflight.json"
    payload = read_json(path, warnings)
    stamp = _instant(payload.get("generated_at"))
    if stamp is None and path.exists():
        stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    age = (now - stamp).total_seconds() if stamp else None
    return payload, {
        "path": str(path),
        "generated_at": stamp.isoformat() if stamp else None,
        "age_seconds": age,
        "fresh": age is not None and -60 <= age <= 600,
    }


def _state(evidence: dict, now: datetime, stall_seconds: int) -> tuple[str, str]:
    marker, quota = evidence["marker"], evidence["quota"]
    if evidence["marker_exists"] or evidence["blocked_by_audit"]:
        return "audit_blocked", f"persistent semantic audit block: {marker.get('acceptance', '')}"
    if (
        not evidence["provenance"]["fresh"]
        or quota.get("quota_date") != evidence["day"]
        or evidence["warnings"]
    ):
        return "unknown_stale_evidence", "missing, invalid, expired, or wrong-day evidence"
    if quota.get("closed_reason") or any(
        quota.get(k) == 0 for k in ("available_requests", "available_rows")
    ):
        return "quota_wait", str(quota.get("closed_reason") or "daily quota exhausted")
    if evidence["free"] < evidence["min_free"]:
        return "storage_wait", "free storage below configured minimum"
    return _activity_state(evidence, now, stall_seconds)


def _activity_state(evidence: dict, now: datetime, stall_seconds: int) -> tuple[str, str]:
    exit_record = evidence["exit"]
    at = _instant(exit_record.get("at"))
    failed = exit_record.get("service_result") not in (None, "success", "exec-condition")
    if failed and at and 0 <= (now - at).total_seconds() < 600:
        if not exit_record.get("planned_restart"):
            return "cooldown", "recent failed service exit"
    last = _instant(evidence["runnable_since"])
    if (
        last
        and evidence["active"]
        and evidence["runnable_samples"] >= 3
        and (now - last).total_seconds() >= max(900, stall_seconds)
    ):
        return "stalled", "no effective progress across at least three observations"
    if evidence["active"]:
        return "running", "canonical writer lock held"
    return "idle", "canonical writer lock free"


def _previous(value: object, warnings: list[str]) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        warnings.append("invalid previous snapshot")
        return {}
    value = dict(value)
    if not isinstance(value.get("quota", {}), dict):
        warnings.append("invalid previous quota")
        value["quota"] = {}
    if not isinstance(value.get("progress", {}), dict):
        warnings.append("invalid previous progress")
        value["progress"] = {}
    for key in ("samples", "runnable_samples"):
        if type(value.get(key, 0)) is not int or value.get(key, 0) < 0:
            warnings.append(f"invalid previous {key}")
            value[key] = 0
    read_warnings = value.get("snapshot_read_warnings", [])
    if isinstance(read_warnings, list) and all(isinstance(item, str) for item in read_warnings):
        warnings.extend(read_warnings)
    else:
        warnings.append("invalid snapshot warnings")
    return value


def _progress_time(
    progress: dict, previous: dict, day: str, now: datetime, warnings: list[str]
) -> tuple[str, bool]:
    prior = previous.get("progress", {})
    increased = not previous
    same_day = previous.get("quota", {}).get("quota_date") == day
    for key in ("settled_dates", "independently_audited_dates", "returned_rows_today"):
        if key == "returned_rows_today" and not same_day:
            continue
        old, new = prior.get(key), progress.get(key)
        if type(old) is int and type(new) is int:
            increased |= new > old
            expired_audit = (
                key == "independently_audited_dates"
                and progress.get("expired_audit_bindings", 0) > 0
            )
            if new < old and not expired_audit:
                warnings.append(f"progress count decreased: {key}")
    old_min, new_min = prior.get("current_min_date"), progress.get("current_min_date")
    increased |= isinstance(old_min, str) and isinstance(new_min, str) and new_min < old_min
    return (
        now.isoformat() if increased else previous.get("last_progress_at", now.isoformat()),
        bool(increased),
    )


def _runnable(previous: dict, active: bool, changed: bool, now: datetime) -> tuple[str, int]:
    continuing = active and previous.get("state") in {"running", "stalled"}
    count = previous.get("runnable_samples", 0) + 1 if continuing else 1
    since = previous.get("runnable_since") if continuing and not changed else None
    return since or now.isoformat(), count if active else 0


def history_status(  # noqa: PLR0913 - public monitoring interface
    root: Path,
    runtime_root: Path,
    *,
    previous: dict | None = None,
    now: datetime | None = None,
    min_free_bytes: int = _MIN_FREE,
    stall_seconds: int = 900,
) -> dict[str, Any]:
    """Observe metadata, lock and quota ledger without creating or modifying files."""
    now = now or datetime.now(UTC)
    warnings: list[str] = []
    previous = _previous(previous, warnings)
    preflight, provenance = _preflight(runtime_root, now, warnings)
    quota = _quota(preflight)
    available = [quota[key] for key in ("available_requests", "available_rows") if key in quota]
    if not available or any(type(value) is not int or value < 0 for value in available):
        warnings.append("missing or invalid quota availability")
    if "blocked_by_audit" in preflight and type(preflight["blocked_by_audit"]) is not bool:
        warnings.append("invalid blocked_by_audit flag")
    day = now.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    progress = inventory(root, warnings, runtime_root)
    progress["returned_rows_today"] = _returned_rows(root, day, warnings)
    last, changed = _progress_time(progress, previous, day, now, warnings)
    marker_path = runtime_root / "semantic-audit-blocked.json"
    marker = read_json(marker_path, warnings)
    from quant_market_data_platform.minute_semantic_quarantine import quarantine_status

    quarantine = quarantine_status(root, marker_path)
    warnings.extend(quarantine["errors"])
    progress["semantic_quarantine_dates"] = quarantine["dates"]
    progress["semantic_quarantine_count"] = len(quarantine["dates"])
    exit_record = read_json(runtime_root / "last-main-exit.json", warnings)
    active = _writer_active(root, warnings)
    since, runnable_samples = _runnable(previous, active, changed, now)
    free = preflight.get("free_disk_bytes", shutil.disk_usage(root).free)
    if type(free) is not int or free < 0:
        warnings.append("invalid free_disk_bytes")
        free = 0
    evidence = {
        "marker": marker,
        "blocked_by_audit": (
            preflight.get("blocked_by_audit") is True and not quarantine["marker_quarantined"]
        ),
        "runnable_since": since,
        "runnable_samples": runnable_samples,
        "marker_exists": quarantine["blocked"],
        "quota": quota,
        "provenance": provenance,
        "day": day,
        "warnings": warnings,
        "free": free,
        "min_free": min_free_bytes,
        "exit": exit_record,
        "last_progress": last,
        "samples": int(previous.get("samples", 0)) + 1,
        "active": active,
    }
    marker_complete = read_json(runtime_root / "download-complete.json", warnings)
    state, reason = _state(evidence, now, stall_seconds)
    return {
        "state": state,
        "reason": reason,
        "generated_at": now.isoformat(),
        "progress": progress,
        "quota": quota,
        "last_progress_at": last,
        "samples": evidence["samples"],
        "runnable_since": since if state in {"running", "stalled"} else None,
        "runnable_samples": runnable_samples if state in {"running", "stalled"} else 0,
        "provenance": provenance,
        "writer_active": active,
        "free_disk_bytes": free,
        "evidence_warnings": warnings,
        "download_completion": marker_complete.get("status"),
        "writes_production": False,
    }


def watch_history(  # noqa: PLR0913 - public monitoring interface
    root: Path,
    runtime_root: Path,
    state_root: Path,
    *,
    now: datetime | None = None,
    min_free_bytes: int = _MIN_FREE,
    stall_seconds: int = 900,
) -> dict[str, Any]:
    """Persist observations and deduplicated local alerts only in ``state_root``."""
    warnings: list[str] = []
    previous = read_json(state_root / "status.json", warnings)
    previous["snapshot_read_warnings"] = warnings
    result = history_status(
        root,
        runtime_root,
        previous=previous,
        now=now,
        min_free_bytes=min_free_bytes,
        stall_seconds=stall_seconds,
    )
    critical = result["state"] in {"audit_blocked", "stalled", "unknown_stale_evidence"}
    identity = f"{result['state']}:{result['reason']}" if critical else None
    pending = result["progress"]["semantic_quarantine_dates"]
    if not critical and pending:
        identity = "semantic_quarantine:" + ",".join(pending)
    old = previous.get("active_alert")
    result["active_alert"] = identity
    state_root.mkdir(parents=True, exist_ok=True)
    events = []
    if old and old != identity:
        events.append({"event": "resolved", "identity": old})
    if identity and old != identity:
        events.append(
            {
                "event": "opened",
                "identity": identity,
                "severity": "critical" if critical else "warning",
            }
        )
    if events:
        with (state_root / "alerts.jsonl").open("a") as handle:
            for event in events:
                handle.write(json.dumps({**event, "at": result["generated_at"]}) + "\n")
    temporary = state_root / "status.json.tmp"
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(state_root / "status.json")
    return result
