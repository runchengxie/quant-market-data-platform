"""Immutable planning and resumable execution for TuShare minute backfills."""

from __future__ import annotations

from typing import Any

from market_data_platform.tushare_minute_backfill_part01 import (
    MinuteBackfillRunOptions,
)
from market_data_platform.tushare_minute_backfill_part02 import (
    _execute_backfill_plan,
    _execution_context,
    _load_run_plan,
    _new_receipt,
    _save_receipt,
)


def run_minute_backfill(options: MinuteBackfillRunOptions) -> dict[str, Any]:
    """Execute a plan sequentially, checkpointing segment and daily progress."""
    plan_path, receipt_path, plan, identity, resolved_api_url = _load_run_plan(options)

    if options.dry_run:
        receipt = _new_receipt(plan, plan_path=plan_path, dry_run=True)
        _save_receipt(receipt, receipt_path)
        return receipt
    execution = _execution_context(
        options,
        receipt_path=receipt_path,
        plan=plan,
        identity=identity,
        resolved_api_url=resolved_api_url,
    )
    return _execute_backfill_plan(execution, plan=plan, plan_path=plan_path)


def summarize_minute_backfill_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a concise CLI summary without printing every planned date."""
    return {
        key: payload.get(key)
        for key in (
            "schema_version",
            "plan_id",
            "plan_path",
            "status",
            "dry_run",
            "fetch_vintage",
            "endpoint_id",
            "output",
            "summary",
            "totals",
        )
        if key in payload
    }
