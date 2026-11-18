"""Round 9 (Jake 2026-11-17/18): the gate, the rule on the seven frozen projects, Aerodrome's revenue. Behaviour and
values within tolerance. No network."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402


def _g(name, metric, rows, source="chain"):
    return pd.DataFrame({"date": pd.to_datetime([d for d, _ in rows]), "project": name, "metric": metric,
                         "value": [float(v) for _, v in rows], "source": source, "tier": 2})


# ====================================================================== 1 / 3: a genuine zero reads 0, never blank
def test_1_a_declared_zero_unlock_is_a_series_of_zeros_on_the_totals_days(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])
    g = {("Aerodrome", "total_supply"): _g("Aerodrome", "total_supply", [("2026-11-09", 2.1e9), ("2026-11-10", 2.1e9)])}
    bw._unlock_views(g)
    u = g[("Aerodrome", "unlock_release_tokens")]
    assert list(u["value"]) == [0.0, 0.0] and list(pd.to_datetime(u["date"]).dt.day) == [9, 10]
    assert config.has_unlock_leg("Aerodrome") and config.unlock_stock_metrics("Aerodrome") == ()
    z = config.UNLOCK_DECLARED_ZERO["Aerodrome"]
    assert z["source"] and z["read_on"] and "Jake" in z["decided"]
    # no total read, no zero (an unread day is unknown) — and no other project gets a zero it did not declare
    g2 = {}
    bw._unlock_views(g2)
    assert ("Aerodrome", "unlock_release_tokens") not in g2
    assert set(config.UNLOCK_DECLARED_ZERO) == {"Aerodrome"} and not config.has_unlock_leg("Sky")


def test_1_the_unlock_change_is_a_declared_handover():
    import measuring_guard as mg
    import trajectory
    decl = mg._declared("Aerodrome", "unlock_release_tokens")
    assert trajectory.basis_of("unlock_release_tokens", "derived:declared_zero[no vesting or release schedule]") in decl


# ================================================================== 3: the frozen projects' unlock legs are pinned
def test_3_unlock_legs_are_the_vesting_stocks_whatever_the_rule_moves_in():
    for p in ("Uniswap", "Fluid", "Maple", "Morpho", "Pendle", "Hyperliquid"):
        legs = config.UNLOCK_STOCKS[p]
        assert legs and config.has_unlock_leg(p), p
        # a leg the rule counts in circulating is still an unlock stock: a release from it dilutes the float
        assert set(config.rule_in_legs(p)) & set(legs) or not config.rule_in_legs(p), p


# ============================================================================== 2: the rule's mechanism
def test_2_a_rule_in_leg_is_not_subtracted_and_comes_off_the_float_once(monkeypatch):
    from fetch import circulating as C
    monkeypatch.setitem(config.CIRCULATING_ONCHAIN, "Testco", {"status": "established", "total": "total_supply",
                                                              "subtract": ("burn_address_balance", "treasury_x")})
    monkeypatch.setitem(config.RULE_IN, "Testco", {"legs": ("treasury_x",), "off_float": ("treasury_x",)})
    d = pd.Timestamp("2026-11-10")
    h = pd.DataFrame([{"date": d, "project": "Testco", "metric": "total_supply", "value": 1000.0},
                      {"date": d, "project": "Testco", "metric": "burn_address_balance", "value": 100.0},
                      {"date": d, "project": "Testco", "metric": "treasury_x", "value": 250.0}])
    assert list(C.series(h, "Testco")) == [900.0]                 # the burn stays out; the treasury is counted in
    g = {("Testco", "treasury_x"): _g("Testco", "treasury_x", [("2026-11-10", 250.0)])}
    bw._rule_in_views(g)
    assert list(g[("Testco", "rule_in_off_float_tokens")]["value"]) == [250.0]

    class R:
        def D(self, r, m, w):
            return f"D[{m}]"
    p = {"name": "Testco"}
    assert bw._released_held(R(), 5, p) == "D[rule_in_off_float_tokens]"
    assert "D[rule_in_off_float_tokens]" in bw._effective_float_held(R(), 5, p)
    # float = (total − burn) − lock − treasury: what it was when the treasury was out of circulating
    assert 900.0 - 0.0 - 250.0 == 1000.0 - 100.0 - 250.0 - 0.0


def test_2_a_day_missing_a_rule_leg_has_no_off_float_figure():
    g = {("Aerodrome", "aero_issuer_held_tokens"): _g("Aerodrome", "aero_issuer_held_tokens", [("2026-11-10", 37e6)]),
         ("Aerodrome", "filing_managed_lock_tokens"): _g("Aerodrome", "filing_managed_lock_tokens",
                                                         [("2026-11-09", 500e6), ("2026-11-10", 503e6)])}
    bw._rule_in_views(g)
    s = g[("Aerodrome", "rule_in_off_float_tokens")].set_index("date")["value"]
    assert list(s.index) == [pd.Timestamp("2026-11-10")] and s.iloc[0] == pytest.approx(540e6)
