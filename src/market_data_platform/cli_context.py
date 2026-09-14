from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, time
from pathlib import Path

CONTEXT_FETCH_PROVIDERS = ("tushare", "nbs", "nea")


def _as_of_end(value: str):
    import pandas as pd

    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError("--as-of must use YYYYMMDD")
    return pd.Timestamp(
        datetime.combine(
            datetime.strptime(text, "%Y%m%d").date(),
            time(23, 59, 59),
            tzinfo=UTC,
        )
    )


def _build_root(artifacts_root: Path, as_of: str) -> Path:
    return artifacts_root / "assets" / "context" / "cn" / "builds" / f"as_of={as_of}"


def _latest_build(artifacts_root: Path, as_of: str) -> Path:
    alias = _build_root(artifacts_root, as_of) / "latest"
    try:
        return alias.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"no context build is available for {as_of}: {alias}") from exc


def _replace_latest(alias: Path, target: Path) -> None:
    alias.parent.mkdir(parents=True, exist_ok=True)
    if alias.is_symlink() or alias.is_file():
        alias.unlink()
    elif alias.exists():
        raise FileExistsError(f"refusing to replace non-symlink context build alias: {alias}")
    alias.symlink_to(target.name, target_is_directory=True)


def _write_build(artifacts_root: Path, *, as_of: str) -> Path:
    from market_data_platform.context.build import build_context_frames

    frames = build_context_frames(artifacts_root, as_of=_as_of_end(as_of))
    now = datetime.now(UTC)
    parent = _build_root(artifacts_root, as_of)
    target = parent / f"build={now.strftime('%Y%m%dT%H%M%S%fZ')}"
    target.mkdir(parents=True, exist_ok=False)
    frames.catalog.to_parquet(target / "catalog.parquet", index=False)
    frames.observations.to_parquet(target / "observations.parquet", index=False)
    frames.pit.to_parquet(target / "pit.parquet", index=False)
    frames.release_calendar.to_parquet(target / "release_calendar.parquet", index=False)
    receipt = {
        "schema_version": "cn_context.build.v1",
        "as_of": as_of,
        "built_at": now.isoformat(),
        "rows": {
            "catalog": len(frames.catalog),
            "observations": len(frames.observations),
            "pit": len(frames.pit),
            "release_calendar": len(frames.release_calendar),
        },
        "lineage": [dict(item) for item in frames.lineage],
    }
    (target / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    _replace_latest(parent / "latest", target)
    return target


def _fetch_tushare(args: argparse.Namespace):
    from market_data_platform.context.source_payload import SourcePayload
    from market_data_platform.providers._client import get_tushare_client
    from market_data_platform.providers.tushare_context import fetch_tushare_context_endpoint

    retrieved_at = datetime.now(UTC)
    client = get_tushare_client(token_env=args.token_env, api_url=args.api_url)
    frame = fetch_tushare_context_endpoint(
        client,
        args.dataset,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    return SourcePayload(
        provider="tushare",
        dataset=args.dataset,
        source_locator=f"tushare://{args.dataset}",
        retrieved_at=retrieved_at,
        content_type="text/csv;charset=utf-8",
        body=frame.to_csv(index=False).encode("utf-8"),
        metadata={"start_date": args.start_date, "end_date": args.end_date},
    )


def _fetch_nbs(args: argparse.Namespace):
    from market_data_platform.providers.nbs_context import fetch_nbs_payload

    period = str(args.period or "").strip()
    if len(period) != 6 or not period.isdigit():
        raise ValueError("NBS context fetch requires --period YYYYMM")
    return fetch_nbs_payload(args.dataset, period=period)


def _fetch_nea(args: argparse.Namespace):
    from market_data_platform.providers.nea_context import fetch_nea_payload

    if args.dataset != "electricity":
        raise ValueError("NEA context fetch currently supports --dataset electricity")
    url = str(args.source_url or "").strip()
    if not url:
        raise ValueError("NEA context fetch requires --source-url")
    return fetch_nea_payload(url)


def handle_context_fetch(args: argparse.Namespace) -> int:
    from market_data_platform.context.snapshots import seal_context_snapshot
    from market_data_platform.paths import resolve_artifacts_root

    fetchers = {
        "tushare": _fetch_tushare,
        "nbs": _fetch_nbs,
        "nea": _fetch_nea,
    }
    payload = fetchers[args.provider](args)
    root = resolve_artifacts_root(args.artifacts_root)
    snapshot = seal_context_snapshot(
        root,
        provider=payload.provider,
        dataset=payload.dataset,
        source_locator=payload.source_locator,
        retrieved_at=payload.retrieved_at,
        body=payload.body,
        parser_version=f"{payload.provider}-context.v1",
        request_metadata=dict(payload.metadata),
        content_type=payload.content_type,
    )
    print(snapshot)
    return 0


def handle_context_build(args: argparse.Namespace) -> int:
    from market_data_platform.paths import resolve_artifacts_root

    root = resolve_artifacts_root(args.artifacts_root)
    target = _write_build(root, as_of=args.as_of)
    print(target)
    return 0


def handle_context_publish(args: argparse.Namespace) -> int:
    import pandas as pd

    from market_data_platform.context.publish import publish_context_assets
    from market_data_platform.paths import resolve_artifacts_root

    root = resolve_artifacts_root(args.artifacts_root)
    build = _latest_build(root, args.as_of)
    receipt = json.loads((build / "receipt.json").read_text(encoding="utf-8"))
    output = publish_context_assets(
        root,
        catalog=pd.read_parquet(build / "catalog.parquet"),
        observations=pd.read_parquet(build / "observations.parquet"),
        pit=pd.read_parquet(build / "pit.parquet"),
        release_calendar=pd.read_parquet(build / "release_calendar.parquet"),
        as_of=args.as_of,
        lineage=list(receipt.get("lineage") or []),
    )
    print(output)
    return 0


def handle_context_inspect(args: argparse.Namespace) -> int:
    import pandas as pd

    from market_data_platform.context.pit import select_context_as_of
    from market_data_platform.paths import resolve_artifacts_root
    from market_data_platform.published_assets import PublishedAssetContract

    root = resolve_artifacts_root(args.artifacts_root)
    contract = PublishedAssetContract.load_current(root, market="cn_context")
    pit_ref = contract.asset("context_pit")
    data_path = pit_ref.resolve_data_path("data.parquet")
    panel = select_context_as_of(pd.read_parquet(data_path), as_of=_as_of_end(args.as_of))
    payload = {
        "contract": str(contract.path),
        "contract_sha256": contract.contract_sha256,
        "asset": pit_ref.provenance_dict(),
        "audit": dict(panel.audit),
        "visible_rows": len(panel.frame),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def add_context_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("context", help="Macro and industry context data assets.")
    commands = parser.add_subparsers(dest="context_command", required=True)

    fetch = commands.add_parser("fetch", help="Fetch and seal one provider dataset snapshot.")
    fetch.set_defaults(handler=handle_context_fetch)
    fetch.add_argument("--artifacts-root")
    fetch.add_argument("--provider", required=True, choices=CONTEXT_FETCH_PROVIDERS)
    fetch.add_argument("--dataset", required=True)
    fetch.add_argument("--start-date")
    fetch.add_argument("--end-date")
    fetch.add_argument("--period", help="NBS monthly period in YYYYMM form.")
    fetch.add_argument("--source-url", help="Official NEA release URL.")
    fetch.add_argument("--token-env", default="TUSHARE_TOKEN")
    fetch.add_argument("--api-url")

    build = commands.add_parser("build", help="Normalize sealed snapshots into a PIT build.")
    build.set_defaults(handler=handle_context_build)
    build.add_argument("--artifacts-root")
    build.add_argument("--as-of", required=True)

    publish = commands.add_parser("publish", help="Publish the latest context build as current.")
    publish.set_defaults(handler=handle_context_publish)
    publish.add_argument("--artifacts-root")
    publish.add_argument("--as-of", required=True)

    inspect = commands.add_parser("inspect", help="Inspect the published context PIT as of a date.")
    inspect.set_defaults(handler=handle_context_inspect)
    inspect.add_argument("--artifacts-root")
    inspect.add_argument("--as-of", required=True)
