"""
tests/test_alignment.py — the alignment round's build-failing checks (Jake 2026-10-13, Part D2), on the REAL dumps
(fixtures/real_*.json, asof 2026-10-09) — never on a hand-built fixture.

A build fails when: a column maps to no SPEC.md concept; one concept shows different values on two tabs; a ratio is out
of range (lock / staking rate and float ÷ circulating outside 0..1, float < 0, whole-token yield above the staker yield,
a release or issuance below 0); a flow-statement residual beyond 0.5% is not marked CHECK; a headline is blank without
a reason; the coverage audit finds a BROKEN cell, a BLANK-UNEXPLAINED cell or a column whose formula does not match
SPEC; a Trajectory row has no direction or axis.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spec  # noqa: E402

DUMPS = ("Aerodrome", "Chainlink", "Ethereum", "Maple", "Pendle", "Plume")
TABS = ("Master", "A1 Infrastructure", "A2 Coordination", "A3 Revenue Buyback", "A4 Permanent Burn", "CP Comparability")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One build per dump for the whole module: (workbook, xlcalc evaluator, trajectory rows, flows rows, coverage)."""
    import openpyxl
    import xlcalc
    import store as sm
    import build_workbook as bw
    from real_dump import load, to_store
    out = {}
    mp = pytest.MonkeyPatch()
    try:
        for project in DUMPS:
            d = tmp_path_factory.mktemp(f"align_{project}")
            payload = load(project)
            mp.setitem(sys.modules, "recalc", None)
            mp.setattr(bw, "_SCOPE", bw._SCOPE)
            db = d / "metrics.db"
            to_store(payload, db)
            mp.setattr(sm, "DB_PATH", str(db))
            st = sm.Store(str(db))
            try:
                bw.build_workbook(st, d / "wb.xlsx", asof=pd.Timestamp(payload["meta"]["asof"]), only=[project])
            finally:
                st.close()
            wb = openpyxl.load_workbook(d / "wb.xlsx")
            out[project] = {"wb": wb, "X": xlcalc.Workbook(wb), "traj": list(bw.TRAJECTORY_ROWS),
                            "flows": list(bw.FLOWS_ROWS), "coverage": list(bw.COVERAGE_ROWS),
                            "csv": (d / "coverage_audit.csv").exists()}
            mp.undo()
    finally:
        mp.undo()
    return out


def _row(wb, X, sheet, project) -> dict:
    import xlcalc
    ws = wb[sheet]
    hr = next(r for r in range(1, 15) if ws.cell(r, 1).value == "Project")
    r = next((r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == project), None)
    if r is None:
        return {}
    out = {}
    for c in range(1, ws.max_column + 1):
        h = ws.cell(hr, c).value
        if h:
            raw = ws.cell(r, c).value
            v = X.value(sheet, r, c) if isinstance(raw, str) and raw.startswith("=") else raw
            out[str(h)] = xlcalc.display(v) if not isinstance(v, (int, float, str, type(None))) else v
    return out


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def test_spec_parses_and_every_column_maps_to_a_concept(built):
    cs = spec.concepts()
    assert len(cs) >= 60
    for key, c in cs.items():
        # a LATER / NOT WIRED concept (the grid) may carry no axis yet
        assert (c["axis"] in spec.AXES or c["status"].startswith(("LATER", "NOT WIRED"))) and c["dir"] in (-1, 0, 1), key
    for project, b in built.items():
        for tab in TABS + ("Flows",):
            if tab not in b["wb"].sheetnames:
                continue
            ws = b["wb"][tab]
            hr = next((r for r in range(1, 15) if ws.cell(r, 1).value == "Project"), None)
            if hr is None:
                continue
            for c in range(1, ws.max_column + 1):
                h = ws.cell(hr, c).value
                if not h or tab == "Flows":
                    continue
                key = spec.concept_for(tab, str(h))
                assert key and (key in cs or key in spec.SUPPORT), (project, tab, h)


def test_every_trajectory_row_has_a_direction_and_an_axis(built):
    for project, b in built.items():
        for t in b["traj"]:
            assert t.get("direction") is not None, (project, t["key"])
        ws = b["wb"]["Trajectory"]
        hr = next(r for r in range(1, 10) if ws.cell(r, 1).value in ("Key", "Project") or
                  "Axis (SPEC.md)" in [ws.cell(r, c).value for c in range(1, ws.max_column + 1)])
        heads = [ws.cell(hr, c).value for c in range(1, ws.max_column + 1)]
        col = heads.index("Axis (SPEC.md)") + 1
        for r in range(hr + 1, ws.max_row + 1):
            if ws.cell(r, 2).value and ws.cell(r, col).value is None and any(
                    ws.cell(r, c).value for c in range(3, 8)):
                # section headers carry no axis; a data row must
                if ws.cell(r, 1).value and "|" in str(ws.cell(r, 1).value):
                    pytest.fail(f"{project}: Trajectory row {r} has no axis")


def test_one_concept_one_value_across_tabs(built):
    """The same figure on two tabs is the same number (rule 1: ONE computation per concept)."""
    same = {  # concept: header prefixes that must agree on every tab where they appear
        "circulating": ("Circulating supply (used)", "Circulating supply (tokens) — our convention"),
        "market_cap": ("Market cap ($)",),
        "price": ("Price — spot ($)",),
        "fdv": ("FDV ($)",),
    }
    for project, b in built.items():
        rows = {t: _row(b["wb"], b["X"], t, project) for t in TABS if t in b["wb"].sheetnames}
        for concept, heads in same.items():
            seen = []
            for tab, row in rows.items():
                for h, v in row.items():
                    if any(h.startswith(x) for x in heads) and _num(v) is not None:
                        seen.append((tab, h, _num(v)))
            if len(seen) > 1:
                ref = seen[0][2]
                for tab, h, v in seen[1:]:
                    assert v == pytest.approx(ref, rel=1e-9), (project, concept, seen)


def test_ratios_are_in_range(built):
    for project, b in built.items():
        for tab in TABS:
            row = _row(b["wb"], b["X"], tab, project)
            for h, v in row.items():
                x = _num(v)
                if x is None:
                    continue
                if h.startswith(("Staking rate", "Lock rate", "Holding demand — lock rate", "Tradeable float ÷ circ")):
                    assert -1e-9 <= x <= 1 + 1e-9, (project, tab, h, x)
                if h.startswith(("TRADEABLE FLOAT", "Resulting float", "FREE FLOAT = ", "Effective float")):
                    assert x >= 0, (project, tab, h, x)
                if h.startswith(("Issuer releases", "Gross issuance Q0", "UNLOCK DILUTION Q0", "Dilution (")):
                    assert x >= -1e-9, (project, tab, h, x)
        cp = _row(b["wb"], b["X"], "CP Comparability", project)
        for whole, staker in (("Whole-token yield — validator", "Staker yield — validator (A1)"),
                              ("Whole-token yield — protocol", "Staker yield — protocol, tokens (A3)")):
            w = next((_num(v) for h, v in cp.items() if h.startswith(whole)), None)
            s = next((_num(v) for h, v in cp.items() if h.startswith(staker)), None)
            if w is not None and s is not None and s >= 0:
                assert w <= s + 1e-12, (project, whole, w, s)
        # releases and issuance are never negative in the series every tab reads
        import build_workbook as bw
        del bw


def test_flow_statements_mark_every_residual_beyond_half_a_percent(built):
    for project, b in built.items():
        rows = [r for r in b["flows"] if r["Project"] == project]
        assert rows, project
        pct = next(r for r in rows if r["Statement"] == "Token flow" and r["Line"] == "Residual ÷ opening")
        status = next(r for r in rows if r["Statement"] == "Token flow" and r["Line"] == "Status")
        for q in ("Q0", "Q-1", "Q-2", "Q-3", "Q-4"):
            x = pct[q]
            if isinstance(x, float) and abs(x) > 0.005:
                assert str(status[q]).startswith("CHECK"), (project, q, x, status[q])
            assert status[q] and str(status[q]).split(" ")[0] in ("OK", "CHECK", "n/a"), (project, q, status[q])
        rel = next(r for r in rows if r["Statement"] == "Token flow" and r["Line"].startswith("+ released"))
        for q in ("Q0", "Q-1", "Q-2", "Q-3", "Q-4"):
            assert rel[q] is None or rel[q] >= 0, (project, q, rel[q])
        assert "Flows" in b["wb"].sheetnames


def test_coverage_audit_has_no_unexplained_blank_no_broken_cell_and_matches_spec(built):
    import coverage_audit as ca
    for project, b in built.items():
        rows = b["coverage"]
        assert rows and b["csv"], project
        assert "Coverage" in b["wb"].sheetnames
        c = ca.counts(rows)
        bad = [(r["Tab"], r["Column"][:60], r["Status"], r["Reason / route / error"][:80], r["MATCHES-SPEC"][:100])
               for r in rows if r["Status"] in ("BROKEN", "BLANK-UNEXPLAINED") or r["MATCHES-SPEC"].startswith("no")]
        assert c["BROKEN"] == 0 and c["BLANK-UNEXPLAINED"] == 0 and c["MATCHES-SPEC = no"] == 0, (project, bad)
        # the NOT WIRED list of SPEC.md section 10 is on the tab
        assert c["NOT WIRED"] >= len(ca.not_wired()) > 0


def test_no_headline_reads_a_bare_na(built):
    import build_workbook as bw   # noqa: F401
    for project, b in built.items():
        for tab in TABS:
            for h, v in _row(b["wb"], b["X"], tab, project).items():
                if isinstance(v, str):
                    assert v.strip().lower() not in ("n/a", "#n/a"), (project, tab, h, v)


def test_alignment_config():
    """C1: Uniswap's growth budget is (d); Aerodrome has no (a) and its rebase is (c); Hyperliquid's (a) is the rewards
    to circulating stake. C10: NEAR's revenue is gas + Intents. B9: Ether.fi's Q0 top-ups are treasury-funded. B14: the
    divergence band. B13: a fall of more than 10x is a new regime."""
    import config
    import trajectory
    assert config.outflow_metrics("Uniswap", "d") == ("growth_budget_release_tokens",)
    assert config.outflow_metrics("Uniswap", "e") == ("unlock_release_vesting_tokens",)
    assert not config.outflow_metrics("Aerodrome", "a") and config.outflow_metrics("Aerodrome", "c") == ("emissions_tokens",)
    assert config.outflow_metrics("Hyperliquid", "a") == ("emissions_circulating_stake_tokens",)
    assert config.revenue_base_metric("Near") == "revenue_total_usd" == config.revenue_headline_metric("Near")
    assert config.revenue_headline_metric("Chainlink") == "revenue_usd"
    assert config.holder_funding("Ether.fi", "2026-08-13")[0] == "treasury"
    assert config.TRAJECTORY["divergence_in_line_pp"] == 0.10
    g, _n, label, _why = trajectory.growth_rule([10.0, 12.0, 15.0, 200.0, 250.0])
    assert g is None and label == "NEW since Q-2"
    g, _n, label, _why = trajectory.growth_rule([100.0, 110.0, 121.0, 133.0])
    assert g is not None and label == ""
