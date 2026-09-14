"""Restartable TuShare constraint references and reconstructed historical ST status."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers._bse_code_mapping import canonical_bse_symbol
from market_data_platform.providers.tushare_common import normalize_ts_code, pandas
from market_data_platform.providers.tushare_constraint_download import (
    CONSTRAINT_REFERENCE_DATASETS,
    download_constraint_reference,
)
from market_data_platform.providers.tushare_constraint_io import (
    CONSTRAINT_RECEIPT_SCHEMA,
    ConstraintDownloadOptions,
    atomic_json,
    atomic_parquet,
    sha256,
)
from market_data_platform.providers.tushare_constraint_publish import (
    CONSTRAINT_PUBLISH_DATASETS,
    publish_constraint_assets,
)

ST_RECEIPT_SCHEMA = "market-data-platform.reconstructed-st-history.v1"
ST_NAME_PATTERN = re.compile(r"^\s*(?:S\*?ST|\*?ST|PT)", re.IGNORECASE)


@dataclass(frozen=True)
class ReconstructedSTOptions:
    namechange_path: str | Path
    trade_cal_path: str | Path
    instruments_path: str | Path
    out_dir: str | Path
    start_date: str
    end_date: str
    stock_st_path: str | Path | None = None
    min_precision: float = 0.90
    min_recall: float = 0.90


def _validate_date(value: str, field: str) -> None:
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError as error:
        raise ValueError(f"{field} must be a valid YYYYMMDD date") from error
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError(f"{field} must be a valid YYYYMMDD date")


def _calendar_frame(options: ReconstructedSTOptions) -> Any:
    pd = pandas()
    frame = pd.read_parquet(Path(options.trade_cal_path).expanduser().resolve())
    column = "cal_date" if "cal_date" in frame else "trade_date"
    if column not in frame:
        raise ValueError("trade calendar lacks cal_date/trade_date")
    if "is_open" in frame:
        frame = frame[frame["is_open"].astype(str).isin(("1", "True", "true"))]
    dates = frame[column].astype(str).str.replace("-", "", regex=False).str.slice(0, 8)
    dates = dates[(dates >= options.start_date) & (dates <= options.end_date)]
    if dates.empty:
        raise ValueError("trade calendar is empty in the requested ST window")
    return pd.DataFrame({"trade_date": sorted(dates.unique())})


def _st_intervals(options: ReconstructedSTOptions) -> Any:
    pd = pandas()
    frame = pd.read_parquet(Path(options.namechange_path).expanduser().resolve())
    required = {"ts_code", "name", "start_date", "end_date"}
    if missing := required - set(frame):
        raise ValueError(f"namechange lacks required columns: {sorted(missing)}")
    frame["ts_code"] = frame["ts_code"].map(normalize_ts_code)
    for column in ("start_date", "end_date", "ann_date"):
        if column in frame:
            frame[column] = (
                frame[column].astype("string").str.replace("-", "", regex=False).str.slice(0, 8)
            )
    frame = frame.dropna(subset=["start_date"])
    frame = frame.sort_values(["ts_code", "start_date", "end_date"], na_position="last")
    next_start = frame.groupby("ts_code", sort=False)["start_date"].shift(-1)
    frame["next_name_start_minus_one"] = (
        pd.to_datetime(next_start, format="%Y%m%d", errors="coerce")
        .sub(pd.Timedelta(days=1))
        .dt.strftime("%Y%m%d")
    )

    instruments_path = Path(options.instruments_path).expanduser().resolve()
    instruments = pd.read_parquet(instruments_path)
    instrument_columns = ("ts_code", "list_date", "delist_date")
    instrument_required = set(instrument_columns)
    if missing := instrument_required - set(instruments):
        raise ValueError(f"instruments lacks required columns: {sorted(missing)}")
    instruments = instruments[list(instrument_columns)].copy()
    instruments["ts_code"] = instruments["ts_code"].map(normalize_ts_code)
    for column in ("list_date", "delist_date"):
        instruments[column] = (
            instruments[column].astype("string").str.replace("-", "", regex=False).str.slice(0, 8)
        )
    instruments = instruments.drop_duplicates("ts_code", keep="last")
    frame = frame.merge(instruments, on="ts_code", how="left", validate="many_to_one")
    frame["delist_date_minus_one"] = (
        pd.to_datetime(frame["delist_date"], format="%Y%m%d", errors="coerce")
        .sub(pd.Timedelta(days=1))
        .dt.strftime("%Y%m%d")
    )
    starts = pd.DataFrame(
        {
            "query_start": options.start_date,
            "name_start": frame["start_date"],
            "list_date": frame["list_date"],
        }
    )
    ends = pd.DataFrame(
        {
            "query_end": options.end_date,
            "explicit_name_end": frame["end_date"],
            "next_name_start_minus_one": frame["next_name_start_minus_one"],
            "delist_date_minus_one": frame["delist_date_minus_one"],
        }
    )
    frame["interval_start"] = starts.fillna("00000000").max(axis=1)
    bounded_ends = ends.fillna("99999999")
    frame["interval_end"] = bounded_ends.min(axis=1)
    frame["effective_end_reason"] = bounded_ends.idxmin(axis=1)
    frame = frame[frame["name"].astype("string").str.match(ST_NAME_PATTERN, na=False)].copy()
    frame = frame[frame["interval_start"] <= frame["interval_end"]]
    missing_instruments = frame["list_date"].isna() & frame["delist_date"].isna()
    if missing_instruments.any():
        symbols = sorted(frame.loc[missing_instruments, "ts_code"].unique())
        raise ValueError(
            "ST namechange symbols are missing from instruments: " + ", ".join(symbols[:10])
        )
    columns = ["ts_code", "name", "interval_start", "interval_end"]
    columns.extend(
        column
        for column in ("ann_date", "change_reason", "effective_end_reason")
        if column in frame
    )
    intervals = frame[columns].drop_duplicates().sort_values(columns[:4]).reset_index(drop=True)
    intervals["source"] = "tushare_namechange_reconstructed"
    intervals["pit_class"] = "reconstructed_pit"
    return intervals


def _expand_st_history(intervals: Any, calendar: Any) -> Any:
    pd = pandas()
    frames: list[Any] = []
    for row in intervals.itertuples(index=False):
        dates = calendar.loc[
            calendar["trade_date"].between(row.interval_start, row.interval_end),
            "trade_date",
        ]
        if dates.empty:
            continue
        frames.append(
            pd.DataFrame(
                {
                    "trade_date": dates.to_numpy(),
                    "ts_code": row.ts_code,
                    "name": row.name,
                    "interval_start": row.interval_start,
                    "interval_end": row.interval_end,
                    "ann_date": getattr(row, "ann_date", None),
                    "source": row.source,
                    "pit_class": row.pit_class,
                }
            )
        )
    if not frames:
        return pd.DataFrame(columns=["trade_date", "ts_code"])
    history = pd.concat(frames, ignore_index=True, sort=False)
    return (
        history.sort_values(["trade_date", "ts_code", "interval_start"])
        .drop_duplicates(["trade_date", "ts_code"], keep="last")
        .reset_index(drop=True)
    )


def _cross_validation(history: Any, stock_st_path: str | Path | None) -> dict[str, Any]:
    pd = pandas()
    if stock_st_path is None:
        return {"status": "not_available"}
    path = Path(stock_st_path).expanduser().resolve()
    if not path.is_file():
        return {"status": "not_available", "path": str(path)}
    truth = pd.read_parquet(path, columns=["trade_date", "ts_code"])
    truth["trade_date"] = (
        truth["trade_date"].astype(str).str.replace("-", "", regex=False).str.slice(0, 8)
    )
    truth["ts_code"] = truth["ts_code"].map(normalize_ts_code)
    historical_codes = truth["ts_code"].copy()
    truth["ts_code"] = truth["ts_code"].map(canonical_bse_symbol)
    truth_keys = set(truth[["trade_date", "ts_code"]].itertuples(index=False, name=None))
    dates = set(truth["trade_date"])
    rebuilt = history[history["trade_date"].isin(dates)]
    rebuilt_keys = set(rebuilt[["trade_date", "ts_code"]].itertuples(index=False, name=None))
    true_positive = len(truth_keys & rebuilt_keys)
    false_positive = len(rebuilt_keys - truth_keys)
    false_negative = len(truth_keys - rebuilt_keys)
    precision = true_positive / (true_positive + false_positive) if rebuilt_keys else 0.0
    recall = true_positive / len(truth_keys) if truth_keys else 0.0
    union = len(truth_keys | rebuilt_keys)
    return {
        "status": "compared",
        "start_date": min(dates) if dates else None,
        "end_date": max(dates) if dates else None,
        "truth_rows": len(truth_keys),
        "reconstructed_rows": len(rebuilt_keys),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "jaccard": true_positive / union if union else 0.0,
        "canonicalized_bse_alias_rows": int((historical_codes != truth["ts_code"]).sum()),
    }


def build_reconstructed_st_history(options: ReconstructedSTOptions) -> dict[str, Any]:
    """Reconstruct positive ST trading-day rows from effective name intervals."""
    _validate_date(options.start_date, "start_date")
    _validate_date(options.end_date, "end_date")
    if options.start_date > options.end_date:
        raise ValueError("start_date must not exceed end_date")
    if not 0 <= options.min_precision <= 1 or not 0 <= options.min_recall <= 1:
        raise ValueError("min_precision and min_recall must be between 0 and 1")
    calendar = _calendar_frame(options)
    intervals = _st_intervals(options)
    history = _expand_st_history(intervals, calendar)
    validation = _cross_validation(history, options.stock_st_path)
    compared = validation.get("status") == "compared"
    passed = compared and (
        validation["precision"] >= options.min_precision
        and validation["recall"] >= options.min_recall
    )
    quality_status = "complete" if passed else "partial"
    root = Path(options.out_dir).expanduser().resolve()
    interval_path = root / "st_intervals_reconstructed.parquet"
    history_path = root / "st_history_reconstructed.parquet"
    atomic_parquet(intervals, interval_path)
    atomic_parquet(history, history_path)
    receipt = {
        "schema_version": ST_RECEIPT_SCHEMA,
        "built_at": datetime.now(UTC).isoformat(),
        "start_date": options.start_date,
        "end_date": options.end_date,
        "rows": int(len(history)),
        "symbols": int(history["ts_code"].nunique()),
        "intervals": int(len(intervals)),
        "quality_status": quality_status,
        "pit_class": "reconstructed_pit",
        "revision_safe": False,
        "namechange_sha256": sha256(Path(options.namechange_path).expanduser().resolve()),
        "trade_cal_sha256": sha256(Path(options.trade_cal_path).expanduser().resolve()),
        "instruments_sha256": sha256(Path(options.instruments_path).expanduser().resolve()),
        "history_sha256": sha256(history_path),
        "intervals_sha256": sha256(interval_path),
        "cross_validation": validation,
        "thresholds": {
            "min_precision": options.min_precision,
            "min_recall": options.min_recall,
        },
    }
    receipt_path = root / "st_history_reconstructed.receipt.json"
    atomic_json(receipt_path, receipt)
    return {
        **receipt,
        "status": "passed" if passed else "failed",
        "history_path": str(history_path),
        "interval_path": str(interval_path),
        "receipt_path": str(receipt_path),
    }


__all__ = [
    "CONSTRAINT_PUBLISH_DATASETS",
    "CONSTRAINT_RECEIPT_SCHEMA",
    "CONSTRAINT_REFERENCE_DATASETS",
    "ConstraintDownloadOptions",
    "ReconstructedSTOptions",
    "ST_RECEIPT_SCHEMA",
    "build_reconstructed_st_history",
    "download_constraint_reference",
    "publish_constraint_assets",
]
