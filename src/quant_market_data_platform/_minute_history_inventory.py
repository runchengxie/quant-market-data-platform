"""Compact, provider-free inventory; never read minute table bodies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


def read_json(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("expected JSON object")
        return value
    except (OSError, ValueError) as exc:
        warnings.append(f"{path}: {exc}")
        return {}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _current_publication(root: Path, warnings: list[str]) -> dict[str, Any]:
    alias = root / "assets/derived/a_share/minute_1m_tushare"
    contract = root / "metadata/current_assets/a_share_current.json"
    if contract.exists():
        from quant_market_data_platform.published_assets import PublishedAssetContract

        try:
            alias = (
                PublishedAssetContract.load(contract, artifacts_root=root)
                .asset("minute_1m_tushare")
                .resolved_path
            )
        except (OSError, ValueError, KeyError) as exc:
            warnings.append(f"current contract: {exc}")
            return {}
    if not alias.exists():
        return {}
    from quant_market_data_platform._minute_operational_receipt import read_operational_receipt

    try:
        receipt_path, receipt = read_operational_receipt(alias.resolve())
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        warnings.append(f"current receipt: {exc}")
        return {}
    summary = receipt.get("summary", {})
    if not isinstance(summary, dict):
        warnings.append("invalid current receipt summary")
        return {}
    result: dict[str, Any] = {"receipt": str(receipt_path)}
    for key in ("date_min", "date_max", "market_scope", "dates", "rows"):
        value = summary.get(key)
        if value is None:
            continue
        valid = (
            type(value) is int and value >= 0
            if key in {"dates", "rows"}
            else isinstance(value, str)
        )
        if not valid:
            warnings.append(f"invalid current receipt summary: {key}")
            return {}
        result[key] = value
    return result


def _deferred_count(metadata: Path, warnings: list[str]) -> int | None:
    path = metadata / "deferred_missing.json"
    if not path.exists():
        return None
    queue = read_json(path, warnings).get("dates")
    if not isinstance(queue, dict) or any(
        not isinstance(day, str) or len(day) != 8 or not day.isdigit() or not isinstance(item, dict)
        for day, item in queue.items()
    ):
        warnings.append("invalid deferred missing queue")
        return None
    return len(queue)


def _open_dates(root: Path, minimum: str | None, warnings: list[str]) -> set[str] | None:
    path = root / "assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet"
    if not path.exists() or not minimum:
        return None
    try:
        table = pq.ParquetFile(path).read(columns=["cal_date", "is_open"]).to_pydict()
        return {
            str(day).replace("-", "")[:8]
            for day, opened in zip(table["cal_date"], table["is_open"], strict=True)
            if str(opened) == "1" and "20160101" <= str(day) < minimum
        }
    except (OSError, ValueError, KeyError) as exc:
        warnings.append(f"calendar: {exc}")
        return None


def _records(report: dict, warnings: list[str]) -> list:
    records = report.get("partitions", report.get("records", []))
    if not isinstance(records, list):
        warnings.append("invalid evidence record collection")
        return []
    return records


def _accepted_bj(
    metadata: Path, versions: dict[str, tuple[str, str]], warnings: list[str]
) -> set[str]:
    report = read_json(metadata / "bj_missing_report.json", warnings)
    accepted = set()
    for record in _records(report, warnings):
        if not isinstance(record, dict):
            warnings.append("invalid BJ report partition")
            continue
        path = record.get("sidecar")
        version = versions.get(str(Path(path).resolve())) if isinstance(path, str) else None
        if version and version[1] == record.get("sidecar_sha256"):
            accepted.add(version[0])
        else:
            warnings.append(f"BJ report sidecar binding mismatch: {path}")
    return accepted


def _audited_dates(
    metadata: Path, versions: dict[str, tuple[str, str]], warnings: list[str]
) -> tuple[dict[str, set[str]], int, set[str]]:
    scopes: dict[str, set[str]] = {
        name: set()
        for name in (
            "passed_requested_universe_only",
            "passed_sh_sz_with_bj_omissions",
            "passed_full_market",
        )
    }
    expired = 0
    expired_dates: set[str] = set()
    paths = list(metadata.glob("**/*audit*.json"))
    paths.extend(metadata.glob("batch-acceptance/*/acceptance.json"))
    for path in paths:
        report = read_json(path, warnings)
        for record in _records(report, warnings):
            if not isinstance(record, dict):
                warnings.append(f"{path}: invalid audit record")
                continue
            sidecar = record.get("sidecar", record.get("sidecar_path"))
            bound = versions.get(str(Path(sidecar).resolve())) if isinstance(sidecar, str) else None
            if bound is None or bound[1] != record.get("sidecar_sha256"):
                expired += 1
                day = bound[0] if bound else str(record.get("trade_date", ""))
                if len(day) == 8 and day.isdigit():
                    expired_dates.add(day)
                continue
            issues = record.get("issues", record.get("issue_count"))
            clean = (
                issues == []
                or type(issues) is int
                and issues == 0
                or (
                    isinstance(issues, dict)
                    and bool(issues)
                    and all(type(value) is int and value == 0 for value in issues.values())
                )
            )
            scope = record.get("status")
            if not isinstance(scope, str):
                warnings.append(f"{path}: invalid audit status")
                continue
            if scope in scopes and record.get("binding_matches") is True and clean:
                scopes[scope].add(bound[0])
    return scopes, expired, expired_dates


def inventory(root: Path, warnings: list[str], runtime_root: Path | None = None) -> dict[str, Any]:
    metadata = root / "metadata/minute_backfill/reverse_scheduler"
    staging = root / "staging/tushare_minute_backfill_reverse"
    complete: set[str] = set()
    versions: dict[str, tuple[str, str]] = {}
    for sidecar in staging.glob("runs/*/data/trade_date=*/_minute_mirror.json"):
        payload = read_json(sidecar, warnings)
        day = str(payload.get("trade_date", ""))
        if len(day) != 8 or not day.isdigit() or sidecar.parent.name != f"trade_date={day}":
            warnings.append(f"{sidecar}: invalid trade date")
            continue
        versions[str(sidecar.resolve())] = (day, _sha(sidecar))
        if payload.get("status") == "complete":
            complete.add(day)
    bj = _accepted_bj(metadata, versions, warnings)
    scopes, expired, expired_dates = _audited_dates(metadata, versions, warnings)
    if runtime_root is not None:
        runtime_scopes, runtime_expired, runtime_expired_dates = _audited_dates(
            runtime_root, versions, warnings
        )
        expired += runtime_expired
        expired_dates.update(runtime_expired_dates)
        for name, dates in runtime_scopes.items():
            scopes[name].update(dates)
    audited = set().union(*scopes.values())
    publication = _current_publication(root, warnings)
    minimum = publication.get("date_min")
    open_dates = _open_dates(root, minimum, warnings)
    return {
        "metadata_complete_dates": len(complete),
        "accepted_bj_omission_dates": len(bj),
        "independently_audited_dates": len(audited),
        "audit_scope_dates": {name: len(dates) for name, dates in scopes.items()},
        "expired_audit_bindings": expired,
        "expired_audit_dates": len(expired_dates),
        "superseded_expired_audit_dates": len(expired_dates & audited),
        "unresolved_expired_audit_dates": len(expired_dates - audited),
        "settled_dates": len(complete | bj),
        "remaining_open_dates": len(open_dates - complete - bj) if open_dates is not None else None,
        "current_min_date": minimum,
        "deferred_dates": _deferred_count(metadata, warnings),
        "published_asset": publication,
        "full_market_ready": False,
    }
