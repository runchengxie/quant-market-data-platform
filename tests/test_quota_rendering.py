from market_data_platform.quota_rendering import (
    augment_quota_payload,
    format_quota_pretty,
)


def test_quota_payload_adds_derived_usage_values():
    payload = augment_quota_payload({"bytes_used": 512, "bytes_limit": 1024})
    assert payload["bytes_remaining"] == 512
    assert payload["used_pct"] == 50.0
    assert payload["remaining_pct"] == 50.0


def test_quota_pretty_renders_bytes_and_progress():
    text = format_quota_pretty({"bytes_used": 512, "bytes_limit": 1024, "used_pct": 50.0})
    assert "bytes_used: 512.00 B (512 B)" in text
    assert "usage: [##########----------] 50.00% used" in text
