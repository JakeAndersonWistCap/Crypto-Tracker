"""
progress.py — output that shows up while a long run is still running.

Jake's run (2026-10-11): two probes piped through Tee-Object printed nothing for 40+ minutes. Python
block-buffers stdout when it is not a terminal, so every line sat in a buffer until exit. Two parts:

  line_buffered()   every entry point calls it first: stdout and stderr flush at each newline, piped or not.
  Heartbeat         a context manager around a run (or a probe). A daemon thread prints one line at least
                    every `every` seconds — the step, n/N, elapsed and ETA — even while a single slow call
                    blocks the loop. Loops report where they are with tick(step, n, total); the innermost
                    open heartbeat takes it.

    with Heartbeat("near_lockups"):
        for i, acc in enumerate(accounts, 1):
            tick("lockups read", i, len(accounts))
            ...

Nothing here touches what a run computes; it only says how far it has got.
"""
from __future__ import annotations

import sys
import threading
import time

_STACK: list = []
_LOCK = threading.Lock()


def line_buffered() -> None:
    """stdout and stderr flush at every newline, also when piped (Tee-Object, | tee, > file)."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):          # a replaced stream (pytest capture) has no reconfigure
            pass


def _fmt(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


class Heartbeat:
    """At least one line every `every` seconds while open: '[heartbeat] <name> — <step> n/N (p%), elapsed E,
    ETA T'. The ETA is the elapsed time per item since the step began x the items left; without a total it says
    so. `say` defaults to print (flushed); token_metrics passes its logger."""

    def __init__(self, name: str, every: float = 60.0, say=None):
        self.name, self.every = name, float(every)
        self.say = say or (lambda line: print(line, flush=True))
        self.step, self.n, self.total = "running", None, None
        self.t0 = self.step_t0 = time.monotonic()
        self._stop = threading.Event()
        self._thread = None

    def tick(self, step: str | None = None, n: int | None = None, total: int | None = None) -> None:
        with _LOCK:
            if step is not None and step != self.step:
                self.step, self.step_t0 = step, time.monotonic()
            self.n, self.total = n, total

    def line(self) -> str:
        now = time.monotonic()
        with _LOCK:
            step, n, total, st0 = self.step, self.n, self.total, self.step_t0
        where = step
        if n is not None:
            where += f" {n:,}" + (f"/{total:,} ({n / total:.0%})" if total else "")
        if n and total and total >= n:
            eta = _fmt((now - st0) / n * (total - n))
        else:
            eta = "unknown (no total yet)" if total is None else "unknown"
        return f"  [heartbeat] {self.name} — {where}, elapsed {_fmt(now - self.t0)}, ETA {eta}"

    def _run(self):
        while not self._stop.wait(self.every):
            try:
                self.say(self.line())
            except Exception:  # noqa: BLE001 — a closed stream at exit must not raise in a daemon thread
                return

    def __enter__(self):
        _STACK.append(self)
        self._thread = threading.Thread(target=self._run, name=f"heartbeat:{self.name}", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self in _STACK:
            _STACK.remove(self)
        return False


def tick(step: str | None = None, n: int | None = None, total: int | None = None) -> None:
    """Report progress to the innermost open Heartbeat (a no-op when none is open)."""
    if _STACK:
        _STACK[-1].tick(step, n, total)
