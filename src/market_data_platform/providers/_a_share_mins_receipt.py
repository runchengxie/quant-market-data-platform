"""Receipt and symbol-inventory helpers for the A-share minute mirror.

Extracted from ``_a_share_mins_partition`` so the partition module stays a thin
orchestration shell. These functions build the immutable promotion receipt and
validate the sidecar symbol inventories.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from market_data_platform.providers._a_share_mins_constants import MINUTE_BARS_PER_DAY
from market_data_platform.providers._a_share_mins_validation import _is_cn_stock_ts_code

__all__ = [
    "_sidecar_symbol_inventory",
    "_exact_partition_receipt",
    "_universe_hash",
]


def _sidecar_symbol_inventory(
    payload: dict[str, Any],
    *,
    sidecar_path: Path,
    trade_date: str,
) -> tuple[set[str], set[str], set[str]]:
    inventories = (
        payload.get("expected_symbols"),
        payload.get("completed_symbols"),
        payload.get("missing_request_symbols"),
    )
    if not all(isinstance(value, list) for value in inventories):
        raise ValueError(f"TuShare minute sidecar has malformed symbol inventories: {sidecar_path}")
    expected_symbols = {str(value) for value in cast("list[Any]", inventories[0])}
    completed_symbols = {str(value) for value in cast("list[Any]", inventories[1])}
    missing_symbols = {str(value) for value in cast("list[Any]", inventories[2])}
    invalid_symbols = sorted(
        symbol for symbol in expected_symbols if not _is_cn_stock_ts_code(symbol)
    )
    if invalid_symbols:
        raise ValueError(
            f"TuShare minute sidecar has invalid expected symbols for {trade_date}: "
            f"{invalid_symbols}"
        )
    if not expected_symbols:
        raise ValueError(f"TuShare minute sidecar has an empty universe for {trade_date}")
    return expected_symbols, completed_symbols, missing_symbols


def _exact_partition_receipt(
    payload: dict[str, Any],
    *,
    sidecar_path: Path,
    trade_date: str,
    expected_symbols: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    partition = payload.get("partition")
    if not isinstance(partition, dict):
        raise ValueError(f"TuShare minute sidecar has no partition receipt: {sidecar_path}")
    request_policy = payload.get("request_policy")
    if not isinstance(request_policy, dict):
        raise ValueError(f"TuShare minute sidecar has no request policy: {sidecar_path}")
    partition_symbols = {str(value) for value in partition.get("symbols", [])}
    complete_symbols = {str(value) for value in partition.get("complete_symbols", [])}
    files = partition.get("files")
    exact_file = (
        isinstance(files, list)
        and len(files) == 1
        and isinstance(files[0], dict)
        and files[0].get("name") == "part-00000.parquet"
    )
    if (
        partition_symbols != expected_symbols
        or complete_symbols != expected_symbols
        or partition.get("rows") != len(expected_symbols) * MINUTE_BARS_PER_DAY
        or not exact_file
    ):
        raise ValueError(
            f"TuShare minute sidecar does not bind an exact 241-bar universe for {trade_date}"
        )
    return partition, files, request_policy


def _universe_hash(symbols: set[str], *, rule: str) -> str:
    payload = json.dumps(
        {"rule": rule, "symbols": sorted(symbols)},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
