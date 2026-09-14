import json
import shutil
from pathlib import Path

import pytest

from market_data_platform.minute_candidate import MinuteCandidateError
from market_data_platform.tushare_minute_operational import OPERATIONAL_VERSION_SCHEMA
from market_data_platform.tushare_minute_operational_daily import _base_receipt, _paths
from market_data_platform.tushare_minute_reverse_backfill import _current_min_date


@pytest.mark.parametrize("reader", ["daily", "reverse"])
@pytest.mark.parametrize("kind", ["entity", "symlink", "mismatch", "missing"])
def test_readers_validate_current_version_receipt(tmp_path: Path, reader: str, kind: str) -> None:
    alias = tmp_path / "assets/derived/a_share/minute_1m_tushare"
    version = alias.with_name("minute_1m_tushare_v1_20260908")
    version.mkdir(parents=True)
    payload = {
        "schema_version": OPERATIONAL_VERSION_SCHEMA,
        "status": "published_operational_version",
        "output_dir": str(version),
        "summary": {"date_min": "20220715", "date_max": "20260908"},
    }
    receipt = version / "_operational_receipt.json"
    receipt.write_text(json.dumps(payload))
    if kind == "symlink":
        alias.symlink_to(version, target_is_directory=True)
    else:
        shutil.copytree(version, alias)
    if kind == "mismatch":
        receipt.write_text(json.dumps({**payload, "summary": {"date_min": "20220101"}}))
    if kind == "missing":
        receipt.unlink()

    def read():
        return _base_receipt(_paths(tmp_path)) if reader == "daily" else _current_min_date(alias)

    if kind in {"mismatch", "missing"}:
        with pytest.raises(MinuteCandidateError):
            read()
    elif reader == "daily":
        assert read() == (receipt, payload)
    else:
        assert read() == "20220715"
