"""Synthetic contract tests for the repository-only knowledge pilot."""

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from quant_market_data_platform.knowledge_index import (
    build_knowledge_index,
    load_manifest_documents,
    validate_knowledge_documents,
)

FIELDS = {
    "schema_version": "knowledge/v2",
    "id": "quant-market-data-platform.dataset.a_share.daily_clean",
    "type": "dataset",
    "owner": "quant-market-data-platform",
    "status": "active",
    "last_verified": "2026-09-27",
    "source_of_truth": False,
    "authority_ref": "asset:quant-market-data-platform:a_share:daily_clean",
    "relations": [],
    "asset_key": "daily_clean",
}


def make_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "repo"
    (root / "docs/knowledge/datasets").mkdir(parents=True)
    (root / "docs/contracts.md").write_text(
        "## 数据资产键名\n| 资产键名 | 默认路径 |\n| --- | --- |\n"
        "| `daily_clean` | `assets/daily` |\n"
        "| `limit_status` | `assets/limit` |\n",
        encoding="utf-8",
    )
    first = root / "docs/knowledge/datasets/daily.md"
    second = root / "docs/knowledge/datasets/limit.md"
    write_page(first, FIELDS)
    limit = FIELDS | {
        "id": "quant-market-data-platform.dataset.a_share.limit_status",
        "asset_key": "limit_status",
        "authority_ref": "asset:quant-market-data-platform:a_share:limit_status",
    }
    write_page(second, limit)
    manifest = root / "docs/knowledge/pilot.yml"
    manifest.write_text(
        "schema_version: knowledge-pilot/v2\ndatasets:\n"
        "  - asset_key: daily_clean\n    document: docs/knowledge/datasets/daily.md\n"
        "  - asset_key: limit_status\n    document: docs/knowledge/datasets/limit.md\n",
        encoding="utf-8",
    )
    return root, first, manifest


def write_page(path: Path, fields: Mapping[str, object]) -> None:
    import yaml

    path.write_text(
        "---\n" + yaml.safe_dump(fields, sort_keys=False) + "---\n# Dataset\n", encoding="utf-8"
    )


def test_valid_dataset_documents(tmp_path: Path) -> None:
    root, first, manifest = make_repo(tmp_path)
    paths = load_manifest_documents(manifest, root)
    assert len(paths) == 2
    assert validate_knowledge_documents(paths, root) == []
    assert first in paths


@pytest.mark.parametrize(
    "change",
    [
        {"asset_key": ""},
        {"owner": "other"},
        {"type": "service"},
        {"status": "draft"},
        {"source_of_truth": True},
        {"authority_ref": "docs/contracts.md"},
        {"last_verified": "yesterday"},
        {"schema_version": "knowledge/v1"},
        {"id": "dataset.daily_clean"},
        {"unexpected": "field"},
    ],
)
def test_dataset_metadata_requires_v2_fields(tmp_path: Path, change: dict[str, object]) -> None:
    root, first, _ = make_repo(tmp_path)
    write_page(first, FIELDS | change)
    assert validate_knowledge_documents([first], root)


def test_repository_relative_document_paths_are_supported(tmp_path: Path) -> None:
    root, _, manifest = make_repo(tmp_path)
    paths = load_manifest_documents(manifest, root)
    relative_paths = [path.relative_to(root) for path in paths]
    assert validate_knowledge_documents(relative_paths, root) == []
    assert build_knowledge_index(relative_paths, root) == build_knowledge_index(paths, root)


def test_dataset_missing_field_fails(tmp_path: Path) -> None:
    root, first, _ = make_repo(tmp_path)
    fields = FIELDS.copy()
    del fields["relations"]
    write_page(first, fields)
    assert validate_knowledge_documents([first], root)


def test_duplicate_dataset_id_and_asset_key_fail(tmp_path: Path) -> None:
    root, first, _ = make_repo(tmp_path)
    second = root / "docs/knowledge/datasets/duplicate.md"
    write_page(second, FIELDS)
    issues = validate_knowledge_documents([first, second], root)
    assert any("duplicate id" in issue.message for issue in issues)
    assert any("duplicate asset_key" in issue.message for issue in issues)


def test_duplicate_yaml_keys_fail(tmp_path: Path) -> None:
    root, first, manifest = make_repo(tmp_path)
    first.write_text(
        first.read_text(encoding="utf-8").replace("type: dataset", "type: dataset\ntype: dataset"),
        encoding="utf-8",
    )
    assert any(
        "duplicate" in issue.message for issue in validate_knowledge_documents([first], root)
    )
    manifest.write_text(manifest.read_text(encoding="utf-8") + "datasets: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest_documents(manifest, root)


def test_unhashable_yaml_key_returns_issue(tmp_path: Path) -> None:
    root, first, _ = make_repo(tmp_path)
    first.write_text(
        "---\n? [schema_version, id]\n: knowledge/v2\n---\n# Dataset\n",
        encoding="utf-8",
    )
    issues = validate_knowledge_documents([first], root)
    assert len(issues) == 1
    assert issues[0].path == first
    assert "YAML key" in issues[0].message


def test_manifest_rejects_path_escape_and_duplicate(tmp_path: Path) -> None:
    root, _, manifest = make_repo(tmp_path)
    payload = (
        "schema_version: knowledge-pilot/v2\n"
        "datasets:\n"
        "  - asset_key: daily_clean\n"
        "    document: ../outside.md\n"
    )
    manifest.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match="path"):
        load_manifest_documents(manifest, root)
    manifest.write_text(
        payload.replace("../outside.md", "docs/knowledge/datasets/daily.md")
        + "  - asset_key: daily_clean\n    document: docs/knowledge/datasets/daily.md\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest_documents(manifest, root)


def test_manifest_rejects_undocumented_asset_key(tmp_path: Path) -> None:
    root, _, manifest = make_repo(tmp_path)
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("limit_status", "invented"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="contracts.md"):
        load_manifest_documents(manifest, root)


def test_manifest_key_must_be_in_stable_key_table(tmp_path: Path) -> None:
    root, _, manifest = make_repo(tmp_path)
    contracts = root / "docs/contracts.md"
    contracts.write_text(
        "| unrelated | value |\n| --- | --- |\n| `invented` | `other` |\n"
        "## 数据资产键名\n| 资产键名 | 默认路径 |\n| --- | --- |\n"
        "| `daily_clean` | `assets/daily` |\n",
        encoding="utf-8",
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("limit_status", "invented"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="contracts.md"):
        load_manifest_documents(manifest, root)


def test_build_rejects_repository_output(tmp_path: Path) -> None:
    root, _, manifest = make_repo(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts/dev/knowledge_index.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "build",
            "--manifest",
            str(manifest),
            "--output",
            str(root / "index.json"),
            "--repository-root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not (root / "index.json").exists()


def test_index_build_is_deterministic(tmp_path: Path) -> None:
    root, _, manifest = make_repo(tmp_path)
    paths = load_manifest_documents(manifest, root)
    rows = build_knowledge_index(list(reversed(paths)), root)
    assert rows == build_knowledge_index(paths, root)
    assert [row["asset_key"] for row in rows] == ["daily_clean", "limit_status"]
    assert rows[0] == FIELDS | {"path": "docs/knowledge/datasets/daily.md"}
    output = tmp_path / "index.json"
    script = Path(__file__).resolve().parents[1] / "scripts/dev/knowledge_index.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "build",
            "--manifest",
            str(manifest),
            "--output",
            str(output),
            "--repository-root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == rows
