"""Partition, sidecar, receipt and validation helpers for the A-share minute mirror.

These are internal helpers for ``tushare_a_share_mins``.  They are kept in their
own submodule so the public module can stay a thin re-export shell while the
complex per-partition logic remains independently inspectable and testable.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.providers._a_share_mins_constants import (
    COMPLETENESS_FILENAME,
    COMPLETENESS_SCHEMA_VERSION,
    DEFAULT_MINS_FIELDS,
    FULL_DAILY_UNIVERSE_RULES,
    MINS_ARROW_SCHEMA,
    MINUTE_BARS_PER_DAY,
    PARTITION_KEY,
    _MinuteProgress,
)
from market_data_platform.providers._a_share_mins_receipt import (
    _exact_partition_receipt,
    _sidecar_symbol_inventory,
    _universe_hash,
)
from market_data_platform.providers._a_share_mins_validation import (
    _complete_symbols,
    _has_expected_minute_grid,
    _has_valid_ohlcv,
    _is_cn_stock_ts_code,
    _normalize_minute_frame,
    _normalize_stock_dates,
    _validate_batch_frame,
)
from market_data_platform.providers.tushare_a_share_dates import _validate_date

__all__ = [
    "_normalize_minute_frame",
    "_has_expected_minute_grid",
    "_has_valid_ohlcv",
    "_validate_batch_frame",
    "_partition_files",
    "_sha256_file",
    "_redacted_error_message",
    "_quarantine_partition_files",
    "_read_existing_partition",
    "_complete_symbols",
    "_prepare_partition_frame",
    "_read_completeness",
    "_sidecar_identity_matches",
    "_atomic_write_json",
    "_atomic_write_partition",
    "_partition_state",
    "_partition_files_match",
    "_partition_binding_matches",
    "_sidecar_symbol_inventory",
    "_exact_partition_receipt",
    "_universe_hash",
    "_write_completeness",
    "_persist_progress",
    "_is_cn_stock_ts_code",
    "_normalize_stock_dates",
    "validate_complete_minute_partition",
]


def _partition_files(part_dir: Path) -> list[Path]:
    return sorted(path for path in part_dir.glob("*.parquet") if path.is_file())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _redacted_error_message(exc: BaseException, *, secrets: tuple[str | None, ...]) -> str:
    message = str(exc) or type(exc).__name__
    for secret in secrets:
        if secret:
            message = message.replace(secret, "<redacted>")
    return message


def _quarantine_partition_files(part_dir: Path) -> list[Path]:
    quarantined: list[Path] = []
    suffix = time.time_ns()
    quarantine_dir = part_dir / "_quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(_partition_files(part_dir)):
        target = quarantine_dir / f"{suffix}-{index}-{path.name}"
        os.replace(path, target)
        quarantined.append(target)
    return quarantined


def _read_existing_partition(part_dir: Path) -> pd.DataFrame:
    files = _partition_files(part_dir)
    if not files:
        return pd.DataFrame(columns=pd.Index(DEFAULT_MINS_FIELDS))
    # ParquetFile avoids Hive partition inference for legacy files that also
    # embedded a differently typed trade_date column.
    frames = [pq.ParquetFile(path).read().to_pandas() for path in files]
    combined = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    return _normalize_minute_frame(combined, context=f"Existing minute partition {part_dir}")


def _prepare_partition_frame(frames: list[pd.DataFrame], *, trade_date: str) -> pd.DataFrame:
    nonempty = [frame for frame in frames if not frame.empty]
    if not nonempty:
        return pd.DataFrame(columns=pd.Index(DEFAULT_MINS_FIELDS))
    combined = _normalize_minute_frame(
        pd.concat(nonempty, ignore_index=True), context=f"Minute partition {trade_date}"
    )
    combined = combined[combined["trade_time"].dt.strftime("%Y%m%d") == trade_date]
    return (
        combined.drop_duplicates(list(PARTITION_KEY), keep="last")
        .sort_values(list(PARTITION_KEY))
        .reset_index(drop=True)
    )


def _read_completeness(part_dir: Path) -> dict[str, Any] | None:
    path = part_dir / COMPLETENESS_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid minute completeness sidecar: {path}") from exc
    return payload if isinstance(payload, dict) else None


def _sidecar_identity_matches(
    payload: dict[str, Any] | None,
    *,
    trade_date: str,
    freq: str,
    universe_hash: str,
    universe_rule: str,
) -> bool:
    return bool(
        payload
        and payload.get("schema_version") == COMPLETENESS_SCHEMA_VERSION
        and payload.get("trade_date") == trade_date
        and payload.get("freq") == freq
        and payload.get("universe_hash") == universe_hash
        and payload.get("universe_rule") == universe_rule
        and payload.get("expected_bars_per_symbol") == MINUTE_BARS_PER_DAY
        and isinstance(payload.get("completed_symbols"), list)
    )


def _atomic_write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _atomic_write_partition(frame: pd.DataFrame, part_dir: Path, *, trade_date: str) -> Path:
    part_dir.mkdir(parents=True, exist_ok=True)
    part_path = part_dir / "part-00000.parquet"
    canonical = _prepare_partition_frame([frame], trade_date=trade_date)
    if canonical.empty:
        raise ValueError(f"Refusing to write empty minute partition for {trade_date}")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=part_dir,
            prefix=".part-00000.",
            suffix=".parquet.tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        table = pa.Table.from_pandas(
            canonical.loc[:, DEFAULT_MINS_FIELDS],
            schema=MINS_ARROW_SCHEMA,
            preserve_index=False,
            safe=True,
        )
        pq.write_table(table, temporary_path, compression="zstd")
        os.replace(temporary_path, part_path)
        for stale_path in _partition_files(part_dir):
            if stale_path != part_path:
                stale_path.unlink()
        return part_path
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _partition_state(part_dir: Path, *, trade_date: str) -> dict[str, Any] | None:
    files = _partition_files(part_dir)
    if not files:
        return None
    frame = _read_existing_partition(part_dir)
    key_unique = not frame.duplicated(list(PARTITION_KEY)).any()
    trade_date_valid = bool(
        not frame.empty and frame["trade_time"].dt.strftime("%Y%m%d").eq(trade_date).all()
    )
    schema_valid = len(files) == 1 and pq.ParquetFile(files[0]).schema_arrow.equals(
        MINS_ARROW_SCHEMA, check_metadata=False
    )
    file_entries = []
    for path in files:
        stat = path.stat()
        file_entries.append(
            {
                "name": path.name,
                "rows": pq.ParquetFile(path).metadata.num_rows,
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": _sha256_file(path),
            }
        )
    return {
        "files": file_entries,
        "rows": len(frame),
        "schema": str(MINS_ARROW_SCHEMA),
        "schema_valid": schema_valid,
        "key": list(PARTITION_KEY),
        "key_unique": key_unique,
        "trade_date_valid": trade_date_valid,
        "symbols": sorted(set(frame["ts_code"])),
        "complete_symbols": sorted(_complete_symbols(frame, trade_date=trade_date)),
    }


def _partition_files_match(partition: dict[str, Any], part_dir: Path) -> bool:
    expected_files = partition.get("files")
    if not isinstance(expected_files, list) or not all(
        isinstance(entry, dict) for entry in expected_files
    ):
        return False
    expected_files = cast(list[dict[str, Any]], expected_files)
    actual_files = _partition_files(part_dir)
    if [path.name for path in actual_files] != [entry.get("name") for entry in expected_files]:
        return False
    actual_rows = 0
    for path, expected in zip(actual_files, expected_files, strict=True):
        stat = path.stat()
        parquet_file = pq.ParquetFile(path)
        if (
            stat.st_size != expected.get("size_bytes")
            or stat.st_mtime_ns != expected.get("mtime_ns")
            or parquet_file.metadata.num_rows != expected.get("rows")
            or _sha256_file(path) != expected.get("sha256")
            or not parquet_file.schema_arrow.equals(MINS_ARROW_SCHEMA, check_metadata=False)
        ):
            return False
        actual_rows += parquet_file.metadata.num_rows
    return actual_rows == partition.get("rows")


def _partition_binding_matches(payload: dict[str, Any], part_dir: Path) -> bool:
    try:
        partition = payload.get("partition")
        return bool(
            isinstance(partition, dict)
            and partition.get("schema") == str(MINS_ARROW_SCHEMA)
            and partition.get("key") == list(PARTITION_KEY)
            and all(
                partition.get(field) is True
                for field in ("schema_valid", "key_unique", "trade_date_valid")
            )
            and _partition_files_match(partition, part_dir)
        )
    except Exception:
        return False


def validate_complete_minute_partition(
    partition_dir: str | Path,
    *,
    trade_date: str,
    require_full_universe: bool = True,
) -> dict[str, Any]:
    """Validate a completed mirror partition and return its immutable receipt.

    This is the promotion boundary used by downstream canonical builders.  It
    deliberately rejects symbol-explicit mirrors when ``require_full_universe``
    is true, even when those symbols each contain a valid 241-bar grid.
    """
    _validate_date(trade_date)
    part_dir = Path(partition_dir).expanduser()
    sidecar_path = part_dir / COMPLETENESS_FILENAME
    payload = _read_completeness(part_dir)
    if payload is None:
        raise ValueError(f"TuShare minute partition has no completeness sidecar: {part_dir}")

    expected_symbols, completed_symbols, missing_symbols = _sidecar_symbol_inventory(
        payload,
        sidecar_path=sidecar_path,
        trade_date=trade_date,
    )

    universe_rule = str(payload.get("universe_rule", ""))
    universe_hash = _universe_hash(expected_symbols, rule=universe_rule)
    if not _sidecar_identity_matches(
        payload,
        trade_date=trade_date,
        freq="1min",
        universe_hash=universe_hash,
        universe_rule=universe_rule,
    ):
        raise ValueError(
            f"TuShare minute sidecar identity mismatch for {trade_date}: {sidecar_path}"
        )
    if require_full_universe and universe_rule not in FULL_DAILY_UNIVERSE_RULES:
        raise ValueError(
            f"TuShare minute partition {trade_date} is not a full daily universe mirror"
        )
    if (
        payload.get("status") != "complete"
        or completed_symbols != expected_symbols
        or missing_symbols
    ):
        raise ValueError(f"TuShare minute partition is incomplete for {trade_date}: {sidecar_path}")
    if not _partition_binding_matches(payload, part_dir):
        raise ValueError(
            f"TuShare minute partition changed after its sidecar was written: {part_dir}"
        )

    partition, files, request_policy = _exact_partition_receipt(
        payload,
        sidecar_path=sidecar_path,
        trade_date=trade_date,
        expected_symbols=expected_symbols,
    )

    market_symbol_counts = {
        exchange: sum(symbol.endswith(f".{exchange}") for symbol in expected_symbols)
        for exchange in ("SH", "SZ", "BJ")
    }
    partition_path = part_dir / "part-00000.parquet"
    return {
        "schema_version": "tushare.a_share.minute_partition.promotion.v1",
        "trade_date": trade_date,
        "partition_path": str(partition_path),
        "sidecar_path": str(sidecar_path),
        "partition_sha256": str(files[0]["sha256"]),
        "sidecar_sha256": _sha256_file(sidecar_path),
        "rows": int(partition["rows"]),
        "symbols": len(expected_symbols),
        "expected_symbols": sorted(expected_symbols),
        "market_symbol_counts": market_symbol_counts,
        "expected_bars_per_symbol": MINUTE_BARS_PER_DAY,
        "universe_hash": universe_hash,
        "universe_rule": universe_rule,
        "universe_source": str(payload.get("universe_source", "")),
        "mirror_generated_at": str(payload.get("generated_at", "")),
        "request_policy": request_policy,
    }


def _write_completeness(
    progress: _MinuteProgress,
    *,
    completed_symbols: set[str],
    partition: dict[str, Any] | None,
    status: str,
    error: dict[str, Any] | None = None,
) -> None:
    expected_symbols = set(progress.expected_symbols)
    partition_complete = set(partition.get("complete_symbols", [])) if partition else set()
    partition_symbols = set(partition.get("symbols", [])) if partition else set()
    completed_symbols = completed_symbols.intersection(partition_complete)
    if status == "complete" and (
        partition is None
        or partition_symbols != expected_symbols
        or not expected_symbols.issubset(completed_symbols)
        or not all(
            partition.get(field) is True
            for field in ("schema_valid", "key_unique", "trade_date_valid")
        )
    ):
        raise ValueError(f"Cannot mark incomplete minute partition {progress.trade_date} complete")
    payload: dict[str, Any] = {
        "schema_version": COMPLETENESS_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": status,
        "trade_date": progress.trade_date,
        "freq": progress.freq,
        "expected_bars_per_symbol": MINUTE_BARS_PER_DAY,
        "universe_hash": progress.universe_hash,
        "universe_rule": progress.universe_rule,
        "universe_source": progress.universe_source,
        "expected_symbols": sorted(expected_symbols),
        "completed_symbols": sorted(completed_symbols),
        "missing_request_symbols": sorted(expected_symbols - completed_symbols),
        "partition": partition,
        "request_policy": {
            "attempts": progress.request_policy.attempts,
            "retry_sleep_seconds": progress.request_policy.retry_sleep_seconds,
            "retry_max_sleep_seconds": progress.request_policy.retry_max_sleep_seconds,
            "quota_cooldown_seconds": progress.request_policy.quota_cooldown_seconds,
            "disable_proxy": progress.request_policy.disable_proxy,
            "sdk_retry_count": 1,
        },
    }
    if error is not None:
        payload["error"] = error
    _atomic_write_json(payload, progress.part_dir / COMPLETENESS_FILENAME)


def _persist_progress(
    progress: _MinuteProgress,
    *,
    completed_symbols: set[str],
    frames: list[pd.DataFrame],
    status: str,
    error: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    expected_symbols = set(progress.expected_symbols)
    combined = _prepare_partition_frame(frames, trade_date=progress.trade_date)
    if not combined.empty:
        combined = combined[combined["ts_code"].isin(expected_symbols)].reset_index(drop=True)
    if not combined.empty:
        _atomic_write_partition(combined, progress.part_dir, trade_date=progress.trade_date)
    partition = _partition_state(progress.part_dir, trade_date=progress.trade_date)
    _write_completeness(
        progress,
        completed_symbols=completed_symbols,
        partition=partition,
        status=status,
        error=error,
    )
    return combined, partition
