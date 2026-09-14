"""Preflight (staging resume) orchestration for the campaign runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from market_data_platform._campaign_common import (
    EXPECTED_CHECKPOINT_EXIT_CODES,
    CampaignRetryableError,
    _AdvanceContext,
    _CommandRunOptions,
    _PhaseRunResult,
    _PreflightAttempt,
    _write_ledger,
)
from market_data_platform._campaign_progress import _partition_key
from market_data_platform._campaign_receipts import (
    _date_receipt,
    _partition_shared_quota_stop_reason,
)
from market_data_platform._campaign_supervisor import (
    _combined_stop_check,
    _resume_command,
    _run_commands,
)


def _runner():
    return sys.modules["market_data_platform.tushare_minute_replacement_campaign_runner"]


def _run_preflight_attempt(
    context: _AdvanceContext,
    trade_date: str,
    data_root: Path,
) -> tuple[_PreflightAttempt | None, str | None]:
    controls = context.controls
    active_keys = (_partition_key(data_root, trade_date),)
    stop_check = _combined_stop_check(controls, active_keys)
    stop_reason = stop_check() if stop_check is not None else None
    if stop_reason is not None:
        return None, stop_reason
    run_result = _run_commands(
        context.manifest,
        [("preflight", _resume_command(context.manifest, trade_date, str(data_root)))],
        options=_CommandRunOptions(
            stop_check=stop_check,
            poll_seconds=controls.poll_seconds,
            interrupt_grace_seconds=controls.interrupt_grace_seconds,
            heartbeat=controls.heartbeat,
            hard_stop_check=controls.hard_stop_check,
        ),
    )
    if controls.progress is not None:
        controls.progress.refresh(active_keys)
    return (
        _PreflightAttempt(
            trade_date=trade_date,
            data_root=data_root,
            receipt=_runner()._validated_date(data_root, trade_date),
            run_result=run_result,
        ),
        None,
    )


def _preflight_exit_reason(attempt: _PreflightAttempt) -> str | None:
    run_result = attempt.run_result
    exit_code = run_result.exit_codes.get("preflight")
    if run_result.stop_reason is not None:
        if exit_code not in {None, *EXPECTED_CHECKPOINT_EXIT_CODES}:
            raise CampaignRetryableError(
                "Preflight did not checkpoint cleanly after "
                f"{run_result.stop_reason}: exit_code={exit_code}"
            )
        return None
    if exit_code == 0:
        return None
    shared_quota_reason = _partition_shared_quota_stop_reason(
        attempt.data_root,
        attempt.trade_date,
    )
    if shared_quota_reason is not None:
        return shared_quota_reason
    raise CampaignRetryableError(
        f"Preflight exited before completing {attempt.trade_date}: exit_code={exit_code}"
    )


def _write_preflight_entry(
    context: _AdvanceContext,
    attempt: _PreflightAttempt,
) -> None:
    if attempt.receipt is None:
        entry = {
            "status": "partial",
            "exit_code": attempt.run_result.exit_codes.get("preflight"),
        }
    else:
        entry = {"status": "complete", **_date_receipt(attempt.receipt)}
    context.ledger["preflight"][attempt.trade_date] = entry
    _write_ledger(context.ledger_path, context.ledger)


def _finish_preflight_attempt(
    context: _AdvanceContext,
    attempt: _PreflightAttempt,
) -> tuple[bool, str | None]:
    exit_reason = _preflight_exit_reason(attempt)
    if exit_reason is not None:
        return False, exit_reason
    _write_preflight_entry(context, attempt)
    if attempt.receipt is None or attempt.run_result.stop_reason is not None:
        return False, attempt.run_result.stop_reason
    return True, None


def _record_completed_preflight(
    context: _AdvanceContext,
    trade_date: str,
    data_root: Path,
    receipt: dict[str, Any],
) -> None:
    _write_preflight_entry(
        context,
        _PreflightAttempt(
            trade_date=trade_date,
            data_root=data_root,
            receipt=receipt,
            run_result=_PhaseRunResult({"preflight": 0}),
        ),
    )


def _resume_preflight(context: _AdvanceContext) -> tuple[bool, str | None]:
    controls = context.controls
    for trade_date, item in sorted(context.manifest["baseline"]["partial"].items()):
        data_root = Path(item["data_root"])
        receipt = _runner()._validated_date(data_root, trade_date)
        if receipt is not None:
            if not controls.dry_run:
                _record_completed_preflight(context, trade_date, data_root, receipt)
            continue
        if controls.dry_run:
            command = _resume_command(context.manifest, trade_date, str(data_root))
            print(json.dumps({"resume": trade_date, "command": command}, ensure_ascii=False))
            return False, "dry_run"
        attempt, stop_reason = _run_preflight_attempt(context, trade_date, data_root)
        if attempt is None:
            return False, stop_reason
        completed, stop_reason = _finish_preflight_attempt(context, attempt)
        if not completed:
            return False, stop_reason
    return True, None


__all__ = [
    "_finish_preflight_attempt",
    "_preflight_exit_reason",
    "_record_completed_preflight",
    "_resume_preflight",
    "_run_preflight_attempt",
    "_write_preflight_entry",
]
