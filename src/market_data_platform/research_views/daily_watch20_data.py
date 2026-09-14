"""Resolve and read the published A-share assets used by DailyWatch20."""

from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
import pandas as pd

MinuteDataset = Literal["legacy", "tushare"]


@dataclass(frozen=True)
class DailyWatch20Assets:
    data_root: Path
    current_contract: Path
    daily_clean: Path
    instruments: Path
    trade_cal: Path
    minute_current: Path
    minute_coverage: Path | None
    daily_as_of: str
    minute_date_min: str | None
    minute_date_max: str | None
    minute_dataset: MinuteDataset = "legacy"
    minute_provider: str = "guan"


def _duckdb():
    try:
        return importlib.import_module("duckdb")
    except ImportError as exc:
        raise RuntimeError(
            "DailyWatch20 requires DuckDB. Install market-data-platform with the 'duckdb' extra."
        ) from exc


def _sql_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


# Public owner API: cross-repo callers must import the non-underscore names.
sql_literal = _sql_literal
duckdb = _duckdb


def _asset_path(payload: dict[str, Any], key: str) -> Path:
    entry = payload.get("assets", {}).get(key)
    if not isinstance(entry, dict):
        raise ValueError(f"A-share current contract is missing asset: {key}")
    value = entry.get("resolved_path") or entry.get("alias_path")
    if not value:
        raise ValueError(f"A-share current contract has no path for asset: {key}")
    path = Path(str(value)).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"A-share current asset does not exist: {path}")
    return path


def _asset_as_of(payload: dict[str, Any], key: str) -> str:
    entry = payload.get("assets", {}).get(key)
    value = entry.get("as_of") if isinstance(entry, dict) else None
    text = str(value or "").replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"A-share current contract has no valid as_of for asset: {key}")
    return text


def _minute_date_range(coverage: Path | None) -> tuple[str | None, str | None]:
    if coverage is None:
        return None, None
    payload = json.loads(coverage.read_text(encoding="utf-8"))
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError(f"Minute coverage has no summary: {coverage}")
    date_min = str(summary.get("date_min") or "").replace("-", "") or None
    date_max = str(summary.get("date_max") or "").replace("-", "") or None
    for label, value in (("date_min", date_min), ("date_max", date_max)):
        if value is not None and (len(value) != 8 or not value.isdigit()):
            raise ValueError(f"Minute coverage has invalid {label}: {value!r}")
    return date_min, date_max


def _minute_asset(
    root: Path,
    minute_dataset: MinuteDataset,
) -> tuple[Path, Path | None, MinuteDataset, str]:
    base = root / "assets" / "derived" / "a_share"
    if minute_dataset == "legacy":
        alias = base / "minute_1m"
        if not alias.is_dir():
            raise FileNotFoundError(f"A-share minute current not found: {alias}")
        resolved = alias.resolve()
        coverage = root / "metadata" / "minute_fusion" / f"a_share_{resolved.name}.coverage.json"
        return resolved, coverage if coverage.is_file() else None, "legacy", "guan"
    if minute_dataset != "tushare":
        raise ValueError(f"unsupported DailyWatch20 minute dataset: {minute_dataset}")
    alias = base / "minute_1m_tushare"
    if not alias.is_dir():
        raise FileNotFoundError(f"A-share TuShare operational minute alias not found: {alias}")
    resolved = alias.resolve()
    receipt = resolved / "_operational_receipt.json"
    if not receipt.is_file():
        raise FileNotFoundError(f"TuShare operational minute receipt not found: {receipt}")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    declared_output = Path(str(payload.get("output_dir") or "")).expanduser().resolve()
    valid = (
        isinstance(payload, dict)
        and payload.get("schema_version") == "a_share.minute_tushare_operational_version.v1"
        and payload.get("status") == "published_operational_version"
        and payload.get("provider") == "tushare"
        and declared_output.is_dir()
        and payload.get("current_alias_mutated", False) is False
        and payload.get("legacy_canonical_mutated", False) is False
    )
    if not valid:
        raise ValueError(f"Invalid TuShare operational minute receipt: {receipt}")
    # Some published aliases are materialized directories rather than symlinks;
    # consume the version directory named by the receipt in that case.
    return declared_output, receipt, "tushare", "tushare"


def resolve_daily_watch20_assets(
    data_root: str | Path | None = None,
    *,
    minute_dataset: MinuteDataset = "tushare",
) -> DailyWatch20Assets:
    root = (
        Path(
            data_root
            or os.environ.get("DATA_PLATFORM_ROOT")
            or Path.home() / "data" / "market-data-platform"
        )
        .expanduser()
        .resolve()
    )
    contract = root / "metadata" / "current_assets" / "a_share_current.json"
    if not contract.is_file():
        raise FileNotFoundError(f"A-share current contract not found: {contract}")
    payload = json.loads(contract.read_text(encoding="utf-8"))
    minute_current, resolved_coverage, resolved_dataset, provider = _minute_asset(
        root, minute_dataset
    )
    minute_date_min, minute_date_max = _minute_date_range(resolved_coverage)
    return DailyWatch20Assets(
        data_root=root,
        current_contract=contract,
        daily_clean=_asset_path(payload, "daily_clean"),
        instruments=_asset_path(payload, "instruments"),
        trade_cal=_asset_path(payload, "trade_cal"),
        minute_current=minute_current,
        minute_coverage=resolved_coverage,
        daily_as_of=_asset_as_of(payload, "daily_clean"),
        minute_date_min=minute_date_min,
        minute_date_max=minute_date_max,
        minute_dataset=resolved_dataset,
        minute_provider=provider,
    )


def _validate_daily_watch20_daily_result(
    frame: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    required = {"trade_date", "symbol", "pb", "pe_ttm", "ps_ttm"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"DailyWatch20 daily result is missing columns: {missing}")
    if frame.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("DailyWatch20 daily result contains duplicate stock-date rows")
    dates = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    start = pd.Timestamp(start_date).normalize()  # ty: ignore[unresolved-attribute]
    end = pd.Timestamp(end_date).normalize()  # ty: ignore[unresolved-attribute]
    if dates.lt(start).any() or dates.gt(end).any():
        raise ValueError("DailyWatch20 daily result escaped the requested date range")
    return frame


def load_daily_watch20_daily(
    assets: DailyWatch20Assets,
    *,
    start_date: str,
    end_date: str,
    memory_limit: str = "12GB",
    threads: int = 3,
) -> pd.DataFrame:
    data_glob = assets.daily_clean / "data" / "*.parquet"
    if not list(data_glob.parent.glob("*.parquet")):
        raise FileNotFoundError(f"Daily clean parquet files not found: {data_glob}")
    columns = (
        "trade_date, symbol, open, adj_open, up_limit, down_limit, "
        "tr_close, high, low, close, amount, "
        "turnover_rate, volume_ratio, total_mv, pb, pe_ttm, ps_ttm, listed_days, board, "
        "is_st, is_suspended, is_limit_up, is_limit_down"
    )
    query = f"""
        SELECT {columns}
        FROM read_parquet({_sql_literal(data_glob)}, union_by_name = true)
        WHERE trade_date BETWEEN {_sql_literal(start_date)} AND {_sql_literal(end_date)}
          AND (symbol LIKE '%.SH' OR symbol LIKE '%.SZ')
        ORDER BY symbol, trade_date
    """
    duckdb = _duckdb()
    conn = duckdb.connect()
    try:
        conn.execute(f"SET threads = {max(1, int(threads))}")
        conn.execute(f"SET memory_limit = {_sql_literal(memory_limit)}")
        frame = conn.execute(query).fetch_df()
        return _validate_daily_watch20_daily_result(
            frame,
            start_date=start_date,
            end_date=end_date,
        )
    finally:
        conn.close()


def load_daily_watch20_instruments(assets: DailyWatch20Assets) -> pd.DataFrame:
    columns = ["symbol", "name", "industry", "list_status", "market", "exchange"]
    frame = pd.read_parquet(assets.instruments, columns=columns)
    frame["symbol"] = frame["symbol"].astype(str)
    return frame.drop_duplicates("symbol", keep="last")


def load_open_trade_dates(assets: DailyWatch20Assets) -> pd.DatetimeIndex:
    calendar = pd.read_parquet(assets.trade_cal)
    date_col = "cal_date" if "cal_date" in calendar.columns else "trade_date"
    if "is_open" in calendar.columns:
        is_open = cast(pd.Series, pd.to_numeric(calendar["is_open"], errors="coerce"))
        calendar = calendar.loc[np.asarray(is_open, dtype=float) == 1]
    raw_dates = cast(pd.Series, calendar[date_col])
    parsed_dates = pd.to_datetime(raw_dates, errors="coerce")
    dates = parsed_dates.dropna().dt.normalize().unique()
    return pd.DatetimeIndex(dates).sort_values()


def next_open_trade_date(open_dates: pd.DatetimeIndex, source_date: object) -> pd.Timestamp:
    source = cast(pd.Timestamp, pd.Timestamp(str(source_date))).normalize()
    later = open_dates[open_dates > source]
    if later.empty:
        raise ValueError(f"Trade calendar has no open date after {source.date()}")
    return cast(pd.Timestamp, later[0]).normalize()


__all__ = [
    "DailyWatch20Assets",
    "MinuteDataset",
    "_duckdb",
    "_sql_literal",
    "load_daily_watch20_daily",
    "load_daily_watch20_instruments",
    "load_open_trade_dates",
    "next_open_trade_date",
    "resolve_daily_watch20_assets",
]
