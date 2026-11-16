"""Project round 6 — Sky (Jake 2026-11-16). Sky cells only. No network."""
import pandas as pd
import pytest

import config


# ================================================================================================ 1: S13 lines
def test_1_sky_s13_lines_follow_jakes_decisions():
    rs = config.REVENUE_SPLIT["Sky"]
    assert (rs["fees"]["metric"], rs["revenue"]["metric"], rs["holder_revenue"]["metric"]) == (
        "revenue_usd_ba", "sky_revenue_after_savings_usd_ba", "holder_value_revenue_usd")
    assert "Spark" in rs["fees"]["route"] and "Grove" in rs["fees"]["route"] and "RWA" in rs["fees"]["route"]
    assert "SSR" in rs["revenue"]["route"] and "DSR" in rs["revenue"]["route"]
    assert "Pause Proxy" in rs["holder_revenue"]["route"] and "staking_rewards_usds_usd" in rs["holder_revenue"]["route"]


def test_1_the_fetch_stores_the_savings_rate_and_revenue_net_of_it(monkeypatch):
    import fetch.sky_accounting as sa
    from fetch import scrape
    from fetch.base import FetchOutput
    monkeypatch.setattr(scrape, "robots_verdict", lambda url: (True, "robots.txt read (HTTP 404 = allowed)"))
    monkeypatch.setattr(sa, "today", lambda: pd.Timestamp("2026-11-16"))

    class H:
        def get(self, url, params=None):
            params = params or {}
            if url.endswith("/profit-and-loss/items/history/") and params.get("type") == "expense":
                return {"success": True, "data": [
                    {"date": "2026-10", "uid": "SSR", "subcategory": "Savings", "amount": "15121442"},
                    {"date": "2026-10", "uid": "DSR", "subcategory": "Savings", "amount": "210919"},
                    {"date": "2026-10", "uid": "STR", "subcategory": "Staking", "amount": "854775"},
                    {"date": "2026-10", "uid": "spark-DR", "subcategory": "Distribution Rewards", "amount": "809350"}]}
            if url.endswith("/profit-and-loss/history/") and not params.get("type"):
                return {"success": True, "data": [{"date": "2026-10", "type": "revenue", "amount": "32484100"},
                                                  {"date": "2026-10", "type": "expense", "amount": "17564150"},
                                                  {"date": "2026-10", "type": "revenue_distribution", "amount": "8771190"}]}
            return {"success": True, "data": []}
    out = FetchOutput()
    sa.SkyAccounting(http=H()).run([config.PROJECT_BY_NAME["Sky"]], 90, out)
    f = out.frame()
    sav = f[f.metric == "sky_savings_cost_usd_ba"]
    rev = f[f.metric == "sky_revenue_after_savings_usd_ba"]
    assert sav["value"].iloc[0] == pytest.approx(15_121_442 + 210_919)          # SSR + DSR only
    assert "SSR" in sav["source"].iloc[0] and "STR" not in sav["source"].iloc[0]
    assert rev["value"].iloc[0] == pytest.approx(32_484_100 - 15_121_442 - 210_919)


# ===================================================================================== 2: emissions and dilution
def test_2_every_staker_reward_is_emissions_c_and_none_is_an_unlock():
    assert config.outflow_metrics("Sky", "c") == ("emissions_tokens",)
    # no staker reward is an unlock: (e) holds only releases from a non-circulating wallet set, if Sky has one
    e = config.outflow_metrics("Sky", "e")
    assert "emissions_tokens" not in e and set(e) <= {"unlock_release_tokens"}
    assert "not an unlock" in config.OUTFLOW_CATEGORIES["Sky"]["basis"]
    na = config.declared_na_reason("Sky", "n/a — no non-circulating wallet set on this project (config.unlock_stock_"
                                          "metrics)")
    assert na and na["read_on"] == "2026-11-16" and "VestedRewardsDistribution" in na["source"]


def test_2_the_declared_streams_follow_the_vest_contract():
    steps = config.PROJECT_BY_NAME["Sky"]["issuance_schedule"]["steps"]
    by = {s["from"]: s for s in steps}
    assert by["2026-08-17"]["until"] == "2026-09-12" and by["2026-09-13"]["until"] == "2026-10-11"
    assert by["2026-10-12"]["tokens_per_day"] == pytest.approx(99_525_882 / 90)
    assert "2026-08-13" not in by
    # Q0 of Jake's 11-16 build (2026-08-18 .. 11-16, 90 days): the declared accrual against the measured 106,026,762 is
    # now the window edge only (the 11-09 .. 11-15 days paid by the 11-16 distribute), inside the ±10%
    days = pd.date_range("2026-08-18", "2026-11-15")
    def rate(d):
        cur = None
        for s in steps:
            if pd.Timestamp(s["from"]) <= d and (not s.get("until") or d <= pd.Timestamp(s["until"])):
                cur = s["tokens_per_day"]
        return cur or 0.0
    declared = sum(rate(d) for d in days)
    assert abs(106_026_762 / declared - 1) < 0.10, declared


# ================================================================================ 4: live references, not stale
def test_4_the_farm_rows_read_block_analitica_live_not_jakes_10_07_reading():
    cr = config.CREDIBILITY["Sky"]
    for k, m in (("in_locked_sky_farm", "locked_tokens_sky_farm_ba"), ("in_locked_usds_farm", "locked_tokens_usds_farm_ba"),
                 ("in_apy_sky_farm", "sky_farm_apy_ba"), ("in_apy_usds_farm", "usds_farm_apy_ba")):
        ref = cr[k]["ref"]
        assert m in str(ref.get("metric") or ref.get("args")) and "manual" not in ref, k      # live, never manual
        assert ("Sky", k) in config.LIVE_REFERENCE_OVER_MANUAL                               # not overridden (R7.B2)
    a3 = cr["a3_protocol_yield"]
    assert "usds_farm_apy_ba" in str(a3.get("metric") or a3.get("args")) and "manual" not in a3


# ================================================================ 3: the SBE's bought SKY is a held buyback
def _sg(metric, rows):
    return pd.DataFrame({"date": pd.to_datetime([d for d, _v in rows]), "project": "Sky", "metric": metric,
                         "value": [float(v) for _d, v in rows], "tier": 2, "source": "chain", "is_manual": False,
                         "entered_on": ""})


def test_3_the_pause_proxy_is_a_held_buyback_in_circulating_and_off_float(monkeypatch):
    import build_workbook as bw
    spec = config.CIRCULATING_ONCHAIN["Sky"]
    assert "treasury_holding_tokens" not in spec["subtract"] and spec["subtract"] == ("noncirculating_holding_tokens",)
    assert spec["handover"]["on"] == "2026-11-16" and "Pause Proxy" in spec["handover"]["from"]
    h = config.held_buyback_float("Sky")
    assert h["how"] == "subtract" and config.held_buyback_float_metric("Sky") == "treasury_holding_tokens"
    assert "CIRCULATING — DECLARED HANDOVER" in config.circulating_rule_note("Sky")
    # circulating = total − the converters + the MKR-backed add-back, every day; the Pause Proxy's SKY stays in
    monkeypatch.setattr(bw, "scoped_projects", lambda: [dict(config.PROJECT_BY_NAME["Sky"])])
    monkeypatch.setitem(config.CIRCULATING_ONCHAIN["Sky"], "subtract_when_complete", None)
    d = "2026-11-16"
    g = {("Sky", "total_supply"): _sg("total_supply", [(d, 23_460_000_000)]),
         ("Sky", "noncirculating_holding_tokens"): _sg("noncirculating_holding_tokens", [(d, 2_023_933_778.79)]),
         ("Sky", "sky_mkr_nonissuer_backing_tokens"): _sg("sky_mkr_nonissuer_backing_tokens", [(d, 1_000_000)]),
         ("Sky", "treasury_holding_tokens"): _sg("treasury_holding_tokens", [(d, 33_265_937.27)])}
    bw._circulating_views(g)
    c = g[("Sky", "circulating_supply_onchain")].set_index("date")["value"]
    assert c.loc[pd.Timestamp(d)] == pytest.approx(23_460_000_000 - 2_023_933_778.79 + 1_000_000)


def test_3_the_held_buyback_comes_off_a3s_effective_float_on_sky_only():
    import build_workbook as bw
    R = bw.Refs(100, 10, ["2026-09"])
    sky = bw._effective_float_held(R, 5, config.PROJECT_BY_NAME["Sky"])
    assert sky.startswith("-IF(ISNUMBER(") and '|treasury_holding_tokens"' in sky and sky.endswith(",NA())")
    for frozen in config.FROZEN_PROJECTS:                        # the frozen projects' formula does not change
        assert bw._effective_float_held(R, 5, config.PROJECT_BY_NAME[frozen]) == ""


# ================================================================== 5: done_check — declared, with a source
def test_5_skys_two_declared_reasons_carry_a_source_and_a_read_date():
    for text in ("n/a — no supply-side incentive (b) series on file; outflows held: (c) demand-side incentives",
                 "n/a — buyback_fund_balance (now): not applicable to this project — NO BUYBACK FUND EXISTS. Sky's "
                 "surplus passes through the Splitter"):
        e = config.declared_na_reason("Sky", text)
        assert e and e["source"] and e["read_on"] == "2026-11-16" and e["decided"].startswith("Jake 2026-11-16"), text
    assert config.declared_na_reason("Fluid", "n/a — no supply-side incentive (b) series on file") is None
