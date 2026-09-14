from __future__ import annotations

from datetime import UTC, datetime

import pytest

from market_data_platform.context.source_payload import SourcePayload


def test_source_payload_is_immutable_and_hashes_exact_bytes():
    payload = SourcePayload(
        provider="nbs",
        dataset="industrial_activity",
        source_locator="https://data.stats.gov.cn/example",
        retrieved_at=datetime(2026, 8, 28, 12, tzinfo=UTC),
        content_type="application/json",
        body=b'{"value": 1}',
        metadata={"period": "202607"},
    )

    assert payload.byte_count == len(payload.body)
    assert payload.sha256 == "e1d70a18cc129fcc812ebbe309bc5197df6ffa2228c77a4a7b98653ec5605354"
    with pytest.raises((AttributeError, TypeError)):
        payload.provider = "nea"  # ty: ignore[invalid-assignment]
