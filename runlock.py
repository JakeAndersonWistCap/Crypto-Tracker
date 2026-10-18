"""
runlock.py — ONE WRITER ON metrics.db AT A TIME (Round G 1.8).

Jake's 2026-10-18: the scheduled daily run started at 10:42 and his manual runs at 10:48, both on the same metrics.db —
two runs upserting the same rows, each with its own run id, each writing the workbook. Every entry point that writes the
store (token_metrics.py: a run, a seed, --no-fetch) takes this lock first.

An OS lock on `<metrics.db>.lock` (msvcrt on Windows, fcntl elsewhere): the operating system drops it when the process
ends, however it ends, so a crashed run never leaves a stale lock behind. The file also says WHO holds it (pid, start
time, command line) so the second run can name the first. The second run WAITS up to TOKEN_METRICS_LOCK_WAIT seconds
(default 1800 — a full run takes ~10 minutes), saying once a minute what it is waiting for, then exits with code 75 and
a plain message. TOKEN_METRICS_LOCK_WAIT=0 exits at once.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

EXIT_LOCKED = 75


class RunLocked(SystemExit):
    """Another run holds metrics.db; raised after the wait, with the message and exit code 75."""


def _try_lock(fh) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def holder(path) -> str:
    """Who holds the lock, as the holder wrote it beside the lock file (empty when unreadable)."""
    try:
        return Path(f"{path}.who").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


class RunLock:
    """with RunLock(db_path, what): ... — waits for (or refuses) a second writer."""

    def __init__(self, db_path, what: str, wait_s: float | None = None, say=print, sleep=time.sleep,
                 clock=time.monotonic):
        self.path = Path(f"{db_path}.lock")
        self.what = what
        self.wait_s = float(os.environ.get("TOKEN_METRICS_LOCK_WAIT", 1800) if wait_s is None else wait_s)
        self.say, self.sleep, self.clock = say, sleep, clock
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        t0, said = self.clock(), None
        while not _try_lock(fh):
            waited = self.clock() - t0
            who = holder(self.path) or "another run"
            if waited >= self.wait_s:
                fh.close()
                raise RunLocked(
                    f"RUN LOCKED — {self.path.parent / self.path.stem} is in use by {who}. Waited {waited:.0f}s; "
                    f"this {self.what} did NOT run (exit {EXIT_LOCKED}). Run it again when that one has finished "
                    f"(its window prints its final line), or set TOKEN_METRICS_LOCK_WAIT to wait longer.")
            if said is None or self.clock() - said >= 60:
                self.say(f"RUN LOCK: waiting for {who} to finish before this {self.what} starts "
                         f"(waited {waited:.0f}s of {self.wait_s:.0f}s)")
                said = self.clock()
            self.sleep(min(5.0, max(0.1, self.wait_s - waited)))
        # WHO HOLDS IT, in a file beside the lock (Windows refuses reads of a locked byte range to other processes)
        try:
            Path(f"{self.path}.who").write_text(
                f"pid {os.getpid()}, {self.what} started {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC ("
                + " ".join(os.path.basename(a) if i == 0 else a for i, a in enumerate(sys.argv))[:200] + ")",
                encoding="utf-8")
        except OSError:
            pass
        self.fh = fh
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            try:
                Path(f"{self.path}.who").unlink()
            except OSError:
                pass
            _unlock(self.fh)
            self.fh.close()
            self.fh = None
        return False
