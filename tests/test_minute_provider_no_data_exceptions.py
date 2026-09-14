from __future__ import annotations

import json
from pathlib import Path

import pytest

from market_data_platform import _campaign_readiness as readiness


def _write_policy(path: Path, *, allow_exclusion: bool = False) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "market_data_platform.minute_provider_no_data_exceptions.v1",
                "policy": {
                    "allow_exclusion_from_provider_verified_universe": allow_exclusion,
                    "allow_synthetic_bars": False,
                    "allow_complete_partition_promotion": False,
                },
                "exceptions": [{"ts_code": "001872.SZ"}],
            }
        ),
        encoding="utf-8",
    )


def test_no_data_exception_policy_is_bound_and_fail_closed(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    policy_path = tmp_path / "provider_no_data_exceptions.v1.json"
    _write_policy(policy_path)

    result = readiness._validated_no_data_exceptions(manifest_path, {})

    assert result is not None
    assert result["count"] == 1
    assert result["sha256"]


def test_no_data_exception_policy_cannot_enable_exclusion(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    _write_policy(tmp_path / "provider_no_data_exceptions.v1.json", allow_exclusion=True)

    with pytest.raises(readiness.CampaignFatalError, match="fail-closed"):
        readiness._validated_no_data_exceptions(manifest_path, {})
