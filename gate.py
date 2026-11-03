"""gate.py - the one full gate on a commit (P5-0, Jake 2026-11-03).

    python gate.py --rev HEAD --out <dir> [--workers 3]

Runs, on a clean snapshot of REV (git archive, so the working tree can keep changing while it runs):

    1. collect   pytest --collect-only: the test ids, written to <out>/collected.txt (the same tests must run)
    2. tests     the whole of tests/, with --durations=0 and --junitxml (every test id with its outcome); with
                 --workers N > 1 through pytest-xdist, -n N --dist loadgroup: conftest makes every test file one
                 group (a file never splits across workers, so its module fixtures are built once) and the eight
                 files sharing the build of the 15 dumps one group (tests/shared_build.py, so it is built once)
    3. guard     python measuring_guard.py --compare tests/fixtures/guard_baseline.json (the fixture guard; the
                 live-store guard is Jake's own step, `python measuring_guard.py --newer metrics.db`)
    4. ruff      ruff check --select F,E9: the total must not exceed --ruff-max (81, the P4 count)

and passes only when all four pass. Nothing is skipped and nothing is retried.

GATE HYGIENE (P5-0 d2). The P4 gates timed the same tests at 5 h and at 1 h 15 min: the difference was the machine,
not the tests, and nothing had recorded it. Every step now records, sampled every --sample seconds:
    - the 1-minute load average (min / mean / max over the step) and the machine's CPU count;
    - every OTHER test process on the machine (pytest or measuring_guard.py outside this gate's own process tree);
    - free memory (MemAvailable) and the peak resident memory of each of this gate's own processes.
A step is marked CONTENDED when another test process ran during it, or when its mean load exceeds the processes
the step itself runs by more than 1.5. A contended step's time is not a measure of the tests.

MEMORY (Jake's rule): the gate does not start with less than 10 GB free, and never runs more than 3 workers.

Output: <out>/times.txt (one line per step, the table the report repeats), <out>/gate.json (the same and the
samples), <out>/<step>.txt (each step's full output). Exit 0 = the gate passed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_FREE_GB = 10.0          # Jake: >= 10 GB free before a gate starts
MAX_WORKERS = 3             # Jake: at most 3 workers
RUFF_MAX = 81               # the P4 count of ruff --select F,E9; the gate fails above it
CONTENTION_MARGIN = 1.5     # mean load above the step's own processes by more than this = CONTENDED
TEST_MARKERS = ("pytest", "measuring_guard.py")
EXIT_LOW_MEMORY = 3


# ---------------------------------------------------------------------------------------------------- machine state
def loadavg1() -> float | None:
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):        # Windows has no load average
        return None


def mem_available_gb(meminfo: str | None = None) -> float | None:
    """MemAvailable from /proc/meminfo, in GB (None where there is no /proc)."""
    if meminfo is None:
        try:
            meminfo = Path("/proc/meminfo").read_text()
        except OSError:
            return None
    m = re.search(r"^MemAvailable:\s+(\d+)\s+kB", meminfo, re.M)
    return int(m.group(1)) / 1024 / 1024 if m else None


def _proc_table(proc: Path = Path("/proc")) -> dict[int, tuple[int, str, int]]:
    """{pid: (parent pid, command line, resident kB)} for every readable process."""
    out = {}
    if not proc.is_dir():
        return out
    for p in proc.iterdir():
        if not p.name.isdigit():
            continue
        try:
            stat = (p / "stat").read_text()
            ppid = int(stat[stat.rfind(")") + 2:].split()[1])
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            rss = 0
            for line in (p / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1])
            out[int(p.name)] = (ppid, cmd, rss)
        except (OSError, ValueError, IndexError):
            continue
    return out


def descendants(table: dict[int, tuple[int, str, int]], root: int) -> set[int]:
    """root and every process below it."""
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _c, _r) in table.items():
        kids.setdefault(ppid, []).append(pid)
    seen, todo = set(), [root]
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        todo.extend(kids.get(p, ()))
    return seen


def foreign_test_processes(table: dict[int, tuple[int, str, int]], own: set[int]) -> list[tuple[int, str]]:
    """Test processes on the machine that are not this gate's: they share its CPUs and make its times wrong."""
    return sorted((pid, cmd[:160]) for pid, (_pp, cmd, _r) in table.items()
                  if pid not in own and "python" in cmd.split(" ")[0] and any(m in cmd for m in TEST_MARKERS))


# ---------------------------------------------------------------------------------------------------- the sampler
class Sampler(threading.Thread):
    """Samples load, free memory, foreign test processes and this gate's own resident memory."""

    def __init__(self, interval: float = 15.0, root_pid: int | None = None):
        super().__init__(daemon=True)
        self.interval, self.root = interval, root_pid or os.getpid()
        self.samples: list[dict] = []
        self._stop = threading.Event()

    def sample(self) -> dict:
        table = _proc_table()
        own = descendants(table, self.root)
        return {"t": time.time(), "load1": loadavg1(), "mem_gb": mem_available_gb(),
                "foreign": foreign_test_processes(table, own),
                "own_rss_kb": {pid: table[pid][2] for pid in own if pid in table and table[pid][2]},
                "own_cmd": {pid: table[pid][1][:120] for pid in own if pid in table and table[pid][2]}}

    def run(self):
        while not self._stop.is_set():
            self.samples.append(self.sample())
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()


def summarize(samples: list[dict], t0: float, t1: float, procs: int) -> dict:
    """The step's load (min/mean/max), the foreign test processes seen, peak memory, and the CONTENDED verdict."""
    win = [s for s in samples if t0 <= s["t"] <= t1]
    loads = [s["load1"] for s in win if s.get("load1") is not None]
    foreign = sorted({tuple(f) for s in win for f in s.get("foreign", ())})
    peak: dict[str, int] = {}
    for s in win:
        for pid, kb in s.get("own_rss_kb", {}).items():
            cmd = s.get("own_cmd", {}).get(pid, "")
            key = f"{pid} {cmd}"
            peak[key] = max(peak.get(key, 0), kb)
    mems = [s["mem_gb"] for s in win if s.get("mem_gb") is not None]
    mean = sum(loads) / len(loads) if loads else None
    contended = bool(foreign) or (mean is not None and mean > procs + CONTENTION_MARGIN)
    return {"samples": len(win), "load_min": min(loads) if loads else None, "load_mean": mean,
            "load_max": max(loads) if loads else None, "foreign": foreign, "contended": contended,
            "mem_min_gb": min(mems) if mems else None,
            "peak_rss_mb": {k: round(v / 1024) for k, v in sorted(peak.items(), key=lambda kv: -kv[1])}}


# ---------------------------------------------------------------------------------------------------- the steps
def ruff_total(output: str) -> int:
    """The total of `ruff check --statistics` (one line per rule, the count first)."""
    total = 0
    for line in output.splitlines():
        m = re.match(r"\s*(\d+)\s+\S", line)
        if m:
            total += int(m.group(1))
    return total


def guard_violations(output: str) -> int | None:
    m = re.search(r"MEASURING-POINT GUARD\s*\S*\s*(\d+) violation", output)
    return int(m.group(1)) if m else None


def tests_command(workers: int, python: str = sys.executable, junit: str | None = None) -> list[str]:
    cmd = [python, "-m", "pytest", "-q", "-rf", "--durations=0", "-p", "no:cacheprovider"]
    if junit:
        cmd += [f"--junitxml={junit}"]
    if workers > 1:
        cmd += ["-n", str(workers), "--dist", "loadgroup"]
    return cmd + ["tests"]


def junit_outcomes(path: Path) -> dict[str, str]:
    """{test id: passed / failed / error / skipped} from a junit xml file."""
    import xml.etree.ElementTree as ET
    out = {}
    for tc in ET.parse(path).getroot().iter("testcase"):
        mod = tc.get("classname", "").replace(".", "/")
        tid = f"{mod}.py::{tc.get('name')}" if mod else tc.get("name")
        kids = {c.tag for c in tc}
        out[tid] = ("failed" if "failure" in kids else "error" if "error" in kids else
                    "skipped" if "skipped" in kids else "passed")
    return out


def durations(output: str) -> list[tuple[float, str, str]]:
    """(seconds, phase, test id) from pytest's slowest-durations section."""
    out = []
    for line in output.splitlines():
        m = re.match(r"\s*([\d.]+)s (setup|call|teardown)\s+(\S+)", line)
        if m:
            out.append((float(m.group(1)), m.group(2), m.group(3)))
    return sorted(out, reverse=True)


def per_file(durs: list[tuple[float, str, str]]) -> list[tuple[float, str]]:
    acc: dict[str, float] = {}
    for s, _ph, tid in durs:
        acc[tid.split("::")[0]] = acc.get(tid.split("::")[0], 0.0) + s
    return sorted(((round(v, 1), k) for k, v in acc.items()), reverse=True)


def snapshot(rev: str, dest: Path) -> str:
    """git archive REV into dest; returns the full commit hash."""
    full = subprocess.run(["git", "rev-parse", rev], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()
    data = subprocess.run(["git", "archive", full], cwd=ROOT, check=True, capture_output=True).stdout
    with tempfile.TemporaryFile() as f:
        f.write(data)
        f.seek(0)
        with tarfile.open(fileobj=f) as t:
            t.extractall(dest)  # noqa: S202 - our own repository's archive
    return full


def run_step(name: str, cmd: list[str], cwd: Path, out: Path, sampler: Sampler, procs: int,
             env: dict | None = None, timeout: float = 4 * 3600) -> dict:
    t0 = time.time()
    start = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(out / f"{name}.txt", "w", encoding="utf-8") as fh:
        try:
            rc = subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, env=env, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    t1 = time.time()
    sampler.samples.append(sampler.sample())
    return {"step": name, "start": start, "seconds": round(t1 - t0), "exit": rc, "procs": procs,
            **summarize(sampler.samples, t0, t1, procs)}


def fmt_row(r: dict) -> str:
    lo = "n/a" if r.get("load_mean") is None else f"{r['load_min']:.2f}/{r['load_mean']:.2f}/{r['load_max']:.2f}"
    mem = "n/a" if r.get("mem_min_gb") is None else f"{r['mem_min_gb']:.1f}"
    return (f"{r['step']:8s} start {r['start']}  {r['seconds']:6d} s  exit {r['exit']!s:7s} load min/mean/max {lo}"
            f"  free-min {mem} GB  {'CONTENDED' if r['contended'] else 'clean'}"
            + (f"  foreign={len(r['foreign'])}" if r["foreign"] else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--sample", type=float, default=15.0, help="seconds between samples")
    ap.add_argument("--ruff-max", type=int, default=RUFF_MAX)
    a = ap.parse_args(argv)
    if not 1 <= a.workers <= MAX_WORKERS:
        print(f"gate: --workers must be 1..{MAX_WORKERS}")
        return 2
    free = mem_available_gb()
    if free is not None and free < MIN_FREE_GB:
        print(f"gate: {free:.1f} GB free, below the {MIN_FREE_GB:.0f} GB rule - not started")
        return EXIT_LOW_MEMORY
    out = Path(a.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix="gate_"))
    sampler = Sampler(a.sample)
    sampler.start()
    t_start = time.time()
    rows = []
    try:
        full = snapshot(a.rev, work)
        head = f"gate rev {full[:7]} workers {a.workers} cpus {os.cpu_count()} free {free if free is None else round(free, 1)} GB"
        env = dict(os.environ)
        env.pop("PYTEST_XDIST_WORKER", None)
        r = run_step("collect", [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                                 "tests"], work, out, sampler, 1, env)
        ids = [x for x in (out / "collect.txt").read_text(encoding="utf-8").splitlines() if "::" in x]
        (out / "collected.txt").write_text("\n".join(ids) + "\n", encoding="utf-8")
        r["tests"] = len(ids)
        rows.append(r)
        r = run_step("tests", tests_command(a.workers, junit=str(out / "junit.xml")), work, out, sampler,
                     a.workers, env)
        text = (out / "tests.txt").read_text(encoding="utf-8")
        summ = [x for x in text.splitlines() if re.search(r"\d+ (passed|failed)", x)]
        r["summary"] = summ[-1] if summ else "no summary line"
        # THE SAME TESTS RAN: every collected id has an outcome, and nothing ran that was not collected
        try:
            got = junit_outcomes(out / "junit.xml")
        except (OSError, Exception):  # noqa: BLE001 - a missing or broken file is a failed comparison
            got = {}
        (out / "outcomes.txt").write_text("".join(f"{v}\t{k}\n" for k, v in sorted(got.items())),
                                          encoding="utf-8")
        r["ran"] = len(got)
        r["same_tests"] = len(got) == len(ids)
        r["not_passed"] = sorted(k for k, v in got.items() if v != "passed")
        rows.append(r)
        r = run_step("guard", [sys.executable, "measuring_guard.py", "--compare", "tests/fixtures/guard_baseline.json"],
                     work, out, sampler, 1, env)
        r["violations"] = guard_violations((out / "guard.txt").read_text(encoding="utf-8"))
        rows.append(r)
        r = run_step("ruff", ["ruff", "check", "--select", "F,E9", "--statistics", "."], work, out, sampler, 1, env)
        r["ruff_total"] = ruff_total((out / "ruff.txt").read_text(encoding="utf-8"))
        rows.append(r)
    finally:
        sampler.stop()
        shutil.rmtree(work, ignore_errors=True)
    total = round(time.time() - t_start)
    by = {r["step"]: r for r in rows}
    ok = (by.get("collect", {}).get("exit") == 0 and by.get("tests", {}).get("exit") == 0
          and by.get("tests", {}).get("same_tests") is True
          and by.get("guard", {}).get("exit") == 0 and by.get("guard", {}).get("violations") == 0
          and by.get("ruff", {}).get("ruff_total", 10 ** 9) <= a.ruff_max)
    durs = durations((out / "tests.txt").read_text(encoding="utf-8")) if (out / "tests.txt").exists() else []
    lines = [head, *(fmt_row(r) for r in rows),
             f"total {total} s ({total // 3600} h {total % 3600 // 60:02d} min {total % 60:02d} s)",
             f"tests: {by.get('tests', {}).get('summary', '-')}",
             f"same tests: collected {by.get('collect', {}).get('tests')}, ran {by.get('tests', {}).get('ran')}"
             f" -> {'yes' if by.get('tests', {}).get('same_tests') else 'NO'}",
             *(f"  not passed: {t}" for t in by.get("tests", {}).get("not_passed", [])[:50]),
             f"guard violations: {by.get('guard', {}).get('violations')}",
             f"ruff F,E9: {by.get('ruff', {}).get('ruff_total')} (max {a.ruff_max})",
             f"GATE {'PASSED' if ok else 'FAILED'}", "", "slowest 40:",
             *(f"  {s:8.1f}  {ph:8s} {tid}" for s, ph, tid in durs[:40]), "", "per test file (seconds):",
             *(f"  {s:8.1f}  {f}" for s, f in per_file(durs))]
    for r in rows:
        if r["foreign"]:
            lines += ["", f"foreign test processes during {r['step']}:", *(f"  {p} {c}" for p, c in r["foreign"])]
        if r["peak_rss_mb"]:
            lines += ["", f"peak resident memory during {r['step']} (MB):",
                      *(f"  {mb:7d}  {k}" for k, mb in list(r["peak_rss_mb"].items())[:8])]
    (out / "times.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "gate.json").write_text(json.dumps({"rows": rows, "total": total, "passed": ok,
                                               "samples": sampler.samples}, default=str), encoding="utf-8")
    print("\n".join(lines[:9]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
