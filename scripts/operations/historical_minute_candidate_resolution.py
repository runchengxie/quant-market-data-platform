"""Explicit, hash-bound adjudication of retained historical minute candidates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from scripts.operations import historical_minute_coverage_pilot as pilot


def audit_selection(
    row: dict[str, Any], resolution: dict[str, Any], paths: dict[str, Path]
) -> None:
    audit_key = resolution["audit"]
    if audit_key not in row["prior_audit"] or resolution["selected"] != row["partitions"][0]:
        raise ValueError("Selection must match explicit prior audit and primary candidate")
    audit = json.loads(paths[audit_key].read_text())
    matches = [r for r in audit["partitions"] if r["trade_date"] == row["date"]]
    if len(matches) != 1:
        raise ValueError("Require exactly one dated audit record")
    entry = matches[0]
    expected = {
        "path": str(paths[row["partitions"][0]]),
        "sidecar": str(paths[row["sidecar"]]),
        "partition_sha256": pilot.bind(paths[row["partitions"][0]])["sha256"],
        "sidecar_sha256": pilot.bind(paths[row["sidecar"]])["sha256"],
        "binding_matches": True,
        "status": "passed_requested_universe_only",
    }
    if any(entry.get(k) != v for k, v in expected.items()):
        raise ValueError("Prior audit does not authenticate selected partition and sidecar")
    if not entry.get("issues") or any(
        type(v) is not int or v != 0 for v in entry["issues"].values()
    ):
        raise ValueError("Prior audit has unresolved issues")


def resolve(
    row: dict[str, Any], resolution: dict[str, Any], paths: dict[str, Path]
) -> dict[str, Any]:
    audit_selection(row, resolution, paths)
    if len(row["partitions"]) != 2 or len(row["candidate_sidecars"]) != 2:
        raise ValueError("Adjudication supports exactly two explicit candidates")
    parts = [paths[k] for k in row["partitions"]]
    sides = [paths[k] for k in row["candidate_sidecars"]]
    if len({p.resolve() for p in parts}) != 2:
        raise ValueError("Candidate paths must be distinct")
    for part, side in zip(parts, sides, strict=True):
        payload = json.loads(side.read_text())
        if part != side.parent / "part-00000.parquet" or payload["trade_date"] != row["date"]:
            raise ValueError("Candidate sidecar/path/date mismatch")
        if not pilot._partition_binding_matches(payload, side.parent):
            raise ValueError("Candidate sidecar binding failed")
    kind = resolution["kind"]
    if kind == "identical_content":
        if pilot.bind(parts[0])["sha256"] != pilot.bind(parts[1])["sha256"]:
            raise ValueError("Candidates are not byte identical")
        details = {"added_rows": 0, "added_symbols": []}
    elif kind == "audited_additive_patch":
        details = additive_patch(row, resolution, paths)
    else:
        raise ValueError("Unsupported candidate resolution")
    return {
        "kind": kind,
        "selected": str(parts[0]),
        "retained_candidates": [pilot.bind(p) for p in parts],
        **details,
    }


def dated_record(path: Path, collection: str, date_key: str, date: str) -> dict[str, Any]:
    records = json.loads(path.read_text())[collection]
    matches = [r for r in records if r[date_key] == date]
    if len(matches) != 1:
        raise ValueError("Require one dated repair lineage record")
    return matches[0]


def additive_patch(
    row: dict[str, Any], resolution: dict[str, Any], paths: dict[str, Path]
) -> dict[str, Any]:
    patch_path = paths[resolution["patch"]]
    old_side = paths[row["candidate_sidecars"][1]]
    source_sha = pilot.bind(old_side)["sha256"]
    patch_sha = pilot.bind(patch_path)["sha256"]
    acquired = dated_record(paths[resolution["acquisition"]], "records", "date", row["date"])
    built = dated_record(paths[resolution["build"]], "candidates", "trade_date", row["date"])
    common = {"source_sidecar": str(old_side), "patch": str(patch_path), "patch_sha256": patch_sha}
    acquisition_expected = {
        **common,
        "source_sha256": source_sha,
        "status": "patch_ready",
        "rows": 241,
    }
    build_expected = {
        **common,
        "source_sidecar_sha256": source_sha,
        "status": "audited_candidate_ready",
        "candidate_sidecar_sha256": pilot.bind(paths[row["sidecar"]])["sha256"],
    }
    for record, expected in [(acquired, acquisition_expected), (built, build_expected)]:
        if any(record.get(k) != v for k, v in expected.items()):
            raise ValueError("Repair lineage hash/path/status mismatch")
    patch = pq.ParquetFile(patch_path).read().to_pandas()
    old = pq.ParquetFile(paths[row["partitions"][1]]).read().to_pandas()
    selected = pq.ParquetFile(paths[row["partitions"][0]]).read().to_pandas()
    if (
        len(patch) != 241
        or set(patch.ts_code) != {acquired["code"]}
        or set(old.ts_code) & set(patch.ts_code)
    ):
        raise ValueError("Require one disjoint native 241-row patch")
    expected = pd.concat([old, patch], ignore_index=True)
    keys = ["ts_code", "trade_time"]
    try:
        pd.testing.assert_frame_equal(
            selected.sort_values(keys).reset_index(drop=True),
            expected[selected.columns].sort_values(keys).reset_index(drop=True),
            check_exact=True,
            check_dtype=False,
        )
    except (AssertionError, KeyError) as exc:
        raise ValueError("Selected partition is not the exact old-plus-patch union") from exc
    return {
        "added_rows": len(patch),
        "added_symbols": sorted(set(patch.ts_code)),
        "patch": pilot.bind(patch_path),
        "original_rows_unchanged": True,
    }
