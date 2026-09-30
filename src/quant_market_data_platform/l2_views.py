"""Low-copy, canonical views over raw L2 parquet files.

The raw file remains the source of truth.  This module projects only the
fields needed by consumers and joins sparse quality labels by source row
number while streaming record batches.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

_CANONICAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "deal": (
        "SecuCode",
        "TradingDay",
        "DealTime",
        "DealID",
        "Price",
        "Volume",
        "Side",
        "BuyID",
        "SellID",
    ),
    "order": (
        "SecuCode",
        "TradingDay",
        "OrderTime",
        "OrderID",
        "DBOrderID",
        "Price",
        "LastPrice",
        "Volume",
        "OrderType",
    ),
    "snapshot": (
        "SecuCode",
        "TradingDay",
        "TickTime",
        "Price",
        "Volume",
        "DealNum",
        *(f"BidPrice{i}" for i in range(1, 11)),
        *(f"BidVolume{i}" for i in range(1, 11)),
        *(f"AskPrice{i}" for i in range(1, 11)),
        *(f"AskVolume{i}" for i in range(1, 11)),
    ),
}

_ALIASES: dict[str, tuple[str, ...]] = {
    "SecuCode": ("SecuCode", "ticker"),
    "TradingDay": ("TradingDay",),
    "DealTime": ("DealTime", "time_ms"),
    "OrderTime": ("OrderTime", "time_ms"),
    "TickTime": ("TickTime", "time_ms"),
}


def canonical_columns(kind: str) -> tuple[str, ...]:
    """Return the stable, minimal consumer schema for one L2 stream."""
    try:
        return _CANONICAL_COLUMNS[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported L2 kind: {kind!r}") from exc


def _resolve_projection(kind: str, available: set[str], requested: Sequence[str]) -> list[str]:
    projection: list[str] = []
    missing: list[str] = []
    for name in requested:
        candidates = _ALIASES.get(name, (name,))
        source = next((candidate for candidate in candidates if candidate in available), None)
        if source is None:
            missing.append(name)
        elif source not in projection:
            projection.append(source)
    if missing:
        raise ValueError(f"{kind} parquet is missing canonical columns: {', '.join(missing)}")
    return projection


def _labels(path: Path | None) -> dict[int, tuple[str, str]]:
    if path is None or not path.exists():
        return {}
    table = pq.read_table(path, columns=["row_number", "decision", "reason"])
    return {
        int(row): (str(decision), str(reason))
        for row, decision, reason in zip(
            table["row_number"].to_pylist(),
            table["decision"].to_pylist(),
            table["reason"].to_pylist(),
            strict=True,
        )
    }


def iter_l2_batches(  # noqa: PLR0913
    path: str | Path,
    kind: str,
    *,
    columns: Sequence[str] | None = None,
    labels_path: str | Path | None = None,
    batch_size: int = 262_144,
    include_tagged: bool = False,
) -> Iterator[pa.RecordBatch]:
    """Stream a canonical projection and optionally attach/filter sparse labels.

    By default only rows not marked ``tag`` or ``exclude`` are yielded.  Set
    ``include_tagged=True`` to preserve every source row and inspect its label.
    The source row order is preserved; no in-place sorting or mutation occurs.
    """
    source = Path(path)
    parquet = pq.ParquetFile(source)
    requested = tuple(columns or canonical_columns(kind))
    projection = _resolve_projection(kind, set(parquet.schema_arrow.names), requested)
    label_map = _labels(Path(labels_path) if labels_path is not None else None)
    offset = 0
    for batch in parquet.iter_batches(columns=projection, batch_size=batch_size):
        arrays: list[pa.Array] = []
        names: list[str] = []
        for canonical in requested:
            source_name = next(
                candidate
                for candidate in _ALIASES.get(canonical, (canonical,))
                if candidate in batch.schema.names
            )
            arrays.append(batch.column(source_name))
            names.append(canonical)
        decisions: list[str | None] = []
        reasons: list[str | None] = []
        keep: list[bool] = []
        for row_number in range(offset, offset + batch.num_rows):
            decision, reason = label_map.get(row_number, (None, None))
            decisions.append(decision)
            reasons.append(reason)
            keep.append(include_tagged or decision not in {"tag", "exclude"})
        offset += batch.num_rows
        arrays.extend((pa.array(decisions), pa.array(reasons)))
        names.extend(("quality_decision", "quality_reason"))
        output = pa.RecordBatch.from_arrays(arrays, names=names)
        if not include_tagged:
            output = output.filter(pa.array(keep))
        yield output
