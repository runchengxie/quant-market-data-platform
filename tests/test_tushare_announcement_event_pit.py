from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from market_data_platform.cli import build_parser
from market_data_platform.providers.tushare_a_share_fundamentals import (
    AnnouncementEventPitOptions,
    build_announcement_event_pit,
    load_announcement_event_as_of_panel,
    load_announcement_event_fundamental_panel,
    select_announcement_events_as_of,
)


def _raw_asset(tmp_path: Path, value_column: str = "revenue") -> Path:
    root = tmp_path / "raw-income"
    data = root / "data"
    data.mkdir(parents=True)
    part = data / "part.parquet"
    pd.DataFrame(
        [
            {
                "ts_code": "600519.SH",
                "ann_date": "20210401",
                "f_ann_date": "20210402",
                "end_date": "20201231",
                "report_type": "1",
                "update_flag": "0",
                value_column: 100.0,
            },
            {
                "ts_code": "600519.SH",
                "ann_date": "20210801",
                "f_ann_date": "20210802",
                "end_date": "20201231",
                "report_type": "5",
                "update_flag": "1",
                value_column: 95.0,
            },
        ]
    ).to_parquet(part, index=False)
    (root / "manifest.yml").write_text(
        "\n".join(
            [
                "schema_version: tushare.a_share.fundamentals.raw.v2",
                "status: completed",
                "immutable_snapshot: true",
                "source_run_id: test-run",
                "parts:",
                f"- path: {part}",
                "  retrieved_at: '2026-08-02T01:00:00+00:00'",
                "integrity:",
                "  production_eligible: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def test_event_pit_preserves_provider_versions_and_as_of_visibility(tmp_path: Path) -> None:
    raw = _raw_asset(tmp_path)
    output = tmp_path / "event-pit"

    manifest = build_announcement_event_pit(
        AnnouncementEventPitOptions(raw_dir=raw, out_dir=output, dataset="income")
    )

    frame = pd.read_parquet(output / "data" / "part.parquet")
    assert manifest["schema_version"] == "tushare.a_share.fundamentals.announcement_event_pit.v1"
    assert manifest["pit_class"] == "announcement_event_pit"
    assert frame[["report_type", "update_flag"]].to_dict("records") == [
        {"report_type": "1", "update_flag": "0"},
        {"report_type": "5", "update_flag": "1"},
    ]
    assert frame["available_date"].tolist() == ["20210402", "20210802"]
    assert frame["event_id"].is_unique
    assert frame["revision_id"].is_unique

    visible = frame.loc[frame["available_date"] <= "20210430"]
    assert visible["revenue"].tolist() == [100.0]
    assert (
        json.loads((output / "manifest.json").read_text())["semantics"]["complete_revision_history"]
        is False
    )


def test_event_pit_cli_parser_is_explicitly_research_only() -> None:
    args = build_parser().parse_args(
        [
            "tushare",
            "build-a-share-announcement-event-pit",
            "--dataset",
            "income",
            "--raw-dir",
            "raw",
            "--out-dir",
            "out",
            "--value-column",
            "revenue",
            "--report-type",
            "1",
            "--report-type",
            "5",
        ]
    )
    assert args.tushare_command == "build-a-share-announcement-event-pit"
    assert args.value_columns == ["revenue"]
    assert args.report_types == ["1", "5"]


def test_as_of_panel_applies_explicit_report_type_policy_and_latest_visible_revision() -> None:
    frame = pd.DataFrame(
        [
            {
                "symbol": "600519",
                "report_period": "20201231",
                "available_date": "20210402",
                "report_type": "1",
                "update_flag": "0",
                "revenue": 100.0,
            },
            {
                "symbol": "600519",
                "report_period": "20201231",
                "available_date": "20210802",
                "report_type": "1",
                "update_flag": "1",
                "revenue": 98.0,
            },
            {
                "symbol": "600519",
                "report_period": "20201231",
                "available_date": "20210802",
                "report_type": "5",
                "update_flag": "1",
                "revenue": 95.0,
            },
        ]
    )

    early = select_announcement_events_as_of(
        frame, as_of_date="20210430", report_type_policy="standard"
    )
    assert early[["report_type", "revenue"]].to_dict("records") == [
        {"report_type": "1", "revenue": 100.0}
    ]

    late = select_announcement_events_as_of(
        frame, as_of_date="20210901", report_type_policy="standard"
    )
    assert late[["report_type", "revenue"]].to_dict("records") == [
        {"report_type": "1", "revenue": 98.0}
    ]

    diagnostic = select_announcement_events_as_of(
        frame, as_of_date="20210901", report_type_policy="diagnostic_including_type5"
    )
    assert diagnostic[["report_type", "revenue"]].to_dict("records") == [
        {"report_type": "1", "revenue": 98.0},
        {"report_type": "5", "revenue": 95.0},
    ]


def test_as_of_panel_loader_returns_audit_and_rejects_unknown_policy(tmp_path: Path) -> None:
    raw = _raw_asset(tmp_path)
    output = tmp_path / "event-pit"
    build_announcement_event_pit(
        AnnouncementEventPitOptions(raw_dir=raw, out_dir=output, dataset="income")
    )

    panel, audit = load_announcement_event_as_of_panel(
        output, as_of_date="20210430", report_type_policy="standard"
    )
    assert panel["revenue"].tolist() == [100.0]
    assert audit == {
        "as_of_date": "20210430",
        "report_type_policy": "standard",
        "visible_event_rows": 1,
        "panel_rows": 1,
        "selected_revision_rows": 1,
    }

    try:
        select_announcement_events_as_of(
            frame=pd.DataFrame(), as_of_date="20210430", report_type_policy="unknown"
        )
    except ValueError as exc:
        assert "Unknown report_type policy" in str(exc)
    else:
        raise AssertionError("unknown report_type policy should fail")


def test_dataset_native_policy_is_required_when_report_type_is_unavailable() -> None:
    frame = pd.DataFrame(
        [{"symbol": "600519", "report_period": "20201231", "available_date": "20210402"}]
    )
    try:
        select_announcement_events_as_of(frame, as_of_date="20210430")
    except ValueError as exc:
        assert "dataset_native" in str(exc)
    else:
        raise AssertionError("missing report_type should require an explicit policy")

    selected = select_announcement_events_as_of(
        frame, as_of_date="20210430", report_type_policy="dataset_native"
    )
    assert len(selected) == 1


def test_fundamental_panel_joins_components_and_uses_latest_component_visibility(
    tmp_path: Path,
) -> None:
    assets: dict[str, Path] = {}
    columns = {
        "income": "revenue",
        "balancesheet": "total_assets",
        "cashflow": "n_cashflow_act",
        "fina_indicator": "roa",
    }
    for dataset, value_column in columns.items():
        raw = _raw_asset(tmp_path / dataset, value_column)
        output = tmp_path / f"event-{dataset}"
        build_announcement_event_pit(
            AnnouncementEventPitOptions(
                raw_dir=raw,
                out_dir=output,
                dataset=dataset,
                value_columns=(value_column,),
            )
        )
        assets[dataset] = output

    panel, audit = load_announcement_event_fundamental_panel(
        assets,
        as_of_date="20210430",
        value_columns=columns,
    )
    assert panel[["symbol", "report_period", *columns.values()]].to_dict("records") == [
        {
            "symbol": "600519.SH",
            "report_period": "20201231",
            "revenue": 100.0,
            "total_assets": 100.0,
            "n_cashflow_act": 100.0,
            "roa": 100.0,
        }
    ]
    assert audit["component_count"] == 4
    assert audit["panel_rows"] == 1
