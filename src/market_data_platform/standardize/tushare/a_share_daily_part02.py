from __future__ import annotations

import gc
import shutil
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.parquet_scanning import ParquetBatchScanner
from market_data_platform.runtime_memory import (
    DEFAULT_MEMORY_HARD_AVAILABLE_MB,
    DEFAULT_MEMORY_SOFT_AVAILABLE_MB,
    MemoryPolicy,
    MemorySnapshot,
    read_memory_snapshot,
)
from market_data_platform.standardize._manifest import write_manifest
from market_data_platform.standardize.schema.a_share_daily import (
    LIMIT_COLUMNS,
    PRICE_COLUMNS,
    VALUATION_COLUMNS,
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
    _prepare_output_dirs,
    _read_trade_date_part,
    _safe_numeric,
    _trade_date_part_map,
    _update_daily_clean_stats,
    _update_first_trade_dates,
    _write_daily_clean_staging_batch,
)


def _compact_daily_clean_staging(
    *,
    staging_dir: Path,
    data_dir: Path,
    memory_policy: MemoryPolicy,
) -> _DailyCleanStats:
    stats = _DailyCleanStats()
    telemetry = _empty_compaction_telemetry(memory_policy)
    for symbol_index, symbol_dir in enumerate(sorted(staging_dir.glob("symbol=*")), start=1):
        memory_policy.require_safe(label=f"daily_clean compact {symbol_dir.name}")
        symbol = symbol_dir.name.removeprefix("symbol=")
        part_files = sorted(symbol_dir.glob("part_*.parquet"))
        if not part_files:
            continue
        schemas = [pq.ParquetFile(path).schema_arrow for path in part_files]
        schema = pa.unify_schemas(schemas, promote_options="permissive")
        scanner = ParquetBatchScanner(
            columns=schema.names,
            memory_policy=memory_policy,
            stage=f"daily_clean_compact:{symbol}",
        )
        target = data_dir / f"{symbol}.parquet"
        previous_key: tuple[str, str] | None = None
        writer: pq.ParquetWriter | None = None
        try:
            for _path, frame in scanner.iter_frames(part_files):
                if frame.empty:
                    continue
                frame, previous_key = _prepare_compaction_frame(
                    frame=frame,
                    previous_key=previous_key,
                    stats=stats,
                )
                if frame.empty:
                    continue
                table = pa.Table.from_pandas(
                    frame.reindex(columns=schema.names),
                    schema=schema,
                    preserve_index=False,
                    safe=False,
                )
                if writer is None:
                    writer = pq.ParquetWriter(target, schema=schema)
                writer.write_table(table)
                _update_daily_clean_stats(stats, frame)
                del frame, table
        finally:
            if writer is not None:
                writer.close()
        _merge_compaction_telemetry(telemetry, scanner.telemetry.to_dict())
        if symbol_index % 64 == 0:
            gc.collect()
    gc.collect()
    stats.files = stats.symbol_count
    telemetry["effective_batch_rows"] = sorted(telemetry["effective_batch_rows"])
    stats.compaction_scan = telemetry
    return stats


def _prepare_compaction_frame(
    *,
    frame: pd.DataFrame,
    previous_key: tuple[str, str] | None,
    stats: _DailyCleanStats,
) -> tuple[pd.DataFrame, tuple[str, str] | None]:
    if not {"symbol", "trade_date"}.issubset(frame.columns):
        return frame.sort_values("trade_date").reset_index(drop=True), previous_key

    before = len(frame)
    frame = frame.drop_duplicates(subset=["symbol", "trade_date"], keep="last")
    stats.duplicate_rows += before - len(frame)
    frame = frame.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    if previous_key is not None:
        keys = zip(
            frame["symbol"].astype(str),
            frame["trade_date"].astype(str),
            strict=False,
        )
        keep = [key > previous_key for key in keys]
        stats.duplicate_rows += len(keep) - sum(keep)
        frame = cast(pd.DataFrame, frame.loc[keep].reset_index(drop=True))
    if frame.empty:
        return frame, previous_key
    return frame, (
        str(frame.iloc[-1]["symbol"]),
        str(frame.iloc[-1]["trade_date"]),
    )


def _empty_compaction_telemetry(memory_policy: MemoryPolicy) -> dict[str, Any]:
    return {
        "mode": "streaming_parquet_writer",
        "files_scanned": 0,
        "batches_scanned": 0,
        "rows_scanned": 0,
        "configured_batch_rows": memory_policy.target_batch_rows,
        "effective_batch_rows": set(),
        "estimated_bytes_per_row": None,
        "memory_samples": [],
        "flush_reasons": [],
    }


def _merge_compaction_telemetry(target: dict[str, Any], source: dict[str, object]) -> None:
    for key in ("files_scanned", "batches_scanned", "rows_scanned"):
        target[key] += int(cast(int, source[key]))
    target["effective_batch_rows"].update(source["effective_batch_rows"])
    estimate = source.get("estimated_bytes_per_row")
    if isinstance(estimate, (float, int)):
        target["estimated_bytes_per_row"] = estimate
    for key in ("memory_samples", "flush_reasons"):
        remaining = 40 - len(target[key])
        if remaining > 0:
            target[key].extend(list(cast(list[Any], source[key]))[:remaining])


def _check_daily_clean_size(
    rows: int,
    symbols: int,
    *,
    min_rows: int,
    min_symbols: int,
) -> tuple[int, int]:
    if rows < min_rows or symbols < min_symbols:
        raise ValueError(
            f"daily_clean quality gate failed: rows={rows} symbols={symbols} "
            f"min_rows={min_rows} min_symbols={min_symbols}"
        )
    return rows, symbols


def _resolved_optional_path(path: str | Path | None) -> str | None:
    return str(Path(path).expanduser().resolve()) if path else None


def _build_daily_clean_manifest(request: _DailyCleanManifestRequest) -> dict[str, Any]:
    inputs = request.inputs
    output_dir = request.output_dir
    stats = request.stats
    runtime = request.runtime
    memory_policy = request.memory_policy
    rows = stats.rows
    symbols = stats.symbol_count
    return {
        "schema_version": "tushare.a_share.daily_clean.v1",
        "dataset": "daily_clean",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(output_dir),
        "query": {
            "start_date": stats.start_date,
            "end_date": stats.end_date,
            "partition_by": "symbol",
        },
        "inputs": {
            "daily_dir": str(Path(inputs.daily_dir).expanduser().resolve()),
            "adj_factor_dir": _resolved_optional_path(inputs.adj_factor_dir),
            "daily_basic_dir": _resolved_optional_path(inputs.daily_basic_dir),
            "limit_status_dir": _resolved_optional_path(inputs.limit_status_dir),
            "suspend_dir": _resolved_optional_path(inputs.suspend_dir),
            "instruments_file": _resolved_optional_path(inputs.instruments_file),
        },
        "build": {
            "mode": "streaming_trade_date_to_symbol",
            "batch_trade_dates": request.batch_trade_dates,
            "staging_batches": runtime.batches_written,
            "staging_files": runtime.staging_files,
            "trade_dates_processed": runtime.trade_dates_processed,
            "soft_memory_flushes": runtime.soft_flushes,
            "memory_policy": memory_policy.to_dict(),
            "batch_rows": memory_policy.batch_rows_to_dict(),
            "memory_samples": runtime.memory_samples,
            "compaction_scan": stats.compaction_scan,
        },
        "totals": {"rows": rows, "symbols": symbols, "files": symbols},
        "quality": {
            "duplicate_rows": stats.duplicate_rows,
            "missing_tr_close": stats.missing_tr_close,
            "st_rows": stats.st_rows,
            "suspended_rows": stats.suspended_rows,
            "limit_up_rows": stats.limit_up_rows,
            "limit_down_rows": stats.limit_down_rows,
        },
        "columns": sorted(stats.columns or set()),
    }


def _build_daily_clean_trade_date_frame(
    inputs: _DailyCleanTradeDateFrameInputs,
) -> pd.DataFrame:
    if inputs.daily.empty:
        return inputs.daily
    out = inputs.daily.drop_duplicates(subset=["symbol", "trade_date"], keep="last").copy()
    _safe_numeric(out, (*PRICE_COLUMNS, "vol", "amount", "pct_chg", "change"))
    _fill_missing_pre_close(out)
    _update_first_trade_dates(out, inputs.first_trade_dates)
    out = _merge_adjustment_columns_for_trade_date(
        out,
        inputs.adj,
        latest_adj_factors=inputs.latest_adj_factors,
    )
    out = _merge_overlay_frame(out, inputs.daily_basic, columns=VALUATION_COLUMNS)
    out = _merge_overlay_frame(out, inputs.limit_status, columns=LIMIT_COLUMNS)
    out = _add_limit_flags(out)
    out["is_suspended"] = _derive_is_suspended(out, inputs.suspend)
    out = _add_instrument_columns_frame(
        out,
        inputs.instruments,
        first_trade_dates=inputs.first_trade_dates,
    )
    return out.sort_values(["symbol", "trade_date"]).reset_index(drop=True)


def _add_instrument_columns_frame(
    frame: pd.DataFrame,
    instruments: pd.DataFrame,
    *,
    first_trade_dates: dict[str, str] | None = None,
) -> pd.DataFrame:
    out = frame
    out["is_st"] = _derive_st_flag(out, instruments)
    if not instruments.empty and "list_date" in instruments.columns:
        listed_frame = cast(pd.DataFrame, instruments[["symbol", "list_date"]])
        listed = listed_frame.sort_values("symbol").groupby("symbol", as_index=False).tail(1)
        out = out.merge(listed, on="symbol", how="left")
    else:
        out["list_date"] = pd.NA
    out["list_date"] = _effective_list_dates(out, first_trade_dates)
    trade_ts = pd.to_datetime(out["trade_date"], format="%Y%m%d", errors="coerce")
    list_ts = pd.to_datetime(out["list_date"], format="%Y%m%d", errors="coerce")
    out["listed_days"] = (trade_ts - list_ts).dt.days
    out["board"] = out["symbol"].map(_board_from_symbol)
    out["platform_market"] = "a_share"
    return out


def _record_memory_sample(runtime: _DailyCleanBuildRuntime, snapshot: MemorySnapshot) -> None:
    samples = runtime.memory_samples if runtime.memory_samples is not None else []
    if len(samples) < 20:
        samples.append(snapshot.to_dict())


def build_a_share_daily_clean(  # noqa: PLR0913
    *,
    daily_dir: str | Path,
    adj_factor_dir: str | Path | None = None,
    daily_basic_dir: str | Path | None = None,
    limit_status_dir: str | Path | None = None,
    suspend_dir: str | Path | None = None,
    instruments_file: str | Path | None = None,
    out_dir: str | Path,
    min_rows: int = 1,
    min_symbols: int = 1,
    batch_trade_dates: int = DEFAULT_DAILY_CLEAN_BATCH_TRADE_DATES,
    memory_soft_limit_mb: float | None = DEFAULT_MEMORY_SOFT_AVAILABLE_MB,
    memory_hard_limit_mb: float | None = DEFAULT_MEMORY_HARD_AVAILABLE_MB,
) -> dict[str, Any]:
    inputs = _DailyCleanInputs(
        daily_dir=daily_dir,
        adj_factor_dir=adj_factor_dir,
        daily_basic_dir=daily_basic_dir,
        limit_status_dir=limit_status_dir,
        suspend_dir=suspend_dir,
        instruments_file=instruments_file,
        out_dir=out_dir,
    )

    batch_size = max(1, int(batch_trade_dates))
    memory_policy = MemoryPolicy(
        soft_available_mb=memory_soft_limit_mb,
        hard_available_mb=memory_hard_limit_mb,
    )
    output_dir, data_dir, staging_dir = _prepare_output_dirs(inputs.out_dir)
    runtime = _DailyCleanBuildRuntime()

    daily_parts = _trade_date_part_map(inputs.daily_dir, label="daily")
    adj_parts = (
        _trade_date_part_map(inputs.adj_factor_dir, label="adj_factor")
        if inputs.adj_factor_dir
        else {}
    )
    daily_basic_parts = (
        _trade_date_part_map(inputs.daily_basic_dir, label="daily_basic")
        if inputs.daily_basic_dir
        else {}
    )
    limit_status_parts = (
        _trade_date_part_map(inputs.limit_status_dir, label="limit_status")
        if inputs.limit_status_dir
        else {}
    )
    suspend_parts = (
        _trade_date_part_map(inputs.suspend_dir, label="suspend") if inputs.suspend_dir else {}
    )
    instruments = _load_instruments(inputs.instruments_file)
    latest_adj_factors = _latest_adj_factors(adj_parts, memory_policy=memory_policy)
    first_trade_dates: dict[str, str] = {}

    trade_dates = sorted(daily_parts)
    batch_frames: list[pd.DataFrame] = []
    try:
        for trade_date in trade_dates:
            snapshot = memory_policy.require_safe(label=f"daily_clean build {trade_date}")
            _record_memory_sample(runtime, snapshot)
            daily = _read_trade_date_part(daily_parts, trade_date, label="daily")
            clean = _build_daily_clean_trade_date_frame(
                _DailyCleanTradeDateFrameInputs(
                    daily=daily,
                    adj=_read_trade_date_part(adj_parts, trade_date, label="adj_factor"),
                    daily_basic=_read_trade_date_part(
                        daily_basic_parts,
                        trade_date,
                        label="daily_basic",
                    ),
                    limit_status=_read_trade_date_part(
                        limit_status_parts,
                        trade_date,
                        label="limit_status",
                    ),
                    suspend=_load_suspension_trade_date_frame(suspend_parts, trade_date),
                    instruments=instruments,
                    latest_adj_factors=latest_adj_factors,
                    first_trade_dates=first_trade_dates,
                )
            )
            runtime.trade_dates_processed += 1
            if not clean.empty:
                batch_frames.append(clean)
            snapshot = read_memory_snapshot()
            if (
                len(batch_frames) >= batch_size
                or memory_policy.should_flush(snapshot)
                or trade_date == trade_dates[-1]
            ):
                if memory_policy.should_flush(snapshot):
                    runtime.soft_flushes += 1
                runtime.batches_written += 1
                runtime.staging_files += _write_daily_clean_staging_batch(
                    batch_frames,
                    staging_dir=staging_dir,
                    batch_index=runtime.batches_written,
                )
                batch_frames.clear()
                gc.collect()
    finally:
        batch_frames.clear()

    stats = _compact_daily_clean_staging(
        staging_dir=staging_dir,
        data_dir=data_dir,
        memory_policy=memory_policy,
    )
    shutil.rmtree(staging_dir, ignore_errors=True)

    rows, symbols = _check_daily_clean_size(
        stats.rows,
        stats.symbol_count,
        min_rows=min_rows,
        min_symbols=min_symbols,
    )
    stats.rows = rows
    if stats.symbols is not None and len(stats.symbols) != symbols:
        raise ValueError("daily_clean symbol accounting mismatch.")
    manifest = _build_daily_clean_manifest(
        _DailyCleanManifestRequest(
            inputs=inputs,
            output_dir=output_dir,
            stats=stats,
            runtime=runtime,
            batch_trade_dates=batch_size,
            memory_policy=memory_policy,
        )
    )
    write_manifest(output_dir / "manifest.yml", manifest)
    return manifest
