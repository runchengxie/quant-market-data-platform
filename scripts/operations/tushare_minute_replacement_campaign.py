#!/usr/bin/env python3
"""Prepare and advance the staged Guan-to-TuShare minute replacement campaign.
The campaign is intentionally acquisition-only.  It never writes a production
alias and treats the per-date completeness sidecar as the source of truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

# This file is a systemd entrypoint and is invoked by absolute path.  The
# production shared venv is non-editable, therefore the src-layout package is
# not importable unless the repository paths are bootstrapped explicitly.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
for _path in (_REPOSITORY_ROOT, _SOURCE_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import market_data_platform.tushare_minute_replacement_campaign_reconcile as reconcile_module  # noqa: E402
import market_data_platform.tushare_minute_replacement_campaign_runner as campaign_runner  # noqa: E402
from market_data_platform.providers.tushare_a_share_mins import (  # noqa: E402
    COMPLETENESS_FILENAME,
    validate_complete_minute_partition,
)
from market_data_platform.tushare_minute_replacement_campaign_defaults import (  # noqa: E402
    DEFAULT_BLOCKERS,
)

SCHEMA_VERSION = "tushare.minute_replacement_campaign.v1"
campaign_status = campaign_runner.campaign_status
run_budgeted = campaign_runner.run_budgeted
run_next = campaign_runner.run_next


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_dates(path: Path) -> list[str]:
    payload = _read_json(path)
    values = payload.get("dates") if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise ValueError(f"Date file must contain a JSON list or dates field: {path}")
    dates = [str(value).strip() for value in values]
    if any(len(value) != 8 or not value.isdigit() for value in dates):
        raise ValueError(f"Date file contains a non-YYYYMMDD value: {path}")
    if len(dates) != len(set(dates)):
        raise ValueError(f"Date file contains duplicate values: {path}")
    return sorted(dates)


def _chunks(values: Sequence[str], size: int) -> list[list[str]]:
    return [list(values[index : index + size]) for index in range(0, len(values), size)]


def build_campaign_days(
    fresh_dates: Sequence[str], *, dates_per_day: int, canary_per_lane: int
) -> list[dict[str, Any]]:
    """Split sorted dates into disjoint, balanced two-lane campaign days."""
    if dates_per_day < 2 or dates_per_day % 2:
        raise ValueError("dates_per_day must be an even integer of at least 2")
    if not 1 <= canary_per_lane <= dates_per_day // 2:
        raise ValueError("canary_per_lane must fit within one lane")
    days: list[dict[str, Any]] = []
    for day_index, day_dates in enumerate(_chunks(sorted(fresh_dates), dates_per_day), start=1):
        lane_a = day_dates[::2]
        lane_b = day_dates[1::2]
        phases: list[dict[str, Any]] = []
        if day_index == 1:
            phases.append(
                {
                    "name": "canary",
                    "lanes": {
                        "a": lane_a[:canary_per_lane],
                        "b": lane_b[:canary_per_lane],
                    },
                }
            )
            remainder = {
                "a": lane_a[canary_per_lane:],
                "b": lane_b[canary_per_lane:],
            }
            if remainder["a"] or remainder["b"]:
                phases.append({"name": "main", "lanes": remainder})
        else:
            phases.append({"name": "main", "lanes": {"a": lane_a, "b": lane_b}})
        days.append(
            {
                "day": day_index,
                "dates": day_dates,
                "phases": phases,
            }
        )
    return days


def _sidecar_inventory(
    roots: Iterable[Path], target_dates: set[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    complete: dict[str, dict[str, Any]] = {}
    partial_candidates: dict[str, list[dict[str, Any]]] = {}
    for root in roots:
        for sidecar_path in sorted(root.glob(f"trade_date=*/{COMPLETENESS_FILENAME}")):
            payload = _read_json(sidecar_path)
            trade_date = str(payload.get("trade_date", ""))
            if trade_date not in target_dates:
                continue
            if payload.get("status") == "complete":
                receipt = validate_complete_minute_partition(
                    sidecar_path.parent,
                    trade_date=trade_date,
                    require_full_universe=True,
                )
                complete[trade_date] = {
                    "data_root": str(root),
                    "rows": receipt["rows"],
                    "partition_sha256": receipt["partition_sha256"],
                    "sidecar_sha256": receipt["sidecar_sha256"],
                }
                continue
            completed_symbols = payload.get("completed_symbols") or []
            partial_candidates.setdefault(trade_date, []).append(
                {
                    "data_root": str(root),
                    "completed_symbols": len(completed_symbols),
                    "expected_symbols": len(payload.get("expected_symbols") or []),
                    "rows": int((payload.get("partition") or {}).get("rows") or 0),
                    "generated_at": str(payload.get("generated_at", "")),
                }
            )
    partial: dict[str, dict[str, Any]] = {}
    for trade_date, candidates in partial_candidates.items():
        if trade_date in complete:
            continue
        partial[trade_date] = max(
            candidates,
            key=lambda item: (item["completed_symbols"], item["generated_at"]),
        )
    return complete, partial


class _PlannerConfig(NamedTuple):
    marketdata_bin: Path
    trade_cal: Path
    instruments: Path
    token_env: str
    batch_size: int
    cooldown_seconds: float
    retry_attempts: int
    quota_cooldown_seconds: float


class _PrepareContext(NamedTuple):
    args: argparse.Namespace
    campaign_dir: Path
    planner: _PlannerConfig


def _planner_command(
    config: _PlannerConfig,
    *,
    dates_path: Path,
    backfill_root: Path,
    plan_path: Path,
) -> list[str]:
    dates = _load_dates(dates_path)
    return [
        str(config.marketdata_bin),
        "tushare",
        "plan-a-share-minute-backfill",
        "--scope",
        "all-a",
        "--start-date",
        min(dates),
        "--end-date",
        max(dates),
        "--dates-file",
        str(dates_path),
        "--trade-cal",
        str(config.trade_cal),
        "--instruments",
        str(config.instruments),
        "--backfill-root",
        str(backfill_root),
        "--plan",
        str(plan_path),
        "--segment",
        "month",
        "--batch-size",
        str(config.batch_size),
        "--cooldown-seconds",
        str(config.cooldown_seconds),
        "--workers",
        "1",
        "--token-env",
        config.token_env,
        "--retry-attempts",
        str(config.retry_attempts),
        "--retry-sleep-seconds",
        "2",
        "--retry-max-sleep-seconds",
        "30",
        "--quota-cooldown-seconds",
        str(config.quota_cooldown_seconds),
    ]


def _plan_lane(
    context: _PrepareContext,
    day_index: int,
    phase_name: str,
    lane_name: str,
    dates: list[str],
) -> dict[str, Any]:
    stem = f"day-{day_index:02d}-{phase_name}-lane-{lane_name}"
    dates_path = context.campaign_dir / "dates" / f"{stem}.json"
    plan_path = context.campaign_dir / "plans" / f"{stem}.plan.json"
    receipt_path = context.campaign_dir / "receipts" / f"{stem}.receipt.json"
    backfill_root = (
        context.args.staging_root / f"day-{day_index:02d}" / phase_name / f"lane-{lane_name}"
    )
    _atomic_write_json(dates_path, {"dates": dates})
    command = _planner_command(
        context.planner,
        dates_path=dates_path,
        backfill_root=backfill_root,
        plan_path=plan_path,
    )
    subprocess.run(command, check=True, cwd=context.args.repo_root)
    plan = _read_json(plan_path)
    if plan["output"]["writes_production"]:
        raise RuntimeError(f"Planner unexpectedly selected production: {plan_path}")
    return {
        "dates": dates,
        "dates_path": str(dates_path),
        "dates_sha256": _sha256(dates_path),
        "plan_path": str(plan_path),
        "plan_sha256": _sha256(plan_path),
        "plan_id": plan["plan_id"],
        "receipt_path": str(receipt_path),
        "data_root": plan["output"]["data_dir"],
        "writes_production": plan["output"]["writes_production"],
    }


def prepare_campaign(args: argparse.Namespace) -> int:
    campaign_dir = args.campaign_dir.expanduser().resolve()
    manifest_path = campaign_dir / "manifest.json"
    if manifest_path.exists():
        raise FileExistsError(f"Campaign manifest already exists: {manifest_path}")
    target_dates = _load_dates(args.target_dates)
    complete, partial = _sidecar_inventory(args.existing_data_root, set(target_dates))
    fresh_dates = sorted(set(target_dates) - set(complete) - set(partial))
    days = build_campaign_days(
        fresh_dates,
        dates_per_day=args.dates_per_day,
        canary_per_lane=args.canary_per_lane,
    )
    context = _PrepareContext(
        args=args,
        campaign_dir=campaign_dir,
        planner=_PlannerConfig(
            marketdata_bin=args.marketdata_bin,
            trade_cal=args.trade_cal,
            instruments=args.instruments,
            token_env=args.token_env,
            batch_size=args.batch_size,
            cooldown_seconds=args.cooldown_seconds,
            retry_attempts=args.retry_attempts,
            quota_cooldown_seconds=args.quota_cooldown_seconds,
        ),
    )
    planned_days: list[dict[str, Any]] = []
    for day in days:
        planned_phases: list[dict[str, Any]] = []
        for phase in day["phases"]:
            planned_lanes: dict[str, Any] = {}
            for lane_name, dates in phase["lanes"].items():
                if not dates:
                    continue
                planned_lanes[lane_name] = _plan_lane(
                    context,
                    int(day["day"]),
                    str(phase["name"]),
                    str(lane_name),
                    dates,
                )
            planned_phases.append({"name": phase["name"], "lanes": planned_lanes})
        planned_days.append({"day": day["day"], "dates": day["dates"], "phases": planned_phases})
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": _now(),
        "target": {
            "dates_path": str(args.target_dates),
            "dates_sha256": _sha256(args.target_dates),
            "dates": len(target_dates),
        },
        "baseline": {
            "complete": complete,
            "partial": partial,
            "fresh_dates": len(fresh_dates),
        },
        "config": {
            "repo_root": str(args.repo_root),
            "marketdata_bin": str(args.marketdata_bin),
            "token_env": args.token_env,
            "batch_size": args.batch_size,
            "cooldown_seconds": args.cooldown_seconds,
            "retry_attempts": args.retry_attempts,
            "quota_cooldown_seconds": args.quota_cooldown_seconds,
            "stagger_seconds": args.stagger_seconds,
            "blocker_services": list(args.blocker_service),
        },
        "days": planned_days,
        "ledger_path": str(campaign_dir / "ledger.json"),
        "lock_path": str(campaign_dir / "campaign.lock"),
        "provider_no_data_exceptions_path": str(
            campaign_dir / "provider_no_data_exceptions.v1.json"
        ),
    }
    _atomic_write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "manifest": str(manifest_path),
                "target_dates": len(target_dates),
                "baseline_complete": len(complete),
                "preflight_partial": len(partial),
                "fresh_dates": len(fresh_dates),
                "campaign_days": len(planned_days),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def _add_shared_quota_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ignore-blocker-service",
        action="append",
        default=[],
        help="Ignore a matching immutable manifest blocker for this run only.",
    )
    parser.add_argument(
        "--minute-quota-mode",
        choices=("off", "observe", "enforce"),
        default=None,
    )
    parser.add_argument("--minute-quota-db", type=_path)
    parser.add_argument("--minute-quota-consumer")
    parser.add_argument("--minute-quota-limit-rows", type=_positive_int)
    parser.add_argument("--minute-quota-safety-rows", type=_non_negative_int)
    parser.add_argument("--minute-quota-gate", choices=("rows", "requests", "dual"))
    parser.add_argument("--minute-quota-limit-requests", type=_positive_int)
    parser.add_argument("--minute-quota-burst-limit-requests", type=_positive_int)
    parser.add_argument("--minute-quota-safety-requests", type=_non_negative_int)
    parser.add_argument(
        "--minute-quota-allow-burst",
        action=argparse.BooleanOptionalAction,
        default=None,
    )


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--repo-root", type=_path, required=True)
    prepare.add_argument("--marketdata-bin", type=_path, required=True)
    prepare.add_argument("--target-dates", type=_path, required=True)
    prepare.add_argument("--existing-data-root", type=_path, action="append", required=True)
    prepare.add_argument("--campaign-dir", type=_path, required=True)
    prepare.add_argument("--staging-root", type=_path, required=True)
    prepare.add_argument("--trade-cal", type=_path, required=True)
    prepare.add_argument("--instruments", type=_path, required=True)
    prepare.add_argument("--dates-per-day", type=int, default=50)
    prepare.add_argument("--canary-per-lane", type=int, default=3)
    prepare.add_argument("--batch-size", type=int, default=33)
    prepare.add_argument("--cooldown-seconds", type=float, default=1.2)
    prepare.add_argument("--retry-attempts", type=int, default=2)
    prepare.add_argument("--quota-cooldown-seconds", type=float, default=390.0)
    prepare.add_argument("--stagger-seconds", type=float, default=45.0)
    prepare.add_argument("--blocker-service", action="append", default=list(DEFAULT_BLOCKERS))
    prepare.add_argument("--token-env", default="TUSHARE_TOKEN_2")
    prepare.set_defaults(func=prepare_campaign)
    run = subparsers.add_parser("run-next")
    run.add_argument("--manifest", type=_path, required=True)
    run.add_argument("--dry-run", action="store_true")
    _add_shared_quota_arguments(run)
    run.set_defaults(func=campaign_runner.run_next)
    budgeted = subparsers.add_parser("run-budgeted")
    budgeted.add_argument("--manifest", type=_path, required=True)
    budgeted.add_argument("--max-new-rows", type=_positive_int, required=True)
    budgeted.add_argument("--max-runtime-seconds", type=_positive_float, required=True)
    budgeted.add_argument("--poll-seconds", type=_positive_float, default=10.0)
    budgeted.add_argument("--interrupt-grace-seconds", type=_non_negative_float, default=300.0)
    budgeted.add_argument("--quota-timezone", default="Asia/Shanghai")
    budgeted.add_argument("--quota-reset-guard-seconds", type=_non_negative_float, default=1200.0)
    budgeted.add_argument("--run-window-start")
    budgeted.add_argument("--run-window-drain")
    budgeted.add_argument("--run-window-stop")
    budgeted.add_argument("--heartbeat-seconds", type=_positive_float, default=300.0)
    budgeted.add_argument("--no-progress-limit", type=_positive_int, default=2)
    budgeted.add_argument("--single-lane-threshold-rows", type=_non_negative_int, default=0)
    budgeted.add_argument("--allow-stalled-retry", action="store_true")
    budgeted.add_argument("--dry-run", action="store_true")
    _add_shared_quota_arguments(budgeted)
    budgeted.set_defaults(func=campaign_runner.run_budgeted)
    reconcile_parser = subparsers.add_parser("reconcile-readiness")
    reconcile_parser.add_argument("--manifest", type=_path, required=True)
    reconcile_parser.set_defaults(func=reconcile_module.reconcile_readiness)
    status = subparsers.add_parser("status")
    status.add_argument("--manifest", type=_path, required=True)
    status.add_argument("--max-new-rows", type=_positive_int, default=67_000_000)
    status.add_argument("--quota-timezone", default="Asia/Shanghai")
    status.add_argument("--run-window-start", default="00:45")
    status.add_argument("--run-window-drain", default="04:15")
    status.add_argument("--run-window-stop", default="04:20")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=campaign_runner.campaign_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
