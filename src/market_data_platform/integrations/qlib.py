"""Lazy Qlib DataLoader adapter for explicitly mapped published assets."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from market_data_platform.published_assets import PublishedAssetContract
from market_data_platform.published_frames import PublishedFramePlan, PublishedParquetFrameReader


class QlibIntegrationUnavailableError(ImportError):
    """Raised when the optional Qlib runtime has not been installed."""


class QlibPublishedAssetAdapter:
    """Keep Qlib behind a leaf adapter while preserving framework-neutral sources."""

    def __init__(self, contract: PublishedAssetContract, plan: PublishedFramePlan) -> None:
        self._reader = PublishedParquetFrameReader(contract, plan)

    @property
    def dataset_metadata(self) -> dict[str, Any]:
        """Return source hashes, mapping identity, and explicit backend provenance."""

        metadata = self._reader.metadata
        source_backend = metadata["backend"]
        metadata["backend"] = {
            "name": "qlib",
            "package": "pyqlib",
            "adapter": "market_data_platform.integrations.qlib",
            "adapter_version": 1,
        }
        metadata["source_backend"] = source_backend
        return metadata

    def load(
        self,
        instruments: Iterable[str] | Mapping[str, Any] | None = None,
        start_time: object | None = None,
        end_time: object | None = None,
    ) -> Any:
        """Load the exact frame used by the Qlib DataLoader wrapper."""

        return self._reader.load(instruments, start_time, end_time)

    def as_data_loader(self) -> Any:
        """Create a real ``qlib.data.dataset.loader.DataLoader`` lazily."""

        try:
            from market_data_platform.integrations._qlib_runtime import (
                PublishedAssetQlibDataLoader,
            )
        except ImportError as exc:
            if exc.name and (exc.name == "qlib" or exc.name.startswith("qlib.")):
                raise QlibIntegrationUnavailableError(
                    "Qlib is optional. Install market-data-platform[qlib] before creating "
                    "a Qlib DataLoader."
                ) from exc
            raise
        return PublishedAssetQlibDataLoader(self)


def create_data_loader(
    contract: PublishedAssetContract,
    plan: PublishedFramePlan,
) -> Any:
    """Create a Qlib DataLoader from a framework-neutral contract and mapping plan."""

    return QlibPublishedAssetAdapter(contract, plan).as_data_loader()
