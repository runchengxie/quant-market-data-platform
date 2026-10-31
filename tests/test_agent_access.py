"""Agent summaries must not turn absent quality evidence into a pass."""

from __future__ import annotations

import json

from test_published_asset_snapshot import fixture

from quant_market_data_platform.cli import main


def test_capabilities_require_no_config_or_data(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["capabilities"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "quant.agent_capabilities.v1"
    assert {row["id"] for row in payload["operations"]} == {
        "contract.inspect",
        "contract.capture",
        "contract.assess",
    }
    assert all(row["side_effects"] == [] for row in payload["operations"])
    assert not list(tmp_path.iterdir())


def test_assessment_preserves_unknown_pit_and_content_integrity(tmp_path, capsys):
    contract = fixture(tmp_path)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert (
        main(
            [
                "contract",
                "assess",
                "--artifacts-root",
                str(tmp_path),
                "--current-contract",
                str(contract.path),
                "--asset",
                "daily",
                "--target-date",
                "20261030",
                "--require-start-date",
                "20260101",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "quant.data_assessment.v1"
    checks = {row["id"]: row for row in result["checks"]}
    assert checks["daily:historical_pit"]["outcome"] == "unknown"
    assert checks["daily:data_content_integrity"]["outcome"] == "unknown"
    assert checks["daily:contiguous_coverage"]["outcome"] == "unknown"
    assert checks["daily:manifest_identity"]["outcome"] == "passed"
    assert result["snapshot"]["data_content_verified"] is False
    assert result["research_ready"] is False
    assert before == {
        str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }


def test_incomplete_manifest_is_failed_not_verified(tmp_path, capsys):
    import yaml

    contract = fixture(tmp_path)
    (tmp_path / "daily-v1" / "manifest.yml").write_text(
        yaml.safe_dump({"dataset": "daily", "status": "running"})
    )
    assert (
        main(
            [
                "contract",
                "assess",
                "--artifacts-root",
                str(tmp_path),
                "--current-contract",
                str(contract.path),
                "--asset",
                "daily",
            ]
        )
        != 0
    )


def test_freshness_is_scoped_and_does_not_certify_pit(tmp_path, capsys):
    import yaml

    contract = fixture(tmp_path)
    manifest = tmp_path / "daily-v1" / "manifest.yml"
    manifest.write_text(yaml.safe_dump({"dataset": "daily", "status": "completed"}))
    payload = json.loads(contract.path.read_text())
    payload["assets"]["daily"].update(
        {"exists": True, "as_of": "20261030", "manifest": {"status": "completed"}}
    )
    contract.path.write_text(json.dumps(payload))
    assert (
        main(
            [
                "contract",
                "assess",
                "--artifacts-root",
                str(tmp_path),
                "--current-contract",
                str(contract.path),
                "--asset",
                "daily",
                "--target-date",
                "20261030",
            ]
        )
        == 0
    )
    checks = {row["id"]: row for row in json.loads(capsys.readouterr().out)["checks"]}
    assert checks["daily:freshness"]["outcome"] == "passed"
    assert checks["daily:historical_pit"]["outcome"] == "unknown"


def test_skipped_freshness_does_not_pass_on_stale_optional_metadata(tmp_path, capsys):
    contract = fixture(tmp_path)
    payload = json.loads(contract.path.read_text())
    payload["assets"]["daily"]["as_of"] = "20200101"
    contract.path.write_text(json.dumps(payload))
    assert (
        main(
            [
                "contract",
                "assess",
                "--artifacts-root",
                str(tmp_path),
                "--current-contract",
                str(contract.path),
                "--asset",
                "daily",
                "--target-date",
                "20261030",
            ]
        )
        == 0
    )
    checks = {row["id"]: row for row in json.loads(capsys.readouterr().out)["checks"]}
    assert checks["daily:freshness"]["outcome"] == "unknown"


def test_inspect_discovery_promises_json_and_matches_cli(tmp_path, capsys):
    import shlex

    from quant_market_data_platform.agent_access import capabilities

    operation = next(row for row in capabilities()["operations"] if row["id"] == "contract.inspect")
    argv = shlex.split(operation["examples"][0])[1:]
    argv[argv.index("--artifacts-root") + 1] = str(tmp_path)
    main(argv)
    assert isinstance(json.loads(capsys.readouterr().out)["quality_checks"], list)
