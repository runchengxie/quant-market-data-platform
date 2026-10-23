"""Seal explicit minute snapshot inputs; verify and restore without source access.

Seals bind bytes and relative names, not authenticity, physical media independence,
writer quiescence, coverage validity or authority to publish a rollback contract.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "minute.snapshot.v1"


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if path.parts and path.parts[0] in {"LOCK", "STATE.json", "RESTORED.json"}:
        raise ValueError("reserved restore control path")
    if (
        not value
        or path.is_absolute()
        or any(p in ("", ".", "..") for p in value.split("/"))
        or "\\" in value
    ):
        raise ValueError("unsafe relative path")
    return str(path)


def _no_links(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("linked path is forbidden")


def _absolute(path: Path) -> Path:
    _no_links(path.absolute())
    return path.resolve(strict=False)


def _stamp(path: Path) -> tuple[int, ...]:
    _no_links(path)
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("expected regular file")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _hash(path: Path) -> str:
    before = _stamp(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if (os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino) != before[:2]:
            raise ValueError("file changed before reading")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if _stamp(path) != before:
        raise ValueError("file changed while reading")
    return digest.hexdigest()


def _scan(root: Path) -> list[Path]:
    _no_links(root)
    if root.is_file():
        _stamp(root)
        return [root]
    if not root.is_dir():
        raise ValueError("missing input directory")
    files = []
    for parent, directories, names in os.walk(root, followlinks=False):
        if not directories and not names:
            raise ValueError("empty directories are not supported")
        for name in directories:
            child = Path(parent) / name
            _no_links(child)
            if not child.is_dir():
                raise ValueError("non-directory entry")
        for name in names:
            child = Path(parent) / name
            _stamp(child)
            files.append(child)
    return sorted(files)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(_json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)


@contextmanager
def _lock(root: Path, *, shared: bool = False):
    _no_links(root / "LOCK")
    with (root / "LOCK").open("rb" if shared else "xb") as stream:
        fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        yield


def _copy_file(source: Path, target: Path) -> None:
    before = _stamp(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, target.open("xb") as writer:
        opened = os.fstat(reader.fileno())
        if (opened.st_dev, opened.st_ino) != before[:2]:
            raise ValueError("source replaced before copy")
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            writer.write(block)
        writer.flush()
        os.fsync(writer.fileno())
    if _stamp(source) != before or _hash(source) != _hash(target):
        raise ValueError("unstable source copy")
    if (
        target.stat().st_nlink != 1
        or target.stat().st_ino == before[1]
        and target.stat().st_dev == before[0]
    ):
        raise ValueError("copy must have independent inode")


def _inputs(
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Path], dict[str, tuple[int, ...]]]:
    version = plan.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("version is required")
    records, sources, stamps = [], {}, {}
    for entry in plan["entries"]:
        prefix = _relative(entry["path"])
        source = _absolute(Path(entry["source"]))
        role = entry["role"]
        if not isinstance(role, str) or not role:
            raise ValueError("role is required")
        for item in _scan(source):
            relative = (
                prefix
                if source.is_file()
                else _relative(prefix + "/" + item.relative_to(source).as_posix())
            )
            if any(
                relative == other
                or relative.startswith(other + "/")
                or other.startswith(relative + "/")
                for other in sources
            ):
                raise ValueError("overlapping restore paths")
            sources[relative] = item
            stamps[relative] = _stamp(item)
            records.append(
                {
                    "path": relative,
                    "role": role,
                    "version": version,
                    "bytes": stamps[relative][2],
                    "sha256": _hash(item),
                }
            )
    if not records:
        raise ValueError("empty snapshot")
    return (
        {"schema": SCHEMA, "version": version, "files": sorted(records, key=lambda r: r["path"])},
        sources,
        stamps,
    )


def _new_root(root: Path) -> None:
    _no_links(root)
    root.mkdir(parents=False, exist_ok=False)
    _fsync_dir(root.parent)


def _sync_tree(root: Path) -> None:
    for parent, _, _ in os.walk(root, topdown=False):
        _fsync_dir(Path(parent))


def archive_snapshot(plan: dict[str, Any], destination: Path) -> dict[str, Any]:
    manifest, sources, stamps = _inputs(plan)
    destination = _absolute(destination)
    for entry in plan["entries"]:
        source = _absolute(Path(entry["source"]))
        if source == destination or destination in source.parents or source in destination.parents:
            raise ValueError("source and archive overlap")
    _new_root(destination)
    with _lock(destination):
        try:
            _write_json(destination / "STATE.json", {"status": "copying"})
            for relative, source in sources.items():
                _copy_file(source, destination / "payload" / relative)
            latest, _, latest_stamps = _inputs(plan)
            if latest != manifest or latest_stamps != stamps:
                raise ValueError("snapshot input set changed")
            _write_json(destination / "MANIFEST.json", manifest)
            _verify_payload(destination / "payload", manifest)
            _write_json(destination / "STATE.json", {"status": "complete"})
            _sync_tree(destination)
            _write_json(
                destination / "SEALED.json",
                {"schema": SCHEMA, "manifest_sha256": _hash(destination / "MANIFEST.json")},
            )
        except BaseException:
            _write_json(destination / "STATE.json", {"status": "failed"})
            raise
    return manifest


def _verify_payload(root: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != SCHEMA or not manifest.get("version"):
        raise ValueError("unsupported snapshot schema")
    expected = {}
    for entry in manifest["files"]:
        relative = _relative(entry["path"])
        if relative in expected or entry["version"] != manifest["version"] or not entry["role"]:
            raise ValueError("invalid manifest entry")
        expected[relative] = entry
    actual = {p.relative_to(root).as_posix(): p for p in _scan(root)}
    if not expected or actual.keys() != expected.keys():
        raise ValueError("snapshot file set differs")
    for relative, path in actual.items():
        record = expected[relative]
        if path.stat().st_nlink != 1:
            raise ValueError("archive payload has hardlinks")
        if path.stat().st_size != record["bytes"] or _hash(path) != record["sha256"]:
            raise ValueError("snapshot byte hash differs")


def _verified_manifest(archive: Path) -> dict[str, Any]:
    _no_links(archive)
    actual = {p.relative_to(archive).as_posix() for p in _scan(archive)}
    metadata = {"LOCK", "STATE.json", "MANIFEST.json", "SEALED.json"}
    if {p for p in actual if not p.startswith("payload/")} != metadata:
        raise ValueError("archive metadata set differs or snapshot unsealed")
    seal = json.loads((archive / "SEALED.json").read_bytes())
    if seal.get("schema") != SCHEMA or seal.get("manifest_sha256") != _hash(
        archive / "MANIFEST.json"
    ):
        raise ValueError("invalid seal")
    if json.loads((archive / "STATE.json").read_bytes()) != {"status": "complete"}:
        raise ValueError("incomplete snapshot")
    manifest = json.loads((archive / "MANIFEST.json").read_bytes())
    _verify_payload(archive / "payload", manifest)
    return manifest


def verify_snapshot(archive: Path) -> dict[str, Any]:
    with _lock(archive, shared=True):
        return _verified_manifest(archive)


def restore_snapshot(archive: Path, destination: Path) -> dict[str, Any]:
    archive, destination = _absolute(archive), _absolute(destination)
    if archive == destination or archive in destination.parents or destination in archive.parents:
        raise ValueError("archive and restore overlap")
    with _lock(archive, shared=True):
        manifest = _verified_manifest(archive)
        _new_root(destination)
        with _lock(destination):
            try:
                _write_json(destination / "STATE.json", {"status": "restoring"})
                for record in manifest["files"]:
                    _copy_file(archive / "payload" / record["path"], destination / record["path"])
                actual = {p.relative_to(destination).as_posix() for p in _scan(destination)}
                if actual != {r["path"] for r in manifest["files"]} | {"LOCK", "STATE.json"}:
                    raise ValueError("restore file set differs")
                for record in manifest["files"]:
                    if _hash(destination / record["path"]) != record["sha256"]:
                        raise ValueError("restored byte hash differs")
                _verified_manifest(archive)
                _write_json(destination / "STATE.json", {"status": "complete"})
                _sync_tree(destination)
                _write_json(
                    destination / "RESTORED.json",
                    {"schema": SCHEMA, "manifest_sha256": _hash(archive / "MANIFEST.json")},
                )
            except BaseException:
                _write_json(destination / "STATE.json", {"status": "failed"})
                raise
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("archive", "verify", "restore"))
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.action == "archive":
        if args.plan is None:
            parser.error("archive requires --plan")
        result = archive_snapshot(json.loads(args.plan.read_bytes()), args.archive)
    elif args.action == "restore":
        if args.destination is None:
            parser.error("restore requires --destination")
        result = restore_snapshot(args.archive, args.destination)
    else:
        result = verify_snapshot(args.archive)
    print(
        json.dumps(
            {
                "schema": result["schema"],
                "version": result["version"],
                "files": len(result["files"]),
            }
        )
    )


if __name__ == "__main__":
    main()
