"""Materialize completed current TuShare assets as immutable directories.

The legacy ``latest`` entry is kept as a relative compatibility symlink.  The
publisher must be moved to explicit version directories before that alias is
removed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest is not a mapping: {path}")
    return payload


def _version_date(manifest: dict[str, Any], fallback: str) -> str:
    query = manifest.get("query")
    candidates = [manifest.get("as_of_date")]
    if isinstance(query, dict):
        candidates.append(query.get("end_date"))
    for value in candidates:
        text = str(value or "")
        if re.fullmatch(r"20\d{6}", text):
            return text
    return fallback


def materialize_dataset(
    root: Path,
    *,
    dataset: str,
    fallback_date: str,
    dry_run: bool = False,
) -> dict[str, str]:
    parent = root / "assets" / "tushare" / "a_share" / dataset
    latest = next(parent.glob(f"a_share_all_{dataset}_latest"), None)
    if latest is None or not latest.is_dir() or latest.is_symlink():
        raise FileNotFoundError(f"mutable latest directory not found: {parent}")
    manifest_path = latest / "manifest.yml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    manifest = _load_manifest(manifest_path)
    if manifest.get("status") != "completed":
        raise ValueError(f"asset is not completed: {latest}")
    totals = manifest.get("totals")
    if not isinstance(totals, dict) or int(totals.get("rows", 0) or 0) <= 0:
        raise ValueError(f"asset has no rows and cannot be materialized: {latest}")

    date = _version_date(manifest, fallback_date)
    version_name = f"a_share_all_{dataset}_{date}"
    version = parent / version_name
    if version.exists() or version.is_symlink():
        raise FileExistsError(f"version directory already exists: {version}")
    relative_target = os.path.relpath(version, parent)
    result = {
        "dataset": dataset,
        "source": str(latest),
        "version": str(version),
        "alias": str(latest),
        "version_date": date,
        "status": "planned" if dry_run else "materialized",
    }
    if dry_run:
        return result

    latest.rename(version)
    version_manifest = version / "manifest.yml"
    manifest["output_dir"] = str(version)
    manifest["snapshot_name"] = version_name
    version_manifest.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    latest.symlink_to(relative_target)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-root", required=True)
    parser.add_argument("--dataset", action="append", required=True)
    parser.add_argument("--fallback-date", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(args.artifacts_root).expanduser().resolve()
    results = [
        materialize_dataset(
            root,
            dataset=dataset,
            fallback_date=args.fallback_date,
            dry_run=args.dry_run,
        )
        for dataset in args.dataset
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
