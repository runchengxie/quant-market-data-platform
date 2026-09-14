from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from market_data_platform.contract import (
    infer_manifest_path,
)
from market_data_platform.manifest import load_manifest_summary
from market_data_platform.paths import (
    candidate_asset_paths,
    resolve_artifacts_root,
)
from market_data_platform.tushare_backfill import build_a_share_backfill_plan

PROMOTION_RAW_DATASETS = ("daily", "adj_factor", "daily_basic", "limit_status")

PROMOTION_REQUIRED_ASSETS = (
    "instruments",
    "trade_cal",
    "daily",
    "adj_factor",
    "daily_basic",
    "limit_status",
    "daily_clean",
    "universe_by_date",
    "universe_symbols",
    "universe_meta",
)


@dataclass(frozen=True)
class AShareCurrentRefreshPaths:
    root: Path
    aliases: Mapping[str, Path]
    raw_outputs: Mapping[str, str]
    daily_clean_output: Path
    universe_by_date: Path
    universe_symbols: Path
    universe_meta: Path
    universe_version_by_date: Path
    universe_version_symbols: Path
    universe_version_meta: Path
    daily_clean_baseline_validation_report: Path
    daily_clean_research_validation_report: Path
    universe_validation_report: Path
    current_health_report: Path
    release_evidence: Path
    trade_cal_file: Path


@dataclass(frozen=True)
class AShareCurrentPromotionOptions:
    start_date: str
    end_date: str
    artifacts_root: str | Path | None = None
    segment: str = "month"
    daily_dir: str | Path | None = None
    adj_factor_dir: str | Path | None = None
    daily_basic_dir: str | Path | None = None
    limit_status_dir: str | Path | None = None
    daily_clean_dir: str | Path | None = None
    universe_by_date: str | Path | None = None
    universe_symbols: str | Path | None = None
    universe_meta: str | Path | None = None
    daily_clean_baseline_report: str | Path | None = None
    daily_clean_research_report: str | Path | None = None
    universe_validation_report: str | Path | None = None
    current_health_report: str | Path | None = None
    evidence_out: str | Path | None = None
    apply: bool = False
    fail_on_severity: str = "warning"
    required_assets: Iterable[str] | None = None


def _command(*parts: object) -> list[str]:
    return [str(part) for part in parts if part is not None]


def _resolve_refresh_paths(
    root: Path,
    raw_plan: Mapping[str, Any],
    *,
    start_date: str,
    end_date: str,
) -> AShareCurrentRefreshPaths:
    aliases = candidate_asset_paths(root, market="a_share", provider="tushare")
    raw_outputs = {str(row["dataset"]): str(row["output_dir"]) for row in raw_plan["datasets"]}
    missing_raw = sorted(
        {"daily", "adj_factor", "daily_basic", "limit_status"} - raw_outputs.keys()
    )
    if missing_raw:
        raise ValueError(
            "A-share current refresh requires all daily_clean raw inputs: " + ", ".join(missing_raw)
        )
    universe_staging_dir = aliases["universe_by_date"].parent / "staging"
    return AShareCurrentRefreshPaths(
        root=root,
        aliases=aliases,
        raw_outputs=raw_outputs,
        daily_clean_output=(
            aliases["daily_clean"].parent / f"a_share_all_{start_date}_{end_date}_daily_clean"
        ),
        universe_by_date=(
            universe_staging_dir / f"a_share_all_{start_date}_{end_date}_full_by_date.csv"
        ),
        universe_symbols=(
            universe_staging_dir / f"a_share_all_{start_date}_{end_date}_full_symbols.txt"
        ),
        universe_meta=(
            universe_staging_dir / f"a_share_all_{start_date}_{end_date}_full_by_date.meta.yml"
        ),
        universe_version_by_date=(
            aliases["universe_by_date"].parent
            / f"a_share_all_{start_date}_{end_date}_full_by_date.csv"
        ),
        universe_version_symbols=(
            aliases["universe_symbols"].parent
            / f"a_share_all_{start_date}_{end_date}_full_symbols.txt"
        ),
        universe_version_meta=(
            aliases["universe_meta"].parent
            / f"a_share_all_{start_date}_{end_date}_full_by_date.meta.yml"
        ),
        daily_clean_baseline_validation_report=(
            root
            / "reports"
            / f"a_share_daily_clean_baseline_validation_{start_date}_{end_date}.json"
        ),
        daily_clean_research_validation_report=(
            root
            / "reports"
            / f"a_share_daily_clean_research_validation_{start_date}_{end_date}.json"
        ),
        universe_validation_report=(
            root / "reports" / f"a_share_universe_validation_{start_date}_{end_date}.json"
        ),
        current_health_report=root / "reports" / f"a_share_current_health_{end_date}.json",
        release_evidence=(
            root / "reports" / f"a_share_current_release_{start_date}_{end_date}.json"
        ),
        trade_cal_file=aliases["trade_cal"],
    )


def _raw_backfill_stage(
    paths: AShareCurrentRefreshPaths,
    raw_plan: Mapping[str, Any],
    *,
    start_date: str,
    end_date: str,
    segment: str,
) -> dict[str, Any]:
    return {
        "name": "raw_backfill",
        "writes_latest_aliases": False,
        "command": _command(
            "marketdata",
            "tushare",
            "backfill-a-share-history",
            "--artifacts-root",
            paths.root,
            "--start-date",
            start_date,
            "--end-date",
            end_date,
            "--segment",
            segment,
        ),
        "plan": raw_plan,
    }


def _daily_clean_build_stage(paths: AShareCurrentRefreshPaths) -> dict[str, Any]:
    return {
        "name": "daily_clean_build",
        "writes_latest_aliases": False,
        "command": _command(
            "marketdata",
            "tushare",
            "build-a-share-daily-clean",
            "--daily-dir",
            paths.raw_outputs["daily"],
            "--adj-factor-dir",
            paths.raw_outputs["adj_factor"],
            "--daily-basic-dir",
            paths.raw_outputs["daily_basic"],
            "--limit-status-dir",
            paths.raw_outputs["limit_status"],
            "--instruments-file",
            paths.aliases["instruments"],
            "--out-dir",
            paths.daily_clean_output,
        ),
    }


def _daily_clean_validate_baseline_stage(paths: AShareCurrentRefreshPaths) -> dict[str, Any]:
    return {
        "name": "daily_clean_validate_baseline",
        "writes_latest_aliases": False,
        "command": _command(
            "marketdata",
            "tushare",
            "validate-a-share-daily-clean",
            "--daily-clean-dir",
            paths.daily_clean_output,
            "--require-valuation",
            "--require-limit-status",
            "--profile",
            "baseline",
            "--out",
            paths.daily_clean_baseline_validation_report,
        ),
    }


def _daily_clean_validate_research_stage(paths: AShareCurrentRefreshPaths) -> dict[str, Any]:
    return {
        "name": "daily_clean_validate_research",
        "writes_latest_aliases": False,
        "command": _command(
            "marketdata",
            "tushare",
            "validate-a-share-daily-clean",
            "--daily-clean-dir",
            paths.daily_clean_output,
            "--require-valuation",
            "--require-limit-status",
            "--profile",
            "research",
            "--trade-cal-file",
            paths.trade_cal_file,
            "--out",
            paths.daily_clean_research_validation_report,
        ),
    }


def _universe_build_stage(
    paths: AShareCurrentRefreshPaths,
    *,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    return {
        "name": "universe_build",
        "writes_latest_aliases": False,
        "command": _command(
            "marketdata",
            "tushare",
            "build-a-share-universe",
            "--artifacts-root",
            paths.root,
            "--daily-clean-dir",
            paths.daily_clean_output,
            "--start-date",
            start_date,
            "--end-date",
            end_date,
            "--out",
            paths.universe_by_date,
            "--latest-out",
            paths.universe_symbols,
            "--meta-out",
            paths.universe_meta,
        ),
    }


def _universe_validate_stage(
    paths: AShareCurrentRefreshPaths,
    *,
    end_date: str,
) -> dict[str, Any]:
    return {
        "name": "universe_validate",
        "writes_latest_aliases": False,
        "command": _command(
            "marketdata",
            "tushare",
            "validate-a-share-universe",
            "--by-date-file",
            paths.universe_by_date,
            "--latest-symbols-file",
            paths.universe_symbols,
            "--meta-file",
            paths.universe_meta,
            "--expected-as-of",
            end_date,
            "--out",
            paths.universe_validation_report,
        ),
    }


def _publish_aliases(paths: AShareCurrentRefreshPaths) -> dict[str, dict[str, str]]:
    return {
        **{
            dataset: {"alias_path": str(paths.aliases[dataset]), "target": output}
            for dataset, output in paths.raw_outputs.items()
        },
        "daily_clean": {
            "alias_path": str(paths.aliases["daily_clean"]),
            "target": str(paths.daily_clean_output),
        },
    }


def _promote_current_stage(
    paths: AShareCurrentRefreshPaths,
    *,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    return {
        "name": "promote_current",
        "writes_latest_aliases": True,
        "alias_updates": _publish_aliases(paths),
        "file_updates": {
            "universe_by_date": {
                "destination": str(paths.universe_version_by_date),
                "alias": str(paths.aliases["universe_by_date"]),
                "source": str(paths.universe_by_date),
            },
            "universe_symbols": {
                "destination": str(paths.universe_version_symbols),
                "alias": str(paths.aliases["universe_symbols"]),
                "source": str(paths.universe_symbols),
            },
            "universe_meta": {
                "destination": str(paths.universe_version_meta),
                "alias": str(paths.aliases["universe_meta"]),
                "source": str(paths.universe_meta),
            },
        },
        "command": _command(
            "marketdata",
            "tushare",
            "promote-a-share-current",
            "--artifacts-root",
            paths.root,
            "--start-date",
            start_date,
            "--end-date",
            end_date,
            "--daily-clean-dir",
            paths.daily_clean_output,
            "--universe-by-date",
            paths.universe_by_date,
            "--universe-symbols",
            paths.universe_symbols,
            "--universe-meta",
            paths.universe_meta,
            "--daily-clean-baseline-report",
            paths.daily_clean_baseline_validation_report,
            "--daily-clean-research-report",
            paths.daily_clean_research_validation_report,
            "--universe-validation-report",
            paths.universe_validation_report,
            "--current-health-report",
            paths.current_health_report,
            "--evidence-out",
            paths.release_evidence,
            "--apply",
        ),
        "required_reports": {
            "daily_clean_baseline_validation": str(paths.daily_clean_baseline_validation_report),
            "daily_clean_research_validation": str(paths.daily_clean_research_validation_report),
            "universe_validation": str(paths.universe_validation_report),
        },
    }


def _refresh_stages(
    paths: AShareCurrentRefreshPaths,
    raw_plan: Mapping[str, Any],
    *,
    start_date: str,
    end_date: str,
    segment: str,
) -> list[dict[str, Any]]:
    return [
        _raw_backfill_stage(
            paths, raw_plan, start_date=start_date, end_date=end_date, segment=segment
        ),
        _daily_clean_build_stage(paths),
        _daily_clean_validate_baseline_stage(paths),
        _daily_clean_validate_research_stage(paths),
        _universe_build_stage(paths, start_date=start_date, end_date=end_date),
        _universe_validate_stage(paths, end_date=end_date),
        _promote_current_stage(paths, start_date=start_date, end_date=end_date),
    ]


def build_a_share_current_refresh_plan(
    *,
    artifacts_root: str | Path | None = None,
    start_date: str,
    end_date: str,
    datasets: Iterable[str] | None = None,
    segment: str = "month",
) -> dict[str, Any]:
    """Build an auditable A-share refresh plan without provider calls or writes."""
    root = resolve_artifacts_root(artifacts_root)
    raw_plan = build_a_share_backfill_plan(
        artifacts_root=root,
        start_date=start_date,
        end_date=end_date,
        datasets=datasets,
        segment=segment,
    )
    paths = _resolve_refresh_paths(root, raw_plan, start_date=start_date, end_date=end_date)
    return {
        "schema_version": "tushare.a_share.current_refresh_plan.v1",
        "market": "a_share",
        "provider": "tushare",
        "status": "planned",
        "no_write": True,
        "artifacts_root": str(root),
        "query": {"start_date": start_date, "end_date": end_date, "segment": segment},
        "publication_policy": {
            "latest_alias_update": "after_daily_clean_and_universe_validation",
            "daily_clean_baseline_validation_report": str(
                paths.daily_clean_baseline_validation_report
            ),
            "daily_clean_research_validation_report": str(
                paths.daily_clean_research_validation_report
            ),
            "daily_clean_validation_required": True,
            "universe_validation_report": str(paths.universe_validation_report),
            "current_contract_update": "after_latest_alias_update",
            "dataset_registry_update": "with_current_contract_update",
            "release_evidence": str(paths.release_evidence),
        },
        "stages": _refresh_stages(
            paths,
            raw_plan,
            start_date=start_date,
            end_date=end_date,
            segment=segment,
        ),
    }


def _resolved_optional_path(value: str | Path | None, fallback: Path) -> Path:
    return Path(value).expanduser().resolve() if value is not None else fallback.resolve()


def _promotion_raw_sources(
    raw_plan: Mapping[str, Any],
    options: AShareCurrentPromotionOptions,
) -> dict[str, Path]:
    defaults = {str(row["dataset"]): Path(str(row["output_dir"])) for row in raw_plan["datasets"]}
    overrides = {
        "daily": options.daily_dir,
        "adj_factor": options.adj_factor_dir,
        "daily_basic": options.daily_basic_dir,
        "limit_status": options.limit_status_dir,
    }
    return {
        dataset: _resolved_optional_path(overrides[dataset], defaults[dataset])
        for dataset in PROMOTION_RAW_DATASETS
    }


def _manifest_summary_for_path(path: Path, *, label: str) -> dict[str, Any]:
    manifest_path = infer_manifest_path(path)
    if manifest_path is None:
        raise FileNotFoundError(f"{label} manifest not found: {path}")
    summary = load_manifest_summary(manifest_path)
    status = str(summary.get("status") or "").strip()
    if status != "completed":
        raise ValueError(f"{label} manifest status must be completed: {manifest_path}")
    return summary


def _load_json_report(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} report not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} report is not a JSON object: {path}")
    return payload


def _passed_report(path: Path, *, label: str) -> dict[str, Any]:
    payload = _load_json_report(path, label=label)
    status = str(payload.get("status") or "").strip().lower()
    if status != "passed":
        raise ValueError(f"{label} report status must be passed: {path}")
    return payload


def _companion_manifest_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.manifest.yml")
