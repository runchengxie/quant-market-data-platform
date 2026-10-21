"""Repair one explicitly deferred stock-day, preserving every source artifact."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant_market_data_platform._reverse_backfill_missing import _missing_symbols, missing_queue
from quant_market_data_platform.minute_candidate import _atomic_write_json, _read_json
from quant_market_data_platform.providers._a_share_mins_partition import (
    _sha256_file,
    _universe_hash,
)


@dataclass(frozen=True)
class GapRepairOptions:
    artifacts_root: Path
    runtime_root: Path
    token_env: str = "TUSHARE_TOKEN_2"
    api_url: str | None = None
    daily_request_limit: int = 500
    max_attempts: int = 3
    cooldown_seconds: float = 1.2
    allow_burst: bool = False
    min_free_bytes: int = 10 * 1024**3
    dry_run: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


def _result(status: str, **details: Any) -> dict[str, Any]:
    return {"status": status, "writes_production": False, **details}


class _QueueRowInvalid(ValueError):
    """A fixed, safe reason for rejecting a queue row."""


def _require_native_provider_universe(source: dict[str, Any], date: str) -> None:
    if not source["universe_rule"].startswith("daily_crosschecked_"):
        return
    from quant_market_data_platform.providers._a_share_mins_universe import (
        _apply_historical_code_transitions,
    )

    symbols = set(source["expected_symbols"])
    try:
        native, _ = _apply_historical_code_transitions(symbols, trade_date=date)
    except RuntimeError:
        raise _QueueRowInvalid("historical_code_transition_required") from None
    if native != symbols:
        raise _QueueRowInvalid("historical_code_transition_required")


def _queue_jobs(
    root: Path, queue: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    staging = root / "staging/tushare_minute_backfill_reverse"
    current = missing_queue(staging, set(), selected_dates=set(queue["dates"]))["dates"]
    jobs, skipped = [], []
    for date, row in sorted(queue["dates"].items()):
        try:
            paths = row["sidecars"]
            if len(paths) != 1:
                raise _QueueRowInvalid("stale_queue")
            path = Path(paths[0]).resolve()
            relative = path.relative_to((staging / "runs").resolve())
            if len(relative.parts) != 4 or relative.parts[1:] != (
                "data",
                f"trade_date={date}",
                "_minute_mirror.json",
            ):
                raise _QueueRowInvalid("invalid_source_path")
            payload = _read_json(path)
            missing = _missing_symbols(path, payload)
            if not missing or missing != row["missing_symbols"]:
                raise _QueueRowInvalid("source_binding_mismatch")
            rule = payload["universe_rule"]
            if (
                payload["universe_hash"]
                != _universe_hash(set(payload["expected_symbols"]), rule=rule)
                or not payload["universe_source"]
            ):
                raise _QueueRowInvalid("invalid_universe_identity")
            _require_native_provider_universe(payload, date)
            if row != current.get(date):
                raise _QueueRowInvalid("stale_queue")
            jobs.append(
                {
                    "date": date,
                    "path": path,
                    "source": payload,
                    "sha": _sha256_file(path),
                    "missing": missing,
                }
            )
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            reason = str(exc) if isinstance(exc, _QueueRowInvalid) else type(exc).__name__
            skipped.append({"date": str(date), "reason": reason})
    return jobs, skipped


def _ledger(options: GapRepairOptions):
    from quant_market_data_platform.providers._env import _resolve_token
    from quant_market_data_platform.tushare_minute_quota import (
        MinuteQuotaRequestOverrides,
        build_minute_quota_ledger,
    )

    return build_minute_quota_ledger(
        token=_resolve_token(None, options.token_env),
        mode="enforce",
        database_path=options.artifacts_root / "metadata/tushare/minute_quota/minute_quota.sqlite3",
        consumer="minute_gap_repair",
        limit_rows=160_000_000,
        safety_rows=4_000_000,
        request_overrides=MinuteQuotaRequestOverrides(
            gate="requests",
            limit_requests=10_000,
            burst_limit_requests=20_000,
            safety_requests=500,
            allow_burst=options.allow_burst,
        ),
    )


def _daily_used(ledger: Any) -> int:
    consumers = ledger.status(now=_now())["consumers"]
    return sum(
        int(row.get(field, 0))
        for row in consumers
        if row["consumer"] == "minute_gap_repair"
        for field in ("committed_requests", "reserved_requests", "uncertain_requests")
    )


def _key(job: dict[str, Any], code: str) -> str:
    return f"{job['date']}:{code}:{job['sha']}"


def _eligible(job: dict[str, Any], state: dict[str, Any], options: GapRepairOptions):
    for code in job["missing"]:
        entry = state["jobs"].get(_key(job, code), {})
        if entry.get("outcome") == "patch_ready":
            continue
        if entry.get("attempts", 0) >= min(3, options.max_attempts):
            continue
        if entry.get("next_eligible", 0) > _now().timestamp():
            continue
        return code
    return None


def _load_queue(root: Path) -> dict[str, Any]:
    path = root / "metadata/minute_backfill/reverse_scheduler/deferred_missing.json"
    if not path.exists():
        return {"dates": {}}
    queue = _read_json(path)
    if queue.get("schema_version") != "tushare.minute.deferred_missing.v1" or not isinstance(
        queue.get("dates"), dict
    ):
        raise ValueError("invalid_queue")
    return queue


def _run_locked(options: GapRepairOptions) -> dict[str, Any]:  # noqa: PLR0911
    from quant_market_data_platform._minute_gap_repair_artifacts import assemble
    from quant_market_data_platform._minute_gap_repair_state import existing_candidate, load_state

    root = options.artifacts_root
    try:
        queue = _load_queue(root)
        if not queue["dates"]:
            return _result("empty")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return _result("invalid_queue")
    state_path = root / "metadata/minute_backfill/reverse_scheduler/gap_repair/state.json"
    try:
        state = load_state(state_path)
        candidate = existing_candidate(options, queue, state)
    except (OSError, ValueError, TypeError, KeyError):
        return _result("invalid_state")
    if candidate:
        return _result("candidate_ready", candidate=candidate)
    jobs, skipped = _queue_jobs(root, queue)
    if not jobs:
        return _result("no_eligible_queue", skipped=skipped)
    if shutil.disk_usage(root).free < options.min_free_bytes:
        return _result("storage_blocked")
    if options.dry_run:
        return _result("dry_run", queued_dates=[job["date"] for job in jobs])
    for job in jobs:
        candidate = assemble(options, job, state)
        if candidate:
            _atomic_write_json(state_path, state)
            return _result("candidate_ready", candidate=candidate, skipped=skipped)
    return _spend_one(options, jobs, skipped, state, state_path)


def _spend_one(
    options: GapRepairOptions,
    jobs: list[dict[str, Any]],
    skipped: list[dict[str, str]],
    state: dict[str, Any],
    state_path: Path,
) -> dict[str, Any]:
    from quant_market_data_platform._minute_gap_repair_artifacts import fetch_patch

    targets = [(job, _eligible(job, state, options)) for job in jobs]
    target = next(((job, code) for job, code in targets if code), None)
    if target is None:
        pending = [
            state["jobs"].get(_key(job, code), {}) for job in jobs for code in job["missing"]
        ]
        exhausted = all(
            entry.get("outcome") == "patch_ready"
            or entry.get("attempts", 0) >= min(3, options.max_attempts)
            for entry in pending
        )
        return _result("attempts_exhausted" if exhausted else "cooldown", skipped=skipped)
    job, code = target
    ledger = _ledger(options)
    if _daily_used(ledger) >= min(options.daily_request_limit, 500):
        return _result("daily_cap")
    return fetch_patch(options, job, code, state, state_path, ledger)


def run_gap_repair(options: GapRepairOptions) -> dict[str, Any]:
    """Consume at most one singleton request; never resume the reverse scheduler."""
    from quant_market_data_platform._minute_gap_repair_artifacts import RepairAuditBlocked
    from quant_market_data_platform._minute_history_lock import (
        HistoryWriterBusy,
        history_writer_lock,
    )
    from quant_market_data_platform.minute_semantic_quarantine import quarantine_status

    marker = options.runtime_root / "semantic-audit-blocked.json"
    if quarantine_status(options.artifacts_root, marker)["blocked"]:
        return _result("audit_blocked")
    try:
        with history_writer_lock(options.artifacts_root):
            if quarantine_status(options.artifacts_root, marker)["blocked"]:
                return _result("audit_blocked")
            return _run_locked(options)
    except HistoryWriterBusy:
        return _result("writer_busy")
    except RepairAuditBlocked:
        return _result("audit_blocked")
