"""Manifest-backed full scans, routed interval queries and independent verification."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .archive import SYMBOL
from .guards import member_inventory
from .layout import VERSION, bucket, file_path, lexical, safe_file
from .profile import check_date, clock, identity_summary, record_identity
from .proof import load, logical_hash, sha_file


def catalog(root: Path):
    manifest = load(root / "manifest.json")
    if manifest.get("status") != "verified" or manifest["config"]["version"] != VERSION:
        raise ValueError("Dataset has no verified supported manifest")
    if sha_file(root / "profile.json") != manifest["profile_sha256"]:
        raise ValueError("Profile integrity check failed")
    paths = [record["path"] for record in manifest["files"]]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate output catalog entry")
    for relative in paths:
        safe_file(root, relative)
    profile = load(root / "profile.json")
    member_inventory(profile)
    if (
        manifest.get("identity_summary") != identity_summary(profile)
        or manifest["config"].get("identity_policy") != profile["identity_policy"]
    ):
        raise ValueError("Catalog identity evidence differs from source profile")
    return manifest, profile


def scan(root: Path, modality: str, symbols=None, start=None, stop=None):
    root = Path(root)
    manifest, profile = catalog(root)
    if modality not in profile["modalities"]:
        raise ValueError("Unknown modality")
    if symbols is not None and (not symbols or any(not SYMBOL.fullmatch(s) for s in symbols)):
        raise ValueError("Provide a nonempty list of valid security identifiers")
    if start is not None and stop is not None and start >= stop:
        raise ValueError("Interval start must be before exclusive stop")
    buckets = (
        None
        if symbols is None
        else {
            bucket(m["symbol"], manifest["config"]["buckets"])
            for m in profile["members"]
            if m["modality"] == modality and set(m["record_symbols"]).intersection(symbols)
        }
    )
    files = [
        safe_file(root, f["path"])
        for f in manifest["files"]
        if f["modality"] == modality and (buckets is None or f["bucket"] in buckets)
    ]
    info = profile["modalities"][modality]
    payload_schema = pa.schema(
        [(n, pa.int64() if n in info["integer_columns"] else pa.string()) for n in info["columns"]]
        + [("_time", pa.int64()), ("_source_row", pa.int64()), ("_source_symbol", pa.string())]
    )
    dataset = ds.dataset(files, format="parquet", schema=payload_schema)
    predicate = ds.scalar(True)
    if symbols is not None:
        predicate = predicate & ds.field("万得代码").isin(symbols)
    if start is not None:
        predicate = predicate & (ds.field("_time") >= start)
    if stop is not None:
        predicate = predicate & (ds.field("_time") < stop)
    options = ds.ParquetFragmentScanOptions(page_checksum_verification=True)
    return dataset.scanner(
        filter=predicate, batch_size=65536, use_threads=False, fragment_scan_options=options
    )


def verify_bucket(path: Path, members: list, info) -> dict:
    dataset = ds.dataset(path, format="parquet")
    expected_rows = sum(m["rows"] for m in members)
    if pq.ParquetFile(path).metadata.num_rows != expected_rows:
        raise ValueError("Output row count differs from frozen source members")
    for member in members:
        restored = dataset.to_table(
            filter=ds.field("_source_symbol") == member["symbol"], use_threads=False
        )
        raw = lexical(restored, info)
        check_date(raw, member["path"].split("/", 1)[0])
        identity = record_identity(raw, member["symbol"], "preserve-source-labels")
        if any(identity[key] != member[key] for key in identity):
            raise ValueError("Output record identity differs from source evidence")
        ordinal = restored.sort_by([("_source_row", "ascending")])["_source_row"].to_numpy()
        if (
            raw.num_rows != member["rows"]
            or not np.array_equal(ordinal, np.arange(member["rows"]))
            or logical_hash(raw) != member["logical_sha256"]
            or not clock(raw).equals(restored.sort_by([("_source_row", "ascending")])["_time"])
        ):
            raise ValueError(f"All-field member verification failed: {member['path']}")
    return {"bytes": path.stat().st_size, "sha256": sha_file(path), "rows": expected_rows}


def verify(root: Path) -> dict:
    root = Path(root)
    manifest, profile = catalog(root)
    return verify_contents(root, manifest, profile)


def verify_contents(root: Path, manifest, profile) -> dict:
    expected = set()
    for record in manifest["files"]:
        path = safe_file(root, record["path"])
        expected.add(path)
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            raise ValueError("Output file inventory changed")
        if sha_file(path) != record["sha256"]:
            raise ValueError("Output SHA-256 verification failed")
        members = [
            m
            for m in profile["members"]
            if m["modality"] == record["modality"]
            and bucket(m["symbol"], manifest["config"]["buckets"]) == record["bucket"]
        ]
        if record["path"] != file_path(record["modality"], record["bucket"]):
            raise ValueError("Catalog routing differs from file path")
        verify_bucket(path, members, profile["modalities"][record["modality"]])
    actual = set((root / "data").rglob("*.parquet"))
    wanted = {
        file_path(m["modality"], bucket(m["symbol"], manifest["config"]["buckets"]))
        for m in profile["members"]
    }
    if actual != expected or {r["path"] for r in manifest["files"]} != wanted:
        raise ValueError("Catalog does not account for all source members/output files")
    return {
        "status": "verified",
        "rows": sum(m["rows"] for m in profile["members"]),
        "members": len(profile["members"]),
        "files": len(expected),
        "source_label_conflicts": identity_summary(profile)["conflicting_members"],
        "mixed_record_label_members": identity_summary(profile)["mixed_record_label_members"],
    }
