"""Resolve retry source provenance for development and immutable deployments."""

from __future__ import annotations

import json
import re
import subprocess
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

_PACKAGE = "quant_market_data_platform"
_ERROR = "Vintage source identity is unproven."


def _installed_source() -> Path:
    record = metadata.distribution("quant-market-data-platform").read_text("direct_url.json")
    payload = json.loads(record or "null")
    if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
        raise ValueError(_ERROR)
    url = urlsplit(payload["url"])
    if url.scheme != "file" or url.netloc or url.query or url.fragment:
        raise ValueError(_ERROR)
    path = Path(unquote(url.path))
    if not path.is_absolute():
        raise ValueError(_ERROR)
    return path.resolve()


def _archive_revision(source: Path) -> str:
    revision = source.name
    if source.parent.name != "releases" or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError(_ERROR)
    manifest = source.parent.parent / "manifests" / f"{revision}.txt"
    fields: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in fields:
            raise ValueError(_ERROR)
        fields[key] = value
    expected = {
        "framework": "quant-market-data-platform",
        "commit": revision,
        "release_format": "git-archive-v1",
    }
    if any(fields.get(key) != value for key, value in expected.items()):
        raise ValueError(_ERROR)
    return revision


def source_identity(source_root: Path | None = None) -> tuple[Path, str, bool]:
    """Require a real source tree and exact Git root or deployment manifest."""
    try:
        module = Path(__file__).resolve()
        candidate = module.parents[3]
        source = (
            source_root or (candidate if (candidate / ".git").exists() else _installed_source())
        ).resolve()
        package = source / "src" / _PACKAGE
        if (
            not (source / "pyproject.toml").is_file()
            or not (source / "scripts/operations/archive_tushare_fundamentals_vintage.py").is_file()
        ):
            raise ValueError(_ERROR)
        # An explicit script root cannot identify a different installed implementation.
        installed = module.parents[1]
        paths = sorted(installed.rglob("*.py"))
        if {path.relative_to(installed) for path in paths} != {
            path.relative_to(package) for path in package.rglob("*.py")
        }:
            raise ValueError(_ERROR)
        if not paths or any(
            not (package / path.relative_to(installed)).is_file()
            or path.read_bytes() != (package / path.relative_to(installed)).read_bytes()
            for path in paths
        ):
            raise ValueError(_ERROR)
        if (source / ".git").exists():

            def git(*arguments: str) -> str:
                return subprocess.run(
                    ["git", "-C", str(source), *arguments],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()

            if Path(git("rev-parse", "--show-toplevel")).resolve() != source:
                raise ValueError(_ERROR)
            revision = git("rev-parse", "HEAD")
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise ValueError(_ERROR)
            dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
        else:
            revision, dirty = _archive_revision(source), False
        return source, revision, dirty
    except (
        OSError,
        ValueError,
        TypeError,
        metadata.PackageNotFoundError,
        subprocess.CalledProcessError,
    ):
        raise RuntimeError(_ERROR) from None
