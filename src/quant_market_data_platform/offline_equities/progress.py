from __future__ import annotations

import datetime as dt
import os
import pathlib
import threading
import time

from .integrity import save, sha_file

P = pathlib.Path


class Job:
    def __init__(self, out):
        self.out = out
        self.state = {
            "status": "running",
            "phase": "initializing",
            "pid": os.getpid(),
            "started_utc": dt.datetime.now(dt.UTC).isoformat(),
        }
        self.guard = threading.RLock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.beat, daemon=True)
        self.thread.start()

    def beat(self):
        while not self.stop.is_set():
            with self.guard:
                self.state["updated_utc"] = dt.datetime.now(dt.UTC).isoformat()
                save(self.out / "progress.json", self.state)
            self.stop.wait(10)

    def update(self, **kw):
        with self.guard:
            self.state.update(kw)
            self.state["updated_utc"] = dt.datetime.now(dt.UTC).isoformat()
            save(self.out / "progress.json", self.state)

    def finish(self, **kw):
        self.update(**kw)
        self.stop.set()
        self.thread.join()


def recover(path, out, label):
    path = P(path)
    if not path.exists():
        return
    if not (path.resolve().is_relative_to(out.resolve()) and path.resolve() != out.resolve()):
        raise ValueError("Integrity check failed")
    dest = out / "recovery" / f"{label}-{time.time_ns()}"
    dest.parent.mkdir(exist_ok=True)
    path.rename(dest)


def verify_part(path, info):
    if not P(path).stat().st_size == info["bytes"]:
        raise ValueError("Integrity check failed")
    if not sha_file(path) == info["sha256"]:
        raise ValueError("Integrity check failed")
