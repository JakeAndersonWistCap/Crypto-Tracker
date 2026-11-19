"""Round 7D (Jake 2026-11-16, overnight): gate green, sign off Sky and Hyperliquid. Behaviour and values. No network."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import project_freeze as pf  # noqa: E402


# ============================================================ A1: every frozen-move declaration stays on record
def test_a1_a_later_move_is_appended_and_the_latest_is_judged():
    for p, first, latest in (("Fluid", 3.0, 4.0), ("Maple", 2.0, 3.0)):
        for k in ("reader|A3 Revenue Buyback|Rank in A3", "reader|Summary|Rank in archetype"):
            d = config.FROZEN_MOVES[p][k]
            assert d["to"] == first and d["date"] == "2026-11-11" and d["why"].startswith("PR3B.")   # as declared then
            assert [t["to"] for t in d["then"]] == [latest] and d["then"][-1]["date"] == "2026-11-16"
    decl = {"k": {"date": "2026-11-11", "to": 3.0, "why": "first",
                  "then": [{"date": "2026-11-16", "to": 4.0, "why": "later"}]}}
    assert pf.undeclared({"k": 2.0}, {"k": 4.0}, decl) == []                 # the latest declared value
    assert pf.undeclared({"k": 2.0}, {"k": 3.0}, decl) == ["k: 2.0 -> 3.0"]  # the superseded one no longer excuses
    assert pf.undeclared({"k": 2.0}, {"k": 5.0}, decl) == ["k: 2.0 -> 5.0"]


def test_a1_declare_later_move_keeps_an_existing_declaration(monkeypatch):
    monkeypatch.setitem(config.FROZEN_MOVES, "X", {"a": {"date": "d1", "to": 1.0, "why": "w1"}})
    config.declare_later_move("X", "a", 2.0, "w2", "d2")
    config.declare_later_move("X", "b", 9.0, "w3", "d2")
    assert config.FROZEN_MOVES["X"]["a"]["to"] == 1.0 and config.FROZEN_MOVES["X"]["a"]["then"][0]["to"] == 2.0
    assert config.FROZEN_MOVES["X"]["b"] == {"date": "d2", "to": 9.0, "why": "w3"}


# ===================================== A1: the reader's "Issuance or release" reads the key's own denominator (s9)
def test_a1_option_c_issuance_is_what_the_reader_shows_beside_the_key():
    src = (ROOT / "reader.py").read_text(encoding="utf-8")
    i = src.index('elif kind == "issuance":')
    block = src[i:i + 1800]
    assert "OPTION_C_ISSUANCE" in block and 'A.at("Data", dr, dc)' in block
    assert config.OPTION_C_ISSUANCE["Hyperliquid"]["metric"] == "issuance_option_c_tokens"


# ================================================= A2: Hyperliquid's net supply change, declared until 2026-12-29
def test_a2_hyperliquid_net_supply_gap_is_declared_dated_and_expires(monkeypatch):
    import done_check as dc
    spec = config.CIRC_CHANGE_DECLARED_GAP["Hyperliquid"]
    # R12 (Jake 2026-11-19): clear-by 2027-01-11, the rule-basis series starting 2026-10-13
    # (was clear_by 2026-12-29, first_stored 2026-09-30 — Jake 2026-11-16)
    assert spec["clear_by"] == "2027-01-11" and spec["first_stored"] == "2026-10-13"
    assert (pd.Timestamp(spec["first_stored"]) + pd.Timedelta(days=90)).date() == date(2027, 1, 11)
    gaps = [(pd.Timestamp("2026-08-19"), "no circulating reading on 2026-08-18 (the opening)")]
    monkeypatch.setitem(bw.CIRC_CHANGE_GAPS, "Hyperliquid", gaps)
    monkeypatch.setitem(bw.CIRC_CHANGE_GAPS, "Sky", gaps)
    hl = "n/a — " + bw._circ_change_gap_text({"name": "Hyperliquid"})
    assert "Assistance Fund" in hl and "2027-01-11" in hl                     # was "2026-12-29"
    ok, cls, why = dc.classify("n/a", hl, date(2026, 11, 16))
    assert ok and cls == "history" and "2027-01-11" in why
    assert dc.classify("n/a", hl, date(2027, 1, 11))[0] is True               # was date(2026, 12, 29)
    assert dc.classify("n/a", hl, date(2027, 1, 12))[0] is False              # past the date it is open work again
    sky = "n/a — " + bw._circ_change_gap_text({"name": "Sky"})
    assert "DECLARED" not in sky and dc.classify("n/a", sky, date(2026, 11, 16))[0] is False   # no other project


def test_a2_a_long_reason_is_joined_from_pieces_of_at_most_255_characters(monkeypatch):
    import re

    class R:
        def D(self, r, metric, col="q0"):
            return "Data!$A$1"
    monkeypatch.setitem(bw.CIRC_CHANGE_GAPS, "Hyperliquid",
                        [(pd.Timestamp("2026-08-19"), "no circulating reading on 2026-08-18 (the opening)")])
    monkeypatch.setitem(bw._SHIFT, "k", 0)
    f = bw._net_supply_circ(R(), 5, config.PROJECT_BY_NAME["Hyperliquid"])
    lits = re.findall(r'"([^"]*)"', f)
    assert lits and max(len(x) for x in lits) <= 255 and "2027-01-11" in "".join(lits)   # was "2026-12-29" (R12)
    assert len(f) < 8000


# ================================================================ decision 2: the USDH sends stay named, not booked
def test_usdh_sends_stay_named_and_unbooked():
    ev = config.HL_USDH_YIELD_EVIDENCE
    assert ev["sender_documented"] is False and "not booked" in config.HL_HOLDER_SHARE_GAP


# ================================================== B: a store export carries the macro rows its project's cells read
def test_b_store_export_carries_the_macro_rows_and_they_load_under_the_macro_project(tmp_path):
    import gzip
    import json
    import sqlite3
    import store as sm
    import store_export
    from real_dump import to_store
    db = tmp_path / "metrics.db"
    sm.Store(db).close()
    con = sqlite3.connect(db)
    con.executemany("INSERT INTO metrics (date, project, metric, value, source, tier, fetched_at) VALUES (?,?,?,?,?,?,?)",
                    [("2026-11-15", "Sky", "price_usd", 0.06, "coingecko", 1, "2026-11-16T00:00:00Z"),
                     ("2026-11-14", config.MACRO_PROJECT, "tbill_3m_yield", 0.0391, "treasury", 1, "2026-11-16T00:00:00Z"),
                     ("2026-11-14", config.MACRO_PROJECT, "not_a_macro_metric", 1.0, "x", 1, "2026-11-16T00:00:00Z"),
                     ("2026-11-15", "Pendle", "price_usd", 3.0, "coingecko", 1, "2026-11-16T00:00:00Z")])
    con.commit()
    con.close()
    path = store_export.export("Sky", str(db), tmp_path / "exports")
    pl = json.load(gzip.open(path, "rt", encoding="utf-8"))
    assert [r["metric"] for r in pl["macro_metrics"]] == ["tbill_3m_yield"] and pl["meta"]["counts"]["macro_metrics"] == 1
    assert {r["metric"] for r in pl["metrics"]} == {"price_usd"} and len(pl["metrics"]) == 1      # Sky's rows only
    db2 = tmp_path / "loaded.db"
    to_store(pl, db2)
    con = sqlite3.connect(db2)
    got = con.execute("SELECT project, metric, value FROM metrics ORDER BY project").fetchall()
    con.close()
    assert got == [(config.MACRO_PROJECT, "tbill_3m_yield", 0.0391), ("Sky", "price_usd", 0.06)]


# ============================================================================ B: Sky and Hyperliquid frozen
def test_b_sky_and_hyperliquid_are_frozen_with_their_rounds_moves_on_record():
    import json
    assert set(config.FROZEN_PROJECTS) == set(config.FROZEN_BEFORE_R7D) | {"Sky", "Hyperliquid"}
    assert len(config.FROZEN_BEFORE_R7D) == 5
    for p, n, rounds in (("Sky", 482, {"R6", "R7B", "R7C"}), ("Hyperliquid", 616, {"R7", "R7B", "R7C", "R7D"})):
        base = json.loads(pf.baseline_path(p).read_text(encoding="utf-8"))
        moves = config.frozen_moves_before_r9(p)          # R9.2's moves (the rule) are their own record
        assert len(base) == n and len(moves) > 200
        assert all(d["why"] and d["date"] and "from" in d for d in moves.values())
        assert all(base[k] == pf._norm(d["to"]) for k, d in moves.items())      # the baseline holds every declared value
        assert {d["why"].split(" ")[0] for d in moves.values()} == rounds
        assert pf.undeclared(base, base, moves) == []
    # the freeze holds from here: a frozen dump is never refreshed again
    import dump_fixture
    assert dump_fixture.main(["--project", "Sky", "--from-export", "x.json.gz"]) == 2
