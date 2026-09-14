from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def seal_context_snapshot(  # noqa: PLR0913
    artifacts_root: str | Path,
    *,
    provider: str,
    dataset: str,
    source_locator: str,
    retrieved_at: datetime,
    body: bytes,
    parser_version: str,
    request_metadata: Mapping[str, Any],
    content_type: str = "application/octet-stream",
) -> Path:
    root = Path(artifacts_root).expanduser().resolve()
    timestamp = _utc(retrieved_at)
    vintage = timestamp.strftime("%Y%m%dT%H%M%SZ")
    parent = root / "assets" / "context" / "cn" / "raw" / provider / dataset
    target = parent / f"vintage={vintage}"
    if target.exists():
        raise FileExistsError(f"sealed context snapshot already exists: {target}")
    parent.mkdir(parents=True, exist_ok=True)
    temp = parent / f".{target.name}.tmp-{os.getpid()}"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=False)
    try:
        raw_path = temp / "raw.bin"
        raw_path.write_bytes(body)
        receipt = {
            "provider": str(provider),
            "dataset": str(dataset),
            "source_locator": str(source_locator),
            "retrieved_at": timestamp.isoformat(),
            "content_type": str(content_type),
            "parser_version": str(parser_version),
            "request_metadata": dict(request_metadata),
            "byte_count": len(body),
            "sha256": _sha256_bytes(body),
            "immutable_snapshot": True,
        }
        receipt_bytes = _json_bytes(receipt)
        (temp / "receipt.json").write_bytes(receipt_bytes)
        seal = {
            "schema_version": "cn_context.raw.seal.v1",
            "immutable_snapshot": True,
            "files": {
                "raw.bin": _sha256_file(raw_path),
                "receipt.json": _sha256_bytes(receipt_bytes),
            },
        }
        (temp / "manifest.seal.json").write_bytes(_json_bytes(seal))
        temp.replace(target)
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        raise
    return target
