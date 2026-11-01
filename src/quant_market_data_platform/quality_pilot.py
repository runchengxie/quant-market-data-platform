"""Low-copy pilot classification for raw L2 files."""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute
import pyarrow.parquet as pq


@dataclass(frozen=True)
class L2PilotOptions:
    files: list[str | Path]
    output_dir: str | Path
    batch_size: int = 262_144
    max_rows_per_file: int | None = None


def _kind(path: Path) -> str:
    if path.name.startswith(("deal_", "trades_")):
        return "deal"
    if path.name.startswith("snapshot_"):
        return "snapshot"
    return "order"


def _columns(kind: str) -> tuple[str, str, str, str | None]:
    if kind == "deal":
        return "SecuCode", "DealTime", "DealID", "Price"
    if kind == "snapshot":
        return "SecuCode", "TickTime", "", "Price"
    return "SecuCode", "OrderTime", "OrderID", "Price"


def _classify_batch(batch: pa.RecordBatch, kind: str, offset: int) -> tuple[pa.Table, pa.Table]:
    ticker, timestamp, identifier, price = _columns(kind)
    names = set(batch.schema.names)
    relevant = {
        name: batch.column(name).to_pylist() if name in names else [None] * batch.num_rows
        for name in (ticker, timestamp, identifier, price)
    }
    decisions: list[str] = []
    reasons: list[str] = []
    row_numbers: list[int] = []
    codes: list[Any] = []
    event_ids: list[Any] = []
    keep_indices: list[int] = []
    for index in range(batch.num_rows):
        ticker_value = relevant[ticker][index]
        timestamp_value = relevant[timestamp][index]
        identifier_value = relevant[identifier][index] if identifier else None
        price_value = relevant[price][index]
        reason = ""
        decision = "keep"
        if any(value is None for value in (ticker_value, timestamp_value, price_value)):
            decision, reason = "exclude", "missing_required_value"
        elif kind != "snapshot" and identifier_value is None:
            decision, reason = "exclude", "missing_event_id"
        elif kind == "snapshot" and price_value <= 0:
            decision, reason = "tag", "empty_snapshot_price"
        elif price_value <= 0:
            decision, reason = "tag", "special_price_or_event"
        if decision == "keep":
            keep_indices.append(index)
        else:
            row_numbers.append(offset + index)
            decisions.append(decision)
            reasons.append(reason)
            codes.append(ticker_value)
            event_ids.append(identifier_value)
    canonical = pa.Table.from_batches([batch]).take(pa.array(keep_indices, type=pa.int64()))
    labels = pa.table(
        {
            "row_number": row_numbers,
            "decision": decisions,
            "reason": reasons,
            "SecuCode": codes,
            "event_id": event_ids,
        }
    )
    return canonical, labels


@dataclass(frozen=True)
class _PilotSource:
    source: Path
    canonical: Path
    labels: Path


def _check_destination(path: Path, output_dir: Path) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Pilot output already exists: {path}")
    for parent in (path.parent, output_dir):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError(f"Pilot output parent must be a regular directory: {parent}")


def _plan_pilot(options: L2PilotOptions) -> tuple[Path, list[_PilotSource]]:
    """Validate every source and destination without creating or replacing any file."""
    output_dir = Path(options.output_dir).expanduser().resolve()
    sources: list[_PilotSource] = []
    identities: set[tuple[int, int]] = set()
    for raw_file in options.files:
        source = Path(raw_file).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError(f"Pilot input must be a regular file: {source}")
        identity = source.stat()
        key = (identity.st_dev, identity.st_ino)
        if key in identities:
            raise ValueError(f"Pilot input aliases another selected input: {source}")
        identities.add(key)
        with pq.ParquetFile(source):
            pass
        sources.append(
            _PilotSource(
                source,
                output_dir / "canonical" / source.name,
                output_dir / "labels" / f"{source.stem}.parquet",
            )
        )
    targets = [output_dir / "manifest.json"]
    for source in sources:
        targets.extend((source.canonical, source.labels))
    if len({path.resolve() for path in targets}) != len(targets):
        raise ValueError("Pilot output names collide across selected inputs")
    for target in targets:
        _check_destination(target, output_dir)
    return output_dir, sources


def _open_writer(path: Path, schema: pa.Schema, resources: ExitStack) -> pq.ParquetWriter:
    """Own and close an exclusively created file and its Parquet writer together."""
    path.parent.mkdir(parents=True, exist_ok=True)
    destination = resources.enter_context(path.open("xb"))
    return resources.enter_context(pq.ParquetWriter(destination, schema))


def _write_table(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as destination:
        pq.write_table(table, destination)


def run_l2_pilot(options: L2PilotOptions) -> dict[str, Any]:
    """Write keep-only canonical samples and sparse labels without changing raw files."""
    output_dir, sources = _plan_pilot(options)
    summary = {"keep": 0, "tag": 0, "exclude": 0}
    reports = []
    for source in sources:
        path = source.source
        kind = _kind(path)
        rows_read = 0
        canonical_path = source.canonical
        labels_path = source.labels
        canonical_writer = None
        labels_writer = None
        labels_rows = 0
        with pq.ParquetFile(path) as parquet, ExitStack() as resources:
            for batch in parquet.iter_batches(batch_size=options.batch_size):
                if options.max_rows_per_file is not None:
                    remaining = options.max_rows_per_file - rows_read
                    if remaining <= 0:
                        break
                    batch = batch.slice(0, min(batch.num_rows, remaining))
                canonical, labels = _classify_batch(batch, kind, rows_read)
                rows_read += batch.num_rows
                summary["keep"] += canonical.num_rows
                summary["tag"] += labels.filter(
                    pa.compute.equal(labels["decision"], "tag")  # ty: ignore[unresolved-attribute]
                ).num_rows
                summary["exclude"] += labels.filter(
                    pa.compute.equal(labels["decision"], "exclude")  # ty: ignore[unresolved-attribute]
                ).num_rows
                if canonical_writer is None:
                    canonical_writer = _open_writer(canonical_path, canonical.schema, resources)
                canonical_writer.write_table(canonical)
                if labels.num_rows:
                    if labels_writer is None:
                        labels_writer = _open_writer(labels_path, labels.schema, resources)
                    labels_writer.write_table(labels)
                    labels_rows += labels.num_rows
        if labels_writer is None:
            _write_table(
                labels_path,
                pa.table(
                    {
                        "row_number": pa.array([], type=pa.int64()),
                        "decision": [],
                        "reason": [],
                        "SecuCode": [],
                        "event_id": [],
                    }
                ),
            )
        reports.append(
            {
                "source": str(path),
                "kind": kind,
                "rows_read": rows_read,
                "rows_labeled": labels_rows,
            }
        )
    manifest = {
        "schema_version": "market_data_platform.l2_pilot.v1",
        "files_processed": len(reports),
        "summary": summary,
        "reports": reports,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "manifest.json").open("x", encoding="utf-8") as destination:
        destination.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest
