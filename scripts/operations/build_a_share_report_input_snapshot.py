#!/usr/bin/env python3
"""Build a date-pinned, symlink-only A-share report input snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import yaml

SNAPSHOT_SCHEMA = "a_share.report_input_snapshot.v1"
_DATE_RE = re.compile(r"(?<!\d)(20\d{6})(?!\d)")


class SnapshotBuildError(RuntimeError):
    """Raised when a complete immutable input snapshot cannot be built."""


def _valid_date(value: str) -> date:
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except (ValueError, IndexError) as exc:
        raise SnapshotBuildError(f"invalid YYYYMMDD date: {value!r}") from exc


def _manifest(path: Path) -> dict[str, Any] | None:
    manifest_path = path / "manifest.yml"
    if not manifest_path.is_file():
        return None
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return payload if isinstance(payload, dict) else None


def _version_dates(path: Path, payload: dict[str, Any]) -> tuple[str, str] | None:
    query = payload.get("query")
    if isinstance(query, dict):
        start = query.get("start_date")
        end = query.get("end_date")
        if isinstance(start, str) and isinstance(end, str) and len(start) == len(end) == 8:
            return start, end
    dates = _DATE_RE.findall(path.name)
    if not dates:
        return None
    return min(dates), max(dates)


def _manifest_sha256(path: Path) -> str:
    return hashlib.sha256((path / "manifest.yml").read_bytes()).hexdigest()


def _select_version(dataset_root: Path, target: date) -> tuple[Path, dict[str, Any], str, str]:
    candidates: list[tuple[str, str, Path, dict[str, Any]]] = []
    if dataset_root.is_dir():
        for path in sorted(dataset_root.iterdir()):
            if not path.is_dir() or path.is_symlink() or "quarantine" in path.name:
                continue
            payload = _manifest(path)
            if payload is None or payload.get("status") not in {None, "completed"}:
                continue
            dates = _version_dates(path, payload)
            if dates is None:
                continue
            start, end = dates
            try:
                if _valid_date(start) <= target <= _valid_date(end):
                    candidates.append((end, start, path, payload))
            except SnapshotBuildError:
                continue
    if not candidates:
        raise SnapshotBuildError(
            f"no immutable version covering {target:%Y%m%d}: {dataset_root.name}"
        )
    partition_name = f"trade_date={target:%Y%m%d}"
    partitioned = [
        candidate for candidate in candidates if (candidate[2] / "data" / partition_name).is_dir()
    ]
    if partitioned:
        candidates = partitioned
    end, start, path, payload = min(candidates, key=lambda item: (item[0], item[1], item[2].name))
    return path, payload, start, end


def _snapshot_id(target_date: str, datasets: dict[str, dict[str, Any]]) -> str:
    identity = {"target_date": target_date, "datasets": datasets}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def build_snapshot(
    *,
    artifacts_root: Path,
    target_date: str,
    output_root: Path,
    datasets: tuple[str, ...],
) -> dict[str, Any]:
    """Build a symlink overlay and write a content-addressed receipt."""
    target = _valid_date(target_date)
    root = artifacts_root.expanduser().resolve()
    output = output_root.expanduser().resolve()
    if not root.is_dir():
        raise SnapshotBuildError(f"artifacts root does not exist: {root}")
    if output.exists() and any(output.iterdir()):
        raise SnapshotBuildError(f"snapshot output is not empty: {output}")

    selections: dict[str, dict[str, Any]] = {}
    selected_paths: dict[str, Path] = {}
    for dataset in datasets:
        if not dataset or "/" in dataset or dataset in {".", ".."}:
            raise SnapshotBuildError(f"invalid dataset name: {dataset!r}")
        source_root = root / "assets" / "tushare" / "a_share" / dataset
        path, payload, start, end = _select_version(source_root, target)
        selections[dataset] = {
            "source_path": str(path.relative_to(root)),
            "version_date": end,
            "version_start_date": start,
            "version_end_date": end,
            "manifest_sha256": _manifest_sha256(path),
            "manifest_schema": payload.get("schema_version"),
        }
        selected_paths[dataset] = path

    snapshot_id = _snapshot_id(target_date, selections)
    receipt: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA,
        "snapshot_id": snapshot_id,
        "target_date": target_date,
        "artifacts_root": str(root),
        "datasets": selections,
    }

    output.mkdir(parents=True, exist_ok=True)
    for dataset, source in selected_paths.items():
        link = output / "assets" / "tushare" / "a_share" / dataset / f"{dataset}_latest"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(source)
    receipt_path = output / "snapshot_receipt.json"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".snapshot_receipt.", dir=output)
    temporary = Path(temporary_name)
    try:
        with open(descriptor, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temporary.replace(receipt_path)
    finally:
        temporary.unlink(missing_ok=True)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-root", required=True, type=Path)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--dataset", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = build_snapshot(
            artifacts_root=args.artifacts_root,
            target_date=args.target_date,
            output_root=args.output_root,
            datasets=tuple(args.dataset),
        )
    except SnapshotBuildError as exc:
        parser.exit(2, f"snapshot build failed: {exc}\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
