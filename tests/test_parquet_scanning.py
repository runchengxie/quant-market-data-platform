from __future__ import annotations

from typing import Any, cast

import pytest

from market_data_platform.parquet_scanning import ParquetBatchScanner
from market_data_platform.runtime_memory import MemoryPolicy, MemorySnapshot


def test_scanner_projects_columns_and_reads_multiple_batches(tmp_path):
    pd = pytest.importorskip("pandas")
    path = tmp_path / "part.parquet"
    pd.DataFrame({"symbol": ["A", "B", "C"], "unused": [1, 2, 3]}).to_parquet(path, index=False)
    scanner = ParquetBatchScanner(
        columns=["symbol", "missing"],
        batch_rows=2,
        memory_policy=MemoryPolicy(
            soft_available_mb=None,
            hard_available_mb=None,
            min_batch_rows=1,
            target_batch_rows=2,
            max_batch_rows=2,
        ),
    )

    frames = [frame for _path, frame in scanner.iter_frames([path])]

    assert [column for frame in frames for column in frame.columns] == ["symbol", "symbol"]
    assert sum(len(frame) for frame in frames) == 3
    telemetry: dict[str, Any] = cast(dict[str, Any], scanner.telemetry.to_dict())
    assert telemetry["batches_scanned"] == 2
    assert telemetry["projected_columns"] == ["symbol", "missing"]
    assert telemetry["missing_columns"][str(path.resolve())] == ["missing"]


def test_memory_policy_reduces_rows_under_soft_pressure():
    policy = MemoryPolicy(
        soft_available_mb=2048,
        hard_available_mb=1024,
        min_batch_rows=100,
        target_batch_rows=1000,
        max_batch_rows=2000,
    )

    rows = policy.choose_batch_rows(
        MemorySnapshot(available_mb=1500, rss_mb=100),
        current_rows=1000,
    )

    assert rows == 500


def test_memory_policy_aborts_under_hard_pressure():
    policy = MemoryPolicy(
        soft_available_mb=2048,
        hard_available_mb=1024,
        min_batch_rows=100,
        target_batch_rows=1000,
        max_batch_rows=2000,
    )

    with pytest.raises(MemoryError, match="rss_mb=100"):
        policy.choose_batch_rows(MemorySnapshot(available_mb=900, rss_mb=100))


def test_memory_policy_uses_bounded_fallback_without_telemetry():
    policy = MemoryPolicy(
        min_batch_rows=100,
        target_batch_rows=1000,
        max_batch_rows=2000,
    )

    rows = policy.choose_batch_rows(MemorySnapshot(available_mb=None, rss_mb=None))

    assert rows == 1000
