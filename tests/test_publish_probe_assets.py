from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest
import yaml

_MODULE_PATH = Path(__file__).parents[1] / "scripts/operations/publish_probe_assets.py"
_SPEC = importlib.util.spec_from_file_location("publish_probe_assets", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
ProbePublicationError = _MODULE.ProbePublicationError
publish_asset = _MODULE.publish_asset


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "probe"
    data = source / "data"
    data.mkdir(parents=True)
    pd.DataFrame({"trade_date": ["20260908"], "value": [1]}).to_parquet(
        data / "part.parquet", index=False
    )
    (source / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "dataset": "demo",
                "status": "completed",
                "totals": {"rows": 1, "files": 1},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return source


def test_publish_asset_moves_probe_and_preserves_old_alias(tmp_path: Path) -> None:
    source = _source(tmp_path)
    parent = tmp_path / "assets" / "demo"
    parent.mkdir(parents=True)
    alias = parent / "demo_latest"
    old = alias / "old.txt"
    old.parent.mkdir()
    old.write_text("old", encoding="utf-8")
    target = parent / "demo_20260908"
    quarantine = parent / "demo_quarantine_20260908"

    result = publish_asset(
        source=source,
        target=target,
        alias=alias,
        quarantine=quarantine,
    )

    assert result["status"] == "published"
    assert target.is_dir()
    assert quarantine.is_dir()
    assert alias.is_symlink()
    assert alias.resolve() == target
    manifest = yaml.safe_load((target / "manifest.yml").read_text(encoding="utf-8"))
    assert manifest["output_dir"] == str(target)
    assert manifest["snapshot_name"] == target.name


def test_publish_asset_rejects_manifest_file_mismatch(tmp_path: Path) -> None:
    source = _source(tmp_path)
    manifest_path = source / "manifest.yml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["totals"]["files"] = 2
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    with pytest.raises(ProbePublicationError, match="file count"):
        publish_asset(
            source=source,
            target=tmp_path / "target",
            alias=tmp_path / "alias",
            quarantine=tmp_path / "quarantine",
        )
