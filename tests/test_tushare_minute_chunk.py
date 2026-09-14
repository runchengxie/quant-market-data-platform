from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from market_data_platform import tushare_minute_chunk as chunk
from market_data_platform.cli import build_parser
from market_data_platform.dataset_lock import DatasetLockError, exclusive_file_lock
from market_data_platform.providers.tushare_a_share_mins import MinsMirrorOptions

DATES = ["20240102", "20240103", "20240104", "20240105"]


def _write_plan(tmp_path: Path, dates: list[str] | None = None) -> Path:
    path = tmp_path / "full-day-plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "a_share.minute_tushare_full_day_plan.v1",
                "phase": "production",
                "dates": dates or DATES,
            }
        ),
        encoding="utf-8",
    )
    return path


def _source_receipt(part_dir: Path, trade_date: str) -> dict[str, Any]:
    return {
        "schema_version": "tushare.a_share.minute_partition.promotion.v1",
        "trade_date": trade_date,
        "partition_path": str(part_dir / "part-00000.parquet"),
        "sidecar_path": str(part_dir / "_minute_mirror.json"),
        "partition_sha256": "a" * 64,
        "sidecar_sha256": "b" * 64,
        "rows": 241,
        "symbols": 1,
        "market_symbol_counts": {"SH": 0, "SZ": 1, "BJ": 0},
        "expected_bars_per_symbol": 241,
        "universe_hash": "c" * 64,
        "universe_rule": "full-daily-universe-test",
        "mirror_generated_at": "2026-07-11T00:00:00Z",
    }


def _install_partition_inspector(
    monkeypatch: pytest.MonkeyPatch,
    *,
    completed: set[str],
) -> None:
    def validate(
        partition_dir: str | Path,
        *,
        trade_date: str,
        require_full_universe: bool,
    ) -> dict[str, Any]:
        assert require_full_universe is True
        if trade_date not in completed:
            raise ValueError(f"partial {trade_date}")
        return _source_receipt(Path(partition_dir), trade_date)

    monkeypatch.setattr(chunk, "validate_complete_minute_partition", validate)


def _prepare_source_root(tmp_path: Path) -> Path:
    source_root = tmp_path / "full-day"
    source_root.mkdir()
    for trade_date in DATES:
        (source_root / f"trade_date={trade_date}").mkdir()
    return source_root


def test_dry_run_is_read_only_and_selects_first_pending_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path)
    source_root = _prepare_source_root(tmp_path)
    receipt_dir = tmp_path / "receipts"
    _install_partition_inspector(monkeypatch, completed={DATES[0]})
    monkeypatch.setattr(
        chunk,
        "_load_tushare_env_files",
        lambda: pytest.fail("dry-run loaded environment files"),
    )
    monkeypatch.setattr(
        chunk,
        "mirror_minute_bars",
        lambda _options: pytest.fail("dry-run called TuShare mirror"),
    )

    result = chunk.run_minute_full_day_chunk(
        chunk.MinuteFullDayChunkOptions(
            plan_path=plan,
            source_root=source_root,
            receipt_dir=receipt_dir,
            max_dates=2,
            dry_run=True,
        )
    )

    assert result["status"] == "dry_run"
    assert result["selection"]["completed_before_dates"] == [DATES[0]]
    assert result["selection"]["selected_dates"] == DATES[1:3]
    assert not receipt_dir.exists()
    assert not (source_root / ".tushare-minute-full-day-chunk.lock").exists()


def test_actual_chunk_is_serial_full_universe_and_writes_unique_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path)
    source_root = _prepare_source_root(tmp_path)
    receipt_dir = tmp_path / "receipts"
    completed = {DATES[0]}
    _install_partition_inspector(monkeypatch, completed=completed)
    monkeypatch.setattr(chunk, "_load_tushare_env_files", lambda: None)
    monkeypatch.setenv("TEST_TUSHARE_TOKEN", "secret-token-value")
    captured: list[MinsMirrorOptions] = []

    def mirror(options: MinsMirrorOptions) -> dict[str, Any]:
        captured.append(options)
        completed.update(options.trading_dates or [])
        return {
            "dates_fetched": len(options.trading_dates or []),
            "dates_skipped": 0,
            "requests_made": 10,
            "total_bars": 482,
            "output_dir": str(source_root),
        }

    monkeypatch.setattr(chunk, "mirror_minute_bars", mirror)
    result = chunk.run_minute_full_day_chunk(
        chunk.MinuteFullDayChunkOptions(
            plan_path=plan,
            source_root=source_root,
            receipt_dir=receipt_dir,
            max_dates=2,
            token_env="TEST_TUSHARE_TOKEN",
            api_url="https://user:password@example.test/mins",
        )
    )

    assert result["status"] == "complete"
    assert result["selection"]["selected_dates"] == DATES[1:3]
    assert result["summary"]["completed_selected_dates"] == 2
    assert result["summary"]["pending_plan_date_values"] == [DATES[3]]
    assert len(captured) == 1
    assert captured[0].trading_dates == DATES[1:3]
    assert captured[0].exchange is None
    assert captured[0].batch_size == 20
    assert captured[0].cooldown_seconds == 1.0
    receipt_files = list(receipt_dir.glob("minute_full_day_chunk_*.json"))
    assert len(receipt_files) == 1
    receipt_text = receipt_files[0].read_text(encoding="utf-8")
    assert "secret-token-value" not in receipt_text
    assert "password" not in receipt_text
    assert json.loads(receipt_text)["parameters"]["endpoint_id"] == ("https://example.test/mins")


def test_failed_chunk_receipt_allows_next_invocation_to_resume_by_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, DATES[:3])
    source_root = _prepare_source_root(tmp_path)
    receipt_dir = tmp_path / "receipts"
    completed: set[str] = set()
    _install_partition_inspector(monkeypatch, completed=completed)
    monkeypatch.setattr(chunk, "_load_tushare_env_files", lambda: None)
    monkeypatch.setenv("TEST_TUSHARE_TOKEN", "resume-secret")

    def interrupted(options: MinsMirrorOptions) -> dict[str, Any]:
        assert options.trading_dates is not None
        completed.add(options.trading_dates[0])
        raise RuntimeError("provider failed with resume-secret")

    monkeypatch.setattr(chunk, "mirror_minute_bars", interrupted)
    first = chunk.run_minute_full_day_chunk(
        chunk.MinuteFullDayChunkOptions(
            plan_path=plan,
            source_root=source_root,
            receipt_dir=receipt_dir,
            max_dates=2,
            token_env="TEST_TUSHARE_TOKEN",
        )
    )

    assert first["status"] == "partial"
    assert first["dates"][DATES[0]]["status"] == "complete"
    assert first["dates"][DATES[1]]["status"] == "pending"
    assert "resume-secret" not in Path(first["receipt_path"]).read_text(encoding="utf-8")

    captured: list[list[str]] = []

    def resumed(options: MinsMirrorOptions) -> dict[str, Any]:
        selected = list(options.trading_dates or [])
        captured.append(selected)
        completed.update(selected)
        return {
            "dates_fetched": len(selected),
            "dates_skipped": 0,
            "requests_made": 2,
            "total_bars": 482,
            "output_dir": str(source_root),
        }

    monkeypatch.setattr(chunk, "mirror_minute_bars", resumed)
    second = chunk.run_minute_full_day_chunk(
        chunk.MinuteFullDayChunkOptions(
            plan_path=plan,
            source_root=source_root,
            receipt_dir=receipt_dir,
            max_dates=2,
            token_env="TEST_TUSHARE_TOKEN",
        )
    )

    assert captured == [DATES[1:3]]
    assert second["status"] == "complete"
    assert second["summary"]["plan_complete"] is True
    assert len(list(receipt_dir.glob("minute_full_day_chunk_*.json"))) == 2


def test_missing_token_is_a_structured_failed_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, DATES[:1])
    source_root = _prepare_source_root(tmp_path)
    receipt_dir = tmp_path / "receipts"
    _install_partition_inspector(monkeypatch, completed=set())
    monkeypatch.setattr(chunk, "_load_tushare_env_files", lambda: None)
    monkeypatch.delenv("ABSENT_TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        chunk,
        "mirror_minute_bars",
        lambda _options: pytest.fail("mirror called without a token"),
    )

    result = chunk.run_minute_full_day_chunk(
        chunk.MinuteFullDayChunkOptions(
            plan_path=plan,
            source_root=source_root,
            receipt_dir=receipt_dir,
            token_env="ABSENT_TUSHARE_TOKEN",
        )
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "RuntimeError"
    assert Path(result["receipt_path"]).is_file()


def test_complete_plan_is_a_noop_without_token_or_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, DATES[:2])
    source_root = _prepare_source_root(tmp_path)
    receipt_dir = tmp_path / "receipts"
    _install_partition_inspector(monkeypatch, completed=set(DATES[:2]))
    monkeypatch.setattr(
        chunk,
        "_load_tushare_env_files",
        lambda: pytest.fail("complete plan loaded environment files"),
    )

    result = chunk.run_minute_full_day_chunk(
        chunk.MinuteFullDayChunkOptions(
            plan_path=plan,
            source_root=source_root,
            receipt_dir=receipt_dir,
        )
    )

    assert result["status"] == "noop"
    assert result["summary"]["plan_complete"] is True
    assert "receipt_path" not in result
    assert not receipt_dir.exists()


def test_source_root_lock_rejects_an_overlapping_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, DATES[:1])
    source_root = _prepare_source_root(tmp_path)
    receipt_dir = tmp_path / "receipts"
    _install_partition_inspector(monkeypatch, completed=set())
    lock_path = source_root / ".tushare-minute-full-day-chunk.lock"

    with exclusive_file_lock(lock_path, operation="test-owner"):
        with pytest.raises(DatasetLockError, match="Dataset lock is held"):
            chunk.run_minute_full_day_chunk(
                chunk.MinuteFullDayChunkOptions(
                    plan_path=plan,
                    source_root=source_root,
                    receipt_dir=receipt_dir,
                )
            )

    assert not receipt_dir.exists()


def test_cli_defaults_to_three_dates_and_has_no_parallel_flag(tmp_path: Path) -> None:
    parser = build_parser()
    parsed = parser.parse_args(
        [
            "tushare",
            "run-a-share-minute-full-day-chunk",
            "--plan",
            str(tmp_path / "plan.json"),
            "--full-day-dir",
            str(tmp_path / "full"),
            "--receipt-dir",
            str(tmp_path / "receipts"),
        ]
    )

    assert parsed.max_dates == 3
    assert parsed.batch_size == 20
    assert parsed.cooldown_seconds == 1.0
    assert not hasattr(parsed, "workers")


@pytest.mark.parametrize("max_dates", [0, 6])
def test_chunk_rejects_unbounded_date_counts(tmp_path: Path, max_dates: int) -> None:
    plan = _write_plan(tmp_path)
    source_root = _prepare_source_root(tmp_path)

    with pytest.raises(ValueError, match="max_dates must be between 1 and 5"):
        chunk.run_minute_full_day_chunk(
            chunk.MinuteFullDayChunkOptions(
                plan_path=plan,
                source_root=source_root,
                receipt_dir=tmp_path / "receipts",
                max_dates=max_dates,
                dry_run=True,
            )
        )


def test_chunk_batch_size_accepts_33_and_rejects_34(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path)
    source_root = _prepare_source_root(tmp_path)
    options = chunk.MinuteFullDayChunkOptions(
        plan_path=plan,
        source_root=source_root,
        receipt_dir=tmp_path / "receipts",
        batch_size=33,
        dry_run=True,
    )

    result = chunk.run_minute_full_day_chunk(options)

    assert result["parameters"]["batch_size"] == 33

    with pytest.raises(ValueError, match="batch_size must be between 1 and 33 for 1min data"):
        chunk.run_minute_full_day_chunk(
            chunk.MinuteFullDayChunkOptions(
                plan_path=plan,
                source_root=source_root,
                receipt_dir=tmp_path / "receipts",
                batch_size=34,
                dry_run=True,
            )
        )
