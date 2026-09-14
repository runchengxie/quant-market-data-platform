from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from market_data_platform import (
    minute_candidate,
    minute_candidate_audit,
    minute_candidate_publish,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_resolve_campaign_locations_rejects_conflicting_roots(tmp_path: Path) -> None:
    manifest = {
        "baseline": {
            "complete": {"20260102": {"data_root": str(tmp_path / "one")}},
            "partial": {},
        },
        "days": [
            {
                "phases": [
                    {
                        "lanes": {
                            "a": {
                                "data_root": str(tmp_path / "two"),
                                "dates": ["20260102"],
                            }
                        }
                    }
                ]
            }
        ],
    }

    with pytest.raises(minute_candidate.MinuteCandidateError, match="multiple roots"):
        minute_candidate.resolve_campaign_locations(manifest)


def _candidate_inventory_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    trade_date = "20260102"
    data_root = tmp_path / "candidate"
    partition_dir = data_root / f"trade_date={trade_date}"
    partition_dir.mkdir(parents=True)
    partition_path = partition_dir / "part-00000.parquet"
    sidecar_path = partition_dir / "_minute_mirror.json"
    partition_path.write_bytes(b"candidate")
    sidecar_path.write_text("{}", encoding="utf-8")
    canonical_path = tmp_path / "canonical.parquet"
    canonical_path.write_bytes(b"canonical")
    manifest_path = tmp_path / "campaign" / "manifest.json"
    ledger_path = manifest_path.parent / "ledger.json"
    manifest = {
        "schema_version": minute_candidate.CAMPAIGN_SCHEMA_VERSION,
        "ledger_path": str(ledger_path),
        "target": {"dates": 1},
        "baseline": {
            "complete": {trade_date: {"data_root": str(data_root)}},
            "partial": {},
        },
        "days": [],
    }
    _write_json(manifest_path, manifest)
    _write_json(ledger_path, {"status": "complete"})
    readiness_path = manifest_path.parent / "acquisition-readiness.json"
    _write_json(
        readiness_path,
        {
            "schema_version": minute_candidate.READINESS_SCHEMA_VERSION,
            "manifest_path": str(manifest_path),
            "manifest_sha256": minute_candidate.file_sha256(manifest_path),
            "ledger_path": str(ledger_path),
            "ledger_sha256_at_reconciliation": minute_candidate.file_sha256(ledger_path),
            "acquisition_complete": True,
            "structural_ready": True,
            "semantic_audit": "pending",
            "promotion_ready": False,
            "writes_production": False,
            "dates_complete": 1,
            "rows": 241,
            "date_receipts_sha256": "a" * 64,
        },
    )
    coverage_path = tmp_path / "coverage.json"
    _write_json(
        coverage_path,
        {
            "status": "passed",
            "quality_status": "passed",
            "output_dir": str(tmp_path),
            "daily": [
                {
                    "date": trade_date,
                    "path": str(canonical_path),
                    "content_sha256": minute_candidate.file_sha256(canonical_path),
                    "rows": 240,
                    "symbols": 1,
                    "canonical_source": "guan_annual_minbar",
                    "tier": "annual_full_sh_sz",
                    "market_scope": "SH_SZ",
                    "time_min": "2026-01-02 09:31:00",
                    "time_max": "2026-01-02 15:00:00",
                }
            ],
        },
    )
    return manifest_path, coverage_path, partition_path, sidecar_path


def test_build_candidate_inventory_binds_candidate_and_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path, coverage_path, partition_path, sidecar_path = _candidate_inventory_fixture(
        tmp_path
    )
    monkeypatch.setattr(
        minute_candidate,
        "_validate_partition",
        lambda _root, _date: {
            "partition_path": str(partition_path),
            "sidecar_path": str(sidecar_path),
            "partition_sha256": "b" * 64,
            "sidecar_sha256": "c" * 64,
            "rows": 241,
            "symbols": 1,
            "market_symbol_counts": {"SH": 1, "SZ": 0, "BJ": 0},
            "expected_bars_per_symbol": 241,
            "universe_hash": "d" * 64,
            "universe_rule": "test",
        },
    )

    payload = minute_candidate.build_candidate_inventory(
        manifest_path,
        coverage_path,
        tmp_path / "inventory.json",
    )

    assert payload["status"] == "passed"
    assert payload["summary"]["dates"] == 1
    assert payload["summary"]["rows"] == 241
    assert payload["partitions"][0]["canonical"]["canonical_source"] == "guan_annual_minbar"


def _semantic_source_frames(
    trade_date: str,
    symbols: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    canonical_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for index, symbol in enumerate(symbols, start=1):
        base = float(index * 10)
        candidate_rows.append(
            {
                "ts_code": symbol,
                "trade_time": pd.Timestamp(f"{trade_date} 09:30:00"),
                "open": base - 0.1,
                "close": base,
                "high": base,
                "low": base - 0.1,
                "vol": 5.0,
                "amount": 5.0 * base,
            }
        )
        for minute in ("09:31:00", "09:32:00"):
            volume = float(10 * index)
            row = {
                "ts_code": symbol,
                "trade_time": pd.Timestamp(f"{trade_date} {minute}"),
                "open": base,
                "close": base + 0.1,
                "high": base + 0.2,
                "low": base,
                "vol": volume,
                "amount": volume * (base + 0.1),
            }
            canonical_rows.append(row)
            candidate_rows.append(row)
    return pd.DataFrame(canonical_rows), pd.DataFrame(candidate_rows)


def test_semantic_audit_aligns_annual_candidate_after_0930(tmp_path: Path) -> None:
    trade_date = "20260102"
    canonical_path = tmp_path / "guan.parquet"
    candidate_path = tmp_path / "tushare.parquet"
    sidecar_path = tmp_path / "_minute_mirror.json"
    sidecar_path.write_text("{}", encoding="utf-8")
    symbols = ["000001.SZ", "600000.SH", "600001.SH"]
    canonical, candidate = _semantic_source_frames(trade_date, symbols)
    canonical.to_parquet(canonical_path, index=False)
    candidate.to_parquet(candidate_path, index=False)
    partitions = [
        {
            "trade_date": trade_date,
            "candidate": {
                "partition_path": str(candidate_path),
                "sidecar_path": str(sidecar_path),
                "partition_sha256": "a" * 64,
                "sidecar_sha256": "c" * 64,
                "rows": 9,
                "symbols": 4,
                "market_symbol_counts": {"SH": 2, "SZ": 1, "BJ": 1},
            },
            "canonical": {
                "partition_path": str(canonical_path),
                "partition_sha256": "b" * 64,
                "canonical_source": "guan_annual_minbar",
                "tier": "annual_full_sh_sz",
            },
        }
    ]
    inventory_path = tmp_path / "inventory.json"
    _write_json(
        inventory_path,
        {
            "schema_version": minute_candidate.INVENTORY_SCHEMA_VERSION,
            "status": "passed",
            "partitions_sha256": minute_candidate._json_sha256(partitions),
            "summary": {
                "dates": 1,
                "date_min": trade_date,
                "date_max": trade_date,
                "rows": 9,
                "symbol_days": 4,
            },
            "partitions": partitions,
        },
    )

    audit_path = tmp_path / "audit.json"
    payload = minute_candidate_audit.audit_candidate_semantics(
        inventory_path,
        audit_path,
        tmp_path / "work",
        threads=1,
    )

    assert payload["summary"]["dates"] == 1
    assert payload["summary"]["price"]["mean_abs_error"] == pytest.approx(0.0)
    assert payload["summary"]["universe"]["canonical_only_symbol_days"] == 0
    assert payload["daily"][0]["time_grid"]["comparison_excludes_candidate_0930"] is True
    assert payload["daily"][0]["feature_regression"]["volume"]["rank_correlation"] == pytest.approx(
        1.0
    )

    candidate_dir = tmp_path / "published"
    receipt = minute_candidate_publish.publish_candidate(
        inventory_path,
        audit_path,
        candidate_dir,
        tmp_path / "candidate-receipt.json",
    )
    published = candidate_dir / f"trade_date={trade_date}" / "part-00000.parquet"
    assert receipt["status"] == "published_candidate"
    assert receipt["current_alias_mutated"] is False
    assert published.stat().st_ino == candidate_path.stat().st_ino
