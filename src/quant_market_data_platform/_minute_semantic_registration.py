"""Independent validation before recording acquisition-only semantic quarantine."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from quant_market_data_platform.minute_candidate import _atomic_write_json
from quant_market_data_platform.minute_semantic_quarantine import (
    REGISTRY,
    bound_path,
    load_registry,
    reference,
)
from quant_market_data_platform.providers._a_share_mins_partition import _partition_binding_matches
from quant_market_data_platform.providers._a_share_mins_validation import (
    _complete_symbols,
    _normalize_minute_frame,
    _validate_batch_frame,
)
from quant_market_data_platform.providers.a_share_minute_price_flow import price_flow_diagnostics
from quant_market_data_platform.quality_a_share_minute import _frame_validation_issues


def _validate_source(
    root: Path, record: dict, date: str, symbol: str
) -> tuple[Path, Path, pd.DataFrame]:
    partition = Path(record["path"]).resolve(strict=True)
    sidecar = Path(record["sidecar"]).resolve(strict=True)
    relative = partition.relative_to(root / "staging/tushare_minute_backfill_reverse/runs")
    if len(relative.parts) != 4 or relative.parts[1:] != (
        "data",
        f"trade_date={date}",
        "part-00000.parquet",
    ):
        raise ValueError("invalid quarantine source path")
    if sidecar != partition.with_name("_minute_mirror.json"):
        raise ValueError("invalid quarantine sidecar path")
    payload = json.loads(sidecar.read_text())
    if payload.get("trade_date") != date or payload.get("status") != "complete":
        raise ValueError("quarantine requires a complete acquired source")
    if not _partition_binding_matches(payload, partition.parent):
        raise ValueError("quarantine source binding mismatch")
    if (
        reference(root, partition)["sha256"] != record["partition_sha256"]
        or reference(root, sidecar)["sha256"] != record["sidecar_sha256"]
    ):
        raise ValueError("quarantine audit hash mismatch")
    frame = pq.ParquetFile(partition).read().to_pandas()
    normalized = _normalize_minute_frame(frame, context="quarantine source")
    expected = set(payload["expected_symbols"])
    if (
        set(normalized.ts_code) != expected
        or _complete_symbols(normalized, trade_date=date) != expected
    ):
        raise ValueError("quarantine source grid or universe failure")
    structural = _frame_validation_issues(date, normalized)
    flow = price_flow_diagnostics(normalized)
    if any(structural.values()) or flow["zero_volume_nonzero_amount_rows"]:
        raise ValueError("quarantine cannot waive structural errors")
    actual = (normalized.vol > 0) & (
        (normalized.amount < normalized.low * normalized.vol * 0.9 - 1)
        | (normalized.amount > normalized.high * normalized.vol * 1.1 + 1)
    )
    bad = normalized[actual]
    if bad.empty or set(bad.ts_code) != {symbol}:
        raise ValueError("quarantine symbol does not cover all semantic failures")
    issues = record["issues"]
    if not isinstance(issues, dict) or any(type(v) is not int or v < 0 for v in issues.values()):
        raise ValueError("invalid quarantine audit issues")
    if {k: v for k, v in issues.items() if v} != {
        "notional_beyond_hard_price_guard_rows": len(bad)
    }:
        raise ValueError("quarantine cannot waive other audit issues")
    return (
        partition,
        sidecar,
        normalized[normalized.ts_code.eq(symbol)].sort_values("trade_time").reset_index(drop=True),
    )


def register(  # noqa: PLR0913 - explicit evidence registration interface
    root: Path,
    runtime: Path,
    acceptance: Path,
    requery: Path,
    *,
    trade_date: str,
    symbol: str,
    reason: str,
) -> dict:
    if not reason.strip():
        raise ValueError("quarantine reason is required")
    acceptance, requery = acceptance.resolve(strict=True), requery.resolve(strict=True)
    marker = json.loads((runtime / "semantic-audit-blocked.json").read_text())
    if Path(marker["acceptance"]).resolve() != acceptance:
        raise ValueError("quarantine must bind the current failed acceptance")
    report = json.loads(acceptance.read_text())
    records = [r for r in report["partitions"] if r.get("trade_date") == trade_date]
    if (
        len(records) != 1
        or records[0].get("status") != "failed_semantics"
        or records[0].get("binding_matches") is not True
    ):
        raise ValueError("quarantine requires a bound failed semantic audit")
    partition, sidecar, original = _validate_source(root, records[0], trade_date, symbol)
    fresh, valid, problems = _validate_batch_frame(
        pq.ParquetFile(requery).read().to_pandas(),
        requested_symbols=[symbol],
        trade_date=trade_date,
    )
    fresh = fresh.sort_values("trade_time").reset_index(drop=True)
    if problems or valid != {symbol} or not original.equals(fresh):
        raise ValueError("requery must reproduce the complete original stock-day")
    entry = {
        "status": "quarantined",
        "trade_date": trade_date,
        "symbol": symbol,
        "reason": reason,
        "registered_at": datetime.now(UTC).isoformat(),
        "issues": records[0]["issues"],
        "partition": reference(root, partition),
        "sidecar": reference(root, sidecar),
        "acceptance": reference(root, acceptance),
        "requery": reference(root, requery),
        "publication_allowed": False,
        "source_values_modified": False,
    }
    data = load_registry(root)
    for existing in data["dates"].values():
        for name in ("partition", "sidecar", "acceptance", "requery"):
            bound_path(root, existing[name])
    if trade_date in data["dates"]:
        old = data["dates"][trade_date]
        if any(
            old[k] != entry[k]
            for k in ("symbol", "partition", "sidecar", "acceptance", "requery", "issues")
        ):
            raise ValueError("quarantine date is already bound to different evidence")
        return {"status": "quarantined", "entry": old, "registry": str(root / REGISTRY)}
    data["dates"][trade_date] = entry
    _atomic_write_json(root / REGISTRY, data)
    return {"status": "quarantined", "entry": entry, "registry": str(root / REGISTRY)}
