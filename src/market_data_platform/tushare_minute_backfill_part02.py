"""Immutable planning and resumable execution for TuShare minute backfills."""

from __future__ import annotations

import fcntl
import json
import math
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from market_data_platform.providers.tushare_a_share import (
    _load_tushare_env_files,
    resolve_tushare_api_url,
)
from market_data_platform.providers.tushare_a_share_mins import (
    MinsMirrorOptions,
    MinuteMirrorIncompleteDatesError,
)
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
from market_data_platform.tushare_minute_backfill_part01 import (
    BSE_FIRST_TRADE_DATE,
    MINUTE_BACKFILL_PLAN_SCHEMA,
    MINUTE_BACKFILL_RECEIPT_SCHEMA,
    MinuteBackfillBudgetError,
    MinuteBackfillPlanOptions,
    MinuteBackfillRunOptions,
    _active_symbol_count,
    _atomic_write_json,
    _canonical_instrument_intervals,
    _endpoint_identifier,
    _expanded,
    _load_open_dates,
    _load_requested_dates,
    _MinuteBackfillExecution,
    _plan_id,
    _policy_from_payload,
    _policy_payload,
    _read_json,
    _segment_dates,
    _source_fingerprint,
    _utc_now,
    _validate_plan_options,
    _write_immutable_plan,
)


def build_minute_backfill_plan(options: MinuteBackfillPlanOptions) -> dict[str, Any]:
    """Build an offline plan from local calendar and instrument snapshots."""
    start_date, end_date = _validate_plan_options(options)
    trade_cal_path = _expanded(options.trade_cal_path)
    instruments_path = _expanded(options.instruments_path)
    backfill_root = _expanded(options.backfill_root)
    effective_start = (
        max(start_date, BSE_FIRST_TRADE_DATE) if options.scope == "bj-only" else start_date
    )
    open_dates = _load_open_dates(
        trade_cal_path,
        start_date=effective_start,
        end_date=end_date,
    )
    dates_path = _expanded(options.dates_path) if options.dates_path is not None else None
    requested_dates = _load_requested_dates(dates_path) if dates_path is not None else open_dates
    non_open_dates = sorted(set(requested_dates) - set(open_dates))
    if non_open_dates:
        raise ValueError(
            "Explicit minute backfill dates must be open dates inside the effective range: "
            f"{non_open_dates}"
        )
    if options.date_order not in {"ascending", "descending"}:
        raise ValueError(f"Unsupported minute backfill date order: {options.date_order}")
    requested_dates = sorted(requested_dates, reverse=options.date_order == "descending")
    list_dates, delist_dates, instrument_stats = _canonical_instrument_intervals(
        instruments_path,
        scope=options.scope,
    )

    scope_symbol_upper_bound = int(instrument_stats["scope_symbols"])
    if open_dates and scope_symbol_upper_bound <= 0:
        raise ValueError(f"Instrument master has no canonical symbols for scope={options.scope}")
    requests_per_date_upper_bound = math.ceil(scope_symbol_upper_bound / options.batch_size)
    eligible: list[tuple[str, int, int, int]] = []
    for trade_date in requested_dates:
        active_count = _active_symbol_count(
            trade_date,
            list_dates=list_dates,
            delist_dates=delist_dates,
        )
        active_interval_requests = math.ceil(active_count / options.batch_size)
        eligible.append(
            (
                trade_date,
                active_count,
                active_interval_requests,
                requests_per_date_upper_bound,
            )
        )

    selected: list[tuple[str, int, int, int]] = []
    request_upper_bound = 0
    truncated_by: str | None = None
    for trade_date, active_count, active_interval_requests, date_request_upper_bound in eligible:
        if options.max_dates is not None and len(selected) >= options.max_dates:
            truncated_by = "max_dates"
            break
        if (
            options.request_budget is not None
            and request_upper_bound + date_request_upper_bound > options.request_budget
        ):
            truncated_by = "request_budget"
            break
        selected.append(
            (
                trade_date,
                active_count,
                active_interval_requests,
                date_request_upper_bound,
            )
        )
        request_upper_bound += date_request_upper_bound

    segments = _segment_dates(selected, segment=options.segment)
    policy = options.request_policy or TushareRequestPolicy()
    resolved_api_url = resolve_tushare_api_url(options.api_url, token_env=options.token_env)
    identity: dict[str, Any] = {
        "scope": options.scope,
        "exchange_filter": (
            "BJ"
            if options.scope == "bj-only"
            else "SH_SZ"
            if options.scope == "sh-sz-only"
            else None
        ),
        "query": {
            "requested_start_date": start_date,
            "effective_start_date": effective_start,
            "end_date": end_date,
            "segment": options.segment,
            "date_order": options.date_order,
            "freq": "1min",
            "partition_by": "trade_date",
            "date_selection": "explicit_file" if dates_path is not None else "open_range",
        },
        "limits": {
            "request_budget": options.request_budget,
            "request_budget_unit": "conservative_pro_bar_batch_call_slots",
            "max_dates": options.max_dates,
            "batch_size": options.batch_size,
            "cooldown_seconds": options.cooldown_seconds,
            "workers": 1,
        },
        "endpoint_id": _endpoint_identifier(resolved_api_url),
        "token_env": options.token_env,
        "request_policy": _policy_payload(policy),
        "sources": {
            "trade_calendar": _source_fingerprint(trade_cal_path),
            "instruments": _source_fingerprint(instruments_path),
            **({"dates_file": _source_fingerprint(dates_path)} if dates_path is not None else {}),
        },
        "segments": segments,
    }
    plan_id = _plan_id(identity)
    run_root = backfill_root / "runs" / plan_id
    selected_date_count = len(selected)
    plan: dict[str, Any] = {
        "schema_version": MINUTE_BACKFILL_PLAN_SCHEMA,
        "plan_id": plan_id,
        "created_at": _utc_now(),
        "status": "planned",
        "identity": identity,
        "output": {
            "run_root": str(run_root),
            "data_dir": str(run_root / "data"),
            "writes_production": False,
        },
        "summary": {
            "calendar_open_dates": len(open_dates),
            "requested_dates": len(requested_dates),
            "eligible_dates": len(eligible),
            "selected_dates": selected_date_count,
            "omitted_dates": max(0, len(eligible) - selected_date_count),
            "segments": len(segments),
            "active_interval_estimated_minute_requests": sum(entry[2] for entry in selected),
            "minute_request_upper_bound": request_upper_bound,
            "minute_requests_per_date_upper_bound": requests_per_date_upper_bound,
            "estimated_discovery_requests_minimum": len(segments) * 4 + selected_date_count * 2,
            "truncated_by": truncated_by,
            "instrument_stats": instrument_stats,
        },
        "security": {
            "contains_token": False,
            "endpoint_query_and_credentials_recorded": False,
        },
    }
    if options.dry_run:
        return {**plan, "dry_run": True, "plan_path": None}
    assert options.plan_path is not None
    plan_path = _expanded(options.plan_path)
    written = _write_immutable_plan(plan, plan_path)
    return {**written, "plan_path": str(plan_path)}


def _validate_plan(plan: dict[str, Any]) -> None:
    if plan.get("schema_version") != MINUTE_BACKFILL_PLAN_SCHEMA:
        raise ValueError("Unsupported minute backfill plan schema")
    identity = plan.get("identity")
    if not isinstance(identity, dict) or plan.get("plan_id") != _plan_id(identity):
        raise ValueError("Minute backfill plan identity hash mismatch")
    plan_id = str(plan["plan_id"])
    backfill_root = Path(str(plan["output"]["run_root"])).parent.parent
    expected_run_root = backfill_root / "runs" / plan_id
    expected_data_dir = expected_run_root / "data"
    if (
        Path(str(plan["output"]["run_root"])) != expected_run_root
        or Path(str(plan["output"]["data_dir"])) != expected_data_dir
        or plan["output"].get("writes_production") is not False
    ):
        raise ValueError("Minute backfill plan output is not isolated under its run directory")
    if identity.get("limits", {}).get("workers") != 1:
        raise ValueError("Minute backfill plan must use exactly one worker")


def _new_receipt(plan: dict[str, Any], *, plan_path: Path, dry_run: bool) -> dict[str, Any]:
    identity = plan["identity"]
    return {
        "schema_version": MINUTE_BACKFILL_RECEIPT_SCHEMA,
        "plan_id": plan["plan_id"],
        "plan_path": str(plan_path),
        "status": "dry_run" if dry_run else "pending",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "fetch_vintage": None,
        "endpoint_id": identity["endpoint_id"],
        "token_env": identity["token_env"],
        "worker_count": 1,
        "output": plan["output"],
        "segments": [
            {
                "segment_id": segment["segment_id"],
                "start_date": segment["start_date"],
                "end_date": segment["end_date"],
                "date_count": segment["date_count"],
                "active_interval_estimated_minute_requests": segment[
                    "active_interval_estimated_minute_requests"
                ],
                "minute_request_upper_bound": segment["minute_request_upper_bound"],
                "status": "planned" if dry_run else "pending",
                "attempts": 0,
            }
            for segment in identity["segments"]
        ],
        "totals": {},
        "security": {"contains_token": False},
    }


def _receipt_totals(receipt: dict[str, Any]) -> dict[str, int]:
    segments = receipt["segments"]
    completed = [segment for segment in segments if segment["status"] == "complete"]
    return {
        "segments_total": len(segments),
        "segments_complete": len(completed),
        "dates_complete": sum(int(segment["date_count"]) for segment in completed),
        "minute_request_upper_bound": sum(
            int(segment["minute_request_upper_bound"]) for segment in segments
        ),
        "actual_successful_minute_requests": sum(
            int(segment.get("result", {}).get("requests_made", 0))
            + int(segment.get("result", {}).get("fallback_requests_made", 0))
            for segment in completed
        ),
        "actual_attempted_minute_requests": sum(
            int(segment.get("result", {}).get("requests_made", 0))
            + int(segment.get("result", {}).get("fallback_requests_made", 0))
            for segment in segments
        ),
        "fallback_successful_minute_requests": sum(
            int(segment.get("result", {}).get("fallback_requests_made", 0)) for segment in completed
        ),
        "fallback_attempted_minute_requests": sum(
            int(segment.get("result", {}).get("fallback_requests_made", 0)) for segment in segments
        ),
        "dates_fetched": sum(
            int(segment.get("result", {}).get("dates_fetched", 0)) for segment in completed
        ),
        "dates_skipped": sum(
            int(segment.get("result", {}).get("dates_skipped", 0)) for segment in completed
        ),
        "total_bars": sum(
            int(segment.get("result", {}).get("total_bars", 0)) for segment in completed
        ),
        "dates_partial": sum(
            int(segment.get("result", {}).get("dates_partial", 0)) for segment in segments
        ),
        "persisted_bars": sum(
            int(segment.get("result", {}).get("total_bars", 0)) for segment in segments
        ),
    }


def _save_receipt(receipt: dict[str, Any], path: Path) -> None:
    receipt["updated_at"] = _utc_now()
    receipt["totals"] = _receipt_totals(receipt)
    _atomic_write_json(receipt, path)


@contextmanager
def _run_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Minute backfill run is already locked: {path}") from exc
        handle.seek(0)
        handle.truncate()
        json.dump(
            {"pid": os.getpid(), "host": socket.gethostname(), "acquired_at": _utc_now()},
            handle,
        )
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _redacted_error(exc: BaseException, *, secrets: list[str]) -> dict[str, str]:
    message = str(exc) or type(exc).__name__
    for secret in secrets:
        if secret:
            message = message.replace(secret, "<redacted>")
    return {"type": type(exc).__name__, "message": message[:2000]}


def _load_or_create_receipt(
    *, plan: dict[str, Any], plan_path: Path, receipt_path: Path
) -> dict[str, Any]:
    if not receipt_path.exists():
        return _new_receipt(plan, plan_path=plan_path, dry_run=False)
    receipt = _read_json(receipt_path)
    if (
        receipt.get("schema_version") != MINUTE_BACKFILL_RECEIPT_SCHEMA
        or receipt.get("plan_id") != plan["plan_id"]
    ):
        raise ValueError("Minute backfill receipt does not match the immutable plan")
    return receipt


def _load_run_plan(
    options: MinuteBackfillRunOptions,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any], str | None]:
    if options.workers != 1:
        raise ValueError("Minute backfill supports exactly one worker")
    plan_path = _expanded(options.plan_path)
    receipt_path = _expanded(options.receipt_path)
    plan = _read_json(plan_path)
    _validate_plan(plan)
    identity = plan["identity"]
    token_env = str(options.token_env or identity["token_env"])
    _load_tushare_env_files()
    resolved_api_url = resolve_tushare_api_url(options.api_url, token_env=token_env)
    endpoint_id = _endpoint_identifier(resolved_api_url)
    if endpoint_id != identity["endpoint_id"]:
        raise ValueError(
            "Runtime TuShare endpoint does not match the plan: "
            f"planned={identity['endpoint_id']} runtime={endpoint_id}"
        )
    return plan_path, receipt_path, plan, identity, resolved_api_url


def _execution_context(
    options: MinuteBackfillRunOptions,
    *,
    receipt_path: Path,
    plan: dict[str, Any],
    identity: dict[str, Any],
    resolved_api_url: str | None,
) -> _MinuteBackfillExecution:
    token_env = str(options.token_env or identity["token_env"])
    token_value = str(os.environ.get(token_env) or "").strip()
    if not token_value:
        raise RuntimeError(f"No TuShare token found in environment variable {token_env}.")
    scope = str(identity["scope"])
    return _MinuteBackfillExecution(
        receipt_path=receipt_path,
        run_root=Path(str(plan["output"]["run_root"])),
        data_dir=Path(str(plan["output"]["data_dir"])),
        token_env=token_env,
        resolved_api_url=resolved_api_url,
        provider_no_data_exceptions_path=options.provider_no_data_exceptions_path,
        policy=_policy_from_payload(identity["request_policy"]),
        exchange=("BJ" if scope == "bj-only" else "SH_SZ" if scope == "sh-sz-only" else None),
        secrets=[token_value, str(options.api_url or ""), str(resolved_api_url or "")],
        limits=identity["limits"],
        minute_quota_mode=options.minute_quota_mode,
        minute_quota_db=options.minute_quota_db,
        minute_quota_consumer=options.minute_quota_consumer,
        minute_quota_limit_rows=options.minute_quota_limit_rows,
        minute_quota_safety_rows=options.minute_quota_safety_rows,
        minute_quota_gate=options.minute_quota_gate,
        minute_quota_limit_requests=options.minute_quota_limit_requests,
        minute_quota_burst_limit_requests=options.minute_quota_burst_limit_requests,
        minute_quota_safety_requests=options.minute_quota_safety_requests,
        minute_quota_allow_burst=options.minute_quota_allow_burst,
    )


_SegmentExecutionOutcome = Literal["complete", "partial", "stopped"]


def _result_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "dates_fetched": int(result.get("dates_fetched", 0)),
        "dates_skipped": int(result.get("dates_skipped", 0)),
        "dates_partial": int(result.get("dates_partial", 0)),
        "partial_dates": list(result.get("partial_dates", [])),
        "requests_made": int(result.get("requests_made", 0)),
        "fallback_requests_made": int(result.get("fallback_requests_made", 0)),
        "total_bars": int(result.get("total_bars", 0)),
        "output_dir": str(result["output_dir"]),
    }


def _execute_planned_segment(
    execution: _MinuteBackfillExecution,
    receipt: dict[str, Any],
    planned_segment: dict[str, Any],
    state: dict[str, Any],
) -> _SegmentExecutionOutcome:
    state["status"] = "running"
    state["attempts"] = int(state.get("attempts", 0)) + 1
    state["fetch_started_at"] = _utc_now()
    state.pop("error", None)
    _save_receipt(receipt, execution.receipt_path)
    try:
        from market_data_platform import tushare_minute_backfill as _backfill_shell

        result = _backfill_shell.mirror_minute_bars(
            MinsMirrorOptions(
                start_date=planned_segment["start_date"],
                end_date=planned_segment["end_date"],
                trading_dates=list(planned_segment["dates"]),
                exchange=execution.exchange,
                output_dir=execution.data_dir,
                token_env=execution.token_env,
                api_url=execution.resolved_api_url,
                provider_no_data_exceptions_path=execution.provider_no_data_exceptions_path,
                request_policy=execution.policy,
                skip_existing=True,
                continue_on_partial_dates=True,
                batch_size=int(execution.limits["batch_size"]),
                cooldown_seconds=float(execution.limits["cooldown_seconds"]),
                minute_quota_mode=execution.minute_quota_mode,
                minute_quota_db=execution.minute_quota_db,
                minute_quota_consumer=execution.minute_quota_consumer,
                minute_quota_limit_rows=execution.minute_quota_limit_rows,
                minute_quota_safety_rows=execution.minute_quota_safety_rows,
                minute_quota_gate=execution.minute_quota_gate,
                minute_quota_limit_requests=execution.minute_quota_limit_requests,
                minute_quota_burst_limit_requests=(execution.minute_quota_burst_limit_requests),
                minute_quota_safety_requests=execution.minute_quota_safety_requests,
                minute_quota_allow_burst=execution.minute_quota_allow_burst,
            )
        )
        actual_requests = int(result.get("requests_made", 0))
        if actual_requests > int(planned_segment["minute_request_upper_bound"]):
            state["actual_successful_minute_requests"] = actual_requests
            state["budget_violation"] = True
            raise MinuteBackfillBudgetError(
                "Minute mirror exceeded the plan's conservative request upper bound: "
                f"segment={planned_segment['segment_id']} "
                f"actual={actual_requests} "
                f"upper_bound={planned_segment['minute_request_upper_bound']}"
            )
    except MinuteMirrorIncompleteDatesError as exc:
        result = exc.result
        actual_requests = int(result.get("requests_made", 0))
        if actual_requests > int(planned_segment["minute_request_upper_bound"]):
            state["actual_successful_minute_requests"] = actual_requests
            state["budget_violation"] = True
            state["status"] = "partial"
            state["error"] = _redacted_error(
                MinuteBackfillBudgetError(
                    "Minute mirror exceeded the plan's conservative request upper bound: "
                    f"segment={planned_segment['segment_id']} "
                    f"actual={actual_requests} "
                    f"upper_bound={planned_segment['minute_request_upper_bound']}"
                ),
                secrets=execution.secrets,
            )
            receipt["status"] = "failed"
            _save_receipt(receipt, execution.receipt_path)
            return "stopped"
        state["status"] = "partial"
        state["result"] = _result_payload(result)
        state["error"] = _redacted_error(exc, secrets=execution.secrets)
        receipt["status"] = "partial"
        _save_receipt(receipt, execution.receipt_path)
        return "partial"
    except (KeyboardInterrupt, SystemExit) as exc:
        state["status"] = "interrupted"
        state["error"] = _redacted_error(exc, secrets=execution.secrets)
        receipt["status"] = "interrupted"
        _save_receipt(receipt, execution.receipt_path)
        raise
    except Exception as exc:
        state["status"] = "partial"
        state["error"] = _redacted_error(exc, secrets=execution.secrets)
        receipt["status"] = "failed" if isinstance(exc, MinuteBackfillBudgetError) else "partial"
        _save_receipt(receipt, execution.receipt_path)
        return "stopped"
    state["status"] = "complete"
    state["fetch_completed_at"] = _utc_now()
    state["result"] = _result_payload(result)
    _save_receipt(receipt, execution.receipt_path)
    return "complete"


def _execute_backfill_plan(
    execution: _MinuteBackfillExecution,
    *,
    plan: dict[str, Any],
    plan_path: Path,
) -> dict[str, Any]:
    identity = plan["identity"]
    with _run_lock(execution.run_root / ".minute-backfill.lock"):
        receipt = _load_or_create_receipt(
            plan=plan,
            plan_path=plan_path,
            receipt_path=execution.receipt_path,
        )
        if receipt.get("status") == "complete":
            return receipt
        budget_violations = [
            segment["segment_id"]
            for segment in receipt["segments"]
            if segment.get("budget_violation") is True
        ]
        if budget_violations:
            raise MinuteBackfillBudgetError(
                "Minute backfill receipt contains a prior request-budget violation; "
                f"create a new plan before retrying: {budget_violations}"
            )
        receipt["status"] = "running"
        receipt["fetch_vintage"] = receipt.get("fetch_vintage") or _utc_now()
        receipt["token_env"] = execution.token_env
        _save_receipt(receipt, execution.receipt_path)

        states = {segment["segment_id"]: segment for segment in receipt["segments"]}
        partial_segments = False
        for planned_segment in identity["segments"]:
            state = states[planned_segment["segment_id"]]
            if state["status"] == "complete":
                continue
            outcome = _execute_planned_segment(execution, receipt, planned_segment, state)
            if outcome == "stopped":
                return receipt
            partial_segments = partial_segments or outcome == "partial"

        receipt["status"] = "partial" if partial_segments else "complete"
        if not partial_segments:
            receipt["fetch_completed_at"] = _utc_now()
        _save_receipt(receipt, execution.receipt_path)
        return receipt
