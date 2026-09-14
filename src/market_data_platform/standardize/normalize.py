"""Provider-neutral normalization helpers shared by standardization code."""

from __future__ import annotations


def normalize_ts_code(value: object) -> str:
    """Normalize an A-share TuShare-style security code without provider IO dependencies."""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text.endswith((".SH", ".SZ", ".BJ")):
        code, exchange = text.rsplit(".", 1)
        return f"{code.zfill(6)}.{exchange}"
    return text
