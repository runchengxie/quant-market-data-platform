"""Production builder for Guan annual A-share minute-bar files.

The source Parquet is read exactly once.  That scan writes a partitioned raw
staging dataset containing canonical values and audit flags.  All subsequent
cleanup accounting, per-day compaction, validation, and promotion operate on
staging files, never on the annual source again.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.dataset_lock import (
    DatasetLockError,
    exclusive_file_lock,
)

CANONICAL_MINUTE_COLUMNS = (
    "ts_code",
    "trade_time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
)

CANONICAL_MINUTE_SCHEMA = pa.schema(
    [
        pa.field("ts_code", pa.string()),
        pa.field("trade_time", pa.timestamp("ns")),
        pa.field("open", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("vol", pa.float64()),
        pa.field("amount", pa.float64()),
    ]
)

ANNUAL_MINBAR_MANIFEST_SCHEMA = "guan.annual_minbar.v1"

ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION = "guan.annual_minbar.transform.v4"

SOURCE_FINGERPRINT_SCHEMA = "stat-parquet-footer-samples.v1"

PromotionPolicy = Literal["missing_or_invalid", "replace_all"]

_REQUIRED_SOURCE_COLUMNS = (
    "ticker",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
)

_NUMERIC_SOURCE_COLUMNS = ("open", "close", "high", "low", "volume", "amount")

_PARTITION_PATTERN = re.compile(r"(?:_partition|trade_date)=(\d{8})$")

_PART_TEMP_PATTERN = re.compile(r"^\.part-00000\.parquet\.[0-9a-f]{32}\.tmp$")

_TS_CODE_PATTERN = r"^[0-9]{6}\.(SH|SZ|BJ)$"

_FINGERPRINT_SAMPLE_BYTES = 1024 * 1024

_MIN_SAFE_EPOCH_SECONDS = -2_208_988_800  # 1900-01-01, interpreted as naive wall time.

_MAX_SAFE_EPOCH_SECONDS = 4_102_444_800  # 2100-01-01, interpreted as naive wall time.


class AnnualMinbarBuildError(RuntimeError):
    """Base exception for annual minbar build failures."""


class AnnualMinbarValidationError(AnnualMinbarBuildError):
    """Raised before promotion when source or staging validation fails."""


@dataclass(frozen=True)
class AnnualMinbarUnitProfile:
    """Unit conversion from one annual Guan file to canonical units."""

    name: str
    volume_scale: float
    amount_scale: float


@dataclass(frozen=True)
class AnnualMinbarBuildOptions:
    """Configuration for one annual, single-source-scan build."""

    source_path: str | Path
    output_dir: str | Path
    manifest_path: str | Path
    year: int
    staging_root: str | Path | None = None
    resume: bool = True
    force: bool = False
    promotion_policy: PromotionPolicy = "missing_or_invalid"
    replace_dates: tuple[str, ...] = ()
    threads: int = 2
    memory_limit: str = "auto"
    keep_staging: bool = False

    def __post_init__(self) -> None:
        if not 2016 <= self.year <= 2026:
            raise ValueError(f"Supported Guan annual minbar years are 2016-2026, got {self.year}")
        if self.promotion_policy not in {"missing_or_invalid", "replace_all"}:
            raise ValueError(f"Unsupported promotion_policy: {self.promotion_policy!r}")
        if not 1 <= self.threads <= 4:
            raise ValueError("threads must be between 1 and 4")
        if not self.memory_limit.strip():
            raise ValueError("memory_limit must not be empty")
        invalid_dates = [
            value
            for value in self.replace_dates
            if not re.fullmatch(r"\d{8}", value) or not value.startswith(str(self.year))
        ]
        if invalid_dates:
            raise ValueError(
                f"replace_dates must be YYYYMMDD dates in {self.year}: {invalid_dates}"
            )


def unit_profile_for_year(year: int) -> AnnualMinbarUnitProfile:
    """Return the audited Guan unit regime for an annual source file."""
    if 2016 <= year <= 2025:
        return AnnualMinbarUnitProfile(
            name="guan_lots_yuan",
            volume_scale=100.0,
            amount_scale=1.0,
        )
    if year == 2026:
        return AnnualMinbarUnitProfile(
            name="guan_shares_cents_times_shares",
            volume_scale=1.0,
            amount_scale=0.01,
        )
    raise ValueError(f"No audited Guan annual minbar unit profile for {year}")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _expanded(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _sql_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _require_duckdb() -> ModuleType:
    try:
        return importlib.import_module("duckdb")
    except ImportError as exc:  # pragma: no cover - exercised only without the extra.
        raise RuntimeError(
            "Annual Guan minbar builds require DuckDB. Install the 'duckdb' optional dependency."
        ) from exc


def _source_schema_summary(source: Path) -> dict[str, Any]:
    parquet_file = pq.ParquetFile(source)
    schema = parquet_file.schema_arrow
    missing = set(_REQUIRED_SOURCE_COLUMNS).difference(schema.names)
    if missing:
        raise AnnualMinbarValidationError(
            f"Annual Guan source {source} is missing columns: {sorted(missing)}"
        )
    timestamp_type = schema.field("timestamp").type
    if not pa.types.is_integer(timestamp_type):
        raise AnnualMinbarValidationError(
            f"Annual Guan source timestamp must be integer epoch seconds, got {timestamp_type}"
        )
    for column in _NUMERIC_SOURCE_COLUMNS:
        column_type = schema.field(column).type
        if not (pa.types.is_integer(column_type) or pa.types.is_floating(column_type)):
            raise AnnualMinbarValidationError(
                f"Annual Guan source {column} must be numeric, got {column_type}"
            )
    return {
        "schema": str(schema),
        "rows": parquet_file.metadata.num_rows,
        "row_groups": parquet_file.metadata.num_row_groups,
        "footer_bytes": parquet_file.metadata.serialized_size,
    }


def _sampled_file_hash(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        first = handle.read(_FINGERPRINT_SAMPLE_BYTES)
        digest.update(first)
        if size > _FINGERPRINT_SAMPLE_BYTES:
            handle.seek(max(0, size - _FINGERPRINT_SAMPLE_BYTES))
            digest.update(handle.read(_FINGERPRINT_SAMPLE_BYTES))
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(source_path: str | Path) -> dict[str, Any]:
    """Build a cheap, reproducible source identity without another full-file scan."""
    source = _expanded(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"Annual Guan minbar source does not exist: {source}")
    stat = source.stat()
    payload: dict[str, Any] = {
        "schema_version": SOURCE_FINGERPRINT_SCHEMA,
        "path": str(source),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "sampled_sha256": _sampled_file_hash(source),
        "parquet": _source_schema_summary(source),
    }
    stable = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["id"] = hashlib.sha256(stable).hexdigest()
    return payload


def _atomic_write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": ANNUAL_MINBAR_MANIFEST_SCHEMA,
            "transform_contract_version": ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION,
            "created_at": _utc_now(),
            "years": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnnualMinbarBuildError(f"Cannot read annual minbar manifest {path}: {exc}") from exc
    if payload.get("schema_version") != ANNUAL_MINBAR_MANIFEST_SCHEMA:
        raise AnnualMinbarBuildError(
            f"Unsupported annual minbar manifest schema in {path}: "
            f"{payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("years"), dict):
        raise AnnualMinbarBuildError(f"Annual minbar manifest has invalid years mapping: {path}")
    return payload


def _save_manifest(payload: dict[str, Any], path: Path) -> None:
    payload["updated_at"] = _utc_now()
    _atomic_write_json(payload, path)


@contextmanager
def _dataset_lock(output_root: Path):
    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = output_root / ".annual-minbar-build.lock"
    try:
        with exclusive_file_lock(
            lock_path,
            operation="build-guan-annual-minbar",
            remove_on_release=True,
        ):
            yield
    except DatasetLockError as exc:
        raise AnnualMinbarBuildError(
            f"Another annual minbar build holds the dataset lock: {lock_path}: {exc}"
        ) from None


def _remove_stale_temp_for_target(target: Path) -> None:
    if not target.parent.is_dir():
        return
    pattern = re.compile(rf"^\.{re.escape(target.name)}\.[0-9a-f]{{32}}\.tmp$")
    for candidate in target.parent.glob(f".{target.name}.*.tmp"):
        if pattern.fullmatch(candidate.name) is None:
            continue
        candidate_stat = candidate.lstat()
        if stat.S_ISREG(candidate_stat.st_mode):
            candidate.unlink()


def _cleanup_stale_annual_temps(
    *,
    output_root: Path,
    work_root: Path,
    manifest_path: Path,
) -> None:
    """Remove only UUID temporaries created by a killed annual builder."""
    _remove_stale_temp_for_target(manifest_path)
    for root in (output_root, work_root):
        if not root.is_dir():
            continue
        for candidate in root.rglob(".part-00000.parquet.*.tmp"):
            if _PART_TEMP_PATTERN.fullmatch(candidate.name) is None:
                continue
            candidate_stat = candidate.lstat()
            if stat.S_ISREG(candidate_stat.st_mode):
                candidate.unlink()


def _configure_connection(
    connection: Any,
    options: AnnualMinbarBuildOptions,
    temp: Path,
    *,
    resolved_memory_limit: str,
) -> None:
    temp.mkdir(parents=True, exist_ok=True)
    connection.execute(f"SET threads = {options.threads}")
    connection.execute(f"SET memory_limit = {_sql_literal(resolved_memory_limit)}")
    connection.execute(f"SET temp_directory = {_sql_literal(temp)}")
    connection.execute("SET preserve_insertion_order = false")
    # A year has roughly 240 date partitions.  DuckDB's default of 100 open
    # partition writers repeatedly evicts dates because every source row group
    # spans the year, producing tens of thousands of tiny staging fragments.
    connection.execute("SET partitioned_write_max_open_files = 300")


def _mapped_ts_code_sql(ticker_expression: str) -> str:
    return f"""
        CASE
            WHEN regexp_full_match({ticker_expression}, '^(600|601|603|605|688|689)[0-9]{{3}}$')
                THEN {ticker_expression} || '.SH'
            WHEN regexp_full_match(
                {ticker_expression}, '^(000|001|002|003|300|301|302)[0-9]{{3}}$'
            )
                THEN {ticker_expression} || '.SZ'
            WHEN regexp_full_match({ticker_expression}, '^(43|83|87|88|92)[0-9]{{4}}$')
                THEN {ticker_expression} || '.BJ'
            ELSE NULL
        END
    """
