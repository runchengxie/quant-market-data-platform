from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.operations.build_a_share_report_input_snapshot import (
    SNAPSHOT_SCHEMA,
    SnapshotBuildError,
    build_snapshot,
)


def _version(root: Path, dataset: str, name: str, end_date: str) -> Path:
    path = root / "assets" / "tushare" / "a_share" / dataset / name
    (path / "data" / "trade_date=20260907").mkdir(parents=True)
    (path / "data" / "trade_date=20260907" / "part.parquet").write_bytes(b"fixture")
    (path / "manifest.yml").write_text(
        "\n".join(
            [
                f"dataset: {dataset}",
                "status: completed",
                "query:",
                "  start_date: '20260101'",
                f"  end_date: '{end_date}'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _flat_version(root: Path, dataset: str, name: str, end_date: str) -> Path:
    path = root / "assets" / "tushare" / "a_share" / dataset / name
    (path / "data").mkdir(parents=True)
    (path / "data" / "000001.SZ.parquet").write_bytes(b"flat fixture")
    (path / "manifest.yml").write_text(
        "\n".join(
            [
                f"dataset: {dataset}",
                "status: completed",
                "query:",
                "  start_date: '20260101'",
                f"  end_date: '{end_date}'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_build_snapshot_selects_immutable_versions_and_writes_receipt(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    _version(
        artifacts_root,
        "daily",
        "a_share_all_daily_20260907",
        "20260907",
    )
    _version(
        artifacts_root,
        "moneyflow_ths",
        "a_share_all_moneyflow_ths_20260907",
        "20260907",
    )

    receipt = build_snapshot(
        artifacts_root=artifacts_root,
        target_date="20260907",
        output_root=tmp_path / "snapshot",
        datasets=("daily", "moneyflow_ths"),
    )

    assert receipt["schema_version"] == SNAPSHOT_SCHEMA
    assert receipt["target_date"] == "20260907"
    assert receipt["snapshot_id"]
    for dataset in ("daily", "moneyflow_ths"):
        link = (
            tmp_path / "snapshot" / "assets" / "tushare" / "a_share" / dataset / f"{dataset}_latest"
        )
        assert link.is_symlink()
        assert link.resolve().is_dir()
        assert receipt["datasets"][dataset]["version_date"] == "20260907"
    receipt_path = tmp_path / "snapshot" / "snapshot_receipt.json"
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt


def test_build_snapshot_fails_closed_when_dataset_version_is_missing(tmp_path: Path) -> None:
    _version(
        tmp_path / "artifacts",
        "daily",
        "a_share_all_daily_20260907",
        "20260907",
    )
    with pytest.raises(SnapshotBuildError, match="moneyflow_ths"):
        build_snapshot(
            artifacts_root=tmp_path / "artifacts",
            target_date="20260907",
            output_root=tmp_path / "snapshot",
            datasets=("daily", "moneyflow_ths"),
        )


def test_build_snapshot_prefers_version_with_target_partition_over_flat_version(
    tmp_path: Path,
) -> None:
    artifacts_root = tmp_path / "artifacts"
    _flat_version(
        artifacts_root,
        "daily",
        "a_share_all_daily_flat_20260907",
        "20260907",
    )
    partitioned = _version(
        artifacts_root,
        "daily",
        "a_share_all_daily_partitioned_20260908",
        "20260908",
    )

    receipt = build_snapshot(
        artifacts_root=artifacts_root,
        target_date="20260907",
        output_root=tmp_path / "snapshot",
        datasets=("daily",),
    )

    assert receipt["datasets"]["daily"]["source_path"].endswith(partitioned.name)
