from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

_MODULE_PATH = (
    Path(__file__).parents[1] / "scripts" / "operations" / "materialize_current_versions.py"
)
_SPEC = importlib.util.spec_from_file_location("materialize_current_versions", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
materialize_dataset = _MODULE.materialize_dataset


def _write_asset(root: Path, *, dataset: str, rows: int) -> Path:
    asset = root / "assets" / "tushare" / "a_share" / dataset
    latest = asset / f"a_share_all_{dataset}_latest"
    latest.mkdir(parents=True)
    (latest / "data").mkdir()
    (latest / "data" / "part.parquet").write_bytes(b"data")
    (latest / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "status": "completed",
                "query": {"end_date": "20260907"},
                "totals": {"rows": rows},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return latest


def test_materialize_keeps_compatibility_alias(tmp_path: Path) -> None:
    latest = _write_asset(tmp_path, dataset="limit_step", rows=1)

    result = materialize_dataset(tmp_path, dataset="limit_step", fallback_date="20260908")

    version = tmp_path / "assets/tushare/a_share/limit_step/a_share_all_limit_step_20260907"
    assert result["status"] == "materialized"
    assert version.is_dir()
    assert latest.is_symlink()
    assert latest.resolve() == version
    manifest = yaml.safe_load((version / "manifest.yml").read_text(encoding="utf-8"))
    assert manifest["output_dir"] == str(version)
    assert manifest["snapshot_name"] == version.name


def test_materialize_rejects_empty_asset(tmp_path: Path) -> None:
    _write_asset(tmp_path, dataset="margin", rows=0)

    with pytest.raises(ValueError, match="no rows"):
        materialize_dataset(tmp_path, dataset="margin", fallback_date="20260908")
