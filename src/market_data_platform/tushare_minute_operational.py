"""Publish and promote a TuShare-native operational minute dataset."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from market_data_platform._tushare_minute_campaign_readiness import file_sha256
from market_data_platform.minute_candidate import (
    MinuteCandidateError,
    _atomic_write_json,
    _json_sha256,
    _now,
    _read_json,
)

OPERATIONAL_VERSION_SCHEMA = "a_share.minute_tushare_operational_version.v1"
OPERATIONAL_PROMOTION_SCHEMA = "a_share.minute_tushare_operational_promotion.v1"
EXPECTED_PARTITION_SCHEMA = "tushare.a_share.minute_partition.v3"
OPERATIONAL_ALIAS_NAME = "minute_1m_tushare"
LEGACY_ALIAS_NAME = "minute_1m"


@dataclass(frozen=True)
class OperationalAssembly:
    base_receipt: Path
    incremental_roots: tuple[Path, ...]
    trade_calendar: Path
    end_date: str
    output_dir: Path
    receipt_json: Path
    repair_dates: tuple[str, ...] = ()


def _base_records(receipt: dict[str, Any], receipt_path: Path) -> list[dict[str, Any]]:
    schema_and_status = (
        receipt.get("schema_version"),
        receipt.get("status"),
    )
    if (
        schema_and_status
        not in {
            ("a_share.minute_tushare_candidate.v1", "published_candidate"),
            (OPERATIONAL_VERSION_SCHEMA, "published_operational_version"),
        }
        or receipt.get("current_alias_mutated") is not False
    ):
        raise MinuteCandidateError(f"Invalid TuShare base receipt: {receipt_path}")
    output_dir = Path(receipt["output_dir"]).expanduser().resolve()
    records: list[dict[str, Any]] = []
    for raw in receipt.get("daily", []):
        trade_date = str(raw.get("trade_date", ""))
        source = (output_dir / f"trade_date={trade_date}" / "part-00000.parquet").resolve()
        if not source.is_relative_to(output_dir) or not source.is_file():
            raise MinuteCandidateError(f"Missing base partition: {source}")
        expected_hash = str(raw.get("content_sha256", ""))
        if file_sha256(source) != expected_hash:
            raise MinuteCandidateError(f"Base partition hash mismatch: {source}")
        sidecar = source.parent / "_minute_mirror.json"
        records.append(
            {
                "trade_date": trade_date,
                "rows": int(raw["rows"]),
                "symbols": int(raw["symbols"]),
                "content_sha256": expected_hash,
                "source_partition_path": str(source),
                "source_sidecar_path": str(sidecar) if sidecar.is_file() else None,
                "source_kind": (
                    "audited_candidate"
                    if schema_and_status[0] == "a_share.minute_tushare_candidate.v1"
                    else "prior_operational_version"
                ),
            }
        )
    return _validated_records(records, expected_dates=None)


def _open_dates(
    trade_calendar: Path,
    *,
    after_date: str,
    end_date: str,
) -> list[str]:
    table = pq.read_table(trade_calendar, columns=["cal_date", "is_open"])
    rows = table.to_pylist()
    return sorted(
        str(row["cal_date"])
        for row in rows
        if int(row["is_open"]) == 1 and after_date < str(row["cal_date"]) <= end_date
    )


def _validated_repair_dates(
    trade_calendar: Path,
    requested_dates: Sequence[str],
    *,
    base_dates: set[str],
    base_max: str,
) -> list[str]:
    dates = sorted(str(value) for value in requested_dates)
    if len(dates) != len(set(dates)):
        raise MinuteCandidateError("Operational repair dates are duplicated")
    if not dates:
        return []
    if any(len(value) != 8 or not value.isdigit() for value in dates):
        raise MinuteCandidateError("Operational repair dates must use YYYYMMDD")
    overlap = sorted(set(dates) & base_dates)
    if overlap:
        raise MinuteCandidateError(
            f"Operational repair refuses to replace existing dates: {overlap}"
        )
    if any(value > base_max for value in dates):
        raise MinuteCandidateError("Operational repair dates must not exceed the base date_max")
    open_dates = set(_open_dates(trade_calendar, after_date="", end_date=base_max))
    invalid = sorted(set(dates) - open_dates)
    if invalid:
        raise MinuteCandidateError(
            f"Operational repair dates are not open trading dates: {invalid}"
        )
    return dates


def _sidecar_record(partition_dir: Path, trade_date: str) -> dict[str, Any]:
    sidecar_path = partition_dir / "_minute_mirror.json"
    partition_path = partition_dir / "part-00000.parquet"
    sidecar = _read_json(sidecar_path)
    partition = sidecar.get("partition", {})
    files = partition.get("files", [])
    expected_symbols = sidecar.get("expected_symbols", [])
    completed_symbols = sidecar.get("completed_symbols", [])
    valid = (
        sidecar.get("schema_version") == EXPECTED_PARTITION_SCHEMA
        and sidecar.get("status") == "complete"
        and sidecar.get("trade_date") == trade_date
        and sidecar.get("expected_bars_per_symbol") == 241
        and expected_symbols == completed_symbols
        and sidecar.get("missing_request_symbols") == []
        and partition.get("schema_valid") is True
        and partition.get("key_unique") is True
        and partition.get("trade_date_valid") is True
        and isinstance(files, list)
        and len(files) == 1
        and files[0].get("name") == partition_path.name
        and partition_path.is_file()
        and files[0].get("sha256") == file_sha256(partition_path)
        and int(files[0].get("rows", -1)) == int(partition.get("rows", -2))
    )
    if not valid:
        raise MinuteCandidateError(f"Invalid complete minute partition: {partition_dir}")
    return {
        "trade_date": trade_date,
        "rows": int(partition["rows"]),
        "symbols": len(completed_symbols),
        "content_sha256": str(files[0]["sha256"]),
        "source_partition_path": str(partition_path.resolve()),
        "source_sidecar_path": str(sidecar_path.resolve()),
        "source_sidecar_sha256": file_sha256(sidecar_path),
        "source_kind": "complete_incremental_partition",
    }


def _incremental_records(
    roots: Sequence[Path],
    expected_dates: Sequence[str],
) -> list[dict[str, Any]]:
    records = []
    for trade_date in expected_dates:
        matches = [
            root / f"trade_date={trade_date}"
            for root in roots
            if (root / f"trade_date={trade_date}").is_dir()
        ]
        if len(matches) != 1:
            raise MinuteCandidateError(
                f"Expected exactly one complete source for {trade_date}, found {len(matches)}"
            )
        records.append(_sidecar_record(matches[0], trade_date))
    return _validated_records(records, expected_dates=expected_dates)


def _validated_records(
    records: list[dict[str, Any]],
    *,
    expected_dates: Sequence[str] | None,
) -> list[dict[str, Any]]:
    ordered = sorted(records, key=lambda item: item["trade_date"])
    dates = [item["trade_date"] for item in ordered]
    if not dates or len(dates) != len(set(dates)):
        raise MinuteCandidateError("Minute operational records are empty or duplicated")
    if expected_dates is not None and dates != list(expected_dates):
        raise MinuteCandidateError("Minute operational records do not cover expected dates")
    return ordered


def _operational_payload(
    assembly: OperationalAssembly,
    base_receipt_path: Path,
    base_receipt: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    inputs = {
        "base_receipt": str(base_receipt_path),
        "base_receipt_sha256": file_sha256(base_receipt_path),
        "base_status": base_receipt.get("status"),
        "base_quality_status": base_receipt.get("quality_status"),
        "trade_calendar": str(assembly.trade_calendar),
        "trade_calendar_sha256": file_sha256(assembly.trade_calendar),
        "incremental_roots": [str(root) for root in assembly.incremental_roots],
    }
    if assembly.repair_dates:
        inputs["repair_dates"] = list(assembly.repair_dates)
    return {
        "schema_version": OPERATIONAL_VERSION_SCHEMA,
        "status": "published_operational_version",
        "quality_status": "tushare_native_baseline_required",
        "generated_at": _now(),
        "dataset": "a_share_minute_1m_tushare",
        "provider": "tushare",
        "output_dir": str(assembly.output_dir),
        "writes_versioned_asset": True,
        "current_alias_mutated": False,
        "legacy_canonical_mutated": False,
        "policy": {
            "source_values": "preserved_no_shift_no_fill_no_clipping",
            "cross_source_equivalence_required": False,
            "tushare_native_rebaseline_required": True,
            "guan_status": "legacy_canonical",
        },
        "inputs": inputs,
        "summary": {
            "dates": len(records),
            "date_min": records[0]["trade_date"],
            "date_max": records[-1]["trade_date"],
            "rows": sum(int(item["rows"]) for item in records),
            "symbol_days": sum(int(item["symbols"]) for item in records),
            "market_scope": "SH_SZ_BJ",
        },
        "daily_sha256": _json_sha256(records),
        "daily": records,
    }


def _link_operational_tree(records: Sequence[dict[str, Any]], staging_dir: Path) -> None:
    for record in records:
        destination_dir = staging_dir / f"trade_date={record['trade_date']}"
        destination_dir.mkdir(parents=True)
        source = Path(record["source_partition_path"])
        destination = destination_dir / "part-00000.parquet"
        os.link(source, destination)
        if source.stat().st_ino != destination.stat().st_ino:
            raise MinuteCandidateError(f"Operational hardlink mismatch: {destination}")
        sidecar = record.get("source_sidecar_path")
        if sidecar:
            os.link(Path(sidecar), destination_dir / "_minute_mirror.json")


def assemble_operational_version(
    assembly: OperationalAssembly,
) -> dict[str, Any]:
    """Assemble an immutable TuShare-native version without changing aliases."""

    base_receipt_path = assembly.base_receipt.expanduser().resolve()
    roots = tuple(root.expanduser().resolve() for root in assembly.incremental_roots)
    calendar_path = assembly.trade_calendar.expanduser().resolve()
    destination = assembly.output_dir.expanduser().resolve()
    receipt_path = assembly.receipt_json.expanduser().resolve()
    base_receipt = _read_json(base_receipt_path)
    base_records = _base_records(base_receipt, base_receipt_path)
    base_dates = {str(record["trade_date"]) for record in base_records}
    repair_dates = _validated_repair_dates(
        calendar_path,
        assembly.repair_dates,
        base_dates=base_dates,
        base_max=base_records[-1]["trade_date"],
    )
    extension_dates = _open_dates(
        calendar_path,
        after_date=base_records[-1]["trade_date"],
        end_date=assembly.end_date,
    )
    expected_dates = sorted(repair_dates + extension_dates)
    extension = _incremental_records(roots, expected_dates)
    records = _validated_records(base_records + extension, expected_dates=None)
    payload = _operational_payload(
        assembly,
        base_receipt_path=base_receipt_path,
        base_receipt=base_receipt,
        records=records,
    )
    if destination.exists():
        embedded = destination / "_operational_receipt.json"
        embedded_payload = _read_json(embedded) if embedded.is_file() else {}
        if embedded_payload.get("daily_sha256") == payload["daily_sha256"]:
            _atomic_write_json(receipt_path, embedded_payload)
            return embedded_payload
        raise MinuteCandidateError(f"Operational output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
    try:
        staging_dir.mkdir()
        _link_operational_tree(records, staging_dir)
        _atomic_write_json(staging_dir / "_operational_receipt.json", payload)
        os.replace(staging_dir, destination)
        _atomic_write_json(receipt_path, payload)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
    return payload


def _replace_operational_alias(alias: Path, target: Path) -> None:
    if alias.name != OPERATIONAL_ALIAS_NAME:
        raise MinuteCandidateError(f"Operational alias must be named {OPERATIONAL_ALIAS_NAME}")
    if alias.parent != target.parent:
        raise MinuteCandidateError("Operational alias and version must share a parent")
    if alias.exists() and not alias.is_symlink() and not alias.is_dir():
        raise MinuteCandidateError(f"Refusing to replace non-directory alias: {alias}")
    temporary = alias.parent / f".{alias.name}.tmp-{uuid.uuid4().hex}"
    shutil.copytree(target, temporary, symlinks=False)
    if alias.is_symlink() or alias.is_file():
        alias.unlink()
    elif alias.is_dir():
        shutil.rmtree(alias)
    os.replace(temporary, alias)


def promote_operational_alias(
    version_receipt_json: str | Path,
    alias_path: str | Path,
    legacy_alias_path: str | Path,
    promotion_receipt_json: str | Path,
) -> dict[str, Any]:
    """Promote the TuShare-native alias while proving Guan remains unchanged."""

    version_receipt_path = Path(version_receipt_json).expanduser().resolve()
    alias = Path(alias_path).expanduser().absolute()
    legacy_alias = Path(legacy_alias_path).expanduser().absolute()
    promotion_path = Path(promotion_receipt_json).expanduser().resolve()
    receipt = _read_json(version_receipt_path)
    output_dir = Path(receipt.get("output_dir", "")).expanduser().resolve()
    if (
        receipt.get("schema_version") != OPERATIONAL_VERSION_SCHEMA
        or receipt.get("status") != "published_operational_version"
        or not output_dir.is_dir()
        or legacy_alias.name != LEGACY_ALIAS_NAME
    ):
        raise MinuteCandidateError(f"Invalid operational version receipt: {version_receipt_path}")
    embedded = output_dir / "_operational_receipt.json"
    if not embedded.is_file() or file_sha256(embedded) != file_sha256(version_receipt_path):
        raise MinuteCandidateError("Operational version receipt does not match embedded evidence")
    legacy_before = legacy_alias.resolve(strict=True)
    _replace_operational_alias(alias, output_dir)
    if alias.is_symlink() or alias.resolve(strict=True) == output_dir:
        raise MinuteCandidateError("Operational promotion must leave an entity alias")
    if legacy_alias.resolve(strict=True) != legacy_before:
        raise MinuteCandidateError("Operational promotion alias verification failed")
    payload = {
        "schema_version": OPERATIONAL_PROMOTION_SCHEMA,
        "status": "operational_canonical",
        "generated_at": _now(),
        "provider": "tushare",
        "operational_scope": "tushare_native",
        "alias_path": str(alias),
        "resolved_path": str(output_dir),
        "version_receipt": str(version_receipt_path),
        "version_receipt_sha256": file_sha256(version_receipt_path),
        "legacy_canonical": {
            "provider": "guan",
            "alias_path": str(legacy_alias),
            "resolved_path": str(legacy_before),
            "mutated": False,
        },
        "policy": receipt["policy"],
        "summary": receipt["summary"],
    }
    _atomic_write_json(promotion_path, payload)
    return payload


__all__ = [
    "OperationalAssembly",
    "assemble_operational_version",
    "promote_operational_alias",
]
