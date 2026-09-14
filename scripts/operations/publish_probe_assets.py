"""Publish validated probe output as an immutable asset version."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import yaml


class ProbePublicationError(RuntimeError):
    """Raised when probe output does not pass the publication gates."""


def _manifest(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProbePublicationError(f"manifest is not a mapping: {path}")
    return payload


def _validate(source: Path) -> tuple[dict[str, Any], int, int]:
    if not source.is_dir() or source.is_symlink():
        raise ProbePublicationError(f"source is not a real directory: {source}")
    manifest_path = source / "manifest.yml"
    if not manifest_path.is_file():
        raise ProbePublicationError(f"manifest not found: {manifest_path}")
    manifest = _manifest(manifest_path)
    if manifest.get("status") != "completed":
        raise ProbePublicationError(f"probe is not completed: {source}")
    totals = manifest.get("totals")
    if not isinstance(totals, dict) or int(totals.get("rows", 0) or 0) <= 0:
        raise ProbePublicationError(f"probe has no rows: {source}")
    parquet_files = sorted(source.rglob("*.parquet"))
    actual_files = len(parquet_files)
    actual_rows = sum(pq.ParquetFile(path).metadata.num_rows for path in parquet_files)
    if int(totals.get("files", 0) or 0) != actual_files:
        raise ProbePublicationError(
            f"file count mismatch: manifest={totals.get('files')} actual={actual_files}"
        )
    if int(totals.get("rows", 0) or 0) != actual_rows:
        raise ProbePublicationError(
            f"row count mismatch: manifest={totals.get('rows')} actual={actual_rows}"
        )
    return manifest, actual_files, actual_rows


def publish_asset(
    *, source: Path, target: Path, alias: Path, quarantine: Path, dry_run: bool = False
) -> dict[str, object]:
    manifest, files, rows = _validate(source)
    if target.exists() or target.is_symlink():
        raise ProbePublicationError(f"target already exists: {target}")
    if alias.exists() and not quarantine:
        raise ProbePublicationError(f"existing alias requires quarantine: {alias}")
    if quarantine.exists() or quarantine.is_symlink():
        raise ProbePublicationError(f"quarantine destination already exists: {quarantine}")
    result = {
        "source": str(source),
        "target": str(target),
        "alias": str(alias),
        "quarantine": str(quarantine) if alias.exists() else "",
        "files": files,
        "rows": rows,
        "status": "planned" if dry_run else "published",
    }
    if dry_run:
        return result

    target.parent.mkdir(parents=True, exist_ok=True)
    if alias.exists():
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        alias.rename(quarantine)
    shutil.move(str(source), str(target))
    manifest["output_dir"] = str(target)
    manifest["snapshot_name"] = target.name
    (target / "manifest.yml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(os.path.relpath(target, alias.parent))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True, type=Path)
    parser.add_argument("--target", action="append", required=True, type=Path)
    parser.add_argument("--alias", action="append", required=True, type=Path)
    parser.add_argument("--quarantine", action="append", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    groups = zip(args.source, args.target, args.alias, args.quarantine, strict=True)
    results = [
        publish_asset(
            source=source,
            target=target,
            alias=alias,
            quarantine=quarantine,
            dry_run=args.dry_run,
        )
        for source, target, alias, quarantine in groups
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
