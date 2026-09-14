"""Small, resumable newest-to-oldest TuShare minute backfill scheduler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from market_data_platform._minute_operational_receipt import read_operational_receipt
from market_data_platform._reverse_backfill_bj_policy import persist_bj_report, scan_bj_missing
from market_data_platform.minute_candidate import MinuteCandidateError, _read_json
from market_data_platform.providers._env import resolve_tushare_api_url
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
from market_data_platform.published_assets import PublishedAssetContract
from market_data_platform.tushare_minute_backfill import (
    MinuteBackfillPlanOptions,
    MinuteBackfillRunOptions,
    build_minute_backfill_plan,
    run_minute_backfill,
)
from market_data_platform.tushare_minute_backfill_part01 import _load_open_dates


@dataclass(frozen=True)
class ReverseBackfillOptions:
    artifacts_root: Path
    token_env: str = "TUSHARE_TOKEN_2"
    api_url: str | None = None
    max_dates: int = 1
    bj_missing_policy: str = "report"
    dry_run: bool = False
    batch_size: int = 20
    cooldown_seconds: float = 1.2
    quota_cooldown_seconds: float = 390.0
    minute_quota_limit_requests: int = 10_000
    minute_quota_burst_limit_requests: int = 20_000
    minute_quota_safety_requests: int = 500
    minute_quota_limit_rows: int = 160_000_000
    minute_quota_safety_rows: int = 4_000_000


def _select_reverse_dates(
    *,
    open_dates: list[str],
    current_min_date: str,
    complete_dates: set[str],
    max_dates: int,
) -> list[str]:
    if max_dates <= 0:
        raise ValueError("max_dates must be positive")
    return [
        date
        for date in reversed(open_dates)
        if date < current_min_date and date not in complete_dates
    ][:max_dates]


def _scheduler_plan_stem(dates: list[str]) -> str:
    if not dates:
        raise ValueError("dates must not be empty")
    return f"reverse_{min(dates)}_{max(dates)}"


def _paths(root: Path) -> dict[str, Path]:
    return {
        "trade_calendar": root
        / "assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet",
        "instruments": root
        / "assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet",
        "alias": root / "assets/derived/a_share/minute_1m_tushare",
        "quota_db": root / "metadata/tushare/minute_quota/minute_quota.sqlite3",
        "metadata": root / "metadata/minute_backfill/reverse_scheduler",
        "staging": root / "staging/tushare_minute_backfill_reverse",
        "exceptions": root
        / "metadata/minute_backfill/reverse_pilot_20220714_provider_no_data_exceptions.v1.json",
    }


def _current_min_date(alias: Path) -> str:
    root = alias.parents[3]
    contract_path = root / "metadata/current_assets/a_share_current.json"
    if contract_path.exists():
        try:
            output_dir = (
                PublishedAssetContract.load(contract_path, artifacts_root=root)
                .asset("minute_1m_tushare")
                .resolved_path
            )
        except (KeyError, OSError, ValueError) as exc:
            raise MinuteCandidateError(
                f"Invalid current minute asset contract: {contract_path}"
            ) from exc
    else:
        output_dir = alias.resolve(strict=True)
    _, receipt = read_operational_receipt(output_dir)
    return str(receipt["summary"]["date_min"])


def _complete_dates(staging_root: Path, *, bj_missing_policy: str = "error") -> set[str]:
    complete: set[str] = set()
    for sidecar in staging_root.glob("runs/*/data/trade_date=*/_minute_mirror.json"):
        try:
            payload = _read_json(sidecar)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if payload.get("status") == "complete":
            trade_date = str(payload.get("trade_date", ""))
            if trade_date:
                complete.add(trade_date)
    if bj_missing_policy == "report":
        complete.update(report["trade_date"] for report in scan_bj_missing(staging_root))
    return complete


def _write_dates(path: Path, dates: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"dates": dates}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _plan_date_count(plan_path: Path) -> int | None:
    try:
        payload = _read_json(plan_path)
        if not isinstance(payload, dict):
            return None
        segments = payload.get("identity", {}).get("segments", payload.get("segments", []))
        if not isinstance(segments, list):
            return None
        return sum(
            len(segment.get("dates", []))
            for segment in segments
            if isinstance(segment, dict) and isinstance(segment.get("dates", []), list)
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _plan_dates(plan_path: Path) -> set[str]:
    try:
        plan = _read_json(plan_path)
        segments = plan.get("identity", {}).get("segments", plan.get("segments", []))
        return {date for segment in segments for date in segment["dates"]}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return set()


def _resume_paths(
    metadata: Path, *, max_dates: int = 1, settled_dates: set[str] | None = None
) -> tuple[Path, Path] | None:
    for plan_path in sorted(metadata.glob("plans/reverse_*.plan.json")):
        date_count = _plan_date_count(plan_path)
        if date_count is None or date_count > max_dates:
            continue
        if settled_dates:
            dates = _plan_dates(plan_path)
            if dates & settled_dates:
                continue
        receipt_path = metadata / "receipts" / plan_path.name.replace(".plan.json", ".receipt.json")
        if not receipt_path.exists():
            return plan_path, receipt_path
        try:
            if _read_json(receipt_path).get("status") != "complete":
                return plan_path, receipt_path
        except (OSError, ValueError, json.JSONDecodeError):
            return plan_path, receipt_path
    return None


def run_reverse_backfill(options: ReverseBackfillOptions) -> dict[str, Any]:
    """Acquire at most ``max_dates`` missing dates into isolated staging."""
    if options.bj_missing_policy not in {"report", "error"}:
        raise ValueError("bj_missing_policy must be report or error")
    root = options.artifacts_root.expanduser().resolve()
    paths = _paths(root)
    paths["metadata"].mkdir(parents=True, exist_ok=True)
    reports = scan_bj_missing(paths["staging"]) if options.bj_missing_policy == "report" else []
    settled = _complete_dates(paths["staging"]) | {report["trade_date"] for report in reports}
    report_path = paths["metadata"] / "bj_missing_report.json"
    if options.bj_missing_policy == "report" and not options.dry_run:
        persist_bj_report(report_path, reports)
    resume = _resume_paths(paths["metadata"], max_dates=options.max_dates, settled_dates=settled)
    if resume is None:
        current_min = _current_min_date(paths["alias"])
        open_dates = _load_open_dates(
            paths["trade_calendar"], start_date="20160101", end_date=current_min
        )
        dates = _select_reverse_dates(
            open_dates=open_dates,
            current_min_date=current_min,
            complete_dates=settled,
            max_dates=options.max_dates,
        )
        if not dates:
            return {"status": "noop", "reason": "reverse_history_is_complete_or_exhausted"}
        stem = _scheduler_plan_stem(dates)
        dates_path = paths["metadata"] / "dates" / f"{stem}.json"
        plan_path = paths["metadata"] / "plans" / f"{stem}.plan.json"
        if plan_path.exists() and _plan_dates(plan_path) != set(dates):
            digest = hashlib.sha256(",".join(dates).encode()).hexdigest()[:12]
            stem = f"{stem}_remaining_{digest}"
            dates_path = paths["metadata"] / "dates" / f"{stem}.json"
            plan_path = paths["metadata"] / "plans" / f"{stem}.plan.json"
        receipt_path = paths["metadata"] / "receipts" / f"{stem}.receipt.json"
        _write_dates(dates_path, dates)
        resolved_api_url = options.api_url or resolve_tushare_api_url(token_env=options.token_env)
        build_minute_backfill_plan(
            MinuteBackfillPlanOptions(
                start_date=min(dates),
                end_date=max(dates),
                scope="all-a",
                trade_cal_path=paths["trade_calendar"],
                instruments_path=paths["instruments"],
                dates_path=dates_path,
                backfill_root=paths["staging"],
                plan_path=plan_path,
                segment="month",
                date_order="descending",
                batch_size=options.batch_size,
                cooldown_seconds=options.cooldown_seconds,
                token_env=options.token_env,
                api_url=resolved_api_url,
                request_policy=TushareRequestPolicy(
                    attempts=2,
                    retry_sleep_seconds=1.0,
                    retry_max_sleep_seconds=10.0,
                    quota_cooldown_seconds=options.quota_cooldown_seconds,
                    request_timeout_seconds=15.0,
                ),
            )
        )
    else:
        plan_path, receipt_path = resume

    result = run_minute_backfill(
        MinuteBackfillRunOptions(
            plan_path=plan_path,
            receipt_path=receipt_path,
            token_env=options.token_env,
            api_url=options.api_url,
            provider_no_data_exceptions_path=paths["exceptions"],
            dry_run=options.dry_run,
            minute_quota_mode="enforce",
            minute_quota_db=paths["quota_db"],
            minute_quota_consumer="reverse_backfill",
            minute_quota_gate="requests",
            minute_quota_limit_requests=options.minute_quota_limit_requests,
            minute_quota_burst_limit_requests=options.minute_quota_burst_limit_requests,
            minute_quota_safety_requests=options.minute_quota_safety_requests,
            minute_quota_allow_burst=False,
            minute_quota_limit_rows=options.minute_quota_limit_rows,
            minute_quota_safety_rows=options.minute_quota_safety_rows,
        )
    )
    status = result["status"]
    if options.bj_missing_policy == "report" and not options.dry_run:
        reports = scan_bj_missing(paths["staging"])
        persist_bj_report(report_path, reports)
        dates = _plan_dates(plan_path)
        settled = _complete_dates(paths["staging"]) | {report["trade_date"] for report in reports}
        if status == "partial" and dates and dates.issubset(settled):
            status = "accepted_bj_missing"
    return {
        "status": status,
        "bj_missing_policy": options.bj_missing_policy,
        "bj_missing_report": (
            str(report_path)
            if options.bj_missing_policy == "report" and not options.dry_run
            else None
        ),
        "plan": str(plan_path),
        "receipt": str(receipt_path),
        "dates": result.get("summary", {}).get("dates"),
        "rows": result.get("summary", {}).get("rows"),
    }


__all__ = [
    "ReverseBackfillOptions",
    "run_reverse_backfill",
    "_scheduler_plan_stem",
    "_select_reverse_dates",
]
