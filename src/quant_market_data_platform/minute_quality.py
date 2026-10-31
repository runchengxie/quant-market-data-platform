"""Version-bound, opt-in minute research exclusions over immutable source values."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .published_assets import PublishedAssetContract, PublishedAssetRef, PublishedAssetSnapshot


class MinuteQualityError(ValueError):
    """A guarded minute request lacks required quality evidence or acknowledgement."""


def _sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise MinuteQualityError(f"Quality evidence disappeared: {path}") from exc


def _contained_path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise MinuteQualityError("Quality paths must be nonempty strings")
    path = Path(value)
    try:
        resolved = (root / path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MinuteQualityError("Quality path is missing or invalid") from exc
    if not resolved.is_relative_to(root):
        raise MinuteQualityError("Quality path escapes artifacts root")
    return resolved


def _date(value: Any) -> str:
    from .published_frames import _pandas

    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise MinuteQualityError("Quality dates must be YYYYMMDD strings")
    try:
        _pandas().Timestamp(value)
    except ValueError as exc:
        raise MinuteQualityError(f"Invalid quality date: {value}") from exc
    return value


def _market_scope(value: Any) -> list[str]:
    if isinstance(value, str) and value in {"SH_SZ", "SH_SZ_BJ"}:
        value = value.split("_")
    valid = (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item in {"SH", "SZ", "BJ"} for item in value)
    )
    if not valid or len(value) != len(set(value)):
        raise MinuteQualityError("market_scope must list unique SH/SZ/BJ or be SH_SZ/SH_SZ_BJ")
    return [market for market in ("SH", "SZ", "BJ") if market in value]


def _validate_requested_scope(instruments: Any, entries: Mapping[str, Any]) -> None:
    if instruments is None or isinstance(instruments, str):
        return
    suffixes = {str(symbol).rsplit(".", 1)[-1] for symbol in instruments}
    known = suffixes.intersection({"SH", "SZ", "BJ"})
    for key, entry in entries.items():
        outside = known - set(entry["market_scope"])
        if outside:
            raise MinuteQualityError(
                f"Requested market outside asset {key} scope: {sorted(outside)}"
            )


def local_timestamp(value: Any) -> Any:
    """Interpret naive timestamps in Shanghai and convert aware timestamps there."""
    from .published_frames import _pandas

    stamp = _pandas().Timestamp(value)
    if _pandas().isna(stamp):
        raise MinuteQualityError("Quality request timestamps cannot be NaT")
    return (
        stamp.tz_localize("Asia/Shanghai")
        if stamp.tzinfo is None
        else stamp.tz_convert("Asia/Shanghai")
    )


def local_index(values: Any) -> Any:
    from .published_frames import _pandas

    index = _pandas().DatetimeIndex(values)
    return (
        index.tz_localize("Asia/Shanghai")
        if index.tz is None
        else index.tz_convert("Asia/Shanghai")
    )


def _date_in_window(date: str, start: Any, end: Any) -> bool:
    return (start is None or date >= local_timestamp(start).strftime("%Y%m%d")) and (
        end is None or date <= local_timestamp(end).strftime("%Y%m%d")
    )


def _requested_date(date: str, instruments: Any, start: Any, end: Any) -> bool:
    if not _date_in_window(date, start, end):
        return False
    if instruments is None or isinstance(instruments, str):
        return True
    if not isinstance(instruments, Mapping):
        return bool(instruments)
    from .published_frames import _instrument_spans

    for raw_spans in instruments.values():
        spans = _instrument_spans(raw_spans)
        if spans is None or any(_date_in_window(date, left, right) for left, right in spans):
            return True
    return False


def _parquet_files(source: Path, asset_root: Path) -> tuple[Path, ...]:
    if source.is_dir():
        descendants = tuple(source.rglob("*"))
        if any(path.is_symlink() for path in descendants):
            raise MinuteQualityError("Mapped partition directory contains a symlink")
        files = tuple(sorted(path for path in descendants if path.suffix == ".parquet"))
    else:
        files = (source,)
    if not files or any(
        not path.is_file()
        or path.suffix != ".parquet"
        or path.resolve() != path
        or not path.is_relative_to(asset_root)
        for path in files
    ):
        raise MinuteQualityError("Mapped partition path is missing or outside asset root")
    return files


def _requested_issue(issue: Mapping[str, Any], instruments: Any, start: Any, end: Any) -> bool:
    if not _date_in_window(issue["trade_date"], start, end):
        return False
    if instruments is None:
        return True
    if isinstance(instruments, str):
        raise MinuteQualityError("Guarded requests require explicit instrument identifiers")
    if issue["symbol"] not in instruments:
        return False
    if not isinstance(instruments, Mapping):
        return True
    from .published_frames import _instrument_spans

    spans = _instrument_spans(instruments[issue["symbol"]])
    return spans is None or any(
        _date_in_window(issue["trade_date"], left, right) for left, right in spans
    )


class MinuteQualityGuard:
    """Pin registry, manifests and issue evidence; assess and filter one reader request."""

    def __init__(
        self,
        contract: PublishedAssetContract,
        mapped_assets: Iterable[tuple[PublishedAssetRef, str]],
        header: Mapping[str, Any],
        *,
        mode: str,
        acknowledged_missing_dates: Iterable[str],
    ) -> None:
        if mode not in {"error", "exclude_stock_days"}:
            raise MinuteQualityError("quality_mode must be error or exclude_stock_days")
        self.contract = contract
        mappings = tuple(mapped_assets)
        assets = {ref.key: ref for ref, _ in mappings}
        self.mode = mode
        self.acknowledged = sorted({_date(date) for date in acknowledged_missing_dates})
        self.root = contract.artifacts_root.resolve()
        registry_path = _contained_path(self.root, header.get("registry_path"))
        self.identities = {
            contract.path: contract.contract_sha256,
            registry_path: header.get("registry_sha256"),
        }
        self._validate_hashes()
        try:
            registry = json.loads(registry_path.read_text())
        except (ValueError, OSError) as exc:
            raise MinuteQualityError("Invalid minute quality registry") from exc
        if (
            not isinstance(registry, dict)
            or registry.get("schema_version") != "minute.quality_registry.v1"
        ):
            raise MinuteQualityError("Unsupported minute quality registry schema")
        entries = registry.get("assets")
        if not isinstance(entries, dict) or not entries:
            raise MinuteQualityError("Quality registry requires assets")
        self.entries: dict[str, dict[str, Any]] = {}
        for key, ref in assets.items():
            if key not in entries:
                raise MinuteQualityError(f"Missing minute quality asset: {key}")
            self.entries[key] = self._entry(entries[key], ref)
        self.mapped_sources: list[tuple[Path, Path, tuple[Path, ...]]] = []
        for ref, relative_path in mappings:
            self._bind_partitions(ref, relative_path)
        self.snapshot = PublishedAssetSnapshot(
            contract.path, contract.contract_sha256, tuple(assets.values())
        )
        self.validate()
        self._policy = {
            "registry_path": str(registry_path),
            "registry_sha256": header["registry_sha256"],
            "mode": mode,
            "acknowledged_missing_dates": self.acknowledged,
        }

    @property
    def policy(self) -> dict[str, Any]:
        """Return a detached policy identity suitable for receipts and fingerprints."""
        return json.loads(json.dumps(self._policy))

    def _entry(self, entry: Any, ref: PublishedAssetRef) -> dict[str, Any]:
        if not isinstance(entry, dict):
            raise MinuteQualityError("Quality asset entries must be objects")
        if _contained_path(self.root, entry.get("resolved_path")) != ref.resolved_path:
            raise MinuteQualityError(f"Quality asset identity mismatch: {ref.key}")
        if entry.get("manifest_sha256") != ref.manifest_sha256:
            raise MinuteQualityError(f"Quality manifest identity mismatch: {ref.key}")
        entry["market_scope"] = _market_scope(entry.get("market_scope"))
        for field in ("expected_dates", "available_dates"):
            values = entry.get(field)
            if not isinstance(values, list):
                raise MinuteQualityError(f"Quality {field} must be a unique date list")
            dates = [_date(value) for value in values]
            if len(dates) != len(set(dates)):
                raise MinuteQualityError(f"Quality {field} must be a unique date list")
            entry[field] = sorted(dates)
        if not set(entry["available_dates"]).issubset(entry["expected_dates"]):
            raise MinuteQualityError("Available dates must belong to expected dates")
        issues = entry.get("blocked_stock_days")
        if not isinstance(issues, list):
            raise MinuteQualityError("Quality blocked_stock_days must be a list")
        for issue in issues:
            self._issue(issue, entry)
        entry["blocked_stock_days"] = [
            issue for issue in issues if issue.get("severity") != "diagnostic"
        ]
        return entry

    def _bind_partitions(self, ref: PublishedAssetRef, relative_path: str) -> None:
        bindings = self.entries[ref.key].get("partition_sha256")
        if not isinstance(bindings, dict):
            raise MinuteQualityError("Quality asset requires partition_sha256 bindings")
        source = ref.resolve_data_path(relative_path)
        files = _parquet_files(source, ref.resolved_path)
        for path in files:
            key = path.relative_to(ref.resolved_path).as_posix()
            if key not in bindings:
                raise MinuteQualityError(f"Missing partition_sha256 binding: {key}")
            self.identities[path] = bindings[key]
        self.mapped_sources.append((source, ref.resolved_path, files))

    def validate_frame(self, key: str, frame: Any, request: tuple[Any, Any, Any]) -> None:
        instruments, start, end = request
        covered = set(local_index(frame.index.get_level_values("datetime")).strftime("%Y%m%d"))
        required = {
            date
            for date in self.entries[key]["available_dates"]
            if _requested_date(date, instruments, start, end)
        }
        absent = sorted(required - covered)
        if absent:
            raise MinuteQualityError(f"Minute mapping for {key} omits available dates: {absent}")
        symbols = frame.index.get_level_values("instrument").unique()
        markets = {str(symbol).rsplit(".", 1)[-1] for symbol in symbols}
        outside = markets - set(self.entries[key]["market_scope"])
        if outside:
            raise MinuteQualityError(
                f"Returned symbols outside asset {key} scope: {sorted(outside)}"
            )

    def _issue(self, issue: Any, entry: dict[str, Any]) -> None:
        if not isinstance(issue, dict) or not all(
            isinstance(issue.get(key), str) and issue[key] for key in ("symbol", "issue_id")
        ):
            raise MinuteQualityError("Quality issue requires symbol and issue_id")
        if _date(issue.get("trade_date")) not in entry["expected_dates"]:
            raise MinuteQualityError("Quality issue date outside expected dates")
        evidence = _contained_path(self.root, issue.get("evidence_path"))
        digest = issue.get("evidence_sha256")
        if evidence in self.identities and self.identities[evidence] != digest:
            raise MinuteQualityError("Conflicting quality evidence identities")
        self.identities[evidence] = digest

    def _validate_hashes(self) -> None:
        for path, expected in self.identities.items():
            try:
                physical = path.resolve(strict=True) == path and path.is_relative_to(self.root)
            except (OSError, RuntimeError) as exc:
                raise MinuteQualityError("Quality evidence identity changed") from exc
            if not physical:
                raise MinuteQualityError("Quality evidence identity changed or escaped root")
            if (
                not isinstance(expected, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected)
                or _sha256(path) != expected
            ):
                raise MinuteQualityError(f"Quality hash identity changed: {path}")

    def validate(self) -> None:
        for source, root, files in self.mapped_sources:
            if _parquet_files(source, root) != files:
                raise MinuteQualityError("Mapped partition inventory identity changed")
        self._validate_hashes()
        self.snapshot.validate_metadata()

    def assess(self, instruments: Any, start: Any, end: Any) -> dict[str, Any]:
        self.validate()
        _validate_requested_scope(instruments, self.entries)
        if start is not None and end is not None and local_timestamp(start) > local_timestamp(end):
            raise MinuteQualityError("Quality request end precedes start")
        missing = sorted(
            {
                date
                for entry in self.entries.values()
                for date in set(entry["expected_dates"]) - set(entry["available_dates"])
                if _requested_date(date, instruments, start, end)
            }
        )
        unacknowledged = sorted(set(missing) - set(self.acknowledged))
        if unacknowledged:
            raise MinuteQualityError(f"Unacknowledged missing minute dates: {unacknowledged}")
        issues = [
            dict(issue, asset_key=key)
            for key, entry in sorted(self.entries.items())
            for issue in entry["blocked_stock_days"]
            if _requested_issue(issue, instruments, start, end)
        ]
        issues.sort(
            key=lambda issue: (
                issue["trade_date"],
                issue["symbol"],
                issue["issue_id"],
                issue["asset_key"],
            )
        )
        if issues and self.mode == "error":
            raise MinuteQualityError(
                f"Blocked minute stock-days: {[issue['issue_id'] for issue in issues]}"
            )
        return {
            "schema_version": "minute.quality_decision.v1",
            **self.policy,
            "missing_dates": missing,
            "excluded_stock_days": issues,
            "excluded_rows": 0,
            "requested_window": {
                "start": local_timestamp(start).isoformat() if start is not None else None,
                "end": local_timestamp(end).isoformat() if end is not None else None,
            },
            "market_scopes": {
                key: json.loads(json.dumps(entry["market_scope"]))
                for key, entry in sorted(self.entries.items())
            },
        }

    def apply(self, data: Any, decision: dict[str, Any]) -> Any:
        dates = local_index(data.index.get_level_values("datetime")).strftime("%Y%m%d")
        symbols = data.index.get_level_values("instrument")
        keep = [True] * len(data)
        blocked = {
            (issue["trade_date"], issue["symbol"]) for issue in decision["excluded_stock_days"]
        }
        for position, pair in enumerate(zip(dates, symbols, strict=True)):
            if pair in blocked:
                keep[position] = False
        decision["excluded_rows"] = len(keep) - sum(keep)
        result = data[keep].copy()
        self.validate()
        result.attrs["minute_quality"] = decision
        return result
