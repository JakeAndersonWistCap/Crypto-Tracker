"""
tests/real_dump.py — load a dump_fixture.py file (fixtures/real_<project>.json, exported from Jake's metrics.db) into
a fresh store and run credibility_report on it, so a fix is tested against the REAL rows.

    from real_dump import evaluate_real
    o = evaluate_real(tmp_path, monkeypatch, "Pendle")         # skips while fixtures/real_pendle.json is absent

The rows go in verbatim (date, value, source, tier, fetched_at; review-queue flags too), with no validation, so the
store the build reads is the one Jake's run read. The as-of date is the dump's.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def dump_path(project: str) -> Path:
    return FIXTURES / f"real_{project.lower().replace('.', '').replace(' ', '_')}.json"


def load(project: str) -> dict:
    p = dump_path(project)
    if not p.exists():
        pytest.skip(f"{p.name} not on file yet — run `python dump_fixture.py --project {project}` and commit it")
    return json.loads(p.read_text(encoding="utf-8"))


def to_store(payload: dict, db: Path) -> None:
    """Write the dump's rows into a fresh metrics.db at `db`, verbatim."""
    import store as sm
    sm.Store(db).close()                                   # the schema
    project = payload["meta"]["project"]
    con = sqlite3.connect(db)
    try:
        con.executemany("INSERT OR REPLACE INTO metrics (date, project, metric, value, source, tier, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(r["date"], project, r["metric"], r["value"], r["source"], r["tier"], r["fetched_at"])
                         for r in payload["metrics"]])
        for table in ("review_queue", "manual_overrides", "gap_report", "run_log"):
            rows = payload.get(table) or []
            if not rows:
                continue
            cols = [c[1] for c in con.execute(f"PRAGMA table_info({table})").fetchall()]
            keep = [c for c in cols if c in rows[0]]
            con.executemany(f"INSERT INTO {table} ({', '.join(keep)}) VALUES ({', '.join('?' * len(keep))})",
                            [tuple(r.get(c) for c in keep) for r in rows])
        con.commit()
    finally:
        con.close()


def long_frame(payload: dict) -> pd.DataFrame:
    """The dump's metric rows as the long frame credibility's formulas read (date as Timestamp), fetched_at included as
    store.load_long gives it."""
    df = pd.DataFrame(payload["metrics"])
    df["project"] = payload["meta"]["project"]
    df["date"] = pd.to_datetime(df["date"])
    return df[["date", "project", "metric", "value", "source", "tier", "fetched_at"]]


def evaluate_real(tmp_path, monkeypatch, project: str, payload: dict | None = None) -> dict:
    """{row id: {verdict, ours, ref, note}} from credibility_report on the real dump, as of the dump's date. `payload`:
    the dump as loaded, with rows added from a committed source (manual_overrides.csv) — never hand-built values."""
    payload = payload or load(project)
    import credibility_report as cr
    import store as sm
    db = tmp_path / "metrics.db"
    to_store(payload, db)
    monkeypatch.setattr(sm, "DB_PATH", str(db))
    monkeypatch.setitem(sys.modules, "recalc", None)
    rws, tab = cr.evaluate(project, asof=pd.Timestamp(payload["meta"]["asof"]), narrow=True)
    assert rws is not None, "credibility_report built nothing from the dump"
    return {r["id"]: {"verdict": t[10], "ours": t[4], "ref": t[6], "note": t[11]}
            for r, t in zip(rws, tab) if r["project"] == project}
