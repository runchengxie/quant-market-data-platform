"""Exchange-ordering provenance and bounded sequence-quality accounting for L2 data."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

CHANNEL_COLUMNS = ("ChannelNo", "Channel", "channel_no", "channel")
SEQUENCE_COLUMNS = (
    "ApplSeqNum",
    "BizIndex",
    "SeqNum",
    "SeqNo",
    "Sequence",
    "seq_num",
    "sequence",
)


def _first_present(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    return next((candidate for candidate in candidates if candidate in columns), None)


def detect_ordering_columns(columns: Iterable[str]) -> dict[str, Any]:
    names = {str(column) for column in columns}
    channel = _first_present(names, CHANNEL_COLUMNS)
    sequence = _first_present(names, SEQUENCE_COLUMNS)
    available = sequence is not None
    return {
        "exchange_sequence_available": available,
        "channel_column": channel,
        "sequence_column": sequence,
        "ordering_mode": (
            "channel_sequence"
            if channel and sequence
            else "sequence_only"
            if sequence
            else "timestamp_fallback"
        ),
        "cross_channel_total_order": False,
        "fallback": "timestamp_then_source_order",
    }


def _sequence_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class SequenceQualityState:
    max_tracked_sequences: int = 1_000_000
    rows_observed: int = 0
    non_numeric_rows: int = 0
    duplicate_rows: int = 0
    backwards_rows: int = 0
    gap_events: int = 0
    gap_span: int = 0
    tracking_truncated: bool = False
    _last_by_channel: dict[str, int] = field(default_factory=dict, repr=False)
    _seen: set[tuple[str, int]] = field(default_factory=set, repr=False)

    def update(self, channels: Iterable[Any], sequences: Iterable[Any]) -> None:
        for channel_value, sequence_value in zip(channels, sequences, strict=True):
            self.rows_observed += 1
            sequence = _sequence_int(sequence_value)
            if sequence is None:
                self.non_numeric_rows += 1
                continue
            channel = "__global__" if channel_value is None else str(channel_value)
            key = (channel, sequence)
            if key in self._seen:
                self.duplicate_rows += 1
            elif len(self._seen) < self.max_tracked_sequences:
                self._seen.add(key)
            else:
                self.tracking_truncated = True

            previous = self._last_by_channel.get(channel)
            if previous is not None:
                if sequence < previous:
                    self.backwards_rows += 1
                elif sequence > previous + 1:
                    self.gap_events += 1
                    self.gap_span += sequence - previous - 1
            self._last_by_channel[channel] = sequence

    def to_payload(self) -> dict[str, Any]:
        return {
            "rows_observed": self.rows_observed,
            "non_numeric_rows": self.non_numeric_rows,
            "duplicate_rows": self.duplicate_rows,
            "backwards_rows": self.backwards_rows,
            "gap_events": self.gap_events,
            "gap_span": self.gap_span,
            "tracking_truncated": self.tracking_truncated,
        }
