"""Formal retrospective coverage receipts fail closed at the consumer boundary."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/operations/historical_minute_coverage_receipts.py"


def module():
    assert SCRIPT.exists(), "formal coverage receipts not implemented"
    spec = importlib.util.spec_from_file_location("formal_receipts", SCRIPT)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def evidence(tmp_path):
    from quant_market_data_platform.providers._a_share_mins_partition import (
        _atomic_write_partition,
        _partition_state,
    )

    date = "20200102"
    times = pd.date_range("2020-01-02 09:30", "2020-01-02 11:30", freq="min").append(
        pd.date_range("2020-01-02 13:01", "2020-01-02 15:00", freq="min")
    )
    frame = pd.DataFrame(
        {
            "ts_code": "000001.SZ",
            "trade_time": times,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "vol": 100.0,
            "amount": 1000.0,
        }
    )
    minute = tmp_path / "minute"
    minute.mkdir()
    part = _atomic_write_partition(frame, minute, trade_date=date)
    sidecar = minute / "_minute_mirror.json"
    sidecar.write_text(
        json.dumps({"trade_date": date, "partition": _partition_state(minute, trade_date=date)})
    )
    daily = tmp_path / "daily.parquet"
    basic = tmp_path / "basic.parquet"
    ref = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [date]})
    ref.to_parquet(daily)
    ref.to_parquet(basic)
    paths = {"part": part, "sidecar": sidecar, "daily": daily, "basic": basic}
    for status in "LDPG":
        stock = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "list_status": ["L"],
                "list_date": ["19910101"],
                "delist_date": [""],
                "curr_type": ["CNY"],
            }
        )
        path = tmp_path / f"{status}.parquet"
        stock.loc[stock.list_status.eq(status)].to_parquet(path)
        paths[status] = path
    for key, data in [("policy", {"exceptions": []}), ("quarantine", {"dates": {}})]:
        paths[key] = tmp_path / f"{key}.json"
        paths[key].write_text(json.dumps(data))
    row = {
        "date": date,
        "quarantined": False,
        "sidecar": "sidecar",
        "partitions": ["part"],
        "daily": ["daily"],
        "daily_basic": ["basic"],
        "stocks": dict(zip("LDPG", "LDPG", strict=True)),
        "policy": "policy",
        "candidate_sidecars": ["sidecar"],
        "prior_audit": [],
    }
    return {"dates": [row], "quarantine": "quarantine", "resolutions": {}}, paths


def test_clean_receipt_has_truthful_scope_and_no_publication(evidence):
    manifest, paths = evidence
    result = module().evaluate(manifest, paths)[0]
    assert result["status"] == "passed_retrospective_independent_coverage"
    assert result["historical_pit_certified"] is False
    assert result["full_market_promotion_allowed"] is False
    assert result["cross_source_acceptance"] == "not_evaluated_requires_0.99_gate"
    assert result["original_producer_revision"] == "unknown"


def test_quarantine_registry_overrides_false_manifest_flag(evidence):
    manifest, paths = evidence
    paths["quarantine"].write_text(json.dumps({"dates": {"20200102": {"status": "quarantined"}}}))
    result = module().evaluate(manifest, paths)[0]
    assert result["status"] == "unresolved"
    assert "quarantined" in result["unresolved"]


def test_reused_minute_as_independent_reference_rejected(evidence):
    manifest, paths = evidence
    paths["daily"] = paths["part"]
    with pytest.raises(ValueError, match="independent"):
        module().evaluate(manifest, paths)


def test_duplicate_without_explicit_resolution_stays_unresolved(evidence):
    manifest, paths = evidence
    manifest["dates"][0]["partitions"].append("part")
    result = module().evaluate(manifest, paths)[0]
    assert result["status"] == "unresolved"


def test_consumer_rejects_wrong_receipt_hash_before_reading(tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}")
    with pytest.raises(ValueError, match="receipt hash"):
        module().validate_receipt(receipt, "0" * 64, "20200102", tmp_path / "minute")


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "record",
        "input",
        "partition",
        "missing_date",
        "promotion",
        "revision",
        "auditor_field",
    ],
)
def test_consumer_replays_real_inputs_and_enforces_scope(evidence, tmp_path, monkeypatch, mutation):
    tool = module()
    monkeypatch.setattr(tool, "REPO_ROOT", auditor_repo(tmp_path))
    manifest, paths = evidence
    manifest["inputs"] = {key: tool.pilot.bind(path) for key, path in paths.items()}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    receipt_path = tmp_path / "receipt.json"
    tool.issue(manifest_path, receipt_path)
    date, part = "20200102", paths["part"]
    report = json.loads(receipt_path.read_text())
    if mutation == "revision":
        report["auditor"]["revision"] = "not-a-commit"
    elif mutation == "auditor_field":
        report["auditor"]["unexpected_claim"] = True
    elif mutation == "record":
        report["records"][0]["symbols"] += 1
    elif mutation == "promotion":
        report["full_market_promotion_allowed"] = True
    elif mutation == "input":
        paths["policy"].write_text('{"exceptions": [], "modified": true}')
    elif mutation == "partition":
        part = tmp_path / "other.parquet"
        part.write_bytes(paths["part"].read_bytes())
    elif mutation == "missing_date":
        date = "20200103"
    receipt_path.write_text(json.dumps(report))
    sha = tool.pilot.bind(receipt_path)["sha256"]
    if mutation == "none":
        assert (
            tool.validate_receipt(receipt_path, sha, date, part)["status"]
            == "passed_retrospective_independent_coverage"
        )
    else:
        with pytest.raises(ValueError):
            tool.validate_receipt(receipt_path, sha, date, part)


def duplicate_evidence(evidence, tmp_path):
    import shutil

    manifest, paths = evidence
    other = tmp_path / "old"
    other.mkdir()
    paths["other"] = other / "part-00000.parquet"
    paths["other_sidecar"] = other / "_minute_mirror.json"
    shutil.copy2(paths["part"], paths["other"])
    shutil.copy2(paths["sidecar"], paths["other_sidecar"])
    row = manifest["dates"][0]
    row["partitions"].append("other")
    row["candidate_sidecars"].append("other_sidecar")
    tool = module()
    audit = {
        "trade_date": row["date"],
        "path": str(paths["part"]),
        "sidecar": str(paths["sidecar"]),
        "partition_sha256": tool.pilot.bind(paths["part"])["sha256"],
        "sidecar_sha256": tool.pilot.bind(paths["sidecar"])["sha256"],
        "binding_matches": True,
        "status": "passed_requested_universe_only",
        "issues": {"duplicate_rows": 0},
    }
    paths["audit"] = tmp_path / "audit.json"
    paths["audit"].write_text(json.dumps({"partitions": [audit]}))
    row["prior_audit"] = ["audit"]
    manifest["resolutions"][row["date"]] = {
        "kind": "identical_content",
        "selected": "part",
        "audit": "audit",
    }
    return manifest, paths


def test_explicit_identical_resolution_retains_both_paths(evidence, tmp_path):
    manifest, paths = duplicate_evidence(evidence, tmp_path)
    result = module().evaluate(manifest, paths)[0]
    assert result["status"] == "passed_retrospective_independent_coverage"
    assert result["candidate_resolution"]["kind"] == "identical_content"
    assert len(result["candidate_resolution"]["retained_candidates"]) == 2
    assert paths["other"].exists()


def three_candidates(evidence, tmp_path):
    import shutil

    manifest, paths = duplicate_evidence(evidence, tmp_path)
    third = tmp_path / "third"
    third.mkdir()
    paths["third"] = third / "part-00000.parquet"
    paths["third_sidecar"] = third / "_minute_mirror.json"
    shutil.copy2(paths["part"], paths["third"])
    shutil.copy2(paths["sidecar"], paths["third_sidecar"])
    manifest["dates"][0]["partitions"].append("third")
    manifest["dates"][0]["candidate_sidecars"].append("third_sidecar")
    return manifest, paths


def test_three_identical_candidates_are_all_retained_and_consumer_replayed(
    evidence, tmp_path, monkeypatch
):
    tool = module()
    monkeypatch.setattr(tool, "REPO_ROOT", auditor_repo(tmp_path))
    manifest, paths = three_candidates(evidence, tmp_path)
    manifest["inputs"] = {key: tool.pilot.bind(path) for key, path in paths.items()}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    receipt = tmp_path / "receipt.json"
    tool.issue(manifest_path, receipt)
    row = tool.validate_receipt(
        receipt, tool.pilot.bind(receipt)["sha256"], "20200102", paths["part"]
    )
    retained = row["candidate_resolution"]["retained_candidates"]
    assert [r["path"] for r in retained] == [str(paths[key]) for key in ("part", "other", "third")]
    assert row["status"] == "passed_retrospective_independent_coverage"
    assert row["full_market_promotion_allowed"] is False
    paths["third_sidecar"].write_text("{}")
    with pytest.raises(ValueError, match="Input changed"):
        tool.validate_receipt(
            receipt, tool.pilot.bind(receipt)["sha256"], "20200102", paths["part"]
        )


@pytest.mark.parametrize(
    "corruption",
    ["different_content", "binding", "same_path", "missing_sidecar", "four_candidates", "patch"],
)
def test_three_candidates_cannot_hide_unverified_inputs(evidence, tmp_path, corruption):
    from quant_market_data_platform.providers._a_share_mins_partition import _partition_state

    manifest, paths = three_candidates(evidence, tmp_path)
    row = manifest["dates"][0]
    if corruption == "different_content":
        frame = pd.read_parquet(paths["third"])
        frame.loc[0, "vol"] = 99.0
        frame.to_parquet(paths["third"])
        paths["third_sidecar"].write_text(
            json.dumps(
                {
                    "trade_date": row["date"],
                    "partition": _partition_state(paths["third"].parent, trade_date=row["date"]),
                }
            )
        )
    elif corruption == "binding":
        paths["third_sidecar"].write_text(json.dumps({"trade_date": row["date"], "partition": {}}))
    elif corruption == "same_path":
        paths["third"] = paths["other"]
    elif corruption == "missing_sidecar":
        row["candidate_sidecars"].pop()
    elif corruption == "four_candidates":
        row["partitions"].append("part")
        row["candidate_sidecars"].append("sidecar")
    else:
        manifest["resolutions"][row["date"]]["kind"] = "audited_additive_patch"
    with pytest.raises(ValueError):
        module().evaluate(manifest, paths)


@pytest.mark.parametrize("corruption", ["hash", "content", "missing_audit", "false_audit"])
def test_resolution_never_hides_unverified_candidate(evidence, tmp_path, corruption):
    manifest, paths = duplicate_evidence(evidence, tmp_path)
    if corruption == "hash":
        audit = json.loads(paths["audit"].read_text())
        audit["partitions"][0]["partition_sha256"] = "0" * 64
        paths["audit"].write_text(json.dumps(audit))
    elif corruption == "content":
        frame = pd.read_parquet(paths["other"])
        frame.loc[0, "close"] = 9.0
        frame.to_parquet(paths["other"])
    elif corruption == "missing_audit":
        manifest["dates"][0]["prior_audit"] = []
    else:
        audit = json.loads(paths["audit"].read_text())
        audit["partitions"][0]["binding_matches"] = False
        paths["audit"].write_text(json.dumps(audit))
    with pytest.raises(ValueError):
        module().evaluate(manifest, paths)


@pytest.mark.parametrize(
    "corruption", ["none", "patch_hash", "common_rows", "patch_rows", "build_hash"]
)
def test_audited_patch_requires_exact_union_and_lineage(evidence, tmp_path, corruption):
    from quant_market_data_platform.providers._a_share_mins_partition import (
        _atomic_write_partition,
        _partition_state,
    )

    manifest, paths = duplicate_evidence(evidence, tmp_path)
    tool = module()
    row = manifest["dates"][0]
    patch = pd.read_parquet(paths["part"]).assign(ts_code="000002.SZ")
    paths["patch"] = tmp_path / "patch.parquet"
    patch.to_parquet(paths["patch"])
    combined = pd.concat([pd.read_parquet(paths["other"]), patch], ignore_index=True)
    if corruption == "common_rows":
        combined.loc[0, "close"] = 9.0
    _atomic_write_partition(combined, paths["part"].parent, trade_date=row["date"])
    paths["sidecar"].write_text(
        json.dumps(
            {
                "trade_date": row["date"],
                "partition": _partition_state(paths["part"].parent, trade_date=row["date"]),
            }
        )
    )
    for key in ["daily", "basic"]:
        pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"], "trade_date": row["date"]}).to_parquet(
            paths[key]
        )
    stock = pd.read_parquet(paths["L"])
    pd.concat([stock, stock.assign(ts_code="000002.SZ")], ignore_index=True).to_parquet(paths["L"])
    audit = json.loads(paths["audit"].read_text())
    audit["partitions"][0].update(
        partition_sha256=tool.pilot.bind(paths["part"])["sha256"],
        sidecar_sha256=tool.pilot.bind(paths["sidecar"])["sha256"],
    )
    paths["audit"].write_text(json.dumps(audit))
    source_sha = tool.pilot.bind(paths["other_sidecar"])["sha256"]
    patch_sha = tool.pilot.bind(paths["patch"])["sha256"]
    acquisition = {
        "date": row["date"],
        "source_sidecar": str(paths["other_sidecar"]),
        "source_sha256": source_sha,
        "code": "000002.SZ",
        "status": "patch_ready",
        "rows": 241,
        "patch": str(paths["patch"]),
        "patch_sha256": patch_sha,
    }
    build = {
        "trade_date": row["date"],
        "source_sidecar": str(paths["other_sidecar"]),
        "source_sidecar_sha256": source_sha,
        "patch": str(paths["patch"]),
        "patch_sha256": patch_sha,
        "status": "audited_candidate_ready",
        "candidate_sidecar_sha256": tool.pilot.bind(paths["sidecar"])["sha256"],
    }
    if corruption == "patch_hash":
        acquisition["patch_sha256"] = "0" * 64
    if corruption == "build_hash":
        build["candidate_sidecar_sha256"] = "0" * 64
    if corruption == "patch_rows":
        patch.iloc[:240].to_parquet(paths["patch"])
    for key, payload in [
        ("acquisition", {"records": [acquisition]}),
        ("build", {"candidates": [build]}),
    ]:
        paths[key] = tmp_path / f"{key}.json"
        paths[key].write_text(json.dumps(payload))
    manifest["resolutions"][row["date"]].update(
        kind="audited_additive_patch", patch="patch", acquisition="acquisition", build="build"
    )
    if corruption == "none":
        result = tool.evaluate(manifest, paths)[0]
        assert result["status"] == "passed_retrospective_independent_coverage"
        assert result["candidate_resolution"]["added_rows"] == 241
        assert result["candidate_resolution"]["added_symbols"] == ["000002.SZ"]
    else:
        with pytest.raises(ValueError):
            tool.evaluate(manifest, paths)


def test_bound_rejects_oversized_input_before_hash_scan(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"inputs": {"huge": {"bytes": 3 * 1024**3}}}))
    with pytest.raises(ValueError, match="2 GiB"):
        module().load_inputs(manifest)


def test_bound_rejects_more_than_ten_dates(evidence):
    manifest, paths = evidence
    manifest["dates"] *= 11
    with pytest.raises(ValueError, match="1–10"):
        module().evaluate(manifest, paths)


def test_partition_row_bound_fails_before_materialization(evidence, monkeypatch):
    tool = module()
    monkeypatch.setattr(tool, "MAX_PARTITION_ROWS", 240)
    manifest, paths = evidence
    with pytest.raises(ValueError, match="bounded row limit"):
        tool.evaluate(manifest, paths)


def auditor_repo(tmp_path):
    repo = tmp_path / "auditor-repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/fixture.py").write_text("VALUE = 1\n")
    (repo / "scripts/operations").mkdir(parents=True)
    (repo / "scripts/operations/fixture.py").write_text("VALUE = 2\n")
    commands = [
        ["git", "init", "-q"],
        ["git", "add", "src", "scripts"],
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
    ]
    for command in commands:
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    return repo


def test_auditor_revision_must_bind_the_claimed_source_tree(tmp_path, monkeypatch):
    tool = module()
    repo = auditor_repo(tmp_path)
    monkeypatch.setattr(tool, "REPO_ROOT", repo)
    identity = tool.source_identity()
    tool.verify_auditor(identity)
    identity["files"]["src/fixture.py"] = "0" * 64
    with pytest.raises(ValueError, match="source tree"):
        tool.verify_auditor(identity)


def test_patch_union_cannot_drop_original_columns():
    tool = module()
    old = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_time": ["2020-01-02"], "extra": [1]})
    patch = old.assign(ts_code="000002.SZ")
    selected = pd.concat([old, patch]).drop(columns="extra")
    with pytest.raises(ValueError, match="column"):
        tool.candidates.exact_union(selected, old, patch)


def test_patch_union_cannot_change_original_column_types():
    tool = module()
    old = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_time": ["2020-01-02"], "value": [1.0]})
    patch = old.assign(ts_code="000002.SZ")
    selected = pd.concat([old, patch]).astype({"value": "int64"})
    with pytest.raises(ValueError, match="exact old-plus-patch"):
        tool.candidates.exact_union(selected, old, patch)
