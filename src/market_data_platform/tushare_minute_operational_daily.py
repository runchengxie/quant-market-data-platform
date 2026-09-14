"""Daily catch-up and atomic promotion for TuShare-native minute data."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from market_data_platform._minute_operational_receipt import read_operational_receipt
from market_data_platform.minute_candidate import MinuteCandidateError
from market_data_platform.providers._env import resolve_tushare_api_url
from market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
from market_data_platform.published_assets import PublishedAssetContract
from market_data_platform.tushare_minute_backfill import (
    MinuteBackfillPlanOptions,
    MinuteBackfillRunOptions,
    build_minute_backfill_plan,
    run_minute_backfill,
)
from market_data_platform.tushare_minute_operational import (
    OperationalAssembly,
    assemble_operational_version,
    promote_operational_alias,
)


@dataclass(frozen=True)
class OperationalDailyOptions:
    artifacts_root: Path
    token_env: str = "TUSHARE_TOKEN_2"
    fallback_token_env: str | None = "TUSHARE_TOKEN"
    target_date: str | None = None
    api_url: str | None = None
    batch_size: int = 33
    cooldown_seconds: float = 1.2
    quota_cooldown_seconds: float = 390.0
    minute_quota_limit_requests: int = 10_000
    minute_quota_burst_limit_requests: int = 20_000
    minute_quota_safety_requests: int = 500
    minute_quota_limit_rows: int = 160_000_000
    minute_quota_safety_rows: int = 4_000_000


@dataclass(frozen=True)
class _OperationalPaths:
    trade_calendar: Path
    instruments: Path
    alias: Path
    legacy_alias: Path
    metadata_root: Path
    backfill_root: Path
    quota_db: Path


def _paths(root: Path) -> _OperationalPaths:
    return _OperationalPaths(
        trade_calendar=root / "assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet",
        instruments=root
        / "assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet",
        alias=root / "assets/derived/a_share/minute_1m_tushare",
        legacy_alias=root / "assets/derived/a_share/minute_1m",
        metadata_root=root / "metadata/minute_operational",
        backfill_root=root / "staging/tushare_minute_operational_daily",
        quota_db=root / "metadata/tushare/minute_quota/minute_quota.sqlite3",
    )


def _latest_open_date(trade_calendar: Path, target_date: str) -> str:
    rows = pq.read_table(trade_calendar, columns=["cal_date", "is_open"]).to_pylist()
    eligible = [
        str(row["cal_date"])
        for row in rows
        if int(row["is_open"]) == 1 and str(row["cal_date"]) <= target_date
    ]
    if not eligible:
        raise MinuteCandidateError(f"No open trading date at or before {target_date}")
    return max(eligible)


def _next_calendar_date(trade_date: str) -> str:
    value = datetime.strptime(trade_date, "%Y%m%d").date() + timedelta(days=1)
    return value.strftime("%Y%m%d")


def _target_date(options: OperationalDailyOptions) -> str:
    return options.target_date or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")


def _current_operational_dir(paths: _OperationalPaths) -> Path:
    contract_path = paths.metadata_root.parent / "current_assets" / "a_share_current.json"
    if contract_path.exists():
        try:
            return (
                PublishedAssetContract.load(contract_path, artifacts_root=paths.alias.parents[3])
                .asset("minute_1m_tushare")
                .resolved_path
            )
        except (KeyError, OSError, ValueError) as exc:
            raise MinuteCandidateError(
                f"Invalid current minute asset contract: {contract_path}"
            ) from exc
    return paths.alias.resolve(strict=True)


def _base_receipt(paths: _OperationalPaths) -> tuple[Path, dict[str, Any]]:
    output_dir = _current_operational_dir(paths)
    return read_operational_receipt(output_dir)


def _build_daily_plan(
    options: OperationalDailyOptions,
    paths: _OperationalPaths,
    *,
    start_date: str,
    end_date: str,
    plan_path: Path,
) -> dict[str, Any]:
    resolved_api_url = options.api_url or resolve_tushare_api_url(token_env=options.token_env)
    return build_minute_backfill_plan(
        MinuteBackfillPlanOptions(
            start_date=start_date,
            end_date=end_date,
            scope="all-a",
            trade_cal_path=paths.trade_calendar,
            instruments_path=paths.instruments,
            backfill_root=paths.backfill_root,
            plan_path=plan_path,
            segment="month",
            batch_size=options.batch_size,
            cooldown_seconds=options.cooldown_seconds,
            token_env=options.token_env,
            api_url=resolved_api_url,
            request_policy=TushareRequestPolicy(
                attempts=3,
                retry_sleep_seconds=2.0,
                retry_max_sleep_seconds=30.0,
                quota_cooldown_seconds=options.quota_cooldown_seconds,
            ),
        )
    )


def _run_daily_plan(
    options: OperationalDailyOptions,
    paths: _OperationalPaths,
    plan_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    return run_minute_backfill(
        MinuteBackfillRunOptions(
            plan_path=plan_path,
            receipt_path=receipt_path,
            token_env=options.token_env,
            api_url=options.api_url,
            minute_quota_mode="enforce",
            minute_quota_db=paths.quota_db,
            minute_quota_consumer="operational_canonical",
            minute_quota_gate="requests",
            minute_quota_limit_requests=options.minute_quota_limit_requests,
            minute_quota_burst_limit_requests=options.minute_quota_burst_limit_requests,
            minute_quota_safety_requests=options.minute_quota_safety_requests,
            minute_quota_allow_burst=False,
            minute_quota_limit_rows=options.minute_quota_limit_rows,
            minute_quota_safety_rows=options.minute_quota_safety_rows,
        )
    )


def _attempt_token_envs(options: OperationalDailyOptions) -> tuple[str, ...]:
    values = [options.token_env, options.fallback_token_env]
    return tuple(dict.fromkeys(value for value in values if value))


def _backfill_failure_reason(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for segment in payload.get("segments", []):
        error = segment.get("error")
        if isinstance(error, dict) and error.get("type"):
            return str(error["type"])
    return None


def run_operational_daily(options: OperationalDailyOptions) -> dict[str, Any]:
    """Catch up every missing open date, publish a successor, and move only TuShare."""

    root = options.artifacts_root.expanduser().resolve()
    paths = _paths(root)
    base_receipt_path, base_receipt = _base_receipt(paths)
    target = _latest_open_date(paths.trade_calendar, _target_date(options))
    base_max = str(base_receipt["summary"]["date_max"])
    if target <= base_max:
        return {
            "status": "noop",
            "reason": "operational_version_is_current",
            "date_max": base_max,
        }
    start_date = _next_calendar_date(base_max)
    run_metadata = paths.metadata_root / "runs"
    attempts: list[dict[str, Any]] = []
    completed: tuple[OperationalDailyOptions, Path, dict[str, Any]] | None = None
    for attempt_index, token_env in enumerate(_attempt_token_envs(options)):
        attempt_options = replace(
            options,
            token_env=token_env,
            api_url=options.api_url or resolve_tushare_api_url(token_env=token_env),
        )
        resolved_api_url = attempt_options.api_url
        endpoint_fingerprint = hashlib.sha256(
            (resolved_api_url or "tushare-default").encode("utf-8")
        ).hexdigest()[:8]
        # Preserve the historical primary-run path for resumability. A fallback
        # credential gets its own immutable plan/receipt because quota state is
        # keyed by credential fingerprint.
        suffix = (
            endpoint_fingerprint
            if attempt_index == 0
            else f"{endpoint_fingerprint}_{token_env.lower()}"
        )
        run_id = f"{start_date}_{target}_{suffix}"
        plan_path = run_metadata / f"{run_id}.plan.json"
        backfill_receipt_path = run_metadata / f"{run_id}.backfill.json"
        plan = _build_daily_plan(
            attempt_options,
            paths,
            start_date=start_date,
            end_date=target,
            plan_path=plan_path,
        )
        backfill = _run_daily_plan(attempt_options, paths, plan_path, backfill_receipt_path)
        status = str(backfill.get("status"))
        attempts.append(
            {
                "token_env": token_env,
                "status": status,
                "backfill_receipt": str(backfill_receipt_path),
                "failure_reason": _backfill_failure_reason(backfill_receipt_path),
            }
        )
        if status == "complete":
            completed = (attempt_options, Path(plan["output"]["data_dir"]), plan)
            break
    if completed is None:
        return {
            "status": "download_incomplete",
            "target_date": target,
            "attempts": attempts,
            "backfill_receipt": attempts[-1]["backfill_receipt"],
            "failure_reason": attempts[-1]["failure_reason"],
        }
    _completed_options, incremental_data_dir, plan = completed
    version_dir = paths.alias.parent / f"minute_1m_tushare_v1_{target}"
    version_receipt = paths.metadata_root / "versions" / f"minute_1m_tushare_v1_{target}.json"
    version = assemble_operational_version(
        OperationalAssembly(
            base_receipt=base_receipt_path,
            incremental_roots=(incremental_data_dir,),
            trade_calendar=paths.trade_calendar,
            end_date=target,
            output_dir=version_dir,
            receipt_json=version_receipt,
        )
    )
    promotion_receipt = paths.metadata_root / "promotions" / f"minute_1m_tushare_v1_{target}.json"
    promotion = promote_operational_alias(
        version_receipt,
        paths.alias,
        paths.legacy_alias,
        promotion_receipt,
    )
    return {
        "status": "operational_canonical_updated",
        "date_max": target,
        "dates": version["summary"]["dates"],
        "rows": version["summary"]["rows"],
        "version_receipt": str(version_receipt),
        "promotion_receipt": str(promotion_receipt),
        "legacy_canonical_mutated": promotion["legacy_canonical"]["mutated"],
    }


__all__ = ["OperationalDailyOptions", "run_operational_daily"]
