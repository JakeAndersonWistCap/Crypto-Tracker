"""Round 17 (Jake 2026-11-23) — GEODNET on the circulating rule; Plume's RWA read (SPEC "Round 17 — GEODNET, Aethir,
Plume"). Behaviour and tolerances, not wording."""
from __future__ import annotations

import pandas as pd
import pytest

import config
from fetch import circulating as circ
from fetch import llama
from fetch.base import FetchOutput
from fetch.llama import DefiLlama
from fetch.logcache import DailyChecks

G = "GEODNET"
READ = config.GEODNET_RULE_READ
DAY = "2026-11-23"
TOL = config.CIRCULATING_TOLERANCE


def _h(values: dict, day: str = DAY, project: str = G) -> pd.DataFrame:
    return pd.DataFrame([{"date": pd.Timestamp(day), "project": project, "metric": m, "value": float(v), "tier": 2,
                          "source": "t"} for m, v in values.items()])


def _legs() -> dict:
    by = {}
    for _role, _addr, tokens, _who, metric in READ["wallets"]:
        by[metric] = by.get(metric, 0.0) + tokens
    return by


LIVE = {"total_supply_gross": READ["total"], "burn_address_balance": sum(READ["burned"].values()), **_legs()}


# =========================================================================== R17.1 GEODNET on the rule
def test_1_every_subtracted_wallet_can_be_moved_so_the_rule_counts_it_in():
    assert len(READ["wallets"]) == 7
    for role, _addr, _tokens, who, metric in READ["wallets"]:
        assert "2-of-3" in who or "one key" in who, role                    # a Safe's signers or a key: movable
        assert metric in config.rule_in_legs(G), role
    assert set(config.RULE_IN[G]["off_float"]) == set(config.rule_in_legs(G))   # in circulating, OFF the float
    # the legs stay the project's issuer-side set (the unlock legs and the references still read them)
    assert set(config.rule_in_legs(G)) <= set(config.CIRCULATING_ONCHAIN[G]["subtract"])
    assert config.UNLOCK_STOCKS[G] == ("noncirculating_holding_tokens", "ecosystem_holding_tokens")   # R9.3: unchanged


def test_1_circulating_is_total_less_the_burn_only():
    h = _h(LIVE)
    ours = circ.series(h, G).iloc[-1]
    assert ours == pytest.approx(READ["total"] - sum(READ["burned"].values()), abs=1e-6)
    assert ours == pytest.approx(READ["ours"], abs=1.0)                      # 926,216,062.51
    # the old basis is the like-for-like figure (rule=False): what Jake's store read on 2026-11-23, to the token
    assert circ.series(h, G, rule=False).iloc[-1] == pytest.approx(READ["ours_before"], abs=1.0)
    assert config.RULE_IN[G]["circulating"] == (524_985_588, 926_216_062.51)
    # burned stays out of circulating AND of total
    assert config.classify_position(burned=True)["in_total"] is False
    assert "burn_address_balance" in config.CIRCULATING_ONCHAIN[G]["subtract"]
    assert "burn_address_balance" not in config.rule_in_legs(G)


def test_1_the_float_does_not_move():
    legs = _legs()
    old_float_base = READ["ours_before"]                                      # circulating before, nothing more off it
    new_float_base = READ["ours"] - sum(legs[m] for m in config.RULE_IN[G]["off_float"])
    assert new_float_base == pytest.approx(old_float_base, abs=1.0)


def test_1_the_change_of_basis_is_a_declared_handover():
    ho = config.CIRCULATING_ONCHAIN[G]["handover"]
    assert ho["on"] == "2026-11-23" and "restated" in config.RULE_IN[G]
    assert "524,985,588" in ho["from"] and "926,216,062.51" in ho["to"]


# =========================================================================== R17.2 the CoinGecko gap, by wallet
def test_2_the_gap_to_coingecko_is_named_by_wallet_to_the_token():
    cg = READ["coingecko"]
    assert cg["total"] == pytest.approx(READ["ours"], abs=0.01)              # CoinGecko's total = ours under the rule
    assert cg["circulating"] + sum(cg["leaves_out"].values()) == pytest.approx(READ["ours"], abs=0.01)
    ref, named = config.RULE_IN[G]["references"]["coingecko"]
    assert ref + sum(named.values()) == pytest.approx(READ["ours"], abs=0.01)
    # the gap Jake's store showed (ours before − CoinGecko), by wallet: far inside the 2% asked for
    og = READ["old_gap"]
    gap = cg["circulating"] - READ["ours_before"]
    named_gap = sum(og["coingecko_counts_we_subtracted"].values()) - sum(og["coingecko_leaves_out_we_counted"].values())
    assert abs(named_gap - gap) <= 0.02 * abs(gap)
    assert abs(named_gap - gap) < 2.0                                        # in fact to the token


def test_2_the_daily_check_logs_the_declared_difference_and_reviews_a_figure_off_it():
    p = config.PROJECT_BY_NAME[G]
    cg = READ["coingecko"]["circulating"]
    d = circ.rule_basis_difference(_h(LIVE), G, cg, pd.Timestamp(DAY))
    assert d and abs(d["diff"]) < 1e-6 and d["ours_rule"] == pytest.approx(READ["ours"], abs=1.0)
    out = FetchOutput()
    circ.check(out, _h({**LIVE, "circulating_supply": cg}), [p])
    assert not [r for r in out.review if r["reason"] == "onchain_vs_coingecko"]
    assert any("rule-basis difference" in str(e.message) for e in out.log if e.project == G)
    # CoinGecko back on some other basis (its old static figure): beyond the SAME tolerance — a review row again
    out = FetchOutput()
    circ.check(out, _h({**LIVE, "circulating_supply": 600_000_000.0}), [p])
    assert [r for r in out.review if r["reason"] == "onchain_vs_coingecko"]
    # the wallets moved by more than the tolerance (the Mining Safe paid out 30M): reviewed again, never absorbed
    moved = {**LIVE, "treasury_holding_tokens": LIVE["treasury_holding_tokens"] - 30_000_000.0,
             "circulating_supply": cg + 30_000_000.0}
    out = FetchOutput()
    circ.check(out, _h(moved), [p])
    assert [r for r in out.review if r["reason"] == "onchain_vs_coingecko"]
    assert TOL == 0.02                                                       # the tolerance is the one it was


def test_2_the_declared_check_is_geodnets_only():
    assert [n for n, s in config.RULE_IN.items() if s.get("reference_check") == "declared"] == [G]
    assert circ.rule_basis_difference(_h({"total_supply": 1e9}, project="Uniswap"), "Uniswap", 6e8,
                                      pd.Timestamp(DAY)) is None


def test_2_credibility_judges_the_rule_figure_like_for_like(tmp_path, monkeypatch):
    from real_dump import evaluate_real
    o = evaluate_real(tmp_path, monkeypatch, G)
    assert str(o["in_circ"]["verdict"]).startswith("PASS")
    # ours on the row is the like-for-like figure (the rule's stock taken off), inside 2% of the recorded reconciliation
    rec = config.CIRCULATING_ONCHAIN[G]["onchain_2026_10_07"]["circulating"]
    assert abs(float(o["in_circ"]["ours"]) / rec - 1) <= 0.02
    # the free float is the figure it was on the dump (513,407,599.81); the retirement rate now divides by the rule's
    # circulating (was 0.0770 on total − burned − the seven wallets)
    assert float(o["a2_free_float"]["ours"]) == pytest.approx(513_407_599.81, rel=1e-6)
    assert float(o["a3_circ_retirement"]["ours"]) == pytest.approx(0.04274, abs=5e-5)


# =========================================================================== R17.3 Plume's RWA read
MID = 1_795_305_600                         # 2026-11-22 00:00:00 UTC
PTS = {  # /protocol/{slug} chainTvls['Plume Mainnet']['tvl'] as read 2026-11-23 (last points)
    "realtyx": [(MID - 86_400, 474_247), (MID, 473_910), (MID + 86_400, 473_783), (MID + 86_400 + 33_131, 467_059)],
    "harvest-flow": [(MID - 86_400, 514_586), (MID, 514_217), (MID + 75_791, 514_109)],
    "spice-protocol": [(MID - 86_400, 1_129), (MID, 1_128), (MID + 86_400, 1_125), (MID + 86_400 + 27_611, 1_107)],
    "fractit-fleet": [(MID - 86_400, 9), (MID, 9), (MID + 86_400, 9), (MID + 86_400 + 20_051, 9)],
}
CHAIN = "Plume Mainnet"


class _Http:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def get(self, url, params=None, **_):
        self.calls.append(url)
        if url.endswith("/protocols"):
            return [{"slug": s, "category": "RWA", "chains": [CHAIN], "chainTvls": {CHAIN: float(pts[-1][1])}}
                    for s, pts in PTS.items()]
        slug = url.rsplit("/", 1)[-1]
        if slug in self.fail:
            raise RuntimeError("timeout")
        return {"chainTvls": {CHAIN: {"tvl": [{"date": t, "totalLiquidityUSD": v} for t, v in PTS[slug]]}}}


def _llama(tmp_path, stored=True, fail=()):
    st = (pd.DataFrame([{"project": "Plume", "metric": "rwa_defillama_usd", "date": pd.Timestamp("2026-10-01"),
                         "value": 1.0}]) if stored else None)
    d = DefiLlama(stored_long=st, part="tvl")
    d.daily = DailyChecks(tmp_path)
    d.http = _Http(fail)
    d._today = "2026-11-23"
    return d


def test_3_the_timestamp_fixture_is_the_day_it_says():
    assert str(pd.Timestamp(MID, unit="s")) == "2026-11-22 00:00:00"


def test_3_one_value_a_day_per_protocol_the_current_point_is_not_added_to_the_daily_one():
    pts = [{"date": t, "totalLiquidityUSD": v} for t, v in PTS["harvest-flow"]]
    daily = DefiLlama._daily_tvl(pts)
    assert daily[pd.Timestamp("2026-11-22")] == 514_217                      # was 514,217 + 514,109
    # a day whose only point is the current one keeps it; `before` leaves today out
    assert DefiLlama._daily_tvl([{"date": MID + 86_400 + 5, "totalLiquidityUSD": 7.0}]) == {pd.Timestamp("2026-11-23"): 7.0}
    assert pd.Timestamp("2026-11-23") not in DefiLlama._daily_tvl(
        [{"date": t, "totalLiquidityUSD": v} for t, v in PTS["realtyx"]], before=pd.Timestamp("2026-11-23"))


def test_3_the_history_build_reads_989_156_for_the_day_that_read_1_503_373(tmp_path):
    d = _llama(tmp_path)
    f = d._build_rwa_by_chain({CHAIN})
    by = {str(r.date.date()): r.value for r in f.itertuples()}
    assert by["2026-11-22"] == pytest.approx(473_910 + 514_217 + 1_128 + 9)  # 989,264: one point a protocol
    assert by["2026-11-22"] < 1_000_000 < 1_503_373                          # the doubled read is gone
    assert "2026-11-23" not in by                                            # today is not over: not in the history


def test_3_a_protocol_that_cannot_be_read_stores_no_past_day(tmp_path, monkeypatch):
    monkeypatch.setattr(llama, "window", lambda f, w: f)
    monkeypatch.setenv("TOKEN_METRICS_DAILY_CHECKS", "")
    monkeypatch.setattr(config, "RWA_ISSUERS", {})        # R18: the category read, as a chain without an issuer list
    d = _llama(tmp_path, stored=False, fail=("harvest-flow",))
    out = FetchOutput()
    d.rwa([config.PROJECT_BY_NAME["Plume"]], 30, out)
    f = out.frame()
    days = set() if f is None or f.empty else {str(x.date()) for x in pd.to_datetime(f["date"])}
    assert days <= {"2026-11-23"}                                            # today's one-call sum at most; no past day
    assert any(e.status != "ok" and "could not be read" in str(e.message) for e in out.log)


def test_3_plumes_window_is_rebuilt_once_then_one_call_a_day(tmp_path, monkeypatch):
    monkeypatch.setattr(llama, "window", lambda f, w: f)
    monkeypatch.setenv("TOKEN_METRICS_DAILY_CHECKS", "")
    assert "Plume" in config.RWA_REREAD_ONCE
    monkeypatch.setattr(config, "RWA_ISSUERS", {})        # R18: the category read, as a chain without an issuer list
    plume = config.PROJECT_BY_NAME["Plume"]
    d = _llama(tmp_path)
    out = FetchOutput()
    d.rwa([plume], 30, out)
    f = out.frame()
    by = {str(pd.Timestamp(r.date).date()): r.value for r in f.itertuples()}
    assert by["2026-11-22"] == pytest.approx(989_264) and len(d.http.calls) == 1 + len(PTS)
    # today's row is the one-call sum of /protocols (every protocol's current figure, once each)
    assert by["2026-11-23"] == pytest.approx(467_059 + 514_109 + 1_107 + 9)
    d2 = _llama(tmp_path)
    d2.daily.done("defillama:rwa", "2026-11-22")                             # another day: the daily read is due again
    out2 = FetchOutput()
    d2.rwa([plume], 30, out2)
    assert d2.http.calls == [c for c in d2.http.calls if c.endswith("/protocols")] and len(d2.http.calls) == 1


# =========================================================================== R17.5 GEODNET's three revenue lines
def test_5_geodnets_lines_are_series_already_on_file_and_say_what_is_inferred():
    s = config.revenue_split(G)
    assert s and {s["fees"]["metric"], s["revenue"]["metric"], s["holder_revenue"]["metric"]} <= set(config.METRICS)
    assert s["holder_revenue"]["primary"] is True                            # the burn, measured on chain
    # R18 (Jake 2026-11-24): revenue = the burn ÷ 0.8, fees = the burn ÷ 0.4 — both inferred (tests/test_r18_geodnet.py)
    assert s["fees"]["primary"] is False and s["revenue"]["primary"] is False
    assert config.REVENUE_TIER[G][0] == "revenue"                            # the existing cell's line is unchanged
    assert G in config.REVENUE_IMPLIED                                        # still labelled implied from the burn


def test_5_defillamas_own_three_series_restate_the_declared_80_percent():     # R18: the cross-check, not the lines
    from real_dump import load
    rows = pd.DataFrame(load(G)["metrics"])
    rows = rows[rows["source"] == "defillama"]
    by = {m: rows[rows["metric"] == m].set_index("date")["value"] for m in ("fees_usd", "revenue_usd", "holders_revenue_usd")}
    days = by["revenue_usd"].index.intersection(by["holders_revenue_usd"].index)
    assert len(days) > 300
    assert (by["fees_usd"].reindex(days) - by["revenue_usd"].reindex(days)).abs().max() < 0.01     # ONE series
    share = by["holders_revenue_usd"].reindex(days).sum() / by["revenue_usd"].reindex(days).sum()
    assert share == pytest.approx(config.PROJECT_BY_NAME[G]["fee_split"]["share_to_buyback"], abs=1e-5)   # 0.80: restated


# =========================================================================== R17 the three probes are registered
def test_6_the_live_reads_are_checks_jake_can_run():
    import check_offline_items as coi
    for fn in (coi.geod_rule_read, coi.plume_rwa_points, coi.plume_payout_weeks):
        assert fn in coi.CHECKS
