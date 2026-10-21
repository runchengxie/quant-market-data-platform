"""Qlib-dependent runtime class imported only by the public lazy adapter."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib import import_module
from typing import Any, Protocol

# Resolve the optional runtime base only when the public lazy adapter imports this
# module. This preserves the real Qlib subclass without requiring Qlib in the
# standard type-check environment or environment-dependent import suppressions.
DataLoader = import_module("qlib.data.dataset.loader").DataLoader


class _PublishedAssetAdapter(Protocol):
    @property
    def dataset_metadata(self) -> dict[str, Any]: ...

    def load(
        self,
        instruments: Iterable[str] | Mapping[str, Any] | None = None,
        start_time: object | None = None,
        end_time: object | None = None,
    ) -> Any: ...


class PublishedAssetQlibDataLoader(DataLoader):
    """A Qlib DataLoader backed by an immutable published-asset selection."""

    def __init__(self, adapter: _PublishedAssetAdapter) -> None:
        self._adapter = adapter

    @property
    def dataset_metadata(self) -> dict[str, Any]:
        """Expose serializable source and backend provenance alongside Qlib output."""

        return self._adapter.dataset_metadata

    def load(
        self,
        instruments: Iterable[str] | Mapping[str, Any] | None = None,
        start_time: object | None = None,
        end_time: object | None = None,
    ) -> Any:
        """Return a Qlib-compatible ``(datetime, instrument)`` indexed frame."""

        return self._adapter.load(instruments, start_time, end_time)
