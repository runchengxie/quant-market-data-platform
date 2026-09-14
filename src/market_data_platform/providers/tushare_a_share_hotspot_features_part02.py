"""A-share hotspot features derived from TuShare hot-list and concept assets."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_hotspot_features_part01 import (
    BUY_RATING_FRAGMENTS,
    HOTSPOT_FEATURE_COLUMNS,
    HOTSPOT_FEATURE_KEY_COLUMNS,
    HotspotFeatureBuildSpec,
    _add_days_since_hot,
    _EventAssetRequest,
    _EventFeaturesRequest,
    _hot_list_features,
    _load_daily_basic_grid,
    _load_event_asset,
    _load_month_asset,
    _load_trade_date_asset,
    _normalize_symbol_column,
    _numeric,
    _symbol_date_counts,
    _theme_features,
)
from market_data_platform.providers.tushare_common import (
    pandas,
    validate_date,
    write_frame,
    write_manifest,
)
from market_data_platform.providers.tushare_flow_utils import (
    partition_payload,
)


def _rolling_sum_by_symbol(frame: Any, column: str, window: int) -> Any:
    return (
        frame.groupby("symbol", sort=False)[column]
        .rolling(window, min_periods=1)
        .sum()
        .reset_index(level=0, drop=True)
    )


def _merge_kpl_event_counts(
    out: Any,
    kpl_list_dir: str | Path | None,
    *,
    start_date: str,
    end_date: str,
) -> Any:
    kpl = _load_trade_date_asset(
        kpl_list_dir,
        label="kpl_list",
        start_date=start_date,
        end_date=end_date,
        columns=("trade_date", "ts_code", "symbol", "tag"),
    )
    if not kpl.empty:
        kpl = _normalize_symbol_column(kpl)
        kpl["tag"] = kpl["tag"].fillna("").astype(str)
        limit_up = _symbol_date_counts(kpl.loc[kpl["tag"].str.contains("涨停")], value_col="_lu")
        failed = _symbol_date_counts(kpl.loc[kpl["tag"].str.contains("炸板")], value_col="_fb")
        out = out.merge(limit_up, on=["trade_date", "symbol"], how="left")
        out = out.merge(failed, on=["trade_date", "symbol"], how="left")
    for column in ("_lu", "_fb"):
        if column not in out.columns:
            out[column] = 0.0
        out[column] = _numeric(out, column, default=0.0).fillna(0.0)
    return out


def _merge_limit_step_features(
    out: Any,
    limit_step_dir: str | Path | None,
    *,
    start_date: str,
    end_date: str,
) -> Any:
    limit_step = _load_trade_date_asset(
        limit_step_dir,
        label="limit_step",
        start_date=start_date,
        end_date=end_date,
        columns=("trade_date", "ts_code", "symbol", "nums"),
    )
    if not limit_step.empty:
        limit_step = _normalize_symbol_column(limit_step)
        limit_step["nums"] = _numeric(limit_step, "nums", default=0.0).fillna(0.0)
        step_features = (
            limit_step.groupby(["trade_date", "symbol"], as_index=False)["nums"]
            .max()
            .rename(columns={"nums": "limit_step_max"})
        )
        out = out.merge(step_features, on=["trade_date", "symbol"], how="left")
    if "limit_step_max" not in out.columns:
        out["limit_step_max"] = 0.0
    out["limit_step_max"] = _numeric(out, "limit_step_max", default=0.0).fillna(0.0)
    return out


def _merge_report_features(
    out: Any,
    report_rc_dir: str | Path | None,
    *,
    start_date: str,
    end_date: str,
) -> Any:
    pd = pandas()
    report = _load_event_asset(
        _EventAssetRequest(
            asset_dir=report_rc_dir,
            label="report_rc",
            date_col="report_date",
            start_date=start_date,
            end_date=end_date,
            columns=("trade_date", "ts_code", "symbol", "report_date", "rating"),
        )
    )
    if not report.empty:
        report = _normalize_symbol_column(report)
        report_counts = _symbol_date_counts(report, value_col="_report")
        rating_text = report.get("rating", pd.Series("", index=report.index)).fillna("").astype(str)
        buy_mask = rating_text.str.lower().map(
            lambda text: any(fragment in text for fragment in BUY_RATING_FRAGMENTS)
        )
        buy_counts = _symbol_date_counts(report.loc[buy_mask], value_col="_buy_rating")
        out = out.merge(report_counts, on=["trade_date", "symbol"], how="left")
        out = out.merge(buy_counts, on=["trade_date", "symbol"], how="left")
    for column in ("_report", "_buy_rating"):
        if column not in out.columns:
            out[column] = 0.0
        out[column] = _numeric(out, column, default=0.0).fillna(0.0)
    return out


def _merge_survey_features(
    out: Any,
    stk_surv_dir: str | Path | None,
    *,
    start_date: str,
    end_date: str,
) -> Any:
    survey = _load_event_asset(
        _EventAssetRequest(
            asset_dir=stk_surv_dir,
            label="stk_surv",
            date_col="surv_date",
            start_date=start_date,
            end_date=end_date,
            columns=("trade_date", "ts_code", "symbol", "surv_date"),
        )
    )
    if not survey.empty:
        survey = _normalize_symbol_column(survey)
        survey_counts = _symbol_date_counts(survey, value_col="_survey")
        out = out.merge(survey_counts, on=["trade_date", "symbol"], how="left")
    if "_survey" not in out.columns:
        out["_survey"] = 0.0
    out["_survey"] = _numeric(out, "_survey", default=0.0).fillna(0.0)
    return out


def _add_event_rolling_windows(out: Any) -> Any:
    out = out.sort_values(["symbol", "trade_date"])
    out["kpl_limit_up_count_5d"] = _rolling_sum_by_symbol(out, "_lu", 5)
    out["failed_board_count_5d"] = _rolling_sum_by_symbol(out, "_fb", 5)
    out["report_rc_count_20d"] = _rolling_sum_by_symbol(out, "_report", 20)
    out["rating_buy_count_20d"] = _rolling_sum_by_symbol(out, "_buy_rating", 20)
    out["survey_count_20d"] = _rolling_sum_by_symbol(out, "_survey", 20)
    return out


def _merge_broker_features(
    out: Any,
    broker_recommend_dir: str | Path | None,
    *,
    start_date: str,
    end_date: str,
) -> Any:
    broker = _load_month_asset(
        broker_recommend_dir,
        label="broker_recommend",
        start_date=start_date,
        end_date=end_date,
        columns=("month", "ts_code", "symbol", "broker"),
    )
    if not broker.empty:
        broker = _normalize_symbol_column(broker)
        broker["month"] = broker["month"].astype(str).str.replace(r"\.0$", "", regex=True).str[:6]
        broker_counts = (
            broker.groupby(["month", "symbol"], as_index=False)
            .size()
            .rename(columns={"size": "broker_recommend_count"})
        )
        out["month"] = out["trade_date"].str[:6]
        out = out.merge(broker_counts, on=["month", "symbol"], how="left").drop(columns=["month"])
    if "broker_recommend_count" not in out.columns:
        out["broker_recommend_count"] = 0.0
    out["broker_recommend_count"] = _numeric(
        out,
        "broker_recommend_count",
        default=0.0,
    ).fillna(0.0)
    return out


def _event_features(request: _EventFeaturesRequest) -> Any:
    out = request.base[["trade_date", "symbol"]].copy()
    out = _merge_kpl_event_counts(
        out,
        request.kpl_list_dir,
        start_date=request.start_date,
        end_date=request.end_date,
    )
    out = _merge_limit_step_features(
        out,
        request.limit_step_dir,
        start_date=request.start_date,
        end_date=request.end_date,
    )
    out = _merge_report_features(
        out,
        request.report_rc_dir,
        start_date=request.start_date,
        end_date=request.end_date,
    )
    out = _merge_survey_features(
        out,
        request.stk_surv_dir,
        start_date=request.start_date,
        end_date=request.end_date,
    )
    out = _add_event_rolling_windows(out)
    out = _merge_broker_features(
        out,
        request.broker_recommend_dir,
        start_date=request.start_date,
        end_date=request.end_date,
    )
    return out.drop(columns=["_lu", "_fb", "_report", "_buy_rating", "_survey"])


def _write_trade_date_partitions(frame: Any, out_dir: Path) -> int:
    files = 0
    for trade_date, group in frame.groupby("trade_date", sort=True):
        write_frame(
            partition_payload(group.reset_index(drop=True)),
            out_dir / "data" / f"trade_date={trade_date}" / "part.parquet",
        )
        files += 1
    return files


def _feature_non_null_values(frame: Any) -> int:
    present = [column for column in HOTSPOT_FEATURE_COLUMNS if column in frame.columns]
    if not present:
        return 0
    return int(frame[present].notna().to_numpy().sum())


def _build_spec_from_kwargs(
    spec: HotspotFeatureBuildSpec | None,
    kwargs: dict[str, Any],
) -> HotspotFeatureBuildSpec:
    if spec is not None:
        if kwargs:
            names = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected hotspot feature build arguments with spec: {names}")
        return spec
    return HotspotFeatureBuildSpec(**kwargs)


def _hotspot_source_path(value: str | Path | None) -> str | None:
    return str(Path(value).expanduser().resolve()) if value else None


def _normalized_hotspot_frame(data: Any) -> Any:
    for column in HOTSPOT_FEATURE_COLUMNS:
        if column not in data.columns:
            data[column] = 0.0
        data[column] = _numeric(data, column, default=0.0)
    data["days_since_hot"] = data["days_since_hot"].fillna(999.0)
    fill_zero_columns = [column for column in HOTSPOT_FEATURE_COLUMNS if column != "days_since_hot"]
    data[fill_zero_columns] = data[fill_zero_columns].fillna(0.0)
    return data.loc[:, [*HOTSPOT_FEATURE_KEY_COLUMNS, *HOTSPOT_FEATURE_COLUMNS]]


def _build_hotspot_feature_frame(
    spec: HotspotFeatureBuildSpec,
    *,
    start: str,
    end: str,
) -> Any:
    base = _load_daily_basic_grid(spec.daily_basic_dir, start_date=start, end_date=end)
    hot_features = _hot_list_features(spec.ths_hot_dir, start_date=start, end_date=end)
    data = base.merge(hot_features, on=["trade_date", "symbol"], how="left")
    data = _add_days_since_hot(data, hot_features)
    data = data.merge(
        _theme_features(
            dc_concept_dir=spec.dc_concept_dir,
            dc_concept_cons_dir=spec.dc_concept_cons_dir,
            kpl_concept_cons_dir=spec.kpl_concept_cons_dir,
            start_date=start,
            end_date=end,
        ),
        on=["trade_date", "symbol"],
        how="left",
    )
    data = data.merge(
        _event_features(
            _EventFeaturesRequest(
                base=base,
                kpl_list_dir=spec.kpl_list_dir,
                limit_step_dir=spec.limit_step_dir,
                report_rc_dir=spec.report_rc_dir,
                stk_surv_dir=spec.stk_surv_dir,
                broker_recommend_dir=spec.broker_recommend_dir,
                start_date=start,
                end_date=end,
            )
        ),
        on=["trade_date", "symbol"],
        how="left",
    )
    return _normalized_hotspot_frame(data)


def _hotspot_totals(frame: Any) -> dict[str, int]:
    return {
        "rows": int(len(frame)),
        "symbols": int(frame["symbol"].nunique()),
        "files": int(frame["trade_date"].nunique()),
        "feature_columns": len(HOTSPOT_FEATURE_COLUMNS),
        "feature_non_null_values": _feature_non_null_values(frame),
    }


def _validate_hotspot_totals(totals: dict[str, int], spec: HotspotFeatureBuildSpec) -> None:
    if totals["rows"] < spec.min_rows:
        raise ValueError(f"hotspot_features asset is too small: {totals}")
    if totals["symbols"] < spec.min_symbols:
        raise ValueError(f"hotspot_features symbol coverage is too small: {totals}")
    if totals["feature_non_null_values"] <= 0:
        raise ValueError("hotspot_features contains no non-null feature values.")


def _hotspot_manifest(
    *,
    spec: HotspotFeatureBuildSpec,
    output_dir: Path,
    start: str,
    end: str,
    totals: dict[str, int],
) -> dict[str, Any]:
    return {
        "schema_version": "tushare.a_share.hotspot_features.v1",
        "dataset": "hotspot_features",
        "market": "a_share",
        "provider": "derived",
        "status": "completed",
        "output_dir": str(output_dir),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "daily_basic_dir": _hotspot_source_path(spec.daily_basic_dir),
            "ths_hot_dir": _hotspot_source_path(spec.ths_hot_dir),
            "dc_concept_dir": _hotspot_source_path(spec.dc_concept_dir),
            "dc_concept_cons_dir": _hotspot_source_path(spec.dc_concept_cons_dir),
            "kpl_list_dir": _hotspot_source_path(spec.kpl_list_dir),
            "kpl_concept_cons_dir": _hotspot_source_path(spec.kpl_concept_cons_dir),
            "limit_step_dir": _hotspot_source_path(spec.limit_step_dir),
            "report_rc_dir": _hotspot_source_path(spec.report_rc_dir),
            "stk_surv_dir": _hotspot_source_path(spec.stk_surv_dir),
            "broker_recommend_dir": _hotspot_source_path(spec.broker_recommend_dir),
        },
        "query": {
            "start_date": start,
            "end_date": end,
            "partition_by": "trade_date",
            "hot_count_windows": [5],
            "slow_confirm_windows": [20],
        },
        "semantics": {
            "point_in_time": True,
            "available_date_column": "available_date",
            "available_date_rule": "same as trade_date; use with next-session execution shift",
            "base_universe": "daily_basic trade_date/symbol grid",
            "missing_event_values": "zero-filled; days_since_hot is 999 when never observed",
        },
        "feature_columns": list(HOTSPOT_FEATURE_COLUMNS),
        "totals": totals,
    }


def build_a_share_hotspot_features(
    *,
    spec: HotspotFeatureBuildSpec | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build PIT A-share hotspot features from TuShare raw hotspot assets."""
    build_spec = _build_spec_from_kwargs(spec, kwargs)
    start = validate_date(build_spec.start_date)
    end = validate_date(build_spec.end_date)
    output_dir = Path(build_spec.out_dir).expanduser().resolve()
    data = _build_hotspot_feature_frame(build_spec, start=start, end=end)
    totals = _hotspot_totals(data)
    _validate_hotspot_totals(totals, build_spec)
    data_dir = output_dir / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    files = _write_trade_date_partitions(data, output_dir)
    totals["files"] = files
    manifest = _hotspot_manifest(
        spec=build_spec,
        output_dir=output_dir,
        start=start,
        end=end,
        totals=totals,
    )
    write_manifest(output_dir / "manifest.yml", manifest)
    return manifest
