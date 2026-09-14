from __future__ import annotations

import argparse
import shutil

import yaml

from market_data_platform.data_warehouse_materialize_part01 import (
    _build_materialize_manifest,
    _coerce_frequency,
    _collect_input_files,
    _infer_source_manifest,
    _materialize_column_defaults,
    _materialize_input_files,
    _sanitize_identifier,
)

from .artifacts import (
    resolve_artifacts_root,
    resolve_repo_path,
    standardized_dir_for,
)
from .data_warehouse_cli import add_materialize_args as _add_materialize_args_from_cli
from .data_warehouse_models import (
    MaterializeManifestInputs,
)


def materialize_standardized(args) -> int:
    preset = str(getattr(args, "preset", "generic") or "generic").strip().lower()
    dataset, date_col, symbol_col = _materialize_column_defaults(args)
    frequency = _coerce_frequency(getattr(args, "frequency", "D"))
    name = str(getattr(args, "name", "") or "").strip()
    if not name:
        raise SystemExit("--name is required.")

    asset_dir = resolve_repo_path(args.asset_dir) if getattr(args, "asset_dir", None) else None
    file_path = resolve_repo_path(args.file) if getattr(args, "file", None) else None
    input_files, source_mode = _collect_input_files(asset_dir=asset_dir, file_path=file_path)
    source_manifest = _infer_source_manifest(asset_dir=asset_dir, file_path=file_path)

    artifacts_root = resolve_artifacts_root(getattr(args, "artifacts_root", None))
    out_root = resolve_repo_path(
        getattr(args, "out_root", None) or standardized_dir_for(artifacts_root)
    )
    market = str(getattr(args, "market", "a_share") or "a_share").strip().lower()
    output_dir = out_root / market / dataset / name
    output_data_dir = output_dir / "data"
    if output_dir.exists():
        if not getattr(args, "force", False):
            raise SystemExit(f"Refusing to overwrite existing output: {output_dir}")
        shutil.rmtree(output_dir)
    output_data_dir.mkdir(parents=True, exist_ok=True)

    view_name = _sanitize_identifier(name)
    materialized = _materialize_input_files(
        input_files,
        output_data_dir=output_data_dir,
        date_col=date_col,
        symbol_col=symbol_col,
        frequency=frequency,
    )
    stats = materialized.to_stats()
    manifest = _build_materialize_manifest(
        MaterializeManifestInputs(
            name=name,
            dataset=dataset,
            market=market,
            preset=preset,
            frequency=frequency,
            source_mode=source_mode,
            asset_dir=asset_dir,
            file_path=file_path,
            source_manifest=source_manifest,
            output_dir=output_dir,
            output_data_dir=output_data_dir,
            view_name=view_name,
            date_col=date_col,
            symbol_col=symbol_col,
            column_dtypes=materialized.column_dtypes,
            stats=stats,
        )
    )
    manifest_path = output_dir / "manifest.yml"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    print(
        f"Materialized standardized layer to {output_dir} "
        f"({stats.output_rows} rows, {stats.output_files} files, view={view_name})"
    )
    return 0


def add_materialize_args(parser: argparse.ArgumentParser) -> None:
    _add_materialize_args_from_cli(parser)
