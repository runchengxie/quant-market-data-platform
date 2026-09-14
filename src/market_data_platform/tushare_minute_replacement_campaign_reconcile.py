"""Control-plane reconciliation for completed TuShare minute campaigns."""

from __future__ import annotations

import fcntl
import sys
from pathlib import Path
from typing import Any

from market_data_platform.tushare_minute_replacement_campaign_runner import (
    RETRYABLE_EXIT_CODE,
    SCHEMA_VERSION,
    CampaignFatalError,
    _load_ledger,
    _read_json,
    _write_readiness_marker,
)


def reconcile_readiness(args: Any) -> int:
    """Rebuild acquisition readiness from immutable campaign evidence only."""

    manifest_path = args.manifest.expanduser().resolve()
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported campaign manifest: {manifest_path}")
    lock_path = Path(str(manifest["lock_path"]))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("campaign lock is already held; retry later", file=sys.stderr)
            return RETRYABLE_EXIT_CODE
        ledger_path = Path(str(manifest["ledger_path"]))
        ledger = _load_ledger(ledger_path, manifest_path)
        if ledger.get("status") != "complete":
            raise CampaignFatalError(
                "Cannot reconcile readiness before the campaign ledger is complete"
            )
        payload = _write_readiness_marker(manifest_path, manifest, ledger_path)
    print(
        "campaign acquisition readiness reconciled: "
        f"dates={payload['dates_complete']} rows={payload['rows']}"
    )
    return 0
