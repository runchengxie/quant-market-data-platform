"""Raw observation receipts, deliberately distinct from PIT vintage certification."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from .tushare_a_share_fundamentals_support import (
    asset_manifest_payload,
    file_sha256,
    require_asset_integrity,
)
from .tushare_common import write_manifest

SCHEMA = "tushare.a_share.fundamentals.raw_observation.v1"
MARKER = "OBSERVATION_SEALED.json"


def observation_root(artifacts_root: Path, snapshot_date: str) -> Path:
    return (
        artifacts_root.expanduser().resolve()
        / "assets/tushare/a_share"
        / "fundamentals_observations"
        / f"observation={snapshot_date}"
    )


def require_observation_paths(root: Path, datasets: Sequence[str]) -> None:
    """Reject redirected output before attempts, provider writes or sealed reuse."""
    paths = [root, root / "raw", *(root / "raw" / dataset for dataset in datasets)]
    for path in paths:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise RuntimeError("Unsafe raw observation output path")
    for name in ("manifest.yml", MARKER, "retry_identity.json", "archive_state.json", "attempts"):
        if (root / name).is_symlink():
            raise RuntimeError("Unsafe raw observation metadata path")


def _receipt(root: Path, dataset: str) -> dict[str, object]:
    child = root / "raw" / dataset
    if (
        (root / "raw").is_symlink()
        or child.is_symlink()
        or (child / "manifest.yml").is_symlink()
        or (child / "manifest.seal.json").is_symlink()
        or not child.resolve().is_relative_to(root.resolve())
    ):
        raise RuntimeError("Unsafe raw observation child path")
    manifest = asset_manifest_payload(child)
    if manifest.get("status") != "completed" or manifest.get("immutable_snapshot") is not True:
        raise RuntimeError("Raw observation child is incomplete")
    require_asset_integrity(child, manifest)
    return {
        "path": f"raw/{dataset}",
        "manifest_sha256": file_sha256(child / "manifest.yml"),
        "content_aggregate_sha256": manifest["integrity"]["aggregate_sha256"],
    }


def seal_observation(root: Path, identity: Mapping[str, object], datasets: Sequence[str]) -> dict:
    manifest = {
        "schema_version": SCHEMA,
        "status": "completed",
        "immutable_snapshot": True,
        "archive_mode": "raw",
        "identity": dict(identity),
        "children": [_receipt(root, dataset) for dataset in datasets],
        "publication": {"latest_switched": False},
    }
    write_manifest(root / "manifest.yml", manifest)
    (root / MARKER).write_text(
        json.dumps(
            {
                "schema_version": SCHEMA,
                "manifest_sha256": file_sha256(root / "manifest.yml"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_observation(root: Path, identity: Mapping[str, object], datasets: Sequence[str]) -> dict:
    require_observation_paths(root, datasets)
    seal = json.loads((root / MARKER).read_text(encoding="utf-8"))
    if seal.get("schema_version") != SCHEMA or seal.get("manifest_sha256") != file_sha256(
        root / "manifest.yml"
    ):
        raise RuntimeError("Raw observation manifest seal does not match")
    manifest = asset_manifest_payload(root)
    if (
        manifest.get("schema_version") != SCHEMA
        or manifest.get("status") != "completed"
        or manifest.get("archive_mode") != "raw"
        or manifest.get("immutable_snapshot") is not True
    ):
        raise RuntimeError("Raw observation manifest is incomplete")
    if manifest.get("identity") != identity:
        raise RuntimeError("Raw observation identity changed")
    if manifest.get("children") != [_receipt(root, dataset) for dataset in datasets]:
        raise RuntimeError("Raw observation child receipts do not match")
    return manifest
