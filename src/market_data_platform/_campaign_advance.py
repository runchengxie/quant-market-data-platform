"""Day/phase advance orchestration for the campaign runner."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from market_data_platform._campaign_common import (
    _AdvanceContext,
    _AdvanceControls,
    _AdvanceResult,
    _now,
    _PhaseExecution,
    _write_ledger,
)
from market_data_platform._campaign_progress import (
    PartitionKey,
    _phase_partition_keys,
)
from market_data_platform._campaign_receipts import (
    _phase_receipts,
    _validate_phase_outcome,
)
from market_data_platform._campaign_supervisor import _combined_stop_check


def _runner():
    return sys.modules["market_data_platform.tushare_minute_replacement_campaign_runner"]


def _select_execution_phase(
    phase: dict[str, Any],
    controls: _AdvanceControls,
) -> tuple[dict[str, Any], bool]:
    progress = controls.progress
    if progress is None or controls.single_lane_threshold_rows <= 0:
        return phase, False
    soft_remaining = max(0, progress.max_new_rows - progress.quota_window_new_rows)
    pending_lanes = {
        lane_name: lane
        for lane_name, lane in sorted(phase["lanes"].items())
        if any(
            _runner()._validated_acquisition_date(Path(lane["data_root"]), trade_date) is None
            for trade_date in lane["dates"]
        )
    }
    if (
        soft_remaining > controls.single_lane_threshold_rows
        or not pending_lanes
        or len(phase["lanes"]) <= 1
    ):
        return phase, False
    selected_name = next(iter(pending_lanes))
    return {**phase, "lanes": {selected_name: pending_lanes[selected_name]}}, True


def _single_lane_stop_check(
    execution_phase: dict[str, Any],
    active_keys: tuple[PartitionKey, ...],
    controls: _AdvanceControls,
) -> Callable[[], bool] | None:
    progress = controls.progress
    if (
        progress is None
        or controls.single_lane_threshold_rows <= 0
        or len(execution_phase["lanes"]) <= 1
    ):
        return None

    def threshold_reached() -> bool:
        progress.refresh(active_keys)
        return (
            progress.max_new_rows - progress.quota_window_new_rows
            <= controls.single_lane_threshold_rows
        )

    return threshold_reached


def _phase_result_after_run(
    context: _AdvanceContext,
    execution: _PhaseExecution,
) -> _AdvanceResult | None:
    if context.controls.progress is not None:
        context.controls.progress.refresh(execution.active_keys)
    complete, receipts = _phase_receipts(execution.phase)
    execution_complete, _execution_receipts = _phase_receipts(execution.execution_phase)
    evidence_stop_reason = _validate_phase_outcome(
        execution.execution_phase,
        execution.run_result,
        sidecars_complete=execution_complete,
    )
    execution.day_ledger["phases"][execution.phase["name"]] = {
        "status": "complete" if complete else "partial",
        "exit_codes": execution.run_result.exit_codes,
        "execution_mode": "single_lane" if execution.single_lane_mode else "dual_lane",
        "intentional_checkpoint_lanes": list(execution.run_result.intentional_checkpoint_lanes),
        "lanes": receipts,
    }
    _write_ledger(context.ledger_path, context.ledger)
    if execution.run_result.stop_reason is not None:
        return _AdvanceResult(
            "stopped",
            day_key=execution.day_key,
            phase_name=execution.phase["name"],
            stop_reason=execution.run_result.stop_reason,
        )
    if evidence_stop_reason is not None:
        return _AdvanceResult(
            "stopped",
            day_key=execution.day_key,
            phase_name=execution.phase["name"],
            stop_reason=evidence_stop_reason,
        )
    if complete:
        return None
    if (
        execution.single_lane_mode and execution_complete
    ) or execution.run_result.intentional_checkpoint_lanes:
        return _AdvanceResult(
            "stopped",
            day_key=execution.day_key,
            phase_name=execution.phase["name"],
            stop_reason="single_lane_checkpoint",
        )
    return _AdvanceResult(
        "phase_incomplete",
        day_key=execution.day_key,
        phase_name=execution.phase["name"],
    )


def _run_pending_phase(
    context: _AdvanceContext,
    day_key: str,
    day_ledger: dict[str, Any],
    phase: dict[str, Any],
) -> _AdvanceResult | None:
    execution_phase, single_lane_mode = _select_execution_phase(phase, context.controls)
    active_keys = _phase_partition_keys(execution_phase)
    stop_check = _combined_stop_check(context.controls, active_keys)
    if stop_check is not None:
        stop_reason = stop_check()
        if stop_reason is not None:
            return _AdvanceResult(
                "stopped",
                day_key=day_key,
                phase_name=phase["name"],
                stop_reason=stop_reason,
            )
    run_result = _runner()._run_phase(
        context.manifest,
        execution_phase,
        dry_run=context.controls.dry_run,
        stop_check=stop_check,
        poll_seconds=context.controls.poll_seconds,
        interrupt_grace_seconds=context.controls.interrupt_grace_seconds,
        heartbeat=context.controls.heartbeat,
        hard_stop_check=context.controls.hard_stop_check,
        single_lane_check=_single_lane_stop_check(
            execution_phase,
            active_keys,
            context.controls,
        ),
    )
    if context.controls.dry_run:
        return _AdvanceResult("dry_run", day_key=day_key, phase_name=phase["name"])
    return _phase_result_after_run(
        context,
        _PhaseExecution(
            day_key=day_key,
            day_ledger=day_ledger,
            phase=phase,
            execution_phase=execution_phase,
            single_lane_mode=single_lane_mode,
            active_keys=active_keys,
            run_result=run_result,
        ),
    )


def _advance_with_context(context: _AdvanceContext) -> _AdvanceResult:
    for day in context.manifest["days"]:
        day_key = f"{int(day['day']):02d}"
        day_ledger = (
            context.ledger.get("days", {}).get(day_key, {"phases": {}})
            if context.controls.dry_run
            else context.ledger["days"].setdefault(day_key, {"phases": {}})
        )
        advanced = False
        for phase in day["phases"]:
            complete, receipts = _phase_receipts(phase)
            if complete:
                if not context.controls.dry_run:
                    day_ledger["phases"][phase["name"]] = {
                        "status": "complete",
                        "lanes": receipts,
                    }
                continue
            advanced = True
            result = _run_pending_phase(context, day_key, day_ledger, phase)
            if result is not None:
                return result
        if not context.controls.dry_run and day_ledger.get("status") != "complete":
            day_ledger.update({"status": "complete", "completed_at": _now()})
            _write_ledger(context.ledger_path, context.ledger)
        if advanced:
            print(f"campaign day {day_key} complete")
            return _AdvanceResult("day_complete", day_key=day_key)
    return _AdvanceResult("campaign_complete")


def _advance_one_day(
    manifest: dict[str, Any],
    ledger: dict[str, Any],
    ledger_path: Path,
    *,
    controls: _AdvanceControls | None = None,
    **legacy_controls: Any,
) -> _AdvanceResult:
    if controls is not None and legacy_controls:
        raise TypeError("Pass advance controls either as a context or keyword values, not both")
    resolved = controls or _AdvanceControls(**legacy_controls)
    return _advance_with_context(_AdvanceContext(manifest, ledger, ledger_path, resolved))


__all__ = [
    "_advance_one_day",
    "_advance_with_context",
    "_phase_result_after_run",
    "_run_pending_phase",
    "_select_execution_phase",
    "_single_lane_stop_check",
]
