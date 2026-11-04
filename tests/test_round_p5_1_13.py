"""P5-1.13 (Jake 2026-11-04): payout ratio above 1 — Ethereum 1.002, Hyperliquid 1.068, Uniswap 1.105 on the live store.

Two counting errors, fixed:
  * Etherscan's ethsupply2 figures are the previous UTC day's close; its deltas were dated a day late, so the first
    measured burn (09-30) repeated 09-29, which the DefiLlama-derived leg held: one day counted twice (Ethereum 1.057 on
    the 10-13 dump, 1.004 after);
  * a delta rebuilt by rederive.py across missed reads carried no [span=Nd]: Hyperliquid's 09-21 Assistance Fund row
    holds three days and counted as one (covered days 87 instead of 90).
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


def test_an_ethsupply2_delta_is_dated_on_the_day_it_measures_and_the_handover_day_is_counted_once():
    derived = "derived:defillama_burned_fee_revenue/price"
    es = "etherscan:ethsupply2.BurntFees:delta"
    groups = {("Ethereum", "gross_burn_tokens"): _f("Ethereum", "gross_burn_tokens", [
        ("2026-09-28", 240.7, derived), ("2026-09-29", 229.7, derived),
        ("2026-09-30", 230.8, es), ("2026-10-01", 68.4, es), ("2026-10-05", 233.1, es + "[span=4d]")]),
        ("Ethereum", "burn_cumulative_tokens"): _f("Ethereum", "burn_cumulative_tokens", [
            ("2026-09-30", 100.0, "etherscan:ethsupply2.BurntFees")])}
    bw._dated_day_late_views(groups)
    g = groups[("Ethereum", "gross_burn_tokens")].sort_values("date")
    got = [(d.strftime("%m-%d"), v) for d, v in zip(g["date"], g["value"])]
    assert got == [("09-28", 240.7), ("09-29", 230.8), ("09-30", 68.4), ("10-04", 233.1)], got
    assert "[span=4d]" in g["source"].iloc[-1] and "dated −1d" in g["source"].iloc[-1]
    from fetch.validate import _span_days
    assert _span_days(g["source"].iloc[-1]) == 4, "the span survives the re-dating"
    from fetch.base import _measuring_point
    assert _measuring_point(g["source"].iloc[1]) == _measuring_point(es), "same measuring point"
    # the stock is left as read
    assert groups[("Ethereum", "burn_cumulative_tokens")]["date"].iloc[0] == pd.Timestamp("2026-09-30")
    assert "1 row(s) of the earlier leg" in bw.DATED_DAY_LATE_LOG[("Ethereum", "gross_burn_tokens")]


def test_the_lag_is_on_the_record_for_both_ethsupply2_flows():
    assert set(config.DATED_DAY_LATE) == {"etherscan:ethsupply2.BurntFees", "etherscan:ethsupply2.Eth2Staking"}
    assert "corr 1.000" in config.DATED_DAY_LATE["etherscan:ethsupply2.BurntFees"]


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
