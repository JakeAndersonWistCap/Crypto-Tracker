"""Rounds 6B + 7 (Jake 2026-11-16): housekeeping, Sky 6B, Hyperliquid 7. Behaviour and values, not cell wording.
No network."""
from pathlib import Path

import pandas as pd
import pytest

import config

ROOT = Path(__file__).resolve().parents[1]


# ============================================================================================ A0: the gate's two
def test_a0_the_s13_concepts_list_skys_series():
    import spec
    c = spec.concepts()
    assert "revenue_usd_ba" in c["fees"]["inputs"]
    assert "sky_revenue_after_savings_usd_ba" in c["revenue"]["inputs"]


# ======================================================================================= A2c: .ps1 files are ASCII
def test_a2c_every_tracked_ps1_is_ascii():
    # R7B.A1: walked on disk, not `git ls-files` — the gate's worktree answered git with exit 128 (no index there)
    skip = {".git", ".venv", "venv", "node_modules", "__pycache__", "_scratch"}
    files = [p for p in ROOT.rglob("*.ps1") if not (set(p.relative_to(ROOT).parts) & skip)]
    assert any(p.name == "daily_run.ps1" for p in files)
    for f in files:
        assert f.read_bytes().isascii(), f


# ============================================================================ A2a: Pendle's revenue row like for like
def test_a2a_defillama_breakdown_sums_the_named_chains():
    from fetch.base import FetchOutput
    from fetch.llama import DefiLlama
    dl = DefiLlama.__new__(DefiLlama)
    day = 1_794_787_200
    dl._memo = {("pendle-v2", "dailyRevenue"): {"totalDataChartBreakdown": [
        [day, {"Ethereum": {"Pendle V2": 2500}, "Robinhood Chain": {"Pendle V2": 3}, "Hyperliquid L1": {"Pendle V2": 1.5}}],
        [day + 86400, {"Ethereum": {"Pendle V2": 10}}]]}}
    p = config.PROJECT_BY_NAME["Pendle"]
    out = FetchOutput()
    dl._chain_breakdown(p, "pendle-v2", p["defillama_chain_breakdown"][0], 10_000, out)
    v = out.frame().sort_values("date")["value"].tolist()
    assert v == pytest.approx([4.5, 0.0])             # a chain absent on a day: DefiLlama booked nothing there


def test_a2a_pendles_unpriced_leg_sums_its_markets_on_the_two_chains():
    from fetch.base import FetchOutput
    from fetch.pendle_epochs import PendleEpochs
    mk = lambda c, a, b: {"chainId": c, "protocolFeeUsd": a, "realizedYtFeeUsd": b}  # noqa: E731
    payload = {"epochs": [{"epochStart": "2026-09-08T00:00:00.000Z", "isOngoing": False,
                           "totals": {"protocolFeeUsd": 100.0, "realizedYtFeeUsd": 20.0, "poolFeeUsd": 150.0},
                           "markets": [mk(1, 70, 10), mk(4663, 20, 5), mk(999, 10, 5)]}]}
    out = FetchOutput()
    PendleEpochs(get=lambda url: payload)._market_revenue(
        "Pendle", config.PROJECT_BY_NAME["Pendle"]["spendle_epochs"], pd.Timestamp("2026-11-16"), out)
    f = out.frame()
    assert f[f.metric == "pendle_epoch_market_revenue_unpriced_usd"]["value"].tolist() == [40.0]


def _pendle_long(unpriced=True):
    days = pd.date_range("2026-09-08", periods=28)
    rows = [(d, "revenue_usd", 100.0) for d in days] + [(d, "revenue_usd_defillama", 92.0) for d in days]
    rows += [(pd.Timestamp("2026-09-08"), "pendle_epoch_market_revenue_usd", 1300.0),
             (pd.Timestamp("2026-09-22"), "pendle_epoch_market_revenue_usd", 1300.0)]
    if unpriced:
        rows += [(pd.Timestamp("2026-09-08"), "pendle_epoch_market_revenue_unpriced_usd", 140.0),
                 (pd.Timestamp("2026-09-22"), "pendle_epoch_market_revenue_unpriced_usd", 140.0)]
        rows += [(d, "revenue_usd_defillama_unpriced", 2.0) for d in days]
    return pd.DataFrame([{"date": d, "project": "Pendle", "metric": m, "value": v} for d, m, v in rows])


def test_a2a_the_row_is_judged_like_for_like_and_waits_on_its_legs():
    import credibility as cred
    args = config.CREDIBILITY["Pendle"]["in_revenue"]["ref"]["args"]
    kw = {k: v for k, v in args.items() if k not in ("side", "use_views")}
    asof = pd.Timestamp("2026-10-07")
    a, _d, _h = cred._pendle_basis_vs_defillama("Pendle", {}, _pendle_long(), asof, side="ours", **kw)
    b, _d, how = cred._pendle_basis_vs_defillama("Pendle", {}, _pendle_long(), asof, side="ref", **kw)
    assert a == pytest.approx(2800 - 280) and b == pytest.approx(2576 - 56)
    assert "LIKE FOR LIKE" in how and "Robinhood Chain" in how and "HyperEVM" in how
    assert config.CREDIBILITY["Pendle"]["in_revenue"]["ref"]["tol"] == 10.0          # not widened
    v, _d, why = cred._pendle_basis_vs_defillama("Pendle", {}, _pendle_long(False), asof, side="ref", **kw)
    assert v is None and "unpriced" in why                                         # unknown, never the old basis


# ============================================================ A2b: Fluid's allowance renewed on a new read; Pendle not
def test_a2b_fluids_allowance_is_renewed_with_its_read_and_pendles_lapses():
    hit = "CP SUBSIDY COVER = revenue ÷ $ supply-side incentives (b): 56.5 in the previous run, now BLOCKED"
    a = config.regression_allowance("Fluid", hit, on="2026-12-01")
    assert a and a["until"] == "2026-12-16" and "888.29" in a["reason"] and "rpc.plasma.to" in a["source"]
    assert config.regression_allowance("Fluid", hit, on="2026-12-17") is None          # lapses on its own date
    assert config.regression_allowance("Pendle", hit, on="2026-12-01") is None         # not renewed (chains wired)


# ================================================================= A1: gate speed — slow items marked and run first
def test_a1_slow_tests_are_marked_and_collected_first_and_none_is_dropped():
    import conftest

    class _Item:
        def __init__(self, f, n):
            self.fspath, self.name, self.marks = f, n, []

        def add_marker(self, m):
            self.marks.append(m)
    items = [_Item("/x/tests/test_adapters.py", "test_quick"), _Item("/x/tests/test_round_p3_6.py", "test_a"),
             _Item("/x/tests/test_adapters.py", "test_headline_diff_runs_on_a_store_circulating"),
             _Item("/x/tests/test_r7_round.py", "test_b")]
    before = list(items)
    conftest.pytest_collection_modifyitems(None, None, items)
    assert sorted(map(id, items)) == sorted(map(id, before))                     # nothing dropped
    assert [i.name for i in items[:2]] == ["test_a", "test_headline_diff_runs_on_a_store_circulating"]
    assert all(any(m.name == "slow" for m in i.marks) for i in items[:2])
    assert not any(m.name == "slow" for i in items[2:] for m in i.marks)


# ================================================================================== B1: Sky's endowments — stopped
def test_b1_the_endowment_evidence_is_kept_and_jakes_decision_counts_them_in():
    # R7: stopped on the SFF report's lock; R7B.B1 (Jake 2026-11-16): that is the Foundation's own pledge, not a release
    # restriction — the endowments are circulating, and the evidence stays cited on the note
    ev = config.SKY_ENDOWMENT_LOCK_EVIDENCE
    assert "locked and is not available for sale or distribution" in ev["quote"] and ev["url"].startswith("https://")
    assert not config.CIRCULATING_ONCHAIN["Sky"].get("subtract_when_complete")
    assert "SFF Q1 2026" in config.CIRCULATING_ONCHAIN["Sky"]["endowment_note"]
    assert config.unlock_stock_metrics("Sky") == () and config.outflow_metrics("Sky", "e") == ()


# ====================================================== B2: the backing's history, live refs, the USDS-farm rate
def test_b2_the_mkr_backing_history_reads_each_missing_day_at_its_block(monkeypatch):
    import fetch.sky_mkr as sm
    monkeypatch.setattr(sm, "today", lambda: pd.Timestamp("2026-11-16"))
    spec = config.PROJECT_BY_NAME["Sky"]["mkr_backing"]
    word = lambda v: "0x" + hex(int(v))[2:].rjust(64, "0")  # noqa: E731

    def call(to, data, block):
        if to == spec["converter"]:
            return word(24_000) if data.startswith("0x2c4e722e") else word(5e16)          # rate() else fee()
        return word(82_664.63e18) if data.startswith("0x18160ddd") else word(49.56e18)

    class DB:
        def at(self, day, head):
            return 26_000_000 - (pd.Timestamp("2026-11-16") - day).days * 7200
    rows, why = sm.backing_history(config.PROJECT_BY_NAME["Sky"], None, DB(), 3, {"2026-11-14"}, 26_000_000,
                                   call=call)
    f = pd.DataFrame(rows)
    back = f[f.metric == "sky_mkr_nonissuer_backing_tokens"]
    assert sorted(back["date"].astype(str).str[:10]) == ["2026-11-13", "2026-11-15"]     # the stored day skipped
    assert back["value"].iloc[0] == pytest.approx((82_664.63 - 49.56) * 24_000 * 0.95, rel=1e-9)
    assert why == "reached the start of the window" and back["source"].iloc[0].endswith(":archive")


def test_b2_four_sky_rows_judge_against_the_live_reference_not_jakes_10_07_reading():
    for k in ("in_locked_sky_farm", "in_locked_usds_farm", "in_apy_sky_farm", "in_apy_usds_farm", "a3_protocol_yield"):
        assert ("Sky", k) in config.LIVE_REFERENCE_OVER_MANUAL
    import manual_refs
    assert ("Sky", "in_apy_sky_farm") in manual_refs.by_row()                              # Jake's reading kept
    src = open(ROOT / "credibility.py", encoding="utf-8").read()
    assert src.count("not in config.LIVE_REFERENCE_OVER_MANUAL") == 2                     # both reading paths


def test_b2_the_usds_farm_rate_is_judged_against_the_same_28_days():
    import credibility as cred
    days = pd.date_range("2026-10-19", "2026-11-15")
    vals = [0.048 + 0.0185 * i / (len(days) - 1) for i in range(len(days))]           # 4.8% -> 6.65%, spot rises
    long = pd.DataFrame({"date": days, "project": "Sky", "metric": "usds_farm_apy_ba", "value": vals})
    v, d, how = cred._window_mean("Sky", {}, long, pd.Timestamp("2026-11-16"), metric="usds_farm_apy_ba", days=28)
    assert v == pytest.approx(sum(vals) / 28) and d == "2026-11-15" and "28 daily readings" in how
    v, _d, why = cred._window_mean("Sky", {}, long.iloc[1:], pd.Timestamp("2026-11-16"), metric="usds_farm_apy_ba",
                                   days=28)
    assert v is None and "27 of 28" in why
    for k in ("in_apy_usds_farm", "a3_protocol_yield"):
        ref = config.CREDIBILITY["Sky"][k].get("ref", config.CREDIBILITY["Sky"][k])
        assert ref["formula"] == "window_mean" and ref["tol"] == 10.0                     # tolerance unchanged


# ================================================================================ C1: Hyperliquid's circulating
def _hg(metric, rows):
    return pd.DataFrame({"date": pd.to_datetime([d for d, _v, _s in rows]), "project": "Hyperliquid", "metric": metric,
                         "value": [float(v) for _d, v, _s in rows], "tier": 1, "source": [s for _d, _v, s in rows]})


def test_c1_the_foundation_and_grants_stake_is_released_and_their_spot_stays_out():
    import build_workbook as bw
    raw, net = "hypercore_info:tokenDetails.circulatingSupply-hyperevm_burn", \
        "hypercore_info:tokenDetails.circulatingSupply-exclude_wallets-hyperevm_burn"
    g = {("Hyperliquid", "circulating_supply_first_party"): _hg("circulating_supply_first_party", [
            ("2026-10-10", 302_000_000, raw), ("2026-10-12", 238_500_000, net), ("2026-10-13", 238_600_000, net)]),
         ("Hyperliquid", "excluded_wallets_tokens"): _hg("excluded_wallets_tokens", [
             ("2026-10-10", 63_500_000, "x"), ("2026-10-12", 63_500_000, "x"), ("2026-10-13", 63_600_000, "x")]),
         ("Hyperliquid", "staked_noncirculating_tokens"): _hg("staked_noncirculating_tokens", [
             ("2026-10-10", 301_300_000, "x"), ("2026-10-12", 301_300_000, "x"), ("2026-10-13", 301_400_000, "x")]),
         ("Hyperliquid", "team_wallet_tokens"): _hg("team_wallet_tokens", [
             ("2026-10-10", 237_900_000, "x"), ("2026-10-12", 237_900_000, "x")])}           # 10-13: no team read
    bw._released_staked_views(g)
    c = g[("Hyperliquid", "circulating_supply_first_party")].set_index("date")["value"]
    spot = 63_500_000 - (301_300_000 - 237_900_000)                                       # 100,000 unstaked
    assert c[pd.Timestamp("2026-10-10")] == pytest.approx(302_000_000 - spot)            # raw day: their spot out
    assert c[pd.Timestamp("2026-10-12")] == pytest.approx(238_500_000 + 63_400_000)      # net day: their stake back
    assert pd.Timestamp("2026-10-13") not in c.index                                      # a leg missing: no value
    assert g[("Hyperliquid", "circulating_supply_first_party")]["source"].nunique() == 1  # one basis
    assert config.unlock_stock_metrics("Hyperliquid") == ("excluded_wallets_spot_tokens", "team_wallet_tokens")
    assert config.locked_excluded_metrics("Hyperliquid") == ("team_wallet_tokens",) or \
        tuple(config.locked_excluded_metrics("Hyperliquid")) == ("team_wallet_tokens",)


def test_c1_the_classification_names_every_gap_over_two_percent():
    r = config.HYPERLIQUID_CIRC_R7
    assert r["wallets"]["foundation"][0] > 6e7 and r["wallets"]["assistance_fund"][0] == 0.0
    for ref in ("coingecko", "coinmarketcap"):
        assert abs(r["ours_estimate"] / r[ref] - 1) > 0.02
    assert "60.63M" in r["gaps"] and "2.87M" in r["gaps"] and "not established" in r["gaps"]
    assert "DERIVED" in r["team_schedule"] and "9,722,176" in r["team_schedule"]


# ===================================================================================== C2: emissions, option C
def test_c2_one_emissions_figure_and_option_c_judged_on_the_read_to_read_basis():
    import build_workbook as bw
    assert "SECURITY EMISSIONS" in config.EMISSIONS_ARE_INCENTIVE_SPEND["Hyperliquid"]
    assert "Hyperliquid" in config.OPTION_C_INTERVAL_BASIS
    import inspect
    assert "interval_basis" in inspect.signature(bw.hl_option_c).parameters


# ============================================================================ C3 / C4: S13 lines and the checks
def test_c3_s13_lines_follow_jakes_decisions():
    rs = config.REVENUE_SPLIT["Hyperliquid"]
    # R7B.C2: REVENUE is every stream that funds the Fund (revenue_total_usd), the AQAv2 yield counted once
    assert rs["fees"]["metric"] == "fees_usd" and rs["holder_revenue"]["metric"] == "holder_value_revenue_usd"
    assert rs["revenue"]["metric"] in ("revenue_usd", "revenue_total_usd")
    assert not rs["fees"]["primary"] and "AccessDenied" in rs["fees"]["route"] and rs["holder_revenue"]["primary"]
    assert "hip-3 deployers" in rs["revenue"]["route"].lower()                 # the named difference


def test_c4_burns_and_revenue_judge_on_hyperliquids_own_data_and_asxn_is_beside():
    import credibility as cred
    cr = config.CREDIBILITY["Hyperliquid"]
    assert cr["a4_gross_burn"]["formula"] == "delta_q0" and cr["a4_gross_burn"]["tol"] == 15.0
    assert cr["a1_gross_burn"]["args"]["metrics"] == ["af_noncirc_listed_tokens", "hyperevm_zero_address_balance"]
    assert cr["in_revenue"]["ref"]["args"]["metric"] == "actual_buyback_usd" and cr["in_revenue"]["ref"]["tol"] == 15.0
    for k in ("in_a4_gross_burn_asxn", "in_a1_gross_burn_asxn", "in_revenue_asxn"):
        assert cr[k]["ref"]["force_verdict"] == "N/A (recorded)" and cr[k]["ref"]["tol"] is None
    d = pd.date_range("2026-07-01", "2026-10-13")
    long = pd.concat([pd.DataFrame({"date": d, "project": "Hyperliquid", "metric": m,
                                    "value": [b + 10.0 * i for i in range(len(d))]})
                      for m, b in (("af_noncirc_listed_tokens", 4.8e7), ("hyperevm_zero_address_balance", 3e5))])
    v, _d, how = cred._sum_of_deltas_q0("Hyperliquid", {}, long, pd.Timestamp("2026-10-13"),
                                        metrics=["af_noncirc_listed_tokens", "hyperevm_zero_address_balance"])
    one, _d1, _h = cred._delta_q0("Hyperliquid", {}, long, pd.Timestamp("2026-10-13"), metric="af_noncirc_listed_tokens")
    assert v == pytest.approx(2 * one) and " + " in how
