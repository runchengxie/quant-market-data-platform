"""Private materialization helpers for A-share minute coverage."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from market_data_platform.dataset_lock import minute_dataset_lock
from market_data_platform.providers._coverage_classify import (
    _classify_minute_coverage,
)
from market_data_platform.providers._coverage_common import (
    _ANNUAL_FULL_TIME_MAX,
    _ANNUAL_PARTIAL_TIME_MAX,
    _DEAL_FULL_TIME_MIN,
    _PARTITION_PATTERN,
    _TUSHARE_FULL_TIME_MIN,
    ANNUAL_FULL_SH_SZ,
    DEAL_FULL_SH_SZ,
    GUAN_PARTIAL_SESSION,
    MISSING,
    TUSHARE_FULL_A_SHARE,
    TUSHARE_FULL_DAY_RECEIPT_SCHEMA_VERSION,
    TUSHARE_PARTIAL_TOP200,
    DailyCoverageExpectation,
    ExpectedPartitionStats,
    MarketReferenceStats,
    TushareFullDayPhase,
    TushareFullDayPlan,
    _atomic_write_json,
    _CoverageClassificationRequest,
    _date_set,
    _partition_path,
    _sha256_file,
    _sum_issue_maps,
    _validate_date,
)
from market_data_platform.providers._coverage_manifest import (
    _clock_text,
    _timestamp_text,
)
from market_data_platform.providers._coverage_materialize_part01 import (
    _accepted_diagnostics,
    _fatal_issues,
    _frame_issues,
    _partial_shape_issues,
)
from market_data_platform.providers.a_share_minute_fusion import (
    CANONICAL_MINUTE_COLUMNS,
    normalize_tushare_partition,
    write_canonical_minute_partition,
)
from market_data_platform.providers.a_share_minute_price_flow import (
    NOTIONAL_HARD_GUARD_ISSUE,
    POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
    VWAP_EXTREME_UNIT_SCALE_ISSUE,
    VWAP_OHLC_DIAGNOSTIC,
    VWAP_SOURCE_GUARD_ISSUE,
    ZERO_VOLUME_NONZERO_AMOUNT_ISSUE,
    tushare_price_flow_policy,
)


def _read_validated_tushare_full_day(
    partition_dir: Path,
    *,
    trade_date: str,
) -> tuple[pd.DataFrame, ExpectedPartitionStats, dict[str, Any]]:
    from market_data_platform.providers.tushare_a_share_mins import (
        MINUTE_BARS_PER_DAY,
        validate_complete_minute_partition,
    )

    promotion = validate_complete_minute_partition(
        partition_dir,
        trade_date=trade_date,
        require_full_universe=True,
    )
    frame = normalize_tushare_partition(Path(str(promotion["partition_path"])))
    expected_symbols = {str(value) for value in promotion["expected_symbols"]}
    actual_symbols = set(frame["ts_code"].astype(str))
    issues = {
        **_frame_issues(trade_date, frame, sh_sz_only=False),
        **_partial_shape_issues(
            frame,
            expected_symbols=len(expected_symbols),
            expected_bars_per_symbol=MINUTE_BARS_PER_DAY,
        ),
        "sidecar_symbol_set_mismatch": int(actual_symbols != expected_symbols),
    }
    failures = _fatal_issues(issues, canonical_source="tushare_full_day")
    if failures:
        raise ValueError(f"TuShare full-day partition {trade_date} failed validation: {failures}")

    numeric = frame.loc[:, list(CANONICAL_MINUTE_COLUMNS[2:])]
    time_min = str(frame["trade_time"].min())
    time_max = str(frame["trade_time"].max())
    stats = ExpectedPartitionStats(
        rows=len(frame),
        symbols=len(expected_symbols),
        traded_symbols=int(frame.loc[frame["vol"].gt(0), "ts_code"].nunique()),
        vol_sum=float(numeric["vol"].sum()),
        amount_sum=float(numeric["amount"].sum()),
        unit_profile="tushare_shares_yuan",
        time_min=time_min,
        time_max=time_max,
    )
    compact_promotion = {
        key: value for key, value in promotion.items() if key != "expected_symbols"
    }
    price_flow_issue_names = (
        VWAP_OHLC_DIAGNOSTIC,
        VWAP_SOURCE_GUARD_ISSUE,
        NOTIONAL_HARD_GUARD_ISSUE,
        VWAP_EXTREME_UNIT_SCALE_ISSUE,
        ZERO_VOLUME_NONZERO_AMOUNT_ISSUE,
        POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
    )
    source_receipt = {
        **compact_promotion,
        "selected_market_scope": "SH_SZ_BJ",
        "selected_rows": len(frame),
        "selected_symbols": len(expected_symbols),
        "selected_traded_symbols": stats.traded_symbols,
        "selected_vol_sum": stats.vol_sum,
        "selected_amount_sum": stats.amount_sum,
        "selected_time_min": time_min,
        "selected_time_max": time_max,
        "price_flow_diagnostics": {
            name: int(issues.get(name, 0)) for name in price_flow_issue_names
        },
        "tushare_price_flow_validation": dict(tushare_price_flow_policy()),
        "accepted_diagnostics": _accepted_diagnostics(
            issues,
            canonical_source="tushare_full_day",
        ),
    }
    return frame, stats, source_receipt


@dataclass(frozen=True)
class _FullDayMaterializationRequest:
    output_dir: str | Path
    trade_dates: Iterable[str]
    annual_dates: Iterable[str]
    deal_dates: Iterable[str]
    tushare_dates: Iterable[str]
    tushare_full_partitions: Mapping[str, str | Path]
    replacement_dates: Iterable[str]
    phase: TushareFullDayPhase
    plan_sha256: str | None = None
    partial_session_annual_dates: Iterable[str] = ()
    deal_override_dates: Iterable[str] = ()
    manifest_path: str | Path | None = None
    dry_run: bool = False


def _materialize_tushare_full_days(
    request: _FullDayMaterializationRequest,
) -> dict[str, Any]:
    """Atomically replace eligible dates with complete TuShare day partitions.

    An explicit plan may select a top-200-only date, a Guan 14:57 partial-session
    date, or one with no baseline source.  The old target is never read into the
    promoted frame, making intraday or closing-tail source splicing impossible.
    """
    plan = TushareFullDayPlan(
        phase=request.phase,
        dates=tuple(request.replacement_dates),
        sha256=request.plan_sha256,
    )
    calendar = _date_set(request.trade_dates)
    annual = _date_set(request.annual_dates)
    deal = _date_set(request.deal_dates)
    tushare = _date_set(request.tushare_dates)
    annual_partial = _date_set(request.partial_session_annual_dates)
    deal_overrides = _date_set(request.deal_override_dates)
    requested = set(plan.dates)
    outside_calendar = sorted(requested - calendar)
    if outside_calendar:
        raise ValueError(
            f"TuShare full-day replacement dates are outside calendar: {outside_calendar}"
        )

    baseline = _classify_minute_coverage(
        _CoverageClassificationRequest(
            trade_dates=calendar,
            annual_dates=annual,
            deal_dates=deal,
            tushare_dates=tushare,
            partial_session_annual_dates=annual_partial,
            deal_override_dates=deal_overrides,
        )
    )
    baseline_by_date = {item.trade_date: item for item in baseline}
    eligible_tiers = {GUAN_PARTIAL_SESSION, MISSING, TUSHARE_PARTIAL_TOP200}
    ineligible = {
        date: baseline_by_date[date].tier
        for date in plan.dates
        if baseline_by_date[date].tier not in eligible_tiers
    }
    if ineligible:
        raise ValueError(
            "TuShare full-day replacements target only missing, top200, or Guan partial-session "
            f"dates: {ineligible}"
        )

    normalized_partitions = {
        _validate_date(date): Path(path).expanduser()
        for date, path in request.tushare_full_partitions.items()
    }
    missing_sources = sorted(requested - set(normalized_partitions))
    if missing_sources:
        raise FileNotFoundError(f"Missing TuShare full-day source partitions: {missing_sources}")

    planned_stats: dict[str, ExpectedPartitionStats] = {}
    source_receipts: dict[str, dict[str, Any]] = {}
    actions: list[dict[str, Any]] = []
    for date in plan.dates:
        _frame, stats, source_receipt = _read_validated_tushare_full_day(
            normalized_partitions[date],
            trade_date=date,
        )
        planned_stats[date] = stats
        source_receipts[date] = source_receipt
        del _frame
        actions.append(
            {
                "date": date,
                "status": "validated_full_day" if not request.dry_run else "planned_full_day",
                "replaces_tier": baseline_by_date[date].tier,
                "replacement_unit": "whole_trade_date",
                "rows": stats.rows,
                "symbols": stats.symbols,
                "market_scope": "SH_SZ_BJ",
            }
        )

    output_root = Path(request.output_dir).expanduser()
    if not request.dry_run:
        for action in actions:
            date = str(action["date"])
            frame, stats, source_receipt = _read_validated_tushare_full_day(
                normalized_partitions[date],
                trade_date=date,
            )
            binding_fields = ("partition_sha256", "sidecar_sha256", "universe_hash")
            if stats != planned_stats[date] or any(
                source_receipt[field] != source_receipts[date][field] for field in binding_fields
            ):
                raise RuntimeError(
                    f"TuShare full-day source changed between validation and write: {date}"
                )
            output_path = _partition_path(output_root, date)
            write_canonical_minute_partition(frame, output_path)
            output_stat = output_path.stat()
            action.update(
                {
                    "status": "written_full_day",
                    "output_path": str(output_path),
                    "output_size_bytes": output_stat.st_size,
                    "output_sha256": _sha256_file(output_path),
                }
            )

    payload: dict[str, Any] = {
        "schema_version": TUSHARE_FULL_DAY_RECEIPT_SCHEMA_VERSION,
        "status": "planned" if request.dry_run else "passed",
        "generated_at": datetime.now(UTC).isoformat(),
        "phase": plan.phase,
        "plan_sha256": plan.sha256,
        "output_dir": str(output_root),
        "policy": {
            "replacement_unit": "whole_trade_date",
            "intraday_source_merge": "forbidden",
            "eligible_input_tiers": sorted(eligible_tiers),
            "source_requirement": "bound_complete_full_universe_sidecar",
            "market_scope": "SH_SZ_BJ",
            "tushare_price_flow_validation": dict(tushare_price_flow_policy()),
        },
        "dates": list(plan.dates),
        "summary": {
            "date_count": len(plan.dates),
            "top200_replacement_dates": sum(
                item["replaces_tier"] == TUSHARE_PARTIAL_TOP200 for item in actions
            ),
            "guan_partial_session_replacement_dates": sum(
                item["replaces_tier"] == GUAN_PARTIAL_SESSION for item in actions
            ),
            "missing_replacement_dates": sum(item["replaces_tier"] == MISSING for item in actions),
            "rows": sum(item.rows for item in planned_stats.values()),
            "symbols_by_date": {date: stats.symbols for date, stats in planned_stats.items()},
        },
        "expected_partition_stats": {date: asdict(stats) for date, stats in planned_stats.items()},
        "source_receipts": source_receipts,
        "actions": actions,
    }
    if request.manifest_path is not None:
        _atomic_write_json(payload, Path(request.manifest_path).expanduser())
    return payload


def materialize_tushare_full_days(  # noqa: PLR0913
    output_dir: str | Path,
    *,
    trade_dates: Iterable[str],
    annual_dates: Iterable[str],
    deal_dates: Iterable[str],
    tushare_dates: Iterable[str],
    tushare_full_partitions: Mapping[str, str | Path],
    replacement_dates: Iterable[str],
    phase: TushareFullDayPhase,
    plan_sha256: str | None = None,
    partial_session_annual_dates: Iterable[str] = (),
    deal_override_dates: Iterable[str] = (),
    manifest_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Materialize whole-day TuShare replacements under the minute writer lock."""
    request = _FullDayMaterializationRequest(
        output_dir=output_dir,
        trade_dates=trade_dates,
        annual_dates=annual_dates,
        deal_dates=deal_dates,
        tushare_dates=tushare_dates,
        tushare_full_partitions=tushare_full_partitions,
        replacement_dates=replacement_dates,
        phase=phase,
        plan_sha256=plan_sha256,
        partial_session_annual_dates=partial_session_annual_dates,
        deal_override_dates=deal_override_dates,
        manifest_path=manifest_path,
        dry_run=dry_run,
    )
    if dry_run:
        return _materialize_tushare_full_days(request)
    with minute_dataset_lock(output_dir, operation="materialize-tushare-full-days"):
        return _materialize_tushare_full_days(request)


def _discover_output_layout(root: Path) -> tuple[dict[str, Path], set[str], list[str]]:
    files: dict[str, Path] = {}
    partition_dirs: set[str] = set()
    unexpected_files: list[str] = []
    if not root.exists():
        return files, partition_dirs, unexpected_files
    for child in root.iterdir():
        matched = _PARTITION_PATTERN.fullmatch(child.name)
        if matched is None or not child.is_dir():
            continue
        trade_date = matched.group(1)
        partition_dirs.add(trade_date)
        canonical = child / "part-00000.parquet"
        if canonical.is_file():
            files[trade_date] = canonical
        unexpected_files.extend(
            str(path) for path in child.glob("*.parquet") if path.name != "part-00000.parquet"
        )
    return files, partition_dirs, sorted(unexpected_files)


def _coerce_expected_stats(
    value: ExpectedPartitionStats | Mapping[str, Any],
) -> ExpectedPartitionStats:
    if isinstance(value, ExpectedPartitionStats):
        return value
    return ExpectedPartitionStats(
        rows=int(value["rows"]),
        symbols=int(value["symbols"]),
        traded_symbols=(
            int(value["traded_symbols"]) if value.get("traded_symbols") is not None else None
        ),
        vol_sum=float(value["vol_sum"]) if value.get("vol_sum") is not None else None,
        amount_sum=float(value["amount_sum"]) if value.get("amount_sum") is not None else None,
        unit_profile=str(value["unit_profile"]) if value.get("unit_profile") else None,
        time_min=_timestamp_text(value.get("time_min")),
        time_max=_timestamp_text(value.get("time_max")),
        output_sha256=(str(value["output_sha256"]) if value.get("output_sha256") else None),
    )


def _coerce_market_reference(
    value: MarketReferenceStats | Mapping[str, Any],
) -> MarketReferenceStats:
    if isinstance(value, MarketReferenceStats):
        return value
    return MarketReferenceStats(
        sh_sz_symbols=int(value["sh_sz_symbols"]),
        bj_symbols=int(value.get("bj_symbols", 0)),
    )


def _source_reconciliation_issues(
    actual: Mapping[str, Any],
    expected: ExpectedPartitionStats | None,
) -> dict[str, int]:
    if expected is None:
        return {}
    issues = {
        "source_row_mismatch": int(actual["rows"] != expected.rows),
        "source_symbol_mismatch": int(actual["symbols"] != expected.symbols),
    }
    if expected.traded_symbols is not None:
        issues["source_traded_symbol_mismatch"] = int(
            actual["traded_symbols"] != expected.traded_symbols
        )
    for field in ("vol_sum", "amount_sum"):
        expected_value = getattr(expected, field)
        if expected_value is not None:
            issues[f"source_{field}_mismatch"] = int(
                not np.isclose(
                    float(actual[field]),
                    expected_value,
                    rtol=1e-8,
                    atol=1e-6,
                )
            )
    for field in ("time_min", "time_max"):
        expected_value = getattr(expected, field)
        if expected_value is not None:
            actual_value = actual[field]
            issues[f"source_{field}_mismatch"] = int(
                actual_value is None or pd.Timestamp(actual_value) != pd.Timestamp(expected_value)
            )
    return issues


def _session_semantic_issues(
    expectation: DailyCoverageExpectation,
    *,
    actual_time_min: str | None,
    actual_time_max: str | None,
) -> dict[str, int]:
    expected_max_by_tier = {
        ANNUAL_FULL_SH_SZ: _ANNUAL_FULL_TIME_MAX,
        DEAL_FULL_SH_SZ: _ANNUAL_FULL_TIME_MAX,
        TUSHARE_FULL_A_SHARE: _ANNUAL_FULL_TIME_MAX,
        GUAN_PARTIAL_SESSION: _ANNUAL_PARTIAL_TIME_MAX,
    }
    expected_max = expected_max_by_tier.get(expectation.tier)
    issues: dict[str, int] = {}
    if expected_max is not None:
        issues["unexpected_session_time_max"] = int(_clock_text(actual_time_max) != expected_max)
    if expectation.tier == DEAL_FULL_SH_SZ:
        issues["unexpected_deal_session_time_min"] = int(
            _clock_text(actual_time_min) != _DEAL_FULL_TIME_MIN
        )
    if expectation.tier == TUSHARE_FULL_A_SHARE:
        issues["unexpected_tushare_session_time_min"] = int(
            _clock_text(actual_time_min) != _TUSHARE_FULL_TIME_MIN
        )
    return issues


def _overlay_frame_disposition(
    frame: pd.DataFrame,
    expectation: DailyCoverageExpectation,
    *,
    unit_profile: str | None,
) -> tuple[dict[str, int], dict[str, int], dict[str, dict[str, int]]]:
    codes = frame["ts_code"].astype("string")
    guan_frame = frame.loc[~codes.str.endswith(".BJ")]
    bj_frame = frame.loc[codes.str.endswith(".BJ")]
    guan_issues = _frame_issues(
        expectation.trade_date,
        guan_frame,
        sh_sz_only=True,
    )
    bj_issues = _frame_issues(
        expectation.trade_date,
        bj_frame,
        sh_sz_only=False,
    )
    guan_accepted = _accepted_diagnostics(
        guan_issues,
        canonical_source=expectation.canonical_source,
        unit_profile=unit_profile,
    )
    tushare_accepted = _accepted_diagnostics(
        bj_issues,
        canonical_source="tushare_full_day",
        unit_profile="tushare_shares_yuan",
    )
    accepted = _sum_issue_maps(guan_accepted, tushare_accepted)
    fatal = _sum_issue_maps(
        _fatal_issues(
            guan_issues,
            canonical_source=expectation.canonical_source,
            unit_profile=unit_profile,
        ),
        _fatal_issues(
            bj_issues,
            canonical_source="tushare_full_day",
            unit_profile="tushare_shares_yuan",
        ),
    )
    return accepted, fatal, {"guan": guan_accepted, "tushare": tushare_accepted}
