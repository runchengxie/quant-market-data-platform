"""Immutable patch evidence and isolated requested-universe candidates."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from quant_market_data_platform.minute_candidate import _atomic_write_json, _read_json
from quant_market_data_platform.providers._a_share_mins_constants import _MinuteProgress
from quant_market_data_platform.providers._a_share_mins_partition import (
    _persist_progress,
    _read_existing_partition,
    _sha256_file,
    validate_complete_minute_partition,
)
from quant_market_data_platform.providers._a_share_mins_validation import _validate_batch_frame
from quant_market_data_platform.providers.a_share_minute_price_flow import price_flow_diagnostics
from quant_market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
from quant_market_data_platform.quality_a_share_minute import _frame_validation_issues


def _validate(frame: pd.DataFrame, date: str, symbols: list[str]) -> pd.DataFrame:
    normalized, valid, issues = _validate_batch_frame(
        frame, requested_symbols=symbols, trade_date=date
    )
    if issues or valid != set(symbols):
        raise ValueError("invalid_minute_grid")
    diagnostics = _frame_validation_issues(date, normalized)
    diagnostics.update(price_flow_diagnostics(normalized))
    fatal = (
        "notional_beyond_hard_price_guard_rows",
        "zero_volume_nonzero_amount_rows",
        "positive_volume_zero_amount_rows",
    )
    diagnostic_only = (
        "vwap_outside_ohlc_rows",
        "vwap_beyond_source_guard_rows",
        "extreme_vwap_unit_scale_rows",
    )
    if any(value for key, value in diagnostics.items() if key not in diagnostic_only) or any(
        diagnostics[k] for k in fatal
    ):
        raise ValueError("semantic_validation_failed")
    if (normalized[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("nonpositive_price")
    return normalized


def _patch_frame(
    options: Any, job: dict[str, Any], code: str, entry: dict[str, Any]
) -> pd.DataFrame:
    path = Path(entry["patch_path"]).resolve()
    path.relative_to((options.artifacts_root / "staging/minute_gap_repair").resolve())
    if (
        entry["source_sha256"] != job["sha"]
        or entry["symbol"] != code
        or entry["trade_date"] != job["date"]
    ):
        raise ValueError("patch_binding_mismatch")
    if _sha256_file(path) != entry["patch_sha256"]:
        raise ValueError("patch_hash_mismatch")
    return _validate(pd.read_parquet(path), job["date"], [code])


class RepairAuditBlocked(ValueError):
    """Cached evidence failed independent validation."""


def assemble(options: Any, job: dict[str, Any], state: dict[str, Any]) -> str | None:
    try:
        return _assemble(options, job, state)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        _save_bad(options, job["path"], exc)
        raise RepairAuditBlocked(type(exc).__name__) from None


def _assemble(options: Any, job: dict[str, Any], state: dict[str, Any]) -> str | None:
    from quant_market_data_platform.minute_gap_repair import _key

    entries = [state["jobs"].get(_key(job, code), {}) for code in job["missing"]]
    patches = [
        _patch_frame(options, job, code, entry)
        for code, entry in zip(job["missing"], entries, strict=True)
        if entry.get("outcome") == "patch_ready"
    ]
    if len(patches) != len(entries):
        return None
    from quant_market_data_platform._reverse_backfill_missing import _missing_symbols

    if (
        _sha256_file(job["path"]) != job["sha"]
        or _missing_symbols(job["path"], _read_json(job["path"])) != job["missing"]
    ):
        raise ValueError("source_changed_after_request")
    frames = [_read_existing_partition(job["path"].parent), *patches]
    source = job["source"]
    symbols = source["expected_symbols"]
    combined = _validate(pd.concat(frames, ignore_index=True), job["date"], symbols)
    run_id = f"repair-{uuid4().hex}"
    candidate = (
        options.artifacts_root
        / "staging/minute_gap_repair/candidates"
        / run_id
        / "data"
        / f"trade_date={job['date']}"
    )
    source_policy = source.get("request_policy", {})
    policy = TushareRequestPolicy(
        **{
            key: value
            for key, value in source_policy.items()
            if key in TushareRequestPolicy.__dataclass_fields__
        }
    )
    progress = _MinuteProgress(
        candidate,
        job["date"],
        "1min",
        source["universe_hash"],
        source["universe_rule"],
        source["universe_source"],
        frozenset(symbols),
        policy,
    )
    _persist_progress(progress, completed_symbols=set(symbols), frames=[combined], status="partial")
    _validate(_read_existing_partition(candidate), job["date"], symbols)
    _persist_progress(
        progress, completed_symbols=set(symbols), frames=[combined], status="complete"
    )
    try:
        validation = validate_complete_minute_partition(
            candidate, trade_date=job["date"], require_full_universe=False
        )
    except Exception as exc:
        sidecar = candidate / "_minute_mirror.json"
        payload = _read_json(sidecar)
        payload["status"] = "audit_blocked"
        _atomic_write_json(sidecar, payload)
        _save_bad(options, candidate, exc)
        raise RepairAuditBlocked(type(exc).__name__) from None
    destination = options.artifacts_root / "staging/tushare_minute_backfill_reverse/runs" / run_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    final_candidate = destination / "data" / f"trade_date={job['date']}"
    validation["partition_path"] = str(final_candidate / "part-00000.parquet")
    validation["sidecar_path"] = str(final_candidate / "_minute_mirror.json")
    receipt = {
        "source_sidecar": str(job["path"]),
        "source_sha256": job["sha"],
        "candidate": str(final_candidate),
        "trade_date": job["date"],
        "candidate_sha256": _sha256_file(candidate / "_minute_mirror.json"),
        "semantic_validation": "passed",
        "validation": validation,
        "patches": entries,
    }
    _atomic_write_json(candidate.parents[1] / "gap_repair.receipt.json", receipt)
    candidate.parents[1].rename(destination)
    state.setdefault("candidates", {})[job["sha"]] = receipt
    return str(final_candidate)


def _save_bad(options: Any, evidence: Path, error: BaseException) -> None:
    if (options.runtime_root / "semantic-audit-blocked.json").exists():
        return
    _atomic_write_json(
        options.runtime_root / "semantic-audit-blocked.json",
        {
            "status": "blocked",
            "source": "minute_gap_repair",
            "error_type": type(error).__name__,
            "evidence": str(evidence),
        },
    )


def _fetch(options: Any, job: dict[str, Any], code: str, ledger: Any) -> pd.DataFrame:
    from quant_market_data_platform.providers._mins_fetch import _fetch_minute_batch
    from quant_market_data_platform.providers.tushare_a_share import get_tushare_client

    pro = get_tushare_client(token_env=options.token_env, api_url=options.api_url)
    policy = replace(
        TushareRequestPolicy(), attempts=1, quota_cooldown_seconds=options.cooldown_seconds
    )
    return _fetch_minute_batch(pro, [code], job["date"], "1min", policy, ledger)


def _preserve_raw(raw: pd.DataFrame, evidence: Path) -> None:
    evidence.mkdir(parents=True, exist_ok=False)
    try:
        raw.to_parquet(evidence / "raw.parquet", index=False)
    except Exception:
        raw.to_json(evidence / "raw.json", orient="split", date_format="iso", default_handler=str)


def fetch_patch(  # noqa: PLR0913
    options: Any,
    job: dict[str, Any],
    code: str,
    state: dict[str, Any],
    state_path: Path,
    ledger: Any,
) -> dict[str, Any]:
    from quant_market_data_platform.minute_gap_repair import _daily_used, _key, _now, _result
    from quant_market_data_platform.tushare_minute_quota import (
        MinuteQuotaExceeded,
        MinuteQuotaPoolClosed,
    )

    used_before = _daily_used(ledger)
    key = _key(job, code)
    attempt = int(state["jobs"].get(key, {}).get("attempts", 0)) + 1
    entry: dict[str, Any] = {
        "attempts": attempt,
        "last_attempt": _now().isoformat(),
        "next_eligible": _now().timestamp() + 1800 * 2 ** (attempt - 1),
        "outcome": "attempting",
        "source_sha256": job["sha"],
        "trade_date": job["date"],
        "symbol": code,
    }
    state["jobs"][key] = entry
    _atomic_write_json(state_path, state)
    evidence = (
        options.artifacts_root
        / "staging/minute_gap_repair"
        / job["sha"]
        / job["date"]
        / code
        / f"attempt-{attempt}"
    )
    try:
        raw = _fetch(options, job, code, ledger)
    except Exception as exc:
        outcome = "request_failed"
        if isinstance(exc, (MinuteQuotaExceeded, MinuteQuotaPoolClosed)):
            outcome = (
                "quota_closed" if isinstance(exc, MinuteQuotaPoolClosed) else "quota_exhausted"
            )
            if _daily_used(ledger) == used_before:
                entry["attempts"] = attempt - 1
                entry["next_eligible"] = 0
        entry.update(outcome=outcome, error_type=type(exc).__name__)
        _atomic_write_json(state_path, state)
        return _result(outcome, error_type=type(exc).__name__)
    if raw.empty:
        entry["outcome"] = "missing_response"
    else:
        _preserve_raw(raw, evidence)
        try:
            frame = _validate(raw, job["date"], [code])
        except Exception as exc:
            _save_bad(options, evidence, exc)
            entry["outcome"] = "audit_blocked"
        else:
            patch = evidence / "patch.parquet"
            frame.to_parquet(patch, index=False)
            entry["outcome"] = "patch_ready"
            entry["patch_path"] = str(patch)
            entry["patch_sha256"] = _sha256_file(patch)
    _atomic_write_json(state_path, state)
    candidate = assemble(options, job, state) if entry["outcome"] == "patch_ready" else None
    _atomic_write_json(state_path, state)
    return _result(
        "candidate_ready" if candidate else entry["outcome"],
        candidate=candidate,
        trade_date=job["date"],
        symbol=code,
    )
