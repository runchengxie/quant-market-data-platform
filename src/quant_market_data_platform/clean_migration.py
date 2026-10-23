"""Full-byte physical clean alias migration and retained-directory recovery."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CleanMigrationTarget:
    path: Path
    source_sha256: str


def tree_inventory(directory: Path) -> dict[str, Any]:
    """Hash every regular file in bounded chunks, retaining relative inventory."""
    entries = sorted(directory.rglob("*"))
    records = []
    for path in entries:
        if path.is_symlink():
            raise ValueError("Migration inventory refuses symlinks")
        relative = path.relative_to(directory).as_posix()
        if path.is_dir():
            records.append({"path": relative, "kind": "directory"})
            continue
        if not path.is_file():
            raise ValueError("Migration inventory requires regular files")
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("Migration input changed while hashing")
        records.append(
            {"path": relative, "kind": "file", "size": after.st_size, "sha256": digest.hexdigest()}
        )
    if entries != sorted(directory.rglob("*")):
        raise ValueError("Migration inventory changed while hashing")
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "entries": records}


def physical_plan(alias: Path, target: Path) -> dict[str, Any]:
    source = tree_inventory(alias)
    destination = tree_inventory(target)
    return {
        "source_sha256": source["sha256"],
        "target_sha256": destination["sha256"],
        "equivalent": source["entries"] == destination["entries"],
        "inventory": source["entries"],
    }


def restore_physical(
    *,
    alias: Path,
    backup: Path,
    expected_sha256: str,
    new_link: str,
    restore_metadata: Callable[[], None],
) -> None:
    """Restore only the original retained directory, including interrupted rollback."""
    if backup.parent != alias.parent or not backup.name.startswith(
        f".{alias.name}.physical-rollback."
    ):
        raise ValueError("Physical rollback backup identity mismatch")
    if backup.is_symlink():
        raise ValueError("Physical rollback backup must be a physical directory")
    if backup.exists():
        if not backup.is_dir() or tree_inventory(backup)["sha256"] != expected_sha256:
            raise ValueError("Retained physical rollback directory changed")
        if alias.is_symlink():
            if str(alias.readlink()) != new_link:
                raise ValueError("Alias changed outside this migration")
        elif alias.exists():
            raise ValueError("Refusing recovery over another physical alias")
        restore_metadata()
        alias.unlink(missing_ok=True)
        backup.rename(alias)
    else:
        if alias.is_symlink() or not alias.is_dir():
            raise ValueError("Physical rollback backup is missing")
        if tree_inventory(alias)["sha256"] != expected_sha256:
            raise ValueError("Restored physical alias changed")
        restore_metadata()
