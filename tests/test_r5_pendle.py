"""Project round 5 — Pendle (Jake 2026-11-13). Pendle cells only. No network."""
import pandas as pd

import build_workbook as bw
import config


def _rows(metric, days, values, source="chain:x", project="Pendle"):
    return pd.DataFrame({"date": days, "project": project, "metric": metric, "value": values, "source": source,
                         "tier": 2})


# ============================================================================================== 1: circulating
def test_1_the_released_wallets_are_added_back_once_held_and_the_reserves_stay_out():
    spec = config.CIRCULATING_ONCHAIN["Pendle"]
    assert tuple(spec["subtract"]) == ("treasury_holding_tokens", "noncirculating_holding_tokens")
    swc = spec["subtract_when_complete"]
    assert (swc["metric"], swc["sign"], swc["whole_series"], swc["unlock_legs"]) == (
        "pendle_released_held_tokens", 1, True, True)
    assert config.current_set_point("Pendle", "pendle_released_held_tokens") == {
        "ethereum:rel_team_multisig", "ethereum:rel_fee_wallet"}
    c = config.PROJECT_BY_NAME["Pendle"]["contracts"]
    assert c["rel_team_multisig"]["address"] == c["noncirc_team_multisig"]["address"]
    assert c["rel_fee_wallet"]["address"] == c["treasury"]["address"]
    # the reserves stay out of circulating: the Ecosystem Fund and the Governance multisig are not in the add-back
    assert c["noncirc_ecosystem_fund"]["metric_override"] == "noncirculating_holding_tokens"
    assert "pendle-docs/ProtocolMechanics/Mechanisms/Tokenomics.md" in c["rel_team_multisig"]["source_url"]
    # pending: as before; in force: + the two wallets (Maple's pattern)
    days = pd.date_range("2026-01-01", periods=5)
    s = pd.Series(237_886_870.0, index=days)
    sw = dict(swc)
    part = {("Pendle", "pendle_released_held_tokens"): _rows("pendle_released_held_tokens", days[2:], 7.7e6,
                                                            "chain:sum(ethereum:rel_fee_wallet+ethereum:rel_team_multisig)")}
    out, _t = bw._subtract_when_complete(part, "Pendle", s, "t", sw)
    assert sw["in_force"] is False and out.equals(s)
    full = {("Pendle", "pendle_released_held_tokens"): _rows("pendle_released_held_tokens", days, 7.7e6,
                                                            "chain:sum(ethereum:rel_fee_wallet+ethereum:rel_team_multisig)")}
    out, _t = bw._subtract_when_complete(full, "Pendle", s, "t", sw)
    assert sw["in_force"] is True and (out == 237_886_870.0 + 7.7e6).all()


def test_1_the_float_takes_the_released_wallets_out_only_in_force_and_their_payouts_are_no_release(monkeypatch):
    swc = config.CIRCULATING_ONCHAIN["Pendle"]["subtract_when_complete"]
    days = pd.date_range("2026-01-01", periods=4)
    tot = _rows("total_supply", days, 281_527_448.0)
    rel = _rows("pendle_released_held_tokens", days, [7.80e6, 7.80e6, 7.70e6, 7.70e6],
                "chain:sum(ethereum:rel_fee_wallet+ethereum:rel_team_multisig)")
    g = {("Pendle", "total_supply"): tot, ("Pendle", "pendle_released_held_tokens"): rel}
    bw._released_held_view(g, "Pendle", {"in_force": False}, tot.columns)
    v = g[("Pendle", "issuer_held_released_tokens")]
    assert (v["value"] == 0.0).all() and "PENDING" in v["source"].iloc[0]          # nothing more off the float
    sw = dict(swc, in_force=True)
    bw._released_held_view(g, "Pendle", sw, tot.columns)
    assert list(g[("Pendle", "issuer_held_released_tokens")]["value"]) == [7.80e6, 7.80e6, 7.70e6, 7.70e6]
    # in force the team's 100,000 payout is not a release: only the reserves' falls are (e)
    monkeypatch.setitem(swc, "in_force", True)
    legs = config.unlock_stock_metrics("Pendle")
    assert legs == ("treasury_holding_tokens", "noncirculating_holding_tokens", "-pendle_released_held_tokens")
    g2 = {("Pendle", "treasury_holding_tokens"): _rows("treasury_holding_tokens", days, [11_000.0] * 4),
          ("Pendle", "noncirculating_holding_tokens"): _rows("noncirculating_holding_tokens", days,
                                                            [43.6e6, 43.6e6, 43.5e6, 43.3e6]),
          ("Pendle", "pendle_released_held_tokens"): _rows("pendle_released_held_tokens", days,
                                                          [7.811e6, 7.811e6, 7.711e6, 7.711e6])}
    bw._unlock_release_view(g2, "Pendle", "unlock_release_tokens", legs)
    rel_e = g2[("Pendle", "unlock_release_tokens")].set_index("date")["value"].round(6)
    assert list(rel_e) == [0.0, 0.0, 200_000.0]          # day 3: the team paid 100k (no release), day 4: Gov 200k


def test_1_the_gauge_funding_netted_out_of_the_release_is_its_own_flows_line(monkeypatch):
    monkeypatch.setitem(bw.GLOBALS, "period_days", 90)
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Pendle"]])
    days = pd.date_range("2025-09-01", "2026-11-12")
    n = len(days)
    fund = pd.Series(0.0, index=days)
    fund[days.day == 3] = 150_000.0                       # the Governance multisig funds the gauges monthly
    gov = 60e6 - fund.cumsum().values - (days.day == 15) * 0 - pd.Series((days.day == 20) * 50_000.0).cumsum().values
    g = {("Pendle", "total_supply"): _rows("total_supply", days, 281_527_448.0),
         ("Pendle", "treasury_holding_tokens"): _rows("treasury_holding_tokens", days, 300.0),
         ("Pendle", "noncirculating_holding_tokens"): _rows("noncirculating_holding_tokens", days, gov),
         ("Pendle", "issuer_to_gauges_tokens"): _rows("issuer_to_gauges_tokens", days, fund.values)}
    circ = 281_527_448.0 - 300.0 - gov
    g[("Pendle", "circulating_supply_used")] = _rows("circulating_supply_used", days, circ,
                                                     "derived:circulating_used[circulating_supply_onchain]")
    bw._unlock_views(g)
    tf = {r["Line"]: r for r in bw.flow_statements(g, pd.Timestamp("2026-11-12"))
          if r["Project"] == "Pendle" and r["Statement"] == "Token flow"}
    line = tf[config.FLOW_LESS_LINE["Pendle"]["label"]]
    assert line["Series"] == config.FLOW_LESS_LINE["Pendle"]["series"]
    for lab in bw.trajectory.LABELS[:4]:
        assert abs(tf["Residual (closing − expected)"][lab]) < 1e-3, lab
        assert tf["Status"][lab].startswith("OK"), tf["Status"][lab]
    assert n > 400 and bw.FLOW_RESIDUAL_LIMIT == 0.005


# ======================================================================================= 2: emissions, one figure
def test_2_cp_reads_the_gauge_payouts_and_the_check_is_like_for_like():
    assert "Nothing is minted" in config.EMISSIONS_ARE_INCENTIVE_SPEND["Pendle"]
    cr = config.CREDIBILITY["Pendle"]
    assert cr["in_emissions"]["ours"]["py"] == "scans_q0"
    assert cr["in_emissions"]["ours"]["args"]["metrics"] == ("emissions_tokens_gauge_mainnet",
                                                             "emissions_tokens_gauge_arbitrum",
                                                             "emissions_tokens_gauge_optimism")
    assert cr["in_emissions"]["ref"]["args"]["metrics"] == ("emissions_tokens_gauge_mainnet_direct",
                                                            "emissions_tokens_gauge_arbitrum_direct",
                                                            "emissions_tokens_gauge_optimism_direct")
    assert "in_emissions_unrouted" not in cr and "none is unrouted" in cr["in_emissions_other_chains"]["ref"]["why"]
    other = set(cr["in_emissions_other_chains"]["ours"]["args"]["metrics"])
    direct = set(cr["in_emissions"]["ours"]["args"]["metrics"])
    # the two rows together are the headline's legs, each once
    p = config.PROJECT_BY_NAME["Pendle"]
    saved = list(p["log_scans"])
    try:
        config.pin_frozen_probe()
        assert other | direct == set(p["emissions_from_metric"]) and not (other & direct)
    finally:
        p["log_scans"] = saved
    assert cr["a3_net_absorption"]["inputs"] == ("in_buyback", "in_emissions", "in_emissions_other_chains")


# ========================================================================== 3: a declared publish lag is matched
def test_3_the_follow_series_are_cut_at_the_buybacks_last_published_day():
    days = pd.date_range("2026-08-15", "2026-11-12")
    last = pd.Timestamp("2026-11-01")                                     # 11 days of publish lag
    buy = _rows("actual_buyback_tokens", days[days <= last], 7_000.0, "pendle_api:x")
    em = _rows("emissions_tokens", days, 700.0, "derived:sum(x)")
    ob = _rows("outflow_b_usd", days, 2_000.0, "derived:outflow")
    g = {("Pendle", "actual_buyback_tokens"): buy, ("Pendle", "emissions_tokens"): em, ("Pendle", "outflow_b_usd"): ob}
    bw._lag_matched_views(g, pd.Timestamp("2026-11-12"))
    cut = g[("Pendle", "emissions_tokens_lagm")]
    assert pd.to_datetime(cut["date"]).max() == last and len(cut) == (days <= last).sum()
    assert ("Pendle", "outflow_e_usd_lagm") not in g                       # no rows: not cut, the cell keeps its own
    p = config.PROJECT_BY_NAME["Pendle"]
    assert bw.lag_matched(p, "emissions_tokens") == "emissions_tokens_lagm"
    assert bw.lag_matched(p, "outflow_e_usd") == "outflow_e_usd"
    note = bw.lag_matched_note(p)
    assert note.startswith("MATCHED WINDOW (R5.3") and "2026-11-01" in note and "never filled or estimated" in note
    # a lag beyond two epochs + two days is not the declared lag: nothing is cut
    g2 = {("Pendle", "actual_buyback_tokens"): buy, ("Pendle", "emissions_tokens"): em}
    bw._lag_matched_views(g2, pd.Timestamp("2026-12-31"))
    assert ("Pendle", "emissions_tokens_lagm") not in g2 and bw.lag_matched(p, "emissions_tokens") == "emissions_tokens"
    assert bw.lag_matched_note(p).startswith("NOT LAG-MATCHED")
    # the cells Jake's 11-13 build blocked for the lag read the matched series: net absorption and net holder yield,
    # A3's key (emissions + unlocks) and the payout ratios (revenue / fees)
    assert set(config.LAG_MATCHED["Pendle"]["follow"]) == {"emissions_tokens", "outflow_b_usd", "outflow_e_usd",
                                                           "unlock_release_tokens", "revenue_usd", "fees_usd"}
    src = open("build_workbook.py", encoding="utf-8").read()
    assert "rv = lag_matched(p, rv)" in src and "unlocks=[lag_matched(p, m) for m in lg[\"unlocks\"]]" in src
    assert "_absorption_emissions(R, r, p, lag=True)" in src and 'om = lambda c: lag_matched(p, f"outflow_{c}_usd")' in src


# ================================================================================================= 4: S13 lines
def test_4_pendles_three_revenue_lines_are_its_own_basis():
    rs = config.REVENUE_SPLIT["Pendle"]
    assert (rs["fees"]["metric"], rs["revenue"]["metric"], rs["holder_revenue"]["metric"]) == (
        "fees_usd", "revenue_usd", "holders_revenue_usd")
    assert all(rs[k]["primary"] for k in ("fees", "revenue", "holder_revenue"))
    assert "statistics/market-fees" in rs["revenue"]["route"] and "spendle/data" in rs["holder_revenue"]["route"]
    assert config.EPOCH_PRIMARY["Pendle"]["series"]["holders_revenue_usd"]["epoch"] == "pendle_epoch_buyback_usd"
    tol = config.CREDIBILITY["Pendle"]["in_revenue"]["ref"]["tol"]
    assert tol == 10.0                                                     # not widened


# ============================================================================================ 5: insider share
def test_5_the_published_issuer_wallets_make_a_measured_zero_the_insider_share():
    mon = config.INSIDER_SHARE_MONITOR["Pendle"]
    assert "Tokenomics.md L29-31" in mon["wallets_published"] and "Fees.md" in mon["wallets_published"]
    assert not mon["not_wired"].startswith("MISSING E — needs issuer wallet list")
    assert mon["numerator"] == "insider_stake_shares"
