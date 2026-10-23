"""Commit current metadata with a recoverable derived-registry pending marker."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .publication_lock import atomic_write, publication_lock


def current_contracts(root: Path) -> list[dict[str, Any]]:
    """Read all published markets while holding the publication lock."""
    from .paths import SUPPORTED_MARKETS, current_contract_path

    contracts = []
    for market in sorted(SUPPORTED_MARKETS):
        path = current_contract_path(root, market=market)
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid current contract: {path}")
        contracts.append(payload)
    return contracts


def commit_current(root: Path, path: Path, payload: dict[str, Any]) -> None:
    """Caller builds under the root lock. Failure retains pending evidence.

    Each file is atomic, the pair is not. Readers treat the contract as authority.
    Retrying the publisher or rebuilding the registry reconciles the derived CSV.
    """
    from .contract import write_current_contract
    from .paths import dataset_registry_path
    from .registry import write_combined_dataset_registry

    with publication_lock(root):
        pending = root / "metadata/current_assets/.registry-pending.json"
        atomic_write(
            pending, json.dumps({"status": "metadata_pending", "contract": str(path)}).encode()
        )
        write_current_contract(path, payload)
        write_combined_dataset_registry(dataset_registry_path(root), current_contracts(root))
        pending.unlink(missing_ok=True)


def sync_minute_catalog(root: Path, alias: Path) -> None:
    """Merge the locked current minute descriptor without changing business target_date."""
    from .contract import build_current_contract, describe_current_path
    from .paths import current_contract_path

    with publication_lock(root):
        path = current_contract_path(root, market="a_share")
        payload = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else build_current_contract(root, market="a_share", provider="tushare")
        )
        payload["assets"]["minute_1m_tushare"] = describe_current_path(alias)
        payload["contract"]["generated_at"] = (
            datetime.now().astimezone().isoformat(timespec="seconds")
        )
        payload["contract"]["generated_by"] = "marketdata minute operational metadata sync"
        commit_current(root, path, payload)
