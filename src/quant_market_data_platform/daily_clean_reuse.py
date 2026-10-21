"""Verified immutable snapshot shard reuse; mutable raw inputs are never linked."""

from __future__ import annotations

import errno
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .daily_clean_snapshot_io import hash_file, stamp

DATASETS = ("daily", "adj_factor", "daily_basic", "limit_status")
NAME = re.compile(r"a_share_all_(\d{8})_(\d{8})\.(\d{8}T\d{6})\.(\d+)")


def _real(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts or path != path.resolve(strict=True):
        raise ValueError(f"reuse path must be canonical without symlinks: {path}")


def select_latest(inputs: Path, output: Path) -> Path | None:
    """Skip archive aliases and retired/unreceipted attempts without following links."""
    candidates = []
    for path in inputs.glob("*"):
        if path == output or not NAME.fullmatch(path.name) or path.is_symlink():
            continue
        _real(path)
        marker = path / "retirement_receipt.json"
        if marker.exists() or marker.is_symlink():
            continue
        receipt = path / "snapshot_receipt.json"
        if not receipt.exists() and not receipt.is_symlink():
            continue
        _real(receipt)
        payload = json.loads(receipt.read_text())
        if not isinstance(payload, dict):
            raise ValueError(f"invalid candidate receipt: {receipt}")
        if payload.get("status") == "completed":
            match = NAME.fullmatch(path.name)
            assert match is not None
            candidates.append(((match[3], int(match[4]), path.name), path))
        else:
            raise ValueError(f"invalid candidate receipt status: {receipt}")
    return max(candidates)[1] if candidates else None


def _raise_walk_error(error: OSError) -> None:
    raise error


def _raw_inventory(roots: tuple[Path, ...]) -> dict[Path, tuple[int, int, int, int, int]]:
    """Inspect every Parquet inode in the four raw roots, without following links."""
    files = {}
    for root in roots:
        _real(root)
        for directory, folders, names in os.walk(
            root, followlinks=False, onerror=_raise_walk_error
        ):
            parent = Path(directory)
            for name in folders:
                _real(parent / name)
            for name in names:
                path = parent / name
                _real(path)
                if path.suffix == ".parquet":
                    if not path.is_file():
                        raise ValueError(f"raw shard must be a regular file: {path}")
                    files[path] = stamp(path)
    return files


class SnapshotReuse:
    """Track donor identity across deliberate link-count/ctime changes."""

    def __init__(self, inputs: Path, donor: Path):
        _real(donor)
        match = NAME.fullmatch(donor.name)
        if donor.parent != inputs or not match:
            raise ValueError("reuse donor must be a dated direct input snapshot")
        for day in (match[1], match[2]):
            datetime.strptime(day, "%Y%m%d")
        datetime.strptime(match[3], "%Y%m%dT%H%M%S")
        if (donor / "retirement_receipt.json").exists() or (
            donor / "retirement_receipt.json"
        ).is_symlink():
            raise ValueError("retired snapshot cannot be reused")
        self.donor = donor
        self.raw_roots: tuple[Path, ...] = ()
        self.raw_files: dict[Path, tuple[int, int, int, int, int]] = {}
        self.records: dict[Path, dict[str, Any]] = {}
        self.stamps: dict[Path, tuple[int, int, int, int, int]] = {}
        self.hashes: dict[Path, str] = {}
        metadata = [
            donor / "snapshot_receipt.json",
            *(donor / d / "manifest.yml" for d in DATASETS),
        ]
        receipt = self._load_metadata(metadata)
        if (
            receipt.get("schema_version") != "a_share.daily_clean_input_snapshot.v1"
            or receipt.get("status") != "completed"
            or set(receipt.get("datasets", {})) != set(DATASETS)
            or receipt.get("start_date") != match[1]
            or receipt.get("end_date") != match[2]
        ):
            raise ValueError("completed owner receipt with four datasets required for reuse")
        for dataset in DATASETS:
            for record in receipt["datasets"][dataset]["files"]:
                relative = Path(record["path"])
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or relative.parts[0] != "data"
                    or relative.suffix != ".parquet"
                ):
                    raise ValueError("invalid donor receipt path")
                path = donor / dataset / relative
                if path in self.records:
                    raise ValueError("duplicate donor receipt path")
                self._remember(path)
                if (
                    path.stat().st_mode & 0o222
                    or path.stat().st_size != record["bytes"]
                    or self.hashes[path] != record["sha256"]
                ):
                    raise ValueError(
                        f"donor must be read-only with matching receipt hash/size: {path}"
                    )
                self.records[path] = record
        if not self.records:
            raise ValueError("empty donor inventory")
        self.inodes: dict[tuple[int, int], list[Path]] = {}
        for path, value in self.stamps.items():
            self.inodes.setdefault(value[:2], []).append(path)
        self.expected = set(self.records) | set(metadata)
        self.verify()

    def bind_raw_sources(self, roots: tuple[Path, ...]) -> None:
        self.raw_roots = roots
        self.raw_files = _raw_inventory(roots)
        self._check_raw_inodes(self.raw_files)

    def _check_raw_inodes(self, files: dict[Path, tuple[int, int, int, int, int]]) -> None:
        raw_inodes = {value[:2] for value in files.values()}
        if any(self.stamps[path][:2] in raw_inodes for path in self.records):
            raise ValueError("donor shares inode with mutable raw source inventory")

    def _load_metadata(self, metadata: list[Path]) -> dict[str, Any]:
        for path in metadata:
            self._remember(path)
        for manifest in metadata[1:]:
            payload = yaml.safe_load(manifest.read_text())
            if not isinstance(payload, dict) or payload.get("status") != "completed":
                raise ValueError(f"completed donor dataset manifest required: {manifest}")
        return json.loads((self.donor / "snapshot_receipt.json").read_text())

    def _remember(self, path: Path) -> None:
        _real(path)
        if not path.is_file():
            raise ValueError(f"donor entry must be regular: {path}")
        before = stamp(path)
        self.hashes[path] = hash_file(path)
        if stamp(path) != before:
            raise ValueError("donor changed while hashing")
        self.stamps[path] = before

    def verify(self) -> None:
        if self.raw_roots:
            current_raw = _raw_inventory(self.raw_roots)
            self._check_raw_inodes(current_raw)
            if current_raw != self.raw_files:
                raise ValueError("complete raw inventory changed during snapshot reuse")
        actual = set()
        for path in self.donor.rglob("*"):
            _real(path)
            if path.is_file():
                actual.add(path)
            elif not path.is_dir():
                raise ValueError("nonregular donor entry")
        if (
            actual != self.expected
            or any(stamp(p) != value for p, value in self.stamps.items())
            or any(path.stat().st_mode & 0o222 for path in self.records)
        ):
            raise ValueError("donor inventory or metadata changed")

    def link(self, original: Path, relative: Path, captured: Path) -> bool:
        donor = self.donor / relative
        record = self.records.get(donor)
        if (
            record is None
            or original.stat().st_size != record["bytes"]
            or hash_file(original) != record["sha256"]
        ):
            return False
        if stamp(donor)[:2] == stamp(original)[:2]:
            raise ValueError("donor shares inode with mutable raw source")
        if stamp(donor) != self.stamps[donor]:
            raise ValueError("donor changed before link")
        try:
            os.link(donor, captured, follow_symlinks=False)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                return False
            raise
        after = stamp(donor)
        if (
            after[:4] != self.stamps[donor][:4]
            or captured.stat().st_mode & 0o222
            or hash_file(captured) != record["sha256"]
        ):
            raise ValueError("donor changed while linking")
        # link(2) changes ctime on every name for this inode, including any
        # existing hardlinked shards in the selected snapshot.
        for path in self.inodes[after[:2]]:
            self.stamps[path] = after
        return True
