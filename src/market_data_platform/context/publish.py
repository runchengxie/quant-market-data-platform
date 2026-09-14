from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from market_data_platform.contract import build_current_contract, write_current_contract
from market_data_platform.paths import current_contract_path, resolve_artifacts_root

from .models import normalize_context_catalog, validate_context_observations


def _safe_remove_alias(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
            return
        raise FileExistsError(f"refusing to replace non-file context alias: {path}")


def _replace_alias(alias: Path, target: Path) -> None:
    alias.parent.mkdir(parents=True, exist_ok=True)
    temporary = alias.with_name(f".{alias.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise FileExistsError(f"context alias temporary path already exists: {temporary}")
    if target.is_dir():
        shutil.copytree(target, temporary, symlinks=False)
    else:
        shutil.copy2(target, temporary)
    _safe_remove_alias(alias)
    temporary.replace(alias)


def _manifest(  # noqa: PLR0913
    *,
    dataset: str,
    schema_version: str,
    output_dir: Path,
    as_of: str,
    rows: int,
    lineage: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "provider": "composite",
        "schema_version": schema_version,
        "status": "completed",
        "output_dir": str(output_dir),
        "as_of_date": as_of,
        "totals": {"rows": int(rows)},
        "lineage": {"sources": [dict(item) for item in lineage]},
    }


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(dict(payload), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _write_single_file_asset(  # noqa: PLR0913
    parent: Path,
    *,
    stem: str,
    frame: pd.DataFrame,
    as_of: str,
    schema_version: str,
    lineage: Sequence[Mapping[str, Any]],
) -> None:
    parent.mkdir(parents=True, exist_ok=True)
    version_file = parent / f"{stem}_{as_of}.parquet"
    version_manifest = parent / f"{stem}_{as_of}.manifest.yml"
    frame.to_parquet(version_file, index=False)
    _write_yaml(
        version_manifest,
        _manifest(
            dataset=stem,
            schema_version=schema_version,
            output_dir=version_file,
            as_of=as_of,
            rows=len(frame),
            lineage=lineage,
        ),
    )
    alias = parent / f"{stem}_latest.parquet"
    alias_manifest = parent / f"{stem}_latest.manifest.yml"
    _replace_alias(alias, version_file)
    _replace_alias(alias_manifest, version_manifest)


def _write_directory_asset(  # noqa: PLR0913
    parent: Path,
    *,
    stem: str,
    frame: pd.DataFrame,
    as_of: str,
    schema_version: str,
    lineage: Sequence[Mapping[str, Any]],
) -> None:
    parent.mkdir(parents=True, exist_ok=True)
    version_dir = parent / f"{stem}_{as_of}"
    if version_dir.exists():
        raise FileExistsError(f"context publication already exists: {version_dir}")
    version_dir.mkdir()
    try:
        frame.to_parquet(version_dir / "data.parquet", index=False)
        _write_yaml(
            version_dir / "manifest.yml",
            _manifest(
                dataset=stem,
                schema_version=schema_version,
                output_dir=version_dir,
                as_of=as_of,
                rows=len(frame),
                lineage=lineage,
            ),
        )
    except Exception:
        shutil.rmtree(version_dir)
        raise
    _replace_alias(parent / f"{stem}_latest", version_dir)


def publish_context_assets(  # noqa: PLR0913
    artifacts_root: str | Path | None,
    *,
    catalog: pd.DataFrame,
    observations: pd.DataFrame,
    pit: pd.DataFrame,
    release_calendar: pd.DataFrame,
    as_of: str,
    lineage: Sequence[Mapping[str, Any]],
) -> Path:
    root = resolve_artifacts_root(artifacts_root)
    normalized_catalog = normalize_context_catalog(catalog)
    normalized_observations = validate_context_observations(observations)
    normalized_pit = validate_context_observations(pit)

    base = root / "assets" / "context" / "cn"
    _write_single_file_asset(
        base / "catalog",
        stem="cn_context_catalog",
        frame=normalized_catalog,
        as_of=as_of,
        schema_version="cn_context.catalog.v1",
        lineage=lineage,
    )
    _write_directory_asset(
        base / "normalized",
        stem="cn_context_observations",
        frame=normalized_observations,
        as_of=as_of,
        schema_version="cn_context.observations.v1",
        lineage=lineage,
    )
    _write_directory_asset(
        base / "pit",
        stem="cn_context_pit",
        frame=normalized_pit,
        as_of=as_of,
        schema_version="cn_context.pit.v1",
        lineage=lineage,
    )
    _write_single_file_asset(
        base / "release_calendar",
        stem="cn_context_release_calendar",
        frame=release_calendar.copy(),
        as_of=as_of,
        schema_version="cn_context.release_calendar.v1",
        lineage=lineage,
    )

    contract = build_current_contract(
        root,
        market="cn_context",
        provider="composite",
        generated_by="marketdata context publish",
        target_date=as_of,
    )
    output = current_contract_path(root, market="cn_context")
    write_current_contract(output, contract)
    return output
