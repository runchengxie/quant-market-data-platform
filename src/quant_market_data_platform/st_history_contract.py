"""Recognize direct and owner-published ST availability provenance."""

from collections.abc import Mapping
from typing import Any


def has_st_availability_contract(receipt: Mapping[str, Any]) -> bool:
    schema = receipt.get("schema_version")
    return schema == "market-data-platform.reconstructed-st-history.v2" or (
        schema == "market-data-platform.tushare-reference.v1"
        and receipt.get("source_receipt_schema_version")
        == "market-data-platform.reconstructed-st-history.v2"
        and receipt.get("source_quality_status") == "complete"
    )
