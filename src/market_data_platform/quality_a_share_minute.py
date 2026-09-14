"""Quality acceptance checks for the canonical A-share one-minute dataset."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from market_data_platform.standardize.fusion.a_share_minute import (
    CANONICAL_MINUTE_COLUMNS,
    CANONICAL_MINUTE_SCHEMA,
    MINUTE_KEY_COLUMNS,
)

_PARTITION_PATTERN = re.compile(r"trade_date=(\d{8})$")
_TS_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")


def _validate_date(value: str, *, name: str) -> None:
    if not re.fullmatch(r"\d{8}", str(value)):
        raise ValueError(f"{name} must use YYYYMMDD, got {value!r}")
    pd.to_datetime(str(value), format="%Y%m%d", errors="raise")


def _discover_minute_partitions(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not root.exists():
        return result
    for child in root.iterdir():
        matched = _PARTITION_PATTERN.fullmatch(child.name)
        if matched is None or not child.is_dir():
            continue
        part = child / "part-00000.parquet"
        if part.is_file():
            result[matched.group(1)] = part
    return dict(sorted(result.items()))


def _frame_validation_issues(date: str, frame: pd.DataFrame) -> dict[str, int]:
    duplicate_rows = int(frame.duplicated(list(MINUTE_KEY_COLUMNS)).sum())
    null_rows = int(frame.loc[:, list(CANONICAL_MINUTE_COLUMNS)].isna().any(axis=1).sum())
    numeric = frame.loc[:, list(CANONICAL_MINUTE_COLUMNS[2:])].apply(
        pd.to_numeric,
        errors="coerce",
    )
    numeric_values = numeric.to_numpy(dtype="float64", na_value=np.nan)
    non_finite_rows = int((~np.isfinite(numeric_values)).any(axis=1).sum())
    invalid_ohlc = int(
        (
            (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1))
            | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1))
        ).sum()
    )
    negative_flow = int(((numeric["vol"] < 0) | (numeric["amount"] < 0)).sum())

    trade_time = pd.to_datetime(frame["trade_time"], errors="coerce")
    valid_time = trade_time.notna()
    wrong_date_rows = int((valid_time & trade_time.dt.strftime("%Y%m%d").ne(date)).sum())
    non_minute_rows = int(
        (
            valid_time
            & (
                trade_time.dt.second.ne(0)
                | trade_time.dt.microsecond.ne(0)
                | trade_time.dt.nanosecond.ne(0)
            )
        ).sum()
    )
    minute_of_day = trade_time.dt.hour * 60 + trade_time.dt.minute
    in_session = minute_of_day.between(9 * 60 + 30, 11 * 60 + 30) | minute_of_day.between(
        13 * 60 + 1,
        15 * 60,
    )
    off_session_rows = int((valid_time & ~in_session).sum())
    invalid_ts_code_rows = int(
        (~frame["ts_code"].astype("string").str.fullmatch(_TS_CODE_PATTERN, na=False)).sum()
    )
    return {
        "empty_partition": int(frame.empty),
        "duplicate_rows": duplicate_rows,
        "null_rows": null_rows,
        "non_finite_rows": non_finite_rows,
        "invalid_ohlc_rows": invalid_ohlc,
        "negative_flow_rows": negative_flow,
        "wrong_date_rows": wrong_date_rows,
        "invalid_ts_code_rows": invalid_ts_code_rows,
        "non_minute_rows": non_minute_rows,
        "off_session_rows": off_session_rows,
    }


def validate_minute_candidate_frame(date: str, frame: pd.DataFrame, *, label: str) -> None:
    """Reject a candidate partition before materialization replaces canonical output."""
    issues = _frame_validation_issues(date, frame)
    failures = {name: count for name, count in issues.items() if count}
    if failures:
        raise ValueError(f"{label} failed canonical validation: {failures}")


def validate_guan_deal_override_session(date: str, frame: pd.DataFrame) -> dict[str, Any]:
    """Require the full expected market session for an explicit annual deal override."""
    trade_time = pd.to_datetime(frame["trade_time"], errors="coerce")
    expected_min = pd.to_datetime(f"{date} 09:30:00", format="%Y%m%d %H:%M:%S")
    expected_max = pd.to_datetime(f"{date} 15:00:00", format="%Y%m%d %H:%M:%S")
    time_min = trade_time.min() if not frame.empty else pd.NaT
    time_max = trade_time.max() if not frame.empty else pd.NaT
    opening = frame.loc[trade_time.eq(expected_min)]
    closing = frame.loc[trade_time.eq(expected_max)]
    issues = {
        "unexpected_market_time_min": int(
            cast(bool, bool(pd.isna(time_min)) or time_min != expected_min)
        ),
        "unexpected_market_time_max": int(
            cast(bool, bool(pd.isna(time_max)) or time_max != expected_max)
        ),
        "missing_opening_execution_bar": int(opening.empty),
        "missing_closing_execution_bar": int(closing.empty),
    }
    failures = {name: value for name, value in issues.items() if value}
    if failures:
        raise ValueError(
            f"Guan deal whole-day annual override {date} failed session validation: "
            f"{failures}, time_min={time_min}, time_max={time_max}"
        )
    return {
        "profile": "guan_deal_full_session",
        "time_min": str(time_min),
        "time_max": str(time_max),
        "opening_bar_rows": len(opening),
        "opening_bar_symbols": int(opening["ts_code"].nunique()),
        "closing_bar_rows": len(closing),
        "closing_bar_symbols": int(closing["ts_code"].nunique()),
        "issues": issues,
        "valid": True,
    }


# Temporary private aliases keep the historical provider part02 compatibility facade working.
_validate_candidate_frame = validate_minute_candidate_frame
_validate_deal_override_session = validate_guan_deal_override_session


def _validate_partition(date: str, path: Path) -> dict[str, Any]:
    schema = pq.ParquetFile(path).schema_arrow
    if not schema.equals(CANONICAL_MINUTE_SCHEMA):
        raise ValueError(f"Canonical schema mismatch for {path}: {schema}")
    frame = pq.ParquetFile(path).read().to_pandas()
    issues = _frame_validation_issues(date, frame)
    return {
        "date": date,
        "path": str(path),
        "rows": len(frame),
        "symbols": int(frame["ts_code"].nunique()),
        "time_min": str(frame["trade_time"].min()) if not frame.empty else None,
        "time_max": str(frame["trade_time"].max()) if not frame.empty else None,
        "issues": issues,
        "valid": not any(issues.values()),
    }


def _validate_prevalidated_partition(date: str, path: Path) -> dict[str, Any]:
    parquet_file = pq.ParquetFile(path)
    schema = parquet_file.schema_arrow
    if not schema.equals(CANONICAL_MINUTE_SCHEMA):
        raise ValueError(f"Canonical schema mismatch for {path}: {schema}")
    rows = int(parquet_file.metadata.num_rows)
    issues = {
        "empty_partition": int(rows == 0),
        "duplicate_rows": 0,
        "null_rows": 0,
        "non_finite_rows": 0,
        "invalid_ohlc_rows": 0,
        "negative_flow_rows": 0,
        "wrong_date_rows": 0,
        "invalid_ts_code_rows": 0,
        "non_minute_rows": 0,
        "off_session_rows": 0,
    }
    return {
        "date": date,
        "path": str(path),
        "rows": rows,
        "symbols": None,
        "time_min": None,
        "time_max": None,
        "content_validation": "candidate_prevalidated",
        "issues": issues,
        "valid": not any(issues.values()),
    }


def _partitions_in_range(
    output_dir: str | Path,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Path]:
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date and end_date must be provided together")
    if start_date is not None and end_date is not None:
        _validate_date(start_date, name="start_date")
        _validate_date(end_date, name="end_date")
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")

    partitions = _discover_minute_partitions(Path(output_dir).expanduser())
    if start_date is not None and end_date is not None:
        partitions = {
            date: path for date, path in partitions.items() if start_date <= date <= end_date
        }
    return partitions


def validate_fused_minute_dataset(
    output_dir: str | Path,
    *,
    expected_dates: set[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    prevalidated_dates: set[str] | None = None,
) -> dict[str, Any]:
    """Validate every canonical partition and return manifest-ready details."""
    partitions = _partitions_in_range(output_dir, start_date, end_date)
    trusted = prevalidated_dates or set()
    details = [
        (
            _validate_prevalidated_partition(date, path)
            if date in trusted
            else _validate_partition(date, path)
        )
        for date, path in partitions.items()
    ]
    invalid = [item["date"] for item in details if not item["valid"]]
    output_dates = set(partitions)
    expected = set(expected_dates) if expected_dates is not None else None
    missing = sorted(expected.difference(output_dates)) if expected is not None else []
    orphan = sorted(output_dates.difference(expected)) if expected is not None else []
    empty_dataset = not details
    empty_source_inventory = expected is not None and not expected
    failed = bool(invalid or missing or orphan or empty_dataset or empty_source_inventory)
    return {
        "status": "failed" if failed else "passed",
        "partition_count": len(details),
        "rows": sum(int(item["rows"]) for item in details),
        "date_min": details[0]["date"] if details else None,
        "date_max": details[-1]["date"] if details else None,
        "invalid_dates": invalid,
        "missing_dates": missing,
        "orphan_dates": orphan,
        "empty_dataset": empty_dataset,
        "empty_source_inventory": empty_source_inventory,
        "output_dates_in_range": sorted(output_dates),
        "partitions": details,
    }


__all__ = [
    "validate_fused_minute_dataset",
    "validate_guan_deal_override_session",
    "validate_minute_candidate_frame",
]
