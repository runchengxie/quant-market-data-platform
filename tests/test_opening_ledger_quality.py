from __future__ import annotations

from market_data_platform.quality_opening import (
    OpeningCancel,
    OpeningOrder,
    OpeningTrade,
    audit_opening_ledger,
    trace_opening_level,
)


def test_audit_opening_ledger_reconstructs_levels() -> None:
    result = audit_opening_ledger(
        [
            OpeningOrder("B1", 1, 1000, 500),
            OpeningOrder("B2", 1, 995, 700),
            OpeningOrder("A1", -1, 1010, 600),
        ],
        [OpeningTrade("B1", "A1", 200)],
        [OpeningCancel("B2", 100)],
        expected_bid_levels=((1000, 300), (995, 600)),
        expected_ask_levels=((1010, 400),),
    )

    assert result.status == "matched"
    assert result.bid_levels == ((1000, 300), (995, 600))
    assert result.unknown_trade_count == 0
    assert result.overdrawn_count == 0


def test_audit_opening_ledger_reports_identity_and_volume_gaps() -> None:
    result = audit_opening_ledger(
        [OpeningOrder("B1", 1, 1000, 100)],
        [OpeningTrade("UNKNOWN-B", "UNKNOWN-A", 30)],
        [OpeningCancel("B1", 120), OpeningCancel("UNKNOWN-C", 10)],
        expected_bid_levels=((1000, 0),),
        expected_ask_levels=(),
    )

    assert result.status == "mismatched"
    assert result.unknown_trade_volume == 30
    assert result.unknown_cancel_volume == 10
    assert result.overdrawn_volume == 20


def test_audit_opening_ledger_consumes_known_side_when_other_trade_id_is_unknown() -> None:
    result = audit_opening_ledger(
        [
            OpeningOrder("B1", 1, 1000, 100),
            OpeningOrder("A1", -1, 1010, 100),
        ],
        [OpeningTrade("UNKNOWN-B", "A1", 30)],
        [],
        expected_bid_levels=((1000, 100),),
        expected_ask_levels=((1010, 70),),
    )

    assert result.status == "mismatched"
    assert result.ask_levels == ((1010, 70),)


def test_trace_opening_level_explains_residuals() -> None:
    rows = trace_opening_level(
        [OpeningOrder("B1", 1, 777, 10000), OpeningOrder("B2", 1, 777, 5000)],
        [OpeningTrade("B1", "A1", 4000)],
        [OpeningCancel("B2", 2100)],
        side=1,
        price=777,
    )

    assert [(row.order_id, row.remaining_volume) for row in rows] == [
        ("B1", 6000),
        ("B2", 2900),
    ]
