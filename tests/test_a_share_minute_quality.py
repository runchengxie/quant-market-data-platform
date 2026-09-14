from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from market_data_platform.quality_a_share_minute import validate_fused_minute_dataset
from market_data_platform.standardize.fusion.a_share_minute import (
    write_canonical_minute_partition,
)


def _row(date: str, clock: str = "09:31:00") -> dict[str, object]:
    return {
        "ts_code": "000001.SZ",
        "trade_time": f"{date[:4]}-{date[4:6]}-{date[6:]} {clock}",
        "open": 10.0,
        "close": 10.1,
        "high": 10.2,
        "low": 9.9,
        "vol": 100.0,
        "amount": 1_000.0,
    }


def _write(root: Path, date: str, *, clock: str = "09:31:00") -> None:
    write_canonical_minute_partition(
        pd.DataFrame([_row(date, clock)]),
        root / f"trade_date={date}" / "part-00000.parquet",
    )


def test_validate_fused_minute_dataset_accepts_canonical_partition(tmp_path: Path) -> None:
    date = "20260706"
    _write(tmp_path, date)

    result = validate_fused_minute_dataset(tmp_path, expected_dates={date})

    assert result["status"] == "passed"
    assert result["partition_count"] == 1
    assert result["invalid_dates"] == []
    assert result["missing_dates"] == []


def test_validate_fused_minute_dataset_reports_semantic_and_coverage_failures(
    tmp_path: Path,
) -> None:
    date = "20260706"
    _write(tmp_path, date, clock="12:00:00")

    result = validate_fused_minute_dataset(
        tmp_path,
        expected_dates={date, "20260707"},
    )

    assert result["status"] == "failed"
    assert result["invalid_dates"] == [date]
    assert result["missing_dates"] == ["20260707"]
    assert result["partitions"][0]["issues"]["off_session_rows"] == 1


def test_range_and_trust_preserve_coverage_checks(tmp_path: Path) -> None:
    _write(tmp_path, "20260706", clock="12:00:00")
    _write(tmp_path, "20260707")
    result = validate_fused_minute_dataset(
        tmp_path,
        start_date="20260706",
        end_date="20260706",
        expected_dates={"20260708"},
        prevalidated_dates={"20260706"},
    )
    assert result["status"] == "failed"
    assert result["invalid_dates"] == []
    assert result["output_dates_in_range"] == ["20260706"]
    assert result["missing_dates"] == ["20260708"]
    assert result["orphan_dates"] == ["20260706"]
    assert result["partitions"][0]["content_validation"] == "candidate_prevalidated"


@pytest.mark.parametrize(
    "start,end",
    [
        ("20260706", None),
        (None, "20260706"),
        ("20260707", "20260706"),
        ("20260230", "20260706"),
    ],
)
def test_invalid_range_is_rejected_before_discovery(tmp_path: Path, start, end) -> None:
    with pytest.raises(ValueError):
        validate_fused_minute_dataset(tmp_path, start_date=start, end_date=end)
