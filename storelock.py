#!/usr/bin/env python3
"""
storelock.py — ONE WRITER OF THE STORE AT A TIME, scheduled or by hand (R15.11, Jake 2026-11-20).

Jake's 2026-11-20: the missed 05:30 daily run started at 09:13 when he logged on - the same minute he opened his own
PowerShell and ran the R14 block (token_metrics.py, archive_backfill.py, store_export.py) on the same store. runlock.py
(Round G 1.8) already serialises the metrics.db writes themselves, but a second writer then WAITS (up to 30 min) and
runs after: two runs of the day, each with its own run id. This lock is taken BEFORE anything starts, by every writer:

    windows/daily_run.ps1          who "scheduled" (-Scheduled) or "manual", for the whole run (its steps are nested)
    windows/near_lockups_run.ps1   who "scheduled NEAR lockups"
    token_metrics.py               every mode - a run, a --seed, --catch-up and the --no-fetch build (it writes)
    archive_backfill.py --run      (the plan, without --run, writes nothing and takes no lock)

Read-only commands (done_check.py, project_freeze.py --compare, store_export.py) never take it.

The lock is one file, logs/store.lock beside the store (TOKEN_METRICS_STORE_LOCK overrides), holding JSON: pid, the
process's START TIME (so a PID Windows has reused for another process is not trusted), since, who, command, max_h.
  * LIVE holder (its PID runs AND started at the recorded time, and it is younger than max_h): a second writer does not
    start. A manual command prints ONE line and exits 76 unless given --wait N (wait up to N minutes); the scheduled run
    logs "SKIPPED: a manual run holds the store (PID, since)" and exits 76 - a MISSED DAY, named in its log, on the
    console, in a notification and in logs/store_lock.log; never a silent skip.
  * STALE holder (PID gone, PID reused, or held longer than max_h - 6 h, the daily task's limit; 8 h for the weekly
    lockup read): taken over, and the takeover recorded in logs/store_lock.log. Nothing is ever stopped from here:
    a hung run is stopped by PID only by daily_run.ps1's existing stale path (R15.8).
  * Released on exit, error included (try/finally in Python and in PowerShell); a killed holder leaves a dead PID,
    which the next writer takes over.
A child of a holder (a daily_run step) sees TOKEN_METRICS_STORE_LOCK_HELD=<holder pid> and runs under it (nested).

    python storelock.py acquire --pid <pid> --who scheduled --command "daily_run.ps1" [--wait-min N]
                                [--wait-scheduled-min N] [--max-hours H]     exit 0 = taken, 76 = held (one line)
    python storelock.py release --pid <pid>                                  exit 0
    python storelock.py status                                               exit 0 = free, 76 = held
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

EXIT_HELD = 76
STALE_H = 6.0                 # the daily task's ExecutionTimeLimit (register_task.ps1)
START_TOLERANCE_S = 2.0       # the recorded start time vs the process's, both read by this module
HELD_ENV = "TOKEN_METRICS_STORE_LOCK_HELD"


class StoreHeld(SystemExit):
    """A live writer holds the store; .code is the one-line message (the exit code is EXIT_HELD)."""


def lock_path(db=None) -> Path:
    env = os.environ.get("TOKEN_METRICS_STORE_LOCK")
    if env:
        return Path(env)
    if db is None:
        import store as store_mod
        db = store_mod.DB_PATH
    return Path(db).resolve().parent / "logs" / "store.lock"


def process_start(pid: int) -> float | None:
    """The process's start time (epoch seconds), or None when no such process runs."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
                return None
            ft = [wintypes.FILETIME() for _ in range(4)]
            if not k32.GetProcessTimes(h, *[ctypes.byref(f) for f in ft]):
                return None
            t = (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
            return t / 1e7 - 11644473600.0
        finally:
            k32.CloseHandle(h)
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":                                 # a zombie has finished
                return None
            ticks = int(fields[19])
            btime = next(int(ln.split()[1]) for ln in Path("/proc/stat").read_text().splitlines()
                         if ln.startswith("btime"))
            return btime + ticks / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError, StopIteration):
            return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    try:
        import psutil  # type: ignore
        return float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 — alive, start unknown: compared as unknown
        return -1.0


def read(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def state(rec: dict | None, now: float | None = None) -> str:
    """'free' (no record), 'live', 'dead' (PID gone), 'reused' (PID runs, another start time) or 'old' (> max_h)."""
    if not rec:
        return "free"
    start = process_start(rec.get("pid"))
    if start is None:
        return "dead"
    want = rec.get("pid_start")
    if start != -1.0 and (want is None or abs(float(want) - start) > START_TOLERANCE_S):
        return "reused"
    now = time.time() if now is None else now
    if now - float(rec.get("since_epoch") or 0) > float(rec.get("max_h") or STALE_H) * 3600:
        return "old"
    return "live"


def describe(rec: dict) -> str:
    return (f"a {rec.get('who') or 'other'} run holds the store (PID {rec.get('pid')}, since {rec.get('since')}: "
            f"{rec.get('command') or '?'})")


def _record_line(path: Path, text: str) -> None:
    try:
        with open(path.with_suffix(".log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
    except OSError:
        pass


def _write_new(path: Path, rec: dict, replace: bool) -> bool:
    data = json.dumps(rec)
    if replace:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)
    else:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
    got = read(path) or {}                                  # two takers at once: the last write wins, the other yields
    return got.get("pid") == rec["pid"] and got.get("pid_start") == rec["pid_start"]


def acquire(who: str, command: str, pid: int | None = None, wait_s: float = 0.0, wait_scheduled_s: float = 0.0,
            max_h: float = STALE_H, path: Path | None = None, say=print, sleep=time.sleep,
            clock=time.monotonic) -> dict:
    """Take the lock for `pid` (default: this process). Returns {'status': 'taken' | 'nested' | 'held', 'rec': ...,
    'took_over': <stale record or None>}. Waits up to wait_s for any live holder, or up to wait_scheduled_s when the
    holder is another scheduled task; never waits for a manual holder unless wait_s says so."""
    path = Path(path) if path else lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid() if pid is None else int(pid)
    t0, said = clock(), None
    while True:
        cur = read(path) if path.exists() else None
        st = state(cur)
        if st == "live" and str(cur.get("pid")) == os.environ.get(HELD_ENV) and int(cur["pid"]) != pid:
            return {"status": "nested", "rec": cur, "took_over": None}
        if st == "live" and int(cur.get("pid")) == pid:
            return {"status": "nested", "rec": cur, "took_over": None}
        if st == "live":
            limit = max(wait_s, wait_scheduled_s if str(cur.get("who", "")).startswith("scheduled") else 0.0)
            waited = clock() - t0
            if waited >= limit:
                return {"status": "held", "rec": cur, "took_over": None}
            if said is None or clock() - said >= 60:
                say(f"STORE LOCK: waiting for {describe(cur)} (waited {waited / 60:.0f} of {limit / 60:.0f} min)")
                said = clock()
            sleep(min(5.0, max(0.1, limit - waited)))
            continue
        rec = {"pid": pid, "pid_start": process_start(pid), "since": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
               "since_epoch": time.time(), "who": who, "command": command[:200], "max_h": max_h}
        if cur is None and path.exists():                     # being written right now by another taker
            try:
                young = time.time() - path.stat().st_mtime < 5
            except OSError:
                young = False
            if young:
                sleep(0.2)
                continue
        stale = cur if st in ("dead", "reused", "old") else None
        if _write_new(path, rec, replace=stale is not None or (cur is None and path.exists())):
            if stale:
                why = {"dead": "its PID is gone", "reused": "its PID now belongs to another process",
                       "old": f"held more than {float(stale.get('max_h') or STALE_H):g} h"}[st]
                line = f"STORE LOCK: taken over from a stale lock ({why}): {describe(stale)}"
                say(line)
                _record_line(path, f"{line} -> PID {pid} ({who}: {command})")
            return {"status": "taken", "rec": rec, "took_over": stale}
        sleep(0.1)


def release(pid: int | None = None, path: Path | None = None) -> bool:
    """Remove the lock if `pid` (default: this process) holds it. True when removed."""
    path = Path(path) if path else lock_path()
    pid = os.getpid() if pid is None else int(pid)
    cur = read(path)
    if not cur or int(cur.get("pid") or -1) != pid:
        return False
    start = process_start(pid)
    if start not in (None, -1.0) and cur.get("pid_start") not in (None, -1.0) and \
            abs(float(cur["pid_start"]) - start) > START_TOLERANCE_S:
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def held_message(rec: dict, manual: bool = True) -> str:
    if manual:
        return (f"STORE LOCKED - {describe(rec)}. Nothing run (exit {EXIT_HELD}); run it again when that one has "
                f"finished, or add --wait N to wait up to N minutes.")
    return f"SKIPPED: {describe(rec)}"


@contextlib.contextmanager
def hold(command: str, wait_min: float = 0.0, who: str | None = None, path: Path | None = None, say=print):
    """with hold("token_metrics run", wait_min=args.wait): ... - raises StoreHeld (one line, exit 76) when a live writer
    holds the store; released when the block ends, however it ends."""
    who = who or os.environ.get("TOKEN_METRICS_STORE_LOCK_WHO") or "manual"
    got = acquire(who, command, wait_s=float(wait_min or 0) * 60, path=path, say=say)
    if got["status"] == "held":
        msg = held_message(got["rec"])
        _record_line(Path(path) if path else lock_path(), f"REFUSED {command} ({who}): {describe(got['rec'])}")
        raise StoreHeld(msg)
    try:
        yield got
    finally:
        if got["status"] == "taken":
            release(path=path)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="the store's one-writer lock (see the module doc)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("acquire")
    a1.add_argument("--pid", type=int, required=True)
    a1.add_argument("--who", default="manual")
    a1.add_argument("--command", default="")
    a1.add_argument("--wait-min", type=float, default=0.0)
    a1.add_argument("--wait-scheduled-min", type=float, default=0.0)
    a1.add_argument("--max-hours", type=float, default=STALE_H)
    r1 = sub.add_parser("release")
    r1.add_argument("--pid", type=int, required=True)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    path = lock_path()
    if a.cmd == "status":
        cur = read(path) if path.exists() else None
        st = state(cur)
        print(f"STORE LOCK: {st}" + (f" - {describe(cur)}" if cur else ""))
        return EXIT_HELD if st == "live" else 0
    if a.cmd == "release":
        print("STORE LOCK: released" if release(a.pid, path) else "STORE LOCK: not held by that PID - nothing done")
        return 0
    got = acquire(a.who, a.command, pid=a.pid, wait_s=a.wait_min * 60, wait_scheduled_s=a.wait_scheduled_min * 60,
                  max_h=a.max_hours, path=path)
    if got["status"] == "held":
        line = held_message(got["rec"], manual=not a.who.startswith("scheduled"))
        print(line)
        _record_line(path, f"REFUSED {a.command} ({a.who}): {describe(got['rec'])}")
        return EXIT_HELD
    print(f"STORE LOCK: {got['status']} by PID {a.pid} ({a.who}: {a.command})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
