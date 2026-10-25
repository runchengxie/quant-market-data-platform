"""Opt-in manual field adjudication, independent of production PIT and selectors.

Hashes authenticate bytes, never the human interpretation or official provenance.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant_market_data_platform.statement_consensus import _digest
from quant_market_data_platform.statement_research_asset import _load_asset
from quant_market_data_platform.statement_selection import _date, _sha

_SCHEMA = "statement-manual-field-research.v1"
_KEYS = ("dataset", "symbol", "report_period", "disclosure_date")
FieldKey = tuple[str, str, str, str, str]


def _stamp(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("Evidence and as_of timestamps require timezone")
    return stamp


def _bytes(path: Path, expected: str) -> bytes:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError("Evidence file missing") from exc
    if hashlib.sha256(raw).hexdigest() != _sha(expected):
        raise ValueError("Evidence checksum mismatch")
    return raw


def _key(value: Any) -> FieldKey:
    if not isinstance(value, (tuple, list)) or len(value) != 5:
        raise ValueError("An exact event and field key is required")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError("An exact event and field key is required")
    _date(value[2])
    _date(value[3])
    return (value[0], value[1], value[2], value[3], value[4])


def _evidence(entry: dict) -> datetime:
    if (
        entry.get("original_period_current_column") is not True
        or entry.get("historical_possession_certified") is not False
        or entry.get("whole_row_selector_certified") is not False
        or not isinstance(entry.get("field_semantics"), str)
        or not entry["field_semantics"].strip()
        or not isinstance(entry.get("official_url"), str)
        or not entry["official_url"].startswith("https://")
    ):
        raise ValueError("Manual evidence must declare its bounded interpretation")
    pages = entry.get("pdf_pages")
    if not isinstance(pages, list) or not pages or any(type(p) is not int or p < 1 for p in pages):
        raise ValueError("Explicit positive PDF pages required")
    acquisition = entry.get("source_acquisition", {})
    times = [
        _stamp(acquisition[name])
        for name in ("retrieved_at", "download_completed_before", "verified_at", "observed_at")
        if name in acquisition
    ]
    if not times:
        raise ValueError("Evidence acquisition timestamp required")
    _bytes(Path(entry["source_file"]), entry["sha256"])
    return max(_stamp(entry["verified_at"]), *times)


def _resolve(source: dict, entries: list, selections: tuple, created_at: str) -> list[dict]:
    events = {
        tuple(event[k] for k in _KEYS): event
        for report in source["reports"]
        for event in report["events"]
    }
    projection = {
        report["source"]["dataset"]: set(report["projection_fields"])
        for report in source["reports"]
    }
    ledger = {}
    for entry in entries:
        key = tuple(entry[k] for k in _KEYS)
        if key in ledger or key not in events:
            raise ValueError("Unknown or duplicate evidence event")
        fields = entry.get("fields")
        projected = projection[key[0]]
        if not isinstance(fields, dict) or not fields or not set(fields) <= projected:
            raise ValueError("Unknown evidence field")
        ledger[key] = entry
    keys = [_key(key) for key in selections]
    if not keys or len(set(keys)) != len(keys):
        raise ValueError("Explicit unique field selections required")
    resolutions = []
    for key in keys:
        event = events.get(key[:4])
        entry = ledger.get(key[:4])
        field = key[4]
        if event is None or entry is None or field not in event["differing_fields"]:
            raise ValueError("Selection must name an evidenced original conflict field")
        if field not in entry["fields"]:
            raise ValueError("Selected field evidence missing")
        value = entry["fields"][field]
        supporting = [
            candidate["row_sha256"]
            for candidate in event["candidates"]
            if _digest(candidate["row"][field]) == _digest(value)
        ]
        if not supporting:
            raise ValueError("Evidence value must match an original candidate exactly")
        available = max(_stamp(event["usable_at"]), _evidence(entry), _stamp(created_at))
        resolutions.append(
            {
                "field_key": list(key),
                "value": value,
                "supporting_candidate_sha256": supporting,
                "usable_at": available.isoformat(),
                "evidence": entry,
            }
        )
    return resolutions


def build_field_adjudicated_asset(
    *,
    source: tuple[Path, str],
    ledger: tuple[Path, str],
    selections: tuple[FieldKey, ...],
    code_revision: str,
) -> dict[str, Any]:
    """Build a separate field asset from caller-selected manual assertions.

    Ledger fields outside selections confer no permission. No category is selected
    and original common_values, conflict flags and candidates remain unchanged.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", code_revision):
        raise ValueError("code_revision must be a full Git commit")
    source_path, source_sha256 = source
    ledger_path, ledger_sha256 = ledger
    original, version = _load_asset(source_path, source_sha256)
    entries = json.loads(_bytes(ledger_path, ledger_sha256))
    if not isinstance(entries, list):
        raise ValueError("Evidence ledger must be a list")
    created_at = datetime.now(UTC).isoformat()
    asset = {
        "schema_version": _SCHEMA,
        "code_revision": code_revision,
        "created_at": created_at,
        "partial": True,
        "reconstructed": True,
        "historical_pit_certified": False,
        "whole_pit_certified": False,
        "full_market_certified": False,
        "whole_row_selector_certified": False,
        "complete_current_eligible": False,
        "status": "partial_reconstructed",
        "evidence_basis": "caller-selected manual interpretation; hashes verify bytes only",
        "source": {"path": str(source_path.resolve()), "sha256": _sha(source_sha256)},
        "ledger": {"path": str(ledger_path.resolve()), "sha256": _sha(ledger_sha256)},
        "original_version_sha256": version,
        "original_asset": original,
        "resolutions": _resolve(original, entries, selections, created_at),
    }
    asset["version_sha256"] = _digest(asset)
    return asset


def _load(path: Path, expected: str) -> tuple[dict, str]:
    asset = json.loads(_bytes(path, expected))
    version = asset.pop("version_sha256")
    if _digest(asset) != version or asset["schema_version"] != _SCHEMA:
        raise ValueError("Invalid adjudicated asset version")
    original, original_version = _load_asset(
        Path(asset["source"]["path"]), asset["source"]["sha256"]
    )
    entries = json.loads(_bytes(Path(asset["ledger"]["path"]), asset["ledger"]["sha256"]))
    selections = tuple(tuple(r["field_key"]) for r in asset["resolutions"])
    resolutions = _resolve(original, entries, selections, asset["created_at"])
    if (
        original != asset["original_asset"]
        or original_version != asset["original_version_sha256"]
        or resolutions != asset["resolutions"]
        or asset["partial"] is not True
        or asset["reconstructed"] is not True
        or any(
            asset[flag] is not False
            for flag in (
                "historical_pit_certified",
                "whole_pit_certified",
                "full_market_certified",
                "whole_row_selector_certified",
                "complete_current_eligible",
            )
        )
    ):
        raise ValueError("Invalid adjudicated asset scope or evidence")
    return asset, version


def read_adjudicated_field(
    path: Path,
    *,
    expected_sha256: str,
    accept_manual_field_adjudication: bool,
    field_key: FieldKey,
    as_of: str,
) -> dict[str, Any]:
    """Consume one field, rechecking all hash-bound inputs on every read.

    No evidence-free conflict field, entire row, fallback, or historical possession
    is authorized by this API. Before usable time, candidate/evidence values hide.
    """
    if accept_manual_field_adjudication is not True:
        raise ValueError("Explicit acceptance of manual field adjudication required")
    key = _key(field_key)
    stamp = _stamp(as_of)
    asset, version = _load(path, expected_sha256)
    base = {
        "version_sha256": version,
        "partial": True,
        "reconstructed": True,
        "historical_pit_certified": False,
        "whole_pit_certified": False,
        "full_market_certified": False,
        "whole_row_selector_certified": False,
        "blocks_fallback": True,
        "value": None,
    }
    for resolution in asset["resolutions"]:
        if tuple(resolution["field_key"]) != key:
            continue
        available = resolution["usable_at"]
        if stamp < _stamp(available):
            return {**base, "status": "not_yet_available", "usable_at": available}
        event = next(
            event
            for report in asset["original_asset"]["reports"]
            for event in report["events"]
            if tuple(event[k] for k in _KEYS) == key[:4]
        )
        return {
            **base,
            **resolution,
            "status": "available",
            "event": event,
            "evidence_basis": asset["evidence_basis"],
        }
    return {**base, "status": "unadjudicated"}
