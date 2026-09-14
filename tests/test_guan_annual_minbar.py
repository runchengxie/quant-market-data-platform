from __future__ import annotations

import calendar
import json
import socket
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from market_data_platform.providers import guan_annual_minbar as annual
from market_data_platform.providers.guan_annual_minbar import (
    CANONICAL_MINUTE_COLUMNS,
    CANONICAL_MINUTE_SCHEMA,
    AnnualMinbarBuildError,
    AnnualMinbarBuildOptions,
    AnnualMinbarValidationError,
    build_guan_annual_minbar,
)
from market_data_platform.runtime_memory import MemorySnapshot


def _wall_epoch(value: str) -> int:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return calendar.timegm(parsed.timetuple())


def _source_row(date: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ticker": "000001",
        "timestamp": _wall_epoch(f"{date} 09:31:00"),
        "open": 10.0,
        "high": 10.2,
        "low": 9.9,
        "close": 10.1,
        "volume": 2.0,
        "amount": 20.0,
    }
    row.update(overrides)
    return row


def _write_source(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    source_schema = pa.schema(
        [
            pa.field("ticker", pa.string()),
            pa.field("timestamp", pa.int64()),
            pa.field("open", pa.float64()),
            pa.field("high", pa.float64()),
            pa.field("low", pa.float64()),
            pa.field("close", pa.float64()),
            pa.field("volume", pa.float64()),
            pa.field("amount", pa.float64()),
        ]
    )
    columns = {
        field.name: pa.array([row[field.name] for row in rows], type=field.type)
        for field in source_schema
    }
    pq.write_table(pa.table(columns, schema=source_schema), path, row_group_size=2)


def _options(tmp_path: Path, source: Path, year: int, **overrides: object):
    values: dict[str, Any] = {
        "source_path": source,
        "output_dir": tmp_path / "output",
        "manifest_path": tmp_path / "annual-manifest.json",
        "year": year,
        "threads": 1,
    }
    values.update(overrides)
    return AnnualMinbarBuildOptions(**values)


def _part(root: Path, date: str) -> Path:
    return root / f"trade_date={date.replace('-', '')}" / "part-00000.parquet"


def _read_part(path: Path):
    return pq.ParquetFile(path).read().to_pandas()


def _canonical_row(date: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ts_code": "000001.SZ",
        "trade_time": datetime.strptime(f"{date} 09:31:00", "%Y-%m-%d %H:%M:%S"),
        "open": 10.0,
        "close": 10.1,
        "high": 10.2,
        "low": 9.9,
        "vol": 200.0,
        "amount": 20.0,
    }
    row.update(overrides)
    return row


def _write_canonical(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays = {
        field.name: pa.array([row[field.name] for row in rows], type=field.type)
        for field in CANONICAL_MINUTE_SCHEMA
    }
    pq.write_table(pa.table(arrays, schema=CANONICAL_MINUTE_SCHEMA), path)


def test_annual_2025_single_scan_units_cleanup_mapping_and_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "minbar_2025.parquet"
    empty = _source_row("2025-01-03", ticker="000002")
    for column in annual._NUMERIC_SOURCE_COLUMNS:
        empty[column] = float("nan")
    _write_source(
        source,
        [
            _source_row("2025-01-02", high=9.8, low=10.3),
            _source_row("2025-01-02", ticker="600000", volume=3.0, amount=60.0),
            _source_row("2025-01-03", ticker="430001", volume=4.0, amount=120.0),
            _source_row("2025-01-03", ticker="302132"),
            empty,
        ],
    )
    scans = 0
    original_copy = annual._copy_source_to_raw_staging

    def counted_copy(
        connection: Any,
        *,
        source: Path,
        raw_root: Path,
        year: int,
        profile: Any,
    ) -> None:
        nonlocal scans
        scans += 1
        original_copy(connection, source=source, raw_root=raw_root, year=year, profile=profile)

    monkeypatch.setattr(annual, "_copy_source_to_raw_staging", counted_copy)
    result = build_guan_annual_minbar(_options(tmp_path, source, 2025))

    assert scans == 1
    assert result["source_full_scans"] == 1
    assert result["source_stats"] == {
        "input_rows": 5,
        "dropped_empty_rows": 1,
        "unmapped_ticker_rows": 0,
        "dropped_off_session_zero_flow_rows": 0,
        "zero_volume_nonzero_amount_rows": 0,
        "positive_volume_zero_amount_rows": 0,
        "partial_null_rows": 0,
        "non_finite_rows": 0,
        "invalid_timestamp_rows": 0,
        "wrong_year_rows": 0,
        "non_minute_rows": 0,
        "off_session_rows": 0,
        "negative_flow_rows": 0,
        "repaired_ohlc_rows": 1,
        "candidate_rows": 4,
    }
    first_part = _part(tmp_path / "output", "2025-01-02")
    assert pq.read_schema(first_part).equals(CANONICAL_MINUTE_SCHEMA)
    assert pq.read_schema(first_part).names == list(CANONICAL_MINUTE_COLUMNS)
    frame = _read_part(first_part)
    assert frame["ts_code"].tolist() == ["000001.SZ", "600000.SH"]
    assert frame["trade_time"].tolist()[0] == datetime(2025, 1, 2, 9, 31)
    assert frame.loc[0, "vol"] == pytest.approx(200.0)
    assert frame.loc[0, "amount"] == pytest.approx(20.0)
    assert frame.loc[0, "high"] == pytest.approx(10.1)
    assert frame.loc[0, "low"] == pytest.approx(10.0)
    assert _read_part(_part(tmp_path / "output", "2025-01-03"))["ts_code"].tolist() == [
        "302132.SZ",
        "430001.BJ",
    ]


def test_annual_2026_uses_shares_and_divides_amount_by_100(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2026.parquet"
    _write_source(source, [_source_row("2026-01-05", volume=1_527_100.0, amount=1_746_163_000.0)])

    result = build_guan_annual_minbar(_options(tmp_path, source, 2026))
    frame = _read_part(_part(tmp_path / "output", "2026-01-05"))

    assert result["unit_profile"]["name"] == "guan_shares_cents_times_shares"
    assert frame.loc[0, "vol"] == pytest.approx(1_527_100.0)
    assert frame.loc[0, "amount"] == pytest.approx(17_461_630.0)


def test_auto_memory_budget_is_recorded_in_result_and_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02")])
    snapshot = MemorySnapshot(
        total_mb=32 * 1024,
        available_mb=24 * 1024,
        rss_mb=512,
        system_total_mb=32 * 1024,
        system_available_mb=24 * 1024,
    )
    monkeypatch.setattr(annual, "read_memory_snapshot", lambda: snapshot)

    result = build_guan_annual_minbar(_options(tmp_path, source, 2025))

    expected = {
        "requested": "auto",
        "resolved": "15872MiB",
        "resolved_mb": 15_872,
        "snapshot": snapshot.to_dict(),
    }
    assert result["memory_limit"] == expected
    manifest = json.loads((tmp_path / "annual-manifest.json").read_text(encoding="utf-8"))
    assert manifest["years"]["2025"]["memory_limit"] == expected
    assert expected["snapshot"]["system_total_mb"] == 32 * 1024


def test_explicit_memory_limit_is_passed_through_and_audited(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02")])

    result = build_guan_annual_minbar(_options(tmp_path, source, 2025, memory_limit="8GB"))

    assert result["memory_limit"]["requested"] == "8GB"
    assert result["memory_limit"]["resolved"] == "8192MiB"
    assert result["memory_limit"]["resolved_mb"] == 8192


def test_unitless_explicit_memory_limit_is_normalized_for_duckdb(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02")])

    result = build_guan_annual_minbar(_options(tmp_path, source, 2025, memory_limit="1536"))

    assert result["memory_limit"]["requested"] == "1536"
    assert result["memory_limit"]["resolved"] == "1536MiB"
    assert result["memory_limit"]["resolved_mb"] == 1536


def test_exact_1300_equal_price_zero_flow_placeholder_is_dropped(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(
        source,
        [
            _source_row("2025-01-02"),
            _source_row(
                "2025-01-31",
                timestamp=_wall_epoch("2025-01-31 13:00:00"),
                open=10.0,
                close=10.0,
                high=10.0,
                low=10.0,
                volume=0.0,
                amount=0.0,
            ),
        ],
    )

    result = build_guan_annual_minbar(_options(tmp_path, source, 2025))

    assert result["source_stats"]["dropped_off_session_zero_flow_rows"] == 1
    assert result["source_stats"]["off_session_rows"] == 0
    assert result["source_stats"]["candidate_rows"] == 1
    assert result["staging_validation"]["dates"] == ["20250102"]
    assert not _part(tmp_path / "output", "2025-01-31").exists()


def test_inconsistent_zero_flow_rows_are_preserved_and_audited(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(
        source,
        [
            _source_row("2025-01-02"),
            _source_row(
                "2025-01-02",
                ticker="600000",
                timestamp=_wall_epoch("2025-01-02 09:32:00"),
                volume=0.0,
                amount=125.0,
            ),
            _source_row(
                "2025-01-02",
                ticker="300001",
                timestamp=_wall_epoch("2025-01-02 09:33:00"),
                volume=1.0,
                amount=0.0,
            ),
        ],
    )

    result = build_guan_annual_minbar(_options(tmp_path, source, 2025))
    frame = _read_part(_part(tmp_path / "output", "2025-01-02"))

    assert result["source_stats"]["zero_volume_nonzero_amount_rows"] == 1
    assert result["source_stats"]["positive_volume_zero_amount_rows"] == 1
    assert result["source_stats"]["candidate_rows"] == 3
    assert frame["ts_code"].tolist() == ["000001.SZ", "300001.SZ", "600000.SH"]


@pytest.mark.parametrize(
    "off_session_row",
    [
        _source_row(
            "2025-01-31",
            timestamp=_wall_epoch("2025-01-31 13:00:00"),
            open=10.0,
            close=10.0,
            high=10.0,
            low=10.0,
            volume=1.0,
            amount=0.0,
        ),
        _source_row(
            "2025-01-31",
            timestamp=_wall_epoch("2025-01-31 13:00:00"),
            open=10.0,
            close=10.0,
            high=10.0,
            low=10.0,
            volume=0.0,
            amount=1.0,
        ),
        _source_row(
            "2025-01-31",
            timestamp=_wall_epoch("2025-01-31 13:00:00"),
            open=10.0,
            close=10.1,
            high=10.1,
            low=10.0,
            volume=0.0,
            amount=0.0,
        ),
        _source_row(
            "2025-01-31",
            timestamp=_wall_epoch("2025-01-31 12:00:00"),
            open=10.0,
            close=10.0,
            high=10.0,
            low=10.0,
            volume=0.0,
            amount=0.0,
        ),
        _source_row(
            "2025-01-31",
            timestamp=_wall_epoch("2025-01-31 15:01:00"),
            open=10.0,
            close=10.0,
            high=10.0,
            low=10.0,
            volume=0.0,
            amount=0.0,
        ),
    ],
)
def test_other_off_session_rows_remain_fatal(
    tmp_path: Path,
    off_session_row: dict[str, object],
) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02"), off_session_row])

    with pytest.raises(AnnualMinbarValidationError, match="off_session_rows"):
        build_guan_annual_minbar(_options(tmp_path, source, 2025))

    manifest = json.loads((tmp_path / "annual-manifest.json").read_text(encoding="utf-8"))
    stats = manifest["years"]["2025"]["source_stats"]
    assert stats["dropped_off_session_zero_flow_rows"] == 0
    assert stats["off_session_rows"] == 1


def test_new_attempt_preserves_previous_failure_evidence(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(
        source,
        [
            _source_row("2025-01-02"),
            _source_row(
                "2025-01-31",
                timestamp=_wall_epoch("2025-01-31 12:00:00"),
            ),
        ],
    )
    options = _options(tmp_path, source, 2025)

    with pytest.raises(AnnualMinbarValidationError, match="off_session_rows"):
        build_guan_annual_minbar(options)
    failed_manifest = json.loads((tmp_path / "annual-manifest.json").read_text(encoding="utf-8"))
    failed_entry = failed_manifest["years"]["2025"]

    _write_source(source, [_source_row("2025-01-02")])
    build_guan_annual_minbar(options)

    completed_manifest = json.loads((tmp_path / "annual-manifest.json").read_text(encoding="utf-8"))
    entry = completed_manifest["years"]["2025"]
    assert entry["status"] == "complete"
    assert len(entry["previous_attempts"]) == 1
    previous = entry["previous_attempts"][0]
    assert previous == {
        "status": "source_validation_failed",
        "source_stats": failed_entry["source_stats"],
        "fatal_issues": failed_entry["fatal_issues"],
        "started_at": failed_entry["started_at"],
        "failed_at": failed_entry["failed_at"],
        "error": None,
    }


def test_nonempty_unmapped_ticker_is_fatal(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(
        source,
        [_source_row("2025-01-02"), _source_row("2025-01-02", ticker="510300")],
    )

    with pytest.raises(AnnualMinbarValidationError, match="unmapped_ticker_rows"):
        build_guan_annual_minbar(_options(tmp_path, source, 2025))

    manifest = json.loads((tmp_path / "annual-manifest.json").read_text(encoding="utf-8"))
    entry = manifest["years"]["2025"]
    assert entry["source_stats"]["unmapped_ticker_rows"] == 1
    assert entry["fatal_issues"]["unmapped_ticker_rows"] == 1
    assert not _part(tmp_path / "output", "2025-01-02").exists()


@pytest.mark.parametrize(
    ("overrides", "issue"),
    [
        ({"open": None}, "partial_null_rows"),
        ({"open": float("nan")}, "partial_null_rows"),
        ({"amount": float("inf")}, "non_finite_rows"),
        ({"volume": -1.0}, "negative_flow_rows"),
        ({"timestamp": _wall_epoch("2025-01-02 12:00:00")}, "off_session_rows"),
        ({"timestamp": _wall_epoch("2024-12-31 15:00:00")}, "wrong_year_rows"),
    ],
)
def test_source_validation_failure_preserves_existing_partition(
    tmp_path: Path,
    overrides: dict[str, object],
    issue: str,
) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02", **overrides)])
    existing = _part(tmp_path / "output", "2025-01-02")
    _write_canonical(existing, [_canonical_row("2025-01-02", close=77.0, high=77.0)])

    with pytest.raises(AnnualMinbarValidationError, match=issue):
        build_guan_annual_minbar(_options(tmp_path, source, 2025))

    assert _read_part(existing).loc[0, "close"] == pytest.approx(77.0)


def test_duplicate_key_fails_full_staging_before_promotion(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02"), _source_row("2025-01-02")])
    existing = _part(tmp_path / "output", "2025-01-02")
    _write_canonical(existing, [_canonical_row("2025-01-02", close=77.0, high=77.0)])

    with pytest.raises(AnnualMinbarValidationError, match="staging failed validation"):
        build_guan_annual_minbar(_options(tmp_path, source, 2025))

    assert _read_part(existing).loc[0, "close"] == pytest.approx(77.0)
    manifest = json.loads((tmp_path / "annual-manifest.json").read_text(encoding="utf-8"))
    entry = manifest["years"]["2025"]
    assert entry["status"] == "staging_validation_failed"
    assert entry["source_full_scans"] == 1
    assert entry["source_stats"]["candidate_rows"] == 2
    assert entry["error"].startswith("AnnualMinbarValidationError:")


def test_missing_or_invalid_preserves_valid_and_replaces_invalid(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(
        source,
        [
            _source_row("2025-01-02", close=11.0, high=11.0),
            _source_row("2025-01-03", close=12.0, high=12.0),
        ],
    )
    valid = _part(tmp_path / "output", "2025-01-02")
    invalid = _part(tmp_path / "output", "2025-01-03")
    _write_canonical(valid, [_canonical_row("2025-01-02", close=77.0, high=77.0)])
    _write_canonical(invalid, [_canonical_row("2025-01-03", open=float("nan"))])

    result = build_guan_annual_minbar(_options(tmp_path, source, 2025))

    assert result["preserved_dates"] == ["20250102"]
    assert result["replaced_invalid_dates"] == ["20250103"]
    assert _read_part(valid).loc[0, "close"] == pytest.approx(77.0)
    assert _read_part(invalid).loc[0, "close"] == pytest.approx(12.0)


def test_resume_uses_manifest_without_rescanning_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02")])
    options = _options(tmp_path, source, 2025)
    first = build_guan_annual_minbar(options)

    def unexpected_scan(
        connection: Any,
        *,
        source: Path,
        raw_root: Path,
        year: int,
        profile: Any,
    ) -> None:
        raise AssertionError("completed resume must not scan the annual source")

    monkeypatch.setattr(annual, "_copy_source_to_raw_staging", unexpected_scan)
    second = build_guan_annual_minbar(options)

    assert first["status"] == "complete"
    assert second["status"] == "skipped_complete"
    assert second["source_full_scans"] == 0
    manifest = json.loads((tmp_path / "annual-manifest.json").read_text(encoding="utf-8"))
    assert (
        manifest["years"]["2025"]["transform_contract_version"]
        == annual.ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION
    )


def test_old_complete_transform_contract_forces_rescan_and_full_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02", close=11.0, high=11.0)])
    options = _options(tmp_path, source, 2025)
    build_guan_annual_minbar(options)
    output = _part(tmp_path / "output", "2025-01-02")
    _write_canonical(output, [_canonical_row("2025-01-02", close=77.0, high=77.0)])

    manifest_path = tmp_path / "annual-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("transform_contract_version", None)
    manifest["years"]["2025"].pop("transform_contract_version", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    scans = 0
    original_copy = annual._copy_source_to_raw_staging

    def counted_copy(
        connection: Any,
        *,
        source: Path,
        raw_root: Path,
        year: int,
        profile: Any,
    ) -> None:
        nonlocal scans
        scans += 1
        original_copy(connection, source=source, raw_root=raw_root, year=year, profile=profile)

    monkeypatch.setattr(annual, "_copy_source_to_raw_staging", counted_copy)
    rebuilt = build_guan_annual_minbar(options)

    assert scans == 1
    assert rebuilt["source_full_scans"] == 1
    assert rebuilt["promoted_dates"] == ["20250102"]
    assert rebuilt["preserved_dates"] == []
    assert _read_part(output).loc[0, "close"] == pytest.approx(11.0)
    updated = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = updated["years"]["2025"]
    assert entry["transform_contract_changed"] is True
    assert entry["transform_contract_version"] == annual.ANNUAL_MINBAR_TRANSFORM_CONTRACT_VERSION


def test_old_transform_contract_staging_is_not_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02")])
    options = _options(tmp_path, source, 2025, keep_staging=True)
    build_guan_annual_minbar(options)
    manifest_path = tmp_path / "annual-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["years"]["2025"]
    entry["status"] = "promotion_failed"
    entry["transform_contract_version"] = "old-transform-contract"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    scans = 0
    original_copy = annual._copy_source_to_raw_staging

    def counted_copy(
        connection: Any,
        *,
        source: Path,
        raw_root: Path,
        year: int,
        profile: Any,
    ) -> None:
        nonlocal scans
        scans += 1
        original_copy(connection, source=source, raw_root=raw_root, year=year, profile=profile)

    monkeypatch.setattr(annual, "_copy_source_to_raw_staging", counted_copy)
    rebuilt = build_guan_annual_minbar(options)

    assert scans == 1
    assert rebuilt["source_full_scans"] == 1
    assert rebuilt["promoted_dates"] == ["20250102"]


def test_changed_source_fingerprint_replaces_all_valid_existing_dates(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02", close=11.0, high=11.0)])
    options = _options(tmp_path, source, 2025)
    first = build_guan_annual_minbar(options)
    output = _part(tmp_path / "output", "2025-01-02")

    _write_source(source, [_source_row("2025-01-02", close=12.0, high=12.0)])
    second = build_guan_annual_minbar(options)

    assert first["source_fingerprint"]["id"] != second["source_fingerprint"]["id"]
    assert second["promoted_dates"] == ["20250102"]
    assert second["preserved_dates"] == []
    assert _read_part(output).loc[0, "close"] == pytest.approx(12.0)
    manifest = json.loads((tmp_path / "annual-manifest.json").read_text(encoding="utf-8"))
    assert manifest["years"]["2025"]["source_fingerprint_changed"] is True


def test_changed_source_replacement_survives_mid_promotion_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(
        source,
        [
            _source_row("2025-01-02", close=11.0, high=11.0),
            _source_row("2025-01-03", close=12.0, high=12.0),
        ],
    )
    options = _options(tmp_path, source, 2025)
    build_guan_annual_minbar(options)
    _write_source(
        source,
        [
            _source_row("2025-01-02", close=21.0, high=21.0),
            _source_row("2025-01-03", close=22.0, high=22.0),
        ],
    )

    original_promote = annual._atomic_promote
    calls = 0

    def fail_second_promotion(staged: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated promotion interruption")
        original_promote(staged, destination)

    monkeypatch.setattr(annual, "_atomic_promote", fail_second_promotion)
    with pytest.raises(OSError, match="simulated promotion interruption"):
        build_guan_annual_minbar(options)
    failed = json.loads((tmp_path / "annual-manifest.json").read_text(encoding="utf-8"))
    assert failed["years"]["2025"]["status"] == "promotion_failed"
    assert failed["years"]["2025"]["source_fingerprint_changed"] is True
    assert _read_part(_part(tmp_path / "output", "2025-01-02")).loc[0, "close"] == pytest.approx(
        21.0
    )
    assert _read_part(_part(tmp_path / "output", "2025-01-03")).loc[0, "close"] == pytest.approx(
        12.0
    )

    monkeypatch.setattr(annual, "_atomic_promote", original_promote)
    resumed = build_guan_annual_minbar(options)

    assert resumed["source_full_scans"] == 0
    assert resumed["promoted_dates"] == ["20250102", "20250103"]
    assert resumed["preserved_dates"] == []
    assert _read_part(_part(tmp_path / "output", "2025-01-02")).loc[0, "close"] == pytest.approx(
        21.0
    )
    assert _read_part(_part(tmp_path / "output", "2025-01-03")).loc[0, "close"] == pytest.approx(
        22.0
    )


def test_force_and_replace_dates_can_override_valid_existing(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(
        source,
        [_source_row("2025-01-02", close=11.0, high=11.0), _source_row("2025-01-03")],
    )
    output = tmp_path / "output"
    for date in ("2025-01-02", "2025-01-03"):
        _write_canonical(_part(output, date), [_canonical_row(date, close=77.0, high=77.0)])

    result = build_guan_annual_minbar(_options(tmp_path, source, 2025, replace_dates=("20250102",)))

    assert result["promoted_dates"] == ["20250102"]
    assert result["preserved_dates"] == ["20250103"]
    assert _read_part(_part(output, "2025-01-02")).loc[0, "close"] == pytest.approx(11.0)
    assert _read_part(_part(output, "2025-01-03")).loc[0, "close"] == pytest.approx(77.0)

    for date in ("2025-01-02", "2025-01-03"):
        _write_canonical(_part(output, date), [_canonical_row(date, close=88.0, high=88.0)])
    forced = build_guan_annual_minbar(_options(tmp_path, source, 2025, force=True))

    assert forced["promoted_dates"] == ["20250102", "20250103"]
    assert forced["preserved_dates"] == []
    assert _read_part(_part(output, "2025-01-02")).loc[0, "close"] == pytest.approx(11.0)
    assert _read_part(_part(output, "2025-01-03")).loc[0, "close"] == pytest.approx(10.1)


def test_dataset_lock_rejects_concurrent_builder(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02")])
    output = tmp_path / "output"
    output.mkdir()
    (output / ".annual-minbar-build.lock").write_text(
        '{"pid": 1, "hostname": "another-host"}', encoding="utf-8"
    )

    with pytest.raises(AnnualMinbarBuildError, match="dataset lock"):
        build_guan_annual_minbar(_options(tmp_path, source, 2025))


def test_stale_legacy_lock_and_killed_uuid_temps_are_recovered(tmp_path: Path) -> None:
    source = tmp_path / "minbar_2025.parquet"
    _write_source(source, [_source_row("2025-01-02")])
    output = tmp_path / "output"
    partition = output / "trade_date=20250102"
    partition.mkdir(parents=True)
    stale = partition / f".part-00000.parquet.{uuid.uuid4().hex}.tmp"
    malformed = partition / ".part-00000.parquet.not-a-uuid.tmp"
    stale.write_bytes(b"killed-promotion")
    malformed.write_bytes(b"foreign")
    manifest = tmp_path / "annual-manifest.json"
    stale_manifest = manifest.parent / f".{manifest.name}.{uuid.uuid4().hex}.tmp"
    stale_manifest.write_text("killed-checkpoint", encoding="utf-8")
    lock_path = output / ".annual-minbar-build.lock"
    lock_path.write_text(
        json.dumps({"pid": 2_000_000_000, "hostname": socket.gethostname()}),
        encoding="utf-8",
    )

    result = build_guan_annual_minbar(_options(tmp_path, source, 2025))

    assert result["status"] == "complete"
    assert not stale.exists()
    assert not stale_manifest.exists()
    assert malformed.read_bytes() == b"foreign"
    assert not lock_path.exists()
