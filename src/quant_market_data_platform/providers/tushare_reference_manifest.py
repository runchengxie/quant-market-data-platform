"""Hash-bound manifests for owner-issued single-file reference receipts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def write_reference_manifest(asset_path: Path, receipt_path: Path) -> Path:
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    with asset_path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if receipt.get("sha256") != digest:
        raise ValueError("reference receipt hash does not match asset")
    coverage_end = receipt.get("end_date") or receipt["target_date"]
    manifest = {
        "schema_version": "market-data-platform.tushare-reference-manifest.v1",
        "dataset": receipt["dataset"],
        "provider": "tushare",
        "market": "a_share",
        "status": (
            "completed"
            if receipt.get("quality_status") == "complete"
            and receipt.get("source_quality_status", "complete") == "complete"
            else "partial"
        ),
        "as_of_date": coverage_end,
        "version_date": receipt["target_date"],
        "query": {"end_date": coverage_end},
        "totals": {"rows": receipt["rows"], "files": 1},
        "columns": receipt["columns"],
        "sha256": digest,
        "lineage": {
            "owner_receipt": str(receipt_path),
            "owner_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
            "source_quality_status": receipt.get("quality_status"),
        },
    }
    for key in (
        "semantics",
        "pit_class",
        "revision_safe",
        "start_date",
        "end_date",
        "source_receipt_sha256",
        "source_receipt_schema_version",
        "source_quality_status",
    ):
        if key in receipt:
            manifest[key] = receipt[key]
    path = asset_path.with_suffix(".manifest.yml")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
