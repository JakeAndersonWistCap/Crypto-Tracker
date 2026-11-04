"""P5-1.13 (Jake 2026-11-04): payout ratio above 1 — Ethereum 1.002, Hyperliquid 1.068, Uniswap 1.105 on the live store.

One counting error, fixed: a delta rebuilt by rederive.py across missed reads carried no [span=Nd] — Hyperliquid's 09-21
Assistance Fund row holds three days and counted as one (covered days 87 instead of 90). Ethereum's day-late Etherscan
BurntFees deltas were ALREADY re-dated at the source by E5 / orphan_cleanup CQ (2026-10-14): the build must not move
them again (a second shift was tried in this round and withdrawn after the gate — test_e5 caught it).
What is real gets a note on both payout cells (config.PAYOUT_ABOVE_ONE).
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402

COLS = ["date", "project", "metric", "value", "source", "tier"]


def _f(project, metric, rows):
    return pd.DataFrame([{"date": pd.Timestamp(d), "project": project, "metric": metric, "value": float(v),
                          "source": s, "tier": 1} for d, v, s in rows], columns=COLS)


def test_the_build_never_re_dates_ethsupply2_rows_the_fetch_and_cq_already_date():
    """E5 / CQ date BurntFees to the day it measures at the source and in the store; a build-time shift would move every
    burn row a second day (the P5 round 1 gate: burn_30d BROKEN on a CQ store). The rows come out as stored."""
    import inspect
    assert not hasattr(bw, "_dated_day_late_views") and not hasattr(config, "DATED_DAY_LATE")
    assert "_dated_day_late" not in inspect.getsource(bw.aggregate)
    import fetch.etherscan_supply as es
    assert "burn_day = (pd.Timestamp(when) - pd.Timedelta(days=1)).normalize()" in inspect.getsource(es)


def test_a_rederived_delta_across_missed_reads_covers_every_day_since_the_previous_reading(monkeypatch):
    stock = _f("Hyperliquid", "burn_address_balance", [("2026-09-17", 1.0, "hypercore_info:spotClearinghouseState"),
                                                      ("2026-09-18", 2.0, "hypercore_info:spotClearinghouseState"),
                                                      ("2026-09-21", 5.0, "hypercore_info:spotClearinghouseState"),
                                                      ("2026-09-22", 6.0, "hypercore_info:spotClearinghouseState")])
    rd = "hypercore_info:spotClearinghouseState:delta:rederived"
    flow = _f("Hyperliquid", "gross_burn_tokens", [("2026-09-18", 1.0, rd), ("2026-09-21", 3.0, rd),
                                                   ("2026-09-22", 1.0, "hypercore_info:spotClearinghouseState:delta")])
    monkeypatch.setattr(bw.wiring, "view_edges", lambda: {})
    cov = bw._covered_day_sets({("Hyperliquid", "burn_address_balance"): stock,
                                ("Hyperliquid", "gross_burn_tokens"): flow})
    days = sorted(d.strftime("%m-%d") for d in cov[("Hyperliquid", "gross_burn_tokens")])
    assert days == ["09-18", "09-19", "09-20", "09-21", "09-22"], days
    monkeypatch.setitem(config.REDERIVED_SPAN_FROM_STOCK, "on", False)       # headline_diff's BEFORE: one day a row
    cov = bw._covered_day_sets({("Hyperliquid", "burn_address_balance"): stock,
                                ("Hyperliquid", "gross_burn_tokens"): flow})
    assert sorted(d.strftime("%m-%d") for d in cov[("Hyperliquid", "gross_burn_tokens")]) == ["09-18", "09-21", "09-22"]


def test_rederive_tags_the_span_of_a_pair_read_days_apart(tmp_path):
    import rederive
    db = tmp_path / "m.db"
    import store as sm
    sm.Store(str(db)).close()
    con = sqlite3.connect(db)
    con.executemany("INSERT INTO metrics (date, project, metric, value, source, tier, fetched_at) VALUES (?,?,?,?,?,?,?)",
                    [("2026-09-18", "Hyperliquid", "burn_address_balance", 2.0, "hypercore_info:spotClearinghouseState",
                      1, "x"),
                     ("2026-09-21", "Hyperliquid", "burn_address_balance", 5.0, "hypercore_info:spotClearinghouseState",
                      1, "x"),
                     ("2026-09-22", "Hyperliquid", "burn_address_balance", 6.0, "hypercore_info:spotClearinghouseState",
                      1, "x")])
    con.commit()
    plan = rederive.rederive_flow_from_stock(con, "Hyperliquid", "gross_burn_tokens", "burn_address_balance")
    con.close()
    by = {p.date: p.source for p in plan.pairs}
    assert by["2026-09-21"].endswith("[span=3d]") and "[span=" not in by["2026-09-22"]
    from fetch.base import _measuring_point
    assert _measuring_point(by["2026-09-21"]) == _measuring_point(by["2026-09-22"])


@pytest.mark.parametrize("name, words", [("Hyperliquid", "builders fees"), ("Uniswap", "timing"),
                                         ("Ethereum", "valuation")])
def test_both_payout_cells_say_why_a_real_excess_is_real(name, words):
    note = bw._payout_note(config.PROJECT_BY_NAME[name])
    assert "PAYOUT ABOVE 1 (P5-1.13)" in note and words in note
    assert "PAYOUT ABOVE 1" not in bw._payout_note(config.PROJECT_BY_NAME["Chainlink"])


# ---------- P5-1.14: NEAR's (e) structural zero ----------

def _near_groups(values):
    days = pd.date_range("2026-10-08", periods=len(values))
    src = "nearblocks:stats/balance[lockup.near+contributors.near]"
    return {("Near", "near_foundation_accounts_tokens"): _f("Near", "near_foundation_accounts_tokens",
                                                           [(d, v, src) for d, v in zip(days, values)])}


def test_a_fee_sized_fall_of_the_near_foundation_accounts_is_not_a_release(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Near"]])
    monkeypatch.setattr(config, "unlock_stock_metrics", lambda n: ("near_foundation_accounts_tokens",))
    monkeypatch.setattr(config, "UNLOCK_RELEASE_SUBSETS", {})
    g = _near_groups([171059.13121036976, 171059.131, 171059.13123678928, 171059.131, 170000.0])
    bw._unlock_views(g)
    rel = bw._daily(g[("Near", "unlock_release_tokens")])
    assert list(rel.values) == [0.0, 0.0, 0.0, pytest.approx(1059.131)], rel
    assert (rel.iloc[:3] == 0.0).all(), "a structural zero is stored as 0, not 1e-4"
    assert "2 fall(s) under the fee floor" in g[("Near", "unlock_release_tokens")]["source"].iloc[0]


def test_only_a_project_that_declares_a_floor_has_one():
    assert set(config.UNLOCK_FEE_FLOOR_TOKENS) == {"Near"}
    assert bw._measurable_fall("Hyperliquid", 0.0002, 1.0, 0.9998) == 0.0002


def test_the_runbook_gives_the_daily_task_command_check_and_removal_and_the_read_only_checks():
    text = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
    sec = text[text.index("## 11dx."):text.index("## 11du.")]
    for want in ("windows\\register_task.ps1 -At 17:20", "Get-ScheduledTaskInfo -TaskName \"Crypto-Tracker daily\"",
                 "Unregister-ScheduledTask -TaskName \"Crypto-Tracker daily\" -Confirm:$false", "mode=ro",
                 "nothing here sets it up"):
        assert want in sec, want
    assert sec.count("mode=ro") == 2, "both store checks are read-only"
    reg = (ROOT / "windows" / "register_task.ps1").read_text(encoding="utf-8")
    assert reg.isascii() and '[string]$At = "06:30"' in reg and '"Crypto-Tracker daily"' in reg


def test_headline_diff_can_show_the_span_fix_on_every_numeric_cell():
    """Jake 2026-11-04: every cell that moves because of the span fix, old -> new, on his store — headline_diff.py
    --only p5_1_13_spans --cells all (read-only builds)."""
    import headline_diff as hd
    assert hd.BEFORE["p5_1_13_spans"]() == [(config.REDERIVED_SPAN_FROM_STOCK, "on", False)]
    assert hd.ALL_CELL_TABS == ("A1", "A2", "A3", "A4", "CP", "Master")
    import openpyxl
    import xlcalc
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CP Capture"
    ws["A1"], ws["B1"], ws["C1"] = "Project", "Holder value yield", "Note"
    ws["A2"], ws["B2"], ws["C2"] = "Hyperliquid", 0.038, "text"
    ws2 = wb.create_sheet("Data")
    ws2["A2"], ws2["B2"] = "Hyperliquid", 1.0
    got = hd._all_cells(xlcalc.Workbook(wb), wb)
    assert got[("Hyperliquid", "CP Capture", "Holder value yield")] == 0.038
    assert not any(k[1] == "Data" for k in got), "only the A1-A4, CP and Master tabs"
