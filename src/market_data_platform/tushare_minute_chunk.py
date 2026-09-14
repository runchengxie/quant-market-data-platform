"""Bounded, resumable invocations for TuShare full-day minute gaps."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from market_data_platform.dataset_lock import exclusive_file_lock
from market_data_platform.providers.a_share_minute_coverage import (
    TushareFullDayPlan,
    load_tushare_full_day_plan,
)
from market_data_platform.providers.tushare_a_share import (
    _load_tushare_env_files,
    resolve_tushare_api_url,
)
from market_data_platform.providers.tushare_a_share_mins import (
    DEFAULT_MINS_BATCH_SIZE,
    MinsMirrorOptions,
    mirror_minute_bars,
    validate_complete_minute_partition,
    validate_mins_batch_size,
)
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy

MINUTE_FULL_DAY_CHUNK_RECEIPT_SCHEMA = "tushare.a_share.minute_full_day_chunk.v1"
DEFAULT_CHUNK_DATES = 3
MAX_CHUNK_DATES = 5
_LOCK_FILENAME = ".tushare-minute-full-day-chunk.lock"


@dataclass(frozen=True)
class MinuteFullDayChunkOptions:
    """Inputs for one bounded full-day minute download invocation."""

    plan_path: str | Path
    source_root: str | Path
    receipt_dir: str | Path
    max_dates: int = DEFAULT_CHUNK_DATES
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    request_policy: TushareRequestPolicy | None = None
    cooldown_seconds: float = 1.0
    batch_size: int = DEFAULT_MINS_BATCH_SIZE
    gc_frequency: int = 100
    dry_run: bool = False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _expanded(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _atomic_write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _endpoint_identifier(api_url: str | None) -> str:
    if not api_url:
        return "tushare-default"
    parsed = urlsplit(api_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("TuShare endpoint must be an HTTP(S) URL")
    host = parsed.hostname.lower()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme.lower()}://{host}{parsed.path.rstrip('/')}"


def _compact_source_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        key: receipt[key]
        for key in (
            "schema_version",
            "trade_date",
            "partition_path",
            "sidecar_path",
            "partition_sha256",
            "sidecar_sha256",
            "rows",
            "symbols",
            "market_symbol_counts",
            "expected_bars_per_symbol",
            "universe_hash",
            "universe_rule",
            "mirror_generated_at",
        )
    }


def _inspect_date(source_root: Path, trade_date: str) -> dict[str, Any]:
    part_dir = source_root / f"trade_date={trade_date}"
    if not part_dir.is_dir():
        return {"trade_date": trade_date, "status": "pending", "reason": "partition_missing"}
    try:
        source_receipt = validate_complete_minute_partition(
            part_dir,
            trade_date=trade_date,
            require_full_universe=True,
        )
    except Exception as exc:
        return {
            "trade_date": trade_date,
            "status": "pending",
            "reason": "partition_incomplete_or_invalid",
            "detail": (str(exc) or type(exc).__name__)[:1000],
        }
    return {
        "trade_date": trade_date,
        "status": "complete",
        "source_receipt": _compact_source_receipt(source_receipt),
    }


def _inspect_plan(plan: TushareFullDayPlan, source_root: Path) -> list[dict[str, Any]]:
    return [_inspect_date(source_root, trade_date) for trade_date in plan.dates]


def _validate_options(options: MinuteFullDayChunkOptions) -> tuple[Path, Path, Path]:
    if not 1 <= options.max_dates <= MAX_CHUNK_DATES:
        raise ValueError(f"max_dates must be between 1 and {MAX_CHUNK_DATES}")
    validate_mins_batch_size(options.batch_size)
    if options.cooldown_seconds < 0:
        raise ValueError("cooldown_seconds must be non-negative")
    if options.gc_frequency <= 0:
        raise ValueError("gc_frequency must be positive")
    plan_path = _expanded(options.plan_path)
    source_root = _expanded(options.source_root)
    receipt_dir = _expanded(options.receipt_dir)
    if not plan_path.is_file():
        raise FileNotFoundError(f"TuShare full-day plan not found: {plan_path}")
    if not source_root.is_dir():
        raise FileNotFoundError(f"TuShare full-day source root not found: {source_root}")
    return plan_path, source_root, receipt_dir


def _selection_payload(
    plan: TushareFullDayPlan, states: list[dict[str, Any]], *, max_dates: int
) -> dict[str, Any]:
    complete = [str(state["trade_date"]) for state in states if state["status"] == "complete"]
    pending = [str(state["trade_date"]) for state in states if state["status"] != "complete"]
    selected = pending[:max_dates]
    return {
        "plan_date_count": len(plan.dates),
        "completed_before_count": len(complete),
        "completed_before_dates": complete,
        "pending_before_count": len(pending),
        "pending_before_dates": pending,
        "selected_dates": selected,
        "deferred_dates": pending[len(selected) :],
    }


def _new_invocation(
    options: MinuteFullDayChunkOptions,
    *,
    plan: TushareFullDayPlan,
    plan_path: Path,
    source_root: Path,
    selection: dict[str, Any],
) -> dict[str, Any]:
    invocation_id = uuid.uuid4().hex
    policy = options.request_policy or TushareRequestPolicy()
    return {
        "schema_version": MINUTE_FULL_DAY_CHUNK_RECEIPT_SCHEMA,
        "invocation_id": invocation_id,
        "status": "dry_run" if options.dry_run else "running",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "plan": {
            "path": str(plan_path),
            "sha256": plan.sha256,
            "phase": plan.phase,
            "dates": len(plan.dates),
        },
        "source_root": str(source_root),
        "parameters": {
            "max_dates": options.max_dates,
            "batch_size": options.batch_size,
            "cooldown_seconds": options.cooldown_seconds,
            "gc_frequency": options.gc_frequency,
            "workers": 1,
            "exchange_filter": None,
            "token_env": options.token_env,
            "endpoint_id": None,
            "request_policy": {
                "attempts": policy.attempts,
                "retry_sleep_seconds": policy.retry_sleep_seconds,
                "retry_max_sleep_seconds": policy.retry_max_sleep_seconds,
                "quota_cooldown_seconds": policy.quota_cooldown_seconds,
                "disable_proxy": policy.disable_proxy,
            },
        },
        "selection": selection,
        "dates": {trade_date: {"status": "selected"} for trade_date in selection["selected_dates"]},
        "summary": {
            "selected_dates": len(selection["selected_dates"]),
            "completed_selected_dates": 0,
            "pending_plan_dates": selection["pending_before_count"],
        },
        "security": {"contains_token": False},
    }


def _receipt_path(receipt_dir: Path, invocation: dict[str, Any]) -> Path:
    timestamp = str(invocation["created_at"]).replace("-", "").replace(":", "")
    timestamp = timestamp.replace(".", "")
    return receipt_dir / f"minute_full_day_chunk_{timestamp}_{invocation['invocation_id']}.json"


def _save_invocation(invocation: dict[str, Any], receipt_path: Path) -> None:
    invocation["updated_at"] = _utc_now()
    invocation["receipt_path"] = str(receipt_path)
    _atomic_write_json(invocation, receipt_path)


def _redacted_error(exc: BaseException, *, secrets: list[str]) -> dict[str, str]:
    message = str(exc) or type(exc).__name__
    for secret in secrets:
        if secret:
            message = message.replace(secret, "<redacted>")
    return {"type": type(exc).__name__, "message": message[:2000]}


def _refresh_after_run(
    invocation: dict[str, Any], *, plan: TushareFullDayPlan, source_root: Path
) -> None:
    states = _inspect_plan(plan, source_root)
    by_date = {str(state["trade_date"]): state for state in states}
    selected = [str(value) for value in invocation["selection"]["selected_dates"]]
    invocation["dates"] = {trade_date: by_date[trade_date] for trade_date in selected}
    complete_selected = sum(by_date[trade_date]["status"] == "complete" for trade_date in selected)
    pending = [str(state["trade_date"]) for state in states if state["status"] != "complete"]
    invocation["summary"] = {
        "selected_dates": len(selected),
        "completed_selected_dates": complete_selected,
        "pending_selected_dates": len(selected) - complete_selected,
        "completed_plan_dates": len(states) - len(pending),
        "pending_plan_dates": len(pending),
        "pending_plan_date_values": pending,
        "plan_complete": not pending,
    }


def _mirror_selection(
    options: MinuteFullDayChunkOptions,
    *,
    selected: list[str],
    source_root: Path,
    resolved_api_url: str | None,
) -> dict[str, Any]:
    return mirror_minute_bars(
        MinsMirrorOptions(
            start_date=selected[0],
            end_date=selected[-1],
            trading_dates=selected,
            output_dir=source_root,
            token_env=options.token_env,
            api_url=resolved_api_url,
            request_policy=options.request_policy or TushareRequestPolicy(),
            skip_existing=True,
            batch_size=options.batch_size,
            cooldown_seconds=options.cooldown_seconds,
            gc_frequency=options.gc_frequency,
            exchange=None,
        )
    )


def _execute_locked(
    options: MinuteFullDayChunkOptions,
    *,
    plan: TushareFullDayPlan,
    plan_path: Path,
    source_root: Path,
    receipt_dir: Path,
) -> dict[str, Any]:
    states = _inspect_plan(plan, source_root)
    selection = _selection_payload(plan, states, max_dates=options.max_dates)
    selected = [str(value) for value in selection["selected_dates"]]
    if not selected:
        invocation = _new_invocation(
            options,
            plan=plan,
            plan_path=plan_path,
            source_root=source_root,
            selection=selection,
        )
        invocation["status"] = "noop"
        invocation["summary"]["plan_complete"] = True
        return invocation

    invocation = _new_invocation(
        options,
        plan=plan,
        plan_path=plan_path,
        source_root=source_root,
        selection=selection,
    )
    receipt_path = _receipt_path(receipt_dir, invocation)
    _save_invocation(invocation, receipt_path)
    token_value = ""
    resolved_api_url: str | None = None
    secrets = [str(options.api_url or "")]
    try:
        _load_tushare_env_files()
        token_value = str(os.environ.get(options.token_env) or "").strip()
        secrets.append(token_value)
        if not token_value:
            raise RuntimeError(
                f"No TuShare token found in environment variable {options.token_env}."
            )
        resolved_api_url = resolve_tushare_api_url(options.api_url, token_env=options.token_env)
        secrets.append(str(resolved_api_url or ""))
        invocation["parameters"]["endpoint_id"] = _endpoint_identifier(resolved_api_url)
        _save_invocation(invocation, receipt_path)
        invocation["mirror_result"] = _mirror_selection(
            options,
            selected=selected,
            source_root=source_root,
            resolved_api_url=resolved_api_url,
        )
    except (KeyboardInterrupt, SystemExit) as exc:
        _refresh_after_run(invocation, plan=plan, source_root=source_root)
        invocation["status"] = "interrupted"
        invocation["error"] = _redacted_error(exc, secrets=secrets)
        _save_invocation(invocation, receipt_path)
        raise
    except Exception as exc:
        _refresh_after_run(invocation, plan=plan, source_root=source_root)
        invocation["status"] = (
            "partial" if invocation["summary"]["completed_selected_dates"] else "failed"
        )
        invocation["error"] = _redacted_error(exc, secrets=secrets)
        _save_invocation(invocation, receipt_path)
        return invocation
    _refresh_after_run(invocation, plan=plan, source_root=source_root)
    invocation["status"] = (
        "complete" if invocation["summary"]["pending_selected_dates"] == 0 else "partial"
    )
    _save_invocation(invocation, receipt_path)
    return invocation


def run_minute_full_day_chunk(options: MinuteFullDayChunkOptions) -> dict[str, Any]:
    """Run at most ``max_dates`` pending promotion-plan dates, sequentially."""
    plan_path, source_root, receipt_dir = _validate_options(options)
    plan = load_tushare_full_day_plan(plan_path)
    if options.dry_run:
        states = _inspect_plan(plan, source_root)
        selection = _selection_payload(plan, states, max_dates=options.max_dates)
        return _new_invocation(
            options,
            plan=plan,
            plan_path=plan_path,
            source_root=source_root,
            selection=selection,
        )
    with exclusive_file_lock(
        source_root / _LOCK_FILENAME,
        operation="run-tushare-minute-full-day-chunk",
    ):
        return _execute_locked(
            options,
            plan=plan,
            plan_path=plan_path,
            source_root=source_root,
            receipt_dir=receipt_dir,
        )


def summarize_minute_full_day_chunk(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a compact command result while the receipt retains full lineage."""
    return {
        key: payload.get(key)
        for key in (
            "schema_version",
            "invocation_id",
            "status",
            "created_at",
            "updated_at",
            "receipt_path",
            "plan",
            "source_root",
            "parameters",
            "selection",
            "summary",
            "error",
        )
        if key in payload
    }


__all__ = [
    "DEFAULT_CHUNK_DATES",
    "MAX_CHUNK_DATES",
    "MINUTE_FULL_DAY_CHUNK_RECEIPT_SCHEMA",
    "MinuteFullDayChunkOptions",
    "run_minute_full_day_chunk",
    "summarize_minute_full_day_chunk",
]
