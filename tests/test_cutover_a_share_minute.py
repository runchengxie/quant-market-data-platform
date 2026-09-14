from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from market_data_platform.dataset_lock import DatasetLockError
from market_data_platform.providers.a_share_minute_price_flow import tushare_price_flow_policy


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "operations" / "cutover_a_share_minute.py"
    spec = importlib.util.spec_from_file_location("cutover_a_share_minute_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cutover = _load_script()


def _production_dates() -> list[str]:
    pre_bse = [
        (date(2016, 1, 4) + timedelta(days=offset)).strftime("%Y%m%d") for offset in range(1_426)
    ]
    bse_era = [
        (date(2021, 11, 15) + timedelta(days=offset)).strftime("%Y%m%d") for offset in range(1_126)
    ]
    dates = [
        *pre_bse,
        *bse_era,
        "20260709",
        "20260710",
        "20260713",
        "20260714",
    ]
    assert dates == sorted(set(dates))
    assert len(dates) == 2_556
    assert sum(value >= "20211115" for value in dates) == 1_130
    return dates


def _tier(index: int) -> str:
    if index < 2_430:
        return "annual_full_sh_sz"
    if index < 2_467:
        return "deal_full_sh_sz"
    return "tushare_full_a_share"


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _empty_price_flow_diagnostics() -> dict[str, int]:
    return {
        "vwap_outside_ohlc_rows": 0,
        "vwap_beyond_source_guard_rows": 0,
        "notional_beyond_hard_price_guard_rows": 0,
        "extreme_vwap_unit_scale_rows": 0,
        "zero_volume_nonzero_amount_rows": 0,
        "positive_volume_zero_amount_rows": 0,
    }


def test_cutover_price_flow_receipt_requires_typed_hard_and_accepted_evidence() -> None:
    diagnostics: dict[str, Any] = _empty_price_flow_diagnostics()
    source: dict[str, Any] = {
        "price_flow_diagnostics": diagnostics,
        "accepted_diagnostics": {},
        "tushare_price_flow_validation": dict(tushare_price_flow_policy()),
    }
    assert cutover._valid_tushare_price_flow_receipt(
        source,
        diagnostics_field="price_flow_diagnostics",
    )

    diagnostics["positive_volume_zero_amount_rows"] = 1
    source["accepted_diagnostics"] = {"positive_volume_zero_amount_rows": 1}
    assert cutover._valid_tushare_price_flow_receipt(
        source,
        diagnostics_field="price_flow_diagnostics",
    )

    diagnostics["notional_beyond_hard_price_guard_rows"] = 1
    assert not cutover._valid_tushare_price_flow_receipt(
        source,
        diagnostics_field="price_flow_diagnostics",
    )
    diagnostics["notional_beyond_hard_price_guard_rows"] = "0"
    assert not cutover._valid_tushare_price_flow_receipt(
        source,
        diagnostics_field="price_flow_diagnostics",
    )


def test_cutover_reconciles_dynamic_tushare_zero_amount_diagnostics() -> None:
    payload = {
        "requirements": {
            "expected_accepted_zero_volume_nonzero_amount_rows": 136_246,
            "expected_accepted_positive_volume_zero_amount_rows": 1,
        },
        "summary": {
            "accepted_guan_zero_volume_nonzero_amount_rows": 136_246,
            "accepted_guan_positive_volume_zero_amount_rows": 1,
            "accepted_zero_volume_nonzero_amount_rows": 136_246,
            "accepted_positive_volume_zero_amount_rows": 2,
        },
    }
    records = {
        "guan": {
            "accepted_diagnostics_by_source": {
                "guan": {
                    "zero_volume_nonzero_amount_rows": 136_246,
                    "positive_volume_zero_amount_rows": 1,
                },
                "tushare": {},
            }
        },
        "tushare": {
            "accepted_diagnostics_by_source": {
                "guan": {},
                "tushare": {"positive_volume_zero_amount_rows": 1},
            }
        },
    }
    cutover._validate_price_flow_reconciliation(
        payload,
        records,
        cutover.Counter({"positive_volume_zero_amount_rows": 1}),
    )

    payload["summary"]["accepted_positive_volume_zero_amount_rows"] = 1
    with pytest.raises(cutover.MinuteCutoverError, match="zero-flow summary"):
        cutover._validate_price_flow_reconciliation(
            payload,
            records,
            cutover.Counter({"positive_volume_zero_amount_rows": 1}),
        )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _promotion_evidence(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    provider_root = tmp_path / "raw" / "source_native" / "guan_mobile"
    guan_deal_dir = provider_root / "deal"
    organized_raw = guan_deal_dir / "202603" / "deal_20260302.parquet"
    organized_raw.parent.mkdir(parents=True)
    organized_raw.write_bytes(b"verified-guan-mobile-raw")
    promotion_receipt = tmp_path / "guan_mobile_promotion_20260711.json"
    _write_manifest(
        promotion_receipt,
        {
            "schema_version": "guan.mobile_raw_promotion.v1",
            "status": "complete",
            "verification_status": "verified",
            "receipt": str(promotion_receipt),
            "provider_root": str(provider_root),
            "entries": [
                {
                    "logical_key": "deal:20260302",
                    "organized_path": str(organized_raw),
                    "sha256": _file_sha256(organized_raw),
                    "size": organized_raw.stat().st_size,
                    "status": "promoted",
                    "verification_status": "verified",
                }
            ],
            "source_duplicates": [],
            "summary": {"complete_logical_artifacts": 1},
            "verification": {
                "status": "verified",
                "verified_artifacts": 1,
                "verified_source_duplicates": 0,
                "verified_bytes": organized_raw.stat().st_size,
                "failures": [],
            },
        },
    )
    return provider_root, guan_deal_dir, promotion_receipt, _file_sha256(promotion_receipt)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    parent = tmp_path / "a_share"
    current = parent / "minute_1m"
    version = parent / "minute_1m_v3_20260711"
    backup = parent / "minute_1m_pre_v3_20260711"
    current.mkdir(parents=True)
    (current / "old-marker").write_text("old", encoding="utf-8")
    daily = []
    full_day_dates: list[str] = []
    full_day_actions: list[dict[str, Any]] = []
    full_day_sources: dict[str, dict[str, Any]] = {}
    overlay_dates: list[str] = []
    overlay_actions: list[dict[str, Any]] = []
    source_receipts: dict[str, dict[str, Any]] = {}
    for index, trade_date in enumerate(_production_dates()):
        path = version / f"trade_date={trade_date}" / "part-00000.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"partition-{trade_date}".encode()
        path.write_bytes(content)
        file_stat = path.stat()
        tier = _tier(index)
        is_overlay = trade_date >= "20211115" and tier != "tushare_full_a_share"
        if tier == "tushare_full_a_share":
            full_day_index = len(full_day_dates)
            replaces_tier = (
                "guan_partial_session"
                if full_day_index < 58
                else "tushare_partial_top200"
                if full_day_index < 86
                else "missing"
            )
            full_day_dates.append(trade_date)
            full_day_actions.append(
                {
                    "date": trade_date,
                    "status": "written_full_day",
                    "replaces_tier": replaces_tier,
                    "replacement_unit": "whole_trade_date",
                    "output_path": str(path),
                    "output_sha256": hashlib.sha256(content).hexdigest(),
                }
            )
            full_day_sources[trade_date] = {
                "partition_path": str(
                    tmp_path / "all-a" / f"trade_date={trade_date}" / "part-00000.parquet"
                ),
                "sidecar_path": str(
                    tmp_path / "all-a" / f"trade_date={trade_date}" / "_minute_mirror.json"
                ),
                "partition_sha256": hashlib.sha256(
                    f"full-partition-{trade_date}".encode()
                ).hexdigest(),
                "sidecar_sha256": hashlib.sha256(f"full-sidecar-{trade_date}".encode()).hexdigest(),
                "universe_hash": hashlib.sha256(f"full-universe-{trade_date}".encode()).hexdigest(),
                "universe_rule": "dynamic-daily-universe:v1",
                "expected_bars_per_symbol": 241,
                "selected_market_scope": "SH_SZ_BJ",
                "price_flow_diagnostics": _empty_price_flow_diagnostics(),
                "accepted_diagnostics": {},
                "tushare_price_flow_validation": dict(tushare_price_flow_policy()),
            }
        if is_overlay:
            overlay_dates.append(trade_date)
            output_sha256 = hashlib.sha256(content).hexdigest()
            overlay_actions.append(
                {
                    "date": trade_date,
                    "status": "written_bj_overlay",
                    "base_tier": tier,
                    "base_sha256": hashlib.sha256(f"base-{trade_date}".encode()).hexdigest(),
                    "output_path": str(path),
                    "output_sha256": output_sha256,
                }
            )
            source_receipts[trade_date] = {
                "partition_path": str(
                    tmp_path / "bj" / f"trade_date={trade_date}" / "part-00000.parquet"
                ),
                "sidecar_path": str(
                    tmp_path / "bj" / f"trade_date={trade_date}" / "_minute_mirror.json"
                ),
                "partition_sha256": hashlib.sha256(
                    f"bj-partition-{trade_date}".encode()
                ).hexdigest(),
                "sidecar_sha256": hashlib.sha256(f"bj-sidecar-{trade_date}".encode()).hexdigest(),
                "universe_hash": hashlib.sha256(f"bj-universe-{trade_date}".encode()).hexdigest(),
                "universe_rule": "dynamic-daily-universe:v1:exchange=BJ",
                "expected_bars_per_symbol": 241,
                "flow_diagnostics": _empty_price_flow_diagnostics(),
                "accepted_diagnostics": {},
                "tushare_price_flow_validation": dict(tushare_price_flow_policy()),
            }
        daily.append(
            {
                "date": trade_date,
                "tier": tier,
                "market_scope": (
                    "SH_SZ_BJ" if tier == "tushare_full_a_share" or is_overlay else "SH_SZ"
                ),
                "overlay_sources": ["tushare_bj_overlay"] if is_overlay else [],
                "path": str(path),
                "valid": True,
                "rows": 1,
                "file_size": file_stat.st_size,
                "file_mtime_ns": file_stat.st_mtime_ns,
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "accepted_diagnostics": (
                    {
                        "zero_volume_nonzero_amount_rows": 136_246,
                        "positive_volume_zero_amount_rows": 1,
                    }
                    if index == 0
                    else {}
                ),
                "accepted_diagnostics_by_source": {
                    "guan": (
                        {
                            "zero_volume_nonzero_amount_rows": 136_246,
                            "positive_volume_zero_amount_rows": 1,
                        }
                        if index == 0
                        else {}
                    ),
                    "tushare": {},
                },
            }
        )

    assert len(full_day_dates) == 89
    assert len(overlay_dates) == 1_041
    full_day_policy = {
        "replacement_unit": "whole_trade_date",
        "intraday_source_merge": "forbidden",
        "eligible_input_tiers": [
            "guan_partial_session",
            "missing",
            "tushare_partial_top200",
        ],
        "source_requirement": "bound_complete_full_universe_sidecar",
        "market_scope": "SH_SZ_BJ",
        "tushare_price_flow_validation": dict(tushare_price_flow_policy()),
    }
    full_day_plan = tmp_path / "full-day-plan.json"
    _write_manifest(
        full_day_plan,
        {
            "schema_version": "a_share.minute_tushare_full_day_plan.v1",
            "phase": "production",
            "dates": full_day_dates,
        },
    )
    full_day_receipt = tmp_path / "full-day-receipt.json"
    _write_manifest(
        full_day_receipt,
        {
            "schema_version": "a_share.minute_tushare_full_day_receipt.v2",
            "status": "passed",
            "phase": "production",
            "plan_sha256": _file_sha256(full_day_plan),
            "output_dir": str(version),
            "policy": full_day_policy,
            "dates": full_day_dates,
            "summary": {
                "date_count": len(full_day_dates),
                "guan_partial_session_replacement_dates": 58,
                "top200_replacement_dates": 28,
                "missing_replacement_dates": 3,
            },
            "actions": full_day_actions,
            "source_receipts": full_day_sources,
        },
    )
    overlay_policy = {
        "operation": "append_disjoint_exchange_whole_day_rows",
        "eligible_base_tiers": ["annual_full_sh_sz", "deal_full_sh_sz"],
        "required_universe_rule": "dynamic-daily-universe:v1:exchange=BJ",
        "bars_per_symbol": 241,
        "same_security_intraday_merge": "forbidden",
        "tushare_full_a_share_dates": "reject_already_contains_bj",
        "market_scope_after_overlay": "SH_SZ_BJ",
        "source_validation": "passed",
        "tushare_price_flow_validation": dict(tushare_price_flow_policy()),
    }
    overlay_plan = tmp_path / "bj-overlay-plan.json"
    _write_manifest(
        overlay_plan,
        {
            "schema_version": "a_share.minute_tushare_bj_overlay_plan.v1",
            "phase": "production",
            "dates": overlay_dates,
        },
    )
    overlay_receipt = tmp_path / "bj-overlay-receipt.json"
    _write_manifest(
        overlay_receipt,
        {
            "schema_version": "a_share.minute_tushare_bj_overlay_receipt.v2",
            "status": "passed",
            "phase": "production",
            "plan_sha256": _file_sha256(overlay_plan),
            "output_dir": str(version),
            "policy": overlay_policy,
            "dates": overlay_dates,
            "summary": {"date_count": len(overlay_dates)},
            "actions": overlay_actions,
            "source_receipts": source_receipts,
        },
    )
    provider_root, guan_deal_dir, promotion_receipt, promotion_sha256 = _promotion_evidence(
        tmp_path
    )
    manifest = tmp_path / "coverage.json"
    _write_manifest(
        manifest,
        {
            "schema_version": "a_share.minute_1m.coverage.v1",
            "status": "passed",
            "quality_status": "passed",
            "coverage_status": "full_a_share",
            "output_dir": str(version),
            "requirements": {
                "expected_trade_dates": 2_556,
                "expected_annual_full_sh_sz_dates": 2_430,
                "expected_deal_full_sh_sz_dates": 37,
                "expected_tushare_full_a_share_dates": 89,
                "expected_guan_partial_session_dates": 0,
                "expected_partial_top200_dates": 0,
                "expected_accepted_zero_volume_nonzero_amount_rows": 136_246,
                "expected_accepted_positive_volume_zero_amount_rows": 1,
                "require_full_source_stats": True,
            },
            "summary": {
                "calendar_dates": 2_556,
                "date_min": "20160104",
                "date_max": "20260714",
                "partition_files": 2_556,
                "partition_dirs": 2_556,
                "annual_full_sh_sz_dates": 2_430,
                "deal_full_sh_sz_dates": 37,
                "tushare_full_a_share_dates": 89,
                "bj_overlay_date_count": 1_041,
                "tushare_bj_overlay_dates": 1_041,
                "guan_partial_session_dates": 0,
                "full_sh_sz_dates": 2_556,
                "full_a_share_dates": 2_556,
                "tushare_partial_top200_dates": 0,
                "missing_source_dates": 0,
                "missing_output_dates": 0,
                "invalid_output_dates": 0,
                "orphan_output_dates": 0,
                "accepted_zero_volume_nonzero_amount_rows": 136_246,
                "accepted_positive_volume_zero_amount_rows": 1,
                "accepted_guan_zero_volume_nonzero_amount_rows": 136_246,
                "accepted_guan_positive_volume_zero_amount_rows": 1,
            },
            "failures": {
                "missing_source_dates": [],
                "missing_output_dates": [],
                "invalid_output_dates": [],
                "orphan_output_dates": [],
                "source_orphan_dates": {},
                "unexpected_partition_files": [],
                "missing_full_source_stats": [],
                "requirement_mismatches": [],
            },
            "policy": {"overlap_policy": "explicit_whole_day_replacement_only_no_intraday_merge"},
            "inputs": {
                "bj_overlay_date_count": 1_041,
                "bj_overlay_dates": overlay_dates,
                "bj_overlay_selected_dates": 1_041,
                "tushare_full_selected_dates": 89,
                "lineage": {
                    "guan_deal_dir": str(guan_deal_dir),
                    "source_audits": [str(promotion_receipt)],
                    "source_audit_receipts": [
                        {
                            "path": str(promotion_receipt),
                            "sha256": promotion_sha256,
                            "schema_version": "guan.mobile_raw_promotion.v1",
                            "status": "complete",
                            "verification_status": "verified",
                        }
                    ],
                    "guan_mobile_promotion_receipt": str(promotion_receipt),
                    "guan_mobile_promotion_receipt_sha256": promotion_sha256,
                    "guan_mobile_promotion_verification_status": "verified",
                    "guan_mobile_provider_root": str(provider_root),
                    "tushare_full_phase": "production",
                    "tushare_full_dates": full_day_dates,
                    "tushare_full_plan": str(full_day_plan),
                    "tushare_full_plan_sha256": _file_sha256(full_day_plan),
                    "tushare_full_receipt": str(full_day_receipt),
                    "tushare_full_receipt_sha256": _file_sha256(full_day_receipt),
                    "tushare_full_status": "passed",
                    "tushare_full_policy": full_day_policy,
                    "tushare_bj_overlay_plan": str(overlay_plan),
                    "tushare_bj_overlay_plan_sha256": _file_sha256(overlay_plan),
                    "tushare_bj_overlay_receipt": str(overlay_receipt),
                    "tushare_bj_overlay_receipt_sha256": _file_sha256(overlay_receipt),
                    "tushare_bj_overlay_phase": "production",
                    "tushare_bj_overlay_status": "passed",
                    "tushare_bj_overlay_policy": overlay_policy,
                    "tushare_bj_overlay_dates": overlay_dates,
                },
            },
            "daily": daily,
        },
    )
    return current, version, backup, manifest


def _install_sh_sz_contract(manifest: Path) -> dict[str, Any]:
    payload = _read_manifest(manifest)
    payload["coverage_status"] = "full_sh_sz"
    payload["summary"].pop("bj_overlay_date_count")
    payload["summary"].pop("tushare_bj_overlay_dates")
    payload["summary"]["full_a_share_dates"] = 1_515
    inputs = payload["inputs"]
    inputs.pop("bj_overlay_date_count")
    inputs.pop("bj_overlay_dates")
    inputs.pop("bj_overlay_selected_dates")
    inputs["tushare_full_selected_dates"] = 89
    lineage = inputs["lineage"]
    for field in list(lineage):
        if field.startswith("tushare_bj_overlay_"):
            lineage.pop(field)
    for record in payload["daily"]:
        if record["tier"] in {"annual_full_sh_sz", "deal_full_sh_sz"}:
            record["market_scope"] = "SH_SZ"
        record["overlay_sources"] = []
    payload["known_gaps"] = {
        "bj_market": {
            "status": "point_in_time_partial_coverage",
            "affected_trade_dates": 1_041,
        },
        "partial_top200_dates": [],
        "guan_partial_session": {
            "status": "session_ends_at_14_57",
            "affected_trade_dates": 0,
            "dates": [],
        },
    }
    payload["policy"].update(
        {
            "source_priority": [
                "guan_deal_explicit_whole_day_override",
                "guan_annual_minbar",
                "guan_deal_non_annual_dates",
                "tushare_full_day_explicit_whole_day_replacement",
                "guan_annual_partial_session",
                "tushare_top200_missing_dates_only",
            ],
            "full_market_definition": ("point_in_time_A_share; TuShare full days include SH_SZ_BJ"),
        }
    )
    _write_manifest(manifest, payload)
    return payload


def _install_old_version_link(current: Path) -> Path:
    old_version = current.parent / "minute_1m_v2_20260710"
    current.rename(old_version)
    current.symlink_to(old_version.name)
    return old_version


def _run(  # noqa: PLR0913
    current: Path,
    version: Path,
    backup: Path,
    manifest: Path,
    *,
    dry_run: bool = False,
    target_market_scope: str = "full-a-share",
) -> dict[str, Any]:
    result = cutover.cutover_minute_current(
        coverage_manifest=manifest,
        current_path=current,
        new_version=version,
        backup_path=backup,
        dry_run=dry_run,
        target_market_scope=target_market_scope,
    )
    assert isinstance(result, dict)
    return result


def test_cutover_dry_run_exchange_and_idempotent_rerun(tmp_path: Path) -> None:
    current, version, backup, manifest = _fixture(tmp_path)

    planned = _run(current, version, backup, manifest, dry_run=True)
    assert planned["status"] == "planned"
    assert current.is_dir() and not current.is_symlink()
    assert not backup.exists()
    assert not Path(planned["pending_path"]).exists()

    result = _run(current, version, backup, manifest)
    assert result["status"] == "cutover_complete"
    assert current.is_symlink()
    assert os.readlink(current) == version.name
    assert current.resolve() == version.resolve()
    assert (backup / "old-marker").read_text(encoding="utf-8") == "old"

    repeated = _run(current, version, backup, manifest)
    assert repeated["status"] == "already_current"
    assert (backup / "old-marker").is_file()
    with pytest.raises(DatasetLockError, match="immutable current minute version"):
        with cutover.minute_dataset_lock(version, operation="late-minute-writer"):
            pass


def test_cutover_atomically_upgrades_existing_version_symlink(tmp_path: Path) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    old_version = _install_old_version_link(current)
    legacy_backup = current.parent / "minute_1m_pre_v2_20260710"
    legacy_backup.mkdir()

    planned = _run(current, version, backup, manifest, dry_run=True)
    assert planned["status"] == "planned"
    assert planned["previous_endpoint_type"] == "version_link"
    assert planned["previous_version"] == str(old_version)
    assert current.resolve() == old_version.resolve()
    assert not backup.exists()

    result = _run(current, version, backup, manifest)

    assert result["status"] == "cutover_complete"
    assert current.is_symlink() and os.readlink(current) == version.name
    assert current.resolve() == version.resolve()
    assert backup.is_symlink() and os.readlink(backup) == old_version.name
    assert backup.resolve() == old_version.resolve()
    assert (old_version / "old-marker").read_text(encoding="utf-8") == "old"
    assert legacy_backup.is_dir()

    repeated = _run(current, version, backup, manifest)
    assert repeated["status"] == "already_current"
    assert repeated["previous_version"] == str(old_version)


def test_cutover_requires_and_rechecks_content_hash(tmp_path: Path) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    payload = _read_manifest(manifest)
    del payload["daily"][0]["content_sha256"]
    _write_manifest(manifest, payload)
    with pytest.raises(cutover.MinuteCutoverError, match="content_sha256"):
        _run(current, version, backup, manifest)

    payload = _read_manifest(manifest)
    partition = version / "trade_date=20160104" / "part-00000.parquet"
    old_stat = partition.stat()
    old_content = partition.read_bytes()
    partition.write_bytes(b"x" * len(old_content))
    os.utime(partition, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    payload["daily"][0]["content_sha256"] = hashlib.sha256(old_content).hexdigest()
    _write_manifest(manifest, payload)
    with pytest.raises(cutover.MinuteCutoverError, match="hash changed after audit"):
        _run(current, version, backup, manifest)

    assert current.is_dir() and not current.is_symlink()
    assert not backup.exists()


def test_cutover_enforces_complete_fixed_production_contract(tmp_path: Path) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    original = _read_manifest(manifest)

    mutations: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
        (
            "coverage status",
            lambda payload: payload.__setitem__("coverage_status", "full_sh_sz"),
            "fixed full-A production contract",
        ),
        (
            "requirements",
            lambda payload: payload["requirements"].__setitem__("expected_trade_dates", 2),
            "expected_trade_dates",
        ),
        (
            "summary range",
            lambda payload: payload["summary"].__setitem__("date_min", "20260708"),
            "date_min",
        ),
        (
            "daily subset",
            lambda payload: payload["daily"].pop(),
            "complete 2556-date",
        ),
        (
            "tier count",
            lambda payload: payload["daily"][0].__setitem__("tier", "tushare_partial_top200"),
            "Invalid production coverage tier",
        ),
        (
            "BJ overlay count",
            lambda payload: payload["summary"].__setitem__("bj_overlay_date_count", 1_040),
            "bj_overlay_date_count",
        ),
    ]
    for _label, mutate, message in mutations:
        payload = json.loads(json.dumps(original))
        mutate(payload)
        _write_manifest(manifest, payload)
        with pytest.raises(cutover.MinuteCutoverError, match=message):
            _run(current, version, backup, manifest)


def test_sh_sz_cutover_requires_explicit_scope_and_complete_bound_full_days(
    tmp_path: Path,
) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    with pytest.raises(
        cutover.MinuteCutoverError,
        match="fixed full-Shanghai/Shenzhen production contract",
    ):
        cutover.validate_cutover_receipt(
            manifest,
            version,
            target_market_scope="sh-sz",
        )
    _install_sh_sz_contract(manifest)

    with pytest.raises(cutover.MinuteCutoverError, match="fixed full-A production contract"):
        cutover.validate_cutover_receipt(manifest, version)

    validated = cutover.validate_cutover_receipt(
        manifest,
        version,
        target_market_scope="sh-sz",
    )
    assert validated["coverage_status"] == "full_sh_sz"
    assert validated["summary"]["full_sh_sz_dates"] == 2_556
    assert validated["summary"]["full_a_share_dates"] == 1_515
    assert validated["known_gaps"]["bj_market"]["affected_trade_dates"] == 1_041
    assert not any(
        field.startswith("tushare_bj_overlay_") for field in validated["inputs"]["lineage"]
    )

    planned = _run(
        current,
        version,
        backup,
        manifest,
        dry_run=True,
        target_market_scope="sh-sz",
    )
    assert planned["status"] == "planned"
    assert planned["target_market_scope"] == "sh-sz"


def test_sh_sz_cutover_rejects_incomplete_or_obscured_scope_contract(
    tmp_path: Path,
) -> None:
    _current, version, _backup, manifest = _fixture(tmp_path)
    original = _install_sh_sz_contract(manifest)

    mutations: list[tuple[Callable[[dict[str, Any]], None], str]] = [
        (
            lambda payload: payload["known_gaps"]["bj_market"].__setitem__(
                "affected_trade_dates", 1_040
            ),
            "exact transparent 1041-date BJ known gap",
        ),
        (
            lambda payload: payload["inputs"].__setitem__("tushare_full_selected_dates", 85),
            "all 89 full-day selections",
        ),
        (
            lambda payload: payload["inputs"]["lineage"].__setitem__(
                "tushare_bj_overlay_receipt", "/tmp/unbound.json"
            ),
            "known gap, not bind BJ overlay evidence",
        ),
        (
            lambda payload: payload["summary"].__setitem__("bj_overlay_date_count", 1_041),
            "summary must not claim BJ overlays",
        ),
        (
            lambda payload: payload["policy"].__setitem__(
                "full_market_definition", "point_in_time_A_share; SH_SZ_BJ"
            ),
            "policy does not expose the BJ coverage gap",
        ),
        (
            lambda payload: payload["daily"][0].__setitem__("tier", "guan_partial_session"),
            "Invalid production coverage tier",
        ),
        (
            lambda payload: payload["daily"][0].__setitem__("tier", "tushare_partial_top200"),
            "Invalid production coverage tier",
        ),
        (
            lambda payload: payload["daily"][0].__setitem__("valid", False),
            "missing or invalid daily partition",
        ),
        (
            lambda payload: payload["daily"][0].__setitem__(
                "overlay_sources", ["tushare_bj_overlay"]
            ),
            "contains an overlay or wrong scope",
        ),
        (
            lambda payload: payload["failures"]["missing_source_dates"].append("20260709"),
            "quality failures",
        ),
    ]
    for mutate, message in mutations:
        payload = json.loads(json.dumps(original))
        mutate(payload)
        _write_manifest(manifest, payload)
        with pytest.raises(cutover.MinuteCutoverError, match=message):
            cutover.validate_cutover_receipt(
                manifest,
                version,
                target_market_scope="sh-sz",
            )


def test_cutover_cli_market_scope_defaults_to_strict_full_a() -> None:
    required = [
        "--coverage-manifest",
        "coverage.json",
        "--current-path",
        "current",
        "--new-version",
        "version",
        "--backup-path",
        "backup",
    ]
    assert cutover._parser().parse_args(required).target_market_scope == "full-a-share"
    assert (
        cutover._parser()
        .parse_args([*required, "--target-market-scope", "sh-sz"])
        .target_market_scope
        == "sh-sz"
    )


def test_cutover_rejects_version_root_symlink(tmp_path: Path) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    real_version = version.with_name("real-version")
    version.rename(real_version)
    version.symlink_to(real_version.name)

    with pytest.raises(cutover.MinuteCutoverError, match="real directory"):
        _run(current, version, backup, manifest)
    assert current.is_dir() and not current.is_symlink()


def test_cutover_rejects_partition_directory_symlink_escape(tmp_path: Path) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    partition = version / "trade_date=20160104"
    escaped = version.parent / "escaped-partition"
    partition.rename(escaped)
    partition.symlink_to(escaped, target_is_directory=True)

    with pytest.raises(cutover.MinuteCutoverError, match="Symlink is forbidden"):
        _run(current, version, backup, manifest)
    assert current.is_dir() and not current.is_symlink()


def test_cutover_rejects_partition_file_symlink_escape(tmp_path: Path) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    partition = version / "trade_date=20160104" / "part-00000.parquet"
    escaped = version.parent / "escaped.parquet"
    partition.rename(escaped)
    partition.symlink_to(escaped)

    with pytest.raises(cutover.MinuteCutoverError, match="real regular file"):
        _run(current, version, backup, manifest)
    assert current.is_dir() and not current.is_symlink()


def test_cutover_rejects_partition_hardlinked_to_another_version(tmp_path: Path) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    partition = version / "trade_date=20160104" / "part-00000.parquet"
    old_version_partition = (
        version.parent / "minute_1m_v2_20260710" / "trade_date=20160104" / partition.name
    )
    old_version_partition.parent.mkdir(parents=True)
    os.link(partition, old_version_partition)
    assert partition.stat().st_nlink == 2

    with pytest.raises(cutover.MinuteCutoverError, match="still hardlinked"):
        _run(current, version, backup, manifest)

    assert current.is_dir() and not current.is_symlink()
    assert not backup.exists()


def test_cutover_rejects_receipt_path_escape_and_nonrelative_pending_link(
    tmp_path: Path,
) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    payload = _read_manifest(manifest)
    payload["daily"][0]["path"] = str(
        version / "trade_date=20160104" / ".." / ".." / "escaped.parquet"
    )
    _write_manifest(manifest, payload)
    with pytest.raises(cutover.MinuteCutoverError, match="path no longer matches"):
        _run(current, version, backup, manifest)

    payload["daily"][0]["path"] = str(version / "trade_date=20160104" / "part-00000.parquet")
    _write_manifest(manifest, payload)
    pending = current.parent / ".minute_1m.cutover-pending"
    pending.symlink_to(version.resolve())
    with pytest.raises(cutover.MinuteCutoverError, match="Pending cutover link"):
        _run(current, version, backup, manifest)
    assert current.is_dir() and not current.is_symlink()
    assert not backup.exists()


def test_symlink_upgrade_rejects_current_and_backup_target_escape(tmp_path: Path) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    outside = tmp_path / "outside-version"
    current.rename(outside)
    current.symlink_to(os.path.relpath(outside, current.parent))

    with pytest.raises(cutover.MinuteCutoverError, match="Current version link"):
        _run(current, version, backup, manifest)
    assert current.is_symlink() and current.resolve() == outside.resolve()
    assert not backup.exists()

    current.unlink()
    current.symlink_to(version.name)
    backup.symlink_to(os.path.relpath(outside, backup.parent))
    with pytest.raises(cutover.MinuteCutoverError, match="Backup must"):
        _run(current, version, backup, manifest)
    assert current.is_symlink() and current.resolve() == version.resolve()
    assert backup.is_symlink() and backup.resolve() == outside.resolve()


def test_cutover_accepts_only_hash_bound_fixed_full_a_production_contract(
    tmp_path: Path,
) -> None:
    _current, version, _backup, manifest = _fixture(tmp_path)
    payload = _read_manifest(manifest)

    validated = cutover.validate_cutover_receipt(manifest, version)

    assert validated["coverage_status"] == "full_a_share"
    assert validated["summary"]["tushare_full_a_share_dates"] == 89
    assert validated["summary"]["bj_overlay_date_count"] == 1_041

    pilot = json.loads(json.dumps(payload))
    pilot["inputs"]["lineage"]["tushare_bj_overlay_phase"] = "pilot"
    _write_manifest(manifest, pilot)
    with pytest.raises(cutover.MinuteCutoverError, match="production-phase"):
        cutover.validate_cutover_receipt(manifest, version)

    subset = json.loads(json.dumps(payload))
    subset["inputs"]["lineage"]["tushare_bj_overlay_dates"].pop()
    _write_manifest(manifest, subset)
    with pytest.raises(cutover.MinuteCutoverError, match="fixed production date set"):
        cutover.validate_cutover_receipt(manifest, version)

    _write_manifest(manifest, payload)
    receipt_path = Path(payload["inputs"]["lineage"]["tushare_bj_overlay_receipt"])
    receipt_bytes = receipt_path.read_bytes()
    receipt_path.write_bytes(receipt_bytes + b"\n")
    with pytest.raises(cutover.MinuteCutoverError, match="hash changed"):
        cutover.validate_cutover_receipt(manifest, version)

    receipt_path.write_bytes(receipt_bytes)
    unbound_bj_receipt = _read_manifest(receipt_path)
    unbound_bj_receipt["plan_sha256"] = "0" * 64
    _write_manifest(receipt_path, unbound_bj_receipt)
    unbound_bj_coverage = json.loads(json.dumps(payload))
    unbound_bj_coverage["inputs"]["lineage"]["tushare_bj_overlay_receipt_sha256"] = _file_sha256(
        receipt_path
    )
    _write_manifest(manifest, unbound_bj_coverage)
    with pytest.raises(cutover.MinuteCutoverError, match="current production plan hash"):
        cutover.validate_cutover_receipt(manifest, version)

    receipt_path.write_bytes(receipt_bytes)
    _write_manifest(manifest, payload)
    full_receipt_path = Path(payload["inputs"]["lineage"]["tushare_full_receipt"])
    full_receipt_bytes = full_receipt_path.read_bytes()
    full_receipt_path.write_bytes(full_receipt_bytes + b"\n")
    with pytest.raises(cutover.MinuteCutoverError, match="full-day TuShare receipt hash changed"):
        cutover.validate_cutover_receipt(manifest, version)

    full_receipt_path.write_bytes(full_receipt_bytes)
    unbound_receipt = _read_manifest(full_receipt_path)
    unbound_receipt["plan_sha256"] = "0" * 64
    _write_manifest(full_receipt_path, unbound_receipt)
    unbound_coverage = json.loads(json.dumps(payload))
    unbound_coverage["inputs"]["lineage"]["tushare_full_receipt_sha256"] = _file_sha256(
        full_receipt_path
    )
    _write_manifest(manifest, unbound_coverage)
    with pytest.raises(cutover.MinuteCutoverError, match="bind the current plan hash"):
        cutover.validate_cutover_receipt(manifest, version)


def test_cutover_requires_verified_hash_bound_raw_promotion(tmp_path: Path) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    payload = _read_manifest(manifest)
    lineage = payload["inputs"]["lineage"]
    promotion_path = Path(lineage["guan_mobile_promotion_receipt"])
    promotion = _read_manifest(promotion_path)
    promotion["verification_status"] = "not_run"
    _write_manifest(promotion_path, promotion)
    changed_sha256 = _file_sha256(promotion_path)
    lineage["guan_mobile_promotion_receipt_sha256"] = changed_sha256
    lineage["source_audit_receipts"][0]["sha256"] = changed_sha256
    _write_manifest(manifest, payload)

    with pytest.raises(cutover.MinuteCutoverError, match="not complete and verified"):
        _run(current, version, backup, manifest)
    assert current.is_dir() and not current.is_symlink()

    current, version, backup, manifest = _fixture(tmp_path / "ancestry")
    payload = _read_manifest(manifest)
    wrong_deal_dir = tmp_path / "wrong-provider" / "deal"
    wrong_deal_dir.mkdir(parents=True)
    payload["inputs"]["lineage"]["guan_deal_dir"] = str(wrong_deal_dir)
    _write_manifest(manifest, payload)
    with pytest.raises(cutover.MinuteCutoverError, match="promoted provider root"):
        _run(current, version, backup, manifest)
    assert current.is_dir() and not current.is_symlink()


def test_unsupported_exchange_fails_before_modifying_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, version, backup, manifest = _fixture(tmp_path)

    def unsupported(_parent: Path) -> None:
        raise cutover.MinuteCutoverError("unsupported test filesystem")

    monkeypatch.setattr(cutover, "_probe_rename_exchange", unsupported)
    with pytest.raises(cutover.MinuteCutoverError, match="unsupported test filesystem"):
        _run(current, version, backup, manifest)

    assert current.is_dir() and not current.is_symlink()
    assert (current / "old-marker").is_file()
    assert not backup.exists()
    assert not (current.parent / ".minute_1m.cutover-pending").exists()


def test_cutover_recovers_crash_after_pending_link_before_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    real_exchange = cutover._rename_exchange
    calls = 0

    def fail_production_exchange(left: Path, right: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated process loss before exchange")
        real_exchange(left, right)

    monkeypatch.setattr(cutover, "_rename_exchange", fail_production_exchange)
    with pytest.raises(OSError, match="simulated process loss"):
        _run(current, version, backup, manifest)

    pending = current.parent / ".minute_1m.cutover-pending"
    assert current.is_dir() and not current.is_symlink()
    assert pending.is_symlink() and pending.resolve() == version.resolve()
    assert not backup.exists()

    monkeypatch.setattr(cutover, "_rename_exchange", real_exchange)
    recovered = _run(current, version, backup, manifest)
    assert recovered["status"] == "recovered_before_exchange"
    assert current.is_symlink() and current.resolve() == version.resolve()
    assert (backup / "old-marker").is_file()
    assert not pending.exists()


def test_cutover_recovers_crash_after_exchange_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    real_preserve = cutover._preserve_pending_directory

    def fail_preserve(_pending: Path, _backup: Path) -> None:
        raise OSError("simulated process loss after exchange")

    monkeypatch.setattr(cutover, "_preserve_pending_directory", fail_preserve)
    with pytest.raises(OSError, match="simulated process loss"):
        _run(current, version, backup, manifest)

    pending = current.parent / ".minute_1m.cutover-pending"
    assert current.is_symlink() and current.resolve() == version.resolve()
    assert pending.is_dir() and not pending.is_symlink()
    assert (pending / "old-marker").is_file()
    assert not backup.exists()

    monkeypatch.setattr(cutover, "_preserve_pending_directory", real_preserve)
    recovered = _run(current, version, backup, manifest)
    assert recovered["status"] == "recovered_after_exchange"
    assert current.is_symlink() and current.resolve() == version.resolve()
    assert (backup / "old-marker").is_file()
    assert not pending.exists()


def test_symlink_upgrade_recovers_before_atomic_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    old_version = _install_old_version_link(current)
    real_exchange = cutover._rename_exchange
    calls = 0

    def fail_production_exchange(left: Path, right: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated symlink process loss before exchange")
        real_exchange(left, right)

    monkeypatch.setattr(cutover, "_rename_exchange", fail_production_exchange)
    with pytest.raises(OSError, match="before exchange"):
        _run(current, version, backup, manifest)

    pending = current.parent / ".minute_1m.cutover-pending"
    assert current.is_symlink() and current.resolve() == old_version.resolve()
    assert pending.is_symlink() and pending.resolve() == version.resolve()
    assert not backup.exists()

    monkeypatch.setattr(cutover, "_rename_exchange", real_exchange)
    recovered = _run(current, version, backup, manifest)
    assert recovered["status"] == "recovered_before_exchange"
    assert current.is_symlink() and current.resolve() == version.resolve()
    assert backup.is_symlink() and backup.resolve() == old_version.resolve()
    assert not pending.exists()


def test_symlink_upgrade_recovers_after_atomic_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    old_version = _install_old_version_link(current)
    real_preserve = cutover._preserve_pending_directory

    def fail_preserve(_pending: Path, _backup: Path) -> None:
        raise OSError("simulated symlink process loss after exchange")

    monkeypatch.setattr(cutover, "_preserve_pending_directory", fail_preserve)
    with pytest.raises(OSError, match="after exchange"):
        _run(current, version, backup, manifest)

    pending = current.parent / ".minute_1m.cutover-pending"
    assert current.is_symlink() and current.resolve() == version.resolve()
    assert pending.is_symlink() and pending.resolve() == old_version.resolve()
    assert not backup.exists()

    monkeypatch.setattr(cutover, "_preserve_pending_directory", real_preserve)
    recovered = _run(current, version, backup, manifest)
    assert recovered["status"] == "recovered_after_exchange"
    assert current.is_symlink() and current.resolve() == version.resolve()
    assert backup.is_symlink() and backup.resolve() == old_version.resolve()
    assert not pending.exists()


def test_atomic_exchange_has_no_missing_current_for_concurrent_readers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    _install_old_version_link(current)
    relative = Path("trade_date=20160104") / "part-00000.parquet"
    old_visible = current / relative
    old_visible.parent.mkdir()
    old_visible.write_bytes(b"partition-20160104")
    expected_values = {b"partition-20160104"}
    stop = threading.Event()
    started = threading.Event()
    errors: list[BaseException] = []
    reads = 0

    def reader() -> None:
        nonlocal reads
        started.set()
        while not stop.is_set():
            try:
                value = (current / relative).read_bytes()
                if value not in expected_values:
                    raise AssertionError(f"unexpected reader value: {value!r}")
                reads += 1
            except BaseException as exc:
                errors.append(exc)
                stop.set()

    real_preserve = cutover._preserve_pending_directory

    def slow_preserve(pending: Path, target: Path) -> None:
        time.sleep(0.05)
        real_preserve(pending, target)

    monkeypatch.setattr(cutover, "_preserve_pending_directory", slow_preserve)
    thread = threading.Thread(target=reader)
    thread.start()
    assert started.wait(timeout=1)
    try:
        result = _run(current, version, backup, manifest)
    finally:
        stop.set()
        thread.join(timeout=2)

    assert result["status"] == "cutover_complete"
    assert reads > 0
    assert errors == []


def test_cutover_refuses_backup_conflict_and_shared_dataset_lock(tmp_path: Path) -> None:
    current, version, backup, manifest = _fixture(tmp_path)
    backup.mkdir()
    with pytest.raises(cutover.MinuteCutoverError, match="recoverable cutover state"):
        _run(current, version, backup, manifest)
    backup.rmdir()

    with cutover.minute_dataset_lock(version, operation="another-minute-writer"):
        with pytest.raises(DatasetLockError, match="Dataset lock is held"):
            _run(current, version, backup, manifest)
    assert current.is_dir() and not current.is_symlink()
    assert not backup.exists()
