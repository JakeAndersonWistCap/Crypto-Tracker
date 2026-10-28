"""disk_guard.py — STOP EARLY WHEN THE DISK IS TOO FULL, AND SAY WHAT THE REPO CAN SAFELY GIVE BACK (P2f-4).

Jake's P2e suite ran with 1.7 GB free; a run that fills the disk half-way writes half a workbook or fails inside
SQLite. The run (token_metrics.py, archive_backfill.py --run) and the full test suite (tests/conftest.py) now start
with one check and stop with ONE line:

    only 1.7 GB free on /home/user/Crypto-Tracker; need 3.0 GB (full suite) - free space first: python disk_guard.py --list

    python disk_guard.py                      check the run's need (exit 74 when short, 0 when enough)
    python disk_guard.py --need 3 --what X    check an explicit need
    python disk_guard.py --list               what the repo can safely give back, with sizes and the commands
    python disk_guard.py --clean --yes        delete exactly that safe set (without --yes it deletes nothing)

The need: the run needs 1 GB plus twice the store (a backup copy and the working rewrite); the full suite 3 GB; both
overridable with TOKEN_METRICS_MIN_FREE_GB (run) and TOKEN_METRICS_MIN_FREE_GB_SUITE (suite). Writes nothing but
what --clean --yes deletes. Never touches metrics.db, its backups, .cache/ (the scanned-event cache: deleting it costs
a re-scan of the chains), data/, the manual_*.csv files, or the NEWEST THREE snapshots: the guard's --newer and the
credibility continuity read the previous run's snapshots/snapshot_<date>.csv.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXIT_DISK = 74                        # EX_IOERR: nothing was started
GB = 1024 ** 3
RUN_BASE_GB = 1.0                     # workbooks, logs, temp files
SUITE_GB = 3.0
SUITE_MIN_TESTS = 300                 # a smaller collection is a targeted run, not the full suite
SMALL_RUN_GB = 0.3
KEEP_SNAPSHOTS = 3
KEEP_LOG_DAYS = 30


def free_gb(path) -> float:
    """Free space in GB on the drive holding `path` (the nearest existing parent when it does not exist yet)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / GB


def short_line(free: float, need: float, where, what: str) -> str:
    return (f"only {free:.1f} GB free on {where}; need {need:.1f} GB ({what}) - "
            f"free space first: python disk_guard.py --list")


def check(paths, need_gb: float, what: str) -> str | None:
    """None when every drive holding one of `paths` has `need_gb` free, else the one-line message for the fullest."""
    worst = None
    for p in paths:
        f = free_gb(p)
        if f < need_gb and (worst is None or f < worst[0]):
            worst = (f, p)
    return None if worst is None else short_line(worst[0], need_gb, worst[1], what)


def run_need_gb(db_path=None) -> float:
    """The run's need: TOKEN_METRICS_MIN_FREE_GB, else 1 GB + twice the store's size."""
    env = os.environ.get("TOKEN_METRICS_MIN_FREE_GB")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    db = Path(db_path) if db_path else ROOT / "metrics.db"
    size = db.stat().st_size / GB if db.exists() else 0.0
    return RUN_BASE_GB + 2 * size


def suite_need_gb(n_tests: int) -> float:
    env = os.environ.get("TOKEN_METRICS_MIN_FREE_GB_SUITE")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return SUITE_GB if n_tests >= SUITE_MIN_TESTS else SMALL_RUN_GB


def guard_or_code(what: str = "run", db_path=None, say=print) -> int:
    """0 when the run may start; EXIT_DISK (after saying why in one line) when it may not."""
    where = [ROOT] + ([Path(db_path).resolve().parent] if db_path else [])
    msg = check(where, run_need_gb(db_path), what)
    if msg:
        say(msg)
        return EXIT_DISK
    return 0


# ------------------------------------------------------------------------------------------------ what can go
def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else 0


def safe_to_delete(root: Path = ROOT, now: float | None = None) -> list[tuple[Path, str]]:
    """[(path, why)] — files and folders the repo regenerates on its own. Never the newest KEEP_SNAPSHOTS snapshots,
    never a log newer than KEEP_LOG_DAYS days, never the store, its backups, .cache/ or data/."""
    now = time.time() if now is None else now
    out: list[tuple[Path, str]] = []
    for d in (root / "tests" / "_scratch", root / ".pytest_cache"):
        if d.exists():
            out.append((d, "test scratch, rebuilt by the next test run"))
    for d in root.rglob("__pycache__"):
        rel = d.relative_to(root).parts
        if ".claude" not in rel and ".git" not in rel:
            out.append((d, "Python bytecode, rebuilt on import"))
    for f in root.glob("*.credibility.pkl"):
        out.append((f, "credibility evaluation cache (12 h), re-evaluated on a miss; no stored data"))
    snaps = sorted((root / "snapshots").glob("snapshot_*.csv")) if (root / "snapshots").is_dir() else []
    for f in snaps[:-KEEP_SNAPSHOTS] if len(snaps) > KEEP_SNAPSHOTS else []:
        out.append((f, f"old daily theory snapshot (the newest {KEEP_SNAPSHOTS} are kept: the guard reads the previous)"))
    cut = now - KEEP_LOG_DAYS * 86400
    for f in sorted((root / "logs").glob("run_*.log")) if (root / "logs").is_dir() else []:
        if f.stat().st_mtime < cut:
            out.append((f, f"daily run log older than {KEEP_LOG_DAYS} days"))
    return out


def yours_to_decide(root: Path = ROOT) -> list[tuple[Path, str]]:
    """Large things only Jake can judge: listed with sizes, never deleted by --clean."""
    out = [(f, "a store backup - delete the old ones by hand, keep the newest")
           for f in sorted(root.glob("metrics.db.bak*"))]
    for name, why in (("dumps", "dump_fixture.py output - copy what you want into fixtures/ first"),
                      (".cache", "scanned-event cache - deleting it costs a re-scan of the chains")):
        if (root / name).exists():
            out.append((root / name, why))
    return out


def _fmt(n: int) -> str:
    return f"{n / GB:.2f} GB" if n >= GB else f"{n / 1024 ** 2:.1f} MB"


def list_report(root: Path = ROOT, say=print) -> int:
    """Prints both lists with sizes and the PowerShell commands; returns the safe set's total bytes."""
    safe = safe_to_delete(root)
    total = sum(_size(p) for p, _ in safe)
    say(f"SAFE TO DELETE - regenerated by the repo ({_fmt(total)}):")
    for p, why in safe:
        say(f"  {_fmt(_size(p)):>10}  {p.relative_to(root)}  - {why}")
    if not safe:
        say("  (nothing)")
    say("  delete exactly these:   python disk_guard.py --clean --yes")
    say("  by hand (PowerShell):   Remove-Item -Recurse -Force .pytest_cache, tests\\_scratch; "
        "Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force")
    say("YOURS TO DECIDE - never deleted by --clean:")
    mine = yours_to_decide(root)
    for p, why in mine:
        say(f"  {_fmt(_size(p)):>10}  {p.relative_to(root)}  - {why}")
    if not mine:
        say("  (nothing)")
    say("NEVER: metrics.db, data\\, manual_*.csv, the newest 3 snapshots")
    return total


def clean(root: Path = ROOT, say=print) -> int:
    n = 0
    for p, _why in safe_to_delete(root):
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        n += 1
    say(f"deleted {n} item(s) from the safe set")
    return n


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--need", type=float, help="GB needed (default: the run's need)")
    ap.add_argument("--what", default="run")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--yes", action="store_true", help="with --clean: really delete")
    a = ap.parse_args(argv)
    if a.list:
        list_report()
        return 0
    if a.clean:
        if not a.yes:
            print("--clean deletes nothing without --yes; python disk_guard.py --list shows what it would delete")
            return 2
        clean()
        return 0
    need = a.need if a.need is not None else run_need_gb()
    msg = check([ROOT], need, a.what)
    if msg:
        print(msg)
        return EXIT_DISK
    print(f"disk ok: {free_gb(ROOT):.1f} GB free, need {need:.1f} GB ({a.what})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
