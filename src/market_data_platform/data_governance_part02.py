"""Conservative, inode-aware retention planning for local data artifacts.

This module only inventories paths and classifies retention candidates.  It
does not unlink, rename, or otherwise mutate any artifact.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from typing import cast

from market_data_platform.data_governance_part01 import (
    ExplicitPathRule,
    GovernanceRule,
    JsonStatusRule,
    PlanItem,
    RetainNewestRule,
    _validate_inventory_schema,
)


def _candidate_statuses(rule: Mapping[str, object], *, index: int) -> frozenset[str]:
    raw_statuses = rule.get("candidate_statuses")
    if not isinstance(raw_statuses, list) or any(
        not isinstance(value, str) or not value for value in raw_statuses
    ):
        raise ValueError(f"retention_rules[{index}].candidate_statuses must be a string list")
    return frozenset(cast(list[str], raw_statuses))


def _rule_from_inventory(raw_rule: object, *, index: int) -> GovernanceRule:
    if not isinstance(raw_rule, Mapping):
        raise ValueError(f"retention_rules[{index}] must be an object")
    rule = {str(key): value for key, value in raw_rule.items()}
    name = _required_string(rule, "name", index=index)
    kind = _required_string(rule, "kind", index=index)
    if kind == "explicit_path":
        disposition = _required_string(rule, "disposition", index=index)
        if disposition not in {"keep", "retire_candidate", "review"}:
            raise ValueError(
                f"retention_rules[{index}].disposition is unsupported: {disposition!r}"
            )
        return ExplicitPathRule(
            name=name,
            path=_required_string(rule, "path", index=index),
            disposition=disposition,
        )
    if kind == "retain_newest":
        retain_newest = rule.get("retain_newest")
        if isinstance(retain_newest, bool) or not isinstance(retain_newest, int):
            raise ValueError(f"retention_rules[{index}].retain_newest must be an integer")
        return RetainNewestRule(
            name=name,
            parent=_required_string(rule, "parent", index=index),
            pattern=_required_string(rule, "pattern", index=index),
            retain_newest=retain_newest,
        )
    if kind == "json_status":
        return JsonStatusRule(
            name=name,
            parent=_required_string(rule, "parent", index=index),
            pattern=_required_string(rule, "pattern", index=index),
            candidate_statuses=_candidate_statuses(rule, index=index),
        )
    raise ValueError(f"retention_rules[{index}].kind is unsupported: {kind!r}")


def rules_from_inventory(payload: Mapping[str, object]) -> list[GovernanceRule]:
    """Validate and materialize retention rules from a lifecycle inventory."""

    _validate_inventory_schema(payload)
    raw_rules = payload.get("retention_rules")
    if not isinstance(raw_rules, list):
        raise ValueError("lifecycle inventory retention_rules must be a list")
    return [_rule_from_inventory(raw_rule, index=index) for index, raw_rule in enumerate(raw_rules)]


def _required_string(rule: Mapping[str, object], key: str, *, index: int) -> str:
    value = rule.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"retention_rules[{index}].{key} must be a non-empty string")
    return value


def render_retention_tsv(items: Sequence[PlanItem]) -> str:
    """Render plan items as stable, tab-separated audit output."""

    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(
        (
            "action",
            "rule",
            "logical_bytes",
            "allocated_bytes",
            "reclaimable_bytes",
            "files",
            "unique_inodes",
            "external_hardlink_inodes",
            "path",
            "reason",
            "status",
        )
    )
    for item in items:
        writer.writerow(
            (
                item.action,
                item.rule,
                item.usage.logical_bytes,
                item.usage.allocated_bytes,
                item.usage.reclaimable_bytes,
                item.usage.files,
                item.usage.unique_inodes,
                item.usage.external_hardlink_inodes,
                str(item.path),
                item.reason,
                item.status or "",
            )
        )
    return output.getvalue()
