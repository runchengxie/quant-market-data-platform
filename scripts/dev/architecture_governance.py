#!/usr/bin/env python3
"""Check lightweight architecture and public API boundaries."""

from __future__ import annotations

import argparse
import ast
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from importlib.util import resolve_name
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_MODULES = (
    "src/market_data_platform/artifacts.py",
    "src/market_data_platform/contract.py",
    "src/market_data_platform/data_provider_contracts.py",
    "src/market_data_platform/manifest.py",
    "src/market_data_platform/paths.py",
    "src/market_data_platform/registry.py",
    "src/market_data_platform/repo_paths.py",
)
A_SHARE_MODULES = (
    "src/market_data_platform/providers/rqdata_a_share.py",
    "src/market_data_platform/providers/tushare_a_share.py",
    "src/market_data_platform/providers/tushare_a_share_clean.py",
    "src/market_data_platform/providers/tushare_a_share_fundamentals.py",
    "src/market_data_platform/providers/tushare_a_share_quality.py",
    "src/market_data_platform/providers/tushare_a_share_research.py",
    "src/market_data_platform/providers/tushare_a_share_universe.py",
    "src/market_data_platform/tushare_backfill.py",
    "src/market_data_platform/tushare_cli.py",
    "src/market_data_platform/tushare_refresh.py",
)
BANNED_CORE_IMPORT_PREFIXES = (
    "market_data_platform.cli",
    "market_data_platform.hk_assets",
    "market_data_platform.hk_depth",
    "market_data_platform.hk_workflows",
    "market_data_platform.providers",
    "market_data_platform.release_tools",
    "market_data_platform.rqdata_runtime",
)
PUBLIC_EXPORTS_PATH = Path("src/market_data_platform/hk_assets/_public_exports.py")
PUBLIC_API_PATH = Path("src/market_data_platform/hk_assets/public_api.py")
HK_SPLIT_BOUNDARY_PATH = Path("docs/hk-split-boundary.yml")


@dataclass(frozen=True)
class BoundaryIssue:
    path: str
    line: int
    message: str


def _relative_path(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _load_yaml_manifest(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def _source_files(repo_root: Path, relative_paths: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for relative_text in relative_paths:
        path = repo_root / relative_text
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
    return files


def _package_name_for_path(repo_root: Path, path: Path) -> str | None:
    try:
        module_path = path.relative_to(repo_root / "src").with_suffix("")
    except ValueError:
        return None
    parts = list(module_path.parts)
    if not parts:
        return None
    if parts[-1] == "__init__":
        parts.pop()
    module_name = ".".join(parts)
    if path.name == "__init__.py":
        return module_name
    package_name, _, _ = module_name.rpartition(".")
    return package_name or module_name


def _module_name_for_path(repo_root: Path, path: Path) -> str | None:
    try:
        module_path = path.relative_to(repo_root / "src").with_suffix("")
    except ValueError:
        return None
    parts = list(module_path.parts)
    if not parts:
        return None
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(
    tree: ast.Module,
    *,
    package_name: str | None = None,
    top_level_only: bool = False,
) -> Iterable[tuple[int, str]]:
    nodes = tree.body if top_level_only else ast.walk(tree)
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if package_name is None:
                    continue
                relative_name = "." * node.level + (node.module or "")
                yield node.lineno, resolve_name(relative_name, package_name)
            elif node.module:
                yield node.lineno, node.module


def _internal_module_graph(repo_root: Path) -> dict[str, set[str]]:
    source_root = repo_root / "src" / "market_data_platform"
    module_paths = {
        module: path
        for path in sorted(source_root.rglob("*.py"))
        if (module := _module_name_for_path(repo_root, path)) is not None
    }
    modules = set(module_paths)
    graph: dict[str, set[str]] = {}
    for module, path in module_paths.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for _, imported in _imported_modules(
            tree,
            package_name=_package_name_for_path(repo_root, path),
            top_level_only=True,
        ):
            if not imported.startswith("market_data_platform"):
                continue
            parts = imported.split(".")
            for index in range(len(parts), 1, -1):
                candidate = ".".join(parts[:index])
                if candidate in modules:
                    imports.add(candidate)
                    break
        imports.discard(module)
        graph[module] = imports
    return graph


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[list[str]] = []

    def visit(module: str) -> None:
        nonlocal index
        indexes[module] = index
        lowlinks[module] = index
        index += 1
        stack.append(module)
        on_stack.add(module)
        for dependency in graph[module]:
            if dependency not in indexes:
                visit(dependency)
                lowlinks[module] = min(lowlinks[module], lowlinks[dependency])
            elif dependency in on_stack:
                lowlinks[module] = min(lowlinks[module], indexes[dependency])
        if lowlinks[module] != indexes[module]:
            return
        component: list[str] = []
        while True:
            dependency = stack.pop()
            on_stack.remove(dependency)
            component.append(dependency)
            if dependency == module:
                break
        if len(component) > 1:
            components.append(sorted(component))

    for module in sorted(graph):
        if module not in indexes:
            visit(module)
    return components


def check_internal_import_cycles(repo_root: Path = REPO_ROOT) -> list[BoundaryIssue]:
    issues: list[BoundaryIssue] = []
    for component in _strongly_connected_components(_internal_module_graph(repo_root)):
        issues.append(
            BoundaryIssue(
                "src/market_data_platform",
                1,
                "internal import cycle: " + " -> ".join(component),
            )
        )
    return issues


def check_core_import_boundaries(repo_root: Path = REPO_ROOT) -> list[BoundaryIssue]:
    issues: list[BoundaryIssue] = []
    for relative_text in CORE_MODULES:
        path = repo_root / relative_text
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, module in _imported_modules(
            tree,
            package_name=_package_name_for_path(repo_root, path),
        ):
            if module.startswith(BANNED_CORE_IMPORT_PREFIXES):
                issues.append(
                    BoundaryIssue(
                        path=relative_text,
                        line=line,
                        message=f"core module imports implementation boundary: {module}",
                    )
                )
    return issues


def check_data_lifecycle_import_boundaries(repo_root: Path = REPO_ROOT) -> list[BoundaryIssue]:
    issues: list[BoundaryIssue] = []
    standardize_root = repo_root / "src" / "market_data_platform" / "standardize"
    for path in _source_files(repo_root, (str(standardize_root.relative_to(repo_root)),)):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, module in _imported_modules(
            tree,
            package_name=_package_name_for_path(repo_root, path),
        ):
            if module.startswith("market_data_platform.providers"):
                issues.append(
                    BoundaryIssue(
                        path=_relative_path(repo_root, path),
                        line=line,
                        message=f"standardize layer imports provider implementation: {module}",
                    )
                )
            if module.startswith("market_data_platform.ingest"):
                issues.append(
                    BoundaryIssue(
                        path=_relative_path(repo_root, path),
                        line=line,
                        message=f"standardize layer imports ingest implementation: {module}",
                    )
                )
    return issues


def _tracked_hk_platform_dependencies(
    repo_root: Path,
    *,
    extraction_roots: Iterable[str],
    platform_seam_prefixes: Iterable[str],
    internal_hk_prefixes: Iterable[str],
) -> set[str]:
    seam = tuple(platform_seam_prefixes)
    internal_hk = tuple(internal_hk_prefixes)
    dependencies: set[str] = set()
    for path in _source_files(repo_root, extraction_roots):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for _, module in _imported_modules(
            tree,
            package_name=_package_name_for_path(repo_root, path),
        ):
            if not module.startswith("market_data_platform."):
                continue
            if module.startswith(internal_hk) or module.startswith(seam):
                continue
            parts = module.split(".")
            dependencies.add(".".join(parts[:2]))
    return dependencies


def check_hk_split_boundaries(repo_root: Path = REPO_ROOT) -> list[BoundaryIssue]:
    issues: list[BoundaryIssue] = []
    boundary_path = repo_root / HK_SPLIT_BOUNDARY_PATH
    if not boundary_path.exists():
        return []

    manifest = _load_yaml_manifest(boundary_path)
    if manifest.get("schema_version") != "hk_split_boundary.v1":
        issues.append(
            BoundaryIssue(
                str(HK_SPLIT_BOUNDARY_PATH),
                1,
                "HK split boundary manifest has unexpected schema_version",
            )
        )
        return issues

    import_freeze = manifest.get("core_a_share_import_freeze", {})
    core_modules = tuple(import_freeze.get("core_modules", []))
    a_share_modules = tuple(import_freeze.get("a_share_modules", []))
    enforce_builtin_scopes = repo_root.resolve() == REPO_ROOT.resolve()
    if enforce_builtin_scopes and core_modules != CORE_MODULES:
        issues.append(
            BoundaryIssue(
                str(HK_SPLIT_BOUNDARY_PATH),
                1,
                "HK split core module scope does not match architecture governance",
            )
        )
    if enforce_builtin_scopes and a_share_modules != A_SHARE_MODULES:
        issues.append(
            BoundaryIssue(
                str(HK_SPLIT_BOUNDARY_PATH),
                1,
                "HK split A-share module scope does not match architecture governance",
            )
        )

    frozen_paths = [*core_modules, *a_share_modules]
    forbidden_prefixes = tuple(import_freeze.get("forbidden_prefixes", []))
    for path in _source_files(repo_root, frozen_paths):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, module in _imported_modules(
            tree,
            package_name=_package_name_for_path(repo_root, path),
        ):
            if module.startswith(forbidden_prefixes):
                issues.append(
                    BoundaryIssue(
                        path=_relative_path(repo_root, path),
                        line=line,
                        message=(f"core/A-share boundary imports HK implementation: {module}"),
                    )
                )

    extraction_roots = manifest.get("hk_extraction_roots", [])
    actual_dependencies = _tracked_hk_platform_dependencies(
        repo_root,
        extraction_roots=extraction_roots,
        platform_seam_prefixes=manifest.get("platform_seam_prefixes", []),
        internal_hk_prefixes=manifest.get("internal_hk_prefixes", []),
    )
    declared_dependencies = set(manifest.get("tracked_hk_platform_dependencies", []))
    for module in sorted(actual_dependencies - declared_dependencies):
        issues.append(
            BoundaryIssue(
                str(HK_SPLIT_BOUNDARY_PATH),
                1,
                f"HK split dependency not tracked: {module}",
            )
        )
    for module in sorted(declared_dependencies - actual_dependencies):
        issues.append(
            BoundaryIssue(
                str(HK_SPLIT_BOUNDARY_PATH),
                1,
                f"HK split dependency no longer observed: {module}",
            )
        )
    return issues


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


def _has_public_api_all_assignment(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        has_all_target = any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        )
        if not has_all_target:
            continue
        value = node.value
        return (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "list"
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Name)
            and value.args[0].id == "PUBLIC_API_EXPORTS"
        )
    return False


def check_public_api_exports(repo_root: Path = REPO_ROOT) -> list[BoundaryIssue]:
    issues: list[BoundaryIssue] = []
    exports_path = repo_root / PUBLIC_EXPORTS_PATH
    public_api_path = repo_root / PUBLIC_API_PATH
    if not exports_path.exists():
        if not public_api_path.exists():
            return issues
        issues.append(BoundaryIssue(str(PUBLIC_EXPORTS_PATH), 1, "public exports file missing"))
        return issues

    exports_tree = ast.parse(exports_path.read_text(encoding="utf-8"))
    exports = _literal_names_from_assignment(exports_tree, "PUBLIC_API_EXPORTS")
    for name in exports:
        if name.startswith("_"):
            issues.append(
                BoundaryIssue(
                    str(PUBLIC_EXPORTS_PATH),
                    1,
                    f"private helper appears in public exports: {name}",
                )
            )

    if not public_api_path.exists():
        issues.append(BoundaryIssue(str(PUBLIC_API_PATH), 1, "public_api.py missing"))
    else:
        public_api_tree = ast.parse(public_api_path.read_text(encoding="utf-8"))
        if not _has_public_api_all_assignment(public_api_tree):
            issues.append(
                BoundaryIssue(
                    str(PUBLIC_API_PATH),
                    1,
                    "__all__ must be derived from PUBLIC_API_EXPORTS",
                )
            )
    return issues


def check_private_test_imports(repo_root: Path = REPO_ROOT) -> list[BoundaryIssue]:
    issues: list[BoundaryIssue] = []
    tests_root = repo_root / "tests"
    if not tests_root.exists():
        return issues
    for path in sorted(tests_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module not in {
                "market_data_platform.hk_assets",
                "market_data_platform.hk_assets.public_api",
            }:
                continue
            for alias in node.names:
                if alias.name.startswith("_"):
                    issues.append(
                        BoundaryIssue(
                            path=_relative_path(repo_root, path),
                            line=node.lineno,
                            message=(
                                f"test imports private helper through public facade: {alias.name}"
                            ),
                        )
                    )
    return issues


def build_report(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    issues = [
        *check_core_import_boundaries(repo_root),
        *check_data_lifecycle_import_boundaries(repo_root),
        *check_hk_split_boundaries(repo_root),
        *check_internal_import_cycles(repo_root),
        *check_public_api_exports(repo_root),
        *check_private_test_imports(repo_root),
    ]
    return {"issues": [asdict(issue) for issue in issues]}


def format_text(report: dict[str, Any]) -> str:
    issues = report["issues"]
    if not issues:
        return "Architecture governance issues: 0"
    lines = ["Architecture governance issues:"]
    lines.extend(f"- {issue['path']}:{issue['line']} {issue['message']}" for issue in issues)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate architecture dependency and public API boundaries.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--check", action="store_true", help="Fail if boundary issues exist.")
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root. Defaults to this checkout.",
    )
    args = parser.parse_args(argv)

    report = build_report(args.root.resolve())
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_text(report))
    return 1 if args.check and report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
