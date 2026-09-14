"""Stateful TuShare A-share fundamentals ingestion and PIT publication."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from market_data_platform.parquet_scanning import ParquetBatchScanner
from market_data_platform.providers.tushare_a_share_fundamentals_part02 import (
    _coalesce_pit_frame,
    _empty_pit_frame,
    _write_parquet_part,
)
from market_data_platform.providers.tushare_a_share_fundamentals_part03 import (
    PitBuildContext,
    PitBuildOptions,
    PitOutputStats,
    _field_mappings,
    _pit_output_columns,
    _pit_projected_columns,
    _pit_telemetry,
    _require_revision_safe_normalized_sources,
    _reset_pit_output_dirs,
    _source_bundle_observation_ladder,
    _source_observed_vintages,
    _validate_pit_build_options,
    _validate_pit_source_columns,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitAsOfPanel as PitAsOfPanel,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitAsOfView as PitAsOfView,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    PitFundamentalsEvents as PitFundamentalsEvents,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of as load_pit_fundamentals_as_of,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of_panel as load_pit_fundamentals_as_of_panel,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_as_of_view as load_pit_fundamentals_as_of_view,
)
from market_data_platform.providers.tushare_a_share_fundamentals_pit import (
    load_pit_fundamentals_events as load_pit_fundamentals_events,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    PIT_SOURCE_COLUMNS,
    FieldValidationError,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    add_days as _add_days,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    asset_manifest_payload as _asset_manifest_payload,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    asset_parquet_files as _asset_parquet_files,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    build_asset_integrity as _build_asset_integrity,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    bundle_observation_state as _bundle_observation_state,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    date_token as _date_token,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    file_sha256 as _file_sha256,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    require_mutable_asset_output as _require_mutable_asset_output,
)
from market_data_platform.providers.tushare_a_share_fundamentals_support import (
    seal_manifest as _seal_manifest,
)
from market_data_platform.providers.tushare_common import (
    pandas,
    write_manifest,
)
from market_data_platform.runtime_memory import MemoryPolicy


def _pit_build_context(options: PitBuildOptions) -> PitBuildContext:
    mappings = _field_mappings(options.field_mappings)
    _validate_pit_build_options(
        available_delay_days=options.available_delay_days,
        max_observation_age_days=options.max_observation_age_days,
        bucket_count=options.bucket_count,
        mappings=mappings,
    )
    sources = [Path(path).expanduser().resolve() for path in options.normalized_dirs]
    source_manifests = {str(source): _asset_manifest_payload(source) for source in sources}
    _require_revision_safe_normalized_sources(sources, source_manifests)
    source_observation = {
        source: manifest.get("source_observation", {})
        for source, manifest in source_manifests.items()
    }
    source_observed_vintage_dates = _source_observed_vintages(sources, source_manifests)
    source_bundle_observations = _source_bundle_observation_ladder(sources, source_manifests)
    source_retrieved_at = {
        source: manifest.get("source_retrieved_at", [])
        for source, manifest in source_manifests.items()
    }
    latest_source_dates = [
        max(values) for values in source_observed_vintage_dates.values() if values
    ]
    latest_target = max(latest_source_dates) if latest_source_dates else ""
    latest_observation_state = _bundle_observation_state(
        source_observed_vintage_dates,
        as_of_date=latest_target,
        max_age_days=options.max_observation_age_days,
        source_bundle_observations=source_bundle_observations,
    )
    oldest = latest_observation_state["oldest_component_retrieval_date"]
    bundle_available = latest_observation_state["bundle_available_date"]
    valid_through = _add_days(oldest, options.max_observation_age_days) if oldest else None
    freshness_valid_through_date = (
        valid_through
        if valid_through and bundle_available and valid_through >= bundle_available
        else None
    )
    files = [path for source in sources for path in _asset_parquet_files(source)]
    if not files:
        raise FileNotFoundError("No normalized fundamentals parquet files found.")
    _validate_pit_source_columns(files, mappings)
    resolved_memory_policy = options.memory_policy or MemoryPolicy()
    output = Path(options.out_dir).expanduser().resolve()
    _require_mutable_asset_output(output)
    staging, data_dir, quarantine_dir = _reset_pit_output_dirs(output)
    return PitBuildContext(
        sources=sources,
        files=files,
        output=output,
        staging=staging,
        data_dir=data_dir,
        quarantine_dir=quarantine_dir,
        mappings=mappings,
        value_columns=list(mappings.values()),
        projected_columns=_pit_projected_columns(mappings),
        output_columns=_pit_output_columns(mappings),
        available_delay_days=options.available_delay_days,
        bucket_count=options.bucket_count,
        batch_rows=options.batch_rows,
        memory_policy=resolved_memory_policy,
        telemetry=_pit_telemetry(
            files=files,
            bucket_count=options.bucket_count,
            batch_rows=options.batch_rows,
            memory_policy=resolved_memory_policy,
        ),
        source_observation=source_observation,
        source_observed_vintage_dates=source_observed_vintage_dates,
        source_bundle_observations=source_bundle_observations,
        source_retrieved_at=source_retrieved_at,
        latest_observation_state=latest_observation_state,
        freshness_valid_through_date=freshness_valid_through_date,
        max_observation_age_days=options.max_observation_age_days,
    )


def _ensure_pit_batch_columns(batch: Any, mappings: Mapping[str, str]) -> None:
    pd = pandas()
    for column in ("symbol", "report_period", "disclosure_date"):
        if column not in batch.columns:
            raise FieldValidationError(f"Normalized fundamentals are missing PIT column: {column}")
    for column in PIT_SOURCE_COLUMNS:
        if column not in batch.columns:
            batch[column] = ""
    for column in mappings:
        if column not in batch.columns:
            batch[column] = pd.NA


def _write_pit_quarantine(
    *,
    context: PitBuildContext,
    frame: Any,
    reason: str,
    quarantine_index: int,
) -> int:
    if frame.empty:
        return quarantine_index
    quarantine_path = context.quarantine_dir / f"{reason}-{quarantine_index:06d}.parquet"
    _write_parquet_part(frame, quarantine_path)
    context.telemetry["quarantined_rows"] += int(len(frame))
    return quarantine_index + 1


def _select_usable_pit_rows(
    context: PitBuildContext,
    batch: Any,
    quarantine_index: int,
) -> tuple[Any, int]:
    pd = pandas()
    usable = (batch["report_period"].map(_date_token).str.len() == 8) & (
        batch["disclosure_date"].map(_date_token).str.len() == 8
    )
    quarantine_index = _write_pit_quarantine(
        context=context,
        frame=batch.loc[~usable].copy(),
        reason="missing_disclosure_semantics",
        quarantine_index=quarantine_index,
    )
    selected = batch.loc[usable].copy()
    if selected.empty:
        return selected, quarantine_index

    selected["report_period"] = selected["report_period"].map(_date_token)
    selected["disclosure_date"] = selected["disclosure_date"].map(_date_token)
    disclosure = pd.to_datetime(
        selected["disclosure_date"],
        format="%Y%m%d",
        errors="coerce",
    )
    selected["available_date"] = (
        disclosure + timedelta(days=context.available_delay_days)
    ).dt.strftime("%Y%m%d")
    selected["trade_date"] = selected["available_date"]
    report_period = pd.to_datetime(
        selected["report_period"],
        format="%Y%m%d",
        errors="coerce",
    )
    invalid_order = disclosure < report_period
    invalid_order_rows = selected.loc[invalid_order].copy()
    previous_quarantine_index = quarantine_index
    quarantine_index = _write_pit_quarantine(
        context=context,
        frame=invalid_order_rows,
        reason="invalid_disclosure_order",
        quarantine_index=quarantine_index,
    )
    if quarantine_index != previous_quarantine_index:
        context.telemetry["invalid_disclosure_order_rows"] += int(len(invalid_order_rows))
    return selected.loc[~invalid_order].copy(), quarantine_index


def _write_pit_staging_buckets(
    context: PitBuildContext,
    result: Any,
    staging_index: int,
) -> int:
    pd = pandas()
    bucket_values = (
        pd.util.hash_pandas_object(result["symbol"].astype(str), index=False)
        .astype("uint64")
        .mod(context.bucket_count)
    )
    result["_pit_bucket"] = bucket_values.astype(int)
    for bucket, bucket_frame in result.groupby("_pit_bucket", sort=False):
        bucket_id = int(bucket)
        part = context.staging / f"bucket={bucket_id:04d}" / f"part-{staging_index:08d}.parquet"
        _write_parquet_part(bucket_frame.drop(columns=["_pit_bucket"]), part)
        staging_index += 1
        context.telemetry["staging_files"] += 1
    return staging_index


def _stage_pit_batches(context: PitBuildContext) -> ParquetBatchScanner:
    scanner = ParquetBatchScanner(
        columns=context.projected_columns,
        batch_rows=context.batch_rows,
        memory_policy=context.memory_policy,
        stage="pit_fundamentals_build",
    )
    staging_index = 0
    quarantine_index = 0
    for _, batch in scanner.iter_frames(context.files):
        context.telemetry["input_batches"] += 1
        context.telemetry["input_rows"] += int(len(batch))
        if batch.empty:
            continue
        _ensure_pit_batch_columns(batch, context.mappings)
        selected, quarantine_index = _select_usable_pit_rows(
            context,
            batch,
            quarantine_index,
        )
        if selected.empty:
            continue
        result = selected.loc[:, context.output_columns].rename(columns=context.mappings)
        result = result.dropna(subset=["symbol", "trade_date"])
        if result.empty:
            continue
        context.telemetry["usable_rows"] += int(len(result))
        staging_index = _write_pit_staging_buckets(context, result, staging_index)
    return scanner


def _pit_keep_columns() -> tuple[str, ...]:
    return (
        "symbol",
        "trade_date",
        "report_period",
        "disclosure_date",
        "available_date",
        "_source_dataset",
        "_source_raw_asset",
        "_source_run_id",
        "_source_retrieved_at",
        "_source_bundle_retrieval_start_date",
        "_source_bundle_available_date",
    )


def _load_pit_bucket_frame(
    paths: Sequence[Path],
    keep_columns: Sequence[str],
    value_columns: Sequence[str],
) -> Any:
    pd = pandas()
    frames = []
    keep_set = set(keep_columns)
    for path in paths:
        bucket_part = pd.read_parquet(path)
        drop_columns = [
            column
            for column in bucket_part.columns
            if column not in keep_set and bucket_part[column].isna().all()
        ]
        frames.append(bucket_part.drop(columns=drop_columns))
    frame = (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else _empty_pit_frame(value_columns)
    )
    for column in [*keep_columns, *value_columns]:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame


def _record_pit_bucket_stats(
    *,
    context: PitBuildContext,
    stats: PitOutputStats,
    bucket_name: str,
    result: Any,
) -> None:
    stats.rows += int(len(result))
    stats.symbols.update(result["symbol"].dropna().astype(str).unique().tolist())
    date_tokens = result["trade_date"].map(_date_token)
    date_tokens = date_tokens[date_tokens.str.len() == 8]
    if not date_tokens.empty:
        bucket_start = str(date_tokens.min())
        bucket_end = str(date_tokens.max())
        stats.query_start_date = (
            bucket_start
            if stats.query_start_date is None
            else min(stats.query_start_date, bucket_start)
        )
        stats.query_end_date = (
            bucket_end if stats.query_end_date is None else max(stats.query_end_date, bucket_end)
        )
    if len(context.telemetry["memory_samples"]) < 40:
        context.telemetry["memory_samples"].append(
            context.memory_policy.require_safe(
                label=f"pit_fundamentals wrote {bucket_name}"
            ).to_dict()
            | {"bucket": bucket_name, "rows": int(len(result))}
        )


def _write_coalesced_pit_bucket(
    context: PitBuildContext,
    bucket_dir: Path,
    keep_columns: Sequence[str],
) -> Any:
    context.memory_policy.require_safe(label=f"pit_fundamentals coalesce {bucket_dir.name}")
    paths = sorted(bucket_dir.glob("*.parquet"))
    if not paths:
        return _empty_pit_frame(context.value_columns)
    frame = _load_pit_bucket_frame(paths, keep_columns, context.value_columns)
    result = _coalesce_pit_frame(frame, context.value_columns)
    if result.empty:
        return result
    part = context.data_dir / bucket_dir.name / "part.parquet"
    _write_parquet_part(result, part)
    context.telemetry["output_files"] += 1
    return result


def _coalesce_pit_buckets(context: PitBuildContext) -> PitOutputStats:
    stats = PitOutputStats()
    keep_columns = _pit_keep_columns()
    for bucket_dir in sorted(context.staging.glob("bucket=*")):
        result = _write_coalesced_pit_bucket(context, bucket_dir, keep_columns)
        if result.empty:
            continue
        _record_pit_bucket_stats(
            context=context,
            stats=stats,
            bucket_name=bucket_dir.name,
            result=result,
        )
    return stats


def _ensure_pit_output_file(context: PitBuildContext, stats: PitOutputStats) -> None:
    if stats.rows > 0:
        return
    _write_parquet_part(
        _empty_pit_frame(context.value_columns),
        context.data_dir / "bucket=0000" / "part.parquet",
    )
    context.telemetry["output_files"] += 1


def _pit_manifest(context: PitBuildContext, stats: PitOutputStats) -> dict[str, Any]:
    source_integrity = {
        str(source): {
            "manifest_sha256": _file_sha256(source / "manifest.yml"),
            "content_aggregate_sha256": _asset_manifest_payload(source)["integrity"][
                "aggregate_sha256"
            ],
        }
        for source in context.sources
    }
    output_files = sorted(context.output.glob("**/*.parquet"))
    return {
        "schema_version": "tushare.a_share.fundamentals.pit.v2",
        "dataset": "pit_fundamentals",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "immutable_snapshot": True,
        "output_dir": str(context.output),
        "source_normalized_dirs": [str(source) for source in context.sources],
        "source_integrity": source_integrity,
        "source_observation": context.source_observation,
        "source_observed_vintage_dates": context.source_observed_vintage_dates,
        "source_bundle_observations": context.source_bundle_observations,
        "source_retrieved_at": context.source_retrieved_at,
        "observed_vintage_dates": sorted(
            {value for values in context.source_observed_vintage_dates.values() for value in values}
        ),
        "latest_observation_state": context.latest_observation_state,
        "bundle_available_date": context.latest_observation_state["bundle_available_date"],
        "oldest_component_retrieval_date": context.latest_observation_state[
            "oldest_component_retrieval_date"
        ],
        "freshness_policy": {
            "max_observation_age_days": context.max_observation_age_days,
        },
        "freshness_valid_through_date": context.freshness_valid_through_date,
        "as_of_date": context.freshness_valid_through_date,
        "integrity": _build_asset_integrity(context.output, output_files),
        "validity": {
            "observed_from": context.latest_observation_state["bundle_available_date"],
            "freshness_valid_through": context.freshness_valid_through_date,
        },
        "revision_safety": {
            "observation_class": "observed_vintage_pit_v2",
            "revision_safe_from": context.latest_observation_state["bundle_available_date"],
            "historical_periods_before_first_observation": "reconstructed_pit",
        },
        "query_start_date": stats.query_start_date,
        "query_end_date": stats.query_end_date,
        "query": {
            "start_date": stats.query_start_date,
            "end_date": stats.query_end_date,
        },
        "coverage": {
            "event_available_start_date": stats.query_start_date,
            "event_available_end_date": stats.query_end_date,
            "bundle_available_date": context.latest_observation_state["bundle_available_date"],
            "freshness_valid_through_date": context.freshness_valid_through_date,
            "rows": stats.rows,
            "symbols": len(stats.symbols),
        },
        "semantics": {
            "point_in_time": True,
            "availability_delay_days": context.available_delay_days,
            "field_mappings": context.mappings,
            "quarantine_missing_disclosure_semantics": True,
            "event_key": [
                "symbol",
                "trade_date",
                "report_period",
                "_source_retrieved_at",
            ],
            "coalesced_by_symbol_trade_date_report_period_retrieval": True,
            "bucketed_parquet_dataset": True,
            "calendar_as_of_semantics": (
                "available_date is disclosure_date plus calendar delay days; an event on a "
                "non-trading date is visible to the first requested as_of_date on or after it"
            ),
            "trade_date_compatibility_alias": (
                "trade_date equals available_date and is not guaranteed to be an exchange session"
            ),
            "source_observation_provenance": "_source_retrieved_at",
            "freshness_semantics": "latest_component_vintage_at_or_before_as_of",
            "query_range_semantics": "event_available_date_not_freshness",
            "ambiguous_same_day_revisions": "fail_closed",
        },
        "totals": {
            "rows": stats.rows,
            "symbols": len(stats.symbols),
            "files": int(context.telemetry["output_files"]),
            "quarantined_rows": int(context.telemetry["quarantined_rows"]),
        },
        "runtime": context.telemetry,
    }


def build_pit_fundamentals(
    options: PitBuildOptions,
) -> dict[str, Any]:
    context = _pit_build_context(options)
    scanner = _stage_pit_batches(context)
    context.telemetry["scan"] = scanner.telemetry.to_dict()
    stats = _coalesce_pit_buckets(context)
    shutil.rmtree(context.staging, ignore_errors=True)
    _ensure_pit_output_file(context, stats)
    manifest = _pit_manifest(context, stats)
    manifest_path = context.output / "manifest.yml"
    write_manifest(manifest_path, manifest)
    _seal_manifest(manifest_path)
    return manifest


def _replace_symlink(alias: Path, target: Path) -> None:
    alias.parent.mkdir(parents=True, exist_ok=True)
    if alias.exists() and not alias.is_symlink():
        raise FileExistsError(f"Refusing to replace non-symlink latest alias: {alias}")
    if alias.is_symlink():
        alias.unlink()
    alias.symlink_to(os.path.relpath(target, alias.parent))


def _require_fundamentals_validation(
    validation: Mapping[str, Any],
    *,
    label: str,
) -> None:
    if validation.get("status") == "passed":
        return
    failed = [
        str(row.get("id"))
        for row in validation.get("checks", [])
        if isinstance(row, Mapping) and not row.get("passed")
    ]
    raise ValueError(f"{label} validation failed: {', '.join(failed) or 'unknown check'}.")
