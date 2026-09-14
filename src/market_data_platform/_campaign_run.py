"""Campaign run context, budgeting, finalization, and the top-level run driver."""

from __future__ import annotations

import fcntl
import json
import os
import socket
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from market_data_platform._campaign_advance import _advance_with_context
from market_data_platform._campaign_common import (
    FATAL_EXIT_CODE,
    NO_PROGRESS_REASONS,
    RETRYABLE_EXIT_CODE,
    SCHEMA_VERSION,
    CampaignAccountingError,
    CampaignFatalError,
    CampaignRetryableError,
    _AdvanceContext,
    _AdvanceControls,
    _AdvanceResult,
    _build_run_window,
    _FinishMeasurement,
    _load_ledger,
    _now,
    _read_json,
    _RunWindow,
    _write_ledger,
)
from market_data_platform._campaign_preflight import _resume_preflight
from market_data_platform._campaign_progress import (
    _CampaignProgress,
)
from market_data_platform._campaign_readiness import (
    _checkpoint_inventory,
    _validated_readiness,
    _write_readiness_marker,
)


def _runner():
    return sys.modules["market_data_platform.tushare_minute_replacement_campaign_runner"]


@dataclass
class _CampaignRunContext:
    args: Any
    budgeted: bool
    manifest_path: Path
    manifest: dict[str, Any]
    ledger_path: Path
    ledger: dict[str, Any]
    no_progress_limit: int
    allow_stalled_retry: bool
    progress: _CampaignProgress | None = None
    quota_window: dict[str, Any] | None = None
    quota_window_key: str | None = None
    poll_seconds: float = 10.0
    interrupt_grace_seconds: float = 300.0
    quota_timezone: str = "Asia/Shanghai"
    run_window: _RunWindow | None = None
    final_reason: str = "single_day_complete"
    run_id: str = ""
    started_monotonic: float = 0.0
    started_at: str = ""
    heartbeat_seconds: float = 300.0
    single_lane_threshold_rows: int = 0
    limits: dict[str, Any] = field(default_factory=dict)
    starting_inventory: dict[str, Any] | None = None
    last_heartbeat_monotonic: float = 0.0

    def heartbeat(self, *, force: bool = False) -> None:
        if self.args.dry_run:
            return
        current = time.monotonic()
        if not force and current - self.last_heartbeat_monotonic < self.heartbeat_seconds:
            return
        if self.progress is not None:
            self.progress.refresh()
        active = self.ledger.get("active_run")
        if not isinstance(active, dict) or active.get("run_id") != self.run_id:
            raise CampaignAccountingError("Campaign active_run heartbeat identity changed")
        active.update(
            {
                "heartbeat_at": _now(),
                "elapsed_seconds": max(0.0, current - self.started_monotonic),
                "new_rows": self.progress.new_rows if self.progress is not None else 0,
                "quota_window_new_rows": (
                    self.progress.quota_window_new_rows if self.progress is not None else None
                ),
            }
        )
        _write_ledger(self.ledger_path, self.ledger)
        self.last_heartbeat_monotonic = current


def _validate_quota_window_counters(
    baseline_rows: Any,
    high_water: Any,
    progress: _CampaignProgress,
    window_key: str,
) -> int:
    if (
        isinstance(baseline_rows, bool)
        or not isinstance(baseline_rows, int)
        or baseline_rows < 0
        or isinstance(high_water, bool)
        or not isinstance(high_water, int)
        or high_water < 0
    ):
        raise CampaignAccountingError(f"Invalid campaign quota window counters: {window_key}")
    observed_new_rows = progress.baseline_rows - baseline_rows
    if observed_new_rows < 0:
        raise CampaignAccountingError(
            f"Campaign rows moved below quota-window baseline: {window_key}"
        )
    return observed_new_rows


def _bind_quota_window(
    ledger: dict[str, Any],
    manifest: dict[str, Any],
    progress: _CampaignProgress,
    *,
    quota_date: str,
    mutate: bool,
) -> tuple[str, dict[str, Any]]:
    token_env = str(manifest["config"]["token_env"])
    window_key = f"{quota_date}:{token_env}"
    windows = ledger.get("quota_windows")
    if windows is None:
        windows = {}
        if mutate:
            ledger["quota_windows"] = windows
    if not isinstance(windows, dict):
        raise CampaignAccountingError("Campaign quota_windows ledger field must be an object")
    existing = windows.get(window_key)
    if existing is None:
        window: dict[str, Any] = {
            "quota_date": quota_date,
            "token_env": token_env,
            "baseline_rows": progress.baseline_rows,
            "high_water_new_rows": 0,
            "created_at": _now(),
        }
        if mutate:
            windows[window_key] = window
    else:
        if not isinstance(existing, dict):
            raise CampaignAccountingError(f"Invalid campaign quota window: {window_key}")
        window = existing
        if window.get("quota_date") != quota_date or window.get("token_env") != token_env:
            raise CampaignAccountingError(f"Campaign quota window identity mismatch: {window_key}")
    baseline_rows = window.get("baseline_rows")
    high_water = window.get("high_water_new_rows", 0)
    observed_new_rows = _validate_quota_window_counters(
        baseline_rows, high_water, progress, window_key
    )
    progress.carry_forward(max(high_water, observed_new_rows))
    if mutate:
        window["high_water_new_rows"] = progress.carried_new_rows
        window["updated_at"] = _now()
    return window_key, window


def _configure_run_window(context: _CampaignRunContext) -> int | None:
    context.quota_timezone = str(getattr(context.args, "quota_timezone", "Asia/Shanghai"))
    context.heartbeat_seconds = float(getattr(context.args, "heartbeat_seconds", 300.0))
    context.single_lane_threshold_rows = int(getattr(context.args, "single_lane_threshold_rows", 0))
    if context.heartbeat_seconds <= 0:
        raise ValueError("heartbeat_seconds must be greater than 0")
    if context.single_lane_threshold_rows < 0:
        raise ValueError("single_lane_threshold_rows must be non-negative")
    context.run_window = (
        _build_run_window(context.args, timezone_name=context.quota_timezone)
        if context.budgeted
        else None
    )
    if context.run_window is None:
        return None
    window_state = context.run_window.start_state()
    if window_state is None:
        return None
    if not context.args.dry_run:
        context.ledger["events"].append(
            {
                "at": _now(),
                "status": "budgeted_run_skipped",
                "stop_reason": window_state,
                "next_window": context.run_window.next_start().isoformat(),
            }
        )
        _write_ledger(context.ledger_path, context.ledger)
    print(f"campaign run skipped: {window_state}")
    return 0


def _no_progress_fuse_gate(context: _CampaignRunContext) -> int | None:
    streak = int(context.ledger.get("health", {}).get("no_progress_streak", 0))
    if not context.budgeted or streak < context.no_progress_limit or context.allow_stalled_retry:
        return None
    if not context.args.dry_run:
        context.ledger["events"].append(
            {
                "at": _now(),
                "status": "no_progress_fuse",
                "no_progress_streak": streak,
            }
        )
        context.ledger["health"] = {
            "state": "stalled",
            "no_progress_streak": streak,
            "updated_at": _now(),
        }
        _write_ledger(context.ledger_path, context.ledger)
    print(
        "campaign stalled after consecutive no-progress runs; "
        "inspect status before using --allow-stalled-retry"
    )
    return RETRYABLE_EXIT_CODE


def _configure_budget(context: _CampaignRunContext) -> None:
    if not context.budgeted:
        return
    context.poll_seconds = float(context.args.poll_seconds)
    context.interrupt_grace_seconds = float(context.args.interrupt_grace_seconds)
    if context.poll_seconds <= 0:
        raise ValueError("poll_seconds must be greater than 0")
    if context.interrupt_grace_seconds < 0:
        raise ValueError("interrupt_grace_seconds must be non-negative")
    reset_guard_seconds = float(getattr(context.args, "quota_reset_guard_seconds", 1200.0))
    if reset_guard_seconds < 0:
        raise ValueError("quota_reset_guard_seconds must be non-negative")
    quota_date, seconds_until_reset = _runner()._quota_window_clock(context.quota_timezone)
    requested_runtime_seconds = float(context.args.max_runtime_seconds)
    effective_runtime_seconds = min(
        requested_runtime_seconds,
        max(0.0, seconds_until_reset - reset_guard_seconds),
    )
    context.progress = _CampaignProgress(
        context.manifest,
        max_new_rows=int(context.args.max_new_rows),
        max_runtime_seconds=effective_runtime_seconds,
    )
    context.quota_window_key, context.quota_window = _bind_quota_window(
        context.ledger,
        context.manifest,
        context.progress,
        quota_date=quota_date,
        mutate=not context.args.dry_run,
    )
    context.limits = {
        "max_new_rows": context.progress.max_new_rows,
        "requested_max_runtime_seconds": requested_runtime_seconds,
        "effective_max_runtime_seconds": effective_runtime_seconds,
        "poll_seconds": context.poll_seconds,
        "interrupt_grace_seconds": context.interrupt_grace_seconds,
        "quota_timezone": context.quota_timezone,
        "quota_reset_guard_seconds": reset_guard_seconds,
        "quota_window": context.quota_window_key,
        "quota_window_new_rows_before": context.progress.carried_new_rows,
        "baseline_rows": context.progress.baseline_rows,
        "run_window_start": getattr(context.args, "run_window_start", None),
        "run_window_drain": getattr(context.args, "run_window_drain", None),
        "run_window_stop": getattr(context.args, "run_window_stop", None),
        "single_lane_threshold_rows": context.single_lane_threshold_rows,
    }
    print(json.dumps({"budgeted_run": context.limits}, ensure_ascii=False))


def _start_campaign_run(context: _CampaignRunContext) -> None:
    context.starting_inventory = _checkpoint_inventory(context.manifest)
    if context.args.dry_run:
        print(
            json.dumps(
                {"preflight": context.manifest["baseline"]["partial"]},
                ensure_ascii=False,
            )
        )
        return
    context.ledger["active_run"] = {
        "run_id": context.run_id,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": context.started_at,
        "heartbeat_at": context.started_at,
        "new_rows": 0,
    }
    context.ledger["events"].append(
        {
            "at": context.started_at,
            "status": "budgeted_run_started" if context.budgeted else "run_next_started",
            "run_id": context.run_id,
            **context.limits,
        }
    )
    _write_ledger(context.ledger_path, context.ledger)


def _finish_measurement(context: _CampaignRunContext) -> _FinishMeasurement:
    measurement_error: str | None = None
    if context.progress is not None:
        try:
            context.progress.refresh()
        except Exception as exc:
            measurement_error = f"{type(exc).__name__}: {exc}"
            context.final_reason = f"fatal:{type(exc).__name__}"
    if context.starting_inventory is None:
        raise CampaignAccountingError("Campaign starting inventory is unavailable")
    try:
        ending_inventory = _checkpoint_inventory(context.manifest)
    except Exception as exc:
        ending_inventory = context.starting_inventory
        measurement_error = measurement_error or f"{type(exc).__name__}: {exc}"
        context.final_reason = f"fatal:{type(exc).__name__}"
    return _FinishMeasurement(
        ending_inventory=ending_inventory,
        measurement_error=measurement_error,
        elapsed_seconds=max(0.0, time.monotonic() - context.started_monotonic),
        new_rows=context.progress.new_rows if context.progress is not None else 0,
    )


def _finished_event(
    context: _CampaignRunContext,
    measurement: _FinishMeasurement,
) -> dict[str, Any]:
    assert context.starting_inventory is not None
    event: dict[str, Any] = {
        "at": _now(),
        "status": "budgeted_run_finished" if context.budgeted else "run_next_finished",
        "run_id": context.run_id,
        "stop_reason": context.final_reason,
        "elapsed_seconds": measurement.elapsed_seconds,
        "new_rows": measurement.new_rows,
        "dates_completed": max(
            0,
            measurement.ending_inventory["complete_dates"]
            - context.starting_inventory["complete_dates"],
        ),
        "quota_window": context.quota_window_key,
        "quota_window_new_rows": (
            context.progress.quota_window_new_rows if context.progress is not None else None
        ),
    }
    if measurement.measurement_error is not None:
        event["measurement_error"] = measurement.measurement_error
    return event


def _update_quota_high_water(context: _CampaignRunContext) -> None:
    if context.quota_window is None or context.progress is None:
        return
    previous = context.quota_window.get("high_water_new_rows", 0)
    if not isinstance(previous, int) or isinstance(previous, bool):
        return
    context.quota_window["high_water_new_rows"] = max(
        previous,
        context.progress.quota_window_new_rows,
    )
    context.quota_window["updated_at"] = _now()


def _finished_health(context: _CampaignRunContext, *, new_rows: int) -> dict[str, Any]:
    streak = int(context.ledger.get("health", {}).get("no_progress_streak", 0))
    if new_rows > 0:
        streak = 0
    elif context.final_reason in NO_PROGRESS_REASONS:
        streak += 1
    state = "stalled" if streak >= context.no_progress_limit else "healthy"
    if context.final_reason.startswith("fatal:"):
        state = "fatal"
    elif context.final_reason == "retryable_lane_error" and state != "stalled":
        state = "degraded"
    return {
        "state": state,
        "no_progress_streak": streak,
        "last_reason": context.final_reason,
        "last_progress_at": (
            _now() if new_rows > 0 else context.ledger.get("health", {}).get("last_progress_at")
        ),
        "updated_at": _now(),
    }


def _finalize_campaign_run(context: _CampaignRunContext) -> None:
    if context.args.dry_run:
        return
    measurement = _finish_measurement(context)
    event = _finished_event(context, measurement)
    _update_quota_high_water(context)
    context.ledger["health"] = _finished_health(context, new_rows=measurement.new_rows)
    context.ledger.pop("active_run", None)
    context.ledger["events"].append(event)
    _write_ledger(context.ledger_path, context.ledger)
    if measurement.measurement_error is not None:
        raise CampaignFatalError(
            f"Campaign final accounting failed: {measurement.measurement_error}"
        )
    if context.final_reason == "campaign_complete":
        _write_readiness_marker(
            context.manifest_path,
            context.manifest,
            context.ledger_path,
        )


def _campaign_advance_context(context: _CampaignRunContext) -> _AdvanceContext:
    run_window = context.run_window
    controls = _AdvanceControls(
        dry_run=bool(context.args.dry_run),
        progress=context.progress,
        poll_seconds=context.poll_seconds,
        interrupt_grace_seconds=context.interrupt_grace_seconds,
        extra_stop_check=run_window.stop_reason if run_window is not None else None,
        heartbeat=context.heartbeat,
        hard_stop_check=run_window.hard_stop_due if run_window is not None else None,
        single_lane_threshold_rows=context.single_lane_threshold_rows,
    )
    return _AdvanceContext(
        context.manifest,
        context.ledger,
        context.ledger_path,
        controls,
    )


def _runtime_campaign_config(config: dict[str, Any], args: Any) -> dict[str, Any]:
    """Resolve deployment paths and overlay run-only controls on a copy."""

    runtime_config = dict(config)
    stable_root = os.environ.get("MDP_DIR", "").strip()
    if stable_root:
        root = Path(stable_root).expanduser().resolve()
        if root.is_dir():
            runtime_config["repo_root"] = str(root)
            runtime_config["marketdata_bin"] = str(root / ".venv/bin/marketdata")
    exception_path = os.environ.get("TUSHARE_MINUTE_PROVIDER_NO_DATA_EXCEPTIONS", "").strip()
    if exception_path:
        runtime_config["provider_no_data_exceptions_path"] = exception_path
    for key in (
        "minute_quota_mode",
        "minute_quota_db",
        "minute_quota_consumer",
        "minute_quota_limit_rows",
        "minute_quota_safety_rows",
        "minute_quota_gate",
        "minute_quota_limit_requests",
        "minute_quota_burst_limit_requests",
        "minute_quota_safety_requests",
        "minute_quota_allow_burst",
    ):
        value = getattr(args, key, None)
        if value is not None:
            runtime_config[key] = value
    ignored_blockers = {
        str(value).strip()
        for value in (getattr(args, "ignore_blocker_service", None) or [])
        if str(value).strip()
    }
    if ignored_blockers:
        runtime_config["blocker_services"] = [
            service
            for service in runtime_config.get("blocker_services", [])
            if str(service) not in ignored_blockers
        ]
    return runtime_config


def _check_campaign_blockers(  # noqa: PLR0913
    ledger: dict[str, Any],
    ledger_path: Path,
    args: Any,
    no_progress_limit: int,
    allow_stalled_retry: bool,
    blockers: list[str],
) -> int | None:
    if not blockers:
        return None
    if not args.dry_run:
        previous_streak = int(ledger.get("health", {}).get("no_progress_streak", 0))
        if previous_streak >= no_progress_limit and not allow_stalled_retry:
            ledger["events"].append(
                {
                    "at": _now(),
                    "status": "no_progress_fuse",
                    "no_progress_streak": previous_streak,
                }
            )
            ledger["health"] = {
                "state": "stalled",
                "no_progress_streak": previous_streak,
                "last_reason": "no_progress_fuse",
                "updated_at": _now(),
            }
            _write_ledger(ledger_path, ledger)
            print("campaign no-progress fuse remains active")
            return RETRYABLE_EXIT_CODE
        blocker_streak = previous_streak + 1
        ledger["events"].append(
            {
                "at": _now(),
                "status": "blocked_by_service",
                "services": blockers,
                "no_progress_streak": blocker_streak,
            }
        )
        ledger["health"] = {
            "state": ("stalled" if blocker_streak >= no_progress_limit else "degraded"),
            "no_progress_streak": blocker_streak,
            "last_reason": "blocked_by_service",
            "services": blockers,
            "updated_at": _now(),
        }
        _write_ledger(ledger_path, ledger)
    print(f"campaign blocked by active services: {', '.join(blockers)}")
    return RETRYABLE_EXIT_CODE


def _prepare_campaign_run(
    args: Any, budgeted: bool
) -> tuple[int | None, _CampaignRunContext | None]:
    manifest_path = args.manifest.expanduser().resolve()
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported campaign manifest: {manifest_path}")
    # Runtime controls deliberately do not mutate the immutable manifest.
    runtime_config = _runtime_campaign_config(manifest["config"], args)
    manifest = {**manifest, "config": runtime_config}
    lock_path = Path(str(manifest["lock_path"]))
    if args.dry_run:
        lock_target = lock_path if lock_path.exists() else manifest_path
        lock_mode = "r"
    else:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_target = lock_path
        lock_mode = "a+"
    with lock_target.open(lock_mode) as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("campaign lock is already held; retry later", file=sys.stderr)
            return RETRYABLE_EXIT_CODE, None
        ledger_path = Path(str(manifest["ledger_path"]))
        ledger = _load_ledger(ledger_path, manifest_path)
        if _validated_readiness(manifest_path, manifest) is not None:
            print("campaign acquisition is already complete; readiness marker verified")
            return 0, None
        no_progress_limit = int(getattr(args, "no_progress_limit", 2))
        if no_progress_limit < 1:
            raise ValueError("no_progress_limit must be at least 1")
        allow_stalled_retry = bool(getattr(args, "allow_stalled_retry", False))
        blockers = _runner()._active_blockers(manifest["config"]["blocker_services"])
        blocker_exit = _check_campaign_blockers(
            ledger, ledger_path, args, no_progress_limit, allow_stalled_retry, blockers
        )
        if blocker_exit is not None:
            return blocker_exit, None
        context = _CampaignRunContext(
            args=args,
            budgeted=budgeted,
            manifest_path=manifest_path,
            manifest=manifest,
            ledger_path=ledger_path,
            ledger=ledger,
            no_progress_limit=no_progress_limit,
            allow_stalled_retry=allow_stalled_retry,
            run_id=uuid.uuid4().hex,
            started_monotonic=time.monotonic(),
            started_at=_now(),
        )
        gate_status = _configure_run_window(context)
        if gate_status is not None:
            return gate_status, None
        gate_status = _no_progress_fuse_gate(context)
        if gate_status is not None:
            return gate_status, None
        _configure_budget(context)
        _start_campaign_run(context)
        return None, context


def _poll_campaign_stop(context: _CampaignRunContext) -> int | None:
    schedule_reason = context.run_window.stop_reason() if context.run_window is not None else None
    if schedule_reason is not None:
        context.final_reason = schedule_reason
        return 0
    if context.progress is not None:
        stop_reason = context.progress.stop_reason()
        if stop_reason is not None:
            context.final_reason = stop_reason
            print(
                f"budgeted campaign stopped: {stop_reason}; "
                f"new_rows={context.progress.new_rows:,}; "
                "quota_window_new_rows="
                f"{context.progress.quota_window_new_rows:,}"
            )
            return 0
    return None


def _dispatch_campaign_outcome(context: _CampaignRunContext, outcome: _AdvanceResult) -> int | None:
    if outcome.state == "campaign_complete":
        context.final_reason = "campaign_complete"
        if not context.args.dry_run:
            context.ledger.update({"status": "complete", "completed_at": _now()})
            _write_ledger(context.ledger_path, context.ledger)
        print("campaign complete; finalizing acquisition readiness")
        return 0
    if outcome.state == "stopped":
        context.final_reason = outcome.stop_reason or "stopped"
        if context.progress is None:
            print(f"campaign stopped: {context.final_reason}")
        else:
            print(
                f"budgeted campaign stopped: {context.final_reason}; "
                f"new_rows={context.progress.new_rows:,}; "
                "quota_window_new_rows="
                f"{context.progress.quota_window_new_rows:,}"
            )
        return 0
    if outcome.state == "phase_incomplete":
        context.final_reason = "phase_incomplete"
        return RETRYABLE_EXIT_CODE
    if outcome.state == "dry_run":
        context.final_reason = "dry_run"
        return 0
    if not context.budgeted:
        return 0
    return None


def _run_campaign(args: Any, *, budgeted: bool) -> int:
    prepared_exit, context = _prepare_campaign_run(args, budgeted)
    if prepared_exit is not None:
        return prepared_exit
    assert context is not None
    advance_context = _campaign_advance_context(context)
    try:
        context.heartbeat(force=True)
        preflight_ready, preflight_stop_reason = _resume_preflight(advance_context)
        if not preflight_ready:
            context.final_reason = preflight_stop_reason or "preflight_incomplete"
            return 0 if preflight_stop_reason is not None else RETRYABLE_EXIT_CODE
        while True:
            stop = _poll_campaign_stop(context)
            if stop is not None:
                return stop
            outcome = _advance_with_context(advance_context)
            dispatch = _dispatch_campaign_outcome(context, outcome)
            if dispatch is not None:
                return dispatch
    except KeyboardInterrupt:
        context.final_reason = "external_interrupt"
        raise
    except CampaignRetryableError as exc:
        context.final_reason = "retryable_lane_error"
        print(f"campaign retryable failure: {exc}")
        return RETRYABLE_EXIT_CODE
    except Exception as exc:
        context.final_reason = f"fatal:{type(exc).__name__}"
        print(f"campaign fatal failure: {type(exc).__name__}: {exc}")
        return FATAL_EXIT_CODE
    finally:
        _finalize_campaign_run(context)


def run_next(args: Any) -> int:
    """Advance at most one new campaign day while holding its lock."""
    try:
        return _run_campaign(args, budgeted=False)
    except (CampaignAccountingError, CampaignFatalError) as exc:
        print(f"campaign fatal failure: {exc}")
        return FATAL_EXIT_CODE


def run_budgeted(args: Any) -> int:
    """Advance across campaign days until a soft row or runtime limit is reached."""
    try:
        return _run_campaign(args, budgeted=True)
    except (CampaignAccountingError, CampaignFatalError) as exc:
        print(f"campaign fatal failure: {exc}")
        return FATAL_EXIT_CODE


__all__ = [
    "_CampaignRunContext",
    "_bind_quota_window",
    "_check_campaign_blockers",
    "_configure_budget",
    "_configure_run_window",
    "_dispatch_campaign_outcome",
    "_finalize_campaign_run",
    "_finish_measurement",
    "_finished_event",
    "_finished_health",
    "_no_progress_fuse_gate",
    "_poll_campaign_stop",
    "_prepare_campaign_run",
    "_runtime_campaign_config",
    "_start_campaign_run",
    "_update_quota_high_water",
    "run_budgeted",
    "run_next",
]
