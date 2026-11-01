from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from quant_market_data_platform.context.models import validate_context_observations
from quant_market_data_platform.context.pit import select_context_as_of
from quant_market_data_platform.paths import (
    current_contract_path,
    normalize_market,
    normalize_provider,
)


def _observations() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "series_id": "rates.shibor_3m",
                "period_start": "2026-01-01",
                "period_end": "2026-01-01",
                "value": 1.5,
                "unit": "percent",
                "published_at": "2026-01-02T03:00:00Z",
                "observed_at": "2026-01-02T03:00:00Z",
                "ingested_at": "2026-01-02T04:00:00Z",
                "source_retrieved_at": "2026-01-02T04:00:00Z",
                "available_at": "2026-01-02T03:00:00Z",
                "vintage_id": "v1",
                "revision_number": 0,
                "source_hash": "a" * 64,
                "revision_covered": True,
                "reconstructed": False,
            },
            {
                "series_id": "rates.shibor_3m",
                "period_start": "2026-01-01",
                "period_end": "2026-01-01",
                "value": 1.6,
                "unit": "percent",
                "published_at": "2026-01-20T03:00:00Z",
                "observed_at": "2026-01-20T03:00:00Z",
                "ingested_at": "2026-01-20T04:00:00Z",
                "source_retrieved_at": "2026-01-20T04:00:00Z",
                "available_at": "2026-01-20T03:00:00Z",
                "vintage_id": "v2",
                "revision_number": 1,
                "source_hash": "b" * 64,
                "revision_covered": True,
                "reconstructed": False,
            },
            {
                "series_id": "activity.pmi_manufacturing",
                "period_start": "2026-01-01",
                "period_end": "2026-01-31",
                "value": 50.4,
                "unit": "index",
                "published_at": "2026-02-02T01:30:00Z",
                "observed_at": "2026-02-02T01:30:00Z",
                "ingested_at": "2026-02-02T02:00:00Z",
                "source_retrieved_at": "2026-02-02T02:00:00Z",
                "available_at": "2026-02-02T01:30:00Z",
                "vintage_id": "v1",
                "revision_number": 0,
                "source_hash": "c" * 64,
                "revision_covered": True,
                "reconstructed": False,
            },
        ]
    )


def test_cn_context_contract_domain(tmp_path):
    assert normalize_market("cn_context") == "cn_context"
    assert normalize_provider("composite", market="cn_context") == "composite"
    assert current_contract_path(tmp_path, market="cn_context") == (
        tmp_path / "metadata/current_assets/cn_context_current.json"
    )
    with pytest.raises(ValueError, match="Unsupported provider"):
        normalize_provider("tushare", market="cn_context")


def test_context_observation_validation_rejects_duplicate_vintage():
    frame = _observations()
    duplicate = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        validate_context_observations(duplicate)


def test_context_observation_validation_rejects_reconstructed_as_revision_covered():
    frame = _observations().iloc[[0]].copy()
    frame.loc[:, "reconstructed"] = True
    with pytest.raises(ValueError, match="reconstructed"):
        validate_context_observations(frame)


def test_context_pit_hides_future_publication_and_selects_visible_revision():
    frame = validate_context_observations(_observations())

    jan15 = select_context_as_of(
        frame,
        as_of=datetime(2026, 1, 15, 12, tzinfo=UTC),
    )
    assert jan15.frame["series_id"].tolist() == ["rates.shibor_3m"]
    assert jan15.frame.iloc[0]["value"] == pytest.approx(1.5)
    assert jan15.frame.iloc[0]["vintage_id"] == "v1"

    jan25 = select_context_as_of(
        frame,
        as_of=datetime(2026, 1, 25, 12, tzinfo=UTC),
    )
    assert jan25.frame["series_id"].tolist() == ["rates.shibor_3m"]
    assert jan25.frame.iloc[0]["value"] == pytest.approx(1.6)
    assert jan25.frame.iloc[0]["vintage_id"] == "v2"

    feb03 = select_context_as_of(
        frame,
        as_of=datetime(2026, 2, 3, 12, tzinfo=UTC),
    )
    assert set(feb03.frame["series_id"]) == {
        "rates.shibor_3m",
        "activity.pmi_manufacturing",
    }
    assert feb03.audit["revision_covered"] is True
    assert feb03.audit["reconstructed_series"] == []


def test_context_freshness_uses_latest_series_state_without_dropping_history():
    rows = _observations()
    older = rows.iloc[[0]].copy()
    older.loc[:, "period_start"] = "2025-12-01"
    older.loc[:, "period_end"] = "2025-12-01"
    older.loc[:, "published_at"] = "2025-12-02T03:00:00Z"
    older.loc[:, "observed_at"] = "2025-12-02T03:00:00Z"
    older.loc[:, "ingested_at"] = "2025-12-02T04:00:00Z"
    older.loc[:, "source_retrieved_at"] = "2025-12-02T04:00:00Z"
    older.loc[:, "available_at"] = "2025-12-02T03:00:00Z"
    older.loc[:, "vintage_id"] = "old"
    older.loc[:, "source_hash"] = "d" * 64
    frame = validate_context_observations(pd.concat([older, rows], ignore_index=True))

    panel = select_context_as_of(
        frame,
        as_of=datetime(2026, 2, 3, 12, tzinfo=UTC),
        max_staleness_days=20,
    )

    assert len(panel.frame) == 3
    assert len(panel.frame.loc[panel.frame["series_id"] == "rates.shibor_3m"]) == 2
    assert panel.audit["freshness_verified"] is True
    assert panel.audit["series_stale"] == []


@pytest.mark.parametrize("column", ["revision_covered", "reconstructed"])
@pytest.mark.parametrize("value", ["False", "True", "0", "1", "", None, float("nan"), pd.NA, -1, 2])
def test_context_pit_rejects_invalid_boolean_flags(column, value):
    frame = _observations().iloc[[0]].copy()
    frame[column] = value
    with pytest.raises(ValueError, match="boolean flags"):
        select_context_as_of(frame, as_of="2026-01-15", require_revision_covered=True)


@pytest.mark.parametrize("covered,reconstructed", [(True, False), (1, 0), (1.0, 0.0)])
def test_context_pit_preserves_boolean_and_binary_flag_semantics(covered, reconstructed):
    frame = _observations().iloc[[0]].copy()
    frame["revision_covered"], frame["reconstructed"] = covered, reconstructed
    panel = select_context_as_of(frame, as_of="2026-01-15", require_revision_covered=True)
    assert panel.audit["revision_covered"] is True
    assert panel.audit["reconstructed_series"] == []
    frame["revision_covered"] = 0
    with pytest.raises(ValueError, match="not revision-covered"):
        select_context_as_of(frame, as_of="2026-01-15", require_revision_covered=True)
    frame["reconstructed"] = 1
    panel = select_context_as_of(frame, as_of="2026-01-15")
    assert panel.audit["revision_covered"] is False
    assert panel.audit["reconstructed_series"] == ["rates.shibor_3m"]


@pytest.mark.parametrize("column", ["series_id", "vintage_id"])
@pytest.mark.parametrize("value", [None, pd.NA, float("nan"), "", " \t "])
def test_context_pit_rejects_missing_identity(column, value):
    frame = _observations().iloc[[0]].copy()
    frame[column] = value
    with pytest.raises(ValueError, match="series_id.*vintage_id"):
        select_context_as_of(frame, as_of="2026-01-15", require_revision_covered=True)


@pytest.mark.parametrize("value", [None, pd.NA, float("nan"), "", " \t "])
def test_context_catalog_rejects_missing_series_identity(value):
    from quant_market_data_platform.context.models import (
        CATALOG_REQUIRED_COLUMNS,
        normalize_context_catalog,
    )

    row: dict[str, object] = dict.fromkeys(CATALOG_REQUIRED_COLUMNS, "example")
    row.update(series_id=value, max_staleness=10)
    with pytest.raises(ValueError, match="series_id"):
        normalize_context_catalog(pd.DataFrame([row]))


@pytest.mark.parametrize(
    "value", [0.5, "0.5", None, pd.NA, float("nan"), float("inf"), -float("inf"), -1]
)
def test_context_pit_rejects_invalid_revision_numbers(value):
    frame = _observations().iloc[[0]].copy()
    frame["revision_number"] = value
    with pytest.raises(ValueError, match="revision_number"):
        select_context_as_of(frame, as_of="2026-01-15", require_revision_covered=True)


@pytest.mark.parametrize("value", [0, "0", 0.0, "0.0", 1, "1", "1.0", "1e0"])
def test_context_pit_preserves_integer_revision_representations(value):
    frame = _observations().iloc[[0]].copy()
    frame["revision_number"] = value
    frame["series_id"], frame["vintage_id"] = " rates.shibor_3m ", " v1 "
    panel = select_context_as_of(frame, as_of="2026-01-15", require_revision_covered=True)
    assert panel.audit["revision_covered"] is True
    assert panel.frame.iloc[0]["revision_number"] == float(value)
    assert dict(panel.audit["selected_vintages"]) == {
        "rates.shibor_3m|2026-01-01T00:00:00+00:00": "v1"
    }


def test_context_pit_orders_numeric_revisions_instead_of_numeric_text():
    frame = pd.concat([_observations().iloc[[0]]] * 2, ignore_index=True)
    frame["revision_number"], frame["vintage_id"], frame["value"] = (
        ["10", "2"],
        ["v10", "v2"],
        [1.7, 1.6],
    )
    panel = select_context_as_of(frame, as_of="2026-01-15", require_revision_covered=True)
    assert panel.frame.iloc[0]["value"] == pytest.approx(1.7)
    assert panel.frame.iloc[0]["vintage_id"] == "v10"


@pytest.mark.parametrize(
    "value",
    [float("inf"), -float("inf"), "inf", "-inf", "Infinity", "-Infinity", "+Infinity", " INF "],
)
def test_context_pit_rejects_infinite_observation_values(value):
    frame = _observations().iloc[[0]].copy()
    frame["value"] = value
    with pytest.raises(ValueError, match="infinite"):
        select_context_as_of(frame, as_of="2026-01-15", require_revision_covered=True)


@pytest.mark.parametrize("value", [None, float("nan"), -1.5, 0, "-1.5", "0", "3.2", "unknown"])
def test_context_pit_preserves_other_observation_value_semantics(value):
    frame = _observations().iloc[[0]].copy()
    frame["value"] = value
    panel = select_context_as_of(frame, as_of="2026-01-15", require_revision_covered=True)
    pd.testing.assert_series_equal(panel.frame["value"], frame["value"])


@pytest.mark.parametrize("provider", ["tushare", "nbs"])
@pytest.mark.parametrize("value", ["Infinity", "-Infinity"])
def test_context_providers_reject_infinite_numeric_payload(provider, value):
    import json

    from quant_market_data_platform.context.source_payload import SourcePayload
    from quant_market_data_platform.providers.nbs_context import parse_nbs_context
    from quant_market_data_platform.providers.tushare_context import normalize_tushare_context

    with pytest.raises(ValueError, match="infinite"):
        if provider == "tushare":
            normalize_tushare_context(
                "shibor",
                pd.DataFrame([{"date": "20260828", "on": value}]),
                retrieved_at=datetime(2026, 8, 28, 4, tzinfo=UTC),
                source_hash="a" * 64,
            )
        else:
            envelope = {
                "resolved": {
                    "show_name": "规模以上工业增加值同比增长速度",
                    "indic_id": "indicator",
                },
                "data_response": {
                    "data": [
                        {
                            "code": "202607MM",
                            "values": [
                                {
                                    "i_showname": "规模以上工业增加值同比增长速度",
                                    "_id": "indicator",
                                    "value": value,
                                }
                            ],
                        }
                    ]
                },
            }
            parse_nbs_context(
                SourcePayload(
                    provider="nbs",
                    dataset="industrial_value_added_yoy",
                    source_locator="fixture://nbs",
                    retrieved_at=datetime(2026, 8, 20, 4, tzinfo=UTC),
                    content_type="application/json",
                    body=json.dumps(envelope).encode(),
                    metadata={},
                )
            )
