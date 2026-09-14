"""Private orchestration for A-share minute coverage audits."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from market_data_platform.providers.a_share_minute_price_flow import (
    NOTIONAL_HARD_GUARD_ISSUE,
    POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
    VWAP_EXTREME_UNIT_SCALE_ISSUE,
    VWAP_SOURCE_GUARD_ISSUE,
    ZERO_VOLUME_NONZERO_AMOUNT_ISSUE,
    accepted_issue_rows_by_source,
    issue_disposition_summary,
    tushare_price_flow_policy,
    vwap_diagnostic_summary,
)


@dataclass(frozen=True)
class _CoverageAuditBindings:
    annual_full_sh_sz: str
    deal_full_sh_sz: str
    guan_partial_session: str
    missing: str
    tushare_full_a_share: str
    tushare_partial_top200: str
    classification_request: Callable[..., Any]
    classify: Callable[[Any], list[Any]]
    apply_bj_overlay: Callable[..., list[Any]]
    date_set: Callable[[Iterable[str]], set[str]]
    discover_output_layout: Callable[[Path], tuple[dict[str, Path], set[str], list[str]]]
    validate_date: Callable[[str], str]
    coerce_expected_stats: Callable[[Any], Any]
    coerce_market_reference: Callable[[Any], Any]
    audit_partition: Callable[..., dict[str, Any]]
    requirement_mismatches: Callable[..., list[dict[str, Any]]]
    atomic_write_json: Callable[[Mapping[str, Any], Path], None]


@dataclass(frozen=True)
class _CoverageAuditRequest:
    bindings: _CoverageAuditBindings
    output_dir: str | Path
    trade_dates: Iterable[str]
    annual_dates: Iterable[str]
    deal_dates: Iterable[str]
    tushare_dates: Iterable[str]
    partial_session_annual_dates: Iterable[str] = ()
    deal_override_dates: Iterable[str] = ()
    tushare_full_dates: Iterable[str] = ()
    expected_partition_stats: Mapping[str, Any] | None = None
    market_reference: Mapping[str, Any] | None = None
    requirements: Any = None
    partial_symbols: int = 200
    partial_bars_per_symbol: int = 241
    audit_workers: int = 1
    input_lineage: Mapping[str, Any] | None = None
    manifest_path: str | Path | None = None


@dataclass(frozen=True)
class _CoverageAuditContext:
    request: _CoverageAuditRequest
    output_root: Path
    calendar: set[str]
    annual: set[str]
    annual_partial: set[str]
    annual_all: set[str]
    annual_full: set[str]
    deal: set[str]
    deal_overrides: set[str]
    tushare: set[str]
    tushare_full: set[str]
    bj_overlay: set[str]
    expectations: list[Any]
    partitions: dict[str, Path]
    partition_dirs: set[str]
    unexpected_files: list[str]
    missing_dates: list[str]
    orphan_dates: list[str]
    source_orphan_dates: dict[str, list[str]]
    stats: dict[str, Any]
    references: dict[str, Any]


@dataclass(frozen=True)
class _CoverageAuditDaily:
    daily: list[dict[str, Any]]
    invalid_dates: list[str]
    missing_full_source_stats: list[str]


_CoverageAuditTask = tuple[
    Any,
    Path,
    Any,
    Any,
]


def _coverage_audit_context(request: _CoverageAuditRequest) -> _CoverageAuditContext:
    if request.audit_workers < 1:
        raise ValueError("audit_workers must be positive")
    bindings = request.bindings
    calendar = bindings.date_set(request.trade_dates)
    annual = bindings.date_set(request.annual_dates)
    annual_partial = bindings.date_set(request.partial_session_annual_dates)
    annual_all = annual | annual_partial
    annual_full = annual_all - annual_partial
    deal = bindings.date_set(request.deal_dates)
    deal_overrides = bindings.date_set(request.deal_override_dates)
    tushare = bindings.date_set(request.tushare_dates)
    tushare_full = bindings.date_set(request.tushare_full_dates)
    raw_bj_overlay = (request.input_lineage or {}).get("tushare_bj_overlay_dates", [])
    if not isinstance(raw_bj_overlay, (list, tuple, set)):
        raise ValueError("input_lineage.tushare_bj_overlay_dates must be a date list")
    bj_overlay = bindings.date_set(str(value) for value in raw_bj_overlay)
    expectations = bindings.classify(
        bindings.classification_request(
            trade_dates=calendar,
            annual_dates=annual,
            deal_dates=deal,
            tushare_dates=tushare,
            partial_session_annual_dates=annual_partial,
            deal_override_dates=deal_overrides,
            tushare_full_dates=tushare_full,
            partial_symbols=request.partial_symbols,
            partial_bars_per_symbol=request.partial_bars_per_symbol,
        )
    )
    expectations = bindings.apply_bj_overlay(
        expectations,
        bj_overlay_dates=bj_overlay,
        tushare_full_dates=tushare_full,
    )
    output_root = Path(request.output_dir).expanduser()
    partitions, partition_dirs, unexpected_files = bindings.discover_output_layout(output_root)
    actual_dates = set(partitions)
    missing_dates = sorted(calendar.difference(actual_dates))
    orphan_dates = sorted((actual_dates | partition_dirs).difference(calendar))
    source_orphan_dates = {
        "annual": sorted(annual_all.difference(calendar)),
        "deal": sorted(deal.difference(calendar)),
        "deal_override": sorted(deal_overrides.difference(calendar)),
        "tushare": sorted(tushare.difference(calendar)),
        "tushare_full": sorted(tushare_full.difference(calendar)),
    }
    if bj_overlay:
        source_orphan_dates["bj_overlay"] = sorted(bj_overlay.difference(calendar))
    stats = {
        bindings.validate_date(date): bindings.coerce_expected_stats(value)
        for date, value in (request.expected_partition_stats or {}).items()
    }
    references = {
        bindings.validate_date(date): bindings.coerce_market_reference(value)
        for date, value in (request.market_reference or {}).items()
    }
    return _CoverageAuditContext(
        request=request,
        output_root=output_root,
        calendar=calendar,
        annual=annual,
        annual_partial=annual_partial,
        annual_all=annual_all,
        annual_full=annual_full,
        deal=deal,
        deal_overrides=deal_overrides,
        tushare=tushare,
        tushare_full=tushare_full,
        bj_overlay=bj_overlay,
        expectations=expectations,
        partitions=partitions,
        partition_dirs=partition_dirs,
        unexpected_files=unexpected_files,
        missing_dates=missing_dates,
        orphan_dates=orphan_dates,
        source_orphan_dates=source_orphan_dates,
        stats=stats,
        references=references,
    )


def _coverage_audit_tasks(
    context: _CoverageAuditContext,
) -> tuple[dict[str, dict[str, Any]], list[_CoverageAuditTask], list[str]]:
    daily_by_date: dict[str, dict[str, Any]] = {}
    audit_tasks: list[_CoverageAuditTask] = []
    missing_full_source_stats: list[str] = []
    for expectation in context.expectations:
        path = context.partitions.get(expectation.trade_date)
        expected_stats = context.stats.get(expectation.trade_date)
        if (
            expectation.has_guan_source
            or expectation.tier == context.request.bindings.tushare_full_a_share
        ) and expected_stats is None:
            missing_full_source_stats.append(expectation.trade_date)
        if path is None:
            daily_by_date[expectation.trade_date] = {
                **asdict(expectation),
                "audit_only_sources": list(expectation.audit_only_sources),
                "path": None,
                "issues": {"missing_partition": 1},
                "accepted_diagnostics": {},
                "fatal_issues": {"missing_partition": 1},
                "valid": False,
            }
            continue
        audit_tasks.append(
            (
                expectation,
                path,
                expected_stats,
                context.references.get(expectation.trade_date),
            )
        )
    return daily_by_date, audit_tasks, missing_full_source_stats


def _run_coverage_audit_tasks(
    context: _CoverageAuditContext,
    daily_by_date: dict[str, dict[str, Any]],
    audit_tasks: Sequence[_CoverageAuditTask],
) -> None:
    if context.request.audit_workers == 1:
        for expectation, path, expected_stats, market_reference_stats in audit_tasks:
            daily_by_date[expectation.trade_date] = context.request.bindings.audit_partition(
                expectation,
                path,
                expected_stats=expected_stats,
                market_reference=market_reference_stats,
                partial_bars_per_symbol=context.request.partial_bars_per_symbol,
            )
        return
    with ProcessPoolExecutor(max_workers=context.request.audit_workers) as executor:
        futures = {
            executor.submit(
                context.request.bindings.audit_partition,
                expectation,
                path,
                expected_stats=expected_stats,
                market_reference=market_reference_stats,
                partial_bars_per_symbol=context.request.partial_bars_per_symbol,
            ): expectation.trade_date
            for expectation, path, expected_stats, market_reference_stats in audit_tasks
        }
        for future in as_completed(futures):
            trade_date = futures[future]
            daily_by_date[trade_date] = future.result()


def _coverage_audit_daily(context: _CoverageAuditContext) -> _CoverageAuditDaily:
    daily_by_date, audit_tasks, missing_full_source_stats = _coverage_audit_tasks(context)
    _run_coverage_audit_tasks(context, daily_by_date, audit_tasks)
    daily = [daily_by_date[item.trade_date] for item in context.expectations]
    invalid_dates = [
        str(record["date"]) for record in daily if "date" in record and not record["valid"]
    ]
    return _CoverageAuditDaily(
        daily=daily,
        invalid_dates=invalid_dates,
        missing_full_source_stats=missing_full_source_stats,
    )


@dataclass(frozen=True)
class _CoverageAuditDiagnostics:
    vwap_diagnostics: dict[str, Any]
    vwap_source_guard: dict[str, Any]
    vwap_extreme_guard: dict[str, Any]
    notional_hard_guard: dict[str, Any]
    zero_volume_nonzero_amount: dict[str, Any]
    positive_volume_zero_amount: dict[str, Any]
    accepted_zero_volume_nonzero_amount_by_source: dict[str, int]
    accepted_positive_volume_zero_amount_by_source: dict[str, int]


@dataclass(frozen=True)
class _CoverageAuditPlan:
    tier_counts: Counter[str]
    requirement_mismatches: list[dict[str, Any]]
    nonempty_source_orphans: dict[str, list[str]]
    quality_failed: bool
    partial_dates: list[str]
    guan_partial_session_dates: list[str]
    tushare_full_dates_in_plan: list[str]
    bj_overlay_dates_in_plan: list[str]
    missing_source_dates: list[str]
    coverage_status: str
    tushare_components_present: bool
    full_a_share_dates: int
    bj_market_status: str
    source_priority: list[str]
    requirements_payload: dict[str, Any]


def _coverage_audit_diagnostics(
    daily: Sequence[Mapping[str, Any]],
) -> _CoverageAuditDiagnostics:
    vwap_diagnostics = vwap_diagnostic_summary(daily)
    vwap_source_guard = issue_disposition_summary(daily, VWAP_SOURCE_GUARD_ISSUE)
    vwap_extreme_guard = issue_disposition_summary(
        daily,
        VWAP_EXTREME_UNIT_SCALE_ISSUE,
    )
    notional_hard_guard = issue_disposition_summary(
        daily,
        NOTIONAL_HARD_GUARD_ISSUE,
    )
    zero_volume_nonzero_amount = issue_disposition_summary(
        daily,
        ZERO_VOLUME_NONZERO_AMOUNT_ISSUE,
    )
    positive_volume_zero_amount = issue_disposition_summary(
        daily,
        POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
    )
    accepted_zero_volume_nonzero_amount_by_source = accepted_issue_rows_by_source(
        daily,
        ZERO_VOLUME_NONZERO_AMOUNT_ISSUE,
    )
    accepted_positive_volume_zero_amount_by_source = accepted_issue_rows_by_source(
        daily,
        POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
    )
    return _CoverageAuditDiagnostics(
        vwap_diagnostics=vwap_diagnostics,
        vwap_source_guard=vwap_source_guard,
        vwap_extreme_guard=vwap_extreme_guard,
        notional_hard_guard=notional_hard_guard,
        zero_volume_nonzero_amount=zero_volume_nonzero_amount,
        positive_volume_zero_amount=positive_volume_zero_amount,
        accepted_zero_volume_nonzero_amount_by_source=(
            accepted_zero_volume_nonzero_amount_by_source
        ),
        accepted_positive_volume_zero_amount_by_source=(
            accepted_positive_volume_zero_amount_by_source
        ),
    )


def _coverage_audit_plan(
    context: _CoverageAuditContext,
    daily: _CoverageAuditDaily,
    diagnostics: _CoverageAuditDiagnostics,
) -> _CoverageAuditPlan:
    bindings = context.request.bindings
    tier_counts = Counter(item.tier for item in context.expectations)
    requirement_mismatches = bindings.requirement_mismatches(
        tier_counts,
        calendar_count=len(context.calendar),
        accepted_zero_volume_nonzero_amount_rows=(
            diagnostics.accepted_zero_volume_nonzero_amount_by_source["guan"]
        ),
        accepted_positive_volume_zero_amount_rows=(
            diagnostics.accepted_positive_volume_zero_amount_by_source["guan"]
        ),
        requirements=context.request.requirements,
    )
    nonempty_source_orphans = {
        name: dates for name, dates in context.source_orphan_dates.items() if dates
    }
    quality_failed = bool(
        context.missing_dates
        or context.orphan_dates
        or daily.invalid_dates
        or context.unexpected_files
        or nonempty_source_orphans
        or requirement_mismatches
        or (
            context.request.requirements.require_full_source_stats
            and daily.missing_full_source_stats
        )
    )
    partial_dates = [
        item.trade_date
        for item in context.expectations
        if item.tier == bindings.tushare_partial_top200
    ]
    guan_partial_session_dates = [
        item.trade_date
        for item in context.expectations
        if item.tier == bindings.guan_partial_session
    ]
    tushare_full_dates_in_plan = [
        item.trade_date
        for item in context.expectations
        if item.tier == bindings.tushare_full_a_share
    ]
    bj_overlay_dates_in_plan = [
        item.trade_date for item in context.expectations if item.overlay_sources
    ]
    missing_source_dates = [
        item.trade_date for item in context.expectations if item.tier == bindings.missing
    ]
    if missing_source_dates or context.missing_dates:
        coverage_status = "incomplete"
    elif partial_dates or guan_partial_session_dates:
        coverage_status = "known_partial"
    else:
        coverage_status = "full_sh_sz"
    tushare_components_present = bool(tushare_full_dates_in_plan or bj_overlay_dates_in_plan)
    full_a_share_dates = sum(item.is_full_a_share for item in context.expectations)
    if full_a_share_dates == len(context.calendar) and context.calendar:
        coverage_status = "full_a_share"

    if context.calendar and full_a_share_dates == len(context.calendar):
        bj_market_status = "covered_point_in_time"
    elif full_a_share_dates:
        bj_market_status = "point_in_time_partial_coverage"
    else:
        bj_market_status = "not_covered_by_guan"

    source_priority = [
        "guan_deal_explicit_whole_day_override",
        "guan_annual_minbar",
        "guan_deal_non_annual_dates",
    ]
    if tushare_full_dates_in_plan:
        source_priority.append("tushare_full_day_explicit_whole_day_replacement")
    if bj_overlay_dates_in_plan:
        source_priority.append("tushare_bj_overlay_on_complete_guan_sh_sz")
    source_priority.append("guan_annual_partial_session")
    source_priority.append("tushare_top200_missing_dates_only")
    requirements_payload = asdict(context.request.requirements)
    if context.request.requirements.expected_tushare_full_a_share_dates is None:
        requirements_payload.pop("expected_tushare_full_a_share_dates")
    return _CoverageAuditPlan(
        tier_counts=tier_counts,
        requirement_mismatches=requirement_mismatches,
        nonempty_source_orphans=nonempty_source_orphans,
        quality_failed=quality_failed,
        partial_dates=partial_dates,
        guan_partial_session_dates=guan_partial_session_dates,
        tushare_full_dates_in_plan=tushare_full_dates_in_plan,
        bj_overlay_dates_in_plan=bj_overlay_dates_in_plan,
        missing_source_dates=missing_source_dates,
        coverage_status=coverage_status,
        tushare_components_present=tushare_components_present,
        full_a_share_dates=full_a_share_dates,
        bj_market_status=bj_market_status,
        source_priority=source_priority,
        requirements_payload=requirements_payload,
    )


def _full_market_definition(plan: _CoverageAuditPlan) -> str:
    if plan.tushare_full_dates_in_plan and plan.bj_overlay_dates_in_plan:
        return "point_in_time_A_share; TuShare full days and BJ overlays include SH_SZ_BJ"
    if plan.bj_overlay_dates_in_plan:
        return "point_in_time_A_share; Guan SH_SZ plus strict TuShare BJ overlays"
    if plan.tushare_full_dates_in_plan:
        return "point_in_time_A_share; TuShare full days include SH_SZ_BJ"
    return "point_in_time_A_share; complete SH_SZ is full before 20211115, BJ is absent afterward"


def _coverage_audit_policy(plan: _CoverageAuditPlan) -> dict[str, Any]:
    return {
        "source_priority": plan.source_priority,
        "overlap_policy": (
            "explicit_whole_day_replacement_only_no_intraday_merge"
            if plan.tushare_full_dates_in_plan
            else "audit_only_no_overwrite"
        ),
        "minute_shift": "none",
        "full_market_definition": _full_market_definition(plan),
        "fixed_241_bar_completion": False,
        "tushare_components_fixed_241": plan.tushare_components_present,
        "guan_components_fixed_241": False,
        "tushare_price_flow_validation": dict(tushare_price_flow_policy()),
        "zero_flow_requirement_scope": (
            "fixed_guan_accepted_baseline_plus_receipt_derived_tushare_diagnostics"
        ),
    }
