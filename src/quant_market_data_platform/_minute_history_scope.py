"""Shared persisted date boundary for acquisition and read-only monitoring."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from quant_market_data_platform.minute_candidate import _atomic_write_json

_SCHEMA = "tushare.minute_history_scope.v1"
_DEFAULT_START = "20160101"


def _date(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"\d{8}", value) is None:
        raise ValueError("history_start_date must be YYYYMMDD")
    datetime.strptime(value, "%Y%m%d")
    return value


def read_history_start(metadata: Path, requested: str | None = None) -> str:
    """Resolve an explicit override, a stored boundary, or the legacy default."""
    if requested is not None:
        return _date(requested)
    path = metadata / "history_scope.json"
    if not path.exists():
        return _DEFAULT_START
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA:
        raise ValueError("Invalid persisted minute history scope")
    return _date(payload.get("start_date"))


def write_history_start(metadata: Path, start_date: str) -> None:
    """Publish a validated boundary without replacing an unchanged identity."""
    start_date = _date(start_date)
    path = metadata / "history_scope.json"
    if path.exists() and read_history_start(metadata) == start_date:
        return
    _atomic_write_json(path, {"schema_version": _SCHEMA, "start_date": start_date})


def prepare_history_scope(metadata: Path, requested: str | None, *, dry_run: bool) -> str:
    """Resolve the acquisition boundary and persist only a real run's scope."""
    start_date = read_history_start(metadata, requested)
    if not dry_run:
        write_history_start(metadata, start_date)
    return start_date
