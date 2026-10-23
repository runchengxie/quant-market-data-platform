"""Opt-in POSIX clean publication; existing materialized promotion is unchanged.

The alias and metadata are separate writes. A durable journal and exclusive owner
lock permit explicit rollback after interruption; readers must still pin versions.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from quant_market_data_platform.clean_migration import (
    CleanMigrationTarget,
    physical_plan,
    restore_physical,
)
from quant_market_data_platform.publication_lock import publication_lock


def _contained(root: Path, path: Path) -> Path:
    path = Path(os.path.abspath(path.expanduser()))
    if not path.is_relative_to(root) or not path.resolve().is_relative_to(root):
        raise ValueError("Publication paths must remain inside root")
    if path.parent.resolve() != path.parent:
        raise ValueError("Publication parent directories must be physical")
    return path


def _paths(root: Path, alias: Path, target: Path) -> tuple[Path, Path, Path]:
    root = root.expanduser().resolve(strict=True)
    alias = _contained(root, alias)
    target = _contained(root, target).resolve(strict=True)
    if alias == target or target.is_relative_to(alias) or alias.is_relative_to(target):
        raise ValueError("Alias and target must be disjoint")
    if not target.is_dir() or target.is_symlink():
        raise ValueError("Clean target must be a directory")
    manifest = target / "manifest.yml"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Clean target requires a physical manifest")
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        raise ValueError("Clean target manifest must be completed")
    if payload.get("dataset") != "daily_clean":
        raise ValueError("Only daily_clean targets are supported")
    if Path(str(payload.get("output_dir", ""))).resolve() != target:
        raise ValueError("Manifest output_dir must identify the real version")
    if not (target / "data").is_dir() or (target / "data").is_symlink():
        raise ValueError("Clean target requires a physical data directory")
    if any(path.is_symlink() for path in (target / "data").rglob("*")):
        raise ValueError("Clean data must not contain symlinks")
    if alias.is_symlink():
        alias.resolve(strict=True)
    elif alias.exists() and not alias.is_dir():
        raise ValueError("Clean alias must be a directory or symlink")
    return root, alias, target


def plan_clean_migration(*, root: Path, alias: Path, target: Path) -> dict[str, Any]:
    """Read-only plan. Physical aliases require a separately coordinated migration."""
    root, alias, target = _paths(root, alias, target)
    physical = alias.is_dir() and not alias.is_symlink()
    details = physical_plan(alias, target) if physical else {}
    return {
        "root": str(root),
        "alias_path": str(alias),
        "resolved_path": str(target),
        "manifest_path": str(target / "manifest.yml"),
        "materialized": "false",
        "requires_explicit_migration": physical,
        "rollback_source": str(alias) if physical else None,
        "apply_supported": not physical or details["equivalent"],
        **details,
    }


def _write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    created = False
    try:
        with temporary.open("xb") as stream:
            created = True
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        created = False
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def _save(journal: Path, state: dict[str, Any]) -> None:
    _write(journal, json.dumps(state, sort_keys=True).encode())


def _link(alias: Path, target: str) -> None:
    temporary = alias.with_name(f".{alias.name}.{uuid4().hex}.link")
    created = False
    try:
        temporary.symlink_to(target, target_is_directory=True)
        created = True
        temporary.replace(alias)
        created = False
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def _lock(alias: Path) -> Path:
    return alias.with_name(f".{alias.name}.clean-publication-lock")


def _restore_metadata(root: Path, snapshots: list[dict[str, Any]]) -> None:
    destinations = []
    for item in snapshots:
        path = _contained(root, Path(item["path"]))
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Metadata recovery destination must be a physical file")
        content = item["content"]
        destinations.append(
            (path, None if content is None else base64.b64decode(content, validate=True))
        )
    for path, content in destinations:
        if content is None:
            path.unlink(missing_ok=True)
        else:
            _write(path, content)


def _metadata_hashes(state: dict[str, Any]) -> dict[str, str | None]:
    return {
        item["path"]: hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()
        if Path(item["path"]).exists()
        else None
        for item in state["metadata"]
    }


def _check_metadata(state: dict[str, Any]) -> None:
    expected = state.get("metadata_hashes")
    if expected is None or _metadata_hashes(state) != expected:
        raise ValueError("Metadata changed or ownership is unproven; recovery stopped")


def _read_journal(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Publication journal must be physical")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("status") not in {
        "prepared",
        "committed",
        "rolled_back",
        "rollback_requested",
    }:
        raise ValueError("Unknown publication journal state")
    return state


def recover_clean(*, root: Path, alias: Path) -> dict[str, Any]:
    """Rollback the active owner journal. Never infer ownership from directory age."""
    with publication_lock(root):
        root = root.expanduser().resolve(strict=True)
        alias = _contained(root, alias)
        lock = _lock(alias)
        if lock.is_symlink():
            raise ValueError("Publication lock must be physical")
        journal = lock / "journal.json"
        state = _read_journal(journal)
        if state["root"] != str(root) or state["alias"] != str(alias):
            raise ValueError("Journal identity does not match publication")
        if state["status"] == "committed":
            return state
        if state["status"] == "rolled_back":
            return state
        _check_metadata(state)
        if state.get("old_kind") == "directory":
            restore_physical(
                alias=alias,
                backup=_contained(root, Path(state["physical_backup"])),
                expected_sha256=state["source_sha256"],
                new_link=state["new_link"],
                restore_metadata=lambda: _restore_metadata(root, state["metadata"]),
            )
            state["status"] = "rolled_back"
            _save(journal, state)
            return state
        if alias.exists() and not alias.is_symlink():
            raise ValueError("Refusing recovery over a physical alias")
        current = str(alias.readlink()) if alias.is_symlink() else None
        if current not in (state["old_link"], state["new_link"]):
            raise ValueError("Alias changed outside this publication; recovery stopped")
        for link in (state["old_link"], state["new_link"]):
            if link is not None:
                _contained(root, alias.parent / link)
        _restore_metadata(root, state["metadata"])
        if state["old_link"] is None:
            alias.unlink(missing_ok=True)
        else:
            _link(alias, state["old_link"])
        state["status"] = "rolled_back"
        _save(journal, state)
        return state


def _metadata_paths(root: Path, alias: Path, target: Path, metadata: Iterable[Path]) -> list[Path]:
    paths = []
    for entry in metadata:
        path = _contained(root, entry)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Metadata must be physical files")
        protected = (alias, target, _lock(alias))
        if alias.is_symlink():
            protected += (alias.resolve(strict=True),)
        if any(
            path.is_relative_to(parent) or path.resolve().is_relative_to(parent)
            for parent in protected
        ):
            raise ValueError("Metadata must not overlap publication paths")
        if not path.parent.is_dir():
            raise ValueError("Metadata parent must already exist")
        paths.append(path)
    return paths


def publish_clean(
    *,
    root: Path,
    alias: Path,
    target: Path | CleanMigrationTarget,
    metadata: Iterable[Path],
    commit: Callable[[], object],
) -> dict[str, Any]:
    """Explicit opt-in API. Callback writes declared JSON and checks health.

    Only this clean alias and declared metadata are recovered. The callback must
    not modify raw aliases, universe files, providers or any undeclared resource.
    Journals remain for inspection; acknowledge them before another publication.
    """
    with publication_lock(root):
        if os.name != "posix":
            raise ValueError("Clean linked publication is currently POSIX-only")
        migration_sha256 = (
            target.source_sha256 if isinstance(target, CleanMigrationTarget) else None
        )
        target = target.path if isinstance(target, CleanMigrationTarget) else target
        root, alias, target = _paths(root, alias, target)
        plan = plan_clean_migration(root=root, alias=alias, target=target)
        physical = plan["requires_explicit_migration"]
        if migration_sha256 is not None and not physical:
            raise ValueError("Migration confirmation requires a physical alias")
        if physical and migration_sha256 is None:
            raise ValueError("Physical latest requires explicit coordinated migration")
        if physical and (not plan["equivalent"] or migration_sha256 != plan["source_sha256"]):
            raise ValueError("Physical migration equivalence or saved identity changed")
        backup = (
            alias.with_name(f".{alias.name}.physical-rollback.{uuid4().hex}") if physical else None
        )
        if backup is not None and (backup.exists() or backup.is_symlink()):
            raise ValueError("Physical rollback backup already exists")
        snapshots: list[dict[str, Any]] = [
            {"path": str(path), "content": None}
            for path in _metadata_paths(root, alias, target, metadata)
        ]
        lock = _lock(alias)
        lock.mkdir()  # Exclusive; stale locks require explicit recovery, never deletion.
        journal = lock / "journal.json"
        for item in snapshots:
            path = Path(item["path"])
            item["content"] = (
                base64.b64encode(path.read_bytes()).decode() if path.exists() else None
            )
        state: dict[str, Any] = {
            "schema_version": "clean_publication.v1",
            "root": str(root),
            "alias": str(alias),
            "status": "prepared",
            "old_link": str(alias.readlink()) if alias.is_symlink() else None,
            "new_link": os.path.relpath(target, alias.parent),
            "metadata": snapshots,
            "archive_directory": str(lock.with_name(f"{lock.name}.archive.{uuid4().hex}")),
            "old_kind": "directory" if physical else "link",
            **(
                {
                    "physical_backup": str(backup),
                    "source_sha256": plan["source_sha256"],
                    "target_sha256": plan["target_sha256"],
                    "inventory_algorithm": "sha256-json-relative-tree-v1",
                }
                if physical
                else {}
            ),
        }
        state["metadata_hashes"] = _metadata_hashes(state)
        _save(journal, state)
        try:
            if physical:
                alias.rename(Path(state["physical_backup"]))
            _link(alias, state["new_link"])
            commit()
            state["metadata_hashes"] = _metadata_hashes(state)
            state["status"] = "committed"
            _save(journal, state)
        except BaseException as exc:
            # We still own the publication lock, so these writes belong to this callback.
            state["metadata_hashes"] = _metadata_hashes(state)
            _save(journal, state)
            if isinstance(exc, Exception):
                recover_clean(root=root, alias=alias)
            raise
        return {
            **plan,
            "journal": str(journal),
            "status": "committed",
            **({"physical_backup": state["physical_backup"]} if physical else {}),
        }


def acknowledge_clean(*, root: Path, alias: Path) -> Path:
    """Archive a terminal journal, preserving backups before allowing the next run."""
    with publication_lock(root):
        root = root.expanduser().resolve(strict=True)
        alias = _contained(root, alias)
        lock = _lock(alias)
        if lock.is_symlink():
            raise ValueError("Publication lock must be physical")
        state = _read_journal(lock / "journal.json")
        if state["root"] != str(root) or state["alias"] != str(alias):
            raise ValueError("Journal identity does not match publication")
        if state["status"] not in {"committed", "rolled_back"}:
            raise ValueError("Interrupted publication must recover before acknowledgement")
        archive = _contained(
            root,
            Path(
                state.get("archive_directory")
                or lock.with_name(f"{lock.name}.archive.{uuid4().hex}")
            ),
        )
        if archive.parent != lock.parent or not archive.name.startswith(f"{lock.name}.archive."):
            raise ValueError("Invalid publication archive destination")
        if archive.exists() or archive.is_symlink():
            raise FileExistsError("Publication archive destination already exists")
        lock.rename(archive)
        return archive


def rollback_clean(*, root: Path, alias: Path, journal: Path | None = None) -> dict[str, Any]:
    """Explicitly rollback a committed publication after stopping all writers."""
    with publication_lock(root):
        root = root.expanduser().resolve(strict=True)
        alias = _contained(root, alias)
        lock = _lock(alias)
        source = lock / "journal.json" if journal is None else _contained(root, journal)
        if source.is_symlink() or source.parent.is_symlink():
            raise ValueError("Rollback journal must be physical")
        state = _read_journal(source)
        if state["root"] != str(root) or state["alias"] != str(alias):
            raise ValueError("Rollback journal identity mismatch")
        _check_metadata(state)
        if journal is not None:
            if source.parent.parent != alias.parent or not source.parent.name.startswith(
                f"{lock.name}.archive."
            ):
                raise ValueError("Rollback must reference this alias archive")
            lock.mkdir()
            state["rollback_of"] = str(source)
            state["archive_directory"] = str(lock.with_name(f"{lock.name}.archive.{uuid4().hex}"))
        state["status"] = "rollback_requested"
        _save(lock / "journal.json", state)
        return recover_clean(root=root, alias=alias)


def main(argv: list[str] | None = None) -> int:
    """Plan clean migration or explicitly recover, rollback or acknowledge a journal."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--alias", type=Path, required=True)
    parser.add_argument("--target", type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--recover", action="store_true")
    actions.add_argument("--rollback", action="store_true")
    actions.add_argument("--acknowledge", action="store_true")
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args(argv)
    if args.journal is not None and not args.rollback:
        parser.error("--journal requires --rollback")
    if args.recover or args.rollback:
        state = (
            rollback_clean(root=args.root, alias=args.alias, journal=args.journal)
            if args.rollback
            else recover_clean(root=args.root, alias=args.alias)
        )
        payload = {"status": state["status"], "alias_path": str(args.alias)}
    elif args.acknowledge:
        payload = {"archive_directory": str(acknowledge_clean(root=args.root, alias=args.alias))}
    else:
        if args.target is None:
            parser.error("--target is required for planning")
        payload = plan_clean_migration(root=args.root, alias=args.alias, target=args.target)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
