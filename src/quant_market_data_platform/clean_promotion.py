"""Opt-in clean-only promotion; raw and universe publication remain separate."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from quant_market_data_platform.clean_migration import CleanMigrationTarget
from quant_market_data_platform.clean_publication import (
    _contained,
    _read_journal,
    plan_clean_migration,
    publish_clean,
)
from quant_market_data_platform.paths import current_contract_path, dataset_registry_path
from quant_market_data_platform.tushare_refresh_part01 import (
    AShareCurrentPromotionOptions,
    AShareCurrentRefreshPaths,
    _manifest_summary_for_path,
    _passed_report,
)


def run_clean_only_promotion(
    context: Mapping[str, Any],
    options: AShareCurrentPromotionOptions,
    *,
    finish: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    root = Path(context["root"])
    paths = context["refresh_paths"]
    if not isinstance(paths, AShareCurrentRefreshPaths):
        raise ValueError("Invalid clean promotion paths")
    alias = paths.aliases["daily_clean"]
    target = Path(context["daily_clean"])
    plan = plan_clean_migration(root=root, alias=alias, target=target)
    for asset in context["required_assets"]:
        if asset != "daily_clean" and not paths.aliases[asset].exists():
            raise ValueError(f"Clean-only promotion requires existing current asset: {asset}")
    checks = {
        "daily_clean_manifest": _manifest_summary_for_path(target, label="daily_clean asset"),
        "reports": {
            name: _passed_report(Path(context["reports"][name]), label=name)
            for name in ("daily_clean_baseline", "daily_clean_research")
        },
    }
    journal = alias.with_name(f".{alias.name}.clean-publication-lock") / "journal.json"
    clean_update = {**plan, "journal": str(journal), "target": str(target)}
    metadata = _metadata(context)
    if not options.apply:
        return {
            "schema_version": "tushare.a_share.current_release.v1",
            "status": "ready" if plan["apply_supported"] else "blocked",
            "dry_run": True,
            "mode": "clean-only-linked",
            "market": "a_share",
            "provider": "tushare",
            "artifacts_root": str(root),
            "query": {"start_date": options.start_date, "end_date": options.end_date},
            "required_assets": list(context["required_assets"]),
            "checks": checks,
            "planned_publication": {
                "alias_updates": {"daily_clean": clean_update},
                "file_updates": {},
                "metadata": list(map(str, metadata)),
            },
        }
    return finish_linked_promotion(
        context,
        checks,
        options,
        {"alias_updates": {}, "file_updates": {}},
        finish=finish,
    )


def _metadata(context: Mapping[str, Any]) -> list[Path]:
    root = Path(context["root"])
    paths = [
        current_contract_path(root, market="a_share"),
        dataset_registry_path(root),
        root / "metadata/current_assets/.registry-pending.json",
        Path(context["current_health_report"]),
        Path(context["evidence_out"]),
    ]
    paths = [_contained(root, path) for path in paths]
    if any(path.is_symlink() or (path.exists() and not path.is_file()) for path in paths):
        raise ValueError("Linked metadata outputs must be physical files")
    resolved = [path.resolve() for path in paths]
    inputs = {Path(path).resolve() for path in context["reports"].values()}
    if len(set(resolved)) != len(paths):
        raise ValueError("Linked metadata outputs must be distinct")
    if any(path.is_relative_to(root / "assets") or path in inputs for path in resolved):
        raise ValueError("Linked metadata must not overwrite assets or validation inputs")
    return paths


def preflight_recurring_linked(context: Mapping[str, Any], *, apply: bool) -> None:
    from quant_market_data_platform.clean_publication import acknowledge_clean

    _metadata(context)
    root = Path(context["root"])
    paths = context["refresh_paths"]
    alias = paths.aliases["daily_clean"]
    plan = plan_clean_migration(root=root, alias=alias, target=Path(context["daily_clean"]))
    if plan["requires_explicit_migration"]:
        raise ValueError("Recurring linked promotion requires prior clean-only physical migration")
    lock = alias.with_name(f".{alias.name}.clean-publication-lock")
    if lock.is_symlink():
        raise ValueError("Publication journal lock must be physical")
    if lock.exists():
        state = _read_journal(lock / "journal.json")
        if state["root"] != str(root) or state["alias"] != str(alias):
            raise ValueError("Publication journal identity mismatch")
        if (
            state["status"] != "committed"
            or not alias.is_symlink()
            or str(alias.readlink()) != state["new_link"]
        ):
            raise ValueError("Pending publication requires explicit recovery")
        if apply:
            acknowledge_clean(root=root, alias=alias)


def finish_linked_promotion(
    context: Mapping[str, Any],
    checks: Mapping[str, Any],
    options: AShareCurrentPromotionOptions,
    updates: Mapping[str, Any],
    *,
    finish: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    from quant_market_data_platform.clean_publication import acknowledge_clean

    root = Path(context["root"])
    paths = context["refresh_paths"]
    alias = paths.aliases["daily_clean"]
    target = Path(context["daily_clean"])
    metadata = _metadata(context)
    for path in metadata:
        path.parent.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {}
    alias_updates = dict(updates["alias_updates"])
    journal = alias.with_name(f".{alias.name}.clean-publication-lock") / "journal.json"

    def commit() -> None:
        state = _read_journal(journal)
        alias_updates["daily_clean"] = {
            "alias_path": str(alias),
            "target": str(target),
            "materialized": "false",
            "resolved_path": str(target.resolve()),
            "manifest_path": str((target / "manifest.yml").resolve()),
            "publication_mode": "relative-symlink",
            "journal": str(Path(state["archive_directory"]) / "journal.json"),
            **({"physical_backup": state["physical_backup"]} if "physical_backup" in state else {}),
        }
        evidence.update(finish(context, checks, options, alias_updates, updates["file_updates"]))

    publish_clean(
        root=root,
        alias=alias,
        target=(
            CleanMigrationTarget(target, options.clean_migration_sha256)
            if options.clean_migration_sha256 is not None
            else target
        ),
        metadata=metadata,
        commit=commit,
    )
    acknowledge_clean(root=root, alias=alias)
    return evidence
