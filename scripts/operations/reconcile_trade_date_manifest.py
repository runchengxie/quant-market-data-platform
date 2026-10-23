"""Reconcile a completed trade-date asset manifest with its stored partitions."""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml

from quant_market_data_platform.providers.tushare_a_share_manifests import _stored_partition_totals
from quant_market_data_platform.stored_coverage import trade_date_inventory


class ManifestReconciliationError(RuntimeError):
    """Raised when an asset cannot be reconciled safely."""


def _atomic_manifest_write(
    path: Path, text: str, original_text: str, inventory: dict[str, Any]
) -> None:
    original_mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, pending_name = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=path.parent)
    pending = Path(pending_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fchmod(stream.fileno(), original_mode)
            os.fsync(stream.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            if inventory != trade_date_inventory(
                path.parent / "data"
            ) or original_text != path.read_text(encoding="utf-8"):
                raise ManifestReconciliationError(
                    "asset changed before atomic manifest publication"
                )
            os.replace(pending, path)
            try:
                os.fsync(directory_fd)
            except OSError as exc:
                raise ManifestReconciliationError(
                    "manifest replaced but directory fsync failed; "
                    "publication durability is unconfirmed"
                ) from exc
        finally:
            os.close(directory_fd)
    finally:
        pending.unlink(missing_ok=True)


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
        _atomic_manifest_write(
            manifest_path,
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
            original_text,
            inventory,
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
