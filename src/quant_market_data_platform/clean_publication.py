"""Opt-in POSIX clean publication; existing materialized promotion is unchanged.

The alias and metadata are separate writes. A durable journal and exclusive owner
lock permit explicit rollback after interruption; readers must still pin versions.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml


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
    return {
        "root": str(root),
        "alias_path": str(alias),
        "resolved_path": str(target),
        "manifest_path": str(target / "manifest.yml"),
        "materialized": "false",
        "requires_explicit_migration": physical,
        "rollback_source": str(alias) if physical else None,
        "apply_supported": not physical,
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


def recover_clean(*, root: Path, alias: Path) -> dict[str, Any]:
    """Rollback the active owner journal. Never infer ownership from directory age."""
    root = root.expanduser().resolve(strict=True)
    alias = _contained(root, alias)
    lock = _lock(alias)
    if lock.is_symlink():
        raise ValueError("Publication lock must be physical")
    journal = lock / "journal.json"
    state = json.loads(journal.read_text(encoding="utf-8"))
    if state["root"] != str(root) or state["alias"] != str(alias):
        raise ValueError("Journal identity does not match publication")
    if state["status"] == "committed":
        return state
    if state["status"] == "rolled_back":
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


def publish_clean(
    *, root: Path, alias: Path, target: Path, metadata: Iterable[Path], commit: Callable[[], object]
) -> dict[str, Any]:
    """Explicit opt-in API. Callback writes declared JSON and checks health.

    Only this clean alias and declared metadata are recovered. The callback must
    not modify raw aliases, universe files, providers or any undeclared resource.
    Journals remain for inspection; acknowledge them before another publication.
    """
    if os.name != "posix":
        raise ValueError("Clean linked publication is currently POSIX-only")
    root, alias, target = _paths(root, alias, target)
    plan = plan_clean_migration(root=root, alias=alias, target=target)
    if plan["requires_explicit_migration"]:
        raise ValueError("Physical latest requires explicit coordinated migration")
    snapshots: list[dict[str, Any]] = []
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
        snapshots.append({"path": str(path), "content": None})
    lock = _lock(alias)
    lock.mkdir()  # Exclusive; stale locks require explicit recovery, never deletion.
    journal = lock / "journal.json"
    for item in snapshots:
        path = Path(item["path"])
        item["content"] = base64.b64encode(path.read_bytes()).decode() if path.exists() else None
    state = {
        "root": str(root),
        "alias": str(alias),
        "status": "prepared",
        "old_link": str(alias.readlink()) if alias.is_symlink() else None,
        "new_link": os.path.relpath(target, alias.parent),
        "metadata": snapshots,
    }
    _save(journal, state)
    try:
        _link(alias, state["new_link"])
        commit()
        state["status"] = "committed"
        _save(journal, state)
    except Exception:
        recover_clean(root=root, alias=alias)
        raise
    return {**plan, "journal": str(journal), "status": "committed"}


def acknowledge_clean(*, root: Path, alias: Path) -> Path:
    """Archive a terminal journal, preserving backups before allowing the next run."""
    root = root.expanduser().resolve(strict=True)
    alias = _contained(root, alias)
    lock = _lock(alias)
    if lock.is_symlink():
        raise ValueError("Publication lock must be physical")
    state = json.loads((lock / "journal.json").read_text(encoding="utf-8"))
    if state["root"] != str(root) or state["alias"] != str(alias):
        raise ValueError("Journal identity does not match publication")
    if state["status"] not in {"committed", "rolled_back"}:
        raise ValueError("Interrupted publication must recover before acknowledgement")
    archive = lock.with_name(f"{lock.name}.archive.{uuid4().hex}")
    lock.rename(archive)
    return archive


def main(argv: list[str] | None = None) -> int:
    """Print a migration plan without modifying aliases, metadata or journals."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--alias", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            plan_clean_migration(
                root=args.root,
                alias=args.alias,
                target=args.target,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
