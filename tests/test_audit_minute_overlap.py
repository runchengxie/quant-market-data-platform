from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from market_data_platform.providers.a_share_minute_fusion import (
    write_canonical_minute_partition,
)


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "operations" / "audit_minute_overlap.py"
    spec = importlib.util.spec_from_file_location("audit_minute_overlap_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit_script = _load_script()


def _row(
    date: str,
    symbol: str,
    clock: str,
    close: float,
    vol: float,
) -> dict[str, object]:
    return {
        "ts_code": symbol,
        "trade_time": f"{date[:4]}-{date[4:6]}-{date[6:]} {clock}",
        "open": close,
        "close": close,
        "high": close,
        "low": close,
        "vol": vol,
        "amount": close * vol,
    }


def _write_canonical(root: Path, date: str, rows: list[dict[str, object]]) -> Path:
    path = root / f"trade_date={date}" / "part-00000.parquet"
    write_canonical_minute_partition(pd.DataFrame(rows), path)
    return path


def _write_batch(root: Path, date: str, rows: list[dict[str, object]]) -> Path:
    path = root / f"minute_{date}_batch001.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame["trade_date"] = date
    frame.to_parquet(path, index=False)
    return path


def test_overlap_audit_distinguishes_non_uniform_symbol_shifts_and_is_read_only(
    tmp_path: Path,
) -> None:
    date = "20260706"
    canonical_root = tmp_path / "canonical"
    batch_root = tmp_path / "batches"
    canonical_path = _write_canonical(
        canonical_root,
        date,
        [
            _row(date, "000001.SZ", "09:31:00", 10.0, 100.0),
            _row(date, "000001.SZ", "09:32:00", 11.0, 200.0),
            _row(date, "600000.SH", "09:31:00", 20.0, 300.0),
            _row(date, "600000.SH", "09:32:00", 21.0, 400.0),
        ],
    )
    batch_path = _write_batch(
        batch_root,
        date,
        [
            _row(date, "000001.SZ", "09:30:00", 10.0, 100.0),
            _row(date, "000001.SZ", "09:31:00", 11.0, 200.0),
            _row(date, "600000.SH", "09:31:00", 20.0, 300.0),
            _row(date, "600000.SH", "09:32:00", 21.0, 400.0),
        ],
    )
    before = {
        canonical_path: (canonical_path.read_bytes(), canonical_path.stat().st_mtime_ns),
        batch_path: (batch_path.read_bytes(), batch_path.stat().st_mtime_ns),
    }
    output = tmp_path / "audit" / "overlap.json"

    payload = audit_script.audit_minute_overlap(
        canonical_root,
        batch_root,
        output,
    )

    assert payload["status"] == "passed"
    assert payload["diagnostic_only"] is True
    assert payload["mutation_performed"] is False
    assert "must not be used to rewrite" in payload["interpretation"]["warning"]
    daily = payload["daily"][0]
    assert daily["sources"]["guan"]["time_grid"] == ["09:31:00", "09:32:00"]
    assert daily["sources"]["tushare"]["time_grid"] == [
        "09:30:00",
        "09:31:00",
        "09:32:00",
    ]
    assert daily["best_alignment_by_symbol_close_mae"] == {
        "direct": 1,
        "guan_minus_1m": 1,
        "guan_plus_1m": 0,
        "tie": 0,
        "insufficient": 0,
    }
    assert daily["alignments"]["direct"]["common_keys"] == 3
    assert daily["alignments"]["guan_minus_1m"]["common_keys"] == 3
    assert daily["alignments"]["guan_plus_1m"]["common_keys"] == 1
    assert daily["alignments"]["direct"]["close_error"]["exact_count"] == 2
    assert daily["alignments"]["guan_minus_1m"]["close_error"]["exact_count"] == 2
    assert daily["security_day_ratio_distribution"]["guan_over_tushare_vol"]["median"] == 1.0
    assert payload["summary"]["best_alignment_by_symbol_day_close_mae"] == {
        "direct": 1,
        "guan_minus_1m": 1,
        "guan_plus_1m": 0,
        "tie": 0,
        "insufficient": 0,
    }
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == (
        "a_share.minute_overlap_audit.v1"
    )
    assert not list(output.parent.glob(".*.tmp"))
    for path, (content, mtime_ns) in before.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime_ns


def test_overlap_discovery_filters_range_and_main_writes_only_selected_date(
    tmp_path: Path,
    capsys,
) -> None:
    canonical_root = tmp_path / "canonical"
    batch_root = tmp_path / "batches"
    for date in ("20260706", "20260707"):
        rows = [_row(date, "000001.SZ", "09:31:00", 10.0, 100.0)]
        _write_canonical(canonical_root, date, rows)
        _write_batch(batch_root, date, rows)
    _write_batch(
        batch_root,
        "20260708",
        [_row("20260708", "000001.SZ", "09:31:00", 10.0, 100.0)],
    )

    assert audit_script.discover_overlap_dates(
        canonical_root,
        batch_root,
        start_date="20260707",
        end_date="20260708",
    ) == ["20260707"]

    output = tmp_path / "selected.json"
    result = audit_script.main(
        [
            "--canonical-dir",
            str(canonical_root),
            "--tushare-batch-dir",
            str(batch_root),
            "--output-json",
            str(output),
            "--start-date",
            "20260707",
            "--end-date",
            "20260707",
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["inputs"]["overlap_dates"] == ["20260707"]
    assert payload["summary"]["date_count"] == 1
    assert '"date_count": 1' in capsys.readouterr().out


def test_overlap_audit_excludes_explicit_non_guan_dates(tmp_path: Path) -> None:
    canonical_root = tmp_path / "canonical"
    batch_root = tmp_path / "batches"
    for date in ("20260706", "20260707"):
        rows = [_row(date, "000001.SZ", "09:31:00", 10.0, 100.0)]
        _write_canonical(canonical_root, date, rows)
        _write_batch(batch_root, date, rows)

    output = tmp_path / "audit.json"
    payload = audit_script.audit_minute_overlap(
        canonical_root,
        batch_root,
        output,
        exclude_dates=["20260707"],
    )

    assert payload["inputs"]["excluded_overlap_dates"] == ["20260707"]
    assert payload["inputs"]["overlap_dates"] == ["20260706"]
    assert payload["summary"]["date_count"] == 1


def test_overlap_audit_rejects_exclusion_outside_overlap(tmp_path: Path) -> None:
    canonical_root = tmp_path / "canonical"
    batch_root = tmp_path / "batches"
    date = "20260706"
    rows = [_row(date, "000001.SZ", "09:31:00", 10.0, 100.0)]
    _write_canonical(canonical_root, date, rows)
    _write_batch(batch_root, date, rows)

    with pytest.raises(
        audit_script.MinuteOverlapAuditError,
        match="not present in the in-range source overlap",
    ):
        audit_script.audit_minute_overlap(
            canonical_root,
            batch_root,
            tmp_path / "audit.json",
            exclude_dates=["20260707"],
        )
