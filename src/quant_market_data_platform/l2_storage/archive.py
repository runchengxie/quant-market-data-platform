"""Strict 7-Zip inventory and extraction into owned scratch directories."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from .proof import load, save, sha_file

MODALITIES = {"行情.csv": "snapshots", "逐笔委托.csv": "orders", "逐笔成交.csv": "trades"}
SYMBOL = re.compile(r"\d{6}\.(SH|SZ|BJ)")


def member_path(value: str, date: str, directory=False) -> PurePosixPath:
    path = PurePosixPath(value)
    parts = path.parts
    if "\\" in value or path.is_absolute() or ".." in parts or ":" in value:
        raise ValueError(f"Unsafe member path: {value}")
    if str(path) != value or not parts or parts[0] != date:
        raise ValueError(f"Noncanonical member path: {value}")
    if len(parts) == 1 and directory:
        return path
    if len(parts) not in (2, 3) or not SYMBOL.fullmatch(parts[1]):
        raise ValueError(f"Unsupported member identity: {value}")
    if len(parts) == 2 and directory:
        return path
    if directory or len(parts) != 3 or parts[2] not in MODALITIES:
        raise ValueError(f"Unaccounted archive member: {value}")
    return path


def parse_listing(text: str, date: str) -> list[dict]:
    if not re.fullmatch(r"\d{8}", date) or "\n----------\n" not in text:
        raise ValueError("Expected a dated 7-Zip technical inventory")
    members, seen = [], set()
    for block in text.split("\n----------\n", 1)[1].split("\n\n"):
        info = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" not in info:
            continue
        name = info["Path"]
        attributes = info.get("Attributes", "").split()
        if (
            info.get("Encrypted") == "+"
            or info.get("Anti") == "+"
            or any("Link" in key for key in info)
            or any(item.startswith(("l", "b", "c", "p", "s")) for item in attributes)
        ):
            raise ValueError(f"Unsupported linked/encrypted member: {name}")
        directory = bool(attributes and attributes[0] == "D")
        member_path(name, date, directory)
        if name in seen:
            raise ValueError(f"Duplicate archive path: {name}")
        seen.add(name)
        if not directory:
            size = int(info["Size"])
            if size < 0:
                raise ValueError("Negative archive member size")
            members.append({"path": name, "bytes": size})
    if not members:
        raise ValueError("Archive has no supported CSV members")
    return members


def extract(archive: Path, scratch: Path, extractor: str, expected: str) -> Path:
    if sha_file(archive) != expected:
        raise ValueError("Archive SHA-256 differs from frozen input")
    date = archive.stem
    text = subprocess.check_output([extractor, "l", "-slt", str(archive)], text=True)
    members = parse_listing(text, date)
    marker = scratch / "extraction.json"
    destination = scratch / "csv"
    if marker.exists():
        if load(marker) != {"archive_sha256": expected, "members": members}:
            raise ValueError("Extraction checkpoint source changed")
    else:
        if destination.exists():
            raise ValueError("Incomplete extraction retained; use a new owned scratch directory")
        needed = sum(m["bytes"] for m in members) + (20 << 30)
        if shutil.disk_usage(scratch).free < needed:
            raise ValueError("Insufficient extraction space (including 20 GiB reserve)")
        destination.mkdir()
        with (scratch / "extraction.log").open("w") as log:
            subprocess.run(
                [
                    extractor,
                    "x",
                    "-y",
                    "-bd",
                    "-bb0",
                    "-mmt=2",
                    "-o" + str(destination),
                    str(archive),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        save(marker, {"archive_sha256": expected, "members": members})
    actual = []
    for path in destination.rglob("*"):
        if path.is_symlink():
            raise ValueError("Extracted symlink is not supported")
        if path.is_file():
            actual.append(
                {"path": path.relative_to(destination).as_posix(), "bytes": path.stat().st_size}
            )
    if sorted(actual, key=lambda m: m["path"]) != sorted(members, key=lambda m: m["path"]):
        raise ValueError("Extracted inventory differs from archive inventory")
    return destination
