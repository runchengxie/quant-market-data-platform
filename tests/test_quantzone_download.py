from __future__ import annotations

import importlib
import json
from pathlib import Path

import pandas as pd
import pytest
from test_quantzone_plan import FakeClient, fixture_config


def batch_plan(tmp_path: Path, end_date: str = "2024-01-05"):
    api = importlib.import_module("quant_market_data_platform.quantzone_plan")
    return api.build_factor_plan(fixture_config(tmp_path, "query", end_date=end_date), {})


def observations() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-02"],
            "ukey": ["000001", "600519"],
            "factor": ["trend_dominance_factor"] * 2,
            "value": [1.5, None],
        }
    )


class DownloadClient(FakeClient):
    def get_factors(self, **kwargs: object) -> pd.DataFrame:
        self.factor_queries.append(kwargs)
        frame = observations()
        frame["date"] = str(kwargs["start_date"])
        return frame


def test_validation_preserves_codes_and_nulls(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_artifacts")
    batch = batch_plan(tmp_path).batches[0]
    mapping = {"000001": "000001.SZ", "600519": "600519.SH"}
    validated = api.validate_factor_batch(observations(), batch, mapping)
    panel = api.project_factor_panel(validated, mapping)
    assert panel["symbol"].tolist() == ["000001.SZ", "600519.SH"]
    assert panel["trend_dominance_factor"].isna().sum() == 1


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("date", "2023-12-01"),
        ("date", "bad-date"),
        ("ukey", "000002"),
        ("ukey", 1),
        ("factor", "other_factor"),
        ("value", "invalid"),
        ("value", float("inf")),
        ("value", True),
    ],
)
def test_rejects_invalid_observations(tmp_path: Path, column: str, value: object) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_artifacts")
    frame = observations().astype(object)
    frame.loc[0, column] = value
    with pytest.raises(ValueError):
        api.validate_factor_batch(
            frame, batch_plan(tmp_path).batches[0], {"000001": "000001.SZ", "600519": "600519.SH"}
        )


def test_duplicates_rejected_without_averaging(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_artifacts")
    frame = pd.concat([observations(), observations()])
    with pytest.raises(ValueError, match="Duplicate"):
        api.validate_factor_batch(
            frame, batch_plan(tmp_path).batches[0], {"000001": "000001.SZ", "600519": "600519.SH"}
        )


def test_download_records_hashes_and_observed_missingness(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_download")
    plan = batch_plan(tmp_path)
    client = DownloadClient()
    run = api.run_factor_download(plan, client)
    receipt = json.loads((run / "receipt.json").read_text())
    assert receipt["status"] == "complete"
    assert receipt["evidence"]["pit_availability"] == "unknown"
    assert receipt["batches"][0]["null_count"] == 1
    assert receipt["batches"][0]["row_count"] == 2
    assert receipt["projection"]["sha256"]
    assert client.closed and len(client.factor_queries) == 1
    assert pd.read_parquet(run / receipt["projection"]["path"])["symbol"].tolist() == [
        "000001.SZ",
        "600519.SH",
    ]


def test_completed_resume_is_offline_and_immutable(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_download")
    plan = batch_plan(tmp_path)
    run = api.run_factor_download(plan, DownloadClient())
    before = (run / "receipt.json").read_bytes()
    client = DownloadClient()
    assert api.run_factor_download(plan, client, resume=run) == run
    assert not client.factor_queries and client.closed
    assert (run / "receipt.json").read_bytes() == before


def test_interrupted_resume_skips_verified_complete_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_download")
    plan = batch_plan(tmp_path, "2024-01-12")
    client = DownloadClient()
    original = client.get_factors

    def fail_second(**kwargs: object) -> pd.DataFrame:
        if client.factor_queries:
            raise RuntimeError("fixture-secret-never-serialize")
        return original(**kwargs)

    monkeypatch.setattr(client, "get_factors", fail_second)
    with pytest.raises(ValueError) as exc:
        api.run_factor_download(plan, client)
    assert "fixture-secret" not in str(exc.value)
    run = next(plan.root.iterdir())
    receipt = json.loads((run / "receipt.json").read_text())
    assert receipt["status"] == "partial"
    assert receipt["batches"][0]["status"] == "complete"
    resumed = DownloadClient()
    assert api.run_factor_download(plan, resumed, resume=run) == run
    assert len(resumed.factor_queries) == 1
    assert client.closed and resumed.closed


def test_tampering_and_identity_mismatch_fail_before_network(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_download")
    plan = batch_plan(tmp_path)
    run = api.run_factor_download(plan, DownloadClient())
    receipt = json.loads((run / "receipt.json").read_text())
    (run / receipt["batches"][0]["path"]).write_bytes(b"corrupt")
    client = DownloadClient()
    with pytest.raises(ValueError, match="hash"):
        api.run_factor_download(plan, client, resume=run)
    assert client.factor_queries == [] and client.closed
    client = DownloadClient()
    with pytest.raises(ValueError, match="identity"):
        api.run_factor_download(batch_plan(tmp_path, "2024-01-12"), client, resume=run)
    assert client.factor_queries == [] and client.closed


def test_resume_outside_output_root_rejected(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_download")
    with pytest.raises(ValueError):
        api.run_factor_download(batch_plan(tmp_path), DownloadClient(), resume=tmp_path)


def test_empty_batch_records_empty_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_download")
    client = DownloadClient()
    monkeypatch.setattr(client, "get_factors", lambda **kwargs: observations().iloc[:0])
    run = api.run_factor_download(batch_plan(tmp_path), client)
    receipt = json.loads((run / "receipt.json").read_text())
    assert receipt["coverage"] == "observed_only"
    assert receipt["batches"][0]["empty_response"] is True
    assert receipt["projection"]["row_count"] == 0


def test_atomic_parquet_failure_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_artifacts")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail)
    with pytest.raises(OSError):
        api.atomic_parquet(tmp_path / "batch.parquet", observations())
    assert list(tmp_path.iterdir()) == []


def test_projection_works_through_existing_research_interface(tmp_path: Path) -> None:
    from quant_market_data_platform.research_data_interface import ResearchDataInterface

    api = importlib.import_module("quant_market_data_platform.quantzone_download")
    run = api.run_factor_download(batch_plan(tmp_path), DownloadClient())
    interface = ResearchDataInterface(
        "a_share",
        {
            "provider": "local_artifact",
            "source_mode": "fixed_scored_artifact",
            "panel_file": str(run / "research-panel.parquet"),
        },
        tmp_path / "cache",
    )
    frame = interface.fetch_daily("000001.SZ", "20240102", "20240105")
    assert frame["trend_dominance_factor"].tolist() == [1.5]


def test_projection_hash_verified_before_completed_resume(tmp_path: Path) -> None:
    api = importlib.import_module("quant_market_data_platform.quantzone_download")
    plan = batch_plan(tmp_path)
    run = api.run_factor_download(plan, DownloadClient())
    (run / "research-panel.parquet").write_bytes(b"corruption")
    client = DownloadClient()
    with pytest.raises(ValueError, match="hash"):
        api.run_factor_download(plan, client, resume=run)
    assert client.factor_queries == [] and client.closed


def test_factor_chunk_projection_joins_without_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quant_market_data_platform.configuration import load_config
    from quant_market_data_platform.quantzone_plan import build_factor_plan

    api = importlib.import_module("quant_market_data_platform.quantzone_download")
    config = fixture_config(tmp_path, "batch", max_factors=1)
    payload = json.loads(config.path.read_text())
    payload["downloads"]["quantzone"]["query"]["factor"] = [
        "trend_dominance_factor",
        "other_factor",
    ]
    config.path.write_text(json.dumps(payload))
    plan = build_factor_plan(load_config(config.path), {})
    client = DownloadClient()
    original = client.list_factors()
    monkeypatch.setattr(
        client, "list_factors", lambda: [*original, {**original[0], "factor": "other_factor"}]
    )

    def frame(**kwargs: object) -> pd.DataFrame:
        result = observations()
        requested = kwargs["factor"]
        assert isinstance(requested, list)
        result["factor"] = requested[0]
        return result

    monkeypatch.setattr(client, "get_factors", frame)
    run = api.run_factor_download(plan, client)
    projection = pd.read_parquet(run / "research-panel.parquet")
    assert len(projection) == 2
    assert set(projection.columns) == {
        "symbol",
        "trade_date",
        "trend_dominance_factor",
        "other_factor",
    }
