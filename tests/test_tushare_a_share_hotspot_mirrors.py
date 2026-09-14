from __future__ import annotations

import pytest
import yaml

from market_data_platform.cli import build_parser
from market_data_platform.providers import tushare_a_share
from market_data_platform.providers.tushare_a_share_dc_concept_cons import (
    fetch_dc_concept_cons_pages,
)


class ConceptConsClient:
    def __init__(self, pd):
        self.pd = pd
        self.query_calls: list[tuple[str, dict[str, object]]] = []

    def trade_cal(self, **_kwargs):
        return self.pd.DataFrame({"cal_date": ["20260522"], "is_open": [1]})

    def query(self, api_name: str, **kwargs):
        self.query_calls.append((api_name, dict(kwargs)))
        date = str(kwargs.get("trade_date") or "")
        if api_name == "dc_concept_cons":
            return self.pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": [date],
                    "name": ["平安银行"],
                    "theme_code": ["BK0001"],
                    "industry_code": ["801780"],
                    "industry": ["银行"],
                    "reason": ["概念成分"],
                    "hot_num": [1],
                }
            )
        if api_name == "kpl_concept_cons":
            return self.pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "name": ["平安银行"],
                    "con_name": ["金融"],
                    "con_code": ["KPL0001"],
                    "trade_date": [date],
                    "desc": ["概念成分"],
                    "hot_num": [2],
                }
            )
        raise AssertionError(f"Unexpected query api: {api_name}")


class AuctionClient:
    def __init__(self, pd):
        self.pd = pd
        self.query_calls: list[tuple[str, dict[str, object]]] = []

    def trade_cal(self, **_kwargs):
        return self.pd.DataFrame({"cal_date": ["20260522"], "is_open": [1]})

    def query(self, api_name: str, **kwargs):
        self.query_calls.append((api_name, dict(kwargs)))
        date = str(kwargs.get("trade_date") or "")
        if api_name in {"stk_auction_o", "stk_auction_c"}:
            return self.pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [date]})
        raise AssertionError(f"Unexpected query api: {api_name}")


def test_concept_constituent_mirrors_use_query_fallback(tmp_path):
    pd = pytest.importorskip("pandas")
    client = ConceptConsClient(pd)

    dc_manifest = tushare_a_share.mirror_a_share_dc_concept_cons(
        out_dir=tmp_path / "dc_concept_cons",
        start_date="20260522",
        end_date="20260522",
        client=client,
    )
    assert client.query_calls[0][0] == "dc_concept_cons"
    assert client.query_calls[0][1]["limit"] == 3000
    assert client.query_calls[0][1]["offset"] == 0
    assert "theme_code" in str(client.query_calls[0][1]["fields"])
    assert dc_manifest["schema_version"] == "tushare.dc_concept_cons.v1"
    assert dc_manifest["dataset"] == "dc_concept_cons"
    assert dc_manifest["status"] == "completed"
    assert dc_manifest["written_trade_dates"] == ["20260522"]
    assert dc_manifest["totals"]["rows"] == 1
    assert dc_manifest["totals"]["pages"] == 1
    date_receipt = dc_manifest["completeness"]["trade_dates"]["20260522"]
    assert dc_manifest["complete"] is True
    assert date_receipt["complete"] is True
    assert date_receipt["page_count"] == 1
    assert date_receipt["row_count"] == 1
    assert date_receipt["distinct_theme_count"] == 1
    assert date_receipt["coverage"]["row_coverage_ratio"] == 1.0

    client.query_calls.clear()
    kpl_manifest = tushare_a_share.mirror_a_share_kpl_concept_cons(
        out_dir=tmp_path / "kpl_concept_cons",
        start_date="20260522",
        end_date="20260522",
        client=client,
    )
    assert client.query_calls[0][0] == "kpl_concept_cons"
    assert "con_code" in str(client.query_calls[0][1]["fields"])
    assert kpl_manifest["dataset"] == "kpl_concept_cons"


def test_dc_concept_cons_paginates_until_short_page():
    pd = pytest.importorskip("pandas")
    calls: list[dict[str, object]] = []

    def fetch_page(kwargs):
        calls.append(dict(kwargs))
        offset = int(kwargs["offset"])
        rows = {
            0: [
                {"ts_code": "000001.SZ", "trade_date": "20260522", "theme_code": "A"},
                {"ts_code": "000002.SZ", "trade_date": "20260522", "theme_code": "A"},
            ],
            2: [
                {"ts_code": "000003.SZ", "trade_date": "20260522", "theme_code": "B"},
            ],
        }.get(offset, [])
        return pd.DataFrame(rows)

    result = fetch_dc_concept_cons_pages(
        trade_date="20260522",
        api_kwargs={"trade_date": "20260522", "fields": "ts_code,trade_date,theme_code"},
        fetch_page=fetch_page,
        pandas=pd,
        page_size=2,
        max_pages=10,
    )

    assert [call["offset"] for call in calls] == [0, 2]
    assert all(call["limit"] == 2 for call in calls)
    assert len(result.frame) == 3
    assert result.completeness == {
        "trade_date": "20260522",
        "complete": True,
        "row_count": 3,
        "page_count": 2,
        "request_count": 2,
        "page_size": 2,
        "terminal_page_reached": True,
        "last_page_row_count": 1,
        "pagination_strategy": "offset_with_boundary_theme_repair",
        "offset_page_count": 2,
        "offset_request_count": 2,
        "repair_page_count": 0,
        "repair_request_count": 0,
        "boundary_theme_count": 0,
        "boundary_theme_codes": [],
        "raw_row_count": 3,
        "distinct_theme_count": 2,
        "coverage": {
            "field": "theme_code",
            "populated_row_count": 3,
            "row_coverage_ratio": 1.0,
        },
    }


def test_dc_concept_cons_repairs_overlap_at_sorted_theme_boundary():
    pd = pytest.importorskip("pandas")
    calls: list[tuple[str | None, int]] = []

    def fetch_page(kwargs):
        theme_code = kwargs.get("theme_code")
        offset = int(kwargs["offset"])
        calls.append((theme_code, offset))
        if theme_code == "B":
            rows = [
                {"ts_code": "000002.SZ", "trade_date": "20260522", "theme_code": "B"},
                {"ts_code": "000003.SZ", "trade_date": "20260522", "theme_code": "B"},
            ]
            return pd.DataFrame(rows)
        rows = {
            0: [
                {"ts_code": "000001.SZ", "trade_date": "20260522", "theme_code": "A"},
                {"ts_code": "000002.SZ", "trade_date": "20260522", "theme_code": "B"},
                {"ts_code": "000003.SZ", "trade_date": "20260522", "theme_code": "B"},
            ],
            3: [
                {"ts_code": "000003.SZ", "trade_date": "20260522", "theme_code": "B"},
                {"ts_code": "000004.SZ", "trade_date": "20260522", "theme_code": "C"},
                {"ts_code": "000005.SZ", "trade_date": "20260522", "theme_code": "C"},
            ],
            6: [
                {"ts_code": "000006.SZ", "trade_date": "20260522", "theme_code": "D"},
            ],
        }[offset]
        return pd.DataFrame(rows)

    result = fetch_dc_concept_cons_pages(
        trade_date="20260522",
        api_kwargs={"trade_date": "20260522"},
        fetch_page=fetch_page,
        pandas=pd,
        page_size=3,
        max_pages=10,
    )

    assert calls == [(None, 0), (None, 3), (None, 6), ("B", 0)]
    assert len(result.frame) == 6
    assert not result.frame.duplicated(["trade_date", "theme_code", "ts_code"]).any()
    assert result.completeness["complete"] is True
    assert result.completeness["raw_row_count"] == 7
    assert result.completeness["page_count"] == 4
    assert result.completeness["request_count"] == 4
    assert result.completeness["boundary_theme_codes"] == ["B"]


def test_dc_concept_cons_requires_terminal_page_before_publish():
    pd = pytest.importorskip("pandas")

    def fetch_page(kwargs):
        offset = int(kwargs["offset"])
        return pd.DataFrame(
            {
                "ts_code": [f"{offset + 1:06d}.SZ", f"{offset + 2:06d}.SZ"],
                "trade_date": ["20260522", "20260522"],
                "theme_code": [f"T{offset + 1}", f"T{offset + 2}"],
            }
        )

    with pytest.raises(RuntimeError, match="reached max_pages=2"):
        fetch_dc_concept_cons_pages(
            trade_date="20260522",
            api_kwargs={"trade_date": "20260522"},
            fetch_page=fetch_page,
            pandas=pd,
            page_size=2,
            max_pages=2,
        )


def test_dc_concept_cons_exact_full_page_uses_empty_terminal_request():
    pd = pytest.importorskip("pandas")
    calls: list[int] = []

    def fetch_page(kwargs):
        offset = int(kwargs["offset"])
        calls.append(offset)
        if offset:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": ["20260522", "20260522"],
                "theme_code": ["A", "B"],
            }
        )

    result = fetch_dc_concept_cons_pages(
        trade_date="20260522",
        api_kwargs={"trade_date": "20260522"},
        fetch_page=fetch_page,
        pandas=pd,
        page_size=2,
        max_pages=2,
    )

    assert calls == [0, 2]
    assert result.completeness["complete"] is True
    assert result.completeness["page_count"] == 1
    assert result.completeness["request_count"] == 2
    assert result.completeness["last_page_row_count"] == 0


def test_dc_concept_cons_rejects_non_exact_trade_date():
    pd = pytest.importorskip("pandas")

    with pytest.raises(ValueError, match="does not match the requested exact date"):
        fetch_dc_concept_cons_pages(
            trade_date="20260522",
            api_kwargs={"trade_date": "20260522"},
            fetch_page=lambda _kwargs: pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20260521"],
                    "theme_code": ["A"],
                }
            ),
            pandas=pd,
            page_size=2,
            max_pages=2,
        )


def test_dc_concept_cons_empty_terminal_page_is_not_complete():
    pd = pytest.importorskip("pandas")
    result = fetch_dc_concept_cons_pages(
        trade_date="20260522",
        api_kwargs={"trade_date": "20260522"},
        fetch_page=lambda _kwargs: pd.DataFrame(),
        pandas=pd,
        page_size=2,
        max_pages=2,
    )

    assert result.frame.empty
    assert result.completeness["terminal_page_reached"] is True
    assert result.completeness["complete"] is False
    assert result.completeness["distinct_theme_count"] == 0


def test_dc_concept_cons_pagination_failure_preserves_previous_atomic_manifest(tmp_path):
    pd = pytest.importorskip("pandas")

    class RepeatingPageClient:
        def trade_cal(self, **_kwargs):
            return pd.DataFrame({"cal_date": ["20260522"], "is_open": [1]})

        def query(self, api_name, **_kwargs):
            assert api_name == "dc_concept_cons"
            return pd.DataFrame(
                {
                    "ts_code": [f"{index:06d}.SZ" for index in range(3000)],
                    "trade_date": ["20260522"] * 3000,
                    "theme_code": ["A"] * 3000,
                }
            )

    output = tmp_path / "dc_concept_cons"
    output.mkdir()
    manifest_path = output / "manifest.yml"
    manifest_path.write_text("complete: true\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="repeated an earlier page|pages overlap"):
        tushare_a_share.mirror_a_share_dc_concept_cons(
            out_dir=output,
            start_date="20260522",
            end_date="20260522",
            client=RepeatingPageClient(),
        )

    assert manifest_path.read_text(encoding="utf-8") == "complete: true\n"


def test_dc_concept_cons_empty_refresh_preserves_last_known_good_partition(tmp_path):
    pd = pytest.importorskip("pandas")

    class EmptyClient:
        def trade_cal(self, **_kwargs):
            return pd.DataFrame({"cal_date": ["20260522"], "is_open": [1]})

        def query(self, api_name, **_kwargs):
            assert api_name == "dc_concept_cons"
            return pd.DataFrame()

    output = tmp_path / "dc_concept_cons"
    part_path = output / "data" / "trade_date=20260522" / "part.parquet"
    part_path.parent.mkdir(parents=True)
    part_path.write_text("stale", encoding="utf-8")

    manifest = tushare_a_share.mirror_a_share_dc_concept_cons(
        out_dir=output,
        start_date="20260522",
        end_date="20260522",
        client=EmptyClient(),
    )

    assert part_path.read_text(encoding="utf-8") == "stale"
    assert manifest["complete"] is False
    written = yaml.safe_load((output / "manifest.yml").read_text(encoding="utf-8"))
    assert written["completeness"]["trade_dates"]["20260522"]["complete"] is False


def test_auction_mirrors_use_query_fallback_and_are_exposed(tmp_path):
    pd = pytest.importorskip("pandas")
    client = AuctionClient(pd)

    open_manifest = tushare_a_share.mirror_a_share_stk_auction_open(
        out_dir=tmp_path / "stk_auction_o",
        start_date="20260522",
        end_date="20260522",
        client=client,
    )
    assert client.query_calls[0][0] == "stk_auction_o"
    assert client.query_calls[0][1]["fields"] == "ts_code,trade_date"
    assert open_manifest["dataset"] == "stk_auction_o"

    client.query_calls.clear()
    close_manifest = tushare_a_share.mirror_a_share_stk_auction_close(
        out_dir=tmp_path / "stk_auction_c",
        start_date="20260522",
        end_date="20260522",
        client=client,
    )
    assert client.query_calls[0][0] == "stk_auction_c"
    assert close_manifest["dataset"] == "stk_auction_c"

    parser = build_parser()
    required = ["--out-dir", "output", "--start-date", "20260522", "--end-date", "20260522"]
    parsed = parser.parse_args(["tushare", "mirror-a-share-stk-auction-open", *required])
    assert parsed.tushare_command == "mirror-a-share-stk-auction-open"
    parsed = parser.parse_args(["tushare", "mirror-a-share-stk-auction-close", *required])
    assert parsed.tushare_command == "mirror-a-share-stk-auction-close"
