"""Qlib-dependent runtime class imported only by the public lazy adapter."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

# qlib is an optional heavy dependency (the `[qlib]` extra) and is intentionally
# absent from the type-check environment. The runtime import error here is what
# surfaces as `QlibIntegrationUnavailableError` when `as_data_loader()` is called
# without qlib installed, so this unresolved-import is expected, not a defect.
from qlib.data.dataset.loader import DataLoader  # ty: ignore[unresolved-import]


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
