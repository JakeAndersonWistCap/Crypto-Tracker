"""ROUND G15 (Jake 2026-10-23, run on a0e74ee) — fix the G14 guard; two store faults. Nothing new is fetched.

1. the G14 guard refused the CURRENT leg (the chain's total_supply) against an earlier leg's stored row;
2. a differenced flow kept when its timed-out tier's stock reading was dropped — counted again by the next span;
3. Ethereum's NFT leg absent at its source on 2026-09-20..09-23;
4. Chainlink's CCIP Base leg wired;
5. yesterday's DefiLlama rows read in the morning are provisional.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402


def _g(project, metric, days, values, source="x", fetched_at=""):
    return pd.DataFrame({"date": pd.to_datetime(list(days)), "project": project, "metric": metric,
                         "value": [float(v) for v in values], "tier": 1, "source": source, "is_manual": False,
                         "entered_on": "", "fetched_at": fetched_at})


def _row(project, metric, day, value, source, tier=1):
    return {"date": pd.Timestamp(day), "project": project, "metric": metric, "value": float(value),
            "source": source, "tier": tier}


# ================================================================================================== 1 the guard
def test_1b_both_legs_on_one_day_on_the_real_path_store_the_chain_row(tmp_path, monkeypatch):
    """fetch.fetch_all with two tiers — CoinGecko and the chain — writing Chainlink's total for 2026-10-23, over a
    store already holding a CoinGecko row for that day (Jake's 10-23 morning): the chain row is stored, replacing it;
    the CoinGecko row is refused."""
    import fetch
    import store as sm
    from fetch.base import tidy

    class _CG:
        def run(self, projects, window_days, out):
            out.add(tidy([(pd.Timestamp("2026-10-23"), 1e9)], "Chainlink", "total_supply", "coingecko", 1),
                    "coingecko", "Chainlink", "", 1)

    class _Chain:
        def run(self, projects, window_days, out):
            out.add(tidy([(pd.Timestamp("2026-10-23"), 1e9)], "Chainlink", "total_supply", "chain:ethereum:token", 2),
                    "chain", "Chainlink", "", 2)
    monkeypatch.setattr(fetch, "TIER_ORDER", [("coingecko", 1, lambda ctx: _CG()), ("chain", 2, lambda ctx: _Chain())])
    st = sm.Store(str(tmp_path / "metrics.db"))
    days = pd.date_range("2026-10-15", "2026-10-22")
    st.upsert(pd.DataFrame([_row("Chainlink", "total_supply", d, 1e9, "chain:ethereum:token") for d in days]
                           + [_row("Chainlink", "total_supply", "2026-10-23", 1e9, "coingecko")]))
    out = fetch.fetch_all([config.PROJECT_BY_NAME["Chainlink"]], 30, stored_long=st.load_long())
    refused = [e.message for e in out.log if e.message.startswith("FALLBACK REFUSED")]
    assert len(refused) == 1 and "from coingecko" in refused[0] and "current leg chain:ethereum:token" in refused[0]
    st.upsert(out.frame())
    src = st.conn.execute("SELECT source FROM metrics WHERE project='Chainlink' AND metric='total_supply' AND "
                          "date='2026-10-23'").fetchone()[0]
    assert src == "chain:ethereum:token"
    st.close()


def test_1b_the_current_leg_is_never_refused_against_an_earlier_legs_later_row():
    """Pendle's Optimism gauge scan (the current leg) dated 07-16..10-08, with Jake's hand-entered 0 (the earlier leg)
    stored on 2026-10-09: G14 refused all 344; none is refused now."""
    import fetch
    from fetch.base import FetchOutput
    days = pd.date_range("2026-07-16", "2026-10-08")
    stored = _g("Pendle", "emissions_tokens_gauge_optimism", ["2026-10-09"], [0.0], "manual")
    out = FetchOutput()
    out.frames = [pd.DataFrame([_row("Pendle", "emissions_tokens_gauge_optimism", d, 0.0,
                                     "explorer:gauge_pendle_out_optimism:declared-zero") for d in days])]
    assert fetch._refuse_fallback_into_primary(out, stored) == [] and len(out.frame()) == len(days)


def test_1c_every_coingecko_row_after_the_handover_is_removed_and_logged(tmp_path):
    import store as sm
    import token_metrics as tm
    st = sm.Store(str(tmp_path / "metrics.db"))
    rows = [_row("Aethir", "total_supply", "2026-10-13", 4.2e10, "coingecko"),       # before its 10-14 handover: stays
            _row("Aethir", "total_supply", "2026-10-23", 4.2e10, "coingecko"),
            _row("Chainlink", "total_supply", "2026-10-10", 1e9, "coingecko"),       # before 10-11: stays
            _row("Chainlink", "total_supply", "2026-10-22", 1e9, "coingecko"),
            _row("Chainlink", "total_supply", "2026-10-23", 1e9, "coingecko"),
            _row("Chainlink", "total_supply", "2026-10-21", 1e9, "chain:ethereum:token"),
            _row("Ether.fi", "total_supply", "2026-10-23", 998_535_999.0, "coingecko"),
            _row("Ether.fi", "total_supply_coingecko", "2026-10-23", 965_350_000.0, "coingecko")]
    st.upsert(pd.DataFrame(rows))
    lines = tm.apply_run_cleanups(st, "r", logging.getLogger("t"))
    assert sorted(x.split(" from ")[0] for x in lines) == [
        "CLEAN-UP G14-1: removed Chainlink / total_supply 2026-10-22",
        "CLEAN-UP G15-1: removed Aethir / total_supply 2026-10-23",
        "CLEAN-UP G15-1: removed Chainlink / total_supply 2026-10-23",
        "CLEAN-UP G15-1: removed Ether.fi / total_supply 2026-10-23"]
    left = pd.read_sql("SELECT project, metric, date, source FROM metrics", st.conn)
    assert len(left) == 4 and set(left["date"]) == {"2026-10-13", "2026-10-10", "2026-10-21", "2026-10-23"}
    assert tm.apply_run_cleanups(st, "r2", logging.getLogger("t")) == []
    st.close()


# ================================================================================================== 2 flow vs stock
def test_2_a_differenced_flow_goes_with_its_dropped_reading():
    import fetch
    f = pd.DataFrame({"date": ["2026-10-22"] * 3, "project": ["GEODNET", "GEODNET", "Hyperliquid"],
                      "metric": ["burn_address_balance", "gross_burn_tokens", "gross_burn_tokens"],
                      "value": [69_700_000.0, 105_000.0, 22_647.68],
                      "source": ["chain:sum(polygon:burn_polygon+solana:burn_solana_token_account)",
                                 "chain:sum(polygon:burn_polygon+solana:burn_solana_token_account):delta",
                                 "hypercore_info:spotClearinghouseState:delta:PARTIAL"]})
    h = pd.DataFrame({"date": ["2026-10-22"], "project": ["Hyperliquid"], "metric": ["burn_address_balance"],
                      "value": [48.1e6], "source": ["hypercore_info:spotClearinghouseState"]})
    frames, dropped = fetch._drop_stocks([f, h])
    assert dropped == ["GEODNET/burn_address_balance", "GEODNET/gross_burn_tokens", "Hyperliquid/burn_address_balance",
                       "Hyperliquid/gross_burn_tokens"]
    assert not frames
    # a flow read per day (no :delta) is kept, as before
    frames, _ = fetch._drop_stocks([pd.DataFrame({"date": ["2026-10-22"] * 2, "project": ["GEODNET"] * 2,
                                                  "metric": ["burn_address_balance", "fees_usd"],
                                                  "value": [1.0, 2.0], "source": ["chain:x", "defillama:x"]})])
    assert list(frames[0]["metric"]) == ["fees_usd"]


def test_2_a_stock_that_misses_a_day_then_catches_up_still_equals_its_flows(tmp_path):
    import store as sm
    import token_metrics as tm
    days = pd.date_range("2026-10-15", "2026-10-23")
    level = {d: 69_000_000.0 + 105_000.0 * i for i, d in enumerate(days)}
    src = "chain:sum(polygon:burn_polygon+solana:burn_solana_token_account)"
    stock = [_row("GEODNET", "burn_address_balance", d, level[d], src) for d in days if d != pd.Timestamp("2026-10-22")]
    flows = [_row("GEODNET", "gross_burn_tokens", d, 105_000.0, src + ":delta") for d in days[1:-1]]
    flows.append(_row("GEODNET", "gross_burn_tokens", "2026-10-23", 210_000.0, src + ":delta[span=2d]"))
    # the old write: the 10-22 flow kept although its reading was dropped — 105,000 counted twice
    s_df, f_df = pd.DataFrame(stock), pd.DataFrame(flows)
    got = bw.telescoping("GEODNET", "gross_burn_tokens", "burn_address_balance", f_df, s_df)
    assert round(got["flow_residual"]) == -105_000
    # the repair (config.RUN_CLEANUPS G15-2) removes the orphan; the flows then equal the stock's move
    st = sm.Store(str(tmp_path / "metrics.db"))
    st.upsert(pd.DataFrame(stock + flows))
    lines = tm.apply_run_cleanups(st, "r", logging.getLogger("t"))
    assert lines == [f"CLEAN-UP G15-2: removed GEODNET / gross_burn_tokens 2026-10-22 from {src}:delta (105,000.00) — "
                     + config.RUN_CLEANUPS[2]["why"]]
    long = st.load_long()
    st.close()
    got = bw.telescoping("GEODNET", "gross_burn_tokens", "burn_address_balance",
                         long[long.metric == "gross_burn_tokens"], long[long.metric == "burn_address_balance"])
    assert got["flow_recon_blocked"] is None and abs(got["flow_residual"]) < 1e-6
    assert got["flow_stock_move"] == 105_000.0 * 8


# ================================================================================================== 3 the NFT leg
def test_3_an_nft_day_absent_at_its_source_is_rebuilt_on_dex_and_p2p_flagged(monkeypatch):
    days = pd.date_range("2026-09-10", "2026-10-21")
    gap = pd.to_datetime(["2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23"])
    nft_days = [d for d in days if d not in gap and d <= pd.Timestamp("2026-10-18")]
    groups = {("Ethereum", "dex_volume_usd"): _g("Ethereum", "dex_volume_usd", days, [1.5e9] * len(days)),
              ("Ethereum", "nft_volume_usd"): _g("Ethereum", "nft_volume_usd", nft_days,
                                                 [0.7e6 + 1e6 * (i % 9) for i in range(len(nft_days))]),
              ("Ethereum", "p2p_transfer_volume_usd"): _g("Ethereum", "p2p_transfer_volume_usd", days[:-3],
                                                          [5.7e9] * (len(days) - 3))}
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Ethereum"]])
    bw._settlement_rebuild_views(groups)
    g = groups[("Ethereum", "settlement_rebuilt_usd")].assign(date=lambda x: pd.to_datetime(x["date"]))
    on = g[g["date"].isin(gap)]
    assert len(on) == 4 and (on["value"] == 1.5e9 + 5.7e9).all()
    assert on["source"].str.contains("NFT leg absent \\(source gap\\): DEX \\+ P2P only on this day; NFT "
                                     "\\$0.7M–\\$8.7M vs P2P \\$5.7B–\\$5.7B").all()
    assert not g.loc[~g["date"].isin(gap), "source"].str.contains("NFT leg absent").any()
    # one measuring point: the flag is an annotation, so no MEASURING POINT CHANGED
    from fetch.base import _measuring_point
    assert g["source"].map(_measuring_point).nunique() == 1
    # no zero is stored for the NFT leg, and a day after the NFT series' last row is not filled
    assert not groups[("Ethereum", "nft_volume_usd")]["date"].isin(gap).any()
    assert g["date"].max() == pd.Timestamp("2026-10-18")
    assert bw.SETTLEMENT_VALIDATION["Ethereum"]["nft_gap_days"] == [str(d.date()) for d in gap]


def test_3_the_nft_read_names_what_the_chart_itself_lacks(monkeypatch):
    from fetch import eth_settlement as es
    from fetch.base import FetchOutput
    t = lambda d: int(pd.Timestamp(d).timestamp())                                        # noqa: E731
    chart = [[t(d), 1e6] for d in pd.date_range("2026-09-15", "2026-09-30")
             if d not in pd.to_datetime(["2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23"])]
    chart.append([t("2026-09-23"), None])
    chart = sorted(chart, key=lambda x: x[0])

    class _Http:
        def get(self, url, params=None):
            return {"chain": "Ethereum", "totalDataChart": chart}

    class _Daily:
        def due(self, *a):
            return True

        def done(self, *a):
            pass
    a = es.EthNftVolume.__new__(es.EthNftVolume)
    a.http, a.daily = _Http(), _Daily()
    out = FetchOutput()
    a.run([config.PROJECT_BY_NAME["Ethereum"]], 30, out)
    msg = " ".join(e.message for e in out.log)
    assert "SOURCE GAP — the chart has NO POINT for 3 day(s): 2026-09-20, 2026-09-21, 2026-09-22" in msg
    assert "the chart's value is NULL on 1 day(s): 2026-09-23" in msg
    assert not out.frame()["date"].isin(pd.to_datetime(["2026-09-20", "2026-09-23"])).any()


# ================================================================================================== 4 CCIP Base
def test_4_the_ccip_base_leg_has_its_credibility_row():
    import credibility
    import wiring
    spec = config.CREDIBILITY_WIRING["Chainlink"]["in_ccip_v2_base_overlap"]
    assert spec["ours"] == {"metric": "ccip_v2_fees_api_base_usd", "window": "q0"}
    assert spec["ref"]["verdict"] == "UNVERIFIABLE" and "ARBITRUM" in spec["ref"]["why"]
    named: set = set()
    wiring._strings(config.CREDIBILITY_WIRING["Chainlink"], named)
    assert "ccip_v2_fees_api_base_usd" in named
    rows = {"Chainlink|ccip_v2_fees_api_base_usd": {"q0": 1234.0}}
    assert "in_ccip_v2_base_overlap" in credibility.wiring_inputs("Chainlink", rows)


# ================================================================================================== 5 provisional days
def test_5_yesterday_read_before_1600_utc_is_provisional():
    pv = config.provisional
    assert config.PROVISIONAL_UNTIL_UTC_HOUR_NEXT_DAY == 16
    assert pv("Fluid", "fees_usd", "defillama:fees:fluid", "2026-10-22", "2026-10-23T07:42:00Z")
    assert pv("Fluid", "fees_usd", "defillama:fees:fluid", "2026-10-22", "2026-10-23T15:59:00Z")
    assert not pv("Fluid", "fees_usd", "defillama:fees:fluid", "2026-10-22", "2026-10-23T16:00:00Z")
    assert not pv("Fluid", "fees_usd", "defillama:fees:fluid", "2026-10-21", "2026-10-23T07:42:00Z")
    assert not pv("Fluid", "tvl_usd", "defillama:tvl:fluid", "2026-10-22", "2026-10-23T07:42:00Z")   # a stock
    assert not pv("GEODNET", "gross_burn_tokens", "chain:x:delta", "2026-10-22", "2026-10-23T07:42:00Z")


def test_5_a_provisional_row_is_stored_but_not_in_windows_or_change_flags(monkeypatch):
    from fetch import validate
    from fetch.base import FetchOutput
    days = pd.date_range("2026-10-08", "2026-10-22")
    vals = [100.0] * (len(days) - 1) + [20.0]                     # 2026-10-22 read at 07:42 UTC: 80% low
    g = _g("Fluid", "fees_usd", days, vals, "defillama:fees:fluid",
           fetched_at=["2026-10-22T18:00:00Z"] * (len(days) - 1) + ["2026-10-23T07:42:00Z"])
    groups = {("Fluid", "fees_usd"): g}
    bw._provisional_views(groups)
    assert pd.Timestamp("2026-10-22") not in set(groups[("Fluid", "fees_usd")]["date"])
    assert bw.PROVISIONAL[("Fluid", "fees_usd")] == [("2026-10-22", "2026-10-23T07:42:00Z")]
    # the change check at fetch time compares complete, non-provisional days only
    monkeypatch.setattr(validate.pd.Timestamp, "now", classmethod(lambda cls, tz=None: pd.Timestamp(
        "2026-10-23T07:42:00", tz="UTC")))
    out = FetchOutput()
    frame = g[["date", "project", "metric", "value", "source", "tier"]].copy()
    validate.validate_frame(frame, {("Fluid", "fees_usd"): 100.0}, out)
    assert not [r for r in out.review if r["reason"] == "change_threshold"]


def test_5_only_the_latest_day_is_provisional():
    """An older day read early is not held provisional for ever: once a newer day is held, a run has read past it."""
    g = _g("Fluid", "revenue_usd", ["2026-10-10", "2026-10-11", "2026-10-12"], [1.0, 1.0, 1.0], "defillama:fees:fluid",
           fetched_at=["2026-10-11T07:08:00Z", "2026-10-12T07:08:00Z", "2026-10-13T21:15:00Z"])
    groups = {("Fluid", "revenue_usd"): g}
    bw._provisional_views(groups)
    assert len(groups[("Fluid", "revenue_usd")]) == 3 and ("Fluid", "revenue_usd") not in bw.PROVISIONAL


def test_5_the_credibility_rows_read_the_store_less_the_provisional_day(monkeypatch):
    """The Credibility rows reproduce headlines from the store frame: they read it less the day the headline left out,
    so a note never names a headline built on one more day than the cell (Aerodrome's today's-price row)."""
    monkeypatch.setattr(bw, "PROVISIONAL", {("Fluid", "fees_usd"): [("2026-10-22", "2026-10-23T07:42:00Z")]})
    long = pd.DataFrame({"date": pd.to_datetime(["2026-10-21", "2026-10-22", "2026-10-22"]),
                         "project": ["Fluid", "Fluid", "Fluid"], "metric": ["fees_usd", "fees_usd", "revenue_usd"],
                         "value": [1.0, 2.0, 3.0], "source": ["defillama:fees:fluid"] * 3})
    kept = bw._without_provisional(long)
    assert list(zip(kept["metric"], kept["date"].dt.strftime("%Y-%m-%d"))) == [
        ("fees_usd", "2026-10-21"), ("revenue_usd", "2026-10-22")]
    monkeypatch.setattr(bw, "PROVISIONAL", {})
    assert len(bw._without_provisional(long)) == 3
