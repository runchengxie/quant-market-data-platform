"""A-share hotspot features derived from TuShare hot-list and concept assets."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_common import (
    normalize_ts_code,
    pandas,
)
from market_data_platform.providers.tushare_flow_utils import (
    date_token,
    trade_date_part_map,
)

HOTSPOT_FEATURE_KEY_COLUMNS = ("trade_date", "symbol", "available_date")

HOTSPOT_FEATURE_COLUMNS = (
    "hot_rank_pct",
    "hot_zscore",
    "rank_change",
    "days_since_hot",
    "theme_strength_z",
    "theme_hot_z",
    "theme_limit_up_count",
    "strong_theme_count",
    "max_theme_strength",
    "is_theme_leader",
    "kpl_theme_count",
    "kpl_theme_hot_num_max",
    "kpl_limit_up_count_5d",
    "failed_board_count_5d",
    "limit_step_max",
    "report_rc_count_20d",
    "rating_buy_count_20d",
    "survey_count_20d",
    "broker_recommend_count",
)

BUY_RATING_FRAGMENTS = (
    "买入",
    "增持",
    "推荐",
    "强烈推荐",
    "跑赢",
    "优于",
    "outperform",
)


@dataclass(frozen=True)
class HotspotFeatureBuildSpec:
    daily_basic_dir: str | Path
    ths_hot_dir: str | Path
    dc_concept_dir: str | Path
    dc_concept_cons_dir: str | Path
    kpl_list_dir: str | Path
    out_dir: str | Path
    start_date: str
    end_date: str
    kpl_concept_cons_dir: str | Path | None = None
    limit_step_dir: str | Path | None = None
    report_rc_dir: str | Path | None = None
    stk_surv_dir: str | Path | None = None
    broker_recommend_dir: str | Path | None = None
    min_rows: int = 1
    min_symbols: int = 1


@dataclass(frozen=True)
class _EventAssetRequest:
    asset_dir: str | Path | None
    label: str
    date_col: str
    start_date: str
    end_date: str
    columns: Sequence[str] | None = None


@dataclass(frozen=True)
class _EventFeaturesRequest:
    base: Any
    kpl_list_dir: str | Path | None
    limit_step_dir: str | Path | None
    report_rc_dir: str | Path | None
    stk_surv_dir: str | Path | None
    broker_recommend_dir: str | Path | None
    start_date: str
    end_date: str


def _numeric(frame: Any, column: str, default: float = 0.0) -> Any:
    pd = pandas()
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _read_columns(path: Path, columns: Sequence[str] | None = None) -> Any:
    pd = pandas()
    suffix = path.suffix.lower()
    if suffix == ".csv":
        if columns:
            wanted = set(columns)
            return pd.read_csv(path, usecols=lambda column: column in wanted)
        return pd.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        if columns:
            try:
                return pd.read_parquet(path, columns=list(columns))
            except Exception:
                pass
        return pd.read_parquet(path)
    raise ValueError(f"Unsupported source file type: {path}")


def _asset_files(asset_dir: str | Path | None, *, label: str) -> list[Path]:
    if asset_dir is None:
        return []
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


def _month_from_path(path: Path) -> str:
    for part in reversed(path.parts):
        if part.startswith("month="):
            text = str(part.removeprefix("month=")).strip()
            return text[:6] if len(text) >= 6 else ""
    token = date_token(path.stem)
    return token[:6] if token else ""


def _event_date_from_path(path: Path) -> str:
    for part in reversed(path.parts):
        if part.startswith("event_date="):
            return date_token(part.removeprefix("event_date="))
    return date_token(path.stem)


def _normalize_symbol_column(frame: Any, *, source_col: str = "ts_code") -> Any:
    df = frame.copy()
    if "symbol" not in df.columns and source_col in df.columns:
        df["symbol"] = df[source_col]
    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].map(normalize_ts_code)
    return df


def _zscore_by_date(frame: Any, value_col: str) -> Any:
    values = _numeric(frame, value_col, default=float("nan"))
    grouped = values.groupby(frame["trade_date"], sort=False)
    mean = grouped.transform("mean")
    std = grouped.transform(lambda series: series.std(ddof=0)).replace(0.0, float("nan"))
    return ((values - mean) / std).replace([float("inf"), float("-inf")], float("nan"))


def _load_daily_basic_grid(
    daily_basic_dir: str | Path,
    *,
    start_date: str,
    end_date: str,
) -> Any:
    pd = pandas()
    parts = trade_date_part_map(daily_basic_dir, label="daily_basic")
    frames: list[Any] = []
    for trade_date, path in parts.items():
        if trade_date < start_date or trade_date > end_date:
            continue
        df = _read_columns(path, columns=("trade_date", "ts_code", "symbol"))
        if df.empty:
            continue
        if "trade_date" not in df.columns:
            df["trade_date"] = trade_date
        df["trade_date"] = df["trade_date"].map(date_token)
        df = _normalize_symbol_column(df)
        frames.append(df.loc[:, ["trade_date", "symbol"]])
    if not frames:
        raise ValueError("daily_basic grid produced no rows for requested date range.")
    grid = pd.concat(frames, ignore_index=True).dropna(subset=["trade_date", "symbol"])
    grid = grid.drop_duplicates(["trade_date", "symbol"]).sort_values(["trade_date", "symbol"])
    grid["available_date"] = grid["trade_date"]
    return grid.reset_index(drop=True)


def _load_trade_date_asset(
    asset_dir: str | Path | None,
    *,
    label: str,
    start_date: str,
    end_date: str,
    columns: Sequence[str] | None = None,
) -> Any:
    pd = pandas()
    if asset_dir is None:
        return pd.DataFrame()
    parts = trade_date_part_map(asset_dir, label=label)
    frames: list[Any] = []
    for trade_date, path in parts.items():
        if trade_date < start_date or trade_date > end_date:
            continue
        df = _read_columns(path, columns=columns)
        if df.empty:
            continue
        if "trade_date" not in df.columns:
            df["trade_date"] = trade_date
        df["trade_date"] = df["trade_date"].map(date_token)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _load_event_asset(request: _EventAssetRequest) -> Any:
    pd = pandas()
    frames: list[Any] = []
    for path in _asset_files(request.asset_dir, label=request.label):
        event_date = _event_date_from_path(path)
        if event_date and (event_date < request.start_date or event_date > request.end_date):
            continue
        df = _read_columns(path, columns=request.columns)
        if df.empty:
            continue
        if request.date_col not in df.columns:
            df[request.date_col] = event_date
        df["trade_date"] = df[request.date_col].map(date_token)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _load_month_asset(
    asset_dir: str | Path | None,
    *,
    label: str,
    start_date: str,
    end_date: str,
    columns: Sequence[str] | None = None,
) -> Any:
    pd = pandas()
    start_month = start_date[:6]
    end_month = end_date[:6]
    frames: list[Any] = []
    for path in _asset_files(asset_dir, label=label):
        month = _month_from_path(path)
        if month and (month < start_month or month > end_month):
            continue
        df = _read_columns(path, columns=columns)
        if df.empty:
            continue
        if "month" not in df.columns:
            df["month"] = month
        df["month"] = df["month"].astype(str).str.replace(r"\.0$", "", regex=True).str[:6]
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _hot_list_features(ths_hot_dir: str | Path | None, *, start_date: str, end_date: str) -> Any:
    pd = pandas()
    hot = _load_trade_date_asset(
        ths_hot_dir,
        label="ths_hot",
        start_date=start_date,
        end_date=end_date,
        columns=("trade_date", "ts_code", "symbol", "rank", "hot"),
    )
    if hot.empty:
        return pd.DataFrame(
            columns=["trade_date", "symbol", "hot_rank_pct", "hot_zscore", "rank_change"]
        )
    hot = _normalize_symbol_column(hot)
    hot["rank"] = _numeric(hot, "rank", default=float("nan"))
    hot["hot"] = _numeric(hot, "hot", default=float("nan"))
    counts = hot.groupby("trade_date")["symbol"].transform("count").clip(lower=1)
    hot["hot_rank_pct"] = 1.0
    mask = counts > 1
    hot.loc[mask, "hot_rank_pct"] = 1.0 - (hot.loc[mask, "rank"] - 1.0) / (counts[mask] - 1.0)
    hot["hot_zscore"] = _zscore_by_date(hot, "hot")
    hot = hot.sort_values(["symbol", "trade_date"])
    hot["rank_change"] = hot.groupby("symbol")["rank"].shift(1) - hot["rank"]
    return (
        hot.groupby(["trade_date", "symbol"], as_index=False)
        .agg(
            hot_rank_pct=("hot_rank_pct", "max"),
            hot_zscore=("hot_zscore", "max"),
            rank_change=("rank_change", "max"),
        )
        .replace([float("inf"), float("-inf")], float("nan"))
    )


def _add_days_since_hot(base: Any, hot_features: Any) -> Any:
    pd = pandas()
    out = base.copy()
    hot_keys = set(zip(hot_features["trade_date"], hot_features["symbol"], strict=False))
    out["_date_index"] = out["trade_date"].map(
        {date: index for index, date in enumerate(sorted(out["trade_date"].unique()))}
    )
    out["_hot_today"] = [
        (trade_date, symbol) in hot_keys
        for trade_date, symbol in zip(out["trade_date"], out["symbol"], strict=False)
    ]
    out = out.sort_values(["symbol", "trade_date"])
    hot_index = pd.Series(float("nan"), index=out.index, dtype="float64")
    hot_index.loc[out["_hot_today"]] = out.loc[out["_hot_today"], "_date_index"].astype(float)
    last_hot = hot_index.groupby(out["symbol"], sort=False).ffill()
    out["days_since_hot"] = (out["_date_index"].astype(float) - last_hot).fillna(999.0)
    return out.drop(columns=["_date_index", "_hot_today"]).sort_values(["trade_date", "symbol"])


THEME_FEATURE_COLUMNS = (
    "strong_theme_count",
    "max_theme_strength",
    "theme_strength_z",
    "theme_hot_z",
    "theme_limit_up_count",
    "is_theme_leader",
    "kpl_theme_count",
    "kpl_theme_hot_num_max",
)


def _empty_symbol_feature_frame() -> Any:
    pd = pandas()
    return pd.DataFrame(columns=["trade_date", "symbol"])


def _dc_theme_features(
    dc_concept_dir: str | Path | None,
    dc_concept_cons_dir: str | Path | None,
    *,
    start_date: str,
    end_date: str,
) -> Any:
    concepts = _load_trade_date_asset(
        dc_concept_dir,
        label="dc_concept",
        start_date=start_date,
        end_date=end_date,
        columns=(
            "trade_date",
            "theme_code",
            "strength",
            "hot",
            "z_t_num",
            "lead_stock_code",
        ),
    )
    cons = _load_trade_date_asset(
        dc_concept_cons_dir,
        label="dc_concept_cons",
        start_date=start_date,
        end_date=end_date,
        columns=("trade_date", "ts_code", "symbol", "theme_code", "hot_num"),
    )
    if concepts.empty or cons.empty:
        return _empty_symbol_feature_frame()

    concepts["theme_code"] = concepts["theme_code"].astype(str).str.strip()
    concepts["strength"] = _numeric(concepts, "strength", default=float("nan"))
    concepts["hot"] = _numeric(concepts, "hot", default=float("nan"))
    concepts["z_t_num"] = _numeric(concepts, "z_t_num", default=0.0).fillna(0.0)
    concepts["theme_strength_z"] = _zscore_by_date(concepts, "strength")
    concepts["theme_hot_z"] = _zscore_by_date(concepts, "hot")
    concepts["lead_stock_code"] = (
        concepts["lead_stock_code"].map(normalize_ts_code)
        if "lead_stock_code" in concepts.columns
        else ""
    )
    cons = _normalize_symbol_column(cons)
    cons["theme_code"] = cons["theme_code"].astype(str).str.strip()
    exposures = cons.merge(concepts, on=["trade_date", "theme_code"], how="left")
    exposures["is_strong_theme"] = (
        (exposures["theme_strength_z"].fillna(0.0) >= 0.5)
        | (exposures["z_t_num"].fillna(0.0) > 0.0)
    ).astype(float)
    exposures["is_theme_leader_row"] = (
        exposures["lead_stock_code"].astype(str) == exposures["symbol"].astype(str)
    ).astype(float)
    return (
        exposures.groupby(["trade_date", "symbol"], as_index=False)
        .agg(
            strong_theme_count=("is_strong_theme", "sum"),
            max_theme_strength=("strength", "max"),
            theme_strength_z=("theme_strength_z", "max"),
            theme_hot_z=("theme_hot_z", "max"),
            theme_limit_up_count=("z_t_num", "max"),
            is_theme_leader=("is_theme_leader_row", "max"),
        )
        .fillna(
            {
                "strong_theme_count": 0.0,
                "max_theme_strength": 0.0,
                "theme_strength_z": 0.0,
                "theme_hot_z": 0.0,
                "theme_limit_up_count": 0.0,
                "is_theme_leader": 0.0,
            }
        )
    )


def _kpl_theme_features(
    kpl_concept_cons_dir: str | Path | None,
    *,
    start_date: str,
    end_date: str,
) -> Any:
    kpl_cons = _load_trade_date_asset(
        kpl_concept_cons_dir,
        label="kpl_concept_cons",
        start_date=start_date,
        end_date=end_date,
        columns=("trade_date", "ts_code", "con_code", "hot_num"),
    )
    if kpl_cons.empty:
        return _empty_symbol_feature_frame()
    kpl_cons["symbol"] = kpl_cons["con_code"].map(normalize_ts_code)
    kpl_cons["theme_code"] = kpl_cons["ts_code"].astype(str).str.strip()
    kpl_cons["hot_num"] = _numeric(kpl_cons, "hot_num", default=0.0).fillna(0.0)
    return (
        kpl_cons.groupby(["trade_date", "symbol"], as_index=False)
        .agg(
            kpl_theme_count=("theme_code", "nunique"),
            kpl_theme_hot_num_max=("hot_num", "max"),
        )
        .fillna({"kpl_theme_count": 0.0, "kpl_theme_hot_num_max": 0.0})
    )


def _merge_theme_feature_frames(dc_features: Any, kpl_features: Any) -> Any:
    if dc_features.empty and kpl_features.empty:
        return _empty_symbol_feature_frame()
    features = dc_features.merge(kpl_features, on=["trade_date", "symbol"], how="outer")
    for column in THEME_FEATURE_COLUMNS:
        if column not in features.columns:
            features[column] = 0.0
        features[column] = _numeric(features, column, default=0.0).fillna(0.0)
    features["strong_theme_count"] = features["strong_theme_count"] + features["kpl_theme_count"]
    return features


def _theme_features(
    *,
    dc_concept_dir: str | Path | None,
    dc_concept_cons_dir: str | Path | None,
    kpl_concept_cons_dir: str | Path | None,
    start_date: str,
    end_date: str,
) -> Any:
    dc_features = _dc_theme_features(
        dc_concept_dir,
        dc_concept_cons_dir,
        start_date=start_date,
        end_date=end_date,
    )
    kpl_features = _kpl_theme_features(
        kpl_concept_cons_dir,
        start_date=start_date,
        end_date=end_date,
    )
    return _merge_theme_feature_frames(dc_features, kpl_features)


def _symbol_date_counts(frame: Any, *, value_col: str) -> Any:
    pd = pandas()
    if frame.empty:
        return pd.DataFrame(columns=["trade_date", "symbol", value_col])
    return (
        frame.groupby(["trade_date", "symbol"], as_index=False)
        .size()
        .rename(columns={"size": value_col})
    )
