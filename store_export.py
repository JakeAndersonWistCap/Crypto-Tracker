"""store_export.py --project <name> — READ-ONLY: one project's stored rows from metrics.db, in one file under exports/,
for a side branch (PR4F.5, Jake 2026-11-13: "we'll use it from Pendle on").

    python store_export.py --project Morpho          # writes exports/morpho-<as-of>.json.gz

What it holds: every stored row of that project — every date, so the catch-up window and anything older the build reads
(the build reads the whole store) — and the project's review-queue, manual-override, gap-report and last-30-days run-log
rows, in dump_fixture.py's shape, so tests/real_dump.py loads it into a fresh store as it stands. What it never holds:
another project's rows, explorer_routes.json, .env or any key (run-log messages and sources go through the run's URL
redaction, then any key=… left is masked). metrics.db is opened read-only (mode=ro): nothing is written to it.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def slug(project: str) -> str:
    return project.lower().replace(".", "").replace(" ", "_")


def export(project: str, db: str = "metrics.db", out_dir: str | Path = "exports") -> Path:
    from dump_fixture import _redact, _rows                           # the run's redaction; dump_fixture's row reader
    from fetch.archive import catch_up_bounds
    if not Path(db).exists():
        raise SystemExit(f"no {db} here — run this where the daily run keeps its store")
    now = datetime.now(timezone.utc)
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        met = _rows(con, "SELECT date, metric, value, source, tier, fetched_at FROM metrics WHERE project = ? "
                         "ORDER BY metric, date", (project,))
        if not met:
            raise SystemExit(f"no rows for {project!r} in {db}")
        for r in met:
            r["source"] = _redact(r["source"])
        rq = _rows(con, "SELECT * FROM review_queue WHERE project = ? ORDER BY ts", (project,))
        mo = _rows(con, "SELECT * FROM manual_overrides WHERE project = ? ORDER BY date", (project,))
        since = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        rl = _rows(con, "SELECT run_id, ts, source, tier, project, rows, status, message FROM run_log "
                        "WHERE project = ? AND ts >= ? ORDER BY ts DESC LIMIT 5000", (project, since))
        for r in rl:
            r["message"] = _redact(r["message"])
        last = con.execute("SELECT run_id FROM gap_report WHERE project = ? ORDER BY ts DESC LIMIT 1",
                           (project,)).fetchone()
        gaps = _rows(con, "SELECT * FROM gap_report WHERE project = ? AND run_id = ?", (project, last[0])) if last else []
        for r in rq + gaps:
            for k in ("source", "reason", "suggestion"):
                if k in r:
                    r[k] = _redact(r[k])
    finally:
        con.close()
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = ""
    lo, yesterday = catch_up_bounds()
    dates = [r["date"][:10] for r in met]
    payload = {"meta": {"project": project, "asof": now.strftime("%Y-%m-%d"), "dumped_at": now.isoformat(),
                        "git_commit": commit, "db": Path(db).name, "days": "all",
                        "catch_up_window": [str(lo.date()), str(yesterday.date())],
                        "first_date": min(dates), "last_date": max(dates), "metrics_filter": [],
                        "not_included": "other projects' rows, explorer_routes.json, .env, keys",
                        "counts": {"metrics": len(met), "review_queue": len(rq), "manual_overrides": len(mo),
                                   "run_log": len(rl), "gap_report": len(gaps), "manual_references": 0}},
               "metrics": met, "review_queue": rq, "manual_overrides": mo, "run_log": rl, "gap_report": gaps,
               "manual_references": []}
    path = Path(out_dir) / f"{slug(project)}-{payload['meta']['asof']}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=0, default=str)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, help="the project's name as in config (e.g. Morpho, Pendle)")
    ap.add_argument("--db", default="metrics.db")
    ap.add_argument("--out-dir", default="exports")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import config                                          # noqa: PLC0415
    if a.project not in config.PROJECT_BY_NAME:
        print(f"unknown project {a.project!r}; one of: {', '.join(sorted(config.PROJECT_BY_NAME))}")
        return 2
    path = export(a.project, a.db, a.out_dir)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        meta = json.load(fh)["meta"]
    print(f"wrote {path.as_posix()} — " + ", ".join(f"{k} {v:,}" for k, v in meta["counts"].items())
          + f"; rows {meta['first_date']}..{meta['last_date']}; as of {meta['asof']} on "
          + (meta["git_commit"] or "unknown commit"))
    return 0


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    raise SystemExit(main())
