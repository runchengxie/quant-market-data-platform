"""Reconcile a completed trade-date asset manifest with its stored partitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from quant_market_data_platform.providers.tushare_a_share_manifests import _stored_partition_totals
from quant_market_data_platform.stored_coverage import trade_date_inventory


class ManifestReconciliationError(RuntimeError):
    """Raised when an asset cannot be reconciled safely."""


def reconcile_manifest(asset_dir: Path, *, apply: bool = False) -> dict[str, Any]:
    manifest_path = asset_dir / "manifest.yml"
    data_dir = asset_dir / "data"
    if not manifest_path.is_file() or not data_dir.is_dir():
        raise ManifestReconciliationError(f"manifest or data directory is missing: {asset_dir}")
    original_text = manifest_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load(original_text)
    if not isinstance(manifest, dict) or manifest.get("status") != "completed":
        raise ManifestReconciliationError(f"asset is not completed: {asset_dir}")
    partitions = sorted(
        path for path in data_dir.iterdir() if path.is_dir() and path.name.startswith("trade_date=")
    )
    if not partitions:
        raise ManifestReconciliationError(f"no trade-date partitions: {asset_dir}")
    inventory = trade_date_inventory(data_dir)
    stored_totals = _stored_partition_totals(data_dir)
    rows = stored_totals["rows"]
    totals = manifest.setdefault("totals", {})
    if not isinstance(totals, dict):
        raise ManifestReconciliationError(f"manifest totals is not a mapping: {asset_dir}")
    totals.update(stored_totals)
    manifest["stored_coverage"] = inventory
    result = {
        "asset": str(asset_dir),
        "rows": rows,
        "files": stored_totals["files"],
        "first_date": inventory["start_date"],
        "last_date": inventory["end_date"],
        "status": "planned" if not apply else "reconciled",
    }
    if inventory != trade_date_inventory(data_dir) or original_text != manifest_path.read_text(
        encoding="utf-8"
    ):
        raise ManifestReconciliationError(
            "asset changed during reconciliation; stop writers and retry"
        )
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
