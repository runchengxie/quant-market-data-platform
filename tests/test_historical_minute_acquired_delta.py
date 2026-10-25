"""New acquisitions corroborate every retained delta without inventing old lineage."""

import copy
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from quant_market_data_platform.providers._a_share_mins_constants import DEFAULT_MINS_FIELDS
from scripts.operations import historical_minute_coverage_pilot as pilot


def _frames_and_sources(tmp_path):
    times = pd.date_range("2020-01-02 09:30", "2020-01-02 11:30", freq="min").append(
        pd.date_range("2020-01-02 13:01", "2020-01-02 15:00", freq="min")
    )
    old = pd.DataFrame(
        {
            "ts_code": "000001.SZ",
            "trade_time": times,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "vol": 100.0,
            "amount": 1000.0,
        }
    )
    patches = [old.assign(ts_code=code) for code in ["000002.SZ", "000003.SZ"]]
    selected = pd.concat([old, *patches], ignore_index=True)
    paths = {}
    for key, frame in [("selected", selected), ("old", old)]:
        p = tmp_path / f"{key}.parquet"
        frame.to_parquet(p, index=False)
        paths[str(p)] = p
    sides = []
    for name in ["selected", "old"]:
        p = tmp_path / f"{name}.json"
        p.write_text("{}")
        paths[str(p)] = p
        sides.append(str(p))
    parts = [str(tmp_path / f"{name}.parquet") for name in ["selected", "old"]]
    row = {"date": "20200102", "partitions": parts, "candidate_sidecars": sides}
    return row, paths, patches


def evidence(tmp_path):
    row, paths, patches = _frames_and_sources(tmp_path)
    responses = []
    records = []
    assignments = []
    for i, frame in enumerate(patches):
        p = tmp_path / f"raw{i}.parquet"
        frame.to_parquet(p, index=False)
        paths[str(p)] = p
        stamp = f"2026-10-25T10:0{i}:"
        responses.append(
            {
                "code": str(frame.ts_code.iloc[0]),
                "started_at": stamp + "00+00:00",
                "received_at": stamp + "03+00:00",
                "finished_at": stamp + "04+00:00",
                "rows": 241,
                "raw": pilot.bind(p),
                "semantic_validation": "passed",
                "exact_match_to_retained_delta": True,
                "params": {
                    "ts_code": str(frame.ts_code.iloc[0]),
                    "freq": "1min",
                    "start_date": "20200102 09:00:00",
                    "end_date": "20200102 15:30:00",
                    "fields": ",".join(DEFAULT_MINS_FIELDS),
                    "limit": 8000,
                },
            }
        )
        records.append(
            {
                "reservation_id": f"id{i}",
                "consumer": "fixture",
                "quota_date": "20261025",
                "state": "committed",
                "requested_rows": 241,
                "reserved_rows": 241,
                "committed_rows": 241,
                "created_at": stamp + "01+00:00",
                "updated_at": stamp + "02+00:00",
            }
        )
        assignments.append(
            {
                "response_index": i,
                "reservation_id": f"id{i}",
                "binding_kind": "retrospective_unique_time_window",
            }
        )
    acquired = _acquired_record(row, paths, responses)
    acquisition = tmp_path / "acquisition.json"
    quota = tmp_path / "quota.json"
    paths[str(acquisition)] = acquisition
    paths[str(quota)] = quota
    binding = {
        "schema_version": "minute_delta_quota_binding.v1",
        "consumer": "fixture",
        "generated_at": "2026-10-25T10:03:00+00:00",
        "records": records,
        "assignments": assignments,
    }
    resolution = {
        "acquisition": str(acquisition),
        "quota_binding": str(quota),
        "consumer": "fixture",
    }
    return row, resolution, paths, acquired, binding


def _acquired_record(row, paths, responses):
    consumer = {
        "consumer": "fixture",
        "committed_requests": 2,
        "uncertain_requests": 0,
        "reserved_requests": 0,
        "committed_rows": 482,
        "uncertain_rows": 0,
        "active_holds": 0,
    }
    return {
        "date": row["date"],
        "status": "all_delta_corroborated",
        "attempted": 2,
        "max_attempts": 3,
        "max_unique_symbols": 2,
        "symbols": ["000002.SZ", "000003.SZ"],
        "responses": responses,
        "started_at": "2026-10-25T10:00:00+00:00",
        "finished_at": "2026-10-25T10:02:00+00:00",
        "writes_production": False,
        "formal_resolution": False,
        "original_bindings_unchanged": True,
        "original_bindings": {
            k: pilot.bind(paths[k])["sha256"]
            for k in [*row["partitions"], *row["candidate_sidecars"]]
        },
        "budget_after": {
            "consumers": [consumer],
            "allow_burst": False,
            "charged_requests": 2,
            "hold_requests": 0,
            "limit_requests": 10000,
            "safety_requests": 500,
        },
    }


def write_inputs(resolution, paths, acquired, binding):
    archive = paths[resolution["acquisition"]].parent / "original-acquisition.json"
    paths[str(archive)] = archive
    retrospective = [
        a for a in binding["assignments"] if a["binding_kind"] == "retrospective_unique_time_window"
    ]
    prefix_count = len(retrospective)
    if not archive.exists():
        frozen = copy.deepcopy(acquired)
        frozen.update(
            status="stopped", attempted=prefix_count, responses=frozen["responses"][:prefix_count]
        )
        frozen.pop("session_physical_posts", None)
        if prefix_count:
            last = frozen["responses"][-1]
            frozen["finished_at"] = last.get("finished_at", "2026-10-25T09:59:59+00:00")
        archive.write_text(json.dumps(frozen))
    prefix = {"archive": pilot.bind(archive), "attempts": prefix_count}
    resolution["retrospective_prefix"] = prefix
    binding["retrospective_prefix"] = prefix
    for assignment in retrospective:
        assignment["source_acquisition"] = pilot.bind(archive)
    snapshot = archive.with_suffix(".sqlite3")
    paths[str(snapshot)] = snapshot
    if not snapshot.exists():
        db = sqlite3.connect(snapshot)
        columns = list(binding["records"][0])
        definitions = ",".join(
            k + (" INTEGER" if isinstance(binding["records"][0][k], int) else " TEXT")
            for k in columns
        )
        db.execute("CREATE TABLE request_attempts (" + definitions + ")")
        db.executemany(
            "INSERT INTO request_attempts VALUES (" + ",".join("?" for _ in columns) + ")",
            [[r[k] for k in columns] for r in binding["records"]],
        )
        db.execute("CREATE TABLE quota_holds (consumer TEXT,state TEXT,hold_id TEXT)")
        db.execute("INSERT INTO quota_holds VALUES ('fixture','released','hold')")
        db.commit()
        db.close()
    binding["ledger_snapshot"] = pilot.bind(snapshot)
    paths[resolution["acquisition"]].write_text(json.dumps(acquired))
    binding["acquisition"] = pilot.bind(paths[resolution["acquisition"]])
    paths[resolution["quota_binding"]].write_text(json.dumps(binding))


def test_complete_multisymbol_delta_authenticates_each_response(tmp_path):
    from scripts.operations import historical_minute_acquired_delta as tool

    row, resolution, paths, acquired, binding = evidence(tmp_path)
    write_inputs(resolution, paths, acquired, binding)
    selected, old, patch = tool.authenticate(row, resolution, paths)
    assert len(selected) == 723 and len(old) == 241 and len(patch) == 482


@pytest.mark.parametrize(
    "corruption",
    [
        "in_progress",
        "missing_symbol",
        "duplicate_raw",
        "raw_hash",
        "wrong_date",
        "wrong_freq",
        "wrong_fields",
        "source_hash",
        "wrong_consumer",
        "uncertain_dropped",
        "attempts",
        "ambiguous_window",
        "outside_window",
        "future_binding",
        "missing_assignment",
        "quota_rows",
        "unreleased_hold",
        "burst",
        "changed_value",
        "bad_semantics",
        "all_wrong_consumer",
        "missing_raw_hash",
    ],
)
def test_corrupt_delta_or_quota_binding_fails_closed(tmp_path, corruption):
    from scripts.operations import historical_minute_acquired_delta as tool

    row, resolution, paths, acquired, binding = evidence(tmp_path)
    response = acquired["responses"][0]
    actions = {
        "in_progress": lambda: acquired.update(status="stopped"),
        "missing_symbol": lambda: acquired["responses"].pop(),
        "duplicate_raw": lambda: acquired["responses"][1].update(
            raw=copy.deepcopy(response["raw"])
        ),
        "raw_hash": lambda: response["raw"].update(sha256="0" * 64),
        "wrong_date": lambda: acquired.update(date="20200103"),
        "wrong_freq": lambda: response["params"].update(freq="5min"),
        "wrong_fields": lambda: response["params"].update(fields="ts_code"),
        "source_hash": lambda: acquired["original_bindings"].update(
            {row["partitions"][0]: "0" * 64}
        ),
        "wrong_consumer": lambda: binding["records"][0].update(consumer="other"),
        "uncertain_dropped": lambda: acquired["budget_after"]["consumers"][0].update(
            uncertain_requests=1
        ),
        "attempts": lambda: acquired.update(attempted=3),
        "ambiguous_window": lambda: binding["records"][1].update(
            created_at=binding["records"][0]["created_at"],
            updated_at=binding["records"][0]["updated_at"],
        ),
        "outside_window": lambda: binding["records"][0].update(
            created_at="2026-10-25T09:59:59+00:00"
        ),
        "future_binding": lambda: binding.update(generated_at="2026-10-25T09:00:00+00:00"),
        "missing_assignment": lambda: binding["assignments"].pop(),
        "quota_rows": lambda: binding["records"][0].update(committed_rows=240),
        "unreleased_hold": lambda: acquired["budget_after"]["consumers"][0].update(active_holds=1),
        "burst": lambda: acquired["budget_after"].update(allow_burst=True),
        "missing_raw_hash": lambda: response["raw"].pop("sha256"),
    }
    if corruption in actions:
        actions[corruption]()
    if corruption == "all_wrong_consumer":
        binding["consumer"] = "other"
        for r in binding["records"]:
            r["consumer"] = "other"
        acquired["budget_after"]["consumers"][0]["consumer"] = "other"
    if corruption in {"changed_value", "bad_semantics"}:
        entry = acquired["responses"][0]
        p = Path(entry["raw"]["path"])
        frame = pd.read_parquet(p)
        frame.loc[0, "close" if corruption == "changed_value" else "vol"] = (
            11.0 if corruption == "changed_value" else 0.0
        )
        frame.to_parquet(p, index=False)
        entry["raw"] = pilot.bind(p)
    write_inputs(resolution, paths, acquired, binding)
    with pytest.raises(ValueError):
        tool.authenticate(row, resolution, paths)


def test_prospective_physical_requests_and_uncertain_attempt_are_preserved(tmp_path):
    from scripts.operations import historical_minute_acquired_delta as tool

    row, resolution, paths, acquired, binding = evidence(tmp_path)
    acquired.update(sdk_module_requests_restored=True, session_retries=0)
    acquired["session_physical_posts"] = []
    for index, assignment in enumerate(binding["assignments"]):
        assignment["binding_kind"] = "prospective_physical_post"
        acquired["session_physical_posts"].append(
            {
                "sequence": index + 1,
                "attempt": index + 1,
                "reservation_id": assignment["reservation_id"],
                "started_at": binding["records"][index]["created_at"],
                "finished_at": binding["records"][index]["updated_at"],
                "http_status": 200,
                "verify": True,
                "allow_redirects": False,
                "adapter_retries": 0,
                "disable_proxy": True,
            }
        )
    acquired["responses"].insert(
        0,
        {
            "code": "000003.SZ",
            "started_at": "2026-10-25T09:59:57+00:00",
            "params": copy.deepcopy(acquired["responses"][1]["params"]),
        },
    )
    acquired["attempted"] = 3
    binding["records"].append(
        {
            "reservation_id": "uncertain",
            "consumer": "fixture",
            "quota_date": "20261025",
            "state": "uncertain",
            "requested_rows": 241,
            "reserved_rows": 241,
            "committed_rows": 0,
            "created_at": "2026-10-25T09:59:58+00:00",
            "updated_at": "2026-10-25T09:59:59+00:00",
        }
    )
    binding["assignments"].append(
        {
            "response_index": 0,
            "reservation_id": "uncertain",
            "binding_kind": "retrospective_unique_time_window",
        }
    )
    acquired["started_at"] = "2026-10-25T09:59:55+00:00"
    for a in binding["assignments"][:-1]:
        a["response_index"] += 1
    for p in acquired["session_physical_posts"]:
        p["attempt"] += 1
    acquired["budget_after"]["consumers"][0].update(uncertain_requests=1, uncertain_rows=241)
    acquired["budget_after"]["charged_requests"] = 3
    write_inputs(resolution, paths, acquired, binding)
    assert len(tool.authenticate(row, resolution, paths)[2]) == 482


@pytest.mark.parametrize("corruption", ["none", "changed_value", "archive_changed"])
def test_dispatcher_preserves_formal_gates_and_exact_union(tmp_path, corruption):
    import importlib.util

    source = Path(__file__).with_name("test_historical_minute_coverage_receipts.py")
    spec = importlib.util.spec_from_file_location("receipt_fixtures", source)
    assert spec is not None and spec.loader is not None
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    base = tmp_path / "base"
    base.mkdir()
    manifest, paths, _ = fixtures.expanded_candidates(
        fixtures.evidence.__wrapped__(base), base, triple=False
    )
    rawdir = tmp_path / "raw"
    rawdir.mkdir()
    _, resolution, rawpaths, acquired, binding = evidence(rawdir)
    paths.update(rawpaths)
    row = manifest["dates"][0]
    acquired.update(symbols=["000002.SZ"], attempted=1, max_unique_symbols=1)
    acquired["responses"] = acquired["responses"][:1]
    binding["records"] = binding["records"][:1]
    binding["assignments"] = binding["assignments"][:1]
    acquired["budget_after"]["consumers"][0].update(committed_requests=1, committed_rows=241)
    acquired["budget_after"]["charged_requests"] = 1
    acquired["original_bindings"] = {
        str(paths[k]): pilot.bind(paths[k])["sha256"]
        for k in [*row["partitions"], *row["candidate_sidecars"]]
    }
    if corruption == "changed_value":
        entry = acquired["responses"][0]
        p = Path(entry["raw"]["path"])
        frame = pd.read_parquet(p)
        frame.loc[:, ["open", "high", "low", "close"]] = 10.1
        frame["amount"] = 1010.0
        frame.to_parquet(p, index=False)
        entry["raw"] = pilot.bind(p)
    write_inputs(resolution, paths, acquired, binding)
    if corruption == "archive_changed":
        archived = rawdir / "original-acquisition.json"
        archived.write_text('{"responses":[]}')
    manifest["resolutions"][row["date"]].update(resolution, kind="acquired_native_delta")
    tool = fixtures.module()
    if corruption == "none":
        result = tool.evaluate(manifest, paths)[0]
        assert result["status"] == "passed_retrospective_independent_coverage"
        assert result["candidate_resolution"]["added_rows"] == 241
        assert result["historical_pit_certified"] is False
    else:
        with pytest.raises(ValueError):
            tool.evaluate(manifest, paths)


@pytest.mark.parametrize(
    "field,value",
    [
        ("verify", False),
        ("allow_redirects", True),
        ("adapter_retries", 1),
        ("http_status", 302),
        ("attempt", 9),
        ("extra_post", True),
        ("restored", False),
        ("understated_charge", True),
        ("started_at", "2026-10-25T10:00:00+00:00"),
        ("finished_at", "2026-10-25T10:00:02.5+00:00"),
    ],
)
def test_physical_attempts_require_one_to_one_safe_quota_binding(tmp_path, field, value):
    from scripts.operations import historical_minute_acquired_delta as tool

    row, resolution, paths, acquired, binding = evidence(tmp_path)
    acquired.update(sdk_module_requests_restored=True, session_retries=0)
    acquired["session_physical_posts"] = []
    for index, assignment in enumerate(binding["assignments"]):
        assignment["binding_kind"] = "prospective_physical_post"
        acquired["session_physical_posts"].append(
            {
                "sequence": index + 1,
                "attempt": index + 1,
                "reservation_id": assignment["reservation_id"],
                "started_at": binding["records"][index]["created_at"],
                "finished_at": binding["records"][index]["updated_at"],
                "http_status": 200,
                "verify": True,
                "allow_redirects": False,
                "adapter_retries": 0,
                "disable_proxy": True,
            }
        )
    if field == "extra_post":
        extra = copy.deepcopy(acquired["session_physical_posts"][-1])
        extra.update(sequence=3, reservation_id="unbudgeted")
        acquired["session_physical_posts"].append(extra)
    elif field == "restored":
        acquired["sdk_module_requests_restored"] = value
    elif field == "understated_charge":
        acquired["budget_after"]["charged_requests"] = 0
    else:
        acquired["session_physical_posts"][0][field] = value
    write_inputs(resolution, paths, acquired, binding)
    with pytest.raises(ValueError):
        tool.authenticate(row, resolution, paths)


@pytest.mark.parametrize(
    "corruption",
    [
        "quota_archive_value",
        "quota_archive_hash",
        "current_capture_as_original",
        "active_snapshot_hold",
        "late_retrospective_prefix",
    ],
)
def test_independent_original_archives_are_required(tmp_path, corruption):
    from scripts.operations import historical_minute_acquired_delta as tool

    row, resolution, paths, acquired, binding = evidence(tmp_path)
    write_inputs(resolution, paths, acquired, binding)
    if corruption == "quota_archive_value":
        binding["records"][0]["updated_at"] = "2026-10-25T10:00:02.1+00:00"
    elif corruption == "quota_archive_hash":
        binding["ledger_snapshot"]["sha256"] = "0" * 64
    elif corruption == "late_retrospective_prefix":
        prefix = {**binding["retrospective_prefix"], "attempts": 1}
        resolution["retrospective_prefix"] = prefix
        binding["retrospective_prefix"] = prefix
    elif corruption == "active_snapshot_hold":
        snapshot = paths[binding["ledger_snapshot"]["path"]]
        db = sqlite3.connect(snapshot)
        db.execute("UPDATE quota_holds SET state='active'")
        db.commit()
        db.close()
        binding["ledger_snapshot"] = pilot.bind(snapshot)
    else:
        archive = paths[binding["retrospective_prefix"]["archive"]["path"]]
        archive.write_text(json.dumps(acquired))
        prefix = {"archive": pilot.bind(archive), "attempts": 2}
        resolution["retrospective_prefix"] = prefix
        binding["retrospective_prefix"] = prefix
        for a in binding["assignments"]:
            a["source_acquisition"] = pilot.bind(archive)
    paths[resolution["quota_binding"]].write_text(json.dumps(binding))
    with pytest.raises(ValueError):
        tool.authenticate(row, resolution, paths)
