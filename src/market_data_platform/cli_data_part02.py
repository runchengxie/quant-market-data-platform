from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from market_data_platform.cli_data_part01 import (
    _BJOverlayPlanContext,
    _CoverageCommandInputs,
    _CoverageMaterializations,
    _FullDayPlanContext,
    _has_complete_tushare_bj_overlay_args,
    _has_complete_tushare_full_day_args,
    _materialize_tushare_bj_overlay_plan,
    _materialize_tushare_full_day_plan,
    _print_json,
    _source_audit_lineage,
)


def _add_coverage_parser(
    data_subparsers: argparse._SubParsersAction,
) -> list[argparse.ArgumentParser]:
    coverage = data_subparsers.add_parser(
        "finalize-a-share-minute-coverage",
        help="Materialize TuShare-only gaps and audit the production minute dataset.",
    )
    coverage.add_argument("--trade-cal", required=True)
    coverage.add_argument("--annual-manifest", required=True)
    coverage.add_argument("--deal-manifest", required=True)
    coverage.add_argument("--guan-deal-dir", required=True)
    coverage.add_argument("--tushare-batch-dir", required=True)
    coverage.add_argument(
        "--tushare-full-day-dir",
        help="Hardened all-A TuShare minute mirror root used for explicit whole-day replacements.",
    )
    coverage.add_argument(
        "--tushare-full-plan",
        help="Pilot or production JSON plan listing explicit whole-day replacement dates.",
    )
    coverage.add_argument(
        "--tushare-full-receipt",
        help="Write the source-bound whole-day replacement receipt to this path.",
    )
    coverage.add_argument(
        "--tushare-bj-overlay-dir",
        help="BJ-only TuShare v3 minute mirror root for complete Guan SH/SZ dates.",
    )
    coverage.add_argument(
        "--tushare-bj-overlay-plan",
        help="Explicit JSON plan listing complete Guan dates that receive BJ rows.",
    )
    coverage.add_argument(
        "--tushare-bj-overlay-receipt",
        help="Write the source-bound BJ overlay receipt to this path.",
    )
    coverage.add_argument("--out-dir", required=True)
    coverage.add_argument("--manifest", required=True)
    coverage.add_argument("--overlap-audit", required=True)
    coverage.add_argument("--repair-audit")
    coverage.add_argument("--source-audit", action="append", default=[])
    coverage.add_argument("--start-date", default="20160104")
    coverage.add_argument("--end-date", default="20260714")
    coverage.add_argument("--partial-symbols", type=int, default=200)
    coverage.add_argument("--partial-bars-per-symbol", type=int, default=241)
    coverage.add_argument("--audit-workers", type=int, default=1)
    coverage.add_argument("--no-replace-existing-partial", action="store_true")
    coverage.add_argument("--dry-run", action="store_true")
    return [coverage]


def _handle_build_guan_annual_minutes(args: argparse.Namespace) -> int:
    from market_data_platform.providers.guan_annual_minbar import (
        AnnualMinbarBuildOptions,
        build_guan_annual_minbar,
    )

    results: list[dict] = []
    minbar_dir = Path(args.minbar_dir).expanduser()
    for year in args.years:
        try:
            result = build_guan_annual_minbar(
                AnnualMinbarBuildOptions(
                    source_path=minbar_dir / f"minbar_{year}.parquet",
                    output_dir=args.out_dir,
                    manifest_path=args.manifest,
                    year=year,
                    staging_root=args.staging_root,
                    force=args.force,
                    threads=args.threads,
                    memory_limit=args.memory_limit,
                    keep_staging=args.keep_staging,
                )
            )
        except Exception as exc:
            _print_json(
                {
                    "status": "failed",
                    "failed_year": year,
                    "completed_years": [item["year"] for item in results],
                    "results": results,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            )
            return 1
        results.append(
            {
                "year": year,
                "status": result["status"],
                "source_full_scans": result.get("source_full_scans", 0),
                "memory_limit": result.get("memory_limit"),
                "date_count": len(
                    result.get("dates", result.get("staging_validation", {}).get("dates", []))
                ),
                "promoted_date_count": len(result.get("promoted_dates", [])),
                "preserved_date_count": len(result.get("preserved_dates", [])),
                "replaced_invalid_date_count": len(result.get("replaced_invalid_dates", [])),
            }
        )

    _print_json(
        {
            "status": "complete",
            "output_dir": str(Path(args.out_dir).expanduser()),
            "manifest": str(Path(args.manifest).expanduser()),
            "year_count": len(results),
            "date_count": sum(int(item["date_count"]) for item in results),
            "source_full_scans": sum(int(item["source_full_scans"]) for item in results),
            "results": results,
        }
    )
    return 0


def _handle_mirror_public_etf_minute(args: argparse.Namespace) -> int:
    from market_data_platform.providers.public_etf_minute import (
        EtfMinuteMirrorOptions,
        mirror_public_etf_minute,
    )

    symbols = tuple(
        symbol.strip()
        for value in args.symbols
        for symbol in str(value).split(",")
        if symbol.strip()
    )
    result = mirror_public_etf_minute(
        EtfMinuteMirrorOptions(
            symbols=symbols,
            start_date=args.start_date,
            end_date=args.end_date,
            period=args.period,
            source=args.source,
            network_mode=args.network_mode,
            artifacts_root=Path(args.artifacts_root).expanduser() if args.artifacts_root else None,
            version=args.version,
            output_dir=Path(args.out_dir).expanduser() if args.out_dir else None,
            attempts=args.attempts,
            retry_delay=args.retry_delay,
            skip_existing=not args.no_skip,
            dry_run=args.dry_run,
        )
    )
    _print_json(result)
    return 0 if result["status"] in {"completed", "planned"} else 1


def _handle_build_guan_deal_minutes(args: argparse.Namespace) -> int:
    from market_data_platform.providers.a_share_minute_build import (
        MinuteFusionBuildOptions,
        build_fused_minute_dataset,
    )
    from market_data_platform.providers.a_share_minute_coverage import (
        discover_guan_deal_files,
        load_annual_minbar_manifest,
    )

    try:
        annual_result = load_annual_minbar_manifest(args.annual_manifest)
        annual_dates = annual_result.dates
        raw_deal_dates = {
            date
            for date in discover_guan_deal_files(args.guan_deal_dir)
            if args.start_date <= date <= args.end_date
        }
        expected_override_dates = annual_dates & raw_deal_dates
        annual_override_dates = set(args.annual_override_date)
        if annual_override_dates != expected_override_dates:
            raise ValueError(
                "Explicit --annual-override-date values must exactly match in-range "
                "annual/deal overlaps: "
                f"missing={sorted(expected_override_dates - annual_override_dates)}, "
                f"unexpected={sorted(annual_override_dates - expected_override_dates)}"
            )
        result = build_fused_minute_dataset(
            MinuteFusionBuildOptions(
                legacy_input_dir=args.legacy_input_dir,
                output_dir=args.out_dir,
                manifest_path=args.manifest,
                start_date=args.start_date,
                end_date=args.end_date,
                guan_deal_dir=args.guan_deal_dir,
                guan_deal_start_date=args.start_date,
                tushare_batch_dir=None,
                instruments_path=args.instruments,
                resume=not args.no_resume,
                dry_run=args.dry_run,
                legacy_workers=1,
                deal_engine=args.deal_engine,
                deal_batch_row_groups=args.deal_batch_row_groups,
                protected_dates=tuple(sorted(annual_dates)),
                annual_override_dates=tuple(sorted(annual_override_dates)),
            )
        )
    except Exception as exc:
        _print_json(
            {
                "status": "failed",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
        return 1

    validation = result["validation"]
    _print_json(
        {
            "status": result["status"],
            "output_dir": result["output_dir"],
            "partition_count": validation.get("partition_count", 0),
            "rows": validation.get("rows", 0),
            "date_min": validation.get("date_min"),
            "date_max": validation.get("date_max"),
            "invalid_dates": validation.get("invalid_dates", []),
            "tushare_batch_dir": None,
            "protected_annual_date_count": len(annual_dates),
            "annual_override_date_count": len(annual_override_dates),
            "annual_override_dates": sorted(annual_override_dates),
        }
    )
    return 0 if result["status"] in {"passed", "planned"} else 1


def _handle_fuse_a_share_minutes(args: argparse.Namespace) -> int:
    from market_data_platform.providers.a_share_minute_build import (
        MinuteFusionBuildOptions,
        build_fused_minute_dataset,
    )

    result = build_fused_minute_dataset(
        MinuteFusionBuildOptions(
            legacy_input_dir=args.legacy_input_dir,
            output_dir=args.out_dir,
            manifest_path=args.manifest,
            start_date=args.start_date,
            end_date=args.end_date,
            legacy_guan_end_date=args.legacy_guan_end_date,
            hundred_x_start_date=args.hundred_x_start_date,
            hundred_x_end_date=args.hundred_x_end_date,
            guan_deal_dir=args.guan_deal_dir,
            guan_deal_start_date=args.guan_deal_start_date,
            tushare_batch_dir=args.tushare_batch_dir,
            instruments_path=args.instruments,
            resume=not args.no_resume,
            dry_run=args.dry_run,
            legacy_workers=args.legacy_workers,
            deal_engine=args.deal_engine,
            deal_batch_row_groups=args.deal_batch_row_groups,
        )
    )
    validation = result["validation"]
    _print_json(
        {
            "status": result["status"],
            "output_dir": result["output_dir"],
            "partition_count": validation.get("partition_count", 0),
            "rows": validation.get("rows", 0),
            "date_min": validation.get("date_min"),
            "date_max": validation.get("date_max"),
            "invalid_dates": validation.get("invalid_dates", []),
        }
    )
    return 0 if result["status"] in {"passed", "planned"} else 1


def _handle_warehouse_data(args: argparse.Namespace) -> int:
    from market_data_platform import data_warehouse

    if args.data_command == "catalog":
        return int(data_warehouse.refresh_catalog(args) or 0)
    if args.data_command == "materialize":
        return int(data_warehouse.materialize_standardized(args) or 0)
    if args.data_command == "query":
        return int(data_warehouse.query_standardized(args) or 0)
    raise ValueError(f"Unknown data command: {args.data_command}")


def _load_coverage_command_inputs(args: argparse.Namespace) -> _CoverageCommandInputs:
    from market_data_platform.providers.a_share_minute_coverage import (
        discover_guan_deal_files,
        discover_tushare_minute_batches,
        load_annual_minbar_manifest,
        load_guan_deal_manifest,
        load_open_trade_dates,
        validate_overlap_audit,
    )

    _has_complete_tushare_full_day_args(args)
    _has_complete_tushare_bj_overlay_args(args)
    trade_dates = load_open_trade_dates(
        args.trade_cal,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    calendar_dates = set(trade_dates)
    annual_result = load_annual_minbar_manifest(args.annual_manifest)
    all_annual_dates = annual_result.dates
    annual_dates = all_annual_dates & calendar_dates
    annual_partial_session_dates = annual_result.partial_session_dates & calendar_dates
    annual_full_dates = annual_result.full_dates & calendar_dates
    annual_stats = {
        date: stats for date, stats in annual_result.stats.items() if date in calendar_dates
    }

    all_deal_files = discover_guan_deal_files(args.guan_deal_dir)
    deal_files = {date: path for date, path in all_deal_files.items() if date in calendar_dates}
    deal_result = load_guan_deal_manifest(args.deal_manifest)
    all_deal_dates = deal_result.dates
    deal_dates = all_deal_dates & calendar_dates
    deal_override_dates = deal_result.override_dates & calendar_dates
    deal_stats = {
        date: stats for date, stats in deal_result.stats.items() if date in calendar_dates
    }
    raw_deal_dates = set(deal_files)
    expected_override_dates = raw_deal_dates & annual_dates
    if deal_override_dates != expected_override_dates:
        raise ValueError(
            "Guan deal explicit override dates do not match annual/deal overlaps: "
            f"missing={sorted(expected_override_dates - deal_override_dates)}, "
            f"unexpected={sorted(deal_override_dates - expected_override_dates)}"
        )
    expected_deal_dates = raw_deal_dates.difference(annual_dates) | deal_override_dates
    if deal_dates != expected_deal_dates:
        raise ValueError(
            "Guan deal manifest dates do not match non-annual files plus explicit "
            "annual overrides: "
            f"missing={sorted(expected_deal_dates - deal_dates)}, "
            f"unexpected={sorted(deal_dates - expected_deal_dates)}"
        )

    all_tushare_batches = discover_tushare_minute_batches(args.tushare_batch_dir)
    tushare_batches = {
        date: paths for date, paths in all_tushare_batches.items() if date in calendar_dates
    }
    evidence_lineage: dict[str, object] = {}
    overlap_path = Path(args.overlap_audit).expanduser()
    expected_overlap_dates = (annual_dates | raw_deal_dates) & set(tushare_batches)
    overlap = validate_overlap_audit(
        overlap_path,
        output_dir=args.out_dir,
        tushare_batch_dir=args.tushare_batch_dir,
        expected_overlap_dates=expected_overlap_dates,
        tushare_batches=tushare_batches,
    )
    evidence_lineage.update(
        {
            "overlap_audit": str(overlap_path),
            "overlap_audit_date_count": overlap.get("summary", {}).get("date_count"),
            "outside_range_source_dates": {
                "annual": len(all_annual_dates - calendar_dates),
                "guan_deal": len(set(all_deal_files) - calendar_dates),
                "deal_manifest": len(all_deal_dates - calendar_dates),
                "tushare": len(set(all_tushare_batches) - calendar_dates),
            },
        }
    )
    if args.repair_audit:
        repair_path = Path(args.repair_audit).expanduser()
        if not repair_path.is_file():
            raise FileNotFoundError(f"Repair audit not found: {repair_path}")
        evidence_lineage["reasonix_repair_audit"] = str(repair_path)
    source_audits = [Path(value).expanduser() for value in args.source_audit]
    missing_source_audits = [str(path) for path in source_audits if not path.is_file()]
    if missing_source_audits:
        raise FileNotFoundError(f"Source audits not found: {missing_source_audits}")
    if source_audits:
        evidence_lineage.update(
            _source_audit_lineage(source_audits, guan_deal_dir=args.guan_deal_dir)
        )
    return _CoverageCommandInputs(
        trade_dates=trade_dates,
        calendar_dates=calendar_dates,
        annual_dates=annual_dates,
        annual_partial_session_dates=annual_partial_session_dates,
        annual_full_dates=annual_full_dates,
        annual_stats=annual_stats,
        raw_deal_dates=raw_deal_dates,
        deal_dates=deal_dates,
        deal_override_dates=deal_override_dates,
        deal_stats=deal_stats,
        tushare_batches=tushare_batches,
        evidence_lineage=evidence_lineage,
    )


def _materialize_coverage_sources(
    args: argparse.Namespace,
    inputs: _CoverageCommandInputs,
) -> _CoverageMaterializations:
    from market_data_platform.providers.a_share_minute_coverage import (
        materialize_tushare_partial_dates,
    )

    partial = materialize_tushare_partial_dates(
        args.out_dir,
        trade_dates=inputs.trade_dates,
        annual_dates=inputs.annual_dates,
        deal_dates=inputs.deal_dates,
        tushare_batches=inputs.tushare_batches,
        partial_session_annual_dates=inputs.annual_partial_session_dates,
        deal_override_dates=inputs.deal_override_dates,
        expected_symbols=args.partial_symbols,
        expected_bars_per_symbol=args.partial_bars_per_symbol,
        replace_existing_partial=not args.no_replace_existing_partial,
        dry_run=args.dry_run,
    )
    full_day, full_day_dates = _materialize_tushare_full_day_plan(
        args,
        _FullDayPlanContext(
            trade_dates=inputs.trade_dates,
            annual_dates=inputs.annual_dates,
            deal_dates=inputs.deal_dates,
            tushare_dates=inputs.tushare_batches,
            annual_partial_session_dates=inputs.annual_partial_session_dates,
            deal_override_dates=inputs.deal_override_dates,
            evidence_lineage=inputs.evidence_lineage,
        ),
    )
    bj_overlay, _bj_overlay_dates = _materialize_tushare_bj_overlay_plan(
        args,
        _BJOverlayPlanContext(
            annual_full_dates=inputs.annual_full_dates,
            deal_full_dates=inputs.deal_dates,
            tushare_full_dates=full_day_dates,
            base_expected_stats={**inputs.annual_stats, **inputs.deal_stats},
            evidence_lineage=inputs.evidence_lineage,
        ),
    )
    return _CoverageMaterializations(
        partial=partial,
        full_day=full_day,
        full_day_dates=full_day_dates,
        bj_overlay=bj_overlay,
    )


def _coverage_dry_run_payload(
    inputs: _CoverageCommandInputs,
    materializations: _CoverageMaterializations,
) -> dict[str, Any]:
    full_day_summary = (
        materializations.full_day["summary"] if materializations.full_day is not None else None
    )
    return {
        "status": "planned",
        "calendar_date_count": len(inputs.trade_dates),
        "annual_date_count": len(inputs.annual_dates),
        "annual_full_date_count": len(inputs.annual_full_dates),
        "annual_partial_session_date_count": len(inputs.annual_partial_session_dates),
        "deal_date_count": len(inputs.deal_dates),
        "deal_override_date_count": len(inputs.deal_override_dates),
        "deal_audit_overlap_date_count": len(inputs.raw_deal_dates & inputs.annual_dates),
        "tushare_batch_date_count": len(inputs.tushare_batches),
        "protected_full_date_count": len(materializations.partial["protected_full_dates"]),
        "planned_partial_date_count": sum(
            action["status"] == "planned_partial" for action in materializations.partial["actions"]
        ),
        **({"planned_tushare_full_days": full_day_summary} if full_day_summary is not None else {}),
        **(
            {"planned_tushare_bj_overlay": materializations.bj_overlay["summary"]}
            if materializations.bj_overlay is not None
            else {}
        ),
        "uncovered_dates": materializations.partial["uncovered_dates"],
        "outside_calendar_batch_dates": materializations.partial["outside_calendar_batch_dates"],
    }
