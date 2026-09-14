"""Shared marker and parser support for the minute quota schedule script."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

RAW_COMPLETENESS_SCHEMA_VERSION = 1
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_LIMIT_REQUESTS = 10_000
DEFAULT_BURST_LIMIT_REQUESTS = 20_000
DEFAULT_SAFETY_REQUESTS = 500
DEFAULT_LIMIT_ROWS = 160_000_000
DEFAULT_SAFETY_ROWS = 4_000_000
_SHA256 = re.compile(r"[0-9a-f]{64}")


def marker_path(root: Path, quota_date: str, consumer: str) -> Path:
    return root / quota_date / f"{consumer}.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_aware_timestamp(value: Any, *, field: str) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed


def validate_raw_completeness_marker(
    path: Path,
    *,
    quota_date: str,
    consumer: str,
) -> dict[str, Any]:
    payload = _read_json(path)
    if payload.get("schema_version") != RAW_COMPLETENESS_SCHEMA_VERSION:
        raise ValueError(f"unsupported raw-completeness marker schema: {path}")
    if payload.get("quota_date") != quota_date or payload.get("consumer") != consumer:
        raise ValueError(f"raw-completeness marker identity mismatch: {path}")
    if payload.get("raw_complete") is not True:
        raise ValueError(f"raw-completeness marker is not complete: {path}")
    trade_date = str(payload.get("trade_date") or "")
    if not re.fullmatch(r"\d{8}", trade_date):
        raise ValueError(f"raw-completeness marker has invalid trade_date: {path}")
    parse_aware_timestamp(
        payload.get("completed_at"),
        field=f"raw-completeness marker completed_at ({path})",
    )
    evidence_path = Path(str(payload.get("evidence_path") or ""))
    evidence_sha256 = str(payload.get("evidence_sha256") or "").lower()
    if not evidence_path.is_absolute() or not evidence_path.is_file():
        raise ValueError(f"raw-completeness evidence is missing: {evidence_path}")
    if not _SHA256.fullmatch(evidence_sha256) or _sha256(evidence_path) != evidence_sha256:
        raise ValueError(f"raw-completeness evidence hash mismatch: {evidence_path}")
    return payload


def _deadline(
    timezone_name: str,
    value: str | None,
    *,
    quota_now: Callable[[str], datetime],
) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.strptime(value, "%H:%M").time()
    now = quota_now(timezone_name)
    return datetime.combine(now.date(), parsed, tzinfo=now.tzinfo)


def _validate_marker_not_before(
    payload: dict[str, Any],
    *,
    timezone_name: str,
    not_before_local: str | None,
    quota_now: Callable[[str], datetime],
) -> None:
    if not_before_local is None:
        return
    threshold = _deadline(timezone_name, not_before_local, quota_now=quota_now)
    assert threshold is not None
    completed_at = parse_aware_timestamp(payload["completed_at"], field="completed_at")
    if completed_at.astimezone(threshold.tzinfo) < threshold:
        raise ValueError(
            "raw-completeness marker predates the required local threshold: "
            f"completed_at={completed_at.isoformat()} threshold={threshold.isoformat()}"
        )


def await_raw_completeness_marker(
    args: argparse.Namespace,
    *,
    quota_now: Callable[[str], datetime],
    quota_date: Callable[[str], str],
    is_weekday: Callable[[str], bool],
    retryable_exit_code: int,
) -> int:
    """Wait only until the same-day deadline for a trusted raw marker."""

    if args.weekdays_only and not is_weekday(args.quota_timezone):
        print(f"raw marker not required for {args.consumer} on a weekend quota day")
        return 0
    current_quota_date = quota_date(args.quota_timezone)
    path = marker_path(args.raw_completeness_root, current_quota_date, args.consumer)
    deadline = _deadline(args.quota_timezone, args.deadline_local, quota_now=quota_now)
    while True:
        try:
            payload = validate_raw_completeness_marker(
                path,
                quota_date=current_quota_date,
                consumer=args.consumer,
            )
            _validate_marker_not_before(
                payload,
                timezone_name=args.quota_timezone,
                not_before_local=getattr(args, "not_before_local", None),
                quota_now=quota_now,
            )
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
            now = quota_now(args.quota_timezone)
            if deadline is None or now >= deadline:
                print(f"raw marker not ready for {args.consumer}: {type(exc).__name__}: {exc}")
                return retryable_exit_code
            time.sleep(min(args.poll_seconds, max(0.0, (deadline - now).total_seconds())))
            continue
        print(f"raw marker verified for {args.consumer}: {path}")
        return 0


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def add_quota_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--minute-quota-db", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--quota-timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument(
        "--minute-quota-limit-requests",
        type=positive_int,
        default=DEFAULT_LIMIT_REQUESTS,
    )
    parser.add_argument(
        "--minute-quota-burst-limit-requests",
        type=positive_int,
        default=DEFAULT_BURST_LIMIT_REQUESTS,
    )
    parser.add_argument(
        "--minute-quota-safety-requests",
        type=non_negative_int,
        default=DEFAULT_SAFETY_REQUESTS,
    )
    parser.add_argument("--minute-quota-limit-rows", type=positive_int, default=DEFAULT_LIMIT_ROWS)
    parser.add_argument(
        "--minute-quota-safety-rows",
        type=non_negative_int,
        default=DEFAULT_SAFETY_ROWS,
    )


def add_marker_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--raw-completeness-root", type=Path, required=True)
    parser.add_argument("--consumer", required=True)
    parser.add_argument("--weekdays-only", action="store_true")
    parser.add_argument("--not-before-local")
    parser.add_argument("--deadline-local")
    parser.add_argument("--poll-seconds", type=positive_int, default=60)
    parser.add_argument("--quota-timezone", default=DEFAULT_TIMEZONE)
