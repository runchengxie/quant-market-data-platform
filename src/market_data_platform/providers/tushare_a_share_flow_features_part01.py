"""A-share flow and ownership feature assets derived from TuShare raw data."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from market_data_platform.providers.tushare_common import (
    normalize_ts_code,
    pandas,
)
from market_data_platform.providers.tushare_flow_utils import (
    date_token as _flow_date_token,
)
from market_data_platform.providers.tushare_flow_utils import (
    extract_trade_date as _flow_extract_trade_date,
)
from market_data_platform.providers.tushare_flow_utils import (
    numeric as _flow_numeric,
)
from market_data_platform.providers.tushare_flow_utils import (
    partition_payload as _flow_partition_payload,
)
from market_data_platform.providers.tushare_flow_utils import (
    prepare_index_frame as _flow_prepare_index_frame,
)
from market_data_platform.providers.tushare_flow_utils import (
    read_frame as _flow_read_frame,
)
from market_data_platform.providers.tushare_flow_utils import (
    trade_date_part_map as _flow_trade_date_part_map,
)

FLOW_FEATURE_KEY_COLUMNS = ("trade_date", "symbol", "available_date")

DEFAULT_FLOW_FEATURE_WINDOWS = (5, 20, 60)

MONEYFLOW_AMOUNT_COLUMNS = (
    "buy_sm_amount",
    "sell_sm_amount",
    "buy_md_amount",
    "sell_md_amount",
    "buy_lg_amount",
    "sell_lg_amount",
    "buy_elg_amount",
    "sell_elg_amount",
)

MONEYFLOW_BUY_AMOUNT_COLUMNS = (
    "buy_sm_amount",
    "buy_md_amount",
    "buy_lg_amount",
    "buy_elg_amount",
)

MONEYFLOW_SELL_AMOUNT_COLUMNS = (
    "sell_sm_amount",
    "sell_md_amount",
    "sell_lg_amount",
    "sell_elg_amount",
)

MONEYFLOW_NET_AMOUNT_COLUMNS = (
    "net_mf_amount",
    "net_amount",
    "main_net_amount",
    "main_net_inflow",
    "main_net_inflow_amount",
)


def date_token(value: object) -> str:
    return _flow_date_token(value)


def read_frame(path: str | Path, *, columns: list[str] | None = None):
    return _flow_read_frame(path, columns=columns)


def partition_payload(frame: Any, *, partition_column: str = "trade_date") -> Any:
    return _flow_partition_payload(frame, partition_column=partition_column)


def extract_trade_date(path: Path) -> str:
    return _flow_extract_trade_date(path)


def trade_date_part_map(asset_dir: str | Path | None, *, label: str) -> dict[str, Path]:
    return _flow_trade_date_part_map(asset_dir, label=label)


def prepare_index_frame(frame: Any, *, label: str, trade_date: str | None = None) -> Any:
    return _flow_prepare_index_frame(frame, label=label, trade_date=trade_date)


def numeric(frame: Any, column: str) -> Any:
    return _flow_numeric(frame, column)


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


def _prepare_industry_frame(frame: Any) -> Any:
    df = frame.copy()
    if df.empty:
        return df
    if "symbol" not in df.columns and "ts_code" in df.columns:
        df["symbol"] = df["ts_code"]
    if "symbol" not in df.columns:
        raise ValueError("industry asset is missing symbol or ts_code.")
    if "effective_date" not in df.columns:
        raise ValueError("industry asset is missing effective_date.")
    if "end_date" not in df.columns:
        df["end_date"] = ""
    if "industry_code" not in df.columns:
        df["industry_code"] = ""
    if "industry_name" not in df.columns:
        df["industry_name"] = ""
    df["symbol"] = df["symbol"].map(normalize_ts_code)
    df["effective_date"] = df["effective_date"].map(_flow_date_token)
    df["end_date"] = df["end_date"].map(_flow_date_token)
    df["industry_code"] = df["industry_code"].fillna("").astype(str).str.strip()
    df["industry_name"] = df["industry_name"].fillna("").astype(str).str.strip()
    mask = (
        df["symbol"].astype(str).str.fullmatch(r"\d{6}\.(SH|SZ|BJ)", na=False)
        & df["effective_date"].astype(str).str.fullmatch(r"\d{8}", na=False)
        & (
            df["industry_code"].astype(str).str.len().gt(0)
            | df["industry_name"].astype(str).str.len().gt(0)
        )
    )
    return df.loc[mask].copy()


def _load_industry_changes(industry_dir: str | Path | None) -> Any:
    pd = pandas()
    if industry_dir is None:
        return pd.DataFrame()
    frames = [
        _prepare_industry_frame(_flow_read_frame(path))
        for path in _asset_files(industry_dir, label="industry")
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["symbol", "effective_date"])


def _merge_industry_labels(frame: Any, industry: Any, *, trade_date: str) -> Any:
    if industry.empty:
        return frame
    active = industry[
        (industry["effective_date"].astype(str) <= trade_date)
        & (
            industry["end_date"].astype(str).eq("")
            | (industry["end_date"].astype(str) >= trade_date)
        )
    ].copy()
    if active.empty:
        return frame
    active["_industry_label"] = active["industry_code"].where(
        active["industry_code"].astype(str).str.len().gt(0),
        active["industry_name"],
    )
    active = (
        active.sort_values(["symbol", "effective_date"])
        .drop_duplicates(subset=["symbol"], keep="last")
        .loc[:, ["symbol", "_industry_label"]]
    )
    return frame.merge(active, on="symbol", how="left")


def _read_trade_date_part(
    parts: Mapping[str, Path],
    trade_date: str,
    *,
    label: str,
) -> Any:
    pd = pandas()
    path = parts.get(trade_date)
    if path is None:
        return pd.DataFrame()
    return _flow_prepare_index_frame(
        _flow_read_frame(path),
        label=label,
        trade_date=trade_date,
    )


def _overlay_frame(
    parts: Mapping[str, Path],
    trade_date: str,
    *,
    label: str,
    columns: Sequence[str],
) -> Any:
    frame = _read_trade_date_part(parts, trade_date, label=label)
    if frame.empty:
        return frame
    selected = ["symbol", "trade_date", *(column for column in columns if column in frame.columns)]
    return frame.loc[:, list(dict.fromkeys(selected))].drop_duplicates(
        subset=["symbol", "trade_date"],
        keep="last",
    )


def _net_from_pairs(frame: Any, buys: Sequence[str], sells: Sequence[str]) -> Any:
    pd = pandas()
    present = [column for column in (*buys, *sells) if column in frame.columns]
    if not present:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    result = pd.Series(0.0, index=frame.index, dtype="float64")
    for column in buys:
        if column in frame.columns:
            result = result + _flow_numeric(frame, column).fillna(0.0)
    for column in sells:
        if column in frame.columns:
            result = result - _flow_numeric(frame, column).fillna(0.0)
    return result


def _gross_amount(frame: Any) -> Any:
    pd = pandas()
    present = [column for column in MONEYFLOW_AMOUNT_COLUMNS if column in frame.columns]
    if not present:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    result = pd.Series(0.0, index=frame.index, dtype="float64")
    for column in present:
        result = result + _flow_numeric(frame, column).fillna(0.0)
    return result


def _with_moneyflow_metrics(frame: Any) -> Any:
    df = frame.copy()
    net_column = next(
        (column for column in MONEYFLOW_NET_AMOUNT_COLUMNS if column in df.columns),
        "",
    )
    if net_column:
        df["_mf_net_amount"] = _flow_numeric(df, net_column)
    else:
        df["_mf_net_amount"] = _net_from_pairs(
            df,
            MONEYFLOW_BUY_AMOUNT_COLUMNS,
            MONEYFLOW_SELL_AMOUNT_COLUMNS,
        )
    df["_mf_elg_net_amount"] = _net_from_pairs(
        df,
        ("buy_elg_amount",),
        ("sell_elg_amount",),
    )
    df["_mf_lg_net_amount"] = _net_from_pairs(
        df,
        ("buy_lg_amount",),
        ("sell_lg_amount",),
    )
    df["_mf_gross_flow_amount"] = _gross_amount(df)
    if "amount" in df.columns:
        df["_amount_for_ratio"] = _flow_numeric(df, "amount")
    elif "_daily_amount" in df.columns:
        # TuShare daily.amount is thousand CNY; moneyflow amount fields are ten-thousand CNY.
        df["_amount_for_ratio"] = _flow_numeric(df, "_daily_amount") / 10.0
    else:
        df["_amount_for_ratio"] = float("nan")
    if "circ_mv" in df.columns:
        df["_float_mv_for_ratio"] = _flow_numeric(df, "circ_mv")
    elif "_daily_basic_circ_mv" in df.columns:
        df["_float_mv_for_ratio"] = _flow_numeric(df, "_daily_basic_circ_mv")
    else:
        df["_float_mv_for_ratio"] = float("nan")
    return df


def _normalize_windows(windows: Iterable[int] | None) -> tuple[int, ...]:
    values = tuple(sorted({int(window) for window in (windows or DEFAULT_FLOW_FEATURE_WINDOWS)}))
    if not values or any(window <= 0 for window in values):
        raise ValueError("windows must contain positive integers.")
    return values


def _feature_columns(
    windows: Sequence[int],
    *,
    include_industry_features: bool = False,
) -> list[str]:
    columns: list[str] = []
    for window in windows:
        columns.extend(
            [
                f"mf_net_amount_{window}d_to_amount",
                f"mf_elg_net_amount_{window}d_to_amount",
                f"mf_lg_net_amount_{window}d_to_amount",
            ]
        )
    if 20 in windows:
        columns.extend(
            [
                "mf_net_amount_20d_to_float_mv",
                "mf_buy_sell_imbalance_20d",
                "mf_net_amount_20d_cs_rank",
                "mf_net_amount_20d_cs_zscore",
            ]
        )
        if include_industry_features:
            columns.append("mf_net_amount_20d_industry_zscore")
    return columns


def _with_cross_sectional_moneyflow_features(frame: Any) -> Any:
    pd = pandas()
    df = frame.copy()
    base_column = "mf_net_amount_20d_to_amount"
    if base_column not in df.columns:
        return df
    values = pd.to_numeric(df[base_column], errors="coerce")
    df["mf_net_amount_20d_cs_rank"] = values.rank(method="average", pct=True)
    std = float(values.std(ddof=0))
    if math.isfinite(std) and std > 1e-12:
        df["mf_net_amount_20d_cs_zscore"] = (values - float(values.mean())) / std
    else:
        df["mf_net_amount_20d_cs_zscore"] = float("nan")
    return df


def _with_industry_moneyflow_features(frame: Any) -> Any:
    pd = pandas()
    df = frame.copy()
    base_column = "mf_net_amount_20d_to_amount"
    if base_column not in df.columns or "_industry_label" not in df.columns:
        return df
    df["mf_net_amount_20d_industry_zscore"] = float("nan")
    for _label, index in df.groupby("_industry_label", dropna=True).groups.items():
        values = pd.to_numeric(df.loc[index, base_column], errors="coerce")
        std = float(values.std(ddof=0))
        if math.isfinite(std) and std > 1e-12:
            df.loc[index, "mf_net_amount_20d_industry_zscore"] = (
                values - float(values.mean())
            ) / std
    return df


def _as_float(value: object) -> float:
    try:
        result = float(cast(float, value))
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def _zero_if_missing(value: object) -> float:
    result = _as_float(value)
    return result if math.isfinite(result) else 0.0


def _safe_ratio(numerator: object, denominator: object) -> float:
    num = _as_float(numerator)
    den = _as_float(denominator)
    if not math.isfinite(num) or not math.isfinite(den) or abs(den) <= 1e-12:
        return float("nan")
    return num / den


def _append_state(
    state: dict[str, deque[float]],
    *,
    max_window: int,
    values: Mapping[str, object],
) -> None:
    for key, value in values.items():
        series = state.setdefault(key, deque(maxlen=max_window))
        series.append(_zero_if_missing(value))


def _rolling_sum(state: Mapping[str, deque[float]], key: str, window: int) -> float:
    values = state.get(key)
    if not values:
        return 0.0
    return float(sum(list(values)[-window:]))


def _records_for_trade_date(
    frame: Any,
    *,
    windows: Sequence[int],
    states: dict[str, dict[str, deque[float]]],
) -> list[dict[str, object]]:
    max_window = max(windows)
    records: list[dict[str, object]] = []
    metrics = (
        "_mf_net_amount",
        "_mf_elg_net_amount",
        "_mf_lg_net_amount",
        "_mf_gross_flow_amount",
        "_amount_for_ratio",
        "_float_mv_for_ratio",
    )
    rows = frame.sort_values(["trade_date", "symbol"]).to_dict("records")
    for row in rows:
        symbol = str(row["symbol"])
        trade_date = str(row["trade_date"])
        state = states.setdefault(symbol, {})
        _append_state(
            state,
            max_window=max_window,
            values={key: row.get(key) for key in metrics},
        )
        output: dict[str, object] = {
            "trade_date": trade_date,
            "symbol": symbol,
            "available_date": trade_date,
        }
        if "_industry_label" in row:
            output["_industry_label"] = row.get("_industry_label")
        for window in windows:
            amount = _rolling_sum(state, "_amount_for_ratio", window)
            output[f"mf_net_amount_{window}d_to_amount"] = _safe_ratio(
                _rolling_sum(state, "_mf_net_amount", window),
                amount,
            )
            output[f"mf_elg_net_amount_{window}d_to_amount"] = _safe_ratio(
                _rolling_sum(state, "_mf_elg_net_amount", window),
                amount,
            )
            output[f"mf_lg_net_amount_{window}d_to_amount"] = _safe_ratio(
                _rolling_sum(state, "_mf_lg_net_amount", window),
                amount,
            )
        if 20 in windows:
            output["mf_net_amount_20d_to_float_mv"] = _safe_ratio(
                _rolling_sum(state, "_mf_net_amount", 20),
                row.get("_float_mv_for_ratio"),
            )
            output["mf_buy_sell_imbalance_20d"] = _safe_ratio(
                _rolling_sum(state, "_mf_net_amount", 20),
                _rolling_sum(state, "_mf_gross_flow_amount", 20),
            )
        records.append(output)
    return records


def _merge_optional_overlays(
    moneyflow: Any,
    *,
    trade_date: str,
    daily_parts: Mapping[str, Path],
    daily_basic_parts: Mapping[str, Path],
) -> Any:
    df = moneyflow.copy()
    if daily_parts:
        daily = _overlay_frame(daily_parts, trade_date, label="daily", columns=("amount",))
        if not daily.empty and "amount" in daily.columns:
            daily = daily.rename(columns={"amount": "_daily_amount"})
            df = df.merge(
                daily[["symbol", "trade_date", "_daily_amount"]],
                on=["symbol", "trade_date"],
                how="left",
            )
    if daily_basic_parts:
        daily_basic = _overlay_frame(
            daily_basic_parts,
            trade_date,
            label="daily_basic",
            columns=("circ_mv",),
        )
        if not daily_basic.empty and "circ_mv" in daily_basic.columns:
            daily_basic = daily_basic.rename(columns={"circ_mv": "_daily_basic_circ_mv"})
            df = df.merge(
                daily_basic[["symbol", "trade_date", "_daily_basic_circ_mv"]],
                on=["symbol", "trade_date"],
                how="left",
            )
    return df


def _non_null_feature_values(frame: Any, feature_columns: Sequence[str]) -> int:
    present = [column for column in feature_columns if column in frame.columns]
    if not present:
        return 0
    return int(frame[present].notna().to_numpy().sum())


def _flow_feature_sources(
    moneyflow_dir: str | Path,
    daily_dir: str | Path | None,
    daily_basic_dir: str | Path | None,
    industry_dir: str | Path | None,
) -> tuple[Mapping[str, Path], Mapping[str, Path], Mapping[str, Path], Any]:
    moneyflow_parts = _flow_trade_date_part_map(moneyflow_dir, label="moneyflow")
    daily_parts = (
        _flow_trade_date_part_map(daily_dir, label="daily") if daily_dir is not None else {}
    )
    daily_basic_parts = (
        _flow_trade_date_part_map(daily_basic_dir, label="daily_basic")
        if daily_basic_dir is not None
        else {}
    )
    return moneyflow_parts, daily_parts, daily_basic_parts, _load_industry_changes(industry_dir)
