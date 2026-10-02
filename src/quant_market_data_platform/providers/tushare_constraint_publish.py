"""Publication gates and lineage for TuShare constraint reference assets."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from quant_market_data_platform.providers.tushare_constraint_io import sha256

CONSTRAINT_PUBLISH_DATASETS = (
    "namechange",
    "margin_secs",
    "st_history_reconstructed",
    "st_intervals_reconstructed",
)


def _source_receipt_path(source_root: Path, dataset: str) -> Path:
    name = (
        "st_history_reconstructed.receipt.json"
        if dataset.startswith("st_")
        else f"{dataset}.receipt.json"
    )
    return source_root / name


def _validated_source_receipts(
    source_root: Path,
    *,
    allow_partial: bool,
    datasets: tuple[str, ...],
) -> dict[str, tuple[Path, dict[str, Any]]]:
    receipts: dict[str, tuple[Path, dict[str, Any]]] = {}
    for dataset in datasets:
        source_path = source_root / f"{dataset}.parquet"
        if not source_path.is_file():
            continue
        receipt_path = _source_receipt_path(source_root, dataset)
        if not receipt_path.is_file():
            if allow_partial:
                continue
            raise FileNotFoundError(f"missing constraint source receipt: {receipt_path}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        hash_key = {
            "st_history_reconstructed": "history_sha256",
            "st_intervals_reconstructed": "intervals_sha256",
        }.get(dataset, "sha256")
        if receipt.get(hash_key) != sha256(source_path):
            raise ValueError(f"constraint source receipt hash mismatch: {dataset}")
        if receipt.get("quality_status") != "complete" and not allow_partial:
            raise ValueError(f"refusing to publish partial constraint asset: {dataset}")
        receipts[dataset] = (receipt_path, receipt)
    return receipts


def _lineage(dataset: str, receipt_path: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    lineage = {
        "source_receipt_sha256": sha256(receipt_path),
        "source_receipt_schema_version": receipt.get("schema_version"),
        "source_quality_status": receipt.get("quality_status"),
    }
    if dataset.startswith("st_"):
        lineage.update(
            {
                "pit_class": receipt.get("pit_class"),
                "revision_safe": receipt.get("revision_safe"),
                "start_date": receipt.get("start_date"),
                "end_date": receipt.get("end_date"),
            }
        )
    elif receipt.get("semantics"):
        lineage["semantics"] = receipt["semantics"]
    return lineage


def publish_constraint_assets(
    artifacts_root: str | Path | None,
    source_dir: str | Path,
    target_date: str,
    *,
    allow_partial: bool = False,
    datasets: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Publish immutable versions of constraint sources and reconstructed ST outputs."""
    from quant_market_data_platform.paths import candidate_asset_paths
    from quant_market_data_platform.providers.tushare_a_share_reference import (
        publish_reference_asset,
    )

    source_root = Path(source_dir).expanduser().resolve()
    selected = tuple(
        dict.fromkeys(datasets if datasets is not None else CONSTRAINT_PUBLISH_DATASETS)
    )
    unknown = set(selected) - set(CONSTRAINT_PUBLISH_DATASETS)
    if not selected or unknown:
        raise ValueError(f"invalid constraint publish datasets: {sorted(unknown)}")
    paths = candidate_asset_paths(artifacts_root)
    missing = [
        dataset for dataset in selected if not (source_root / f"{dataset}.parquet").is_file()
    ]
    if missing and not allow_partial:
        raise FileNotFoundError("missing constraint assets: " + ", ".join(missing))
    source_receipts = _validated_source_receipts(
        source_root,
        allow_partial=allow_partial,
        datasets=selected,
    )

    published: list[dict[str, Any]] = []
    for dataset in selected:
        source_path = source_root / f"{dataset}.parquet"
        if not source_path.is_file():
            continue
        lineage = {"source_quality_status": "unknown"}
        if dataset in source_receipts:
            receipt_path, source_receipt = source_receipts[dataset]
            lineage = _lineage(dataset, receipt_path, source_receipt)
        item = publish_reference_asset(
            dataset, source_path, paths[dataset], target_date, receipt_lineage=lineage
        )
        published.append(item)
    return {"target_date": target_date, "published": published, "missing": missing}


__all__ = ["CONSTRAINT_PUBLISH_DATASETS", "publish_constraint_assets"]
