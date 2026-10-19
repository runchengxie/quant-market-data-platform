"""One process-safe writer lane shared by history acquisition and gap repair."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class HistoryWriterBusy(RuntimeError):
    """Another historical minute writer currently owns the lane."""


@contextmanager
def history_writer_lock(root: Path) -> Iterator[None]:
    path = root.resolve() / "metadata/minute_backfill/reverse_scheduler/writer.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HistoryWriterBusy("Historical minute writer is already active") from exc
        try:
            handle.seek(0)
            handle.truncate()
            json.dump({"pid": os.getpid(), "acquired_at": datetime.now(UTC).isoformat()}, handle)
            handle.flush()
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
