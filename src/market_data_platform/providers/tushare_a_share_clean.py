"""Compatibility facade for TuShare A-share daily clean and validation entry points."""

from pathlib import Path
from typing import Any

import pandas as pd

from market_data_platform.runtime_memory import (
    DEFAULT_MEMORY_HARD_AVAILABLE_MB,
    DEFAULT_MEMORY_SOFT_AVAILABLE_MB,
)
from market_data_platform.standardize.tushare.a_share_daily_part01 import (
    DEFAULT_DAILY_CLEAN_BATCH_TRADE_DATES,
    _add_limit_flags,
    _board_from_symbol,
    _DailyCleanBuildRuntime,
    _DailyCleanInputs,
    _DailyCleanManifestRequest,
    _DailyCleanStats,
    _DailyCleanTradeDateFrameInputs,
    _derive_is_suspended,
    _derive_st_flag,
    _effective_list_dates,
    _fill_missing_pre_close,
    _latest_adj_factors,
    _load_instruments,
    _load_suspension_trade_date_frame,
    _merge_adjustment_columns_for_trade_date,
    _merge_overlay_frame,
    _normalize_suspension_columns,
    _normalize_trade_date,
    _prepare_daily_frame,
    _prepare_index_frame,
    _prepare_output_dirs,
    _read_trade_date_part,
    _safe_numeric,
    _trade_date_part_map,
    _update_daily_clean_stats,
    _update_first_trade_dates,
    _valid_trade_date_series,
    _write_daily_clean_staging_batch,
)
from market_data_platform.standardize.tushare.a_share_daily_part02 import (
    _add_instrument_columns_frame,
    _build_daily_clean_manifest,
    _build_daily_clean_trade_date_frame,
    _check_daily_clean_size,
    _compact_daily_clean_staging,
    _empty_compaction_telemetry,
    _merge_compaction_telemetry,
    _record_memory_sample,
    _resolved_optional_path,
    build_a_share_daily_clean,
)


def _read_parquet_parts(asset_dir: str | Path, *, label: str) -> pd.DataFrame:
    root = Path(asset_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"{label} asset directory not found: {root}")
    data_root = root / "data" if (root / "data").exists() else root
    files = sorted(data_root.glob("**/*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(path) for path in files]
    frames = [frame for frame in frames if frame is not None and not frame.empty]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def validate_a_share_daily_clean(  # noqa: PLR0913
    *,
    daily_clean_dir: str | Path,
    min_rows: int = 1,
    min_symbols: int = 1,
    require_valuation: bool = False,
    require_limit_status: bool = False,
    profile: str = "baseline",
    trade_cal_file: str | Path | None = None,
    fail_on_severity: str = "error",
    max_warning_rate: float = 0.0,
    pct_chg_tolerance: float = 0.05,
    batch_rows: int = 65536,
    memory_soft_limit_mb: float | None = DEFAULT_MEMORY_SOFT_AVAILABLE_MB,
    memory_hard_limit_mb: float | None = DEFAULT_MEMORY_HARD_AVAILABLE_MB,
    out: str | Path | None = None,
) -> dict[str, Any]:
    from market_data_platform.providers import tushare_a_share_quality

    return tushare_a_share_quality.validate_a_share_daily_clean(
        tushare_a_share_quality.DailyCleanValidationOptions.from_legacy_kwargs(
            daily_clean_dir=daily_clean_dir,
            min_rows=min_rows,
            min_symbols=min_symbols,
            require_valuation=require_valuation,
            require_limit_status=require_limit_status,
            profile=profile,
            trade_cal_file=trade_cal_file,
            fail_on_severity=fail_on_severity,
            max_warning_rate=max_warning_rate,
            pct_chg_tolerance=pct_chg_tolerance,
            batch_rows=batch_rows,
            memory_soft_limit_mb=memory_soft_limit_mb,
            memory_hard_limit_mb=memory_hard_limit_mb,
            out=out,
        )
    )
