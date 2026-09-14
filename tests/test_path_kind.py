from __future__ import annotations

from pathlib import Path

from market_data_platform.contract import path_kind


def test_path_kind_reports_file_directory_and_missing(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    file_path = tmp_path / "file"
    file_path.write_text("data", encoding="utf-8")

    assert path_kind(directory) == "directory"
    assert path_kind(file_path) == "file"
    assert path_kind(tmp_path / "missing") == "missing"


def test_path_kind_accepts_none() -> None:
    assert path_kind(None) is None
