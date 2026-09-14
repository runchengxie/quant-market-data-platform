from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from market_data_platform import tushare_minute_operational_daily
from market_data_platform._tushare_minute_campaign_readiness import file_sha256
from market_data_platform.minute_candidate import MinuteCandidateError
from market_data_platform.tushare_minute_operational import (
    OperationalAssembly,
    assemble_operational_version,
    promote_operational_alias,
)
from market_data_platform.tushare_minute_operational_daily import (
    OperationalDailyOptions,
    run_operational_daily,
)
from scripts.operations import tushare_minute_operational as operational_cli


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _candidate_receipt(tmp_path: Path) -> Path:
    output_dir = tmp_path / "candidate"
    partition_dir = output_dir / "trade_date=20260708"
    partition_dir.mkdir(parents=True)
    partition = partition_dir / "part-00000.parquet"
    partition.write_bytes(b"base-candidate")
    receipt = {
        "schema_version": "a_share.minute_tushare_candidate.v1",
        "status": "published_candidate",
        "quality_status": "review_required",
        "output_dir": str(output_dir),
        "current_alias_mutated": False,
        "daily": [
            {
                "trade_date": "20260708",
                "relative_path": "trade_date=20260708/part-00000.parquet",
                "rows": 241,
                "symbols": 1,
                "content_sha256": file_sha256(partition),
            }
        ],
    }
    receipt_path = tmp_path / "candidate.json"
    _write_json(receipt_path, cast(dict[str, object], receipt))
    return receipt_path


def _incremental_partition(root: Path, trade_date: str) -> Path:
    partition_dir = root / f"trade_date={trade_date}"
    partition_dir.mkdir(parents=True)
    partition = partition_dir / "part-00000.parquet"
    partition.write_bytes(f"incremental-{trade_date}".encode())
    symbols = ["000001.SZ", "920001.BJ"]
    _write_json(
        partition_dir / "_minute_mirror.json",
        {
            "schema_version": "tushare.a_share.minute_partition.v3",
            "status": "complete",
            "trade_date": trade_date,
            "expected_bars_per_symbol": 241,
            "expected_symbols": symbols,
            "completed_symbols": symbols,
            "missing_request_symbols": [],
            "partition": {
                "rows": 482,
                "schema_valid": True,
                "key_unique": True,
                "trade_date_valid": True,
                "files": [
                    {
                        "name": partition.name,
                        "rows": 482,
                        "sha256": file_sha256(partition),
                    }
                ],
            },
        },
    )
    return partition


def _trade_calendar(path: Path, dates: list[str]) -> None:
    pq.write_table(
        pa.table(
            {
                "cal_date": dates,
                "is_open": [1 for _ in dates],
            }
        ),
        path,
    )


def test_assemble_and_promote_tushare_native_version_without_mutating_guan(
    tmp_path: Path,
) -> None:
    candidate_receipt = _candidate_receipt(tmp_path)
    root_a = tmp_path / "full-v1"
    root_b = tmp_path / "catchup"
    first_increment = _incremental_partition(root_a, "20260709")
    _incremental_partition(root_b, "20260710")
    calendar = tmp_path / "trade-cal.parquet"
    _trade_calendar(calendar, ["20260708", "20260709", "20260710"])
    version = tmp_path / "assets" / "minute_1m_tushare_v1_20260710"
    version_receipt = tmp_path / "metadata" / "operational-version.json"

    payload = assemble_operational_version(
        OperationalAssembly(
            base_receipt=candidate_receipt,
            incremental_roots=(root_a, root_b),
            trade_calendar=calendar,
            end_date="20260710",
            output_dir=version,
            receipt_json=version_receipt,
        )
    )

    assert payload["status"] == "published_operational_version"
    assert payload["quality_status"] == "tushare_native_baseline_required"
    assert payload["summary"] == {
        "dates": 3,
        "date_min": "20260708",
        "date_max": "20260710",
        "rows": 1205,
        "symbol_days": 5,
        "market_scope": "SH_SZ_BJ",
    }
    assert payload["policy"]["cross_source_equivalence_required"] is False
    published_increment = version / "trade_date=20260709" / "part-00000.parquet"
    assert published_increment.stat().st_ino == first_increment.stat().st_ino
    assert (version / "_operational_receipt.json").read_bytes() == version_receipt.read_bytes()

    legacy_version = tmp_path / "assets" / "minute_1m_v3"
    legacy_version.mkdir()
    legacy_alias = tmp_path / "assets" / "minute_1m"
    legacy_alias.symlink_to(legacy_version.name, target_is_directory=True)
    operational_alias = tmp_path / "assets" / "minute_1m_tushare"
    promotion_receipt = tmp_path / "metadata" / "promotion.json"

    promotion = promote_operational_alias(
        version_receipt,
        operational_alias,
        legacy_alias,
        promotion_receipt,
    )

    assert operational_alias.is_dir()
    assert not operational_alias.is_symlink()
    assert (operational_alias / "_operational_receipt.json").is_file()
    assert legacy_alias.resolve() == legacy_version
    assert promotion["status"] == "operational_canonical"
    assert promotion["legacy_canonical"]["mutated"] is False

    root_c = tmp_path / "next-day"
    _incremental_partition(root_c, "20260711")
    _trade_calendar(calendar, ["20260708", "20260709", "20260710", "20260711"])
    successor = tmp_path / "assets" / "minute_1m_tushare_v1_20260711"
    successor_receipt = tmp_path / "metadata" / "operational-successor.json"
    successor_payload = assemble_operational_version(
        OperationalAssembly(
            base_receipt=version_receipt,
            incremental_roots=(root_c,),
            trade_calendar=calendar,
            end_date="20260711",
            output_dir=successor,
            receipt_json=successor_receipt,
        )
    )
    assert successor_payload["summary"]["dates"] == 4
    assert successor_payload["daily"][0]["source_kind"] == "prior_operational_version"


def test_assemble_rejects_missing_expected_incremental_date(tmp_path: Path) -> None:
    candidate_receipt = _candidate_receipt(tmp_path)
    incremental = tmp_path / "incremental"
    _incremental_partition(incremental, "20260709")
    calendar = tmp_path / "trade-cal.parquet"
    _trade_calendar(calendar, ["20260708", "20260709", "20260710"])

    with pytest.raises(MinuteCandidateError, match="exactly one complete source"):
        assemble_operational_version(
            OperationalAssembly(
                base_receipt=candidate_receipt,
                incremental_roots=(incremental,),
                trade_calendar=calendar,
                end_date="20260710",
                output_dir=tmp_path / "version",
                receipt_json=tmp_path / "receipt.json",
            )
        )


def test_assemble_can_add_missing_historical_dates_without_replacing_base(
    tmp_path: Path,
) -> None:
    candidate_receipt = _candidate_receipt(tmp_path)
    extension = tmp_path / "extension"
    _incremental_partition(extension, "20260710")
    calendar = tmp_path / "trade-cal.parquet"
    _trade_calendar(calendar, ["20260708", "20260710"])
    base_version = tmp_path / "base-version"
    base_receipt = tmp_path / "base-receipt.json"
    assemble_operational_version(
        OperationalAssembly(
            base_receipt=candidate_receipt,
            incremental_roots=(extension,),
            trade_calendar=calendar,
            end_date="20260710",
            output_dir=base_version,
            receipt_json=base_receipt,
        )
    )

    repair = tmp_path / "repair"
    repaired_partition = _incremental_partition(repair, "20260709")
    _trade_calendar(calendar, ["20260708", "20260709", "20260710"])
    repaired_version = tmp_path / "repaired-version"
    repaired_receipt = tmp_path / "repaired-receipt.json"
    payload = assemble_operational_version(
        OperationalAssembly(
            base_receipt=base_receipt,
            incremental_roots=(repair,),
            trade_calendar=calendar,
            end_date="20260710",
            output_dir=repaired_version,
            receipt_json=repaired_receipt,
            repair_dates=("20260709",),
        )
    )

    assert payload["summary"]["dates"] == 3
    assert payload["inputs"]["repair_dates"] == ["20260709"]
    assert [row["trade_date"] for row in payload["daily"]] == [
        "20260708",
        "20260709",
        "20260710",
    ]
    assert (
        repaired_version / "trade_date=20260709" / "part-00000.parquet"
    ).stat().st_ino == repaired_partition.stat().st_ino

    with pytest.raises(MinuteCandidateError, match="replace existing dates"):
        assemble_operational_version(
            OperationalAssembly(
                base_receipt=repaired_receipt,
                incremental_roots=(repair,),
                trade_calendar=calendar,
                end_date="20260710",
                output_dir=tmp_path / "replacement-version",
                receipt_json=tmp_path / "replacement-receipt.json",
                repair_dates=("20260709",),
            )
        )


def test_operational_cli_accepts_repeated_repair_dates(tmp_path: Path) -> None:
    parsed = operational_cli.build_parser().parse_args(
        [
            "assemble",
            "--base-receipt",
            str(tmp_path / "base.json"),
            "--incremental-root",
            str(tmp_path / "incremental"),
            "--trade-calendar",
            str(tmp_path / "calendar.parquet"),
            "--end-date",
            "20260710",
            "--output-dir",
            str(tmp_path / "version"),
            "--receipt-json",
            str(tmp_path / "receipt.json"),
            "--repair-date",
            "20260707",
            "--repair-date",
            "20260709",
        ]
    )

    assert parsed.repair_date == ["20260707", "20260709"]


def test_promote_replaces_entity_operational_alias(tmp_path: Path) -> None:
    candidate_receipt = _candidate_receipt(tmp_path)
    incremental = tmp_path / "incremental"
    _incremental_partition(incremental, "20260709")
    calendar = tmp_path / "trade-cal.parquet"
    _trade_calendar(calendar, ["20260708", "20260709"])
    version = tmp_path / "assets" / "minute_1m_tushare_v1_20260709"
    version_receipt = tmp_path / "version.json"
    assemble_operational_version(
        OperationalAssembly(
            base_receipt=candidate_receipt,
            incremental_roots=(incremental,),
            trade_calendar=calendar,
            end_date="20260709",
            output_dir=version,
            receipt_json=version_receipt,
        )
    )
    legacy_version = tmp_path / "assets" / "legacy"
    legacy_version.mkdir()
    legacy_alias = tmp_path / "assets" / "minute_1m"
    legacy_alias.symlink_to(legacy_version.name, target_is_directory=True)
    operational_alias = tmp_path / "assets" / "minute_1m_tushare"
    operational_alias.mkdir()

    promote_operational_alias(
        version_receipt,
        operational_alias,
        legacy_alias,
        tmp_path / "promotion.json",
    )
    assert operational_alias.is_dir()
    assert not operational_alias.is_symlink()


def _installed_operational_alias(tmp_path: Path, *, date_max: str) -> Path:
    assets = tmp_path / "assets" / "derived" / "a_share"
    version = assets / f"minute_1m_tushare_v1_{date_max}"
    version.mkdir(parents=True)
    _write_json(
        version / "_operational_receipt.json",
        {
            "schema_version": "a_share.minute_tushare_operational_version.v1",
            "status": "published_operational_version",
            "output_dir": str(version),
            "summary": {"date_max": date_max},
        },
    )
    alias = assets / "minute_1m_tushare"
    alias.symlink_to(version.name, target_is_directory=True)
    legacy = assets / "minute_1m_v3"
    legacy.mkdir()
    (assets / "minute_1m").symlink_to(legacy.name, target_is_directory=True)
    return alias


def test_daily_operational_update_is_noop_when_latest_open_date_is_present(
    tmp_path: Path,
) -> None:
    _installed_operational_alias(tmp_path, date_max="20260710")
    calendar = (
        tmp_path
        / "assets"
        / "tushare"
        / "a_share"
        / "trade_cal"
        / "a_share_trade_cal_latest.parquet"
    )
    calendar.parent.mkdir(parents=True)
    _trade_calendar(calendar, ["20260709", "20260710"])

    payload = run_operational_daily(
        OperationalDailyOptions(artifacts_root=tmp_path, target_date="20260710")
    )

    assert payload == {
        "status": "noop",
        "reason": "operational_version_is_current",
        "date_max": "20260710",
    }


def test_daily_operational_update_downloads_assembles_and_promotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias = _installed_operational_alias(tmp_path, date_max="20260710")
    calendar = (
        tmp_path
        / "assets"
        / "tushare"
        / "a_share"
        / "trade_cal"
        / "a_share_trade_cal_latest.parquet"
    )
    calendar.parent.mkdir(parents=True)
    _trade_calendar(calendar, ["20260710", "20260713"])
    plan_data = tmp_path / "download"
    plan_data.mkdir()
    observed: dict[str, object] = {}

    def fake_build(*args: object, **kwargs: object) -> dict[str, object]:
        observed["build_args"] = args
        observed["build_kwargs"] = kwargs
        return {"output": {"data_dir": str(plan_data)}}

    def fake_run(*args: object, **kwargs: object) -> dict[str, object]:
        observed["run_args"] = args
        observed["run_kwargs"] = kwargs
        return {"status": "complete"}

    def fake_assemble(assembly: OperationalAssembly) -> dict[str, object]:
        observed["assembly"] = assembly
        return {"summary": {"dates": 4, "rows": 4096}}

    def fake_promote(*args: object, **kwargs: object) -> dict[str, object]:
        observed["promote_args"] = args
        observed["promote_kwargs"] = kwargs
        return {"legacy_canonical": {"mutated": False}}

    monkeypatch.setattr(tushare_minute_operational_daily, "_build_daily_plan", fake_build)
    monkeypatch.setattr(tushare_minute_operational_daily, "_run_daily_plan", fake_run)
    monkeypatch.setattr(
        tushare_minute_operational_daily,
        "assemble_operational_version",
        fake_assemble,
    )
    monkeypatch.setattr(
        tushare_minute_operational_daily,
        "promote_operational_alias",
        fake_promote,
    )

    payload = run_operational_daily(
        OperationalDailyOptions(artifacts_root=tmp_path, target_date="20260713")
    )

    assert payload["status"] == "operational_canonical_updated"
    assert payload["date_max"] == "20260713"
    assembly = observed["assembly"]
    assert isinstance(assembly, OperationalAssembly)
    assert assembly.incremental_roots == (plan_data,)
    assert alias.resolve().name == "minute_1m_tushare_v1_20260710"


def test_daily_operational_update_uses_fallback_token_after_incomplete_backfill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed_operational_alias(tmp_path, date_max="20260710")
    calendar = (
        tmp_path
        / "assets"
        / "tushare"
        / "a_share"
        / "trade_cal"
        / "a_share_trade_cal_latest.parquet"
    )
    calendar.parent.mkdir(parents=True)
    _trade_calendar(calendar, ["20260710", "20260713"])
    primary_data = tmp_path / "primary-download"
    fallback_data = tmp_path / "fallback-download"
    primary_data.mkdir()
    fallback_data.mkdir()
    observed_tokens: list[str] = []

    def fake_build(
        options: OperationalDailyOptions,
        *args: object,
        **kwargs: object,
    ) -> dict[str, object]:
        observed_tokens.append(options.token_env)
        data_dir = primary_data if options.token_env == "TUSHARE_TOKEN_2" else fallback_data
        return {"output": {"data_dir": str(data_dir)}}

    def fake_run(
        options: OperationalDailyOptions,
        _paths: object,
        _plan_path: Path,
        receipt_path: Path,
    ) -> dict[str, object]:
        if options.token_env == "TUSHARE_TOKEN_2":
            _write_json(
                receipt_path,
                {"status": "partial", "segments": [{"error": {"type": "MinuteQuotaExceeded"}}]},
            )
            return {"status": "partial"}
        return {"status": "complete"}

    monkeypatch.setattr(tushare_minute_operational_daily, "_build_daily_plan", fake_build)
    monkeypatch.setattr(tushare_minute_operational_daily, "_run_daily_plan", fake_run)
    monkeypatch.setattr(
        tushare_minute_operational_daily,
        "assemble_operational_version",
        lambda assembly: {"summary": {"dates": 4, "rows": 4096}},
    )
    monkeypatch.setattr(
        tushare_minute_operational_daily,
        "promote_operational_alias",
        lambda *args: {"legacy_canonical": {"mutated": False}},
    )

    payload = run_operational_daily(
        OperationalDailyOptions(
            artifacts_root=tmp_path,
            target_date="20260713",
            fallback_token_env="TUSHARE_TOKEN",
        )
    )

    assert payload["status"] == "operational_canonical_updated"
    assert observed_tokens == ["TUSHARE_TOKEN_2", "TUSHARE_TOKEN"]
