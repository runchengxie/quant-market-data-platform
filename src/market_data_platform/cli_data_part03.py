from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from market_data_platform.cli_data_part01 import (
    _coverage_requirements_with_full_days,
    _CoverageCommandInputs,
    _CoverageMaterializations,
    _print_json,
    _sha256_file,
)
from market_data_platform.cli_data_part02 import (
    _coverage_dry_run_payload,
    _load_coverage_command_inputs,
    _materialize_coverage_sources,
)


def _audit_finalized_coverage(
    args: argparse.Namespace,
    inputs: _CoverageCommandInputs,
    materializations: _CoverageMaterializations,
) -> dict[str, Any]:
    from market_data_platform.providers.a_share_minute_coverage import (
        PRODUCTION_COVERAGE_REQUIREMENTS,
        audit_minute_coverage,
    )

    expected_stats = {**inputs.annual_stats, **inputs.deal_stats}
    requirements = PRODUCTION_COVERAGE_REQUIREMENTS
    if materializations.full_day is not None:
        expected_stats.update(materializations.full_day["expected_partition_stats"])
        requirements = _coverage_requirements_with_full_days(
            PRODUCTION_COVERAGE_REQUIREMENTS,
            materializations.partial,
            materializations.full_day_dates,
            annual_partial_session_dates=inputs.annual_partial_session_dates,
            deal_override_dates=inputs.deal_override_dates,
        )
    if materializations.bj_overlay is not None:
        expected_stats.update(materializations.bj_overlay["expected_partition_stats"])
    deal_manifest_path = Path(args.deal_manifest).expanduser()
    return audit_minute_coverage(
        args.out_dir,
        trade_dates=inputs.trade_dates,
        annual_dates=inputs.annual_dates,
        deal_dates=inputs.deal_dates,
        tushare_dates=inputs.tushare_batches,
        partial_session_annual_dates=inputs.annual_partial_session_dates,
        deal_override_dates=inputs.deal_override_dates,
        tushare_full_dates=materializations.full_day_dates,
        expected_partition_stats=expected_stats,
        requirements=requirements,
        partial_symbols=args.partial_symbols,
        partial_bars_per_symbol=args.partial_bars_per_symbol,
        audit_workers=args.audit_workers,
        input_lineage={
            "trade_cal": str(Path(args.trade_cal).expanduser()),
            "annual_manifest": str(Path(args.annual_manifest).expanduser()),
            "annual_manifest_sha256": _sha256_file(args.annual_manifest),
            "deal_manifest": str(deal_manifest_path),
            "deal_manifest_sha256": _sha256_file(deal_manifest_path),
            "guan_deal_dir": str(Path(args.guan_deal_dir).expanduser()),
            "tushare_batch_dir": str(Path(args.tushare_batch_dir).expanduser()),
            "deal_expected_stats": "deal_build_manifest",
            "annual_full_date_count": len(inputs.annual_full_dates),
            "annual_partial_session_date_count": len(inputs.annual_partial_session_dates),
            "audit_workers": args.audit_workers,
            "deal_override_dates": sorted(inputs.deal_override_dates),
            "original_annual_deal_overlap_dates": sorted(
                inputs.raw_deal_dates & inputs.annual_dates
            ),
            **inputs.evidence_lineage,
        },
        manifest_path=args.manifest,
    )


def _coverage_result_payload(
    args: argparse.Namespace,
    inputs: _CoverageCommandInputs,
    materializations: _CoverageMaterializations,
    audit: dict[str, Any],
) -> dict[str, Any]:
    full_day = materializations.full_day
    bj_overlay = materializations.bj_overlay
    return {
        "status": audit["status"],
        "quality_status": audit["quality_status"],
        "coverage_status": audit["coverage_status"],
        "manifest": str(Path(args.manifest).expanduser()),
        "summary": audit["summary"],
        "known_gaps": audit["known_gaps"],
        "failures": audit["failures"],
        "materialization": {
            "written_partial_dates": materializations.partial["written_partial_dates"],
            "protected_full_date_count": len(materializations.partial["protected_full_dates"]),
            "protected_partial_session_date_count": len(
                materializations.partial["protected_partial_session_dates"]
            ),
            "protected_guan_date_count": len(materializations.partial["protected_guan_dates"]),
            **(
                {
                    "tushare_full_day": {
                        "phase": full_day["phase"],
                        "date_count": full_day["summary"]["date_count"],
                        "top200_replacement_dates": full_day["summary"]["top200_replacement_dates"],
                        "guan_partial_session_replacement_dates": full_day["summary"][
                            "guan_partial_session_replacement_dates"
                        ],
                        "missing_replacement_dates": full_day["summary"][
                            "missing_replacement_dates"
                        ],
                    }
                }
                if full_day is not None
                else {}
            ),
            **(
                {
                    "tushare_bj_overlay": {
                        "date_count": bj_overlay["summary"]["date_count"],
                        "written_dates": bj_overlay["summary"]["written_dates"],
                        "skipped_already_overlayed_dates": bj_overlay["summary"][
                            "skipped_already_overlayed_dates"
                        ],
                    }
                }
                if bj_overlay is not None
                else {}
            ),
        },
    }


def _require_coverage_command_dependencies() -> None:
    # Resolve the same lazy dependency surface before the command's JSON error boundary.
    from market_data_platform.providers.a_share_minute_coverage import (
        PRODUCTION_COVERAGE_REQUIREMENTS,
        audit_minute_coverage,
        discover_guan_deal_files,
        discover_tushare_minute_batches,
        load_annual_minbar_manifest,
        load_guan_deal_manifest,
        load_open_trade_dates,
        materialize_tushare_partial_dates,
        validate_overlap_audit,
    )

    _ = (
        PRODUCTION_COVERAGE_REQUIREMENTS,
        audit_minute_coverage,
        discover_guan_deal_files,
        discover_tushare_minute_batches,
        load_annual_minbar_manifest,
        load_guan_deal_manifest,
        load_open_trade_dates,
        materialize_tushare_partial_dates,
        validate_overlap_audit,
    )


def _handle_finalize_a_share_minute_coverage(args: argparse.Namespace) -> int:
    _require_coverage_command_dependencies()
    try:
        inputs = _load_coverage_command_inputs(args)
        materializations = _materialize_coverage_sources(args, inputs)
        if args.dry_run:
            _print_json(_coverage_dry_run_payload(inputs, materializations))
            return 0
        audit = _audit_finalized_coverage(args, inputs, materializations)
    except Exception as exc:
        _print_json(
            {
                "status": "failed",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
        return 1

    _print_json(_coverage_result_payload(args, inputs, materializations, audit))
    return 0 if audit["status"] == "passed" else 1
