"""Validation for derived A-share flow and ownership feature assets."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_flow_features import (
    FLOW_FEATURE_KEY_COLUMNS,
)
from market_data_platform.providers.tushare_common import pandas
from market_data_platform.providers.tushare_flow_utils import (
    date_token,
    extract_trade_date,
    prepare_index_frame,
    read_frame,
)


def _asset_files(asset_dir: str | Path) -> list[Path]:
    root = Path(asset_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Asset directory not found: {root}")
    if root.is_file():
        return [root]
    data_root = root / "data" if (root / "data").exists() else root
    files = sorted(
        path
        for path in data_root.glob("**/*")
        if path.is_file() and path.suffix.lower() in {".parquet", ".pq", ".csv"}
    )
    if not files:
        raise FileNotFoundError(f"Asset data directory contains no CSV/Parquet files: {data_root}")
    return files


def _feature_columns_from_frame(columns: Iterable[str]) -> list[str]:
    prefixes = ("mf_", "fund_", "top10_", "top_inst_", "holder_", "holdertrade_")
    return sorted(
        column
        for column in columns
        if column not in FLOW_FEATURE_KEY_COLUMNS and column.startswith(prefixes)
    )


def _non_null_feature_values(frame, feature_columns: Iterable[str]) -> int:
    present = [column for column in feature_columns if column in frame.columns]
    if not present:
        return 0
    return int(frame[present].notna().to_numpy().sum())


def _validation_result(
    checks: list[dict[str, Any]],
    *,
    totals: Mapping[str, object],
) -> dict[str, object]:
    failed = [check for check in checks if not bool(check.get("passed"))]
    return {
        "status": "passed" if not failed else "failed",
        "checks": checks,
        "totals": dict(totals),
    }


def validate_a_share_flow_ownership_features(
    *,
    asset_dir: str | Path,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, object]:
    pd = pandas()
    files = _asset_files(asset_dir)
    rows = 0
    symbols: set[str] = set()
    columns: set[str] = set()
    feature_columns: set[str] = set()
    feature_non_null_values = 0
    duplicate_rows = 0
    seen_keys: set[tuple[str, str]] = set()
    bad_available_dates = 0

    for path in files:
        frame = prepare_index_frame(
            read_frame(path),
            label="flow_ownership_features",
            trade_date=extract_trade_date(path),
        )
        rows += int(len(frame))
        columns.update(str(column) for column in frame.columns)
        if "symbol" in frame.columns:
            symbols.update(frame["symbol"].dropna().astype(str).tolist())
        current_feature_columns = _feature_columns_from_frame(frame.columns)
        feature_columns.update(current_feature_columns)
        feature_non_null_values += _non_null_feature_values(frame, current_feature_columns)
        if set(FLOW_FEATURE_KEY_COLUMNS).issubset(frame.columns):
            duplicate_rows += int(frame.duplicated(subset=["symbol", "trade_date"]).sum())
            for symbol, trade_date in zip(frame["symbol"], frame["trade_date"], strict=False):
                key = (str(symbol), str(trade_date))
                if key in seen_keys:
                    duplicate_rows += 1
                else:
                    seen_keys.add(key)
            trade_dates = pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce")
            available_dates = pd.to_datetime(
                frame["available_date"].map(date_token),
                format="%Y%m%d",
                errors="coerce",
            )
            bad_available_dates += int((available_dates < trade_dates).sum())

    missing = sorted(set(FLOW_FEATURE_KEY_COLUMNS) - columns)
    checks = [
        {"id": "required_columns", "passed": not missing, "missing": missing},
        {"id": "min_rows", "passed": rows >= min_rows, "actual": rows, "expected": min_rows},
        {
            "id": "min_symbols",
            "passed": len(symbols) >= min_symbols,
            "actual": len(symbols),
            "expected": min_symbols,
        },
        {
            "id": "feature_columns_present",
            "passed": bool(feature_columns),
            "features": sorted(feature_columns),
        },
        {
            "id": "feature_values_non_null",
            "passed": feature_non_null_values > 0,
            "non_null_values": feature_non_null_values,
        },
        {
            "id": "unique_symbol_trade_date",
            "passed": duplicate_rows == 0,
            "duplicates": duplicate_rows,
        },
        {
            "id": "available_not_before_trade_date",
            "passed": bad_available_dates == 0,
            "violations": bad_available_dates,
        },
    ]
    return _validation_result(
        checks,
        totals={
            "rows": rows,
            "symbols": len(symbols),
            "files": len(files),
            "feature_columns": len(feature_columns),
            "feature_non_null_values": feature_non_null_values,
        },
    )
