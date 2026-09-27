"""Validate or build the repository-only knowledge pilot index."""

import argparse
import json
from pathlib import Path

from market_data_platform.knowledge_index import (
    build_knowledge_index,
    load_manifest_documents,
    validate_knowledge_documents,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "build"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root = args.repository_root.resolve()
    try:
        manifest = args.manifest if args.manifest.is_absolute() else root / args.manifest
        paths = load_manifest_documents(manifest, root)
        issues = validate_knowledge_documents(paths, root)
        if issues:
            raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in issues))
        if args.command == "build":
            if args.output is None:
                raise ValueError("build requires --output")
            output = args.output.resolve()
            if output.is_relative_to(root):
                raise ValueError("output must be outside repository root")
            rows = build_knowledge_index(paths, root)
            output.write_text(
                json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        elif args.output is not None:
            raise ValueError("validate does not accept --output")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"knowledge index: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
