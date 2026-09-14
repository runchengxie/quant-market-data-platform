"""A-share hotspot features derived from TuShare hot-list and concept assets."""

from __future__ import annotations

from pathlib import Path

from market_data_platform.providers.tushare_a_share_hotspot_features_part01 import (
    HOTSPOT_FEATURE_COLUMNS,
    HOTSPOT_FEATURE_KEY_COLUMNS,
    _asset_files,
)
from market_data_platform.providers.tushare_common import (
    normalize_ts_code,
    pandas,
)
from market_data_platform.providers.tushare_flow_utils import (
    date_token,
    extract_trade_date,
    read_frame,
)


def validate_a_share_hotspot_features(
    *,
    asset_dir: str | Path,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, object]:
    pd = pandas()
    files = _asset_files(asset_dir, label="hotspot_features")
    rows = 0
    symbols: set[str] = set()
    columns: set[str] = set()
    duplicate_rows = 0
    feature_non_null_values = 0
    bad_available_dates = 0
    seen_keys: set[tuple[str, str]] = set()

    for path in files:
        frame = read_frame(path)
        if "trade_date" not in frame.columns:
            frame["trade_date"] = extract_trade_date(path)
        frame["trade_date"] = frame["trade_date"].map(date_token)
        if "symbol" in frame.columns:
            frame["symbol"] = frame["symbol"].map(normalize_ts_code)
        rows += int(len(frame))
        columns.update(str(column) for column in frame.columns)
        if "symbol" in frame.columns:
            symbols.update(frame["symbol"].dropna().astype(str).tolist())
        present = [column for column in HOTSPOT_FEATURE_COLUMNS if column in frame.columns]
        feature_non_null_values += int(frame[present].notna().to_numpy().sum()) if present else 0
        if set(HOTSPOT_FEATURE_KEY_COLUMNS).issubset(frame.columns):
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

    missing_keys = sorted(set(HOTSPOT_FEATURE_KEY_COLUMNS) - columns)
    missing_features = sorted(set(HOTSPOT_FEATURE_COLUMNS) - columns)
    checks = [
        {"id": "required_columns", "passed": not missing_keys, "missing": missing_keys},
        {"id": "feature_columns", "passed": not missing_features, "missing": missing_features},
        {"id": "min_rows", "passed": rows >= min_rows, "actual": rows, "expected": min_rows},
        {
            "id": "min_symbols",
            "passed": len(symbols) >= min_symbols,
            "actual": len(symbols),
            "expected": min_symbols,
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
    failed = [check for check in checks if not bool(check.get("passed"))]
    return {
        "status": "passed" if not failed else "failed",
        "checks": checks,
        "totals": {
            "rows": rows,
            "symbols": len(symbols),
            "files": len(files),
            "feature_columns": len(set(HOTSPOT_FEATURE_COLUMNS) & columns),
            "feature_non_null_values": feature_non_null_values,
        },
    }
