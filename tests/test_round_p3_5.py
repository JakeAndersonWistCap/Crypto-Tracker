"""P3-5: done_check.py — is each non-parked project DONE? Read-only, one line per project.

Unit cases on small hand-made workbooks (openpyxl): one per blocker class (rule-3 suspect, BROKEN, MISSING on a
headline, STALE over 3 days, unranked without a date, n/a / not wired without a reason, blocked / history without a
clear-by date or with one that has passed) and one per allowed code (by design, history and blocked WITH a clear-by
date, accepted on the allowlist); a parked project listed, not judged; exit codes; header text, not position. Then an
integration run on the 16-project fixture build (the build test_round_p2e_ranks makes) that prints the real table.
"""
from __future__ import annotations

import hashlib
import re
import sys
from datetime import date
from pathlib import Path

import openpyxl
import pytest
from openpyxl.comments import Comment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
import done_check as dc  # noqa: E402
import reader  # noqa: E402

ASOF = date(2026, 10, 13)
KEY3 = reader.ARCHETYPE_KEYS[3][0]
NUM = 0.5


# ============================================================================================ hand-made workbooks
def _put(ws, r, c, v):
    """v: a number, or (code, reason) — the code in the cell, the reason in its comment."""
    if isinstance(v, tuple):
        code, why = v
        cell = ws.cell(r, c, code)
        cell.comment = Comment(why, "t")
    else:
        ws.cell(r, c, v)


def make_books(d: Path, rows: dict, coverage=(), headlines=(), audit_comments=None, lead_cols=0):
    """A reader + an audit workbook. rows: {project: {header: value}} on Summary (Market cap, Archetype key, Rank in
    archetype, Net holder yield) and Comparison (Holder value yield); every project is A3. lead_cols inserts extra
    columns before the value columns, so a header moves (cells are found by header text)."""
    rwb = openpyxl.Workbook()
    about = rwb.active
    about.title = "About"
    about["A1"] = f"Token metrics. Data as of {ASOF.isoformat()}."
    s = rwb.create_sheet(reader.SUMMARY)
    sh = ["Project", "Token", "Archetypes"] + [f"Extra {i}" for i in range(lead_cols)] + [
        "Market cap", "Archetype key", "Rank in archetype", "Net holder yield"]
    for j, h in enumerate(sh, 1):
        s.cell(4, j, h)
    s.cell(5, 1, "A3 Revenue Buyback — ranked by retired ÷ emissions")
    cmp_ = rwb.create_sheet(reader.COMPARISON)
    ch = ["Project", "Token", "Primary archetype"] + [f"Extra {i}" for i in range(lead_cols)] + ["Holder value yield"]
    for j, h in enumerate(ch, 1):
        cmp_.cell(4, j, h)
    a3 = rwb.create_sheet(reader.A3_TAB)
    for j, h in enumerate(["Project", "Token", KEY3, "Rank in A3"], 1):
        a3.cell(4, j, h)
    rank = 0
    for i, (name, vals) in enumerate(rows.items()):
        r = 6 + i
        s.cell(r, 1, name)
        s.cell(r, 2, name[:3].upper())
        s.cell(r, 3, "A3 Revenue Buyback")
        for j, h in enumerate(sh, 1):
            if j > 3 and not h.startswith("Extra"):
                _put(s, r, j, vals.get(h, NUM))
            elif h.startswith("Extra"):
                s.cell(r, j, "a descriptor")
        cmp_.cell(5 + i, 1, name)
        cmp_.cell(5 + i, 2, name[:3].upper())
        cmp_.cell(5 + i, 3, "A3 Revenue Buyback")
        for j, h in enumerate(ch, 1):
            if j > 3 and not h.startswith("Extra"):
                _put(cmp_, 5 + i, j, vals.get(h, NUM))
            elif h.startswith("Extra"):
                cmp_.cell(5 + i, j, "a descriptor")
        a3.cell(5 + i, 1, name)
        a3.cell(5 + i, 2, name[:3].upper())
        _put(a3, 5 + i, 3, vals.get("Archetype key", NUM))
        rk = vals.get("Rank in archetype", None)
        if rk is None:
            rank += 1
            rk = rank
        _put(a3, 5 + i, 4, rk)

    awb = openpyxl.Workbook()
    rl = awb.active
    rl.title = "Run Log"
    rl["A1"] = f"Run log — data as of {ASOF.isoformat()} (UTC)"
    cp = awb.create_sheet("CP Comparability")
    for j, h in enumerate(["Project", "Symbol", "NET HOLDER YIELD = holder value yield − dilution", "Other"], 1):
        cp.cell(4, j, h)
    for i, name in enumerate(rows):
        cp.cell(5 + i, 1, name)
        cp.cell(5 + i, 2, name[:3].upper())
        cp.cell(5 + i, 3, 0.01)
        cp.cell(5 + i, 4, 0.02)
        for (p, coord), text in (audit_comments or {}).items():
            if p == name:
                cp[coord.replace("{r}", str(5 + i))].comment = Comment(text, "t")
    cov = awb.create_sheet("Coverage")
    cov["A1"] = "Coverage — every column against SPEC.md"
    cov.append([])
    cov["A4"] = "Project"
    cov["B4"], cov["C4"], cov["D4"] = "BROKEN", "NOT WIRED", "MISSING"
    cov["A5"] = "ALL"
    head = ["Tab", "Column", "Key", "Project", "Value now", "Status", "Reason / route / error"]
    for j, h in enumerate(head, 1):
        cov.cell(8, j, h)
    for i, (tab, col, proj, status, why) in enumerate(coverage):
        for j, v in enumerate([tab, col, "k", proj, "x", status, why], 1):
            cov.cell(9 + i, j, v)
    cred = awb.create_sheet("Credibility")
    cred["A1"] = "Credibility — every headline"
    for j, h in enumerate(["Project", "Tab", "Cell", "What is checked"], 1):
        cred.cell(22, j, h)
    for i, (proj, tab, cell) in enumerate(headlines):
        for j, v in enumerate([proj, tab, cell, "x"], 1):
            cred.cell(23 + i, j, v)
    rp, ap = d / "reader.xlsx", d / "audit.xlsx"
    rwb.save(rp)
    awb.save(ap)
    return rp, ap


def run(capsys, rp, ap, *extra):
    code = dc.main(["--reader", str(rp), "--audit", str(ap), *extra])
    out = capsys.readouterr().out
    lines = {ln.split(" | ")[0]: ln for ln in out.splitlines() if " | " in ln}
    return code, out, lines


# ============================================================================================ dates and classes
def test_clear_by_dates():
    assert dc.dates_in("valid from 2026-12-01") == [date(2026, 12, 1)]
    assert dc.dates_in("clears about January 2027") == [date(2027, 1, 31)]
    assert dc.dates_in("by Feb. 2027") == [date(2027, 2, 28)]
    assert dc.clear_by("Q0 clears without a backfill about 2027-01-01", ASOF) == (date(2027, 1, 1), "")
    d, why = dc.clear_by("holes: 2026-09-13; held from 2026-05-01", ASOF)
    assert d is None and "2026-09-13" in why and "< as-of" in why
    assert dc.clear_by("no date at all", ASOF) == (None, "no clear-by date")


@pytest.mark.parametrize("code, reason, ok, cls", [
    ("by design", "the key does not apply: a fixed supply emits nothing", True, "by design"),
    ("n/a", "structural: a ratio over 0 by design", True, "by design"),
    ("blocked", "BLOCKED — unequal coverage; valid from 2026-12-01", True, "blocked"),
    ("blocked", "BLOCKED — unequal coverage of its two legs", False, "blocked"),
    ("blocked", "BLOCKED — holes: 2026-09-13", False, "blocked"),
    ("n/a", "needs two years of history; valid from 2027-05-01", True, "history"),
    ("n/a", "needs two years of history; held from 2026-05-01", False, "history"),
    ("n/a", "no rows stored", False, "n/a"),
    ("not wired", "no route yet", False, "not wired"),
    ("unranked", "Unranked, sorted last", False, "unranked"),
    ("by design", "n/a — no buyback programme and no declared payout to holders", False, "rule-3"),
    ("parked", "parked", False, "parked"),
])
def test_classify(code, reason, ok, cls):
    got = dc.classify(code, reason, ASOF)
    assert got[:2] == (ok, cls), got


def test_classify_accepted_wins():
    assert dc.classify("n/a", "no rows stored", ASOF, accepted=True)[:2] == (True, "accepted")


# ============================================================================================ hand-made runs
def test_all_ready_and_parked_exit_0(tmp_path, capsys):
    """Numbers, by design, history and blocked WITH a clear-by date: READY. World Mobile (parked) is listed PARKED and
    not judged, though its cells would block."""
    rows = {
        "Alpha": {},
        "Beta": {"Net holder yield": ("by design", "Beta emits nothing: by design."),
                 "Holder value yield": ("n/a", "needs history: valid from 2026-12-01.")},
        "Gamma": {"Net holder yield": ("blocked", "BLOCKED — unequal coverage; clears about 2027-01-01."),
                  "Market cap": ("n/a", "history too short — clears about January 2027")},
        "World Mobile": {"Market cap": ("n/a", "no rows stored"), "Archetype key": ("n/a", "no rows"),
                         "Rank in archetype": ("parked", "parked")},
    }
    rp, ap = make_books(tmp_path, rows)
    code, out, lines = run(capsys, rp, ap)
    assert lines["Alpha"] == "Alpha | READY |"
    assert lines["Beta"] == "Beta | READY |" and lines["Gamma"] == "Gamma | READY |"
    assert lines["World Mobile"].startswith("World Mobile | PARKED | ")
    assert out.splitlines()[-1] == "DONE CHECK: 3 READY, 0 NOT READY, 1 PARKED"
    assert code == 0


def test_each_blocker_class(tmp_path, capsys):
    stale = "STALE — last point 2026-10-01; last successful fetch never"
    rows = {
        "Ok": {},
        "Rule3": {"Net holder yield": ("n/a", "n/a — no buyback programme and no declared payout to holders")},
        "Broken": {},
        "Missing": {},
        "Stale": {},
        "StaleFresh": {},
        "Unranked": {"Archetype key": ("n/a", "No measured key: a leg has no reading."),
                     "Rank in archetype": ("unranked", "Unranked, sorted last in its group.")},
        "NoReason": {"Market cap": ("n/a", "n/a — Data: no rows stored")},
        "NotWired": {"Holder value yield": ("not wired", "NOT WIRED — route pending")},
        "BlockedNoDate": {"Net holder yield": ("blocked", "BLOCKED — outflow (a) is not formed")},
        "HistoryPassed": {"Market cap": ("n/a", "needs two years of history; held from 2026-05-01")},
    }
    rp, ap = make_books(
        tmp_path, rows,
        coverage=[("CP Comparability", "Other", "Broken", "BROKEN", "#DIV/0! in the cell"),
                  ("CP Comparability", "NET HOLDER YIELD = holder value yield − dilution", "Missing", "MISSING",
                   "MISSING [D — ROUTE NOT FILLED] — the route stored nothing"),
                  # MISSING on a cell that is NOT a headline: not a blocker
                  ("CP Comparability", "Other", "Ok", "MISSING", "MISSING [D — ROUTE NOT FILLED] — x")],
        # the audit CP rows follow the reader order: Ok is row 5, Missing 8, Stale 9, StaleFresh 10
        headlines=[("Missing", "CP Comparability", "C8"), ("Ok", "CP Comparability", "C5"),
                   ("Stale", "CP Comparability", "C9"), ("StaleFresh", "CP Comparability", "C10")],
        audit_comments={("Stale", "C{r}"): stale,
                        ("StaleFresh", "C{r}"): "STALE — last point 2026-10-11; last successful fetch 2026-10-12"})
    code, out, lines = run(capsys, rp, ap, "--verbose")
    assert code == 1
    assert lines["Ok"] == "Ok | READY |"
    assert lines["StaleFresh"] == "StaleFresh | READY |"            # 2 days old: within 3 days
    assert "Summary: Net holder yield — n/a: rule-3 suspect" in lines["Rule3"]
    assert "audit CP Comparability: Other — BROKEN: #DIV/0! in the cell" in lines["Broken"]
    assert "— MISSING: headline MISSING — MISSING [D — ROUTE NOT FILLED]" in lines["Missing"]
    assert "— STALE: last point 2026-10-01, 12 days before the as-of 2026-10-13" in lines["Stale"]
    assert "Summary: Rank in archetype — unranked: unranked, no date" in lines["Unranked"]
    assert "Summary: Market cap — n/a: no allowed reason — n/a — Data: no rows stored" in lines["NoReason"]
    assert "Comparison: Holder value yield — not wired: no allowed reason" in lines["NotWired"]
    assert "Summary: Net holder yield — blocked: no clear-by date" in lines["BlockedNoDate"]
    assert "history, no clear-by date (latest 2026-05-01 < as-of)" in lines["HistoryPassed"]
    for p in ("Rule3", "Broken", "Missing", "Stale", "Unranked", "NoReason", "NotWired", "BlockedNoDate",
              "HistoryPassed"):
        assert lines[p].startswith(f"{p} | NOT READY | "), lines[p]
    assert out.splitlines()[-1] == "DONE CHECK: 2 READY, 9 NOT READY, 0 PARKED"
    assert "    [rule-3] Summary: Net holder yield" in out                 # --verbose: every blocker, with its class


def test_accepted_on_the_allowlist(tmp_path, capsys, monkeypatch):
    """A code on the allowlist (config.regression_allowance — matched on the audit headline the reader cell reads) is
    accepted; past its `until` it accepts nothing."""
    monkeypatch.setattr(config, "REGRESSION_ALLOWLIST", [
        {"project": "Acc", "match": ("CP | NET HOLDER YIELD",), "until": "2027-01-31", "accepted": "2026-10-28",
         "reason": "test allowance"},
        {"project": "Expired", "match": ("CP | NET HOLDER YIELD",), "until": "2026-10-01", "accepted": "2026-09-01",
         "reason": "test allowance, expired"}])
    why = "n/a — a leg has no reading\n\nWorking: audit file, CP Comparability!C{r}."
    rows = {"Acc": {"Net holder yield": ("n/a", why.format(r=5))},
            "Expired": {"Net holder yield": ("n/a", why.format(r=6))}}
    rp, ap = make_books(tmp_path, rows)
    code, out, lines = run(capsys, rp, ap)
    assert lines["Acc"] == "Acc | READY |"
    assert lines["Expired"].startswith("Expired | NOT READY | Summary: Net holder yield — n/a: no allowed reason")
    assert code == 1


def test_at_most_five_then_and_more(tmp_path, capsys):
    bad = ("n/a", "no rows stored")
    rows = {"Many": {h: bad for h in ("Market cap", "Archetype key", "Net holder yield", "Holder value yield")} | {
        "Rank in archetype": ("unranked", "Unranked.")}}
    rows["Many"]["Market cap"] = ("not wired", "route pending")
    rp, ap = make_books(tmp_path, rows,
                        coverage=[("CP Comparability", "Other", "Many", "BROKEN", "#REF!")])
    code, out, lines = run(capsys, rp, ap)
    line = lines["Many"]
    assert line.count("; ") == 5 and line.endswith("; and 1 more"), line
    assert line.split(" | ")[2].startswith("audit CP Comparability: Other — BROKEN")     # the worst first
    code, out, _ = run(capsys, rp, ap, "--verbose")
    assert sum(1 for ln in out.splitlines() if ln.startswith("    [")) == 6


def test_cells_found_by_header_not_position(tmp_path, capsys):
    """Extra columns before the value columns (P3-6 moves columns): the same verdicts."""
    rows = {"Moved": {"Net holder yield": ("n/a", "no rows stored")}, "Fine": {}}
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _, _, la = run(capsys, *make_books(a, rows))
    _, _, lb = run(capsys, *make_books(b, rows, lead_cols=3))
    assert la == lb and "Summary: Net holder yield — n/a" in la["Moved"] and la["Fine"] == "Fine | READY |"


def test_primary_tab_key_and_rank_when_summary_lacks_them(tmp_path, capsys):
    """When Summary holds no key / rank column, the primary tab's key and 'Rank in A3' are judged, by header."""
    rows = {"Lone": {"Archetype key": ("n/a", "No measured key."), "Rank in archetype": ("unranked", "Unranked.")}}
    rp, ap = make_books(tmp_path, rows)
    wb = openpyxl.load_workbook(rp)
    s = wb[reader.SUMMARY]
    for c in sorted((c for c in range(1, s.max_column + 1)
                     if s.cell(4, c).value in ("Archetype key", "Rank in archetype")), reverse=True):
        s.delete_cols(c)
    wb.save(rp)
    code, out, lines = run(capsys, rp, ap)
    assert f"{reader.A3_TAB}: {KEY3} — n/a: no allowed reason" in lines["Lone"]
    assert f"{reader.A3_TAB}: Rank in A3 — unranked: unranked, no date" in lines["Lone"]


def test_asof_override_and_bad_asof(tmp_path, capsys):
    rows = {"Gamma": {"Net holder yield": ("blocked", "BLOCKED — clears about 2026-11-01.")}}
    rp, ap = make_books(tmp_path, rows)
    code, out, lines = run(capsys, rp, ap)
    assert code == 0 and lines["Gamma"] == "Gamma | READY |"
    code, out, lines = run(capsys, rp, ap, "--asof", "2026-12-01")            # the date has passed by then
    assert code == 1 and "no clear-by date (latest 2026-11-01 < as-of)" in lines["Gamma"]
    with pytest.raises(SystemExit):
        dc.main(["--reader", str(rp), "--audit", str(ap), "--asof", "13/10/2026"])


def test_read_only(tmp_path, capsys):
    rp, ap = make_books(tmp_path, {"Alpha": {}})
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (rp, ap)}
    listing = sorted(x.name for x in tmp_path.iterdir())
    run(capsys, rp, ap, "--verbose")
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (rp, ap)} == before
    assert sorted(x.name for x in tmp_path.iterdir()) == listing


def test_unreadable_workbook_exit_1(tmp_path, capsys):
    assert dc.main(["--reader", str(tmp_path / "none.xlsx"), "--audit", str(tmp_path / "none2.xlsx")]) == 1
    assert "cannot open" in capsys.readouterr().out


# ============================================================================================ the fixture build
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The two workbooks built on the committed real dumps (the build test_round_p2e_ranks makes)."""
    import build_workbook as bw
    import measuring_guard as mg
    import pandas as pd
    import store as sm
    from real_dump import to_store
    d = tmp_path_factory.mktemp("p3_5")
    db = d / "metrics.db"
    asofs = []
    for pl in mg._payloads():
        to_store(pl, db)
        asofs.append(pl["meta"]["asof"])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sm, "DB_PATH", str(db))
        mp.setitem(sys.modules, "recalc", None)
        mp.setenv("TOKEN_METRICS_STORE_VALUES", "1")
        st = sm.Store(str(db))
        try:
            bw.build_workbook(st, d / "token_metrics_audit.xlsx", asof=pd.Timestamp(max(asofs)),
                              reader_path=d / "token_metrics.xlsx")
        finally:
            st.close()
    assert "error" not in bw.READER_STATS, bw.READER_STATS
    return {"reader": d / "token_metrics.xlsx", "audit": d / "token_metrics_audit.xlsx",
            "asof": pd.Timestamp(max(asofs)).date()}


def test_integration_fixture_build(built, capsys):
    """The real table on the fixture build (printed: run with -s to see it). Most projects are NOT READY on the
    fixture — that is the finding (the dumps cover 16 of 30 projects, as of 2026-10-13); nothing is weakened here.
    Aerodrome and World Mobile are PARKED, not judged."""
    code = dc.main(["--reader", str(built["reader"]), "--audit", str(built["audit"])])
    out = capsys.readouterr().out
    with capsys.disabled():
        print("\n" + out)
    lines = out.splitlines()
    assert lines[0].startswith(f"done check as of {built['asof']}")
    rows = [ln for ln in lines[1:-1]]
    assert all(re.match(r"^[^|]+ \| (READY|NOT READY|PARKED) \|", ln) for ln in rows), rows
    status = {ln.split(" | ")[0]: ln.split(" | ")[1] for ln in rows}
    assert status["Aerodrome"] == "PARKED" and status["World Mobile"] == "PARKED"
    m = re.match(r"^DONE CHECK: (\d+) READY, (\d+) NOT READY, (\d+) PARKED$", lines[-1])
    assert m and sum(map(int, m.groups())) == len(rows)
    assert int(m.group(2)) == sum(1 for v in status.values() if v == "NOT READY")
    assert code == (1 if int(m.group(2)) else 0)
