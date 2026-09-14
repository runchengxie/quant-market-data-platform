from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

import yaml

from .artifacts import resolve_artifacts_root, resolve_metadata_db_path, resolve_repo_path
from .data_warehouse_models import (
    CatalogArtifact,
    CatalogLineage,
)


def _timestamp_now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def _read_git_value(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def _git_metadata(repo_root: Path) -> dict | None:
    commit = _read_git_value(repo_root, "rev-parse", "HEAD")
    if not commit:
        return None
    short_commit = _read_git_value(repo_root, "rev-parse", "--short", "HEAD")
    branch = _read_git_value(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    status = _read_git_value(repo_root, "status", "--short")
    return {
        "commit": commit,
        "short_commit": short_commit,
        "branch": branch,
        "is_dirty": bool(status),
    }


def _load_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise SystemExit(f"Manifest root must be a mapping: {path}")
    return payload


def _maybe_int(value) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _manifest_paths(artifacts_root: Path) -> Iterable[Path]:
    patterns = ("**/manifest.yml", "**/*.manifest.yml")
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(artifacts_root.glob(pattern)):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved


def _infer_layer(path: Path, payload: Mapping[str, Any]) -> str:
    if payload.get("layer") == "standardized":
        return "standardized"
    dataset = str(payload.get("dataset") or "").strip()
    if payload.get("source_asset_dir") or dataset.endswith("_file"):
        return "derived"
    if payload.get("entries") and payload.get("repo_root"):
        return "snapshot"
    if "/assets/" in path.as_posix():
        return "raw_asset"
    return "manifest"


def _artifact_path(path: Path, payload: Mapping[str, Any]) -> str:
    output_file = payload.get("output_file")
    if output_file is not None:
        return str(resolve_repo_path(output_file))
    output_root = payload.get("output_root")
    if output_root is not None:
        return str(resolve_repo_path(output_root))
    return str(path.parent.resolve())


def _extract_counts(
    payload: Mapping[str, Any],
) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    totals = cast(
        Mapping[str, Any],
        payload.get("totals") if isinstance(payload.get("totals"), Mapping) else {},
    )
    row_count = _maybe_int(totals.get("output_rows"))
    if row_count is None:
        row_count = _maybe_int(totals.get("rows"))
    symbol_count = _maybe_int(totals.get("symbols"))
    trade_date_count = _maybe_int(totals.get("trade_dates"))
    file_count = _maybe_int(totals.get("output_files"))
    if file_count is None:
        file_count = _maybe_int(totals.get("files"))
    total_bytes = _maybe_int(totals.get("bytes"))
    return row_count, symbol_count, trade_date_count, file_count, total_bytes


def _extract_range(payload: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    query = cast(
        Mapping[str, Any], payload.get("query") if isinstance(payload.get("query"), Mapping) else {}
    )
    grid = cast(
        Mapping[str, Any], payload.get("grid") if isinstance(payload.get("grid"), Mapping) else {}
    )
    start_value = (
        query.get("start_date")
        or query.get("start_quarter")
        or grid.get("start_date")
        or payload.get("start_date")
    )
    end_value = (
        query.get("end_date")
        or query.get("end_quarter")
        or grid.get("end_date")
        or payload.get("end_date")
    )
    frequency = query.get("frequency") or payload.get("frequency")
    return (
        str(start_value) if start_value not in {None, ""} else None,
        str(end_value) if end_value not in {None, ""} else None,
        str(frequency) if frequency not in {None, ""} else None,
    )


def _catalog_artifact_from_manifest(
    path: Path,
    payload: Mapping[str, Any],
) -> tuple[CatalogArtifact, list[str], list[CatalogLineage]]:
    dataset = str(payload.get("dataset") or "").strip() or None
    layer = _infer_layer(path, payload)
    created_at = payload.get("created_at")
    row_count, symbol_count, trade_date_count, file_count, total_bytes = _extract_counts(payload)
    start_value, end_value, frequency = _extract_range(payload)
    columns = list(payload.get("columns") or [])
    source = cast(
        Mapping[str, Any],
        payload.get("source") if isinstance(payload.get("source"), Mapping) else {},
    )
    metadata = {
        "query": payload.get("query"),
        "totals": payload.get("totals"),
        "quality": payload.get("quality"),
        "column_dtypes": payload.get("column_dtypes"),
    }
    artifact = CatalogArtifact(
        artifact_id=str(path.resolve()),
        layer=layer,
        dataset=dataset,
        market=str(payload.get("market") or "").strip() or None,
        name=str(payload.get("name") or path.parent.name),
        path=_artifact_path(path, payload),
        manifest_path=str(path.resolve()),
        status=str(payload.get("status") or "").strip() or None,
        output_format=str(payload.get("output_format") or "").strip() or None,
        created_at=str(created_at) if created_at not in {None, ""} else None,
        start_value=start_value,
        end_value=end_value,
        row_count=row_count,
        symbol_count=symbol_count,
        trade_date_count=trade_date_count,
        file_count=file_count,
        total_bytes=total_bytes,
        frequency=frequency,
        source_asset_dir=(
            str(payload.get("source_asset_dir") or source.get("asset_dir") or "").strip() or None
        ),
        source_manifest=(
            str(payload.get("source_manifest") or source.get("source_manifest") or "").strip()
            or None
        ),
        view_name=str(payload.get("view_name") or "").strip() or None,
        metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str),
    )
    lineages: list[CatalogLineage] = []
    if artifact.source_asset_dir:
        lineages.append(
            CatalogLineage(
                artifact_id=artifact.artifact_id,
                relation="source_asset_dir",
                source_path=artifact.source_asset_dir,
            )
        )
    if artifact.source_manifest:
        lineages.append(
            CatalogLineage(
                artifact_id=artifact.artifact_id,
                relation="source_manifest",
                source_path=artifact.source_manifest,
            )
        )
    source_file = str(payload.get("source_file") or source.get("file") or "").strip()
    if source_file:
        lineages.append(
            CatalogLineage(
                artifact_id=artifact.artifact_id,
                relation="source_file",
                source_path=source_file,
            )
        )

    entries = cast(
        list[Any], payload.get("entries") if isinstance(payload.get("entries"), list) else []
    )
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        source_path = entry.get("source")
        if source_path:
            lineages.append(
                CatalogLineage(
                    artifact_id=artifact.artifact_id,
                    relation="snapshot_entry",
                    source_path=str(source_path),
                )
            )
    return artifact, columns, lineages


def _init_catalog_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            layer TEXT,
            dataset TEXT,
            market TEXT,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            manifest_path TEXT NOT NULL,
            status TEXT,
            output_format TEXT,
            created_at TEXT,
            start_value TEXT,
            end_value TEXT,
            row_count INTEGER,
            symbol_count INTEGER,
            trade_date_count INTEGER,
            file_count INTEGER,
            total_bytes INTEGER,
            frequency TEXT,
            source_asset_dir TEXT,
            source_manifest TEXT,
            view_name TEXT,
            metadata_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artifact_columns (
            artifact_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            column_name TEXT NOT NULL,
            PRIMARY KEY (artifact_id, ordinal)
        );

        CREATE TABLE IF NOT EXISTS artifact_lineage (
            artifact_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            source_path TEXT NOT NULL
        );
        """
    )


def _write_catalog_summary_csv(conn: sqlite3.Connection, out_path: Path) -> None:
    rows = conn.execute(
        """
        SELECT
            artifact_id,
            layer,
            dataset,
            market,
            name,
            path,
            manifest_path,
            status,
            output_format,
            created_at,
            start_value,
            end_value,
            row_count,
            symbol_count,
            trade_date_count,
            file_count,
            total_bytes,
            frequency,
            source_asset_dir,
            source_manifest,
            view_name
        FROM artifacts
        ORDER BY layer, dataset, name
        """
    ).fetchall()
    headers = [
        "artifact_id",
        "layer",
        "dataset",
        "market",
        "name",
        "path",
        "manifest_path",
        "status",
        "output_format",
        "created_at",
        "start_value",
        "end_value",
        "row_count",
        "symbol_count",
        "trade_date_count",
        "file_count",
        "total_bytes",
        "frequency",
        "source_asset_dir",
        "source_manifest",
        "view_name",
    ]
    import pandas as pd

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=pd.Index(headers)).to_csv(out_path, index=False)


def refresh_catalog(args) -> int:
    artifacts_root = resolve_artifacts_root(getattr(args, "artifacts_root", None))
    db_path = resolve_metadata_db_path(
        getattr(args, "db_path", None),
        artifacts_root=artifacts_root,
    )
    summary_out = resolve_repo_path(
        getattr(args, "summary_out", None) or (db_path.parent / "catalog_summary.csv")
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)

    manifests = list(_manifest_paths(artifacts_root))
    artifacts: list[CatalogArtifact] = []
    artifact_columns: list[tuple[str, int, str]] = []
    lineages: list[CatalogLineage] = []
    for manifest_path in manifests:
        payload = _load_yaml(manifest_path)
        artifact, columns, artifact_lineages = _catalog_artifact_from_manifest(
            manifest_path, payload
        )
        artifacts.append(artifact)
        artifact_columns.extend(
            (artifact.artifact_id, idx, str(column)) for idx, column in enumerate(columns, start=1)
        )
        lineages.extend(artifact_lineages)

    with sqlite3.connect(db_path) as conn:
        _init_catalog_db(conn)
        conn.execute("DELETE FROM artifact_lineage")
        conn.execute("DELETE FROM artifact_columns")
        conn.execute("DELETE FROM artifacts")
        conn.executemany(
            """
            INSERT INTO artifacts (
                artifact_id, layer, dataset, market, name, path, manifest_path,
                status, output_format, created_at, start_value, end_value,
                row_count, symbol_count, trade_date_count, file_count, total_bytes,
                frequency, source_asset_dir, source_manifest, view_name, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.artifact_id,
                    item.layer,
                    item.dataset,
                    item.market,
                    item.name,
                    item.path,
                    item.manifest_path,
                    item.status,
                    item.output_format,
                    item.created_at,
                    item.start_value,
                    item.end_value,
                    item.row_count,
                    item.symbol_count,
                    item.trade_date_count,
                    item.file_count,
                    item.total_bytes,
                    item.frequency,
                    item.source_asset_dir,
                    item.source_manifest,
                    item.view_name,
                    item.metadata_json,
                )
                for item in artifacts
            ],
        )
        conn.executemany(
            "INSERT INTO artifact_columns (artifact_id, ordinal, column_name) VALUES (?, ?, ?)",
            artifact_columns,
        )
        conn.executemany(
            "INSERT INTO artifact_lineage (artifact_id, relation, source_path) VALUES (?, ?, ?)",
            [(item.artifact_id, item.relation, item.source_path) for item in lineages],
        )
        _write_catalog_summary_csv(conn, summary_out)
        artifact_count = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]

    print(f"Catalog refreshed: {artifact_count} artifacts -> {db_path} (summary: {summary_out})")
    return 0


def add_catalog_args(parser) -> None:
    from .data_warehouse_cli import add_catalog_args as add_args

    add_args(parser)


__all__ = [
    "CatalogArtifact",
    "CatalogLineage",
    "refresh_catalog",
    "add_catalog_args",
    "_git_metadata",
    "_timestamp_now",
]
