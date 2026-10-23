"""Independent partial statement research assets; no production publication path."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from quant_market_data_platform.statement_consensus import _digest, audit_projection_consensus
from quant_market_data_platform.statement_selection import _date, _sha

_SCHEMA = "statement-partial-research.v1"


def build_research_asset(*, sources: list[dict], code_revision: str) -> dict[str, Any]:
    """Reverify immutable sources and retain every candidate in the bounded projection.

    Sources specify dataset, source (Path), expected_sha256, fields (tuple), and
    available_delay_days, exactly as audit_projection_consensus. No report is
    trusted as a substitute for rescanning its hash-bound source.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", code_revision):
        raise ValueError("code_revision must be a full Git commit")
    if not sources or len({s["dataset"] for s in sources}) != len(sources):
        raise ValueError("Provide one explicit source per dataset")
    reports = [audit_projection_consensus(**source) for source in sources]
    for report in reports:
        report.pop("audit_created_at")
    asset = {
        "schema_version": _SCHEMA,
        "code_revision": code_revision,
        "partial": True,
        "reconstructed": True,
        "historical_pit_certified": False,
        "complete_current_eligible": False,
        "scope": "exact multi-category disclosure events only; no latest-report coverage",
        "interval_semantics": (
            "[start, infinity) for exact immutable disclosure; no supersession claim"
        ),
        "status": "partial_reconstructed",
        "event_count": sum(len(r["events"]) for r in reports),
        "conflict_event_count": sum(r["conflict_event_count"] for r in reports),
        "consensus_event_count": sum(r["consensus_event_count"] for r in reports),
        "candidate_row_count": sum(r["candidate_row_count"] for r in reports),
        "reports": reports,
    }
    asset["version_sha256"] = _digest(asset)
    return asset


def _load_asset(path: Path, expected_sha256: str) -> tuple[dict, str]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _sha(expected_sha256):
        raise ValueError("Research asset checksum mismatch")
    asset = json.loads(raw)
    version = asset.pop("version_sha256")
    if _digest(asset) != version or asset["schema_version"] != _SCHEMA:
        raise ValueError("Invalid research asset version")
    if asset["partial"] is not True or asset["reconstructed"] is not True:
        raise ValueError("Invalid research scope")
    return asset, version


def read_research_event(
    path: Path,
    *,
    expected_sha256: str,
    accept_partial_reconstructed: bool,
    event_key: tuple[str, str, str, str],
    as_of: str,
) -> dict[str, Any]:
    """Read one exact disclosure, never substitute an older event or null value.

    expected_sha256 pins the complete serialized artifact, supplied by its caller.
    A conflict blocks the entire declared projection. Common nulls remain missing.
    The result includes the original candidates and source hash when in scope.
    """
    if accept_partial_reconstructed is not True:
        raise ValueError("Explicit acceptance of partial reconstructed data is required")
    dataset, symbol, report_period, disclosure_date = event_key
    _date(report_period)
    _date(disclosure_date)
    stamp = datetime.fromisoformat(as_of)
    if stamp.utcoffset() is None:
        raise ValueError("as_of must include timezone")
    asset, version = _load_asset(path, expected_sha256)
    base = {"version_sha256": version, "partial": True, "reconstructed": True}
    for report in asset["reports"]:
        if report["source"]["dataset"] != dataset:
            continue
        for event in report["events"]:
            key = (event["symbol"], event["report_period"], event["disclosure_date"])
            if key != (symbol, report_period, disclosure_date):
                continue
            event["active_interval"] = {"start": event["usable_at"], "end": None}
            result = {**base, "source": report["source"], "event": event, "values": None}
            if stamp < datetime.fromisoformat(event["usable_at"]):
                return {
                    **base,
                    "status": "not_yet_available",
                    "blocks_fallback": True,
                    "values": None,
                    "usable_at": event["usable_at"],
                }
            values = event["common_values"]
            return {
                **result,
                "status": "conflict" if event["blocks_fallback"] else "available",
                "blocks_fallback": event["blocks_fallback"],
                "values": values,
                "missing_fields": [
                    field for field, value in (values or {}).items() if value is None
                ],
            }
    return {**base, "status": "out_of_scope", "values": None, "blocks_fallback": True}
