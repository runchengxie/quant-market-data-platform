"""Deterministic eligibility gate for raw L2 quality reports."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from market_data_platform.dq_receipts import (
    build_dq_receipt,
    worst_eligibility,
    write_dq_receipt,
)

_QUALITY_RULE_SEVERITIES = {"ignore", "research_only", "quarantine"}


def _sum_mapping(value: object) -> int:
    if not isinstance(value, Mapping):
        return 0
    return sum(int(count or 0) for count in value.values())


def _check(check_id: str, count: int, severity: str) -> dict[str, Any]:
    return {
        "id": check_id,
        "passed": count == 0,
        "severity": severity,
        "count": count,
    }


def _sequence_count(reports: list[Mapping[str, Any]], field: str) -> int:
    total = 0
    for report in reports:
        ordering = report.get("ordering")
        if not isinstance(ordering, Mapping):
            continue
        quality = ordering.get("sequence_quality")
        if not isinstance(quality, Mapping):
            continue
        value = quality.get(field, 0)
        total += int(bool(value)) if isinstance(value, bool) else int(value or 0)
    return total


def _apply_quality_rules(
    checks: list[dict[str, Any]],
    dataset_contract: Mapping[str, Any] | None,
) -> None:
    raw_rules = (
        dataset_contract.get("quality_rules", {}) if isinstance(dataset_contract, Mapping) else {}
    )
    rules = raw_rules if isinstance(raw_rules, Mapping) else {}
    for check in checks:
        override = rules.get(check["id"])
        if override is None:
            check["effective_severity"] = check["severity"]
            continue
        severity = str(override)
        if severity not in _QUALITY_RULE_SEVERITIES:
            raise ValueError(f"Unsupported quality rule severity for {check['id']}: {severity}")
        check["effective_severity"] = severity


def evaluate_l2_quality(  # noqa: PLR0913
    scan_payload: Mapping[str, Any],
    *,
    dataset_id: str,
    provider: str,
    run_id: str,
    pilot_manifest: Mapping[str, Any] | None = None,
    dataset_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if scan_payload.get("status") != "complete":
        raise ValueError("L2 quality gate requires a complete scan payload")
    raw_reports = scan_payload.get("reports", [])
    reports = [report for report in raw_reports if isinstance(report, Mapping)]

    checks = [
        _check(
            "required_columns",
            sum(len(report.get("missing_columns", [])) for report in reports),
            "quarantine",
        ),
        _check(
            "trading_day_consistency",
            sum(int(report.get("trading_day_mismatch_rows", 0) or 0) for report in reports),
            "quarantine",
        ),
        _check(
            "exchange_sequence_duplicate",
            _sequence_count(reports, "duplicate_rows"),
            "quarantine",
        ),
        _check(
            "exchange_sequence_backwards",
            _sequence_count(reports, "backwards_rows"),
            "quarantine",
        ),
        _check(
            "exchange_sequence_non_numeric",
            _sequence_count(reports, "non_numeric_rows"),
            "quarantine",
        ),
        _check(
            "exchange_sequence_gaps",
            _sequence_count(reports, "gap_events"),
            "research_only",
        ),
        _check(
            "exchange_sequence_tracking_truncated",
            _sequence_count(reports, "tracking_truncated"),
            "research_only",
        ),
        _check(
            "timestamp_backwards",
            sum(int(report.get("timestamp_backwards", 0) or 0) for report in reports),
            "research_only",
        ),
        _check(
            "duplicate_event_id",
            sum(int(report.get("duplicate_id_rows", 0) or 0) for report in reports),
            "research_only",
        ),
        _check(
            "id_tracking_truncated",
            sum(int(bool(report.get("id_tracking_truncated"))) for report in reports),
            "research_only",
        ),
        _check(
            "null_values",
            sum(_sum_mapping(report.get("nulls")) for report in reports),
            "research_only",
        ),
        _check(
            "nonpositive_values",
            sum(_sum_mapping(report.get("nonpositive")) for report in reports),
            "research_only",
        ),
    ]

    pilot_summary = pilot_manifest.get("summary", {}) if isinstance(pilot_manifest, Mapping) else {}
    excluded = (
        int(pilot_summary.get("exclude", 0) or 0) if isinstance(pilot_summary, Mapping) else 0
    )
    tagged = int(pilot_summary.get("tag", 0) or 0) if isinstance(pilot_summary, Mapping) else 0
    checks.extend(
        [
            _check("pilot_excluded_rows", excluded, "quarantine"),
            _check("pilot_tagged_rows", tagged, "research_only"),
        ]
    )
    _apply_quality_rules(checks, dataset_contract)

    eligibility = "production"
    for check in checks:
        if not check["passed"] and check["effective_severity"] != "ignore":
            eligibility = worst_eligibility(eligibility, check["effective_severity"])
    status = "failed" if eligibility == "quarantine" else "passed"

    contract_dataset = (
        dataset_contract.get("dataset", {}) if isinstance(dataset_contract, Mapping) else {}
    )
    data_version = (
        contract_dataset.get("data_version") if isinstance(contract_dataset, Mapping) else None
    )
    asset_schema_version = (
        str(contract_dataset.get("schema_version") or "unknown")
        if isinstance(contract_dataset, Mapping)
        else "unknown"
    )
    input_rows = sum(int(report.get("rows", 0) or 0) for report in reports)
    ordering_modes = sorted(
        {
            str(ordering.get("ordering_mode"))
            for report in reports
            if isinstance((ordering := report.get("ordering")), Mapping)
            and ordering.get("ordering_mode")
        }
    )
    return build_dq_receipt(
        dataset_id=dataset_id,
        provider=provider,
        data_version=str(data_version) if data_version is not None else None,
        asset_schema_version=asset_schema_version,
        run_id=run_id,
        input_summary={
            "root": scan_payload.get("root"),
            "files": int(scan_payload.get("files_scanned", len(reports)) or 0),
            "rows": input_rows,
        },
        quality={
            "checks": checks,
            "ordering_modes": ordering_modes,
            "pilot_summary": dict(pilot_summary) if isinstance(pilot_summary, Mapping) else {},
        },
        lineage={
            "scan_status": scan_payload.get("status"),
            "dataset_contract_schema": (
                dataset_contract.get("schema_version")
                if isinstance(dataset_contract, Mapping)
                else None
            ),
        },
        status=status,
        eligibility=eligibility,
    )


@dataclass(frozen=True)
class L2GateOptions:
    root: str | Path
    checkpoint: str | Path
    output: str | Path
    dataset_id: str
    provider: str
    run_id: str
    pattern: str = "*.parquet"
    batch_size: int = 262_144
    max_tracked_ids: int = 1_000_000
    pilot_manifest: str | Path | None = None
    dataset_contract: str | Path | None = None
    scan_output: str | Path | None = None


def _read_json(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def run_l2_quality_gate(options: L2GateOptions) -> dict[str, Any]:
    """Scan one raw tree, evaluate eligibility, and atomically write a DQ receipt."""
    from market_data_platform.quality_scan import QualityScanOptions, scan_parquet_tree

    scan_payload = scan_parquet_tree(
        QualityScanOptions(
            root=options.root,
            checkpoint=options.checkpoint,
            output=options.scan_output,
            pattern=options.pattern,
            batch_size=options.batch_size,
            max_tracked_ids=options.max_tracked_ids,
        )
    )
    receipt = evaluate_l2_quality(
        scan_payload,
        dataset_id=options.dataset_id,
        provider=options.provider,
        run_id=options.run_id,
        pilot_manifest=_read_json(options.pilot_manifest),
        dataset_contract=_read_json(options.dataset_contract),
    )
    write_dq_receipt(options.output, receipt)
    return receipt
