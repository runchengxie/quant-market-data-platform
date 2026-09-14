from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _parse_years(value: str) -> tuple[int, ...]:
    years: list[int] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            raise argparse.ArgumentTypeError("years must not contain empty items")
        if "-" in part:
            raw_start, separator, raw_end = part.partition("-")
            if not separator or not raw_start.isdigit() or not raw_end.isdigit():
                raise argparse.ArgumentTypeError(
                    "years must be comma-separated years or inclusive ranges such as 2016-2026"
                )
            start = int(raw_start)
            end = int(raw_end)
            if start > end:
                raise argparse.ArgumentTypeError(f"year range must be ascending: {part}")
            years.extend(range(start, end + 1))
        elif part.isdigit():
            years.append(int(part))
        else:
            raise argparse.ArgumentTypeError(
                "years must be comma-separated years or inclusive ranges such as 2016-2026"
            )

    unsupported = sorted({year for year in years if not 2016 <= year <= 2026})
    if unsupported:
        raise argparse.ArgumentTypeError(
            f"Guan annual minbar years must be between 2016 and 2026: {unsupported}"
        )
    duplicates = sorted({year for year in years if years.count(year) > 1})
    if duplicates:
        raise argparse.ArgumentTypeError(f"years must not contain duplicates: {duplicates}")
    return tuple(years)


def _add_public_etf_minute_parser(
    data_subparsers: argparse._SubParsersAction,
) -> list[argparse.ArgumentParser]:
    parser = data_subparsers.add_parser(
        "mirror-public-etf-minute",
        help="Mirror public-source ETF minute bars into a versioned asset.",
    )
    parser.add_argument(
        "--symbols",
        action="append",
        required=True,
        help="ETF ts_code values, comma-separated; repeat the option for more values.",
    )
    parser.add_argument("--start-date", required=True, help="Start date YYYYMMDD.")
    parser.add_argument("--end-date", required=True, help="End date YYYYMMDD.")
    parser.add_argument("--period", choices=("1", "5", "15", "30", "60"), default="1")
    parser.add_argument("--source", choices=("auto", "eastmoney", "sina"), default="auto")
    parser.add_argument(
        "--network-mode",
        choices=("system", "direct"),
        default="system",
        help="Network policy; direct removes proxy environment for public-source requests.",
    )
    parser.add_argument("--artifacts-root")
    parser.add_argument("--version")
    parser.add_argument("--out-dir")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    parser.add_argument("--no-skip", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return [parser]


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_audit_lineage(
    paths: list[Path],
    *,
    guan_deal_dir: str | Path,
) -> dict[str, object]:
    receipts: list[dict[str, object]] = []
    promotion: tuple[Path, str, dict[str, Any]] | None = None
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Source audit is not valid JSON: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Source audit is not a JSON mapping: {path}")
        sha256 = _sha256_file(path)
        receipts.append(
            {
                "path": str(path),
                "sha256": sha256,
                "schema_version": payload.get("schema_version"),
                "status": payload.get("status"),
                "verification_status": payload.get("verification_status"),
            }
        )
        if payload.get("schema_version") == "guan.mobile_raw_promotion.v1":
            if promotion is not None:
                raise ValueError("Only one Guan mobile promotion receipt may be supplied")
            promotion = (path, sha256, payload)

    result: dict[str, object] = {
        "source_audits": [str(path) for path in paths],
        "source_audit_receipts": receipts,
    }
    if promotion is None:
        return result
    path, sha256, payload = promotion
    entries = payload.get("entries")
    duplicates = payload.get("source_duplicates")
    verification = payload.get("verification")
    if not (
        payload.get("status") == "complete"
        and payload.get("verification_status") == "verified"
        and isinstance(verification, dict)
        and verification.get("status") == "verified"
        and verification.get("content_validation") in {None, "verified"}
        and isinstance(entries, list)
        and entries
        and all(
            isinstance(entry, dict) and entry.get("verification_status") == "verified"
            for entry in entries
        )
        and isinstance(duplicates, list)
        and all(
            isinstance(item, dict) and item.get("verification_status") == "verified_exact_duplicate"
            for item in duplicates
        )
    ):
        raise ValueError(f"Guan mobile promotion receipt is not fully verified: {path}")
    provider_root_raw = payload.get("provider_root")
    if not isinstance(provider_root_raw, str):
        raise ValueError(f"Guan mobile promotion receipt has no provider_root: {path}")
    provider_root = Path(provider_root_raw).expanduser().resolve()
    deal_root = Path(guan_deal_dir).expanduser().resolve()
    if not deal_root.is_relative_to(provider_root):
        raise ValueError(
            "Guan deal directory is outside the verified mobile provider root: "
            f"deal={deal_root}, provider={provider_root}"
        )
    result.update(
        {
            "guan_mobile_promotion_receipt": str(path),
            "guan_mobile_promotion_receipt_sha256": sha256,
            "guan_mobile_promotion_verification_status": "verified",
            "guan_mobile_provider_root": str(provider_root),
        }
    )
    return result


def _has_complete_tushare_full_day_args(args: argparse.Namespace) -> bool:
    values = (
        args.tushare_full_day_dir,
        args.tushare_full_plan,
        args.tushare_full_receipt,
    )
    if any(values) and not all(values):
        raise ValueError(
            "--tushare-full-day-dir, --tushare-full-plan and "
            "--tushare-full-receipt must be supplied together"
        )
    return all(values)


def _has_complete_tushare_bj_overlay_args(args: argparse.Namespace) -> bool:
    values = (
        args.tushare_bj_overlay_dir,
        args.tushare_bj_overlay_plan,
        args.tushare_bj_overlay_receipt,
    )
    if any(values) and not all(values):
        raise ValueError(
            "--tushare-bj-overlay-dir, --tushare-bj-overlay-plan and "
            "--tushare-bj-overlay-receipt must be supplied together"
        )
    return all(values)


@dataclass(frozen=True)
class _FullDayPlanContext:
    trade_dates: list[str]
    annual_dates: set[str]
    deal_dates: set[str]
    tushare_dates: Any
    annual_partial_session_dates: set[str]
    deal_override_dates: set[str]
    evidence_lineage: dict[str, object]


@dataclass(frozen=True)
class _BJOverlayPlanContext:
    annual_full_dates: set[str]
    deal_full_dates: set[str]
    tushare_full_dates: set[str]
    base_expected_stats: dict[str, Any]
    evidence_lineage: dict[str, object]


@dataclass(frozen=True)
class _CoverageCommandInputs:
    trade_dates: list[str]
    calendar_dates: set[str]
    annual_dates: set[str]
    annual_partial_session_dates: set[str]
    annual_full_dates: set[str]
    annual_stats: dict[str, Any]
    raw_deal_dates: set[str]
    deal_dates: set[str]
    deal_override_dates: set[str]
    deal_stats: dict[str, Any]
    tushare_batches: dict[str, Any]
    evidence_lineage: dict[str, object]


@dataclass(frozen=True)
class _CoverageMaterializations:
    partial: dict[str, Any]
    full_day: dict[str, Any] | None
    full_day_dates: set[str]
    bj_overlay: dict[str, Any] | None


def _materialize_tushare_full_day_plan(
    args: argparse.Namespace,
    context: _FullDayPlanContext,
) -> tuple[dict[str, Any] | None, set[str]]:
    if not _has_complete_tushare_full_day_args(args):
        return None, set()

    from market_data_platform.providers.a_share_minute_coverage import (
        discover_tushare_full_day_partitions,
        load_tushare_full_day_plan,
        materialize_tushare_full_days,
    )

    plan = load_tushare_full_day_plan(args.tushare_full_plan)
    materialization = materialize_tushare_full_days(
        args.out_dir,
        trade_dates=context.trade_dates,
        annual_dates=context.annual_dates,
        deal_dates=context.deal_dates,
        tushare_dates=context.tushare_dates,
        tushare_full_partitions=discover_tushare_full_day_partitions(args.tushare_full_day_dir),
        replacement_dates=plan.dates,
        phase=plan.phase,
        plan_sha256=plan.sha256,
        partial_session_annual_dates=context.annual_partial_session_dates,
        deal_override_dates=context.deal_override_dates,
        manifest_path=args.tushare_full_receipt,
        dry_run=args.dry_run,
    )
    plan_path = Path(args.tushare_full_plan).expanduser()
    receipt_path = Path(args.tushare_full_receipt).expanduser()
    if _sha256_file(plan_path) != plan.sha256:
        raise RuntimeError("TuShare full-day plan changed during materialization")
    context.evidence_lineage.update(
        {
            "tushare_full_day_dir": str(Path(args.tushare_full_day_dir).expanduser()),
            "tushare_full_plan": str(plan_path),
            "tushare_full_plan_sha256": plan.sha256,
            "tushare_full_receipt": str(receipt_path),
            "tushare_full_receipt_sha256": _sha256_file(receipt_path),
            "tushare_full_phase": plan.phase,
            "tushare_full_status": materialization["status"],
            "tushare_full_policy": materialization["policy"],
            "tushare_full_dates": list(plan.dates),
        }
    )
    return materialization, set(plan.dates)


def _coverage_requirements_with_full_days(
    base: Any,
    partial_materialization: dict[str, Any],
    full_day_dates: set[str],
    *,
    annual_partial_session_dates: set[str],
    deal_override_dates: set[str],
) -> Any:
    from market_data_platform.providers.a_share_minute_coverage import CoverageRequirements

    partial_action_statuses = {
        "planned_partial",
        "validated_partial",
        "written_partial",
        "skipped_existing_partial",
    }
    partial_top200_dates = {
        str(action["date"])
        for action in partial_materialization["actions"]
        if action.get("status") in partial_action_statuses
    }
    guan_partial_dates = annual_partial_session_dates - deal_override_dates
    return CoverageRequirements(
        expected_trade_dates=base.expected_trade_dates,
        expected_annual_full_sh_sz_dates=base.expected_annual_full_sh_sz_dates,
        expected_deal_full_sh_sz_dates=base.expected_deal_full_sh_sz_dates,
        expected_tushare_full_a_share_dates=len(full_day_dates),
        expected_guan_partial_session_dates=len(guan_partial_dates - full_day_dates),
        expected_partial_top200_dates=len(partial_top200_dates - full_day_dates),
        expected_accepted_zero_volume_nonzero_amount_rows=(
            base.expected_accepted_zero_volume_nonzero_amount_rows
        ),
        expected_accepted_positive_volume_zero_amount_rows=(
            base.expected_accepted_positive_volume_zero_amount_rows
        ),
        require_full_source_stats=True,
    )


def _materialize_tushare_bj_overlay_plan(
    args: argparse.Namespace,
    context: _BJOverlayPlanContext,
) -> tuple[dict[str, Any] | None, set[str]]:
    if not _has_complete_tushare_bj_overlay_args(args):
        return None, set()
    from market_data_platform.providers.a_share_minute_bj_overlay import (
        discover_tushare_bj_partitions,
        load_bj_overlay_plan,
        materialize_tushare_bj_overlay,
    )

    plan = load_bj_overlay_plan(args.tushare_bj_overlay_plan)
    materialization = materialize_tushare_bj_overlay(
        args.out_dir,
        annual_full_dates=context.annual_full_dates,
        deal_full_dates=context.deal_full_dates,
        tushare_full_dates=context.tushare_full_dates,
        bj_partitions=discover_tushare_bj_partitions(args.tushare_bj_overlay_dir),
        overlay_dates=plan.dates,
        phase=plan.phase,
        plan_sha256=plan.sha256,
        base_expected_stats=context.base_expected_stats,
        receipt_path=args.tushare_bj_overlay_receipt,
        dry_run=args.dry_run,
    )
    plan_path = Path(args.tushare_bj_overlay_plan).expanduser()
    receipt_path = Path(args.tushare_bj_overlay_receipt).expanduser()
    if _sha256_file(plan_path) != plan.sha256:
        raise RuntimeError("TuShare BJ overlay plan changed during materialization")
    context.evidence_lineage.update(
        {
            "tushare_bj_overlay_dir": str(Path(args.tushare_bj_overlay_dir).expanduser()),
            "tushare_bj_overlay_plan": str(plan_path),
            "tushare_bj_overlay_plan_sha256": plan.sha256,
            "tushare_bj_overlay_receipt": str(receipt_path),
            "tushare_bj_overlay_receipt_sha256": _sha256_file(receipt_path),
            "tushare_bj_overlay_phase": plan.phase,
            "tushare_bj_overlay_status": materialization["status"],
            "tushare_bj_overlay_policy": materialization["policy"],
            "tushare_bj_overlay_dates": list(plan.dates),
        }
    )
    return materialization, set(plan.dates)


def _add_warehouse_data_parsers(
    data_subparsers: argparse._SubParsersAction,
) -> list[argparse.ArgumentParser]:
    from market_data_platform import data_warehouse_cli

    catalog = data_subparsers.add_parser(
        "catalog",
        help="Refresh the local manifest-backed artifact catalog.",
    )
    data_warehouse_cli.add_catalog_args(catalog)

    materialize = data_subparsers.add_parser(
        "materialize",
        help="Materialize an input asset or file into the standardized layer.",
    )
    data_warehouse_cli.add_materialize_args(materialize)

    query = data_subparsers.add_parser(
        "query",
        help="Register standardized views in DuckDB and run a query.",
    )
    data_warehouse_cli.add_query_args(query)
    return [catalog, materialize, query]


def _add_minute_fusion_parser(
    data_subparsers: argparse._SubParsersAction,
) -> list[argparse.ArgumentParser]:
    minute_fusion = data_subparsers.add_parser(
        "fuse-a-share-minutes",
        help="Legacy mixed-source minute migration; use the staged production commands instead.",
    )
    minute_fusion.add_argument("--legacy-input-dir", required=True)
    minute_fusion.add_argument("--out-dir", required=True)
    minute_fusion.add_argument("--manifest", required=True)
    minute_fusion.add_argument("--start-date", default="20160101")
    minute_fusion.add_argument("--end-date", default="20991231")
    minute_fusion.add_argument("--legacy-guan-end-date", default="20260424")
    minute_fusion.add_argument("--hundred-x-start-date", default="20260101")
    minute_fusion.add_argument("--hundred-x-end-date", default="20260424")
    minute_fusion.add_argument("--guan-deal-dir")
    minute_fusion.add_argument("--guan-deal-start-date", default="20260601")
    minute_fusion.add_argument("--tushare-batch-dir")
    minute_fusion.add_argument("--instruments")
    minute_fusion.add_argument("--legacy-workers", type=int, default=1)
    minute_fusion.add_argument(
        "--deal-engine",
        choices=("auto", "pandas", "polars"),
        default="auto",
    )
    minute_fusion.add_argument("--deal-batch-row-groups", type=int)
    minute_fusion.add_argument("--no-resume", action="store_true")
    minute_fusion.add_argument("--dry-run", action="store_true")
    return [minute_fusion]


def _add_annual_minute_parser(
    data_subparsers: argparse._SubParsersAction,
) -> list[argparse.ArgumentParser]:
    annual_minute = data_subparsers.add_parser(
        "build-guan-annual-minutes",
        help="Build canonical daily partitions from Guan annual minbar files, one year at a time.",
    )
    annual_minute.add_argument("--minbar-dir", required=True)
    annual_minute.add_argument("--years", required=True, type=_parse_years)
    annual_minute.add_argument("--out-dir", required=True)
    annual_minute.add_argument("--manifest", required=True)
    annual_minute.add_argument("--staging-root", required=True)
    annual_minute.add_argument("--threads", type=int, default=3)
    annual_minute.add_argument(
        "--memory-limit",
        default="auto",
        help="DuckDB memory budget: auto or a positive size such as 8GB or 8192MiB.",
    )
    annual_minute.add_argument("--force", action="store_true")
    annual_minute.add_argument("--keep-staging", action="store_true")
    return [annual_minute]


def _add_deal_minute_parser(
    data_subparsers: argparse._SubParsersAction,
) -> list[argparse.ArgumentParser]:
    deal_minute = data_subparsers.add_parser(
        "build-guan-deal-minutes",
        help="Build canonical daily partitions from Guan deal files without a TuShare overlay.",
    )
    deal_minute.add_argument(
        "--legacy-input-dir",
        required=True,
        help=(
            "Optional external legacy source root. When it is the same path as --out-dir, "
            "existing output partitions are excluded as inputs."
        ),
    )
    deal_minute.add_argument("--annual-manifest", required=True)
    deal_minute.add_argument("--guan-deal-dir", required=True)
    deal_minute.add_argument("--instruments", required=True)
    deal_minute.add_argument("--out-dir", required=True)
    deal_minute.add_argument("--manifest", required=True)
    deal_minute.add_argument("--start-date", required=True)
    deal_minute.add_argument("--end-date", required=True)
    deal_minute.add_argument(
        "--annual-override-date",
        action="append",
        default=[],
        help=(
            "Explicit protected annual date to replace with a whole-day Guan deal aggregate; "
            "repeat once per date. The list must exactly match in-range annual/deal overlaps."
        ),
    )
    deal_minute.add_argument(
        "--deal-engine",
        choices=("pandas", "polars"),
        default="polars",
    )
    deal_minute.add_argument("--deal-batch-row-groups", type=int)
    deal_minute.add_argument("--no-resume", action="store_true")
    deal_minute.add_argument("--dry-run", action="store_true")
    return [deal_minute]
