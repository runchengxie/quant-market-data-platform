from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_dev_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / "dev" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


architecture_governance = _load_dev_script("architecture_governance")
compatibility_governance = _load_dev_script("compatibility_governance")
maintainability_metrics = _load_dev_script("maintainability_metrics")
quality_debt = _load_dev_script("quality_debt")
run_pytest_isolated = _load_dev_script("run_pytest_isolated")


def _command_tree(parser: argparse.ArgumentParser) -> dict[str, dict]:
    tree: dict[str, dict] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in action.choices.items():
                tree[name] = _command_tree(cast(argparse.ArgumentParser, subparser))
    return tree


def _leaf_commands(tree: dict[str, dict], prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    leaves: list[tuple[str, ...]] = []
    for name, subtree in sorted(tree.items()):
        path = prefix + (name,)
        if subtree:
            leaves.extend(_leaf_commands(subtree, path))
        else:
            leaves.append(path)
    return leaves


def _active_markdown_docs(root: Path) -> list[Path]:
    docs_root = root / "docs"
    return [
        root / "README.md",
        root / "AGENTS.md",
        *[
            path
            for path in sorted(docs_root.rglob("*.md"))
            if not {"archive", "superpowers"}.intersection(path.relative_to(docs_root).parts)
        ],
    ]


def test_quality_coverage_report_uses_configured_excludes(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "included.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "excluded.py").write_text("x = 1\nx = 2\n", encoding="utf-8")
    (tmp_path / "src" / "pkg" / "typed_out.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "\n".join(
            [
                "[tool.ruff]",
                'extend-exclude = ["src/excluded.py"]',
                "[tool.ty.src]",
                'include = ["src"]',
                'exclude = ["src/pkg"]',
            ]
        ),
        encoding="utf-8",
    )

    report = quality_debt._coverage_report(tmp_path)

    assert report["tools"]["ruff"]["included_files"] == 2
    assert report["tools"]["ruff"]["excluded_lines"] == 2
    assert report["tools"]["ty"]["included_files"] == 2
    assert report["tools"]["ty"]["included_patterns"] == ["src"]
    assert report["tools"]["ty"]["excluded_patterns"] == ["src/pkg"]


def test_quality_json_output_shape(capsys) -> None:
    assert quality_debt.main(["--json", "--skip-ruff"]) == 0

    payload = json.loads(capsys.readouterr().out)

    assert payload["source_root"] == "src"
    assert {"ruff", "ty"} == set(payload["tools"])
    assert "included_lines" in payload["tools"]["ruff"]


def test_quality_baseline_flags_new_excludes() -> None:
    report = quality_debt._coverage_report()
    baseline = json.loads(json.dumps(report))
    report["tools"]["ruff"]["excluded_patterns"].append("src/new_excluded.py")

    issues = quality_debt._coverage_issues(report, baseline)

    assert any("new excludes" in issue for issue in issues)


def test_quality_baseline_flags_protected_excludes_even_when_baselined() -> None:
    report = quality_debt._coverage_report()
    baseline = json.loads(json.dumps(report))
    protected = quality_debt.PROTECTED_INCLUDED_PATHS[0]
    report["tools"]["ty"]["excluded_patterns"].append(protected)
    baseline["tools"]["ty"]["excluded_patterns"].append(protected)

    issues = quality_debt._coverage_issues(report, baseline)

    assert any("protected paths must stay checked" in issue for issue in issues)


def test_quality_ratchet_flags_complexity_regression() -> None:
    current = {
        "ruff_complexity": {"total": 2, "by_code": {"C901": 2}},
    }
    baseline = {
        "ratchets": {
            "ruff_complexity": {"total": 1, "by_code": {"C901": 1}},
        }
    }

    issues = quality_debt._ratchet_issues(current, baseline)

    assert any("ruff_complexity.total increased" in issue for issue in issues)
    assert any("ruff_complexity.C901 increased" in issue for issue in issues)


def test_quality_ruff_debt_scan_fails_closed_on_tool_error(monkeypatch) -> None:
    completed = quality_debt.subprocess.CompletedProcess(
        args=["ruff"],
        returncode=2,
        stdout="",
        stderr="configuration error",
    )
    monkeypatch.setattr(quality_debt.subprocess, "run", lambda *args, **kwargs: completed)

    with pytest.raises(RuntimeError, match="Ruff debt scan failed with exit code 2"):
        quality_debt._ruff_debt_counts(select="C90")


def test_quality_baseline_flags_ruff_only_protected_excludes() -> None:
    report = quality_debt._coverage_report()
    baseline = json.loads(json.dumps(report))
    protected = "src/market_data_platform/data_providers.py"
    report["tools"]["ruff"]["excluded_patterns"].append(protected)
    baseline["tools"]["ruff"]["excluded_patterns"].append(protected)

    issues = quality_debt._coverage_issues(report, baseline)

    assert any("protected paths must stay checked" in issue for issue in issues)


def test_maintainability_metrics_collect_function_lengths_and_args(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "demo.py").write_text(
        "\n".join(
            [
                "def oversized(a, b, c, d, e, f, g, h, i, j):",
                "    return a",
            ]
        ),
        encoding="utf-8",
    )

    metrics = maintainability_metrics.collect_metrics(tmp_path, ("src",), limit=1)

    assert metrics.python_files == 1
    assert metrics.max_argument_count == 10
    assert metrics.functions_with_10_plus_args == 1


def test_maintainability_baseline_flags_regression(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "demo.py").write_text("def demo(a, b):\n    return a + b\n", encoding="utf-8")
    metrics = maintainability_metrics.collect_metrics(tmp_path, ("src",), limit=1)
    baseline = {"metrics": {**metrics.to_payload(), "max_argument_count": 1}}

    issues = maintainability_metrics.compare_to_baseline(metrics, baseline)

    assert any("max_argument_count increased" in issue for issue in issues)


def test_maintainability_baseline_accepts_legacy_metrics_payload(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "demo.py").write_text("def demo(a, b):\n    return a + b\n", encoding="utf-8")
    metrics = maintainability_metrics.collect_metrics(tmp_path, ("src",), limit=1)

    assert maintainability_metrics.compare_to_baseline(metrics, metrics.to_payload()) == []


def test_isolated_pytest_runner_batches_files_and_rejects_zero_size() -> None:
    paths = [Path(f"test_{index}.py") for index in range(5)]

    assert list(run_pytest_isolated.batches(paths, 2)) == [
        paths[0:2],
        paths[2:4],
        paths[4:5],
    ]
    with pytest.raises(ValueError, match="positive"):
        list(run_pytest_isolated.batches(paths, 0))


def test_compatibility_inventory_is_complete() -> None:
    report = compatibility_governance.build_report()

    assert report["issues"] == []
    scripts = compatibility_governance._project_scripts()
    assert "marketdata" in scripts
    assert not {"hkdata", "rqdata-hk-depth", "rqdata-tick", "rqdata-hk-assets"} & scripts
    usage_labels = {row["label"] for row in report["usage"]}
    assert "marketdata migration freeze-hk / marketdata migration hydrate-hk" in usage_labels
    assert "hkdata CLI" not in usage_labels
    assert "hk_data_platform.*" not in usage_labels


def test_compatibility_inventory_parser_requires_expected_entries() -> None:
    entries = compatibility_governance.parse_inventory(
        "\n".join(
            [
                "| 兼容项 | 当前用途 | 风险 | 推荐替代 | 清理条件 | Owner | 当前状态 | 审计证据 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
                "| `retired wrapper` | use | risk | replace | cleanup | owner | "
                "deprecated; removed | audit |",
            ]
        )
    )

    issues = compatibility_governance.inventory_issues(entries)

    assert any("marketdata migration freeze-hk" in issue for issue in issues)


def test_compatibility_inventory_requires_lifecycle_status() -> None:
    entries = compatibility_governance.parse_inventory(
        "\n".join(
            [
                "| 兼容项 | 当前用途 | 风险 | 推荐替代 | 清理条件 | Owner | 当前状态 | 审计证据 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
                (
                    "| `marketdata migration freeze-hk / marketdata migration hydrate-hk` | use | "
                    "risk | replace | cleanup | owner | retained | audit |"
                ),
            ]
        )
    )

    issues = compatibility_governance.inventory_issues(entries)

    assert any("must include one lifecycle category" in issue for issue in issues)


def test_architecture_governance_current_boundaries() -> None:
    report = architecture_governance.build_report()

    assert report["issues"] == []


def test_architecture_governance_flags_standardize_to_provider_import(tmp_path: Path) -> None:
    standardize_root = tmp_path / "src" / "market_data_platform" / "standardize"
    standardize_root.mkdir(parents=True)
    (standardize_root / "bad.py").write_text(
        "from market_data_platform.providers.demo import fetch_raw\n",
        encoding="utf-8",
    )

    issues = architecture_governance.check_data_lifecycle_import_boundaries(tmp_path)

    assert issues
    assert issues[0].message == (
        "standardize layer imports provider implementation: market_data_platform.providers.demo"
    )


def test_architecture_governance_flags_private_test_facade_import(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_bad.py").write_text(
        "from market_data_platform.hk_assets import _private_helper\n",
        encoding="utf-8",
    )

    issues = architecture_governance.check_private_test_imports(tmp_path)

    assert issues
    assert issues[0].message.endswith("_private_helper")


def test_architecture_governance_flags_a_share_to_hk_import(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    (docs_root / "hk-split-boundary.yml").write_text(
        json.dumps(
            {
                "schema_version": "hk_split_boundary.v1",
                "core_a_share_import_freeze": {
                    "core_modules": [],
                    "a_share_modules": [
                        "src/market_data_platform/providers/tushare_a_share.py",
                    ],
                    "forbidden_prefixes": [
                        "market_data_platform.hk_assets",
                        "market_data_platform.hk_depth",
                    ],
                },
                "hk_extraction_roots": [],
                "internal_hk_prefixes": ["market_data_platform.hk_assets"],
                "platform_seam_prefixes": ["market_data_platform.artifacts"],
                "tracked_hk_platform_dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    provider_path = tmp_path / "src" / "market_data_platform" / "providers"
    provider_path.mkdir(parents=True)
    (provider_path / "tushare_a_share.py").write_text(
        "from ..hk_assets import mirror_hk_daily\n",
        encoding="utf-8",
    )

    issues = architecture_governance.check_hk_split_boundaries(tmp_path)

    assert issues
    assert "imports HK implementation" in issues[0].message


def test_architecture_governance_requires_hk_dependency_inventory(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    (docs_root / "hk-split-boundary.yml").write_text(
        json.dumps(
            {
                "schema_version": "hk_split_boundary.v1",
                "core_a_share_import_freeze": {
                    "core_modules": [],
                    "a_share_modules": [],
                    "forbidden_prefixes": [
                        "market_data_platform.hk_assets",
                        "market_data_platform.hk_depth",
                    ],
                },
                "hk_extraction_roots": ["src/market_data_platform/hk_assets"],
                "internal_hk_prefixes": ["market_data_platform.hk_assets"],
                "platform_seam_prefixes": ["market_data_platform.artifacts"],
                "tracked_hk_platform_dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    hk_root = tmp_path / "src" / "market_data_platform" / "hk_assets"
    hk_root.mkdir(parents=True)
    (hk_root / "demo.py").write_text(
        "from ..symbols import ensure_symbol_columns\n",
        encoding="utf-8",
    )

    issues = architecture_governance.check_hk_split_boundaries(tmp_path)

    assert issues
    assert issues[0].message == "HK split dependency not tracked: market_data_platform.symbols"


def test_docs_link_maintenance_audit_and_avoid_style_regressions() -> None:
    docs_root = Path("docs")
    docs_index = docs_root.joinpath("README.md").read_text(encoding="utf-8")

    assert "maintenance-audit.md" in docs_index
    assert "quant-market-data-deploy" in docs_index
    assert "operations.md" in docs_index

    forbidden_fragments = (
        "不是",
        "而是",
        "目标不是",
        "不等于",
        "不代表",
        "**",
        "；",
        "——",
        "“",
        "”",
    )
    offenders = [
        f"{path}:{line_number}:{fragment}"
        for path in _active_markdown_docs(Path("."))
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        for fragment in forbidden_fragments
        if fragment in line
    ]

    assert offenders == []


def test_current_entry_docs_have_lifecycle_metadata() -> None:
    paths = (
        Path("docs/README.md"),
        Path("docs/contracts.md"),
        Path("docs/data-governance.md"),
        Path("docs/data-lifecycle-architecture.md"),
        Path("docs/integrations.md"),
        Path("docs/ownership-migration.md"),
        Path("docs/operations.md"),
        Path("docs/quality-governance.md"),
        Path("docs/compatibility.md"),
        Path("docs/operations/testing.md"),
        Path("docs/research-integrity.md"),
        Path("docs/maintenance-audit.md"),
    )
    required = (
        "> status: active",
        "> owner: market-data-platform",
        "> audience: human and agent",
        "> superseded_by: n/a",
    )
    missing = [
        f"{path}: {field}"
        for path in paths
        for field in required
        if field not in "\n".join(path.read_text(encoding="utf-8").splitlines()[:12])
    ]
    invalid_dates = [
        str(path)
        for path in paths
        if not any(
            line.startswith("> last_verified: 20")
            and len(line.removeprefix("> last_verified: ").strip()) == 10
            for line in path.read_text(encoding="utf-8").splitlines()[:12]
        )
    ]
    assert missing == []
    assert invalid_dates == []


def test_superseded_docs_point_to_existing_pages() -> None:
    docs_root = Path("docs")
    broken: list[str] = []
    for path in sorted(docs_root.rglob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()[:12]
        if "> status: superseded" not in lines:
            continue
        target_line = next((line for line in lines if line.startswith("> superseded_by:")), "")
        target = target_line.removeprefix("> superseded_by:").strip()
        target_path = docs_root / target if not target.startswith("docs/") else Path(target)
        if not target or target == "n/a" or not target_path.is_file():
            broken.append(f"{path}: {target or '<missing>'}")
    assert broken == []


def test_minute_runbook_tracks_current_v3_contract() -> None:
    runbook = (Path("docs") / "operations" / "a-share-minutes.md").read_text(encoding="utf-8")

    for fact in (
        "minute_1m_v3_20260714",
        "a_share_minute_1m_v3_20260714.coverage.json",
        "2,556",
        "2,587,512,152",
        "Guan annual",
        "Guan deal",
        "TuShare full day",
        "--target-market-scope sh-sz",
        "writes_production=false",
        "当前脚本会拒绝将它直接切回 current",
    ):
        assert fact in runbook

    assert "minute_1m_v2_YYYYMMDD" not in runbook


def test_typecheck_docs_use_ty() -> None:
    legacy_checker = "".join(("based", "py", "right"))
    for path in (
        Path("README.md"),
        Path("AGENTS.md"),
        Path("docs/operations/testing.md"),
        Path("docs/quality-governance.md"),
    ):
        docs = path.read_text(encoding="utf-8")
        assert "ty check" in docs
        assert legacy_checker not in docs.lower()


def test_ty_is_the_only_configured_type_checker() -> None:
    legacy_checker = "".join(("based", "py", "right"))
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8").lower()
    quality_script = Path("scripts/dev/quality_debt.py").read_text(encoding="utf-8").lower()

    # ty 已切换为全量扫描 (src/scripts/tests)，不再逐个文件名白名单。
    assert "[tool.ty.src]" in pyproject
    assert 'include = ["src", "scripts", "tests"]' in pyproject
    assert legacy_checker not in pyproject
    assert legacy_checker not in quality_script


def test_public_marketdata_cli_commands_are_documented() -> None:
    from market_data_platform.cli import build_parser

    docs_text = "\n".join(
        path.read_text(encoding="utf-8") for path in _active_markdown_docs(Path("."))
    )
    expected_commands = {
        f"marketdata {' '.join(command_path)}"
        for command_path in _leaf_commands(_command_tree(build_parser()))
    }
    missing = sorted(command for command in expected_commands if command not in docs_text)

    assert missing == []


def test_tushare_industry_membership_command_is_documented() -> None:
    docs_text = "\n".join(
        path.read_text(encoding="utf-8") for path in _active_markdown_docs(Path("."))
    )

    assert "marketdata tushare download-a-share-industry-membership" in docs_text


def test_public_cli_lifecycle_categories_are_documented() -> None:
    docs_text = (Path("docs") / "compatibility.md").read_text(encoding="utf-8")

    for lifecycle in (
        "active",
        "compatibility",
        "migration-only",
        "deprecated",
        "archival",
        "internal-only",
    ):
        assert lifecycle in docs_text

    assert "marketdata migration freeze-hk" in docs_text
    assert "marketdata migration hydrate-hk" in docs_text

    assert "scripts/internal/" in docs_text
