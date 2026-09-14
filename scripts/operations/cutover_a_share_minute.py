#!/usr/bin/env python3
"""Validate the production receipt and atomically install the minute current link."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from market_data_platform.dataset_lock import minute_dataset_lock
from market_data_platform.providers.a_share_minute_price_flow import (
    NOTIONAL_HARD_GUARD_ISSUE,
    POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
    VWAP_EXTREME_UNIT_SCALE_ISSUE,
    VWAP_OHLC_DIAGNOSTIC,
    VWAP_SOURCE_GUARD_ISSUE,
    ZERO_VOLUME_NONZERO_AMOUNT_ISSUE,
    tushare_price_flow_policy,
)

_PARTITION_PATTERN = re.compile(r"trade_date=(\d{8})$")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_AT_FDCWD = -100
_RENAME_EXCHANGE = 2
_HASH_CHUNK_BYTES = 8 * 1024 * 1024

_PRODUCTION_DATE_MIN = "20160104"
_PRODUCTION_DATE_MAX = "20260714"
_BSE_FIRST_TRADE_DATE = "20211115"
_TUSHARE_FULL_DATE_COUNT = 89
_BJ_OVERLAY_DATE_COUNT = 1_041
_TARGET_MARKET_SCOPE_FULL_A_SHARE = "full-a-share"
_TARGET_MARKET_SCOPE_SH_SZ = "sh-sz"
_TARGET_MARKET_SCOPES = (
    _TARGET_MARKET_SCOPE_FULL_A_SHARE,
    _TARGET_MARKET_SCOPE_SH_SZ,
)
_TUSHARE_FULL_RECEIPT_SCHEMA = "a_share.minute_tushare_full_day_receipt.v2"
_TUSHARE_BJ_OVERLAY_RECEIPT_SCHEMA = "a_share.minute_tushare_bj_overlay_receipt.v2"
_PRODUCTION_TIER_COUNTS = {
    "annual_full_sh_sz": 2_430,
    "deal_full_sh_sz": 37,
    "tushare_full_a_share": _TUSHARE_FULL_DATE_COUNT,
}
_PRODUCTION_DATE_COUNT = sum(_PRODUCTION_TIER_COUNTS.values())
_SH_SZ_FULL_A_SHARE_DATE_COUNT = _PRODUCTION_DATE_COUNT - _BJ_OVERLAY_DATE_COUNT
_PRODUCTION_REQUIREMENTS = {
    "expected_trade_dates": _PRODUCTION_DATE_COUNT,
    "expected_annual_full_sh_sz_dates": _PRODUCTION_TIER_COUNTS["annual_full_sh_sz"],
    "expected_deal_full_sh_sz_dates": _PRODUCTION_TIER_COUNTS["deal_full_sh_sz"],
    "expected_tushare_full_a_share_dates": _TUSHARE_FULL_DATE_COUNT,
    "expected_guan_partial_session_dates": 0,
    "expected_partial_top200_dates": 0,
    "expected_accepted_zero_volume_nonzero_amount_rows": 136_246,
    "expected_accepted_positive_volume_zero_amount_rows": 1,
    "require_full_source_stats": True,
}
_PRODUCTION_SUMMARY = {
    "calendar_dates": _PRODUCTION_DATE_COUNT,
    "date_min": _PRODUCTION_DATE_MIN,
    "date_max": _PRODUCTION_DATE_MAX,
    "partition_files": _PRODUCTION_DATE_COUNT,
    "partition_dirs": _PRODUCTION_DATE_COUNT,
    **{f"{tier}_dates": count for tier, count in _PRODUCTION_TIER_COUNTS.items()},
    "bj_overlay_date_count": _BJ_OVERLAY_DATE_COUNT,
    "tushare_bj_overlay_dates": _BJ_OVERLAY_DATE_COUNT,
    "guan_partial_session_dates": 0,
    "full_sh_sz_dates": _PRODUCTION_DATE_COUNT,
    "full_a_share_dates": _PRODUCTION_DATE_COUNT,
    "tushare_partial_top200_dates": 0,
    "missing_source_dates": 0,
    "missing_output_dates": 0,
    "invalid_output_dates": 0,
    "orphan_output_dates": 0,
    "accepted_guan_zero_volume_nonzero_amount_rows": 136_246,
    "accepted_guan_positive_volume_zero_amount_rows": 1,
}
_SH_SZ_PRODUCTION_SUMMARY = {
    field: value
    for field, value in _PRODUCTION_SUMMARY.items()
    if field not in {"bj_overlay_date_count", "tushare_bj_overlay_dates"}
}
_SH_SZ_PRODUCTION_SUMMARY["full_a_share_dates"] = _SH_SZ_FULL_A_SHARE_DATE_COUNT


class MinuteCutoverError(RuntimeError):
    """Raised when a cutover precondition or atomic operation fails."""


def _load_manifest(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MinuteCutoverError(f"Cannot read coverage manifest {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise MinuteCutoverError(f"Coverage manifest is not a mapping: {path}")
    return payload


def _contains_failure(value: Any) -> bool:
    if value is None or value is False or value == 0 or value == "":
        return False
    if isinstance(value, Mapping):
        return any(_contains_failure(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_failure(item) for item in value)
    return True


def _valid_tushare_price_flow_policy(value: Any) -> bool:
    return isinstance(value, Mapping) and dict(value) == dict(tushare_price_flow_policy())


def _valid_tushare_price_flow_receipt(
    source: Mapping[str, Any],
    *,
    diagnostics_field: str,
) -> bool:
    diagnostics = source.get(diagnostics_field)
    accepted = source.get("accepted_diagnostics")
    expected_names = {
        VWAP_OHLC_DIAGNOSTIC,
        VWAP_SOURCE_GUARD_ISSUE,
        NOTIONAL_HARD_GUARD_ISSUE,
        VWAP_EXTREME_UNIT_SCALE_ISSUE,
        ZERO_VOLUME_NONZERO_AMOUNT_ISSUE,
        POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
    }
    if (
        not isinstance(diagnostics, Mapping)
        or set(diagnostics) != expected_names
        or not isinstance(accepted, Mapping)
    ):
        return False
    if any(
        not isinstance(diagnostics[name], int) or isinstance(diagnostics[name], bool)
        for name in expected_names
    ):
        return False
    counts = {name: diagnostics[name] for name in expected_names}
    if any(count < 0 for count in counts.values()):
        return False
    expected_accepted = {
        name: counts[name]
        for name in (
            VWAP_OHLC_DIAGNOSTIC,
            VWAP_SOURCE_GUARD_ISSUE,
            POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE,
        )
        if counts[name]
    }
    return (
        counts[NOTIONAL_HARD_GUARD_ISSUE] == 0
        and counts[VWAP_EXTREME_UNIT_SCALE_ISSUE] == 0
        and counts[ZERO_VOLUME_NONZERO_AMOUNT_ISSUE] == 0
        and dict(accepted) == expected_accepted
        and _valid_tushare_price_flow_policy(source.get("tushare_price_flow_validation"))
    )


def _absolute_endpoint(value: str | Path) -> Path:
    """Resolve the parent while preserving the final component for symlink checks."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.parent.resolve() / path.name


def _lexical_absolute(value: str | Path) -> Path:
    """Normalize dots without following any symlink in the supplied path."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return Path(os.path.abspath(path))


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _real_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_target_market_scope(target_market_scope: str) -> None:
    if target_market_scope not in _TARGET_MARKET_SCOPES:
        raise MinuteCutoverError(
            f"Unsupported target market scope {target_market_scope!r}; "
            f"expected one of {_TARGET_MARKET_SCOPES}"
        )


def _validate_sh_sz_gap_contract(
    payload: Mapping[str, Any],
    *,
    inputs: Mapping[str, Any],
    lineage: Mapping[str, Any],
) -> None:
    known_gaps = payload.get("known_gaps")
    bj_market = known_gaps.get("bj_market") if isinstance(known_gaps, Mapping) else None
    if not isinstance(known_gaps, Mapping) or bj_market != {
        "status": "point_in_time_partial_coverage",
        "affected_trade_dates": _BJ_OVERLAY_DATE_COUNT,
    }:
        raise MinuteCutoverError(
            "SH/SZ production cutover requires the exact transparent "
            f"{_BJ_OVERLAY_DATE_COUNT}-date BJ known gap"
        )
    if known_gaps.get("partial_top200_dates") != []:
        raise MinuteCutoverError("SH/SZ production cutover forbids partial Top200 dates")
    guan_partial = known_gaps.get("guan_partial_session")
    if not isinstance(guan_partial, Mapping) or (
        guan_partial.get("affected_trade_dates") != 0 or guan_partial.get("dates") != []
    ):
        raise MinuteCutoverError("SH/SZ production cutover forbids truncated Guan sessions")

    forbidden_input_fields = {
        "bj_overlay_date_count",
        "bj_overlay_dates",
        "bj_overlay_selected_dates",
    }
    present_inputs = forbidden_input_fields.intersection(inputs)
    forbidden_lineage = sorted(
        field for field in lineage if str(field).startswith("tushare_bj_overlay_")
    )
    if present_inputs or forbidden_lineage:
        raise MinuteCutoverError(
            "SH/SZ production cutover must record BJ as a known gap, not bind BJ overlay evidence"
        )
    if inputs.get("tushare_full_selected_dates") != _TUSHARE_FULL_DATE_COUNT:
        raise MinuteCutoverError(
            f"SH/SZ production cutover lacks all {_TUSHARE_FULL_DATE_COUNT} full-day selections"
        )

    summary = payload.get("summary")
    policy = payload.get("policy")
    source_priority = policy.get("source_priority") if isinstance(policy, Mapping) else None
    if not isinstance(summary, Mapping) or {
        "bj_overlay_date_count",
        "tushare_bj_overlay_dates",
    }.intersection(summary):
        raise MinuteCutoverError("SH/SZ production summary must not claim BJ overlays")
    if (
        not isinstance(policy, Mapping)
        or policy.get("full_market_definition")
        != "point_in_time_A_share; TuShare full days include SH_SZ_BJ"
        or not isinstance(source_priority, list)
        or "tushare_full_day_explicit_whole_day_replacement" not in source_priority
        or any("bj_overlay" in str(source) for source in source_priority)
    ):
        raise MinuteCutoverError("SH/SZ production policy does not expose the BJ coverage gap")


def _validate_coverage_contract_fields(
    payload: Mapping[str, Any],
    *,
    target_market_scope: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Validate shared coverage fields and return inputs plus source lineage."""
    expected_coverage_status = (
        "full_a_share" if target_market_scope == _TARGET_MARKET_SCOPE_FULL_A_SHARE else "full_sh_sz"
    )
    if payload.get("coverage_status") != expected_coverage_status:
        if target_market_scope == _TARGET_MARKET_SCOPE_FULL_A_SHARE:
            raise MinuteCutoverError(
                "Coverage receipt is not the fixed full-A production contract; "
                "partial and SH/SZ-only versions must remain staging-only unless "
                "--target-market-scope sh-sz is explicit"
            )
        raise MinuteCutoverError(
            "Coverage receipt is not the fixed full-Shanghai/Shenzhen production contract"
        )

    requirements = payload.get("requirements")
    if not isinstance(requirements, Mapping):
        raise MinuteCutoverError("Coverage receipt lacks production requirements")
    for field, expected in _PRODUCTION_REQUIREMENTS.items():
        if requirements.get(field) != expected:
            raise MinuteCutoverError(
                f"Coverage requirement {field} is not the production value {expected!r}"
            )

    summary = payload.get("summary")
    if not isinstance(summary, Mapping):
        raise MinuteCutoverError("Coverage receipt lacks a production summary")
    expected_summary = (
        _PRODUCTION_SUMMARY
        if target_market_scope == _TARGET_MARKET_SCOPE_FULL_A_SHARE
        else _SH_SZ_PRODUCTION_SUMMARY
    )
    for field, expected in expected_summary.items():
        if summary.get(field) != expected:
            raise MinuteCutoverError(
                f"Coverage summary {field} is not the production value {expected!r}"
            )

    inputs = payload.get("inputs")
    lineage = inputs.get("lineage") if isinstance(inputs, Mapping) else None
    if not isinstance(inputs, Mapping) or not isinstance(lineage, Mapping):
        raise MinuteCutoverError("Coverage receipt lacks source lineage")
    if lineage.get("tushare_full_phase") != "production":
        raise MinuteCutoverError(
            "Full-A production cutover requires production-phase full-day TuShare lineage"
        )

    policy = payload.get("policy")
    if not isinstance(policy, Mapping) or policy.get("overlap_policy") != (
        "explicit_whole_day_replacement_only_no_intraday_merge"
    ):
        raise MinuteCutoverError(
            "Full-A production cutover requires the no-intraday-merge replacement policy"
        )
    return inputs, lineage


def _validate_production_contract(
    payload: Mapping[str, Any],
    *,
    target_market_scope: str,
) -> Mapping[str, int]:
    _validate_target_market_scope(target_market_scope)
    inputs, lineage = _validate_coverage_contract_fields(
        payload,
        target_market_scope=target_market_scope,
    )
    if target_market_scope == _TARGET_MARKET_SCOPE_SH_SZ:
        _validate_sh_sz_gap_contract(payload, inputs=inputs, lineage=lineage)
        return _PRODUCTION_TIER_COUNTS

    if (
        inputs.get("bj_overlay_date_count") != _BJ_OVERLAY_DATE_COUNT
        or inputs.get("bj_overlay_selected_dates") != _BJ_OVERLAY_DATE_COUNT
    ):
        raise MinuteCutoverError(
            f"Coverage receipt lacks the fixed {_BJ_OVERLAY_DATE_COUNT}-date BJ overlay input"
        )
    if (
        lineage.get("tushare_bj_overlay_phase") != "production"
        or lineage.get("tushare_bj_overlay_status") != "passed"
    ):
        raise MinuteCutoverError(
            "Full-A production cutover requires a passed production-phase BJ overlay receipt"
        )

    overlay_policy = lineage.get("tushare_bj_overlay_policy")
    if not isinstance(overlay_policy, Mapping) or (
        overlay_policy.get("source_validation") != "passed"
        or overlay_policy.get("same_security_intraday_merge") != "forbidden"
        or overlay_policy.get("market_scope_after_overlay") != "SH_SZ_BJ"
        or not str(overlay_policy.get("required_universe_rule", "")).endswith(":exchange=BJ")
        or not _valid_tushare_price_flow_policy(overlay_policy.get("tushare_price_flow_validation"))
    ):
        raise MinuteCutoverError("Coverage receipt has an invalid BJ overlay policy")
    return _PRODUCTION_TIER_COUNTS


def _validated_date_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MinuteCutoverError(f"{label} must be a JSON date list")
    dates: list[str] = []
    for item in value:
        try:
            parsed = datetime.strptime(item, "%Y%m%d")
        except ValueError as exc:
            raise MinuteCutoverError(f"{label} contains an invalid date: {item!r}") from exc
        if parsed.strftime("%Y%m%d") != item:
            raise MinuteCutoverError(f"{label} contains an invalid date: {item!r}")
        dates.append(item)
    if dates != sorted(set(dates)):
        raise MinuteCutoverError(f"{label} must be sorted and unique")
    return dates


def _load_hashed_lineage_artifact(
    lineage: Mapping[str, Any],
    *,
    path_field: str,
    hash_field: str,
    label: str,
) -> tuple[Path, Mapping[str, Any]]:
    raw_path = lineage.get(path_field)
    expected_hash = lineage.get(hash_field)
    if not isinstance(raw_path, str) or not isinstance(expected_hash, str):
        raise MinuteCutoverError(f"Coverage lineage lacks the {label} path/hash binding")
    if _SHA256_PATTERN.fullmatch(expected_hash) is None:
        raise MinuteCutoverError(f"Coverage lineage has an invalid {label} SHA-256")
    path = _absolute_endpoint(raw_path)
    if not _regular_file(path):
        raise MinuteCutoverError(f"Coverage lineage {label} must be a real file: {path}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise MinuteCutoverError(f"Cannot read {label} {path}: {exc}") from exc
    actual_hash = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(expected_hash, actual_hash):
        raise MinuteCutoverError(f"Coverage lineage {label} hash changed after coverage audit")
    try:
        artifact = json.loads(content)
    except json.JSONDecodeError as exc:
        raise MinuteCutoverError(f"Coverage lineage {label} is invalid JSON: {path}") from exc
    if not isinstance(artifact, Mapping):
        raise MinuteCutoverError(f"Coverage lineage {label} is not a JSON object: {path}")
    return path, artifact


def _validate_raw_promotion_evidence(payload: Mapping[str, Any]) -> None:  # noqa: C901,PLR0912,PLR0915
    inputs = payload.get("inputs")
    lineage = inputs.get("lineage") if isinstance(inputs, Mapping) else None
    if not isinstance(lineage, Mapping):
        raise MinuteCutoverError("Coverage receipt lacks raw promotion lineage")
    if lineage.get("guan_mobile_promotion_verification_status") != "verified":
        raise MinuteCutoverError("Coverage lineage does not record a verified raw promotion")

    receipt_path, promotion = _load_hashed_lineage_artifact(
        lineage,
        path_field="guan_mobile_promotion_receipt",
        hash_field="guan_mobile_promotion_receipt_sha256",
        label="Guan mobile promotion receipt",
    )
    if receipt_path.name != "guan_mobile_promotion_20260711.json":
        raise MinuteCutoverError("Coverage lineage points to the wrong Guan promotion receipt")
    if (
        promotion.get("schema_version") != "guan.mobile_raw_promotion.v1"
        or promotion.get("status") != "complete"
        or promotion.get("verification_status") != "verified"
    ):
        raise MinuteCutoverError("Guan mobile promotion receipt is not complete and verified")
    recorded_receipt = promotion.get("receipt")
    if (
        not isinstance(recorded_receipt, str)
        or _absolute_endpoint(recorded_receipt) != receipt_path
    ):
        raise MinuteCutoverError("Guan mobile promotion receipt path binding is inconsistent")

    source_audits = lineage.get("source_audits")
    if not isinstance(source_audits, list) or not all(
        isinstance(value, str) for value in source_audits
    ):
        raise MinuteCutoverError("Coverage lineage lacks the source audit path inventory")
    if receipt_path not in {_absolute_endpoint(value) for value in source_audits}:
        raise MinuteCutoverError("Guan mobile promotion receipt is absent from source_audits")
    audit_receipts = lineage.get("source_audit_receipts")
    if not isinstance(audit_receipts, list):
        raise MinuteCutoverError("Coverage lineage lacks hash-bound source audit receipts")
    matching_audits = [
        item
        for item in audit_receipts
        if isinstance(item, Mapping)
        and isinstance(item.get("path"), str)
        and _absolute_endpoint(str(item["path"])) == receipt_path
    ]
    if len(matching_audits) != 1:
        raise MinuteCutoverError("Coverage source audit receipts do not uniquely bind promotion")
    audit_binding = matching_audits[0]
    if (
        audit_binding.get("sha256") != lineage.get("guan_mobile_promotion_receipt_sha256")
        or audit_binding.get("schema_version") != "guan.mobile_raw_promotion.v1"
        or audit_binding.get("status") != "complete"
        or audit_binding.get("verification_status") != "verified"
    ):
        raise MinuteCutoverError("Coverage source audit binding for raw promotion is invalid")

    raw_provider_root = promotion.get("provider_root")
    lineage_provider_root = lineage.get("guan_mobile_provider_root")
    guan_deal_dir = lineage.get("guan_deal_dir")
    if not all(
        isinstance(value, str)
        for value in (raw_provider_root, lineage_provider_root, guan_deal_dir)
    ):
        raise MinuteCutoverError("Coverage lineage lacks Guan provider/deal roots")
    provider_root = _absolute_endpoint(str(raw_provider_root))
    if (
        provider_root != _absolute_endpoint(str(lineage_provider_root))
        or provider_root.is_symlink()
        or not _real_directory(provider_root)
        or _absolute_endpoint(str(guan_deal_dir)) != provider_root / "deal"
        or not _real_directory(provider_root / "deal")
    ):
        raise MinuteCutoverError("Coverage Guan deal directory is not the promoted provider root")

    entries = promotion.get("entries")
    duplicates = promotion.get("source_duplicates")
    verification = promotion.get("verification")
    summary = promotion.get("summary")
    if (
        not isinstance(entries, list)
        or not entries
        or not all(isinstance(item, Mapping) for item in entries)
        or not isinstance(duplicates, list)
        or not all(isinstance(item, Mapping) for item in duplicates)
        or not isinstance(verification, Mapping)
        or not isinstance(summary, Mapping)
    ):
        raise MinuteCutoverError("Guan mobile promotion receipt inventory is malformed")
    logical_keys: set[str] = set()
    verified_bytes = 0
    for raw_entry in entries:
        assert isinstance(raw_entry, Mapping)
        logical_key = raw_entry.get("logical_key")
        organized_path = raw_entry.get("organized_path")
        content_hash = raw_entry.get("sha256")
        try:
            size = int(raw_entry.get("size", -1))
        except (TypeError, ValueError) as exc:
            raise MinuteCutoverError("Guan promotion entry has an invalid size") from exc
        if (
            not isinstance(logical_key, str)
            or logical_key in logical_keys
            or not isinstance(organized_path, str)
            or not isinstance(content_hash, str)
            or _SHA256_PATTERN.fullmatch(content_hash) is None
            or size < 0
            or raw_entry.get("verification_status") != "verified"
        ):
            raise MinuteCutoverError("Guan promotion entry lacks verified content evidence")
        organized = _absolute_endpoint(organized_path)
        if (
            not organized.is_relative_to(provider_root)
            or organized.is_symlink()
            or not _regular_file(organized)
        ):
            raise MinuteCutoverError(
                f"Guan promotion organized artifact escapes or is missing: {logical_key}"
            )
        logical_keys.add(logical_key)
        verified_bytes += size
    if any(
        duplicate.get("verification_status") != "verified_exact_duplicate"
        for duplicate in duplicates
    ):
        raise MinuteCutoverError("Guan promotion duplicate inventory is not verified")
    if (
        verification.get("status") != "verified"
        or verification.get("failures") != []
        or verification.get("verified_artifacts") != len(entries)
        or verification.get("verified_source_duplicates") != len(duplicates)
        or verification.get("verified_bytes") != verified_bytes
        or summary.get("complete_logical_artifacts") != len(entries)
    ):
        raise MinuteCutoverError("Guan promotion verification summary is inconsistent")


def _validate_full_day_evidence(  # noqa: C901,PLR0912,PLR0915
    payload: Mapping[str, Any],
    *,
    records: Mapping[str, Mapping[str, Any]],
    version_root: Path,
) -> Counter[str]:
    inputs = payload.get("inputs")
    lineage = inputs.get("lineage") if isinstance(inputs, Mapping) else None
    if not isinstance(lineage, Mapping):
        raise MinuteCutoverError("Coverage receipt lacks full-day TuShare lineage")
    full_day_dates = sorted(
        date for date, record in records.items() if record.get("tier") == "tushare_full_a_share"
    )
    if len(full_day_dates) != _TUSHARE_FULL_DATE_COUNT:
        raise MinuteCutoverError(
            f"Coverage receipt does not contain exactly {_TUSHARE_FULL_DATE_COUNT} full-day dates"
        )
    if (
        _validated_date_list(
            lineage.get("tushare_full_dates"),
            label="coverage lineage full-day dates",
        )
        != full_day_dates
    ):
        raise MinuteCutoverError("Coverage lineage full-day dates do not match daily tiers")
    if (
        lineage.get("tushare_full_phase") != "production"
        or lineage.get("tushare_full_status") != "passed"
    ):
        raise MinuteCutoverError(
            "Full-A production cutover requires a passed production-phase full-day receipt"
        )

    _plan_path, plan = _load_hashed_lineage_artifact(
        lineage,
        path_field="tushare_full_plan",
        hash_field="tushare_full_plan_sha256",
        label="full-day TuShare plan",
    )
    if (
        plan.get("schema_version") != "a_share.minute_tushare_full_day_plan.v1"
        or plan.get("phase") != "production"
        or _validated_date_list(plan.get("dates"), label="full-day TuShare plan dates")
        != full_day_dates
    ):
        raise MinuteCutoverError("Full-day TuShare plan is not the fixed production plan")

    _receipt_path, receipt = _load_hashed_lineage_artifact(
        lineage,
        path_field="tushare_full_receipt",
        hash_field="tushare_full_receipt_sha256",
        label="full-day TuShare receipt",
    )
    if (
        receipt.get("schema_version") != _TUSHARE_FULL_RECEIPT_SCHEMA
        or receipt.get("status") != "passed"
        or receipt.get("phase") != "production"
        or receipt.get("plan_sha256") != lineage.get("tushare_full_plan_sha256")
        or _absolute_endpoint(str(receipt.get("output_dir", ""))) != version_root
        or _validated_date_list(receipt.get("dates"), label="full-day TuShare receipt dates")
        != full_day_dates
    ):
        raise MinuteCutoverError(
            "Full-day TuShare receipt is not passed or does not bind the current plan hash"
        )
    receipt_policy = receipt.get("policy")
    lineage_policy = lineage.get("tushare_full_policy")
    if (
        not isinstance(receipt_policy, Mapping)
        or not isinstance(lineage_policy, Mapping)
        or dict(receipt_policy) != dict(lineage_policy)
        or receipt_policy.get("replacement_unit") != "whole_trade_date"
        or receipt_policy.get("intraday_source_merge") != "forbidden"
        or receipt_policy.get("market_scope") != "SH_SZ_BJ"
        or not _valid_tushare_price_flow_policy(receipt_policy.get("tushare_price_flow_validation"))
    ):
        raise MinuteCutoverError("Full-day TuShare receipt policy differs from coverage lineage")
    receipt_summary = receipt.get("summary")
    if not isinstance(receipt_summary, Mapping) or receipt_summary.get("date_count") != (
        _TUSHARE_FULL_DATE_COUNT
    ):
        raise MinuteCutoverError("Full-day TuShare receipt has the wrong date count")

    raw_actions = receipt.get("actions")
    raw_sources = receipt.get("source_receipts")
    if not isinstance(raw_actions, list) or not isinstance(raw_sources, Mapping):
        raise MinuteCutoverError("Full-day TuShare receipt lacks action/source inventories")
    actions: dict[str, Mapping[str, Any]] = {}
    for raw_action in raw_actions:
        if not isinstance(raw_action, Mapping):
            raise MinuteCutoverError("Full-day TuShare receipt contains a malformed action")
        date = str(raw_action.get("date", ""))
        if date in actions:
            raise MinuteCutoverError(f"Full-day TuShare receipt has duplicate actions for {date}")
        actions[date] = raw_action
    if set(actions) != set(full_day_dates) or set(raw_sources) != set(full_day_dates):
        raise MinuteCutoverError("Full-day TuShare receipt inventories do not match its plan")

    accepted_counts: Counter[str] = Counter()
    for date in full_day_dates:
        record = records[date]
        action = actions[date]
        expected_output = version_root / f"trade_date={date}" / "part-00000.parquet"
        if (
            record.get("market_scope") != "SH_SZ_BJ"
            or record.get("overlay_sources") not in (None, [], ())
            or action.get("status") != "written_full_day"
            or action.get("replacement_unit") != "whole_trade_date"
            or _lexical_absolute(str(action.get("output_path", ""))) != expected_output
        ):
            raise MinuteCutoverError(f"Full-day TuShare action is invalid for {date}")
        output_hash = action.get("output_sha256")
        if (
            not isinstance(output_hash, str)
            or _SHA256_PATTERN.fullmatch(output_hash) is None
            or not hmac.compare_digest(output_hash, str(record.get("content_sha256", "")))
        ):
            raise MinuteCutoverError(f"Full-day TuShare output hash is invalid for {date}")
        source = raw_sources[date]
        if not isinstance(source, Mapping):
            raise MinuteCutoverError(f"Full-day TuShare source receipt is malformed for {date}")
        if (
            any(
                not isinstance(source.get(field), str)
                or _SHA256_PATTERN.fullmatch(str(source.get(field))) is None
                for field in ("partition_sha256", "sidecar_sha256", "universe_hash")
            )
            or not str(source.get("universe_rule", ""))
            or ":exchange=" in str(source.get("universe_rule", ""))
            or source.get("expected_bars_per_symbol") != 241
            or source.get("selected_market_scope") != "SH_SZ_BJ"
            or not _valid_tushare_price_flow_receipt(
                source,
                diagnostics_field="price_flow_diagnostics",
            )
        ):
            raise MinuteCutoverError(f"Full-day TuShare source hash receipt is invalid for {date}")
        accepted_counts.update(source["accepted_diagnostics"])
    return accepted_counts


def _validate_bj_overlay_evidence(  # noqa: C901,PLR0912,PLR0915
    payload: Mapping[str, Any],
    *,
    records: Mapping[str, Mapping[str, Any]],
    version_root: Path,
) -> Counter[str]:
    inputs = payload.get("inputs")
    lineage = inputs.get("lineage") if isinstance(inputs, Mapping) else None
    if not isinstance(inputs, Mapping) or not isinstance(lineage, Mapping):
        raise MinuteCutoverError("Coverage receipt lacks BJ overlay lineage")

    full_day_dates = sorted(
        date for date, record in records.items() if record.get("tier") == "tushare_full_a_share"
    )
    post_bse_dates = {date for date in records if date >= _BSE_FIRST_TRADE_DATE}
    expected_overlay_dates = sorted(post_bse_dates.difference(full_day_dates))
    if len(full_day_dates) != _TUSHARE_FULL_DATE_COUNT:
        raise MinuteCutoverError(
            f"Coverage receipt does not contain exactly {_TUSHARE_FULL_DATE_COUNT} full-day dates"
        )
    if len(post_bse_dates) != _BJ_OVERLAY_DATE_COUNT + _TUSHARE_FULL_DATE_COUNT:
        raise MinuteCutoverError("Coverage receipt has the wrong point-in-time BSE-era date count")
    if len(expected_overlay_dates) != _BJ_OVERLAY_DATE_COUNT:
        raise MinuteCutoverError(
            f"Coverage receipt does not require exactly {_BJ_OVERLAY_DATE_COUNT} BJ overlays"
        )

    bound_date_lists = {
        "coverage inputs BJ overlay dates": inputs.get("bj_overlay_dates"),
        "coverage lineage BJ overlay dates": lineage.get("tushare_bj_overlay_dates"),
    }
    for label, raw_dates in bound_date_lists.items():
        if _validated_date_list(raw_dates, label=label) != expected_overlay_dates:
            raise MinuteCutoverError(f"{label} do not match the fixed production date set")
    if (
        _validated_date_list(
            lineage.get("tushare_full_dates"),
            label="coverage lineage full-day dates",
        )
        != full_day_dates
    ):
        raise MinuteCutoverError("Coverage lineage full-day dates do not match daily tiers")

    _plan_path, plan = _load_hashed_lineage_artifact(
        lineage,
        path_field="tushare_bj_overlay_plan",
        hash_field="tushare_bj_overlay_plan_sha256",
        label="BJ overlay plan",
    )
    if (
        plan.get("schema_version") != "a_share.minute_tushare_bj_overlay_plan.v1"
        or plan.get("phase") != "production"
        or _validated_date_list(plan.get("dates"), label="BJ overlay plan dates")
        != expected_overlay_dates
    ):
        raise MinuteCutoverError("BJ overlay plan is not the fixed production plan")

    _receipt_path, receipt = _load_hashed_lineage_artifact(
        lineage,
        path_field="tushare_bj_overlay_receipt",
        hash_field="tushare_bj_overlay_receipt_sha256",
        label="BJ overlay receipt",
    )
    if (
        receipt.get("schema_version") != _TUSHARE_BJ_OVERLAY_RECEIPT_SCHEMA
        or receipt.get("status") != "passed"
        or receipt.get("phase") != "production"
        or receipt.get("plan_sha256") != lineage.get("tushare_bj_overlay_plan_sha256")
        or _absolute_endpoint(str(receipt.get("output_dir", ""))) != version_root
        or _validated_date_list(receipt.get("dates"), label="BJ overlay receipt dates")
        != expected_overlay_dates
    ):
        raise MinuteCutoverError(
            "BJ overlay receipt is not passed or does not bind the current production plan hash"
        )
    receipt_policy = receipt.get("policy")
    if (
        not isinstance(receipt_policy, Mapping)
        or dict(receipt_policy) != dict(lineage["tushare_bj_overlay_policy"])
        or not _valid_tushare_price_flow_policy(receipt_policy.get("tushare_price_flow_validation"))
    ):
        raise MinuteCutoverError("BJ overlay receipt policy differs from coverage lineage")
    receipt_summary = receipt.get("summary")
    if not isinstance(receipt_summary, Mapping) or receipt_summary.get("date_count") != (
        _BJ_OVERLAY_DATE_COUNT
    ):
        raise MinuteCutoverError("BJ overlay receipt has the wrong date count")

    raw_actions = receipt.get("actions")
    raw_sources = receipt.get("source_receipts")
    if not isinstance(raw_actions, list) or not isinstance(raw_sources, Mapping):
        raise MinuteCutoverError("BJ overlay receipt lacks action/source inventories")
    actions: dict[str, Mapping[str, Any]] = {}
    for raw_action in raw_actions:
        if not isinstance(raw_action, Mapping):
            raise MinuteCutoverError("BJ overlay receipt contains a malformed action")
        date = str(raw_action.get("date", ""))
        if date in actions:
            raise MinuteCutoverError(f"BJ overlay receipt contains duplicate actions for {date}")
        actions[date] = raw_action
    if set(actions) != set(expected_overlay_dates) or set(raw_sources) != set(
        expected_overlay_dates
    ):
        raise MinuteCutoverError("BJ overlay receipt inventories do not match the fixed date set")

    accepted_counts: Counter[str] = Counter()
    for date, record in records.items():
        overlay_sources = record.get("overlay_sources")
        if date not in actions:
            if overlay_sources not in (None, [], ()):
                raise MinuteCutoverError(f"Unexpected BJ overlay annotation for {date}")
            if date < _BSE_FIRST_TRADE_DATE and record.get("market_scope") != "SH_SZ":
                raise MinuteCutoverError(f"Pre-BSE Guan partition has the wrong scope: {date}")
            continue

        if (
            record.get("tier") not in {"annual_full_sh_sz", "deal_full_sh_sz"}
            or record.get("market_scope") != "SH_SZ_BJ"
            or overlay_sources != ["tushare_bj_overlay"]
        ):
            raise MinuteCutoverError(f"BJ overlay daily coverage is invalid for {date}")
        action = actions[date]
        expected_output = version_root / f"trade_date={date}" / "part-00000.parquet"
        if (
            action.get("status") not in {"written_bj_overlay", "skipped_already_overlayed"}
            or action.get("base_tier") != record.get("tier")
            or _lexical_absolute(str(action.get("output_path", ""))) != expected_output
        ):
            raise MinuteCutoverError(f"BJ overlay action is invalid for {date}")
        output_hash = action.get("output_sha256")
        base_hash = action.get("base_sha256")
        if (
            not isinstance(output_hash, str)
            or _SHA256_PATTERN.fullmatch(output_hash) is None
            or not hmac.compare_digest(output_hash, str(record.get("content_sha256", "")))
            or not isinstance(base_hash, str)
            or _SHA256_PATTERN.fullmatch(base_hash) is None
        ):
            raise MinuteCutoverError(f"BJ overlay action hash binding is invalid for {date}")
        source = raw_sources[date]
        if not isinstance(source, Mapping):
            raise MinuteCutoverError(f"BJ overlay source receipt is malformed for {date}")
        if (
            any(
                not isinstance(source.get(field), str)
                or _SHA256_PATTERN.fullmatch(str(source.get(field))) is None
                for field in ("partition_sha256", "sidecar_sha256", "universe_hash")
            )
            or not str(source.get("universe_rule", "")).endswith(":exchange=BJ")
            or source.get("expected_bars_per_symbol") != 241
            or not _valid_tushare_price_flow_receipt(
                source,
                diagnostics_field="flow_diagnostics",
            )
        ):
            raise MinuteCutoverError(f"BJ overlay source hash receipt is invalid for {date}")
        accepted_counts.update(source["accepted_diagnostics"])
    return accepted_counts


def _validate_price_flow_reconciliation(
    payload: Mapping[str, Any],
    records: Mapping[str, Mapping[str, Any]],
    tushare_receipt_counts: Counter[str],
) -> None:
    requirements = payload["requirements"]
    summary = payload["summary"]
    daily_tushare: Counter[str] = Counter()
    daily_guan: Counter[str] = Counter()
    for record in records.values():
        by_source = record.get("accepted_diagnostics_by_source")
        if not isinstance(by_source, Mapping):
            raise MinuteCutoverError("Coverage daily record lacks source-aware diagnostics")
        for source, target in (("guan", daily_guan), ("tushare", daily_tushare)):
            diagnostics = by_source.get(source)
            if not isinstance(diagnostics, Mapping):
                raise MinuteCutoverError("Coverage daily source diagnostics are malformed")
            target.update(diagnostics)
    if daily_tushare != tushare_receipt_counts:
        raise MinuteCutoverError("Coverage TuShare diagnostics do not match bound receipts")
    guan_baselines = {
        ZERO_VOLUME_NONZERO_AMOUNT_ISSUE: requirements[
            "expected_accepted_zero_volume_nonzero_amount_rows"
        ],
        POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE: requirements[
            "expected_accepted_positive_volume_zero_amount_rows"
        ],
    }
    for issue, guan_count in guan_baselines.items():
        if daily_guan[issue] != guan_count:
            raise MinuteCutoverError(f"Coverage Guan diagnostic baseline differs for {issue}")
    expected_total = {
        issue: int(guan_count) + tushare_receipt_counts[issue]
        for issue, guan_count in guan_baselines.items()
    }
    if (
        summary.get("accepted_guan_zero_volume_nonzero_amount_rows")
        != guan_baselines[ZERO_VOLUME_NONZERO_AMOUNT_ISSUE]
        or summary.get("accepted_guan_positive_volume_zero_amount_rows")
        != guan_baselines[POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE]
        or summary.get("accepted_zero_volume_nonzero_amount_rows")
        != expected_total[ZERO_VOLUME_NONZERO_AMOUNT_ISSUE]
        or summary.get("accepted_positive_volume_zero_amount_rows")
        != expected_total[POSITIVE_VOLUME_ZERO_AMOUNT_ISSUE]
    ):
        raise MinuteCutoverError("Coverage zero-flow summary differs from source receipts")


def _validate_tree_layout(version_root: Path) -> tuple[dict[str, Path], set[Path]]:
    """Return the partition inventory after rejecting indirection and extra layout."""
    partitions: dict[str, Path] = {}
    parquet_files: set[Path] = set()
    try:
        root_entries = list(version_root.iterdir())
    except OSError as exc:
        raise MinuteCutoverError(
            f"Cannot inspect new minute version {version_root}: {exc}"
        ) from exc

    for child in root_entries:
        if child.is_symlink():
            raise MinuteCutoverError(f"Symlink is forbidden inside minute version: {child}")
        matched = _PARTITION_PATTERN.fullmatch(child.name)
        if matched is None:
            raise MinuteCutoverError(f"Unexpected entry inside minute version: {child}")
        if not _real_directory(child):
            raise MinuteCutoverError(f"Partition is not a real directory: {child}")

        trade_date = matched.group(1)
        expected_file = child / "part-00000.parquet"
        try:
            partition_entries = list(child.iterdir())
        except OSError as exc:
            raise MinuteCutoverError(f"Cannot inspect partition {child}: {exc}") from exc
        if partition_entries != [expected_file]:
            names = sorted(entry.name for entry in partition_entries)
            raise MinuteCutoverError(
                f"Partition {trade_date} has an unexpected file inventory: {names}"
            )
        if expected_file.is_symlink() or not _regular_file(expected_file):
            raise MinuteCutoverError(f"Partition file must be a real regular file: {expected_file}")
        if expected_file.lstat().st_nlink != 1:
            raise MinuteCutoverError(
                f"Partition file is still hardlinked outside the immutable version: {expected_file}"
            )
        resolved = expected_file.resolve(strict=True)
        if resolved.parent != child or not resolved.is_relative_to(version_root):
            raise MinuteCutoverError(f"Partition path escapes the version tree: {expected_file}")
        partitions[trade_date] = expected_file
        parquet_files.add(expected_file)
    return partitions, parquet_files


def validate_cutover_receipt(  # noqa: C901,PLR0912,PLR0915
    coverage_manifest: str | Path,
    new_version: str | Path,
    *,
    target_market_scope: str = _TARGET_MARKET_SCOPE_FULL_A_SHARE,
) -> Mapping[str, Any]:
    """Validate the fixed production contract and rehash its complete version tree."""
    manifest_path = Path(coverage_manifest).expanduser().resolve()
    version_root = _absolute_endpoint(new_version)
    payload = _load_manifest(manifest_path)
    if not (
        payload.get("schema_version") == "a_share.minute_1m.coverage.v1"
        and payload.get("status") == "passed"
        and payload.get("quality_status") == "passed"
    ):
        raise MinuteCutoverError("Coverage manifest is not a passed production receipt")
    expected_tier_counts = _validate_production_contract(
        payload,
        target_market_scope=target_market_scope,
    )
    if _absolute_endpoint(str(payload.get("output_dir", ""))) != version_root:
        raise MinuteCutoverError("Coverage manifest output_dir does not match --new-version")
    if version_root.is_symlink() or not _real_directory(version_root):
        raise MinuteCutoverError(f"New minute version must be a real directory: {version_root}")
    if (version_root / ".annual-minbar-build.lock").exists():
        raise MinuteCutoverError(f"Annual builder still holds the version lock: {version_root}")
    _validate_raw_promotion_evidence(payload)
    failures = payload.get("failures")
    if not isinstance(failures, Mapping) or _contains_failure(failures):
        raise MinuteCutoverError("Coverage receipt still contains quality failures")

    daily = payload.get("daily")
    if not isinstance(daily, list) or len(daily) != _PRODUCTION_DATE_COUNT:
        raise MinuteCutoverError(
            "Coverage receipt daily inventory is not the complete "
            f"{_PRODUCTION_DATE_COUNT}-date production set"
        )
    partitions, actual_parquet = _validate_tree_layout(version_root)

    records: dict[str, Mapping[str, Any]] = {}
    expected_files: set[Path] = set()
    tier_counts: Counter[str] = Counter()
    for raw_record in daily:
        if not isinstance(raw_record, Mapping) or raw_record.get("valid") is not True:
            raise MinuteCutoverError(
                "Coverage receipt contains a missing or invalid daily partition"
            )
        trade_date = str(raw_record.get("date", ""))
        try:
            parsed_date = datetime.strptime(trade_date, "%Y%m%d")
        except ValueError as exc:
            raise MinuteCutoverError(f"Invalid coverage date: {trade_date!r}") from exc
        if parsed_date.strftime("%Y%m%d") != trade_date or trade_date in records:
            raise MinuteCutoverError(f"Invalid or duplicate coverage date: {trade_date!r}")
        if not _PRODUCTION_DATE_MIN <= trade_date <= _PRODUCTION_DATE_MAX:
            raise MinuteCutoverError(f"Coverage date is outside the production range: {trade_date}")

        tier = str(raw_record.get("tier", ""))
        if tier not in expected_tier_counts:
            raise MinuteCutoverError(f"Invalid production coverage tier: {tier!r}")
        if tier == "tushare_full_a_share" and raw_record.get("market_scope") != "SH_SZ_BJ":
            raise MinuteCutoverError(
                f"TuShare full-day partition has the wrong market scope: {trade_date}"
            )
        if target_market_scope == _TARGET_MARKET_SCOPE_SH_SZ and (
            (
                tier in {"annual_full_sh_sz", "deal_full_sh_sz"}
                and raw_record.get("market_scope") != "SH_SZ"
            )
            or raw_record.get("overlay_sources") not in (None, [], ())
        ):
            raise MinuteCutoverError(
                f"SH/SZ production partition contains an overlay or wrong scope: {trade_date}"
            )
        tier_counts[tier] += 1

        expected = version_root / f"trade_date={trade_date}" / "part-00000.parquet"
        recorded_value = raw_record.get("path")
        if not isinstance(recorded_value, str) or _lexical_absolute(recorded_value) != expected:
            raise MinuteCutoverError(f"Coverage partition path no longer matches: {trade_date}")
        if partitions.get(trade_date) != expected:
            raise MinuteCutoverError(
                f"Coverage partition is missing from version tree: {trade_date}"
            )
        stat_result = expected.stat()
        if stat_result.st_size < 1 or int(raw_record.get("rows", 0)) < 1:
            raise MinuteCutoverError(f"Coverage partition is empty: {trade_date}")
        if (
            raw_record.get("file_size") != stat_result.st_size
            or raw_record.get("file_mtime_ns") != stat_result.st_mtime_ns
        ):
            raise MinuteCutoverError(f"Coverage partition changed after audit: {trade_date}")
        recorded_hash = raw_record.get("content_sha256")
        if not isinstance(recorded_hash, str) or not _SHA256_PATTERN.fullmatch(recorded_hash):
            raise MinuteCutoverError(
                f"Coverage partition lacks a valid content_sha256: {trade_date}"
            )
        actual_hash = _sha256(expected)
        if not hmac.compare_digest(recorded_hash, actual_hash):
            raise MinuteCutoverError(f"Coverage partition hash changed after audit: {trade_date}")
        records[trade_date] = raw_record
        expected_files.add(expected)

    if min(records) != _PRODUCTION_DATE_MIN or max(records) != _PRODUCTION_DATE_MAX:
        raise MinuteCutoverError("Coverage daily inventory has the wrong production date range")
    if dict(tier_counts) != dict(expected_tier_counts):
        raise MinuteCutoverError(
            f"Coverage daily tier counts do not match production: {dict(tier_counts)}"
        )
    if set(partitions) != set(records) or actual_parquet != expected_files:
        raise MinuteCutoverError("Minute version inventory changed after coverage audit")
    if target_market_scope == _TARGET_MARKET_SCOPE_SH_SZ:
        bj_gap_dates = [
            date
            for date, record in records.items()
            if date >= _BSE_FIRST_TRADE_DATE and record.get("market_scope") == "SH_SZ"
        ]
        if len(bj_gap_dates) != _BJ_OVERLAY_DATE_COUNT:
            raise MinuteCutoverError(
                "SH/SZ production daily inventory does not expose exactly "
                f"{_BJ_OVERLAY_DATE_COUNT} BJ-gap dates"
            )
    full_day_price_flow = _validate_full_day_evidence(
        payload,
        records=records,
        version_root=version_root,
    )
    bj_price_flow = Counter()
    if target_market_scope == _TARGET_MARKET_SCOPE_FULL_A_SHARE:
        bj_price_flow = _validate_bj_overlay_evidence(
            payload,
            records=records,
            version_root=version_root,
        )
    _validate_price_flow_reconciliation(
        payload,
        records,
        full_day_price_flow + bj_price_flow,
    )
    return payload


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_exchange(left: Path, right: Path) -> None:
    """Exchange two same-filesystem paths with the Linux renameat2 syscall."""
    if not sys.platform.startswith("linux"):
        raise MinuteCutoverError("Atomic minute cutover requires Linux renameat2")
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise MinuteCutoverError("Atomic minute cutover requires renameat2 support")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(left),
        _AT_FDCWD,
        os.fsencode(right),
        _RENAME_EXCHANGE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), f"{left} <-> {right}")


def _probe_rename_exchange(parent: Path) -> None:
    """Prove filesystem support without touching current or any dataset endpoint."""
    token = uuid.uuid4().hex
    directory = parent / f".minute-cutover.exchange-probe-dir.{token}"
    link = parent / f".minute-cutover.exchange-probe-link.{token}"
    directory.mkdir()
    link.symlink_to(directory.name)
    exchanged = False
    try:
        _rename_exchange(directory, link)
        exchanged = True
        if not directory.is_symlink() or not _real_directory(link):
            raise MinuteCutoverError("renameat2 exchange probe produced an invalid state")
        _rename_exchange(directory, link)
        exchanged = False
    except Exception as exc:
        if exchanged:
            try:
                _rename_exchange(directory, link)
            except Exception:
                pass
        if isinstance(exc, MinuteCutoverError):
            raise
        raise MinuteCutoverError(
            f"Filesystem does not support atomic rename exchange: {exc}"
        ) from exc
    finally:
        if link.is_symlink():
            link.unlink()
        elif _real_directory(link):
            link.rmdir()
        if directory.is_symlink():
            directory.unlink()
        elif _real_directory(directory):
            directory.rmdir()


def _link_targets_version(link: Path, version: Path) -> bool:
    if not link.is_symlink():
        return False
    try:
        target = os.readlink(link)
        return (
            not os.path.isabs(target)
            and target == os.path.relpath(version, link.parent)
            and link.resolve(strict=True) == version
        )
    except OSError:
        return False


def _local_version_link_target(
    link: Path,
    *,
    forbidden_targets: set[Path],
) -> Path | None:
    """Return a canonical relative link target when it is a real sibling version."""
    if not link.is_symlink():
        return None
    try:
        raw_target = os.readlink(link)
        if os.path.isabs(raw_target):
            return None
        target = Path(os.path.abspath(link.parent / raw_target))
        if (
            target.parent != link.parent
            or target in forbidden_targets
            or raw_target != os.path.relpath(target, link.parent)
            or target.is_symlink()
            or not _real_directory(target)
            or link.resolve(strict=True) != target
        ):
            return None
        return target
    except OSError:
        return None


def _previous_endpoint(
    path: Path,
    *,
    forbidden_targets: set[Path],
) -> tuple[str, Path | None] | None:
    if _real_directory(path):
        return "directory", None
    target = _local_version_link_target(path, forbidden_targets=forbidden_targets)
    if target is None:
        return None
    return "version_link", target


def _endpoint_identity(path: Path) -> tuple[int, int, int]:
    value = path.lstat()
    return value.st_dev, value.st_ino, value.st_mode


def _prepare_pending_link(pending: Path, current: Path, version: Path) -> None:
    pending.symlink_to(os.path.relpath(version, current.parent))
    _fsync_directory(current.parent)


def _preserve_pending_directory(pending: Path, backup: Path) -> None:
    os.rename(pending, backup)
    _fsync_directory(backup.parent)


def _validate_cutover_endpoints(
    current_path: str | Path,
    new_version: str | Path,
    backup_path: str | Path,
) -> tuple[Path, Path, Path, Path]:
    """Resolve and validate the three endpoints used by a minute cutover."""
    current = _absolute_endpoint(current_path)
    version = _absolute_endpoint(new_version)
    backup = _absolute_endpoint(backup_path)
    if current.parent != version.parent or backup.parent != version.parent:
        raise MinuteCutoverError("Current, new version, and backup must share one parent directory")
    if len({current, version, backup}) != 3:
        raise MinuteCutoverError("Current, new version, and backup paths must be distinct")
    pending = current.parent / f".{current.name}.cutover-pending"
    if pending in {current, version, backup}:
        raise MinuteCutoverError("Cutover pending path conflicts with a dataset endpoint")
    return current, version, backup, pending


def cutover_minute_current(  # noqa: PLR0912,PLR0913
    *,
    coverage_manifest: str | Path,
    current_path: str | Path,
    new_version: str | Path,
    backup_path: str | Path,
    dry_run: bool = False,
    target_market_scope: str = _TARGET_MARKET_SCOPE_FULL_A_SHARE,
) -> dict[str, Any]:
    """Atomically exchange current with a prepared link and preserve the old endpoint."""
    current, version, backup, pending = _validate_cutover_endpoints(
        current_path, new_version, backup_path
    )

    with minute_dataset_lock(version, operation="cutover-a-share-minute-current"):
        validate_cutover_receipt(
            coverage_manifest,
            version,
            target_market_scope=target_market_scope,
        )

        forbidden_targets = {current, version, backup, pending}
        backup_previous = _previous_endpoint(
            backup,
            forbidden_targets=forbidden_targets,
        )
        if _lexists(backup) and backup_previous is None:
            raise MinuteCutoverError(
                "Backup must be a real directory or a canonical relative link to a sibling version"
            )

        if _link_targets_version(current, version) and backup_previous is not None:
            if _lexists(pending):
                raise MinuteCutoverError(f"Unexpected pending cutover state: {pending}")
            return {
                "status": "already_current",
                "target_market_scope": target_market_scope,
                "current_path": str(current),
                "new_version": str(version),
                "backup_path": str(backup),
                "previous_endpoint_type": backup_previous[0],
                "previous_version": (
                    str(backup_previous[1]) if backup_previous[1] is not None else None
                ),
                "dry_run": dry_run,
            }

        current_previous = _previous_endpoint(
            current,
            forbidden_targets=forbidden_targets,
        )
        pending_previous = _previous_endpoint(
            pending,
            forbidden_targets=forbidden_targets,
        )
        if (
            current.is_symlink()
            and not _link_targets_version(current, version)
            and current_previous is None
        ):
            raise MinuteCutoverError(
                "Current version link must target a real sibling directory using its canonical "
                "relative path"
            )
        if (
            pending.is_symlink()
            and not _link_targets_version(pending, version)
            and pending_previous is None
        ):
            raise MinuteCutoverError(
                "Pending cutover link escapes or does not target a real sibling version"
            )

        exchanged_recovery = (
            _link_targets_version(current, version)
            and pending_previous is not None
            and not _lexists(backup)
        )
        prepared_recovery = (
            current_previous is not None
            and _link_targets_version(pending, version)
            and not _lexists(backup)
        )
        fresh = current_previous is not None and not _lexists(pending) and not _lexists(backup)

        if not (exchanged_recovery or prepared_recovery or fresh):
            raise MinuteCutoverError(
                "Current, pending, and backup paths do not form a recoverable cutover state"
            )

        if exchanged_recovery:
            if dry_run:
                return {
                    "status": "planned_finalize_recovery",
                    "target_market_scope": target_market_scope,
                    "current_path": str(current),
                    "new_version": str(version),
                    "backup_path": str(backup),
                    "pending_path": str(pending),
                    "previous_endpoint_type": pending_previous[0],
                    "previous_version": (
                        str(pending_previous[1]) if pending_previous[1] is not None else None
                    ),
                    "dry_run": True,
                }
            _preserve_pending_directory(pending, backup)
            preserved = _previous_endpoint(
                backup,
                forbidden_targets=forbidden_targets,
            )
            if preserved is None:
                raise MinuteCutoverError("Recovered backup endpoint is invalid")
            return {
                "status": "recovered_after_exchange",
                "target_market_scope": target_market_scope,
                "current_path": str(current),
                "new_version": str(version),
                "backup_path": str(backup),
                "relative_target": os.readlink(current),
                "previous_endpoint_type": preserved[0],
                "previous_version": str(preserved[1]) if preserved[1] is not None else None,
                "dry_run": False,
            }

        # Probe the exact filesystem operation before creating or exchanging a
        # production endpoint. A failure leaves current untouched.
        assert current_previous is not None
        _probe_rename_exchange(current.parent)
        if dry_run:
            return {
                "status": "planned_resume_exchange" if prepared_recovery else "planned",
                "target_market_scope": target_market_scope,
                "current_path": str(current),
                "new_version": str(version),
                "backup_path": str(backup),
                "pending_path": str(pending),
                "previous_endpoint_type": current_previous[0],
                "previous_version": (
                    str(current_previous[1]) if current_previous[1] is not None else None
                ),
                "dry_run": True,
            }

        previous_identity = _endpoint_identity(current)
        if fresh:
            _prepare_pending_link(pending, current, version)
        _rename_exchange(current, pending)
        _fsync_directory(current.parent)
        preserved_pending = _previous_endpoint(
            pending,
            forbidden_targets=forbidden_targets,
        )
        if (
            not _link_targets_version(current, version)
            or preserved_pending is None
            or _endpoint_identity(pending) != previous_identity
        ):
            raise MinuteCutoverError("Atomic exchange produced an invalid pending state")
        _preserve_pending_directory(pending, backup)
        preserved_backup = _previous_endpoint(
            backup,
            forbidden_targets=forbidden_targets,
        )
        if preserved_backup is None:
            raise MinuteCutoverError("Cutover backup endpoint is invalid")
        return {
            "status": "cutover_complete" if fresh else "recovered_before_exchange",
            "target_market_scope": target_market_scope,
            "current_path": str(current),
            "new_version": str(version),
            "backup_path": str(backup),
            "relative_target": os.readlink(current),
            "previous_endpoint_type": preserved_backup[0],
            "previous_version": (
                str(preserved_backup[1]) if preserved_backup[1] is not None else None
            ),
            "dry_run": False,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-manifest", required=True)
    parser.add_argument("--current-path", required=True)
    parser.add_argument("--new-version", required=True)
    parser.add_argument("--backup-path", required=True)
    parser.add_argument(
        "--target-market-scope",
        choices=_TARGET_MARKET_SCOPES,
        default=_TARGET_MARKET_SCOPE_FULL_A_SHARE,
        help=(
            "Production coverage contract to publish. The default requires full A-share "
            "coverage including BJ; sh-sz explicitly publishes complete SH/SZ with BJ "
            "recorded as a known gap."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = cutover_minute_current(
        coverage_manifest=args.coverage_manifest,
        current_path=args.current_path,
        new_version=args.new_version,
        backup_path=args.backup_path,
        dry_run=args.dry_run,
        target_market_scope=args.target_market_scope,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
