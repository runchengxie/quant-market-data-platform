"""Freeze staged TuShare minute replacement partitions into an inventory."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_data_platform._tushare_minute_campaign_readiness import (
    file_sha256,
    readiness_summary_is_valid,
)
from market_data_platform.providers.tushare_a_share_mins import (
    validate_complete_minute_partition,
)

CAMPAIGN_SCHEMA_VERSION = "tushare.minute_replacement_campaign.v1"
READINESS_SCHEMA_VERSION = "tushare.minute_replacement_campaign.readiness.v1"
INVENTORY_SCHEMA_VERSION = "a_share.minute_tushare_candidate_inventory.v1"
SEMANTIC_AUDIT_SCHEMA_VERSION = "a_share.minute_tushare_candidate_semantic_audit.v1"
CANDIDATE_RECEIPT_SCHEMA_VERSION = "a_share.minute_tushare_candidate.v1"
FEATURE_NAMES = (
    "intraday_return",
    "range",
    "volume",
    "amount",
    "opening_volume_share",
    "closing_volume_share",
    "vwap",
    "active_minutes",
)


class MinuteCandidateError(RuntimeError):
    """Raised when candidate evidence is incomplete, stale, or inconsistent."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _distribution(values: Iterable[Any]) -> dict[str, Any]:
    import numpy as np

    array = np.asarray(
        [value for item in values if (value := _finite_float(item)) is not None],
        dtype="float64",
    )
    if array.size == 0:
        return {
            "count": 0,
            "min": None,
            "p01": None,
            "p10": None,
            "median": None,
            "mean": None,
            "p90": None,
            "p99": None,
            "max": None,
        }
    quantiles = np.quantile(array, [0.01, 0.10, 0.50, 0.90, 0.99])
    return {
        "count": int(array.size),
        "min": float(array.min()),
        "p01": float(quantiles[0]),
        "p10": float(quantiles[1]),
        "median": float(quantiles[2]),
        "mean": float(array.mean()),
        "p90": float(quantiles[3]),
        "p99": float(quantiles[4]),
        "max": float(array.max()),
    }


def _campaign_location_entries(manifest: dict[str, Any]) -> Iterable[tuple[str, str | Path]]:
    baseline = manifest.get("baseline", {})
    for group in ("complete", "partial"):
        for trade_date, item in baseline.get(group, {}).items():
            yield str(trade_date), item["data_root"]
    for day in manifest.get("days", []):
        for phase in day.get("phases", []):
            for lane in phase.get("lanes", {}).values():
                for trade_date in lane.get("dates", []):
                    yield str(trade_date), lane["data_root"]


def _register_campaign_location(
    locations: dict[str, Path],
    trade_date: str,
    data_root: str | Path,
) -> None:
    if len(trade_date) != 8 or not trade_date.isdigit():
        raise MinuteCandidateError(f"Invalid campaign trade date: {trade_date!r}")
    root = Path(data_root).expanduser().resolve()
    previous = locations.get(trade_date)
    if previous is not None and previous != root:
        raise MinuteCandidateError(
            f"Campaign date {trade_date} resolves to multiple roots: {previous}, {root}"
        )
    locations[trade_date] = root


def resolve_campaign_locations(manifest: dict[str, Any]) -> dict[str, Path]:
    """Resolve each immutable campaign date to exactly one data root."""

    locations: dict[str, Path] = {}
    for trade_date, data_root in _campaign_location_entries(manifest):
        _register_campaign_location(locations, trade_date, data_root)
    expected = manifest.get("target", {}).get("dates")
    if type(expected) is int and len(locations) != expected:
        raise MinuteCandidateError(
            f"Campaign target mismatch: expected={expected} actual={len(locations)}"
        )
    if not locations:
        raise MinuteCandidateError("Campaign contains no candidate dates")
    return dict(sorted(locations.items()))


def _validated_readiness(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    expected_dates: int,
) -> tuple[Path, dict[str, Any]]:
    readiness_path = manifest_path.parent / "acquisition-readiness.json"
    if not readiness_path.is_file():
        raise MinuteCandidateError(f"Campaign readiness is missing: {readiness_path}")
    payload = _read_json(readiness_path)
    ledger_path = Path(str(manifest["ledger_path"]))
    valid = (
        isinstance(payload, dict)
        and payload.get("schema_version") == READINESS_SCHEMA_VERSION
        and payload.get("manifest_sha256") == file_sha256(manifest_path)
        and payload.get("acquisition_complete") is True
        and payload.get("structural_ready") is True
        and payload.get("semantic_audit") == "pending"
        and payload.get("promotion_ready") is False
        and payload.get("writes_production") is False
        and readiness_summary_is_valid(
            payload,
            manifest_path=manifest_path,
            ledger_path=ledger_path,
            expected_dates=expected_dates,
        )
    )
    if not valid:
        raise MinuteCandidateError(f"Campaign readiness is invalid or stale: {readiness_path}")
    return readiness_path, payload


def _coverage_by_date(coverage_path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    coverage = _read_json(coverage_path)
    if (
        not isinstance(coverage, dict)
        or coverage.get("status") != "passed"
        or coverage.get("quality_status") != "passed"
    ):
        raise MinuteCandidateError(f"Canonical coverage is not passed: {coverage_path}")
    records: dict[str, dict[str, Any]] = {}
    for item in coverage.get("daily", []):
        if not isinstance(item, dict):
            continue
        trade_date = str(item.get("date", ""))
        if trade_date:
            records[trade_date] = item
    return coverage, records


def _validate_partition(data_root: Path, trade_date: str) -> dict[str, Any]:
    return validate_complete_minute_partition(
        data_root / f"trade_date={trade_date}",
        trade_date=trade_date,
        require_full_universe=True,
    )


def _candidate_partition_record(
    trade_date: str,
    data_root: Path,
    canonical: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    canonical_source = str(canonical.get("canonical_source", ""))
    if canonical_source not in {"guan_annual_minbar", "guan_deal"}:
        raise MinuteCandidateError(
            f"Candidate date {trade_date} does not replace Guan: {canonical_source}"
        )
    receipt = _validate_partition(data_root, trade_date)
    partition_path = Path(str(receipt["partition_path"])).resolve()
    sidecar_path = Path(str(receipt["sidecar_path"])).resolve()
    canonical_path = Path(str(canonical["path"])).resolve()
    if not canonical_path.is_file():
        raise MinuteCandidateError(
            f"Canonical partition is missing for {trade_date}: {canonical_path}"
        )
    if canonical.get("content_sha256") != file_sha256(canonical_path):
        raise MinuteCandidateError(
            f"Canonical partition changed after coverage for {trade_date}: {canonical_path}"
        )
    return (
        {
            "trade_date": trade_date,
            "candidate": {
                "data_root": str(data_root),
                "partition_path": str(partition_path),
                "sidecar_path": str(sidecar_path),
                "partition_size": partition_path.stat().st_size,
                "sidecar_size": sidecar_path.stat().st_size,
                "partition_sha256": receipt["partition_sha256"],
                "sidecar_sha256": receipt["sidecar_sha256"],
                "rows": int(receipt["rows"]),
                "symbols": int(receipt["symbols"]),
                "market_symbol_counts": receipt["market_symbol_counts"],
                "expected_bars_per_symbol": int(receipt["expected_bars_per_symbol"]),
                "universe_hash": receipt["universe_hash"],
                "universe_rule": receipt["universe_rule"],
            },
            "canonical": {
                "partition_path": str(canonical_path),
                "partition_size": canonical_path.stat().st_size,
                "partition_sha256": canonical["content_sha256"],
                "rows": int(canonical["rows"]),
                "symbols": int(canonical["symbols"]),
                "canonical_source": canonical_source,
                "tier": canonical["tier"],
                "market_scope": canonical["market_scope"],
                "time_min": canonical["time_min"],
                "time_max": canonical["time_max"],
            },
        },
        canonical_source,
    )


def build_candidate_inventory(
    campaign_manifest: str | Path,
    canonical_coverage: str | Path,
    output_json: str | Path,
) -> dict[str, Any]:
    """Revalidate and freeze every campaign partition into an immutable receipt."""

    manifest_path = Path(campaign_manifest).expanduser().resolve()
    coverage_path = Path(canonical_coverage).expanduser().resolve()
    output_path = Path(output_json).expanduser().resolve()
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != CAMPAIGN_SCHEMA_VERSION:
        raise MinuteCandidateError(f"Unsupported campaign manifest: {manifest_path}")
    locations = resolve_campaign_locations(manifest)
    readiness_path, readiness = _validated_readiness(
        manifest_path,
        manifest,
        expected_dates=len(locations),
    )
    coverage, coverage_records = _coverage_by_date(coverage_path)
    partitions: list[dict[str, Any]] = []
    rows_by_year: Counter[str] = Counter()
    dates_by_source: Counter[str] = Counter()
    dates_by_root: Counter[str] = Counter()
    total_rows = 0
    total_symbols = 0
    for trade_date, data_root in locations.items():
        canonical = coverage_records.get(trade_date)
        if canonical is None:
            raise MinuteCandidateError(
                f"Candidate date is absent from canonical coverage: {trade_date}"
            )
        record, canonical_source = _candidate_partition_record(
            trade_date,
            data_root,
            canonical,
        )
        partitions.append(record)
        rows = int(record["candidate"]["rows"])
        symbols = int(record["candidate"]["symbols"])
        total_rows += rows
        total_symbols += symbols
        rows_by_year[trade_date[:4]] += rows
        dates_by_source[canonical_source] += 1
        dates_by_root[str(data_root)] += 1
    payload = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "status": "passed",
        "generated_at": _now(),
        "writes_production": False,
        "inputs": {
            "campaign_manifest": str(manifest_path),
            "campaign_manifest_sha256": file_sha256(manifest_path),
            "campaign_ledger": str(manifest["ledger_path"]),
            "campaign_ledger_sha256": file_sha256(Path(str(manifest["ledger_path"]))),
            "campaign_readiness": str(readiness_path),
            "campaign_readiness_sha256": file_sha256(readiness_path),
            "campaign_date_receipts_sha256": readiness["date_receipts_sha256"],
            "canonical_coverage": str(coverage_path),
            "canonical_coverage_sha256": file_sha256(coverage_path),
            "canonical_output_dir": coverage["output_dir"],
        },
        "summary": {
            "dates": len(partitions),
            "date_min": partitions[0]["trade_date"],
            "date_max": partitions[-1]["trade_date"],
            "rows": total_rows,
            "symbol_days": total_symbols,
            "dates_by_canonical_source": dict(sorted(dates_by_source.items())),
            "dates_by_data_root": dict(sorted(dates_by_root.items())),
            "rows_by_year": dict(sorted(rows_by_year.items())),
        },
        "partitions_sha256": _json_sha256(partitions),
        "partitions": partitions,
    }
    _atomic_write_json(output_path, payload)
    return payload


def _valid_inventory(inventory: Any) -> bool:
    return bool(
        isinstance(inventory, dict)
        and inventory.get("schema_version") == INVENTORY_SCHEMA_VERSION
        and inventory.get("status") == "passed"
        and inventory.get("partitions_sha256") == _json_sha256(inventory.get("partitions"))
    )


__all__ = [
    "CANDIDATE_RECEIPT_SCHEMA_VERSION",
    "FEATURE_NAMES",
    "INVENTORY_SCHEMA_VERSION",
    "MinuteCandidateError",
    "SEMANTIC_AUDIT_SCHEMA_VERSION",
    "build_candidate_inventory",
    "resolve_campaign_locations",
]
