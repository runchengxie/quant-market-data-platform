"""Audit same-day reconstructed ST rows against dated TuShare ST events."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant_market_data_platform.providers.tushare_common import normalize_ts_code, pandas
from quant_market_data_platform.providers.tushare_constraint_io import (
    atomic_json,
    atomic_parquet,
    sha256,
)

ST_PREFIX = re.compile(r"^\s*(?:S\*?ST|\*?ST|PT)", re.IGNORECASE)
SCHEMA = "market-data-platform.st-event-timing-audit.v1"


def _classify_row(row: Any, candidates: Any, pd: Any) -> dict[str, Any]:
    latest = None
    same_date = None
    later = None
    if candidates is not None:
        nearby_end = (pd.Timestamp(row.trade_date) + pd.Timedelta(days=10)).strftime("%Y%m%d")
        eligible = candidates[
            (candidates["pub_date"] < row.trade_date) & (candidates["imp_date"] <= row.trade_date)
        ]
        latest = eligible.iloc[-1] if not eligible.empty else None
        same_day = candidates[
            (candidates["pub_date"] == row.trade_date) & (candidates["imp_date"] <= row.trade_date)
        ]
        same_date = same_day.iloc[-1] if not same_day.empty else None
        following = candidates[
            (candidates["pub_date"] > row.trade_date)
            & (candidates["imp_date"] > row.trade_date)
            & (candidates["imp_date"] <= nearby_end)
            & candidates["name"].astype(str).str.match(ST_PREFIX)
        ]
        later = following.iloc[0] if not following.empty else None
    if latest is not None and bool(ST_PREFIX.match(str(latest["name"]))):
        status, evidence = "prior_dated_st_event", latest
    elif same_date is not None:
        status, evidence = "same_day_time_unknown", same_date
    elif later is not None:
        status, evidence = "later_event_date_conflict", later
    else:
        status, evidence = "no_active_prior_event", latest
    return {
        "trade_date": row.trade_date,
        "ts_code": row.ts_code,
        "history_name": row.name,
        "status": status,
        "event_name": None if evidence is None else evidence["name"],
        "event_type": None if evidence is None else evidence["st_type"],
        "event_pub_date": None if evidence is None else evidence["pub_date"],
        "event_imp_date": None if evidence is None else evidence["imp_date"],
    }


def audit_st_event_timing(
    history_path: str | Path,
    events_path: str | Path,
    out_dir: str | Path,
) -> dict[str, Any]:
    """Classify same-day name announcements; dates never prove intraday availability."""
    pd = pandas()
    history_file = Path(history_path).expanduser().resolve()
    events_file = Path(events_path).expanduser().resolve()
    receipt_file = events_file.with_suffix(".receipt.json")
    if not receipt_file.is_file():
        raise ValueError("ST events require a source receipt")
    source_receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    if source_receipt.get("dataset") != "st" or source_receipt.get("sha256") != sha256(events_file):
        raise ValueError("ST event source receipt or hash mismatch")
    history = pd.read_parquet(history_file)
    events = pd.read_parquet(events_file)
    if missing := {"trade_date", "ts_code", "ann_date", "name"} - set(history):
        raise ValueError(f"ST history lacks columns: {sorted(missing)}")
    if missing := {"ts_code", "name", "pub_date", "imp_date", "st_type"} - set(events):
        raise ValueError(f"ST events lack columns: {sorted(missing)}")
    for frame, columns in (
        (history, ("trade_date", "ann_date")),
        (events, ("pub_date", "imp_date")),
    ):
        frame["ts_code"] = frame["ts_code"].map(normalize_ts_code)
        for column in columns:
            frame[column] = frame[column].astype("string").str.replace("-", "", regex=False).str[:8]
    same_day = history.loc[history["trade_date"] == history["ann_date"]]
    same_day = same_day.drop_duplicates(["trade_date", "ts_code"])
    grouped = {
        code: group.sort_values(["imp_date", "pub_date"])
        for code, group in events.groupby("ts_code", sort=False)
    }
    rows = [
        _classify_row(row, grouped.get(row.ts_code), pd) for row in same_day.itertuples(index=False)
    ]
    root = Path(out_dir).expanduser().resolve()
    audit_path = root / "st_event_timing_audit.parquet"
    audit = pd.DataFrame(
        rows,
        columns=[
            "trade_date",
            "ts_code",
            "history_name",
            "status",
            "event_name",
            "event_type",
            "event_pub_date",
            "event_imp_date",
        ],
    )
    atomic_parquet(audit, audit_path)
    receipt = {
        "schema_version": SCHEMA,
        "built_at": datetime.now(UTC).isoformat(),
        "scope": "reconstructed_st_rows_with_ann_date_equal_trade_date",
        "rows": len(audit),
        "status_counts": dict(Counter(audit["status"])),
        "history_sha256": sha256(history_file),
        "events_sha256": sha256(events_file),
        "events_receipt_sha256": sha256(receipt_file),
        "audit_sha256": sha256(audit_path),
        "revision_safe": False,
        "limitation": "event dates contain no intraday publication time",
    }
    receipt_path = root / "st_event_timing_audit.receipt.json"
    atomic_json(receipt_path, receipt)
    return {**receipt, "path": str(audit_path), "receipt_path": str(receipt_path)}


__all__ = ["audit_st_event_timing"]
