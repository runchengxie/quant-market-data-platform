"""Validation primitives for minute campaign readiness evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from market_data_platform.file_receipts import file_sha256


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def readiness_summary_is_valid(
    payload: dict[str, Any],
    *,
    manifest_path: Path,
    ledger_path: Path,
    expected_dates: int,
) -> bool:
    """Check that a marker carries a complete, current reconciliation summary."""

    dates_complete = payload.get("dates_complete")
    rows = payload.get("rows")
    return (
        payload.get("manifest_path") == str(manifest_path)
        and payload.get("ledger_path") == str(ledger_path)
        and type(dates_complete) is int
        and dates_complete == expected_dates
        and type(rows) is int
        and rows >= 0
        and _is_sha256(payload.get("date_receipts_sha256"))
        and ledger_path.is_file()
        and payload.get("ledger_sha256_at_reconciliation") == file_sha256(ledger_path)
    )
