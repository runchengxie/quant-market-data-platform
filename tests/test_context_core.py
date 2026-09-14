from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from market_data_platform.context.models import validate_context_observations
from market_data_platform.context.pit import select_context_as_of
from market_data_platform.paths import current_contract_path, normalize_market, normalize_provider


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
