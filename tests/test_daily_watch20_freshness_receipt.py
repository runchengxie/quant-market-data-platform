from __future__ import annotations

import hashlib
import json
from pathlib import Path

from market_data_platform.research_views.daily_watch20_data import DailyWatch20Assets
from market_data_platform.research_views.daily_watch20_freshness_receipt import (
    canonical_unavailability_reason,
)


def _assets(root: Path) -> DailyWatch20Assets:
    minute = root / "minute"
    partition = minute / "trade_date=20260713"
    partition.mkdir(parents=True)
    part = partition / "part.parquet"
    part.write_bytes(b"minute")
    coverage = root / "minute.coverage.json"
    coverage.write_text(
        json.dumps(
            {
                "status": "passed",
                "quality_status": "passed",
                "coverage_status": "full_sh_sz",
                "daily": [
                    {
                        "date": "20260713",
                        "valid": True,
                        "market_scope": "SH_SZ",
                        "sh_sz_symbols": 1,
                        "content_sha256": hashlib.sha256(part.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return DailyWatch20Assets(
        data_root=root,
        current_contract=root / "current.json",
        daily_clean=root / "daily",
        instruments=root / "instruments.parquet",
        trade_cal=root / "trade_cal.parquet",
        minute_current=minute,
        minute_coverage=coverage,
        daily_as_of="20260713",
        minute_date_min="20260713",
        minute_date_max="20260713",
    )


def test_canonical_unavailability_reason_accepts_verified_partition(tmp_path: Path) -> None:
    assets = _assets(tmp_path)

    assert (
        canonical_unavailability_reason(
            assets,
            "20260713",
            sha256_file=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        is None
    )


def test_canonical_unavailability_reason_reports_hash_mismatch(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    part = assets.minute_current / "trade_date=20260713" / "part.parquet"
    part.write_bytes(b"tampered")

    reason = canonical_unavailability_reason(
        assets,
        "20260713",
        sha256_file=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
    )

    assert reason is not None
    assert "hash" in reason
