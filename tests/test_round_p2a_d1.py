"""Round P2a, D1 (2026-10-27): why Hyperliquid's pool_release_tokens has interior holes, as a look-only check
(orphan_cleanup.sql section DC) that names each day's cause from the store and the run log — and agrees, day for
day, with the read-time view that forms the release (build_workbook._onchain_release_views). No network."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

RAW = "hypercore_info:tokenDetails.circulatingSupply"
A1 = "hypercore_info:tokenDetails.circulatingSupply-exclude_wallets"


def _section_rows(db: Path, step: str = "DC1") -> list[dict]:
    import run_sql
    sec = run_sql.parse_sections(run_sql.SQL_FILE.read_text(encoding="utf-8"))["DC"]
    stmts = [s for s in run_sql.split_statements(sec["text"]) if run_sql.classify(s) == "select"]
    stmt = next(s for s in stmts if f"-- {step}." in s)
    con = sqlite3.connect(db)
    try:
        cur = con.execute(run_sql.strip_comments(stmt))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _fake_store(tmp_path: Path) -> Path:
    """Six kinds of day: formed, no run (10-02..04), first day after a gap (10-05), a basis change (10-12), a tier
    timeout (10-17: no stock, the next day loses its prior), a wallet unread (10-22: tokenDetails stored, circulating
    not), and days formed after each."""
    import store as sm
    db = tmp_path / "metrics.db"
    sm.Store(db).close()
    rows, logs = [], []
    days = {"2026-09-30": RAW, "2026-10-01": RAW, "2026-10-05": RAW, "2026-10-06": RAW, "2026-10-11": RAW,
            "2026-10-12": A1, "2026-10-13": A1, "2026-10-16": A1, "2026-10-18": A1, "2026-10-19": A1,
            "2026-10-21": A1, "2026-10-23": A1, "2026-10-24": A1}
    for n, (d, src) in enumerate(sorted(days.items())):
        rows.append((d, "Hyperliquid", "circulating_supply_first_party", 3e8 - 1000.0 * n, src, 1, f"{d}T20:00:00Z"))
        logs.append(("r", f"{d}T20:00:01Z", "hypercore_info", 1, "Hyperliquid", 30, "ok", "tokenDetails read"))
    rows.append(("2026-10-22", "Hyperliquid", "circulating_supply_tokendetails", 3.02e8, RAW, 1, "2026-10-22T20:00:00Z"))
    logs += [("r", "2026-10-17T20:00:00Z", "hypercore_info", 1, None, 0, "failed",
              "TIER TIMED OUT after 60s — abandoned and the run moved on. STOCKS NOT STORED: "
              "Hyperliquid/circulating_supply_first_party"),
             ("r", "2026-10-22T20:00:00Z", "hypercore_info", 1, "Hyperliquid", 0, "failed",
              "circulating_supply_first_party NOT STORED: Hyper Foundation (0xd57ecca4…) unread — HTTP 500"),
             ("r", "2026-10-20T20:00:00Z", "hl_candles", 1, "Hyperliquid", 1, "ok", "another tier only")]
    con = sqlite3.connect(db)
    con.executemany("INSERT INTO metrics (date, project, metric, value, source, tier, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.executemany("INSERT INTO run_log (run_id, ts, source, tier, project, rows, status, message) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", logs)
    con.commit()
    con.close()
    return db


def _view_release_days(db: Path) -> set[str]:
    """The days build_workbook's first-party release view forms a release on, from the same stored rows."""
    import build_workbook as bw
    con = sqlite3.connect(db)
    try:
        long = pd.read_sql("SELECT date, project, metric, value, source, tier FROM metrics "
                           "WHERE project = 'Hyperliquid'", con)
    finally:
        con.close()
    long["date"] = pd.to_datetime(long["date"])
    groups = {k: g for k, g in long.groupby(["project", "metric"])}
    bw._onchain_release_views(groups)
    rel = groups.get(("Hyperliquid", "pool_release_tokens"))
    return set() if rel is None else {str(pd.Timestamp(d).date()) for d in rel["date"]}


def test_dc_names_the_cause_of_every_hole(tmp_path):
    db = _fake_store(tmp_path)
    got = {r["day"]: r["verdict"] for r in _section_rows(db)}
    assert list(got) == [str(d.date()) for d in pd.date_range("2026-09-30", "2026-10-24")]
    assert got["2026-10-01"] == "release formed"
    for d in ("2026-10-02", "2026-10-03", "2026-10-04", "2026-10-20"):
        assert got[d].startswith("HOLE: no hypercore_info line"), (d, got[d])     # a tier line of another source only
    assert got["2026-10-05"].startswith("HOLE: no reading the day before"), got["2026-10-05"]
    assert got["2026-10-12"] == "HOLE: basis change from the day before - not differenced"
    assert got["2026-10-17"].startswith("HOLE: the tier timed out"), got["2026-10-17"]
    assert got["2026-10-18"].startswith("HOLE: no reading the day before"), got["2026-10-18"]
    assert got["2026-10-22"].startswith("HOLE: a declared wallet or the HyperEVM burn was unread"), got["2026-10-22"]
    row22 = next(r for r in _section_rows(db) if r["day"] == "2026-10-22")
    assert row22["tokendetails_row"] == 1 and row22["source"] is None
    assert got["2026-10-24"] == "release formed"


def test_dc_agrees_day_for_day_with_the_release_view(tmp_path):
    """The look-only check is the view's own rule: 'release formed' exactly on the days the view forms one."""
    db = _fake_store(tmp_path)
    formed = {r["day"] for r in _section_rows(db) if r["verdict"] == "release formed"}
    assert formed == _view_release_days(db) == {"2026-10-01", "2026-10-06", "2026-10-13", "2026-10-19",
                                                "2026-10-24"}


def test_dc_on_jakes_10_13_dump_names_the_d7_2_holes(tmp_path):
    """Jake's 10-13 dump: the release forms on 10-01, 10-06..10-11 and 10-13 (D7-2's 'holes: 2026-10-02..10-05,
    2026-10-12'); 10-02..10-04 had no hypercore_info read at all (no run reached the tier), 10-05 differences a 4-day
    change and 10-12 is decision A1's change of basis."""
    from real_dump import load, to_store
    db = tmp_path / "metrics.db"
    to_store(load("Hyperliquid"), db)
    rows = _section_rows(db)
    got = {r["day"]: r["verdict"] for r in rows}
    formed = {d for d, v in got.items() if v == "release formed"}
    assert formed == {"2026-10-01", *(f"2026-10-{d:02d}" for d in range(6, 12)), "2026-10-13"}
    assert formed == _view_release_days(db)
    assert all(got[f"2026-10-0{d}"].startswith("HOLE: no hypercore_info line") for d in (2, 3, 4))
    assert got["2026-10-05"].startswith("HOLE: no reading the day before")
    assert got["2026-10-12"].startswith("HOLE: basis change")
    fails = _section_rows(db, "DC2")
    assert [f["ts"][:10] for f in fails] == ["2026-10-06"] and "TIER TIMED OUT" in fails[0]["message"]


def test_dc_is_look_only_and_runs_through_run_sql(tmp_path, capsys):
    import run_sql
    db = _fake_store(tmp_path)
    before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    assert run_sql.main(["DC", "--db", str(db)]) == 0
    assert "section DC: 2 SELECT(s) run" in capsys.readouterr().out
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == before
    sec = run_sql.parse_sections(run_sql.SQL_FILE.read_text(encoding="utf-8"))["DC"]
    assert not run_sql.uncommented_write(sec["text"]), "DC is look-only: it ships no DELETE / UPDATE"
