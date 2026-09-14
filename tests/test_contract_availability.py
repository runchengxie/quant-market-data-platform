from __future__ import annotations

from pathlib import Path

import yaml

from market_data_platform.contract import describe_current_path


def test_missing_current_asset_has_explicit_unavailable_state(tmp_path: Path) -> None:
    result = describe_current_path(tmp_path / "missing.parquet")

    assert result["exists"] is False
    assert result["availability"] == "unavailable"
    assert result["availability_reason"] == "No published asset exists at the configured path."
    assert result["next_action"] == "publish_or_retire"


def test_existing_current_asset_has_available_state(tmp_path: Path) -> None:
    path = tmp_path / "asset.parquet"
    path.write_bytes(b"asset")

    result = describe_current_path(path)

    assert result["exists"] is True
    assert result["availability"] == "available"
    assert result["availability_reason"] is None
    assert result["next_action"] is None


def test_empty_completed_asset_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "asset"
    path.mkdir()
    (path / "manifest.yml").write_text(
        yaml.safe_dump({"status": "completed", "totals": {"rows": 0}}),
        encoding="utf-8",
    )

    result = describe_current_path(path)

    assert result["exists"] is True
    assert result["availability"] == "unavailable"
    assert result["availability_reason"] == "Published manifest contains zero rows."
    assert result["next_action"] == "refresh_or_retire"


def test_directory_without_manifest_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "asset"
    path.mkdir()

    result = describe_current_path(path)

    assert result["availability"] == "unavailable"
    assert result["availability_reason"] == "Published directory has no manifest."
    assert result["next_action"] == "republish_with_manifest"


def test_manifest_file_count_mismatch_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "asset"
    path.mkdir()
    (path / "data").mkdir()
    (path / "data" / "part.parquet").write_bytes(b"not a parquet file")
    (path / "manifest.yml").write_text(
        yaml.safe_dump({"status": "completed", "totals": {"rows": 1, "files": 0}}),
        encoding="utf-8",
    )

    result = describe_current_path(path)

    assert result["availability"] == "unavailable"
    assert result["availability_reason"] == (
        "Manifest file count does not match stored Parquet files."
    )
    assert result["next_action"] == "republish_consistently"
