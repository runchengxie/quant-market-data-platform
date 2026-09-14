from __future__ import annotations

from pathlib import Path

import pytest

from market_data_platform.providers._io import _prepare_output_dir


def test_prepare_output_dir_rejects_existing_parquet(tmp_path: Path) -> None:
    output = tmp_path / "asset"
    output.mkdir()
    (output / "part.parquet").write_bytes(b"old")

    with pytest.raises(FileExistsError, match="non-empty mirror output"):
        _prepare_output_dir(output)


def test_prepare_output_dir_allows_explicit_resume(tmp_path: Path) -> None:
    output = tmp_path / "asset"
    output.mkdir()
    (output / "part.parquet").write_bytes(b"old")

    assert _prepare_output_dir(output, allow_existing=True) == output.resolve()
