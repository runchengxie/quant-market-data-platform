"""Market-level features derived from TuShare moneyflow_hsgt data."""

from __future__ import annotations

import shutil
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_common import (
    pandas,
    write_frame,
    write_manifest,
)
from market_data_platform.providers.tushare_flow_utils import (
    date_token,
    numeric,
    read_frame,
    trade_date_part_map,
)

HSGT_FEATURE_KEY_COLUMNS = ("trade_date", "available_date")
DEFAULT_HSGT_FEATURE_WINDOWS = (5, 20, 60)
HSGT_RAW_COLUMNS = (
    "north_money",
    "south_money",
    "hgt",
    "sgt",
    "ggt_ss",
    "ggt_sz",
)


def _normalize_windows(windows: Iterable[int] | None) -> tuple[int, ...]:
    values = tuple(sorted({int(window) for window in (windows or DEFAULT_HSGT_FEATURE_WINDOWS)}))
    if not values or any(window <= 0 for window in values):
        raise ValueError("windows must contain positive integers.")
    return values


def _prepare_hsgt_frame(frame: Any, *, trade_date: str | None = None) -> Any:
    df = frame.copy()
    if df.empty:
        return df
    if "trade_date" not in df.columns:
        if trade_date is None:
            raise ValueError("moneyflow_hsgt is missing trade_date.")
        df["trade_date"] = trade_date
    df["trade_date"] = df["trade_date"].map(date_token)
    for column in HSGT_RAW_COLUMNS:
        if column in df.columns:
            df[column] = numeric(df, column)
    mask = df["trade_date"].astype(str).str.fullmatch(r"\d{8}", na=False)
    return df.loc[mask].copy()


def _sum_columns(frame: Any, columns: Sequence[str]) -> Any:
    pd = pandas()
    present = [column for column in columns if column in frame.columns]
    if not present:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    result = pd.Series(0.0, index=frame.index, dtype="float64")
    for column in present:
        result = result + numeric(frame, column).fillna(0.0)
    return result


def _load_hsgt_frame(hsgt_dir: str | Path) -> Any:
    pd = pandas()
    parts = trade_date_part_map(hsgt_dir, label="moneyflow_hsgt")
    frames = [
        _prepare_hsgt_frame(read_frame(path), trade_date=trade_date)
        for trade_date, path in parts.items()
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "north_money" not in df.columns:
        df["north_money"] = _sum_columns(df, ("hgt", "sgt"))
    if "south_money" not in df.columns:
        df["south_money"] = _sum_columns(df, ("ggt_ss", "ggt_sz"))
    for column in HSGT_RAW_COLUMNS:
        if column not in df.columns:
            df[column] = float("nan")
    return (
        df.groupby("trade_date", dropna=False, sort=True)
        .agg(
            north_money=("north_money", "sum"),
            south_money=("south_money", "sum"),
            hgt=("hgt", "sum"),
            sgt=("sgt", "sum"),
            ggt_ss=("ggt_ss", "sum"),
            ggt_sz=("ggt_sz", "sum"),
        )
        .reset_index()
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def _rolling_zscore(values: Any, window: int) -> Any:
    rolling = values.rolling(window, min_periods=2)
    mean = rolling.mean()
    std = rolling.std(ddof=0)
    return (values - mean) / std.where(std > 1e-12)


def _with_hsgt_features(frame: Any, *, windows: Sequence[int]) -> Any:
    df = frame.sort_values("trade_date").copy()
    df["available_date"] = df["trade_date"]
    df["hsgt_north_money"] = numeric(df, "north_money")
    df["hsgt_south_money"] = numeric(df, "south_money")
    df["hsgt_north_south_spread"] = df["hsgt_north_money"] - df["hsgt_south_money"]
    for window in windows:
        df[f"hsgt_north_money_{window}d_sum"] = (
            df["hsgt_north_money"].rolling(window, min_periods=1).sum()
        )
        df[f"hsgt_south_money_{window}d_sum"] = (
            df["hsgt_south_money"].rolling(window, min_periods=1).sum()
        )
        df[f"hsgt_north_south_spread_{window}d_sum"] = (
            df["hsgt_north_south_spread"].rolling(window, min_periods=1).sum()
        )
        df[f"hsgt_north_money_{window}d_zscore"] = _rolling_zscore(
            df["hsgt_north_money"],
            window,
        )
        df[f"hsgt_south_money_{window}d_zscore"] = _rolling_zscore(
            df["hsgt_south_money"],
            window,
        )
        df[f"hsgt_north_positive_ratio_{window}d"] = (
            (df["hsgt_north_money"] > 0.0).astype(float).rolling(window, min_periods=1).mean()
        )
    feature_columns = [column for column in df.columns if column.startswith("hsgt_")]
    return df.loc[:, [*HSGT_FEATURE_KEY_COLUMNS, *feature_columns]]


def _feature_columns(frame: Any) -> list[str]:
    return sorted(
        column
        for column in frame.columns
        if column not in HSGT_FEATURE_KEY_COLUMNS and column.startswith("hsgt_")
    )


def _non_null_feature_values(frame: Any, feature_columns: Sequence[str]) -> int:
    present = [column for column in feature_columns if column in frame.columns]
    if not present:
        return 0
    return int(frame[present].notna().to_numpy().sum())


def _write_trade_date_partitions(frame: Any, out_dir: Path) -> int:
    files = 0
    for trade_date, group in frame.groupby("trade_date", sort=True):
        write_frame(
            group.reset_index(drop=True),
            out_dir / "data" / f"trade_date={trade_date}" / "part.parquet",
        )
        files += 1
    return files


def build_a_share_hsgt_market_features(
    *,
    moneyflow_hsgt_dir: str | Path,
    out_dir: str | Path,
    windows: Iterable[int] | None = None,
    min_rows: int = 1,
) -> dict[str, Any]:
    """Build market-level Connect moneyflow regime features from moneyflow_hsgt."""
    output_dir = Path(out_dir).expanduser().resolve()
    normalized_windows = _normalize_windows(windows)
    raw = _load_hsgt_frame(moneyflow_hsgt_dir)
    features = _with_hsgt_features(raw, windows=normalized_windows)
    feature_columns = _feature_columns(features)
    feature_non_null_values = _non_null_feature_values(features, feature_columns)
    totals = {
        "rows": int(len(features)),
        "files": int(features["trade_date"].nunique()) if "trade_date" in features else 0,
        "feature_columns": len(feature_columns),
        "feature_non_null_values": feature_non_null_values,
    }
    if totals["rows"] < min_rows:
        raise ValueError(f"hsgt_market_features asset is too small: {totals}")
    if feature_non_null_values <= 0:
        raise ValueError("hsgt_market_features contains no non-null feature values.")

    data_dir = output_dir / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    files = _write_trade_date_partitions(features, output_dir)
    totals["files"] = files
    manifest = {
        "schema_version": "tushare.a_share.hsgt_market_features.v1",
        "dataset": "hsgt_market_features",
        "market": "a_share",
        "provider": "derived",
        "status": "completed",
        "output_dir": str(output_dir),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "moneyflow_hsgt_dir": str(Path(moneyflow_hsgt_dir).expanduser().resolve()),
        },
        "query": {
            "start_date": str(features["trade_date"].min()),
            "end_date": str(features["trade_date"].max()),
            "partition_by": "trade_date",
            "windows": list(normalized_windows),
        },
        "semantics": {
            "point_in_time": True,
            "available_date_column": "available_date",
            "available_date_rule": "same as trade_date for post-close market flow features",
            "amount_unit": "CNY 100 million, as returned by TuShare moneyflow_hsgt",
            "scope": "market-level regime features; no symbol column is present",
        },
        "feature_columns": feature_columns,
        "totals": totals,
    }
    write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def validate_a_share_hsgt_market_features(
    *,
    asset_dir: str | Path,
    min_rows: int = 1,
) -> dict[str, object]:
    pd = pandas()
    root = Path(asset_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Asset directory not found: {root}")
    data_root = root / "data" if (root / "data").exists() else root
    files = sorted(
        path
        for path in data_root.glob("**/*")
        if path.is_file() and path.suffix.lower() in {".parquet", ".pq", ".csv"}
    )
    if not files:
        raise FileNotFoundError(f"hsgt_market_features contains no data files: {data_root}")

    frames = [_prepare_hsgt_frame(read_frame(path)) for path in files]
    frames = [frame for frame in frames if not frame.empty]
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    columns = {str(column) for column in frame.columns}
    feature_columns = _feature_columns(frame)
    duplicate_dates = int(frame.duplicated(["trade_date"]).sum()) if "trade_date" in frame else 0
    missing = sorted(set(HSGT_FEATURE_KEY_COLUMNS) - columns)
    feature_non_null_values = _non_null_feature_values(frame, feature_columns)
    checks = [
        {"id": "required_columns", "passed": not missing, "missing": missing},
        {
            "id": "min_rows",
            "passed": len(frame) >= min_rows,
            "actual": int(len(frame)),
            "expected": min_rows,
        },
        {
            "id": "feature_columns_present",
            "passed": bool(feature_columns),
            "features": feature_columns,
        },
        {
            "id": "feature_values_non_null",
            "passed": feature_non_null_values > 0,
            "non_null_values": feature_non_null_values,
        },
        {
            "id": "unique_trade_date",
            "passed": duplicate_dates == 0,
            "duplicates": duplicate_dates,
        },
    ]
    failed = [check for check in checks if not bool(check.get("passed"))]
    return {
        "status": "passed" if not failed else "failed",
        "checks": checks,
        "totals": {
            "rows": int(len(frame)),
            "files": len(files),
            "feature_columns": len(feature_columns),
            "feature_non_null_values": feature_non_null_values,
        },
    }
