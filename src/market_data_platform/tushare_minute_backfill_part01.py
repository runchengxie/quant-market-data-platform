"""Immutable planning and resumable execution for TuShare minute backfills."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import pandas as pd

from market_data_platform.providers.tushare_a_share_dates import _validate_date
from market_data_platform.providers.tushare_a_share_mins import (
    DEFAULT_MINS_BATCH_SIZE,
    validate_mins_batch_size,
)
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy

MINUTE_BACKFILL_PLAN_SCHEMA = "tushare.a_share.minute_backfill.plan.v2"

MINUTE_BACKFILL_RECEIPT_SCHEMA = "tushare.a_share.minute_backfill.receipt.v2"

MINUTE_BACKFILL_SCOPES = ("bj-only", "all-a")

MINUTE_BACKFILL_SEGMENTS = ("month", "year")

BSE_FIRST_TRADE_DATE = "20211115"

MinuteBackfillScope = Literal["bj-only", "all-a"]

MinuteBackfillSegment = Literal["month", "year"]

MinuteBackfillDateOrder = Literal["ascending", "descending"]


class MinuteBackfillBudgetError(RuntimeError):
    """Raised when runtime calls invalidate a plan's conservative upper bound."""


@dataclass(frozen=True)
class MinuteBackfillPlanOptions:
    """Inputs for an offline, immutable minute backfill plan."""

    start_date: str
    end_date: str
    scope: MinuteBackfillScope
    trade_cal_path: str | Path
    instruments_path: str | Path
    backfill_root: str | Path
    dates_path: str | Path | None = None
    plan_path: str | Path | None = None
    segment: MinuteBackfillSegment = "year"
    date_order: MinuteBackfillDateOrder = "ascending"
    batch_size: int = DEFAULT_MINS_BATCH_SIZE
    cooldown_seconds: float = 1.0
    request_budget: int | None = None
    max_dates: int | None = None
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    request_policy: TushareRequestPolicy | None = None
    workers: int = 1
    dry_run: bool = False


@dataclass(frozen=True)
class MinuteBackfillRunOptions:
    """Inputs for executing one immutable minute backfill plan."""

    plan_path: str | Path
    receipt_path: str | Path
    token_env: str | None = None
    api_url: str | None = None
    provider_no_data_exceptions_path: str | Path | None = None
    dry_run: bool = False
    workers: int = 1
    minute_quota_mode: str | None = None
    minute_quota_db: str | Path | None = None
    minute_quota_consumer: str | None = None
    minute_quota_limit_rows: int | None = None
    minute_quota_safety_rows: int | None = None
    minute_quota_gate: str | None = None
    minute_quota_limit_requests: int | None = None
    minute_quota_burst_limit_requests: int | None = None
    minute_quota_safety_requests: int | None = None
    minute_quota_allow_burst: bool | str | None = None


@dataclass(frozen=True)
class _MinuteBackfillExecution:
    receipt_path: Path
    run_root: Path
    data_dir: Path
    token_env: str
    resolved_api_url: str | None
    provider_no_data_exceptions_path: str | Path | None
    policy: TushareRequestPolicy
    exchange: str | None
    secrets: list[str]
    limits: dict[str, Any]
    minute_quota_mode: str | None
    minute_quota_db: str | Path | None
    minute_quota_consumer: str | None
    minute_quota_limit_rows: int | None
    minute_quota_safety_rows: int | None
    minute_quota_gate: str | None
    minute_quota_limit_requests: int | None
    minute_quota_burst_limit_requests: int | None
    minute_quota_safety_requests: int | None
    minute_quota_allow_burst: bool | str | None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _expanded(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fingerprint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Minute backfill planning source not found: {path}")
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256_file(path),
    }


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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid minute backfill JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Minute backfill JSON must contain an object: {path}")
    return payload


def _normalize_nullable_dates(values: pd.Series) -> pd.Series:
    normalized = values.astype("string").fillna("").str.strip()
    normalized = normalized.replace({"None": "", "none": "", "nan": "", "NaT": "", "<NA>": ""})
    return normalized.str.replace("-", "", regex=False).str.replace(r"\.0$", "", regex=True)


def _load_open_dates(path: Path, *, start_date: str, end_date: str) -> list[str]:
    frame = pd.read_parquet(path, columns=["cal_date", "is_open"])
    if frame.empty:
        raise ValueError(f"Trade calendar is empty: {path}")
    dates = _normalize_nullable_dates(frame["cal_date"])
    if dates.eq("").any() or not dates.str.fullmatch(r"\d{8}").all():
        raise ValueError(f"Trade calendar contains invalid cal_date values: {path}")
    is_open = pd.to_numeric(frame["is_open"], errors="raise")
    if not is_open.isin([0, 1]).all():
        raise ValueError(f"Trade calendar is_open must contain only 0 or 1: {path}")
    normalized = pd.DataFrame({"cal_date": dates, "is_open": is_open.astype(int)})
    contradictions = normalized.groupby("cal_date")["is_open"].nunique()
    if contradictions.gt(1).any():
        raise ValueError(f"Trade calendar has contradictory duplicate dates: {path}")
    return sorted(
        set(
            normalized.loc[
                normalized["is_open"].eq(1) & normalized["cal_date"].between(start_date, end_date),
                "cal_date",
            ]
        )
    )


def _requested_dates_from_json(raw_text: str, path: Path) -> list[str]:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON minute backfill dates file: {path}") from exc
    if isinstance(payload, dict):
        payload = next(
            (
                payload[key]
                for key in ("dates", "trade_dates", "target_dates", "selected_dates")
                if isinstance(payload.get(key), list)
            ),
            payload,
        )
    if not isinstance(payload, list):
        raise ValueError(
            "JSON minute backfill dates file must be an array or contain a dates array"
        )
    return [str(value).strip() for value in payload if str(value).strip()]


def _requested_dates_from_text(raw_text: str) -> list[str]:
    raw_dates: list[str] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.split("#", maxsplit=1)[0].strip()
        if line:
            raw_dates.extend(value for value in re.split(r"[\s,]+", line) if value)
    return raw_dates


def _load_requested_dates(path: Path) -> list[str]:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"Minute backfill dates file not found: {path}") from exc
    raw_dates = (
        _requested_dates_from_json(raw_text, path)
        if path.suffix.lower() == ".json"
        else _requested_dates_from_text(raw_text)
    )
    dates = [_validate_date(value) for value in raw_dates]
    if not dates:
        raise ValueError(f"Minute backfill dates file is empty: {path}")
    if len(dates) != len(set(dates)):
        raise ValueError(f"Minute backfill dates file contains duplicate dates: {path}")
    return sorted(dates)


def _canonical_instrument_intervals(
    path: Path, *, scope: MinuteBackfillScope
) -> tuple[list[str], list[str], dict[str, int]]:
    frame = pd.read_parquet(
        path,
        columns=["ts_code", "list_date", "delist_date", "curr_type"],
    )
    if frame.empty:
        raise ValueError(f"Instrument master is empty: {path}")
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame["curr_type"] = frame["curr_type"].astype(str).str.strip().str.upper()
    frame["list_date"] = _normalize_nullable_dates(frame["list_date"])
    frame["delist_date"] = _normalize_nullable_dates(frame["delist_date"])
    canonical = frame["ts_code"].str.fullmatch(r"\d{6}\.(SH|SZ|BJ)")
    eligible = frame[frame["curr_type"].eq("CNY") & canonical].copy()
    if scope == "bj-only":
        eligible = eligible[eligible["ts_code"].str.endswith(".BJ")]
    invalid_list_dates = eligible[~eligible["list_date"].str.fullmatch(r"\d{8}")]
    invalid_delist_dates = eligible[
        eligible["delist_date"].ne("") & ~eligible["delist_date"].str.fullmatch(r"\d{8}")
    ]
    if not invalid_list_dates.empty or not invalid_delist_dates.empty:
        raise ValueError(f"Instrument master contains invalid listing intervals: {path}")
    eligible = eligible.drop_duplicates(["ts_code", "list_date", "delist_date"], keep="last")
    return (
        sorted(eligible["list_date"].tolist()),
        sorted(value for value in eligible["delist_date"] if value),
        {
            "input_rows": len(frame),
            "canonical_cny_rows": int((frame["curr_type"].eq("CNY") & canonical).sum()),
            "scope_rows": len(eligible),
            "scope_symbols": eligible["ts_code"].nunique(),
            "excluded_noncanonical_rows": int((~canonical).sum()),
        },
    )


def _active_symbol_count(trade_date: str, *, list_dates: list[str], delist_dates: list[str]) -> int:
    listed = bisect_right(list_dates, trade_date)
    delisted_before_date = bisect_left(delist_dates, trade_date)
    return max(0, listed - delisted_before_date)


def _endpoint_identifier(api_url: str | None) -> str:
    if not api_url:
        return "tushare-default"
    parsed = urlsplit(api_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("TuShare endpoint must be an HTTP(S) URL")
    host = parsed.hostname.lower()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{host}{path}"


def _policy_payload(policy: TushareRequestPolicy) -> dict[str, Any]:
    payload = {
        "attempts": policy.attempts,
        "retry_sleep_seconds": policy.retry_sleep_seconds,
        "retry_max_sleep_seconds": policy.retry_max_sleep_seconds,
        "quota_cooldown_seconds": policy.quota_cooldown_seconds,
        "disable_proxy": policy.disable_proxy,
    }
    if policy.request_timeout_seconds is not None:
        payload["request_timeout_seconds"] = policy.request_timeout_seconds
    return payload


def _policy_from_payload(payload: dict[str, Any]) -> TushareRequestPolicy:
    return TushareRequestPolicy(
        attempts=int(payload["attempts"]),
        retry_sleep_seconds=float(payload["retry_sleep_seconds"]),
        retry_max_sleep_seconds=float(payload["retry_max_sleep_seconds"]),
        quota_cooldown_seconds=float(payload["quota_cooldown_seconds"]),
        disable_proxy=bool(payload["disable_proxy"]),
        request_timeout_seconds=(
            None
            if payload.get("request_timeout_seconds") is None
            else float(payload["request_timeout_seconds"])
        ),
    )


def _plan_id(identity: dict[str, Any]) -> str:
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _segment_dates(
    selected: list[tuple[str, int, int, int]], *, segment: MinuteBackfillSegment
) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[str, int, int, int]]] = {}
    for entry in selected:
        period = entry[0][:6] if segment == "month" else entry[0][:4]
        groups.setdefault(period, []).append(entry)
    segments: list[dict[str, Any]] = []
    for index, (period, entries) in enumerate(groups.items(), start=1):
        dates = [entry[0] for entry in entries]
        active_counts = [entry[1] for entry in entries]
        segments.append(
            {
                "segment_id": f"{index:04d}-{period}",
                "period": period,
                # Keep API range bounds chronological even when execution dates
                # are intentionally ordered newest-first.
                "start_date": min(dates),
                "end_date": max(dates),
                "dates": dates,
                "date_count": len(dates),
                "estimated_active_symbols_min": min(active_counts),
                "estimated_active_symbols_max": max(active_counts),
                "estimated_symbol_dates": sum(active_counts),
                "active_interval_estimated_minute_requests": sum(entry[2] for entry in entries),
                "minute_request_upper_bound": sum(entry[3] for entry in entries),
            }
        )
    return segments


def _validate_plan_options(options: MinuteBackfillPlanOptions) -> tuple[str, str]:
    start_date = _validate_date(options.start_date)
    end_date = _validate_date(options.end_date)
    if start_date > end_date:
        raise ValueError(f"start_date {start_date} > end_date {end_date}")
    if options.scope not in MINUTE_BACKFILL_SCOPES:
        raise ValueError(f"Unsupported minute backfill scope: {options.scope}")
    if options.segment not in MINUTE_BACKFILL_SEGMENTS:
        raise ValueError(f"Unsupported minute backfill segment: {options.segment}")
    validate_mins_batch_size(options.batch_size)
    if options.cooldown_seconds < 0:
        raise ValueError("cooldown_seconds must be non-negative")
    if options.request_budget is not None and options.request_budget <= 0:
        raise ValueError("request_budget must be positive")
    if options.max_dates is not None and options.max_dates <= 0:
        raise ValueError("max_dates must be positive")
    if options.workers != 1:
        raise ValueError("Minute backfill supports exactly one worker")
    if not options.dry_run and options.plan_path is None:
        raise ValueError("plan_path is required unless dry_run is enabled")
    return start_date, end_date


def _write_immutable_plan(plan: dict[str, Any], path: Path) -> dict[str, Any]:
    if path.exists():
        existing = _read_json(path)
        if (
            existing.get("schema_version") != MINUTE_BACKFILL_PLAN_SCHEMA
            or existing.get("plan_id") != plan["plan_id"]
        ):
            raise FileExistsError(f"Refusing to replace a different minute backfill plan: {path}")
        return existing
    _atomic_write_json(plan, path)
    return plan
