"""Manifest writing for published and staged platform assets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
