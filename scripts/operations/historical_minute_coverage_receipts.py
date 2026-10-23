"""Issue and replay bounded retrospective coverage receipts; never publish data."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from scripts.operations import historical_minute_candidate_resolution as candidates
from scripts.operations import historical_minute_coverage_pilot as pilot

MAX_PARTITION_ROWS = 2_000_000
SCHEMA = "historical_minute_retrospective_coverage.v1"
PASSED = "passed_retrospective_independent_coverage"
BOUNDARIES = {
    "historical_pit_certified": False,
    "full_market_promotion_allowed": False,
    "cross_source_acceptance": "not_evaluated_requires_0.99_gate",
    "original_producer_revision": "unknown",
}


def evaluate(manifest: dict[str, Any], paths: dict[str, Path]) -> list[dict[str, Any]]:
    pilot.validate_dates(manifest["dates"])
    quarantine = json.loads(paths[manifest["quarantine"]].read_text())["dates"]
    results = []
    for original in manifest["dates"]:
        row = dict(original)
        for key in row["partitions"]:
            if pq.ParquetFile(paths[key]).metadata.num_rows > MAX_PARTITION_ROWS:
                raise ValueError("Partition exceeds bounded row limit")
        minute = {paths[k].resolve() for k in row["partitions"]}
        daily = {paths[k].resolve() for k in row["daily"]}
        basic = {paths[k].resolve() for k in row["daily_basic"]}
        if minute & (daily | basic) or daily & basic:
            raise ValueError("Require distinct independent reference sources")
        row["quarantined"] |= row["date"] in quarantine
        resolution = manifest.get("resolutions", {}).get(row["date"])
        adjudication = None
        if resolution is not None:
            adjudication = candidates.resolve(row, resolution, paths)
            row["partitions"] = row["partitions"][:1]
        result = pilot.inspect_date(row, paths)
        result["candidate_resolution"] = adjudication
        result.pop("seconds")
        result.pop("eligible_for_receipt_review")
        result.update(BOUNDARIES)
        result["status"] = "unresolved" if result["unresolved"] else PASSED
        result["selected_partition"] = pilot.bind(paths[row["partitions"][0]])
        results.append(result)
    return results


def source_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    relevant = ["src", "scripts/operations"]
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--", *relevant], cwd=root, text=True
    )
    if dirty:
        raise ValueError("Receipt requires committed auditor source")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    # Bind the whole executed source surface, including transitive semantic/transition helpers.
    files = subprocess.check_output(
        ["git", "ls-files", *relevant], cwd=root, text=True
    ).splitlines()
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
    return {"revision": revision, "files": hashes}


def load_inputs(manifest_path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    manifest = json.loads(manifest_path.read_text())
    bindings = manifest["inputs"]
    if sum(b["bytes"] for b in bindings.values()) > 2 * 1024**3:
        raise ValueError("Bounded receipt inputs exceed 2 GiB")
    paths = {key: pilot.verify(value) for key, value in bindings.items()}
    return manifest, paths


def build(manifest_path: Path) -> dict[str, Any]:
    source = source_identity()
    binding = pilot.bind(manifest_path)
    manifest, paths = load_inputs(manifest_path)
    records = evaluate(manifest, paths)
    for value in manifest["inputs"].values():
        pilot.verify(value)
    pilot.verify(binding)
    if source_identity() != source:
        raise ValueError("Auditor source changed during scan")
    return dict(
        schema_version=SCHEMA,
        auditor=source,
        manifest=binding,
        records=records,
        provider_requests=0,
        **BOUNDARIES,
    )


def issue(manifest_path: Path, output: Path) -> dict[str, Any]:
    report = build(manifest_path)
    with output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    return report


def validate_receipt(receipt: Path, sha256: str, date: str, partition: Path) -> dict[str, Any]:
    binding = pilot.bind(receipt)
    if binding["sha256"] != sha256:
        raise ValueError("Unexpected receipt hash")
    report = json.loads(receipt.read_text())
    if report.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported receipt schema")
    manifest_path = pilot.verify(report["manifest"])
    fresh = build(manifest_path)
    # A later merge commit may execute identical source. Content, not checkout name, is decisive.
    if fresh["auditor"]["files"] != report["auditor"]["files"]:
        raise ValueError("Auditor source differs; regenerate the receipt")
    fresh["auditor"] = report["auditor"]
    if json.loads(json.dumps(fresh)) != report:
        raise ValueError("Receipt does not match independently replayed evidence")
    matches = [row for row in report["records"] if row["date"] == date]
    if len(matches) != 1 or matches[0]["status"] != PASSED:
        raise ValueError("Requested date has no passing independent coverage receipt")
    if pilot.bind(partition) != matches[0]["selected_partition"]:
        raise ValueError("Requested partition does not match receipt")
    pilot.verify(binding)
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("issue")
    generate.add_argument("--manifest", required=True, type=Path)
    generate.add_argument("--output", required=True, type=Path)
    validate = commands.add_parser("validate")
    validate.add_argument("--receipt", required=True, type=Path)
    validate.add_argument("--sha256", required=True)
    validate.add_argument("--date", required=True)
    validate.add_argument("--partition", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "issue":
        report = issue(args.manifest, args.output)
        print(json.dumps({"statuses": {r["date"]: r["status"] for r in report["records"]}}))
    else:
        result = validate_receipt(args.receipt, args.sha256, args.date, args.partition)
        print(json.dumps({"date": result["date"], "status": result["status"], **BOUNDARIES}))


if __name__ == "__main__":
    main()
