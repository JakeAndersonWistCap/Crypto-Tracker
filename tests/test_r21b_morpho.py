"""ROUND 21b — MORPHO (Jake 2026-11-25, item 2): "recommendation C, including the 6 and 11 Nov claims. Founder claims of
already-vested tokens aren't new supply; under Jake's rule they were circulating once vested. Show them as a named
insider float movement on the day."

1. The founders' Vester claims (config.MORPHO_FOUNDER_VESTER: awards #10, #13, #14, #15, #17, #18, vested 2025-06-11)
   are a declared series of on-chain Vest events, dated where the DAO's stock shows the fall.
2. They are NOT a release: netted out of unlock_release_tokens (and so outflow (e), total dilution, net holder yield
   and net float pressure) on their day (config.RELEASE_LESS).
3. On the free float they are a move out of the issuer / treasury component (the DAO, off the float) into the
   founder's own wallet (on the float): inside the "± issuer" line, named on its own line with the amount.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import free_float  # noqa: E402

V = config.MORPHO_FOUNDER_VESTER
M = V["metric"]


def _frame(name, metric, pairs, source="test:read"):
    return pd.DataFrame({"date": [pd.Timestamp(d) for d, _v in pairs], "project": name, "metric": metric,
                         "value": [float(v) for _d, v in pairs], "source": source, "tier": 1,
                         "fetched_at": "2026-11-20T00:00:00Z"})


def _days(start, values):
    d0 = pd.Timestamp(start)
    return [(d0 + pd.Timedelta(days=i), v) for i, v in enumerate(values) if v is not None]


# ====================================================================================== 1. the declared claims
def test_the_claims_are_the_six_founder_awards_vest_events_to_the_wei():
    assert set(V["awards"]) == {10, 13, 14, 15, 17, 18} and V["fin"] == "2025-06-11"
    by_day = config.morpho_founder_claims_by_day()
    assert by_day == {"2026-04-28": 1_500_000.0, "2026-11-06": pytest.approx(3_264_486.30401235),
                      "2026-11-11": pytest.approx(17_015_150.97736626), "2026-11-23": 65_000_000.0}
    # the 6 and 11 Nov claims Jake named: 20,279,637.28 — and the 23 Nov 65M (Paul Frambot's #17 / #18)
    assert by_day["2026-11-06"] + by_day["2026-11-11"] == pytest.approx(20_279_637.28, abs=0.01)
    assert V["unpaid"]["tokens"] == 32_000_000.0 == sum(V["unpaid"]["awards"].values())
    assert "paulframbot.eth" in V["awards"][17][1] and V["awards"][10][1] == "INFERRED founder"
    assert config.RELEASE_LESS["Morpho"] == (M,)


def test_the_view_dates_a_claim_where_the_daos_stock_falls(monkeypatch):
    """A live read shows the claim the same day (11-06); an archive read at the day's first block a day later (the
    04-28 claim fell on 04-29 in the store): the claim is dated on the fall. 0 on every other day, from fin."""
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Morpho"]])
    stock = [300e6, 300e6, 300e6 - 1.5e6, 298.5e6]                                     # 04-27 .. 04-30
    nov = [298.5e6, 298.5e6 - 3_264_486.30401235, 295_235_513.69598765]                # 11-05 .. 11-07
    groups = {("Morpho", "noncirculating_holding_tokens"): _frame(
        "Morpho", "noncirculating_holding_tokens", _days("2026-04-27", stock) + _days("2026-11-05", nov))}
    bw._founder_vest_claim_views(groups)
    s = bw._daily(groups[("Morpho", M)])
    assert s[pd.Timestamp("2026-04-29")] == 1_500_000.0 and s[pd.Timestamp("2026-04-28")] == 0.0
    assert s[pd.Timestamp("2026-11-06")] == pytest.approx(3_264_486.30401235)
    assert s[pd.Timestamp("2026-11-23")] == 65_000_000.0          # a claim after the last read keeps its own day
    assert s.index.min() >= pd.Timestamp(V["fin"]) and s.drop(pd.Timestamp("2026-11-23")).sum() == pytest.approx(
        1_500_000.0 + 3_264_486.30401235 + 17_015_150.97736626)


# ====================================================================================== 2. not a release
def test_a_founder_claim_is_netted_out_of_the_release_and_other_falls_stay(monkeypatch):
    """The DAO's stock falls by the 11-06 claim (3,264,486.30) and, on 11-08, by 1,000 that is no founder claim: the
    release holds the 1,000 only. Was: 3,264,486.30 + 1,000 (the claim read as an unlock, outflow (e))."""
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Morpho"]])
    claim = 3_264_486.30401235
    vals = [300e6, 300e6 - claim, 300e6 - claim, 300e6 - claim - 1000.0]                # 11-05 .. 11-08
    groups = {("Morpho", "noncirculating_holding_tokens"): _frame(
        "Morpho", "noncirculating_holding_tokens", _days("2026-11-05", vals), source="chain:sum(test)")}
    bw._founder_vest_claim_views(groups)
    bw._unlock_release_view(groups, "Morpho", "unlock_release_tokens", ("noncirculating_holding_tokens",))
    rel = bw._daily(groups[("Morpho", "unlock_release_tokens")])
    assert rel[pd.Timestamp("2026-11-06")] == pytest.approx(0.0, abs=1e-6)       # was 3,264,486.30
    assert rel[pd.Timestamp("2026-11-08")] == pytest.approx(1000.0)
    assert M in str(groups[("Morpho", "unlock_release_tokens")]["source"].iloc[0])
    # without the declaration the claim IS a release (the old reading)
    monkeypatch.setitem(config.RELEASE_LESS, "Morpho", ())
    bw._unlock_release_view(groups, "Morpho", "unlock_release_tokens", ("noncirculating_holding_tokens",))
    assert bw._daily(groups[("Morpho", "unlock_release_tokens")])[pd.Timestamp("2026-11-06")] == pytest.approx(claim)


# ====================================================================================== 3. the named float movement
def test_the_claim_moves_the_float_inside_the_issuer_line_and_is_named(monkeypatch):
    """Day 3: the DAO (rule_in_off_float_tokens, off the float) falls by the claim 50 → the float rises by 50 on the
    issuer line, NOT the unlocks line (which keeps the relock's 1), and the named line reads 50; the day balances."""
    name = "Morpho"
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[name]])
    monkeypatch.setattr(config, "RULE_IN_PENDING", {})
    spec = dict(config.FLOAT_NAMED_MOVES[name])
    monkeypatch.setitem(config.FLOAT_NAMED_MOVES, name,
                        dict(spec, names=lambda a, b: ["claim 50"] if pd.Timestamp(a) < pd.Timestamp("2026-11-03")
                             <= pd.Timestamp(b) else []))
    sp = free_float.parts(config.PROJECT_BY_NAME[name])
    assert not isinstance(sp, str)
    n = 5
    series = {"circulating_supply_used": _days("2026-11-01", [800 + i for i in range(n)]),
              "rule_in_off_float_tokens": _days("2026-11-01", [300, 300, 250, 250, 250]),
              "unlock_release_tokens": _days("2026-11-01", [1] * n),
              "unlock_release_outside_tokens": _days("2026-11-01", [1] * n),
              M: _days("2026-11-01", [0, 0, 50, 0, 0])}
    for m in (*sp["issuer"], *sp["lock"]["add"], *sp["lock"]["less"], *sp["held"]["add"], *sp["held"]["less"]):
        series.setdefault(m, _days("2026-11-01", [0] * n))
    groups = {(name, m): _frame(name, m, pairs) for m, pairs in series.items()}
    free_float.views(groups, pd.Timestamp("2026-11-05"))
    mv = free_float.MOVES[name]
    day = mv.loc[pd.Timestamp("2026-11-03")]
    assert day["issuer"] == pytest.approx(50.0) and day["unlocks"] == pytest.approx(1.0)
    assert day["named"] == pytest.approx(50.0) and abs(day["residual"]) < 1e-9
    assert day["close"] - day["open"] == pytest.approx(51.0)
    rows = {r["Line"]: r for r in free_float.statements(groups, pd.Timestamp("2026-11-05")) if r["Project"] == name}
    line = rows[spec["label"]]
    assert line["Q0"] == pytest.approx(50.0) and line["Series"] == M and "claim 50" in line["Note"]
    daily = free_float.period_table(name, groups, pd.Timestamp("2026-11-05"), 1, 5)
    i_amt = free_float.BLOCK_HEAD.index("Named insider float movement (tokens, inside the lines above)")
    i_txt = free_float.BLOCK_HEAD.index("Named insider float movement — what")
    on = next(r for r in daily if r[2] == "2026-11-03")
    assert on[i_amt] == pytest.approx(50.0) and on[i_txt] == "claim 50"
    off = next(r for r in daily if r[2] == "2026-11-04")
    assert off[i_amt] == 0.0 and off[i_txt] == ""


def test_the_declared_leg_never_blocks_the_release_and_is_not_a_b_claim(monkeypatch):
    """With the switch in force every release_less leg is required (a missing distributor claim BLOCKS the release,
    PR4.1); the founders' leg is a declaration, so its absence never blocks, and what it takes is not put on the
    "+ claimed out of the distributors (b)" line (UNLOCK_LESS_TAKEN)."""
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Morpho"]])
    monkeypatch.setattr(config, "release_less", lambda n: ((M, "emissions_tokens_claims_ethereum"), True))
    claim = 17_015_150.97736626
    vals = [300e6, 300e6, 300e6 - claim - 500.0]                                        # 11-09 .. 11-11
    groups = {("Morpho", "noncirculating_holding_tokens"): _frame("Morpho", "noncirculating_holding_tokens",
                                                                  _days("2026-11-09", vals)),
              ("Morpho", "emissions_tokens_claims_ethereum"): _frame(
                  "Morpho", "emissions_tokens_claims_ethereum", _days("2026-11-09", [0, 0, 500.0]))}
    bw.UNLOCK_LESS_TAKEN.clear()
    bw._unlock_release_view(groups, "Morpho", "unlock_release_tokens", ("noncirculating_holding_tokens",))
    assert ("Morpho", "unlock_release_tokens") in groups                    # the founders' leg absent: not BLOCKED
    bw._founder_vest_claim_views(groups)
    bw.UNLOCK_LESS_TAKEN.clear()
    bw._unlock_release_view(groups, "Morpho", "unlock_release_tokens", ("noncirculating_holding_tokens",))
    assert bw._daily(groups[("Morpho", "unlock_release_tokens")])[pd.Timestamp("2026-11-11")] == pytest.approx(
        0.0, abs=1e-6)
    taken = bw.UNLOCK_LESS_TAKEN[("Morpho", "unlock_release_tokens")]
    assert float(taken.sum()) == pytest.approx(500.0)                        # the (b) claim only


def test_the_names_carry_the_award_the_wallet_and_the_amount():
    names = config.morpho_founder_claim_names("2026-11-01", "2026-11-30")
    assert len(names) == 4
    assert names[0].startswith("2026-11-06: founder vest() claim 3,264,486.30 MORPHO") and "#10" in names[0]
    assert any("52,500,000.00" in x and "#17" in x and "paulframbot.eth" in x for x in names)
    assert config.morpho_founder_claim_names("2026-08-22", "2026-11-20")[-1].startswith("2026-11-11")
