"""Shared helpers for TuShare flow/feature asset builders.

These helpers are intentionally small and generic so multiple feature builders can share
stable semantics for trade-date parsing, partition discovery, and basic frame operations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .tushare_common import normalize_ts_code, pandas, validate_date


def date_token(value: object) -> str:
    """Normalize arbitrary value to an 8-digit YYYYMMDD token.

    Behavior intentionally matches existing feature parsing paths, including ignoring
    decimal suffixes and non-date placeholders.
    """
    text = str(value or "").strip().replace("-", "")
    if text.endswith(".0"):
        text = text[:-2]
    if not text or text.lower() in {"nan", "none", "nat"}:
        return ""
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) >= 8:
        return validate_date(digits[:8])
    return ""


def read_frame(path: str | Path, *, columns: list[str] | None = None) -> Any:
    pd = pandas()
    source = Path(path).expanduser().resolve()
    suffix = source.suffix.lower()
    if suffix == ".csv":
        if columns:
            return pd.read_csv(source, usecols=lambda column: column in set(columns))
        return pd.read_csv(source)
    if suffix in {".parquet", ".pq"}:
        if columns:
            try:
                return pd.read_parquet(source, columns=columns)
            except Exception:
                pass
        return pd.read_parquet(source)
    raise ValueError(f"Unsupported source file type: {source}")


def partition_payload(frame: Any, *, partition_column: str = "trade_date") -> Any:
    if partition_column not in frame.columns:
        return frame
    return frame.drop(columns=[partition_column])


def extract_trade_date(path: Path) -> str:
    for part in reversed(path.parts):
        if part.startswith("trade_date="):
            return date_token(part.removeprefix("trade_date="))
    return date_token(path.stem)


def trade_date_part_map(asset_dir: str | Path | None, *, label: str) -> dict[str, Path]:
    if asset_dir is None:
        return {}
    root = Path(asset_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"{label} asset directory not found: {root}")
    if root.is_file():
        trade_date = extract_trade_date(root)
        if not trade_date:
            raise ValueError(f"{label} file path does not include a YYYYMMDD trade date: {root}")
        return {trade_date: root}
    data_root = root / "data" if (root / "data").exists() else root
    files = sorted(
        path
        for path in data_root.glob("**/*")
        if path.is_file() and path.suffix.lower() in {".parquet", ".pq", ".csv"}
    )
    parts: dict[str, Path] = {}
    for path in files:
        trade_date = extract_trade_date(path)
        if trade_date:
            parts[trade_date] = path
    if not parts:
        raise ValueError(
            f"{label} must contain files partitioned by trade_date=YYYYMMDD or dated filenames."
        )
    return dict(sorted(parts.items()))


def numeric(frame: Any, column: str) -> Any:
    pd = pandas()
    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def prepare_index_frame(frame: Any, *, label: str, trade_date: str | None = None) -> Any:
    df = frame.copy()
    if df.empty:
        return df
    if "symbol" not in df.columns and "ts_code" in df.columns:
        df["symbol"] = df["ts_code"]
    if "symbol" not in df.columns:
        raise ValueError(f"{label} is missing symbol or ts_code.")
    df["symbol"] = df["symbol"].map(normalize_ts_code)
    if "trade_date" not in df.columns:
        if trade_date is None:
            raise ValueError(f"{label} is missing trade_date.")
        df["trade_date"] = trade_date
    else:
        df["trade_date"] = df["trade_date"].map(date_token)
    mask = (df["symbol"].astype(str).str.len() > 0) & (
        df["trade_date"].astype(str).str.fullmatch(r"\d{8}", na=False)
    )
    return df.loc[mask].copy()


def _asset_files(asset_dir: str | Path, *, label: str) -> list[Path]:
    root = Path(asset_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"{label} asset directory not found: {root}")
    if root.is_file():
        return [root]
    data_root = root / "data" if (root / "data").exists() else root
    files = sorted(
        path
        for path in data_root.glob("**/*")
        if path.is_file() and path.suffix.lower() in {".parquet", ".pq", ".csv"}
    )
    if not files:
        raise FileNotFoundError(f"{label} contains no CSV/Parquet files: {data_root}")
    return files


def _trade_date_from_path(path: Path) -> str:
    return extract_trade_date(path)


__all__ = [
    "date_token",
    "read_frame",
    "partition_payload",
    "extract_trade_date",
    "trade_date_part_map",
    "numeric",
    "prepare_index_frame",
    "_asset_files",
]
