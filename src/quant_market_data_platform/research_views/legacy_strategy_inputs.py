"""Read explicit, hash-pinned research panels without legacy path discovery.

An operator-provided availability timestamp is a cutoff check, not proof of
historical source visibility. These manifests produce diagnostic evidence only.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pandas as pd

_FIELDS = {"schema_version", "asset_kind", "path", "sha256", "available_at"}
_KINDS = {"etf_daily", "a_share_daily", "ths_hot", "concept_members", "rotation_signal"}
_MAX_BYTES = 64 * 1024 * 1024


def _manifest(path: Path, asset_kind: str, cutoff: pd.Timestamp) -> dict:
    values = json.loads(path.read_text())
    if not isinstance(values, dict) or set(values) != _FIELDS:
        raise ValueError("strategy manifest must have exactly the declared fields")
    if values["schema_version"] != "research.strategy_input.v1":
        raise ValueError("unsupported strategy manifest schema_version")
    if asset_kind not in _KINDS or values["asset_kind"] != asset_kind:
        raise ValueError("strategy manifest asset_kind mismatch")
    available = values["available_at"]
    if available is not None:
        timestamp = pd.Timestamp(available)
        if timestamp.tzinfo is None:
            raise ValueError("available_at must include a timezone")
        if pd.isna(timestamp) or timestamp > cutoff:
            raise ValueError("asset available_at exceeds research cutoff")
    return values


def load_strategy_panel(
    manifest_path: Path,
    *,
    asset_kind: str,
    as_of: str,
) -> tuple[pd.DataFrame, dict]:
    """Read a bounded Parquet panel with explicit identity and observation cutoff."""
    manifest_path = Path(manifest_path)
    cutoff = pd.Timestamp(as_of)
    if not isinstance(cutoff, pd.Timestamp):
        raise ValueError("as_of must be a valid timestamp")
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    manifest = _manifest(manifest_path, asset_kind, cutoff)
    path = Path(manifest["path"]).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("strategy panel exceeds 64 MiB; publish a bounded research view")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != manifest["sha256"]:
        raise ValueError("strategy input sha256 mismatch")
    frame = pd.read_parquet(io.BytesIO(raw))
    if "trade_date" not in frame:
        raise ValueError("strategy panel requires trade_date")
    dates = pd.to_datetime(
        frame["trade_date"].astype("string"), format="mixed", utc=True, errors="raise"
    )
    if dates.isna().any():
        raise ValueError("strategy panel contains missing trade_date")
    frame["trade_date"] = dates.dt.tz_localize(None).to_numpy()
    frame = frame.loc[(dates <= cutoff).to_numpy()].copy()
    return frame.reset_index(drop=True), {
        "schema_version": "research.strategy_input_receipt.v1",
        "asset_kind": asset_kind,
        "sha256": digest,
        "as_of": cutoff.isoformat(),
        "strict_point_in_time": False,
        "evidence_stage": "diagnostic",
        "limitation": "Manifest availability is not an independently validated visibility receipt.",
    }
