"""Direct, resumable all-column lossless Parquet delta/ZSTD12 repacker."""

from __future__ import annotations

import argparse
import pathlib
from contextlib import contextmanager

import pyarrow as pa
import pyarrow.parquet as pq

from .integrity import check_order, fingerprint, load, save, sha_file, volume
from .progress import Job, recover
from .repack_io import (
    config_digest,
    configuration,
    destination,
    own_output,
    plan,
    relative_file,
    write_index,
)

P = pathlib.Path


def writer_options(schema):
    encodings = {}
    for field in schema:
        if pa.types.is_string(field.type):
            encodings[field.name] = "DELTA_BYTE_ARRAY"
        elif (
            pa.types.is_integer(field.type)
            or pa.types.is_timestamp(field.type)
            or pa.types.is_date(field.type)
        ):
            encodings[field.name] = "DELTA_BINARY_PACKED"
        elif pa.types.is_floating(field.type):
            encodings[field.name] = "BYTE_STREAM_SPLIT"
    return {
        "compression": "zstd",
        "compression_level": 12,
        "use_dictionary": False,
        "row_group_size": 262144,
        "write_statistics": True,
        "write_page_checksum": True,
        "column_encoding": encodings,
    }


@contextmanager
def exclusive_lock(output):
    # OS advisory lock releases on process death. The stable file is never unlinked,
    # preventing a waiter from locking an orphaned inode after another process exits.
    from filelock import FileLock

    with FileLock(str(output / "repack.lock"), timeout=0):
        yield


def verify_bytes(path, info):
    if (
        not path.is_file()
        or path.stat().st_size != info["bytes"]
        or sha_file(path) != info["sha256"]
    ):
        raise ValueError(f"File integrity failed: {path}")


def read_bounded(path, limit=None):
    import psutil

    limit = min(1 << 30, psutil.virtual_memory().available // 6) if limit is None else limit
    batches = []
    decoded_bytes = 0
    with pq.ParquetFile(path, page_checksum_verification=True) as reader:
        if (
            sum(reader.metadata.row_group(i).total_byte_size for i in range(reader.num_row_groups))
            > 2 << 30
        ):
            raise ValueError("Input partition exceeds 2 GiB uncompressed page guard")
        schema = reader.schema_arrow
        for batch in reader.iter_batches(batch_size=8192):
            decoded_bytes += batch.nbytes
            if decoded_bytes > limit:
                raise ValueError("Partition exceeds decoded memory budget")
            batches.append(batch)
    return pa.Table.from_batches(batches, schema=schema)


def repack_file(source, output, row, digest):
    original = relative_file(source, row["file"])
    verify_bytes(original, row)
    relative = destination(row)
    target = output / relative
    if not target.resolve().is_relative_to(output):
        raise ValueError("Output partition escapes owned directory")
    proof_path = target.parent / "proof.json"
    if target.parent.exists():
        if not proof_path.is_file():
            raise ValueError("Unknown published partition; refusing overwrite")
        proof = load(proof_path)
        if proof["config_sha256"] != digest or proof["input_sha256"] != row["sha256"]:
            raise ValueError("Published proof configuration/input changed")
        verify_bytes(target, proof)
        return {**row, **proof, "file": relative.as_posix()}
    work = output / "work" / row["sha256"]
    recover(work, output, row["sha256"])
    work.mkdir(parents=True)
    table = read_bounded(original)
    if table.num_rows != row["rows"] or volume(table) != row["volume"]:
        raise ValueError("Input rows/volume differ from index")
    check_order(table, None)
    logical = fingerprint(table)
    schema = table.schema
    pq.write_table(table, work / "data.parquet", **writer_options(table.schema))
    del table
    restored = read_bounded(work / "data.parquet")
    if not schema.equals(restored.schema, check_metadata=True) or fingerprint(restored) != logical:
        raise ValueError("All-column readback integrity failed")
    proof = {
        "config_sha256": digest,
        "input_sha256": row["sha256"],
        "logical_sha256": logical,
        "rows": restored.num_rows,
        "volume": volume(restored),
        "bytes": (work / "data.parquet").stat().st_size,
        "sha256": sha_file(work / "data.parquet"),
    }
    save(work / "proof.json", proof)
    target.parent.parent.mkdir(parents=True, exist_ok=True)
    work.rename(target.parent)
    return {**row, **proof, "file": relative.as_posix()}


def release(output, source, config, rows):
    import duckdb

    if configuration(source) != config:
        raise ValueError("Source/config changed during run")
    if {output / row["file"] for row in rows} != set((output / "parquet").rglob("*.parquet")):
        raise ValueError("Unexpected output Parquet files")
    write_index(output, rows)
    with duckdb.connect(str(output / "catalog.duckdb")) as connection:
        pattern = (output / "parquet/**/*.parquet").as_posix().replace("'", "''")
        connection.execute(
            "CREATE OR REPLACE VIEW bars_by_security AS SELECT * FROM "
            f"read_parquet('{pattern}', hive_partitioning=true)"
        )
    summary = {
        "status": "complete",
        "files": len(rows),
        "rows": sum(r["rows"] for r in rows),
        "volume": sum(r["volume"] for r in rows),
        "parquet_bytes": sum(r["bytes"] for r in rows),
        "source_parquet_bytes": sum(r["bytes"] for r in plan(source)),
        "all_columns_logical_sha256_verified": True,
        "zstd_level": 12,
        "row_group_rows": 262144,
        "config_sha256": config_digest(config),
    }
    save(output / "summary.json", summary)
    return {**summary, "index": rows}


def run(source, output):
    source, output = P(source).resolve(), P(output).resolve()
    own_output(source, output)
    with exclusive_lock(output):
        job = Job(output)
        try:
            save(output / "summary.json", {"status": "running"})
            rows = plan(source)
            config = configuration(source)
            config_path = output / "run_config.json"
            if config_path.exists() and load(config_path) != config:
                raise ValueError("Configuration/input/code changed; use separate output")
            save(config_path, config)
            results = []
            for position, row in enumerate(rows):
                job.update(
                    phase="repacking",
                    completed_files=position,
                    total_files=len(rows),
                    verified_rows=sum(r["rows"] for r in results),
                    active_symbol=row["symbol"],
                )
                results.append(repack_file(source, output, row, config_digest(config)))
            result = release(output, source, config, results)
            job.finish(
                status="complete",
                phase="complete",
                completed_files=len(rows),
                verified_rows=result["rows"],
                parquet_bytes=result["parquet_bytes"],
            )
            return result
        except BaseException as exc:
            save(output / "summary.json", {"status": "failed", "error": str(exc)})
            job.finish(status="failed", error=str(exc))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    pa.set_cpu_count(2)
    pa.set_io_thread_count(2)
    run(args.source, args.output)


if __name__ == "__main__":
    main()
