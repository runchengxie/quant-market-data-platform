from market_data_platform.l2_ordering import SequenceQualityState, detect_ordering_columns


def test_detects_exchange_ordering_aliases() -> None:
    ordering = detect_ordering_columns(
        ["SecuCode", "OrderTime", "ChannelNo", "ApplSeqNum", "OrderID"]
    )

    assert ordering == {
        "exchange_sequence_available": True,
        "channel_column": "ChannelNo",
        "sequence_column": "ApplSeqNum",
        "ordering_mode": "channel_sequence",
        "cross_channel_total_order": False,
        "fallback": "timestamp_then_source_order",
    }


def test_sequence_quality_reports_gaps_reversals_and_duplicates() -> None:
    state = SequenceQualityState(max_tracked_sequences=10)
    state.update([1, 1, 1], [10, 12, 11])
    state.update([1], [11])

    assert state.to_payload() == {
        "rows_observed": 4,
        "non_numeric_rows": 0,
        "duplicate_rows": 1,
        "backwards_rows": 1,
        "gap_events": 1,
        "gap_span": 1,
        "tracking_truncated": False,
    }


def test_missing_sequence_is_explicit_timestamp_fallback() -> None:
    ordering = detect_ordering_columns(["SecuCode", "OrderTime", "OrderID"])
    assert ordering["exchange_sequence_available"] is False
    assert ordering["ordering_mode"] == "timestamp_fallback"
