#!/usr/bin/env python3
"""Coordinate request-first TuShare minute quota scheduling.

This operational wrapper deliberately lives outside the quota accounting core.
It creates idempotent request-slot holds for production consumers, validates
their independently published raw-completeness markers, and releases only the
matching hold.  No token value is written to the schedule state.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# These operational scripts are executed by systemd via an absolute path.  In
# production the shared environment is intentionally non-editable, so Python
# does not automatically add this repository's ``src`` layout to sys.path.
# Bootstrap both the repository root (for ``scripts.operations`` fallbacks)
# and ``src`` before importing project modules.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
for _path in (_REPOSITORY_ROOT, _SOURCE_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

try:
    import _tushare_minute_quota_schedule_support as _support
except ModuleNotFoundError:
    from scripts.operations import _tushare_minute_quota_schedule_support as _support

_add_marker_args = _support.add_marker_args
_add_quota_args = _support.add_quota_args
_await_raw_completeness_marker = _support.await_raw_completeness_marker
_marker_path = _support.marker_path
_parse_aware_timestamp = _support.parse_aware_timestamp
_positive_int = _support.positive_int
validate_raw_completeness_marker = _support.validate_raw_completeness_marker

SCHEDULE_SCHEMA_VERSION = 1
RETRYABLE_EXIT_CODE = 75
DEFAULT_DAILY_CONSUMER = "daily_watch20"
DEFAULT_TOP200_CONSUMER = "top200_factor_observation"
DEFAULT_DAILY_HOLD_REQUESTS = 300
DEFAULT_TOP200_HOLD_REQUESTS = 30


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


@contextmanager
def _exclusive_lock(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _quota_now(timezone_name: str) -> datetime:
    return datetime.now(ZoneInfo(timezone_name))


def _quota_date(timezone_name: str) -> str:
    return _quota_now(timezone_name).strftime("%Y%m%d")


def _is_weekday(timezone_name: str) -> bool:
    return _quota_now(timezone_name).weekday() < 5


def _manifest_token_env(path: Path) -> str:
    payload = _read_json(path)
    token_env = str((payload.get("config") or {}).get("token_env") or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
        raise ValueError(f"campaign manifest has an invalid token_env: {path}")
    return token_env


def _load_tushare_environment() -> None:
    # Keep credential loading identical to the downloader without persisting it.
    from market_data_platform.providers.tushare_a_share import _load_tushare_env_files

    _load_tushare_env_files()


def _build_ledger(args: argparse.Namespace, *, allow_burst: bool = False) -> Any:
    from market_data_platform.tushare_minute_quota import (
        MinuteQuotaLedger,
        MinuteQuotaRequestOverrides,
    )

    _load_tushare_environment()
    ledger = MinuteQuotaLedger.from_env(
        token_env=_manifest_token_env(args.manifest),
        mode="enforce",
        database_path=args.minute_quota_db,
        consumer="minute_quota_schedule",
        limit_rows=args.minute_quota_limit_rows,
        safety_rows=args.minute_quota_safety_rows,
        request_overrides=MinuteQuotaRequestOverrides(
            gate="requests",
            limit_requests=args.minute_quota_limit_requests,
            burst_limit_requests=args.minute_quota_burst_limit_requests,
            safety_requests=args.minute_quota_safety_requests,
            allow_burst=allow_burst,
        ),
    )
    if ledger is None:
        raise RuntimeError("request-first schedule cannot run with minute quota accounting off")
    return ledger


def _state_path(state_root: Path, quota_date: str, token_fingerprint: str) -> Path:
    safe_fingerprint = re.sub(r"[^0-9a-f]", "", token_fingerprint.lower())
    if not safe_fingerprint:
        raise ValueError("quota status did not provide a usable token fingerprint")
    return state_root / quota_date / f"{safe_fingerprint}.json"


def _initial_state(
    *,
    quota_date: str,
    token_fingerprint: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEDULE_SCHEMA_VERSION,
        "quota_date": quota_date,
        "timezone": args.quota_timezone,
        "token_fingerprint": token_fingerprint,
        "generated_at": _now(),
        "updated_at": _now(),
        "policy": {
            "gate": "requests",
            "limit_requests": args.minute_quota_limit_requests,
            "burst_limit_requests": args.minute_quota_burst_limit_requests,
            "safety_requests": args.minute_quota_safety_requests,
            "limit_rows": args.minute_quota_limit_rows,
            "safety_rows": args.minute_quota_safety_rows,
        },
        "holds": {},
    }


def _load_or_initialize_state(
    path: Path,
    *,
    quota_date: str,
    token_fingerprint: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if not path.exists():
        return _initial_state(
            quota_date=quota_date,
            token_fingerprint=token_fingerprint,
            args=args,
        )
    payload = _read_json(path)
    if (
        payload.get("schema_version") != SCHEDULE_SCHEMA_VERSION
        or payload.get("quota_date") != quota_date
        or payload.get("token_fingerprint") != token_fingerprint
        or not isinstance(payload.get("holds"), dict)
    ):
        raise ValueError(f"invalid minute quota schedule state: {path}")
    return payload


def _active_request_hold(status: dict[str, Any], consumer: str) -> dict[str, Any] | None:
    matches = [
        item
        for item in status.get("active_request_holds", [])
        if isinstance(item, dict) and item.get("consumer") == consumer
    ]
    if len(matches) > 1:
        raise RuntimeError(f"multiple active request holds found for {consumer}")
    return matches[0] if matches else None


def _ensure_request_hold(
    ledger: Any,
    status: dict[str, Any],
    state: dict[str, Any],
    *,
    consumer: str,
    requests: int,
) -> None:
    existing = _active_request_hold(status, consumer)
    recorded = state["holds"].get(consumer)
    if isinstance(recorded, dict) and recorded.get("released_at") is not None:
        # Requires= may reactivate the coordinator after the producer has
        # released its hold. A completed hold is terminal for this quota day.
        if existing is not None and str(existing.get("hold_id")) == str(recorded.get("hold_id")):
            raise RuntimeError(f"released request hold is unexpectedly active for {consumer}")
        return
    hold_id = ledger.create_request_hold(
        requests,
        consumer=consumer,
        note=f"scheduled priority hold for quota day {state['quota_date']}",
    )
    if existing is not None and str(existing.get("hold_id")) != str(hold_id):
        raise RuntimeError(f"request hold identity changed unexpectedly for {consumer}")
    state["holds"][consumer] = {
        "hold_id": str(hold_id),
        "request_slots": requests,
        "created_at": str((existing or {}).get("created_at") or _now()),
        "released_at": None,
    }


def coordinate(
    args: argparse.Namespace,
    *,
    ledger_factory: Callable[..., Any] = _build_ledger,
) -> int:
    """Create per-consumer request holds exactly once for the quota day."""

    ledger = ledger_factory(args, allow_burst=False)
    quota_date = _quota_date(args.quota_timezone)
    lock_path = args.state_root / quota_date / ".schedule.lock"
    with _exclusive_lock(lock_path):
        from market_data_platform.tushare_minute_quota import MinuteQuotaConfigurationError

        try:
            status = ledger.status(quota_date=quota_date)
        except MinuteQuotaConfigurationError as exc:
            # Pool policy is immutable within a quota day.  During a rollout,
            # defer hold initialization instead of failing every dependent
            # service; the next Asia/Shanghai day will create the new pool.
            print(
                json.dumps(
                    {
                        "quota_date": quota_date,
                        "status": "deferred_until_next_quota_day",
                        "reason": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
            return RETRYABLE_EXIT_CODE
        fingerprint = str(status["token_fingerprint"])
        path = _state_path(args.state_root, quota_date, fingerprint)
        state = _load_or_initialize_state(
            path,
            quota_date=quota_date,
            token_fingerprint=fingerprint,
            args=args,
        )
        if not args.weekdays_only or _is_weekday(args.quota_timezone):
            _ensure_request_hold(
                ledger,
                status,
                state,
                consumer=args.daily_consumer,
                requests=args.daily_hold_requests,
            )
            # Refresh because the first hold changes available capacity atomically.
            status = ledger.status(quota_date=quota_date)
            _ensure_request_hold(
                ledger,
                status,
                state,
                consumer=args.top200_consumer,
                requests=args.top200_hold_requests,
            )
        state["updated_at"] = _now()
        state["status"] = "complete"
        _atomic_write_json(path, state)
    print(json.dumps({"schedule_state": str(path), "quota_date": quota_date}, ensure_ascii=False))
    return 0


def await_marker(args: argparse.Namespace) -> int:
    return _await_raw_completeness_marker(
        args,
        quota_now=_quota_now,
        quota_date=_quota_date,
        is_weekday=_is_weekday,
        retryable_exit_code=RETRYABLE_EXIT_CODE,
    )


def release_ready(
    args: argparse.Namespace,
    *,
    ledger_factory: Callable[..., Any] = _build_ledger,
) -> int:
    """Release one consumer hold only after its raw evidence is complete."""

    marker_status = await_marker(args)
    if marker_status != 0:
        return marker_status
    if args.weekdays_only and not _is_weekday(args.quota_timezone):
        return 0
    ledger = ledger_factory(args, allow_burst=False)
    quota_date = _quota_date(args.quota_timezone)
    status = ledger.status(quota_date=quota_date)
    fingerprint = str(status["token_fingerprint"])
    path = _state_path(args.state_root, quota_date, fingerprint)
    lock_path = args.state_root / quota_date / ".schedule.lock"
    with _exclusive_lock(lock_path):
        state = _load_or_initialize_state(
            path,
            quota_date=quota_date,
            token_fingerprint=fingerprint,
            args=args,
        )
        hold = state["holds"].get(args.consumer)
        if hold is None:
            active = _active_request_hold(ledger.status(quota_date=quota_date), args.consumer)
            if active is None:
                print(f"no active request hold for {args.consumer}; release is already complete")
                return 0
            hold = {
                "hold_id": str(active["hold_id"]),
                "request_slots": int(active["request_slots"]),
                "created_at": str(active["created_at"]),
                "released_at": None,
            }
            state["holds"][args.consumer] = hold
        if hold.get("released_at") is None:
            marker = validate_raw_completeness_marker(
                _marker_path(args.raw_completeness_root, quota_date, args.consumer),
                quota_date=quota_date,
                consumer=args.consumer,
            )
            completed_at = _parse_aware_timestamp(marker["completed_at"], field="completed_at")
            hold_created_at = _parse_aware_timestamp(hold["created_at"], field="hold.created_at")
            if completed_at < hold_created_at:
                raise ValueError(
                    f"raw marker for {args.consumer} predates its request hold: "
                    f"{completed_at.isoformat()} < {hold_created_at.isoformat()}"
                )
            ledger.release_hold(str(hold["hold_id"]))
            hold["released_at"] = _now()
            state["updated_at"] = _now()
            _atomic_write_json(path, state)
    print(f"request hold released for {args.consumer} on {quota_date}")
    return 0


def schedule_status(
    args: argparse.Namespace,
    *,
    ledger_factory: Callable[..., Any] = _build_ledger,
) -> int:
    from market_data_platform.tushare_minute_quota import MinuteQuotaConfigurationError

    ledger = ledger_factory(args, allow_burst=bool(args.allow_burst))
    quota_date = _quota_date(args.quota_timezone)
    try:
        quota = ledger.status(quota_date=quota_date)
    except MinuteQuotaConfigurationError as exc:
        print(
            json.dumps(
                {
                    "quota_date": quota_date,
                    "status": "policy_mismatch",
                    "reason": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return RETRYABLE_EXIT_CODE
    path = _state_path(args.state_root, quota_date, str(quota["token_fingerprint"]))
    payload = {
        "schema_version": SCHEDULE_SCHEMA_VERSION,
        "generated_at": _now(),
        "quota": quota,
        "schedule_state_path": str(path),
        "schedule_state": _read_json(path) if path.exists() else None,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    coordinator = commands.add_parser("coordinate")
    _add_quota_args(coordinator)
    coordinator.add_argument("--daily-consumer", default=DEFAULT_DAILY_CONSUMER)
    coordinator.add_argument("--top200-consumer", default=DEFAULT_TOP200_CONSUMER)
    coordinator.add_argument(
        "--daily-hold-requests", type=_positive_int, default=DEFAULT_DAILY_HOLD_REQUESTS
    )
    coordinator.add_argument(
        "--top200-hold-requests", type=_positive_int, default=DEFAULT_TOP200_HOLD_REQUESTS
    )
    coordinator.add_argument("--weekdays-only", action="store_true")
    coordinator.set_defaults(func=coordinate)

    marker = commands.add_parser("await-marker")
    _add_marker_args(marker)
    marker.set_defaults(func=await_marker)

    release = commands.add_parser("release-ready")
    _add_quota_args(release)
    release.add_argument("--raw-completeness-root", type=Path, required=True)
    release.add_argument("--consumer", required=True)
    release.add_argument("--weekdays-only", action="store_true")
    release.add_argument("--not-before-local")
    release.add_argument("--deadline-local")
    release.add_argument("--poll-seconds", type=_positive_int, default=60)
    release.set_defaults(func=release_ready)

    status = commands.add_parser("status")
    _add_quota_args(status)
    status.add_argument("--allow-burst", action="store_true")
    status.set_defaults(func=schedule_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not hasattr(args, "minute_quota_limit_requests"):
        return int(args.func(args))
    if args.minute_quota_burst_limit_requests < args.minute_quota_limit_requests:
        raise ValueError("burst request limit must be at least the base request limit")
    if (
        args.minute_quota_burst_limit_requests - args.minute_quota_safety_requests
        < args.minute_quota_limit_requests
    ):
        raise ValueError("burst capacity after safety must preserve the full base request limit")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
