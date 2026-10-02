from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from quant_market_data_platform.parquet_scanning import ParquetBatchScanner
from quant_market_data_platform.providers.tushare_a_share_quality_part01 import (
    PROJECTED_COLUMNS,
    RESEARCH_PROFILE,
    SAMPLE_LIMIT,
    SEVERITY_RANK,
    DailyCleanValidationOptions,
    _Accumulator,
    _BuildChecksRequest,
    _check_row,
    _load_manifest,
    _load_trade_calendar,
    _normalize_fail_on_severity,
    _record_common_checks,
    _record_research_checks,
    _safe_bool,
    _ValidationReportRequest,
)
from quant_market_data_platform.runtime_memory import (
    MemoryPolicy,
)

from .tushare_a_share_daily_schema import PRICE_COLUMNS


def _load_st_keys(
    manifest: dict[str, Any] | None,
) -> tuple[dict[tuple[str, str], str | None] | None, str | None]:
    source = (manifest or {}).get("inputs", {}).get("st_history_file")
    if not source:
        return None, "dated ST history source is missing"
    try:
        path = Path(source).expanduser().resolve()
        receipt_path = path.with_name("st_history_reconstructed.receipt.json")
        if not receipt_path.is_file():
            receipt_path = path.with_suffix(".receipt.json")
        if not path.is_file() or not receipt_path.is_file():
            raise ValueError("dated ST history source or receipt is missing")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        from quant_market_data_platform.st_history_contract import has_st_availability_contract

        if not has_st_availability_contract(receipt):
            raise ValueError("dated ST history receipt does not include the availability contract")
        if (
            receipt.get("quality_status") != "complete"
            or receipt.get("source_quality_status", "complete") != "complete"
        ):
            raise ValueError("dated ST history receipt is not quality complete")
        with path.open("rb") as source_file:
            digest = hashlib.file_digest(source_file, "sha256").hexdigest()
        if receipt.get("history_sha256", receipt.get("sha256")) != digest:
            raise ValueError("dated ST history receipt hash does not match")
        query = (manifest or {}).get("query", {})
        if str(query.get("start_date", "99999999")) < receipt.get("start_date", "99999999") or str(
            query.get("end_date", "00000000")
        ) > receipt.get("end_date", "00000000"):
            raise ValueError("dated ST history receipt does not cover daily_clean dates")
        if "available_from" not in pq.read_schema(path).names:
            raise ValueError("dated ST history is missing available_from")
        history = pd.read_parquet(path, columns=["ts_code", "trade_date", "available_from"])
        if history.duplicated(["ts_code", "trade_date"]).any():
            raise ValueError("dated ST history has duplicate symbol/date keys")
        availability = history["available_from"].astype("string").str.replace("-", "", regex=False)
        valid = availability.isna() | availability.str.fullmatch(r"\d{8}", na=False)
        if not bool(valid.all()):
            raise ValueError("dated ST history contains malformed availability dates")
        history["available_from"] = availability
    except (OSError, ValueError, KeyError, pd.errors.ParserError) as error:
        return None, f"dated ST history could not be read: {error}"
    return {
        (str(symbol), str(day)): None if pd.isna(available_from) else str(available_from)
        for symbol, day, available_from in history[
            ["ts_code", "trade_date", "available_from"]
        ].itertuples(index=False, name=None)
    }, None


def _build_checks(request: _BuildChecksRequest) -> list[dict[str, Any]]:
    accumulator = request.accumulator
    counts = accumulator.counts
    checks = [
        _check_row(
            check="required_columns",
            severity="error",
            message=f"missing columns: {sorted(request.required_columns - accumulator.columns)}",
            affected=len(request.required_columns - accumulator.columns),
            samples={},
        ),
        _check_row(
            check="manifest_presence",
            severity="error",
            message="daily_clean manifest.yml must exist and contain a mapping.",
            affected=int(request.manifest is None),
            samples={},
        ),
    ]
    structural = (
        "symbol_format",
        "trade_date_format",
        "duplicate_symbol_trade_date",
        "symbol_trade_date_order",
        *(f"numeric_{column}" for column in PRICE_COLUMNS),
        "non_negative_vol",
        "non_negative_amount",
        "positive_non_suspended_prices",
        "ohlc_bounds",
    )
    for check in structural:
        checks.append(
            _check_row(
                check=check,
                severity="error",
                message=f"{check} invariant violations.",
                affected=counts.get(check, 0),
                samples=accumulator.samples,
            )
        )
    checks.append(_manifest_reconciliation_check(accumulator, request.manifest))
    if request.profile == RESEARCH_PROFILE:
        for check in (
            "limit_price_order",
            "limit_up_flag_consistency",
            "limit_down_flag_consistency",
            "suspension_consistency",
            "listed_days",
            "board_classification",
        ):
            checks.append(
                _check_row(
                    check=check,
                    severity="error",
                    message=f"{check} invariant violations.",
                    affected=counts.get(check, 0),
                    samples=accumulator.samples,
                )
            )
        checks.append(
            {
                "check": "trading_calendar_input",
                "severity": "error",
                "status": "failed" if request.trade_calendar_path is None else "passed",
                "message": "Research-profile validation requires an A 股 trading calendar input.",
                "affected_rows": int(request.trade_calendar_path is None),
                "sample_rows": [],
            }
        )
        relevant_calendar_dates = {
            date
            for date in request.trade_calendar_dates
            if (accumulator.start_date is None or date >= accumulator.start_date)
            and (accumulator.end_date is None or date <= accumulator.end_date)
        }
        missing_dates = sorted(relevant_calendar_dates - accumulator.trade_dates)
        checks.append(
            {
                "check": "trading_calendar_coverage",
                "severity": "error",
                "status": "failed" if missing_dates else "passed",
                "message": "Open trading dates must be represented in daily_clean.",
                "affected_rows": len(missing_dates),
                "sample_rows": [{"trade_date": date} for date in missing_dates[:SAMPLE_LIMIT]],
            }
        )
        pct_count = counts.get("pct_chg_consistency", 0)
        warning_rate = pct_count / accumulator.rows if accumulator.rows else 0.0
        checks.append(
            {
                **_check_row(
                    check="pct_chg_consistency",
                    severity="warning",
                    message="pct_chg differs from the close/pre_close implied value.",
                    affected=pct_count,
                    samples=accumulator.samples,
                ),
                "affected_rate": warning_rate,
                "max_warning_rate": request.max_warning_rate,
            }
        )
        st_history_file = (request.manifest or {}).get("inputs", {}).get("st_history_file")
        checks.append(
            {
                "check": "st_provenance",
                "severity": "error",
                "status": "passed" if st_history_file and not request.st_source_error else "failed",
                "message": request.st_source_error
                or ("Research-profile is_st requires validated dated ST history."),
                "affected_rows": (
                    0 if st_history_file and not request.st_source_error else accumulator.rows
                ),
                "sample_rows": [],
            }
        )
        checks.append(
            _check_row(
                check="st_flag_consistency",
                severity="error",
                message="daily_clean is_st disagrees with its dated ST source.",
                affected=counts.get("st_flag_consistency", 0),
                samples=accumulator.samples,
            )
        )
        checks.append(
            _check_row(
                check="st_availability_consistency",
                severity="error",
                message="daily_clean ST availability disagrees with its dated ST source.",
                affected=counts.get("st_availability_consistency", 0),
                samples=accumulator.samples,
            )
        )
    return checks


def _manifest_reconciliation_check(
    accumulator: _Accumulator,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    if manifest is None:
        return _check_row(
            check="manifest_reconciliation",
            severity="error",
            message="Cannot reconcile daily_clean rows without manifest.yml.",
            affected=1,
            samples={},
        )
    totals = manifest.get("totals", {}) if isinstance(manifest.get("totals"), dict) else {}
    query = manifest.get("query", {}) if isinstance(manifest.get("query"), dict) else {}
    actual = {
        "rows": accumulator.rows,
        "symbols": len(accumulator.symbols),
        "files": accumulator.files,
        "start_date": accumulator.start_date,
        "end_date": accumulator.end_date,
    }
    expected = {
        "rows": totals.get("rows"),
        "symbols": totals.get("symbols"),
        "files": totals.get("files"),
        "start_date": query.get("start_date"),
        "end_date": query.get("end_date"),
    }
    mismatches = {
        key: {"expected": expected[key], "actual": actual[key]}
        for key in actual
        if expected[key] != actual[key]
    }
    return {
        "check": "manifest_reconciliation",
        "severity": "error",
        "status": "failed" if mismatches else "passed",
        "message": "daily_clean manifest totals and date range must match scanned files.",
        "affected_rows": len(mismatches),
        "mismatches": mismatches,
        "sample_rows": [],
    }


def _quality_verdict(
    checks: list[dict[str, Any]],
    *,
    fail_on_severity: str,
    max_warning_rate: float,
) -> dict[str, Any]:
    threshold = _normalize_fail_on_severity(fail_on_severity)
    issues = [check for check in checks if check["status"] == "failed"]
    severity_counts = {
        severity: sum(1 for check in issues if check["severity"] == severity)
        for severity in ("error", "warning", "info")
    }
    triggered: list[str] = []
    for check in issues:
        severity = str(check["severity"])
        if severity == "error":
            triggered.append(str(check["check"]))
            continue
        if threshold == "none" or SEVERITY_RANK[severity] < SEVERITY_RANK[threshold]:
            continue
        if severity == "warning" and float(check.get("affected_rate", 0.0)) <= max_warning_rate:
            continue
        triggered.append(str(check["check"]))
    overall = next(
        (severity for severity in ("error", "warning", "info") if severity_counts[severity]),
        "none",
    )
    return {
        "overall_severity": overall,
        "issue_count": len(issues),
        "severity_counts": severity_counts,
        "fail_on_severity": threshold,
        "max_warning_rate": max_warning_rate,
        "gate_triggered": bool(triggered),
        "gate_status": "fail" if triggered else "pass",
        "failing_checks": triggered,
    }


def _collect_validation_inputs(
    options: DailyCleanValidationOptions,
) -> tuple[Path, list[Path], dict[str, Any] | None, Path, str | None, set[str]]:
    root = Path(options.daily_clean_dir)
    if not root.exists():
        raise FileNotFoundError(f"daily_clean asset directory not found: {root}")
    data_root = root / "data" if (root / "data").exists() else root
    files = sorted(data_root.glob("**/*.parquet"))
    manifest_path, manifest = _load_manifest(root)
    trade_calendar_path, trade_calendar_dates = _load_trade_calendar(options.trade_cal_file)
    return (
        root,
        files,
        manifest,
        manifest_path,
        trade_calendar_path,
        trade_calendar_dates,
    )


def _collect_readable_parquet_files(
    *,
    files: list[Path],
    accumulator: _Accumulator,
) -> tuple[list[Path], list[dict[str, str]]]:
    unreadable: list[dict[str, str]] = []
    readable_files: list[Path] = []
    for path in files:
        try:
            accumulator.columns.update(pq.ParquetFile(path).schema_arrow.names)
        except Exception as exc:  # pragma: no cover - backend-specific parse errors
            unreadable.append({"path": str(path), "error": str(exc)})
        else:
            readable_files.append(path)
    return readable_files, unreadable


def _scan_validation_frames(
    *,
    options: DailyCleanValidationOptions,
    scanner: ParquetBatchScanner,
    accumulator: _Accumulator,
    readable_files: list[Path],
    st_keys: dict[tuple[str, str], str | None] | None,
) -> None:
    for path, frame in scanner.iter_frames(readable_files):
        _record_common_checks(accumulator, frame, path=path)
        if options.profile == RESEARCH_PROFILE:
            if st_keys is not None and {"symbol", "trade_date", "is_st"} <= set(frame):
                expected = pd.Series(
                    [
                        (symbol, day) in st_keys
                        for symbol, day in zip(
                            frame.symbol.astype(str), frame.trade_date.astype(str), strict=True
                        )
                    ],
                    index=frame.index,
                )
                accumulator.increment(
                    "st_flag_consistency",
                    _safe_bool(frame, "is_st").ne(expected),
                    frame,
                    sample_columns=("symbol", "trade_date", "is_st"),
                )
                expected_availability = pd.Series(
                    [
                        st_keys.get((str(symbol), str(day)))
                        for symbol, day in zip(
                            frame.symbol.astype(str), frame.trade_date.astype(str), strict=True
                        )
                    ],
                    index=frame.index,
                    dtype="string",
                )
                actual_availability = (
                    frame["st_available_from"].astype("string").str.replace("-", "", regex=False)
                )
                accumulator.increment(
                    "st_availability_consistency",
                    actual_availability.fillna("").ne(expected_availability.fillna("")),
                    frame,
                    sample_columns=("symbol", "trade_date", "is_st", "st_available_from"),
                )
            _record_research_checks(
                accumulator,
                frame,
                pct_chg_tolerance=options.pct_chg_tolerance,
            )


def _append_minimum_size_check(
    checks: list[dict[str, Any]],
    *,
    accumulator: _Accumulator,
    min_rows: int,
    min_symbols: int,
) -> None:
    if accumulator.rows < min_rows or len(accumulator.symbols) < min_symbols:
        checks.append(
            {
                "check": "minimum_size",
                "severity": "error",
                "status": "failed",
                "message": (
                    f"below minimum size: rows={accumulator.rows} "
                    f"symbols={len(accumulator.symbols)}"
                ),
                "affected_rows": 1,
                "sample_rows": [],
            }
        )
        return
    checks.append(
        {
            "check": "minimum_size",
            "severity": "error",
            "status": "passed",
            "message": "daily_clean minimum size requirements are satisfied.",
            "affected_rows": 0,
            "sample_rows": [],
        }
    )


def _validation_report(request: _ValidationReportRequest) -> dict[str, Any]:
    options = request.options
    accumulator = request.accumulator
    checks = request.checks
    st_history_file = (request.manifest or {}).get("inputs", {}).get("st_history_file")
    checks.append(
        {
            "check": "file_readability",
            "severity": "error",
            "status": "failed" if request.unreadable else "passed",
            "message": "All daily_clean Parquet files must be readable.",
            "affected_rows": len(request.unreadable),
            "sample_rows": request.unreadable[:SAMPLE_LIMIT],
        }
    )
    _append_minimum_size_check(
        checks,
        accumulator=accumulator,
        min_rows=options.min_rows,
        min_symbols=options.min_symbols,
    )
    verdict = _quality_verdict(
        checks,
        fail_on_severity=options.fail_on_severity,
        max_warning_rate=options.max_warning_rate,
    )
    errors = [
        str(check["message"])
        for check in checks
        if check["status"] == "failed" and check["severity"] == "error"
    ]
    return {
        "schema_version": "tushare.a_share.daily_clean.validation.v2",
        "dataset": "daily_clean",
        "market": "a_share",
        "provider": "tushare",
        "profile": options.profile,
        "status": "failed" if verdict["gate_triggered"] else "passed",
        "quality_verdict": verdict,
        "checks": checks,
        "metrics": {
            "rows": accumulator.rows,
            "symbols": len(accumulator.symbols),
            "files": accumulator.files,
            "start_date": accumulator.start_date,
            "end_date": accumulator.end_date,
            "trade_dates": len(accumulator.trade_dates),
        },
        "totals": {"rows": accumulator.rows, "symbols": len(accumulator.symbols)},
        "samples": accumulator.samples,
        "lineage": {
            "daily_clean_dir": str(request.root),
            "manifest": str(request.manifest_path),
            "trade_cal_file": request.trade_calendar_path,
            "st_provenance": (
                "invalid_dated_history"
                if st_history_file and request.st_source_error
                else (
                    "validated_historical_effective_date"
                    if st_history_file
                    else "unknown_no_dated_history"
                )
            ),
            "st_availability_contract": (request.manifest or {})
            .get("contracts", {})
            .get("st_availability", "unknown"),
            "daily_basic_provenance": "daily_valuation_overlay_not_pit_fundamentals",
        },
        "scanner": request.scanner.telemetry.to_dict(),
        "build": {"validation_mode": "projected_parquet_batch_scan"},
        "thresholds": {
            "min_rows": options.min_rows,
            "min_symbols": options.min_symbols,
            "fail_on_severity": options.fail_on_severity,
            "max_warning_rate": options.max_warning_rate,
            "pct_chg_tolerance": options.pct_chg_tolerance,
            "batch_rows": options.batch_rows,
            "memory_policy": request.policy.to_dict(),
            "batch_rows_policy": request.policy.batch_rows_to_dict(),
        },
        "quality": {"duplicate_rows": accumulator.counts.get("duplicate_symbol_trade_date", 0)},
        "errors": errors,
    }


def _execute_validation(options: DailyCleanValidationOptions) -> dict[str, Any]:
    (
        root,
        files,
        manifest,
        manifest_path,
        trade_calendar_path,
        trade_calendar_dates,
    ) = _collect_validation_inputs(options)
    required = options.required_columns()
    policy = MemoryPolicy(
        soft_available_mb=options.memory_soft_limit_mb,
        hard_available_mb=options.memory_hard_limit_mb,
        target_batch_rows=options.batch_rows,
    )
    scanner = ParquetBatchScanner(
        columns=PROJECTED_COLUMNS,
        batch_rows=options.batch_rows,
        memory_policy=policy,
        stage="a_share_daily_clean_validation",
    )
    accumulator = _Accumulator(files=len(files))
    readable_files, unreadable = _collect_readable_parquet_files(
        files=files,
        accumulator=accumulator,
    )
    st_keys, st_source_error = (
        _load_st_keys(manifest) if options.profile == RESEARCH_PROFILE else (None, None)
    )
    _scan_validation_frames(
        options=options,
        scanner=scanner,
        accumulator=accumulator,
        readable_files=readable_files,
        st_keys=st_keys,
    )
    checks = _build_checks(
        _BuildChecksRequest(
            accumulator=accumulator,
            profile=options.profile,
            required_columns=required,
            manifest=manifest,
            trade_calendar_path=trade_calendar_path,
            trade_calendar_dates=trade_calendar_dates,
            max_warning_rate=options.max_warning_rate,
            st_source_error=st_source_error,
        )
    )
    report = _validation_report(
        _ValidationReportRequest(
            options=options,
            root=root,
            manifest_path=manifest_path,
            manifest=manifest,
            trade_calendar_path=trade_calendar_path,
            accumulator=accumulator,
            checks=checks,
            policy=policy,
            scanner=scanner,
            unreadable=unreadable,
            st_source_error=st_source_error,
        )
    )
    if options.out is not None:
        output = Path(options.out).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report


def validate_a_share_daily_clean(
    options: DailyCleanValidationOptions,
) -> dict[str, Any]:
    return _execute_validation(options)
