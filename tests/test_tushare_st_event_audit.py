"""Date-only ST event audit behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from quant_market_data_platform.cli import main
from quant_market_data_platform.providers.tushare_constraint_io import sha256
from quant_market_data_platform.providers.tushare_st_event_audit import audit_st_event_timing


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    history = tmp_path / "history.parquet"
    events = tmp_path / "st.parquet"
    pd.DataFrame(
        [
            {"ts_code": code, "trade_date": "20240105", "ann_date": "20240105", "name": "*ST样例"}
            for code in ("000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ")
        ]
    ).to_parquet(history, index=False)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "name": "*ST旧名",
                "pub_date": "20240102",
                "imp_date": "20240103",
                "st_type": "*ST",
            },
            {
                "ts_code": "000002.SZ",
                "name": "*ST样例",
                "pub_date": "20240105",
                "imp_date": "20240105",
                "st_type": "*ST",
            },
            {
                "ts_code": "000003.SZ",
                "name": "*ST样例",
                "pub_date": "20240106",
                "imp_date": "20240108",
                "st_type": "*ST",
            },
        ]
    ).to_parquet(events, index=False)
    events.with_suffix(".receipt.json").write_text(
        json.dumps({"dataset": "st", "sha256": sha256(events)}), encoding="utf-8"
    )
    return history, events


def test_audit_classifies_all_date_evidence(tmp_path: Path) -> None:
    history, events = _sources(tmp_path)
    summary = audit_st_event_timing(history, events, tmp_path / "out")

    assert summary["status_counts"] == {
        "prior_dated_st_event": 1,
        "same_day_time_unknown": 1,
        "later_event_date_conflict": 1,
        "no_active_prior_event": 1,
    }
    assert summary["revision_safe"] is False
    assert sha256(Path(summary["path"])) == summary["audit_sha256"]


def test_audit_rejects_modified_event_source(tmp_path: Path) -> None:
    history, events = _sources(tmp_path)
    pd.DataFrame([{"ts_code": "000001.SZ"}]).to_parquet(events, index=False)

    with pytest.raises(ValueError, match="hash mismatch"):
        audit_st_event_timing(history, events, tmp_path / "out")


def test_st_event_audit_cli_returns_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history, events = _sources(tmp_path)

    assert (
        main(
            [
                "tushare",
                "audit-a-share-st-event-timing",
                "--st-history",
                str(history),
                "--st-events",
                str(events),
                "--out-dir",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["rows"] == 4
