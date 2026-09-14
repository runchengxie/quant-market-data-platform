"""Private materialization helpers for A-share minute coverage."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from market_data_platform.dataset_lock import minute_dataset_lock
from market_data_platform.providers._coverage_classify import (
    _classify_minute_coverage,
)
from market_data_platform.providers._coverage_common import (
    _SH_SZ_CODE_PATTERN,
    _TS_CODE_PATTERN,
    MISSING,
    TUSHARE_PARTIAL_TOP200,
    DailyCoverageExpectation,
    _CoverageClassificationRequest,
    _date_set,
    _partition_path,
    _sum_issue_maps,
    _validate_date,
)
from market_data_platform.providers.a_share_minute_fusion import (
    CANONICAL_MINUTE_COLUMNS,
    MINUTE_KEY_COLUMNS,
    normalize_tushare_partition,
    write_canonical_minute_partition,
)
from market_data_platform.providers.a_share_minute_price_flow import (
    NOTIONAL_HARD_GUARD_ISSUE,
    POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
    TUSHARE_VWAP_DIAGNOSTIC_SOURCES,
    VWAP_DIAGNOSTIC_POLICY,
    VWAP_DIAGNOSTIC_SOURCES,
    VWAP_EXTREME_UNIT_SCALE_ISSUE,
    VWAP_OHLC_DIAGNOSTIC,
    VWAP_PRICE_FLOW_INCOMPARABLE_UNIT_PROFILES,
    VWAP_SOURCE_GUARD_ISSUE,
    ZERO_VOLUME_NONZERO_AMOUNT_ISSUE,
    price_flow_diagnostics,
    tushare_price_flow_policy,
)


def _load_tushare_batches(paths: Sequence[Path]) -> pd.DataFrame:
    if not paths:
        raise ValueError("At least one TuShare batch file is required")
    frames = [pq.ParquetFile(path).read().to_pandas() for path in paths]
    return normalize_tushare_partition(pd.concat(frames, ignore_index=True))


def _frame_issues(
    trade_date: str,
    frame: pd.DataFrame,
    *,
    sh_sz_only: bool,
) -> dict[str, int]:
    duplicate_key_rows = int(frame.duplicated(list(MINUTE_KEY_COLUMNS), keep=False).sum())
    null_rows = int(frame.loc[:, list(CANONICAL_MINUTE_COLUMNS)].isna().any(axis=1).sum())
    numeric = frame.loc[:, list(CANONICAL_MINUTE_COLUMNS[2:])].apply(
        pd.to_numeric,
        errors="coerce",
    )
    values = numeric.to_numpy(dtype="float64", na_value=np.nan)
    non_finite_rows = int((~np.isfinite(values)).any(axis=1).sum())
    invalid_ohlc_rows = int(
        (
            (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1))
            | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1))
        ).sum()
    )
    negative_flow_rows = int(((numeric["vol"] < 0) | (numeric["amount"] < 0)).sum())
    flow_diagnostics = price_flow_diagnostics(numeric)

    trade_time = pd.to_datetime(frame["trade_time"], errors="coerce")
    valid_time = trade_time.notna()
    wrong_date_rows = int((valid_time & trade_time.dt.strftime("%Y%m%d").ne(trade_date)).sum())
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
    codes = frame["ts_code"].astype("string")
    invalid_ts_code_rows = int((~codes.str.fullmatch(_TS_CODE_PATTERN, na=False)).sum())
    unexpected_market_rows = (
        int((~codes.str.fullmatch(_SH_SZ_CODE_PATTERN, na=False)).sum()) if sh_sz_only else 0
    )
    return {
        "empty_partition": int(frame.empty),
        "duplicate_key_rows": duplicate_key_rows,
        "null_rows": null_rows,
        "non_finite_rows": non_finite_rows,
        "invalid_ohlc_rows": invalid_ohlc_rows,
        "negative_flow_rows": negative_flow_rows,
        **flow_diagnostics,
        "wrong_date_rows": wrong_date_rows,
        "invalid_ts_code_rows": invalid_ts_code_rows,
        "unexpected_market_rows": unexpected_market_rows,
        "non_minute_rows": non_minute_rows,
        "off_session_rows": off_session_rows,
    }


def _accepted_diagnostic_names(
    canonical_source: str | None,
    *,
    unit_profile: str | None = None,
) -> frozenset[str]:
    accepted: set[str] = set()
    if canonical_source in VWAP_DIAGNOSTIC_SOURCES:
        accepted.add(VWAP_OHLC_DIAGNOSTIC)
    if canonical_source in TUSHARE_VWAP_DIAGNOSTIC_SOURCES:
        accepted.update(
            {
                VWAP_SOURCE_GUARD_ISSUE,
                POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
            }
        )
    if (
        canonical_source == "guan_annual_minbar"
        and unit_profile in VWAP_PRICE_FLOW_INCOMPARABLE_UNIT_PROFILES
    ):
        accepted.update(
            {
                VWAP_SOURCE_GUARD_ISSUE,
                VWAP_EXTREME_UNIT_SCALE_ISSUE,
                NOTIONAL_HARD_GUARD_ISSUE,
                ZERO_VOLUME_NONZERO_AMOUNT_ISSUE,
                POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
            }
        )
    return frozenset(accepted)


def _accepted_diagnostics(
    issues: Mapping[str, int],
    *,
    canonical_source: str | None,
    unit_profile: str | None = None,
) -> dict[str, int]:
    return {
        name: count
        for name in _accepted_diagnostic_names(canonical_source, unit_profile=unit_profile)
        if (count := int(issues.get(name, 0)))
    }


def _fatal_issues(
    issues: Mapping[str, int],
    *,
    canonical_source: str | None,
    unit_profile: str | None = None,
) -> dict[str, int]:
    accepted = _accepted_diagnostic_names(canonical_source, unit_profile=unit_profile)
    return {name: int(count) for name, count in issues.items() if count and name not in accepted}


def _partial_shape_issues(
    frame: pd.DataFrame,
    *,
    expected_symbols: int,
    expected_bars_per_symbol: int,
) -> dict[str, int]:
    counts = frame.groupby("ts_code", observed=True).size()
    return {
        "unexpected_row_count": int(len(frame) != expected_symbols * expected_bars_per_symbol),
        "unexpected_symbol_count": int(frame["ts_code"].nunique() != expected_symbols),
        "symbols_with_unexpected_bar_count": int(
            counts.ne(expected_bars_per_symbol).to_numpy().sum()
        ),
    }


def _validate_partial_frame(
    trade_date: str,
    frame: pd.DataFrame,
    *,
    expected_symbols: int,
    expected_bars_per_symbol: int,
) -> dict[str, int]:
    issues = {
        **_frame_issues(trade_date, frame, sh_sz_only=True),
        **_partial_shape_issues(
            frame,
            expected_symbols=expected_symbols,
            expected_bars_per_symbol=expected_bars_per_symbol,
        ),
    }
    failures = _fatal_issues(issues, canonical_source="tushare_top200")
    if failures:
        raise ValueError(f"TuShare partial partition {trade_date} failed validation: {failures}")
    return _accepted_diagnostics(issues, canonical_source="tushare_top200")


@dataclass(frozen=True)
class _PartialMaterializationRequest:
    output_dir: str | Path
    trade_dates: Iterable[str]
    annual_dates: Iterable[str]
    deal_dates: Iterable[str]
    tushare_batches: Mapping[str, Sequence[str | Path]]
    partial_session_annual_dates: Iterable[str] = ()
    deal_override_dates: Iterable[str] = ()
    expected_symbols: int = 200
    expected_bars_per_symbol: int = 241
    replace_existing_partial: bool = True
    dry_run: bool = False


@dataclass(frozen=True)
class _PartialMaterializationContext:
    request: _PartialMaterializationRequest
    output_root: Path
    calendar: set[str]
    annual_partial: set[str]
    normalized_batches: dict[str, list[Path]]
    expectations: list[DailyCoverageExpectation]


def _partial_materialization_context(
    request: _PartialMaterializationRequest,
) -> _PartialMaterializationContext:
    calendar = _date_set(request.trade_dates)
    annual = _date_set(request.annual_dates)
    annual_partial = _date_set(request.partial_session_annual_dates)
    deal = _date_set(request.deal_dates)
    deal_overrides = _date_set(request.deal_override_dates)
    normalized_batches = {
        _validate_date(date): [Path(path).expanduser() for path in paths]
        for date, paths in request.tushare_batches.items()
    }
    expectations = _classify_minute_coverage(
        _CoverageClassificationRequest(
            trade_dates=calendar,
            annual_dates=annual,
            deal_dates=deal,
            tushare_dates=normalized_batches,
            partial_session_annual_dates=annual_partial,
            deal_override_dates=deal_overrides,
            partial_symbols=request.expected_symbols,
            partial_bars_per_symbol=request.expected_bars_per_symbol,
        )
    )
    return _PartialMaterializationContext(
        request=request,
        output_root=Path(request.output_dir).expanduser(),
        calendar=calendar,
        annual_partial=annual_partial,
        normalized_batches=normalized_batches,
        expectations=expectations,
    )


def _prepare_partial_materialization(
    context: _PartialMaterializationContext,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    prepared: dict[str, pd.DataFrame] = {}
    actions: list[dict[str, Any]] = []
    for expectation in context.expectations:
        paths = context.normalized_batches.get(expectation.trade_date)
        if paths is None:
            continue
        if expectation.has_guan_source:
            actions.append(
                {
                    "date": expectation.trade_date,
                    "status": "audit_only",
                    "protected_source": expectation.canonical_source,
                    "files": len(paths),
                }
            )
            continue
        if expectation.tier != TUSHARE_PARTIAL_TOP200:
            continue
        output_path = _partition_path(context.output_root, expectation.trade_date)
        if output_path.exists() and not context.request.replace_existing_partial:
            actions.append(
                {
                    "date": expectation.trade_date,
                    "status": "skipped_existing_partial",
                    "files": len(paths),
                }
            )
            continue
        if context.request.dry_run:
            actions.append(
                {
                    "date": expectation.trade_date,
                    "status": "planned_partial",
                    "files": len(paths),
                }
            )
            continue
        missing_files = [str(path) for path in paths if not path.is_file()]
        if missing_files:
            raise FileNotFoundError(f"Missing TuShare batch files: {missing_files}")
        frame = _load_tushare_batches(paths)
        accepted_diagnostics = _validate_partial_frame(
            expectation.trade_date,
            frame,
            expected_symbols=context.request.expected_symbols,
            expected_bars_per_symbol=context.request.expected_bars_per_symbol,
        )
        prepared[expectation.trade_date] = frame
        actions.append(
            {
                "date": expectation.trade_date,
                "status": "validated_partial",
                "files": len(paths),
                "rows": len(frame),
                "symbols": int(frame["ts_code"].nunique()),
                "accepted_diagnostics": accepted_diagnostics,
            }
        )
    return prepared, actions


def _write_prepared_partial_dates(
    context: _PartialMaterializationContext,
    prepared: Mapping[str, pd.DataFrame],
    actions: Sequence[dict[str, Any]],
) -> None:
    for trade_date, frame in prepared.items():
        write_canonical_minute_partition(
            frame,
            _partition_path(context.output_root, trade_date),
        )
    for action in actions:
        if action["status"] == "validated_partial":
            action["status"] = "written_partial"


def _partial_materialization_payload(
    context: _PartialMaterializationContext,
    prepared: Mapping[str, pd.DataFrame],
    actions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    outside_calendar = sorted(set(context.normalized_batches).difference(context.calendar))
    uncovered_dates = sorted(
        expectation.trade_date
        for expectation in context.expectations
        if expectation.tier == MISSING
    )
    accepted_price_flow_by_date = {
        str(action["date"]): dict(action["accepted_diagnostics"])
        for action in actions
        if "accepted_diagnostics" in action
    }
    accepted_price_flow_totals = _sum_issue_maps(*accepted_price_flow_by_date.values())
    return {
        "status": "planned" if context.request.dry_run else "completed",
        "written_partial_dates": sorted(prepared) if not context.request.dry_run else [],
        "protected_full_dates": sorted(
            {
                item.trade_date
                for item in context.expectations
                if item.is_full_sh_sz and item.trade_date in context.normalized_batches
            }
        ),
        "protected_partial_session_dates": sorted(
            context.annual_partial.intersection(context.normalized_batches)
        ),
        "protected_guan_dates": sorted(
            {
                item.trade_date
                for item in context.expectations
                if item.has_guan_source and item.trade_date in context.normalized_batches
            }
        ),
        "outside_calendar_batch_dates": outside_calendar,
        "uncovered_dates": uncovered_dates,
        "diagnostics": {
            "policy": VWAP_DIAGNOSTIC_POLICY,
            "tushare_price_flow_policy": dict(tushare_price_flow_policy()),
            "accepted_diagnostics": accepted_price_flow_totals,
            "dates_affected": sorted(
                date for date, counts in accepted_price_flow_by_date.items() if counts
            ),
            "by_date": accepted_price_flow_by_date,
            "hard_notional_guard": {
                "issue": NOTIONAL_HARD_GUARD_ISSUE,
                "rows": 0,
                "disposition": "fatal",
            },
        },
        "actions": actions,
    }


def _materialize_tushare_partial_dates(
    request: _PartialMaterializationRequest,
) -> dict[str, Any]:
    """Write TuShare only for dates without a Guan source.

    Batches overlapping an annual or deal date are reported as audit-only and
    never read into or written over the canonical partition.
    """
    context = _partial_materialization_context(request)
    prepared, actions = _prepare_partial_materialization(context)
    if not request.dry_run:
        _write_prepared_partial_dates(context, prepared, actions)
    return _partial_materialization_payload(context, prepared, actions)


def materialize_tushare_partial_dates(  # noqa: PLR0913
    output_dir: str | Path,
    *,
    trade_dates: Iterable[str],
    annual_dates: Iterable[str],
    deal_dates: Iterable[str],
    tushare_batches: Mapping[str, Sequence[str | Path]],
    partial_session_annual_dates: Iterable[str] = (),
    deal_override_dates: Iterable[str] = (),
    expected_symbols: int = 200,
    expected_bars_per_symbol: int = 241,
    replace_existing_partial: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Materialize partial dates under the lock shared by every minute writer."""
    request = _PartialMaterializationRequest(
        output_dir=output_dir,
        trade_dates=trade_dates,
        annual_dates=annual_dates,
        deal_dates=deal_dates,
        tushare_batches=tushare_batches,
        partial_session_annual_dates=partial_session_annual_dates,
        deal_override_dates=deal_override_dates,
        expected_symbols=expected_symbols,
        expected_bars_per_symbol=expected_bars_per_symbol,
        replace_existing_partial=replace_existing_partial,
        dry_run=dry_run,
    )
    if dry_run:
        return _materialize_tushare_partial_dates(request)
    with minute_dataset_lock(output_dir, operation="materialize-tushare-partial-minutes"):
        return _materialize_tushare_partial_dates(request)
