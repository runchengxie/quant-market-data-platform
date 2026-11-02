"""Linux daily immutable broker-forecast observations, without publishing aliases."""

from __future__ import annotations

import argparse
import fcntl
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import report_rc_history as history
from .report_rc_history import utc_now


def verify_observation(root: Path) -> dict[str, Any]:
    """Verify completion, source manifest identity and every monthly payload."""
    history.reject_symlinks(root / "observation.json")
    receipt = json.loads((root / "observation.json").read_text())
    return _verify_contents(root, receipt)


def _observation_clock(receipt: dict[str, Any]) -> tuple[datetime, datetime]:
    if receipt.get("status") != "completed":
        raise ValueError("forecast observation is incomplete")
    started = datetime.fromisoformat(receipt["started_at_utc"])
    finished = datetime.fromisoformat(receipt["finished_at_utc"])
    if started.tzinfo is None or finished.tzinfo is None or finished < started:
        raise ValueError("forecast observation clock is invalid")
    local_day = started.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    if local_day != receipt["observation_date"] or receipt["query"]["end_date"] != local_day:
        raise ValueError("forecast observation clock does not match the query")
    return started, finished


def _month_observation(
    root: Path, record: dict[str, Any], dates: tuple[str, str], clock: tuple[datetime, datetime]
) -> int:
    start, end = dates
    started, finished = clock
    if record["month"] != start[:6]:
        raise ValueError("forecast observation month identity mismatch")
    expected = {"start_date": start, "end_date": end, "fields": history.FIELDS}
    proof = history.verified_month(root / "history" / "raw" / f"month={start[:6]}", expected)
    if proof != {key: value for key, value in record.items() if key != "month"}:
        raise ValueError("forecast observation acquisition metadata mismatch")
    fetched = datetime.fromisoformat(proof["retrieved_at_utc"])
    if fetched.tzinfo is None or not started <= fetched <= finished:
        raise ValueError("forecast observation acquisition clock is outside the attempt")
    return proof["rows"]


def _verify_contents(root: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    started, finished = _observation_clock(receipt)
    manifest_path = root / "history" / "manifest.json"
    history.reject_symlinks(manifest_path)
    if history.file_hash(manifest_path) != receipt.get("history_manifest_sha256"):
        raise ValueError("forecast observation history manifest hash mismatch")
    manifest = json.loads(manifest_path.read_text())
    query = receipt["query"]
    ranges = history.month_ranges(query["start_date"], query["end_date"])
    months = manifest.get("months", [])
    if manifest.get("status") != "completed" or len(months) != len(ranges):
        raise ValueError("forecast observation months are incomplete")
    if any(record.get("acquisition_revision") != receipt["source_revision"] for record in months):
        raise ValueError("forecast observation source metadata mismatch")
    total = sum(
        _month_observation(root, record, (start, end), (started, finished))
        for record, (start, end) in zip(months, ranges, strict=True)
    )
    if total != receipt["rows"] or total != manifest["total_rows"]:
        raise ValueError("forecast observation row count mismatch")
    return receipt


def _collect(args: argparse.Namespace, archive: Path, planned: dict[str, Any]) -> dict[str, Any]:
    start = datetime.fromisoformat(planned["started_at_utc"])
    attempt = start.strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:8]
    root = archive / f"observation_date={planned['observation_date']}" / f"attempt={attempt}"
    history.reject_symlinks(root)
    root.mkdir(parents=True, exist_ok=False)
    receipt = {**planned, "status": "partial", "observation_dir": str(root)}
    history.atomic_json(root / "observation.json", receipt)
    try:
        history.backfill(
            argparse.Namespace(
                out_dir=root / "history",
                start_date=planned["query"]["start_date"],
                end_date=planned["query"]["end_date"],
                source_revision=args.source_revision,
                token_env=args.token_env,
                interval=args.interval,
                workers=args.workers,
            )
        )
        manifest = json.loads((root / "history/manifest.json").read_text())
        if manifest.get("status") != "completed":
            raise ValueError("historical acquisition did not complete")
        receipt.update(
            status="completed",
            finished_at_utc=utc_now(),
            rows=manifest["total_rows"],
            months=len(manifest["months"]),
            history_manifest_sha256=history.file_hash(root / "history/manifest.json"),
        )
        _verify_contents(root, receipt)
        history.atomic_json(root / "observation.json", receipt)
        return receipt
    except BaseException as error:
        receipt.update(
            status="partial", failure_type=type(error).__name__, finished_at_utc=utc_now()
        )
        history.atomic_json(root / "observation.json", receipt)
        raise


def capture_observation(args: argparse.Namespace) -> dict[str, Any]:
    """Record the actual observation clock, never a requested historical vintage."""
    if not 1 <= args.lookback_days <= 366:
        raise ValueError("lookback_days must be between 1 and 366")
    if len(args.source_revision) != 40 or any(
        c not in "0123456789abcdef" for c in args.source_revision
    ):
        raise ValueError("source_revision must be an immutable commit SHA")
    started = utc_now()
    observed = datetime.fromisoformat(started).astimezone(ZoneInfo("Asia/Shanghai")).date()
    archive = Path(args.artifacts_root) / "assets/tushare/a_share/report_rc_observations"
    planned = {
        "schema_version": "tushare.report_rc.observation.v1",
        "started_at_utc": started,
        "observation_date": observed.strftime("%Y%m%d"),
        "source_revision": args.source_revision,
        "local_timezone": "Asia/Shanghai",
        "availability": "actual_local_observation_only",
        "query": {
            "start_date": (observed - timedelta(days=args.lookback_days - 1)).strftime("%Y%m%d"),
            "end_date": observed.strftime("%Y%m%d"),
            "lookback_calendar_days": args.lookback_days,
        },
    }
    if args.dry_run:
        return {**planned, "status": "dry_run"}
    history.reject_symlinks(archive)
    archive.mkdir(parents=True, exist_ok=True)
    lock = archive / ".capture.lock"
    history.reject_symlinks(lock)
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another broker observation capture is active") from error
        return _collect(args, archive, planned)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-root", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--lookback-days", type=int, default=270)
    parser.add_argument("--token-env", default="TUSHARE_TOKEN_2")
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        result = verify_observation(args.verify)
    else:
        if args.artifacts_root is None or args.source_revision is None or args.interval < 0:
            parser.error(
                "capture requires artifacts-root, source-revision and a nonnegative interval"
            )
        result = capture_observation(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
