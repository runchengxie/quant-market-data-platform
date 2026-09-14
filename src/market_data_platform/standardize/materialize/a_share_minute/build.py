"""Build and validate the source-neutral A-share one-minute dataset."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from market_data_platform.dataset_lock import minute_dataset_lock
from market_data_platform.standardize.fusion.a_share_minute import (
    LEGACY_GUAN_CANONICAL_UNITS,
    LEGACY_GUAN_HUNDRED_X_UNITS,
)

from .options import (
    _DEAL_CHECKPOINT_SCHEMA_VERSION,
    MinuteFusionBuildOptions,
    _deal_checkpoint_contract,
    _deal_checkpoint_path,
    _discover_legacy_partitions,
    _discover_source_inventory,
    _expanded_path,
    _file_inventory,
    _json_fingerprint,
    _load_deal_checkpoint,
    _require_input_dir,
    _require_input_file,
    _validate_date,
)
from .validation_adapter import (
    _load_symbol_mapping,
    _merge_deal_inputs,
    _merge_tushare_inputs,
    _normalize_legacy_inputs,
    _validate_partition,
    _validate_prevalidated_partition,
)


def validate_fused_minute_dataset(
    output_dir: str | Path,
    *,
    expected_dates: set[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    prevalidated_dates: set[str] | None = None,
) -> dict[str, Any]:
    """Validate every canonical partition and return manifest-ready details."""
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date and end_date must be provided together")
    if start_date is not None and end_date is not None:
        _validate_date(start_date, name="start_date")
        _validate_date(end_date, name="end_date")
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")

    partitions = _discover_legacy_partitions(_expanded_path(output_dir))
    if start_date is not None and end_date is not None:
        partitions = {
            date: path for date, path in partitions.items() if start_date <= date <= end_date
        }
    trusted = prevalidated_dates or set()
    details = [
        (
            _validate_prevalidated_partition(date, path)
            if date in trusted
            else _validate_partition(date, path)
        )
        for date, path in partitions.items()
    ]
    invalid = [item["date"] for item in details if not item["valid"]]
    output_dates = set(partitions)
    expected = set(expected_dates) if expected_dates is not None else None
    missing = sorted(expected.difference(output_dates)) if expected is not None else []
    orphan = sorted(output_dates.difference(expected)) if expected is not None else []
    empty_dataset = not details
    empty_source_inventory = expected is not None and not expected
    failed = bool(invalid or missing or orphan or empty_dataset or empty_source_inventory)
    return {
        "status": "failed" if failed else "passed",
        "partition_count": len(details),
        "rows": sum(int(item["rows"]) for item in details),
        "date_min": details[0]["date"] if details else None,
        "date_max": details[-1]["date"] if details else None,
        "invalid_dates": invalid,
        "missing_dates": missing,
        "orphan_dates": orphan,
        "empty_dataset": empty_dataset,
        "empty_source_inventory": empty_source_inventory,
        "output_dates_in_range": sorted(output_dates),
        "partitions": details,
    }


def _atomic_write_json(payload: dict[str, Any], path: Path) -> None:
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
            json.dump(payload, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _shift_date(value: str, *, days: int) -> str:
    return (datetime.strptime(value, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def _legacy_unit_regimes(options: MinuteFusionBuildOptions) -> list[dict[str, str]]:
    legacy_start = options.start_date
    legacy_end = min(options.end_date, options.legacy_guan_end_date)
    if legacy_start > legacy_end:
        return []

    scaled_start = max(legacy_start, options.hundred_x_start_date)
    scaled_end = min(legacy_end, options.hundred_x_end_date)
    if scaled_start > scaled_end:
        return [
            {
                "source": "legacy_guan",
                "start_date": legacy_start,
                "end_date": legacy_end,
                "profile": LEGACY_GUAN_CANONICAL_UNITS.name,
            }
        ]

    regimes: list[dict[str, str]] = []
    if legacy_start < scaled_start:
        regimes.append(
            {
                "source": "legacy_guan",
                "start_date": legacy_start,
                "end_date": _shift_date(scaled_start, days=-1),
                "profile": LEGACY_GUAN_CANONICAL_UNITS.name,
            }
        )
    regimes.append(
        {
            "source": "legacy_guan",
            "start_date": scaled_start,
            "end_date": scaled_end,
            "profile": LEGACY_GUAN_HUNDRED_X_UNITS.name,
        }
    )
    if scaled_end < legacy_end:
        regimes.append(
            {
                "source": "legacy_guan",
                "start_date": _shift_date(scaled_end, days=1),
                "end_date": legacy_end,
                "profile": LEGACY_GUAN_CANONICAL_UNITS.name,
            }
        )
    return regimes


def _persist_deal_checkpoint(
    action: dict[str, Any],
    *,
    completed_checkpoint_actions: dict[str, Any],
    checkpoint_payload: dict[str, Any],
    checkpoint_path: Path,
) -> None:
    """Record a completed deal action into the resume checkpoint on disk."""
    completed_checkpoint_actions[str(action["date"])] = action
    checkpoint_payload["status"] = "in_progress"
    checkpoint_payload["updated_at"] = datetime.now(UTC).isoformat()
    checkpoint_payload["completed_actions"] = completed_checkpoint_actions
    _atomic_write_json(checkpoint_payload, checkpoint_path)


def _assemble_fused_manifest_payload(  # noqa: PLR0913
    *,
    options: MinuteFusionBuildOptions,
    output_root: Path,
    manifest_path: Path,
    legacy_root: Path,
    guan_deal_root: Path | None,
    tushare_batch_root: Path | None,
    instruments_path: Path | None,
    source_inventory: Any,
    symbol_mapping_stats: dict[str, Any],
    contract_fingerprint: str,
    checkpoint_path: Path,
    deal_results: list[dict[str, Any]],
    tushare_results: list[dict[str, Any]],
    legacy_results: list[dict[str, Any]],
    validation: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the manifest dict emitted by the fused build (no side effects)."""
    return {
        "schema_version": "a_share.minute_1m.fused.v1",
        "dataset": "minute_1m",
        "market": "a_share",
        "provider": "fused",
        "status": "planned" if options.dry_run else validation["status"],
        "generated_at": datetime.now(UTC).isoformat(),
        "output_dir": str(output_root),
        "options": {
            **asdict(options),
            "protected_dates": list(options.protected_dates),
            "annual_override_dates": list(options.annual_override_dates),
            "legacy_input_dir": str(legacy_root),
            "output_dir": str(output_root),
            "manifest_path": str(manifest_path),
            "guan_deal_dir": str(guan_deal_root) if guan_deal_root is not None else None,
            "tushare_batch_dir": (
                str(tushare_batch_root) if tushare_batch_root is not None else None
            ),
            "instruments_path": str(instruments_path) if instruments_path is not None else None,
        },
        "source_priority": [
            *(["tushare"] if source_inventory.tushare_batches else []),
            *(
                ["guan"]
                if source_inventory.legacy
                or source_inventory.guan_deal
                or source_inventory.override_guan_deal
                or source_inventory.protected_guan_deal
                else []
            ),
        ],
        "annual_overrides": {
            "policy": "explicit_whole_day_deal_only",
            "count": len(options.annual_override_dates),
            "dates": sorted(options.annual_override_dates),
        },
        "deal_checkpoint": {
            "schema_version": _DEAL_CHECKPOINT_SCHEMA_VERSION,
            "path": str(checkpoint_path),
            "contract_fingerprint": contract_fingerprint,
            "completed_date_count": sum(
                action.get("status") == "written" for action in deal_results
            ),
            "reused_dates": sorted(
                str(action["date"])
                for action in deal_results
                if action.get("checkpoint_status") == "reused"
            ),
            "written_dates": sorted(
                str(action["date"])
                for action in deal_results
                if action.get("checkpoint_status") == "written"
            ),
        },
        "source_inventory": source_inventory.as_dict(),
        "symbol_mapping": symbol_mapping_stats,
        "unit_regimes": [
            *_legacy_unit_regimes(options),
            {
                "source": "guan_deal",
                "price": "cents_to_yuan",
                "volume": "shares",
                "amount": "yuan",
            },
        ],
        "build_actions": {
            "legacy": legacy_results,
            "guan_deal": deal_results,
            "tushare_batches": tushare_results,
        },
        "validation": validation,
    }


def _finalize_build_checkpoint(  # noqa: PLR0913
    *,
    options: MinuteFusionBuildOptions,
    manifest_path: Path,
    deal_sources: Any,
    deal_results: list[dict[str, Any]],
    validation: dict[str, Any],
    payload: dict[str, Any],
    checkpoint_payload: dict[str, Any],
    checkpoint_path: Path,
) -> None:
    """Write the already-assembled manifest and flip the deal checkpoint state."""
    if options.dry_run:
        return
    _atomic_write_json(payload, manifest_path)
    if deal_sources:
        completed_checkpoint_actions = {
            str(action["date"]): action
            for action in deal_results
            if action.get("status") == "written"
        }
        checkpoint_payload.update(
            {
                "status": "complete" if validation["status"] == "passed" else "failed",
                "updated_at": datetime.now(UTC).isoformat(),
                "error": None,
                "completed_actions": completed_checkpoint_actions,
                "final_manifest": _file_inventory(manifest_path),
            }
        )
        _atomic_write_json(checkpoint_payload, checkpoint_path)


def _build_fused_minute_dataset(options: MinuteFusionBuildOptions) -> dict[str, Any]:
    """Build the canonical dataset in source-priority order and emit a manifest."""
    legacy_root = _require_input_dir(options.legacy_input_dir, name="legacy_input_dir")
    guan_deal_root = (
        _require_input_dir(options.guan_deal_dir, name="guan_deal_dir")
        if options.guan_deal_dir is not None
        else None
    )
    tushare_batch_root = (
        _require_input_dir(options.tushare_batch_dir, name="tushare_batch_dir")
        if options.tushare_batch_dir is not None
        else None
    )
    instruments_path = (
        _require_input_file(options.instruments_path, name="instruments_path")
        if options.instruments_path is not None
        else None
    )
    output_root = _expanded_path(options.output_dir)
    manifest_path = _expanded_path(options.manifest_path)
    if not options.dry_run:
        output_root.mkdir(parents=True, exist_ok=True)

    source_inventory = _discover_source_inventory(
        options,
        legacy_root=legacy_root,
        output_root=output_root,
        guan_deal_root=guan_deal_root,
        tushare_batch_root=tushare_batch_root,
    )
    symbol_mapping, symbol_mapping_stats = _load_symbol_mapping(instruments_path)
    legacy_results = _normalize_legacy_inputs(options, sources=source_inventory.legacy)
    deal_sources = source_inventory.deal_sources
    checkpoint_path = _deal_checkpoint_path(manifest_path)
    checkpoint_contract = _deal_checkpoint_contract(
        options,
        source_inventory,
        instruments_path=instruments_path,
    )
    contract_fingerprint = _json_fingerprint(checkpoint_contract)
    completed_checkpoint_actions = (
        _load_deal_checkpoint(
            checkpoint_path,
            contract_fingerprint=contract_fingerprint,
        )
        if options.resume and not options.dry_run
        else {}
    )
    checkpoint_payload: dict[str, Any] = {
        "schema_version": _DEAL_CHECKPOINT_SCHEMA_VERSION,
        "status": "in_progress",
        "updated_at": datetime.now(UTC).isoformat(),
        "contract_fingerprint": contract_fingerprint,
        "contract": checkpoint_contract,
        "completed_actions": completed_checkpoint_actions,
    }

    if deal_sources and not options.dry_run:
        _atomic_write_json(checkpoint_payload, checkpoint_path)
    try:
        deal_results = _merge_deal_inputs(
            options,
            symbol_mapping,
            source_inventory=source_inventory,
            resume_actions=completed_checkpoint_actions if options.resume else {},
            checkpoint_action=(
                partial(
                    _persist_deal_checkpoint,
                    completed_checkpoint_actions=completed_checkpoint_actions,
                    checkpoint_payload=checkpoint_payload,
                    checkpoint_path=checkpoint_path,
                )
                if deal_sources and not options.dry_run
                else None
            ),
        )
    except Exception as exc:
        if deal_sources and not options.dry_run:
            checkpoint_payload.update(
                {
                    "status": "failed",
                    "updated_at": datetime.now(UTC).isoformat(),
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                    "completed_actions": completed_checkpoint_actions,
                }
            )
            _atomic_write_json(checkpoint_payload, checkpoint_path)
        raise
    deal_results.extend(
        {
            "date": date,
            "status": "audit_only_protected",
            "source": "guan_deal",
            "protected_source": "guan_annual_minbar",
        }
        for date in source_inventory.protected_guan_deal
    )
    deal_results.sort(key=lambda item: str(item["date"]))
    rebuilt_deal_dates = {
        str(result["date"]) for result in deal_results if result["status"] in {"written", "planned"}
    }
    tushare_results = _merge_tushare_inputs(
        options,
        rebuilt_deal_dates=rebuilt_deal_dates,
        sources=source_inventory.tushare_batches,
        legacy_sources=source_inventory.legacy,
    )
    prevalidated_dates = {
        str(result["date"])
        for results in (legacy_results, deal_results, tushare_results)
        for result in results
        if result["status"] == "written"
    }
    validation = (
        {"status": "not_run", "partition_count": 0, "partitions": []}
        if options.dry_run
        else validate_fused_minute_dataset(
            output_root,
            expected_dates=source_inventory.expected_dates,
            start_date=options.start_date,
            end_date=options.end_date,
            prevalidated_dates=prevalidated_dates,
        )
    )
    payload = _assemble_fused_manifest_payload(
        options=options,
        output_root=output_root,
        manifest_path=manifest_path,
        legacy_root=legacy_root,
        guan_deal_root=guan_deal_root,
        tushare_batch_root=tushare_batch_root,
        instruments_path=instruments_path,
        source_inventory=source_inventory,
        symbol_mapping_stats=symbol_mapping_stats,
        contract_fingerprint=contract_fingerprint,
        checkpoint_path=checkpoint_path,
        deal_results=deal_results,
        tushare_results=tushare_results,
        legacy_results=legacy_results,
        validation=validation,
    )
    if options.dry_run:
        return payload
    _finalize_build_checkpoint(
        options=options,
        manifest_path=manifest_path,
        deal_sources=deal_sources,
        deal_results=deal_results,
        validation=validation,
        payload=payload,
        checkpoint_payload=checkpoint_payload,
        checkpoint_path=checkpoint_path,
    )
    return payload
    return _assemble_fused_manifest_payload(
        options=options,
        output_root=output_root,
        manifest_path=manifest_path,
        legacy_root=legacy_root,
        guan_deal_root=guan_deal_root,
        tushare_batch_root=tushare_batch_root,
        instruments_path=instruments_path,
        source_inventory=source_inventory,
        symbol_mapping_stats=symbol_mapping_stats,
        contract_fingerprint=contract_fingerprint,
        checkpoint_path=checkpoint_path,
        deal_results=deal_results,
        tushare_results=tushare_results,
        legacy_results=legacy_results,
        validation=validation,
    )


def build_fused_minute_dataset(options: MinuteFusionBuildOptions) -> dict[str, Any]:
    """Build under the lock shared with partial materialization and cutover."""
    if options.dry_run:
        return _build_fused_minute_dataset(options)
    with minute_dataset_lock(options.output_dir, operation="build-fused-a-share-minutes"):
        return _build_fused_minute_dataset(options)
