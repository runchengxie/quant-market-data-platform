from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from market_data_platform.providers import tushare_a_share_constraints as constraints
from market_data_platform.providers import tushare_constraint_download as constraint_download


class FakeNamechangeClient:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.namechange: Callable[..., pd.DataFrame] = self._namechange

    def _namechange(
        self,
        *,
        start_date: str,
        end_date: str,
        offset: int,
        limit: int,
    ) -> pd.DataFrame:
        self.calls.append(offset)
        rows = [
            {
                "ts_code": f"{index:06d}.SZ",
                "name": "ST示例",
                "start_date": start_date,
                "end_date": end_date,
                "ann_date": start_date,
            }
            for index in range(6)
        ]
        return pd.DataFrame(rows[offset : offset + limit])


class FakeMarginClient:
    def trade_cal(self, **params: Any) -> pd.DataFrame:
        if int(str(params["offset"])) > 0:
            return pd.DataFrame()
        return pd.DataFrame({"cal_date": ["20240102", "20240103"]})

    def margin_secs(self, **params: Any) -> pd.DataFrame:
        if int(str(params["offset"])) > 0:
            return pd.DataFrame()
        trade_date = str(params["trade_date"])
        return pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "exchange": "SZSE",
                }
            ]
        )


class FakeConstraintClient(FakeMarginClient):
    def margin_detail(self, **params: object) -> pd.DataFrame:
        if int(str(params["offset"])) > 0:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "trade_date": str(params["trade_date"]),
                    "ts_code": "000001.SZ",
                    "rzye": 100.0,
                    "rqyl": 5.0,
                }
            ]
        )

    def suspend_d(self, **params: object) -> pd.DataFrame:
        if int(str(params["offset"])) > 0:
            return pd.DataFrame()
        rows = [
            {
                "trade_date": trade_date,
                "ts_code": "000001.SZ",
                "suspend_timing": "09:30:00",
                "suspend_type": "S",
            }
            for trade_date in ("20240102", "20240103")
            if str(params["start_date"]) <= trade_date <= str(params["end_date"])
        ]
        return pd.DataFrame(rows)

    def slb_sec_detail(self, **params: object) -> pd.DataFrame:
        if int(str(params["offset"])) > 0:
            return pd.DataFrame()
        rows = [
            {
                "trade_date": trade_date,
                "ts_code": "000001.SZ",
                "tenor": "14",
                "fee_rate": 7.1,
                "lent_qnt": 5000,
            }
            for trade_date in ("20240102", "20240103")
            if str(params["start_date"]) <= trade_date <= str(params["end_date"])
        ]
        return pd.DataFrame(rows)


class FakeStClient:
    def __init__(self) -> None:
        self.limits: list[int] = []

    def st(self, **params: object) -> pd.DataFrame:
        self.limits.append(int(str(params["limit"])))
        rows = [
            {
                "ts_code": f"{index:06d}.SZ",
                "pub_date": "20240101",
                "imp_date": "20240102" if index < 6 else "20250102",
                "st_type": "ST",
            }
            for index in range(8)
        ]
        offset = int(str(params["offset"]))
        limit = int(str(params["limit"]))
        return pd.DataFrame(rows[offset : offset + limit])


class FailingNamechangeClient:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def namechange(self, **_params: object) -> pd.DataFrame:
        self.calls += 1
        raise self.error


def _download_options(tmp_path: Path, dataset: str) -> constraints.ConstraintDownloadOptions:
    return constraints.ConstraintDownloadOptions(
        dataset=dataset,
        out_dir=tmp_path,
        start_date="20240101",
        end_date="20241231" if dataset == "namechange" else "20240103",
        token_env="TUSHARE_TOKEN_2",
        api_url="https://proxy-a.example.com",
        page_size=5,
        request_interval_seconds=0,
    )


def test_namechange_download_paginates_and_reuses_hash_checked_part(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeNamechangeClient()
    monkeypatch.setattr(constraint_download, "get_tushare_client", lambda **_kwargs: client)

    first = constraints.download_constraint_reference(_download_options(tmp_path, "namechange"))

    frame = pd.read_parquet(first["path"])
    assert len(frame) == 6
    assert client.calls == [0, 5]
    receipt = json.loads(Path(first["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["api_url"] == "https://proxy-a.example.com"
    assert receipt["part_count"] == 1
    assert "token" not in json.dumps(receipt).lower()

    stale_part = tmp_path / "_parts/namechange/namechange_19990101_19991231.parquet"
    pd.DataFrame([{"ts_code": "999999.SZ"}]).to_parquet(stale_part, index=False)
    stale_part.with_suffix(".receipt.json").write_text(
        json.dumps(
            {
                "dataset": "namechange",
                "params": {"start_date": "19990101", "end_date": "19991231"},
                "sha256": hashlib.sha256(stale_part.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    client.calls.clear()
    second = constraints.download_constraint_reference(_download_options(tmp_path, "namechange"))
    assert second["sha256"] == first["sha256"]
    assert second["part_count"] == 1
    assert client.calls == []


def test_resume_rejects_part_without_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeNamechangeClient()
    monkeypatch.setattr(constraint_download, "get_tushare_client", lambda **_kwargs: client)
    part = tmp_path / "_parts/namechange/namechange_20240101_20241231.parquet"
    part.parent.mkdir(parents=True)
    pd.DataFrame({"ts_code": ["000001.SZ"]}).to_parquet(part, index=False)

    with pytest.raises(ValueError, match="lacks receipt"):
        constraints.download_constraint_reference(_download_options(tmp_path, "namechange"))


def test_resume_rejects_part_from_a_different_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeNamechangeClient()
    monkeypatch.setattr(constraint_download, "get_tushare_client", lambda **_kwargs: client)
    options = _download_options(tmp_path, "namechange")
    first = constraints.download_constraint_reference(options)
    receipt_path = next((tmp_path / "_parts/namechange").glob("*.receipt.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["api_url"] = "https://another-endpoint.example"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="context mismatch.*api_url"):
        constraints.download_constraint_reference(options)

    assert Path(first["path"]).is_file()


def test_constraint_download_does_not_retry_entitlement_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FailingNamechangeClient(RuntimeError("权限不足"))
    monkeypatch.setattr(constraint_download, "get_tushare_client", lambda **_kwargs: client)

    with pytest.raises(RuntimeError, match="权限不足"):
        constraints.download_constraint_reference(_download_options(tmp_path, "namechange"))

    assert client.calls == 1


@pytest.mark.parametrize(
    "transient_error",
    [
        TimeoutError("timed out"),
        RuntimeError(
            "SSLError: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"
        ),
    ],
    ids=("timeout", "ssl_eof"),
)
def test_constraint_download_retries_transient_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transient_error: Exception,
) -> None:
    client = FakeNamechangeClient()
    original = client.namechange
    calls = 0

    def flaky_namechange(
        *,
        start_date: str,
        end_date: str,
        offset: int,
        limit: int,
    ) -> pd.DataFrame:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise transient_error
        return original(start_date=start_date, end_date=end_date, offset=offset, limit=limit)

    client.namechange = flaky_namechange
    monkeypatch.setattr(constraint_download, "get_tushare_client", lambda **_kwargs: client)
    monkeypatch.setattr(
        "market_data_platform.providers.tushare_constraint_io.time.sleep", lambda _: None
    )

    summary = constraints.download_constraint_reference(_download_options(tmp_path, "namechange"))

    assert summary["rows"] == 6
    assert calls == 3


def test_margin_secs_downloads_one_resumable_part_per_trade_date(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        constraint_download,
        "get_tushare_client",
        lambda **_kwargs: FakeMarginClient(),
    )

    summary = constraints.download_constraint_reference(_download_options(tmp_path, "margin_secs"))

    frame = pd.read_parquet(summary["path"])
    assert frame["trade_date"].tolist() == ["20240102", "20240103"]
    assert summary["part_count"] == 2
    assert summary["semantics"] == "borrow_qualification_upper_bound_not_inventory"
    assert len(list((tmp_path / "_parts/margin_secs").glob("*.receipt.json"))) == 2


@pytest.mark.parametrize(
    ("dataset", "semantics"),
    [
        ("margin_detail", "reported_margin_and_securities_lending_activity_not_inventory"),
        ("suspend_d", "explicit_exchange_suspension_events"),
        ("slb_sec_detail", "reported_securities_lending_transactions_not_borrow_availability"),
    ],
)
def test_trade_date_constraint_downloads_are_restartable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dataset: str,
    semantics: str,
) -> None:
    monkeypatch.setattr(
        constraint_download,
        "get_tushare_client",
        lambda **_kwargs: FakeConstraintClient(),
    )

    summary = constraints.download_constraint_reference(_download_options(tmp_path, dataset))

    frame = pd.read_parquet(summary["path"])
    assert frame["trade_date"].tolist() == ["20240102", "20240103"]
    assert summary["part_count"] == (2 if dataset == "margin_detail" else 1)
    assert summary["semantics"] == semantics


def test_st_download_paginates_globally_and_filters_effective_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeStClient()
    monkeypatch.setattr(constraint_download, "get_tushare_client", lambda **_kwargs: client)
    options = constraints.ConstraintDownloadOptions(
        dataset="st",
        out_dir=tmp_path,
        start_date="20240101",
        end_date="20241231",
        token_env="TUSHARE_TOKEN_2",
        api_url="https://proxy-a.example.com",
        page_size=5,
        request_interval_seconds=0,
    )

    summary = constraints.download_constraint_reference(options)

    frame = pd.read_parquet(summary["path"])
    assert len(frame) == 6
    assert summary["part_count"] == 1
    assert summary["semantics"].startswith("provider_st_change_events")
    assert client.limits == [5, 5]


def test_st_download_enforces_provider_page_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeStClient()
    monkeypatch.setattr(constraint_download, "get_tushare_client", lambda **_kwargs: client)
    options = constraints.ConstraintDownloadOptions(
        dataset="st",
        out_dir=tmp_path,
        start_date="20240101",
        end_date="20241231",
        page_size=5000,
        request_interval_seconds=0,
    )

    constraints.download_constraint_reference(options)

    assert client.limits == [1000]
    receipt = json.loads((tmp_path / "_parts/st/st_all.receipt.json").read_text(encoding="utf-8"))
    assert receipt["page_size"] == 1000


def test_publish_constraint_assets_writes_immutable_versions(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for dataset in constraints.CONSTRAINT_PUBLISH_DATASETS:
        pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20240102"}]).to_parquet(
            source / f"{dataset}.parquet",
            index=False,
        )
    for dataset in ("namechange", "margin_secs"):
        path = source / f"{dataset}.parquet"
        (source / f"{dataset}.receipt.json").write_text(
            json.dumps(
                {
                    "schema_version": "source.v1",
                    "quality_status": "complete",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
    (source / "st_history_reconstructed.receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "st.v1",
                "quality_status": "complete",
                "pit_class": "reconstructed_pit",
                "revision_safe": False,
                "history_sha256": hashlib.sha256(
                    (source / "st_history_reconstructed.parquet").read_bytes()
                ).hexdigest(),
                "intervals_sha256": hashlib.sha256(
                    (source / "st_intervals_reconstructed.parquet").read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    summary = constraints.publish_constraint_assets(tmp_path / "lake", source, "20260802")

    assert len(summary["published"]) == 4
    for item in summary["published"]:
        path = Path(item["path"])
        assert path.is_file()
        assert Path(item["version_path"]).is_file()
        assert item["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert item["source_quality_status"] == "complete"

    pd.DataFrame([{"ts_code": "000002.SZ"}]).to_parquet(
        source / "namechange.parquet",
        index=False,
    )
    changed_namechange = source / "namechange.parquet"
    (source / "namechange.receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "source.v1",
                "quality_status": "complete",
                "sha256": hashlib.sha256(changed_namechange.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FileExistsError, match="immutable reference version"):
        constraints.publish_constraint_assets(tmp_path / "lake", source, "20260802")
