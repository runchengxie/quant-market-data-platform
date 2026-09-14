"""Atomic IO helpers for writing frames and manifests to disk."""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml


def _prepare_output_dir(path: Path, *, allow_existing: bool = False) -> Path:
    """Prepare a mirror output without silently mixing old partitions."""
    output = path.expanduser().resolve()
    if output.exists() and not output.is_dir():
        raise NotADirectoryError(f"Mirror output is not a directory: {output}")
    if output.is_dir() and not allow_existing and any(output.rglob("*.parquet")):
        raise FileExistsError(
            f"Refusing to write into a non-empty mirror output directory: {output}. "
            "Use a new version directory or set skip_existing explicitly."
        )
    output.mkdir(parents=True, exist_ok=True)
    return output


def _write_frame(frame: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
        return
    frame.to_parquet(path, index=False)


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _temporary_sibling(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")


def _write_frame_atomically(frame: Any, path: Path) -> None:
    temporary = _temporary_sibling(path)
    try:
        _write_frame(frame, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_manifest_atomically(path: Path, manifest: dict[str, Any]) -> None:
    temporary = _temporary_sibling(path)
    try:
        _write_manifest(temporary, manifest)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _single_file_manifest_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}.manifest.yml")


def _fields_text(fields: Iterable[str] | None, required: tuple[str, ...] = ()) -> str | None:
    if fields is None:
        return None
    selected: list[str] = []
    for field in (*required, *fields):
        value = str(field).strip()
        if value and value not in selected:
            selected.append(value)
    return ",".join(selected)
