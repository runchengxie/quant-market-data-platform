from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts/operations/relocate_data_root.py"
    spec = importlib.util.spec_from_file_location("relocate_data_root", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_find_references_does_not_follow_symlinks(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "source.json"
    source.write_text("/old/data/file.parquet", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(source)

    assert module.find_references(tmp_path, "/old/data") == [source]


def test_rewrite_file_preserves_content_and_replaces_all_paths(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "receipt.json"
    path.write_text("/old/data/a\n/old/data/b\n", encoding="utf-8")

    assert module.rewrite_file(path, "/old/data", "/new/data") == 2
    assert path.read_text(encoding="utf-8") == "/new/data/a\n/new/data/b\n"
