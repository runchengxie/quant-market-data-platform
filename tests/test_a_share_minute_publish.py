from __future__ import annotations

import json
from pathlib import Path

import pytest

from market_data_platform.publish import json_manifest


def test_write_json_manifest_is_utf8_json_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / "metadata" / "minute.json"

    json_manifest.write_json_manifest(path, {"schema_version": "x.v1", "status": "passed"})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": "x.v1",
        "status": "passed",
    }
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_write_json_manifest_preserves_existing_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "metadata" / "minute.json"
    json_manifest.write_json_manifest(path, {"status": "old"})

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise RuntimeError("simulated replace failure")

    monkeypatch.setattr(json_manifest.os, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="simulated replace failure"):
        json_manifest.write_json_manifest(path, {"status": "new"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "old"}
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))
