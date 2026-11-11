from __future__ import annotations

from datetime import datetime
from email.message import Message
from typing import Any

import pytest

from quant_market_data_platform.providers import eastmoney_board_flow as provider


def _payload(rows: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {
        "rc": 0,
        "data": {"total": len(rows) if total is None else total, "diff": rows},
    }


def test_fetch_boards_pages_until_total_and_dedupes() -> None:
    pages = {
        "1": _payload(
            [
                {"f12": "BK0001", "f14": "甲", "f62": 1.5e9, "f184": 1.2, "f3": 0.5, "f6": 2e10},
                {"f12": "BK0002", "f14": "乙", "f62": -3.0e8, "f184": -0.4, "f3": -0.2, "f6": 5e9},
            ],
            total=3,
        ),
        "2": _payload(
            [
                {"f12": "BK0001", "f14": "甲", "f62": 1.5e9},
                {"f12": "BK0003", "f14": "丙", "f62": 0.0},
            ],
            total=3,
        ),
    }
    requested: list[str] = []

    def fetcher(url: str, params: dict[str, str]) -> dict[str, Any]:
        requested.append(params["pn"])
        return pages[params["pn"]]

    client = provider.BoardFlowClient(fetcher=fetcher, page_size=2)
    rows = client.fetch_boards("concept")

    assert [row.board_code for row in rows] == ["BK0001", "BK0002", "BK0003"]
    assert requested == ["1", "2"]
    assert rows[0].main_net_yuan == pytest.approx(1.5e9)
    assert rows[0].board_type == "concept"


def test_fetch_boards_tolerates_missing_numeric_fields() -> None:
    fetcher = lambda url, params: _payload(  # noqa: E731
        [{"f12": "BK0001", "f14": "甲", "f62": "-", "f184": "-", "f3": "-", "f6": None}]
    )
    rows = provider.BoardFlowClient(fetcher=fetcher).fetch_boards("industry")

    assert rows[0].main_net_yuan is None
    assert rows[0].main_net_ratio_pct is None
    assert rows[0].amount_yuan is None


def test_fetch_boards_respects_max_pages() -> None:
    def fetcher(url: str, params: dict[str, str]) -> dict[str, Any]:
        page = int(params["pn"])
        return _payload([{"f12": f"BK{page:04d}", "f14": f"板块{page}", "f62": 1.0}], total=999)

    rows = provider.BoardFlowClient(fetcher=fetcher, page_size=1, max_pages=3).fetch_boards(
        "concept"
    )

    assert len(rows) == 3


def test_fetch_boards_rejects_non_zero_rc() -> None:
    fetcher = lambda url, params: {"rc": 102, "data": None}  # noqa: E731

    with pytest.raises(provider.BoardFlowError, match="rc=102"):
        provider.BoardFlowClient(fetcher=fetcher).fetch_boards("concept")


def test_fetch_boards_rejects_unknown_board_type() -> None:
    with pytest.raises(ValueError, match="Unsupported board type"):
        provider.BoardFlowClient().fetch_boards("concept_v2")


def test_fetch_boards_requires_rows() -> None:
    fetcher = lambda url, params: _payload([], total=0)  # noqa: E731

    with pytest.raises(provider.BoardFlowDataError, match="No concept boards"):
        provider.BoardFlowClient(fetcher=fetcher).fetch_boards("concept")


def _curve_payload(klines: list[str]) -> dict[str, Any]:
    return {"rc": 0, "data": {"code": "BK0800", "market": 90, "klines": klines}}


def test_fetch_minute_curve_parses_cumulative_categories() -> None:
    captured: dict[str, str] = {}

    def fetcher(url: str, params: dict[str, str]) -> dict[str, Any]:
        captured.update(params)
        return _curve_payload(
            [
                "2026-11-11 09:31,-143057872.0,194627088.0,-62711856.0,-87832576.0,-55225296.0",
                "2026-11-11 09:32,-284951808.0,362625456.0,-89923600.0,-183377360.0,-101574448.0",
            ]
        )

    rows = provider.BoardFlowClient(fetcher=fetcher).fetch_minute_curve(
        "BK0800", board_type="concept"
    )

    assert captured["secid"] == "90.BK0800"
    assert captured["klt"] == "1"
    assert [row.trade_time for row in rows] == [
        datetime(2026, 11, 11, 9, 31),
        datetime(2026, 11, 11, 9, 32),
    ]
    assert rows[0].main_net_yuan == pytest.approx(-143057872.0)
    assert rows[0].large_net_yuan + rows[0].xlarge_net_yuan == pytest.approx(rows[0].main_net_yuan)
    assert rows[0].board_type == "concept"


def test_minute_curve_rejects_main_inflow_mismatch() -> None:
    with pytest.raises(provider.BoardFlowDataError, match="main net inflow disagrees"):
        provider.parse_minute_klines(
            ["2026-11-11 09:31,-100.0,10.0,20.0,-30.0,-40.0"],
            board_code="BK0001",
            board_type="concept",
        )


def test_minute_curve_rejects_non_increasing_time() -> None:
    with pytest.raises(provider.BoardFlowDataError, match="not strictly increasing"):
        provider.parse_minute_klines(
            [
                "2026-11-11 09:32,-70.0,10.0,20.0,-30.0,-40.0",
                "2026-11-11 09:32,-70.0,10.0,20.0,-30.0,-40.0",
            ],
            board_code="BK0001",
            board_type="concept",
        )


def test_minute_curve_rejects_malformed_rows() -> None:
    with pytest.raises(provider.BoardFlowDataError, match="malformed kline"):
        provider.parse_minute_klines(
            ["2026-11-11 09:31,-70.0"], board_code="BK1", board_type="concept"
        )
    with pytest.raises(provider.BoardFlowDataError, match="invalid trade time"):
        provider.parse_minute_klines(
            ["nope,-70.0,10.0,20.0,-30.0,-40.0"], board_code="BK1", board_type="concept"
        )
    with pytest.raises(provider.BoardFlowDataError, match="empty minute series"):
        provider.parse_minute_klines([], board_code="BK1", board_type="concept")


def test_minute_curve_raises_when_endpoint_returns_no_data() -> None:
    fetcher = lambda url, params: {"rc": 0, "data": None}  # noqa: E731

    with pytest.raises(provider.BoardFlowDataError, match="no data payload"):
        provider.BoardFlowClient(fetcher=fetcher).fetch_minute_curve("BK0800", board_type="concept")


def test_default_fetch_json_retries_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"rc": 0, "data": {"total": 0, "diff": []}}'

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise provider.URLError("temporary")
        return _Response()

    sleeps: list[float] = []
    monkeypatch.setattr(provider, "urlopen", fake_urlopen)
    monkeypatch.setattr(provider.time, "sleep", sleeps.append)

    payload = provider.default_fetch_json(
        "https://example.invalid", {}, attempts=3, retry_delay=0.5
    )

    assert payload["rc"] == 0
    assert len(attempts) == 3
    assert sleeps == [0.5, 1.0]


def test_default_fetch_json_stops_on_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: float) -> Any:
        raise provider.HTTPError("https://example.invalid", 404, "Not Found", Message(), None)

    monkeypatch.setattr(provider, "urlopen", fake_urlopen)

    with pytest.raises(provider.BoardFlowError, match="HTTP 404"):
        provider.default_fetch_json("https://example.invalid", {}, attempts=3)


def test_board_type_queries_cover_three_families() -> None:
    assert set(provider.BOARD_TYPE_QUERY) == {"industry", "concept", "region"}
    assert provider.BOARD_TYPE_QUERY["concept"] == "m:90+t:3"


def test_default_fetch_json_retries_throttle_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = iter([b'{"rc": 102, "data": null}', b'{"rc": 0, "data": {"total": 0}}'])

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return next(payloads)

    sleeps: list[float] = []
    monkeypatch.setattr(provider, "urlopen", lambda request, timeout: _Response())
    monkeypatch.setattr(provider.time, "sleep", sleeps.append)

    payload = provider.default_fetch_json(
        "https://example.invalid", {}, attempts=3, retry_delay=0.25
    )

    assert payload["rc"] == 0
    assert sleeps == [0.25]


def test_fetch_boards_stops_on_short_page_even_if_total_disagrees() -> None:
    """Live data can report a total that no longer matches a shifting page window."""

    def fetcher(url: str, params: dict[str, str]) -> dict[str, Any]:
        page = int(params["pn"])
        rows = [
            {"f12": f"BK{page:04d}0{index}", "f14": f"板块{index}", "f62": 1.0}
            for index in range(2)
        ]
        return _payload(rows, total=999)

    requested: list[str] = []

    def tracking(url: str, params: dict[str, str]) -> dict[str, Any]:
        requested.append(params["pn"])
        return fetcher(url, params)

    client = provider.BoardFlowClient(fetcher=tracking, page_size=3)
    rows = client.fetch_boards("concept")

    assert requested == ["1"]
    assert len(rows) == 2
