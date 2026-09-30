"""Atomic JSON manifest persistence for platform assets that use JSON contracts."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def write_json_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically replace one UTF-8 JSON manifest without changing its wire format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(dict(payload), temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


__all__ = ["write_json_manifest"]
