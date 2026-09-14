from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.repair_data_root_links import collect_rewrites, main


def test_collect_rewrites_only_changes_old_root_targets(tmp_path: Path) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    link_root = new_root / "assets"
    target = new_root / "published" / "part.parquet"
    target.parent.mkdir(parents=True)
    target.write_text("data", encoding="utf-8")
    link_root.mkdir(parents=True)
    (link_root / "old-link").symlink_to(old_root / "published" / "part.parquet")
    (link_root / "new-link").symlink_to(target)

    rewrites = collect_rewrites(new_root, old_root, new_root)

    assert len(rewrites) == 1
    assert rewrites[0].path == link_root / "old-link"
    assert rewrites[0].new_target == str(target)


def test_main_requires_apply_and_writes_manifest(tmp_path: Path, capsys) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    target = new_root / "published" / "part.parquet"
    target.parent.mkdir(parents=True)
    target.write_text("data", encoding="utf-8")
    link = new_root / "link"
    link.symlink_to(old_root / "published" / "part.parquet")
    manifest = tmp_path / "migration.json"

    assert (
        main(
            [
                "--root",
                str(new_root),
                "--old-root",
                str(old_root),
                "--new-root",
                str(new_root),
                "--manifest",
                str(manifest),
            ]
        )
        == 0
    )
    assert link.is_symlink()
    assert link.readlink() == old_root / "published" / "part.parquet"
    assert "dry-run" in capsys.readouterr().out

    assert (
        main(
            [
                "--root",
                str(new_root),
                "--old-root",
                str(old_root),
                "--new-root",
                str(new_root),
                "--manifest",
                str(manifest),
                "--apply",
            ]
        )
        == 0
    )
    assert link.readlink() == target
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["rewritten_links"] == 1
