"""Append strictly validated TuShare Beijing-market bars to complete Guan days."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from market_data_platform.dataset_lock import minute_dataset_lock
from market_data_platform.providers.a_share_minute_bj_overlay_part01 import (
    BJ_OVERLAY_RECEIPT_SCHEMA,
    BJ_UNIVERSE_RULE,
    BJOverlayPhase,
    BJOverlayPlan,
    _assert_valid_frame,
    _atomic_write_json,
    _BJOverlayMaterializationContext,
    _BJOverlayMaterializationRequest,
    _BJOverlayPreflightEvidence,
    _date_set,
    _frames_equal,
    _merged_stats,
    _prepare_base_evidence,
    _read_base_partition,
    _read_bj_source,
    _sha256_file,
    _sorted_frame,
    _utc_now,
    _validate_base_evidence,
    _validate_date,
)
from market_data_platform.providers.a_share_minute_fusion import (
    MINUTE_KEY_COLUMNS,
    write_canonical_minute_partition,
)
from market_data_platform.providers.a_share_minute_price_flow import (
    tushare_price_flow_policy,
)
from market_data_platform.providers.tushare_a_share_mins import (
    MINUTE_BARS_PER_DAY,
)


def _inspect_overlay(
    output_root: Path,
    source_dir: Path,
    *,
    trade_date: str,
    base_tier: str,
    base_expected: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    base, base_path, base_sha256 = _read_base_partition(output_root, trade_date=trade_date)
    bj, source_receipt = _read_bj_source(source_dir, trade_date=trade_date)
    codes = base["ts_code"].astype("string")
    base_bj = base.loc[codes.str.endswith(".BJ")].reset_index(drop=True)
    base_sh_sz = base.loc[~codes.str.endswith(".BJ")].reset_index(drop=True)
    _assert_valid_frame(base_sh_sz, trade_date=trade_date, market="SH_SZ")
    base_receipt = _validate_base_evidence(
        base_sh_sz,
        trade_date=trade_date,
        base_sha256=base_sha256,
        expected=base_expected,
        already_overlayed=not base_bj.empty,
    )
    base_receipt["base_tier"] = base_tier
    if base_bj.empty:
        key_overlap = pd.MultiIndex.from_frame(
            base_sh_sz.loc[:, list(MINUTE_KEY_COLUMNS)]
        ).intersection(pd.MultiIndex.from_frame(bj.loc[:, list(MINUTE_KEY_COLUMNS)]))
        if len(key_overlap):
            raise ValueError(f"BJ overlay {trade_date} overlaps SH/SZ canonical keys")
        merged = _sorted_frame(pd.concat([base_sh_sz, bj], ignore_index=True))
        status = "planned_overlay"
    else:
        _assert_valid_frame(base_bj, trade_date=trade_date, market="BJ")
        if not _frames_equal(base_bj, bj):
            raise ValueError(f"Canonical partition {trade_date} already has different BJ rows")
        merged = _sorted_frame(base)
        status = "already_overlayed"
    if merged.duplicated(list(MINUTE_KEY_COLUMNS)).any():
        raise RuntimeError(f"BJ overlay produced duplicate canonical keys for {trade_date}")

    action = {
        "date": trade_date,
        "status": status,
        "base_tier": base_tier,
        "base_path": str(base_path),
        "base_sha256": base_sha256,
        "base_rows": len(base),
        "base_sh_sz_symbols": int(base_sh_sz["ts_code"].nunique()),
        "bj_rows": len(bj),
        "bj_symbols": int(bj["ts_code"].nunique()),
        "output_rows": len(merged),
        "output_symbols": int(merged["ts_code"].nunique()),
        "market_scope": "SH_SZ_BJ",
        "base_evidence_status": "passed",
    }
    return (
        merged,
        action,
        {
            "source_receipt": source_receipt,
            "base_receipt": base_receipt,
            "expected_stats": _merged_stats(
                merged,
                unit_profile=str(base_expected["unit_profile"]),
            ),
        },
    )


def _validate_eligibility(
    plan: BJOverlayPlan,
    *,
    annual_full_dates: set[str],
    deal_full_dates: set[str],
    tushare_full_dates: set[str],
) -> dict[str, str]:
    already_full = sorted(set(plan.dates) & tushare_full_dates)
    if already_full:
        raise ValueError(
            f"TuShare full-A dates already contain BJ and cannot receive an overlay: {already_full}"
        )
    tier_by_date = {
        **dict.fromkeys(annual_full_dates, "annual_full_sh_sz"),
        **dict.fromkeys(deal_full_dates, "deal_full_sh_sz"),
    }
    ineligible = [date for date in plan.dates if date not in tier_by_date]
    if ineligible:
        raise ValueError(
            f"BJ overlay may target only complete Guan annual/deal SH/SZ dates: {ineligible}"
        )
    return tier_by_date


def _preflight(
    context: _BJOverlayMaterializationContext,
) -> _BJOverlayPreflightEvidence:
    actions: list[dict[str, Any]] = []
    source_receipts: dict[str, dict[str, Any]] = {}
    base_receipts: dict[str, dict[str, Any]] = {}
    expected_stats: dict[str, dict[str, Any]] = {}
    for date in context.plan.dates:
        _merged, action, evidence = _inspect_overlay(
            context.output_root,
            context.partitions[date],
            trade_date=date,
            base_tier=context.tier_by_date[date],
            base_expected=context.base_expected[date],
        )
        actions.append(action)
        source_receipts[date] = evidence["source_receipt"]
        base_receipts[date] = evidence["base_receipt"]
        expected_stats[date] = evidence["expected_stats"]
    return _BJOverlayPreflightEvidence(
        actions=actions,
        source_receipts=source_receipts,
        base_receipts=base_receipts,
        expected_stats=expected_stats,
    )


def _source_binding(receipt: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        receipt.get(field)
        for field in (
            "partition_sha256",
            "sidecar_sha256",
            "sidecar_schema_version",
            "universe_hash",
            "universe_rule",
            "expected_symbol_count",
        )
    )


def _payload(
    context: _BJOverlayMaterializationContext,
    evidence: _BJOverlayPreflightEvidence,
    *,
    status: str,
) -> dict[str, Any]:
    return {
        "schema_version": BJ_OVERLAY_RECEIPT_SCHEMA,
        "status": status,
        "phase": context.plan.phase,
        "plan_sha256": context.plan.sha256,
        "generated_at": _utc_now(),
        "output_dir": str(context.output_root),
        "policy": {
            "operation": "append_disjoint_exchange_whole_day_rows",
            "eligible_base_tiers": ["annual_full_sh_sz", "deal_full_sh_sz"],
            "required_universe_rule": BJ_UNIVERSE_RULE,
            "bars_per_symbol": MINUTE_BARS_PER_DAY,
            "same_security_intraday_merge": "forbidden",
            "tushare_full_a_share_dates": "reject_already_contains_bj",
            "market_scope_after_overlay": "SH_SZ_BJ",
            "source_validation": "passed",
            "base_validation": "independent_manifest_reconciliation_passed",
            "deal_base_requirement": "strict_flow_and_output_sha256",
            "tushare_price_flow_validation": dict(tushare_price_flow_policy()),
        },
        "dates": list(context.plan.dates),
        "unused_source_dates": context.unused_source_dates,
        "summary": {
            "date_count": len(context.plan.dates),
            "annual_overlay_dates": sum(
                action["base_tier"] == "annual_full_sh_sz" for action in evidence.actions
            ),
            "deal_overlay_dates": sum(
                action["base_tier"] == "deal_full_sh_sz" for action in evidence.actions
            ),
            "bj_rows": sum(int(action["bj_rows"]) for action in evidence.actions),
            "bj_symbols_by_date": {
                str(action["date"]): int(action["bj_symbols"]) for action in evidence.actions
            },
        },
        "expected_partition_stats": dict(evidence.expected_stats),
        "base_receipts": dict(evidence.base_receipts),
        "source_receipts": dict(evidence.source_receipts),
        "actions": list(evidence.actions),
    }


def _apply_overlay_actions(
    context: _BJOverlayMaterializationContext,
    preflight: _BJOverlayPreflightEvidence,
    payload: dict[str, Any],
    manifest: Path | None,
) -> None:
    try:
        for action in preflight.actions:
            date = str(action["date"])
            merged, current_action, current_evidence = _inspect_overlay(
                context.output_root,
                context.partitions[date],
                trade_date=date,
                base_tier=context.tier_by_date[date],
                base_expected=context.base_expected[date],
            )
            if _source_binding(current_evidence["source_receipt"]) != _source_binding(
                preflight.source_receipts[date]
            ):
                raise RuntimeError(f"BJ source changed between validation and write: {date}")
            if action["base_sha256"] != current_action["base_sha256"]:
                raise RuntimeError(f"Canonical base changed between validation and write: {date}")
            if action["status"] == "already_overlayed":
                action["status"] = "skipped_already_overlayed"
            else:
                output_path = context.output_root / f"trade_date={date}" / "part-00000.parquet"
                write_canonical_minute_partition(merged, output_path)
                action["status"] = "written_bj_overlay"
            output_path = context.output_root / f"trade_date={date}" / "part-00000.parquet"
            action["output_path"] = str(output_path)
            action["output_sha256"] = _sha256_file(output_path)
            action["completed_at"] = _utc_now()
            if manifest is not None:
                _atomic_write_json(payload, manifest)
    except Exception as exc:
        payload["status"] = "failed"
        payload["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if manifest is not None:
            _atomic_write_json(payload, manifest)
        raise


def _materialize_bj_overlay(
    request: _BJOverlayMaterializationRequest,
) -> dict[str, Any]:
    plan = BJOverlayPlan(
        tuple(request.overlay_dates),
        phase=request.phase,
        sha256=request.plan_sha256,
    )
    annual = _date_set(request.annual_full_dates)
    deal = _date_set(request.deal_full_dates)
    tushare_full = _date_set(request.tushare_full_dates)
    tier_by_date = _validate_eligibility(
        plan,
        annual_full_dates=annual,
        deal_full_dates=deal,
        tushare_full_dates=tushare_full,
    )
    partitions = {
        _validate_date(date): Path(path).expanduser()
        for date, path in request.bj_partitions.items()
    }
    missing_sources = sorted(set(plan.dates) - set(partitions))
    if missing_sources:
        raise FileNotFoundError(f"Missing BJ mirror source partitions: {missing_sources}")
    base_expected = _prepare_base_evidence(
        plan,
        tier_by_date=tier_by_date,
        base_expected_stats=request.base_expected_stats,
    )

    output_root = Path(request.output_dir).expanduser()
    context = _BJOverlayMaterializationContext(
        output_root=output_root,
        plan=plan,
        partitions=partitions,
        tier_by_date=tier_by_date,
        base_expected=base_expected,
    )
    preflight = _preflight(context)
    payload = _payload(
        context,
        preflight,
        status="planned" if request.dry_run else "in_progress",
    )
    manifest = Path(request.receipt_path).expanduser() if request.receipt_path is not None else None
    if request.dry_run:
        if manifest is not None:
            _atomic_write_json(payload, manifest)
        return payload
    if manifest is not None:
        _atomic_write_json(payload, manifest)

    _apply_overlay_actions(
        context,
        preflight,
        payload,
        manifest,
    )

    payload["status"] = "passed"
    payload["completed_at"] = _utc_now()
    payload["summary"].update(
        {
            "written_dates": sum(
                action["status"] == "written_bj_overlay" for action in preflight.actions
            ),
            "skipped_already_overlayed_dates": sum(
                action["status"] == "skipped_already_overlayed" for action in preflight.actions
            ),
        }
    )
    if manifest is not None:
        _atomic_write_json(payload, manifest)
    return payload


def materialize_tushare_bj_overlay(  # noqa: PLR0913
    output_dir: str | Path,
    *,
    annual_full_dates: Iterable[str],
    deal_full_dates: Iterable[str] = (),
    tushare_full_dates: Iterable[str] = (),
    bj_partitions: Mapping[str, str | Path],
    overlay_dates: Iterable[str],
    phase: BJOverlayPhase = "pilot",
    plan_sha256: str | None = None,
    base_expected_stats: Mapping[str, Any],
    receipt_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate and atomically append BJ rows to complete Guan SH/SZ days."""
    request = _BJOverlayMaterializationRequest(
        output_dir=output_dir,
        annual_full_dates=annual_full_dates,
        deal_full_dates=deal_full_dates,
        tushare_full_dates=tushare_full_dates,
        bj_partitions=bj_partitions,
        overlay_dates=overlay_dates,
        phase=phase,
        plan_sha256=plan_sha256,
        base_expected_stats=base_expected_stats,
        receipt_path=receipt_path,
        dry_run=dry_run,
    )
    if request.dry_run:
        return _materialize_bj_overlay(request)
    with minute_dataset_lock(output_dir, operation="materialize-tushare-bj-overlay"):
        return _materialize_bj_overlay(request)
