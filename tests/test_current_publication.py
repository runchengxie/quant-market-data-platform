"""Publication races and failure recovery use synthetic, credential-free assets."""

import hashlib
import json
import multiprocessing

import pytest

from quant_market_data_platform import registry
from quant_market_data_platform.contract import build_current_contract, write_current_contract
from quant_market_data_platform.current_publication import commit_current, sync_minute_catalog
from quant_market_data_platform.publication_lock import publication_lock
from quant_market_data_platform.tushare_minute_operational import promote_operational_alias


def _version(root, date, base=None):
    parent = root / "assets/derived/a_share"
    version = parent / f"minute_1m_tushare_v1_{date}"
    version.mkdir(parents=True)
    receipt = version / "_operational_receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "a_share.minute_tushare_operational_version.v1",
                "status": "published_operational_version",
                "output_dir": str(version),
                "daily": [],
                "policy": {},
                "summary": {"date_max": date},
                "inputs": {
                    "base_receipt_sha256": hashlib.sha256(base.read_bytes()).hexdigest()
                    if base
                    else None
                },
            }
        )
    )
    legacy = parent / "minute_1m"
    legacy.mkdir(exist_ok=True)
    return receipt


def _promote(root, receipt):
    parent = receipt.parent.parent
    return promote_operational_alias(
        receipt, parent / "minute_1m_tushare", parent / "minute_1m", root / "promotion.json"
    )


def _contract(root):
    return root / "metadata/current_assets/a_share_current.json"


def test_minute_merges_latest_contract_and_preserves_context(tmp_path):
    payload = build_current_contract(tmp_path, target_date="20260101")
    payload["assets"]["other_publisher"] = {"retained": True}
    write_current_contract(_contract(tmp_path), payload)
    context = build_current_contract(tmp_path, market="cn_context", provider="composite")
    write_current_contract(tmp_path / "metadata/current_assets/cn_context_current.json", context)
    first = _version(tmp_path, "20260710")
    _promote(tmp_path, first)
    saved = json.loads(_contract(tmp_path).read_text())
    assert saved["contract"]["target_date"] == "20260101"
    assert saved["assets"]["other_publisher"] == {"retained": True}
    assert saved["assets"]["minute_1m_tushare"]["as_of"] == "20260710"
    assert "cn_context" in (tmp_path / "metadata/dataset_registry.csv").read_text()


def test_registry_failure_marks_pending_and_same_version_retry_skips_copy(tmp_path, monkeypatch):
    receipt = _version(tmp_path, "20260710")
    original = registry.write_combined_dataset_registry

    def fail(*args):
        raise OSError("registry unavailable")

    monkeypatch.setattr(registry, "write_combined_dataset_registry", fail)
    with pytest.raises(OSError, match="registry unavailable"):
        _promote(tmp_path, receipt)
    assert json.loads((tmp_path / "promotion.json").read_text())["status"] == "metadata_pending"
    pending = tmp_path / "metadata/current_assets/.registry-pending.json"
    assert pending.exists()
    monkeypatch.setattr(registry, "write_combined_dataset_registry", original)
    from quant_market_data_platform import tushare_minute_operational

    def forbidden(*args):
        pytest.fail("same-version retry must not copy data")

    monkeypatch.setattr(tushare_minute_operational, "_replace_operational_alias", forbidden)
    assert _promote(tmp_path, receipt)["status"] == "operational_canonical"
    assert not pending.exists()
    assert "minute_1m_tushare" in (tmp_path / "metadata/dataset_registry.csv").read_text()


def test_stale_version_rejected_without_metadata_changes(tmp_path):
    first = _version(tmp_path, "20260710")
    _promote(tmp_path, first)
    second = _version(tmp_path, "20260711", first)
    _promote(tmp_path, second)
    before = _contract(tmp_path).read_bytes()
    with pytest.raises(RuntimeError, match="stale base|older version"):
        _promote(tmp_path, first)
    assert _contract(tmp_path).read_bytes() == before


def _busy_writer(root, queue):
    try:
        registry.write_combined_dataset_registry(root / "metadata/dataset_registry.csv", [])
    except RuntimeError as exc:
        queue.put(str(exc))
    else:
        queue.put("unexpected success")


def test_separate_process_writer_cannot_bypass_root_lock(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    with publication_lock(tmp_path):
        child = ctx.Process(target=_busy_writer, args=(tmp_path, queue))
        child.start()
        child.join(10)
        assert not child.is_alive()
        assert "Publication lock busy" in queue.get(timeout=2)
    assert not (tmp_path / "metadata/dataset_registry.csv").exists()


def test_symlink_lock_rejected(tmp_path):
    directory = tmp_path / "metadata/current_assets"
    directory.mkdir(parents=True)
    (directory / ".publication.lock").symlink_to(tmp_path / "other")
    with pytest.raises(OSError):
        with publication_lock(tmp_path):
            pytest.fail("symlink lock accepted")
    assert not (tmp_path / "other").exists()


def _rebuild_after_signal(root, ready, go):
    ready.set()
    assert go.wait(10)
    with publication_lock(root):
        commit_current(root, _contract(root), build_current_contract(root))


def test_full_rebuild_after_minute_commit_reads_current_alias(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, go = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_rebuild_after_signal, args=(tmp_path, ready, go))
    child.start()
    assert ready.wait(10)
    _promote(tmp_path, _version(tmp_path, "20260710"))
    go.set()
    child.join(10)
    assert child.exitcode == 0
    assert (
        json.loads(_contract(tmp_path).read_text())["assets"]["minute_1m_tushare"]["as_of"]
        == "20260710"
    )


def test_noop_sync_reads_alias_under_lock(tmp_path):
    receipt = _version(tmp_path, "20260710")
    _promote(tmp_path, receipt)
    alias = receipt.parent.parent / "minute_1m_tushare"
    sync_minute_catalog(tmp_path, alias)
    assert (
        json.loads(_contract(tmp_path).read_text())["assets"]["minute_1m_tushare"]["as_of"]
        == "20260710"
    )


def test_daily_noop_completes_pending_receipt_without_download(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quant_market_data_platform.tushare_minute_operational_daily import (
        OperationalDailyOptions,
        run_operational_daily,
    )

    receipt = _version(tmp_path, "20260710")
    _promote(tmp_path, receipt)
    pending = json.loads((tmp_path / "promotion.json").read_text())
    pending["status"] = "metadata_pending"
    path = tmp_path / "metadata/minute_operational/promotions/minute_1m_tushare_v1_20260710.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(pending))
    calendar = tmp_path / "assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet"
    calendar.parent.mkdir(parents=True)
    pq.write_table(pa.table({"cal_date": ["20260710"], "is_open": [1]}), calendar)
    result = run_operational_daily(
        OperationalDailyOptions(artifacts_root=tmp_path, target_date="20260710")
    )
    assert result["status"] == "noop"
    assert json.loads(path.read_text())["status"] == "operational_canonical"


@pytest.mark.parametrize("dry_run", [False, True])
def test_explicit_registry_input_requires_independent_output(tmp_path, dry_run):
    from argparse import Namespace

    from quant_market_data_platform.cli_contract import handle_registry_build

    args = Namespace(
        artifacts_root=tmp_path, out=None, market="all", contract="other.json", dry_run=dry_run
    )
    with pytest.raises(ValueError, match="require --out"):
        handle_registry_build(args)
    assert not (tmp_path / "metadata").exists()


def test_custom_contract_output_does_not_write_shared_catalog(tmp_path):
    from argparse import Namespace

    from quant_market_data_platform.cli_contract import handle_contract_build

    root = tmp_path / "artifacts"
    output = tmp_path / "export/current.json"
    args = Namespace(
        artifacts_root=root,
        out=str(output),
        market="a_share",
        provider="tushare",
        registry_out=None,
        no_registry=False,
        generated_by="test",
        target_date=None,
        dry_run=False,
    )
    assert handle_contract_build(args) == 0
    assert output.exists()
    assert (output.parent / "dataset_registry.csv").exists()
    assert not (root / "metadata").exists()


def test_contract_write_failure_preserves_old_complete_json(tmp_path, monkeypatch):
    from quant_market_data_platform import publication_lock as locking

    path = _contract(tmp_path)
    write_current_contract(path, {"old": True})
    original = locking.os.replace

    def fail(source, destination):
        if destination == path:
            raise OSError("replace failed")
        return original(source, destination)

    monkeypatch.setattr(locking.os, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        write_current_contract(path, {"new": True})
    assert json.loads(path.read_text()) == {"old": True}
    assert not list(path.parent.glob("*.tmp"))


def test_atomic_writes_never_expose_partial_json_to_reader(tmp_path):
    import threading

    path = _contract(tmp_path)
    write_current_contract(path, {"index": -1, "large": "x" * 10000})
    stopped = threading.Event()
    failures = []

    def reader():
        while not stopped.is_set():
            try:
                value = json.loads(path.read_text())
                assert len(value["large"]) == 10000
            except Exception as exc:
                failures.append(exc)
                break

    worker = threading.Thread(target=reader)
    worker.start()
    try:
        for index in range(30):
            write_current_contract(path, {"index": index, "large": "x" * 10000})
    finally:
        stopped.set()
        worker.join(10)
    assert not failures
    assert not worker.is_alive()


def test_invalid_alias_rejected_before_staging_or_pending(tmp_path, monkeypatch):
    from quant_market_data_platform import minute_publication

    receipt = _version(tmp_path, "20260710")

    def forbidden(*args, **kwargs):
        pytest.fail("invalid alias must not copy data")

    monkeypatch.setattr(minute_publication.shutil, "copytree", forbidden)
    with pytest.raises(RuntimeError, match="Invalid operational alias"):
        promote_operational_alias(
            receipt,
            receipt.parent.parent / "wrong",
            receipt.parent.parent / "minute_1m",
            tmp_path / "promotion.json",
        )
    assert not (tmp_path / "promotion.json").exists()
    assert not (tmp_path / "metadata").exists()


def test_staged_receipt_change_refused_before_alias_replacement(tmp_path, monkeypatch):
    from quant_market_data_platform import minute_publication

    first = _version(tmp_path, "20260710")
    _promote(tmp_path, first)
    next_version = _version(tmp_path, "20260711", first)
    before = _contract(tmp_path).read_bytes()
    copy = minute_publication.shutil.copytree

    def changed(source, destination, **kwargs):
        result = copy(source, destination, **kwargs)
        (destination / "_operational_receipt.json").write_text("changed source receipt")
        return result

    monkeypatch.setattr(minute_publication.shutil, "copytree", changed)
    with pytest.raises(RuntimeError, match="Staged operational receipt changed"):
        _promote(tmp_path, next_version)
    assert _contract(tmp_path).read_bytes() == before
    assert (
        first.parent.parent / "minute_1m_tushare/_operational_receipt.json"
    ).read_bytes() == first.read_bytes()


def test_legacy_alias_race_does_not_mutate_minute_alias(tmp_path, monkeypatch):
    from quant_market_data_platform import minute_publication

    first = _version(tmp_path, "20260710")
    _promote(tmp_path, first)
    next_version = _version(tmp_path, "20260711", first)
    legacy = first.parent.parent / "minute_1m"
    other = first.parent.parent / "other_legacy"
    other.mkdir()
    copy = minute_publication.shutil.copytree

    def changed(source, destination, **kwargs):
        result = copy(source, destination, **kwargs)
        legacy.rmdir()
        legacy.symlink_to(other.name, target_is_directory=True)
        return result

    monkeypatch.setattr(minute_publication.shutil, "copytree", changed)
    with pytest.raises(RuntimeError, match="Legacy alias changed"):
        _promote(tmp_path, next_version)
    assert (
        first.parent.parent / "minute_1m_tushare/_operational_receipt.json"
    ).read_bytes() == first.read_bytes()


def test_alias_cas_rechecked_after_staging(tmp_path, monkeypatch):
    from quant_market_data_platform import minute_publication

    first = _version(tmp_path, "20260710")
    _promote(tmp_path, first)
    next_version = _version(tmp_path, "20260711", first)
    before = _contract(tmp_path).read_bytes()
    copy = minute_publication.shutil.copytree
    alias_receipt = first.parent.parent / "minute_1m_tushare/_operational_receipt.json"

    def changed(source, destination, **kwargs):
        result = copy(source, destination, **kwargs)
        alias_receipt.write_text("concurrent publisher")
        return result

    monkeypatch.setattr(minute_publication.shutil, "copytree", changed)
    with pytest.raises(RuntimeError, match="alias changed"):
        _promote(tmp_path, next_version)
    assert alias_receipt.read_text() == "concurrent publisher"
    assert _contract(tmp_path).read_bytes() == before


def test_retired_market_contract_is_not_reactivated(tmp_path):
    from quant_market_data_platform.current_publication import current_contracts

    write_current_contract(_contract(tmp_path), build_current_contract(tmp_path))
    historical = tmp_path / "metadata/current_assets/hk_current.json"
    historical.write_text(json.dumps({"contract": {"market": "hk"}, "assets": {}}))
    assert [entry["contract"]["market"] for entry in current_contracts(tmp_path)] == ["a_share"]
