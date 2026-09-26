from pathlib import Path

import pytest

from market_data_platform.tushare_refresh_part02 import _publish_file_alias


def test_published_universe_alias_replaces_stale_manifest(tmp_path: Path) -> None:
    source = tmp_path / "universe_20260924.csv"
    source.write_text("date,symbol\n20260924,000001.SZ\n")
    source_manifest = source.with_suffix(".manifest.yml")
    source_manifest.write_text("status: completed\nquery:\n  end_date: '20260924'\n")
    alias = tmp_path / "universe_latest.csv"
    alias.write_text("old data")
    manifest = alias.with_suffix(".manifest.yml")
    manifest.write_text("status: completed\nquery:\n  end_date: '20260908'\n")

    _publish_file_alias(alias_path=alias, target=source)

    assert alias.read_bytes() == source.read_bytes()
    assert manifest.read_bytes() == source_manifest.read_bytes()


def test_missing_source_manifest_preserves_published_data(tmp_path: Path) -> None:
    source = tmp_path / "universe_20260924.csv"
    source.write_text("new data")
    alias = tmp_path / "universe_latest.csv"
    alias.write_text("published data")
    manifest = alias.with_suffix(".manifest.yml")
    manifest.write_text("published metadata")

    with pytest.raises(FileNotFoundError):
        _publish_file_alias(alias_path=alias, target=source)

    assert alias.read_text() == "published data"
    assert manifest.read_text() == "published metadata"
