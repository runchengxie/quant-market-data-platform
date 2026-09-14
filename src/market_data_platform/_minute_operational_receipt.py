"""Validate operational receipts for version paths and copied current aliases."""

from pathlib import Path
from typing import Any

from market_data_platform.minute_candidate import MinuteCandidateError, _read_json
from market_data_platform.tushare_minute_operational import (
    OPERATIONAL_ALIAS_NAME,
    OPERATIONAL_VERSION_SCHEMA,
)


def read_operational_receipt(output_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Require copied aliases to retain the referenced version's exact receipt."""
    receipt_path = output_dir / "_operational_receipt.json"
    receipt = _read_json(receipt_path)
    version_dir = Path(receipt.get("output_dir", "")).expanduser().resolve()
    valid_location = version_dir == output_dir
    if not valid_location and output_dir.name == OPERATIONAL_ALIAS_NAME:
        embedded = version_dir / "_operational_receipt.json"
        valid_location = (
            version_dir.parent == output_dir.parent
            and version_dir.name.startswith(f"{OPERATIONAL_ALIAS_NAME}_v")
            and embedded.is_file()
            and embedded.read_bytes() == receipt_path.read_bytes()
        )
    if (
        receipt.get("schema_version") != OPERATIONAL_VERSION_SCHEMA
        or receipt.get("status") != "published_operational_version"
        or not valid_location
    ):
        raise MinuteCandidateError(f"Invalid current operational receipt: {receipt_path}")
    return version_dir / "_operational_receipt.json", receipt
