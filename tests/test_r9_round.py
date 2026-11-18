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


# ============================================================================= 2: Hyperliquid under the rule
def test_2_hyperliquid_team_and_spot_are_counted_in_and_the_float_does_not_move(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Hyperliquid"]])
    d = [("2026-11-14", 0), ("2026-11-15", 0)]
    mk = lambda m, v, src="hypercore_info:x": _g("Hyperliquid", m, [(x, v) for x, _ in d], src)  # noqa: E731
    g = {("Hyperliquid", "circulating_supply_first_party"): mk(
            "circulating_supply_first_party", 237.7e6, "hypercore_info:tokenDetails.circulatingSupply-exclude_wallets-hyperevm_burn"),
         ("Hyperliquid", "excluded_wallets_tokens"): mk("excluded_wallets_tokens", 63.6e6),
         ("Hyperliquid", "staked_noncirculating_tokens"): mk("staked_noncirculating_tokens", 301.7e6),
         ("Hyperliquid", "team_wallet_tokens"): mk("team_wallet_tokens", 238.2e6)}
    bw._released_staked_views(g)
    circ = g[("Hyperliquid", "circulating_supply_first_party")]["value"].iloc[-1]
    # stored net of Foundation + Grants: + their stake 63.5 + their spot 0.1 + the team 238.2
    assert circ == pytest.approx(237.7e6 + 63.6e6 + 238.2e6, rel=1e-9)
    assert "the rule" in g[("Hyperliquid", "circulating_supply_first_party")]["source"].iloc[-1]
    bw._rule_in_views(g)
    off = g[("Hyperliquid", "rule_in_off_float_tokens")]["value"].iloc[-1]
    assert off == pytest.approx(238.2e6 + 0.1e6, rel=1e-6)           # the team stake and the spot leave the float
    spec = config.RULE_IN["Hyperliquid"]
    assert spec["legs"] == ("team_wallet_tokens", "excluded_wallets_spot_tokens") and "earned" in spec["evidence"]
    assert config.UNLOCK_STOCKS["Hyperliquid"] == ("excluded_wallets_spot_tokens", "team_wallet_tokens")   # R9.3


def test_2_hyperliquid_references_are_named_by_wallet():
    r = config.HYPERLIQUID_RULE_REFERENCES
    for key in ("tokendetails", "coingecko", "coinmarketcap"):
        ref, legs = r[key]
        assert ref + sum(legs.values()) == pytest.approx(r["ours"], rel=0.005), key
    assert "rule-basis difference" in r["basis"] and "R9.2" in config.CREDIBILITY["Hyperliquid"]["in_circ"]["ref"]["why"]


def test_2_every_declared_r9_move_keeps_the_earlier_declarations():
    import json
    rec = json.loads((ROOT / "tests" / "fixtures" / "frozen_moves_r9.json").read_text(encoding="utf-8"))
    assert config.R9_MOVES_DECLARED == sum(len(s) for cells in rec.values() for s in cells.values()) > 0
    for proj, cells in rec.items():
        for key, steps in cells.items():
            d = config.FROZEN_MOVES[proj][key]
            last = (d.get("then") or [d])[-1]
            assert last["to"] == steps[-1]["to"] and last["why"].startswith("R9."), (proj, key)
            assert all(s["why"] and s["date"] and "from" in s for s in steps)


# ============================================================================= 2: Morpho, Uniswap, the flow statement
def test_2_morpho_keeps_the_relock_out_and_adds_the_switch_legs_back():
    spec = config.RULE_IN["Morpho"]
    assert spec["legs"] == ("noncirculating_holding_tokens",) and spec["swc_leg"] == "morpho_undistributed_tokens"
    assert "morpho_relocked_tokens" in config.CIRCULATING_ONCHAIN["Morpho"]["subtract"]          # the relock stays out
    assert "morpho_relocked_tokens" not in config.rule_in_legs("Morpho")
    assert "no wallet or contract published" in spec["relock_findings"]


def test_2_uniswap_treasury_is_in_and_the_references_add_up_by_wallet():
    spec = config.RULE_IN["Uniswap"]
    assert spec["legs"] == ("treasury_holding_tokens", "noncirculating_holding_tokens") == spec["off_float"]
    assert "burn_address_balance" not in spec["legs"]                                # burned stays out of both
    for key in ("coingecko", "coinmarketcap"):
        ref, legs = spec["references"][key]
        assert ref + sum(legs.values()) == pytest.approx(spec["circulating"][1], rel=0.005), key


def _flow_rows(name, groups, asof):
    keep = bw._SCOPE
    bw._SCOPE = [config.PROJECT_BY_NAME[name]]
    bw._ONE_OFF_IN_SERIES.clear()
    try:
        return {r["Line"]: r for r in bw.flow_statements(groups, asof) if r["Statement"] == "Token flow"}
    finally:
        bw._SCOPE = keep


def test_2_a_release_from_a_rule_wallet_is_not_a_circulating_change():
    """Uniswap under the rule: the Timelock is circulating, so what it releases moves the float, not circulating — the
    statement balances on minted − burned alone, and a burn out of the Timelock IS a circulating burn."""
    asof = pd.Timestamp("2026-11-09")
    days = pd.date_range("2026-05-01", "2026-11-09")
    burn = [1000.0] * len(days)
    circ = [900e6 - 1000.0 * (i + 1) for i in range(len(days))]
    g = {("Uniswap", "circulating_supply_used"): _g("Uniswap", "circulating_supply_used", list(zip(days, circ)),
                                                    "derived:circulating_onchain"),
         ("Uniswap", "gross_burn_tokens"): _g("Uniswap", "gross_burn_tokens", list(zip(days, burn))),
         ("Uniswap", "gross_issuance_tokens"): _g("Uniswap", "gross_issuance_tokens", [(d, 0.0) for d in days]),
         ("Uniswap", "unlock_release_tokens"): _g("Uniswap", "unlock_release_tokens", [(d, 50_000.0) for d in days])}
    tf = _flow_rows("Uniswap", g, asof)
    assert not any(line.startswith("+ released") for line in tf)
    assert not any("one-off burn from issuer-side" in line for line in tf)
    assert tf["− burned"]["Q0"] == pytest.approx(90_000.0)
    assert abs(tf["Residual ÷ opening"]["Q0"]) < 1e-9 and tf["Status"]["Q0"].startswith("OK"), tf["Status"]["Q0"]


def test_2_hyperliquid_and_morpho_flow_legs_are_declared():
    assert config.RULE_IN["Hyperliquid"]["flow_in"][0][1] == "emissions_tokens"
    lab, m = config.RULE_IN["Morpho"]["flow_outside"][0]
    assert m == "unlock_release_outside_tokens" and "relock" in lab
    assert config.UNLOCK_RELEASE_SUBSETS["Morpho"][m] == ("morpho_relocked_tokens",)


def test_2_fluid_igp137_wallet_is_in_on_the_proposal_text_and_its_signer():
    spec, w = config.RULE_IN["Fluid"], config.FLUID_IGP137_RULE
    assert "noncirculating_igp137_tokens" in spec["legs"] and "noncirculating_igp137_tokens" in spec["off_float"]
    assert any("legally locked" in t for t in w["proposal_text"]) and "externally owned" in w["signers"]
    assert w["tokens"] == 5_000_000.0 and "mislabelled" in w["was"] and "Jake" in w["decided"]
    for key in ("coingecko", "coinmarketcap"):
        ref, legs = spec["references"][key]
        assert ref + sum(legs.values()) == pytest.approx(spec["circulating"][1], rel=0.005), key
    # counted in: nothing of the set is subtracted any more, so circulating is the total
    from fetch import circulating as C
    d = pd.Timestamp("2026-11-10")
    h = pd.DataFrame([{"date": d, "project": "Fluid", "metric": m, "value": v} for m, v in (
        ("total_supply", 100e6), ("treasury_holding_tokens", 16.3e6), ("noncirculating_holding_tokens", 0.7e6),
        ("noncirculating_igp137_tokens", 5e6))])
    assert list(C.series(h, "Fluid")) == [100e6]


def test_2_maple_mints_to_the_dao_multisig_enter_circulating_when_minted():
    """Maple under the rule: the DAO multisig is circulating, so a RecapitalizationModule mint to it is a circulating
    change on its day — the statement balances on it, and a quarter with no mint event reads 0 (not a coverage gap)."""
    spec = config.RULE_IN["Maple"]
    assert spec["legs"] == ("treasury_holding_tokens_chain", "noncirculating_holding_tokens")
    assert spec["flow_in"][0][1] == "issuer_mint_tokens"
    asof = pd.Timestamp("2026-11-09")
    days = pd.date_range("2026-01-01", "2026-11-09")
    mint_day = pd.Timestamp("2026-10-09")
    circ = [1_244_000_000.0 + (23_000_000.0 if d >= mint_day else 0.0) for d in days]
    g = {("Maple", "circulating_supply_used"): _g("Maple", "circulating_supply_used", list(zip(days, circ)),
                                                  "derived:circulating_onchain"),
         ("Maple", "issuer_mint_tokens"): _g("Maple", "issuer_mint_tokens", [(mint_day, 23_000_000.0)]),
         ("Maple", "unlock_release_tokens"): _g("Maple", "unlock_release_tokens", [(d, 100_000.0) for d in days])}
    tf = _flow_rows("Maple", g, asof)
    line = next(k for k in tf if k.startswith("+ minted to the DAO multisig"))
    assert tf[line]["Q0"] == 23_000_000.0 and tf[line]["Q-1"] == 0.0
    assert not any(k.startswith("+ released") for k in tf)                 # the multisig's releases move the float only
    for q in ("Q0", "Q-1"):
        assert abs(tf["Residual ÷ opening"][q]) < 1e-9 and tf["Status"][q].startswith("OK"), tf["Status"][q]
    for key in ("coingecko", "coinmarketcap"):
        ref, legs = spec["references"][key]
        assert ref + sum(legs.values()) == pytest.approx(spec["circulating"][1], rel=0.005), key
