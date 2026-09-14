"""Private orchestration for A-share minute coverage audits."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform.providers._a_share_minute_coverage_audit_part01 import (
    _coverage_audit_context,
    _coverage_audit_daily,
    _coverage_audit_diagnostics,
    _coverage_audit_plan,
    _coverage_audit_policy,
    _CoverageAuditContext,
    _CoverageAuditDaily,
    _CoverageAuditDiagnostics,
    _CoverageAuditPlan,
    _CoverageAuditRequest,
)


def _coverage_audit_inputs(
    context: _CoverageAuditContext,
    plan: _CoverageAuditPlan,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "annual_date_count": len(context.annual_all),
        "annual_full_date_count": len(context.annual_full),
        "annual_partial_session_date_count": len(context.annual_partial),
        "deal_date_count": len(context.deal),
        "deal_override_date_count": len(context.deal_overrides),
        "tushare_date_count": len(context.tushare),
    }
    if context.tushare_full:
        inputs["tushare_full_date_count"] = len(context.tushare_full)
    if context.bj_overlay:
        inputs["bj_overlay_date_count"] = len(context.bj_overlay)
        inputs["bj_overlay_dates"] = sorted(context.bj_overlay)
    inputs.update(
        {
            "annual_deal_overlap_dates": len(context.annual_all & context.deal),
            "annual_tushare_audit_dates": len(
                (context.annual_all - context.deal_overrides) & context.tushare
            ),
            "deal_tushare_audit_dates": len(
                ((context.deal - context.annual_all) | context.deal_overrides) & context.tushare
            ),
        }
    )
    if context.tushare_full:
        inputs["tushare_full_selected_dates"] = len(plan.tushare_full_dates_in_plan)
    if context.bj_overlay:
        inputs["bj_overlay_selected_dates"] = len(plan.bj_overlay_dates_in_plan)
    inputs["lineage"] = dict(context.request.input_lineage or {})
    return inputs


def _coverage_audit_summary(
    context: _CoverageAuditContext,
    daily: _CoverageAuditDaily,
    diagnostics: _CoverageAuditDiagnostics,
    plan: _CoverageAuditPlan,
) -> dict[str, Any]:
    bindings = context.request.bindings
    summary: dict[str, Any] = {
        "calendar_dates": len(context.calendar),
        "date_min": min(context.calendar) if context.calendar else None,
        "date_max": max(context.calendar) if context.calendar else None,
        "partition_files": len(context.partitions),
        "partition_dirs": len(context.partition_dirs),
        "annual_full_sh_sz_dates": plan.tier_counts.get(bindings.annual_full_sh_sz, 0),
        "deal_full_sh_sz_dates": plan.tier_counts.get(bindings.deal_full_sh_sz, 0),
    }
    if plan.tushare_full_dates_in_plan:
        summary["tushare_full_a_share_dates"] = plan.tier_counts.get(
            bindings.tushare_full_a_share,
            0,
        )
    if plan.bj_overlay_dates_in_plan:
        summary["tushare_bj_overlay_dates"] = len(plan.bj_overlay_dates_in_plan)
        summary["bj_overlay_date_count"] = len(plan.bj_overlay_dates_in_plan)
    summary.update(
        {
            "guan_partial_session_dates": plan.tier_counts.get(
                bindings.guan_partial_session,
                0,
            ),
            "full_sh_sz_dates": (
                plan.tier_counts.get(bindings.annual_full_sh_sz, 0)
                + plan.tier_counts.get(bindings.deal_full_sh_sz, 0)
                + plan.tier_counts.get(bindings.tushare_full_a_share, 0)
            ),
            "full_a_share_dates": plan.full_a_share_dates,
            "tushare_partial_top200_dates": plan.tier_counts.get(
                bindings.tushare_partial_top200,
                0,
            ),
            "missing_source_dates": len(plan.missing_source_dates),
            "missing_output_dates": len(context.missing_dates),
            "invalid_output_dates": len(daily.invalid_dates),
            "orphan_output_dates": len(context.orphan_dates),
            "accepted_vwap_diagnostic_rows": diagnostics.vwap_diagnostics["accepted_rows"],
            "accepted_vwap_diagnostic_dates": len(diagnostics.vwap_diagnostics["accepted_dates"]),
            "accepted_vwap_source_guard_rows": diagnostics.vwap_source_guard["accepted_rows"],
            "accepted_vwap_extreme_guard_rows": diagnostics.vwap_extreme_guard["accepted_rows"],
            "accepted_notional_hard_guard_rows": diagnostics.notional_hard_guard["accepted_rows"],
            "fatal_notional_hard_guard_rows": diagnostics.notional_hard_guard["fatal_rows"],
            "accepted_zero_volume_nonzero_amount_rows": diagnostics.zero_volume_nonzero_amount[
                "accepted_rows"
            ],
            "accepted_positive_volume_zero_amount_rows": (
                diagnostics.positive_volume_zero_amount["accepted_rows"]
            ),
            "accepted_guan_zero_volume_nonzero_amount_rows": (
                diagnostics.accepted_zero_volume_nonzero_amount_by_source["guan"]
            ),
            "accepted_tushare_zero_volume_nonzero_amount_rows": (
                diagnostics.accepted_zero_volume_nonzero_amount_by_source["tushare"]
            ),
            "accepted_guan_positive_volume_zero_amount_rows": (
                diagnostics.accepted_positive_volume_zero_amount_by_source["guan"]
            ),
            "accepted_tushare_positive_volume_zero_amount_rows": (
                diagnostics.accepted_positive_volume_zero_amount_by_source["tushare"]
            ),
        }
    )
    return summary


def _coverage_audit_failures(
    context: _CoverageAuditContext,
    daily: _CoverageAuditDaily,
    plan: _CoverageAuditPlan,
) -> dict[str, Any]:
    return {
        "missing_source_dates": plan.missing_source_dates,
        "missing_output_dates": context.missing_dates,
        "invalid_output_dates": sorted(daily.invalid_dates),
        "orphan_output_dates": context.orphan_dates,
        "source_orphan_dates": plan.nonempty_source_orphans,
        "unexpected_partition_files": context.unexpected_files,
        "missing_full_source_stats": (
            sorted(daily.missing_full_source_stats)
            if context.request.requirements.require_full_source_stats
            else []
        ),
        "requirement_mismatches": plan.requirement_mismatches,
    }


def _coverage_audit_payload(
    context: _CoverageAuditContext,
    daily: _CoverageAuditDaily,
    diagnostics: _CoverageAuditDiagnostics,
    plan: _CoverageAuditPlan,
) -> dict[str, Any]:
    return {
        "schema_version": "a_share.minute_1m.coverage.v1",
        "dataset": "minute_1m",
        "market": "a_share",
        "provider": "source_neutral",
        "status": "failed" if plan.quality_failed else "passed",
        "quality_status": "failed" if plan.quality_failed else "passed",
        "coverage_status": plan.coverage_status,
        "generated_at": datetime.now(UTC).isoformat(),
        "output_dir": str(context.output_root),
        "policy": _coverage_audit_policy(plan),
        "requirements": plan.requirements_payload,
        "inputs": _coverage_audit_inputs(context, plan),
        "summary": _coverage_audit_summary(context, daily, diagnostics, plan),
        "diagnostics": {
            "vwap_outside_ohlc": diagnostics.vwap_diagnostics,
            "vwap_source_guard": diagnostics.vwap_source_guard,
            "vwap_extreme_guard": diagnostics.vwap_extreme_guard,
            "notional_hard_guard": diagnostics.notional_hard_guard,
            "zero_volume_nonzero_amount": diagnostics.zero_volume_nonzero_amount,
            "positive_volume_zero_amount": diagnostics.positive_volume_zero_amount,
        },
        "known_gaps": {
            "bj_market": {
                "status": plan.bj_market_status,
                "affected_trade_dates": len(context.calendar) - plan.full_a_share_dates,
            },
            "partial_top200_dates": plan.partial_dates,
            "guan_partial_session": {
                "status": "session_ends_at_14_57",
                "affected_trade_dates": len(plan.guan_partial_session_dates),
                "dates": plan.guan_partial_session_dates,
            },
        },
        "failures": _coverage_audit_failures(context, daily, plan),
        "daily": daily.daily,
    }


def _audit_minute_coverage(request: _CoverageAuditRequest) -> dict[str, Any]:
    context = _coverage_audit_context(request)
    daily = _coverage_audit_daily(context)
    diagnostics = _coverage_audit_diagnostics(daily.daily)
    plan = _coverage_audit_plan(context, daily, diagnostics)
    payload = _coverage_audit_payload(context, daily, diagnostics, plan)
    if request.manifest_path is not None:
        request.bindings.atomic_write_json(payload, Path(request.manifest_path).expanduser())
    return payload
