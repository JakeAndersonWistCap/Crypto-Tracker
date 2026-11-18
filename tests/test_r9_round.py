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


def test_2_pendle_multisigs_are_in_and_come_off_the_float_with_the_released_wallets():
    spec = config.RULE_IN["Pendle"]
    assert spec["legs"] == ("treasury_holding_tokens", "noncirculating_holding_tokens")
    for key in ("coingecko", "coinmarketcap"):
        ref, legs = spec["references"][key]
        assert ref + sum(legs.values()) == pytest.approx(spec["circulating"][1], rel=0.005), key

    class R:
        def D(self, r, m, w):
            return f"D[{m}]"
    held = bw._released_held(R(), 5, {"name": "Pendle"})
    # both come off the float: the wallets released per Pendle's docs (R5.1) and the stock the rule moved in
    assert "D[rule_in_off_float_tokens]" in held and config.FLOAT_RELEASED_HELD["Pendle"]["metric"] in held


# ============================================================================= 2: Sky — two legs from stored series
def test_2_sky_take_and_issuer_backing_follow_from_stored_series_and_the_float_does_not_move(monkeypatch):
    """The audit's read (2026-11-17): converter SKY 2,023,933,778.79; MKR outstanding 82,664.63, the Pause Proxy's
    49.56; rate 24,000, fee 5%. take() read directly: 39,982,641.85."""
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Sky"]])
    day = [("2026-11-17", 0)]
    mk = lambda m, v: _g("Sky", m, [(d, v) for d, _ in day])  # noqa: E731
    out_mkr, iss_mkr, conv, total = 82_664.63, 49.56, 2_023_933_778.79, 23_452_431_915.61
    g = {("Sky", "total_supply"): mk("total_supply", total),
         ("Sky", "noncirculating_holding_tokens"): mk("noncirculating_holding_tokens", conv),
         ("Sky", "mkr_outstanding_tokens"): mk("mkr_outstanding_tokens", out_mkr),
         ("Sky", "mkr_issuer_tokens"): mk("mkr_issuer_tokens", iss_mkr),
         ("Sky", "sky_mkr_nonissuer_backing_tokens"): mk("sky_mkr_nonissuer_backing_tokens",
                                                         (out_mkr - iss_mkr) * 24_000 * 0.95)}
    bw._derived_leg_views(g)
    take = g[("Sky", "sky_converter_take_tokens")]["value"].iloc[0]
    back = g[("Sky", "sky_issuer_mkr_backing_tokens")]["value"].iloc[0]
    assert take == pytest.approx(39_982_641.85, abs=25.0)          # the direct take() read, to the MKR supply's 2 decimals
    assert back == pytest.approx(1_189_410.0, abs=50.0)
    bw._circulating_views(g)
    circ = g[("Sky", "circulating_supply_onchain")]["value"].iloc[0]
    stays_out = (out_mkr - iss_mkr) * 24_000 * 0.05                 # the fee on unconverted non-issuer MKR only
    assert circ == pytest.approx(total - stays_out, abs=1.0)
    assert stays_out == pytest.approx(config.RULE_IN["Sky"]["stays_out"], rel=1e-4)
    before = total - conv + (out_mkr - iss_mkr) * 24_000 * 0.95
    bw._rule_in_views(g)
    off = g[("Sky", "rule_in_off_float_tokens")]["value"].iloc[0]
    assert circ - before == pytest.approx(off, abs=1e-3) == pytest.approx(take + back)   # in circulating = off the float
    assert "does not move" in config.RULE_IN["Sky"]["float_answer"]


def test_2_a_derived_leg_is_no_value_where_an_input_is_missing_or_it_would_be_negative(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Sky"]])
    g = {("Sky", "noncirculating_holding_tokens"): _g("Sky", "noncirculating_holding_tokens",
                                                      [("2026-11-16", 2.0e9), ("2026-11-17", 1.0e9)]),
         ("Sky", "mkr_outstanding_tokens"): _g("Sky", "mkr_outstanding_tokens", [("2026-11-17", 82_664.63)])}
    bw._derived_leg_views(g)
    assert ("Sky", "sky_converter_take_tokens") not in g            # 11-16 has no MKR read; 11-17 would be negative
    assert ("Sky", "sky_issuer_mkr_backing_tokens") not in g        # the Pause Proxy's MKR was not read


def test_2_sky_references_are_inside_two_percent_and_named():
    spec = config.RULE_IN["Sky"]
    for key in ("coingecko", "coinmarketcap"):
        ref, legs = spec["references"][key]
        assert abs(ref / spec["circulating"][1] - 1) < 0.02
        assert ref + sum(legs.values()) == pytest.approx(spec["circulating"][1], rel=0.001), key


# ============================================================================= 4: Aerodrome's S13 lines, on chain
def _aero_epoch_groups(issuer=10_000.0, dl_days=7, with_issuer=True):
    e = "2026-10-29"
    days = pd.date_range(e, periods=dl_days)
    g = {("Aerodrome", "voter_rewards_onchain_fees_usd"): _g("Aerodrome", "voter_rewards_onchain_fees_usd", [(e, 1_500_000.0)]),
         ("Aerodrome", "voter_rewards_onchain_bribes_usd"): _g("Aerodrome", "voter_rewards_onchain_bribes_usd", [(e, 200_000.0)]),
         ("Aerodrome", "fees_usd"): _g("Aerodrome", "fees_usd", [(d, 320_000.0) for d in days]),
         ("Aerodrome", "revenue_usd"): _g("Aerodrome", "revenue_usd", [(d, 250_000.0) for d in days]),
         ("Aerodrome", "holders_revenue_usd"): _g("Aerodrome", "holders_revenue_usd", [(d, 240_000.0) for d in days]),
         ("Aerodrome", "filing_managed_lock_tokens"): _g("Aerodrome", "filing_managed_lock_tokens", [(e, 466.7)]),
         ("Aerodrome", "locked_tokens"): _g("Aerodrome", "locked_tokens", [(e, 1000.0)])}
    if with_issuer:
        g[("Aerodrome", "voter_rewards_onchain_bribes_issuer_usd")] = _g(
            "Aerodrome", "voter_rewards_onchain_bribes_issuer_usd", [(e, issuer)])
    return g, pd.Timestamp(e)


def test_4_the_lines_come_from_the_onchain_epoch_with_issuer_incentives_out_and_stay_ordered():
    g, e = _aero_epoch_groups()
    bw._s13_views(g)
    v = lambda m: g[("Aerodrome", m)].set_index("date")["value"][e]  # noqa: E731
    hold, rev, fees = v("s13_holder_revenue_usd"), v("s13_revenue_usd"), v("s13_fees_usd")
    assert hold == pytest.approx(1_500_000 + 200_000 - 10_000)             # third-party incentives only
    assert rev == pytest.approx(hold + 7 * (250_000 - 240_000))            # + fees locked in dead gauges
    assert fees == pytest.approx(rev + 7 * (320_000 - 250_000))            # + what unstaked LPs keep, measured
    assert fees >= rev >= hold
    assert v("s13_issuer_incentives_usd") == 10_000.0                      # the Foundation-funded amount is reported
    assert v("s13_holder_revenue_insider_usd") == pytest.approx(hold * 0.4667)   # gross line, insider part beside it
    # the cross-check covers the same days: DefiLlama summed over the epoch's 7 days
    assert v("s13_holder_revenue_defillama_usd") == 7 * 240_000.0 and v("s13_fees_defillama_usd") == 7 * 320_000.0
    assert bw.S13_STATE["Aerodrome"]["days"] == 7


def test_4_no_line_forms_where_the_issuer_split_or_the_crosscheck_days_are_missing():
    g, _e = _aero_epoch_groups(with_issuer=False)       # a legacy epoch: the incentives' funder was never read
    bw._s13_views(g)
    assert ("Aerodrome", "s13_fees_usd") not in g and "no rows stored" in bw.S13_STATE["Aerodrome"]["why"]
    assert "NOT FORMED" in bw.s13_note({"name": "Aerodrome"})
    g, _e = _aero_epoch_groups(dl_days=6)               # DefiLlama holds 6 of the epoch's 7 days: not a covered epoch
    bw._s13_views(g)
    assert ("Aerodrome", "s13_holder_revenue_usd") not in g


def test_4_issuer_created_incentives_are_prorated_like_every_programme():
    from fetch.aero_v3 import WAD, epoch_issuer_incentives
    start, end = 1_000_000, 1_000_000 + 604_800
    tge = config.aero_issuer_incentive_creators()[0]
    rate = 5 * WAD                                                          # 5 raw units a second
    progs = [("0xAA", 0, rate, start - 100, start + 1000, tge.upper().replace("0X", "0x")),   # overlap 1,000 s
             ("0xAA", 0, rate, start, end, "0x" + "9" * 40),                                  # a third party
             ("0xBB", 0, rate, end, end + 50, tge)]                                           # after the epoch
    assert epoch_issuer_incentives(progs, start, end, set(config.aero_issuer_incentive_creators())) == {"0xaa": 5_000}
    assert "0x7269de76188e6597444d0859c4e5c336d3c39ddb" in config.aero_issuer_incentive_creators()


# ============================================================================= 5: Hyperliquid, the same covered days
def test_5_holder_revenue_and_revenue_are_read_over_the_days_both_hold_never_capped():
    days = pd.date_range("2026-08-01", "2026-10-29")                        # 90 days
    hv = [(d, 3.0) for d in days]                                           # the Fund's buybacks: every day
    rv = [(d, 2.0) for d in days[:-1]]                                      # revenue: the last day is a read lag
    g = {("Hyperliquid", "holder_value_revenue_usd"): _g("Hyperliquid", "holder_value_revenue_usd", hv),
         ("Hyperliquid", "revenue_total_usd"): _g("Hyperliquid", "revenue_total_usd", rv)}
    bw._s13_views(g)
    h = g[("Hyperliquid", "holder_value_revenue_usd_s13d")]
    r = g[("Hyperliquid", "revenue_total_usd_s13d")]
    assert len(h) == len(r) == 89 and set(h["date"]) == set(r["date"])
    assert h["value"].sum() == 267.0 > r["value"].sum() == 178.0            # above revenue on the same days: not capped
    assert bw.s13_metric("Hyperliquid", "holder_revenue") == "holder_value_revenue_usd_s13d"
    assert bw.s13_metric("Hyperliquid", "fees") == "fees_usd"               # a line not compared keeps its own days
    assert "2026-10-28" in bw.s13_note({"name": "Hyperliquid"})


def test_5_a_quiet_day_inside_an_event_series_drops_nothing_from_the_other_line():
    days = pd.date_range("2026-08-01", "2026-10-29")
    g = {("Hyperliquid", "holder_value_revenue_usd"): _g("Hyperliquid", "holder_value_revenue_usd",
                                                         [(d, 9.0) for d in days[::3]]),      # a row every third day
         ("Hyperliquid", "revenue_total_usd"): _g("Hyperliquid", "revenue_total_usd", [(d, 2.0) for d in days])}
    bw._s13_views(g)
    last = days[::3][-1]
    r = g[("Hyperliquid", "revenue_total_usd_s13d")]
    assert pd.to_datetime(r["date"]).max() == last and len(r) == (last - days[0]).days + 1    # only the end is cut


def test_5b_the_vote_date_is_kept_and_labelled_unverified_without_a_primary_post():
    v = config.HL_VOTE_DATE
    assert v["in_use"] == "2025-12-27" and v["status"] == "UNVERIFIED" and "primary" in v["rule"]
    assert "DATE UNVERIFIED" in config.PROJECT_BY_NAME["Hyperliquid"]["notes"]


# ============================================================================= 3: unlock legs under the rule
def test_3_every_rule_project_keeps_its_unlock_leg_on_its_vesting_stocks():
    """Jake's dilution decision (a): scheduled vesting releases count as float dilution even when the wallet is in
    circulating — so moving a wallet in never empties the unlock leg."""
    for name in ("Hyperliquid", "Morpho", "Uniswap", "Fluid", "Maple", "Pendle"):
        assert config.has_unlock_leg(name), name
        moved = set(config.rule_in_legs(name))
        assert moved & set(config.UNLOCK_STOCKS[name]) or name == "Morpho", name    # the moved wallets still feed it
    assert config.has_unlock_leg("Aerodrome") and "Aerodrome" in config.UNLOCK_DECLARED_ZERO   # a genuine zero reads 0
    assert not config.UNLOCK_STOCKS.get("Sky")                 # Sky has no vesting wallet set: n/a with its reason, as before


def test_2_circ_legs_names_a_rule_leg_as_in_circulating_not_subtracted():
    import circ_legs
    roles = dict(circ_legs.legs("Fluid"))
    assert roles["noncirculating_igp137_tokens"].startswith("in circulating by the rule")
    assert dict(circ_legs.legs("Morpho"))["morpho_relocked_tokens"] == "subtract"       # the relock stays out
    assert dict(circ_legs.legs("Sky"))["sky_converter_take_tokens"] == "add"


def test_2_an_addback_switch_in_force_adds_nothing_under_the_rule_and_leaves_the_float_once(monkeypatch):
    """Pendle on Jake's store (the switch in force): the Team multisig + fee wallet (7.75M) are inside the wallet set the
    rule counts in — circulating is the total (it read total + 7.75M), and the float loses the set once."""
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Pendle"]])
    spec = config.circulating_onchain("Pendle")
    swc = spec["subtract_when_complete"]
    keep = {k: swc.get(k) for k in ("in_force", "state")}
    days = [d for d in pd.date_range("2026-11-01", "2026-11-16")]
    mk = lambda m, v, src="chain": _g("Pendle", m, [(d, v) for d in days], src)  # noqa: E731
    monkeypatch.setattr(bw, "_swc_leg", lambda groups, name, metric, s: bw._daily(groups.get((name, metric))))
    monkeypatch.setattr("fetch.archive.catch_up_window_days", lambda: 10)
    g = {("Pendle", "total_supply"): mk("total_supply", 281_527_448.0),
         ("Pendle", "treasury_holding_tokens"): mk("treasury_holding_tokens", 0.0),
         ("Pendle", "noncirculating_holding_tokens"): mk("noncirculating_holding_tokens", 43_620_000.0),
         ("Pendle", "pendle_released_held_tokens"): mk("pendle_released_held_tokens", 7_750_000.0)}
    try:
        bw._circulating_views(g)
        assert swc["in_force"] is True
        circ = g[("Pendle", "circulating_supply_onchain")]
        assert set(circ["value"]) == {281_527_448.0}                        # the total: nothing added back
        assert "inside the set the rule counts in" in circ["source"].iloc[-1]
        bw._rule_in_views(g)
        off = g[("Pendle", "rule_in_off_float_tokens")]["value"].iloc[-1]
        own = g[("Pendle", config.FLOAT_RELEASED_HELD["Pendle"]["metric"])]["value"].iloc[-1]
        assert off + own == pytest.approx(43_620_000.0)                     # the whole set leaves the float, once
    finally:
        swc.update(keep)


def test_2_sky_keeps_its_flow_statement_lines():
    # the rule un-subtracts no Sky wallet (its legs are added from the converter, which stays outside circulating)
    assert bw._rule_flow("Sky") is False and all(bw._rule_flow(n) for n in ("Uniswap", "Morpho", "Maple", "Pendle"))


def test_2_the_daily_coingecko_check_is_like_for_like_not_a_review_row_for_the_rule():
    """Uniswap's live reads of 2026-11-05: under the rule ours is total − burned (886.2M) and CoinGecko's is that less
    the Timelock (624.0M). The fetch-time check compares CoinGecko with ours LESS the rule's wallets — no review row for
    a difference the rule declares; the figure every ratio uses still counts the Timelock in."""
    from fetch import circulating
    from fetch.base import FetchOutput
    live = {"total_supply_gross": 1_000_000_000.0, "burn_address_balance": 113_781_581.21,
            "treasury_holding_tokens": 262_247_996.31, "noncirculating_holding_tokens": 219_165.09}
    d = pd.Timestamp("2026-10-13")
    h = pd.DataFrame([{"date": d, "project": "Uniswap", "metric": m, "value": v} for m, v in {
        **live, "circulating_supply": 886_218_418.79 - 262_247_996.31 - 219_165.09}.items()])
    in_use, like = circulating.series(h, "Uniswap").iloc[-1], circulating.series(h, "Uniswap", rule=False).iloc[-1]
    assert in_use == pytest.approx(1e9 - 113_781_581.21) and in_use - like == pytest.approx(262_247_996.31 + 219_165.09)
    out = FetchOutput()
    circulating.check(out, h, [config.PROJECT_BY_NAME["Uniswap"]])
    assert not [r for r in out.review if r["reason"] == "onchain_vs_coingecko"]
