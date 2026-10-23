"""Immutable financing versions in a separate opt-in publication catalog."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .margin_transaction import catalog_lock, commit_alias, current_link, durable_json, sync_version
from .margin_transaction import recover_margin_catalog as recover_margin_catalog
from .margin_version import (
    DATASETS,
    digest_json,
    file_hash,
    inventory,
    require_external,
    validate_candidate,
    validate_lineage,
    verify_version,
    verify_version_identity,
)
from .publish.json_manifest import write_json_manifest
from .publish.manifest import write_manifest


def prepare_margin_version(*, candidate: Path, catalog: Path, lineage: Path) -> dict[str, Any]:
    if candidate.is_symlink():
        raise ValueError("Candidate must be a physical directory")
    candidate, catalog, lineage = candidate.resolve(), catalog.absolute(), lineage.resolve()
    if catalog.resolve().is_relative_to(candidate) or candidate.is_relative_to(catalog.resolve()):
        raise ValueError("Candidate and publication catalog must be independent")
    require_external(candidate)
    original = inventory(candidate)
    raw, quality = validate_candidate(candidate)
    sources = validate_lineage(lineage)
    covered = {item["sha256"] for item in sources["inputs"]}
    if any(value not in covered for key, value in original.items() if key.startswith("data/")):
        raise ValueError("Source lineage does not cover candidate partitions")
    lineage_sha = file_hash(lineage)
    identity = digest_json({"inventory": original, "lineage_sha256": lineage_sha})
    version = catalog / "versions" / raw["dataset"] / identity
    with catalog_lock(catalog, create=True):
        if version.exists():
            raise ValueError("Immutable version already exists")
        if original != inventory(candidate) or file_hash(lineage) != lineage_sha:
            raise ValueError("Candidate or lineage changed during preparation")
        validate_lineage(lineage)
        version.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(candidate, version)
        if inventory(version) != original:
            raise ValueError("Candidate changed while copying; partial version retained")
        write_json_manifest(version / "source-lineage.json", sources)
        manifest = {
            **raw,
            "schema_version": f"tushare.{raw['dataset']}.publication.v1",
            "output_dir": str(version),
            "candidate_only": True,
            "published": False,
            "production_eligible": False,
            "source_candidate_inventory": original,
            "source_lineage_sha256": lineage_sha,
            "quality": quality,
        }
        write_manifest(version / "manifest.yml", manifest)
        write_json_manifest(version / "inventory.json", inventory(version))
        receipt = {
            "schema_version": "tushare.margin.publication.v1",
            "status": "prepared",
            "dataset": raw["dataset"],
            "version": str(version),
            "prepared_at": datetime.now(UTC).isoformat(),
            "historical_pit_certified": False,
            "full_market_certified": False,
            "inventory_sha256": file_hash(version / "inventory.json"),
            "candidate_digest": identity,
            "validation_scope": ["schema", "keys", "input_hashes", "declared_partition_dates"],
            "producer": {"owner": "quant-market-data-platform", "source_revision": None},
        }
        if original != inventory(candidate) or file_hash(lineage) != lineage_sha:
            raise ValueError("Source changed; partial version retained for inspection")
        validate_lineage(lineage)
        sync_version(version)
        durable_json(version / "publication-receipt.json", receipt)
        return {**receipt, "receipt_sha256": file_hash(version / "publication-receipt.json")}


@dataclass(frozen=True)
class MarginSwitchOptions:
    catalog: Path
    dataset: str
    target: Path
    target_sha256: str
    expected: str | None
    apply: bool = False
    expected_current_sha256: str | None = None
    operation: str = "adopt"


def switch_margin_version(options: MarginSwitchOptions) -> dict[str, Any]:
    if options.target.is_symlink():
        raise ValueError("Version target must be physical")
    catalog, dataset, target = options.catalog, options.dataset, options.target
    target_sha256, expected, apply = options.target_sha256, options.expected, options.apply
    expected_current_sha256, operation = options.expected_current_sha256, options.operation
    catalog, target = catalog.absolute(), target.resolve()
    if dataset not in DATASETS or operation not in {"adopt", "rollback"}:
        raise ValueError("Invalid financing adoption request")
    if not target.is_relative_to(catalog.resolve() / "versions" / dataset):
        raise ValueError("Target must be inside this dataset's publication catalog")
    with catalog_lock(catalog):
        alias = catalog / "current" / dataset
        if current_link(alias) != expected:
            raise ValueError("Current pointer CAS mismatch")
        receipt = verify_version(target, target_sha256)
        if receipt["dataset"] != dataset:
            raise ValueError("Version dataset mismatch")
        if expected is not None:
            if not alias.resolve().is_relative_to(catalog.resolve() / "versions" / dataset):
                raise ValueError("Current target escapes publication catalog")
            if expected_current_sha256 is None:
                raise ValueError("Current version receipt hash required for CAS")
            check = verify_version_identity if operation == "rollback" else verify_version
            check(alias.resolve(), expected_current_sha256)
        result = {
            "schema_version": "tushare.margin.adoption.v1",
            "operation": operation,
            "dataset": dataset,
            "previous_link": expected,
            "previous_receipt_sha256": expected_current_sha256,
            "target": str(target),
            "target_receipt_sha256": target_sha256,
            "dry_run": not apply,
            "legacy_alias_mutated": False,
        }
        if not apply:
            return result
        commit_alias(alias, target, result)
        return result


def export_margin_contract(*, catalog: Path) -> dict[str, Any]:
    """Return an explicit opt-in PublishedAsset contract; never register it as default."""
    catalog = catalog.absolute()
    with catalog_lock(catalog):
        assets = {}
        for dataset in DATASETS:
            alias = catalog / "current" / dataset
            if current_link(alias) is None:
                continue
            target = alias.resolve()
            if not target.is_relative_to(catalog.resolve() / "versions" / dataset):
                raise ValueError("Current target escapes catalog")
            receipt_path = target / "publication-receipt.json"
            verify_version(target, file_hash(receipt_path))
            assets[dataset] = {
                "alias_path": str(alias),
                "resolved_path": str(target),
                "manifest_path": str(target / "manifest.yml"),
                "exists": True,
                "as_of": None,
            }
        return {
            "contract": {
                "name": "a_share_margin_catalog",
                "version": 1,
                "market": "a_share",
                "provider": "tushare",
                "artifacts_root": str(catalog.resolve()),
            },
            "assets": assets,
        }
