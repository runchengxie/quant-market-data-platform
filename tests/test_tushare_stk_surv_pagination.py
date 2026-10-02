"""Institutional survey pagination and resume completeness regressions."""

import json

import pandas as pd
import pytest
import yaml

from quant_market_data_platform.providers import _adapters_part03 as adapters
from quant_market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy


def survey_rows(count, offset=0):
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * count,
            "surv_date": ["20260930"] * count,
            "fund_visitors": [f"visitor-{i}" for i in range(offset, offset + count)],
        }
    )


def install_provider(monkeypatch, responder):
    class Provider:
        def stk_surv(self, **kwargs):
            return responder(kwargs)

    monkeypatch.setattr(
        adapters,
        "_tushare_runtime",
        lambda **kwargs: (Provider(), TushareRequestPolicy(attempts=1), "https://example.invalid"),
    )


def mirror(tmp_path, **kwargs):
    return adapters.mirror_a_share_stk_surv(
        out_dir=tmp_path / "survey", start_date="20260930", end_date="20260930", **kwargs
    )


def test_survey_paginates_full_page_and_hash_binds_completion(tmp_path, monkeypatch):
    calls = []

    def respond(kwargs):
        calls.append(kwargs)
        return survey_rows(400 if kwargs.get("offset", 0) == 0 else 3, kwargs.get("offset", 0))

    install_provider(monkeypatch, respond)
    result = mirror(tmp_path)
    assert result["totals"]["rows"] == 403
    assert [(c["limit"], c["offset"]) for c in calls] == [(400, 0), (400, 400)]
    assert result["pagination"]["complete"] is True
    proof = json.loads((tmp_path / "survey/data/event_date=20260930/pagination.json").read_text())
    assert proof["page_rows"] == [400, 3]
    assert proof["rows"] == 403 and len(proof["payload_sha256"]) == 64


def test_survey_repeated_page_fails_partial_without_payload(tmp_path, monkeypatch):
    install_provider(monkeypatch, lambda kwargs: survey_rows(400))
    with pytest.raises(ValueError, match="repeated"):
        mirror(tmp_path)
    manifest = yaml.safe_load((tmp_path / "survey/manifest.yml").read_text())
    assert manifest["status"] == "partial"
    assert manifest["pagination"]["complete"] is False
    assert not list((tmp_path / "survey/data").rglob("*.parquet"))


def test_survey_exact_cap_requires_empty_terminal_page_and_spaces_requests(tmp_path, monkeypatch):
    calls = []
    sleeps = []

    def respond(kwargs):
        calls.append(kwargs)
        return survey_rows(400) if kwargs.get("offset", 0) == 0 else survey_rows(0)

    install_provider(monkeypatch, respond)
    monkeypatch.setattr(adapters.time, "sleep", sleeps.append)
    result = mirror(tmp_path, request_interval_seconds=2)
    assert len(calls) == 2 and result["totals"]["rows"] == 400
    assert sleeps == [2, 2]


def test_survey_skip_existing_rejects_unproved_old_cap(tmp_path, monkeypatch):
    path = tmp_path / "survey/data/event_date=20260930/part.parquet"
    path.parent.mkdir(parents=True)
    survey_rows(400).to_parquet(path, index=False)
    before = path.read_bytes()
    install_provider(monkeypatch, lambda kwargs: pytest.fail("must fail before request"))
    with pytest.raises(ValueError, match="pagination"):
        mirror(tmp_path, skip_existing=True)
    assert path.read_bytes() == before


def test_survey_verified_resume_retains_physical_counts(tmp_path, monkeypatch):
    install_provider(monkeypatch, lambda kwargs: survey_rows(3))
    mirror(tmp_path)
    install_provider(monkeypatch, lambda kwargs: pytest.fail("verified partition should skip"))
    result = mirror(tmp_path, skip_existing=True)
    assert result["totals"]["rows"] == 3 and result["totals"]["files"] == 1
    assert result["totals"]["event_dates_skipped"] == 1
    assert result["run_totals"]["rows"] == 0
    assert result["run_totals"]["files"] == 0
    assert result["pagination"]["complete"] is True


@pytest.mark.parametrize("mode", ["oversized", "wrong_date", "provider_error", "reordered_repeat"])
def test_survey_invalid_pages_fail_closed(tmp_path, monkeypatch, mode):
    def respond(kwargs):
        if mode == "provider_error":
            raise RuntimeError("request failed secret-do-not-persist")
        frame = survey_rows(401 if mode == "oversized" else 400)
        if mode == "wrong_date":
            frame["surv_date"] = "20260929"
        if mode == "reordered_repeat" and kwargs.get("offset", 0):
            frame = frame.iloc[::-1]
        return frame

    install_provider(monkeypatch, respond)
    with pytest.raises((ValueError, RuntimeError)):
        mirror(tmp_path)
    text = (tmp_path / "survey/manifest.yml").read_text()
    assert yaml.safe_load(text)["status"] == "partial"
    assert "secret-do-not-persist" not in text
    assert not list((tmp_path / "survey").rglob("*.parquet"))


def test_survey_max_page_bound_fails_closed(tmp_path, monkeypatch):
    from quant_market_data_platform.providers import tushare_stk_surv_pagination as pagination

    monkeypatch.setattr(pagination, "MAX_PAGES", 2)
    install_provider(monkeypatch, lambda kwargs: survey_rows(400, kwargs["offset"]))
    with pytest.raises(ValueError, match="maximum page bound"):
        mirror(tmp_path)
    assert yaml.safe_load((tmp_path / "survey/manifest.yml").read_text())["status"] == "partial"


@pytest.mark.parametrize("tamper", ["bytes", "query", "counts"])
def test_survey_resume_rejects_changed_proof_or_payload(tmp_path, monkeypatch, tamper):
    install_provider(monkeypatch, lambda kwargs: survey_rows(3))
    mirror(tmp_path)
    folder = tmp_path / "survey/data/event_date=20260930"
    proof_path = folder / "pagination.json"
    if tamper == "bytes":
        survey_rows(2).to_parquet(folder / "part.parquet", index=False)
    else:
        proof = json.loads(proof_path.read_text())
        if tamper == "query":
            proof["query"]["fields"] = "ts_code"
        else:
            proof["page_rows"] = [400]
        proof_path.write_text(json.dumps(proof))
    install_provider(monkeypatch, lambda kwargs: pytest.fail("reject stale proof before request"))
    with pytest.raises(ValueError, match="pagination"):
        mirror(tmp_path, skip_existing=True)


def test_survey_rejects_offset_override_before_request(tmp_path, monkeypatch):
    install_provider(monkeypatch, lambda kwargs: pytest.fail("unsafe pagination options"))
    with pytest.raises(ValueError, match="reserved"):
        mirror(tmp_path, query_options={"offset": 400})


def test_survey_rejects_symlink_partition_on_resume(tmp_path, monkeypatch):
    target = tmp_path / "outside"
    target.mkdir()
    output = tmp_path / "survey/data"
    output.mkdir(parents=True)
    (output / "event_date=20260930").symlink_to(target, target_is_directory=True)
    install_provider(monkeypatch, lambda kwargs: survey_rows(3))
    with pytest.raises(ValueError, match="symlink"):
        mirror(tmp_path, skip_existing=True)
    assert list(target.iterdir()) == []


def test_survey_failed_legacy_resume_reports_physical_inventory(tmp_path, monkeypatch):
    path = tmp_path / "survey/data/event_date=20260930/part.parquet"
    path.parent.mkdir(parents=True)
    survey_rows(400).to_parquet(path, index=False)
    install_provider(monkeypatch, lambda kwargs: pytest.fail("must reject unproved partition"))
    with pytest.raises(ValueError, match="pagination"):
        mirror(tmp_path, skip_existing=True)
    result = yaml.safe_load((tmp_path / "survey/manifest.yml").read_text())
    assert result["totals"]["rows"] == 400 and result["totals"]["files"] == 1
    assert result["pagination"]["complete"] is False


def test_survey_resume_accounts_outside_query_retained_payload(tmp_path, monkeypatch):
    old = tmp_path / "survey/data/event_date=20260929/part.parquet"
    old.parent.mkdir(parents=True)
    survey_rows(2).to_parquet(old, index=False)
    install_provider(monkeypatch, lambda kwargs: survey_rows(3))
    result = mirror(tmp_path, skip_existing=True)
    assert result["totals"]["rows"] == 5 and result["totals"]["files"] == 2
    assert result["run_totals"]["rows"] == 3
    assert result["pagination"]["unverified_retained_event_dates"] == ["20260929"]
    assert result["status"] == "partial" and result["pagination"]["complete"] is False
