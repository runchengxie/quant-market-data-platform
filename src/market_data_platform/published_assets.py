"""Read-only access to assets selected by a current-asset contract."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from market_data_platform.paths import (
    current_contract_path,
    normalize_market,
    normalize_provider,
    resolve_artifacts_root,
)


class PublishedAssetError(ValueError):
    """Base error for invalid or unavailable published assets."""


class PublishedAssetContractError(PublishedAssetError):
    """Raised when a current-asset contract is malformed or inconsistent."""


class PublishedAssetPathError(PublishedAssetError):
    """Raised when a contract path escapes its declared artifacts root."""


class PublishedAssetUnavailableError(PublishedAssetError):
    """Raised when a requested asset has not been published completely."""


def _mapping(value: object, *, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PublishedAssetContractError(f"{context} must be a mapping.")
    return {str(key): item for key, item in value.items()}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise PublishedAssetContractError(
        f"Published metadata contains an unsupported value type: {type(value).__name__}."
    )


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _ensure_contained(path: Path, root: Path, *, field: str) -> None:
    if not path.is_relative_to(root):
        raise PublishedAssetPathError(f"{field} escapes artifacts root {root}: {path}")


def _safe_path(
    value: object,
    *,
    root: Path,
    field: str,
    must_exist: bool,
) -> tuple[Path, Path]:
    text = str(value or "").strip()
    if not text:
        raise PublishedAssetContractError(f"{field} must be a non-empty path.")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    lexical = _absolute_without_resolving(candidate)
    _ensure_contained(lexical, root, field=field)
    try:
        resolved = lexical.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise PublishedAssetUnavailableError(f"{field} does not exist: {lexical}") from exc
    _ensure_contained(resolved, root, field=f"resolved {field}")
    return lexical, resolved


def _load_json_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishedAssetContractError(
            f"Cannot read current-asset contract {path}: {exc}"
        ) from exc
    return _mapping(payload, context=f"Current-asset contract {path}")


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PublishedAssetContractError(f"Cannot read asset manifest {path}: {exc}") from exc
    return _mapping(payload, context=f"Asset manifest {path}")


def _contract_artifacts_root(
    contract_path: Path,
    header: Mapping[str, Any],
    requested_root: str | Path | None,
) -> Path:
    recorded_text = str(header.get("artifacts_root") or "").strip()
    if not recorded_text:
        raise PublishedAssetContractError(
            "Current-asset contract.contract.artifacts_root must be a non-empty path."
        )
    recorded_path = Path(recorded_text).expanduser()
    if not recorded_path.is_absolute():
        raise PublishedAssetContractError(
            "Current-asset contract.contract.artifacts_root must be absolute."
        )
    root = (
        resolve_artifacts_root(requested_root)
        if requested_root is not None
        else recorded_path.resolve()
    )
    _ensure_contained(contract_path, root, field="current-asset contract path")
    recorded_root = recorded_path.resolve()
    if recorded_root != root:
        raise PublishedAssetContractError(
            "Current-asset contract artifacts_root does not match the requested root: "
            f"{recorded_root} != {root}"
        )
    return root


def _contract_identity(header: Mapping[str, Any]) -> tuple[str, str, str, int]:
    required = {
        field: str(header.get(field) or "").strip() for field in ("name", "market", "provider")
    }
    for field, value in required.items():
        if not value:
            raise PublishedAssetContractError(
                f"Current-asset contract.contract.{field} is required."
            )
    market = normalize_market(required["market"])
    provider = normalize_provider(required["provider"], market=market)
    version = header.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PublishedAssetContractError(
            "Current-asset contract.contract.version must be a positive integer."
        )
    return required["name"], market, provider, version


def _validated_contract_assets(raw_assets: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    validated: dict[str, Mapping[str, Any]] = {}
    for raw_key, raw_entry in raw_assets.items():
        key = str(raw_key).strip()
        if not key:
            raise PublishedAssetContractError("Current-asset contract has an empty asset key.")
        entry = _mapping(raw_entry, context=f"Current-asset contract.assets.{key}")
        validated[key] = _freeze(entry)
    return MappingProxyType(validated)


@dataclass(frozen=True)
class PublishedAssetRef:
    """A validated reference to one immutable publication selected as current.

    ``manifest`` is the complete manifest read from disk. ``content_fingerprint``
    is a canonical hash of that manifest, while ``manifest_sha256`` fingerprints
    its exact serialized bytes. The API deliberately does not scan large Parquet
    trees merely to construct a reference.
    """

    key: str
    market: str
    provider: str
    as_of: str | None
    artifacts_root: Path
    alias_path: Path
    resolved_path: Path
    manifest_path: Path
    manifest: Mapping[str, Any]
    contract_sha256: str
    manifest_sha256: str
    content_fingerprint: str

    @property
    def lineage(self) -> Mapping[str, Any]:
        """Return manifest lineage without interpreting provider-specific fields."""

        value = self.manifest.get("lineage", {})
        return value if isinstance(value, Mapping) else MappingProxyType({})

    def resolve_data_path(self, relative_path: str | Path = ".") -> Path:
        """Resolve an explicitly named data path while enforcing root containment."""

        relative = Path(relative_path)
        if relative.is_absolute():
            raise PublishedAssetPathError(
                f"Asset data path must be relative to {self.resolved_path}: {relative}"
            )
        if self.resolved_path.is_file():
            if relative == Path("."):
                return self.resolved_path
            raise PublishedAssetPathError(
                f"Single-file asset '{self.key}' does not contain relative data paths: {relative}"
            )
        base = self.resolved_path
        lexical = _absolute_without_resolving(base / relative)
        _ensure_contained(lexical, base, field=f"asset '{self.key}' data path")
        try:
            resolved = lexical.resolve(strict=True)
        except FileNotFoundError as exc:
            raise PublishedAssetUnavailableError(
                f"Asset '{self.key}' data path does not exist: {lexical}"
            ) from exc
        _ensure_contained(resolved, base.resolve(), field=f"resolved asset '{self.key}' data path")
        _ensure_contained(
            resolved,
            self.artifacts_root,
            field=f"resolved asset '{self.key}' data path",
        )
        return resolved

    def provenance_dict(self) -> dict[str, Any]:
        """Return JSON-serializable source provenance for downstream metadata."""

        return {
            "asset_key": self.key,
            "market": self.market,
            "provider": self.provider,
            "as_of": self.as_of,
            "alias_path": str(self.alias_path),
            "resolved_path": str(self.resolved_path),
            "manifest_path": str(self.manifest_path),
            "contract_sha256": self.contract_sha256,
            "manifest_sha256": self.manifest_sha256,
            "content_fingerprint": self.content_fingerprint,
            "schema_version": self.manifest.get("schema_version"),
            "dataset": self.manifest.get("dataset"),
            "lineage": _json_value(self.lineage),
        }


@dataclass(frozen=True)
class PublishedAssetContract:
    """Validated, framework-neutral view of a current-asset contract."""

    path: Path
    artifacts_root: Path
    name: str
    market: str
    provider: str
    version: int
    contract_sha256: str
    payload: Mapping[str, Any]
    assets: Mapping[str, Mapping[str, Any]]

    @classmethod
    def load_current(
        cls,
        artifacts_root: str | Path | None = None,
        *,
        market: str = "a_share",
    ) -> PublishedAssetContract:
        """Load the canonical current contract for ``market`` from an artifacts root."""

        root = resolve_artifacts_root(artifacts_root)
        return cls.load(current_contract_path(root, market=market), artifacts_root=root)

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        artifacts_root: str | Path | None = None,
    ) -> PublishedAssetContract:
        """Load and validate a current-asset contract from an explicit path."""

        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = candidate.absolute()
        try:
            contract_path = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise PublishedAssetContractError(
                f"Current-asset contract does not exist: {candidate}"
            ) from exc
        payload = _load_json_mapping(contract_path)
        header = _mapping(payload.get("contract"), context="Current-asset contract.contract")
        raw_assets = _mapping(payload.get("assets"), context="Current-asset contract.assets")
        root = _contract_artifacts_root(contract_path, header, artifacts_root)
        name, market, provider, version = _contract_identity(header)

        return cls(
            path=contract_path,
            artifacts_root=root,
            name=name,
            market=market,
            provider=provider,
            version=version,
            contract_sha256=_sha256_file(contract_path),
            payload=_freeze(payload),
            assets=_validated_contract_assets(raw_assets),
        )

    @property
    def asset_keys(self) -> tuple[str, ...]:
        """Return all keys recorded by the current contract, including missing candidates."""

        return tuple(sorted(self.assets))

    def require_assets(self, keys: Iterable[str]) -> tuple[PublishedAssetRef, ...]:
        """Resolve several required assets in caller-specified order."""

        return tuple(self.asset(key) for key in keys)

    def asset(self, key: str) -> PublishedAssetRef:
        """Load and validate one published asset and its complete manifest."""

        normalized_key = str(key).strip()
        try:
            entry = self.assets[normalized_key]
        except KeyError as exc:
            available = ", ".join(self.asset_keys)
            raise KeyError(
                f"Asset '{normalized_key}' is not present in {self.path}. Available: {available}"
            ) from exc
        if entry.get("exists") is False:
            raise PublishedAssetUnavailableError(
                f"Asset '{normalized_key}' is recorded as unavailable in {self.path}."
            )

        alias_path, resolved_alias = _safe_path(
            entry.get("alias_path"),
            root=self.artifacts_root,
            field=f"assets.{normalized_key}.alias_path",
            must_exist=True,
        )
        declared_resolved = entry.get("resolved_path")
        if declared_resolved is not None:
            _, validated_declared = _safe_path(
                declared_resolved,
                root=self.artifacts_root,
                field=f"assets.{normalized_key}.resolved_path",
                must_exist=True,
            )
            if validated_declared != resolved_alias:
                raise PublishedAssetContractError(
                    f"Asset '{normalized_key}' resolved_path does not match alias target: "
                    f"{validated_declared} != {resolved_alias}"
                )

        _, manifest_path = _safe_path(
            entry.get("manifest_path"),
            root=self.artifacts_root,
            field=f"assets.{normalized_key}.manifest_path",
            must_exist=True,
        )
        if not manifest_path.is_file():
            raise PublishedAssetUnavailableError(
                f"Asset '{normalized_key}' manifest is not a file: {manifest_path}"
            )
        manifest_payload = _load_yaml_mapping(manifest_path)
        if not manifest_payload:
            raise PublishedAssetContractError(
                f"Asset '{normalized_key}' manifest must not be empty: {manifest_path}"
            )

        as_of_text = str(entry.get("as_of") or "").strip()
        return PublishedAssetRef(
            key=normalized_key,
            market=self.market,
            provider=self.provider,
            as_of=as_of_text or None,
            artifacts_root=self.artifacts_root,
            alias_path=alias_path,
            resolved_path=resolved_alias,
            manifest_path=manifest_path,
            manifest=_freeze(manifest_payload),
            contract_sha256=self.contract_sha256,
            manifest_sha256=_sha256_file(manifest_path),
            content_fingerprint=_canonical_sha256(manifest_payload),
        )
