"""Explicit frame mappings over read-only published Parquet assets."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from market_data_platform.published_assets import PublishedAssetContract, PublishedAssetRef


class PublishedFrameError(ValueError):
    """Base error for invalid published-frame plans or data."""


class PublishedFrameSchemaError(PublishedFrameError):
    """Raised when a Parquet frame does not satisfy its explicit mapping."""


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_column_mapping(value: Mapping[str, str]) -> Mapping[str, str]:
    columns = {str(source).strip(): str(target).strip() for source, target in dict(value).items()}
    if not columns or any(not source or not target for source, target in columns.items()):
        raise PublishedFrameError("columns must contain non-empty source-to-output mappings.")
    if len(set(columns.values())) != len(columns):
        raise PublishedFrameError("columns must not map multiple sources to one output name.")
    return MappingProxyType(columns)


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PublishedFrameError(f"{field} must be non-empty.")
    return text


@dataclass(frozen=True)
class ParquetFrameMapping:
    """Map explicit source columns from one published Parquet asset into Qlib shape."""

    asset_key: str
    datetime_column: str
    instrument_column: str
    columns: Mapping[str, str]
    column_group: str = "feature"
    relative_path: str = "."

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_key", _required_text(self.asset_key, field="asset_key"))
        object.__setattr__(
            self,
            "datetime_column",
            _required_text(self.datetime_column, field="datetime_column"),
        )
        object.__setattr__(
            self,
            "instrument_column",
            _required_text(self.instrument_column, field="instrument_column"),
        )
        object.__setattr__(
            self,
            "column_group",
            _required_text(self.column_group, field="column_group"),
        )
        object.__setattr__(
            self,
            "relative_path",
            _required_text(self.relative_path, field="relative_path"),
        )
        object.__setattr__(self, "columns", _normalized_column_mapping(self.columns))
        keys = {self.datetime_column, self.instrument_column}
        overlap = sorted(keys.intersection(self.columns))
        if overlap:
            raise PublishedFrameError(
                "columns must contain values only; index columns are configured separately: "
                f"{overlap}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-serializable mapping description."""

        return {
            "asset_key": self.asset_key,
            "relative_path": self.relative_path,
            "datetime_column": self.datetime_column,
            "instrument_column": self.instrument_column,
            "columns": dict(self.columns),
            "column_group": self.column_group,
        }


@dataclass(frozen=True)
class TradingCalendarMapping:
    """Define an explicit open-session filter from a published calendar asset."""

    asset_key: str
    datetime_column: str
    open_column: str
    open_values: tuple[object, ...]
    relative_path: str = "."

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_key", _required_text(self.asset_key, field="asset_key"))
        object.__setattr__(
            self,
            "datetime_column",
            _required_text(self.datetime_column, field="datetime_column"),
        )
        object.__setattr__(
            self,
            "open_column",
            _required_text(self.open_column, field="open_column"),
        )
        object.__setattr__(
            self,
            "relative_path",
            _required_text(self.relative_path, field="relative_path"),
        )
        object.__setattr__(self, "open_values", tuple(self.open_values))
        if not self.open_values:
            raise PublishedFrameError("open_values must explicitly define an open session.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_key": self.asset_key,
            "relative_path": self.relative_path,
            "datetime_column": self.datetime_column,
            "open_column": self.open_column,
            "open_values": list(self.open_values),
        }


@dataclass(frozen=True)
class PITUniverseMapping:
    """Define exact-date, point-in-time membership from a published asset."""

    asset_key: str
    datetime_column: str
    instrument_column: str
    membership_column: str
    included_values: tuple[object, ...]
    relative_path: str = "."

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_key", _required_text(self.asset_key, field="asset_key"))
        object.__setattr__(
            self,
            "datetime_column",
            _required_text(self.datetime_column, field="datetime_column"),
        )
        object.__setattr__(
            self,
            "instrument_column",
            _required_text(self.instrument_column, field="instrument_column"),
        )
        object.__setattr__(
            self,
            "membership_column",
            _required_text(self.membership_column, field="membership_column"),
        )
        object.__setattr__(
            self,
            "relative_path",
            _required_text(self.relative_path, field="relative_path"),
        )
        object.__setattr__(self, "included_values", tuple(self.included_values))
        if not self.included_values:
            raise PublishedFrameError(
                "included_values must explicitly define point-in-time membership."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_key": self.asset_key,
            "relative_path": self.relative_path,
            "datetime_column": self.datetime_column,
            "instrument_column": self.instrument_column,
            "membership_column": self.membership_column,
            "included_values": list(self.included_values),
            "point_in_time": True,
        }


@dataclass(frozen=True)
class PublishedFramePlan:
    """Framework-neutral plan for feature, calendar, and PIT-universe frames."""

    frames: tuple[ParquetFrameMapping, ...]
    calendar: TradingCalendarMapping | None = None
    universe: PITUniverseMapping | None = None
    join: str = "outer"

    def __post_init__(self) -> None:
        object.__setattr__(self, "frames", tuple(self.frames))
        if not self.frames:
            raise PublishedFrameError("PublishedFramePlan requires at least one frame mapping.")
        if self.join not in {"inner", "outer"}:
            raise PublishedFrameError("join must be 'inner' or 'outer'.")

        output_columns: set[tuple[str, str]] = set()
        for frame in self.frames:
            for output_name in frame.columns.values():
                column = (frame.column_group, output_name)
                if column in output_columns:
                    raise PublishedFrameError(
                        f"PublishedFramePlan contains duplicate output column {column}."
                    )
                output_columns.add(column)

    @property
    def asset_keys(self) -> tuple[str, ...]:
        keys = [frame.asset_key for frame in self.frames]
        if self.calendar is not None:
            keys.append(self.calendar.asset_key)
        if self.universe is not None:
            keys.append(self.universe.asset_key)
        return tuple(dict.fromkeys(keys))

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames": [frame.to_dict() for frame in self.frames],
            "calendar": self.calendar.to_dict() if self.calendar is not None else None,
            "universe": self.universe.to_dict() if self.universe is not None else None,
            "join": self.join,
        }


class PublishedParquetFrameReader:
    """Load an explicitly mapped Qlib-shaped frame from published Parquet assets."""

    def __init__(self, contract: PublishedAssetContract, plan: PublishedFramePlan) -> None:
        self.contract = contract
        self.plan = plan
        self._assets = dict(
            zip(
                plan.asset_keys,
                contract.require_assets(plan.asset_keys),
                strict=True,
            )
        )

    @property
    def metadata(self) -> dict[str, Any]:
        """Return deterministic dataset mapping and source provenance."""

        plan_payload = self.plan.to_dict()
        sources = [self._assets[key].provenance_dict() for key in self.plan.asset_keys]
        configuration_sha256 = _canonical_sha256(plan_payload)
        content_fingerprint = _canonical_sha256(
            {
                "configuration_sha256": configuration_sha256,
                "source_content_fingerprints": [
                    source["content_fingerprint"] for source in sources
                ],
            }
        )
        return {
            "schema_version": "market_data_platform.published_frame.v1",
            "backend": {
                "name": "market_data_platform.published_parquet",
                "version": 1,
            },
            "contract": {
                "name": self.contract.name,
                "version": self.contract.version,
                "path": str(self.contract.path),
                "sha256": self.contract.contract_sha256,
            },
            "mapping": plan_payload,
            "configuration_sha256": configuration_sha256,
            "content_fingerprint": content_fingerprint,
            "sources": sources,
        }

    def load(
        self,
        instruments: Iterable[str] | Mapping[str, Any] | None = None,
        start_time: object | None = None,
        end_time: object | None = None,
    ) -> Any:
        """Load a deterministic ``(datetime, instrument)`` indexed pandas frame."""

        pd = _pandas()
        frames = [self._read_feature_frame(mapping) for mapping in self.plan.frames]
        data = pd.concat(frames, axis=1, join=self.plan.join).sort_index()
        if self.plan.calendar is not None:
            open_dates = self._read_open_dates(self.plan.calendar)
            data = data[data.index.get_level_values("datetime").isin(open_dates)]
        if self.plan.universe is not None:
            membership = self._read_membership(self.plan.universe)
            data = data[data.index.isin(membership)]

        data = _filter_instruments(data, instruments, pd)
        datetimes = data.index.get_level_values("datetime")
        if start_time is not None:
            data = data[datetimes >= pd.Timestamp(start_time)]
            datetimes = data.index.get_level_values("datetime")
        if end_time is not None:
            data = data[datetimes <= pd.Timestamp(end_time)]
        return data.sort_index()

    def _read_parquet(
        self,
        asset: PublishedAssetRef,
        relative_path: str,
        columns: Sequence[str],
    ) -> Any:
        pd = _pandas()
        source = asset.resolve_data_path(relative_path)
        if not source.is_file() and not source.is_dir():
            raise PublishedFrameSchemaError(
                f"Asset '{asset.key}' mapped path is not a Parquet file or dataset: {source}"
            )
        try:
            return pd.read_parquet(source, columns=list(dict.fromkeys(columns)))
        except (KeyError, ValueError, OSError) as exc:
            raise PublishedFrameSchemaError(
                f"Asset '{asset.key}' does not satisfy explicit Parquet columns {list(columns)}: "
                f"{source}"
            ) from exc

    def _read_feature_frame(self, mapping: ParquetFrameMapping) -> Any:
        pd = _pandas()
        required = [mapping.datetime_column, mapping.instrument_column, *mapping.columns]
        frame = self._read_parquet(
            self._assets[mapping.asset_key],
            mapping.relative_path,
            required,
        )
        frame = frame.rename(
            columns={
                mapping.datetime_column: "datetime",
                mapping.instrument_column: "instrument",
                **dict(mapping.columns),
            }
        )
        frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
        frame["instrument"] = frame["instrument"].astype(str)
        frame = frame.set_index(["datetime", "instrument"])[list(mapping.columns.values())]
        if frame.index.has_duplicates:
            raise PublishedFrameSchemaError(
                f"Asset '{mapping.asset_key}' contains duplicate datetime/instrument rows."
            )
        frame.columns = pd.MultiIndex.from_product(
            [[mapping.column_group], list(mapping.columns.values())]
        )
        return frame.sort_index()

    def _read_open_dates(self, mapping: TradingCalendarMapping) -> Any:
        pd = _pandas()
        frame = self._read_parquet(
            self._assets[mapping.asset_key],
            mapping.relative_path,
            [mapping.datetime_column, mapping.open_column],
        )
        dates = pd.to_datetime(frame[mapping.datetime_column], errors="raise")
        if dates.duplicated().any():
            raise PublishedFrameSchemaError(
                f"Calendar asset '{mapping.asset_key}' contains duplicate dates."
            )
        return pd.Index(dates[frame[mapping.open_column].isin(mapping.open_values)])

    def _read_membership(self, mapping: PITUniverseMapping) -> Any:
        pd = _pandas()
        frame = self._read_parquet(
            self._assets[mapping.asset_key],
            mapping.relative_path,
            [
                mapping.datetime_column,
                mapping.instrument_column,
                mapping.membership_column,
            ],
        )
        frame = frame[frame[mapping.membership_column].isin(mapping.included_values)].copy()
        frame["datetime"] = pd.to_datetime(frame[mapping.datetime_column], errors="raise")
        frame["instrument"] = frame[mapping.instrument_column].astype(str)
        membership = pd.MultiIndex.from_frame(frame[["datetime", "instrument"]])
        if membership.has_duplicates:
            raise PublishedFrameSchemaError(
                f"PIT universe asset '{mapping.asset_key}' contains duplicate membership rows."
            )
        return membership


def _filter_instruments(
    data: Any,
    instruments: Iterable[str] | Mapping[str, Any] | None,
    pd: Any,
) -> Any:
    if instruments is None:
        return data
    if isinstance(instruments, str):
        raise KeyError(
            "Published Parquet loading cannot resolve a Qlib market name. "
            "Pass an explicit iterable or mapping of instrument identifiers."
        )
    instrument_index = data.index.get_level_values("instrument")
    if not isinstance(instruments, Mapping):
        selected = frozenset(str(value) for value in instruments)
        return data[instrument_index.isin(selected)]

    datetime_index = data.index.get_level_values("datetime")
    mask = pd.Series(False, index=data.index)
    for raw_instrument, raw_spans in instruments.items():
        instrument_mask = instrument_index == str(raw_instrument)
        spans = _instrument_spans(raw_spans)
        if spans is None:
            mask |= instrument_mask
            continue
        for start, end in spans:
            span_mask = instrument_mask.copy()
            if start is not None:
                span_mask &= datetime_index >= pd.Timestamp(start)
            if end is not None:
                span_mask &= datetime_index <= pd.Timestamp(end)
            mask |= span_mask
    return data[mask.to_numpy()]


def _instrument_spans(value: Any) -> tuple[tuple[object | None, object | None], ...] | None:
    if value is None:
        return None
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise KeyError(
            "Instrument mapping values must be None, a (start, end) span, or a sequence of spans."
        )
    raw_spans: Sequence[Any]
    first_is_span = (
        len(value) > 0 and isinstance(value[0], Sequence) and not isinstance(value[0], str | bytes)
    )
    if len(value) == 2 and not first_is_span:
        raw_spans = (value,)
    else:
        raw_spans = value
    spans: list[tuple[object | None, object | None]] = []
    for raw_span in raw_spans:
        if isinstance(raw_span, str | bytes) or not isinstance(raw_span, Sequence):
            raise KeyError("Each instrument validity span must contain (start, end).")
        if len(raw_span) != 2:
            raise KeyError("Each instrument validity span must contain exactly (start, end).")
        spans.append((raw_span[0], raw_span[1]))
    return tuple(spans)


def _pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - exercised in minimal installations
        raise ImportError(
            "Published Parquet frame loading requires pandas and pyarrow. "
            "Install market-data-platform[qlib] or market-data-platform[dev]."
        ) from exc
    return pd
