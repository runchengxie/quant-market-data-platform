"""THS concept member mirror implementation."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from market_data_platform.providers._io import _prepare_output_dir
from market_data_platform.providers.tushare_a_share_options import (
    DEFAULT_THS_MEMBER_FIELDS,
    TushareRequestPolicy,
)


@dataclass(frozen=True)
class ThsMemberMirrorDependencies:
    pandas: Callable[[], Any]
    tushare_runtime: Callable[..., tuple[Any, TushareRequestPolicy, str | None]]
    call_tushare_api: Callable[..., Any]
    fields_text: Callable[..., str | None]
    normalize_ts_code: Callable[[object], str]
    write_frame: Callable[[Any, Path], None]
    write_manifest: Callable[[Path, dict[str, Any]], None]
    request_policy_payload: Callable[[TushareRequestPolicy], dict[str, Any]]


@dataclass(frozen=True)
class ThsMemberMirrorOptions:
    out_dir: str | Path
    fields: Iterable[str] | None = None
    token_env: str = "TUSHARE_TOKEN"
    api_url: str | None = None
    request_policy: TushareRequestPolicy | None = None
    request_interval_seconds: float = 0.1
    request_options: dict[str, Any] | None = None


@dataclass(frozen=True)
class ThsMemberMirrorContext:
    pd: Any
    pro: Any
    policy: TushareRequestPolicy
    resolved_api_url: str | None
    output_dir: Path
    data_dir: Path
    requested_fields: tuple[str, ...]
    fields_text: str | None
    concept_codes: tuple[str, ...]
    dependencies: ThsMemberMirrorDependencies


def _prepare_ths_member_output(
    frame: Any,
    *,
    normalize_ts_code: Callable[[object], str],
) -> Any:
    output = frame.copy()
    if "con_code" in output.columns:
        output["symbol"] = output["con_code"].map(normalize_ts_code)
    dedupe_keys = [
        col
        for col in ("ts_code", "con_code", "in_date", "out_date", "is_new")
        if col in output.columns
    ]
    if dedupe_keys:
        output = output.drop_duplicates(subset=dedupe_keys, keep="last")
    sort_keys = [
        col for col in ("ts_code", "con_code", "in_date", "out_date") if col in output.columns
    ]
    if sort_keys:
        output = output.sort_values(sort_keys)
    return output.reset_index(drop=True)


def _load_ths_concept_codes(context: ThsMemberMirrorContext) -> tuple[str, ...]:
    index_df = context.dependencies.call_tushare_api(
        lambda: context.pro.ths_index(src="THS", type="N"),
        policy=context.policy,
    )
    index_df = context.pd.DataFrame(index_df)
    if index_df.empty:
        raise ValueError("ths_index returned no concept entries for THS type=N.")
    if "ts_code" not in index_df.columns:
        raise ValueError("ths_index response missing ts_code column.")
    return tuple(
        sorted({code for code in index_df["ts_code"].dropna().astype(str).str.strip() if code})
    )


def _ths_member_api_kwargs(
    *,
    fields_text: str | None,
    ts_code: str | None = None,
) -> dict[str, Any]:
    api_kwargs: dict[str, Any] = {}
    if ts_code is not None:
        api_kwargs["ts_code"] = ts_code
    if fields_text is not None:
        api_kwargs["fields"] = fields_text
    return api_kwargs


def _safe_ts_code_partition(code: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in code).strip("_") or code


def _validate_ths_member_part(frame: Any, *, concept_code: str) -> None:
    missing = [column for column in ("ts_code", "con_code") if column not in frame.columns]
    if missing:
        raise ValueError(
            "ths_member response is missing required identity fields: " + ", ".join(missing)
        )
    returned_codes = {code for code in frame["ts_code"].dropna().astype(str).str.strip() if code}
    if returned_codes != {concept_code}:
        raise ValueError(
            "ths_member response did not match requested concept ts_code "
            f"{concept_code!r}; returned {sorted(returned_codes)!r}."
        )
    stock_codes = frame["con_code"].fillna("").astype(str).str.strip()
    if stock_codes.eq("").any():
        raise ValueError(
            f"ths_member response contains blank con_code for concept ts_code {concept_code!r}."
        )


def _fetch_filtered_ths_member_parts(
    *,
    context: ThsMemberMirrorContext,
    request_interval_seconds: float,
) -> tuple[list[Any], int]:
    part_dir = context.output_dir / "parts"
    part_frames: list[Any] = []
    skipped_existing = 0
    for code in context.concept_codes:
        part_path = part_dir / f"ts_code={_safe_ts_code_partition(code)}" / "part.parquet"
        if part_path.is_file():
            cached = context.pd.read_parquet(part_path)
            _validate_ths_member_part(cached, concept_code=code)
            part_frames.append(cached)
            skipped_existing += 1
            continue
        api_kwargs = _ths_member_api_kwargs(fields_text=context.fields_text, ts_code=code)
        frame = context.dependencies.call_tushare_api(
            lambda k=dict(api_kwargs): context.pro.ths_member(**k),
            policy=context.policy,
        )
        frame = context.pd.DataFrame(frame)
        if frame.empty:
            continue
        _validate_ths_member_part(frame, concept_code=code)
        context.dependencies.write_frame(frame, part_path)
        part_frames.append(frame)
        if request_interval_seconds > 0.0:
            time.sleep(request_interval_seconds)
    return part_frames, skipped_existing


def _ths_member_totals(
    output: Any,
    *,
    files: int,
    concept_codes: tuple[str, ...],
    skipped_existing_parts: int,
) -> dict[str, Any]:
    return {
        "rows": int(len(output)),
        "symbols": int(output["symbol"].nunique() if "symbol" in output.columns else 0),
        "files": files,
        "concept_codes": len(concept_codes),
        "skipped_existing_parts": skipped_existing_parts,
    }


def _ths_member_manifest(
    *,
    context: ThsMemberMirrorContext,
    query_extra: dict[str, Any],
    totals: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "tushare.ths_member.v1",
        "dataset": "ths_member",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(context.output_dir),
        "query": {
            "api": "ths_member",
            "fields": list(context.requested_fields),
            "concept_codes": len(context.concept_codes),
            "concept_codes_list": list(context.concept_codes),
            "filter_field": "ts_code",
            **query_extra,
        },
        "api_url": context.resolved_api_url,
        "request_policy": context.dependencies.request_policy_payload(context.policy),
        "totals": totals,
    }


def _ths_member_context(
    options: ThsMemberMirrorOptions,
    dependencies: ThsMemberMirrorDependencies,
) -> ThsMemberMirrorContext:
    pd = dependencies.pandas()
    output_dir = _prepare_output_dir(Path(options.out_dir))
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    pro, policy, resolved_api_url = dependencies.tushare_runtime(
        token_env=options.token_env,
        api_url=options.api_url,
        request_policy=options.request_policy,
        request_options=dict(options.request_options or {}),
    )
    requested_fields = tuple(
        DEFAULT_THS_MEMBER_FIELDS if options.fields is None else options.fields
    )
    fields_text = dependencies.fields_text(
        requested_fields,
        required=("ts_code", "con_code"),
    )
    seed = ThsMemberMirrorContext(
        pd=pd,
        pro=pro,
        policy=policy,
        resolved_api_url=resolved_api_url,
        output_dir=output_dir,
        data_dir=data_dir,
        requested_fields=requested_fields,
        fields_text=fields_text,
        concept_codes=(),
        dependencies=dependencies,
    )
    return replace(seed, concept_codes=_load_ths_concept_codes(seed))


def mirror_a_share_ths_member_impl(
    options: ThsMemberMirrorOptions,
    dependencies: ThsMemberMirrorDependencies,
) -> dict[str, Any]:
    context = _ths_member_context(options, dependencies)

    part_frames, skipped_existing = _fetch_filtered_ths_member_parts(
        context=context,
        request_interval_seconds=max(0.0, float(options.request_interval_seconds)),
    )
    if not part_frames:
        raise ValueError("ths_member returned no rows for any concept code.")

    output = _prepare_ths_member_output(
        context.pd.concat(part_frames, ignore_index=True),
        normalize_ts_code=context.dependencies.normalize_ts_code,
    )
    context.dependencies.write_frame(output, context.data_dir / "part.parquet")
    manifest = _ths_member_manifest(
        context=context,
        query_extra={
            "mode": "per_concept",
            "request_interval_seconds": max(0.0, float(options.request_interval_seconds)),
        },
        totals=_ths_member_totals(
            output,
            files=int(len(part_frames)),
            concept_codes=context.concept_codes,
            skipped_existing_parts=skipped_existing,
        ),
    )
    context.dependencies.write_manifest(context.output_dir / "manifest.yml", manifest)
    return manifest
