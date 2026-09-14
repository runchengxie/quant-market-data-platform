#!/usr/bin/env python3
"""Collect maintainability metrics and enforce outlier baselines.

This script is now a thin wrapper around ``research-code-quality``. The
cross-repo-identical scan algorithm (file discovery, line counts, function-length
counting, C901 per-file-ignore counting) lives in that shared package; this file
keeps only market-data-platform's local concerns:

- ``functions_with_10_plus_args`` / ``max_argument_count`` (mdp ratchet tracks
  parameter-count, which the shared scan does not)
- ``public_facade`` (count of ``PUBLIC_API_EXPORTS`` re-exports)
- ``Metrics``, the baseline keys, and CLI formatting

Usage:
  python scripts/dev/maintainability_metrics.py            # human-readable text
  python scripts/dev/maintainability_metrics.py --json     # machine-readable JSON
  python scripts/dev/maintainability_metrics.py --check-baseline  # fail on regression
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from research_code_quality.scanner import (
    FileMetric,
    ScanResult,
    scan_repository,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPO_ROOT / "scripts" / "dev" / "maintainability_baseline.json"
DEFAULT_ROOTS = ("src", "scripts", "tests")
DEFAULT_LIMIT = 15
PUBLIC_EXPORTS_PATH = Path("src/market_data_platform/__init__.py")
BASELINE_VERSION = 1
BASELINE_METRIC_KEYS = (
    "functions_over_100",
    "functions_over_250",
    "functions_over_500",
    "functions_with_10_plus_args",
    "max_file_lines",
    "max_function_lines",
    "max_argument_count",
)


@dataclass(frozen=True)
class PublicFacadeMetric:
    path: str
    exports: int
    private_exports: int


@dataclass(frozen=True)
class FunctionMetric:
    path: str
    name: str
    start_line: int
    end_line: int
    lines: int
    arguments: int


@dataclass(frozen=True)
class Metrics:
    roots: list[str]
    python_files: int
    python_lines: int
    functions_over_100: int
    functions_over_250: int
    functions_over_500: int
    functions_with_10_plus_args: int
    max_file_lines: int
    max_function_lines: int
    max_argument_count: int
    public_facade: PublicFacadeMetric
    largest_files: list[FileMetric]
    largest_functions: list[FunctionMetric]

    def to_payload(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "version": BASELINE_VERSION,
            "thresholds": {
                "large_function_lines": 100,
                "very_large_function_lines": 250,
                "huge_function_lines": 500,
                "many_arguments": 10,
            },
        }


def _relative_path(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _argument_count(arguments: ast.arguments) -> int:
    total = len(arguments.posonlyargs) + len(arguments.args) + len(arguments.kwonlyargs)
    if arguments.vararg is not None:
        total += 1
    if arguments.kwarg is not None:
        total += 1
    return total


def _argument_counts_by_signature(
    repo_root: Path,
    files: Sequence[Path],
) -> dict[tuple[str, str, int], int]:
    counts: dict[tuple[str, str, int], int] = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        relative = _relative_path(repo_root, path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            end_line = getattr(node, "end_lineno", None)
            if end_line is None:
                continue
            counts[(relative, node.name, node.lineno)] = _argument_count(node.args)
    return counts


def _literal_names_from_assignment(tree: ast.Module, name: str) -> list[str]:
    for node in tree.body:
        value_node: ast.expr | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                value_node = node.value
        if value_node is None:
            continue
        value = ast.literal_eval(value_node)
        if isinstance(value, tuple | list):
            return [str(item) for item in value]
    return []


def public_facade_metric(repo_root: Path = REPO_ROOT) -> PublicFacadeMetric:
    path = repo_root / PUBLIC_EXPORTS_PATH
    if not path.exists():
        return PublicFacadeMetric(path=PUBLIC_EXPORTS_PATH.as_posix(), exports=0, private_exports=0)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    exports = _literal_names_from_assignment(tree, "PUBLIC_API_EXPORTS")
    return PublicFacadeMetric(
        path=PUBLIC_EXPORTS_PATH.as_posix(),
        exports=len(exports),
        private_exports=sum(1 for name in exports if name.startswith("_")),
    )


def collect_metrics(
    repo_root: Path = REPO_ROOT,
    roots: Sequence[str] = DEFAULT_ROOTS,
    limit: int = DEFAULT_LIMIT,
) -> Metrics:
    result: ScanResult = scan_repository(repo_root, roots, limit)

    argument_counts = _argument_counts_by_signature(
        repo_root, [repo_root / item.path for item in result.files]
    )

    largest_functions = [
        FunctionMetric(
            path=item.path,
            name=item.name,
            start_line=item.start_line,
            end_line=item.end_line,
            lines=item.lines,
            arguments=argument_counts.get((item.path, item.name, item.start_line), 0),
        )
        for item in result.largest_functions
    ]
    all_functions = [
        FunctionMetric(
            path=item.path,
            name=item.name,
            start_line=item.start_line,
            end_line=item.end_line,
            lines=item.lines,
            arguments=argument_counts.get((item.path, item.name, item.start_line), 0),
        )
        for item in result.functions
    ]

    return Metrics(
        roots=result.roots,
        python_files=result.python_files,
        python_lines=result.python_lines,
        functions_over_100=result.functions_over_100,
        functions_over_250=result.functions_over_250,
        functions_over_500=result.functions_over_500,
        functions_with_10_plus_args=sum(1 for item in all_functions if item.arguments >= 10),
        max_file_lines=max((item.lines for item in result.files), default=0),
        max_function_lines=max((item.lines for item in all_functions), default=0),
        max_argument_count=max((item.arguments for item in all_functions), default=0),
        public_facade=public_facade_metric(repo_root),
        largest_files=result.largest_files,
        largest_functions=largest_functions,
    )


def _baseline_snapshot(metrics: Metrics) -> dict[str, Any]:
    return {
        "version": BASELINE_VERSION,
        "generated_on": date.today().isoformat(),
        "metrics": metrics.to_payload(),
    }


def write_baseline(path: Path, metrics: Metrics) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_baseline_snapshot(metrics), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_baseline(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _metric_value(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key, 0)
    return int(value) if isinstance(value, int | float) else 0


def _baseline_metrics_payload(baseline: Mapping[str, Any]) -> Mapping[str, Any] | None:
    baseline_metrics = baseline.get("metrics")
    if isinstance(baseline_metrics, Mapping):
        return baseline_metrics
    if any(key in baseline for key in BASELINE_METRIC_KEYS):
        return baseline
    return None


def compare_to_baseline(metrics: Metrics, baseline: Mapping[str, Any]) -> list[str]:
    baseline_metrics = _baseline_metrics_payload(baseline)
    if baseline_metrics is None:
        return ["maintainability: baseline is missing metrics"]

    current = metrics.to_payload()
    issues: list[str] = []
    for key in BASELINE_METRIC_KEYS:
        current_value = _metric_value(current, key)
        baseline_value = _metric_value(baseline_metrics, key)
        if current_value > baseline_value:
            issues.append(f"{key} increased ({current_value} > {baseline_value})")

    current_facade = current.get("public_facade")
    baseline_facade = baseline_metrics.get("public_facade")
    if isinstance(current_facade, Mapping) and isinstance(baseline_facade, Mapping):
        for key in ("exports", "private_exports"):
            current_value = _metric_value(current_facade, key)
            baseline_value = _metric_value(baseline_facade, key)
            if current_value > baseline_value:
                issues.append(f"public_facade.{key} increased ({current_value} > {baseline_value})")
    return issues


def format_text(metrics: Metrics) -> str:
    rows = [
        ("python_files", metrics.python_files),
        ("python_lines", metrics.python_lines),
        ("functions_over_100", metrics.functions_over_100),
        ("functions_over_250", metrics.functions_over_250),
        ("functions_over_500", metrics.functions_over_500),
        ("functions_with_10_plus_args", metrics.functions_with_10_plus_args),
        ("max_file_lines", metrics.max_file_lines),
        ("max_function_lines", metrics.max_function_lines),
        ("max_argument_count", metrics.max_argument_count),
        ("public_facade_exports", metrics.public_facade.exports),
        ("private_public_facade_exports", metrics.public_facade.private_exports),
    ]
    lines = ["Maintainability metrics:"]
    lines.extend(f"- {name}: {value}" for name, value in rows)
    lines.extend(["", "Largest functions:"])
    lines.extend(
        f"- {item.lines} lines, {item.arguments} args: {item.path}:{item.start_line} {item.name}"
        for item in metrics.largest_functions
    )
    return "\n".join(lines)


def format_markdown(metrics: Metrics) -> str:
    lines = [
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Python files | {metrics.python_files} |",
        f"| Python lines | {metrics.python_lines} |",
        f"| Functions over 100 lines | {metrics.functions_over_100} |",
        f"| Functions over 250 lines | {metrics.functions_over_250} |",
        f"| Functions over 500 lines | {metrics.functions_over_500} |",
        f"| Functions with 10+ args | {metrics.functions_with_10_plus_args} |",
        f"| Max file lines | {metrics.max_file_lines} |",
        f"| Max function lines | {metrics.max_function_lines} |",
        f"| Max argument count | {metrics.max_argument_count} |",
        f"| Public facade exports | {metrics.public_facade.exports} |",
        f"| Private public facade exports | {metrics.public_facade.private_exports} |",
        "",
        "Largest functions:",
        "",
        "| Lines | Args | Function | Path |",
        "| ---: | ---: | --- | --- |",
    ]
    for item in metrics.largest_functions:
        lines.append(
            f"| {item.lines} | {item.arguments} | `{item.name}` | `{item.path}:{item.start_line}` |"
        )
    return "\n".join(lines)


def _parse_roots(values: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(values) if values else DEFAULT_ROOTS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect maintainability metrics and enforce baseline regressions.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Print a markdown summary suitable for maintenance docs.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Number of largest files/functions to include. Default: {DEFAULT_LIMIT}.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root. Defaults to this checkout.",
    )
    parser.add_argument(
        "--scope",
        action="append",
        choices=DEFAULT_ROOTS,
        help="Root to include. May be repeated. Defaults to src, scripts, tests.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help=f"Maintainability baseline path. Default: {BASELINE_PATH.relative_to(REPO_ROOT)}",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write the current metrics as the accepted maintainability baseline.",
    )
    parser.add_argument(
        "--check-baseline",
        action="store_true",
        help="Fail if maintainability metrics regress against the baseline.",
    )
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()
    baseline_path = args.baseline if args.baseline.is_absolute() else repo_root / args.baseline
    metrics = collect_metrics(repo_root, _parse_roots(args.scope), max(args.limit, 0))
    issues: list[str] = []

    if args.write_baseline:
        write_baseline(baseline_path, metrics)
    if args.check_baseline:
        issues = compare_to_baseline(metrics, load_baseline(baseline_path))

    if args.json:
        print(json.dumps({**metrics.to_payload(), "baseline_issues": issues}, indent=2))
    elif args.markdown:
        print(format_markdown(metrics))
    else:
        print(format_text(metrics))

    if issues:
        for issue in issues:
            print(f"maintainability baseline regression: {issue}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
