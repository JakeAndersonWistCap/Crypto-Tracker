"""
dump_fixture.py — export what one project's Credibility rows read from YOUR metrics.db, so a fix is tested against the
real store instead of a hand-built fixture (Jake's run 2026-10-09 ~10:15: "fixtures passed, the real store did not").

    python dump_fixture.py --project Pendle
    python dump_fixture.py --project Ethereum --metrics gross_issuance_tokens,gross_burn_tokens,beacon_chain_eth
    python dump_fixture.py --project Chainlink --days 120 --out fixtures/real_chainlink.json

Writes fixtures/real_<project>.json:
    meta               project, the as-of date (today, UTC), when it was dumped, the git commit, the filters used
    metrics            every stored row of the project (date, metric, value, source, tier, fetched_at) — or only
                       --metrics — over the last --days days (default 400: Q0 plus the trailing year)
    review_queue       the project's review-queue rows (flags such as anchor_unconfirmed / stored_flagged)
    manual_overrides   the project's manual overrides
    run_log            the project's run-log lines over the last 30 days (newest 5,000), URLs redacted
    gap_report         the project's rows from the latest run
    manual_references  the project's rows of manual_references.csv (Jake's readings), for the record

Nothing is written to metrics.db. Keys never reach the file: run-log messages go through the same URL redaction the
run uses, and the dump holds no .env values. tests/real_dump.py loads a file back into a fresh store for the tests.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_KEYISH = re.compile(r"(?i)((?:api[-_]?key|apikey|key|token|secret)=)[^&\s'\"]+")


def _redact(text) -> str:
    """Hosts only, never a key: the run's own URL redaction, then any key=... left in free text."""
    s = "" if text is None else str(text)
    try:
        from fetch.chain import redact_urls                # noqa: PLC0415
        s = redact_urls(s)
    except Exception:  # noqa: BLE001 — the regex below still runs
        pass
    return _KEYISH.sub(r"\1<redacted>", s)


def _rows(con, sql, params=()):
    cur = con.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def dump(project: str, db: str = "metrics.db", metrics: list[str] | None = None, days: int = 400,
         out: str | None = None) -> Path:
    if not Path(db).exists():
        raise SystemExit(f"no {db} here — run this where the daily run keeps its store")
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=int(days))).strftime("%Y-%m-%d")
    con = sqlite3.connect(db)
    try:
        q = "SELECT date, metric, value, source, tier, fetched_at FROM metrics WHERE project = ? AND date >= ?"
        params: list = [project, since]
        if metrics:
            q += " AND metric IN (%s)" % ",".join("?" * len(metrics))
            params += list(metrics)
        met = _rows(con, q + " ORDER BY metric, date", params)
        if not met:
            raise SystemExit(f"no rows for {project!r} in {db} since {since}"
                             + (f" for {', '.join(metrics)}" if metrics else ""))
        rq = _rows(con, "SELECT * FROM review_queue WHERE project = ? ORDER BY ts", (project,))
        mo = _rows(con, "SELECT * FROM manual_overrides WHERE project = ? ORDER BY date", (project,))
        log_since = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        # THE TIER-WIDE LINES TOO (Jake's run 2026-10-09 15:59: "tier aero_voter TIMED OUT" is logged with no project)
        # — those of the sources this project's lines name
        rl = _rows(con, "SELECT run_id, ts, source, tier, project, rows, status, message FROM run_log "
                        "WHERE (project = ? OR (project IS NULL AND source IN (SELECT DISTINCT source FROM run_log "
                        "WHERE project = ?))) AND ts >= ? ORDER BY ts DESC LIMIT 5000", (project, project, log_since))
        for r in rl:
            r["message"] = _redact(r["message"])
        last = con.execute("SELECT run_id FROM gap_report WHERE project = ? ORDER BY ts DESC LIMIT 1",
                           (project,)).fetchone()
        gaps = _rows(con, "SELECT * FROM gap_report WHERE project = ? AND run_id = ?", (project, last[0])) if last else []
    finally:
        con.close()
    refs = []
    mr = ROOT / "manual_references.csv"
    if mr.exists():
        with mr.open(newline="", encoding="utf-8") as fh:
            refs = [r for r in csv.DictReader(fh) if (r.get("project") or "") == project]
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = ""
    payload = {"meta": {"project": project, "asof": now.strftime("%Y-%m-%d"), "dumped_at": now.isoformat(),
                        "git_commit": commit, "db": str(db), "days": int(days), "metrics_filter": metrics or [],
                        "counts": {"metrics": len(met), "review_queue": len(rq), "manual_overrides": len(mo),
                                   "run_log": len(rl), "gap_report": len(gaps), "manual_references": len(refs)}},
               "metrics": met, "review_queue": rq, "manual_overrides": mo, "run_log": rl, "gap_report": gaps,
               "manual_references": refs}
    path = Path(out) if out else ROOT / "fixtures" / f"real_{project.lower().replace('.', '').replace(' ', '_')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, help="the project's name as in config (e.g. Pendle, Ether.fi)")
    ap.add_argument("--metrics", default="", help="comma-separated metric keys (default: every metric of the project)")
    ap.add_argument("--days", type=int, default=400, help="days of history to export (default 400)")
    ap.add_argument("--db", default="metrics.db")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import config                                          # noqa: PLC0415
    if a.project not in config.PROJECT_BY_NAME:
        print(f"unknown project {a.project!r}; one of: {', '.join(sorted(config.PROJECT_BY_NAME))}")
        return 2
    metrics = [m.strip() for m in a.metrics.split(",") if m.strip()] or None
    path = dump(a.project, a.db, metrics, a.days, a.out)
    meta = json.loads(path.read_text(encoding="utf-8"))["meta"]
    print(f"wrote {path} — " + ", ".join(f"{k} {v:,}" for k, v in meta["counts"].items())
          + f"; as of {meta['asof']} on {meta['git_commit'] or 'unknown commit'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
