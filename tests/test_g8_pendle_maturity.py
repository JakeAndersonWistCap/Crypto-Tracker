"""
tests/test_g8_pendle_maturity.py — G8, Pendle TVL by maturity (fetch/pendle_maturity.py), 2026-10-20. No network: a
fake Http serves markets/all and historical-data from a dict, with the API's own rate-limit headers.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fetch import pendle_maturity as pm  # noqa: E402
from fetch.base import BudgetExhausted, FetchOutput  # noqa: E402

TODAY = pd.Timestamp("2026-10-20")
WALL0 = dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.timezone.utc).timestamp()


def D(s):
    return dt.date.fromisoformat(s)


def iso(s):
    return f"{s}T00:00:00.000Z"


# (chain, address, name, expiry, listed, value a day). Values are powers of ten so every sum reads at a glance.
MARKETS = [
    (1, "0xa", "A", "2026-10-25", "2026-08-01", 1.0),             # 6 days out on 10-19 — le_30d
    (1, "0xb", "B", "2026-12-31", "2026-08-01", 10.0),            # 73 days — 31_90d
    (42161, "0xc", "C", "2027-03-01", "2026-08-01", 100.0),       # 133 days — 91_180d
    (1, "0xd", "D", "2027-12-30", "2026-08-01", 1000.0),          # 437 days — gt_180d
    (8453, "0xe", "E", "2026-10-01", "2026-07-01", 10000.0),      # expired inside the window (last point 09-30)
    (1, "0xf", "F", "2026-01-01", "2025-07-01", 1e6),             # expired before the window: never read
    (1, "0x9", "G", "2027-01-15", "2026-10-10", 100000.0),        # listed inside the window
]


class FakeDaily:
    def __init__(self):
        self.marks = {}

    def due(self, key, day):
        return self.marks.get(key) != day

    def done(self, key, day):
        self.marks[key] = day

    def ever(self, key):
        return key in self.marks


class FakeHttp:
    """markets/all + historical-data from MARKETS. Each call advances the fake clock by `step` seconds and sets
    last_headers as the API does (cost per call, a one-minute window)."""

    def __init__(self, clock, markets=MARKETS, step=1.0, holes=(), today=TODAY):
        self.clock, self.markets, self.step, self.holes, self.today = clock, markets, step, set(holes), today
        self.calls: list[tuple[str, dict]] = []
        self.last_headers = {}

    def _hdr(self, cost):
        now = WALL0 + self.clock[0]
        return {"x-computing-unit": str(cost), "x-ratelimit-limit": "200", "x-ratelimit-remaining": "199",
                "x-ratelimit-reset": str(int(now // 60 * 60 + 60)), "x-ratelimit-weekly-remaining": "199000"}

    def get(self, url, params=None, headers=None, deadline=None):
        self.clock[0] += self.step
        self.calls.append((url, dict(params or {})))
        if url.endswith("/v2/markets/all"):
            skip, lim = int(params["skip"]), int(params["limit"])
            rows = [{"chainId": c, "address": a, "name": n, "expiry": iso(e), "timestamp": f"{ls}T12:00:00.000Z",
                     "details": {"totalTvl": v}} for c, a, n, e, ls, v in self.markets]
            self.last_headers = self._hdr(2)
            return {"total": len(rows), "results": rows[skip:skip + lim]}
        _, chain, _, addr, _ = url.rsplit("/", 4)
        m = next(x for x in self.markets if str(x[0]) == chain and x[1] == addr)
        d, end = D(params["timestamp_start"][:10]), min(self.today.date(), D(m[3]) - dt.timedelta(days=1))
        d = max(d, D(m[4]))
        pts = []
        while d <= end:
            if (m[2], str(d)) not in self.holes:
                pts.append({"timestamp": iso(d), "totalTvl": m[5]})
            d += dt.timedelta(days=1)
        self.last_headers = self._hdr(max(1, -(-len(pts) // 300)))
        return {"results": pts}

    def history_calls(self, name):
        addr = next(x[1] for x in self.markets if x[2] == name)
        return [p for u, p in self.calls if f"/markets/{addr}/" in u]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "today", lambda: TODAY)
    clock = [0.0]
    spec = {"history_days": 30, "budget_s": 1000, "state_path": str(tmp_path / "state.json")}
    return clock, spec, tmp_path


def make(clock, http, daily=None):
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock[0] += s
    f = pm.PendleMaturity(http=http, daily=daily or FakeDaily(), clock=lambda: clock[0],
                          wall=lambda: WALL0 + clock[0], sleep=sleep)
    f.sleeps = sleeps
    return f


def stored(out) -> dict:
    """{metric: {date: value}} from a FetchOutput."""
    f = out.frame()
    return {m: {str(d.date()): v for d, v in zip(g["date"], g["value"])} for m, g in f.groupby("metric")}


def _rec(name, expiry, created, pts, through, first=None):
    return {"name": name, "expiry": iso(expiry), "created": iso(created), "pts": pts, "through": through,
            "first": first if first is not None else (min(pts) if pts else None)}


# ================================================================================ bucket assignment by days to expiry
def test_bucket_edges_and_days_to_expiry():
    assert [pm.bucket_of(n) for n in (0, -3)] == [None, None]
    assert pm.bucket_of(1) == pm.bucket_of(30) == "tvl_maturity_le_30d_usd"
    assert pm.bucket_of(31) == pm.bucket_of(90) == "tvl_maturity_31_90d_usd"
    assert pm.bucket_of(91) == pm.bucket_of(180) == "tvl_maturity_91_180d_usd"
    assert pm.bucket_of(181) == pm.bucket_of(5000) == "tvl_maturity_gt_180d_usd"
    day = D("2026-10-19")
    assert pm.days_to_expiry(iso("2026-10-20"), day) == 1          # expires at the next midnight: 1 day out
    assert pm.days_to_expiry(iso("2026-10-19"), day) == 0          # expired at the day's own 00:00
    assert pm.days_to_expiry("2026-10-19T08:00:00.000Z", day) == 1  # a mid-day expiry: still live that day


def test_split_day_buckets_by_days_to_expiry_on_that_day():
    day = "2026-10-19"
    mk = {"a": _rec("A", "2026-11-18", "2026-01-01", {day: 1.0}, day),       # 30 days
          "b": _rec("B", "2026-11-19", "2026-01-01", {day: 10.0}, day),      # 31
          "c": _rec("C", "2027-01-17", "2026-01-01", {day: 100.0}, day),     # 90
          "d": _rec("D", "2027-01-18", "2026-01-01", {day: 1000.0}, day),    # 91
          "e": _rec("E", "2027-04-17", "2026-01-01", {day: 1e4}, day),       # 180
          "f": _rec("F", "2027-04-18", "2026-01-01", {day: 1e5}, day)}       # 181
    verdict, v = pm.split_day(mk, D(day))
    assert verdict == "ok"
    assert v == {"tvl_maturity_le_30d_usd": 1.0, "tvl_maturity_31_90d_usd": 110.0,
                 "tvl_maturity_91_180d_usd": 11000.0, "tvl_maturity_gt_180d_usd": 1e5, "tvl_maturity_total_usd": 111111.0}
    # the SAME market moves bucket as its expiry nears: f is 181 days out on 10-19, 180 on 10-20
    mk2 = {"f": _rec("F", "2027-04-18", "2026-01-01", {"2026-10-20": 5.0}, "2026-10-20")}
    assert pm.split_day(mk2, D("2026-10-20"))[1]["tvl_maturity_91_180d_usd"] == 5.0


def test_expired_market_is_excluded_and_never_refuses_the_day():
    mk = {"a": _rec("A", "2026-12-01", "2026-01-01", {"2026-10-10": 7.0}, "2026-10-19"),
          # expired 10-05: no point on 10-10, and that is not a missing point — it is not live
          "x": _rec("X", "2026-10-05", "2026-01-01", {"2026-10-04": 99.0}, "2026-10-04"),
          # a point served on its expiry day would still not count (d >= expiry)
          "y": _rec("Y", "2026-10-10", "2026-01-01", {"2026-10-10": 55.0}, "2026-10-10")}
    verdict, v = pm.split_day(mk, D("2026-10-10"))
    assert verdict == "ok" and v["tvl_maturity_total_usd"] == 7.0
    assert pm.split_day(mk, D("2026-10-04"))[1]["tvl_maturity_total_usd"] == 99.0     # x live (1 day out), a not
    # a market not yet live (before its first point) is not counted and does not refuse
    assert pm.split_day({"n": _rec("N", "2027-01-01", "2026-10-12", {"2026-10-12": 3.0}, "2026-10-19")},
                        D("2026-10-11")) == ("ok", {**{b[0]: 0.0 for b in pm.BUCKETS}, "tvl_maturity_total_usd": 0.0})


def test_missing_day_inside_a_live_span_refuses_the_day():
    pts = {"2026-10-01": 1.0, "2026-10-03": 1.0}                     # 10-02 missing
    mk = {"a": _rec("A", "2026-12-01", "2026-01-01", pts, "2026-10-03"),
          "b": _rec("B", "2027-12-01", "2026-01-01", {f"2026-10-0{i}": 5.0 for i in (1, 2, 3)}, "2026-10-03")}
    verdict, who = pm.split_day(mk, D("2026-10-02"))
    assert verdict == "refused" and who == ["A (a)"]                 # refused for EVERY bucket, not summed as 0
    assert pm.split_day(mk, D("2026-10-03"))[0] == "ok"
    # read through the day vs not yet read through it: the latter waits, it is not refused
    assert pm.split_day(mk, D("2026-10-04"))[0] == "pending"
    # a read market with no point at all counts from the day after its listing — refused, not 0
    nothing = {"z": _rec("Z", "2027-01-01", "2026-09-01", {}, "2026-10-03")}
    assert pm.split_day(nothing, D("2026-09-01"))[0] == "ok"
    assert pm.split_day(nothing, D("2026-09-02"))[0] == "refused"
    # an unread market listed on or before the day blocks it; one listed after does not
    unread = {"u": {"name": "U", "expiry": iso("2027-01-01"), "created": "2026-10-02T12:00:00.000Z"}}
    assert pm.split_day(unread, D("2026-10-02"))[0] == "pending"
    assert pm.split_day(unread, D("2026-10-01"))[0] == "ok"


def test_parse_history_null_is_missing_and_garbage_refuses():
    body = {"results": [{"timestamp": iso("2026-10-01"), "totalTvl": 3.5}, {"timestamp": iso("2026-10-02"),
                                                                            "totalTvl": None}]}
    assert pm.parse_history(body) == {"2026-10-01": 3.5}
    assert "not a number" in pm.parse_history({"results": [{"timestamp": iso("2026-10-01"), "totalTvl": "x"}]})
    assert "no `results`" in pm.parse_history({"data": []})


# ================================================================================ the fetcher end to end (fake HTTP)
def test_full_run_stores_buckets_and_total_is_their_sum(env):
    clock, spec, _ = env
    http = FakeHttp(clock)
    daily = FakeDaily()
    out = FetchOutput()
    make(clock, http, daily)._project("Pendle", spec, out)
    s = stored(out)
    assert set(s) == set(pm.METRICS)
    assert min(s["tvl_maturity_total_usd"]) == "2026-09-20" and max(s["tvl_maturity_total_usd"]) == "2026-10-19"
    for d in s["tvl_maturity_total_usd"]:                           # totals = the sum of the four buckets, every day
        assert s["tvl_maturity_total_usd"][d] == pytest.approx(sum(s[b[0]][d] for b in pm.BUCKETS))
    assert s["tvl_maturity_le_30d_usd"]["2026-10-19"] == 1.0          # A, 6 days out
    assert s["tvl_maturity_31_90d_usd"]["2026-10-19"] == 100010.0     # B (73) + G (88)
    assert s["tvl_maturity_91_180d_usd"]["2026-10-19"] == 100.0
    assert s["tvl_maturity_gt_180d_usd"]["2026-10-19"] == 1000.0
    assert s["tvl_maturity_le_30d_usd"]["2026-09-30"] == 10001.0      # E's last live day + A (25 days out)
    assert s["tvl_maturity_le_30d_usd"]["2026-10-01"] == 1.0          # E expired: gone, and no refusal
    assert s["tvl_maturity_31_90d_usd"]["2026-10-09"] == 10.0         # before G was listed
    assert out.frame()["source"].unique().tolist() == [pm.SRC]
    assert not http.history_calls("F")                                # expired before the window: never read
    assert daily.marks == {"pendle_maturity:Pendle": "2026-10-20"}
    # once a day: a second run the same day reads nothing
    n = len(http.calls)
    out2 = FetchOutput()
    make(clock, http, daily)._project("Pendle", spec, out2)
    assert len(http.calls) == n and len(out2.current) == 5


def test_a_hole_in_one_market_refuses_that_day_only(env):
    clock, spec, _ = env
    out = FetchOutput()
    make(clock, FakeHttp(clock, holes={("C", "2026-10-05")}))._project("Pendle", spec, out)
    s = stored(out)
    for m in pm.METRICS:
        assert "2026-10-05" not in s[m]                               # refused for EVERY bucket
        assert "2026-10-04" in s[m] and "2026-10-06" in s[m]
    msg = next(e.message for e in out.log if "tvl_maturity_total_usd" in e.message)
    assert "1 day(s) REFUSED" in msg


def test_checkpoint_and_resume_across_two_budget_limited_runs(env):
    clock, spec, tmp = env
    spec = {**spec, "budget_s": 45}
    http = FakeHttp(clock, step=10.0)                                 # every call takes 10 s of fake time
    out1 = FetchOutput()
    make(clock, http)._project("Pendle", spec, out1)
    assert out1.frame().empty                                         # a partial first read stores nothing
    e = out1.log[-1]
    assert e.status == "skipped" and "FIRST READ IN PROGRESS" in e.message and "still to read" in e.message
    st = json.loads(Path(spec["state_path"]).read_text())
    done1 = {v["name"] for v in st["markets"].values() if v.get("through")}
    assert 0 < len(done1) < 6 and "F" not in {v["name"] for v in st["markets"].values()}
    # never-read markets go latest-expiry first, so the recent days complete first
    assert done1 == {"D", "C", "G", "B"}           # 4 reads of 10 s fit after the list; A and E wait
    # the second run resumes: the markets read before are NOT read again
    calls_before = {n: len(http.history_calls(n)) for n in "ABCDEG"}
    out2 = FetchOutput()
    make(clock, http)._project("Pendle", {**spec, "budget_s": 1000}, out2)
    for n in done1:
        assert len(http.history_calls(n)) == calls_before[n] == 1
    assert len(stored(out2)["tvl_maturity_total_usd"]) == 30


def test_daily_run_reads_live_markets_from_their_last_day(env, monkeypatch):
    clock, spec, _ = env
    http = FakeHttp(clock)
    make(clock, http)._project("Pendle", spec, FetchOutput())
    tomorrow = TODAY + pd.Timedelta(days=1)
    monkeypatch.setattr(pm, "today", lambda: tomorrow)
    http.today = tomorrow
    n0 = len(http.calls)
    out = FetchOutput()
    make(clock, http)._project("Pendle", spec, out)
    hist = [(u, p) for u, p in http.calls[n0:] if "historical-data" in u]
    assert sorted(u.rsplit("/", 2)[1] for u, _ in hist) == sorted(["0xa", "0xb", "0xc", "0xd", "0x9"])  # live only
    assert {p["timestamp_start"] for _, p in hist} == {"2026-10-17T00:00:00Z"}   # through 10-19, less the overlap
    assert max(stored(out)["tvl_maturity_total_usd"]) == "2026-10-20"


# ================================================================================ rate-limit pacing
def test_pacer_counts_units_and_sleeps_to_the_reset():
    now = [1000.0]
    p = pm.CuPacer(wall=lambda: now[0])
    hdr = {"x-computing-unit": "2", "x-ratelimit-limit": "200", "x-ratelimit-remaining": "199",
           "x-ratelimit-reset": "1030", "x-ratelimit-weekly-remaining": "150000"}
    assert p.wait(2) == 0.0                                           # nothing known yet: go
    for _ in range(94):
        p.after(hdr)                                                  # 188 CU spent this window
    assert p.spent == 188 and p.remaining() == 12                     # own count beats the server's stale 199
    assert p.wait(2) == 0.0                                           # 12 - 2 = 10 = headroom: still fine
    p.after(hdr)
    assert p.wait(2) == pytest.approx(31.0)                           # 30 s to the reset, +1
    now[0] = 1031.0
    assert p.wait(2) == 0.0 and p.remaining() == 200                  # the window turned over
    p.after({**hdr, "x-ratelimit-reset": "1090"})
    assert p.spent == 2                                               # a new reset value starts a new count
    p.after({**hdr, "x-ratelimit-reset": "1090", "x-ratelimit-remaining": "5"})
    assert p.remaining() == 5 and p.wait(2) == pytest.approx(60.0)    # the server's lower count wins too
    assert p.total_cu == 194 and p.costs == {2: 97}
    assert not p.weekly_low()
    p.after({**hdr, "x-ratelimit-weekly-remaining": "100"})
    assert p.weekly_low()


def test_get_sleeps_when_the_window_is_low_and_never_past_the_deadline(env):
    clock, spec, _ = env
    http = FakeHttp(clock)
    f = make(clock, http)
    p = pm.CuPacer(wall=lambda: WALL0 + clock[0])
    reset = int(WALL0 + clock[0]) + 20
    p.after({"x-computing-unit": "195", "x-ratelimit-limit": "200", "x-ratelimit-reset": str(reset)})
    f._get(p, f"{pm.API}/v2/markets/all", {"limit": 100, "skip": 0}, 2, deadline=clock[0] + 100)
    assert f.sleeps == [pytest.approx(21.0)] and len(http.calls) == 1
    p.after({"x-computing-unit": "195", "x-ratelimit-limit": "200", "x-ratelimit-reset": str(int(WALL0 + clock[0]) + 50)})
    with pytest.raises(BudgetExhausted):
        f._get(p, f"{pm.API}/v2/markets/all", {"limit": 100, "skip": 0}, 2, deadline=clock[0] + 10)
    assert len(http.calls) == 1                                       # refused BEFORE calling, not a 429


def test_a_run_paced_by_the_window_sleeps_instead_of_overrunning(env):
    clock, spec, _ = env

    class Tight(FakeHttp):
        def _hdr(self, cost):                                        # 50 CU windows: the run must wait them out
            return {**super()._hdr(cost), "x-ratelimit-limit": "50", "x-computing-unit": "20"}
    http = Tight(clock)
    f = make(clock, http)
    out = FetchOutput()
    f._project("Pendle", spec, out)
    assert f.sleeps and all(0 < s <= 61 for s in f.sleeps)
    assert len(stored(out)["tvl_maturity_total_usd"]) == 30


def test_a_paging_shortfall_stores_nothing(env):
    clock, spec, _ = env

    class Short(FakeHttp):
        def get(self, url, params=None, headers=None, deadline=None):
            j = super().get(url, params, headers, deadline)
            if url.endswith("/v2/markets/all"):
                j["total"] = 101
            return j
    out = FetchOutput()
    make(clock, Short(clock))._project("Pendle", spec, out)
    assert out.frame().empty and out.log[-1].status == "failed" and "paging returned" in out.log[-1].message
