"""Fail-closed identity and append-only attempt records for vintage orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


def endpoint_identity(value: str | None) -> str:
    """Hash a canonical endpoint without retaining credentials in retry evidence."""
    try:
        parts = urlsplit(str(value or "").strip())
        port = parts.port
    except ValueError:
        raise ValueError("Invalid TuShare API URL.") from None
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError("An explicit credential-free TuShare API URL is required.")
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and (parts.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
        host = f"{host}:{port}"
    canonical = urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/"), "", ""))
    return hashlib.sha256(canonical.encode()).hexdigest()


def resolved_retry_parameters(args: argparse.Namespace) -> dict[str, object]:
    """Record code revision and resolved parameters; operational knobs are separate."""
    source = Path(__file__).resolve().parents[3]
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # Source bytes distinguish uncommitted implementations without recording paths/diffs.
    digest = hashlib.sha256()
    for path in sorted((source / "src" / "quant_market_data_platform").rglob("*.py")):
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(path.read_bytes())
    digest.update(
        (source / "scripts/operations/archive_tushare_fundamentals_vintage.py").read_bytes()
    )
    semantic = {
        "schema_version": "tushare.fundamentals.retry_identity.v1",
        "source_revision": revision,
        "implementation_sha256": digest.hexdigest(),
        "snapshot_date": args.snapshot_date,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "datasets": sorted(args.datasets),
        "entitlement_mode": "vip_batch",
        "run_ids": {name: f"{name}-{args.snapshot_date}" for name in sorted(args.datasets)},
        "api_url_sha256": endpoint_identity(args.api_url),
        "report_types": sorted(set(getattr(args, "report_types", None) or ["provider_default"])),
        "field_mappings": sorted(set(args.field_mappings)),
        "available_delay_days": args.available_delay_days,
        "max_observation_age_days": args.max_observation_age_days,
        "observation_frequency": args.observation_frequency,
    }
    execution = {
        key: getattr(args, key)
        for key in (
            "token_env",
            "retry_attempts",
            "retry_backoff_seconds",
            "request_interval_seconds",
            "page_size",
            "max_pages",
            "bucket_count",
            "batch_rows",
        )
    }
    return {
        "source_revision": revision,
        "source_dirty": bool(dirty),
        "semantic": semantic,
        "execution": execution,
    }


def check_retry_identity(root: Path, parameters: dict[str, object]) -> None:
    """Do not infer missing legacy parameters from current defaults."""
    identity_path = root / "retry_identity.json"
    if identity_path.is_file():
        recorded = json.loads(identity_path.read_text(encoding="utf-8"))
        if recorded != parameters["semantic"]:
            raise RuntimeError("Vintage retry identity changed; use a new output vintage.")
    elif root.exists() and any(root.iterdir()):
        raise RuntimeError("Vintage retry identity is unproven; legacy output cannot be retried.")


def begin_attempt(root: Path, parameters: dict[str, object]) -> str:
    """Create unique evidence before any provider operation or progress replacement."""
    check_retry_identity(root, parameters)
    root.mkdir(parents=True, exist_ok=True)
    identity = root / "retry_identity.json"
    if not identity.exists():
        with identity.open("x", encoding="utf-8") as handle:
            json.dump(parameters["semantic"], handle, indent=2)
    attempt_id = uuid4().hex
    attempt = root / "attempts" / attempt_id
    attempt.mkdir(parents=True)
    with (attempt / "parameters.json").open("x", encoding="utf-8") as handle:
        json.dump(parameters, handle, indent=2)
    previous = root / "archive_state.json"
    if previous.is_file():
        # Preserve the exact preceding receipt, including its hashable original bytes.
        (attempt / "previous_archive_state.json").write_bytes(previous.read_bytes())
    return attempt_id


def record_derived_identity(path: Path, identity: object) -> None:
    """Bind requested semantics to the completed manifest, outside its sealed bytes."""
    manifest_hash = hashlib.sha256((path / "manifest.yml").read_bytes()).hexdigest()
    with (path / "archive_retry_identity.json").open("x", encoding="utf-8") as handle:
        json.dump({"semantic": identity, "manifest_sha256": manifest_hash}, handle, indent=2)


def require_derived_identity(path: Path, identity: object) -> None:
    receipt = path / "archive_retry_identity.json"
    if not receipt.is_file():
        raise RuntimeError("Derived retry identity is unproven; legacy output cannot be reused.")
    recorded = json.loads(receipt.read_text(encoding="utf-8"))
    expected = {
        "semantic": identity,
        "manifest_sha256": hashlib.sha256((path / "manifest.yml").read_bytes()).hexdigest(),
    }
    if recorded != expected:
        raise RuntimeError("Derived retry identity changed; use a new output vintage.")
