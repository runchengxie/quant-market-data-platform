"""Dated, same-security minute request identities; never merger peer aliases."""

from __future__ import annotations

from typing import Any

from quant_market_data_platform.providers._bse_code_mapping import bse_minute_request_symbol

SHANGHAI_IDENTITY_START = "19940324"
SHANGHAI_CODE_EFFECTIVE = "20100305"
SHANGHAI_IDENTITY_SOURCE = "https://static.cninfo.com.cn/finalpage/2011-08-15/59816208.PDF"
SHANGHAI_EFFECTIVE_SOURCE = (
    "https://static.sse.com.cn/cs/zhs/scfw/gg/ssgs/2011-03-09/601607_2010_n.pdf"
)


def minute_request_symbol(symbol: str, trade_date: str) -> str:
    """Translate only within the verified original listing/code interval."""
    if symbol == "601607.SH" and SHANGHAI_IDENTITY_START <= trade_date < SHANGHAI_CODE_EFFECTIVE:
        return "600849.SH"
    return bse_minute_request_symbol(symbol, trade_date)


def request_identity(symbol: str, trade_date: str) -> dict[str, Any] | None:
    request = minute_request_symbol(symbol, trade_date)
    if request == symbol:
        return None
    result: dict[str, Any] = {"canonical_symbol": symbol, "request_symbol": request}
    if symbol == "601607.SH":
        result.update(
            valid_from=SHANGHAI_IDENTITY_START,
            valid_until_exclusive=SHANGHAI_CODE_EFFECTIVE,
            source=SHANGHAI_IDENTITY_SOURCE,
            effective_date_source=SHANGHAI_EFFECTIVE_SOURCE,
            effective_date_evidence="indexed_primary_report",
        )
    return result


def validate_request_identities(symbols: list[str], trade_date: str) -> list[str]:
    requests = [minute_request_symbol(symbol, trade_date) for symbol in symbols]
    if len(set(requests)) != len(requests):
        raise ValueError("Minute request identity collision between canonical and historical codes")
    return requests


def validated_identity_lineage(
    payload: dict[str, Any], symbols: set[str], trade_date: str
) -> dict[str, Any]:
    """Validate observed Shanghai lineage before promoting canonicalized bars."""
    lineage = payload.get("code_identity", {})
    if not isinstance(lineage, dict):
        raise ValueError("Malformed minute code identity lineage")
    for symbol in symbols:
        expected = request_identity(symbol, trade_date)
        if symbol != "601607.SH" or expected is None:
            continue
        record = lineage.get(symbol)
        if not isinstance(record, dict) or any(record.get(k) != v for k, v in expected.items()):
            raise ValueError("Missing or mismatched historical minute code identity lineage")
        observed = record.get("response_symbols")
        if (
            not isinstance(observed, list)
            or not observed
            or not set(observed) <= {symbol, expected["request_symbol"]}
        ):
            raise ValueError("Missing or invalid observed minute response identity")
    return lineage
