"""Consistent-scope PIT public-fund top-10 stock ownership features."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_flow_validation import (
    validate_a_share_flow_ownership_features,
)
from market_data_platform.providers.tushare_a_share_ownership_features_part01 import (
    _aggregate_fund_rows,
    _apply_available_dates,
    _load_daily_basic,
    _load_fund_portfolio,
    _trade_dates_from_daily_basic,
)
from market_data_platform.providers.tushare_a_share_ownership_features_part02 import (
    _build_pit_state_events,
    _with_daily_basic_ratios,
)
from market_data_platform.providers.tushare_a_share_ownership_features_part03 import (
    _write_trade_date_partitions,
)
from market_data_platform.providers.tushare_common import pandas, write_manifest

TOP10_RENAME = {
    "fund_count_holding_stock": "fund_top10_count_holding_stock",
    "fund_hold_mv": "fund_top10_hold_mv",
    "fund_hold_amount": "fund_top10_hold_amount",
    "fund_stk_mkv_ratio_sum": "fund_top10_stk_mkv_ratio_sum",
    "fund_stk_float_ratio_sum": "fund_top10_stk_float_ratio_sum",
    "fund_hold_mv_to_total_mv": "fund_top10_hold_mv_to_total_mv",
    "fund_hold_mv_to_float_mv": "fund_top10_hold_mv_to_float_mv",
    "fund_hold_amount_to_float_share": "fund_top10_hold_amount_to_float_share",
}


def _select_top_disclosed_holdings(frame: Any, *, top_n: int = 10) -> Any:
    """Normalize every fund disclosure to its largest ``top_n`` stock positions."""
    pd = pandas()
    if frame.empty:
        return frame
    if top_n <= 0:
        raise ValueError("top_n must be positive.")

    df = frame.copy()
    for column in ("mkv", "amount"):
        df[f"_{column}_rank"] = pd.to_numeric(df[column], errors="coerce").fillna(float("-inf"))
    sort_columns = [
        "available_date",
        "fund_code",
        "report_period",
        "_mkv_rank",
        "_amount_rank",
        "symbol",
    ]
    ascending = [True, True, True, False, False, True]
    ranked = df.sort_values(sort_columns, ascending=ascending)
    return (
        ranked.groupby(
            ["available_date", "fund_code", "report_period"],
            sort=False,
            group_keys=False,
        )
        .head(top_n)
        .drop(columns=["_mkv_rank", "_amount_rank"])
        .reset_index(drop=True)
    )


def _rename_top10_features(frame: Any) -> Any:
    columns = {source: target for source, target in TOP10_RENAME.items() if source in frame.columns}
    return frame.rename(columns=columns)


def build_a_share_fund_top10_portfolio_features(  # noqa: PLR0913
    *,
    fund_portfolio_dir: str | Path,
    out_dir: str | Path,
    daily_basic_dir: str | Path | None = None,
    available_delay_days: int = 1,
    top_n: int = 10,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, Any]:
    """Build PIT stock features from a consistent top-N slice of every fund disclosure.

    TuShare ``fund_portfolio`` may contain only leading holdings in quarterly
    reports while interim/annual reports can contain a broader stock list.  The
    raw disclosure is therefore normalized to the largest ``top_n`` positions
    per fund/report/disclosure before entering the existing PIT state machine.
    """
    if top_n <= 0:
        raise ValueError("top_n must be positive.")

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
    normalized = _select_top_disclosed_holdings(raw, top_n=top_n)
    events = _build_pit_state_events(normalized)
    daily_basic = _load_daily_basic(daily_basic_dir)
    features = _rename_top10_features(_with_daily_basic_ratios(events, daily_basic))
    feature_columns = [column for column in features.columns if column.startswith("fund_top10_")]

    totals = {
        "rows": int(len(features)),
        "symbols": int(features["symbol"].nunique()) if "symbol" in features else 0,
        "feature_columns": len(feature_columns),
        "feature_non_null_values": (
            int(features[feature_columns].notna().to_numpy().sum()) if feature_columns else 0
        ),
    }
    if totals["rows"] < min_rows or totals["symbols"] < min_symbols:
        raise ValueError(f"fund_top10_portfolio_features asset is too small: {totals}")
    if totals["feature_non_null_values"] <= 0:
        raise ValueError("fund_top10_portfolio_features contains no non-null feature values.")

    files = _write_trade_date_partitions(features, output_dir)
    totals["files"] = files
    manifest = {
        "schema_version": "tushare.a_share.fund_top10_portfolio_features.v1",
        "dataset": "fund_top10_portfolio_features",
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
            "holding_scope": f"top_{int(top_n)}_by_mkv_per_fund_disclosure",
            "scope_normalization": (
                "Every disclosed portfolio is truncated to the largest stock positions "
                "before PIT state replacement so quarterly and interim/annual disclosures "
                "share one observable scope."
            ),
            "state_model": (
                "Each fund's latest disclosed top-N portfolio replaces that fund's previous "
                "top-N portfolio; stock-level features aggregate currently known states."
            ),
            "zero_rows_for_exits": True,
            "event_level_change_fields": False,
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


def validate_a_share_fund_top10_portfolio_features(
    *,
    asset_dir: str | Path,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, object]:
    """Validate a derived public-fund top-N ownership feature asset."""
    return validate_a_share_flow_ownership_features(
        asset_dir=asset_dir,
        min_rows=min_rows,
        min_symbols=min_symbols,
    )
