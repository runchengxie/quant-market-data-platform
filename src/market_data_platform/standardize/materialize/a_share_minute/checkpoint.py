"""Resumable execution-state checkpoints for A-share minute materialization."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from market_data_platform.standardize.fusion.a_share_minute import CANONICAL_MINUTE_SCHEMA

from .inventory import _file_inventory, _MinuteSourceInventory
from .options import MinuteFusionBuildOptions, _expanded_path

_DEAL_CHECKPOINT_SCHEMA_VERSION = "a_share.minute_1m.deal_checkpoint.v1"
_DEAL_TRANSFORM_CONTRACT_VERSION = "guan.deal_minute.transform.v2"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deal_checkpoint_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(f".{manifest_path.name}.deal-checkpoint.json")


def _deal_checkpoint_contract(
    options: MinuteFusionBuildOptions,
    source_inventory: _MinuteSourceInventory,
    *,
    instruments_path: Path | None,
) -> dict[str, Any]:
    legacy_root = _expanded_path(options.legacy_input_dir)
    output_root = _expanded_path(options.output_dir)
    manifest_path = _expanded_path(options.manifest_path)
    return {
        "transform_contract_version": _DEAL_TRANSFORM_CONTRACT_VERSION,
        "start_date": options.start_date,
        "end_date": options.end_date,
        "guan_deal_start_date": options.guan_deal_start_date,
        "deal_engine": options.deal_engine,
        "deal_batch_row_groups": options.deal_batch_row_groups,
        "deal_dates": sorted(source_inventory.deal_sources),
        "annual_override_dates": sorted(options.annual_override_dates),
        "protected_dates": sorted(options.protected_dates),
        "legacy_input_dir": str(legacy_root),
        "output_dir": str(output_root),
        "manifest_path": str(manifest_path),
        "instruments": _file_inventory(instruments_path) if instruments_path is not None else None,
        "legacy_sources": {
            date: _file_inventory(path) for date, path in sorted(source_inventory.legacy.items())
        },
    }


def _output_checkpoint_signature(path: Path) -> dict[str, Any]:
    parquet_file = pq.ParquetFile(path)
    if not parquet_file.schema_arrow.equals(CANONICAL_MINUTE_SCHEMA):
        raise ValueError(f"Cannot checkpoint non-canonical deal output: {path}")
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "rows": int(parquet_file.metadata.num_rows),
        "sha256": _sha256_file(path),
    }


def _override_receipt_is_valid(action: Mapping[str, Any], date: str) -> bool:
    return (
        action.get("replacement_policy") == "explicit_whole_day_deal_only"
        and action.get("replaced_source") == "guan_annual_minbar"
        and isinstance(session := action.get("session_validation"), Mapping)
        and session.get("valid") is True
        and session.get("time_min") == f"{date[:4]}-{date[4:6]}-{date[6:]} 09:30:00"
        and session.get("time_max") == f"{date[:4]}-{date[4:6]}-{date[6:]} 15:00:00"
        and int(session.get("opening_bar_rows", 0)) >= 1
        and int(session.get("closing_bar_rows", 0)) >= 1
        and isinstance(issues := session.get("issues"), Mapping)
        and not any(int(value) for value in issues.values())
    )


def _checkpoint_action_metadata_is_current(
    action: Mapping[str, Any],
    *,
    date: str,
    source: Path,
    whole_day_override: bool,
) -> bool:
    return (
        action.get("date") == date
        and action.get("status") == "written"
        and action.get("source_signature") == _file_inventory(source)
        and whole_day_override == bool(action.get("whole_day_annual_override", False))
        and (not whole_day_override or _override_receipt_is_valid(action, date))
    )


def _checkpoint_output_is_current(
    aggregation: Mapping[str, Any],
    signature: Mapping[str, Any],
    output_path: Path,
) -> bool:
    if not output_path.is_file() or signature.get("path") != str(output_path):
        return False
    try:
        parquet_file = pq.ParquetFile(output_path)
        stat = output_path.stat()
    except (OSError, ValueError):
        return False
    if not parquet_file.schema_arrow.equals(CANONICAL_MINUTE_SCHEMA):
        return False
    rows = int(parquet_file.metadata.num_rows)
    return (
        rows == int(signature.get("rows", -1))
        and rows == int(aggregation.get("output_rows", -2))
        and stat.st_size == int(signature.get("size", -1))
        and stat.st_mtime_ns == int(signature.get("mtime_ns", -1))
        and isinstance(expected_sha256 := signature.get("sha256"), str)
        and _sha256_file(output_path) == expected_sha256
    )


def _checkpoint_action_is_current(
    action: Mapping[str, Any],
    *,
    date: str,
    source: Path,
    output_path: Path,
    whole_day_override: bool,
) -> bool:
    if not _checkpoint_action_metadata_is_current(
        action,
        date=date,
        source=source,
        whole_day_override=whole_day_override,
    ):
        return False
    aggregation = action.get("aggregation")
    signature = action.get("output_signature")
    if not isinstance(aggregation, Mapping) or not isinstance(signature, Mapping):
        return False
    return _checkpoint_output_is_current(aggregation, signature, output_path)


def _load_deal_checkpoint(
    path: Path,
    *,
    contract_fingerprint: str,
) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != _DEAL_CHECKPOINT_SCHEMA_VERSION
        or payload.get("contract_fingerprint") != contract_fingerprint
    ):
        return {}
    actions = payload.get("completed_actions")
    if not isinstance(actions, Mapping):
        return {}
    return {
        str(date): dict(action) for date, action in actions.items() if isinstance(action, Mapping)
    }


def _atomic_write_checkpoint_json(payload: Mapping[str, Any], path: Path) -> None:
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
            json.dump(dict(payload), temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
