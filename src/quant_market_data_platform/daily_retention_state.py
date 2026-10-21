"""Classify retained evidence and unfinished daily publication attempts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

import yaml

AttemptState = tuple[Literal["keep", "review"], str]


def _completed(path: Path, *, schema: str | None = None) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            return False
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("status") == "completed"
        and (schema is None or payload.get("schema_version") == schema)
    )


def daily_attempt_state(path: Path) -> AttemptState | None:
    """Exclude retired metadata and incomplete attempts from newest-N slots."""
    marker = path / "retirement_receipt.json"
    if marker.exists() or marker.is_symlink():
        if _completed(marker, schema="a_share.daily_clean_input_retirement.v1"):
            return "keep", "retired snapshot: retain provenance metadata without a version slot"
        return "review", "invalid or incomplete retirement receipt; preserve remaining evidence"
    is_input = path.parent.name == "daily_clean_inputs" and re.fullmatch(
        r"a_share_all_\d{8}_\d{8}\.\d{8}T\d{6}\.\d+", path.name
    )
    if is_input and not _completed(
        path / "snapshot_receipt.json", schema="a_share.daily_clean_input_snapshot.v1"
    ):
        return "review", "uncompleted input capture; preserve failure evidence outside newest-N"
    is_clean = path.parent.name == "daily" and re.fullmatch(
        r"a_share_all_\d{8}_\d{8}_daily_clean_st\.[A-Za-z0-9]+", path.name
    )
    if is_clean and not _completed(path / "manifest.yml"):
        return "review", "uncompleted clean attempt; preserve failure evidence outside newest-N"
    return None
