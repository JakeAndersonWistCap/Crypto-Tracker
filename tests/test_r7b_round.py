"""Round 7B (Jake 2026-11-16): gate fixes, finish Sky, finish Hyperliquid. Behaviour and values. No network."""
import pandas as pd
import pytest

import config


# ======================================================================================== A: the guard's fix
def test_a1_a_first_party_release_never_lets_the_coingecko_route_stand_in(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "_VIEW_BLOCKS", {})
    d = pd.to_datetime(["2026-10-12", "2026-10-13"])
    cg = pd.DataFrame({"date": d, "project": "Hyperliquid", "metric": "pool_release_tokens", "value": [5.0, 6.0],
                       "source": config.POOL_RELEASE_DERIVED_SOURCE, "tier": 2})
    stock = pd.DataFrame({"date": d[:1], "project": "Hyperliquid", "metric": "circulating_supply_first_party",
                          "value": [3e8], "source": "derived:released_staked[x]", "tier": 1})
    g = {("Hyperliquid", "pool_release_tokens"): cg, ("Hyperliquid", "circulating_supply_first_party"): stock}
    bw._onchain_release_views(g)
    assert ("Hyperliquid", "pool_release_tokens") not in g                    # unknown, never the CoinGecko route
    assert len(g[("Hyperliquid", "pool_release_tokens_coingecko")]) == 2       # kept beside, as the cross-check
    assert "never in its place" in bw._VIEW_BLOCKS[("Hyperliquid", "pool_release_tokens")]


# ================================================================================ B1: Sky's endowments are in
def test_b1_the_endowments_are_circulating_and_their_liquid_sky_is_off_the_float():
    spec = config.CIRCULATING_ONCHAIN["Sky"]
    assert not spec.get("subtract_when_complete")                              # never subtracted again
    assert spec["handover"]["decided"].endswith("R7B.B1)") and "endowments" in spec["handover"]["to"]
    assert "not a release restriction" in spec["endowment_note"] and "SFF Q1 2026" in spec["endowment_note"]
    assert config.unlock_stock_metrics("Sky") == () and config.outflow_metrics("Sky", "e") == ()
    fr = config.float_released_held("Sky")
    assert fr["from"] == "sky_endowment_liquid_tokens"
    import build_workbook as bw
    d = pd.to_datetime(["2026-11-14", "2026-11-15"])
    mk = lambda m, v: pd.DataFrame({"date": d, "project": "Sky", "metric": m, "value": v, "tier": 2,  # noqa: E731
                                    "source": "chain"})
    g = {("Sky", "sky_endowment_tokens"): mk("sky_endowment_tokens", [2_968.5e6, 2_970.0e6]),
         ("Sky", "sky_endowment_locked_tokens"): mk("sky_endowment_locked_tokens", [2_930.8e6, 2_931.0e6])}
    bw._difference_views(g)
    liq = g[("Sky", "sky_endowment_liquid_tokens")].set_index("date")["value"]
    assert liq.iloc[0] == pytest.approx(37.7e6) and liq.iloc[1] == pytest.approx(39.0e6)


def test_b2_in_locked_judges_against_both_farms_live():
    import credibility as cred
    row = config.CREDIBILITY["Sky"]["in_locked"]
    assert row["ref"]["formula"] == "sum_now" and ("Sky", "in_locked") in config.LIVE_REFERENCE_OVER_MANUAL
    assert row["ref"]["tol"] == 2.0
    d = pd.to_datetime(["2026-11-15", "2026-11-16"])
    long = pd.concat([pd.DataFrame({"date": d, "project": "Sky", "metric": m, "value": v})
                      for m, v in (("locked_tokens_sky_farm_ba", [8.59e9, 8.60e9]),
                                   ("locked_tokens_usds_farm_ba", [8.80e9, 8.81e9]))])
    v, day, how = cred._sum_now("Sky", {}, long, pd.Timestamp("2026-11-16"), **row["ref"]["args"])
    assert v == pytest.approx(17.41e9) and day == "2026-11-16" and " + " in how
    v, _d, why = cred._sum_now("Sky", {}, long[long.metric != "locked_tokens_usds_farm_ba"], pd.Timestamp("2026-11-16"),
                               **row["ref"]["args"])
    assert v is None and "locked_tokens_usds_farm_ba" in why


# ======================================================================================= C2: Hyperliquid revenue
def test_c2_the_aqa_yield_is_counted_once():
    import build_workbook as bw
    cut = config.REVENUE_LEG_COUNTED_ELSEWHERE[("Hyperliquid", "aqa_revenue_usd")]
    assert cut["from"] == "2026-08-27" and "27 Aug 2026" in cut["source"]
    d = pd.date_range("2026-08-25", "2026-08-29")
    mk = lambda m, v: pd.DataFrame({"date": d, "project": "Hyperliquid", "metric": m, "value": v,  # noqa: E731
                                    "source": "x", "tier": 1})
    g = {("Hyperliquid", "revenue_usd"): mk("revenue_usd", [100.0] * 5),
         ("Hyperliquid", "aqa_revenue_usd"): mk("aqa_revenue_usd", [7.0] * 5)}
    old = bw._SCOPE
    bw._SCOPE = [config.PROJECT_BY_NAME["Hyperliquid"]]
    try:
        bw._revenue_total_views(g)
    finally:
        bw._SCOPE = old
    tot = g[("Hyperliquid", "revenue_total_usd")].set_index("date")["value"]
    assert tot[pd.Timestamp("2026-08-26")] == pytest.approx(107.0)            # before DefiLlama counts it: added
    assert tot[pd.Timestamp("2026-08-27")] == pytest.approx(100.0)            # from then: inside DefiLlama's, once
    rs = config.REVENUE_SPLIT["Hyperliquid"]
    assert rs["revenue"]["metric"] == "revenue_total_usd" and "USDH" in rs["revenue"]["route"]
    assert "USDH" in config.HL_HOLDER_SHARE_GAP


def test_c2_hyperliquids_own_stats_store_only_confirmed_shapes():
    from fetch.base import FetchOutput
    from fetch.hl_stats import HlStats

    class H:
        def post(self, url, json_body=None):
            if json_body["type"] == "userFees":
                return {"dailyUserVlm": [{"date": "2026-11-14", "exchange": "6888770628.6"},
                                         {"date": "2026-11-15", "exchange": "5488584292.4"}]}
            return [None, {"name": "xyz", "feeRecipient": "0x83ff"}, {"name": "flx", "feeRecipient": "0xe287"}]

        def get(self, url, params=None):
            return [{"time": "2026-11-14", "fees": 2_500_000.0}]
    out = FetchOutput()
    HlStats(http=H()).run([config.PROJECT_BY_NAME["Hyperliquid"]], 30, out)
    f = out.frame()
    assert f[f.metric == "hl_exchange_volume_usd"]["value"].tolist() == pytest.approx([6_888_770_628.6, 5_488_584_292.4])
    assert f[f.metric == "hl_hip3_dex_count"]["value"].tolist() == [2.0]
    assert not f.metric.str.startswith("hl_fees").any()                       # unconfirmed: nothing stored
    assert any("UNCONFIRMED" in e.message and "first record keys" in e.message for e in out.log)


# ====================================================================================== C3: declared n/a
def test_c3_subsidy_cover_is_declared_na_with_its_sources_and_a_recheck_date():
    e = config.declared_na_reason("Hyperliquid", "n/a — no supply-side incentive (b) series on file; outflows held")
    assert e and "trading/fees" in e["source"] and "hype-tokenomics" in e["source"]
    assert e["recheck"] == "2026-12-16" and "points" in e["why"]


# ================================================================== C4 / B2: the export's remaining reasons
def test_c4_the_derived_emissions_series_inherit_the_declared_handover_and_no_coingecko_fallback():
    for m in ("emissions_circulating_stake_tokens", "issuance_option_c_tokens"):
        h = config.declared_handover("Hyperliquid", m)
        assert h and h["ordered_points"][0] == "modelled:hl_reference_yield" and "none" in h["composition_change"]
    assert config.no_coingecko_fallback("Hyperliquid")                  # never CoinGecko's basis as a stand-in


def test_b2_sky_has_no_unlock_by_construction_and_says_why():
    e = config.declared_na_reason("Sky", "Not formed on this tab: its total dilution is emissions + unlocks, and no "
                                         "non-circulating wallet set is declared for Sky")
    assert e and "mkrToSky" in e["source"] and e["read_on"] == "2026-11-16"
