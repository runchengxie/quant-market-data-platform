"""Receipt loading and phase-outcome validation for the campaign runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from market_data_platform._campaign_common import (
    COMPLETENESS_FILENAME,
    EXPECTED_CHECKPOINT_EXIT_CODES,
    CampaignFatalError,
    CampaignRetryableError,
    _PhaseRunResult,
    _read_json,
    validate_complete_minute_partition,
)


def _runner():
    return sys.modules["market_data_platform.tushare_minute_replacement_campaign_runner"]


def _validated_date(data_root: Path, trade_date: str) -> dict[str, Any] | None:
    sidecar = data_root / f"trade_date={trade_date}" / COMPLETENESS_FILENAME
    if not sidecar.exists():
        return None
    payload = _read_json(sidecar)
    if payload.get("status") != "complete":
        return None
    return validate_complete_minute_partition(
        sidecar.parent,
        trade_date=trade_date,
        require_full_universe=True,
    )


def _validated_acquisition_date(data_root: Path, trade_date: str) -> dict[str, Any] | None:
    """Validate an acquired partition without granting full-universe promotion."""
    sidecar = data_root / f"trade_date={trade_date}" / COMPLETENESS_FILENAME
    if not sidecar.exists():
        return _runner()._validated_date(data_root, trade_date)
    payload = _read_json(sidecar)
    if payload.get("status") != "complete":
        return None
    if "provider_no_data_exceptions=" not in str(payload.get("universe_rule", "")):
        return _runner()._validated_date(data_root, trade_date)
    return validate_complete_minute_partition(
        sidecar.parent,
        trade_date=trade_date,
        require_full_universe=False,
    )


def _date_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "rows": receipt["rows"],
        "symbols": receipt["symbols"],
        "partition_sha256": receipt["partition_sha256"],
        "sidecar_sha256": receipt["sidecar_sha256"],
        "universe_rule": receipt["universe_rule"],
        "market_symbol_counts": receipt["market_symbol_counts"],
    }


def _receipt_output_is_staging_only(receipt: dict[str, Any], *, immutable: bool) -> bool:
    output = receipt.get("output")
    if not isinstance(output, dict):
        return not immutable
    return output.get("writes_production") is False


def _load_lane_receipt(
    lane_name: str, lane: dict[str, Any], *, required: bool
) -> dict[str, Any] | None:
    path = Path(str(lane["receipt_path"]))
    if not path.exists():
        if required:
            raise CampaignFatalError(f"Lane {lane_name} produced no receipt: {path}")
        return None
    try:
        receipt = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CampaignFatalError(f"Lane {lane_name} receipt is unreadable: {path}") from exc
    if not isinstance(receipt, dict):
        raise CampaignFatalError(f"Lane {lane_name} receipt must contain an object: {path}")
    expected_plan_id = lane.get("plan_id")
    if expected_plan_id is not None and receipt.get("plan_id") != expected_plan_id:
        raise CampaignFatalError(
            f"Lane {lane_name} receipt does not match its immutable plan: {path}"
        )
    if not _receipt_output_is_staging_only(receipt, immutable=expected_plan_id is not None):
        raise CampaignFatalError(f"Lane {lane_name} receipt is not staging-only: {path}")
    violations = [
        str(segment.get("segment_id", "unknown"))
        for segment in receipt.get("segments", [])
        if isinstance(segment, dict) and segment.get("budget_violation") is True
    ]
    if violations:
        raise CampaignFatalError(
            f"Lane {lane_name} receipt contains request-budget violations: {violations}"
        )
    if receipt.get("status") == "failed":
        raise CampaignFatalError(f"Lane {lane_name} receipt is failed: {path}")
    return receipt


def _shared_quota_stop_reason(receipt: dict[str, Any]) -> str | None:
    quota_errors = {"MinuteQuotaExceeded", "MinuteQuotaPoolClosed"}
    for segment in receipt.get("segments", []):
        if not isinstance(segment, dict):
            continue
        error = segment.get("error")
        if isinstance(error, dict) and error.get("type") in quota_errors:
            return "shared_quota_exhausted"
    return None


def _partition_shared_quota_stop_reason(data_root: Path, trade_date: str) -> str | None:
    sidecar = data_root / f"trade_date={trade_date}" / COMPLETENESS_FILENAME
    if not sidecar.exists():
        return None
    payload = _read_json(sidecar)
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("type") in {
        "MinuteQuotaExceeded",
        "MinuteQuotaPoolClosed",
    }:
        return "shared_quota_exhausted"
    return None


def _validate_phase_outcome(
    phase: dict[str, Any],
    run_result: _PhaseRunResult,
    *,
    sidecars_complete: bool,
) -> str | None:
    expected_checkpoint = run_result.stop_reason in {
        "runtime",
        "row_budget",
        "schedule_drain",
        "schedule_hard_stop",
    }
    evidence_stop_reason: str | None = None
    intentional = set(run_result.intentional_checkpoint_lanes)
    for lane_name, exit_code in run_result.exit_codes.items():
        lane = phase["lanes"][lane_name]
        checkpoint_expected_for_lane = expected_checkpoint or lane_name in intentional
        reason = _validate_one_lane_outcome(
            lane_name,
            lane,
            exit_code,
            receipt=_load_lane_receipt(
                lane_name,
                lane,
                required=(lane.get("plan_id") is not None and not checkpoint_expected_for_lane),
            ),
            checkpoint_expected_for_lane=checkpoint_expected_for_lane,
            sidecars_complete=sidecars_complete,
            stop_reason=run_result.stop_reason,
        )
        if reason is not None:
            evidence_stop_reason = reason
    return evidence_stop_reason


def _validate_one_lane_outcome(  # noqa: PLR0913
    lane_name: str,
    lane: dict[str, Any],
    exit_code: int,
    *,
    receipt: dict[str, Any] | None,
    checkpoint_expected_for_lane: bool,
    sidecars_complete: bool,
    stop_reason: str | None,
) -> str | None:
    if receipt is None:
        if checkpoint_expected_for_lane and lane.get("plan_id") is not None:
            raise CampaignRetryableError(
                f"Lane {lane_name} has no receipt after expected checkpoint"
            )
        if checkpoint_expected_for_lane and exit_code in EXPECTED_CHECKPOINT_EXIT_CODES:
            return None
        if exit_code != 0:
            raise CampaignRetryableError(
                f"Legacy lane {lane_name} exited before completing its phase: exit_code={exit_code}"
            )
        return None
    lane_sidecars_complete = sidecars_complete
    if "data_root" in lane and "dates" in lane:
        lane_sidecars_complete, _lane_receipts = _phase_receipts({"lanes": {lane_name: lane}})
    return _validate_lane_receipt_present(
        lane_name,
        receipt,
        exit_code,
        checkpoint_expected_for_lane=checkpoint_expected_for_lane,
        lane_sidecars_complete=lane_sidecars_complete,
        stop_reason=stop_reason,
    )


def _validate_lane_receipt_present(  # noqa: PLR0913
    lane_name: str,
    receipt: dict[str, Any],
    exit_code: int,
    *,
    checkpoint_expected_for_lane: bool,
    lane_sidecars_complete: bool,
    stop_reason: str | None,
) -> str | None:
    if checkpoint_expected_for_lane:
        if exit_code not in EXPECTED_CHECKPOINT_EXIT_CODES:
            raise CampaignRetryableError(
                f"Lane {lane_name} did not checkpoint cleanly after "
                f"{stop_reason or 'single_lane_drain'}: exit_code={exit_code}"
            )
        if receipt.get("status") not in {"complete", "partial", "interrupted"}:
            raise CampaignRetryableError(
                f"Lane {lane_name} has unexpected checkpoint receipt status: "
                f"{receipt.get('status')}"
            )
        return None
    if exit_code != 0:
        shared_quota_reason = _shared_quota_stop_reason(receipt)
        if shared_quota_reason is not None:
            return shared_quota_reason
        raise CampaignRetryableError(
            f"Lane {lane_name} exited before completing its phase: exit_code={exit_code}"
        )
    if not lane_sidecars_complete or receipt.get("status") != "complete":
        raise CampaignRetryableError(
            f"Lane {lane_name} returned without complete sidecars and receipt"
        )
    return None


def _validate_completed_phase_receipts(phase: dict[str, Any]) -> bool:
    """Prevent a complete sidecar from masking a failed immutable-plan receipt."""
    complete = True
    for lane_name, lane in phase["lanes"].items():
        receipt = _load_lane_receipt(lane_name, lane, required=False)
        if receipt is not None and receipt.get("status") != "complete":
            complete = False
    return complete


def _phase_receipts(phase: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result: dict[str, Any] = {}
    complete = True
    for lane_name, lane in phase["lanes"].items():
        dates: dict[str, Any] = {}
        data_root = Path(lane["data_root"])
        for trade_date in lane["dates"]:
            receipt = _runner()._validated_acquisition_date(data_root, trade_date)
            if receipt is None:
                complete = False
                dates[trade_date] = {"status": "incomplete"}
            else:
                dates[trade_date] = {"status": "complete", **_date_receipt(receipt)}
        result[lane_name] = {"dates": dates}
    if complete:
        complete = _validate_completed_phase_receipts(phase)
    return complete, result


__all__ = [
    "_date_receipt",
    "_load_lane_receipt",
    "_partition_shared_quota_stop_reason",
    "_phase_receipts",
    "_receipt_output_is_staging_only",
    "_shared_quota_stop_reason",
    "_validate_completed_phase_receipts",
    "_validate_phase_outcome",
    "_validated_date",
    "_validated_acquisition_date",
]
