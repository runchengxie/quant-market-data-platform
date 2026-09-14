"""Private manifest helpers for A-share minute coverage."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pyarrow.parquet as pq
import yaml

from market_data_platform.providers._coverage_common import (
    _ANNUAL_FULL_TIME_MAX,
    _ANNUAL_PARTIAL_TIME_MAX,
    _DEAL_PATTERN,
    _PARTITION_PATTERN,
    _TUSHARE_BATCH_PATTERN,
    TUSHARE_FULL_DAY_PLAN_SCHEMA_VERSION,
    AnnualMinbarCoverageResult,
    ExpectedPartitionStats,
    TushareFullDayPlan,
    _date_set,
    _partition_path,
    _sha256_file,
    _validate_date,
)


def discover_tushare_minute_batches(root: str | Path) -> dict[str, list[Path]]:
    """Discover local TuShare batches grouped by date."""
    result: dict[str, list[Path]] = {}
    for path in sorted(Path(root).expanduser().glob("minute_*_batch*.parquet")):
        matched = _TUSHARE_BATCH_PATTERN.fullmatch(path.name)
        if matched is not None:
            result.setdefault(matched.group(1), []).append(path)
    return result


def discover_tushare_full_day_partitions(root: str | Path) -> dict[str, Path]:
    """Discover Hive day directories written by the hardened minute mirror."""
    result: dict[str, Path] = {}
    for path in sorted(Path(root).expanduser().glob("trade_date=*")):
        matched = _PARTITION_PATTERN.fullmatch(path.name)
        if matched is not None and path.is_dir():
            result[matched.group(1)] = path
    return result


def load_tushare_full_day_plan(path: str | Path) -> TushareFullDayPlan:
    """Load a pilot or production whole-day replacement plan."""
    plan_path = Path(path).expanduser()
    payload = _structured_payload(plan_path)
    if payload.get("schema_version") != TUSHARE_FULL_DAY_PLAN_SCHEMA_VERSION:
        raise ValueError(f"Unsupported TuShare full-day plan schema: {plan_path}")
    raw_dates = payload.get("dates")
    if not isinstance(raw_dates, list) or not all(isinstance(value, str) for value in raw_dates):
        raise ValueError(f"TuShare full-day plan has malformed dates: {plan_path}")
    phase = payload.get("phase")
    if phase not in {"pilot", "production"}:
        raise ValueError(f"TuShare full-day plan has an invalid phase: {plan_path}")
    return TushareFullDayPlan(
        phase=phase,  # type: ignore[arg-type]
        dates=tuple(raw_dates),
        sha256=_sha256_file(plan_path),
    )


def discover_guan_deal_files(root: str | Path) -> dict[str, Path]:
    """Discover canonical Guan deal files recursively, ignoring copy suffixes."""
    result: dict[str, Path] = {}
    for path in sorted(Path(root).expanduser().rglob("deal_*.parquet")):
        matched = _DEAL_PATTERN.fullmatch(path.name)
        if matched is not None:
            trade_date = matched.group(1)
            existing = result.get(trade_date)
            if existing is not None:
                raise ValueError(
                    "Duplicate Guan deal date below guan_deal_dir: "
                    f"{trade_date}: {existing}, {path}"
                )
            result[trade_date] = path
    return result


@dataclass(frozen=True)
class _OverlapAuditContext:
    path: Path
    output_root: Path
    tushare_batches: Mapping[str, Sequence[str | Path]]


def _overlap_audit_header(
    payload: Mapping[str, Any],
    path: Path,
    expected_dates: set[str],
) -> tuple[Mapping[str, Any], list[Any]]:
    inputs = payload.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError(f"Overlap audit has no input receipt: {path}")
    recorded_dates = _date_set(inputs.get("overlap_dates", []))
    summary = payload.get("summary")
    daily = payload.get("daily")
    if not (
        payload.get("schema_version") == "a_share.minute_overlap_audit.v1"
        and payload.get("status") == "passed"
        and payload.get("diagnostic_only") is True
        and payload.get("mutation_performed") is False
        and recorded_dates == expected_dates
        and isinstance(summary, Mapping)
        and int(summary.get("date_count", -1)) == len(expected_dates)
        and isinstance(daily, list)
    ):
        raise ValueError(
            "Overlap audit is not a passed read-only audit over the exact Guan/TuShare "
            f"date intersection: {path}; missing={sorted(expected_dates - recorded_dates)}, "
            f"unexpected={sorted(recorded_dates - expected_dates)}"
        )
    return inputs, daily


def _overlap_daily_records(
    daily: Sequence[Any],
    path: Path,
    expected_dates: set[str],
) -> dict[str, Mapping[str, Any]]:
    records: dict[str, Mapping[str, Any]] = {}
    for raw_record in daily:
        if not isinstance(raw_record, Mapping):
            raise ValueError(f"Overlap audit contains a malformed daily record: {path}")
        trade_date = _validate_date(str(raw_record.get("trade_date", "")))
        if trade_date in records:
            raise ValueError(f"Overlap audit contains duplicate daily records for {trade_date}")
        records[trade_date] = raw_record
    if set(records) != expected_dates:
        raise ValueError(f"Overlap audit daily records do not match its date receipt: {path}")
    return records


def _assert_overlap_file_receipt(
    receipt: Any,
    expected: Path,
    *,
    label: str,
    audit_path: Path,
) -> None:
    if not isinstance(receipt, Mapping):
        raise ValueError(f"Overlap audit is missing the {label} file receipt: {audit_path}")
    resolved = Path(str(receipt.get("path", ""))).expanduser().resolve()
    if resolved != expected.resolve() or not expected.is_file():
        raise ValueError(f"Overlap audit {label} file no longer matches: {expected}")
    stat = expected.stat()
    if (
        int(receipt.get("size", -1)) != stat.st_size
        or int(receipt.get("mtime_ns", -1)) != stat.st_mtime_ns
    ):
        raise ValueError(f"Overlap audit {label} file changed after auditing: {expected}")


def _validate_overlap_daily_receipts(
    records: Mapping[str, Mapping[str, Any]],
    context: _OverlapAuditContext,
) -> None:
    for trade_date, record in records.items():
        file_inputs = record.get("inputs")
        if not isinstance(file_inputs, Mapping):
            raise ValueError(f"Overlap audit is missing daily inputs for {trade_date}")
        _assert_overlap_file_receipt(
            file_inputs.get("canonical"),
            _partition_path(context.output_root, trade_date),
            label=f"canonical {trade_date}",
            audit_path=context.path,
        )
        expected_batch_paths = [
            Path(value).expanduser() for value in context.tushare_batches[trade_date]
        ]
        raw_batch_receipts = file_inputs.get("tushare_batches")
        if not isinstance(raw_batch_receipts, list):
            raise ValueError(f"Overlap audit is missing TuShare receipts for {trade_date}")
        receipts_by_path = {
            Path(str(receipt.get("path", ""))).expanduser().resolve(): receipt
            for receipt in raw_batch_receipts
            if isinstance(receipt, Mapping)
        }
        if set(receipts_by_path) != {batch.resolve() for batch in expected_batch_paths}:
            raise ValueError(f"Overlap audit TuShare file set changed for {trade_date}")
        for batch in expected_batch_paths:
            _assert_overlap_file_receipt(
                receipts_by_path[batch.resolve()],
                batch,
                label=f"TuShare {trade_date}",
                audit_path=context.path,
            )


def validate_overlap_audit(
    audit_path: str | Path,
    *,
    output_dir: str | Path,
    tushare_batch_dir: str | Path,
    expected_overlap_dates: Iterable[str],
    tushare_batches: Mapping[str, Sequence[str | Path]],
) -> Mapping[str, Any]:
    """Bind a passed overlap receipt to the current canonical and batch files."""
    path = Path(audit_path).expanduser()
    payload = _structured_payload(path)
    expected_dates = _date_set(expected_overlap_dates)
    inputs, daily = _overlap_audit_header(payload, path, expected_dates)

    output_root = Path(output_dir).expanduser().resolve()
    batch_root = Path(tushare_batch_dir).expanduser().resolve()
    if Path(str(inputs.get("canonical_dir", ""))).expanduser().resolve() != output_root:
        raise ValueError(f"Overlap audit canonical_dir does not match current output: {path}")
    if Path(str(inputs.get("tushare_batch_dir", ""))).expanduser().resolve() != batch_root:
        raise ValueError(f"Overlap audit TuShare directory does not match current input: {path}")

    records = _overlap_daily_records(daily, path, expected_dates)
    _validate_overlap_daily_receipts(
        records,
        _OverlapAuditContext(
            path=path,
            output_root=output_root,
            tushare_batches=tushare_batches,
        ),
    )
    return payload


def load_open_trade_dates(
    trade_calendar_path: str | Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    """Load open exchange dates from a TuShare trade calendar mirror."""
    path = Path(trade_calendar_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Trade calendar file not found: {path}")
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    else:
        frame = pq.ParquetFile(path).read().to_pandas()
    date_column = "cal_date" if "cal_date" in frame.columns else "trade_date"
    if date_column not in frame.columns:
        raise ValueError(f"Trade calendar {path} is missing cal_date/trade_date")
    if "is_open" in frame.columns:
        open_mask = frame["is_open"].astype(str).str.strip().isin({"1", "true", "True"})
        frame = frame.loc[open_mask]
    dates = {_validate_date(value) for value in frame[date_column].dropna().astype(str)}
    if start_date is not None:
        start = _validate_date(start_date)
        dates = {date for date in dates if date >= start}
    if end_date is not None:
        end = _validate_date(end_date)
        dates = {date for date in dates if date <= end}
    if start_date is not None and end_date is not None and start > end:
        raise ValueError("start_date must not be after end_date")
    return sorted(dates)


def _structured_payload(path: Path) -> Mapping[str, Any]:
    text = path.read_text(encoding="utf-8")
    value = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(value, Mapping):
        raise ValueError(f"Manifest is not a mapping: {path}")
    return value


def _manifest_date_records(value: Any) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []

    def visit(item: Any, *, date_list: bool = False) -> None:
        if isinstance(item, Mapping):
            record_date = item.get("trade_date", item.get("date"))
            if record_date is not None and re.fullmatch(r"\d{8}", str(record_date).strip()):
                records.append(item)
            for key, child in item.items():
                key_text = str(key)
                if re.fullmatch(r"\d{8}", key_text) and isinstance(child, Mapping):
                    records.append({"trade_date": key_text, **child})
                visit(
                    child,
                    date_list=key_text
                    in {
                        "dates",
                        "output_dates",
                        "output_dates_in_range",
                        "expected_output_dates",
                    },
                )
        elif isinstance(item, list):
            for child in item:
                visit(child, date_list=date_list)
        elif date_list and re.fullmatch(r"\d{8}", str(item).strip()):
            records.append({"trade_date": str(item).strip()})

    visit(value)
    return records


def _first_record_value(record: Mapping[str, Any], names: Sequence[str]) -> Any:
    output = record.get("output")
    candidates = [output, record] if isinstance(output, Mapping) else [record]
    for candidate in candidates:
        for name in names:
            value = candidate.get(name)
            if value is not None:
                return value
    return None


def _timestamp_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(pd.Timestamp(value))


def _clock_text(value: Any) -> str | None:
    timestamp = _timestamp_text(value)
    if timestamp is None:
        return None
    ts = cast("pd.Timestamp", pd.Timestamp(timestamp))
    return ts.strftime("%H:%M:%S")


def _annual_manifest_records(
    payload: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], dict[str, str]]:
    unit_profile_by_date: dict[str, str] = {}
    years = payload.get("years")
    if payload.get("schema_version") == "guan.annual_minbar.v1" and isinstance(years, Mapping):
        records: list[Mapping[str, Any]] = []
        for raw_entry in years.values():
            if not isinstance(raw_entry, Mapping) or raw_entry.get("status") != "complete":
                continue
            records.extend(_manifest_date_records(raw_entry))
            profile = raw_entry.get("unit_profile")
            profile_name = profile.get("name") if isinstance(profile, Mapping) else None
            if profile_name:
                for raw_date in raw_entry.get("dates", []):
                    unit_profile_by_date[_validate_date(str(raw_date))] = str(profile_name)
        return records, unit_profile_by_date
    return _manifest_date_records(payload), unit_profile_by_date


def _annual_record_time_max(
    record: Mapping[str, Any],
    trade_date: str,
    time_max_by_date: dict[str, str],
) -> str | None:
    time_max = _timestamp_text(_first_record_value(record, ("time_max",)))
    if time_max is not None:
        existing_time_max = time_max_by_date.get(trade_date)
        if existing_time_max is not None and existing_time_max != time_max:
            raise ValueError(f"Conflicting annual session maximum for {trade_date}")
        time_max_by_date[trade_date] = time_max
    return time_max


def _annual_record_stats(
    record: Mapping[str, Any],
    trade_date: str,
    time_max: str | None,
    unit_profile_by_date: Mapping[str, str],
) -> ExpectedPartitionStats | None:
    rows = _first_record_value(record, ("output_rows", "canonical_rows", "rows"))
    symbols = _first_record_value(
        record,
        ("output_symbols", "canonical_symbols", "symbols"),
    )
    if rows is None or symbols is None:
        return None
    row_count = int(rows)
    symbol_count = int(symbols)
    raw_traded_symbols = _first_record_value(
        record,
        ("output_traded_symbols", "traded_symbols"),
    )
    traded_symbols = int(raw_traded_symbols) if raw_traded_symbols is not None else None
    raw_vol_sum = _first_record_value(record, ("output_vol_sum", "vol_sum"))
    vol_sum = float(raw_vol_sum) if raw_vol_sum is not None else None
    raw_amount_sum = _first_record_value(record, ("output_amount_sum", "amount_sum"))
    amount_sum = float(raw_amount_sum) if raw_amount_sum is not None else None
    raw_unit_profile = _first_record_value(record, ("unit_profile",))
    unit_profile = (
        str(raw_unit_profile)
        if raw_unit_profile is not None
        else unit_profile_by_date.get(trade_date)
    )
    time_min = _timestamp_text(_first_record_value(record, ("time_min",)))
    raw_output_sha256 = _first_record_value(
        record,
        ("output_sha256", "partition_sha256", "sha256"),
    )
    output_sha256 = str(raw_output_sha256) if raw_output_sha256 is not None else None
    return ExpectedPartitionStats(
        rows=row_count,
        symbols=symbol_count,
        traded_symbols=traded_symbols,
        vol_sum=vol_sum,
        amount_sum=amount_sum,
        unit_profile=unit_profile,
        time_min=time_min,
        time_max=time_max,
        output_sha256=output_sha256,
    )


def _annual_partial_session_dates(time_max_by_date: Mapping[str, str]) -> set[str]:
    partial_session_dates: set[str] = set()
    for trade_date, time_max in time_max_by_date.items():
        clock = _clock_text(time_max)
        if clock == _ANNUAL_PARTIAL_TIME_MAX:
            partial_session_dates.add(trade_date)
        elif clock != _ANNUAL_FULL_TIME_MAX:
            raise ValueError(
                f"Unsupported annual session maximum for {trade_date}: {time_max}; "
                f"expected {_ANNUAL_FULL_TIME_MAX} or {_ANNUAL_PARTIAL_TIME_MAX}"
            )
    return partial_session_dates


def load_annual_minbar_manifest(
    manifest_path: str | Path,
) -> AnnualMinbarCoverageResult:
    """Load actual dates and optional exact output stats from an annual build manifest.

    The reader accepts daily records under common ``daily``/``partitions``
    layouts, date-keyed mappings, and explicit output-date lists.  This keeps
    coverage auditing independent from the annual transform implementation.
    """
    path = Path(manifest_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Annual minbar manifest not found: {path}")
    payload = _structured_payload(path)
    records, unit_profile_by_date = _annual_manifest_records(payload)
    dates: set[str] = set()
    stats: dict[str, ExpectedPartitionStats] = {}
    time_max_by_date: dict[str, str] = {}
    for record in records:
        raw_date = record.get("trade_date", record.get("date"))
        if raw_date is None:
            continue
        trade_date = _validate_date(str(raw_date))
        dates.add(trade_date)
        time_max = _annual_record_time_max(record, trade_date, time_max_by_date)
        candidate = _annual_record_stats(
            record,
            trade_date,
            time_max,
            unit_profile_by_date,
        )
        if candidate is None:
            continue
        existing = stats.get(trade_date)
        if existing is not None and existing != candidate:
            raise ValueError(f"Conflicting annual manifest stats for {trade_date}")
        stats[trade_date] = candidate
    if not dates:
        raise ValueError(f"Annual minbar manifest contains no daily dates: {path}")
    partial_session_dates = _annual_partial_session_dates(time_max_by_date)
    return AnnualMinbarCoverageResult(
        dates=dates,
        stats=stats,
        full_dates=dates - partial_session_dates,
        partial_session_dates=partial_session_dates,
    )


def _guan_deal_manifest_inventory(
    payload: Mapping[str, Any],
    path: Path,
) -> tuple[list[Any], set[str]]:
    if payload.get("schema_version") != "a_share.minute_1m.fused.v1":
        raise ValueError(f"Unsupported Guan deal manifest schema: {path}")
    if payload.get("status") != "passed":
        raise ValueError(f"Guan deal manifest is not complete and passed: {path}")
    actions = payload.get("build_actions")
    records = actions.get("guan_deal") if isinstance(actions, Mapping) else None
    if not isinstance(records, list):
        raise ValueError(f"Guan deal manifest has no deal build actions: {path}")
    options = payload.get("options")
    raw_override_dates = (
        options.get("annual_override_dates", []) if isinstance(options, Mapping) else []
    )
    if not isinstance(raw_override_dates, list):
        raise ValueError(f"Guan deal manifest annual_override_dates must be a list: {path}")
    override_dates = _date_set(str(value) for value in raw_override_dates)
    annual_overrides = payload.get("annual_overrides")
    if override_dates:
        if not isinstance(annual_overrides, Mapping):
            raise ValueError(f"Guan deal manifest has no annual override receipt: {path}")
        receipt_dates = _date_set(annual_overrides.get("dates", []))
        if (
            receipt_dates != override_dates
            or int(annual_overrides.get("count", -1)) != len(override_dates)
            or annual_overrides.get("policy") != "explicit_whole_day_deal_only"
        ):
            raise ValueError(f"Guan deal manifest annual override receipt is inconsistent: {path}")
    return records, override_dates
