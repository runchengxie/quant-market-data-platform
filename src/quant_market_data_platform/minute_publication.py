"""Coordinate operational minute aliases and their current catalog descriptions."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from .current_publication import sync_minute_catalog
from .minute_candidate import MinuteCandidateError, _atomic_write_json, _read_json
from .publication_lock import publication_lock


def publication_root(alias: Path) -> Path:
    if alias.parent.resolve() != alias.parent:
        raise MinuteCandidateError("Operational alias parents must be physical")
    for parent in alias.parents:
        if parent.name == "assets":
            return parent.parent.resolve()
    raise MinuteCandidateError("Operational alias must be inside an artifacts assets directory")


def alias_identity(alias: Path) -> str | None:
    receipt = alias / "_operational_receipt.json"
    return hashlib.sha256(receipt.read_bytes()).hexdigest() if receipt.is_file() else None


def commit_operational(
    alias: Path,
    receipt: dict[str, Any],
    payload: dict[str, Any],
    expected_identity: str | None,
    replace_alias: Callable[[Path], None],
) -> dict[str, Any]:
    root = publication_root(alias)
    promotion_path = Path(payload.pop("promotion_receipt_path"))
    with publication_lock(root):
        current_identity = alias_identity(alias)
        if current_identity != expected_identity:
            raise MinuteCandidateError("Operational alias changed during publication planning")
        same_version = current_identity == payload["version_receipt_sha256"]
        if current_identity is not None and not same_version:
            current = _read_json(alias / "_operational_receipt.json")
            if (
                receipt["summary"]["date_max"] < current["summary"]["date_max"]
                or receipt.get("inputs", {}).get("base_receipt_sha256") != current_identity
            ):
                raise MinuteCandidateError(
                    "Operational publication refuses stale base or older version"
                )
    staging = alias.with_name(f".{alias.name}.staging-{uuid4().hex}")
    try:
        if not same_version:
            shutil.copytree(Path(receipt["output_dir"]), staging, symlinks=False)
            if alias_identity(staging) != payload["version_receipt_sha256"]:
                raise MinuteCandidateError("Staged operational receipt changed during copy")
        with publication_lock(root):
            if alias_identity(alias) != expected_identity:
                raise MinuteCandidateError("Operational alias changed during publication planning")
            _atomic_write_json(promotion_path, {**payload, "status": "metadata_pending"})
            if not same_version:
                replace_alias(staging)
            sync_minute_catalog(root, alias)
            _atomic_write_json(promotion_path, payload)
            return payload
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def reconcile_operational_catalog(root: Path, alias: Path, promotion_path: Path) -> None:
    """Finish a daily metadata-only retry, including its pending promotion receipt."""
    with publication_lock(root):
        payload = _read_json(promotion_path) if promotion_path.exists() else None
        if payload is not None and payload.get("status") == "metadata_pending":
            if payload.get("version_receipt_sha256") != alias_identity(alias):
                raise MinuteCandidateError("Pending promotion does not identify current alias")
            sync_minute_catalog(root, alias)
            _atomic_write_json(promotion_path, {**payload, "status": "operational_canonical"})
        else:
            sync_minute_catalog(root, alias)
