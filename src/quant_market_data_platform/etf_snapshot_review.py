"""Separate policy validation for immutable ETF acquisitions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .etf_snapshot import _adjusted_panel, _validate_coverage, snapshot_file

REVIEWER = "owner-etf-snapshot-policy-v1"
POLICY = {
    "id": REVIEWER,
    "checks": [
        "complete-unreviewed",
        "relative-file-hashes",
        "aware-receipt-clock",
        "bar-close-before-acquisition-start",
        "unique-positive-pairs",
        "calendar-coverage",
    ],
}
POLICY_SHA256 = hashlib.sha256(
    json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def review_etf_snapshot(
    manifest_path: Path,
    *,
    reviewer: str,
    policy_sha256: str,
    now: datetime,
    output: Path,
) -> dict[str, Any]:
    """Validate observed-by evidence without certifying historical delivery."""
    if reviewer != REVIEWER or policy_sha256 != POLICY_SHA256:
        raise ValueError("unknown owner review policy")
    value = _validate_snapshot(manifest_path, now)
    result = {
        "schema_version": "marketdata.etf_snapshot_review.v1",
        "status": "accepted",
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "panel_sha256": value["files"]["panel"]["sha256"],
        "calendar_sha256": value["files"]["calendar"]["sha256"],
        "observed_by": value["capture_completed_at"],
        "reviewed_at": now.isoformat(),
        "reviewer": reviewer,
        "policy_sha256": policy_sha256,
        "reasons": [],
    }
    encoded = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("unsafe receipt output")
    if output.exists():
        previous = json.loads(output.read_bytes())
        if {k: v for k, v in previous.items() if k != "reviewed_at"} != {
            k: v for k, v in result.items() if k != "reviewed_at"
        }:
            raise ValueError("receipt identity conflict")
        return previous
    with output.open("xb") as stream:
        stream.write(encoded)
    return result


def _validate_snapshot(manifest_path: Path, now: datetime) -> dict[str, Any]:
    if manifest_path.is_symlink() or now.tzinfo is None:
        raise ValueError("invalid manifest or review clock")
    value = json.loads(manifest_path.read_bytes())
    if value["schema_version"] != "marketdata.etf_snapshot.v1" or value["status"] != "unreviewed":
        raise ValueError("snapshot is not complete and unreviewed")
    started = datetime.fromisoformat(value["capture_started_at"])
    completed = datetime.fromisoformat(value["capture_completed_at"])
    close = datetime.strptime(value["end_date"], "%Y%m%d").replace(
        hour=15, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    if started.tzinfo is None or completed.tzinfo is None or not started <= completed <= now:
        raise ValueError("invalid observed clock")
    if started < close:
        raise ValueError("acquisition starts before bar close")
    paths = {
        key: snapshot_file(manifest_path.parent, record) for key, record in value["files"].items()
    }
    if set(paths) != {"daily", "factors", "calendar", "panel"}:
        raise ValueError("incomplete snapshot files")
    frames = {key: pd.read_parquet(path) for key, path in paths.items()}
    _validate_coverage(
        frames["calendar"],
        frames["daily"],
        tuple(value["symbols"]),
        value["start_date"],
        value["end_date"],
    )
    expected = _adjusted_panel(
        frames["daily"], frames["factors"], value["start_date"], value["end_date"]
    )
    pd.testing.assert_frame_equal(expected, frames["panel"], check_names=False)
    return value
