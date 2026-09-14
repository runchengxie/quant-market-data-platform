"""Promote verified Guan mobile archives into an isolated provider-native tree."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

PROMOTION_SCHEMA = "guan.mobile_raw_promotion.v1"

ARCHIVE_SCHEMA = "guan.mobile_archive.v2"

SCHEMA_PROFILE = "guan_mobile_native_v1"

PROVIDER_RELATIVE_ROOT = Path("source_native") / "guan_mobile"

LOCK_FILENAME = ".promote-guan-mobile.lock"

COPY_BUFFER_BYTES = 8 * 1024 * 1024

_AT_FDCWD = -100

_RENAME_NOREPLACE = 1

PromotionStrategy = Literal["hardlink", "copy"]

_DAILY_FILE_PATTERN = re.compile(
    r"^(deal|snapshot|order|index)_(\d{8})(?:\((\d+)\))?\.parquet$",
    re.IGNORECASE,
)

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class GuanMobilePromotionError(RuntimeError):
    """Base error for provider-native Guan raw promotion."""


class GuanMobilePromotionConflict(GuanMobilePromotionError):
    """Raised when two artifacts claim one logical key with different content."""


class GuanMobilePromotionVerificationError(GuanMobilePromotionError):
    """Raised when a promotion receipt or promoted artifact fails verification."""


@dataclass(frozen=True)
class PromotionOptions:
    """Inputs for a resumable provider-native promotion."""

    source_dir: str | Path
    raw_root: str | Path
    archive_manifest: str | Path
    receipt: str | Path
    strategy: PromotionStrategy = "hardlink"
    dry_run: bool = False

    def __post_init__(self) -> None:
        if self.strategy not in {"hardlink", "copy"}:
            raise ValueError(f"Unsupported promotion strategy: {self.strategy!r}")


@dataclass(frozen=True)
class _SourceArtifact:
    dataset: str
    trade_date: str | None
    duplicate_suffix: str | None
    relative_path: str
    path: Path
    sha256: str
    size: int
    archive_receipt: Mapping[str, Any]
    output_filename: str

    @property
    def logical_key(self) -> str:
        if self.trade_date is None:
            return f"{self.dataset}:{self.output_filename}"
        return f"{self.dataset}:{self.trade_date}"

    @property
    def canonical_filename(self) -> str:
        return self.output_filename


@dataclass(frozen=True)
class _PromotionPaths:
    source: Path
    raw_root: Path
    archive_manifest: Path
    receipt: Path
    provider_root: Path


@dataclass(frozen=True)
class _PromotionPlanInventory:
    artifacts: Sequence[_SourceArtifact]
    entries: Sequence[Mapping[str, Any]]
    duplicates: Sequence[Mapping[str, Any]]
    ignored: Sequence[Mapping[str, Any]]
    conflicts: Sequence[Mapping[str, Any]]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _absolute_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _receipt_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.parent.resolve() / path.name


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuanMobilePromotionError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GuanMobilePromotionError(f"{label.capitalize()} is not a JSON mapping: {path}")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(COPY_BUFFER_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stat_payload(path: Path) -> dict[str, int]:
    value = path.stat(follow_symlinks=False)
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "mode": value.st_mode,
        "permissions": stat.S_IMODE(value.st_mode),
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
        "nlink": value.st_nlink,
    }


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.stat(follow_symlinks=False).st_mode)
    except OSError:
        return False


def _validate_roots(
    source: Path,
    raw_root: Path,
    archive_manifest: Path,
    receipt: Path,
) -> Path:
    if not source.is_dir():
        raise GuanMobilePromotionError(f"Source archive is not a directory: {source}")
    if not raw_root.is_dir():
        raise GuanMobilePromotionError(f"Raw root is not a directory: {raw_root}")
    if not archive_manifest.is_file():
        raise GuanMobilePromotionError(
            f"Verified archive manifest does not exist: {archive_manifest}"
        )
    provider_root = raw_root / PROVIDER_RELATIVE_ROOT
    if source == provider_root or source.is_relative_to(provider_root):
        raise GuanMobilePromotionError("Source archive must be outside the provider-native tree")
    if provider_root.is_relative_to(source):
        raise GuanMobilePromotionError("Provider-native tree must not be inside the source archive")
    if receipt.is_relative_to(source):
        raise GuanMobilePromotionError("Promotion receipt must not be written inside _incoming")
    return provider_root


def _verified_source_artifact(
    source: Path,
    relative: object,
    raw_receipt: object,
) -> _SourceArtifact:
    if not isinstance(relative, str) or not isinstance(raw_receipt, Mapping):
        raise GuanMobilePromotionError("Archive manifest contains an invalid file receipt")
    raw_receipt = cast(Mapping[str, Any], raw_receipt)
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise GuanMobilePromotionError(f"Unsafe archive relative path: {relative!r}")
    path = source / relative_path
    if not _regular_file(path):
        raise GuanMobilePromotionError(f"Archived input is not a regular file: {path}")
    file_stat = path.stat(follow_symlinks=False)
    try:
        expected_size = int(raw_receipt["size"])
        expected_mtime_ns = int(raw_receipt["destination_mtime_ns"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GuanMobilePromotionError(
            f"Archive receipt lacks destination stat evidence: {relative}"
        ) from exc
    sha256 = raw_receipt.get("verified_sha256")
    if (
        raw_receipt.get("status") != "complete"
        or raw_receipt.get("verification_status") != "verified"
        or not isinstance(sha256, str)
        or _SHA256_PATTERN.fullmatch(sha256) is None
        or raw_receipt.get("sha256") != sha256
    ):
        raise GuanMobilePromotionError(
            f"Archive file lacks matching verified SHA-256 evidence: {relative}"
        )
    if file_stat.st_size != expected_size or file_stat.st_mtime_ns != expected_mtime_ns:
        raise GuanMobilePromotionError(
            f"Archived input stat changed after verification: {relative}"
        )

    matched = _DAILY_FILE_PATTERN.fullmatch(relative_path.name)
    if matched is None:
        return _SourceArtifact(
            dataset="auxiliary",
            trade_date=None,
            duplicate_suffix=None,
            relative_path=relative,
            path=path,
            sha256=sha256,
            size=expected_size,
            archive_receipt=raw_receipt,
            output_filename=relative_path.name,
        )
    dataset, trade_date, duplicate_suffix = matched.groups()
    return _SourceArtifact(
        dataset=dataset.lower(),
        trade_date=trade_date,
        duplicate_suffix=duplicate_suffix,
        relative_path=relative,
        path=path,
        sha256=sha256,
        size=expected_size,
        archive_receipt=raw_receipt,
        output_filename=f"{dataset.lower()}_{trade_date}.parquet",
    )


def _load_verified_archive(
    source: Path,
    manifest_path: Path,
) -> tuple[dict[str, Any], list[_SourceArtifact], list[dict[str, Any]]]:
    manifest = _load_json(manifest_path, label="archive manifest")
    if manifest.get("schema_version") != ARCHIVE_SCHEMA:
        raise GuanMobilePromotionError(
            f"Unsupported archive manifest schema: {manifest.get('schema_version')!r}"
        )
    if manifest.get("status") != "verified" or manifest.get("copy_status") != "complete":
        raise GuanMobilePromotionError("Archive manifest is not copy-complete and verified")
    recorded_destination = manifest.get("destination_dir")
    if not isinstance(recorded_destination, str) or _absolute_path(recorded_destination) != source:
        raise GuanMobilePromotionError(
            "Archive manifest destination_dir does not match --source-dir"
        )
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise GuanMobilePromotionError("Archive manifest has no valid files mapping")

    artifacts = [
        _verified_source_artifact(source, relative, raw_receipt)
        for relative, raw_receipt in sorted(files.items())
    ]
    return manifest, artifacts, []


def _select_logical_sources(
    artifacts: Sequence[_SourceArtifact],
) -> tuple[list[_SourceArtifact], list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[_SourceArtifact]] = defaultdict(list)
    for artifact in artifacts:
        grouped[artifact.logical_key].append(artifact)

    selected: list[_SourceArtifact] = []
    duplicates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for logical_key, candidates in sorted(grouped.items()):
        ordered = sorted(
            candidates,
            key=lambda item: (item.duplicate_suffix is not None, item.relative_path),
        )
        hashes = {candidate.sha256 for candidate in ordered}
        if len(hashes) != 1:
            conflicts.append(
                {
                    "type": "source_logical_key_sha_mismatch",
                    "logical_key": logical_key,
                    "candidates": [
                        {
                            "relative_path": candidate.relative_path,
                            "sha256": candidate.sha256,
                            "size": candidate.size,
                        }
                        for candidate in ordered
                    ],
                }
            )
            continue
        canonical = ordered[0]
        selected.append(canonical)
        for duplicate in ordered[1:]:
            duplicates.append(
                {
                    "logical_key": logical_key,
                    "relative_path": duplicate.relative_path,
                    "path": str(duplicate.path),
                    "sha256": duplicate.sha256,
                    "size": duplicate.size,
                    "duplicate_of": canonical.relative_path,
                    "status": "exact_source_duplicate_skipped",
                }
            )
    return selected, duplicates, conflicts


def _target_path(provider_root: Path, artifact: _SourceArtifact) -> Path:
    if artifact.trade_date is None:
        return provider_root / artifact.dataset / artifact.canonical_filename
    return provider_root / artifact.dataset / artifact.trade_date[:6] / artifact.canonical_filename


def _standardized_overlaps(raw_root: Path, artifact: _SourceArtifact) -> list[str]:
    date = artifact.trade_date
    if date is None:
        return []
    dashed = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    candidates: list[Path]
    if artifact.dataset == "deal":
        candidates = [raw_root / "trades" / date[:6] / f"trades_{dashed}.parquet"]
    elif artifact.dataset == "order":
        candidates = [raw_root / "order" / date[:6] / f"order_{dashed}.parquet"]
    elif artifact.dataset == "snapshot":
        candidates = [
            raw_root / "snapshot" / f"snapshot_{date}.parquet",
            raw_root / "snapshot" / f"snapshot_{date[:6]}.parquet",
        ]
    else:
        candidates = []
    return [str(path) for path in candidates if path.is_file()]


def _native_layout_conflicts(
    provider_root: Path,
    selected: Sequence[_SourceArtifact],
) -> list[dict[str, Any]]:
    expected = {
        artifact.logical_key: _target_path(provider_root, artifact) for artifact in selected
    }
    conflicts: list[dict[str, Any]] = []
    if not provider_root.exists():
        return conflicts
    for path in sorted(provider_root.rglob("*.parquet")):
        matched = _DAILY_FILE_PATTERN.fullmatch(path.name)
        if matched is None:
            continue
        filename_dataset, trade_date, _ = matched.groups()
        try:
            tree_dataset = path.relative_to(provider_root).parts[0]
        except (ValueError, IndexError):
            continue
        logical_key = f"{filename_dataset.lower()}:{trade_date}"
        canonical = expected.get(logical_key)
        if tree_dataset.lower() != filename_dataset.lower():
            conflicts.append(
                {
                    "type": "native_dataset_directory_mismatch",
                    "logical_key": logical_key,
                    "path": str(path),
                }
            )
        elif canonical is not None and path != canonical:
            conflicts.append(
                {
                    "type": "noncanonical_native_target",
                    "logical_key": logical_key,
                    "path": str(path),
                    "expected_path": str(canonical),
                }
            )
    expected_auxiliary = {
        artifact.canonical_filename: expected[artifact.logical_key]
        for artifact in selected
        if artifact.dataset == "auxiliary"
    }
    auxiliary_root = provider_root / "auxiliary"
    if auxiliary_root.exists():
        for path in sorted(auxiliary_root.rglob("*")):
            canonical = expected_auxiliary.get(path.name)
            if canonical is not None and path.is_file() and path != canonical:
                conflicts.append(
                    {
                        "type": "noncanonical_native_target",
                        "logical_key": f"auxiliary:{path.name}",
                        "path": str(path),
                        "expected_path": str(canonical),
                    }
                )
    return conflicts


def _existing_target_status(
    artifact: _SourceArtifact,
    target: Path,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    if not os.path.lexists(target):
        return "missing", None, None
    if target.is_symlink() or not _regular_file(target):
        return (
            "conflict",
            None,
            {
                "type": "target_not_regular",
                "logical_key": artifact.logical_key,
                "path": str(target),
            },
        )
    source_stat = artifact.path.stat(follow_symlinks=False)
    target_stat = target.stat(follow_symlinks=False)
    if (source_stat.st_dev, source_stat.st_ino) == (target_stat.st_dev, target_stat.st_ino):
        actual_sha256 = artifact.sha256
        sha256_evidence = "same_inode_as_verified_archive"
    elif target_stat.st_size != artifact.size:
        actual_sha256 = None
        sha256_evidence = "size_mismatch"
    else:
        actual_sha256 = _sha256_file(target)
        sha256_evidence = "target_rehash"
    if actual_sha256 != artifact.sha256:
        return (
            "conflict",
            _stat_payload(target),
            {
                "type": "target_logical_key_sha_mismatch",
                "logical_key": artifact.logical_key,
                "path": str(target),
                "expected_sha256": artifact.sha256,
                "actual_sha256": actual_sha256,
                "sha256_evidence": sha256_evidence,
            },
        )
    return "same_sha", _stat_payload(target), None
