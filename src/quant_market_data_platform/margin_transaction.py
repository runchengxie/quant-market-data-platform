"""Durable, catalog-local financing pointer transactions and explicit recovery."""

from __future__ import annotations

import fcntl
import os
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .margin_version import (
    DATASETS,
    digest_json,
    read_json,
    require_external,
    verify_version,
    verify_version_identity,
)
from .publish.json_manifest import write_json_manifest


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_json(path: Path, payload: dict[str, Any]) -> None:
    write_json_manifest(path, payload)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    _sync_directory(path.parent)


@contextmanager
def catalog_lock(
    catalog: Path, *, create: bool = False, allow_pending: bool = False
) -> Generator[None]:
    require_external(catalog)
    if catalog.is_symlink():
        raise ValueError("Publication catalog must be physical")
    for relative in (
        "catalog.json",
        "current",
        "versions",
        "receipts",
        "intent.json",
        "versions/margin",
        "versions/margin_detail",
    ):
        if (catalog / relative).is_symlink():
            raise ValueError("Catalog control paths must be physical")
    marker, lock = catalog / "catalog.json", catalog / ".lock"
    new = not catalog.exists()
    if new and not create:
        raise ValueError("An existing catalog is required")
    if not new and (not marker.is_file() or not lock.is_file()):
        raise ValueError("An existing complete catalog is required")
    if new:
        catalog.mkdir(parents=True)
    descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if new else 0), 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if new:
            durable_json(marker, {"schema_version": "tushare.margin.catalog.v1"})
        if read_json(marker) != {"schema_version": "tushare.margin.catalog.v1"}:
            raise ValueError("Invalid publication catalog")
        intent = catalog / "intent.json"
        if intent.exists():
            transaction = _validated_intent(intent)
            if transaction["status"] == "pending" and not allow_pending:
                raise ValueError("Catalog has a pending transaction; explicit recovery required")
        yield
    finally:
        os.close(descriptor)


def current_link(alias: Path) -> str | None:
    if alias.is_symlink():
        if alias.readlink().is_symlink():
            raise ValueError("Current version target must be physical")
        if not alias.readlink().is_absolute():
            raise ValueError("Catalog current links must be absolute")
        return str(alias.readlink())
    if alias.exists():
        raise ValueError("Refusing physical legacy alias conversion")
    return None


def _write_intent(catalog: Path, intent: dict[str, Any]) -> None:
    payload = {key: value for key, value in intent.items() if key != "integrity_sha256"}
    durable_json(catalog / "intent.json", {**payload, "integrity_sha256": digest_json(payload)})


def _restore(alias: Path, intent: dict[str, Any]) -> None:
    old, new = intent["previous_link"], intent["target"]
    current = current_link(alias)
    if current not in (old, new):
        raise ValueError("Unknown current pointer; refusing transaction recovery")
    if current == new:
        if old is None:
            alias.unlink()
        else:
            temporary = alias.with_name(f".restore-{uuid.uuid4().hex}")
            temporary.symlink_to(old)
            os.replace(temporary, alias)
        _sync_directory(alias.parent)


def commit_alias(alias: Path, target: Path, result: dict[str, Any]) -> None:
    catalog = alias.parent.parent
    alias.parent.mkdir(parents=True, exist_ok=True)
    intent = {**result, "status": "pending", "transaction_id": uuid.uuid4().hex}
    _write_intent(catalog, intent)
    temporary = alias.with_name(f".{alias.name}.{uuid.uuid4().hex}")
    try:
        temporary.symlink_to(target)
        if current_link(alias) != result["previous_link"]:
            raise ValueError("Current pointer CAS changed before commit")
        verify_version(target, result["target_receipt_sha256"])
        os.replace(temporary, alias)
        _sync_directory(alias.parent)
        result["committed_at"] = datetime.now(UTC).isoformat()
        durable_json(catalog / "receipts" / f"{intent['transaction_id']}.json", result)
        _write_intent(catalog, {**intent, "status": "committed"})
    except Exception:
        _restore(alias, intent)
        _write_intent(catalog, {**intent, "status": "rolled_back"})
        raise
    finally:
        temporary.unlink(missing_ok=True)


def recover_margin_catalog(*, catalog: Path, apply: bool = False) -> dict[str, Any]:
    catalog = catalog.absolute()
    with catalog_lock(catalog, allow_pending=True):
        intent = read_json(catalog / "intent.json")
        payload = {key: value for key, value in intent.items() if key != "integrity_sha256"}
        if (
            intent.get("integrity_sha256") != digest_json(payload)
            or intent.get("status") != "pending"
        ):
            raise ValueError("Invalid pending transaction integrity/status")
        dataset = intent.get("dataset")
        if dataset not in DATASETS:
            raise ValueError("Invalid transaction dataset")
        target = Path(intent["target"])
        if not target.resolve().is_relative_to(catalog.resolve() / "versions" / dataset):
            raise ValueError("Transaction target escapes catalog")
        verify_version_identity(target, intent["target_receipt_sha256"])
        alias = catalog / "current" / dataset
        if intent["previous_link"] is not None:
            old = Path(intent["previous_link"])
            if not old.resolve().is_relative_to(catalog.resolve() / "versions" / dataset):
                raise ValueError("Previous transaction target escapes catalog")
            verify_version(old, intent["previous_receipt_sha256"])
        if current_link(alias) not in (intent["previous_link"], intent["target"]):
            raise ValueError("Unknown current pointer; refusing transaction recovery")
        if apply:
            _restore(alias, intent)
            _write_intent(catalog, {**intent, "status": "rolled_back"})
        return {
            "dry_run": not apply,
            "operation": "recover-rollback",
            "intent": intent,
            "legacy_alias_mutated": False,
        }


def _validated_intent(path: Path) -> dict[str, Any]:
    intent = read_json(path)
    payload = {key: value for key, value in intent.items() if key != "integrity_sha256"}
    if intent.get("integrity_sha256") != digest_json(payload) or intent.get("status") not in {
        "pending",
        "committed",
        "rolled_back",
    }:
        raise ValueError("Invalid transaction journal integrity/status")
    return intent


def sync_version(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for path in sorted(directories, key=lambda value: len(value.parts), reverse=True):
        _sync_directory(path)
    _sync_directory(root)
    _sync_directory(root.parent)
