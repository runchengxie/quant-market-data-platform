from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from market_data_platform.context.build import RELEASE_CALENDAR_COLUMNS, build_context_frames
from market_data_platform.context.snapshots import seal_context_snapshot

_FIXTURE = Path(__file__).parent / "fixtures" / "context" / "nea_electricity_release.html"


def _seal_nbs(root: Path) -> None:
    body = json.dumps(
        {
            "resolved": {
                "cid": "3f2e14f0542348ed9fe02476eca3450b",
                "indic_id": "ef1b1765960d45a29b4d7c4ca91be916",
                "show_name": "规模以上工业增加值同比增长速度",
            },
            "data_response": {
                "data": [
                    {
                        "code": "202607MM",
                        "values": [
                            {
                                "i_showname": "规模以上工业增加值同比增长速度",
                                "value": "5.7",
                            }
                        ],
                    }
                ]
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    seal_context_snapshot(
        root,
        provider="nbs",
        dataset="industrial_value_added_yoy",
        source_locator="https://data.stats.gov.cn/example",
        retrieved_at=datetime(2026, 8, 20, 4, tzinfo=UTC),
        body=body,
        parser_version="nbs-context.v1",
        request_metadata={"period": "202607"},
        content_type="application/json",
    )


def _seal_nea(root: Path) -> None:
    seal_context_snapshot(
        root,
        provider="nea",
        dataset="electricity",
        source_locator="https://www.nea.gov.cn/20260821/example/c.html",
        retrieved_at=datetime(2026, 8, 22, 2, tzinfo=UTC),
        body=_FIXTURE.read_bytes(),
        parser_version="nea-context.v1",
        request_metadata={},
        content_type="text/html;charset=utf-8",
    )


def test_historical_context_build_excludes_raw_snapshots_retrieved_after_as_of(tmp_path: Path):
    _seal_nbs(tmp_path)
    _seal_nea(tmp_path)

    before_nea = build_context_frames(tmp_path, as_of="2026-08-21T23:59:59Z")
    assert set(before_nea.observations["series_id"]) == {"activity.industrial_value_added_yoy"}
    assert {item["provider"] for item in before_nea.lineage} == {"nbs"}
    assert tuple(before_nea.release_calendar.columns) == RELEASE_CALENDAR_COLUMNS

    after_nea = build_context_frames(tmp_path, as_of="2026-08-23T23:59:59Z")
    assert "energy.electricity_consumption_total" in set(after_nea.observations["series_id"])
    assert {item["provider"] for item in after_nea.lineage} == {"nbs", "nea"}
    assert (
        after_nea.pit["source_retrieved_at"] <= after_nea.pit["source_retrieved_at"].max()
    ).all()
