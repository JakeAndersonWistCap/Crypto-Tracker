"""Round 22 — CIRC (Jake 2026-11-25, item 1): circulating lined up with the industry on Jake's rule. (a) unvested tokens
are out until they vest, staked or not; (b) an unreleased reserve is out like unminted supply, its payouts new supply the
day paid; (c) issuer holdings stay in, off the float. Behaviour and numbers; no network."""
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


DAYS = ("2026-10-12", "2026-10-13")


def _scope(monkeypatch, *names):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[n] for n in names])


# ============================================================================================ 1: Hyperliquid
def test_1_hyperliquid_team_out_foundation_and_grants_in(monkeypatch):
    """The dump's 2026-10-13 legs: tokenDetails net of Foundation / Grants 238,295,601.13; their holding 63,533,250.24;
    the three wallets' stake 301,307,793.72; the team 237,930,044.48. R9.2 read 539,758,895.85; R22 reads 301,828,851.37
    (the team out), Grants' spot the only rule leg off the float."""
    _scope(monkeypatch, "Hyperliquid")
    mk = lambda m, v, src="hypercore_info:x": _g("Hyperliquid", m, [(d, v) for d in DAYS], src)  # noqa: E731
    stored, excl, stk, team = 238_295_601.1281819, 63_533_250.23982319, 301_307_793.7160248, 237_930_044.47947535
    g = {("Hyperliquid", "circulating_supply_first_party"): mk(
            "circulating_supply_first_party", stored,
            "hypercore_info:tokenDetails.circulatingSupply-exclude_wallets-hyperevm_burn"),
         ("Hyperliquid", "excluded_wallets_tokens"): mk("excluded_wallets_tokens", excl),
         ("Hyperliquid", "staked_noncirculating_tokens"): mk("staked_noncirculating_tokens", stk),
         ("Hyperliquid", "team_wallet_tokens"): mk("team_wallet_tokens", team)}
    bw._released_staked_views(g)
    circ = g[("Hyperliquid", "circulating_supply_first_party")]
    # stored + the two wallets' stake + their spot = stored + their whole holding; the team is NOT added
    assert float(circ["value"].iloc[-1]) == pytest.approx(stored + excl, abs=0.01)
    assert float(circ["value"].iloc[-1]) == pytest.approx(config.HYPERLIQUID_R22["fixture"]["after"], abs=1.0)
    assert float(circ["value"].iloc[-1]) + team == pytest.approx(config.HYPERLIQUID_R22["fixture"]["before"], abs=1.0)
    assert circ["source"].iloc[-1] == "derived:released_staked[" + config.CIRC_RELEASED_STAKED["Hyperliquid"][
        "rule_point"] + "]"
    # the new point is a declared handover point of the series (the guard reads it)
    assert circ["source"].iloc[-1] in config.PROJECT_BY_NAME["Hyperliquid"]["series_handover"][
        "circulating_supply_first_party"]["ordered_points"]
    bw._rule_in_views(g)
    off = g[("Hyperliquid", "rule_in_off_float_tokens")]["value"].iloc[-1]
    spot = g[("Hyperliquid", "excluded_wallets_spot_tokens")]["value"].iloc[-1]
    assert off == pytest.approx(spot) and spot == pytest.approx(excl - (stk - team), abs=0.01)
    assert config.rule_in_legs("Hyperliquid") == ("excluded_wallets_spot_tokens",)
    # the team's stake stays inside the lock but outside circulating (the float nets it once), the pro-rata too
    assert "team_wallet_tokens" in config.locked_excluded_metrics("Hyperliquid")
    assert config.STAKE_PRORATA["Hyperliquid"]["outside"] == "team_wallet_tokens"


def test_1_hyperliquid_the_free_float_does_not_move():
    """R9.2: float = (circ_old + team + spot) − (locked − team) − (team + spot); R22: (circ_old + spot) − (locked − team)
    − spot. Both = circ_old − locked + team."""
    circ_old, team, spot, locked = 238.3e6 + 63.4e6, 237.9e6, 0.105e6, 440.1e6
    r92 = (circ_old + team + spot) - (locked - team) - (team + spot)
    r22 = (circ_old + spot) - (locked - team) - spot
    assert r22 == pytest.approx(r92)


def test_1_hyperliquid_the_team_wallet_staked_is_reported_and_the_references_are_named_by_wallet():
    t = config.HYPERLIQUID_R22["team"]
    assert t["staked"] == pytest.approx(238_360_840.87, abs=0.01) and t["vesting_state_in_api"] is None
    assert "reported, not counted" in t["finding"]
    r = config.HYPERLIQUID_RULE_REFERENCES
    for key in ("tokendetails", "coingecko", "coinmarketcap"):
        ref, legs = r[key]
        assert ref + sum(legs.values()) == pytest.approx(r["ours"], abs=1.0), key
        # every gap over 2% of the reference is named by wallet; the unnamed residual is under it
        for k, v in legs.items():
            if "not named" in k:
                assert abs(v) < 0.02 * ref
    h = config.HYPERLIQUID_R22
    assert h["tokendetails"]["circulating"] - h["tokendetails"]["hyperevm_zero_address"] == pytest.approx(h["ours_now"],
                                                                                                          abs=0.01)
    # CoinMarketCap = tokenDetails' circulating less the Assistance Fund, to 737 HYPE
    assert h["tokendetails"]["circulating"] - h["assistance_fund"]["spot"] == pytest.approx(h["coinmarketcap_now"],
                                                                                            abs=800)
    # (b) the pot is out already: totalSupply = circulating + the non-circulating list + futureEmissions
    pot = h["pot"]
    assert pot["genesis_0xdddd"] == pytest.approx(pot["allocation_38_888pct"] + pot["genesis_rest"], abs=1)
    assert pot["genesis_0xdddd"] - pot["future_emissions"] == pytest.approx(pot["paid_since_genesis"], abs=1)


def test_1_hyperliquid_flow_statement_team_rewards_stay_outside_its_payouts_enter():
    rs = config.RULE_IN["Hyperliquid"]
    assert [x[1] for x in rs["flow_in"]] == ["emissions_circulating_stake_tokens"]
    assert [x[1] for x in rs["flow_outside"]] == ["unlock_release_outside_tokens"]
    assert config.UNLOCK_RELEASE_SUBSETS["Hyperliquid"]["unlock_release_outside_tokens"] == ("team_wallet_tokens",)


def test_1_hyperliquid_the_team_payout_is_the_outside_release(monkeypatch):
    _scope(monkeypatch, "Hyperliquid")
    g = {("Hyperliquid", "team_wallet_tokens"): _g("Hyperliquid", "team_wallet_tokens",
                                                   [("2026-11-05", 238.40e6), ("2026-11-06", 238.22e6),
                                                    ("2026-11-07", 238.234e6)], "hypercore_info:x"),
         ("Hyperliquid", "excluded_wallets_spot_tokens"): _g("Hyperliquid", "excluded_wallets_spot_tokens",
                                                             [("2026-11-05", 105_001), ("2026-11-06", 105_001),
                                                              ("2026-11-07", 105_001)], "derived:x")}
    bw._unlock_views(g)
    rel = g[("Hyperliquid", "unlock_release_outside_tokens")].set_index("date")["value"]
    assert rel.loc[pd.Timestamp("2026-11-06")] == pytest.approx(180_000, abs=1)
    assert rel.loc[pd.Timestamp("2026-11-07")] == 0.0               # a reward credited is no negative release


# ============================================================================================ 2: Ether.fi (a)
def test_2_etherfi_unvested_team_leg_is_out_of_circulating(monkeypatch):
    _scope(monkeypatch, "Ether.fi")
    total, unv = 998_535_999.0, config.etherfi_team_unvested_on("2026-10-13")
    assert unv == pytest.approx(35_783_333.33, abs=0.01)
    g = {("Ether.fi", "total_supply"): _g("Ether.fi", "total_supply", [(d, total) for d in DAYS]),
         ("Ether.fi", "treasury_holding_tokens"): _g("Ether.fi", "treasury_holding_tokens", [(d, 0.0) for d in DAYS]),
         ("Ether.fi", "noncirculating_holding_tokens"): _g("Ether.fi", "noncirculating_holding_tokens",
                                                           [(d, 187_839_959.47) for d in DAYS]),
         ("Ether.fi", "etherfi_team_unvested_modelled_tokens"): _g(
             "Ether.fi", "etherfi_team_unvested_modelled_tokens", [(d, unv) for d in DAYS],
             "modelled:etherfi_team_schedule")}
    bw._circulating_views(g)
    c = g[("Ether.fi", "circulating_supply_onchain")]["value"].iloc[-1]
    assert c == pytest.approx(962_752_665.67, abs=0.01)                 # was 998,535,999 (R14.1, decision 1 (a))
    assert c == pytest.approx(config.RULE_IN["Ether.fi"]["circulating"][1], abs=1.0)
    assert "etherfi_team_unvested_modelled_tokens" not in config.RULE_IN["Ether.fi"]["off_float"]
    assert config.UNLOCK_RELEASE_SUBSETS["Ether.fi"]["unlock_release_outside_tokens"] == (
        "etherfi_team_unvested_modelled_tokens",)
    # 2026-11-25: 4/36 of 214.7M unvested
    assert total - config.etherfi_team_unvested_on("2026-11-25") == pytest.approx(974_680_443.44, abs=0.01)


def test_2_etherfi_the_run_time_check_forms_the_modelled_leg_itself():
    """fetch.circulating.series (the daily CoinGecko check) reads the store, which never holds the modelled leg: it is
    formed from the schedule on the total's days, so the check keeps a figure."""
    from fetch import circulating as cm
    h = pd.concat([_g("Ether.fi", "total_supply", [("2026-10-13", 998_535_999.0)]),
                   _g("Ether.fi", "treasury_holding_tokens", [("2026-10-13", 0.0)]),
                   _g("Ether.fi", "noncirculating_holding_tokens", [("2026-10-13", 187_839_959.47)])])
    s = cm.series(h, "Ether.fi")
    assert float(s.iloc[-1]) == pytest.approx(962_752_665.67, abs=0.01)


# ============================================================================================ 3: Chainlink (b)
def test_3_chainlink_the_33_wallets_are_out(monkeypatch):
    _scope(monkeypatch, "Chainlink")
    g = {("Chainlink", "total_supply"): _g("Chainlink", "total_supply", [(d, 1e9) for d in DAYS]),
         ("Chainlink", "buyback_fund_balance"): _g("Chainlink", "buyback_fund_balance", [(d, 6_804_760.10) for d in DAYS]),
         ("Chainlink", "noncirculating_holding_tokens"): _g("Chainlink", "noncirculating_holding_tokens",
                                                            [(d, 233_150_000.10284975) for d in DAYS])}
    bw._circulating_views(g)
    c = g[("Chainlink", "circulating_supply_onchain")]["value"].iloc[-1]
    assert c == pytest.approx(766_849_999.90, abs=0.01)                 # was 1,000,000,000 (R18)
    assert config.rule_in_legs("Chainlink") == ("buyback_fund_balance",)
    assert "noncirculating_holding_tokens" not in config.RULE_IN["Chainlink"]["off_float"]
    assert [x[1] for x in config.RULE_IN["Chainlink"]["flow_outside"]] == ["unlock_release_tokens"]


# ============================================================================================ 4: GEODNET (b)
def test_4_geodnet_the_mining_pot_is_out_the_ecosystem_stays_in(monkeypatch):
    _scope(monkeypatch, "GEODNET")
    d = DAYS
    g = {("GEODNET", "total_supply_gross"): _g("GEODNET", "total_supply_gross", [(x, 1e9) for x in d]),
         ("GEODNET", "burn_address_balance"): _g("GEODNET", "burn_address_balance", [(x, 69_593_936.381899) for x in d]),
         ("GEODNET", "treasury_holding_tokens"): _g("GEODNET", "treasury_holding_tokens",
                                                    [(x, 145_945_987.50437057) for x in d]),
         ("GEODNET", "ecosystem_holding_tokens"): _g("GEODNET", "ecosystem_holding_tokens", [(x, 50_522_654.0) for x in d]),
         ("GEODNET", "noncirculating_holding_tokens"): _g("GEODNET", "noncirculating_holding_tokens",
                                                          [(x, 268_144_476.307) for x in d])}
    bw._circulating_views(g)
    c = g[("GEODNET", "circulating_supply_onchain")]["value"].iloc[-1]
    assert c == pytest.approx(834_982_730.11, abs=0.01)                 # was 930,406,063.62 (R17)
    assert config.RULE_IN["GEODNET"]["off_float"] == ("ecosystem_holding_tokens", "noncirculating_holding_tokens")
    assert [x[1] for x in config.RULE_IN["GEODNET"]["flow_outside"]] == ["pool_release_tokens"]
    # the CoinGecko check names the two wallets it still leaves out: 2026-11-23, within the tolerance
    ours = 926_216_062.51 - 89_000_000 - 1_063_344.64
    ref, named = config.RULE_IN["GEODNET"]["references"]["coingecko"]
    assert abs(ref / (ours - sum(named.values())) - 1) < config.CIRCULATING_TOLERANCE


# ============================================================================================ 5: Near (a)
def test_5_near_locked_is_out_staked_or_not(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Near"]])
    r = config.NEAR_R22_LOCKED_STAKED
    total, day, src = 1_312_113_606.0, "2026-11-19", "near_rpc:lockups@225245079"
    mk = lambda m, v, s="near_rpc:x": pd.DataFrame({"date": pd.to_datetime([day]), "project": "Near", "metric": m,  # noqa: E731
                                                     "value": [float(v)], "source": s, "tier": 1})
    g = {("Near", "total_supply"): mk("total_supply", total),
         ("Near", "near_lockup_locked_tokens"): mk("near_lockup_locked_tokens", r["locked"], src),
         ("Near", "near_lockup_locked_staked_tokens"): mk("near_lockup_locked_staked_tokens", r["locked_staked"], src),
         ("Near", "near_foundation_accounts_tokens"): mk("near_foundation_accounts_tokens", 171_059.13)}
    bw._near_lockup_views(g)
    circ = g[("Near", "circulating_supply_lockup_rule")]
    assert float(circ["value"].iloc[-1]) == pytest.approx(total - r["locked"], abs=0.01)   # was total − 788,417.85 idle
    src_out = circ["source"].iloc[-1]
    assert "R22" in src_out
    import trajectory
    assert trajectory.basis_of("circulating_supply_lockup_rule", src_out) in [
        trajectory.basis_of("circulating_supply_lockup_rule", p)
        for p in config.PROJECT_BY_NAME["Near"]["series_handover"]["circulating_supply_lockup_rule"]["ordered_points"]]


# ============================================================================================ 6: the sweep
def test_6_the_sweep_declares_every_move_and_the_doc_holds_all_fifteen():
    s = config.R22_CIRC_SWEEP
    assert s["moved"] == {"Hyperliquid": "a", "Ether.fi": "a", "Chainlink": "b", "GEODNET": "b", "Near": "a"}
    for p in ("Ether.fi", "Chainlink", "GEODNET"):
        assert config.CIRCULATING_ONCHAIN[p]["handover_r22"]["decided"].startswith("Jake 2026-11-25")
    doc = (ROOT / "docs" / "r22_circulating.md").read_text(encoding="utf-8")
    for p in ("Aerodrome", "Ether.fi", "Fluid", "Hyperliquid", "Maple", "Morpho", "Pendle", "Sky", "Uniswap",
              "Chainlink", "GEODNET", "Ethereum", "Near", "Aethir", "Plume"):
        assert f"| {p} |" in doc, p
