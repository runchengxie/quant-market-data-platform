"""Partition materialization workers for the canonical A-share minute dataset."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from market_data_platform.quality_a_share_minute import (
    validate_guan_deal_override_session,
    validate_minute_candidate_frame,
)
from market_data_platform.standardize.fusion.a_share_minute import (
    CANONICAL_MINUTE_COLUMNS,
    LEGACY_GUAN_CANONICAL_UNITS,
    LEGACY_GUAN_HUNDRED_X_UNITS,
    GuanDealAggregationResult,
    fuse_minute_frames,
    normalize_legacy_guan_partition_with_stats,
    normalize_tushare_partition,
    write_canonical_minute_partition,
)

from .checkpoint import _checkpoint_action_is_current, _output_checkpoint_signature
from .inventory import _file_inventory, _MinuteSourceInventory, _partition_path
from .options import MinuteFusionBuildOptions, _expanded_path, _in_range

AggregateDeal = Callable[..., GuanDealAggregationResult]


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
        normalization = normalize_legacy_guan_partition_with_stats(source, unit_profile=profile)
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
    validate_minute_candidate_frame(date, frame, label=f"legacy minute candidate {date}")
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


def _prepare_deal_candidate(
    date: str,
    frame: pd.DataFrame,
    options: MinuteFusionBuildOptions,
    inventory: _MinuteSourceInventory,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if date in inventory.override_guan_deal:
        return frame, {
            "fusion_priority": ["guan_deal"],
            "fusion_stats_slots": None,
            "fusion": None,
            "replacement_policy": "explicit_whole_day_deal_only",
            "replaced_source": "guan_annual_minbar",
            "session_validation": validate_guan_deal_override_session(date, frame),
        }
    original, original_source = _read_original_minute_source(date, options, inventory.legacy)
    if original_source == "guan_legacy":
        fused = fuse_minute_frames(original, frame)
        priority = ["guan_deal", "guan_legacy"]
        slots = {"guan": "guan_legacy", "tushare": "guan_deal"}
    else:
        fused = fuse_minute_frames(frame, original)
        priority = [original_source, "guan_deal"] if original_source else ["guan_deal"]
        slots = {"guan": "guan_deal", "tushare": original_source or "empty"}
    return fused.frame, {
        "fusion_priority": priority,
        "fusion_stats_slots": slots,
        "fusion": fused.stats.as_dict(),
    }


def _merge_deal_inputs(  # noqa: PLR0913
    options: MinuteFusionBuildOptions,
    symbol_mapping: dict[str, str],
    *,
    source_inventory: _MinuteSourceInventory,
    aggregate_deal: AggregateDeal,
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
        if resume_action is not None and _resume_action_is_current(
            resume_action,
            date=date,
            source=source,
            output_path=output_path,
            whole_day_override=whole_day_override,
        ):
            reused_action = dict(resume_action)
            reused_action["checkpoint_status"] = "reused"
            results.append(reused_action)
            continue

        aggregation = aggregate_deal(
            source,
            symbol_to_ts_code=symbol_mapping,
            trade_date=date,
            engine=options.deal_engine,
            batch_row_groups=options.deal_batch_row_groups,
        )
        candidate, fusion_details = _prepare_deal_candidate(
            date,
            aggregation.frame,
            options,
            source_inventory,
        )
        validate_minute_candidate_frame(date, candidate, label=f"Guan deal candidate {date}")
        write_canonical_minute_partition(candidate, output_path)
        action = {
            "date": date,
            "status": "written",
            "source": "guan_deal",
            "aggregation": aggregation.stats.as_dict(),
            **fusion_details,
            "whole_day_annual_override": whole_day_override,
            "source_signature": _file_inventory(source),
            "output_signature": _output_checkpoint_signature(output_path),
            "checkpoint_status": "written",
        }
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
            lower_priority, _ = _read_original_minute_source(date, options, legacy_sources)
        fused = fuse_minute_frames(lower_priority, tushare)
        validate_minute_candidate_frame(date, fused.frame, label=f"TuShare candidate {date}")
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
