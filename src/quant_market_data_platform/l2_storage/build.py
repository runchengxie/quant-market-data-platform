"""Guarded, recoverable complete-day builds; never delete original archives."""

from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

import pyarrow.parquet as pq
from filelock import FileLock

from .archive import extract
from .guards import outside_source_tree, validate_snapshot
from .layout import VERSION, bucket, digest, file_path, safe_file, typed, writer_options
from .profile import IDENTITY_POLICIES, identity_summary, profile_directory, read_csv
from .proof import load, save, sha_file
from .reader import verify, verify_bucket, verify_contents


def source_table(profile, member):
    root = Path(profile["source_root"])
    source = root / member["path"]
    if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
        raise ValueError("CSV source escapes frozen root")
    if sha_file(source) != member["sha256"]:
        raise ValueError("Frozen source CSV changed")
    return typed(read_csv(source), profile["modalities"][member["modality"]], member["symbol"])


def write_bucket(profile, members, path: Path, config):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.with_suffix(".partial").is_symlink():
        raise ValueError("Refusing linked partial Parquet path")
    partial = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    with partial.open("xb") as handle:
        writer = None
        try:
            for member in members:
                table = source_table(profile, member)
                table = table.sort_by([("_time", "ascending"), ("_source_row", "ascending")])
                if writer is None:
                    writer = pq.ParquetWriter(handle, table.schema, **writer_options(table, config))
                writer.write_table(table, row_group_size=config["row_group_rows"])
        finally:
            if writer is not None:
                writer.close()
        handle.flush()
        os.fsync(handle.fileno())
    verify_bucket(partial, members, profile["modalities"][members[0]["modality"]])
    os.link(partial, path)  # Atomic no-replace publication; never overwrite another file.
    partial.unlink()
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build_csv(profile, root: Path, archive_sha256: str, *, options=None) -> dict:
    settings = {"buckets": 64, "row_group_rows": 65536, "zstd_level": 6}
    if options:
        if set(options) - set(settings):
            raise ValueError("Unknown storage configuration key")
        settings.update(options)
    buckets, row_group_rows, zstd_level = (settings[k] for k in settings)
    root = Path(root)
    outside_source_tree(root)
    validate_snapshot(profile)
    source = Path(profile["source_root"]).resolve()
    if root.resolve().is_relative_to(source) or source.is_relative_to(root.resolve()):
        raise ValueError("Output must be separate from source CSV root")
    if root.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}", archive_sha256):
        raise ValueError("Expected a real output directory and lowercase archive SHA-256")
    if not 1 <= buckets <= 9999 or row_group_rows < 1 or not 1 <= zstd_level <= 22:
        raise ValueError("Invalid bucket/row-group/compression configuration")
    config = {
        "version": VERSION,
        "date": profile["date"],
        "archive_sha256": archive_sha256,
        "buckets": buckets,
        "row_group_rows": row_group_rows,
        "zstd_level": zstd_level,
        "profile_digest": digest(profile),
        "identity_policy": profile["identity_policy"],
    }
    root.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root.parent / ("." + root.name + ".lock")), timeout=0):
        return build_owned(profile, root, config)


def build_owned(profile, root: Path, config):
    if root.exists():
        if not (root / "job.json").is_file() or load(root / "job.json") != config:
            raise ValueError("Unknown output directory or changed source/configuration")
        if load(root / "profile.json") != profile:
            raise ValueError("Persisted output profile changed before resume")
        if (root / "manifest.json").exists():
            verify(root)
            return load(root / "manifest.json")
    else:
        root.mkdir()
        save(root / "job.json", config)
        save(root / "profile.json", profile)
    needed = sum(m["bytes"] for m in profile["members"]) * 2 + (20 << 30)
    if shutil.disk_usage(root).free < needed:
        raise ValueError("Insufficient output space including 20 GiB reserve")
    groups = {}
    for member in profile["members"]:
        key = (member["modality"], bucket(member["symbol"], config["buckets"]))
        groups.setdefault(key, []).append(member)
    files = []
    for (kind, number), members in sorted(groups.items()):
        relative = file_path(kind, number)
        path = safe_file(root, relative)
        if not path.exists():
            write_bucket(profile, members, path, config)
        proof = verify_bucket(path, members, profile["modalities"][kind])
        files.append({"path": relative, "modality": kind, "bucket": number, **proof})
        save(root / "progress.json", {"buckets_verified": len(files), "total_buckets": len(groups)})
    manifest = {
        "status": "verified",
        "config": config,
        "profile_sha256": sha_file(root / "profile.json"),
        "files": files,
        "identity_summary": identity_summary(profile),
    }
    verify_contents(root, manifest, profile)
    save(root / "manifest.json", manifest)
    return manifest


def build(
    archive: Path, output: Path, scratch: Path, extractor: str, expected_sha256: str, **options
):
    policy = options.pop("identity_policy", "strict")
    if policy not in IDENTITY_POLICIES:
        raise ValueError("Unknown identity policy")
    for path in (archive, output, scratch):
        outside_source_tree(Path(path))
    archive, output, scratch = (Path(p).resolve() for p in (archive, output, scratch))
    if output.is_relative_to(scratch) or scratch.is_relative_to(output):
        raise ValueError("Output and scratch must be independent roots")
    scratch.mkdir(parents=True, exist_ok=True)
    with FileLock(str(scratch / "intake.lock"), timeout=0):
        source = extract(archive, scratch, extractor, expected_sha256)
        checkpoint = scratch / "profile.json"
        profile = profile_directory(source, archive.stem, identity_policy=policy)
        if checkpoint.exists():
            if load(checkpoint) != profile:
                raise ValueError("Cached profile differs from complete extracted source")
        else:
            save(checkpoint, profile)
        actual = {m["modality"] for m in profile["members"]}
        if actual != {"orders", "trades", "snapshots"}:
            raise ValueError("Complete-day pilot requires all three modalities")
        return build_csv(profile, output, expected_sha256, options=options)
