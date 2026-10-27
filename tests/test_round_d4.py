"""ROUND D4 (Jake 2026-10-27, head fe9a2e8; his overnight run was on D2 483d926).

1 NEAR lockups: the weekly task runs 8 h; any run resumes an unfinished read at its pin; the ETA from throttled
throughput; the state route (view_account + view_state, the getters' arithmetic) checked against the getters; a lockup
whose getter fails is named with its balance. 2 timeouts: Alchemy is never sent a range it rejects; Merkl's final
campaigns cached and the read self-budgeted; a timeout classified CODE / THROTTLED / NETWORK from its own calls.
3 a same-source reference against a series not refreshed onto its basis is a gap. 5 Pendle's own epoch figures are its
primary fees / revenue / holders revenue, DefiLlama the cross-check.
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

Y = 10 ** 24


# ================================================================================================== 1a / 1b
def test_1a_the_weekly_task_runs_eight_hours():
    reg = (ROOT / "windows" / "register_lockup_task.ps1").read_text(encoding="utf-8")
    assert "[int]$LimitHours = 8" in reg
    assert not any(ord(ch) > 127 for ch in reg)


def test_1a_any_run_resumes_an_unfinished_read_whatever_the_age_gate(monkeypatch, tmp_path, capsys):
    import near_lockups_weekly as nl
    import check_offline_items as coi
    import store as sm
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "lc"))
    monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "m.db"))
    sm.Store(str(tmp_path / "m.db")).close()
    day = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None) - pd.Timedelta(days=2)
    monkeypatch.setattr(nl, "last_read", lambda db, csv=None: day)            # stored 2 days ago: not due
    monkeypatch.setattr(coi, "lockup_resumable", lambda: {"height": 9, "pinned_at": "x", "results": {"a": {}}})
    calls = []
    monkeypatch.setattr(coi, "near_lockups", lambda: calls.append(1) or None)
    nl.main(["--every-days", "8"])
    assert calls == [1] and "resuming it at the same block" in capsys.readouterr().out
    monkeypatch.setattr(coi, "lockup_resumable", lambda: None)
    nl.main(["--every-days", "8"])
    assert calls == [1], "nothing unfinished: the age gate decides"


def test_1a_a_complete_read_is_not_resumable(monkeypatch, tmp_path):
    import check_offline_items as coi
    f = tmp_path / "c.json"
    monkeypatch.setattr(coi, "_lockup_pin_live", lambda c, now=None: True)
    f.write_text(json.dumps({"height": 5, "block_ts": 1, "pinned_at": "2026-10-27 00:00:00", "results": {},
                             "complete": True}))
    assert coi.lockup_resumable(f) is None
    f.write_text(json.dumps({"height": 5, "block_ts": 1, "pinned_at": "2026-10-27 00:00:00", "results": {}}))
    assert coi.lockup_resumable(f)["height"] == 5


def test_1b_the_eta_is_throttled_throughput_not_the_raw_call_rate(monkeypatch, tmp_path):
    import check_offline_items as coi
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    tp = coi._lockup_throughput()
    assert tp["calls_per_s"] == pytest.approx(15_183 / 16_500)            # Jake's 4h35m read, throttling included
    eta_h = 1_955 * coi.NEAR_LOCKUP_CALLS_PER_LOCKUP["getters"] / tp["calls_per_s"] / 3600
    assert eta_h == pytest.approx(4.6, abs=0.1), "the getters route reproduces the 4h35m Jake measured"
    coi._lockup_throughput_save({"calls_per_s": 2.0, "measured": "2026-11-01", "how": "x"})
    assert coi._lockup_throughput()["calls_per_s"] == 2.0                  # the last full read's figure next time


# ================================================================================================== 1c
def _state(transfers=("enabled", 1_602_614_338_000_000_000), lockup_ts=None, release=None, vest=("none",),
           amount=100 * Y, withdrawn=0, duration=0, deposit=0):
    return {"owner": "o", "lockup": {"lockup_amount": amount, "termination_withdrawn_tokens": withdrawn,
                                     "lockup_duration": duration, "release_duration": release,
                                     "lockup_timestamp": lockup_ts, "transfers": transfers},
            "vesting": vest, "whitelist": "w", "staking_pool": None, "deposit": deposit, "foundation": None}


def test_1c_the_state_route_is_getters_rs_arithmetic():
    import check_offline_items as coi
    t = 1_700_000_000_000_000_000
    # transfers still disabled in the state: wholly locked, as the getter says (the lakehouse would force them on)
    assert coi.near_locked_getter(_state(transfers=("disabled", "poll")), t) == 100 * Y
    # before the lockup timestamp: wholly locked less what was withdrawn on termination
    assert coi.near_locked_getter(_state(lockup_ts=t + 1, withdrawn=3 * Y), t) == 97 * Y
    # releasing linearly from the lockup timestamp: half the duration left -> half locked
    st = _state(lockup_ts=t - 50, release=100)
    assert coi.near_locked_getter(st, t) == 50 * Y
    # unvested (schedule) above the unreleased: the larger counts
    st = _state(vest=("schedule", t - 10, t - 10, t + 90))
    assert coi.near_locked_getter(st, t) == 90 * Y and coi.near_unvested(st, t) == 90 * Y
    # balance = account balance + known deposit; owners = balance - locked, floored
    got = coi.near_lockup_from_state(_state(deposit=5 * Y, transfers=("disabled", "p")), 20 * Y, t, buggy=False)
    assert got["balance"] == str(25 * Y) and got["locked"] == str(100 * Y) and got["owners"] == "0"
    assert coi.near_lockup_from_state(_state(deposit=None), 1, t, buggy=False) is None   # an unparsed tail: getters


def test_1c_a_checked_lockup_that_disagrees_refutes_the_route(monkeypatch):
    import check_offline_items as coi
    monkeypatch.setattr(coi, "_read_lockup_state", lambda a, h, ts: ({"balance": "10", "locked": "4", "unvested": "0",
                                                                       "owners": "6", "via": "state"}, ""))
    monkeypatch.setattr(coi, "_read_lockup", lambda a, h, ts: ({"balance": "10", "locked": "5", "unvested": "0",
                                                                 "owners": "5"}, ""))
    res, _ = coi._read_one("a.lockup.near", 1, 1, "state", check=True)
    assert res["via"] == "getters" and "locked state 4 vs getter 5" in res["mismatch"]
    monkeypatch.setattr(coi, "_read_lockup", lambda a, h, ts: ({"balance": "10", "locked": "4", "unvested": "0",
                                                                 "owners": "6"}, ""))
    res, _ = coi._read_one("a.lockup.near", 1, 1, "state", check=True)
    assert res["via"] == "state" and res["checked"] and "mismatch" not in res
    # an unknown code hash or an unparsed state goes to the getters
    monkeypatch.setattr(coi, "_read_lockup_state", lambda a, h, ts: (None, "GETTERS"))
    res, _ = coi._read_one("a.lockup.near", 1, 1, "state")
    assert res["via"] == "getters"


def test_1c_a_refuted_route_drops_its_results_and_stores_nothing(monkeypatch, tmp_path, capsys):
    import check_offline_items as coi
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    f = tmp_path / "l.txt"
    f.write_text("".join(f"x{i}.lockup.near\n" for i in range(4)))
    monkeypatch.setenv("NEAR_LOCKUPS_FILE", str(f))
    monkeypatch.setattr(coi, "_near_post", lambda params, method="query", urls=None, attempts=None:
                        iter([("u", {"result": {"header": {"height": 7, "timestamp": 1}}})]))

    def one(acc, h, ts, route, check):
        r = {"balance": "10", "locked": "4", "unvested": "0", "owners": "6", "via": "state"}
        return (dict(r, via="getters", mismatch=f"{acc}: locked") if acc == "x0.lockup.near" else r), ""
    monkeypatch.setattr(coi, "_read_one", one)
    got = coi.near_lockups(route="state")
    out = capsys.readouterr().out
    assert "STATE ROUTE REFUTED" in out and got["unreadable"] == 3 and got["route"] == "getters"
    import near_lockups_weekly as nl
    assert nl.read_failures(got) == 3                                      # nothing stored


# ================================================================================================== 1d
def test_1d_a_lockup_whose_getter_fails_is_named_with_its_balance(monkeypatch, tmp_path, capsys):
    import check_offline_items as coi
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    f = tmp_path / "l.txt"
    f.write_text("good.lockup.near\nbroken.lockup.near\n")
    monkeypatch.setenv("NEAR_LOCKUPS_FILE", str(f))
    monkeypatch.setattr(coi, "_near_post", lambda params, method="query", urls=None, attempts=None:
                        iter([("u", {"result": {"header": {"height": 7, "timestamp": 1}}})]))
    monkeypatch.setattr(coi, "_near_view_account", lambda a, h: ({"amount": str(3 * Y), "code_hash": "x"}, ""))

    def view(acc, m, args=None, block_id=None, pace=0.12, urls=None):
        if acc.startswith("broken"):
            return None, "rpc.mainnet.fastnear.com: wasm execution failed with error: HostError(GuestPanic)"
        return {"get_balance": str(10 * Y), "get_locked_amount": str(4 * Y), "get_owners_balance": str(6 * Y),
                "get_vesting_information": "None"}[m], ""
    monkeypatch.setattr(coi, "_near_view", view)
    monkeypatch.setattr(coi, "_near_code_hash", lambda a, h, urls=None: ("x", ""))
    got = coi.near_lockups(route="getters")
    out = capsys.readouterr().out
    assert "LEFT OUT: broken.lockup.near" in out and "3.00 NEAR" in out and "at most that" in out
    assert got["faults"] == [("broken.lockup.near", 3.0)] and got["read"] == 1


# ================================================================================================== 2a
def test_2a_alchemy_is_never_sent_a_range_its_plan_rejects(monkeypatch):
    from fetch.chain import ChainReader
    url = "https://eth-mainnet.g.alchemy.com/v2/KEY"
    monkeypatch.delenv("ALCHEMY_GETLOGS_MAX_RANGE", raising=False)
    assert config.ALCHEMY_GETLOGS_MAX_RANGE == 10 and ChainReader.getlogs_cap(url) == 10
    assert ChainReader.getlogs_cap("https://ethereum-rpc.publicnode.com") is None
    r = ChainReader.__new__(ChainReader)
    assert r._cap_refuses(url, 5_000) == 10                    # 500 calls at 10: not asked
    assert r._cap_refuses(url, 150) is None                     # 15 calls: chunked to 10
    monkeypatch.setenv("ALCHEMY_GETLOGS_MAX_RANGE", "10000")
    assert r._cap_refuses(url, 5_000) is None                   # a paid plan's range


def test_2a_the_walk_chunks_to_the_cap_and_skips_alchemy_for_a_wide_span(monkeypatch):
    from fetch.chain import ChainReader, NoGetLogs
    url = "https://eth-mainnet.g.alchemy.com/v2/KEY"
    r = ChainReader.__new__(ChainReader)
    r.__dict__.update({"_w3_url": {}, "log_range_errors": {}, "log_endpoints_refused": {}, "log_chunk_used": {},
                       "log_endpoint_used": {}})
    monkeypatch.setattr(ChainReader, "is_down", lambda self, c, u: False)
    asked = []

    class _Eth:
        def get_logs(self, flt):
            asked.append(flt["toBlock"] - flt["fromBlock"] + 1)
            return []

    class _W3:
        eth = _Eth()
        provider = type("P", (), {"endpoint_uri": url})()
    monkeypatch.setattr(ChainReader, "_w3_at", lambda self, c, u, t: _W3())
    with pytest.raises(NoGetLogs):
        r._get_logs_resilient("ethereum", {}, 1, 5_000, 2_000, only=[url])
    assert asked == [], "a wide span is never sent to Alchemy"
    r.__dict__["_nologs"] = {}
    logs, upper, used = r._get_logs_resilient("ethereum", {}, 1, 150, 2_000, only=[url])
    assert asked == [10] and used == 10 and upper == 10


# ================================================================================================== 2b
def _camp(cid, start, end, amount=100):
    return {"campaignId": cid, "startTimestamp": start, "endTimestamp": end, "amount": str(amount * 10 ** 18),
            "distributionChainId": 8453, "creatorAddress": "0xC",
            "rewardToken": {"address": "0xT", "decimals": 18}, "campaignStatus": {"computedUntil": end}}


class _Http:
    def __init__(self, clock=None, step=0.0):
        self.calls, self.stats, self.clock, self.step = [], {"calls": 0}, clock, step

    def get(self, url, params=None):
        self.calls.append(url)
        self.stats["calls"] += 1
        if self.clock is not None:
            self.clock.t += self.step
        if "/users/" in url:
            return {"amount": "0", "breakdowns": []}
        return {"amount": str(90 * 10 ** 18)}


def test_2b_final_campaigns_are_cached_and_only_live_ones_re_read(tmp_path):
    from fetch import merkl_campaigns as mc
    now = 1_793_000_000
    day = mc.DAY
    camps = [_camp("0x00000001", now - 200 * day, now - 100 * day),       # ended 100 days ago: final once read late
             _camp("0x00000002", now - 20 * day, now - 5 * day),          # ended 5 days ago: re-read every run
             _camp("0x00000003", now - 10 * day, now + 10 * day)]         # running
    http = _Http()
    m = mc.MerklCampaigns(http=http, daily_checks=object(), cache_path=tmp_path / "c.json")
    m._allocations("https://api.merkl.xyz/v4/campaigns", camps, 8453, "0xT", now)
    m._save_cache()                                                       # _project saves at the end of each read
    assert len(http.calls) == 6 and m.stats["read"] == 3
    m2 = mc.MerklCampaigns(http=_Http(), daily_checks=object(), cache_path=tmp_path / "c.json")
    out, _u = m2._allocations("https://api.merkl.xyz/v4/campaigns", camps, 8453, "0xT", now + day)
    m2._save_cache()
    assert m2.stats["cached"] == 1 and len(m2.http.calls) == 4, "only the recent and running campaigns re-read"
    assert out["0x00000001"][0] == 90 * 10 ** 18
    # past its re-verification period the final campaign is read again
    m3 = mc.MerklCampaigns(http=_Http(), daily_checks=object(), cache_path=tmp_path / "c.json")
    m3._allocations("https://api.merkl.xyz/v4/campaigns", camps, 8453, "0xT", now + 45 * day)
    assert m3.stats["cached"] == 0 and m3.stats["reverified"] == 3


def test_2b_the_read_stops_inside_its_budget_keeps_what_it_read_and_stores_nothing(tmp_path):
    from fetch import merkl_campaigns as mc

    class Clock:
        t = 0.0

        def __call__(self):
            return self.t
    clk = Clock()
    now = 1_793_000_000
    camps = [_camp(f"0x{i:08x}", now - 400 * mc.DAY, now - 300 * mc.DAY) for i in range(10)]
    m = mc.MerklCampaigns(http=_Http(clk, 10.0), daily_checks=object(), cache_path=tmp_path / "c.json", budget_s=55,
                          clock=clk)
    m.deadline = clk() + m.budget_s
    with pytest.raises(mc.ReadIncomplete):
        m._allocations("https://api.merkl.xyz/v4/campaigns", camps, 8453, "0xT", now)
    m._save_cache()
    kept = json.loads((tmp_path / "c.json").read_text())
    assert 0 < len(kept) < 10
    assert mc.ROW_SOURCE == "merkl_campaigns:allocated"


# ================================================================================================== 2c
def test_2c_a_timeout_is_classified_from_its_own_calls():
    from fetch import timeout_cause
    ok = {"calls": 131, "http_s": 70.0, "waited_s": 50.0, "net_errors": 0, "throttled": 0,
          "times": [(0.5, "/v4/x")] * 131}
    assert timeout_cause(ok, 120)[0] == "CODE"
    net = dict(ok, net_errors=5)
    assert timeout_cause(net, 120)[0] == "NETWORK"
    thr = dict(ok, throttled=12, waited_s=60.0)
    assert timeout_cause(thr, 120)[0] == "THROTTLED"
    assert timeout_cause(None, 120)[0] == "UNCLASSIFIED"
    assert timeout_cause(None, 240, {"n": 400, "timeouts": 4, "errors": 0, "throttled": 0, "p50": 0.3,
                                     "p90": 2.0})[0] == "NETWORK"


def test_2c_code_timeouts_never_count_towards_network_wide_trouble():
    import token_metrics as tm
    from fetch.base import LogEntry
    log = [LogEntry(s, None, 0, "failed", "TIER TIMED OUT after 120s ... CAUSE: CODE — every call answered", 1)
           for s in ("merkl_campaigns", "explorer", "chain", "migration")]
    txt = "\n".join(tm.run_banner(log))
    assert "NETWORK-WIDE" not in txt and "TIMED OUT, NOT THE NETWORK — 4 source(s)" in txt
    log += [LogEntry(s, None, 0, "failed", "TIER TIMED OUT after 90s ... CAUSE: NETWORK — 5 network error(s)", 1)
            for s in ("a", "b", "c", "d")]
    assert "NETWORK-WIDE TROUBLE — 4 sources" in "\n".join(tm.run_banner(log))


# ================================================================================================== 3
def test_3_a_same_source_reference_against_a_stale_basis_is_a_gap_never_check():
    import references as rf
    key = ("Morpho", "d2_merkl_base_campaign_allocated")
    route = rf.ROUTES[key]
    row = [r for r in rf.load() if (r["project"], r["metric"]) == key][0]
    d = pd.date_range("2026-09-15", "2026-10-25")
    def g(src, v):
        return {("Morpho", route[1]): pd.DataFrame({"date": d, "project": "Morpho", "metric": route[1], "value": v,
                                                    "source": src, "tier": 1, "fetched_at": "2026-10-26T20:00:00Z"})}
    # Jake's store on D2: merkl timed out, the series still on the SCHEDULE basis (6,645.95 a day = 199,378 x 30 days)
    stale = rf.check(g("merkl_campaigns", 6645.954), rows=[row], projects={"Morpho"})[0]
    assert stale["status"] == "MISSING" and "NOT REFRESHED" in stale["detail"] and "CHECK" not in stale["status"]
    assert "merkl_campaigns:allocated" in stale["detail"]
    fresh = rf.check(g("merkl_campaigns:allocated", 4608.0295), rows=[row], projects={"Morpho"})[0]
    assert fresh["status"] == "LIVE-UNVERIFIED"


def test_3_a_series_fetched_before_the_reference_was_read_is_not_refreshed():
    import references as rf
    r = {"source_type": "same-source (x)", "note": "D9 (read 2026-10-26): …"}
    g = {("P", "m"): pd.DataFrame({"date": pd.date_range("2026-10-01", periods=3), "project": "P", "metric": "m",
                                   "value": 1.0, "source": "s", "fetched_at": "2026-10-20T06:00:00Z"})}
    why = rf.not_refreshed(r, ("P", "x"), ("sum_period", "m", "2026-10-01", "2026-10-03"), g, pd.Timestamp("2026-10-03"))
    assert "fetched by 2026-10-20, before the reference was read (2026-10-26)" in why
    r2 = dict(r, source_type="independent")
    assert rf.not_refreshed(r2, ("P", "x"), ("sum_period", "m", "2026-10-01", "2026-10-03"), g,
                            pd.Timestamp("2026-10-03")) == ""


# ================================================================================================== 5
def _pendle_groups(with_epochs=True):
    d = pd.date_range("2026-07-01", "2026-10-26")
    out = {}
    for m in ("revenue_usd_defillama", "fees_usd_defillama", "holders_revenue_usd_defillama"):
        out[("Pendle", m)] = pd.DataFrame({"date": d, "project": "Pendle", "metric": m, "value": 1000.0,
                                           "source": "defillama", "tier": 1})
    if with_epochs:
        ep = pd.to_datetime(["2026-09-08", "2026-09-22", "2026-10-06"])
        for m, v in (("pendle_epoch_market_revenue_usd", 28_000.0), ("pendle_epoch_market_fees_usd", 42_000.0),
                     ("pendle_epoch_buyback_usd", 21_000.0)):
            out[("Pendle", m)] = pd.DataFrame({"date": ep, "project": "Pendle", "metric": m, "value": v,
                                               "source": "pendle_api:x", "tier": 3})
        out[("Pendle", "pendle_pt_looping_fees_usd")] = pd.DataFrame(
            {"date": ep[1:], "project": "Pendle", "metric": "pendle_pt_looping_fees_usd", "value": 1_400.0,
             "source": "pendle_api:y", "tier": 3})
    return out


def test_5a_the_epoch_figure_spread_is_primary_and_defillama_stands_only_around_it(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    g = _pendle_groups()
    bw._epoch_primary_views(g, pd.Timestamp("2026-10-26"))
    r = g[("Pendle", "revenue_usd")].assign(date=lambda x: pd.to_datetime(x["date"])).set_index("date")
    assert r.loc["2026-09-07", "value"] == 1000.0 and r.loc["2026-09-07", "source"] == "defillama"
    assert r.loc["2026-09-08", "value"] == pytest.approx(2000.0)                   # 28,000 / 14, PT-looping not tracked
    assert "not tracked this epoch: pendle_pt_looping_fees_usd" in r.loc["2026-09-08", "source"]
    assert r.loc["2026-09-22", "value"] == pytest.approx(29_400 / 14)              # + the PT-looping leg
    assert r.loc["2026-10-20", "source"].startswith("defillama:in_progress_epoch[") and r.loc["2026-10-20", "value"] == 1000
    assert r.index.max() == pd.Timestamp("2026-10-26")
    f = g[("Pendle", "fees_usd")].set_index("date")
    assert f.loc[pd.Timestamp("2026-10-06"), "value"] == pytest.approx(43_400 / 14)
    h = g[("Pendle", "holders_revenue_usd")].set_index("date")
    assert h.loc[pd.Timestamp("2026-10-06"), "value"] == pytest.approx(1500.0)     # the buyback $ alone
    # the three legs are a declared handover, so a window over them is labelled, never blanked
    from fetch.base import _measuring_point
    pts = {_measuring_point(s) for s in r["source"]}
    assert pts == set(config.PROJECT_BY_NAME["Pendle"]["series_handover"]["revenue_usd"]["ordered_points"])
    assert bw.handover_refusal("Pendle", "revenue_usd", sorted(pts),
                               {p: (r[[_measuring_point(s) == p for s in r["source"]]].index.min(),
                                    r[[_measuring_point(s) == p for s in r["source"]]].index.max()) for p in pts}) is None


def test_5a_an_epoch_ends_where_the_next_begins_and_a_missing_one_is_unformed(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    g = _pendle_groups()
    ep = pd.to_datetime(["2026-01-29", "2026-02-10", "2026-03-10"])               # 12 days, then a missing epoch
    g[("Pendle", "pendle_epoch_buyback_usd")] = pd.DataFrame({"date": ep, "project": "Pendle",
                                                               "metric": "pendle_epoch_buyback_usd",
                                                               "value": [1200.0, 1400.0, 1400.0], "source": "s",
                                                               "tier": 3})
    bw._epoch_primary_views(g, pd.Timestamp("2026-10-26"))
    h = g[("Pendle", "holders_revenue_usd")].assign(date=lambda x: pd.to_datetime(x["date"])).set_index("date")
    assert h.loc["2026-01-29", "value"] == pytest.approx(100.0)                    # 1,200 over its 12 days
    assert pd.Timestamp("2026-02-25") not in h.index                               # the missing epoch: no figure
    assert h.loc["2026-03-10", "value"] == pytest.approx(100.0)


def test_5a_with_no_epoch_stored_the_cross_check_stands_as_stored(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    g = _pendle_groups(with_epochs=False)
    bw._epoch_primary_views(g, pd.Timestamp("2026-10-26"))
    assert set(g[("Pendle", "revenue_usd")]["source"]) == {"defillama"}


def test_5bc_the_cross_check_names_its_legs_and_the_reconciling_line():
    import build_workbook as bw
    import credibility as cr
    g = _pendle_groups()
    ep = pd.to_datetime(["2026-09-08", "2026-09-22", "2026-10-06"])
    g[("Pendle", "pendle_epoch_revenue_usd")] = pd.DataFrame({"date": ep, "project": "Pendle",
                                                               "metric": "pendle_epoch_revenue_usd",
                                                               "value": 33_000.0, "source": "s", "tier": 3})
    bw._SCOPE = list(config.PROJECTS)
    bw._epoch_primary_views(g, pd.Timestamp("2026-10-26"))
    long = pd.concat(g.values(), ignore_index=True).assign(date=lambda x: pd.to_datetime(x["date"]))
    spec = config.CREDIBILITY["Pendle"]["in_revenue"]
    assert spec["ours"]["args"]["use_views"] and spec["ref"]["tol"] == 10.0
    ours = cr._pendle_basis_vs_defillama("Pendle", None, long, pd.Timestamp("2026-10-26"), side="ours")
    ref = cr._pendle_basis_vs_defillama("Pendle", None, long, pd.Timestamp("2026-10-26"), side="ref")
    assert ours[0] == pytest.approx(3 * 28_000 + 2 * 1_400) and ref[0] == pytest.approx(42_000.0)
    assert "RECONCILING LINE" in ref[2] and f"{99_000 - 86_800:,.0f} attributed to no market" in ref[2]
    assert "OUT of revenue" in ref[2] and "not tracked: 2026-09-08" in ref[2]
    note = spec["ref"]["note"]
    assert "Robinhood Chain 574 vs Pendle's 32,994" in note and "HyperEVM 12,438 vs 30,317" in note


def test_5_defillama_is_stored_as_the_cross_check_and_the_parent_rows_go_once():
    p = config.PROJECT_BY_NAME["Pendle"]
    assert p["defillama_metric_as"] == {"fees_usd": "fees_usd_defillama", "revenue_usd": "revenue_usd_defillama",
                                        "holders_revenue_usd": "holders_revenue_usd_defillama"}
    assert p["defillama_fees_slug"] == "pendle-v2"
    for m in ("fees_usd_defillama", "revenue_usd_defillama", "holders_revenue_usd_defillama"):
        assert "Pendle" in config.METRICS[m]["only_projects"]
    assert {c["metric"] for c in config.RUN_CLEANUPS if c["id"] == "D3-5"} == {"fees_usd", "revenue_usd",
                                                                               "holders_revenue_usd"}


def test_2b_the_one_off_fill_reads_everything_with_no_budget_and_stores_nothing(tmp_path):
    from fetch import merkl_campaigns as mc
    now = mc._now()
    camps = [_camp(f"0x{i:08x}", now - 400 * mc.DAY, now - 300 * mc.DAY) for i in range(4)]

    class H(_Http):
        def get(self, url, params=None):
            if url.endswith("/campaigns"):
                self.calls.append(url)
                return camps if params.get("page") == 0 else []
            return super().get(url, params)
    proj = [{"name": "X", "merkl_campaigns": {"url": "https://api.merkl.xyz/v4/campaigns", "creator": "0xC",
                                              "metric": "m", "chains": {"8453": {"token": "0xT", "name": "Base"}}}}]
    got = mc.fill_cache(proj, http=H(), cache_path=tmp_path / "c.json")
    assert got == {"X": (0, 4)} and len(json.loads((tmp_path / "c.json").read_text())) == 4
    assert mc.fill_cache(proj, http=H(), cache_path=tmp_path / "c.json") == {"X": (4, 0)}


def test_1c_only_the_validated_codes_take_the_state_route(monkeypatch):
    import check_offline_items as coi
    assert set(coi.NEAR_LOCKUP_STATE_CODES) == {"3kVY9qcVRoW3B5498SMX6R3rtSLiCdmBzKs7zcnzDJ7Q",
                                                "4Pfw2RU6e35dUsHQQoFYfwX8KFFvSRNwMSNLXuSFHXrC",
                                                "3skHaUtj85RPdUZwx6M4Jp4PfC9qJHqnsyuWLtuq2xBT",
                                                "Cw7bnyp4B6ypwvgZuMmJtY6rHsxP2D4PC8deqeJ3HP7D"}
    states = []
    monkeypatch.setattr(coi, "_near_view_state", lambda a, h: states.append(a) or (None, "x"))
    for code, goes in (("DiC9bKCqUHqoYqUXovAnqugiuntHWnM3cAc7KrgaHTu", "GETTERS"),     # buggy, never sampled
                       ("ETTviw49p5FgDhUTExizKSu5zrxDXK9a2hW2vMq9qHWE", "GETTERS"),    # not a lockup contract
                       ("4Pfw2RU6e35dUsHQQoFYfwX8KFFvSRNwMSNLXuSFHXrC", "state: x")):
        monkeypatch.setattr(coi, "_near_view_account", lambda a, h, c=code: ({"amount": "1", "code_hash": c}, ""))
        assert coi._read_lockup_state("a.lockup.near", 1, 1) == (None, goes)
    assert states == ["a.lockup.near"]


def test_2b_the_schedule_rows_go_once_and_the_allocated_rows_stay(tmp_path, monkeypatch):
    import logging
    import store as sm
    import token_metrics as tm
    from fetch.logcache import DailyChecks
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS if c["id"] == "D4-2b"))
    assert {(c["projects"], c["metric"], c["source"]) for c in config.RUN_CLEANUPS} == {
        (("Morpho",), "merkl_campaigns_non_ethereum_tokens", "merkl_campaigns"),
        (("Plume",), "merkl_campaigns_plume_tokens", "merkl_campaigns")}
    dc = DailyChecks()
    dc.f.parent.mkdir(parents=True, exist_ok=True)
    dc.f.write_text(json.dumps({"merkl_campaigns:Morpho": "2026-10-27"}))
    st = sm.Store(str(tmp_path / "metrics.db"))
    # Jake's store: the schedule rows to 10-25 (6,645.95 a day = 199,378 / 30)
    sched = [{"date": str(d.date()), "project": "Morpho", "metric": "merkl_campaigns_non_ethereum_tokens",
              "value": 6_645.95, "source": "merkl_campaigns", "tier": 1}
             for d in pd.date_range("2026-10-20", "2026-10-25")]
    st.upsert(pd.DataFrame(sched))
    lines = tm.apply_run_cleanups(st, "r", logging.getLogger("t"))
    assert sum("applied once — 6 row(s) of Morpho / merkl_campaigns_non_ethereum_tokens" in x for x in lines) == 1
    assert pd.read_sql("SELECT * FROM metrics", st.conn).empty
    assert dc.get("merkl_campaigns:Morpho") is None, "the same run re-reads Morpho's campaigns"
    # the allocated read, computed only to 10-22 (a running campaign): nothing of the schedule is left past it
    st.upsert(pd.DataFrame([dict(r, value=4_608.03, source="merkl_campaigns:allocated") for r in sched[:3]]))
    assert tm.apply_run_cleanups(st, "r2", logging.getLogger("t")) == []
    left = pd.read_sql("SELECT date, source FROM metrics", st.conn)
    assert set(left["source"]) == {"merkl_campaigns:allocated"} and left["date"].max().startswith("2026-10-22")
    st.close()
