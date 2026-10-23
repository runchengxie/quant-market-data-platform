"""Offline, bounded independent coverage evidence; never an acceptance receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import resource
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from quant_market_data_platform.providers._a_share_mins_partition import _partition_binding_matches
from quant_market_data_platform.providers._a_share_mins_universe import (
    _apply_historical_code_transitions,
    _insert_historical_code_aliases,
)
from quant_market_data_platform.providers.a_share_minute_price_flow import price_flow_diagnostics
from quant_market_data_platform.quality_a_share_minute import _frame_validation_issues


def file_identity(stat: os.stat_result) -> dict[str, int]:
    return {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "ctime_ns": stat.st_ctime_ns,
        "mtime_ns": stat.st_mtime_ns,
        "bytes": stat.st_size,
    }


def bind(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        before = file_identity(os.fstat(stream.fileno()))
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if before != file_identity(os.fstat(stream.fileno())) or before != file_identity(
            path.stat()
        ):
            raise ValueError(f"Input changed while hashing: {path}")
    return {"path": str(path.resolve()), "sha256": digest, **before}


def verify(binding: dict[str, Any]) -> Path:
    path = Path(binding["path"])
    if bind(path) != binding:
        raise ValueError(f"Input changed: {path}")
    return path


def decision(reasons: list[str]) -> dict[str, Any]:
    return {
        "status": "unresolved" if reasons else "eligible_for_receipt_review",
        "unresolved": sorted(set(reasons)),
        "eligible_for_receipt_review": not reasons,
        "full_market_promotion_allowed": False,
    }


def normalize(symbols: set[str], date: str) -> tuple[set[str], tuple[str, ...]]:
    return _apply_historical_code_transitions(symbols, trade_date=date)


def reference(paths: list[Path], date: str) -> set[str]:
    if not paths:
        raise ValueError("Missing independent daily reference")
    symbols: set[str] = set()
    for path in paths:
        table = pq.ParquetFile(path).read(columns=["ts_code", "trade_date"]).to_pandas()
        if not table["trade_date"].astype(str).eq(date).all():
            raise ValueError("Independent reference date mismatch")
        symbols.update(table.ts_code)
    return {s for s in symbols if not s.startswith(("200", "900"))}


def listing_evidence(stock_paths: dict[str, Path], date: str) -> tuple[set[str], list[str]]:
    if set(stock_paths) != set("LDPG"):
        raise ValueError("Require explicit L/D/P/G snapshot roles")
    if len({p.resolve() for p in stock_paths.values()}) != 4:
        raise ValueError("Stock status snapshots must use distinct paths")
    frames = []
    for status, path in stock_paths.items():
        frame = pq.ParquetFile(path).read().to_pandas()
        if not frame.list_status.eq(status).all():
            raise ValueError("Stock snapshot status disagrees with declared role")
        frames.append(frame)
    reasons = []
    history = pd.concat(frames, ignore_index=True)
    if "curr_type" not in history or history["curr_type"].isna().any():
        reasons.append("missing_currency_evidence")
    for column in ["list_date", "delist_date"]:
        history[column] = history[column].fillna("").astype(str)
        dates = history.loc[history[column].ne(""), column]
        if not dates.str.fullmatch(r"[0-9]{8}").all():
            raise ValueError("Invalid listing date")
        pd.to_datetime(dates, format="%Y%m%d", errors="raise")
    bounds = history[["ts_code", "list_date", "delist_date"]].drop_duplicates()
    if bounds.ts_code.duplicated().any():
        raise ValueError("Conflicting listing bounds")
    if (history.delist_date.ne("") & history.delist_date.lt(history.list_date)).any():
        raise ValueError("Delisting precedes listing")
    if history.loc[history.list_status.ne("G"), "list_date"].eq("").any():
        reasons.append("missing_listing_date")
    if history.loc[history.list_status.eq("D"), "delist_date"].eq("").any():
        reasons.append("missing_delisting_date")
    if "curr_type" in history:
        history = history.loc[history.curr_type.eq("CNY")].copy()
    history, _ = _insert_historical_code_aliases(history, set(history.ts_code))
    active = history.list_date.ne("") & history.list_date.le(date)
    active &= history.delist_date.eq("") | history.delist_date.ge(date)
    return set(history.loc[active, "ts_code"]), reasons


def inspect_date(row: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    started = time.monotonic()
    date = row["date"]
    reasons = ["quarantined"] if row["quarantined"] else []
    if len(row["partitions"]) != 1:
        reasons.append("duplicate_partitions")
    sidecar_path = paths[row["sidecar"]]
    sidecar = json.loads(sidecar_path.read_text())
    if sidecar["trade_date"] != date:
        raise ValueError("Sidecar date mismatch")
    partition_path = paths[row["partitions"][0]]
    if partition_path.resolve() != (sidecar_path.parent / "part-00000.parquet").resolve():
        raise ValueError("Scanned partition does not match sidecar partition")
    bound_files = sidecar.get("partition", {}).get("files", [])
    if [entry.get("name") for entry in bound_files] != [partition_path.name]:
        reasons.append("unscanned_sidecar_partitions")
    frame = pq.ParquetFile(partition_path).read().to_pandas()
    actual = set(frame.ts_code)
    issues = _frame_validation_issues(date, frame)
    issues["non_241_bar_symbols"] = int(frame.groupby("ts_code").size().ne(241).sum())
    issues["non_positive_price_rows"] = int(
        frame[["open", "high", "low", "close"]].le(0).any(axis=1).sum()
    )
    flow = price_flow_diagnostics(frame)
    for key in ["notional_beyond_hard_price_guard_rows", "zero_volume_nonzero_amount_rows"]:
        issues[key] = flow[key]
    if any(issues.values()):
        reasons.append("native_semantics_failed")
    if not _partition_binding_matches(sidecar, sidecar_path.parent):
        reasons.append("sidecar_binding_failed")
    raw_daily = reference([paths[k] for k in row["daily"]], date)
    raw_basic = reference([paths[k] for k in row["daily_basic"]], date)
    daily, mappings = normalize(raw_daily, date)
    basic, _ = normalize(raw_basic, date)
    active, listing_reasons = listing_evidence(
        {status: paths[k] for status, k in row["stocks"].items()}, date
    )
    reasons.extend(listing_reasons)
    # Historical BJ references can predate exchange listing. Keep the excluded codes visible.
    stocks = pd.concat(
        [pq.ParquetFile(paths[k]).read().to_pandas() for k in row["stocks"].values()]
    )
    future = stocks.list_date.fillna("").astype(str).gt(date)
    dated_prelisting = set(stocks.loc[future, "ts_code"])
    prelisting_bj = {s for s in daily | basic if s.endswith(".BJ") and s in dated_prelisting}
    daily -= prelisting_bj
    basic -= prelisting_bj
    if {s for s in daily if not s.endswith(".BJ")} != basic:
        reasons.append("daily_reference_disagreement")
    outside_listing = daily - active
    if outside_listing:
        reasons.append("unresolved_listing_bounds")
    if actual != daily:
        reasons.append("minute_universe_mismatch")
    policy = json.loads(paths[row["policy"]].read_text())
    exclusions = [e["ts_code"] for e in policy["exceptions"] if date in e["observed_dates"]]
    if exclusions:
        reasons.append("applicable_provider_exclusions")
    return {
        "date": date,
        **decision(reasons),
        "rows": len(frame),
        "symbols": len(actual),
        "independent_symbols": len(daily),
        "applied_code_transitions": mappings,
        "raw_daily_minus_actual": sorted(raw_daily - actual),
        "raw_actual_minus_daily": sorted(actual - raw_daily),
        "independent_minus_actual": sorted(daily - actual),
        "actual_minus_independent": sorted(actual - daily),
        "daily_minus_basic": sorted(daily - basic),
        "basic_minus_daily": sorted(basic - daily),
        "outside_listing_bounds": sorted(outside_listing),
        "prelisting_bj": sorted(prelisting_bj),
        "applicable_provider_exclusions": exclusions,
        "semantic_issues": issues,
        "flow_diagnostics": flow,
        "seconds": time.monotonic() - started,
    }


def validate_dates(rows: list[dict[str, Any]]) -> None:
    if not 1 <= len(rows) <= 10 or len({r["date"] for r in rows}) != len(rows):
        raise ValueError("Require 1–10 unique dates")
    for row in rows:
        date = row["date"]
        if not isinstance(date, str) or not re.fullmatch(r"[0-9]{8}", date):
            raise ValueError("Trade date must be YYYYMMDD")
        datetime.strptime(date, "%Y%m%d")
        if type(row["quarantined"]) is not bool:
            raise ValueError("quarantined must be a boolean")


def run(manifest_path: Path, output: Path) -> dict[str, Any]:
    started = time.monotonic()
    manifest_binding = bind(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    rows = manifest["dates"]
    validate_dates(rows)
    paths = {key: verify(value) for key, value in manifest["inputs"].items()}
    results = [inspect_date(row, paths) for row in rows]
    # Bind before and after the scan; a concurrent writer invalidates the entire run.
    for binding in manifest["inputs"].values():
        verify(binding)
    verify(manifest_binding)
    report = {
        "schema_version": "historical_minute_coverage_diagnostic.v1",
        "acceptance_receipt": False,
        "full_market_promotion_allowed": False,
        "source_revision": manifest["source_revision"],
        "manifest": manifest_binding,
        "input_bindings": manifest["inputs"],
        "records": results,
        "wall_seconds": time.monotonic() - started,
        "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "unique_input_bytes": sum(b["bytes"] for b in manifest["inputs"].values()),
        "provider_requests": 0,
    }
    with output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.manifest, args.output)
    print(json.dumps({"dates": len(report["records"]), "wall_seconds": report["wall_seconds"]}))


if __name__ == "__main__":
    main()
