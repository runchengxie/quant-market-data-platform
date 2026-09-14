"""Provider field helpers for execution-aware daily data loading."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger("market_data_platform")
_EXECUTION_LIQUIDITY_PROXY_PATTERN = re.compile(r"^(adv|medadv)\d+_amount$")


def _rqdata_fields_for_standard_columns(columns: set[str]) -> list[str]:
    raw_map = {
        "close": "close",
        "open": "open",
        "high": "high",
        "low": "low",
        "vol": "volume",
        "amount": "total_turnover",
        "tr_close": "close",
    }
    fields: list[str] = []
    for column in sorted(columns):
        normalized = str(column).strip()
        raw = raw_map.get(normalized)
        if raw is None and _EXECUTION_LIQUIDITY_PROXY_PATTERN.fullmatch(normalized):
            raw = "total_turnover"
        if raw and raw not in fields:
            fields.append(raw)
    return fields


def ensure_execution_daily_fields(
    *,
    data_cfg: Mapping[str, Any],
    provider: str,
    required_columns: set[str],
) -> None:
    """Add RQData fields needed by an execution or pricing configuration."""
    if provider != "rqdata" or not isinstance(data_cfg, dict):
        return
    required_fields = _rqdata_fields_for_standard_columns(required_columns)
    if not required_fields:
        return

    rq_cfg = data_cfg.get("rqdata")
    if rq_cfg is None:
        rq_cfg = {}
        data_cfg["rqdata"] = rq_cfg
    if not isinstance(rq_cfg, dict):
        return

    current_fields_raw = rq_cfg.get("fields")
    if isinstance(current_fields_raw, str) and current_fields_raw in {"all", "*"}:
        return
    if current_fields_raw is None:
        current_fields = ["close", "volume", "total_turnover"]
    elif isinstance(current_fields_raw, (list, tuple)):
        current_fields = [str(field).strip() for field in current_fields_raw if str(field).strip()]
    else:
        current_fields = [str(current_fields_raw).strip()]

    updated_fields = list(dict.fromkeys(current_fields + required_fields))
    if updated_fields != current_fields:
        rq_cfg["fields"] = updated_fields
        logger.info(
            "Expanded data.rqdata.fields for execution pricing columns: %s",
            updated_fields,
        )


__all__ = ["ensure_execution_daily_fields"]
