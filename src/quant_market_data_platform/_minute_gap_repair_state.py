"""Fail-closed durable repair budgets and completed candidate references."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from quant_market_data_platform.minute_candidate import _read_json


def load_state(path: Path) -> dict[str, Any]:
    state = _read_json(path) if path.exists() else {"jobs": {}, "candidates": {}}
    if not isinstance(state, dict) or not isinstance(state.get("jobs"), dict):
        raise ValueError("invalid_state")
    if not isinstance(state.get("candidates", {}), dict):
        raise ValueError("invalid_state")
    for key, entry in state["jobs"].items():
        if not isinstance(entry, dict):
            raise ValueError("invalid_job")
        attempts = entry.get("attempts")
        due = entry.get("next_eligible")
        if type(attempts) is not int or not 0 <= attempts <= 3:
            raise ValueError("invalid_attempts")
        if type(due) not in (int, float) or not math.isfinite(due) or due < 0:
            raise ValueError("invalid_cooldown")
        identity = f"{entry.get('trade_date')}:{entry.get('symbol')}:{entry.get('source_sha256')}"
        if key != identity or entry.get("outcome") not in {
            "attempting",
            "patch_ready",
            "request_failed",
            "missing_response",
            "audit_blocked",
            "quota_exhausted",
            "quota_closed",
        }:
            raise ValueError("invalid_job_identity")
    return state


def existing_candidate(options: Any, queue: dict[str, Any], state: dict[str, Any]) -> str | None:
    from quant_market_data_platform._minute_gap_repair_artifacts import _validate
    from quant_market_data_platform.providers._a_share_mins_partition import (
        _read_existing_partition,
        _sha256_file,
        validate_complete_minute_partition,
    )

    for sha, entry in state.get("candidates", {}).items():
        if not isinstance(entry, dict):
            raise ValueError("invalid_candidate_reference")
        date = entry["trade_date"]
        if date not in queue["dates"]:
            continue
        source = Path(entry["source_sidecar"]).resolve()
        if queue["dates"][date]["sidecars"] != [str(source)]:
            continue
        if _sha256_file(source) != sha:
            continue
        from quant_market_data_platform.minute_gap_repair import (
            _QueueRowInvalid,
            _require_native_provider_universe,
        )

        try:
            _require_native_provider_universe(_read_json(source), date)
        except _QueueRowInvalid:
            continue
        candidate = Path(entry["candidate"]).resolve()
        relative = candidate.relative_to(
            (options.artifacts_root / "staging/tushare_minute_backfill_reverse/runs").resolve()
        )
        if len(relative.parts) != 3 or not relative.parts[0].startswith("repair-"):
            raise ValueError("invalid_candidate_path")
        if _sha256_file(candidate / "_minute_mirror.json") != entry["candidate_sha256"]:
            raise ValueError("candidate_hash_mismatch")
        payload = _read_json(candidate / "_minute_mirror.json")
        _validate(_read_existing_partition(candidate), date, payload["expected_symbols"])
        validate_complete_minute_partition(candidate, trade_date=date, require_full_universe=False)
        return str(candidate)
    return None
