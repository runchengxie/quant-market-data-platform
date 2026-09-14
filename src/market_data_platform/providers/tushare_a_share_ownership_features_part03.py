"""A-share ownership-style feature assets derived from TuShare holdings data."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_flow_validation import (
    validate_a_share_flow_ownership_features,
)
from market_data_platform.providers.tushare_a_share_ownership_features_part01 import (
    FUND_PORTFOLIO_FEATURE_KEY_COLUMNS,
    _aggregate_fund_rows,
    _apply_available_dates,
    _load_daily_basic,
    _load_fund_portfolio,
    _load_top10_holder_asset,
    _trade_dates_from_daily_basic,
)
from market_data_platform.providers.tushare_a_share_ownership_features_part02 import (
    _build_pit_state_events,
    _days_since_flags,
    _merge_holder_event_frames,
    _prepare_holder_events,
    _with_change_features,
    _with_daily_basic_ratios,
    _with_holder_change_features,
    _with_signed_holdertrade_values,
)
from market_data_platform.providers.tushare_common import (
    pandas,
    write_frame,
    write_manifest,
)
from market_data_platform.providers.tushare_flow_utils import (
    date_token,
    numeric,
    partition_payload,
)


def _prepare_stk_holdertrade_events(
    frame: Any,
    *,
    trade_dates: list[str],
    available_delay_days: int,
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
    df = _with_signed_holdertrade_values(df)
    df["holdertrade_event_count"] = 1.0
    df["holdertrade_buy_event_count"] = (df["holdertrade_direction"] > 0.0).astype(float)
    df["holdertrade_sell_event_count"] = (df["holdertrade_direction"] < 0.0).astype(float)
    df["_holdertrade_holder_name"] = df["holder_name"].where(
        df["holder_name"].astype(str).str.len().gt(0)
    )
    return (
        df.groupby(["trade_date", "symbol"], dropna=False, sort=True)
        .agg(
            holdertrade_net_amount=(
                "holdertrade_signed_amount",
                lambda values: values.sum(min_count=1),
            ),
            holdertrade_net_vol=(
                "holdertrade_signed_vol",
                lambda values: values.sum(min_count=1),
            ),
            holdertrade_event_count=("holdertrade_event_count", "sum"),
            holdertrade_buy_event_count=("holdertrade_buy_event_count", "sum"),
            holdertrade_sell_event_count=("holdertrade_sell_event_count", "sum"),
            holdertrade_holder_count=("_holdertrade_holder_name", "nunique"),
        )
        .reset_index()
    )


def _with_holdertrade_event_features(
    events: Any,
    daily_basic: Any,
    *,
    amount_window: int,
    count_window: int,
) -> Any:
    pd = pandas()
    if events.empty:
        return pd.DataFrame()
    if amount_window <= 0 or count_window <= 0:
        raise ValueError("amount_window and count_window must be positive.")

    if not daily_basic.empty:
        event_symbols = set(events["symbol"].dropna().astype(str))
        start_date = str(events["trade_date"].min())
        end_date = str(events["trade_date"].max())
        panel_cols = [
            column for column in ("trade_date", "symbol", "circ_mv") if column in daily_basic
        ]
        panel = daily_basic[
            daily_basic["symbol"].astype(str).isin(event_symbols)
            & daily_basic["trade_date"].astype(str).between(start_date, end_date)
        ].copy()
        panel = panel.loc[:, list(dict.fromkeys(["trade_date", "symbol", *panel_cols]))]
        if "circ_mv" not in panel.columns:
            panel["circ_mv"] = float("nan")
    else:
        panel = events.loc[:, ["trade_date", "symbol"]].drop_duplicates().copy()
        panel["circ_mv"] = float("nan")

    df = panel.merge(events, on=["trade_date", "symbol"], how="outer")
    zero_columns = (
        "holdertrade_event_count",
        "holdertrade_buy_event_count",
        "holdertrade_sell_event_count",
        "holdertrade_holder_count",
        "holdertrade_net_vol",
    )
    for column in zero_columns:
        if column not in df.columns:
            df[column] = 0.0
        df[column] = numeric(df, column).fillna(0.0)
    if "holdertrade_net_amount" not in df.columns:
        df["holdertrade_net_amount"] = float("nan")
    df["holdertrade_net_amount"] = numeric(df, "holdertrade_net_amount")
    no_event = df["holdertrade_event_count"] <= 0.0
    df.loc[no_event, "holdertrade_net_amount"] = df.loc[
        no_event,
        "holdertrade_net_amount",
    ].fillna(0.0)
    if "circ_mv" not in df.columns:
        df["circ_mv"] = float("nan")
    df["circ_mv"] = numeric(df, "circ_mv")

    pieces = []
    for _symbol, group in df.sort_values(["symbol", "trade_date"]).groupby(
        "symbol",
        sort=True,
    ):
        group = group.copy()
        net_amount_roll = (
            group["holdertrade_net_amount"].fillna(0.0).rolling(amount_window, min_periods=1).sum()
        )
        group[f"holdertrade_net_amount_{amount_window}d"] = net_amount_roll
        group[f"holdertrade_net_amount_{amount_window}d_to_float_mv"] = net_amount_roll / (
            group["circ_mv"].where(group["circ_mv"] > 0.0)
        )
        group[f"holdertrade_net_vol_{amount_window}d"] = (
            group["holdertrade_net_vol"].rolling(amount_window, min_periods=1).sum()
        )
        group[f"holdertrade_buy_count_{count_window}d"] = (
            group["holdertrade_buy_event_count"].rolling(count_window, min_periods=1).sum()
        )
        group[f"holdertrade_sell_count_{count_window}d"] = (
            group["holdertrade_sell_event_count"].rolling(count_window, min_periods=1).sum()
        )
        group[f"holdertrade_event_count_{count_window}d"] = (
            group["holdertrade_event_count"].rolling(count_window, min_periods=1).sum()
        )
        group["days_since_holdertrade"] = _days_since_flags(group["holdertrade_event_count"] > 0.0)
        pieces.append(group)

    output = pd.concat(pieces, ignore_index=True)
    output["available_date"] = output["trade_date"]
    feature_columns = [
        column
        for column in output.columns
        if column.startswith("holdertrade_") or column == "days_since_holdertrade"
    ]
    return (
        output.loc[:, ["trade_date", "symbol", "available_date", *feature_columns]]
        .sort_values(["trade_date", "symbol"])
        .drop_duplicates(subset=["trade_date", "symbol"], keep="last")
        .reset_index(drop=True)
    )


def _normalize_feature_key_columns(frame: Any) -> Any:
    df = frame.copy()
    for column in ("trade_date", "available_date", "report_period", "disclosure_date"):
        if column in df.columns:
            df[column] = df[column].map(date_token)
    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].astype(str).str.strip()
    return df


def _write_trade_date_partitions(frame: Any, out_dir: Path) -> int:
    files = 0
    normalized = _normalize_feature_key_columns(frame)
    for trade_date, group in normalized.groupby("trade_date", sort=True):
        trade_date_token = date_token(trade_date)
        if not trade_date_token:
            continue
        write_frame(
            partition_payload(group.reset_index(drop=True)),
            out_dir / "data" / f"trade_date={trade_date_token}" / "part.parquet",
        )
        files += 1
    return files


def build_a_share_fund_portfolio_features(  # noqa: PLR0913
    *,
    fund_portfolio_dir: str | Path,
    out_dir: str | Path,
    daily_basic_dir: str | Path | None = None,
    available_delay_days: int = 1,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, Any]:
    """Build PIT public-fund ownership features from raw TuShare fund_portfolio."""
    output_dir = Path(out_dir).expanduser().resolve()
    trade_dates = _trade_dates_from_daily_basic(daily_basic_dir)
    raw = _load_fund_portfolio(fund_portfolio_dir)
    raw = _aggregate_fund_rows(
        _apply_available_dates(
            raw,
            trade_dates=trade_dates,
            available_delay_days=available_delay_days,
        )
    )
    events = _build_pit_state_events(raw)
    daily_basic = _load_daily_basic(daily_basic_dir)
    features = _with_change_features(_with_daily_basic_ratios(events, daily_basic))
    feature_columns = [
        column
        for column in features.columns
        if column.startswith("fund_") and column not in FUND_PORTFOLIO_FEATURE_KEY_COLUMNS
    ]
    totals = {
        "rows": int(len(features)),
        "symbols": int(features["symbol"].nunique()) if "symbol" in features else 0,
        "feature_columns": len(feature_columns),
        "feature_non_null_values": (
            int(features[feature_columns].notna().to_numpy().sum()) if feature_columns else 0
        ),
    }
    if totals["rows"] < min_rows or totals["symbols"] < min_symbols:
        raise ValueError(f"fund_portfolio_features asset is too small: {totals}")
    if totals["feature_non_null_values"] <= 0:
        raise ValueError("fund_portfolio_features contains no non-null feature values.")

    files = _write_trade_date_partitions(features, output_dir)
    totals["files"] = files
    manifest = {
        "schema_version": "tushare.a_share.fund_portfolio_features.v1",
        "dataset": "fund_portfolio_features",
        "market": "a_share",
        "provider": "derived",
        "status": "completed",
        "output_dir": str(output_dir),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "fund_portfolio_dir": str(Path(fund_portfolio_dir).expanduser().resolve()),
            "daily_basic_dir": (
                str(Path(daily_basic_dir).expanduser().resolve())
                if daily_basic_dir is not None
                else None
            ),
        },
        "provenance": {
            "source_retrieval_history": "inherited from fund_portfolio manifest when available",
            "revision_safe": False,
            "note": (
                "A single current build retrieval timestamp is not a historical vintage archive; "
                "promotion requires immutable raw vintages."
            ),
        },
        "query": {
            "start_date": str(features["trade_date"].min()),
            "end_date": str(features["trade_date"].max()),
            "partition_by": "trade_date",
        },
        "semantics": {
            "point_in_time": True,
            "raw_report_period_column": "end_date",
            "disclosure_date_column": "ann_date",
            "available_date_column": "available_date",
            "availability_delay_days": int(available_delay_days),
            "state_model": (
                "Each fund's latest disclosed portfolio replaces that fund's previous "
                "portfolio; stock-level features aggregate currently known fund states."
            ),
            "zero_rows_for_exits": True,
            "market_value_unit": (
                "fund_portfolio.mkv is CNY; daily_basic total_mv/circ_mv are ten-thousand CNY"
            ),
            "share_unit": (
                "fund_portfolio.amount is shares; daily_basic float_share is ten-thousand shares"
            ),
        },
        "feature_columns": feature_columns,
        "totals": totals,
    }
    write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def validate_a_share_fund_portfolio_features(
    *,
    asset_dir: str | Path,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, object]:
    return validate_a_share_flow_ownership_features(
        asset_dir=asset_dir,
        min_rows=min_rows,
        min_symbols=min_symbols,
    )


def build_a_share_holder_structure_features(  # noqa: PLR0913
    *,
    top10_holders_dir: str | Path,
    out_dir: str | Path,
    top10_floatholders_dir: str | Path | None = None,
    daily_basic_dir: str | Path | None = None,
    available_delay_days: int = 1,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, Any]:
    """Build PIT shareholder-structure features from TuShare top-10 holder APIs."""
    output_dir = Path(out_dir).expanduser().resolve()
    trade_dates = _trade_dates_from_daily_basic(daily_basic_dir)
    holder_events = _prepare_holder_events(
        _load_top10_holder_asset(top10_holders_dir),
        trade_dates=trade_dates,
        available_delay_days=available_delay_days,
        prefix="top10",
    )
    floatholder_events = (
        _prepare_holder_events(
            _load_top10_holder_asset(top10_floatholders_dir),
            trade_dates=trade_dates,
            available_delay_days=available_delay_days,
            prefix="top10_float",
        )
        if top10_floatholders_dir is not None
        else pandas().DataFrame()
    )
    features = _with_holder_change_features(
        _merge_holder_event_frames([holder_events, floatholder_events])
    )
    feature_columns = [
        column
        for column in features.columns
        if (column.startswith("top10_") or column.startswith("holder_"))
        and not column.endswith("_report_period")
        and not column.endswith("_disclosure_date")
    ]
    totals = {
        "rows": int(len(features)),
        "symbols": int(features["symbol"].nunique()) if "symbol" in features else 0,
        "feature_columns": len(feature_columns),
        "feature_non_null_values": (
            int(features[feature_columns].notna().to_numpy().sum()) if feature_columns else 0
        ),
    }
    if totals["rows"] < min_rows or totals["symbols"] < min_symbols:
        raise ValueError(f"holder_structure_features asset is too small: {totals}")
    if totals["feature_non_null_values"] <= 0:
        raise ValueError("holder_structure_features contains no non-null feature values.")

    files = _write_trade_date_partitions(features, output_dir)
    totals["files"] = files
    manifest = {
        "schema_version": "tushare.a_share.holder_structure_features.v1",
        "dataset": "holder_structure_features",
        "market": "a_share",
        "provider": "derived",
        "status": "completed",
        "output_dir": str(output_dir),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "top10_holders_dir": str(Path(top10_holders_dir).expanduser().resolve()),
            "top10_floatholders_dir": (
                str(Path(top10_floatholders_dir).expanduser().resolve())
                if top10_floatholders_dir is not None
                else None
            ),
            "daily_basic_dir": (
                str(Path(daily_basic_dir).expanduser().resolve())
                if daily_basic_dir is not None
                else None
            ),
        },
        "query": {
            "start_date": str(features["trade_date"].min()),
            "end_date": str(features["trade_date"].max()),
            "partition_by": "trade_date",
        },
        "semantics": {
            "point_in_time": True,
            "raw_report_period_column": "end_date",
            "disclosure_date_column": "ann_date",
            "available_date_column": "available_date",
            "availability_delay_days": int(available_delay_days),
            "state_model": (
                "Each disclosure event is available from the next configured trading "
                "day; downstream daily panels should forward-fill by symbol."
            ),
            "institution_classification": (
                "holder_type is preferred; missing holder_type falls back to "
                "conservative holder-name keyword matching."
            ),
        },
        "feature_columns": feature_columns,
        "totals": totals,
    }
    write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def validate_a_share_holder_structure_features(
    *,
    asset_dir: str | Path,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, object]:
    return validate_a_share_flow_ownership_features(
        asset_dir=asset_dir,
        min_rows=min_rows,
        min_symbols=min_symbols,
    )
