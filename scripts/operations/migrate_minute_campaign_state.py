#!/usr/bin/env python3
"""Migrate legacy minute campaign IDs and references with an external backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan_id(identity: dict[str, Any]) -> str:
    payload = json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".migration-tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _replace_run_id(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(f"/runs/{old}", f"/runs/{new}")
    return value


def migrate(  # noqa: C901,PLR0912,PLR0915
    campaign_dir: Path, *, backup_dir: Path | None, apply: bool
) -> int:
    plans_dir = campaign_dir / "plans"
    plans = sorted(plans_dir.glob("*.plan.json"))
    migrations: dict[str, tuple[str, Path]] = {}
    for path in plans:
        payload = json.loads(path.read_text(encoding="utf-8"))
        old_id = str(payload.get("plan_id", ""))
        identity = payload.get("identity")
        if not old_id or not isinstance(identity, dict):
            raise ValueError(f"invalid plan identity: {path}")
        new_id = _plan_id(identity)
        migrations[old_id] = (new_id, path)
        print(f"{path.name}: {old_id} -> {new_id}")

    if not apply:
        print(f"dry-run: {len(migrations)} plans would be migrated")
        return 0
    if backup_dir is None:
        raise ValueError("--backup-dir is required with --apply")
    if backup_dir.exists():
        raise FileExistsError(f"backup directory already exists: {backup_dir}")
    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(campaign_dir, backup_dir, symlinks=True)

    for old_id, (new_id, path) in migrations.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        old_output = payload.get("output", {})
        old_run = (
            Path(str(old_output["run_root"]))
            if isinstance(old_output, dict) and old_output.get("run_root")
            else None
        )
        payload["plan_id"] = new_id
        output = payload.get("output", {})
        if isinstance(output, dict):
            output["run_root"] = _replace_run_id(output.get("run_root"), old_id, new_id)
            output["data_dir"] = _replace_run_id(output.get("data_dir"), old_id, new_id)
        _write_json(path, payload)
        new_run = (
            Path(str(output["run_root"]))
            if isinstance(output, dict) and output.get("run_root")
            else None
        )
        if (
            old_run is not None
            and new_run is not None
            and old_run.exists()
            and not new_run.exists()
        ):
            new_run.parent.mkdir(parents=True, exist_ok=True)
            new_run.symlink_to(old_run, target_is_directory=True)

    for path in sorted((campaign_dir / "receipts").glob("*.receipt.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        old_id = str(payload.get("plan_id", ""))
        if old_id not in migrations:
            continue
        new_id = migrations[old_id][0]
        payload["plan_id"] = new_id
        for key in ("run_root", "data_dir"):
            payload[key] = _replace_run_id(payload.get(key), old_id, new_id)
        output = payload.get("output")
        if isinstance(output, dict):
            for key in ("run_root", "data_dir"):
                output[key] = _replace_run_id(output.get(key), old_id, new_id)
        _write_json(path, payload)

    manifest_path = campaign_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for day in manifest.get("days", []):
        for phase in day.get("phases", []):
            for lane in phase.get("lanes", {}).values():
                old_id = str(lane.get("plan_id", ""))
                if old_id in migrations:
                    lane["plan_id"] = migrations[old_id][0]
                    lane["data_root"] = _replace_run_id(
                        lane.get("data_root"), old_id, migrations[old_id][0]
                    )
    _write_json(manifest_path, manifest)
    ledger_path = Path(str(manifest["ledger_path"]))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["manifest_sha256"] = _sha256(manifest_path)
    _write_json(ledger_path, ledger)
    readiness_path = campaign_dir / "acquisition-readiness.json"
    if readiness_path.exists():
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        readiness["manifest_sha256"] = _sha256(manifest_path)
        readiness["ledger_sha256_at_reconciliation"] = _sha256(ledger_path)
        _write_json(readiness_path, readiness)
    print(f"applied: {len(migrations)} plans; backup: {backup_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    return migrate(
        args.campaign_dir.expanduser().resolve(),
        backup_dir=args.backup_dir,
        apply=args.apply,
    )


if __name__ == "__main__":
    raise SystemExit(main())
