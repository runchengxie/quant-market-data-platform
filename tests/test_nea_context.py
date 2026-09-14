from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_data_platform.context.source_payload import SourcePayload
from market_data_platform.providers.nea_context import (
    NEAContextSchemaError,
    parse_nea_electricity_release,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "context" / "nea_electricity_release.html"


def _payload(body: bytes, *, retrieved_at: datetime) -> SourcePayload:
    return SourcePayload(
        provider="nea",
        dataset="electricity",
        source_locator="https://www.nea.gov.cn/20260821/example/c.html",
        retrieved_at=retrieved_at,
        content_type="text/html;charset=utf-8",
        body=body,
        metadata={},
    )


def test_parse_nea_release_extracts_monthly_levels_and_yoy():
    payload = _payload(
        _FIXTURE.read_bytes(),
        retrieved_at=datetime(2026, 8, 22, 2, tzinfo=UTC),
    )
    observations = parse_nea_electricity_release(payload)
    values = observations.set_index("series_id")["value"].to_dict()

    assert values["energy.electricity_consumption_total"] == pytest.approx(10400.0)
    assert values["energy.electricity_consumption_total_yoy"] == pytest.approx(1.7)
    assert values["energy.electricity_consumption_primary"] == pytest.approx(167.0)
    assert values["energy.electricity_consumption_primary_yoy"] == pytest.approx(-2.0)
    assert values["energy.electricity_consumption_secondary"] == pytest.approx(6116.0)
    assert values["energy.electricity_consumption_industrial"] == pytest.approx(6055.0)
    assert values["energy.electricity_consumption_high_tech_equipment"] == pytest.approx(1220.0)
    assert values["energy.electricity_consumption_tertiary"] == pytest.approx(2183.0)
    assert values["energy.electricity_consumption_residential"] == pytest.approx(1935.0)
    assert bool(observations["revision_covered"].all()) is True
    assert bool(observations["reconstructed"].any()) is False


def test_old_nea_page_backfill_is_reconstructed_but_keeps_official_publish_date():
    payload = _payload(
        _FIXTURE.read_bytes(),
        retrieved_at=datetime(2026, 10, 1, 2, tzinfo=UTC),
    )
    observations = parse_nea_electricity_release(payload)
    assert bool(observations["revision_covered"].any()) is False
    assert bool(observations["reconstructed"].all()) is True
    assert observations["published_at"].notna().all()
    assert (observations["available_at"] == observations["source_retrieved_at"]).all()


def test_nea_parser_fails_closed_on_unit_or_title_drift():
    html = _FIXTURE.read_text(encoding="utf-8")
    bad_unit = html.replace("10400亿千瓦时", "10400万千瓦时")
    with pytest.raises(NEAContextSchemaError, match="全社会用电量"):
        parse_nea_electricity_release(
            _payload(
                bad_unit.encode("utf-8"),
                retrieved_at=datetime(2026, 8, 22, 2, tzinfo=UTC),
            )
        )

    bad_title = html.replace("2026年1-7月份全社会用电量同比增长4.7%", "能源信息")
    with pytest.raises(NEAContextSchemaError, match="title"):
        parse_nea_electricity_release(
            _payload(
                bad_title.encode("utf-8"),
                retrieved_at=datetime(2026, 8, 22, 2, tzinfo=UTC),
            )
        )
