from __future__ import annotations

import gc
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd

from market_data_platform.runtime_memory import MemoryPolicy
from market_data_platform.standardize.normalize import normalize_ts_code
from market_data_platform.standardize.parquet import read_parquet_parts
from market_data_platform.standardize.schema.a_share_daily import (
    LIMIT_COLUMNS,
    PRICE_COLUMNS,
)

DEFAULT_DAILY_CLEAN_BATCH_TRADE_DATES = 120


@dataclass(frozen=True)
class _DailyCleanInputs:
    daily_dir: str | Path
    adj_factor_dir: str | Path | None
    daily_basic_dir: str | Path | None
    limit_status_dir: str | Path | None
    suspend_dir: str | Path | None
    instruments_file: str | Path | None
    st_history_file: str | Path | None
    out_dir: str | Path


@dataclass
class _DailyCleanStats:
    rows: int = 0
    symbols: set[str] | None = None
    files: int = 0
    duplicate_rows: int = 0
    missing_tr_close: int = 0
    st_rows: int = 0
    suspended_rows: int = 0
    limit_up_rows: int = 0
    limit_down_rows: int = 0
    start_date: str | None = None
    end_date: str | None = None
    columns: set[str] | None = None
    compaction_scan: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.symbols is None:
            self.symbols = set()
        if self.columns is None:
            self.columns = set()

    @property
    def symbol_count(self) -> int:
        return len(self.symbols or set())


@dataclass
class _DailyCleanBuildRuntime:
    batches_written: int = 0
    staging_files: int = 0
    trade_dates_processed: int = 0
    soft_flushes: int = 0
    memory_samples: list[dict[str, float | None]] | None = None

    def __post_init__(self) -> None:
        if self.memory_samples is None:
            self.memory_samples = []


@dataclass(frozen=True)
class _DailyCleanManifestRequest:
    inputs: _DailyCleanInputs
    output_dir: Path
    stats: _DailyCleanStats
    runtime: _DailyCleanBuildRuntime
    batch_trade_dates: int
    memory_policy: MemoryPolicy


@dataclass(frozen=True)
class _DailyCleanTradeDateFrameInputs:
    daily: pd.DataFrame
    adj: pd.DataFrame
    daily_basic: pd.DataFrame
    limit_status: pd.DataFrame
    suspend: pd.DataFrame | None
    instruments: pd.DataFrame
    st_history: pd.DataFrame | None
    latest_adj_factors: dict[str, float]
    first_trade_dates: dict[str, str]


def _trade_date_part_map(asset_dir: str | Path | None, *, label: str) -> dict[str, Path]:
    if asset_dir is None:
        return {}
    root = Path(asset_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"{label} asset directory not found: {root}")
    data_root = root / "data" if (root / "data").exists() else root
    files = sorted(data_root.glob("trade_date=*/part.parquet"))
    parts: dict[str, Path] = {}
    for path in files:
        trade_date = path.parent.name.removeprefix("trade_date=")
        if trade_date.isdigit() and len(trade_date) == 8:
            parts[trade_date] = path
    if not parts:
        raise ValueError(
            f"{label} must be partitioned as data/trade_date=YYYYMMDD/part.parquet "
            "for memory-managed daily_clean builds."
        )
    return parts


def _read_trade_date_part(
    parts: dict[str, Path],
    trade_date: str,
    *,
    label: str,
) -> pd.DataFrame:
    path = parts.get(trade_date)
    if path is None:
        return pd.DataFrame()
    return _prepare_index_frame(pd.read_parquet(path), label=label)


def _normalize_trade_date(value: object) -> str:
    text = str(value or "").strip().replace("-", "")
    if text.endswith(".0"):
        text = text[:-2]
    return text[:8]


def _prepare_index_frame(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    if "ts_code" in out.columns:
        out["symbol"] = out["ts_code"].map(normalize_ts_code)
    elif "symbol" in out.columns:
        out["symbol"] = out["symbol"].map(normalize_ts_code)
    else:
        raise ValueError(f"{label} is missing ts_code/symbol.")
    if "trade_date" not in out.columns:
        raise ValueError(f"{label} is missing trade_date.")
    out["trade_date"] = out["trade_date"].map(_normalize_trade_date)
    mask = (out["symbol"] != "") & out["trade_date"].str.fullmatch(r"\d{8}", na=False)
    return cast(pd.DataFrame, out.loc[mask].copy())


def _load_instruments(instruments_file: str | Path | None) -> pd.DataFrame:
    if instruments_file is None:
        return pd.DataFrame()
    path = Path(instruments_file).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"A 股 instruments file not found: {path}")
    frame = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)
    if frame.empty:
        return frame
    out = frame.copy()
    if "ts_code" in out.columns:
        out["symbol"] = out["ts_code"].map(normalize_ts_code)
    elif "symbol" in out.columns:
        out["symbol"] = out["symbol"].map(normalize_ts_code)
    if "name" not in out.columns:
        if "symbol" in out.columns:
            out["name"] = out["symbol"]
        else:
            out["name"] = ""
    if "list_date" in out.columns:
        out["list_date"] = out["list_date"].map(_normalize_trade_date)
    if "symbol" in out.columns:
        return out.drop_duplicates(subset=["symbol"], keep="last")
    return out


def _normalize_suspension_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    for name in ("is_open", "is_suspended", "suspend", "suspended"):
        if name in out.columns:
            out[name] = out[name].astype(str).str.strip().str.lower()
    return out


def _derive_is_suspended(daily: pd.DataFrame, suspend: pd.DataFrame | None) -> pd.Series:
    result = pd.Series(False, index=daily.index, dtype=bool)
    if daily.empty:
        return result
    if suspend is not None and not suspend.empty:
        keys = list(zip(suspend["symbol"], suspend["trade_date"], strict=False))
        result |= daily[["symbol", "trade_date"]].apply(tuple, axis=1).isin(keys)
    if "vol" in daily.columns:
        vol = pd.Series(pd.to_numeric(daily["vol"], errors="coerce"), index=daily.index)
        result |= vol.fillna(0) <= 0
    if "amount" in daily.columns:
        amount = pd.Series(pd.to_numeric(daily["amount"], errors="coerce"), index=daily.index)
        result |= amount.fillna(0) <= 0
    return result


def _derive_st_flag(daily: pd.DataFrame, st_history: pd.DataFrame | None) -> pd.Series:
    result = pd.Series(pd.NA, index=daily.index, dtype="boolean")
    if daily.empty:
        return result
    if st_history is None:
        return result
    keys = set(zip(st_history["ts_code"], st_history["trade_date"], strict=False))
    return (
        pd.Series(list(zip(daily["symbol"], daily["trade_date"], strict=False)), index=daily.index)
        .isin(keys)
        .astype("boolean")
    )


def _load_st_history(
    st_history_file: str | Path | None, trade_dates: list[str]
) -> dict[str, pd.DataFrame] | None:
    if st_history_file is None:
        return None
    path = Path(st_history_file).expanduser().resolve()
    receipt_path = path.with_name("st_history_reconstructed.receipt.json")
    if not receipt_path.is_file():
        receipt_path = path.with_suffix(".receipt.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        receipt.get("quality_status") != "complete"
        or receipt.get("source_quality_status", "complete") != "complete"
    ):
        raise ValueError("ST history must have a complete validation receipt")
    with path.open("rb") as source:
        actual_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
    if receipt.get("history_sha256", receipt.get("sha256")) != actual_sha256:
        raise ValueError("ST history does not match its validation receipt")
    if trade_dates and (
        trade_dates[0] < receipt.get("start_date", "99999999")
        or trade_dates[-1] > receipt.get("end_date", "00000000")
    ):
        raise ValueError("ST history receipt does not cover the daily_clean date range")
    history = pd.read_parquet(path, columns=["ts_code", "trade_date"])
    history["ts_code"] = history["ts_code"].map(normalize_ts_code)
    history["trade_date"] = history["trade_date"].map(_normalize_trade_date)
    if history.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("ST history has duplicate symbol/date rows")
    return {str(date): group for date, group in history.groupby("trade_date", sort=False)}


def _board_from_symbol(symbol: str) -> str:
    text = str(symbol).upper()
    if text.endswith(".BJ"):
        return "BSE"
    if text.startswith("688") and text.endswith(".SH"):
        return "STAR"
    if text.startswith("300") and text.endswith(".SZ"):
        return "CHINEXT"
    return "MAIN"


def _safe_numeric(frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    for column in columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")


def _fill_missing_pre_close(frame: pd.DataFrame) -> None:
    if "pre_close" not in frame.columns or "close" not in frame.columns:
        return
    pre_close = cast(pd.Series, pd.to_numeric(frame["pre_close"], errors="coerce"))
    close = cast(pd.Series, pd.to_numeric(frame["close"], errors="coerce"))
    missing = pre_close.isna() & close.notna()
    if bool(missing.any()):
        frame.loc[missing, "pre_close"] = close.loc[missing]


def _valid_trade_date_series(values: pd.Series) -> pd.Series:
    text = values.astype("string").str.strip().str.replace("-", "", regex=False).str[:8]
    return text.where(text.str.fullmatch(r"\d{8}", na=False))


def _update_first_trade_dates(frame: pd.DataFrame, first_trade_dates: dict[str, str]) -> None:
    if frame.empty or not {"symbol", "trade_date"}.issubset(frame.columns):
        return
    observed = (
        frame.loc[:, ["symbol", "trade_date"]]
        .dropna()
        .astype(str)
        .sort_values(["symbol", "trade_date"])
        .drop_duplicates(subset=["symbol"], keep="first")
    )
    for row in observed.itertuples(index=False):
        symbol = str(row.symbol)
        trade_date = str(row.trade_date)
        if (
            symbol
            and trade_date
            and (symbol not in first_trade_dates or trade_date < first_trade_dates[symbol])
        ):
            first_trade_dates[symbol] = trade_date


def _effective_list_dates(
    out: pd.DataFrame,
    first_trade_dates: dict[str, str] | None,
) -> pd.Series:
    list_date = _valid_trade_date_series(cast(pd.Series, out["list_date"]))
    if not first_trade_dates:
        return list_date
    first_trade = _valid_trade_date_series(
        cast(pd.Series, out["symbol"].map(lambda symbol: first_trade_dates.get(str(symbol))))
    )
    use_first_trade = first_trade.notna() & (list_date.isna() | (first_trade < list_date))
    return list_date.where(~use_first_trade, first_trade)


def _prepare_daily_frame(daily_dir: str | Path) -> pd.DataFrame:
    daily = _prepare_index_frame(read_parquet_parts(daily_dir, label="daily"), label="daily")
    if daily.empty:
        raise ValueError("TuShare A 股 daily raw asset is empty; cannot build daily_clean.")
    daily = daily.drop_duplicates(subset=["symbol", "trade_date"], keep="last")
    _safe_numeric(daily, (*PRICE_COLUMNS, "vol", "amount", "pct_chg", "change"))
    _fill_missing_pre_close(daily)
    return daily


def _latest_adj_factors(
    adj_factor_parts: dict[str, Path],
    *,
    memory_policy: MemoryPolicy,
) -> dict[str, float]:
    latest: dict[str, float] = {}
    for trade_date in sorted(adj_factor_parts):
        memory_policy.require_safe(label=f"daily_clean latest adj_factor {trade_date}")
        frame = _read_trade_date_part(adj_factor_parts, trade_date, label="adj_factor")
        if frame.empty or "adj_factor" not in frame.columns:
            continue
        frame = frame.drop_duplicates(subset=["symbol", "trade_date"], keep="last")
        frame["adj_factor"] = pd.to_numeric(frame["adj_factor"], errors="coerce")
        for row in frame.loc[:, ["symbol", "adj_factor"]].itertuples(index=False):
            latest[str(row.symbol)] = float(row.adj_factor)
        del frame
        gc.collect()
    return latest


def _merge_adjustment_columns_for_trade_date(
    frame: pd.DataFrame,
    adj: pd.DataFrame,
    *,
    latest_adj_factors: dict[str, float],
) -> pd.DataFrame:
    out = frame
    has_adj_factor_input = bool(latest_adj_factors)
    if not adj.empty and "adj_factor" in adj.columns:
        adj = adj.copy()
        adj["adj_factor"] = pd.to_numeric(adj["adj_factor"], errors="coerce")
        adj = adj.drop_duplicates(subset=["symbol", "trade_date"], keep="last")
        out = out.merge(
            adj[["symbol", "trade_date", "adj_factor"]],
            on=["symbol", "trade_date"],
            how="left",
        )
    if "adj_factor" not in out.columns:
        out["adj_factor"] = pd.NA
    out["adj_factor"] = pd.to_numeric(out["adj_factor"], errors="coerce")
    if has_adj_factor_input:
        latest_factor = out["symbol"].map(lambda symbol: latest_adj_factors.get(str(symbol)))
        factor_ratio = out["adj_factor"] / pd.to_numeric(latest_factor, errors="coerce")
    else:
        factor_ratio = pd.Series(1.0, index=out.index, dtype="float64")
    for column in PRICE_COLUMNS:
        if column in out.columns:
            out[f"adj_{column}"] = pd.to_numeric(out[column], errors="coerce") * factor_ratio
    out["tr_close"] = out["adj_close"] if "adj_close" in out.columns else out.get("close")
    out["adjustment_source"] = "adj_factor" if has_adj_factor_input else "raw_unadjusted"
    return out


def _merge_overlay_frame(
    frame: pd.DataFrame,
    overlay: pd.DataFrame,
    *,
    columns: tuple[str, ...],
) -> pd.DataFrame:
    if overlay.empty:
        return frame
    overlay = overlay.drop_duplicates(subset=["symbol", "trade_date"], keep="last")
    keep = ["symbol", "trade_date", *(col for col in columns if col in overlay.columns)]
    _safe_numeric(overlay, tuple(col for col in keep if col not in {"symbol", "trade_date"}))
    return frame.merge(overlay[keep], on=["symbol", "trade_date"], how="left")


def _add_limit_flags(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame
    for column in LIMIT_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA
    close = pd.Series(pd.to_numeric(out.get("close"), errors="coerce"), index=out.index)
    out["is_limit_up"] = close >= pd.Series(
        pd.to_numeric(out["up_limit"], errors="coerce"), index=out.index
    )
    out["is_limit_down"] = close <= pd.Series(
        pd.to_numeric(out["down_limit"], errors="coerce"), index=out.index
    )
    return out


def _load_suspension_trade_date_frame(
    suspend_parts: dict[str, Path],
    trade_date: str,
) -> pd.DataFrame | None:
    if not suspend_parts:
        return None
    frame = _read_trade_date_part(suspend_parts, trade_date, label="suspend")
    return _normalize_suspension_columns(frame)


def _prepare_output_dirs(out_dir: str | Path) -> tuple[Path, Path, Path]:
    output_dir = Path(out_dir).expanduser().resolve()
    data_dir = output_dir / "data"
    staging_dir = output_dir / "_daily_clean_staging"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    return output_dir, data_dir, staging_dir


def _write_daily_clean_staging_batch(
    frames: list[pd.DataFrame],
    *,
    staging_dir: Path,
    batch_index: int,
) -> int:
    if not frames:
        return 0
    chunk = pd.concat(
        [frame.dropna(axis=1, how="all") for frame in frames],
        ignore_index=True,
        sort=False,
    )
    files = 0
    for symbol, symbol_frame in chunk.groupby("symbol", sort=True):
        symbol_dir = staging_dir / f"symbol={symbol}"
        symbol_dir.mkdir(parents=True, exist_ok=True)
        symbol_frame.to_parquet(symbol_dir / f"part_{batch_index:04d}.parquet", index=False)
        files += 1
    return files


def _update_daily_clean_stats(stats: _DailyCleanStats, frame: pd.DataFrame) -> None:
    rows = int(len(frame))
    stats.rows += rows
    stats.files += 1
    if "symbol" in frame.columns and stats.symbols is not None:
        stats.symbols.update(frame["symbol"].dropna().astype(str).unique().tolist())
    if stats.columns is not None:
        stats.columns.update(str(column) for column in frame.columns)
    if "trade_date" in frame.columns and rows:
        min_date = str(frame["trade_date"].min())
        max_date = str(frame["trade_date"].max())
        stats.start_date = min_date if stats.start_date is None else min(stats.start_date, min_date)
        stats.end_date = max_date if stats.end_date is None else max(stats.end_date, max_date)
    if {"symbol", "trade_date"}.issubset(frame.columns):
        stats.duplicate_rows += int(frame.duplicated(subset=["symbol", "trade_date"]).sum())
    stats.missing_tr_close += (
        int(frame["tr_close"].isna().sum()) if "tr_close" in frame.columns else rows
    )
    if "is_st" in frame.columns:
        stats.st_rows += int(frame["is_st"].sum())
    if "is_suspended" in frame.columns:
        stats.suspended_rows += int(frame["is_suspended"].sum())
    if "is_limit_up" in frame.columns:
        stats.limit_up_rows += int(frame["is_limit_up"].sum())
    if "is_limit_down" in frame.columns:
        stats.limit_down_rows += int(frame["is_limit_down"].sum())
