"""Strict, repository-only index for the knowledge/v2 dataset pilot."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from yaml.resolver import BaseResolver

_FIELDS = frozenset(
    {
        "schema_version",
        "id",
        "type",
        "owner",
        "status",
        "last_verified",
        "source_of_truth",
        "authority_ref",
        "relations",
        "asset_key",
    }
)
_OWNER = "quant-market-data-platform"
_KEY = re.compile(r"[a-z][a-z0-9_]*\Z")
_CONTRACT_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)


@dataclass(frozen=True)
class KnowledgeIssue:
    path: Path
    message: str


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueKeyLoader.add_constructor(BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _read_yaml(text: str) -> object:
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc


def _contained_path(path: Path, repository_root: Path) -> Path:
    root = repository_root.resolve()
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"invalid repository path: {path}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"repository path escapes root: {path}")
    return resolved


def _relative_existing_path(path: Path, repository_root: Path) -> Path:
    root = repository_root.resolve()
    resolved = path.resolve() if path.is_absolute() else _contained_path(path, root)
    if not resolved.is_relative_to(root):
        raise ValueError(f"repository path escapes root: {path}")
    return resolved


def _read_page(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("unterminated YAML frontmatter") from exc
    if end + 1 < len(lines) and lines[end + 1] == "---":
        raise ValueError("multiple YAML frontmatter objects")
    value = _read_yaml("\n".join(lines[1:end]))
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("frontmatter must be a YAML mapping")
    return value


def _page_errors(record: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if record.keys() != _FIELDS:
        errors.append(f"fields must be exactly {', '.join(sorted(_FIELDS))}")
    key = record.get("asset_key")
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        errors.append("asset_key must be nonempty and use lowercase letters, digits or underscores")
    for field, expected in (
        ("schema_version", "knowledge/v2"),
        ("type", "dataset"),
        ("owner", _OWNER),
        ("status", "active"),
    ):
        if record.get(field) != expected:
            errors.append(f"{field} must be {expected}")
    if record.get("source_of_truth") is not False:
        errors.append("source_of_truth must be false")
    if isinstance(key, str):
        if record.get("id") != f"{_OWNER}.dataset.a_share.{key}":
            errors.append("id must use owner.dataset.a_share.asset_key")
        if record.get("authority_ref") != f"asset:{_OWNER}:a_share:{key}":
            errors.append("authority_ref must identify the asset key")
    verified = record.get("last_verified")
    try:
        if isinstance(verified, date) and not isinstance(verified, str):
            verified = verified.isoformat()
        if not isinstance(verified, str) or date.fromisoformat(verified).isoformat() != verified:
            raise ValueError
    except ValueError:
        errors.append("last_verified must be an ISO date")
    relations = record.get("relations")
    if not isinstance(relations, list) or not all(isinstance(item, str) for item in relations):
        errors.append("relations must be a list of strings")
    return errors


def validate_knowledge_documents(
    paths: Sequence[Path], repository_root: Path
) -> list[KnowledgeIssue]:
    """Return every local v2 metadata issue for the selected Markdown documents."""
    issues: list[KnowledgeIssue] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for path in paths:
        try:
            resolved = _relative_existing_path(path, repository_root)
            record = _read_page(resolved)
        except (OSError, ValueError) as exc:
            issues.append(KnowledgeIssue(path, str(exc)))
            continue
        issues.extend(KnowledgeIssue(path, message) for message in _page_errors(record))
        identifier = record.get("id")
        key = record.get("asset_key")
        if isinstance(identifier, str):
            if identifier in seen_ids:
                issues.append(KnowledgeIssue(path, f"duplicate id: {identifier}"))
            seen_ids.add(identifier)
        if isinstance(key, str):
            if key in seen_keys:
                issues.append(KnowledgeIssue(path, f"duplicate asset_key: {key}"))
            seen_keys.add(key)
    return issues


def load_manifest_documents(manifest: Path, repository_root: Path) -> list[Path]:
    """Resolve and validate the pilot selection against public stable keys."""
    manifest_path = _relative_existing_path(manifest, repository_root)
    value = _read_yaml(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema_version", "datasets"}:
        raise ValueError("manifest must contain only schema_version and datasets")
    if value["schema_version"] != "knowledge-pilot/v2":
        raise ValueError("invalid manifest schema_version")
    entries = value["datasets"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest datasets must be nonempty")
    contract = _contained_path(Path("docs/contracts.md"), repository_root)
    contract_text = contract.read_text(encoding="utf-8")
    section = contract_text.split("## 数据资产键名\n", 1)
    if len(section) != 2:
        raise ValueError("docs/contracts.md lacks the stable-key table")
    stable_keys = set(_CONTRACT_ROW.findall(section[1].split("\n## ", 1)[0]))
    paths: list[Path] = []
    seen_paths: set[Path] = set()
    seen_keys: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"asset_key", "document"}:
            raise ValueError("manifest entries require asset_key and document only")
        key, document = entry["asset_key"], entry["document"]
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError("invalid manifest asset_key")
        if key not in stable_keys:
            raise ValueError(f"asset_key {key} is not listed in docs/contracts.md")
        if key in seen_keys:
            raise ValueError(f"duplicate manifest asset_key: {key}")
        if not isinstance(document, str):
            raise ValueError("invalid manifest document path")
        path = _contained_path(Path(document), repository_root)
        if path.suffix != ".md" or path in seen_paths:
            raise ValueError(f"duplicate or invalid manifest document path: {document}")
        record = _read_page(path)
        if record.get("asset_key") != key:
            raise ValueError(f"manifest asset_key mismatch: {document}")
        seen_keys.add(key)
        seen_paths.add(path)
        paths.append(path)
    return paths


def build_knowledge_index(paths: Sequence[Path], repository_root: Path) -> list[dict[str, object]]:
    """Build sorted JSON-compatible rows without touching production assets."""
    issues = validate_knowledge_documents(paths, repository_root)
    if issues:
        raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in issues))
    root = repository_root.resolve()
    rows: list[dict[str, object]] = []
    for path in paths:
        resolved = _relative_existing_path(path, root)
        record = _read_page(resolved)
        verified = record["last_verified"]
        if isinstance(verified, date):
            record["last_verified"] = verified.isoformat()
        rows.append(record | {"path": resolved.relative_to(root).as_posix()})
    return sorted(rows, key=lambda row: str(row["id"]))
