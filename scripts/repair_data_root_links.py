#!/usr/bin/env python3
"""Repair absolute data links after moving the quant data root.

The command is intentionally dry-run by default. It only rewrites symlinks whose
targets are below ``old_root`` and refuses to apply the change if any translated
target is missing.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class LinkRewrite:
    path: Path
    old_target: str
    new_target: str


def _absolute_target(link: Path) -> Path:
    target = Path(os.readlink(link))
    if target.is_absolute():
        return target
    return (link.parent / target).resolve()


def collect_rewrites(root: Path, old_root: Path, new_root: Path) -> list[LinkRewrite]:
    old_root = old_root.resolve()
    new_root = new_root.resolve()
    rewrites: list[LinkRewrite] = []
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        raw_target = os.readlink(path)
        target = _absolute_target(path)
        try:
            relative = target.relative_to(old_root)
        except ValueError:
            continue
        translated = new_root / relative
        rewrites.append(LinkRewrite(path, raw_target, str(translated)))
    return rewrites


def _validate_targets(rewrites: list[LinkRewrite]) -> list[LinkRewrite]:
    return [rewrite for rewrite in rewrites if not Path(rewrite.new_target).exists()]


def _replace_link(rewrite: LinkRewrite) -> None:
    temporary = rewrite.path.with_name(f".{rewrite.path.name}.migration-tmp-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(rewrite.new_target)
    os.replace(temporary, rewrite.path)


def _write_manifest(
    path: Path,
    root: Path,
    old_root: Path,
    new_root: Path,
    rewrites: list[LinkRewrite],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "operation": "rewrite_absolute_data_root_symlinks",
        "completed_at": datetime.now(UTC).isoformat(),
        "root": str(root.resolve()),
        "old_root": str(old_root.resolve()),
        "new_root": str(new_root.resolve()),
        "rewritten_links": len(rewrites),
        "links": [
            {
                "path": str(item.path),
                "old_target": item.old_target,
                "new_target": item.new_target,
            }
            for item in rewrites
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="tree to scan")
    parser.add_argument("--old-root", type=Path, required=True, help="old absolute data root")
    parser.add_argument("--new-root", type=Path, required=True, help="new absolute data root")
    parser.add_argument("--manifest", type=Path, required=True, help="migration manifest output")
    parser.add_argument("--apply", action="store_true", help="rewrite links after validation")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    old_root = args.old_root.resolve()
    new_root = args.new_root.resolve()
    if not root.is_dir():
        parser.error(f"root does not exist or is not a directory: {root}")
    rewrites = collect_rewrites(root, old_root, new_root)
    missing = _validate_targets(rewrites)
    if missing:
        print(f"refusing to continue: {len(missing)} translated targets are missing")
        for item in missing[:10]:
            print(f"  {item.path} -> {item.new_target}")
        return 2

    mode = "apply" if args.apply else "dry-run"
    print(f"mode={mode}")
    print(f"root={root}")
    print(f"rewritable_links={len(rewrites)}")
    if not args.apply:
        for item in rewrites[:10]:
            print(f"  {item.path}: {item.old_target} -> {item.new_target}")
        return 0

    for item in rewrites:
        _replace_link(item)
    _write_manifest(args.manifest, root, old_root, new_root, rewrites)
    print(f"manifest={args.manifest}")
    print(f"rewritten_links={len(rewrites)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
