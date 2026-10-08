"""
fetch/logcache.py — the events a log scan has already read, so the next run reads only newer blocks.

** WHY. ** Every log scan re-read its whole history on every run: GEODNET's two mining wallets
from genesis, Sky's burns from block 20,663,735. Run 20260928T090446Z: mining_wallets_outflow
ran out of its 120s budget before reaching the head, every run, so it never stored anything.
Events below a confirmed block do not change, so each is read once and kept here; a run asks the
source only for blocks after `scanned_to`.

** ONE FILE PER STREAM, KEYED BY THE QUERY. ** A stream is (chain, contract, topics[, start
block]) — the exact filter asked of the source — so two scans that ask the same question share
it, and a changed filter (a different holder, a different from_block) is a different stream and
starts over. Attribution (count_from, exclusions, from_date) is NOT in the key: it is applied to
the events after they are read, so changing it takes effect on the next run without a rescan.

** THE CACHE IS DISPOSABLE; CORRECTNESS NEVER DEPENDS ON IT. ** Delete the directory and the
next run reads full history again — the first-run behaviour. LogScan still reconciles cached +
new events to balanceOf at the pinned block, to the wei, before storing anything, so a bad or
incomplete cache refuses the scan exactly as a dropped page would.

** proven_to: HOW MUCH OF IT A RECONCILIATION HAS VOUCHED FOR. ** A seed too large for one run's
budget is saved as it goes (scanned_to advances, proven_to does not). When a later run reaches
the head it reconciles; success sets proven_to = scanned_to. A FAILED reconciliation cuts every
stream back to its proven_to — to nothing, for a seed never proven — so an unproven segment
cannot keep a scan failing forever. A cut-back is logged with the file it touched.

Location: $TOKEN_METRICS_LOGCACHE, default .cache/logscan (git-ignored with the rest of .cache/).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger("token_metrics.fetch.logcache")

VERSION = 1


def stream_id(chain, address: str, topics: list, start=None) -> str:
    """Readable prefix plus a hash of the exact filter."""
    ident = json.dumps([str(chain), str(address).lower(),
                        [str(t).lower() if t else None for t in topics], start])
    digest = hashlib.sha1(ident.encode()).hexdigest()[:16]
    return f"{chain}-{str(address).lower()[:10]}-{digest}"



# ===== ONE WRITER AT A TIME, A TEMP FILE OF ITS OWN (Jake's run 2026-10-05). =====
# blockscout_stats crashed: "[WinError 32] The process cannot access the file because it is being used by
# another process: '.cache\\logscan\\daily-checks.tmp' -> '.cache\\logscan\\daily-checks.json'". Sources run
# on parallel threads and several write the same cache file through the same "<name>.tmp", so one thread's
# rename met another's open temp. Every cache write now (1) takes a per-file lock (re-entrant, so a
# read-modify-write can hold it across both), (2) writes its own uniquely named temp file, and (3) renames
# with retries on the Windows sharing violation (WinError 32 / 5) — a reader or antivirus holding the target
# for a moment. Across PROCESSES (a seed beside a routine run) the unique temp and the retry still apply.
_FILE_LOCKS: dict = {}
_FILE_LOCKS_GUARD = threading.Lock()


def file_lock(path) -> "threading.RLock":
    key = str(Path(path).resolve())
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(key, threading.RLock())


def unique_tmp(path) -> Path:
    path = Path(path)
    return path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp")


def replace_with_retry(src, dst, attempts: int = 8, sleep=time.sleep) -> None:
    """os.replace, retried with backoff (0.05s doubling, ~6s in all) on a Windows sharing violation."""
    delay = 0.05
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            if i == attempts - 1 or getattr(e, "winerror", 32) not in (5, 32):
                try:
                    os.remove(src)
                except OSError:
                    pass
                raise
            sleep(delay)
            delay *= 2


def atomic_write_text(path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        tmp = unique_tmp(path)
        tmp.write_text(text, encoding="utf-8")
        replace_with_retry(tmp, path)

class LogCache:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("TOKEN_METRICS_LOGCACHE", ".cache/logscan"))

    def path(self, sid: str) -> Path:
        return self.root / f"{sid}.json.gz"

    def load(self, sid: str) -> dict:
        """{"scanned_to": int|None, "proven_to": int|None, "events": [...]}; empty when absent
        or unreadable (an unreadable file is a reseed, never an error)."""
        empty = {"scanned_to": None, "proven_to": None, "events": []}
        f = self.path(sid)
        if not f.exists():
            return empty
        try:
            with gzip.open(f, "rt") as fh:
                st = json.load(fh)
            if st.get("version") != VERSION:
                return empty
            return {"scanned_to": st.get("scanned_to"), "proven_to": st.get("proven_to"),
                    "events": st.get("events") or []}
        except (OSError, ValueError, EOFError) as e:
            log.info("logcache: %s unreadable (%s) — reseeding that stream", f, e)
            return empty

    def save(self, sid: str, events: list, scanned_to: int, proven_to) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        f = self.path(sid)
        with file_lock(f):
            tmp = unique_tmp(f)
            with gzip.open(tmp, "wt") as fh:
                json.dump({"version": VERSION, "scanned_to": int(scanned_to),
                           "proven_to": None if proven_to is None else int(proven_to),
                           "events": events}, fh, separators=(",", ":"))
            replace_with_retry(tmp, f)

    def cut_back(self, sid: str, block_of) -> str:
        """Drop everything a reconciliation has not vouched for. Returns what was done."""
        st = self.load(sid)
        f = self.path(sid)
        if st["scanned_to"] is None:
            return "nothing cached"
        if st["proven_to"] is None:
            f.unlink(missing_ok=True)
            return f"removed {f} (never proven — the next run reseeds this stream)"
        if st["scanned_to"] == st["proven_to"]:
            return f"kept {f} (proven to block {st['proven_to']:,})"
        kept = [e for e in st["events"] if block_of(e) <= st["proven_to"]]
        self.save(sid, kept, st["proven_to"], st["proven_to"])
        return (f"cut {f} back from block {st['scanned_to']:,} to its proven "
                f"{st['proven_to']:,}")


class DailyChecks:
    """Checks that need running once a day, not every run. Added 2026-09-28.

    DefiLlama's restructure probes, Morpho's recovery check and the RWA-category rebuild answer
    questions whose answers change at most daily; they ran on every run. The date each last ran
    is kept in daily-checks.json beside the log cache — disposable like it: delete the file and
    everything runs again. A check marked done only after it completes, so a failed or
    interrupted one runs again on the next run the same day.
    """

    def __init__(self, root: str | Path | None = None):
        self.f = LogCache(root).root / "daily-checks.json"

    def _read(self) -> dict:
        try:
            return json.loads(self.f.read_text())
        except (OSError, ValueError):
            return {}

    def due(self, key: str, today: str) -> bool:
        # TOKEN_METRICS_DAILY_CHECKS=always runs every check on every run (after a config change
        # that needs one re-checked now; the tests use it so each scenario runs its check).
        if os.environ.get("TOKEN_METRICS_DAILY_CHECKS", "").strip().lower() == "always":
            return True
        return self._read().get(key) != today

    def get(self, key: str) -> str | None:
        """The value stored under `key` (a date for a check; any short string set())."""
        v = self._read().get(key)
        return None if v is None else str(v)

    def set(self, key: str, value: str) -> None:
        """Store a short string beside the checks (e.g. which metrics a once-a-day read stored)."""
        self.done(key, value)

    def ever(self, key: str) -> bool:
        """True once the check has completed on any day — a series' history has been read."""
        return key in self._read()

    def done(self, key: str, today: str) -> None:
        with file_lock(self.f):                          # read-modify-write under one lock: no lost key
            state = self._read()
            state[key] = today
            atomic_write_text(self.f, json.dumps(state, sort_keys=True))
