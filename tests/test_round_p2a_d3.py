"""ROUND P2a D3 (Jake 2026-10-27) — Merkl computedUntil.

Jake's run on D4: Plume's (b) (merkl_campaigns_plume_tokens, Ethereum campaign 0x09a36c8a…) and Morpho's Merkl series
(merkl_campaigns_non_ethereum_tokens, Base campaign 0x05f987b9…) stored NOTHING — one running campaign "had no
computedUntil" and the whole chain raised. The cause: GET /v4/campaigns returns the compute status only with
withComputeStatus=true (Merkl's OpenAPI). Now: the list asks for it; a running campaign still without computedUntil does
not void its chain — it is not counted (never 0, no accrual guessed), named PARTIAL with its size, and the days it runs
on are HELD (not stored, on the provisional list); Plume's read has its own tier and budget. Fakes only, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

T = lambda d: int(pd.Timestamp(d, tz="UTC").timestamp())  # noqa: E731
E18 = 10 ** 18
URL = "https://api.merkl.xyz/v4/campaigns"


def _camp(cid, amount, start, end, chain=8453, token="0xTOK", sym="MORPHO", **kw):
    return {"campaignId": cid, "amount": str(int(amount * E18)), "startTimestamp": T(start), "endTimestamp": T(end),
            "distributionChainId": chain, "creatorAddress": "0xCREATOR", "distributionType": "DUTCH_AUCTION",
            "rewardToken": {"address": token, "symbol": sym, "decimals": 18}, **kw}


class _Dc:
    def __init__(self):
        self.done_keys = []

    def due(self, *a):
        return True

    def done(self, key, *a):
        self.done_keys.append(key)


class _Http:
    """Campaign lists by chain; every allocated total from `totals`; nothing credited back to the creator."""

    def __init__(self, lists, totals, clock=None, step=0.0):
        self.lists, self.totals, self.calls, self.params = lists, totals, [], []
        self.stats = {"calls": 0}
        self.clock, self.step = clock, step

    def get(self, url, params=None):
        self.calls.append(url)
        self.params.append(dict(params or {}))
        self.stats["calls"] += 1
        if self.clock is not None:
            self.clock.t += self.step
        if "/users/" in url:
            return {"amount": "0", "breakdowns": []}
        if url.endswith("/rewards/total"):
            return {"amount": str(int(self.totals[params["campaignId"]] * E18))}
        return self.lists.get(int(params["chainId"]), []) if params.get("page") == 0 else []


def _reader(monkeypatch, tmp_path, lists, totals, now="2026-01-10 12:00", **kw):
    from fetch import merkl_campaigns as mc
    monkeypatch.setattr(mc, "_now", lambda: T(now))
    monkeypatch.setattr(mc, "today", lambda: pd.Timestamp(pd.Timestamp(now).date()))
    http = _Http(lists, totals)
    dc = _Dc()
    return mc.MerklCampaigns(http=http, daily_checks=dc, cache_path=tmp_path / "c.json", **kw), http, dc


def _series(out):
    frames = [f for f in out.frames if f is not None and not f.empty]
    if not frames:
        return {}
    f = frames[0]
    return f.assign(date=pd.to_datetime(f["date"]).dt.strftime("%Y-%m-%d")).set_index("date")["value"].round(6).to_dict()


SPEC2 = {"url": URL, "creator": None, "metric": "merkl_campaigns_plume_tokens",
         "chains": {98866: {"name": "Plume", "token": "0xWP"}, 1: {"name": "Ethereum", "token": "0xPL"}}}


# ===================================================================================== the cause: the list asks
def test_the_campaign_list_asks_merkl_for_the_compute_status(monkeypatch, tmp_path):
    m, http, _dc = _reader(monkeypatch, tmp_path, {8453: []}, {})
    m._campaigns(URL, "0xCREATOR", 8453, "0xTOK")
    assert http.params[0]["withComputeStatus"] == "true"
    m._campaigns(URL, None, 1, "0xPL")
    assert http.params[1]["withComputeStatus"] == "true" and http.params[1]["tokenAddress"] == "0xPL"


# ===================================================================================== one campaign, not the chain
def test_one_campaign_without_computed_until_does_not_void_the_chain(monkeypatch, tmp_path):
    from fetch.base import FetchOutput
    lists = {98866: [_camp("0xplume_ended", 300, "2026-01-01", "2026-01-04", chain=98866, token="0xWP", sym="WPLUME")],
             1: [_camp("0xeth_ended", 40, "2026-01-02", "2026-01-06", chain=1, token="0xPL", sym="PLUME"),
                 _camp("0xeth_live", 100, "2026-01-03", "2026-01-13", chain=1, token="0xPL", sym="PLUME",
                       campaignStatus={"computedUntil": T("2026-01-10 06:00")}),
                 # Jake's case: running, and Merkl gives no computedUntil for it
                 _camp("0x09a36c8a6c", 408_011.625, "2026-01-07 21:00", "2026-01-14 21:00", chain=1, token="0xPL",
                       sym="PLUME")]}
    totals = {"0xplume_ended": 300, "0xeth_ended": 40, "0xeth_live": 72.5, "0x09a36c8a6c": 1}
    m, http, dc = _reader(monkeypatch, tmp_path, lists, totals)
    out = FetchOutput()
    m._project("Plume", SPEC2, out)
    s = _series(out)
    # the other campaigns on BOTH chains stand; the series stops the day before the uncomputed campaign began
    assert s == {"2026-01-01": 100, "2026-01-02": 110, "2026-01-03": 110 + 10, "2026-01-04": 10 + 10,
                 "2026-01-05": 10 + 10, "2026-01-06": 10}
    # NEVER 0 FOR IT: none of its days is stored, as 0 or as a smaller sum
    assert not any(d >= "2026-01-07" for d in s)
    # its allocation is not read (it could be placed on no day) — no budget spent on it
    assert not any("0x09a36c8a6c" in c for c in http.calls)
    line = [e.message for e in out.log if e.status == "ok"][0]
    assert "PARTIAL — HELD 2026-01-07..2026-01-09 (3 day(s), not stored)" in line
    # NAMED WITH ITS SIZE: amount, token, start and end, from Merkl's own fields
    assert "Ethereum campaign 0x09a36c8a… (408,011.625 PLUME over 2026-01-07 21:00..2026-01-14 21:00 UTC" in line
    assert "no computedUntil" in line and "never 0, no accrual guessed" in line
    # the held days go on the provisional list, with the reason
    held = config.provisional_held()[("Plume", "merkl_campaigns_plume_tokens")]
    assert held[0] == ["2026-01-07", "2026-01-08", "2026-01-09"] and "408,011.625 PLUME" in held[1]
    assert "HELD 2026-01-07, 2026-01-08, 2026-01-09" in config.provisional_series()[("Plume",
                                                                                     "merkl_campaigns_plume_tokens")]
    # the day's read is not marked done: a later run today re-reads it
    assert dc.done_keys == []


def test_with_computed_until_the_campaign_is_counted_and_nothing_is_held(monkeypatch, tmp_path):
    from fetch.base import FetchOutput
    lists = {98866: [], 1: [_camp("0xeth_live", 100, "2026-01-03", "2026-01-13", chain=1, token="0xPL", sym="PLUME",
                                  campaignStatus={"computedUntil": T("2026-01-10 06:00")})]}
    m, _http, dc = _reader(monkeypatch, tmp_path, lists, {"0xeth_live": 72.5})
    config.set_provisional_held("Plume", "merkl_campaigns_plume_tokens", ["2026-01-07"], "PARTIAL — an earlier read")
    out = FetchOutput()
    m._project("Plume", SPEC2, out)
    s = _series(out)
    assert s == {f"2026-01-{d:02d}": 10 for d in range(3, 10)}          # 72.5 over 7.25 days; ends 01-09
    assert ("Plume", "merkl_campaigns_plume_tokens") not in config.provisional_held(), "an earlier hold is cleared"
    assert dc.done_keys == ["merkl_campaigns:Plume"]


def test_a_campaign_begun_today_without_computed_until_holds_no_stored_day(monkeypatch, tmp_path):
    from fetch.base import FetchOutput
    lists = {98866: [], 1: [_camp("0xeth_ended", 40, "2026-01-02", "2026-01-06", chain=1, token="0xPL", sym="PLUME"),
                            _camp("0xnew", 70, "2026-01-10 03:00", "2026-01-17", chain=1, token="0xPL", sym="PLUME",
                                  campaignStatus={"status": "PROCESSING"})]}
    m, _http, _dc = _reader(monkeypatch, tmp_path, lists, {"0xeth_ended": 40})
    out = FetchOutput()
    m._project("Plume", SPEC2, out)
    s = _series(out)
    assert max(s) == "2026-01-09" and s["2026-01-09"] == 0 and s["2026-01-05"] == 10
    line = [e.message for e in out.log if e.status == "ok"][0]
    assert "PARTIAL (no stored day affected)" in line and "campaignStatus without computedUntil" in line


def test_every_day_held_stores_nothing_and_says_why(monkeypatch, tmp_path):
    from fetch.base import FetchOutput
    lists = {8453: [_camp("0x05f987b985", 207_517.855, "2026-01-01 18:00", "2026-01-31 18:00")]}
    spec = {"url": URL, "creator": "0xCREATOR", "metric": "merkl_campaigns_non_ethereum_tokens",
            "chains": {8453: {"name": "Base", "token": "0xTOK"}}}
    m, _http, _dc = _reader(monkeypatch, tmp_path, lists, {})
    out = FetchOutput()
    m._project("Morpho", spec, out)
    assert _series(out) == {}
    msg = [e.message for e in out.log if e.status != "ok"][0]
    assert "no complete day to store" in msg and "Base campaign 0x05f987b9… (207,517.855 MORPHO" in msg


def test_daily_amounts_names_the_uncomputed_campaign_partial_and_never_counts_it():
    from fetch.merkl_campaigns import daily_amounts
    camps = [_camp("0xa", 30, "2026-01-01", "2026-01-04"), _camp("0xb", 99, "2026-01-01", "2026-01-04")]
    per_day, skipped = daily_amounts(camps, "0xtok", pd.Timestamp("2026-02-01").date(), 8453, set(),
                                     allocated={"0xa": (30 * E18, T("2026-01-04"))}, uncomputed={"0xb": "why"})
    assert {str(k): round(v, 6) for k, v in per_day.items()} == {"2026-01-01": 10, "2026-01-02": 10, "2026-01-03": 10}
    assert skipped == [("0xb", "PARTIAL: why")]


# ===================================================================================== Plume's own budget
def test_plume_reads_in_its_own_tier_with_its_own_budget():
    import fetch
    from fetch import merkl_campaigns as mc
    assert config.PROJECT_BY_NAME["Plume"]["merkl_campaigns"]["own_tier"] == "merkl_campaigns_plume"
    assert "own_tier" not in config.PROJECT_BY_NAME["Morpho"]["merkl_campaigns"]
    names = [t[0] for t in fetch.TIER_ORDER]
    assert "merkl_campaigns" in names and "merkl_campaigns_plume" in names
    assert fetch._budget("merkl_campaigns") == 120 and fetch._budget("merkl_campaigns_plume") == 120
    assert mc.READ_BUDGET_S < 120

    class Spy(mc.MerklCampaigns):
        def _project(self, name, spec, out):
            self.read = getattr(self, "read", []) + [name]
    both = [config.PROJECT_BY_NAME["Plume"], config.PROJECT_BY_NAME["Morpho"]]
    a, b = Spy(http=object(), daily_checks=object()), Spy(http=object(), daily_checks=object(),
                                                          tier="merkl_campaigns_plume")
    a.run(both, None, None)
    b.run(both, None, None)
    assert a.read == ["Morpho"] and b.read == ["Plume"]


def test_a_slow_morpho_read_no_longer_spends_plumes_budget(monkeypatch, tmp_path):
    """The old shared deadline: whichever project read second inherited what the first left. Each tier now has its own:
    Morpho spending its whole budget leaves Plume's read whole."""
    from fetch import merkl_campaigns as mc
    from fetch.base import FetchOutput

    class Clock:
        t = 0.0

        def __call__(self):
            return self.t
    clk = Clock()
    monkeypatch.setattr(mc, "_now", lambda: T("2026-01-10 12:00"))
    monkeypatch.setattr(mc, "today", lambda: pd.Timestamp("2026-01-10"))
    morpho_camps = {8453: [_camp(f"0x{i:08x}", 10, "2026-01-01", "2026-01-02") for i in range(20)]}
    plume_camps = {98866: [_camp("0xp", 30, "2026-01-01", "2026-01-04", chain=98866, token="0xWP")], 1: []}
    mspec = {"url": URL, "creator": "0xC", "metric": "m", "chains": {8453: {"name": "Base", "token": "0xTOK"}}}
    morpho = mc.MerklCampaigns(http=_Http(morpho_camps, {f"0x{i:08x}": 10 for i in range(20)}, clk, 10.0),
                               daily_checks=_Dc(), cache_path=tmp_path / "c.json", clock=clk)
    out_m = FetchOutput()
    morpho._project("Morpho", mspec, out_m)
    assert any("NOT YET COMPLETE" in e.message for e in out_m.log)        # Morpho ran out of its 100 s
    plume = mc.MerklCampaigns(http=_Http(plume_camps, {"0xp": 30}, clk, 10.0), daily_checks=_Dc(),
                              cache_path=tmp_path / "c.json", clock=clk, tier="merkl_campaigns_plume")
    out_p = FetchOutput()
    plume._project("Plume", SPEC2, out_p)
    assert _series(out_p) == {"2026-01-01": 10, "2026-01-02": 10, "2026-01-03": 10, "2026-01-04": 0,
                              "2026-01-05": 0, "2026-01-06": 0, "2026-01-07": 0, "2026-01-08": 0, "2026-01-09": 0}


def test_the_two_tiers_share_the_cache_file_without_losing_each_others_reads(tmp_path):
    from fetch import merkl_campaigns as mc
    f = tmp_path / "c.json"
    a = mc.MerklCampaigns(http=object(), daily_checks=object(), cache_path=f)
    b = mc.MerklCampaigns(http=object(), daily_checks=object(), cache_path=f, tier="merkl_campaigns_plume")
    a._load_cache()
    b._load_cache()                                   # both loaded the same (empty) file before either wrote
    a._cache["8453:0xa"] = {"allocated": "1", "returned": "0", "end": 1, "read_at": 2}
    a._dirty.add("8453:0xa")
    b._cache["1:0xb"] = {"allocated": "2", "returned": "0", "end": 1, "read_at": 2}
    b._dirty.add("1:0xb")
    a._save_cache()
    b._save_cache()
    assert set(json.loads(f.read_text())) == {"8453:0xa", "1:0xb"}


@pytest.mark.parametrize("metric", ["merkl_campaigns_plume_tokens", "merkl_campaigns_non_ethereum_tokens"])
def test_the_held_days_never_reach_a_provisional_drop_of_a_stored_day(metric):
    # merkl_campaigns is not a provider that publishes early: listing its held days drops no stored row at build time
    project = "Plume" if "plume" in metric else "Morpho"
    config.set_provisional_held(project, metric, ["2026-01-07"], "PARTIAL — x")
    assert (project, metric) in config.provisional_series()
    assert not config.provisional(project, metric, "merkl_campaigns:allocated", "2026-01-06", "2026-01-07 10:00")
