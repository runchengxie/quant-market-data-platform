"""A-share flow and ownership feature assets derived from TuShare raw data."""

from __future__ import annotations

import shutil
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers.tushare_a_share_flow_features_part01 import (
    FLOW_FEATURE_KEY_COLUMNS,
    _feature_columns,
    _flow_feature_sources,
    _merge_industry_labels,
    _merge_optional_overlays,
    _non_null_feature_values,
    _normalize_windows,
    _read_trade_date_part,
    _records_for_trade_date,
    _with_cross_sectional_moneyflow_features,
    _with_industry_moneyflow_features,
    _with_moneyflow_metrics,
)
from market_data_platform.providers.tushare_common import (
    pandas,
    write_frame,
    write_manifest,
)
from market_data_platform.providers.tushare_flow_utils import (
    partition_payload as _flow_partition_payload,
)


def build_a_share_flow_ownership_features(  # noqa: PLR0913
    *,
    moneyflow_dir: str | Path,
    out_dir: str | Path,
    daily_dir: str | Path | None = None,
    daily_basic_dir: str | Path | None = None,
    industry_dir: str | Path | None = None,
    windows: Iterable[int] | None = None,
    min_rows: int = 1,
    min_symbols: int = 1,
) -> dict[str, Any]:
    """Build rolling A 股 moneyflow features without loading the full history at once."""
    pd = pandas()
    output_dir = Path(out_dir).expanduser().resolve()
    normalized_windows = _normalize_windows(windows)
    include_industry_features = industry_dir is not None
    feature_columns = _feature_columns(
        normalized_windows,
        include_industry_features=include_industry_features,
    )
    moneyflow_parts, daily_parts, daily_basic_parts, industry = _flow_feature_sources(
        moneyflow_dir,
        daily_dir,
        daily_basic_dir,
        industry_dir,
    )

    data_dir = output_dir / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)

    states: dict[str, dict[str, deque[float]]] = {}
    symbols: set[str] = set()
    rows = 0
    files = 0
    feature_non_null_values = 0
    processed_dates: list[str] = []
    empty_dates: list[str] = []
    for trade_date, _path in moneyflow_parts.items():
        moneyflow = _read_trade_date_part(moneyflow_parts, trade_date, label="moneyflow")
        if moneyflow.empty:
            empty_dates.append(trade_date)
            continue
        enriched = _merge_optional_overlays(
            moneyflow,
            trade_date=trade_date,
            daily_parts=daily_parts,
            daily_basic_parts=daily_basic_parts,
        )
        enriched = _merge_industry_labels(enriched, industry, trade_date=trade_date)
        enriched = _with_moneyflow_metrics(enriched)
        records = _records_for_trade_date(
            enriched,
            windows=normalized_windows,
            states=states,
        )
        if not records:
            empty_dates.append(trade_date)
            continue
        output = pd.DataFrame.from_records(records)
        output = _with_cross_sectional_moneyflow_features(output)
        output = _with_industry_moneyflow_features(output)
        output = output.loc[:, [*FLOW_FEATURE_KEY_COLUMNS, *feature_columns]]
        output = output.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
        write_frame(
            _flow_partition_payload(output),
            data_dir / f"trade_date={trade_date}" / "part.parquet",
        )
        rows += int(len(output))
        files += 1
        symbols.update(output["symbol"].dropna().astype(str).tolist())
        feature_non_null_values += _non_null_feature_values(output, feature_columns)
        processed_dates.append(trade_date)

    totals = {
        "rows": rows,
        "symbols": len(symbols),
        "files": files,
        "trade_dates_processed": len(processed_dates),
        "trade_dates_empty": len(empty_dates),
        "feature_non_null_values": feature_non_null_values,
    }
    if rows < min_rows or len(symbols) < min_symbols:
        raise ValueError(f"flow_ownership_features asset is too small: {totals}")
    if feature_non_null_values <= 0:
        raise ValueError(
            "flow_ownership_features contains no non-null feature values; provide daily_dir "
            "for amount denominators and/or daily_basic_dir for float market value."
        )

    manifest = {
        "schema_version": "tushare.a_share.flow_ownership_features.v1",
        "dataset": "flow_ownership_features",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(output_dir),
        "generated_at": datetime.now(UTC).isoformat(),
        "query": {
            "start_date": processed_dates[0] if processed_dates else None,
            "end_date": processed_dates[-1] if processed_dates else None,
            "moneyflow_dir": str(Path(moneyflow_dir).expanduser().resolve()),
            "daily_dir": str(Path(daily_dir).expanduser().resolve()) if daily_dir else None,
            "daily_basic_dir": (
                str(Path(daily_basic_dir).expanduser().resolve()) if daily_basic_dir else None
            ),
            "industry_dir": (
                str(Path(industry_dir).expanduser().resolve()) if industry_dir else None
            ),
            "windows": list(normalized_windows),
            "partition_by": "trade_date",
        },
        "semantics": {
            "point_in_time": True,
            "available_date_column": "available_date",
            "available_date_rule": "same as trade_date for post-close moneyflow features",
            "amount_unit": "ten_thousand_cny",
            "daily_amount_conversion": "daily.amount divided by 10 when daily_dir supplies amount",
            "feature_columns": feature_columns,
            "industry_zscore": (
                "optional within-industry zscore by trade_date using industry_changes"
                if include_industry_features
                else None
            ),
        },
        "totals": totals,
        "processed_trade_dates": processed_dates,
        "empty_trade_dates": empty_dates,
    }
    write_manifest(output_dir / "manifest.yml", manifest)
    return manifest
