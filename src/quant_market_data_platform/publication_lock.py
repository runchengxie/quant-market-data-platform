"""Root-scoped POSIX publication locking and durable single-file replacement.

Nested calls reuse this thread's transaction token, never acquire a second flock.
Data writer locks precede this lock, which precedes clean journal ownership locks.
"""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

_LOCAL = threading.local()


@contextmanager
def publication_lock(root: Path) -> Generator[None]:
    try:
        import fcntl
    except ImportError as exc:
        raise RuntimeError("Current publication requires POSIX flock support") from exc

    root = root.expanduser().resolve()
    key = (os.getpid(), str(root))
    held = getattr(_LOCAL, "held", set())
    if key in held:
        yield
        return
    directory = root / "metadata" / "current_assets"
    if directory.resolve() != directory:
        raise ValueError("Publication lock parents must be physical")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".publication.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Publication lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Publication lock busy: {root}") from exc
        _LOCAL.held = held | {key}
        try:
            yield
        finally:
            _LOCAL.held = held
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, content: bytes) -> None:
    """Replace a physical file, flushing content and its directory before return."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Publication output must be physical: {path}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    created = False
    try:
        with temporary.open("xb") as stream:
            created = True
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if created:
            temporary.unlink(missing_ok=True)
