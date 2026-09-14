from __future__ import annotations

from pathlib import Path

from market_data_platform.contract import describe_input_path, match_current_contract_entry


def test_match_current_contract_entry_matches_alias_or_resolved_path() -> None:
    alias = Path("/artifacts/panel_latest")
    resolved = Path("/artifacts/panel_20260904")
    contract = {
        "contract": {"name": "a_share_current"},
        "assets": {
            "panel": {
                "alias_path": str(alias),
                "resolved_path": str(resolved),
            }
        },
    }

    assert match_current_contract_entry(
        contract,
        configured_path=alias,
        resolved_path=resolved,
    ) == (
        "panel",
        {"alias_path": str(alias), "resolved_path": str(resolved)},
    )


def test_match_current_contract_entry_returns_none_for_unmatched_paths() -> None:
    assert (
        match_current_contract_entry(
            {"assets": {"panel": {"resolved_path": "/artifacts/other"}}},
            configured_path=Path("/artifacts/panel"),
            resolved_path=Path("/artifacts/panel"),
        )
        is None
    )


def test_describe_input_path_returns_resolution_and_contract_reference(tmp_path):
    snapshot = tmp_path / "assets" / "daily_20260522"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.yml").write_text(
        "dataset: daily\nprovider: tushare\n",
        encoding="utf-8",
    )
    alias = tmp_path / "assets" / "daily_latest"
    alias.symlink_to(snapshot, target_is_directory=True)
    contract_path = tmp_path / "metadata" / "current_assets" / "a_share_current.json"
    contract = {
        "contract": {"name": "a_share_current"},
        "assets": {
            "daily": {
                "alias_path": str(alias),
                "resolved_path": str(snapshot.resolve()),
                "manifest_path": str((snapshot / "manifest.yml").resolve()),
            }
        },
    }

    result = describe_input_path(
        alias,
        current_contract=contract,
        current_contract_path=contract_path,
    )

    assert result is not None
    assert result["configured_path"] == str(alias)
    assert result["resolved_path"] == str(snapshot.resolve())
    assert result["path_kind"] == "directory"
    assert result["points_to_latest_name"] is True
    assert result["manifest"]["provider"] == "tushare"
    assert result["current_contract"]["asset_key"] == "daily"
