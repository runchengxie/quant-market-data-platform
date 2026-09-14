from __future__ import annotations

from pathlib import Path

from market_data_platform.current_path_audit import audit_current_contract_paths


def _entry(path: Path, *, end_date: str = "20260713") -> dict:
    return {
        "alias_path": str(path),
        "resolved_path": str(path.resolve(strict=False)),
        "manifest": {"query_end_date": end_date},
        "as_of": end_date,
    }


def test_current_path_audit_classifies_aliases_missing_and_scoped_date_drift(
    tmp_path: Path,
) -> None:
    daily_version = tmp_path / "a_share_all_20080102_20260707_daily"
    daily_version.mkdir()
    daily_alias = tmp_path / "a_share_all_daily_latest"
    daily_alias.symlink_to(daily_version.name)
    mutable_latest = tmp_path / "a_share_all_moneyflow_latest"
    mutable_latest.mkdir()
    direct_file = tmp_path / "a_share_trade_cal_latest.parquet"
    direct_file.write_text("calendar", encoding="utf-8")
    pit = tmp_path / "a_share_top800_20150227_20260529_pit"
    pit.mkdir()
    nested_latest = tmp_path / "moneyflow_ths_latest"
    nested_latest.mkdir()
    nested_alias = tmp_path / "moneyflow_latest"
    nested_alias.symlink_to(nested_latest.name, target_is_directory=True)

    report = audit_current_contract_paths(
        {
            "contract": {"market": "a_share", "provider": "tushare"},
            "assets": {
                "daily": _entry(daily_alias),
                "moneyflow": _entry(mutable_latest),
                "trade_cal": _entry(direct_file),
                "pit_fundamentals": _entry(pit, end_date="20260615"),
                "nested_latest": _entry(nested_alias),
                "missing": _entry(tmp_path / "a_share_missing_latest"),
            },
        }
    )

    assert report["summary"]["missing_assets"] == 1
    assert report["summary"]["date_drift_assets"] == 1
    assert report["summary"]["mutable_final_target_assets"] == 1
    assert report["assets"]["daily"]["reference_kind"] == "symlink"
    assert report["assets"]["daily"]["latest_semantics"] == "stable_symlink"
    assert report["assets"]["moneyflow"]["latest_semantics"] == "legacy_mutable_directory"
    assert report["assets"]["trade_cal"]["latest_semantics"] == "direct_file"
    assert report["assets"]["pit_fundamentals"]["issues"] == []
    assert report["assets"]["nested_latest"]["issues"][0]["check"] == (
        "symlink_to_mutable_latest_directory"
    )


def test_current_path_audit_reports_matching_version_without_drift(tmp_path: Path) -> None:
    version = tmp_path / "a_share_all_20150101_20260713_daily_clean"
    version.mkdir()
    alias = tmp_path / "a_share_all_daily_clean_latest"
    alias.symlink_to(version.name)

    report = audit_current_contract_paths(
        {
            "contract": {"market": "a_share"},
            "assets": {"daily_clean": _entry(alias)},
        }
    )

    checks = {issue["check"] for issue in report["issues"]}
    assert "resolved_name_date_drift" not in checks


def test_current_path_audit_treats_empty_paths_and_broken_symlinks_as_missing(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "daily_latest"
    broken.symlink_to("missing_version", target_is_directory=True)

    report = audit_current_contract_paths(
        {
            "assets": {
                "broken": _entry(broken),
                "empty": {"alias_path": "", "resolved_path": ""},
            }
        }
    )

    assert report["summary"]["missing_assets"] == 2
    assert report["assets"]["broken"]["reference_kind"] == "broken_symlink"
    assert report["assets"]["empty"]["reference_kind"] == "missing"


def test_current_path_audit_reports_symlink_loops_instead_of_crashing(tmp_path: Path) -> None:
    loop = tmp_path / "daily_latest"
    loop.symlink_to(loop.name, target_is_directory=True)

    report = audit_current_contract_paths(
        {
            "assets": {
                "daily": {
                    "alias_path": str(loop),
                    "resolved_path": "",
                    "manifest": {"query_end_date": "20260713"},
                }
            }
        },
    )

    assert report["summary"]["missing_assets"] == 1
    checks = {issue["check"] for issue in report["assets"]["daily"]["issues"]}
    assert checks == {"missing_current_asset", "unresolvable_current_asset"}
