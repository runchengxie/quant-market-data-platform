from unittest.mock import patch

import pytest

from quant_market_data_platform import tushare_minute_reverse_backfill as scheduler
from quant_market_data_platform._minute_history_lock import (
    HistoryWriterBusy,
    history_writer_lock,
)


def test_main_and_repair_cannot_share_a_writer_lane(tmp_path):
    with history_writer_lock(tmp_path):
        path = tmp_path / "metadata/minute_backfill/reverse_scheduler/writer.lock"
        original = path.read_bytes()
        with pytest.raises(HistoryWriterBusy), history_writer_lock(tmp_path):
            pytest.fail("must not enter second writer")
        assert path.read_bytes() == original
    with history_writer_lock(tmp_path):
        pass


def test_main_obeys_audit_marker_before_network_or_lock(tmp_path):
    marker = tmp_path / "audit-block.json"
    marker.write_text("{}")
    with patch.object(scheduler, "_run_reverse_backfill") as run:
        result = scheduler.run_reverse_backfill(
            scheduler.ReverseBackfillOptions(artifacts_root=tmp_path, audit_block_path=marker)
        )
    assert result["status"] == "audit_blocked"
    run.assert_not_called()
    assert not (tmp_path / "metadata/minute_backfill/reverse_scheduler/writer.lock").exists()


def test_main_returns_busy_without_touching_existing_owner(tmp_path):
    with history_writer_lock(tmp_path), patch.object(scheduler, "_run_reverse_backfill") as run:
        result = scheduler.run_reverse_backfill(
            scheduler.ReverseBackfillOptions(artifacts_root=tmp_path)
        )
    assert result["status"] == "writer_busy"
    run.assert_not_called()
