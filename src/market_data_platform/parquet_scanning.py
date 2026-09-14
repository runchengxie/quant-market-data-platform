from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.runtime_memory import MemoryPolicy, MemorySnapshot

MAX_TELEMETRY_SAMPLES = 40


@dataclass
class ParquetScanTelemetry:
    configured_batch_rows: int
    projected_columns: list[str] | None
    files_scanned: int = 0
    batches_scanned: int = 0
    rows_scanned: int = 0
    effective_batch_rows: set[int] = field(default_factory=set)
    missing_columns: dict[str, list[str]] = field(default_factory=dict)
    memory_samples: list[dict[str, float | int | None | str]] = field(default_factory=list)
    flush_reasons: list[dict[str, float | int | None | str]] = field(default_factory=list)
    estimated_bytes_per_row: float | None = None

    def record_memory(
        self,
        snapshot: MemorySnapshot,
        *,
        path: Path,
        stage: str,
        batch_rows: int,
    ) -> None:
        if len(self.memory_samples) >= MAX_TELEMETRY_SAMPLES:
            return
        self.memory_samples.append(
            {
                "path": str(path),
                "stage": stage,
                "available_mb": snapshot.available_mb,
                "rss_mb": snapshot.rss_mb,
                "batch_rows": batch_rows,
            }
        )

    def record_soft_pressure(
        self,
        snapshot: MemorySnapshot,
        *,
        path: Path,
        stage: str,
        previous_rows: int,
        next_rows: int,
    ) -> None:
        if len(self.flush_reasons) >= MAX_TELEMETRY_SAMPLES:
            return
        self.flush_reasons.append(
            {
                "reason": "soft_memory_pressure",
                "path": str(path),
                "stage": stage,
                "available_mb": snapshot.available_mb,
                "rss_mb": snapshot.rss_mb,
                "previous_batch_rows": previous_rows,
                "next_batch_rows": next_rows,
            }
        )

    def record_batch(self, table: pa.Table, *, effective_batch_rows: int) -> None:
        rows = int(table.num_rows)
        self.batches_scanned += 1
        self.rows_scanned += rows
        self.effective_batch_rows.add(int(effective_batch_rows))
        if rows:
            observed = float(table.nbytes) / rows
            if self.estimated_bytes_per_row is None:
                self.estimated_bytes_per_row = observed
            else:
                self.estimated_bytes_per_row = (self.estimated_bytes_per_row + observed) / 2

    def to_dict(self) -> dict[str, object]:
        return {
            "files_scanned": self.files_scanned,
            "batches_scanned": self.batches_scanned,
            "rows_scanned": self.rows_scanned,
            "projected_columns": self.projected_columns,
            "configured_batch_rows": self.configured_batch_rows,
            "effective_batch_rows": sorted(self.effective_batch_rows),
            "estimated_bytes_per_row": (
                round(self.estimated_bytes_per_row, 2)
                if self.estimated_bytes_per_row is not None
                else None
            ),
            "missing_columns": self.missing_columns,
            "memory_samples": self.memory_samples,
            "flush_reasons": self.flush_reasons,
        }


class ParquetBatchScanner:
    def __init__(
        self,
        *,
        columns: Sequence[str] | None = None,
        batch_rows: int | None = None,
        memory_policy: MemoryPolicy | None = None,
        stage: str = "parquet_scan",
    ) -> None:
        self.columns = list(dict.fromkeys(columns)) if columns is not None else None
        self.memory_policy = memory_policy or MemoryPolicy()
        self.batch_rows = max(1, int(batch_rows or self.memory_policy.target_batch_rows))
        self.stage = stage
        self.telemetry = ParquetScanTelemetry(
            configured_batch_rows=self.batch_rows,
            projected_columns=self.columns,
        )

    def iter_tables(self, paths: Sequence[str | Path]) -> Iterator[tuple[Path, pa.Table]]:
        effective_rows = self.batch_rows
        for raw_path in paths:
            path = Path(raw_path).expanduser().resolve()
            parquet_file = pq.ParquetFile(path)
            available = set(parquet_file.schema_arrow.names)
            projected = (
                [column for column in self.columns if column in available]
                if self.columns is not None
                else None
            )
            if self.columns is not None:
                missing = sorted(set(self.columns) - available)
                if missing:
                    self.telemetry.missing_columns[str(path)] = missing
            self.telemetry.files_scanned += 1
            for row_group in range(parquet_file.num_row_groups):
                snapshot = self.memory_policy.require_safe(
                    label=f"{self.stage} {path.name} row_group={row_group}"
                )
                next_rows = self.memory_policy.choose_batch_rows(
                    snapshot,
                    current_rows=effective_rows,
                    estimated_bytes_per_row=self.telemetry.estimated_bytes_per_row,
                )
                if next_rows < effective_rows:
                    self.telemetry.record_soft_pressure(
                        snapshot,
                        path=path,
                        stage=self.stage,
                        previous_rows=effective_rows,
                        next_rows=next_rows,
                    )
                effective_rows = next_rows
                self.telemetry.record_memory(
                    snapshot,
                    path=path,
                    stage=self.stage,
                    batch_rows=effective_rows,
                )
                for batch in parquet_file.iter_batches(
                    batch_size=effective_rows,
                    row_groups=[row_group],
                    columns=projected,
                ):
                    snapshot = self.memory_policy.require_safe(
                        label=f"{self.stage} {path.name} batch"
                    )
                    self.telemetry.record_memory(
                        snapshot,
                        path=path,
                        stage=self.stage,
                        batch_rows=effective_rows,
                    )
                    table = pa.Table.from_batches([batch])
                    self.telemetry.record_batch(table, effective_batch_rows=effective_rows)
                    yield path, table

    def iter_frames(self, paths: Sequence[str | Path]) -> Iterator[tuple[Path, pd.DataFrame]]:
        for path, table in self.iter_tables(paths):
            yield path, table.to_pandas()
