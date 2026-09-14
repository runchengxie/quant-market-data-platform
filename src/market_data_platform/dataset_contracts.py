"""Machine-readable dataset semantics shared across market-data assets."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATASET_CONTRACT_SCHEMA = "market_data_platform.dataset_contract.v1"
QUALITY_RULE_SEVERITIES = {"ignore", "research_only", "quarantine"}


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): item for key, item in value.items()}


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must be a non-empty string")
    return text


@dataclass(frozen=True)
class DatasetContract:
    dataset_id: str
    provider: str
    market: str
    asset_schema_version: str
    data_version: str | None = None
    primary_key: tuple[str, ...] = ()
    time: Mapping[str, Any] = field(default_factory=dict)
    units: Mapping[str, Any] = field(default_factory=dict)
    null_semantics: Mapping[str, Any] = field(default_factory=dict)
    sentinels: Mapping[str, Any] = field(default_factory=dict)
    ordering: Mapping[str, Any] = field(default_factory=dict)
    expected_cadence: Mapping[str, Any] = field(default_factory=dict)
    quality_rules: Mapping[str, Any] = field(default_factory=dict)
    pit: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "schema_version": DATASET_CONTRACT_SCHEMA,
            "dataset": {
                "id": self.dataset_id,
                "provider": self.provider,
                "market": self.market,
                "schema_version": self.asset_schema_version,
                "data_version": self.data_version,
            },
            "primary_key": list(self.primary_key),
            "time": _copy_mapping(self.time),
            "units": _copy_mapping(self.units),
            "null_semantics": _copy_mapping(self.null_semantics),
            "sentinels": _copy_mapping(self.sentinels),
            "ordering": _copy_mapping(self.ordering),
            "expected_cadence": _copy_mapping(self.expected_cadence),
            "quality_rules": _copy_mapping(self.quality_rules),
            "pit": _copy_mapping(self.pit),
            "metadata": _copy_mapping(self.metadata),
        }
        return validate_dataset_contract(payload)


def validate_dataset_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if normalized.get("schema_version") != DATASET_CONTRACT_SCHEMA:
        raise ValueError(f"schema_version must be {DATASET_CONTRACT_SCHEMA}")
    dataset = normalized.get("dataset")
    if not isinstance(dataset, Mapping):
        raise ValueError("dataset must be a mapping")
    _required_text(dataset.get("id"), "dataset.id")
    _required_text(dataset.get("provider"), "dataset.provider")
    _required_text(dataset.get("market"), "dataset.market")
    _required_text(dataset.get("schema_version"), "dataset.schema_version")

    primary_key = normalized.get("primary_key", [])
    if not isinstance(primary_key, list) or any(not str(value).strip() for value in primary_key):
        raise ValueError("primary_key must be a list of non-empty strings")
    if len(set(map(str, primary_key))) != len(primary_key):
        raise ValueError("primary_key entries must be unique")

    ordering = normalized.get("ordering", {})
    if not isinstance(ordering, Mapping):
        raise ValueError("ordering must be a mapping")
    if ordering.get("exchange_sequence_available") is True:
        _required_text(ordering.get("sequence_column"), "ordering.sequence_column")

    for name in (
        "time",
        "units",
        "null_semantics",
        "sentinels",
        "expected_cadence",
        "quality_rules",
        "pit",
        "metadata",
    ):
        if not isinstance(normalized.get(name, {}), Mapping):
            raise ValueError(f"{name} must be a mapping")
    quality_rules = normalized.get("quality_rules", {})
    for check_id, severity in quality_rules.items():
        if str(severity) not in QUALITY_RULE_SEVERITIES:
            raise ValueError(
                f"Unsupported quality rule severity for {check_id}: {severity}; "
                f"expected one of {sorted(QUALITY_RULE_SEVERITIES)}"
            )
    return normalized


def write_dataset_contract(path: str | Path, payload: Mapping[str, Any]) -> None:
    output = Path(path).expanduser().resolve()
    validated = validate_dataset_contract(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
