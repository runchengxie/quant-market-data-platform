"""Historical forecast pagination and immutable checkpoint regressions."""

import argparse
import json

import pandas as pd
import pytest

from quant_market_data_platform.providers import report_rc_history as history
from quant_market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy


class Client:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def report_rc(self, **kwargs):
        self.calls.append(kwargs)
        return self.responder(kwargs)


def rows(count, offset=0, day="20110105"):
    return pd.DataFrame(
        {
            "ts_code": [f"{i:06}.SZ" for i in range(offset, offset + count)],
            "report_date": [day] * count,
            "eps": [1.0] * count,
        }
    )


def fetch(client):
    return history.fetch_month(client, TushareRequestPolicy(attempts=1), "20110101", "20110131", 0)


def test_month_pagination_requires_terminal_short_page(monkeypatch):
    monkeypatch.setattr(history, "PAGE_SIZE", 3)
    client = Client(lambda q: rows(3 if q["offset"] == 0 else 1, q["offset"]))
    frame, counts = fetch(client)
    assert len(frame) == 4 and counts == [3, 1]
    assert [q["offset"] for q in client.calls] == [0, 3]
    assert "create_time" in client.calls[0]["fields"]


def test_repeated_page_fails_even_if_row_order_changes(monkeypatch):
    monkeypatch.setattr(history, "PAGE_SIZE", 3)
    with pytest.raises(ValueError, match="repeated"):
        fetch(Client(lambda q: rows(3).iloc[::-1] if q["offset"] else rows(3)))


def test_out_of_range_and_oversized_responses_fail(monkeypatch):
    monkeypatch.setattr(history, "PAGE_SIZE", 3)
    with pytest.raises(ValueError, match="date"):
        fetch(Client(lambda q: rows(1, day="20110201")))
    with pytest.raises(ValueError, match="limit"):
        fetch(Client(lambda q: rows(4)))


def test_cross_page_duplicates_fail(monkeypatch):
    monkeypatch.setattr(history, "PAGE_SIZE", 3)
    with pytest.raises(ValueError, match="duplicate"):
        fetch(Client(lambda q: rows(3 if not q["offset"] else 1)))


def test_resume_verifies_hash_query_and_empty_partition(tmp_path):
    query = {"start_date": "20110101", "end_date": "20110131", "fields": history.FIELDS}
    history.store_month(tmp_path, rows(1), [1], query)
    proof = history.verified_month(tmp_path, query)
    assert proof["rows"] == 1 and proof["retrieved_at_utc"]
    with pytest.raises(ValueError, match="checkpoint"):
        history.verified_month(tmp_path, {**query, "end_date": "20110130"})
    receipt = tmp_path / "receipt.json"
    modified = json.loads(receipt.read_text())
    modified["payload_sha256"] = "bad"
    receipt.write_text(json.dumps(modified))
    with pytest.raises(ValueError, match="checkpoint"):
        history.verified_month(tmp_path, query)


def test_calendar_months_clip_both_boundaries():
    assert history.month_ranges("20110115", "20110302") == [
        ("20110115", "20110131"),
        ("20110201", "20110228"),
        ("20110301", "20110302"),
    ]
    with pytest.raises(ValueError):
        history.month_ranges("20110302", "20110101")


def test_backfill_runtime_options_and_zero_row_checkpoint(tmp_path, monkeypatch):
    from quant_market_data_platform.providers._adapters_part01 import _request_policy_from_options

    def runtime(**kwargs):
        policy = _request_policy_from_options(None, kwargs["request_options"])
        return Client(lambda q: pd.DataFrame()), policy, None

    monkeypatch.setattr(history, "_tushare_runtime", runtime)
    history.backfill(
        argparse.Namespace(
            out_dir=tmp_path,
            token_env="TEST",
            start_date="20110101",
            end_date="20110131",
            source_revision="test",
            interval=0,
        )
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["status"] == "completed" and manifest["total_rows"] == 0
    assert manifest["months"][0]["page_rows"] == [0]


def test_checkpoint_records_acquisition_revision_separately(tmp_path):
    query = {"start_date": "20110101", "end_date": "20110131", "fields": history.FIELDS}
    history.store_month(tmp_path, rows(1), [1], query, acquisition_revision="first")
    assert history.verified_month(tmp_path, query)["acquisition_revision"] == "first"


def test_inner_symlinks_cannot_redirect_snapshot_writes(tmp_path):
    outside = tmp_path / "other-asset"
    outside.mkdir()
    output = tmp_path / "snapshot"
    output.mkdir()
    (output / "raw").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        history.store_month(output / "raw" / "month=201101", rows(1), [1], {})
    assert list(outside.iterdir()) == []


def test_pending_symlink_does_not_overwrite_existing_file(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (tmp_path / "receipt.pending").symlink_to(victim)
    with pytest.raises(ValueError, match="symlink"):
        history.atomic_json(tmp_path / "receipt.json", {})
    assert victim.read_text() == "keep"
