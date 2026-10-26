"""ROUND D1 (Jake 2026-10-26, head 3de3ba9) — the data phase, round 1.

1 NEAR DEX volume / Intents fees: a partial DefiLlama day is refused, the stale rows cleaned; 2 Morpho Merkl Base: the
campaign total compared like for like; 4 Pendle's published sPENDLE APR closed by design.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

T = lambda d: int(pd.Timestamp(d).timestamp())  # noqa: E731


# ================================================================================================== 1
def _llama(monkeypatch, payloads):
    from fetch import llama
    calls = []

    class H:
        def get(self, url, params=None):
            calls.append((url, dict(params or {})))
            return payloads(url, params or {})
    d = llama.DefiLlama.__new__(llama.DefiLlama)
    d.http = H()
    monkeypatch.setattr(llama, "today", lambda: pd.Timestamp("2026-10-26"))
    return d, calls


def test_1_a_near_day_without_near_intents_is_refused_not_stored(monkeypatch):
    from fetch.base import FetchOutput
    days = ["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"]
    tot = [167.8e6, 142.1e6, 8.27e6, 9.37e6, 4.44e6]
    bd = [{"NEAR Intents": 151.5e6, "Rhea Dex": 10.6e6}, {"NEAR Intents": 132.3e6, "Rhea Dex": 9.2e6},
          {"Rhea Dex": 8.2e6}, {"Rhea Dex": 9.3e6}, {"Rhea Dex": 4.4e6}]
    d, calls = _llama(monkeypatch, lambda url, p: {
        "chain": "Near", "totalDataChart": [[T(x), v] for x, v in zip(days, tot)],
        "totalDataChartBreakdown": [[T(x), b] for x, b in zip(days, bd)]})
    out = FetchOutput()
    d.trading_volume(config.PROJECT_BY_NAME["Near"], out)
    assert calls[0][1]["excludeTotalDataChartBreakdown"] == "false"
    f = [x for x in out.frames if x is not None and not x.empty][0]
    assert [str(x.date()) for x in pd.to_datetime(f["date"])] == ["2026-10-21", "2026-10-22"]
    assert any("REFUSED 3 day(s) — NEAR Intents absent" in e.message for e in out.log)


def test_1_a_fees_day_below_its_revenue_is_refused(monkeypatch):
    from fetch.base import FetchOutput
    fees = {"2026-10-21": 209_714.0, "2026-10-22": 11_925.0}
    rev = {"2026-10-21": 63_787.0, "2026-10-22": 58_819.0}
    d, _calls = _llama(monkeypatch, None)

    def chart(slug, dt):
        if slug != "near-intents":
            return [(pd.Timestamp("2026-10-21"), 1.0)]
        return [(pd.Timestamp(k), v) for k, v in (rev if dt == "dailyRevenue" else fees).items()]
    monkeypatch.setattr(d, "_summary_chart", chart)
    monkeypatch.setattr(d, "_absent", lambda *a, **k: False)
    monkeypatch.setattr(d, "_stored", lambda n, m: True)
    monkeypatch.setattr(d, "_history_days", lambda: 455)
    d.known_absent, d._today = set(), "2026-10-26"
    d.daily = type("D", (), {"get": lambda self, k: "", "set": lambda self, k, v: None})()
    p = {"name": "Near", "defillama_fees_slug": "near",
         "defillama_component_slugs": config.PROJECT_BY_NAME["Near"]["defillama_component_slugs"]}
    out = FetchOutput()
    d.fees(p, 3650, out)
    fr = {f["metric"].iloc[0]: f for f in out.frames if f is not None and not f.empty}
    got = fr["intents_fees_usd"].set_index(pd.to_datetime(fr["intents_fees_usd"]["date"]))["value"]
    assert list(got.index.strftime("%Y-%m-%d")) == ["2026-10-21"]           # 10-22 (fees < revenue) refused
    assert len(fr["intents_revenue_usd"]) == 2                               # the revenue days stand
    assert any("REFUSED 1 day(s) where intents_fees_usd < intents_revenue_usd" in e.message for e in out.log)


def test_1_the_stale_rows_are_cleaned_by_declared_clean_ups():
    ups = [c for c in config.RUN_CLEANUPS if c["id"] == "D1-1"]
    assert {(c["metric"], c["date"], c["source"]) for c in ups} == {
        ("dex_volume_usd", "2026-10-23", "defillama:dexs:near"), ("dex_volume_usd", "2026-10-24", "defillama:dexs:near"),
        ("dex_volume_usd", "2026-10-25", "defillama:dexs:near"),
        ("intents_fees_usd", "2026-10-22", "defillama:near-intents")}


# ================================================================================================== 2
def test_2_the_merkl_campaign_total_is_compared_like_for_like():
    import references as rf
    route = rf.ROUTES[("Morpho", "g6_merkl_base_campaign_total")]
    assert route == ("mean_period_x", "merkl_campaigns_non_ethereum_tokens", "2026-09-22", "2026-10-20", 30)
    # the repo's own daily series around the campaign: 6,645.954 a day inside it, 6,713.77 on 10-21 (boundary)
    d = pd.date_range("2026-09-15", "2026-10-25")
    v = [6645.954 if x <= pd.Timestamp("2026-10-20") else 6713.77 if x == pd.Timestamp("2026-10-21") else 6917.26
         for x in d]
    g = pd.DataFrame({"date": d, "project": "Morpho", "metric": route[1], "value": v, "source": "merkl_campaigns",
                      "tier": 1})
    got, how, err = rf._our_value(route, {("Morpho", route[1]): g}, "Morpho", pd.Timestamp("2026-10-19"), 199_378.0)
    assert not err and got == pytest.approx(199_378.62, abs=0.1) and "wholly inside" in how
    # the old UTC-day sum took in the neighbours' boundary hours: +6,714 (BROKEN at 2%)
    old = rf._our_value(("sum_period", route[1], "2026-09-21", "2026-10-21"), {("Morpho", route[1]): g}, "Morpho",
                        pd.Timestamp("2026-10-19"), 199_378.0)[0]
    assert old == pytest.approx(199_378.62 + 6645.954 + 6713.77 - 6645.954, abs=0.1)


# ================================================================================================== 4
def test_4_pendle_published_apr_is_closed_by_design_with_its_evidence():
    import coverage_audit as ca
    for m in ("staking_apr_published", "pendle_epoch_apr_published"):
        u = config.UNAVAILABLE_BY_KEY[("Pendle", m)]
        assert u["cell_text"].startswith("n/a — by design: Pendle publishes no sPENDLE APR")
        assert "20 epochs" in u["cell_text"] and "72 reads" in u["cell_text"]
        assert ("Pendle", m) not in config.MISSING_DECLARED
        assert not ca.DISHONEST_NA.search(u["cell_text"])


# ================================================================================================== 3
def test_3_a_pre_minted_token_reads_n_a_for_issuance_at_every_quarter(tmp_path, monkeypatch):
    """Hyperliquid's fees ÷ issuance at Q1-Q3 and self-reported − derived read MISSING C ("needs history") on a series
    config declares n/a since 2026-09-28 (HYPE is pre-minted): never a wait — n/a, as Q0 already read."""
    import xlcalc
    from test_overnight_ab5f2a9 import _get, _row
    from test_trajectory import _built
    wb, _ = _built(tmp_path, monkeypatch, "Hyperliquid")
    X = xlcalc.Workbook(wb)
    o = _row(wb, X, "A1 Infrastructure", "Hyperliquid")
    for h in ("FEES ÷ ISSUANCE (x) — key ratio", "Fees ÷ issuance at Q1 (−3m window)", "Fees ÷ issuance at Q2 (−6m)",
              "Fees ÷ issuance at Q3 (−9m)"):
        assert str(_get(o, h)).startswith("n/a — no issuance to divide by"), h
    o = _row(wb, X, "A4 Permanent Burn", "Hyperliquid")
    assert str(_get(o, "Self-reported − derived (a gap here means one of the two is wrong)")).startswith(
        "n/a — nothing issued and nothing self-reported")


def _camp(cid, amount, start, end, **kw):
    return {"campaignId": cid, "amount": str(int(amount * 10 ** 18)), "startTimestamp": T(start), "endTimestamp": T(end),
            "distributionChainId": 8453, "rewardToken": {"address": "0xTOK", "symbol": "MORPHO", "decimals": 18},
            "creatorAddress": "0xCREATOR", **kw}


def test_2_sub_campaigns_are_not_counted_and_a_cancelled_one_counts_what_was_paid():
    from fetch.merkl_campaigns import daily_amounts
    camps = [_camp("0xroot", 300, "2026-01-01", "2026-01-04"),
             _camp("0xchild", 120, "2026-01-01", "2026-01-04", parentCampaignId="123"),       # forwarder share
             _camp("0xcancel", 100, "2026-01-05", "2026-01-06", type="INVALID")]
    per_day, skipped = daily_amounts(camps, "0xtok", pd.Timestamp("2026-02-01").date(), 8453, set(),
                                     {"0xcancel": 80 * 10 ** 18})
    assert sum(per_day.values()) == pytest.approx(300 + 20)            # child out; cancelled: 100 − 80 refunded
    assert any(c == "0xchild" and "sub-campaign" in r for c, r in skipped)
    per_day, skipped = daily_amounts(camps, "0xtok", pd.Timestamp("2026-02-01").date(), 8453, set(), {})
    assert sum(per_day.values()) == pytest.approx(300)                 # refund not read: not counted, and said
    assert any(c == "0xcancel" and "refund" in r for c, r in skipped)


def test_2_a_failed_refund_read_stores_nothing():
    from fetch.base import FetchOutput
    from fetch.merkl_campaigns import MerklCampaigns

    class H:
        def get(self, url, params=None):
            if "/users/" in url:
                return {"error": "down"}
            return [_camp("0xcancel", 100, "2026-01-05", "2026-01-06", type="INVALID")] if params.get("page") == 0 else []

    class Dc:
        def due(self, *a):
            return True

        def done(self, *a):
            pass
    m = MerklCampaigns(http=H(), daily_checks=Dc())
    out = FetchOutput()
    spec = {"url": "https://api.merkl.xyz/v4/campaigns", "creator": "0xCREATOR", "metric": "x",
            "chains": {8453: {"name": "Base", "token": "0xTOK"}}}
    m._project("Morpho", spec, out)
    assert not [f for f in out.frames if f is not None and not f.empty]
    assert any("NOTHING STORED" in e.message and "no refund amount" in e.message for e in out.log)
