#!/usr/bin/env python3
"""Render the complete TuShare minute campaign user-systemd unit set."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path

_UNRESOLVED = re.compile(r"@[A-Z][A-Z0-9_]*@")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o644)
    finally:
        temporary_path.unlink(missing_ok=True)


def render_units(args: argparse.Namespace) -> list[Path]:
    template_root = args.template_root.expanduser().resolve()
    replacements = {
        "@HOME@": str(args.home.expanduser().resolve()),
        # Keep the deployment's stable `current` symlink in generated units.
        # Resolving it here would pin systemd to an immutable release forever.
        "@MDP_DIR@": str(args.mdp_dir.expanduser()),
        "@DATA_PLATFORM_ROOT@": str(args.data_platform_root.expanduser().resolve()),
        "@CAMPAIGN_MANIFEST@": str(args.campaign_manifest.expanduser().resolve()),
        "@HERMES_LOGS_DIR@": str(args.logs_dir.expanduser().resolve()),
        "@MARKETDATA_CLI@": str(args.marketdata_cli.expanduser().resolve()),
    }
    template_glob = getattr(args, "template_glob", "tushare-minute-*")
    templates = sorted(
        path for path in template_root.glob(template_glob) if path.suffix in {".service", ".timer"}
    )
    if not templates:
        raise FileNotFoundError(f"no TuShare minute systemd templates found under {template_root}")
    rendered: list[Path] = []
    for template in templates:
        text = template.read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        unresolved = sorted(set(_UNRESOLVED.findall(text)))
        if unresolved:
            raise ValueError(f"unresolved placeholders in {template.name}: {unresolved}")
        target = args.output_dir.expanduser().resolve() / template.name
        if not args.dry_run:
            _atomic_write(target, text)
        rendered.append(target)
    return rendered


def build_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template-root", type=Path, default=repo_root / "scripts" / "systemd")
    parser.add_argument("--template-glob", default="tushare-minute-*")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--mdp-dir", type=Path, default=repo_root)
    parser.add_argument("--data-platform-root", type=Path, required=True)
    parser.add_argument("--campaign-manifest", type=Path, required=True)
    parser.add_argument("--logs-dir", type=Path, required=True)
    parser.add_argument(
        "--marketdata-cli",
        type=Path,
        default=repo_root / ".venv" / "bin" / "marketdata",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rendered = render_units(args)
    for path in rendered:
        print(path)
    print("Run: systemctl --user daemon-reload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
