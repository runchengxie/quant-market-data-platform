from __future__ import annotations

import argparse
import json

from .artifacts import (
    resolve_artifacts_root,
    resolve_repo_path,
    resolve_warehouse_db_path,
    standardized_dir_for,
)
from .data_warehouse_cli import add_query_args as _add_query_args_from_cli
from .warehouse_query import execute_standardized_query


def _read_sql(args) -> str:
    sql_text = str(getattr(args, "sql", "") or "").strip()
    sql_file = getattr(args, "sql_file", None)
    if sql_text and sql_file:
        raise SystemExit("Use either --sql or --sql-file, not both.")
    if sql_text:
        return sql_text
    if sql_file:
        return resolve_repo_path(sql_file).read_text(encoding="utf-8")
    raise SystemExit("Provide --sql or --sql-file.")


def query_standardized(args) -> int:
    artifacts_root = resolve_artifacts_root(getattr(args, "artifacts_root", None))
    db_path = resolve_warehouse_db_path(
        getattr(args, "db_path", None),
        artifacts_root=artifacts_root,
    )
    standardized_root = resolve_repo_path(
        getattr(args, "standardized_root", None) or standardized_dir_for(artifacts_root)
    )

    sql_text = _read_sql(args)
    result, registered = execute_standardized_query(
        sql_text,
        db_path=db_path,
        standardized_root=standardized_root,
    )

    output_format = str(getattr(args, "format", "text") or "text").strip().lower()
    out_path = resolve_repo_path(args.out) if getattr(args, "out", None) else None
    if output_format == "json":
        rendered = json.dumps(
            result.to_dict(orient="records"),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        if out_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(rendered, encoding="utf-8")
        else:
            print(rendered)
    elif output_format == "csv":
        if out_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            result.to_csv(out_path, index=False)
        else:
            print(result.to_csv(index=False), end="")
    elif output_format == "parquet":
        if out_path is None:
            raise SystemExit("--out is required when --format parquet.")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result.to_parquet(out_path, index=False)
    else:
        rendered = result.to_string(index=False)
        if out_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)

    if out_path:
        print(f"Wrote query result to {out_path} (registered {registered} standardized view(s))")
    return 0


def add_query_args(parser: argparse.ArgumentParser) -> None:
    _add_query_args_from_cli(parser)


__all__ = [
    "query_standardized",
    "add_query_args",
]
