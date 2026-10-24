from __future__ import annotations

import hashlib
import json
import pathlib
import urllib.parse
from typing import Any, cast

import pyarrow as pa
import pyarrow.compute as arrow_compute

# Arrow generates compute kernel attributes dynamically at import time.
pc = cast(Any, arrow_compute)
P = pathlib.Path


def add_identity(table):
    base = pc.if_else(
        pc.not_equal(table["AuditCandidateSecurityKey"], ""),
        table["AuditCandidateSecurityKey"],
        pc.binary_join_element_wise(pa.scalar("unresolved:"), table["Ticker"], pa.scalar("")),
    )
    wolf = pc.if_else(
        pc.less(table["Date"], "20250929"),
        pa.scalar("research:WOLF:old-equity"),
        pa.scalar("research:WOLF:post-reorganization"),
    )
    return table.append_column(
        "ResearchKey", pc.if_else(pc.equal(table["Ticker"], "WOLF"), wolf, base)
    )


def security_token(symbol, key, basis):
    return hashlib.sha256(
        json.dumps([symbol, key, basis], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()[:20]


def bucket_for(symbol, n):
    return int.from_bytes(hashlib.sha256(symbol.encode()).digest()[:4], "little") % n


def symbol_folder(symbol):
    if not symbol or symbol in {".", ".."}:
        raise ValueError("Expected a nonempty ticker, not a dot path")
    encoded = "".join(
        f"%{ord(c):02X}"
        if (c.isascii() and c.islower()) or (c == "." and i == len(symbol) - 1)
        else urllib.parse.quote(c, safe="._-")
        for i, c in enumerate(symbol)
    )
    return "symbol=" + encoded
