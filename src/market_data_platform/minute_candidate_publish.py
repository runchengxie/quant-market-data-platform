"""Versioned hardlink publication for an audited TuShare minute candidate."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from market_data_platform._tushare_minute_campaign_readiness import file_sha256
from market_data_platform.minute_candidate import (
    CANDIDATE_RECEIPT_SCHEMA_VERSION,
    SEMANTIC_AUDIT_SCHEMA_VERSION,
    MinuteCandidateError,
    _atomic_write_json,
    _json_sha256,
    _now,
    _read_json,
    _valid_inventory,
)


def _validated_candidate_evidence(
    inventory_path: Path,
    semantic_audit_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    inventory = _read_json(inventory_path)
    audit = _read_json(semantic_audit_path)
    valid = (
        _valid_inventory(inventory)
        and isinstance(audit, dict)
        and audit.get("schema_version") == SEMANTIC_AUDIT_SCHEMA_VERSION
        and audit.get("status") == "complete"
        and audit.get("mutation_performed") is False
        and audit.get("inputs", {}).get("inventory_sha256") == file_sha256(inventory_path)
        and audit.get("inputs", {}).get("partitions_sha256") == inventory.get("partitions_sha256")
        and audit.get("cutover_gates", {}).get("canonical_cutover_approved") is False
    )
    if not valid:
        raise MinuteCandidateError("Candidate inventory and semantic audit are inconsistent")
    return inventory, audit


def _candidate_receipt_payload(
    inventory_path: Path,
    semantic_audit_path: Path,
    output_dir: Path,
    inventory: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    daily = [
        {
            "trade_date": record["trade_date"],
            "relative_path": f"trade_date={record['trade_date']}/part-00000.parquet",
            "rows": record["candidate"]["rows"],
            "symbols": record["candidate"]["symbols"],
            "market_symbol_counts": record["candidate"]["market_symbol_counts"],
            "content_sha256": record["candidate"]["partition_sha256"],
            "source_partition_path": record["candidate"]["partition_path"],
            "source_sidecar_path": record["candidate"]["sidecar_path"],
            "source_sidecar_sha256": record["candidate"]["sidecar_sha256"],
        }
        for record in inventory["partitions"]
    ]
    automated_checks_passed = audit["cutover_gates"]["automated_checks_passed"]
    return {
        "schema_version": CANDIDATE_RECEIPT_SCHEMA_VERSION,
        "status": "published_candidate",
        "quality_status": ("passed_diagnostic" if automated_checks_passed else "review_required"),
        "generated_at": _now(),
        "dataset": "a_share_minute_1m_tushare_candidate",
        "output_dir": str(output_dir),
        "writes_production": False,
        "current_alias_mutated": False,
        "canonical_cutover_approved": False,
        "storage": {
            "mode": "hardlink",
            "source_retention_required": False,
            "candidate_files_survive_source_unlink": True,
        },
        "inputs": {
            "inventory": str(inventory_path),
            "inventory_sha256": file_sha256(inventory_path),
            "semantic_audit": str(semantic_audit_path),
            "semantic_audit_sha256": file_sha256(semantic_audit_path),
            "partitions_sha256": inventory["partitions_sha256"],
            "daily_records_sha256": audit["daily_records_sha256"],
        },
        "summary": {
            "dates": inventory["summary"]["dates"],
            "date_min": inventory["summary"]["date_min"],
            "date_max": inventory["summary"]["date_max"],
            "rows": inventory["summary"]["rows"],
            "symbol_days": inventory["summary"]["symbol_days"],
            "market_scope": "SH_SZ_BJ",
        },
        "cutover_gates": audit["cutover_gates"],
        "daily_sha256": _json_sha256(daily),
        "daily": daily,
    }


def _link_candidate_tree(
    inventory: dict[str, Any],
    staging_dir: Path,
) -> None:
    for record in inventory["partitions"]:
        trade_date = record["trade_date"]
        source = Path(record["candidate"]["partition_path"])
        destination_dir = staging_dir / f"trade_date={trade_date}"
        destination_dir.mkdir(parents=True)
        destination = destination_dir / "part-00000.parquet"
        try:
            os.link(source, destination)
        except OSError as exc:
            raise MinuteCandidateError(
                f"Candidate publication requires same-filesystem hardlinks: {source}"
            ) from exc
        if source.stat().st_ino != destination.stat().st_ino:
            raise MinuteCandidateError(f"Candidate hardlink identity mismatch: {destination}")


def publish_candidate(
    inventory_json: str | Path,
    semantic_audit_json: str | Path,
    output_dir: str | Path,
    receipt_json: str | Path,
) -> dict[str, Any]:
    """Publish a versioned hardlink candidate without mutating any current alias."""

    inventory_path = Path(inventory_json).expanduser().resolve()
    semantic_path = Path(semantic_audit_json).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    receipt_path = Path(receipt_json).expanduser().resolve()
    inventory, audit = _validated_candidate_evidence(inventory_path, semantic_path)
    if destination.exists():
        embedded_path = destination / "_candidate_receipt.json"
        if embedded_path.is_file():
            embedded = _read_json(embedded_path)
            if embedded.get("inputs", {}).get("inventory_sha256") == file_sha256(
                inventory_path
            ) and embedded.get("inputs", {}).get("semantic_audit_sha256") == file_sha256(
                semantic_path
            ):
                _atomic_write_json(receipt_path, embedded)
                return embedded
        raise MinuteCandidateError(f"Candidate output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
    payload = _candidate_receipt_payload(
        inventory_path,
        semantic_path,
        destination,
        inventory,
        audit,
    )
    try:
        staging_dir.mkdir()
        _link_candidate_tree(inventory, staging_dir)
        _atomic_write_json(staging_dir / "_candidate_receipt.json", payload)
        os.replace(staging_dir, destination)
        _atomic_write_json(receipt_path, payload)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
    return payload


__all__ = ["publish_candidate"]
