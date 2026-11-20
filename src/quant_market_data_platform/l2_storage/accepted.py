"""Query explicitly accepted daily partitions without silently skipping dates."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .proof import load
from .reader import _scan, catalog


def _accepted_catalog(day: str, receipt_dirs: list[Path]):
    receipts = [
        p for directory in receipt_dirs for p in sorted(directory.glob(f"{day}-*-acceptance.json"))
    ]
    if not receipts:
        raise FileNotFoundError(f"No acceptance receipt for {day}")
    if len(receipts) != 1:
        raise ValueError(f"Multiple acceptance receipts for {day}")
    receipt = load(receipts[0])
    verification = receipt.get("verification", {})
    if (
        receipt.get("date") != day
        or receipt.get("status") != "full_field_and_query_checks_passed"
        or not isinstance(verification, dict)
        or verification.get("status") != "verified"
    ):
        raise ValueError(f"Invalid acceptance receipt for {day}")
    root = Path(receipt.get("output", ""))
    if not root.is_absolute():
        raise ValueError(f"Acceptance receipt requires an absolute output path for {day}")
    manifest, profile = catalog(root)
    config = manifest["config"]
    if (
        config["date"] != day
        or profile["date"] != day
        or config["archive_sha256"] != receipt.get("archive_sha256")
    ):
        raise ValueError(f"Acceptance receipt does not match the verified output for {day}")
    return root, (manifest, profile)


def scan_accepted(
    days: list[str], receipt_dirs: list[Path], modality: str, symbols=None, interval=(None, None)
):
    """Return one scanner per requested date, in input order, after checking every date."""
    if not days or len(set(days)) != len(days):
        raise ValueError("Provide distinct accepted trading dates")
    for day in days:
        if not re.fullmatch(r"[0-9]{8}", day):
            raise ValueError(f"Invalid trading date: {day}")
        datetime.strptime(day, "%Y%m%d")
    return [
        (day, _scan(*_accepted_catalog(day, receipt_dirs), modality, symbols, interval))
        for day in days
    ]
