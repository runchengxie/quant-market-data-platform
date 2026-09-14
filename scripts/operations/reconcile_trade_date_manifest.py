"""Reconcile a completed trade-date asset manifest with its stored partitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


class ManifestReconciliationError(RuntimeError):
    """Raised when an asset cannot be reconciled safely."""


def reconcile_manifest(asset_dir: Path, *, apply: bool = False) -> dict[str, Any]:
    manifest_path = asset_dir / "manifest.yml"
    data_dir = asset_dir / "data"
    if not manifest_path.is_file() or not data_dir.is_dir():
        raise ManifestReconciliationError(f"manifest or data directory is missing: {asset_dir}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("status") != "completed":
        raise ManifestReconciliationError(f"asset is not completed: {asset_dir}")
    partitions = sorted(
        path for path in data_dir.iterdir() if path.is_dir() and path.name.startswith("trade_date=")
    )
    if not partitions:
        raise ManifestReconciliationError(f"no trade-date partitions: {asset_dir}")
    frames = [pd.read_parquet(path / "part.parquet") for path in partitions]
    rows = sum(len(frame) for frame in frames)
    dates = [path.name.split("=", 1)[1] for path in partitions]
    totals = manifest.setdefault("totals", {})
    if not isinstance(totals, dict):
        raise ManifestReconciliationError(f"manifest totals is not a mapping: {asset_dir}")
    totals.update({"rows": rows, "files": len(partitions)})
    if "symbol" in frames[0].columns:
        totals["symbols"] = int(pd.concat(frames, ignore_index=True)["symbol"].nunique())
    elif "ts_code" in frames[0].columns:
        totals["symbols"] = int(pd.concat(frames, ignore_index=True)["ts_code"].nunique())
    manifest["written_trade_dates"] = dates
    manifest["skipped_trade_dates"] = []
    manifest["empty_trade_dates"] = []
    result = {
        "asset": str(asset_dir),
        "rows": rows,
        "files": len(partitions),
        "first_date": dates[0],
        "last_date": dates[-1],
        "status": "planned" if not apply else "reconciled",
    }
    if apply:
        manifest_path.write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset_dir", nargs="+", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    results = [reconcile_manifest(path, apply=args.apply) for path in args.asset_dir]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
