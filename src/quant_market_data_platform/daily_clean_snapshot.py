"""Capture independent, date-filtered raw inputs with verifiable lineage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import yaml

from .paths import candidate_asset_paths

DATASETS = ("daily", "adj_factor", "daily_basic", "limit_status")


def _stamp(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source: Path, destination: Path) -> None:
    # A reflink shares blocks, never an inode: overwriting raw data cannot
    # mutate captured inputs. Ordinary copying is the portable fallback.
    if shutil.which("cp") and os.name == "posix":
        subprocess.run(["cp", "--reflink=auto", "--", str(source), str(destination)], check=True)
    else:
        shutil.copyfile(source, destination)
    if _hash_file(source) != _hash_file(destination):
        raise ValueError(f"source changed while copying: {source}")


def _dates(start: str, end: str) -> None:
    for value in (start, end):
        if not re.fullmatch(r"\d{8}", value):
            raise ValueError("snapshot dates must be YYYYMMDD")
        datetime.strptime(value, "%Y%m%d")
    if start > end:
        raise ValueError("start-date must not be after end-date")


def _source_inventory(source: Path, start: str, end: str) -> list[Path]:
    files: list[Path] = []
    for partition in sorted((source / "data").glob("trade_date=*")):
        day = partition.name.removeprefix("trade_date=")
        if not re.fullmatch(r"\d{8}", day) or not start <= day <= end:
            continue
        if partition.is_symlink():
            raise ValueError(f"mutable partition indirection is unsupported: {partition}")
        for path in sorted(partition.glob("*.parquet")):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"raw Parquet must be a regular file: {path}")
            files.append(path)
    if not any(path.parent.name == f"trade_date={end}" for path in files):
        raise ValueError(f"missing end-date partition for {source.name}: {end}")
    return files


def _sources(root: Path, start: str, end: str) -> dict[str, dict[str, Any]]:
    aliases = candidate_asset_paths(root, market="a_share", provider="tushare")
    sources: dict[str, dict[str, Any]] = {}
    for dataset in DATASETS:
        source = aliases[dataset].resolve(strict=True)
        if not source.is_relative_to(root):
            raise ValueError(f"raw input resolves outside artifacts root: {source}")
        manifest = source / "manifest.yml"
        manifest_stamp = _stamp(manifest)
        payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("status") != "completed":
            raise ValueError(f"source manifest is not completed: {manifest}")
        files = _source_inventory(source, start, end)
        sources[dataset] = {
            "path": source,
            "manifest": manifest,
            "manifest_stamp": manifest_stamp,
            "manifest_sha256": _hash_file(manifest),
            "files": files,
            "stamps": {path: _stamp(path) for path in files},
        }
    return sources


def _capture_dataset(dataset: str, state: dict[str, Any], output: Path) -> dict[str, Any]:
    source: Path = state["path"]
    target = output / dataset
    records = []
    rows = 0
    for original in state["files"]:
        relative = original.relative_to(source)
        captured = target / relative
        captured.parent.mkdir(parents=True, exist_ok=True)
        _copy_file(original, captured)
        rows += pq.ParquetFile(captured).metadata.num_rows
        records.append(
            {
                "path": relative.as_posix(),
                "bytes": captured.stat().st_size,
                "sha256": _hash_file(captured),
            }
        )
        captured.chmod(0o444)
    return {
        "source_path": source.as_posix(),
        "source_manifest_sha256": state["manifest_sha256"],
        "rows": rows,
        "files": records,
    }


def _verify_sources(sources: dict[str, dict[str, Any]], start: str, end: str) -> None:
    for state in sources.values():
        if (
            _stamp(state["manifest"]) != state["manifest_stamp"]
            or _hash_file(state["manifest"]) != state["manifest_sha256"]
            or _source_inventory(state["path"], start, end) != state["files"]
            or any(_stamp(path) != stamp for path, stamp in state["stamps"].items())
        ):
            raise ValueError(f"source changed during snapshot capture: {state['path']}")


def _write_manifest(
    output: Path, dataset: str, entry: dict[str, Any], start: str, end: str
) -> None:
    dates = sorted(
        {Path(record["path"]).parent.name.removeprefix("trade_date=") for record in entry["files"]}
    )
    manifest = {
        "status": "completed",
        "dataset": dataset,
        "output_dir": str(output / dataset),
        "query": {"start_date": start, "end_date": end, "partition_by": "trade_date"},
        "totals": {
            "rows": entry["rows"],
            "files": len(entry["files"]),
            "trade_dates_written": len(dates),
        },
        "written_trade_dates": dates,
        "input_snapshot_receipt": "../snapshot_receipt.json",
        "source_manifest_sha256": entry["source_manifest_sha256"],
    }
    (output / dataset / "manifest.yml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    (output / dataset / "manifest.yml").chmod(0o444)


def build_daily_clean_snapshot(
    root: Path, start_date: str, end_date: str, output: Path
) -> dict[str, Any]:
    """Publish a receipt only after all independent copies pass source checks.

    Failure leaves an unreceipted attempt for inspection. No existing output or
    source is modified. Callers must serialize writers for a coherent capture.
    """
    _dates(start_date, end_date)
    root = root.expanduser().resolve(strict=True)
    output = output.expanduser().absolute()
    if not output.resolve().is_relative_to(root) or output == root or output.is_symlink():
        raise ValueError(
            "snapshot output must be below the artifacts root without alias indirection"
        )
    if output.exists():
        raise FileExistsError(f"snapshot output already exists: {output}")
    sources = _sources(root, start_date, end_date)
    if any(
        state["path"].is_relative_to(output) or output.is_relative_to(state["path"])
        for state in sources.values()
    ):
        raise ValueError("snapshot output overlaps a raw source")
    output.mkdir(parents=True, exist_ok=False)
    entries = {
        dataset: _capture_dataset(dataset, state, output) for dataset, state in sources.items()
    }
    _verify_sources(sources, start_date, end_date)
    for dataset, entry in entries.items():
        entry["source_path"] = Path(entry["source_path"]).relative_to(root).as_posix()
        _write_manifest(output, dataset, entry, start_date, end_date)
    receipt = {
        "schema_version": "a_share.daily_clean_input_snapshot.v1",
        "status": "completed",
        "created_at": datetime.now(UTC).isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "datasets": entries,
    }
    with (output / "snapshot_receipt.json").open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")
    (output / "snapshot_receipt.json").chmod(0o444)
    return receipt
