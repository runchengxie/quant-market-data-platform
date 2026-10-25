"""Authenticate actual quota attempts, with explicit retrospective time-window links."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from quant_market_data_platform.tushare_minute_quota import quota_date_at
from scripts.operations import historical_minute_coverage_pilot as pilot


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Acquisition timestamps require a time zone")
    return parsed


def authenticate(
    record: dict[str, Any], binding: dict[str, Any], paths: dict[str, Path], prefix: dict[str, Any]
) -> None:
    _sources(record, binding, paths, prefix)
    responses = record["responses"]
    attempts = binding["records"]
    assignments = binding["assignments"]
    count = record["attempted"]
    if (
        binding["schema_version"] != "minute_delta_quota_binding.v1"
        or len(attempts) != count
        or len(assignments) != count
        or len({r["reservation_id"] for r in attempts}) != count
        or {r["response_index"] for r in assignments} != set(range(count))
        or len({r["reservation_id"] for r in assignments}) != count
        or instant(binding["generated_at"]) < instant(record["finished_at"])
    ):
        raise ValueError("Incomplete or ambiguous acquisition quota linkage")
    by_id = {r["reservation_id"]: r for r in attempts}
    posts = record.get("session_physical_posts", [])
    post_ids = {p["reservation_id"] for p in posts}
    if (
        len(post_ids) != len(posts)
        or not post_ids.issubset(by_id)
        or {p["sequence"] for p in posts} != set(range(1, len(posts) + 1))
        or (
            posts
            and (
                record.get("sdk_module_requests_restored") is not True
                or record.get("session_retries") != 0
            )
        )
    ):
        raise ValueError("Repeated physical request reservation")
    for assignment in assignments:
        index = assignment["response_index"]
        response = responses[index]
        attempt = by_id[assignment["reservation_id"]]
        end = response.get("received_at") or (
            responses[index + 1]["started_at"] if index + 1 < count else record["finished_at"]
        )
        start_time, end_time = instant(response["started_at"]), instant(end)
        candidates = [a for a in attempts if start_time <= instant(a["created_at"]) <= end_time]
        if (
            len(candidates) != 1
            or candidates[0]["reservation_id"] != attempt["reservation_id"]
            or attempt["consumer"] != binding["consumer"]
            or attempt["quota_date"] != quota_date_at(instant(attempt["created_at"]))
            or not instant(record["started_at"])
            <= start_time
            <= instant(attempt["created_at"])
            <= instant(attempt["updated_at"])
            <= end_time
            <= instant(record["finished_at"])
            or attempt["requested_rows"] != 241
            or attempt["reserved_rows"] != 241
        ):
            raise ValueError("Quota consumer, rows or unique request window mismatch")
        success = response.get("exact_match_to_retained_delta") is True
        if (attempt["state"], attempt["committed_rows"]) != (
            ("committed", 241) if success else ("uncertain", 0)
        ):
            raise ValueError("Acquisition success/uncertain accounting mismatch")
        kind = assignment["binding_kind"]
        if kind == "prospective_physical_post":
            matching = [p for p in posts if p["reservation_id"] == attempt["reservation_id"]]
            if len(matching) != 1:
                raise ValueError("Missing prospective physical request")
            post = matching[0]
            if (
                post["attempt"] != index + 1
                or post["http_status"] != 200
                or post.get("causes")
                or post["verify"] is not True
                or post["allow_redirects"] is not False
                or post["adapter_retries"] != 0
                or post["disable_proxy"] is not True
                or not instant(attempt["created_at"])
                <= instant(post["started_at"])
                <= instant(post["finished_at"])
                <= instant(attempt["updated_at"])
                <= end_time
            ):
                raise ValueError("Physical request disagrees with acquisition")
        elif (
            kind == "retrospective_unique_time_window" and attempt["reservation_id"] not in post_ids
        ):
            source = assignment["source_acquisition"]
            archived = paths[source["path"]]
            if (
                pilot.bind(archived) != source
                or json.loads(archived.read_text())["responses"][index] != response
            ):
                raise ValueError("Retrospective binding changed original acquisition evidence")
        else:
            raise ValueError("Untruthful prospective/retrospective quota binding")
    _budget(record, binding, attempts)


def _budget(
    record: dict[str, Any], binding: dict[str, Any], attempts: list[dict[str, Any]]
) -> None:
    budget = record["budget_after"]
    consumers = [c for c in budget["consumers"] if c["consumer"] == binding["consumer"]]
    if len(consumers) != 1:
        raise ValueError("Missing unique task budget consumer")
    consumer = consumers[0]
    committed = sum(a["state"] == "committed" for a in attempts)
    uncertain = len(attempts) - committed
    expected = {
        "committed_requests": committed,
        "uncertain_requests": uncertain,
        "reserved_requests": 0,
        "committed_rows": committed * 241,
        "uncertain_rows": uncertain * 241,
        "active_holds": 0,
    }
    if (
        any(consumer.get(k) != v for k, v in expected.items())
        or budget["charged_requests"] < len(attempts)
        or budget["allow_burst"] is not False
        or budget["limit_requests"] != 10000
        or budget["safety_requests"] != 500
        or budget["charged_requests"] + budget["hold_requests"] > 9500
    ):
        raise ValueError("Unsettled or inconsistent ordinary quota budget")


def _sources(record, binding, paths, prefix):
    snapshot = binding["ledger_snapshot"]
    ledger = paths[snapshot["path"]]
    if pilot.bind(ledger) != snapshot or binding["retrospective_prefix"] != prefix:
        raise ValueError("Independent quota snapshot or declared prefix mismatch")
    connection = sqlite3.connect(ledger.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    columns = (
        "reservation_id,quota_date,consumer,state,requested_rows,reserved_rows,"
        "committed_rows,created_at,updated_at"
    )
    try:
        exported = [
            dict(r)
            for r in connection.execute(
                "SELECT " + columns + " FROM request_attempts WHERE consumer=?",
                (binding["consumer"],),
            )
        ]
        holds = connection.execute(
            "SELECT state FROM quota_holds WHERE consumer=?", (binding["consumer"],)
        ).fetchall()
    finally:
        connection.close()
    if (
        sorted(exported, key=lambda r: r["reservation_id"])
        != sorted(binding["records"], key=lambda r: r["reservation_id"])
        or not holds
        or any(r["state"] != "released" for r in holds)
    ):
        raise ValueError("Quota self-description differs from independent complete snapshot")
    count = prefix["attempts"]
    retrospective = [
        a for a in binding["assignments"] if a["binding_kind"] == "retrospective_unique_time_window"
    ]
    if (
        type(count) is not int
        or not 0 <= count <= 12
        or {a["response_index"] for a in retrospective} != set(range(count))
        or any(a["source_acquisition"] != prefix["archive"] for a in retrospective)
    ):
        raise ValueError("Retrospective links exceed the declared original prefix")
    if not count:
        return
    archive = paths[prefix["archive"]["path"]]
    frozen = json.loads(archive.read_text())
    first_new = (
        record["responses"][count]["started_at"]
        if count < len(record["responses"])
        else record["finished_at"]
    )
    if (
        pilot.bind(archive) != prefix["archive"]
        or frozen["status"] != "stopped"
        or frozen["attempted"] != count
        or frozen["responses"] != record["responses"][:count]
        or frozen["original_bindings"] != record["original_bindings"]
        or frozen.get("session_physical_posts")
        or not instant(frozen["finished_at"])
        <= instant(first_new)
        < instant(binding["generated_at"])
    ):
        raise ValueError("Retrospective source is not the untouched earlier acquisition prefix")
