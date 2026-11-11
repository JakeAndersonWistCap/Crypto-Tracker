"""Project round 3B — finish Maple, fix Fluid's freeze (Jake 2026-11-11). No network."""
import json

import pandas as pd
import pytest

import build_workbook as bw
import config
import done_check
import project_freeze as pf


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from shared_build import plain_build
    rec = plain_build(tmp_path_factory, "pr3b")
    assert "error" not in rec["stats"], rec["stats"]
    return rec


def test_1_the_frozen_build_wires_fluids_l2_scans_as_jakes_machine_does(monkeypatch):
    """Jake's project_freeze read 18 undeclared moves, this sandbox 0: config.wire_probed_scans reads the machine-local
    explorer_routes.json at import. The frozen build now pins Fluid's Base / Plasma scans (idempotent; no fetch route
    touched; no other project's scan)."""
    monkeypatch.setitem(config.OUTFLOW_CATEGORIES["Fluid"], "b", ("emissions_tokens", "emissions_tokens_arbitrum"))
    monkeypatch.setitem(config.PROJECT_BY_NAME["Fluid"], "log_scans",
                        [s for s in config.PROJECT_BY_NAME["Fluid"].get("log_scans") or ()
                         if not s["key"].startswith("merkle_rewards_out_")])
    routes = {k: list(v) for k, v in config.EXPLORER_LOG_ROUTES.items()}
    pendle = len(config.PROJECT_BY_NAME["Pendle"].get("log_scans") or ())
    assert sorted(config.pin_frozen_probe()) == ["merkle_rewards_out_base", "merkle_rewards_out_plasma"]
    assert config.OUTFLOW_CATEGORIES["Fluid"]["b"][-2:] == ("emissions_tokens_base", "emissions_tokens_plasma")
    assert config.pin_frozen_probe() == []                                     # idempotent
    assert {k: list(v) for k, v in config.EXPLORER_LOG_ROUTES.items()} == routes
    assert len(config.PROJECT_BY_NAME["Pendle"].get("log_scans") or ()) == pendle
    assert "pin_frozen_probe()" in open(pf.__file__, encoding="utf-8").read()


def test_1_fluids_baseline_holds_the_blocked_b_and_declares_the_18(built):
    base = json.loads(pf.baseline_path("Fluid").read_text(encoding="utf-8"))
    now = pf.cells(built["audit"], built["reader"], "Fluid")
    assert len(now) == 353 and pf.undeclared(base, now, config.FROZEN_MOVES["Fluid"]) == []
    moves = [k for k, d in config.FROZEN_MOVES["Fluid"].items() if d["why"].startswith("PR3B.1")]
    assert len(moves) == 18
    assert str(now["reader|Summary|Net holder yield"]) == "blocked"


def test_2_maples_uniformity_and_ended_b_carry_declared_reasons():
    u = config.declared_na_text("Maple", "UNIFORMITY = whole-token yield ÷ staker yield")
    assert u.startswith("n/a — no staker yield exists: stSYRUP staking rewards were sunset with MIP-019")
    assert config.declared_na_reason("Maple", u)["source"].startswith("https://docs.maple.finance")
    t = config.outflow_ended_text("Maple", "b")
    assert t.startswith("n/a — outflow (b) is a measured 0: Drips ended") and "2026-02-18" in t and len(t) <= 240
    e = config.declared_na_reason("Maple", t)
    assert e and e["read_on"] == "2026-11-11" and "block 24,483,486" in e["source"]
    hits = [{"project": "Maple", "tab": "reader:Comparison", "header": "Subsidy cover (x)", "code": "n/a", "reason": t},
            {"project": "Maple", "tab": "audit:CP Comparability", "header": "UNIFORMITY", "code": "n/a", "reason": u}]
    assert done_check.judge_suspects(hits, "Maple", []) == []
    src = open(bw.__file__, encoding="utf-8").read()
    assert "config.outflow_ended_text(p[\"name\"], c) for c in got" in src
    assert config.outflow_ended_text("Fluid", "b") == ""                      # declared for Maple only


def test_2_a_monthly_revenue_reads_its_latest_complete_month(built):
    """Maple's revenue is monthly from its page: the period growth is the latest complete published month against the
    month before (not N daily windows that a monthly series can never fill)."""
    import openpyxl
    import xlcalc
    wb = openpyxl.load_workbook(built["reader"])
    X, ws = xlcalc.Workbook(wb), wb["Summary"]
    hr = next(r for r in range(1, 15) if ws.cell(r, 1).value == "Project")
    row = next(r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == "Maple")
    col = next(c for c in range(1, ws.max_column + 1) if str(ws.cell(hr, c).value or "").startswith("Revenue growth"))
    v = X.value("Summary", row, col)
    note = ws.cell(row, col).comment.text
    assert isinstance(v, float) and "the latest complete published month against the month before" in note
    assert "2026-09 vs 2026-08" in note
    assert config.series_granularity("Aethir", "customer_revenue_usd") == "weekly"     # untouched: not monthly


def test_3_maples_holder_revenue_and_its_buyback_are_one_flow_on_one_window(built):
    g = pd.DataFrame({"date": pd.to_datetime(["2026-07-31", "2026-08-31"]), "value": [1.0, 2.0],
                      "source": "derived:holder_value[revenue-funded]"})
    assert bw._effective_granularity("Maple", "holder_value_revenue_usd", g) == "monthly"
    for n in ("Sky", "Hyperliquid", "Aerodrome", "Ethereum", "Fluid"):
        assert bw._effective_granularity(n, "holder_value_revenue_usd", g) == "daily", n
    import openpyxl
    ws = openpyxl.load_workbook(built["audit"], read_only=True)["Data"]
    rows = {r[0]: r for r in ws.iter_rows(values_only=True) if r and r[0]}
    q0 = 11                                                                     # the Q0 column
    assert rows["Maple|holder_value_revenue_usd"][q0] == rows["Maple|actual_buyback_usd"][q0] == 658866
