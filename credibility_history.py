#!/usr/bin/env python3
"""
credibility_history.py — every run's Credibility verdicts, kept, and how each row behaves over weeks (Jake, 2026-10-11).

    python credibility_history.py                 the report: per row, verdict flips in the last 7 / 14 / 28 days,
                                                  mean and max |gap| over 28 days, and every row whose verdict
                                                  changed since the previous run
    python credibility_history.py --project Sky   one project
    python credibility_history.py --days 28       the |gap| window (default 28)

`--record` evaluates the tab (credibility_report, cached per store state) and records the run — windows/daily_run.ps1
runs it after each run, before the daily summary: one row per Credibility row in the table `credibility_history` of logs/credibility_history.sqlite — run id, timestamp, project, row key, ours,
reference, gap, tolerance, verdict. A separate file, not metrics.db: the credibility cache keys on the store's
fingerprint, and nothing here may change what a build computes. READ-ONLY on the verdict logic: it stores what the
tab said.

A FLIP is a change of verdict CATEGORY between two consecutive recorded runs of the same row (PASS, CHECK, FRESH,
UNVERIFIABLE, N/A, LIMITATION, MATURING, FINDING) — "PASS" to "PASS (NEAR LIMIT)" is not a flip; PASS to CHECK is.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent / "logs" / "credibility_history.sqlite"
SCHEMA = """
CREATE TABLE IF NOT EXISTS credibility_history (
    run_id    TEXT NOT NULL,
    ts        TEXT NOT NULL,
    project   TEXT NOT NULL,
    row_key   TEXT NOT NULL,
    what      TEXT,
    ours      REAL,
    reference REAL,
    gap       REAL,
    tolerance REAL,
    verdict   TEXT,
    PRIMARY KEY (run_id, project, row_key)
)"""
CATEGORIES = ("PASS", "CHECK", "FRESH", "UNVERIFIABLE", "N/A", "DOCUMENTED LIMITATION", "MATURING", "VERIFIED FINDING")


def category(verdict) -> str:
    v = str(verdict or "").strip().upper()
    for c in CATEGORIES:
        if v.startswith(c):
            return c
    return v.split(" ")[0] if v else "BLANK"


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def connect(path: Path | str = DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    return con


def record(rows: list, tab: list, run_id: str, ts: str | None = None, path: Path | str = DB) -> int:
    """Store one run: `rows` (credibility.build_rows, with project / id / what) paired with `tab` (the evaluated tab
    rows: [4] ours, [6] reference, [8] gap, [9] tolerance, [10] verdict). Re-recording a run id replaces it."""
    ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    con = connect(path)
    try:
        data = [(run_id, ts, r["project"], r["id"], str(r.get("what") or "")[:200], _num(t[4]), _num(t[6]),
                 _num(t[8]), _num(t[9]), str(t[10] or "")) for r, t in zip(rows, tab)]
        con.executemany("INSERT OR REPLACE INTO credibility_history VALUES (?,?,?,?,?,?,?,?,?,?)", data)
        con.commit()
        return len(data)
    finally:
        con.close()


def report(project: str | None = None, days: int = 28, now: datetime | None = None,
           path: Path | str = DB) -> list[str]:
    """The report lines (see the module docstring)."""
    if not Path(path).exists():
        return [f"no history yet ({path}) — `credibility_history.py --record` records each run"]
    now = now or datetime.now(timezone.utc)
    con = connect(path)
    try:
        q = "SELECT run_id, ts, project, row_key, what, gap, tolerance, verdict FROM credibility_history"
        args: tuple = ()
        if project:
            q, args = q + " WHERE lower(project) = lower(?)", (project,)
        recs = con.execute(q + " ORDER BY ts, run_id", args).fetchall()
    finally:
        con.close()
    if not recs:
        return ["no recorded runs" + (f" for {project}" if project else "")]
    runs = list(dict.fromkeys((r[1], r[0]) for r in recs))          # (ts, run_id) in order
    by_row: dict = {}
    for run_id, ts, proj, key, what, gap, tol, verdict in recs:
        by_row.setdefault((proj, key), []).append((datetime.fromisoformat(ts), run_id, what, gap, tol, verdict))
    win = {d: now - timedelta(days=d) for d in (7, 14, 28)}
    gap_from = now - timedelta(days=days)
    last_run, prev_run = runs[-1][1], (runs[-2][1] if len(runs) > 1 else None)
    out = [f"CREDIBILITY HISTORY — {len(runs)} recorded run(s), {runs[0][0][:10]} .. {runs[-1][0][:10]}; "
           f"{len(by_row)} row(s)" + (f" ({project})" if project else ""),
           "",
           f"  {'project':<12} {'row':<34} {'flips 7d':>8} {'14d':>4} {'28d':>4} {'mean|gap|':>10} {'max|gap|':>9} "
           f"{'tol':>6}  verdict now"]
    changed = []
    for (proj, key), hist in sorted(by_row.items()):
        flips = {d: 0 for d in win}
        for (_t0, _r0, _w0, _g0, _l0, v0), (t1, _r1, _w1, _g1, _l1, v1) in zip(hist, hist[1:]):
            if category(v0) != category(v1):
                for d, since in win.items():
                    if t1 >= since:
                        flips[d] += 1
        gaps = [abs(g) for t, _r, _w, g, _l, _v in hist if g is not None and t >= gap_from]
        mean = f"{sum(gaps) / len(gaps):.2%}" if gaps else "-"
        mx = f"{max(gaps):.2%}" if gaps else "-"
        tol = hist[-1][4]
        out.append(f"  {proj:<12} {key[:34]:<34} {flips[7]:>8} {flips[14]:>4} {flips[28]:>4} {mean:>10} {mx:>9} "
                   f"{(f'{tol:.0%}' if tol is not None else '-'):>6}  {hist[-1][5][:60]}")
        if prev_run is not None:
            now_v = next((h[5] for h in hist if h[1] == last_run), None)
            was_v = next((h[5] for h in hist if h[1] == prev_run), None)
            if now_v is not None and was_v is not None and category(now_v) != category(was_v):
                changed.append(f"  {proj:<12} {key[:34]:<34} {was_v[:40]} -> {now_v[:60]}")
            elif now_v is not None and was_v is None:
                changed.append(f"  {proj:<12} {key[:34]:<34} (new row) -> {now_v[:60]}")
    out += ["", f"CHANGED VERDICT SINCE THE PREVIOUS RUN ({prev_run or 'none recorded'} -> {last_run}): {len(changed)}"]
    out += changed or ["  none"]
    return out


def latest_run_id() -> str:
    """The newest run id in metrics.db's run_log, else a timestamp (a build without a fetching run)."""
    import store as store_mod
    try:
        con = sqlite3.connect(store_mod.DB_PATH)
        try:
            row = con.execute("SELECT run_id FROM run_log ORDER BY ts DESC LIMIT 1").fetchone()
        finally:
            con.close()
        if row and row[0]:
            return str(row[0])
    except sqlite3.Error:
        pass
    return "build-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--record", action="store_true",
                    help="first evaluate the tab on metrics.db (credibility_report, cached per store state) and record "
                         "this run — windows/daily_run.ps1 does this after the run, before the daily summary")
    a = ap.parse_args(argv)
    if a.record:
        from credibility_report import evaluate            # noqa: PLC0415
        rows, tab = evaluate()
        if rows is None:
            return 1
        run_id = latest_run_id()
        print(f"recorded run {run_id}: {record(rows, tab, run_id, path=a.db)} row(s)")
    for ln in report(a.project, a.days, path=a.db):
        print(ln.encode("ascii", "replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    sys.exit(main())
