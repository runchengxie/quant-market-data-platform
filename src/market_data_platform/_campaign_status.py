"""Read-only campaign status view derived from checkpoints and the ledger."""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform._campaign_common import (
    SCHEMA_VERSION,
    _build_run_window,
    _load_ledger,
    _now,
    _read_json,
    _RunWindow,
)
from market_data_platform._campaign_readiness import (
    _checkpoint_inventory,
    _readiness_path,
    _validated_readiness,
)


def _runner():
    return sys.modules["market_data_platform.tushare_minute_replacement_campaign_runner"]


def _recent_throughput_samples(events: Sequence[Any]) -> list[float]:
    samples: list[float] = []
    started_at: datetime | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        status = event.get("status")
        if status == "budgeted_run_started":
            try:
                started_at = datetime.fromisoformat(str(event["at"]))
            except (KeyError, ValueError):
                started_at = None
            continue
        if status != "budgeted_run_finished":
            continue
        rows = int(event.get("new_rows", 0))
        elapsed = float(event.get("elapsed_seconds", 0))
        if elapsed <= 0 and started_at is not None:
            try:
                finished_at = datetime.fromisoformat(str(event["at"]))
                elapsed = (finished_at.astimezone(UTC) - started_at.astimezone(UTC)).total_seconds()
            except (KeyError, ValueError):
                elapsed = 0
        started_at = None
        if rows > 0 and elapsed > 0:
            samples.append(rows / elapsed * 3600)
    return samples[-7:]


def _status_eta(inventory: dict[str, Any], events: Sequence[Any]) -> dict[str, Any]:
    throughputs = _recent_throughput_samples(events)
    throughput = statistics.median(throughputs) if throughputs else None
    estimated_rows = inventory.get("estimated_remaining_rows")
    if throughput and isinstance(estimated_rows, int) and estimated_rows > 0:
        hours = estimated_rows / throughput
        return {
            "available": True,
            "hours_low": round(hours / 1.25, 1),
            "hours_base": round(hours, 1),
            "hours_high": round(hours / 0.8, 1),
            "recent_median_rows_per_hour": round(throughput),
            "samples": len(throughputs),
        }
    if not throughputs:
        return {
            "available": False,
            "reason": "no completed run has positive rows and a measurable duration",
            "samples": 0,
        }
    return {
        "available": False,
        "reason": "remaining rows cannot yet be estimated from persisted checkpoints",
        "samples": len(throughputs),
    }


def _heartbeat_age(active_run: dict[str, Any]) -> float | None:
    try:
        heartbeat_at = datetime.fromisoformat(str(active_run["heartbeat_at"]))
    except (KeyError, ValueError):
        return None
    return (datetime.now(UTC) - heartbeat_at.astimezone(UTC)).total_seconds()


def _status_health(
    manifest: dict[str, Any],
    ledger: dict[str, Any],
    last_event: Any,
    *,
    ready: bool,
) -> tuple[dict[str, Any], Any]:
    health = dict(ledger.get("health", {}))
    active_run = ledger.get("active_run")
    lock_held = _runner()._lock_is_held(Path(manifest["lock_path"]))
    heartbeat_age: float | None = None
    reason: str | None = None
    if ready:
        state = "complete"
    elif isinstance(active_run, dict):
        heartbeat_age = _heartbeat_age(active_run)
        if not lock_held:
            state, reason = "stalled", "active_run_without_lock"
        elif heartbeat_age is None:
            state, reason = "stalled", "active_run_has_invalid_heartbeat"
        elif heartbeat_age > 900:
            state, reason = "stalled", "active_run_heartbeat_stale"
        else:
            state = "running"
    elif lock_held:
        state, reason = "stalled", "lock_held_without_active_run"
        active_run = {
            "legacy_lock_held": True,
            "started_at": (last_event.get("at") if isinstance(last_event, dict) else None),
        }
    else:
        state = str(health.get("state", "idle"))
        reason = health.get("last_reason")
    return (
        {
            "state": state,
            "no_progress_streak": int(health.get("no_progress_streak", 0)),
            "last_progress_at": health.get("last_progress_at"),
            "heartbeat_age_seconds": heartbeat_age,
            "lock_held": lock_held,
            "reason": reason,
        },
        active_run,
    )


def _status_run_window(args: Any, *, timezone_name: str) -> _RunWindow:
    window_args = type(
        "StatusWindowArgs",
        (),
        {
            "run_window_start": getattr(args, "run_window_start", "00:45"),
            "run_window_drain": getattr(args, "run_window_drain", "04:15"),
            "run_window_stop": getattr(args, "run_window_stop", "04:20"),
        },
    )()
    run_window = _build_run_window(window_args, timezone_name=timezone_name)
    assert run_window is not None
    return run_window


def _campaign_status_payload(
    args: Any,
    manifest_path: Path,
    manifest: dict[str, Any],
    ledger: dict[str, Any],
) -> dict[str, Any]:
    inventory = _checkpoint_inventory(manifest)
    timezone_name = str(getattr(args, "quota_timezone", "Asia/Shanghai"))
    quota_date, _seconds_until_reset = _runner()._quota_window_clock(timezone_name)
    token_env = str(manifest["config"]["token_env"])
    quota_key = f"{quota_date}:{token_env}"
    quota_window = ledger.get("quota_windows", {}).get(quota_key, {})
    consumed = int(quota_window.get("high_water_new_rows", 0))
    max_new_rows = int(getattr(args, "max_new_rows", 67_000_000))
    events = ledger.get("events", [])
    last_event = events[-1] if events else None
    readiness = _validated_readiness(manifest_path, manifest)
    health_payload, active_run = _status_health(
        manifest,
        ledger,
        last_event,
        ready=readiness is not None,
    )
    health = dict(ledger.get("health", {}))
    return {
        "schema_version": "tushare.minute_replacement_campaign.status.v1",
        "generated_at": _now(),
        "manifest_path": str(manifest_path),
        "health": health_payload,
        "progress": inventory,
        "quota": {
            "kind": "campaign_local_soft_limit",
            "quota_date": quota_date,
            "token_env": token_env,
            "max_new_rows": max_new_rows,
            "consumed_rows": consumed,
            "remaining_rows": max(0, max_new_rows - consumed),
        },
        "active_run": active_run,
        "last_reason": health.get("last_reason")
        or (last_event.get("stop_reason") if isinstance(last_event, dict) else None)
        or (last_event.get("status") if isinstance(last_event, dict) else None),
        "last_event": last_event,
        "next_window": (
            _status_run_window(args, timezone_name=timezone_name).next_start().isoformat()
        ),
        "scheduler_note": (
            "next_window is the next daily safety-window start; opportunity ticks "
            "inside the window are reported by systemctl list-timers"
        ),
        "eta": _status_eta(inventory, events),
        "readiness_path": (
            str(_readiness_path(manifest_path, manifest)) if readiness is not None else None
        ),
        "promotion_ready": False,
        "cutover_performed": False,
    }


def _print_campaign_status(payload: dict[str, Any]) -> None:
    progress_payload = payload["progress"]
    quota_payload = payload["quota"]
    eta = payload["eta"]
    print(f"health: {payload['health']['state']}; last_reason: {payload['last_reason'] or '-'}")
    print(
        "dates: "
        f"complete={progress_payload['complete_dates']}/"
        f"{progress_payload['target_dates']} "
        f"partial={progress_payload['partial_dates']} "
        f"remaining={progress_payload['remaining_dates']}"
    )
    print(
        "campaign quota: "
        f"used={quota_payload['consumed_rows']:,}/"
        f"{quota_payload['max_new_rows']:,} "
        f"remaining={quota_payload['remaining_rows']:,}"
    )
    if progress_payload.get("next"):
        print(f"next: {json.dumps(progress_payload['next'], ensure_ascii=False)}")
    print(f"next window: {payload['next_window']}")
    if eta.get("available") is True:
        print(
            "ETA: "
            f"{eta['hours_low']}-{eta['hours_high']} hours "
            f"(median {eta['recent_median_rows_per_hour']:,} rows/hour)"
        )
    else:
        print(f"ETA: unavailable ({eta['reason']})")


def campaign_status(args: Any) -> int:
    """Print a read-only operational view derived from checkpoints and the ledger."""
    manifest_path = args.manifest.expanduser().resolve()
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported campaign manifest: {manifest_path}")
    ledger = _load_ledger(Path(manifest["ledger_path"]), manifest_path)
    payload = _campaign_status_payload(args, manifest_path, manifest, ledger)
    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_campaign_status(payload)
    return 0


__all__ = [
    "_campaign_status_payload",
    "_heartbeat_age",
    "_print_campaign_status",
    "_recent_throughput_samples",
    "_status_eta",
    "_status_health",
    "_status_run_window",
    "campaign_status",
]
