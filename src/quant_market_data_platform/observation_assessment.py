"""Bounded JSON inspection of an externally pinned raw observation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .providers.tushare_a_share_fundamentals_support import asset_manifest_payload, file_sha256
from .providers.tushare_fundamentals_observation import (
    require_observation_paths,
    verify_observation,
)

_DATASETS = frozenset({"income", "balancesheet", "cashflow", "fina_indicator"})


def _utc_timestamp(value: object) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Recorded retrieval timestamp has no time zone")
    return parsed.astimezone(UTC).isoformat()


def _inspect_children(
    root: Path, receipts: list[dict[str, Any]], max_content_bytes: int, max_files: int
) -> tuple[list[str], list[dict[str, Any]], int, int]:
    datasets = []
    children = []
    file_count = content_bytes = 0
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError("Invalid observation child receipt")
        relative = str(receipt.get("path", ""))
        dataset = relative.removeprefix("raw/")
        if dataset not in _DATASETS or relative != f"raw/{dataset}" or dataset in datasets:
            raise ValueError("Invalid or duplicate raw observation dataset")
        datasets.append(dataset)
        child = root / relative
        if child.is_symlink() or not child.resolve().is_relative_to(root.resolve()):
            raise ValueError("Unsafe raw observation child")
        require_observation_paths(root, [dataset])
        payload = asset_manifest_payload(child)
        integrity = payload.get("integrity")
        if not isinstance(integrity, dict):
            raise ValueError("Invalid integrity receipt")
        files = integrity.get("files", [])
        if not isinstance(files, list):
            raise ValueError("Invalid content receipts")
        file_count += len(files)
        if file_count > max_files:
            raise ValueError("Observation exceeds explicit file budget")
        content_bytes += _content_size(child, files)
        if file_count > max_files or content_bytes > max_content_bytes:
            raise ValueError("Observation exceeds explicit verification budget")
        children.append(
            {
                "dataset": dataset,
                "manifest_sha256": receipt.get("manifest_sha256"),
                "content_aggregate_sha256": receipt.get("content_aggregate_sha256"),
                "retrieved_at": _utc_timestamp(payload.get("retrieved_at")),
                "observed_vintage_dates": payload.get("observed_vintage_dates"),
                "file_count": len(files),
            }
        )
    return datasets, children, file_count, content_bytes


def _content_size(child: Path, files: list[dict[str, Any]]) -> int:
    total = 0
    for row in files:
        if not isinstance(row, dict):
            raise ValueError("Invalid content receipt")
        size = row.get("bytes")
        if not isinstance(size, int) or size < 0:
            raise ValueError("Invalid declared content size")
        path = (child / str(row.get("path", ""))).resolve()
        if not path.is_relative_to(child.resolve()):
            raise ValueError("Unsafe content path")
        total += max(size, path.stat().st_size)
    return total


def assess_observation(
    root: Path, expected_manifest_sha256: str, *, max_content_bytes: int, max_files: int
) -> dict[str, Any]:
    """Verify current bytes; never infer historical availability from capture dates."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
        raise ValueError("Expected manifest SHA-256 must be 64 lowercase hexadecimal characters")
    if max_content_bytes < 1 or max_files < 1:
        raise ValueError("Verification budgets must be positive")
    if root.is_symlink():
        raise ValueError("Observation root must not be a symlink")
    root = root.expanduser().absolute()
    require_observation_paths(root, [])
    if file_sha256(root / "manifest.yml") != expected_manifest_sha256:
        raise ValueError("Observation does not match expected manifest SHA-256")
    manifest = asset_manifest_payload(root)
    receipts = manifest.get("children")
    if not isinstance(receipts, list) or not 1 <= len(receipts) <= len(_DATASETS):
        raise ValueError("Expected between one and four raw observation children")
    datasets, children, file_count, content_bytes = _inspect_children(
        root, receipts, max_content_bytes, max_files
    )
    identity = manifest.get("identity")
    if not isinstance(identity, dict) or set(identity.get("datasets", [])) != set(datasets):
        raise ValueError("Observation dataset identity differs from children")
    verify_observation(root, identity, datasets)
    if file_sha256(root / "manifest.yml") != expected_manifest_sha256:
        raise ValueError("Observation changed during verification")
    for child in children:
        if (
            file_sha256(root / "raw" / child["dataset"] / "manifest.yml")
            != child["manifest_sha256"]
        ):
            raise ValueError("Child manifest changed during verification")
    snapshot = json.dumps(receipts, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema": "quant.raw_observation_assessment.v1",
        "manifest_sha256": expected_manifest_sha256,
        "content_snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
        "data_content_integrity": "passed",
        "historical_pit": "unknown",
        "derived_materialization": "absent",
        "research_ready": False,
        "time_basis": "UTC",
        "snapshot_date": identity.get("snapshot_date"),
        "source_revision": identity.get("source_revision"),
        "file_count": file_count,
        "content_bytes": content_bytes,
        "children": children,
        "limitations": [
            "Retrieval times record local capture, not historical vendor availability.",
            "Vintage dates are retained as recorded; snapshot date is not intraday availability.",
            "This raw archive supplies no derived PIT certification or economic approval.",
            "Expected manifest identity must come from independently retained owner evidence.",
        ],
    }


def handle_verify_observation(args: argparse.Namespace) -> int:
    try:
        result = assess_observation(
            Path(args.root),
            args.expected_manifest_sha256,
            max_content_bytes=args.max_content_bytes,
            max_files=args.max_files,
        )
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, yaml.YAMLError) as exc:
        print(f"Observation verification failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0
