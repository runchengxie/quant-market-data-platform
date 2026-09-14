"""A-share ownership-style feature assets derived from TuShare holdings data."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_flow_validation import (
    validate_a_share_flow_ownership_features,
)
from market_data_platform.providers.tushare_a_share_ownership_features_part01 import (
    _load_daily_basic,
    _load_stk_holdertrade_asset,
    _load_top_inst_events,
    _trade_dates_from_daily_basic,
)
from market_data_platform.providers.tushare_a_share_ownership_features_part02 import (
    _load_daily_amount,
    _with_top_inst_event_features,
)
from market_data_platform.providers.tushare_a_share_ownership_features_part03 import (
    _prepare_stk_holdertrade_events,
    _with_holdertrade_event_features,
    _write_trade_date_partitions,
)
from market_data_platform.providers.tushare_common import (
    write_manifest,
)


def build_a_share_top_inst_events(  # noqa: PLR0913
    *,
    top_inst_dir: str | Path,
    out_dir: str | Path,
    daily_dir: str | Path | None = None,
    window: int = 20,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, Any]:
    """Build sparse institutional trading event features from TuShare top_inst."""
    output_dir = Path(out_dir).expanduser().resolve()
    raw_events = _load_top_inst_events(top_inst_dir)
    daily_amount = _load_daily_amount(daily_dir)
    features = _with_top_inst_event_features(raw_events, daily_amount, window=window)
    feature_columns = [column for column in features.columns if column.startswith("top_inst_")]
    totals = {
        "rows": int(len(features)),
        "symbols": int(features["symbol"].nunique()) if "symbol" in features else 0,
        "feature_columns": len(feature_columns),
        "feature_non_null_values": (
            int(features[feature_columns].notna().to_numpy().sum()) if feature_columns else 0
        ),
    }
    if totals["rows"] < min_rows or totals["symbols"] < min_symbols:
        raise ValueError(f"top_inst_events asset is too small: {totals}")
    if totals["feature_non_null_values"] <= 0:
        raise ValueError("top_inst_events contains no non-null feature values.")

    files = _write_trade_date_partitions(features, output_dir)
    totals["files"] = files
    manifest = {
        "schema_version": "tushare.a_share.top_inst_events.v1",
        "dataset": "top_inst_events",
        "market": "a_share",
        "provider": "derived",
        "status": "completed",
        "output_dir": str(output_dir),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "top_inst_dir": str(Path(top_inst_dir).expanduser().resolve()),
            "daily_dir": (
                str(Path(daily_dir).expanduser().resolve()) if daily_dir is not None else None
            ),
        },
        "query": {
            "start_date": str(features["trade_date"].min()),
            "end_date": str(features["trade_date"].max()),
            "partition_by": "trade_date",
            "window": int(window),
        },
        "semantics": {
            "point_in_time": True,
            "raw_event_date_column": "trade_date",
            "available_date_column": "available_date",
            "availability_delay_days": 0,
            "state_model": (
                "Sparse institutional trading events are aggregated by symbol and "
                "trade_date; rolling features use available daily rows when daily_dir "
                "is supplied."
            ),
            "amount_unit": (
                "top_inst buy/sell/net_buy are treated as ten-thousand CNY; "
                "daily.amount is divided by 10 from thousand CNY to ten-thousand CNY."
            ),
        },
        "feature_columns": feature_columns,
        "totals": totals,
    }
    write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def validate_a_share_top_inst_events(
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


def build_a_share_holdertrade_events(  # noqa: PLR0913
    *,
    stk_holdertrade_dir: str | Path,
    out_dir: str | Path,
    daily_basic_dir: str | Path | None = None,
    amount_window: int = 20,
    count_window: int = 60,
    available_delay_days: int = 1,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, Any]:
    """Build sparse shareholder increase/decrease event features from stk_holdertrade."""
    output_dir = Path(out_dir).expanduser().resolve()
    trade_dates = _trade_dates_from_daily_basic(daily_basic_dir)
    raw = _load_stk_holdertrade_asset(stk_holdertrade_dir)
    events = _prepare_stk_holdertrade_events(
        raw,
        trade_dates=trade_dates,
        available_delay_days=available_delay_days,
    )
    daily_basic = _load_daily_basic(daily_basic_dir)
    features = _with_holdertrade_event_features(
        events,
        daily_basic,
        amount_window=amount_window,
        count_window=count_window,
    )
    feature_columns = [
        column
        for column in features.columns
        if column.startswith("holdertrade_") or column == "days_since_holdertrade"
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
        raise ValueError(f"holdertrade_events asset is too small: {totals}")
    if totals["feature_non_null_values"] <= 0:
        raise ValueError("holdertrade_events contains no non-null feature values.")

    files = _write_trade_date_partitions(features, output_dir)
    totals["files"] = files
    manifest = {
        "schema_version": "tushare.a_share.holdertrade_events.v1",
        "dataset": "holdertrade_events",
        "market": "a_share",
        "provider": "derived",
        "status": "completed",
        "output_dir": str(output_dir),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "stk_holdertrade_dir": str(Path(stk_holdertrade_dir).expanduser().resolve()),
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
            "amount_window": int(amount_window),
            "count_window": int(count_window),
        },
        "semantics": {
            "point_in_time": True,
            "raw_event_date_column": "ann_date",
            "available_date_column": "available_date",
            "availability_delay_days": int(available_delay_days),
            "state_model": (
                "Sparse shareholder increase/decrease disclosures are available from "
                "the next configured trading day; rolling count features are built on "
                "daily_basic trading dates when supplied."
            ),
            "amount_unit": (
                "holdertrade_net_amount uses change_amount when available, otherwise "
                "abs(change_vol) * avg_price / 10000; daily_basic.circ_mv is "
                "ten-thousand CNY."
            ),
        },
        "feature_columns": feature_columns,
        "totals": totals,
    }
    write_manifest(output_dir / "manifest.yml", manifest)
    return manifest


def validate_a_share_holdertrade_events(
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
