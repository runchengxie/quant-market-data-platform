"""Production builder for Guan annual A-share minute-bar files.

The source Parquet is read exactly once.  That scan writes a partitioned raw
staging dataset containing canonical values and audit flags.  All subsequent
cleanup accounting, per-day compaction, validation, and promotion operate on
staging files, never on the annual source again.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from market_data_platform.providers.guan_annual_minbar_part01 import (
    _MAX_SAFE_EPOCH_SECONDS,
    _MIN_SAFE_EPOCH_SECONDS,
    _NUMERIC_SOURCE_COLUMNS,
    _PARTITION_PATTERN,
    _TS_CODE_PATTERN,
    CANONICAL_MINUTE_SCHEMA,
    AnnualMinbarUnitProfile,
    AnnualMinbarValidationError,
    _mapped_ts_code_sql,
    _sha256_file,
    _sql_literal,
)


def _copy_source_to_raw_staging(
    connection: Any,
    *,
    source: Path,
    raw_root: Path,
    year: int,
    profile: AnnualMinbarUnitProfile,
) -> None:
    """Perform the build's sole full scan of the annual source Parquet."""
    ticker = "lpad(trim(CAST(ticker AS VARCHAR)), 6, '0')"
    ts_code = _mapped_ts_code_sql("_ticker")
    # Guan encodes absent placeholder values as IEEE NaN in real annual files.
    # Treat NaN like SQL NULL for the all-empty/partial-empty distinction, but
    # reserve the non-finite error for +/- infinity.
    numeric_missing = [
        f"({column} IS NULL OR isnan(CAST({column} AS DOUBLE)))"
        for column in _NUMERIC_SOURCE_COLUMNS
    ]
    all_empty = " AND ".join(numeric_missing)
    any_empty = " OR ".join(numeric_missing)
    non_finite = " OR ".join(
        f"({column} IS NOT NULL AND isinf(CAST({column} AS DOUBLE)))"
        for column in _NUMERIC_SOURCE_COLUMNS
    )
    source_literal = _sql_literal(source)
    raw_literal = _sql_literal(raw_root)
    query = f"""
        COPY (
            WITH typed AS (
                SELECT
                    {ticker} AS _ticker,
                    CAST(timestamp AS BIGINT) AS _epoch,
                    CAST(open AS DOUBLE) AS _open,
                    CAST(close AS DOUBLE) AS _close,
                    CAST(high AS DOUBLE) AS _high,
                    CAST(low AS DOUBLE) AS _low,
                    CAST(volume AS DOUBLE) AS _volume,
                    CAST(amount AS DOUBLE) AS _amount,
                    ({all_empty}) AS _empty_numeric,
                    (({any_empty}) AND NOT ({all_empty})) AS _partial_null,
                    ((NOT ({all_empty})) AND ({non_finite})) AS _non_finite
                FROM read_parquet({source_literal}, hive_partitioning = false)
            ), mapped AS (
                SELECT
                    *,
                    {ts_code} AS ts_code,
                    CASE
                        WHEN _epoch BETWEEN {_MIN_SAFE_EPOCH_SECONDS} AND {_MAX_SAFE_EPOCH_SECONDS}
                            THEN CAST(make_timestamp(_epoch * 1000000) AS TIMESTAMP_NS)
                        ELSE NULL
                    END AS trade_time
                FROM typed
            ), flags AS (
                SELECT
                    *,
                    (NOT _empty_numeric AND ts_code IS NULL) AS _invalid_ticker,
                    (NOT _empty_numeric AND trade_time IS NULL) AS _invalid_timestamp,
                    (NOT _empty_numeric AND trade_time IS NOT NULL
                        AND year(trade_time) <> {year}) AS _wrong_year,
                    (NOT _empty_numeric AND trade_time IS NOT NULL
                        AND _epoch % 60 <> 0) AS _non_minute,
                    (NOT _empty_numeric AND NOT _partial_null AND NOT _non_finite
                        AND (_volume < 0 OR _amount < 0)) AS _negative_flow,
                    (NOT _empty_numeric
                        AND NOT _partial_null
                        AND NOT _non_finite
                        AND _volume = 0
                        AND _amount > 0) AS _zero_volume_nonzero_amount,
                    (NOT _empty_numeric
                        AND NOT _partial_null
                        AND NOT _non_finite
                        AND _volume > 0
                        AND _amount = 0) AS _positive_volume_zero_amount,
                    (NOT _empty_numeric
                        AND NOT _partial_null
                        AND NOT _non_finite
                        AND trade_time IS NOT NULL
                        AND strftime(trade_time, '%H%M%S') = '130000'
                        AND _volume = 0
                        AND _amount = 0
                        AND _open = _close
                        AND _open = _high
                        AND _open = _low) AS _dropped_off_session_zero_flow
                FROM mapped
            ), validated AS (
                SELECT
                    *,
                    (NOT _empty_numeric
                        AND trade_time IS NOT NULL
                        AND NOT _dropped_off_session_zero_flow
                        AND NOT (
                            CAST(strftime(trade_time, '%H%M') AS INTEGER) BETWEEN 930 AND 1130
                            OR CAST(strftime(trade_time, '%H%M') AS INTEGER) BETWEEN 1301 AND 1500
                        )) AS _off_session
                FROM flags
            ), classified AS (
                SELECT
                    *,
                    (NOT _empty_numeric
                        AND NOT _partial_null
                        AND NOT _non_finite
                        AND NOT _invalid_timestamp
                        AND NOT _wrong_year
                        AND NOT _non_minute
                        AND NOT _off_session
                        AND NOT _negative_flow
                        AND NOT _dropped_off_session_zero_flow
                        AND NOT _invalid_ticker) AS _keep
                FROM validated
            ), canonical AS (
                SELECT
                    ts_code,
                    trade_time,
                    _open AS open,
                    _close AS close,
                    greatest(_high, _open, _close) AS high,
                    least(_low, _open, _close) AS low,
                    _volume * {profile.volume_scale!r} AS vol,
                    _amount * {profile.amount_scale!r} AS amount,
                    _empty_numeric,
                    _partial_null,
                    _non_finite,
                    _invalid_ticker,
                    _invalid_timestamp,
                    _wrong_year,
                    _non_minute,
                    _off_session,
                    _negative_flow,
                    _zero_volume_nonzero_amount,
                    _positive_volume_zero_amount,
                    _dropped_off_session_zero_flow,
                    (_keep AND (
                        _high IS DISTINCT FROM greatest(_high, _open, _close)
                        OR _low IS DISTINCT FROM least(_low, _open, _close)
                    )) AS _repaired_ohlc,
                    _keep,
                    CASE WHEN _keep THEN strftime(trade_time, '%Y%m%d') ELSE '_audit' END
                        AS _partition
                FROM classified
            )
            SELECT * FROM canonical
        ) TO {raw_literal} (
            FORMAT PARQUET,
            COMPRESSION ZSTD,
            PARTITION_BY (_partition),
            FILENAME_PATTERN 'chunk-{{uuid}}',
            OVERWRITE_OR_IGNORE true,
            ROW_GROUP_SIZE 250000
        )
    """
    connection.execute(query)


def _raw_staging_stats(connection: Any, raw_root: Path) -> dict[str, int]:
    glob = _sql_literal(raw_root / "*" / "*.parquet")
    row = connection.execute(
        f"""
        SELECT
            count(*) AS input_rows,
            count_if(_empty_numeric) AS dropped_empty_rows,
            count_if(_invalid_ticker) AS unmapped_ticker_rows,
            count_if(_dropped_off_session_zero_flow)
                AS dropped_off_session_zero_flow_rows,
            count_if(_zero_volume_nonzero_amount)
                AS zero_volume_nonzero_amount_rows,
            count_if(_positive_volume_zero_amount)
                AS positive_volume_zero_amount_rows,
            count_if(_partial_null) AS partial_null_rows,
            count_if(_non_finite) AS non_finite_rows,
            count_if(_invalid_timestamp) AS invalid_timestamp_rows,
            count_if(_wrong_year) AS wrong_year_rows,
            count_if(_non_minute) AS non_minute_rows,
            count_if(_off_session) AS off_session_rows,
            count_if(_negative_flow) AS negative_flow_rows,
            count_if(_repaired_ohlc) AS repaired_ohlc_rows,
            count_if(_keep) AS candidate_rows
        FROM read_parquet({glob}, hive_partitioning = false)
        """
    ).fetchone()
    if row is None:
        raise AnnualMinbarValidationError(f"Raw annual staging is empty: {raw_root}")
    names = (
        "input_rows",
        "dropped_empty_rows",
        "unmapped_ticker_rows",
        "dropped_off_session_zero_flow_rows",
        "zero_volume_nonzero_amount_rows",
        "positive_volume_zero_amount_rows",
        "partial_null_rows",
        "non_finite_rows",
        "invalid_timestamp_rows",
        "wrong_year_rows",
        "non_minute_rows",
        "off_session_rows",
        "negative_flow_rows",
        "repaired_ohlc_rows",
        "candidate_rows",
    )
    return {name: int(value or 0) for name, value in zip(names, row, strict=True)}


def _source_fatal_issues(stats: dict[str, int]) -> dict[str, int]:
    fatal_names = (
        "partial_null_rows",
        "non_finite_rows",
        "invalid_timestamp_rows",
        "wrong_year_rows",
        "non_minute_rows",
        "off_session_rows",
        "negative_flow_rows",
        "unmapped_ticker_rows",
    )
    issues = {name: stats[name] for name in fatal_names if stats[name]}
    if not stats["candidate_rows"]:
        issues["candidate_rows"] = 0
    return issues


def _raw_date_directories(raw_root: Path, year: int) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for child in raw_root.iterdir():
        matched = _PARTITION_PATTERN.fullmatch(child.name)
        if matched is None or not child.is_dir():
            continue
        date = matched.group(1)
        if not date.startswith(str(year)):
            raise AnnualMinbarValidationError(
                f"Raw annual staging contains an unexpected date partition: {child}"
            )
        result[date] = child
    return dict(sorted(result.items()))


def _compact_raw_dates(
    connection: Any,
    *,
    raw_root: Path,
    canonical_root: Path,
    year: int,
) -> list[str]:
    dates = _raw_date_directories(raw_root, year)
    if not dates:
        raise AnnualMinbarValidationError(f"No valid date partitions were staged for {year}")
    for date, raw_date_dir in dates.items():
        source_glob = _sql_literal(raw_date_dir / "*.parquet")
        destination_dir = canonical_root / f"trade_date={date}"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / "part-00000.parquet"
        temporary = destination_dir / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        try:
            connection.execute(
                f"""
                COPY (
                    SELECT
                        CAST(ts_code AS VARCHAR) AS ts_code,
                        CAST(trade_time AS TIMESTAMP_NS) AS trade_time,
                        CAST(open AS DOUBLE) AS open,
                        CAST(close AS DOUBLE) AS close,
                        CAST(high AS DOUBLE) AS high,
                        CAST(low AS DOUBLE) AS low,
                        CAST(vol AS DOUBLE) AS vol,
                        CAST(amount AS DOUBLE) AS amount
                    FROM read_parquet({source_glob}, hive_partitioning = false)
                    WHERE _keep
                    ORDER BY ts_code, trade_time
                ) TO {_sql_literal(temporary)} (
                    FORMAT PARQUET,
                    COMPRESSION ZSTD,
                    ROW_GROUP_SIZE 250000
                )
                """
            )
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return list(dates)


def _partition_validation(connection: Any, path: Path, date: str) -> dict[str, Any]:
    if not path.is_file():
        return {
            "date": date,
            "path": str(path),
            "valid": False,
            "rows": 0,
            "symbols": 0,
            "issues": {"missing_partition": 1},
        }
    parquet_file = pq.ParquetFile(path)
    if not parquet_file.schema_arrow.equals(CANONICAL_MINUTE_SCHEMA):
        return {
            "date": date,
            "path": str(path),
            "valid": False,
            "rows": parquet_file.metadata.num_rows,
            "symbols": None,
            "issues": {"schema_mismatch": 1},
        }
    source = _sql_literal(path)
    row = connection.execute(
        f"""
        SELECT
            count(*) AS rows,
            count(DISTINCT ts_code) AS symbols,
            count(*) - count(DISTINCT (ts_code, trade_time)) AS duplicate_rows,
            count_if(ts_code IS NULL OR trade_time IS NULL OR open IS NULL OR close IS NULL
                OR high IS NULL OR low IS NULL OR vol IS NULL OR amount IS NULL) AS null_rows,
            count_if(NOT isfinite(open) OR NOT isfinite(close) OR NOT isfinite(high)
                OR NOT isfinite(low) OR NOT isfinite(vol) OR NOT isfinite(amount))
                AS non_finite_rows,
            count_if(high < greatest(open, close) OR low > least(open, close) OR high < low)
                AS invalid_ohlc_rows,
            count_if(vol < 0 OR amount < 0) AS negative_flow_rows,
            count_if(NOT regexp_full_match(ts_code, {_sql_literal(_TS_CODE_PATTERN)}))
                AS invalid_ts_code_rows,
            count_if(strftime(trade_time, '%Y%m%d') <> {_sql_literal(date)}) AS wrong_date_rows,
            count_if(epoch_ns(trade_time) % 60000000000 <> 0) AS non_minute_rows,
            count_if(NOT (
                CAST(strftime(trade_time, '%H%M') AS INTEGER) BETWEEN 930 AND 1130
                OR CAST(strftime(trade_time, '%H%M') AS INTEGER) BETWEEN 1301 AND 1500
            )) AS off_session_rows,
            min(trade_time) AS time_min,
            max(trade_time) AS time_max
        FROM read_parquet({source}, hive_partitioning = false)
        """
    ).fetchone()
    if row is None:
        raise AnnualMinbarValidationError(f"Cannot validate staged partition: {path}")
    issue_names = (
        "duplicate_rows",
        "null_rows",
        "non_finite_rows",
        "invalid_ohlc_rows",
        "negative_flow_rows",
        "invalid_ts_code_rows",
        "wrong_date_rows",
        "non_minute_rows",
        "off_session_rows",
    )
    rows = int(row[0])
    issue_values = row[2:11]
    issues = {name: int(value or 0) for name, value in zip(issue_names, issue_values, strict=True)}
    if not rows:
        issues["empty_partition"] = 1
    failures = {name: value for name, value in issues.items() if value}
    return {
        "date": date,
        "path": str(path),
        "output_sha256": _sha256_file(path),
        "valid": not failures,
        "rows": rows,
        "symbols": int(row[1] or 0),
        "time_min": str(row[11]) if row[11] is not None else None,
        "time_max": str(row[12]) if row[12] is not None else None,
        "issues": issues,
    }


def _validate_staging_year(
    connection: Any,
    *,
    canonical_root: Path,
    year: int,
    expected_rows: int,
) -> dict[str, Any]:
    partitions: dict[str, Path] = {}
    for child in canonical_root.iterdir() if canonical_root.exists() else ():
        matched = _PARTITION_PATTERN.fullmatch(child.name)
        part = child / "part-00000.parquet"
        if matched is not None and child.is_dir() and part.is_file():
            partitions[matched.group(1)] = part
    unexpected = sorted(date for date in partitions if not date.startswith(str(year)))
    if unexpected:
        raise AnnualMinbarValidationError(
            f"Canonical staging for {year} contains unexpected dates: {unexpected[:5]}"
        )
    details = [
        _partition_validation(connection, path, date) for date, path in sorted(partitions.items())
    ]
    invalid = [item for item in details if not item["valid"]]
    rows = sum(int(item["rows"]) for item in details)
    if not details:
        raise AnnualMinbarValidationError(f"Canonical annual staging is empty for {year}")
    if invalid or rows != expected_rows:
        summary: dict[str, Any] = {
            "invalid_dates": [item["date"] for item in invalid],
            "expected_rows": expected_rows,
            "actual_rows": rows,
        }
        raise AnnualMinbarValidationError(
            f"Canonical annual staging failed validation for {year}: {summary}"
        )
    return {
        "valid": True,
        "year": year,
        "dates": [item["date"] for item in details],
        "date_count": len(details),
        "rows": rows,
        "details": details,
    }


def _atomic_promote(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copy2(source, temporary)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
