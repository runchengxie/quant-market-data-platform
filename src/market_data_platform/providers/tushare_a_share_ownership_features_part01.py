"""A-share ownership-style feature assets derived from TuShare holdings data."""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_common import (
    normalize_ts_code,
    pandas,
)
from market_data_platform.providers.tushare_flow_utils import (
    date_token,
    numeric,
    read_frame,
)

FUND_PORTFOLIO_FEATURE_KEY_COLUMNS = (
    "trade_date",
    "symbol",
    "available_date",
    "report_period",
    "disclosure_date",
)

FUND_PORTFOLIO_NUMERIC_COLUMNS = (
    "mkv",
    "amount",
    "stk_mkv_ratio",
    "stk_float_ratio",
)

TOP10_HOLDER_NUMERIC_COLUMNS = (
    "hold_amount",
    "hold_ratio",
    "hold_float_ratio",
    "hold_change",
)

TOP_INST_NUMERIC_COLUMNS = (
    "buy",
    "buy_rate",
    "sell",
    "sell_rate",
    "net_buy",
)

STK_HOLDERTRADE_NUMERIC_COLUMNS = (
    "change_vol",
    "change_ratio",
    "after_share",
    "after_ratio",
    "avg_price",
    "total_share",
    "change_amount",
)

DAILY_BASIC_COLUMNS = (
    "trade_date",
    "ts_code",
    "symbol",
    "total_mv",
    "circ_mv",
    "float_share",
)

DAILY_AMOUNT_COLUMNS = (
    "trade_date",
    "ts_code",
    "symbol",
    "amount",
)

INSTITUTION_HOLDER_NAME_PATTERNS = (
    "公司",
    "集团",
    "有限",
    "银行",
    "基金",
    "保险",
    "证券",
    "信托",
    "资管",
    "资产",
    "投资",
    "社保",
    "汇金",
    "证金",
    "国有",
    "财政",
    "中央",
    "合伙",
    "计划",
    "qfii",
    "llc",
    "ltd",
    "limited",
    "inc",
)

PERSON_HOLDER_TYPES = {"个人", "自然人"}


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
    for part in reversed(path.parts):
        if part.startswith("trade_date="):
            return date_token(part.removeprefix("trade_date="))
    return date_token(path.stem)


def _prepare_fund_portfolio_frame(frame: Any) -> Any:
    df = frame.copy()
    if df.empty:
        return df
    if "symbol" not in df.columns:
        raise ValueError("fund_portfolio is missing stock symbol column.")
    if "ts_code" not in df.columns:
        raise ValueError("fund_portfolio is missing fund ts_code column.")
    if "ann_date" not in df.columns or "end_date" not in df.columns:
        raise ValueError("fund_portfolio is missing ann_date or end_date.")

    df["fund_code"] = df["ts_code"].astype(str).str.strip().str.upper()
    df["symbol"] = df["symbol"].map(normalize_ts_code)
    df["report_period"] = df["end_date"].map(date_token)
    df["disclosure_date"] = df["ann_date"].map(date_token)
    for column in FUND_PORTFOLIO_NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = numeric(df, column)
    mask = (
        df["fund_code"].astype(str).str.len().gt(0)
        & df["symbol"].astype(str).str.fullmatch(r"\d{6}\.(SH|SZ|BJ)", na=False)
        & df["report_period"].astype(str).str.fullmatch(r"\d{8}", na=False)
        & df["disclosure_date"].astype(str).str.fullmatch(r"\d{8}", na=False)
    )
    return df.loc[mask].copy()


def _prepare_top10_holder_frame(frame: Any) -> Any:
    df = frame.copy()
    if df.empty:
        return df
    if "ts_code" not in df.columns:
        raise ValueError("top10 holder data is missing ts_code column.")
    if "ann_date" not in df.columns or "end_date" not in df.columns:
        raise ValueError("top10 holder data is missing ann_date or end_date.")
    if "holder_name" not in df.columns:
        raise ValueError("top10 holder data is missing holder_name column.")

    df["symbol"] = df["ts_code"].map(normalize_ts_code)
    df["report_period"] = df["end_date"].map(date_token)
    df["disclosure_date"] = df["ann_date"].map(date_token)
    df["holder_name"] = df["holder_name"].astype(str).str.strip()
    if "holder_type" not in df.columns:
        df["holder_type"] = ""
    df["holder_type"] = df["holder_type"].fillna("").astype(str).str.strip()
    for column in TOP10_HOLDER_NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = numeric(df, column)
    mask = (
        df["symbol"].astype(str).str.fullmatch(r"\d{6}\.(SH|SZ|BJ)", na=False)
        & df["report_period"].astype(str).str.fullmatch(r"\d{8}", na=False)
        & df["disclosure_date"].astype(str).str.fullmatch(r"\d{8}", na=False)
        & df["holder_name"].astype(str).str.len().gt(0)
    )
    return df.loc[mask].copy()


def _prepare_top_inst_frame(frame: Any, *, trade_date: str | None = None) -> Any:
    df = frame.copy()
    if df.empty:
        return df
    if "symbol" not in df.columns and "ts_code" not in df.columns:
        raise ValueError("top_inst data is missing ts_code or symbol column.")
    if "trade_date" not in df.columns and trade_date is None:
        raise ValueError("top_inst data is missing trade_date.")

    if "symbol" not in df.columns:
        df["symbol"] = df["ts_code"]
    df["symbol"] = df["symbol"].map(normalize_ts_code)
    if "trade_date" not in df.columns:
        df["trade_date"] = trade_date
    df["trade_date"] = df["trade_date"].map(date_token)
    if "exalter" not in df.columns:
        df["exalter"] = ""
    df["exalter"] = df["exalter"].fillna("").astype(str).str.strip()
    for column in TOP_INST_NUMERIC_COLUMNS:
        if column not in df.columns:
            df[column] = float("nan")
        df[column] = numeric(df, column)
    computed_net_buy = numeric(df, "buy") - numeric(df, "sell")
    df["net_buy"] = df["net_buy"].where(df["net_buy"].notna(), computed_net_buy)
    mask = df["symbol"].astype(str).str.fullmatch(r"\d{6}\.(SH|SZ|BJ)", na=False) & df[
        "trade_date"
    ].astype(str).str.fullmatch(r"\d{8}", na=False)
    return df.loc[mask].copy()


def _require_stk_holdertrade_columns(df: Any) -> None:
    if "ts_code" not in df.columns:
        raise ValueError("stk_holdertrade data is missing ts_code column.")
    if "ann_date" not in df.columns:
        raise ValueError("stk_holdertrade data is missing ann_date column.")
    if "holder_name" not in df.columns:
        raise ValueError("stk_holdertrade data is missing holder_name column.")


def _prepare_stk_holdertrade_frame(frame: Any) -> Any:
    df = frame.copy()
    if df.empty:
        return df
    _require_stk_holdertrade_columns(df)
    if "in_de" not in df.columns:
        df["in_de"] = ""

    df["symbol"] = df["ts_code"].map(normalize_ts_code)
    df["disclosure_date"] = df["ann_date"].map(date_token)
    df["holder_name"] = df["holder_name"].astype(str).str.strip()
    if "holder_type" not in df.columns:
        df["holder_type"] = ""
    df["holder_type"] = df["holder_type"].fillna("").astype(str).str.strip()
    df["in_de"] = df["in_de"].fillna("").astype(str).str.strip()
    for column in ("begin_date", "close_date"):
        if column in df.columns:
            df[column] = df[column].map(date_token)
    for column in STK_HOLDERTRADE_NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = numeric(df, column)
    mask = (
        df["symbol"].astype(str).str.fullmatch(r"\d{6}\.(SH|SZ|BJ)", na=False)
        & df["disclosure_date"].astype(str).str.fullmatch(r"\d{8}", na=False)
        & df["holder_name"].astype(str).str.len().gt(0)
    )
    return df.loc[mask].copy()


def _trade_date_partition_from_path(path: Path) -> str | None:
    for part in reversed(path.parts):
        if part.startswith("trade_date="):
            return part.removeprefix("trade_date=")
    return None


def _prepare_daily_basic_part(frame: Any, path: Path) -> Any | None:
    if frame.empty:
        return None
    if "symbol" not in frame.columns and "ts_code" in frame.columns:
        frame["symbol"] = frame["ts_code"]
    if "trade_date" not in frame.columns:
        trade_date = _trade_date_partition_from_path(path)
        if trade_date is not None:
            frame["trade_date"] = trade_date
    if "symbol" not in frame.columns or "trade_date" not in frame.columns:
        return None

    frame["symbol"] = frame["symbol"].map(normalize_ts_code)
    frame["trade_date"] = frame["trade_date"].map(date_token)
    keep = [column for column in DAILY_BASIC_COLUMNS if column in frame.columns]
    return frame.loc[:, list(dict.fromkeys(["trade_date", "symbol", *keep]))]


def _load_fund_portfolio(fund_portfolio_dir: str | Path) -> Any:
    pd = pandas()
    frames = [
        _prepare_fund_portfolio_frame(read_frame(path))
        for path in _asset_files(fund_portfolio_dir, label="fund_portfolio")
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    logical_key = ["fund_code", "report_period", "symbol", "disclosure_date"]
    value_columns = [
        column
        for column in ("mkv", "amount", "stk_mkv_ratio", "stk_float_ratio")
        if column in df.columns
    ]
    duplicate_groups = df.groupby(logical_key, dropna=False, sort=False)
    conflicting = duplicate_groups[value_columns].nunique(dropna=False).gt(1).any(axis=1)
    if conflicting.any():
        examples = conflicting[conflicting].index.tolist()[:3]
        raise ValueError(
            "fund_portfolio contains conflicting duplicate logical keys; "
            f"refusing to aggregate examples={examples}"
        )
    df = df.drop_duplicates(subset=[*logical_key, *value_columns], keep="first")
    grouped = (
        df.groupby(["available_date", "fund_code", "report_period", "symbol"], dropna=False)
        if "available_date" in df.columns
        else None
    )
    if grouped is None:
        return df
    return grouped.agg(
        {
            "disclosure_date": "max",
            "mkv": "sum",
            "amount": "sum",
            "stk_mkv_ratio": "sum",
            "stk_float_ratio": "sum",
        }
    ).reset_index()


def _load_top10_holder_asset(asset_dir: str | Path) -> Any:
    pd = pandas()
    frames = [
        _prepare_top10_holder_frame(read_frame(path))
        for path in _asset_files(asset_dir, label="top10_holder")
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _load_top_inst_events(top_inst_dir: str | Path) -> Any:
    pd = pandas()
    frames = []
    for path in _asset_files(top_inst_dir, label="top_inst"):
        frame = _prepare_top_inst_frame(read_frame(path), trade_date=_trade_date_from_path(path))
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["top_inst_event_count"] = 1.0
    df["top_inst_buy_event_count"] = (numeric(df, "buy").fillna(0.0) > 0.0).astype(float)
    df["top_inst_sell_event_count"] = (numeric(df, "sell").fillna(0.0) > 0.0).astype(float)
    df["_top_inst_exalter"] = df["exalter"].where(df["exalter"].astype(str).str.len().gt(0))
    return (
        df.groupby(["trade_date", "symbol"], dropna=False, sort=True)
        .agg(
            top_inst_buy=("buy", "sum"),
            top_inst_sell=("sell", "sum"),
            top_inst_net_buy=("net_buy", "sum"),
            top_inst_buy_rate=("buy_rate", "mean"),
            top_inst_sell_rate=("sell_rate", "mean"),
            top_inst_event_count=("top_inst_event_count", "sum"),
            top_inst_buy_event_count=("top_inst_buy_event_count", "sum"),
            top_inst_sell_event_count=("top_inst_sell_event_count", "sum"),
            top_inst_exalter_count=("_top_inst_exalter", "nunique"),
        )
        .reset_index()
    )


def _load_stk_holdertrade_asset(asset_dir: str | Path) -> Any:
    pd = pandas()
    frames = [
        _prepare_stk_holdertrade_frame(read_frame(path))
        for path in _asset_files(asset_dir, label="stk_holdertrade")
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _trade_dates_from_daily_basic(daily_basic_dir: str | Path | None) -> list[str]:
    if daily_basic_dir is None:
        return []
    dates: set[str] = set()
    for path in _asset_files(daily_basic_dir, label="daily_basic"):
        for part in reversed(path.parts):
            if part.startswith("trade_date="):
                token = date_token(part.removeprefix("trade_date="))
                if token:
                    dates.add(token)
                    break
    if dates:
        return sorted(dates)

    frames = [
        read_frame(path, columns=["trade_date"])
        for path in _asset_files(daily_basic_dir, label="daily_basic")
    ]
    for frame in frames:
        if "trade_date" in frame.columns:
            dates.update(token for token in frame["trade_date"].map(date_token) if token)
    return sorted(dates)


def _availability_date(
    disclosure_date: str,
    *,
    trade_dates: list[str],
    available_delay_days: int,
) -> str:
    disclosure = datetime.strptime(disclosure_date, "%Y%m%d")
    target = (disclosure + timedelta(days=available_delay_days)).strftime("%Y%m%d")
    if not trade_dates:
        return target
    index = bisect_left(trade_dates, target)
    if index >= len(trade_dates):
        return ""
    return trade_dates[index]


def _apply_available_dates(
    frame: Any,
    *,
    trade_dates: list[str],
    available_delay_days: int,
) -> Any:
    df = frame.copy()
    if available_delay_days < 0:
        raise ValueError("available_delay_days must be non-negative.")
    df["available_date"] = [
        _availability_date(
            disclosure_date,
            trade_dates=trade_dates,
            available_delay_days=available_delay_days,
        )
        for disclosure_date in df["disclosure_date"].astype(str)
    ]
    df = df[df["available_date"].astype(str).str.fullmatch(r"\d{8}", na=False)].copy()
    df["trade_date"] = df["available_date"]
    return df


def _aggregate_fund_rows(frame: Any) -> Any:
    pd = pandas()
    if frame.empty:
        return frame
    df = frame.copy()
    df["_disclosure_date_i64"] = (
        pd.to_numeric(
            df["disclosure_date"],
            errors="coerce",
        )
        .fillna(0)
        .astype("int64")
    )
    aggregated = (
        df.groupby(
            ["available_date", "fund_code", "report_period", "symbol"],
            dropna=False,
            sort=False,
        )
        .agg(
            {
                "_disclosure_date_i64": "max",
                "mkv": "sum",
                "amount": "sum",
                "stk_mkv_ratio": "sum",
                "stk_float_ratio": "sum",
            }
        )
        .reset_index()
    )
    aggregated["disclosure_date"] = aggregated["_disclosure_date_i64"].map(
        lambda value: f"{int(value):08d}" if int(value) > 0 else ""
    )
    return aggregated.drop(columns=["_disclosure_date_i64"]).loc[
        :,
        [
            "available_date",
            "fund_code",
            "report_period",
            "symbol",
            "disclosure_date",
            "mkv",
            "amount",
            "stk_mkv_ratio",
            "stk_float_ratio",
        ],
    ]


def _load_daily_basic(daily_basic_dir: str | Path | None) -> Any:
    pd = pandas()
    if daily_basic_dir is None:
        return pd.DataFrame(columns=["trade_date", "symbol"])
    frames = []
    for path in _asset_files(daily_basic_dir, label="daily_basic"):
        frame = _prepare_daily_basic_part(read_frame(path, columns=list(DAILY_BASIC_COLUMNS)), path)
        if frame is not None:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["trade_date", "symbol"])
    output = pd.concat(frames, ignore_index=True)
    numeric_cols = [column for column in ("total_mv", "circ_mv", "float_share") if column in output]
    for column in numeric_cols:
        output[column] = numeric(output, column)
    return output.drop_duplicates(subset=["trade_date", "symbol"], keep="last")
