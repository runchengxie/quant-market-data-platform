"""Market-agnostic order-identity accounting for opening-auction audits."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

OpeningStatus = Literal["matched", "mismatched", "not_comparable"]
Level = tuple[int, int]


@dataclass(frozen=True)
class OpeningOrder:
    """One order active in the audited opening window."""

    order_id: str
    side: int
    price: int
    volume: int
    time_ms: int = 0


@dataclass(frozen=True)
class OpeningTrade:
    """One trade with buy-side and sell-side order identities."""

    buy_id: str
    sell_id: str
    volume: int
    time_ms: int = 0


@dataclass(frozen=True)
class OpeningCancel:
    """One cancellation with the identity of the cancelled order."""

    order_id: str
    volume: int
    time_ms: int = 0


@dataclass(frozen=True)
class OpeningLedgerAudit:
    """Identity, volume-conservation, and optional top-level comparison results."""

    status: OpeningStatus
    bid_levels: tuple[Level, ...]
    ask_levels: tuple[Level, ...]
    expected_bid_levels: tuple[Level, ...] | None
    expected_ask_levels: tuple[Level, ...] | None
    unknown_trade_count: int
    unknown_trade_volume: int
    unknown_cancel_count: int
    unknown_cancel_volume: int
    overdrawn_count: int
    overdrawn_volume: int


@dataclass(frozen=True)
class OpeningLevelTrace:
    """Order-level quantity conservation details for one price level."""

    order_id: str
    side: int
    price: int
    original_volume: int
    traded_volume: int
    cancelled_volume: int
    remaining_volume: int


def _normalise_levels(levels: Sequence[Level] | None, depth: int) -> tuple[Level, ...] | None:
    if levels is None:
        return None
    return tuple((int(price), int(volume)) for price, volume in levels[:depth] if int(volume) > 0)


def _top_levels(
    remaining: dict[str, list[int]],
    *,
    side: int,
    depth: int,
) -> tuple[Level, ...]:
    levels: dict[int, int] = {}
    for order_side, price, volume in remaining.values():
        if order_side == side and volume > 0:
            levels[price] = levels.get(price, 0) + volume
    prices = sorted(levels, reverse=side == 1)
    return tuple((price, levels[price]) for price in prices[:depth])


def audit_opening_ledger(  # noqa: PLR0913
    orders: Sequence[OpeningOrder],
    trades: Sequence[OpeningTrade],
    cancels: Sequence[OpeningCancel],
    *,
    expected_bid_levels: Sequence[Level] | None,
    expected_ask_levels: Sequence[Level] | None,
    depth: int = 10,
) -> OpeningLedgerAudit:
    """Consume trades and cancellations by order ID and compare top levels."""
    remaining = {
        order.order_id: [order.side, order.price, max(order.volume, 0)] for order in orders
    }
    unknown_trade_count = 0
    unknown_trade_volume = 0
    unknown_cancel_count = 0
    unknown_cancel_volume = 0
    overdrawn_count = 0
    overdrawn_volume = 0

    def consume(order_id: str, volume: int) -> bool:
        nonlocal overdrawn_count, overdrawn_volume
        order = remaining.get(order_id)
        if order is None:
            return False
        requested = max(volume, 0)
        consumed = min(order[2], requested)
        excess = requested - consumed
        order[2] -= consumed
        if excess:
            overdrawn_count += 1
            overdrawn_volume += excess
        return True

    for trade in trades:
        buy_known = consume(trade.buy_id, trade.volume)
        sell_known = consume(trade.sell_id, trade.volume)
        if not buy_known or not sell_known:
            unknown_trade_count += 1
            unknown_trade_volume += max(trade.volume, 0)
    for cancel in cancels:
        if not consume(cancel.order_id, cancel.volume):
            unknown_cancel_count += 1
            unknown_cancel_volume += max(cancel.volume, 0)

    bids = _top_levels(remaining, side=1, depth=depth)
    asks = _top_levels(remaining, side=-1, depth=depth)
    expected_bids = _normalise_levels(expected_bid_levels, depth)
    expected_asks = _normalise_levels(expected_ask_levels, depth)
    comparable = expected_bids is not None and expected_asks is not None
    identity_gap = unknown_trade_count or unknown_cancel_count or overdrawn_count
    if not comparable:
        status: OpeningStatus = "not_comparable"
    elif identity_gap or bids != expected_bids or asks != expected_asks:
        status = "mismatched"
    else:
        status = "matched"
    return OpeningLedgerAudit(
        status=status,
        bid_levels=bids,
        ask_levels=asks,
        expected_bid_levels=expected_bids,
        expected_ask_levels=expected_asks,
        unknown_trade_count=unknown_trade_count,
        unknown_trade_volume=unknown_trade_volume,
        unknown_cancel_count=unknown_cancel_count,
        unknown_cancel_volume=unknown_cancel_volume,
        overdrawn_count=overdrawn_count,
        overdrawn_volume=overdrawn_volume,
    )


def trace_opening_level(
    orders: Sequence[OpeningOrder],
    trades: Sequence[OpeningTrade],
    cancels: Sequence[OpeningCancel],
    *,
    side: int,
    price: int,
) -> tuple[OpeningLevelTrace, ...]:
    """Return per-order original, traded, cancelled, and remaining quantities."""
    original = {
        order.order_id: [order.side, order.price, max(order.volume, 0), 0, 0] for order in orders
    }
    for trade in trades:
        for order_id in (trade.buy_id, trade.sell_id):
            if order_id in original:
                original[order_id][3] += max(trade.volume, 0)
    for cancel in cancels:
        if cancel.order_id in original:
            original[cancel.order_id][4] += max(cancel.volume, 0)
    return tuple(
        OpeningLevelTrace(
            order_id=order_id,
            side=data[0],
            price=data[1],
            original_volume=data[2],
            traded_volume=data[3],
            cancelled_volume=data[4],
            remaining_volume=max(data[2] - data[3] - data[4], 0),
        )
        for order_id, data in original.items()
        if data[0] == side and data[1] == price
    )
