from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class SourcePayload:
    """Immutable provider response evidence passed from fetchers to parsers."""

    provider: str
    dataset: str
    source_locator: str
    retrieved_at: datetime
    content_type: str
    body: bytes
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider = str(self.provider).strip().lower()
        dataset = str(self.dataset).strip()
        locator = str(self.source_locator).strip()
        content_type = str(self.content_type).strip().lower()
        if not provider:
            raise ValueError("source payload provider must be non-empty")
        if not dataset:
            raise ValueError("source payload dataset must be non-empty")
        if not locator:
            raise ValueError("source payload source_locator must be non-empty")
        if not isinstance(self.body, bytes):
            raise TypeError("source payload body must be bytes")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "dataset", dataset)
        object.__setattr__(self, "source_locator", locator)
        object.__setattr__(self, "retrieved_at", _utc(self.retrieved_at))
        object.__setattr__(self, "content_type", content_type)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def byte_count(self) -> int:
        return len(self.body)
