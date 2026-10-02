"""Immutable, resumable QuantZone runs without automatic supplier retries."""

from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quant_market_data_platform.providers.quantzone import (
    FactorClient,
    check_quantzone,
    safe_supplier_error,
)
from quant_market_data_platform.quantzone_artifacts import (
    ArtifactError,
    atomic_json,
    atomic_parquet,
    file_hash,
    project_factor_panel,
    safe_artifact_path,
    validate_factor_batch,
)
from quant_market_data_platform.quantzone_plan import EVIDENCE, FactorDownloadPlan

SCHEMA = "market-data-platform.quantzone-factor-download.v1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _lock(run: Path) -> Iterator[None]:
    # Retain the inode: unlinking a flock file can permit two simultaneous locks.
    import fcntl

    path = safe_artifact_path(run, ".download.lock")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ArtifactError("A download is already using this run") from None
        yield
    finally:
        os.close(descriptor)


def _new_receipt(plan: FactorDownloadPlan) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "query_identity": plan.query_identity,
        "sdk_version": plan.sdk_version,
        "started_at": _now(),
        "status": "partial",
        "evidence": dict(EVIDENCE),
        "coverage": "observed_only",
        "batches": [
            {
                "key": batch.key,
                "ukeys": list(batch.ukeys),
                "factors": list(batch.factors),
                "start_date": str(batch.start_date),
                "end_date": str(batch.end_date),
                "status": "pending",
                "path": batch.key + ".parquet",
            }
            for batch in plan.batches
        ],
    }


def _load_receipt(run: Path, plan: FactorDownloadPlan) -> dict[str, Any]:
    try:
        receipt = json.loads(safe_artifact_path(run, "receipt.json").read_text())
    except (OSError, ValueError):
        raise ArtifactError("Cannot read resume receipt") from None
    expected = _new_receipt(plan)
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != SCHEMA
        or receipt.get("query_identity") != plan.query_identity
    ):
        raise ArtifactError("Resume query identity mismatch")
    if receipt.get("sdk_version") != plan.sdk_version or receipt.get("evidence") != EVIDENCE:
        raise ArtifactError("Resume evidence or SDK identity mismatch")
    entries = receipt.get("batches")
    if not isinstance(entries, list) or len(entries) != len(plan.batches):
        raise ArtifactError("Resume batch identity mismatch")
    for item, wanted in zip(entries, expected["batches"], strict=True):
        if not isinstance(item, dict) or any(
            item.get(key) != wanted[key]
            for key in ("key", "ukeys", "factors", "start_date", "end_date", "path")
        ):
            raise ArtifactError("Resume batch identity mismatch")
        if item.get("status") not in ("pending", "complete"):
            raise ArtifactError("Invalid resume batch state")
        if item["status"] == "complete" and file_hash(
            safe_artifact_path(run, item["path"])
        ) != item.get("sha256"):
            raise ArtifactError("Resume artifact hash mismatch")
    _verify_run_state(run, receipt, entries)
    return receipt


def _verify_run_state(run: Path, receipt: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    if receipt.get("status") == "complete":
        projection = receipt.get("projection")
        if not isinstance(projection, dict) or projection.get("path") != "research-panel.parquet":
            raise ArtifactError("Completed run lacks a valid projection")
        if file_hash(safe_artifact_path(run, projection["path"])) != projection.get("sha256"):
            raise ArtifactError("Projection hash mismatch")
        if any(item["status"] != "complete" for item in entries):
            raise ArtifactError("Completed run contains pending batches")
    elif receipt.get("status") != "partial":
        raise ArtifactError("Invalid resume run state")


def _write_projection(run: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    groups: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    factors: set[str] = set()
    for entry in receipt["batches"]:
        groups[(entry["start_date"], entry["end_date"], tuple(entry["ukeys"]))].append(entry)
        factors.update(entry["factors"])
    schema = pa.schema(
        [
            ("symbol", pa.string()),
            ("trade_date", pa.timestamp("ns")),
            *[(factor, pa.float64()) for factor in sorted(factors)],
        ]
    )
    path = safe_artifact_path(run, "research-panel.parquet")
    if path.exists():
        raise ArtifactError("Projection collision; preserve and inspect the partial run")
    descriptor, temporary = tempfile.mkstemp(prefix=".projection-", dir=run)
    os.close(descriptor)
    temporary_path = Path(temporary)
    rows = 0
    try:
        with pq.ParquetWriter(temporary_path, schema) as writer:
            for entries in groups.values():
                frames = [
                    pd.read_parquet(safe_artifact_path(run, item["path"])) for item in entries
                ]
                panel = project_factor_panel(
                    pd.concat(frames, ignore_index=True), receipt["symbol_map"]
                )
                panel = panel.reindex(columns=schema.names)
                writer.write_table(pa.Table.from_pandas(panel, schema=schema, preserve_index=False))
                rows += len(panel)
        with temporary_path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {"path": path.name, "sha256": file_hash(path), "row_count": rows}


def _acquire(
    run: Path, plan: FactorDownloadPlan, client: FactorClient, receipt: dict[str, Any]
) -> None:
    checked = check_quantzone(client, plan)
    if "symbol_map" in receipt and receipt["symbol_map"] != checked["symbol_map"]:
        raise ArtifactError("Stock mapping changed since the partial run")
    receipt.update(checked)
    atomic_json(run / "receipt.json", receipt)
    for batch, entry in zip(plan.batches, receipt["batches"], strict=True):
        if entry["status"] == "complete":
            continue
        path = safe_artifact_path(run, entry["path"])
        if path.exists():
            raise ArtifactError("Unreceipted batch exists; preserve it for recovery inspection")
        entry["requested_at"] = _now()
        atomic_json(run / "receipt.json", receipt)
        try:
            frame = client.get_factors(
                ukeys=list(batch.ukeys),
                factor=list(batch.factors),
                start_date=str(batch.start_date),
                end_date=str(batch.end_date),
            )
        except Exception as error:
            raise safe_supplier_error(error) from None
        validated = validate_factor_batch(frame, batch, receipt["symbol_map"])
        digest = atomic_parquet(path, validated)
        entry.update(
            status="complete",
            sha256=digest,
            row_count=len(validated),
            null_count=int(validated["value"].isna().sum()),
            empty_response=validated.empty,
            retrieved_at=_now(),
        )
        atomic_json(run / "receipt.json", receipt)
    receipt["projection"] = _write_projection(run, receipt)
    receipt.update(status="complete", completed_at=_now())
    atomic_json(run / "receipt.json", receipt)


def run_factor_download(
    plan: FactorDownloadPlan, client: FactorClient, *, resume: Path | None = None
) -> Path:
    try:
        if plan.root.resolve() != plan.root:
            raise ArtifactError("Output directory changed after planning")
        plan.root.mkdir(parents=True, exist_ok=True)
        if resume is None:
            run = plan.root / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12])
            run.mkdir()
        else:
            run = resume.expanduser().absolute()
            if run.is_symlink() or run.resolve().parent != plan.root or not run.is_dir():
                raise ArtifactError(
                    "Resume directory must be an existing run within the output root"
                )
        with _lock(run):
            receipt = _new_receipt(plan) if resume is None else _load_receipt(run, plan)
            if receipt["status"] != "complete":
                if resume is None:
                    atomic_json(run / "receipt.json", receipt)
                _acquire(run, plan, client, receipt)
        return run
    finally:
        client.close()
