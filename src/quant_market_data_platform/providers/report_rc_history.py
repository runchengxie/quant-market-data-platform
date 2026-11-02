"""Resumable, hash-bound historical broker-forecast snapshots.

Run through ``marketdata config run -- python -m ...report_rc_history``.
This research acquisition never publishes a current/latest alias.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ._adapters_part01 import _tushare_runtime
from ._adapters_part02 import EVENT_DATE_DEFAULT_FIELDS
from ._client import _call_tushare_api, _call_tushare_endpoint, _pandas

PAGE_SIZE = 3000
MAX_PAGES = 1000
FIELDS = ",".join((*EVENT_DATE_DEFAULT_FIELDS["report_rc"], "create_time", "imp_dg"))


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    pending.replace(path)


def month_ranges(start: str, end: str) -> list[tuple[str, str]]:
    current, final = (datetime.strptime(value, "%Y%m%d") for value in (start, end))
    if current > final:
        raise ValueError("start date must not follow end date")
    result = []
    while current <= final:
        following = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = min(final, following - timedelta(days=1))
        result.append((current.strftime("%Y%m%d"), last.strftime("%Y%m%d")))
        current = following
    return result


def validate_frame(frame: Any, start: str, end: str) -> None:
    if frame.empty:
        return
    required = {"ts_code", "report_date"}
    if not required.issubset(frame.columns) or frame[list(required)].isna().any().any():
        raise ValueError("report_rc missing required identifiers or dates")
    dates = _pandas().to_datetime(frame["report_date"], format="%Y%m%d", errors="coerce")
    if dates.isna().any() or not dates.between(start, end).all():
        raise ValueError("report_rc response date outside requested range")
    if frame.duplicated().any():
        raise ValueError("report_rc response contains exact duplicate rows")


def fetch_month(
    client: Any, policy: Any, start: str, end: str, interval: float
) -> tuple[Any, list[int]]:
    frames, counts, fingerprints = [], [], set()
    for page in range(MAX_PAGES):
        query = {
            "start_date": start,
            "end_date": end,
            "fields": FIELDS,
            "limit": PAGE_SIZE,
            "offset": page * PAGE_SIZE,
        }
        frame = _pandas().DataFrame(
            _call_tushare_api(
                lambda query=query: _call_tushare_endpoint(client, "report_rc", query),
                policy=policy,
            )
        )
        count = len(frame)
        if count > PAGE_SIZE:
            raise ValueError("report_rc returned more than requested limit")
        counts.append(count)
        if count:
            validate_frame(frame, start, end)
            fingerprint = tuple(sorted(_pandas().util.hash_pandas_object(frame, index=False)))
            if fingerprint in fingerprints:
                raise ValueError("report_rc repeated page; offset may be ignored")
            fingerprints.add(fingerprint)
            frames.append(frame)
        if interval:
            time.sleep(interval)
        if count < PAGE_SIZE:
            result = _pandas().concat(frames, ignore_index=True) if frames else frame
            validate_frame(result, start, end)
            return result, counts
    raise ValueError("report_rc maximum page bound exceeded")


def store_month(
    output: Path, frame: Any, counts: list[int], query: dict[str, Any]
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    payload = output / "part.parquet"
    pending = output / "part.pending.parquet"
    frame.to_parquet(pending, index=False)
    pending.replace(payload)
    proof = {
        "schema_version": "tushare.report_rc.history.month.v1",
        "query": query,
        "page_size": PAGE_SIZE,
        "page_rows": counts,
        "rows": len(frame),
        "complete": True,
        "retrieved_at_utc": utc_now(),
        "payload_sha256": file_hash(payload),
    }
    atomic_json(output / "receipt.json", proof)
    return proof


def verified_month(output: Path, query: dict[str, Any]) -> dict[str, Any]:
    proof = json.loads((output / "receipt.json").read_text())
    counts = proof.get("page_rows", [])
    valid = (
        proof.get("schema_version") == "tushare.report_rc.history.month.v1"
        and proof.get("query") == query
        and proof.get("complete") is True
        and proof.get("page_size") == PAGE_SIZE
        and 0 < len(counts) <= MAX_PAGES
        and all(type(n) is int and n == PAGE_SIZE for n in counts[:-1])
        and type(counts[-1]) is int
        and 0 <= counts[-1] < PAGE_SIZE
        and proof.get("rows") == sum(counts)
        and proof.get("payload_sha256") == file_hash(output / "part.parquet")
    )
    if not valid:
        raise ValueError("report_rc checkpoint is incompatible or corrupt")
    frame = _pandas().read_parquet(output / "part.parquet")
    if len(frame) != proof["rows"]:
        raise ValueError("report_rc checkpoint row count mismatch")
    validate_frame(frame, query["start_date"], query["end_date"])
    return proof


def backfill(args: argparse.Namespace) -> None:
    output = Path(args.out_dir)
    if output.absolute() != output.resolve():
        raise ValueError("snapshot output must not traverse symlinks")
    output.mkdir(parents=True, exist_ok=True)
    client, policy, _ = _tushare_runtime(
        token_env=args.token_env,
        api_url=None,
        request_policy=None,
        request_options={"request_timeout_seconds": 45, "retry_attempts": 3},
    )
    manifest = {
        "schema_version": "tushare.report_rc.history.v1",
        "api": "report_rc",
        "start_date": args.start_date,
        "end_date": args.end_date,
        "source_revision": args.source_revision,
        "started_at_utc": utc_now(),
        "availability": "retrieved_snapshot_not_certified_historical_vintage",
        "status": "running",
        "months": [],
    }
    try:
        for start, end in month_ranges(args.start_date, args.end_date):
            partition = output / "raw" / f"month={start[:6]}"
            query = {"start_date": start, "end_date": end, "fields": FIELDS}
            if (partition / "receipt.json").exists():
                proof = verified_month(partition, query)
            else:
                frame, counts = fetch_month(client, policy, start, end, args.interval)
                proof = store_month(partition, frame, counts, query)
            manifest["months"].append({"month": start[:6], **proof})
            atomic_json(output / "manifest.json", manifest)
            print(f"{start[:6]} rows={proof['rows']} pages={len(proof['page_rows'])}", flush=True)
        manifest.update(
            status="completed",
            finished_at_utc=utc_now(),
            total_rows=sum(month["rows"] for month in manifest["months"]),
        )
    except Exception as error:
        manifest.update(status="partial", failure_type=type(error).__name__)
        raise
    finally:
        atomic_json(output / "manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--start-date", default="20110101")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--token-env", default="TUSHARE_TOKEN_2")
    parser.add_argument("--interval", type=float, default=0.25)
    args = parser.parse_args()
    if args.interval < 0:
        parser.error("interval must be nonnegative")
    backfill(args)


if __name__ == "__main__":
    main()
