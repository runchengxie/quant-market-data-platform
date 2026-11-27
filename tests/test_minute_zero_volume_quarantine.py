"""Reproduced source failures permit acquisition, never whole-date publication."""

import json
from unittest.mock import patch

import pandas as pd
import pytest

from quant_market_data_platform import minute_semantic_quarantine as quarantine
from quant_market_data_platform.providers._a_share_mins_constants import _MinuteProgress
from quant_market_data_platform.providers._a_share_mins_partition import (
    _persist_progress,
    _universe_hash,
    validate_complete_minute_partition,
)
from quant_market_data_platform.providers.tushare_a_share_options import TushareRequestPolicy
from quant_market_data_platform.quality_a_share_minute import _frame_validation_issues

DATE = "20150428"
SYMBOLS = ["000001.SZ", "000002.SZ"]


@pytest.fixture
def evidence(tmp_path):
    times = pd.date_range(f"{DATE} 09:30", f"{DATE} 11:30", freq="min").append(
        pd.date_range(f"{DATE} 13:01", f"{DATE} 15:00", freq="min")
    )
    frames = []
    for code in [*SYMBOLS, "000003.SZ"]:
        frame = pd.DataFrame(
            {
                "ts_code": code,
                "trade_time": times,
                "open": 10.0,
                "close": 10.0,
                "high": 10.0,
                "low": 10.0,
                "vol": 100.0,
                "amount": 1000.0,
            }
        )
        if code in SYMBOLS:
            frame.loc[215, ["vol", "amount"]] = [0.0, 0.25]
        frames.append(frame)
    return _write_evidence(tmp_path, pd.concat(frames, ignore_index=True))


def _write_evidence(root, frame, *, date=DATE, name="audit"):
    part = root / f"staging/tushare_minute_backfill_reverse/runs/source/data/trade_date={date}"
    codes = set(frame.ts_code)
    progress = _MinuteProgress(
        part_dir=part,
        trade_date=date,
        freq="1min",
        expected_symbols=frozenset(codes),
        universe_hash=_universe_hash(codes, rule="explicit_symbols"),
        universe_rule="explicit_symbols",
        universe_source="fixture",
        request_policy=TushareRequestPolicy(),
    )
    _persist_progress(progress, completed_symbols=codes, frames=[frame], status="complete")
    source = part / "part-00000.parquet"
    sidecar = part / "_minute_mirror.json"
    report = root / f"reports/{name}.json"
    report.parent.mkdir(exist_ok=True)
    from quant_market_data_platform.providers.a_share_minute_price_flow import (
        price_flow_diagnostics,
    )

    issues = _frame_validation_issues(date, frame)
    flow = price_flow_diagnostics(frame)
    for issue in ("zero_volume_nonzero_amount_rows", "notional_beyond_hard_price_guard_rows"):
        issues[issue] = flow[issue]
    record = {
        "trade_date": date,
        "path": str(source),
        "sidecar": str(sidecar),
        "partition_sha256": quarantine.file_hash(source),
        "sidecar_sha256": quarantine.file_hash(sidecar),
        "status": "failed_semantics",
        "binding_matches": True,
        "issues": issues,
    }
    report.write_text(json.dumps({"partitions": [record]}))
    runtime = root / "runtime"
    runtime.mkdir(exist_ok=True)
    marker = runtime / "semantic-audit-blocked.json"
    marker.write_text(json.dumps({"acceptance": str(report)}))
    requery = root / f"reports/{name}-requery.parquet"
    frame[frame.ts_code.isin(SYMBOLS)].to_parquet(requery, index=False)
    return root, runtime, report, requery, part


def _register(evidence, symbols=None):
    root, runtime, report, requery, _ = evidence
    return quarantine.register_zero_volume_quarantine(
        root,
        runtime,
        report,
        requery,
        trade_date=DATE,
        symbols=SYMBOLS if symbols is None else symbols,
        reason="Independently reproduced provider source values",
    )


def test_exact_multistock_reproduction_preserves_source_and_blocks_publication(evidence):
    root, runtime, report, requery, part = evidence
    files = [*part.iterdir(), report, requery, runtime / "semantic-audit-blocked.json"]
    before = {p: p.read_bytes() for p in files}
    result = _register(evidence, list(reversed(SYMBOLS)))
    entry = result["entry"]
    assert entry["symbols"] == SYMBOLS
    assert entry["scope"] == "trade_date"
    assert entry["anomaly_kind"] == "zero_volume_nonzero_amount_rows"
    assert "symbol" not in entry
    assert entry["publication_allowed"] is False and entry["source_values_modified"] is False
    assert (
        quarantine.load_registry(root)["schema_version"] == "tushare.minute.semantic_quarantine.v2"
    )
    assert all(p.read_bytes() == body for p, body in before.items())
    assert not quarantine.quarantine_status(root, runtime / "semantic-audit-blocked.json")[
        "blocked"
    ]
    for path in (root, part):
        with pytest.raises(ValueError, match="quarantin"):
            quarantine.reject_quarantined_dates(path, [DATE])
    with pytest.raises(ValueError, match="quarantin"):
        validate_complete_minute_partition(part, trade_date=DATE, require_full_universe=False)
    quarantine.reject_quarantined_dates(root, ["20150429"])


@pytest.mark.parametrize("symbols", [[], SYMBOLS[:1], [*SYMBOLS, "000003.SZ"], SYMBOLS * 2])
def test_symbol_scope_must_exactly_cover_all_failure_symbols(evidence, symbols):
    with pytest.raises(ValueError, match="symbol"):
        _register(evidence, symbols)
    assert not (evidence[0] / quarantine.REGISTRY).exists()


@pytest.mark.parametrize("change", ["missing_stock", "extra_stock", "missing_bar", "value", "date"])
def test_requery_requires_exact_complete_unchanged_stock_days(evidence, change):
    _, _, _, requery, part = evidence
    frame = pd.read_parquet(requery)
    if change == "missing_stock":
        frame = frame[frame.ts_code.ne(SYMBOLS[0])]
    elif change == "extra_stock":
        frame = pd.read_parquet(part / "part-00000.parquet")
    elif change == "missing_bar":
        frame = frame.iloc[1:]
    elif change == "date":
        frame.loc[0, "trade_time"] += pd.Timedelta(days=1)
    else:
        frame.loc[0, "amount"] += 1
    frame.to_parquet(requery, index=False)
    with pytest.raises(ValueError, match="requery|unexpected symbols"):
        _register(evidence)


@pytest.mark.parametrize(
    "issue", ["unknown_failure", "wrong_date_rows", "notional_beyond_hard_price_guard_rows"]
)
def test_other_audit_failures_are_never_waived(evidence, issue):
    report = evidence[2]
    data = json.loads(report.read_text())
    data["partitions"][0]["issues"][issue] = 1
    report.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="other audit issues"):
        _register(evidence)


def test_audit_counts_must_match_recomputed_source(evidence):
    report = evidence[2]
    data = json.loads(report.read_text())
    data["partitions"][0]["issues"]["zero_volume_nonzero_amount_rows"] = 0
    report.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        _register(evidence)


def test_unreported_notional_failure_still_blocks(evidence):
    frame = pd.read_parquet(evidence[4] / "part-00000.parquet")
    frame.loc[0, "amount"] = 50000.0
    evidence = _write_evidence(evidence[0], frame)
    with pytest.raises(ValueError, match="notional"):
        _register(evidence)


def test_registration_is_idempotent_and_atomic(evidence):
    root = evidence[0]
    first = _register(evidence)
    path = root / quarantine.REGISTRY
    before = path.read_bytes()
    assert _register(evidence)["entry"] == first["entry"]
    assert path.read_bytes() == before


def test_shared_writer_lock_prevents_concurrent_registration(evidence):
    from quant_market_data_platform._minute_history_lock import (
        HistoryWriterBusy,
        history_writer_lock,
    )

    with history_writer_lock(evidence[0]):
        with pytest.raises(HistoryWriterBusy):
            _register(evidence)
    assert not (evidence[0] / quarantine.REGISTRY).exists()


def test_preview_validates_real_evidence_without_lock_or_any_writes(evidence):
    root, runtime, report, requery, _ = evidence
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = quarantine.register_zero_volume_quarantine(
        root,
        runtime,
        report,
        requery,
        trade_date=DATE,
        symbols=SYMBOLS,
        reason="Evidence preview",
        dry_run=True,
    )
    assert result["status"] == "preview"
    assert result["entry"]["symbols"] == SYMBOLS
    assert result["would_update_registry"] is True
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_v2_retains_legacy_entries(evidence):
    root = evidence[0]
    frame = pd.read_parquet(evidence[3])
    frame = frame[frame.ts_code.eq(SYMBOLS[0])].reset_index(drop=True)
    frame["trade_time"] -= pd.Timedelta(days=1)
    frame.loc[:, ["vol", "amount"]] = [100.0, 1000.0]
    frame.loc[0, "amount"] = 50000.0
    _, runtime, report, requery, _ = _write_evidence(root, frame, date="20150427", name="legacy")
    legacy = quarantine.register_quarantine(
        root,
        runtime,
        report,
        requery,
        trade_date="20150427",
        symbol=SYMBOLS[0],
        reason="Reproduced single-stock hard-notional failure",
    )["entry"]
    assert quarantine.load_registry(root)["schema_version"] == quarantine.SCHEMA
    additional = runtime / "semantic-audit-additional-blocks"
    additional.mkdir()
    (additional / "zero-volume.json").write_text(json.dumps({"acceptance": str(evidence[2])}))
    _register(evidence)
    registry = quarantine.load_registry(root)
    assert registry["dates"]["20150427"] == legacy
    assert registry["schema_version"] != quarantine.SCHEMA
    assert not quarantine.quarantine_status(root, runtime / "semantic-audit-blocked.json")[
        "blocked"
    ]


def test_interrupted_registration_leaves_audit_blocked(evidence):
    from quant_market_data_platform import _minute_semantic_registration as registration

    with patch.object(registration, "_atomic_write_json", side_effect=OSError("interrupted")):
        with pytest.raises(OSError, match="interrupted"):
            _register(evidence)
    assert not (evidence[0] / quarantine.REGISTRY).exists()
    assert quarantine.quarantine_status(evidence[0], evidence[1] / "semantic-audit-blocked.json")[
        "blocked"
    ]


@pytest.mark.parametrize("target", ["partition", "sidecar", "acceptance", "requery"])
def test_changed_bound_evidence_blocks_acquisition(evidence, target):
    entry = _register(evidence)["entry"]
    path = evidence[0] / entry[target]["path"]
    path.write_bytes(path.read_bytes() + b" ")
    assert quarantine.quarantine_status(evidence[0], evidence[1] / "semantic-audit-blocked.json")[
        "blocked"
    ]


def test_unknown_additional_marker_remains_blocking(evidence):
    _register(evidence)
    root, runtime, *_ = evidence
    additional = runtime / "semantic-audit-additional-blocks"
    additional.mkdir()
    report = root / "reports/unknown.json"
    report.write_text(json.dumps({"partitions": []}))
    (additional / "unknown.json").write_text(json.dumps({"acceptance": str(report)}))
    assert quarantine.quarantine_status(root, runtime / "semantic-audit-blocked.json")["blocked"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", "stock_day"),
        ("symbols", []),
        ("symbols", SYMBOLS * 2),
        ("publication_allowed", True),
        ("source_values_modified", True),
        ("anomaly_kind", "other"),
        ("reason", ""),
        ("symbols", ["invalid"]),
        ("issues", {"zero_volume_nonzero_amount_rows": True}),
        ("issues", {"zero_volume_nonzero_amount_rows": 1}),
    ],
)
def test_malformed_v2_disposition_is_fail_closed(evidence, field, value):
    _register(evidence)
    path = evidence[0] / quarantine.REGISTRY
    data = json.loads(path.read_text())
    data["dates"][DATE][field] = value
    path.write_text(json.dumps(data))
    assert quarantine.quarantine_status(evidence[0], None)["blocked"]
    with pytest.raises(ValueError):
        quarantine.reject_quarantined_dates(evidence[0], [DATE])


def test_removing_v2_category_cannot_fall_back_to_legacy_disposition(evidence):
    _register(evidence)
    path = evidence[0] / quarantine.REGISTRY
    data = json.loads(path.read_text())
    del data["dates"][DATE]["anomaly_kind"]
    path.write_text(json.dumps(data))
    assert quarantine.quarantine_status(evidence[0], None)["blocked"]


def test_zero_volume_cli_registers_real_evidence_without_provider(evidence, capsys):
    from scripts.operations.tushare_minute_history import main

    root, runtime, report, requery, _ = evidence
    args = [
        "quarantine-zero-volume",
        "--artifacts-root",
        str(root),
        "--runtime-root",
        str(runtime),
        "--acceptance",
        str(report),
        "--requery",
        str(requery),
        "--trade-date",
        DATE,
        "--reason",
        "Reproduced source anomaly",
    ]
    for symbol in SYMBOLS:
        args.extend(["--symbol", symbol])
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["entry"]["symbols"] == SYMBOLS
