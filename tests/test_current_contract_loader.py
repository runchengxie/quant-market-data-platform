from __future__ import annotations

import json
from pathlib import Path

from market_data_platform.contract import load_current_contract


def test_load_current_contract_reads_mapping_payload(tmp_path: Path) -> None:
    path = tmp_path / "a_share_current.json"
    path.write_text(json.dumps({"contract": {"name": "a_share_current"}}), encoding="utf-8")

    assert load_current_contract(path) == {"contract": {"name": "a_share_current"}}


def test_load_current_contract_returns_none_for_missing_path(tmp_path: Path) -> None:
    assert load_current_contract(tmp_path / "missing.json") is None
