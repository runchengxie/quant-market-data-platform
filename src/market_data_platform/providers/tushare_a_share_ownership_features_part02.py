"""A-share ownership-style feature assets derived from TuShare holdings data."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_ownership_features_part01 import (
    DAILY_AMOUNT_COLUMNS,
    INSTITUTION_HOLDER_NAME_PATTERNS,
    PERSON_HOLDER_TYPES,
    TOP10_HOLDER_NUMERIC_COLUMNS,
    _apply_available_dates,
    _asset_files,
    _trade_date_from_path,
)
from market_data_platform.providers.tushare_common import (
    normalize_ts_code,
    pandas,
)
from market_data_platform.providers.tushare_flow_utils import (
    date_token,
    numeric,
    read_frame,
)


def _load_daily_amount(daily_dir: str | Path | None) -> Any:
    pd = pandas()
    if daily_dir is None:
        return pd.DataFrame(columns=["trade_date", "symbol", "daily_amount"])
    frames = []
    for path in _asset_files(daily_dir, label="daily"):
        frame = read_frame(path, columns=list(DAILY_AMOUNT_COLUMNS))
        if frame.empty:
            continue
        if "symbol" not in frame.columns and "ts_code" in frame.columns:
            frame["symbol"] = frame["ts_code"]
        if "trade_date" not in frame.columns:
            trade_date = _trade_date_from_path(path)
            if trade_date:
                frame["trade_date"] = trade_date
        if "symbol" not in frame.columns or "trade_date" not in frame.columns:
            continue
        frame["symbol"] = frame["symbol"].map(normalize_ts_code)
        frame["trade_date"] = frame["trade_date"].map(date_token)
        frame["daily_amount"] = numeric(frame, "amount") / 10.0
        frames.append(frame.loc[:, ["trade_date", "symbol", "daily_amount"]])
    if not frames:
        return pd.DataFrame(columns=["trade_date", "symbol", "daily_amount"])
    output = pd.concat(frames, ignore_index=True)
    return output.drop_duplicates(subset=["trade_date", "symbol"], keep="last")


def _holding_from_row(row: Any) -> dict[str, Any]:
    return {
        "report_period": str(row["report_period"]),
        "disclosure_date": str(row["disclosure_date"]),
        "mkv": float(row.get("mkv", 0.0) or 0.0),
        "amount": float(row.get("amount", 0.0) or 0.0),
        "stk_mkv_ratio": float(row.get("stk_mkv_ratio", 0.0) or 0.0),
        "stk_float_ratio": float(row.get("stk_float_ratio", 0.0) or 0.0),
    }


def _aggregate_symbol_holdings(
    *,
    trade_date: str,
    symbol: str,
    holdings: Mapping[str, Mapping[str, Any]],
    fallback_report_period: str,
    fallback_disclosure_date: str,
) -> dict[str, Any]:
    values = list(holdings.values())
    fund_count = len(values)
    report_period = max((str(item.get("report_period", "")) for item in values), default="")
    disclosure_date = max((str(item.get("disclosure_date", "")) for item in values), default="")
    return {
        "trade_date": trade_date,
        "symbol": symbol,
        "available_date": trade_date,
        "report_period": report_period or fallback_report_period,
        "disclosure_date": disclosure_date or fallback_disclosure_date,
        "fund_count_holding_stock": float(fund_count),
        "fund_hold_mv": sum(float(item.get("mkv", 0.0) or 0.0) for item in values),
        "fund_hold_amount": sum(float(item.get("amount", 0.0) or 0.0) for item in values),
        "fund_stk_mkv_ratio_sum": sum(
            float(item.get("stk_mkv_ratio", 0.0) or 0.0) for item in values
        ),
        "fund_stk_float_ratio_sum": sum(
            float(item.get("stk_float_ratio", 0.0) or 0.0) for item in values
        ),
    }


def _build_pit_state_events(frame: Any) -> Any:
    pd = pandas()
    if frame.empty:
        return pd.DataFrame()
    fund_symbols: dict[str, set[str]] = {}
    stock_holdings: dict[str, dict[str, dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []

    ordered = frame.sort_values(
        ["available_date", "fund_code", "report_period", "symbol"]
    ).reset_index(drop=True)
    for available_date, date_frame in ordered.groupby("available_date", sort=True):
        affected_symbols: set[str] = set()
        fallback_report_period = str(date_frame["report_period"].max())
        fallback_disclosure_date = str(date_frame["disclosure_date"].max())
        for (fund_code, _report_period), fund_frame in date_frame.groupby(
            ["fund_code", "report_period"],
            sort=True,
        ):
            fund = str(fund_code)
            previous_symbols = fund_symbols.get(fund, set())
            affected_symbols.update(previous_symbols)
            for symbol in previous_symbols:
                holdings = stock_holdings.get(symbol)
                if holdings is not None:
                    holdings.pop(fund, None)
                    if not holdings:
                        stock_holdings.pop(symbol, None)

            next_symbols = set(fund_frame["symbol"].astype(str))
            fund_symbols[fund] = next_symbols
            affected_symbols.update(next_symbols)
            for row in fund_frame.itertuples(index=False):
                symbol = str(row.symbol)
                stock_holdings.setdefault(symbol, {})[fund] = _holding_from_row(row._asdict())

        for symbol in sorted(affected_symbols):
            rows.append(
                _aggregate_symbol_holdings(
                    trade_date=str(available_date),
                    symbol=symbol,
                    holdings=stock_holdings.get(symbol, {}),
                    fallback_report_period=fallback_report_period,
                    fallback_disclosure_date=fallback_disclosure_date,
                )
            )
    return pd.DataFrame(rows)


def _with_daily_basic_ratios(events: Any, daily_basic: Any) -> Any:
    df = events.copy()
    if df.empty:
        return df
    if not daily_basic.empty:
        overlay_cols = [
            column
            for column in ("trade_date", "symbol", "total_mv", "circ_mv", "float_share")
            if column in daily_basic.columns
        ]
        df = df.merge(
            daily_basic.loc[:, overlay_cols],
            on=["trade_date", "symbol"],
            how="left",
        )
    pd = pandas()
    total_mv_yuan = numeric(df, "total_mv") * 10000.0
    circ_mv_yuan = numeric(df, "circ_mv") * 10000.0
    float_share = numeric(df, "float_share") * 10000.0
    hold_mv = numeric(df, "fund_hold_mv")
    hold_amount = numeric(df, "fund_hold_amount")
    df["fund_hold_mv_to_total_mv"] = hold_mv / total_mv_yuan.where(total_mv_yuan > 0)
    df["fund_hold_mv_to_float_mv"] = hold_mv / circ_mv_yuan.where(circ_mv_yuan > 0)
    df["fund_hold_amount_to_float_share"] = hold_amount / float_share.where(float_share > 0)
    for column in (
        "fund_hold_mv_to_total_mv",
        "fund_hold_mv_to_float_mv",
        "fund_hold_amount_to_float_share",
    ):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _with_change_features(frame: Any) -> Any:
    df = frame.sort_values(["symbol", "trade_date"]).copy()
    change_columns = (
        "fund_hold_mv_to_total_mv",
        "fund_hold_mv_to_float_mv",
        "fund_hold_amount_to_float_share",
        "fund_count_holding_stock",
    )
    for column in change_columns:
        if column in df.columns:
            df[f"{column}_qoq_change"] = df.groupby("symbol")[column].diff()
    return df.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def _is_institution_holder(holder_name: object, holder_type: object) -> bool:
    holder_type_text = str(holder_type or "").strip()
    if holder_type_text in PERSON_HOLDER_TYPES:
        return False
    if holder_type_text:
        return True
    name = str(holder_name or "").strip().lower()
    if not name:
        return False
    if len(name) <= 4 and all("\u4e00" <= char <= "\u9fff" for char in name):
        return False
    return any(pattern in name for pattern in INSTITUTION_HOLDER_NAME_PATTERNS)


def _prepare_holder_events(
    frame: Any,
    *,
    trade_dates: list[str],
    available_delay_days: int,
    prefix: str,
) -> Any:
    pd = pandas()
    if frame.empty:
        return pd.DataFrame()
    df = _apply_available_dates(
        frame,
        trade_dates=trade_dates,
        available_delay_days=available_delay_days,
    )
    if df.empty:
        return pd.DataFrame()
    df["is_institution_holder"] = [
        _is_institution_holder(holder_name, holder_type)
        for holder_name, holder_type in zip(
            df["holder_name"],
            df["holder_type"],
            strict=False,
        )
    ]
    for column in TOP10_HOLDER_NUMERIC_COLUMNS:
        if column not in df.columns:
            df[column] = float("nan")
    rows: list[dict[str, Any]] = []
    group_cols = ["available_date", "symbol", "report_period", "disclosure_date"]
    concentration_column = (
        f"{prefix}_holder_concentration" if prefix == "top10_float" else f"{prefix}_concentration"
    )
    for keys, group in df.groupby(group_cols, dropna=False, sort=True):
        available_date, symbol, report_period, disclosure_date = (str(value) for value in keys)
        inst = group[group["is_institution_holder"]]
        hold_ratio = numeric(group, "hold_ratio")
        hold_float_ratio = numeric(group, "hold_float_ratio")
        inst_hold_ratio = (
            numeric(inst, "hold_ratio") if not inst.empty else pd.Series(dtype="float64")
        )
        inst_float_ratio = (
            numeric(inst, "hold_float_ratio") if not inst.empty else pd.Series(dtype="float64")
        )
        available_dt = datetime.strptime(available_date, "%Y%m%d")
        report_dt = datetime.strptime(report_period, "%Y%m%d")
        rows.append(
            {
                "trade_date": available_date,
                "symbol": symbol,
                "available_date": available_date,
                f"{prefix}_report_period": report_period,
                f"{prefix}_disclosure_date": disclosure_date,
                f"{prefix}_holder_count": float(group["holder_name"].nunique()),
                f"{prefix}_inst_holder_count": float(inst["holder_name"].nunique()),
                concentration_column: float(hold_ratio.sum(skipna=True)),
                f"{prefix}_float_concentration": float(hold_float_ratio.sum(skipna=True)),
                f"{prefix}_inst_hold_ratio": float(inst_hold_ratio.sum(skipna=True)),
                f"{prefix}_inst_float_hold_ratio": float(inst_float_ratio.sum(skipna=True)),
                f"{prefix}_hold_amount": float(numeric(group, "hold_amount").sum(skipna=True)),
                f"{prefix}_hold_change": float(numeric(group, "hold_change").sum(skipna=True)),
                f"{prefix}_days_since_holder_report": float((available_dt - report_dt).days),
            }
        )
    return _dedupe_holder_events(pd.DataFrame(rows), prefix=prefix)


def _dedupe_holder_events(frame: Any, *, prefix: str) -> Any:
    if frame.empty:
        return frame
    report_col = f"{prefix}_report_period"
    disclosure_col = f"{prefix}_disclosure_date"
    sort_columns = [
        column
        for column in ("available_date", "symbol", report_col, disclosure_col)
        if column in frame.columns
    ]
    return (
        frame.sort_values(sort_columns)
        .drop_duplicates(subset=["available_date", "symbol"], keep="last")
        .sort_values(["trade_date", "symbol"])
        .reset_index(drop=True)
    )


def _merge_holder_event_frames(frames: list[Any]) -> Any:
    pd = pandas()
    non_empty = [frame for frame in frames if frame is not None and not frame.empty]
    if not non_empty:
        return pd.DataFrame()
    merged = non_empty[0]
    for frame in non_empty[1:]:
        merged = merged.merge(frame, on=["trade_date", "symbol", "available_date"], how="outer")
    return merged.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def _with_holder_change_features(frame: Any) -> Any:
    df = frame.sort_values(["symbol", "trade_date"]).copy()
    change_columns = [
        column
        for column in (
            "top10_inst_hold_ratio",
            "top10_concentration",
            "top10_float_concentration",
            "top10_float_inst_hold_ratio",
            "top10_float_holder_concentration",
            "top10_float_inst_float_hold_ratio",
            "top10_float_float_concentration",
        )
        if column in df.columns
    ]
    for column in change_columns:
        df[f"{column}_qoq_change"] = df.groupby("symbol")[column].diff()
    return df.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def _days_since_flags(flags: Any) -> list[float]:
    days: int | None = None
    values: list[float] = []
    for flag in flags:
        if bool(flag):
            days = 0
        elif days is not None:
            days += 1
        values.append(float(days) if days is not None else float("nan"))
    return values


def _with_top_inst_event_features(events: Any, daily_amount: Any, *, window: int) -> Any:
    pd = pandas()
    if events.empty:
        return pd.DataFrame()
    if window <= 0:
        raise ValueError("window must be positive.")

    if not daily_amount.empty:
        event_symbols = set(events["symbol"].dropna().astype(str))
        start_date = str(events["trade_date"].min())
        end_date = str(events["trade_date"].max())
        panel = daily_amount[
            daily_amount["symbol"].astype(str).isin(event_symbols)
            & daily_amount["trade_date"].astype(str).between(start_date, end_date)
        ].copy()
        panel = panel.loc[:, ["trade_date", "symbol", "daily_amount"]]
    else:
        panel = events.loc[:, ["trade_date", "symbol"]].drop_duplicates().copy()
        panel["daily_amount"] = float("nan")

    df = panel.merge(events, on=["trade_date", "symbol"], how="outer")
    if "daily_amount" not in df.columns:
        df["daily_amount"] = float("nan")
    zero_columns = (
        "top_inst_buy",
        "top_inst_sell",
        "top_inst_net_buy",
        "top_inst_event_count",
        "top_inst_buy_event_count",
        "top_inst_sell_event_count",
        "top_inst_exalter_count",
    )
    for column in zero_columns:
        if column not in df.columns:
            df[column] = 0.0
        df[column] = numeric(df, column).fillna(0.0)
    for column in ("top_inst_buy_rate", "top_inst_sell_rate"):
        if column not in df.columns:
            df[column] = float("nan")
        df[column] = numeric(df, column)

    daily_amount_numeric = numeric(df, "daily_amount")
    df["top_inst_net_buy_to_amount"] = numeric(df, "top_inst_net_buy") / (
        daily_amount_numeric.where(daily_amount_numeric > 0.0)
    )
    pieces = []
    for _symbol, group in df.sort_values(["symbol", "trade_date"]).groupby(
        "symbol",
        sort=True,
    ):
        group = group.copy()
        net_buy_roll = group["top_inst_net_buy"].rolling(window, min_periods=1).sum()
        amount_roll = group["daily_amount"].rolling(window, min_periods=1).sum()
        event_count_roll = group["top_inst_event_count"].rolling(window, min_periods=1).sum()
        group[f"top_inst_net_buy_{window}d"] = net_buy_roll
        group[f"top_inst_net_buy_{window}d_to_amount"] = net_buy_roll / (
            amount_roll.where(amount_roll > 0.0)
        )
        group[f"top_inst_buy_count_{window}d"] = (
            group["top_inst_buy_event_count"].rolling(window, min_periods=1).sum()
        )
        group[f"top_inst_sell_count_{window}d"] = (
            group["top_inst_sell_event_count"].rolling(window, min_periods=1).sum()
        )
        group[f"top_inst_event_count_{window}d"] = event_count_roll
        group[f"top_inst_is_event_{window}d"] = (event_count_roll > 0.0).astype(float)
        group["top_inst_days_since_event"] = _days_since_flags(group["top_inst_event_count"] > 0.0)
        group["top_inst_days_since_buy"] = _days_since_flags(
            group["top_inst_buy_event_count"] > 0.0
        )
        pieces.append(group)

    output = pd.concat(pieces, ignore_index=True)
    output["available_date"] = output["trade_date"]
    feature_columns = [column for column in output.columns if column.startswith("top_inst_")]
    return (
        output.loc[:, ["trade_date", "symbol", "available_date", *feature_columns]]
        .sort_values(["trade_date", "symbol"])
        .drop_duplicates(subset=["trade_date", "symbol"], keep="last")
        .reset_index(drop=True)
    )


def _holdertrade_direction(in_de: object, change_vol: object) -> float:
    text = str(in_de or "").strip().lower()
    if any(token in text for token in ("减", "卖", "sell", "decrease")):
        return -1.0
    if any(token in text for token in ("增", "买", "buy", "increase")):
        return 1.0
    try:
        value = float(str(change_vol))
    except (TypeError, ValueError):
        return 1.0
    if value < 0.0:
        return -1.0
    return 1.0


def _with_signed_holdertrade_values(frame: Any) -> Any:
    pd = pandas()
    df = frame.copy()
    change_vol = numeric(df, "change_vol")
    avg_price = numeric(df, "avg_price")
    change_amount = numeric(df, "change_amount").abs()
    estimated_amount = change_vol.abs() * avg_price / 10000.0
    signed_direction = pd.Series(
        [
            _holdertrade_direction(in_de, volume)
            for in_de, volume in zip(df["in_de"], change_vol, strict=False)
        ],
        index=df.index,
        dtype="float64",
    )
    amount = change_amount.where(change_amount.notna(), estimated_amount)
    df["holdertrade_direction"] = signed_direction
    df["holdertrade_signed_vol"] = change_vol.abs() * signed_direction
    df["holdertrade_signed_amount"] = amount * signed_direction
    return df
