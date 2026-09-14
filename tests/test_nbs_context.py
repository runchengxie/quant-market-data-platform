from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from market_data_platform.context.source_payload import SourcePayload
from market_data_platform.providers.nbs_context import (
    NBS_CONTEXT_SERIES,
    NBSContextSchemaError,
    fetch_nbs_payload,
    parse_nbs_context,
    resolve_nbs_indicator,
)


def _search_payload(rows):
    return {"data": {"data": rows}}


def _data_payload(
    *, code="202607MM", name="规模以上工业增加值同比增长速度", value="5.7", indicator_name=None
):
    return {
        "data": [
            {
                "code": code,
                "values": [
                    {
                        "i_showname": indicator_name or name,
                        "_id": "ef1b1765960d45a29b4d7c4ca91be916",
                        "value": value,
                    }
                ],
            }
        ]
    }


def test_nbs_industrial_value_added_has_pinned_new_release_identifier():
    spec = NBS_CONTEXT_SERIES["industrial_value_added_yoy"]
    assert spec.series_id == "activity.industrial_value_added_yoy"
    assert spec.exact_identifier == (
        "3f2e14f0542348ed9fe02476eca3450b:ef1b1765960d45a29b4d7c4ca91be916"
    )


def test_nbs_search_resolution_requires_one_exact_expected_name():
    spec = NBS_CONTEXT_SERIES["electricity_generation"]
    row = {
        "cid": "a" * 32,
        "indic_id": "b" * 32,
        "show_name": spec.expected_name,
        "treeinfo_globalid": "root.123.child",
    }
    assert resolve_nbs_indicator(_search_payload([row]), spec) == row

    with pytest.raises(NBSContextSchemaError, match="unique"):
        resolve_nbs_indicator(_search_payload([row, dict(row)]), spec)

    wrong = dict(row, show_name="发电设备平均利用小时")
    with pytest.raises(NBSContextSchemaError, match="expected indicator"):
        resolve_nbs_indicator(_search_payload([wrong]), spec)


def test_fetch_nbs_payload_uses_current_stream_endpoint():
    calls = []

    def request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return {"data": [{"code": "202607MM", "values": []}], "success": True}

    fetch_nbs_payload(
        "industrial_value_added_yoy",
        period="202607",
        request_json=request_json,
    )

    assert calls == [
        (
            "POST",
            "/publicrelease/web/external/stream/esData",
            None,
            {
                "cid": "3f2e14f0542348ed9fe02476eca3450b",
                "indicatorIds": ["ef1b1765960d45a29b4d7c4ca91be916"],
                "daCatalogId": "",
                "das": [{"text": "national", "value": "000000000000"}],
                "showType": 1,
                "dts": ["202607MM"],
                "rootId": "",
            },
        )
    ]


def test_parse_nbs_context_emits_provider_neutral_observation():
    body = json.dumps(
        {
            "resolved": {
                "cid": "3f2e14f0542348ed9fe02476eca3450b",
                "indic_id": "ef1b1765960d45a29b4d7c4ca91be916",
                "show_name": "规模以上工业增加值同比增长速度",
            },
            "data_response": _data_payload(),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    payload = SourcePayload(
        provider="nbs",
        dataset="industrial_value_added_yoy",
        source_locator="https://data.stats.gov.cn/dg/website/publicrelease/web/external/getEsDataByCidAndDt",
        retrieved_at=datetime(2026, 8, 20, 4, tzinfo=UTC),
        content_type="application/json",
        body=body,
        metadata={"period": "202607"},
    )

    observations = parse_nbs_context(payload)
    assert observations["series_id"].tolist() == ["activity.industrial_value_added_yoy"]
    assert observations.iloc[0]["value"] == pytest.approx(5.7)
    assert observations.iloc[0]["unit"] == "percent"
    assert observations.iloc[0]["available_at"] == observations.iloc[0]["source_retrieved_at"]
    assert bool(observations.iloc[0]["revision_covered"]) is True
    assert bool(observations.iloc[0]["reconstructed"]) is False


def test_parse_nbs_context_accepts_current_official_indicator_display_name():
    body = json.dumps(
        {
            "resolved": {
                "cid": "3f2e14f0542348ed9fe02476eca3450b",
                "indic_id": "ef1b1765960d45a29b4d7c4ca91be916",
                "show_name": "规模以上工业增加值同比增长速度",
            },
            "data_response": _data_payload(indicator_name="规上工业增加值同比增长 (%) "),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    payload = SourcePayload(
        provider="nbs",
        dataset="industrial_value_added_yoy",
        source_locator="https://data.stats.gov.cn/example",
        retrieved_at=datetime(2026, 8, 20, 4, tzinfo=UTC),
        content_type="application/json",
        body=body,
        metadata={"period": "202607"},
    )

    observations = parse_nbs_context(payload)
    assert observations.iloc[0]["value"] == pytest.approx(5.7)


def test_parse_nbs_old_backfill_is_reconstructed_and_schema_drift_fails_closed():
    body = json.dumps(
        {
            "resolved": {
                "cid": "3f2e14f0542348ed9fe02476eca3450b",
                "indic_id": "ef1b1765960d45a29b4d7c4ca91be916",
                "show_name": "规模以上工业增加值同比增长速度",
            },
            "data_response": _data_payload(code="202401MM"),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    payload = SourcePayload(
        provider="nbs",
        dataset="industrial_value_added_yoy",
        source_locator="https://data.stats.gov.cn/example",
        retrieved_at=datetime(2026, 8, 28, 4, tzinfo=UTC),
        content_type="application/json",
        body=body,
        metadata={"period": "202401"},
    )
    observations = parse_nbs_context(payload)
    assert bool(observations.iloc[0]["reconstructed"]) is True
    assert bool(observations.iloc[0]["revision_covered"]) is False

    broken = SourcePayload(
        provider="nbs",
        dataset="industrial_value_added_yoy",
        source_locator="https://data.stats.gov.cn/example",
        retrieved_at=payload.retrieved_at,
        content_type="application/json",
        body=b'{"resolved": {}, "data_response": {"unexpected": []}}',
        metadata={},
    )
    with pytest.raises(NBSContextSchemaError):
        parse_nbs_context(broken)
