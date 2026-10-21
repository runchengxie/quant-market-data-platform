"""Retire only reviewed duplicate shards, preserving reconstructable provenance."""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

DATASETS = ("daily", "adj_factor", "daily_basic", "limit_status")
MARKER = "retirement_receipt.json"
SCHEMA = "a_share.daily_clean_input_retirement.v1"


def _real(path: Path) -> Path:
    path = path.absolute()
    if path != path.resolve(strict=True):
        raise ValueError(f"path contains symlink indirection: {path}")
    return path


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _check_metadata(path: Path) -> None:
    for metadata in [
        path / "snapshot_receipt.json",
        *(path / d / "manifest.yml" for d in DATASETS),
    ]:
        _real(metadata)
        if not metadata.is_file():
            raise ValueError("snapshot metadata must be regular files")


def _snapshot(path: Path, inputs: Path) -> dict[Path, dict[str, Any]]:
    _real(path)
    if path.parent != inputs or not re.fullmatch(
        r"a_share_all_\d{8}_\d{8}\.\d{8}T\d{6}\.\d+", path.name
    ):
        raise ValueError(f"not a dated direct input snapshot: {path}")
    dates = path.name.split(".")
    for day in dates[0].split("_")[-2:]:
        datetime.strptime(day, "%Y%m%d")
    datetime.strptime(dates[1], "%Y%m%dT%H%M%S")
    if (path / MARKER).exists() or (path / MARKER).is_symlink():
        raise ValueError(f"snapshot already retired or retirement attempted: {path}")
    _check_metadata(path)
    receipt = json.loads((path / "snapshot_receipt.json").read_text())
    if (
        receipt.get("schema_version") != "a_share.daily_clean_input_snapshot.v1"
        or receipt.get("status") != "completed"
        or set(receipt.get("datasets", {})) != set(DATASETS)
    ):
        raise ValueError("completed owner snapshot receipt required")
    records = {}
    for dataset in DATASETS:
        for row in receipt["datasets"][dataset]["files"]:
            relative = Path(row["path"])
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.parts[0] != "data"
                or relative.suffix != ".parquet"
            ):
                raise ValueError("invalid receipt shard path")
            shard = path / dataset / relative
            if shard in records:
                raise ValueError("duplicate receipt shard")
            records[shard] = row
    expected = (
        set(records)
        | {path / "snapshot_receipt.json"}
        | {path / dataset / "manifest.yml" for dataset in DATASETS}
    )
    actual = set()
    for entry in path.rglob("*"):
        _real(entry)
        if entry.is_file():
            actual.add(entry)
        elif not entry.is_dir():
            raise ValueError("nonregular snapshot entry")
    if actual != expected or not records:
        raise ValueError("snapshot inventory does not match completed receipt")
    return records


def _restore_dependencies(inputs: Path, target: Path) -> None:
    for marker in inputs.glob("*/retirement_receipt.json"):
        _real(marker)
        payload = json.loads(marker.read_text())
        if payload.get("schema_version") != SCHEMA or not isinstance(payload.get("files"), list):
            raise ValueError(f"invalid prior retirement marker: {marker}")
        for row in payload["files"]:
            peer = Path(row["peer"])
            if not peer.is_absolute() or ".." in peer.parts or not peer.is_relative_to(inputs):
                raise ValueError(f"noncanonical prior restore dependency: {peer}")
            _real(peer)
            if not peer.is_file():
                raise ValueError(f"prior restore dependency is not a regular file: {peer}")
            if peer.is_relative_to(target):
                raise ValueError(f"target is a restore donor for prior retirement: {marker}")


def _references(root: Path, target: Path) -> None:
    # Local publication contracts and clean manifests only. External consumers
    # require the operator's separately reviewed reference audit.
    paths = list((root / "metadata/current_assets").glob("*.json"))
    for dataset in ("daily", "daily_clean"):
        paths += list((root / "assets/tushare/a_share" / dataset).rglob("manifest.yml"))
    needles = (str(target), target.relative_to(root).as_posix(), target.name)
    for path in paths:
        text = path.read_text()
        if any(needle in text for needle in needles):
            raise ValueError(f"live publication reference to target: {path}")


def _sync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("x") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    _sync_dir(path.parent)


@contextmanager
def _lock(root: Path, apply: bool):
    if not apply:
        yield
        return
    lock = root / ".locks/a_share_current_publish.lock"
    lock.parent.mkdir(exist_ok=True)
    if lock.is_symlink() or lock.parent.is_symlink():
        raise ValueError("publication lock must not be a symlink")
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _preflight(root: Path, target: Path, peer_map: Path):
    inputs = root / "assets/tushare/a_share/daily_clean_inputs"
    records = _snapshot(target, inputs)
    _restore_dependencies(inputs, target)
    _references(root, target)
    opener = gzip.open if peer_map.suffix == ".gz" else open
    with opener(peer_map, "rt") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list):
        raise ValueError("reviewed peer map must be a list")
    selected = [row for row in rows if Path(row["path"]).is_relative_to(target)]
    if len(selected) != len(records) or {Path(row["path"]) for row in selected} != set(records):
        raise ValueError("reviewed peer map must cover each target shard exactly once")
    retired_targets = {inputs / Path(row["path"]).relative_to(inputs).parts[0] for row in rows}
    donors = {}
    stamps = {}
    metadata = [
        target / "snapshot_receipt.json",
        *(target / dataset / "manifest.yml" for dataset in DATASETS),
    ]
    for path in metadata:
        stamps[path] = _stamp(path)
    for row in selected:
        path, peer = Path(row["path"]), Path(row["peer"])
        _real(peer)
        relative = path.relative_to(target)
        peer_root = inputs / peer.relative_to(inputs).parts[0]
        if peer_root in retired_targets or peer.relative_to(peer_root) != relative:
            raise ValueError("donor must be a retained distinct matching snapshot shard")
        if peer_root not in donors:
            donors[peer_root] = _snapshot(peer_root, inputs)
            for meta in [
                peer_root / "snapshot_receipt.json",
                *(peer_root / dataset / "manifest.yml" for dataset in DATASETS),
            ]:
                stamps[meta] = _stamp(meta)
        if peer not in donors[peer_root]:
            raise ValueError("donor absent from receipt")
        for file, record in ((path, records[path]), (peer, donors[peer_root][peer])):
            before = _stamp(file)
            if (
                not file.is_file()
                or file.stat().st_size != row["bytes"]
                or record["bytes"] != row["bytes"]
                or record["sha256"] != row["sha256"]
                or _hash(file) != row["sha256"]
                or before != _stamp(file)
            ):
                raise ValueError(f"shard hash/size mismatch or changed: {file}")
            stamps[file] = before
    if any(_stamp(path) != stamp for path, stamp in stamps.items()):
        raise ValueError("snapshot or donor changed during preflight")
    return selected, stamps, metadata


def _record_failure(
    archive: Path, target: Path, result: dict[str, Any], exc: BaseException
) -> None:
    result["status"] = "failed"
    result["error"] = str(exc)
    _write(archive / MARKER, result)
    try:
        _write(target / MARKER, result)
    except OSError:
        pass  # Durable archive receipt still records the failure.


def retire_snapshot(  # noqa: PLR0913 - explicit destructive-operation acknowledgements
    root: Path,
    target: Path,
    peer_map: Path,
    archive: Path,
    *,
    apply: bool = False,
    writers_stopped: bool = False,
) -> dict[str, Any]:
    """Full-read preflight, then unlink exactly the reviewed duplicate shards."""
    root = _real(root)
    target = target.absolute()
    inputs = root / "assets/tushare/a_share/daily_clean_inputs"
    archive = archive.absolute()
    if apply and not writers_stopped:
        raise ValueError("apply requires explicit writers-stopped acknowledgement")
    if (
        archive.exists()
        or archive != archive.resolve()
        or archive.is_relative_to(inputs)
        or target.is_relative_to(archive)
    ):
        raise ValueError("archive must be new and outside all input snapshots")
    with _lock(root, apply):
        _real(peer_map)
        map_stamp = _stamp(peer_map)
        selected, stamps, metadata = _preflight(root, target, peer_map)
        result = {
            "schema_version": SCHEMA,
            "status": "dry_run",
            "snapshot": str(target),
            "peer_map": str(peer_map),
            "peer_map_sha256": _hash(peer_map),
            "files": selected,
            "removed": 0,
            "metadata_sha256": {str(path.relative_to(target)): _hash(path) for path in metadata},
            "restore_recipe": (
                "Copy each files[].peer to files[].path; verify bytes and SHA256; "
                "preserve original manifests and snapshot_receipt.json; "
                "only then remove retirement marker."
            ),
        }
        if _stamp(peer_map) != map_stamp:
            raise ValueError("reviewed peer map changed during preflight")
        if not apply:
            return result
        archive.mkdir(parents=True, exist_ok=False)
        for path in metadata:
            destination = archive / "metadata" / path.relative_to(target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            with destination.open("rb") as handle:
                os.fsync(handle.fileno())
            if _hash(destination) != result["metadata_sha256"][path.relative_to(target).as_posix()]:
                raise ValueError("archived metadata verification failed")
            _sync_dir(destination.parent)
        _sync_dir(archive / "metadata")
        _sync_dir(archive)
        _sync_dir(archive.parent)
        result["status"] = "prepared"
        _write(archive / MARKER, result)
        _write(target / MARKER, result)
        try:
            _references(root, target)
            if any(_stamp(path) != stamp for path, stamp in stamps.items()):
                raise ValueError("snapshot changed before mutation")
            for row in selected:
                Path(row["path"]).unlink()
                _sync_dir(Path(row["path"]).parent)
                result["removed"] += 1
                result["status"] = "removing"
                if result["removed"] % 128 == 0:
                    _write(
                        archive / "progress.json",
                        {
                            "removed": result["removed"],
                            "last_path": row["path"],
                            "status": "removing",
                        },
                    )
            result["status"] = "completed"
            _write(target / MARKER, result)
            _write(archive / MARKER, result)
        except BaseException as exc:
            _record_failure(archive, target, result, exc)
            raise
        return result
