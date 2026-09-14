"""Build and validate the source-neutral A-share one-minute dataset."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from market_data_platform.standardize.fusion.a_share_minute import (
    CANONICAL_MINUTE_COLUMNS,
    CANONICAL_MINUTE_SCHEMA,
    LEGACY_GUAN_CANONICAL_UNITS,
    LEGACY_GUAN_HUNDRED_X_UNITS,
    MINUTE_KEY_COLUMNS,
    aggregate_guan_deal_file,
    fuse_minute_frames,
    normalize_legacy_guan_partition_with_stats,
    normalize_tushare_partition,
    write_canonical_minute_partition,
)

from .options import (
    _TS_CODE_PATTERN,
    MinuteFusionBuildOptions,
    _checkpoint_action_is_current,
    _expanded_path,
    _file_inventory,
    _in_range,
    _MinuteSourceInventory,
    _output_checkpoint_signature,
    _partition_path,
)


def _load_symbol_mapping(path: Path | None) -> tuple[dict[str, str], dict[str, Any]]:
    if path is None:
        return {}, {
            "source_path": None,
            "input_rows": 0,
            "dropped_invalid_rows": 0,
            "mapping_entries": 0,
        }
    parquet_file = pq.ParquetFile(path)
    required = {"symbol", "ts_code"}
    missing = required.difference(parquet_file.schema_arrow.names)
    if missing:
        raise ValueError(f"Instrument asset {path} is missing columns: {sorted(missing)}")
    frame = parquet_file.read(columns=["symbol", "ts_code"]).to_pandas()
    frame = frame.dropna(subset=["symbol", "ts_code"])
    input_rows = len(frame)
    frame["symbol"] = frame["symbol"].astype("string").str.strip().str.split(".", n=1).str[0]
    frame["ts_code"] = frame["ts_code"].astype("string").str.strip().str.upper()
    valid = frame["symbol"].str.fullmatch(r"\d{6}", na=False) & frame["ts_code"].str.fullmatch(
        r"\d{6}\.(?:SH|SZ|BJ)", na=False
    )
    frame = frame.loc[valid].drop_duplicates().copy()
    conflicts = frame.groupby("symbol", observed=True)["ts_code"].nunique().gt(1)
    if conflicts.any():
        examples = conflicts.index[conflicts].astype(str).tolist()[:3]
        raise ValueError(f"Instrument asset {path} has conflicting mappings: {examples}")
    mapping = dict(zip(frame["symbol"].astype(str), frame["ts_code"].astype(str), strict=False))
    return mapping, {
        "source_path": str(path),
        "input_rows": input_rows,
        "dropped_invalid_rows": input_rows - len(frame),
        "mapping_entries": len(mapping),
    }


def _empty_minute_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=pd.Index(CANONICAL_MINUTE_COLUMNS))


def _read_canonical_partition(path: Path) -> pd.DataFrame:
    if not path.exists():
        return _empty_minute_frame()
    return normalize_tushare_partition(path)


def _read_original_minute_source(
    date: str,
    options: MinuteFusionBuildOptions,
    legacy_sources: dict[str, Path],
) -> tuple[pd.DataFrame, str | None]:
    source = legacy_sources.get(date)
    if source is None:
        return _empty_minute_frame(), None
    if date <= options.legacy_guan_end_date:
        normalization = normalize_legacy_guan_partition_with_stats(
            source,
            unit_profile=_legacy_profile(date, options),
        )
        return normalization.frame, "guan_legacy"
    return normalize_tushare_partition(source), "tushare_existing"


def _legacy_profile(date: str, options: MinuteFusionBuildOptions):
    if options.hundred_x_start_date <= date <= options.hundred_x_end_date:
        return LEGACY_GUAN_HUNDRED_X_UNITS
    return LEGACY_GUAN_CANONICAL_UNITS


def _legacy_source_name(date: str, options: MinuteFusionBuildOptions) -> str:
    if date > options.legacy_guan_end_date:
        return "tushare_existing"
    return f"guan_legacy:{_legacy_profile(date, options).name}"


def _normalize_one_legacy(
    date: str,
    source: Path,
    options: MinuteFusionBuildOptions,
) -> dict[str, Any]:
    output_path = _partition_path(_expanded_path(options.output_dir), date)
    source_name = _legacy_source_name(date, options)
    if options.resume and output_path.exists():
        return {"date": date, "status": "skipped_existing", "source": source_name}
    if options.dry_run:
        return {"date": date, "status": "planned", "source": source_name}

    if date <= options.legacy_guan_end_date:
        profile = _legacy_profile(date, options)
        normalization = normalize_legacy_guan_partition_with_stats(
            source,
            unit_profile=profile,
        )
        frame = normalization.frame
        cleanup = normalization.stats.as_dict()
    else:
        frame = normalize_tushare_partition(source)
        cleanup = {
            "input_rows": len(frame),
            "dropped_empty_rows": 0,
            "repaired_ohlc_rows": 0,
            "zero_flow_rows": int((frame["vol"].eq(0) & frame["amount"].eq(0)).sum()),
            "output_rows": len(frame),
        }
    _validate_candidate_frame(date, frame, label=f"legacy minute candidate {date}")
    write_canonical_minute_partition(frame, output_path)
    return {
        "date": date,
        "status": "written",
        "source": source_name,
        "normalization": cleanup,
        "rows": len(frame),
        "symbols": int(frame["ts_code"].nunique()),
    }


def _normalize_legacy_inputs(
    options: MinuteFusionBuildOptions,
    *,
    sources: dict[str, Path],
) -> list[dict[str, Any]]:
    if not sources:
        return []
    if options.dry_run or options.legacy_workers == 1:
        return [_normalize_one_legacy(date, path, options) for date, path in sources.items()]

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=options.legacy_workers) as executor:
        futures = {
            executor.submit(_normalize_one_legacy, date, path, options): date
            for date, path in sources.items()
        }
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: str(item["date"]))


def _resume_action_is_current(
    resume_action: Mapping[str, Any] | None,
    *,
    date: str,
    source: Path,
    output_path: Path,
    whole_day_override: bool,
) -> bool:
    if resume_action is None:
        return False
    try:
        return _checkpoint_action_is_current(
            resume_action,
            date=date,
            source=source,
            output_path=output_path,
            whole_day_override=whole_day_override,
        )
    except (OSError, TypeError, ValueError):
        return False


def _merge_deal_inputs(
    options: MinuteFusionBuildOptions,
    symbol_mapping: dict[str, str],
    *,
    source_inventory: _MinuteSourceInventory,
    resume_actions: Mapping[str, Mapping[str, Any]] | None = None,
    checkpoint_action: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    output_root = _expanded_path(options.output_dir)
    for date, source in source_inventory.deal_sources.items():
        if not _in_range(date, options) or date < options.guan_deal_start_date:
            continue
        whole_day_override = date in source_inventory.override_guan_deal
        output_path = _partition_path(output_root, date)
        if options.dry_run:
            action: dict[str, Any] = {
                "date": date,
                "status": "planned",
                "source": "guan_deal",
            }
            if whole_day_override:
                action.update(
                    {
                        "replacement_policy": "explicit_whole_day_deal_only",
                        "replaced_source": "guan_annual_minbar",
                    }
                )
            results.append(action)
            continue
        resume_action = (resume_actions or {}).get(date)
        resume_is_current = _resume_action_is_current(
            resume_action,
            date=date,
            source=source,
            output_path=output_path,
            whole_day_override=whole_day_override,
        )
        if resume_action is not None and resume_is_current:
            reused_action = dict(resume_action)
            reused_action["checkpoint_status"] = "reused"
            results.append(reused_action)
            continue
        aggregation = aggregate_guan_deal_file(
            source,
            symbol_to_ts_code=symbol_mapping,
            trade_date=date,
            engine=options.deal_engine,
            batch_row_groups=options.deal_batch_row_groups,
        )
        if whole_day_override:
            candidate = aggregation.frame
            fusion_priority = ["guan_deal"]
            fusion_stats_slots = None
            fusion_stats = None
            session_validation = _validate_deal_override_session(date, candidate)
        else:
            original, original_source = _read_original_minute_source(
                date,
                options,
                source_inventory.legacy,
            )
            if original_source == "guan_legacy":
                fused = fuse_minute_frames(original, aggregation.frame)
                fusion_priority = ["guan_deal", "guan_legacy"]
                fusion_stats_slots = {"guan": "guan_legacy", "tushare": "guan_deal"}
            else:
                fused = fuse_minute_frames(aggregation.frame, original)
                fusion_priority = (
                    [original_source, "guan_deal"] if original_source else ["guan_deal"]
                )
                fusion_stats_slots = {
                    "guan": "guan_deal",
                    "tushare": original_source or "empty",
                }
            candidate = fused.frame
            fusion_stats = fused.stats.as_dict()
            session_validation = None
        _validate_candidate_frame(date, candidate, label=f"Guan deal candidate {date}")
        write_canonical_minute_partition(candidate, output_path)
        action = {
            "date": date,
            "status": "written",
            "source": "guan_deal",
            "aggregation": aggregation.stats.as_dict(),
            "fusion_priority": fusion_priority,
            "fusion_stats_slots": fusion_stats_slots,
            "fusion": fusion_stats,
            "whole_day_annual_override": whole_day_override,
            "source_signature": _file_inventory(source),
            "output_signature": _output_checkpoint_signature(output_path),
            "checkpoint_status": "written",
        }
        if whole_day_override:
            action.update(
                {
                    "replacement_policy": "explicit_whole_day_deal_only",
                    "replaced_source": "guan_annual_minbar",
                    "session_validation": session_validation,
                }
            )
        if checkpoint_action is not None:
            checkpoint_action(action)
        results.append(action)
    return results


def _load_tushare_batch_group(paths: list[Path]) -> pd.DataFrame:
    frames = [pq.ParquetFile(path).read().to_pandas() for path in paths]
    if not frames:
        return _empty_minute_frame()
    return normalize_tushare_partition(pd.concat(frames, ignore_index=True))


def _merge_tushare_inputs(
    options: MinuteFusionBuildOptions,
    *,
    rebuilt_deal_dates: set[str],
    sources: dict[str, list[Path]],
    legacy_sources: dict[str, Path],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    output_root = _expanded_path(options.output_dir)
    for date, paths in sources.items():
        if not _in_range(date, options):
            continue
        if options.dry_run:
            results.append(
                {
                    "date": date,
                    "status": "planned",
                    "source": "tushare_batches",
                    "files": len(paths),
                }
            )
            continue
        tushare = _load_tushare_batch_group(paths)
        output_path = _partition_path(output_root, date)
        if date in rebuilt_deal_dates:
            lower_priority = _read_canonical_partition(output_path)
        else:
            lower_priority, _ = _read_original_minute_source(
                date,
                options,
                legacy_sources,
            )
        fused = fuse_minute_frames(lower_priority, tushare)
        _validate_candidate_frame(date, fused.frame, label=f"TuShare candidate {date}")
        write_canonical_minute_partition(fused.frame, output_path)
        results.append(
            {
                "date": date,
                "status": "written",
                "source": "tushare_batches",
                "files": len(paths),
                "fusion": fused.stats.as_dict(),
            }
        )
    return results


def _frame_validation_issues(date: str, frame: pd.DataFrame) -> dict[str, int]:
    duplicate_rows = int(frame.duplicated(list(MINUTE_KEY_COLUMNS)).sum())
    null_rows = int(frame.loc[:, list(CANONICAL_MINUTE_COLUMNS)].isna().any(axis=1).sum())
    numeric = frame.loc[:, list(CANONICAL_MINUTE_COLUMNS[2:])].apply(
        pd.to_numeric,
        errors="coerce",
    )
    numeric_values = numeric.to_numpy(dtype="float64", na_value=np.nan)
    non_finite_rows = int((~np.isfinite(numeric_values)).any(axis=1).sum())
    invalid_ohlc = int(
        (
            (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1))
            | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1))
        ).sum()
    )
    negative_flow = int(((numeric["vol"] < 0) | (numeric["amount"] < 0)).sum())

    trade_time = pd.to_datetime(frame["trade_time"], errors="coerce")
    valid_time = trade_time.notna()
    wrong_date_rows = int((valid_time & trade_time.dt.strftime("%Y%m%d").ne(date)).sum())
    non_minute_rows = int(
        (
            valid_time
            & (
                trade_time.dt.second.ne(0)
                | trade_time.dt.microsecond.ne(0)
                | trade_time.dt.nanosecond.ne(0)
            )
        ).sum()
    )
    minute_of_day = trade_time.dt.hour * 60 + trade_time.dt.minute
    in_session = minute_of_day.between(9 * 60 + 30, 11 * 60 + 30) | minute_of_day.between(
        13 * 60 + 1,
        15 * 60,
    )
    off_session_rows = int((valid_time & ~in_session).sum())
    invalid_ts_code_rows = int(
        (~frame["ts_code"].astype("string").str.fullmatch(_TS_CODE_PATTERN, na=False)).sum()
    )
    return {
        "empty_partition": int(frame.empty),
        "duplicate_rows": duplicate_rows,
        "null_rows": null_rows,
        "non_finite_rows": non_finite_rows,
        "invalid_ohlc_rows": invalid_ohlc,
        "negative_flow_rows": negative_flow,
        "wrong_date_rows": wrong_date_rows,
        "invalid_ts_code_rows": invalid_ts_code_rows,
        "non_minute_rows": non_minute_rows,
        "off_session_rows": off_session_rows,
    }


def _validate_candidate_frame(date: str, frame: pd.DataFrame, *, label: str) -> None:
    issues = _frame_validation_issues(date, frame)
    failures = {name: count for name, count in issues.items() if count}
    if failures:
        raise ValueError(f"{label} failed canonical validation: {failures}")


def _validate_deal_override_session(date: str, frame: pd.DataFrame) -> dict[str, Any]:
    trade_time = pd.to_datetime(frame["trade_time"], errors="coerce")
    expected_min = pd.to_datetime(f"{date} 09:30:00", format="%Y%m%d %H:%M:%S")
    expected_max = pd.to_datetime(f"{date} 15:00:00", format="%Y%m%d %H:%M:%S")
    time_min = trade_time.min() if not frame.empty else pd.NaT
    time_max = trade_time.max() if not frame.empty else pd.NaT
    opening = frame.loc[trade_time.eq(expected_min)]
    closing = frame.loc[trade_time.eq(expected_max)]
    issues = {
        "unexpected_market_time_min": int(
            cast(bool, bool(pd.isna(time_min)) or time_min != expected_min)
        ),
        "unexpected_market_time_max": int(
            cast(bool, bool(pd.isna(time_max)) or time_max != expected_max)
        ),
        "missing_opening_execution_bar": int(opening.empty),
        "missing_closing_execution_bar": int(closing.empty),
    }
    failures = {name: value for name, value in issues.items() if value}
    if failures:
        raise ValueError(
            f"Guan deal whole-day annual override {date} failed session validation: "
            f"{failures}, time_min={time_min}, time_max={time_max}"
        )
    return {
        "profile": "guan_deal_full_session",
        "time_min": str(time_min),
        "time_max": str(time_max),
        "opening_bar_rows": len(opening),
        "opening_bar_symbols": int(opening["ts_code"].nunique()),
        "closing_bar_rows": len(closing),
        "closing_bar_symbols": int(closing["ts_code"].nunique()),
        "issues": issues,
        "valid": True,
    }


def _validate_partition(date: str, path: Path) -> dict[str, Any]:
    schema = pq.ParquetFile(path).schema_arrow
    if not schema.equals(CANONICAL_MINUTE_SCHEMA):
        raise ValueError(f"Canonical schema mismatch for {path}: {schema}")
    frame = pq.ParquetFile(path).read().to_pandas()
    issues = _frame_validation_issues(date, frame)
    return {
        "date": date,
        "path": str(path),
        "rows": len(frame),
        "symbols": int(frame["ts_code"].nunique()),
        "time_min": str(frame["trade_time"].min()) if not frame.empty else None,
        "time_max": str(frame["trade_time"].max()) if not frame.empty else None,
        "issues": issues,
        "valid": not any(issues.values()),
    }


def _validate_prevalidated_partition(date: str, path: Path) -> dict[str, Any]:
    parquet_file = pq.ParquetFile(path)
    schema = parquet_file.schema_arrow
    if not schema.equals(CANONICAL_MINUTE_SCHEMA):
        raise ValueError(f"Canonical schema mismatch for {path}: {schema}")
    rows = int(parquet_file.metadata.num_rows)
    issues = {
        "empty_partition": int(rows == 0),
        "duplicate_rows": 0,
        "null_rows": 0,
        "non_finite_rows": 0,
        "invalid_ohlc_rows": 0,
        "negative_flow_rows": 0,
        "wrong_date_rows": 0,
        "invalid_ts_code_rows": 0,
        "non_minute_rows": 0,
        "off_session_rows": 0,
    }
    return {
        "date": date,
        "path": str(path),
        "rows": rows,
        "symbols": None,
        "time_min": None,
        "time_max": None,
        "content_validation": "candidate_prevalidated",
        "issues": issues,
        "valid": not any(issues.values()),
    }
