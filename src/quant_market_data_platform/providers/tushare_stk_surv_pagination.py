"""Bounded, hash-bound pagination for the 400-row institutional survey API."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ._adapters_part01 import _request_policy_payload
from ._adapters_part02 import EVENT_DATE_DEFAULT_FIELDS, EVENT_DATE_REQUIRED_FIELDS
from ._client import _call_tushare_api, _call_tushare_endpoint, _pandas
from ._frame import _prepare_event_frame
from ._io import _fields_text, _prepare_output_dir, _write_frame, _write_manifest
from .tushare_a_share_dates import _calendar_dates, _validate_date
from .tushare_a_share_manifests import _stored_partition_totals
from .tushare_a_share_options import DateRangeEventMirrorOptions

PAGE_SIZE = 400
MAX_PAGES = 1000


def payload_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def survey_pages(
    pro: Any, policy: Any, query: dict[str, Any], interval: float
) -> tuple[Any, list[int]]:
    frames = []
    fingerprints = set()
    counts = []
    for page in range(MAX_PAGES):
        kwargs = {**query, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE}
        raw = _pandas().DataFrame(
            _call_tushare_api(
                lambda call_kwargs=kwargs: _call_tushare_endpoint(pro, "stk_surv", call_kwargs),
                policy=policy,
            )
        )
        if interval > 0:
            time.sleep(interval)
        count = len(raw)
        counts.append(count)
        if count > PAGE_SIZE:
            raise ValueError("stk_surv pagination returned more than the requested limit")
        if count:
            # Index/order differences cannot hide a provider repeating the same page.
            fingerprint = tuple(
                sorted(_pandas().util.hash_pandas_object(raw, index=False).tolist())
            )
            if fingerprint in fingerprints:
                raise ValueError("stk_surv pagination repeated a page; offset may be ignored")
            fingerprints.add(fingerprint)
            frame = _prepare_event_frame(raw)
            if "surv_date" not in frame or not frame["surv_date"].eq(query["start_date"]).all():
                raise ValueError("stk_surv pagination returned inconsistent survey dates")
            frames.append(frame)
        if count < PAGE_SIZE:
            result = _pandas().concat(frames, ignore_index=True) if frames else raw
            return result, counts
    raise ValueError("stk_surv pagination exceeded the maximum page bound")


def load_verified_partition(path: Path, query: dict[str, Any]) -> tuple[Any, list[int]]:
    proof_path = path.with_name("pagination.json")
    if path.is_symlink() or not proof_path.is_file() or proof_path.is_symlink():
        raise ValueError("stk_surv skip-existing requires a pagination receipt")
    proof = json.loads(proof_path.read_text())
    counts = proof.get("page_rows", [])
    valid = (
        proof.get("schema_version") == "tushare.stk_surv.pagination.v1"
        and proof.get("query") == query
        and proof.get("complete") is True
        and isinstance(counts, list)
        and 0 < len(counts) <= MAX_PAGES
        and all(type(count) is int and count == PAGE_SIZE for count in counts[:-1])
        and type(counts[-1]) is int
        and 0 <= counts[-1] < PAGE_SIZE
        and proof.get("payload_sha256") == payload_hash(path)
    )
    if not valid:
        raise ValueError("stk_surv pagination receipt is missing, incompatible or stale")
    frame = _pandas().read_parquet(path)
    if len(frame) != proof.get("rows") or len(frame) != sum(counts):
        raise ValueError("stk_surv pagination receipt row counts disagree")
    if "surv_date" not in frame or not frame["surv_date"].eq(query["start_date"]).all():
        raise ValueError("stk_surv pagination receipt date mismatch")
    return frame, counts


def survey_manifest(
    options: DateRangeEventMirrorOptions,
    output: Path,
    fields: tuple[str, ...],
    policy: Any,
    api_url: str,
) -> dict[str, Any]:
    start, end = _validate_date(options.start_date), _validate_date(options.end_date)
    interval = max(0.0, float(options.request_interval_seconds))
    return {
        "schema_version": "tushare.stk_surv.v1",
        "dataset": "stk_surv",
        "market": "a_share",
        "provider": "tushare",
        "status": "partial",
        "output_dir": str(output),
        "query": {
            "api": "stk_surv",
            "start_date": start,
            "end_date": end,
            "fields": list(fields),
            "partition_by": "event_date",
            "request_interval_seconds": interval,
            "query_options": options.query_options,
        },
        "api_url": api_url,
        "request_policy": _request_policy_payload(policy),
        "pagination": {
            "page_size": PAGE_SIZE,
            "max_pages": MAX_PAGES,
            "complete": False,
            "event_dates": {},
        },
        "written_event_dates": [],
        "skipped_event_dates": [],
        "empty_event_dates": [],
    }


def write_pagination_receipt(
    path: Path, query: dict[str, Any], counts: list[int], rows: int
) -> None:
    proof = {
        "schema_version": "tushare.stk_surv.pagination.v1",
        "query": query,
        "complete": True,
        "page_rows": counts,
        "rows": rows,
        "payload_sha256": payload_hash(path),
    }
    proof_path = path.with_name("pagination.json")
    pending = proof_path.with_suffix(".pending")
    pending.write_text(json.dumps(proof, indent=2) + "\n")
    pending.replace(proof_path)


def finalize_survey_manifest(output: Path, manifest: dict[str, Any]) -> None:
    manifest["run_totals"] = dict(manifest["totals"])
    paths = sorted((output / "data").rglob("*.parquet"))
    unverified = []
    try:
        if any(path.absolute() != path.resolve() for path in paths):
            raise ValueError("stk_surv physical inventory includes symlink paths")
        manifest["totals"].update(_stored_partition_totals(output / "data"))
        for path in paths:
            day = path.parent.name.removeprefix("event_date=")
            query = {**manifest["query"]["query_options"], "start_date": day, "end_date": day}
            fields = _fields_text(
                manifest["query"]["fields"], required=EVENT_DATE_REQUIRED_FIELDS["stk_surv"]
            )
            if fields is not None:
                query["fields"] = fields
            try:
                _validate_date(day)
                load_verified_partition(path, query)
            except (ValueError, KeyError, TypeError, OSError):
                unverified.append(day)
    except Exception as error:
        manifest["status"] = "partial"
        manifest["pagination"].update(complete=False, inventory_failure_type=type(error).__name__)
        manifest["totals"].update(rows=None, symbols=None, files=len(paths))
        _write_manifest(output / "manifest.yml", manifest)
        raise
    manifest["pagination"]["unverified_retained_event_dates"] = sorted(set(unverified))
    if unverified:
        manifest["status"] = "partial"
        manifest["pagination"]["complete"] = False
    _write_manifest(output / "manifest.yml", manifest)


def mirror_stk_surv(options: DateRangeEventMirrorOptions, *, runtime: Any) -> dict[str, Any]:
    if {"limit", "offset"} & options.query_options.keys():
        raise ValueError("stk_surv pagination limit and offset are reserved")
    start, end = _validate_date(options.start_date), _validate_date(options.end_date)
    dates = _calendar_dates(start, end)
    pro, policy, api_url = runtime(
        token_env=options.token_env,
        api_url=options.api_url,
        request_policy=options.request_policy,
        request_options=options.request_options,
    )
    if Path(options.out_dir).absolute() != Path(options.out_dir).resolve():
        raise ValueError("stk_surv output symlink paths are rejected")
    output = _prepare_output_dir(Path(options.out_dir), allow_existing=options.skip_existing)
    fields = tuple(
        EVENT_DATE_DEFAULT_FIELDS["stk_surv"] if options.fields is None else options.fields
    )
    fields_text = _fields_text(fields, required=EVENT_DATE_REQUIRED_FIELDS["stk_surv"])
    interval = max(0.0, float(options.request_interval_seconds))
    rows = 0
    symbols: set[str] = set()
    manifest = survey_manifest(options, output, fields, policy, api_url)
    written = manifest["written_event_dates"]
    skipped = manifest["skipped_event_dates"]
    empty = manifest["empty_event_dates"]
    receipts = manifest["pagination"]["event_dates"]
    event_date = None
    try:
        for event_date in dates:
            path = output / "data" / f"event_date={event_date}" / "part.parquet"
            if path.absolute() != path.resolve() or path.with_name("pagination.json").is_symlink():
                raise ValueError("stk_surv output symlink paths are rejected")
            query = {**options.query_options, "start_date": event_date, "end_date": event_date}
            if fields_text is not None:
                query["fields"] = fields_text
            if options.skip_existing and path.exists():
                frame, counts = load_verified_partition(path, query)
                skipped.append(event_date)
            else:
                frame, counts = survey_pages(pro, policy, query, interval)
                if frame.empty:
                    empty.append(event_date)
                    receipts[event_date] = {"page_rows": counts, "complete": True, "rows": 0}
                    continue
                _write_frame(frame, path)
                write_pagination_receipt(path, query, counts, len(frame))
                written.append(event_date)
                rows += len(frame)
                symbols.update(frame["symbol"].dropna().astype(str).tolist())
            receipts[event_date] = {
                "page_rows": counts,
                "complete": True,
                "rows": len(frame),
                "payload_sha256": payload_hash(path),
            }
        manifest["status"] = "completed"
        manifest["pagination"]["complete"] = True
    except Exception as error:
        manifest["pagination"].update(
            failed_event_date=event_date, failure_type=type(error).__name__
        )
        raise
    finally:
        manifest["totals"] = {
            "rows": rows,
            "symbols": len(symbols),
            "event_dates_requested": len(dates),
            "event_dates_written": len(written),
            "event_dates_skipped": len(skipped),
            "event_dates_empty": len(empty),
            "files": len(written),
        }
        finalize_survey_manifest(output, manifest)
    return manifest
