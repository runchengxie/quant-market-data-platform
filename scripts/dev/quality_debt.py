from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
BASELINE_PATH = REPO_ROOT / "scripts" / "dev" / "quality_baseline.json"
DEFAULT_RUFF_SELECT = "E,F,I,UP,B,C4,RET,RUF100"
COMPLEXITY_RUFF_SELECT = "C90,PLR0911,PLR0912,PLR0913,PLR0915"
BASELINE_VERSION = 2
TY_PROTECTED_INCLUDED_PATHS = (
    "src/market_data_platform/data_provider_contracts.py",
    "src/market_data_platform/symbols.py",
)
RUFF_PROTECTED_INCLUDED_PATHS = (
    *TY_PROTECTED_INCLUDED_PATHS,
    "src/market_data_platform/data_providers.py",
    "src/market_data_platform/data_warehouse.py",
    "src/market_data_platform/rqdata_runtime.py",
)
PROTECTED_INCLUDED_PATHS_BY_TOOL = {
    "ruff": RUFF_PROTECTED_INCLUDED_PATHS,
    "ty": TY_PROTECTED_INCLUDED_PATHS,
}
PROTECTED_INCLUDED_PATHS = TY_PROTECTED_INCLUDED_PATHS


def _python_files(src_root: Path = SRC_ROOT) -> list[Path]:
    return sorted(path for path in src_root.rglob("*.py") if path.is_file())


def _line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def _load_pyproject(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    payload = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _is_excluded(path: Path, patterns: set[str], *, repo_root: Path = REPO_ROOT) -> bool:
    rel = path.relative_to(repo_root).as_posix()
    return any(_pattern_excludes_path(rel, pattern) for pattern in patterns)


def _pattern_excludes_path(path: str, pattern: str) -> bool:
    return path == pattern or path.startswith(pattern.rstrip("/") + "/")


def _coverage_for(
    exclude_patterns: set[str],
    *,
    include_patterns: set[str] | None = None,
    repo_root: Path = REPO_ROOT,
    src_root: Path | None = None,
) -> dict[str, int | float]:
    src_root = src_root or repo_root / "src"
    files = _python_files(src_root)
    line_counts = {path: _line_count(path) for path in files}
    excluded = [
        path
        for path in files
        if _is_excluded(path, exclude_patterns, repo_root=repo_root)
        or (
            include_patterns
            and not any(
                _pattern_excludes_path(path.relative_to(repo_root).as_posix(), pattern)
                for pattern in include_patterns
            )
        )
    ]
    included = [path for path in files if path not in excluded]
    total_lines = sum(line_counts.values())
    included_lines = sum(line_counts[path] for path in included)
    return {
        "included_files": len(included),
        "excluded_files": len(excluded),
        "total_files": len(files),
        "included_lines": included_lines,
        "excluded_lines": total_lines - included_lines,
        "total_lines": total_lines,
        "included_pct": round((included_lines / total_lines * 100.0) if total_lines else 0.0, 1),
    }


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _tool_excludes(pyproject: Mapping[str, Any], tool_name: str) -> set[str]:
    tool = _as_mapping(pyproject.get("tool"))
    section = _as_mapping(tool.get(tool_name))
    if tool_name == "ty":
        section = _as_mapping(section.get("src"))
    keys = ("exclude", "extend-exclude") if tool_name == "ruff" else ("exclude",)
    values: set[str] = set()
    for key in keys:
        for item in section.get(key) or []:
            values.add(str(item))
    return values


def _tool_includes(pyproject: Mapping[str, Any], tool_name: str) -> set[str]:
    if tool_name != "ty":
        return set()
    tool = _as_mapping(pyproject.get("tool"))
    ty = _as_mapping(tool.get("ty"))
    src = _as_mapping(ty.get("src"))
    return {str(item) for item in src.get("include") or []}


def _coverage_report(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    pyproject = _load_pyproject(repo_root)
    tools: dict[str, Any] = {}
    for tool_name in ("ruff", "ty"):
        excludes = _tool_excludes(pyproject, tool_name)
        includes = _tool_includes(pyproject, tool_name)
        tools[tool_name] = {
            **_coverage_for(
                excludes,
                include_patterns=includes or None,
                repo_root=repo_root,
            ),
            "included_patterns": sorted(includes),
            "excluded_patterns": sorted(excludes),
        }
    return {
        "version": BASELINE_VERSION,
        "source_root": "src",
        "tools": tools,
    }


def _load_baseline(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Quality baseline not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _baseline_snapshot(report: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = {
        "version": BASELINE_VERSION,
        "generated_on": date.today().isoformat(),
        "source_root": report["source_root"],
        "tools": report["tools"],
    }
    if "ratchets" in report:
        snapshot["ratchets"] = report["ratchets"]
    return snapshot


def _write_baseline(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_baseline_snapshot(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _coverage_issues(
    report: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> list[str]:
    issues: list[str] = []
    current_tools = _as_mapping(report.get("tools"))
    baseline_tools = _as_mapping(baseline.get("tools"))
    for tool_name in ("ruff", "ty"):
        current = current_tools.get(tool_name)
        expected = baseline_tools.get(tool_name)
        if not isinstance(current, Mapping) or not isinstance(expected, Mapping):
            issues.append(f"{tool_name}: missing current or baseline coverage payload")
            continue

        current_excludes = set(current.get("excluded_patterns") or [])
        expected_excludes = set(expected.get("excluded_patterns") or [])
        protected_paths = PROTECTED_INCLUDED_PATHS_BY_TOOL.get(
            tool_name,
            PROTECTED_INCLUDED_PATHS,
        )
        protected_excludes = sorted(
            path
            for path in protected_paths
            if any(_pattern_excludes_path(path, pattern) for pattern in current_excludes)
        )
        if protected_excludes:
            issues.append(
                f"{tool_name}: protected paths must stay checked: {', '.join(protected_excludes)}"
            )

        added_excludes = sorted(current_excludes - expected_excludes)
        if added_excludes:
            issues.append(f"{tool_name}: new excludes not in baseline: {', '.join(added_excludes)}")

        if int(current["included_lines"]) < int(expected["included_lines"]):
            issues.append(
                f"{tool_name}: checked lines decreased "
                f"({current['included_lines']} < {expected['included_lines']})"
            )
        if int(current["excluded_lines"]) > int(expected["excluded_lines"]):
            issues.append(
                f"{tool_name}: excluded lines increased "
                f"({current['excluded_lines']} > {expected['excluded_lines']})"
            )
    return issues


def _print_coverage(name: str, coverage: dict[str, int | float]) -> None:
    print(
        f"{name}: {coverage['included_files']}/{coverage['total_files']} files, "
        f"{coverage['included_lines']}/{coverage['total_lines']} lines checked "
        f"({coverage['included_pct']}%).",
        flush=True,
    )
    print(
        f"{name}: {coverage['excluded_files']} files and "
        f"{coverage['excluded_lines']} lines currently excluded.",
        flush=True,
    )


def _ruff_debt_command(select: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "src",
        "--isolated",
        "--select",
        select,
        "--line-length",
        "100",
        "--target-version",
        "py311",
        "--output-format",
        "json",
        "--exit-zero",
    ]


def _ruff_debt_counts(*, select: str) -> dict[str, Any]:
    command = _ruff_debt_command(select)
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"Ruff debt scan failed with exit code {result.returncode}: {detail}")
    try:
        diagnostics = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Ruff debt scan returned invalid JSON") from exc
    if not isinstance(diagnostics, list):
        raise RuntimeError("Ruff debt scan returned a non-list JSON payload")
    by_code = Counter(str(diagnostic.get("code") or "unknown") for diagnostic in diagnostics)
    return {
        "total": int(sum(by_code.values())),
        "by_code": dict(sorted(by_code.items())),
        "command": command,
    }


def _print_ruff_debt_counts(title: str, payload: Mapping[str, Any]) -> None:
    print(f"\n{title}:", flush=True)
    print("+", " ".join(str(part) for part in payload["command"]), flush=True)
    for code, count in sorted(
        payload.get("by_code", {}).items(),
        key=lambda item: (-int(item[1]), str(item[0])),
    ):
        print(f"{count:>2}\t{code}", flush=True)
    print(f"Found {payload.get('total', 0)} errors.", flush=True)


def _ratchet_report(*, complexity_select: str) -> dict[str, Any]:
    complexity = _ruff_debt_counts(select=complexity_select)
    report: dict[str, Any] = {
        "ruff_complexity": {
            "total": complexity["total"],
            "by_code": complexity["by_code"],
        },
    }
    return report


def _ratchet_issues(current: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    expected_ratchets = baseline.get("ratchets")
    if not isinstance(expected_ratchets, Mapping):
        return ["ratchet: baseline is missing ratchets"]

    current_complexity = current.get("ruff_complexity")
    expected_complexity = expected_ratchets.get("ruff_complexity")
    if not isinstance(current_complexity, Mapping) or not isinstance(
        expected_complexity,
        Mapping,
    ):
        issues.append("ruff_complexity: missing current or baseline ratchet")
    else:
        current_total = int(current_complexity.get("total", 0))
        expected_total = int(expected_complexity.get("total", 0))
        if current_total > expected_total:
            issues.append(f"ruff_complexity.total increased ({current_total} > {expected_total})")
        current_by_code = (
            current_complexity.get("by_code")
            if isinstance(current_complexity.get("by_code"), Mapping)
            else {}
        )
        expected_by_code = (
            expected_complexity.get("by_code")
            if isinstance(expected_complexity.get("by_code"), Mapping)
            else {}
        )
        for code, count in sorted(current_by_code.items()):
            expected_count = int(expected_by_code.get(code, 0))
            if int(count) > expected_count:
                issues.append(f"ruff_complexity.{code} increased ({count} > {expected_count})")

    return issues


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report static-check coverage and non-blocking Ruff debt for src/."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable coverage and baseline-check output.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help=f"Quality baseline path. Default: {BASELINE_PATH.relative_to(REPO_ROOT)}",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write the current coverage snapshot as the accepted quality baseline.",
    )
    parser.add_argument(
        "--check-baseline",
        action="store_true",
        help="Fail if static-check coverage regresses against the baseline.",
    )
    parser.add_argument(
        "--check-ratchet",
        action="store_true",
        help="Fail if accepted complexity debt counts increase.",
    )
    parser.add_argument(
        "--skip-ruff",
        action="store_true",
        help="Only report configured coverage; do not run the non-blocking Ruff debt scan.",
    )
    parser.add_argument(
        "--complexity",
        action="store_true",
        help=f"Run a non-blocking Ruff complexity scan ({COMPLEXITY_RUFF_SELECT}).",
    )
    parser.add_argument(
        "--ruff-select",
        default=DEFAULT_RUFF_SELECT,
        help=f"Rule selection for the Ruff debt scan. Default: {DEFAULT_RUFF_SELECT}",
    )
    parser.add_argument(
        "--complexity-select",
        default=COMPLEXITY_RUFF_SELECT,
        help=f"Rule selection for the complexity scan. Default: {COMPLEXITY_RUFF_SELECT}",
    )
    return parser


def _resolved_baseline_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _quality_run_state(
    args: argparse.Namespace,
    report: dict[str, Any],
    baseline_path: Path,
) -> tuple[list[str], list[str], dict[str, Any] | None]:
    baseline_issues: list[str] = []
    ratchet_issues: list[str] = []
    ratchets: dict[str, Any] | None = None

    if args.write_baseline or args.check_ratchet:
        ratchets = _ratchet_report(complexity_select=args.complexity_select)
        report["ratchets"] = ratchets

    if args.write_baseline:
        _write_baseline(baseline_path, report)

    baseline: dict[str, Any] | None = None
    if args.check_baseline:
        baseline = _load_baseline(baseline_path)
        baseline_issues = _coverage_issues(report, baseline)

    if args.check_ratchet:
        baseline = baseline or _load_baseline(baseline_path)
        ratchet_issues = _ratchet_issues(ratchets or {}, baseline)
    return baseline_issues, ratchet_issues, ratchets


def _write_json_report(
    report: Mapping[str, Any],
    baseline_issues: Sequence[str],
    ratchet_issues: Sequence[str],
) -> int:
    payload = {
        **report,
        "baseline_issues": list(baseline_issues),
        "ratchet_issues": list(ratchet_issues),
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    print()
    return 1 if baseline_issues or ratchet_issues else 0


def _print_coverage_report(report: Mapping[str, Any]) -> None:
    for display_name, tool_name in (("Ruff", "ruff"), ("ty", "ty")):
        _print_coverage(display_name, report["tools"][tool_name])


def _print_issue_block(title: str, issues: Sequence[str]) -> bool:
    if not issues:
        return False
    print(f"\n{title}:", file=sys.stderr, flush=True)
    for issue in issues:
        print(f"- {issue}", file=sys.stderr, flush=True)
    return True


def _complexity_payload(
    args: argparse.Namespace, ratchets: Mapping[str, Any] | None
) -> dict[str, Any]:
    if not ratchets:
        return _ruff_debt_counts(select=args.complexity_select)
    return {
        **ratchets["ruff_complexity"],
        "command": _ruff_debt_command(args.complexity_select),
    }


def _print_requested_debt(args: argparse.Namespace, ratchets: Mapping[str, Any] | None) -> None:
    if not args.skip_ruff:
        _print_ruff_debt_counts(
            "Ruff debt scan",
            _ruff_debt_counts(select=args.ruff_select),
        )
    if args.complexity:
        _print_ruff_debt_counts(
            "Ruff complexity debt scan",
            _complexity_payload(args, ratchets),
        )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    baseline_path = _resolved_baseline_path(args.baseline)
    report = _coverage_report()
    baseline_issues, ratchet_issues, ratchets = _quality_run_state(
        args,
        report,
        baseline_path,
    )

    if args.json:
        return _write_json_report(report, baseline_issues, ratchet_issues)

    _print_coverage_report(report)
    if args.write_baseline:
        print(f"\nWrote quality baseline: {baseline_path.relative_to(REPO_ROOT)}", flush=True)
    if _print_issue_block("Quality baseline regressions", baseline_issues):
        return 1
    if _print_issue_block("Quality ratchet regressions", ratchet_issues):
        return 1

    _print_requested_debt(args, ratchets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
