"""Validation shared by research readers of the published daily-clean asset."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

_DAILY_CLEAN_SCHEMA = "tushare.a_share.daily_clean.v2"
_ST_AVAILABILITY_CONTRACT = "daily_clean.st_available_from.v1"


def require_daily_clean_st_availability(daily_clean: Path) -> None:
    """Require the published daily-clean v2 manifest and ST availability contract."""

    manifest = daily_clean / "manifest.yml"
    if not manifest.is_file():
        raise FileNotFoundError(f"daily_clean manifest not found: {manifest}")
    payload: Any = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"daily_clean manifest is not a mapping: {manifest}")
    if payload.get("schema_version") != _DAILY_CLEAN_SCHEMA:
        raise ValueError(f"daily_clean manifest has unsupported schema: {manifest}")
    if payload.get("status") != "completed":
        raise ValueError(f"daily_clean manifest is not completed: {manifest}")
    contracts = payload.get("contracts")
    if not isinstance(contracts, Mapping) or contracts.get("st_availability") != (
        _ST_AVAILABILITY_CONTRACT
    ):
        raise ValueError(f"daily_clean manifest lacks the ST availability contract: {manifest}")


__all__ = ["require_daily_clean_st_availability"]
