"""Owned path and frozen-source guards used before writable operations."""

from __future__ import annotations

from pathlib import Path

from .archive import MODALITIES, member_path
from .proof import sha_file


def outside_source_tree(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Runtime paths must not be symbolic-link aliases")
    resolved = path.resolve()
    if any(
        (parent / ".git").is_file() or (parent / ".git" / "HEAD").is_file()
        for parent in (resolved, *resolved.parents)
    ):
        raise ValueError("Runtime inputs/output/scratch must be outside source checkouts")


def validate_snapshot(profile) -> None:
    root = Path(profile["source_root"])
    outside_source_tree(root)
    expected = {}
    for member in profile["members"]:
        parts = member_path(member["path"], profile["date"]).parts
        if (
            member["path"] in expected
            or parts[1] != member["symbol"]
            or MODALITIES[parts[2]] != member["modality"]
        ):
            raise ValueError("Invalid or duplicate frozen CSV identity")
        expected[member["path"]] = (member["bytes"], member["sha256"])
    actual = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Frozen CSV source contains a symlink")
        if path.is_file():
            actual[path.relative_to(root).as_posix()] = (path.stat().st_size, sha_file(path))
    if expected != actual:
        raise ValueError("Frozen source membership or CSV bytes changed")
