"""
tests/test_g8_subsidy_targeting.py — fetch/subsidy_targeting.py (G8, 2026-10-20): Aerodrome and Pendle emissions against
each pool's own fees, per complete epoch. Pure functions and fake transports only — no network.
"""
from __future__ import annotations

import json
import urllib.parse

import pytest
from eth_abi import decode, encode

from fetch import subsidy_targeting as st
from fetch.base import FetchOutput

WEEK = st.WEEK

# RewardsSugar.epochsByAddress(2, 1, 0xcDAC0d6c…) on Base, read live 2026-10-20 ~09:20 UTC through mainnet.base.org:
# the two COMPLETE epochs before the one in progress (10-08 and 10-01). The research read of 09:09
# (q6a_rewardssugar_ethcall.json, offset 0) decoded the same two records — final once an epoch has ended.
RAW_SUGAR = (
    "0x0000000000000000000000000000000000000000000000000000000000000020000000000000000000000000000000"
    "000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000000000"
    "400000000000000000000000000000000000000000000000000000000000000200000000000000000000000000000000"
    "000000000000000000000000006ac6dd00000000000000000000000000cdac0d6c6c59727a65f871236188350531885c"
    "4300000000000000000000000000000000000000000001f1d4973d2268b0b6375e000000000000000000000000000000"
    "000000000000000000004545fea2a3bbe300000000000000000000000000000000000000000000000000000000000000"
    "c00000000000000000000000000000000000000000000000000000000000000120000000000000000000000000000000"
    "0000000000000000000000000000000001000000000000000000000000311935cd80b76769bf2ecc9d8ab7635b2139cf"
    "820000000000000000000000000000000000000000000000000000000002d141d3000000000000000000000000000000"
    "000000000000000000000000000000000200000000000000000000000042000000000000000000000000000000000000"
    "060000000000000000000000000000000000000000000000000f7bee5a6c2bae23000000000000000000000000833589"
    "fcd6edb6e08f4c7c32d4f71b54bda0291300000000000000000000000000000000000000000000000000000000a04416"
    "9d000000000000000000000000000000000000000000000000000000006abda280000000000000000000000000cdac0d"
    "6c6c59727a65f871236188350531885c430000000000000000000000000000000000000000000261aab8a5e5f8485e15"
    "e10000000000000000000000000000000000000000000000000044adce43ca08c9000000000000000000000000000000"
    "00000000000000000000000000000000c000000000000000000000000000000000000000000000000000000000000000"
    "e00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000200000000000000000000000042000000000000000000000000000000000000"
    "060000000000000000000000000000000000000000000000000f96f498852ed1e9000000000000000000000000833589"
    "fcd6edb6e08f4c7c32d4f71b54bda0291300000000000000000000000000000000000000000000000000000000b2cfbd"
    "ec"
)
RAW_CALLDATA = ("0x8878d06c0000000000000000000000000000000000000000000000000000000000000002"
                "0000000000000000000000000000000000000000000000000000000000000001"
                "000000000000000000000000cdac0d6c6c59727a65f871236188350531885c43")
POOL = "0xcdac0d6c6c59727a65f871236188350531885c43"
WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"


# ===================================================================================================== ABI
def test_selectors_match_the_verified_abi():
    assert st.selector("epochsByAddress(uint256,uint256,address)").hex() == "8878d06c"
    assert st.selector("epochsLatest(uint256,uint256)").hex() == "d94b9bc6"
    assert st.selector("rewardData(address)").hex() == "48e5d9f8"
    assert st.selector("aggregate3((address,bool,bytes)[])").hex() == "82ad56cb"
    assert "0x" + st.sugar_calldata(2, 1, POOL).hex() == RAW_CALLDATA


def test_decode_recorded_sugar_return():
    rows = st.decode_sugar(RAW_SUGAR)
    assert [r["ts"] for r in rows] == [1791417600, 1790812800]           # 2026-10-08, 2026-10-01: complete, newest first
    a, b = rows
    assert a["lp"] == POOL and a["votes"] == 2350937749634346439292766
    assert a["emissions"] == 19498733345684451
    assert a["bribes"] == [("0x311935cd80b76769bf2ecc9d8ab7635b2139cf82", 47268307)]
    assert a["fees"] == [(WETH, 1115747404833599011), (USDC, 2688816797)]
    assert b["emissions"] == 19331399828506825 and b["bribes"] == []
    assert b["fees"] == [(WETH, 1123354092982161897), (USDC, 2999959020)]


def test_emission_rate_is_per_second_times_week():
    # gauge.rewardRateByEpoch is AERO wei per SECOND: 19498733345684451 x 604800 / 1e18 = 11,792.83 AERO for 10-08
    assert st.rate_to_tokens(19498733345684451) == pytest.approx(11792.833, abs=1e-3)
    assert st.rate_to_tokens(10 ** 18, seconds=1) == 1.0


def test_late_notify_is_integrated_not_rate_times_week():
    E = 1790812800
    # notified 3600 s before the epoch ended at 1e16 wei/s: 36 AERO streamed, not 6,048 (rate x WEEK)
    assert st.streamed_tokens(10 ** 16, 0, E + WEEK - 3600, E) == pytest.approx(36.0)
    # unchanged since the early block: rate x WEEK
    assert st.streamed_tokens(10 ** 15, 10 ** 15, None, E) == pytest.approx(604.8)
    # re-notified half way from 1e15 to 3e15: both segments
    assert st.streamed_tokens(3 * 10 ** 15, 10 ** 15, E + WEEK // 2, E) == pytest.approx(302.4 + 907.2)


# ===================================================================================================== epochs
def test_only_complete_epochs():
    E0 = 1792022400                                     # 2026-10-15 00:00 UTC, a Thursday: the epoch in progress
    assert st.complete_epochs(E0 + 5, 2) == [E0 - WEEK, E0 - 2 * WEEK]
    assert st.complete_epochs(E0, 1) == [E0 - WEEK]     # at the flip itself E0 has only begun
    assert st.complete_epochs(E0 - 1, 1) == [E0 - 2 * WEEK]
    assert st.day_of(E0 - WEEK) == "2026-10-08"


# ===================================================================================================== valuation
PRICES = {USDC: {"price": 1.0, "symbol": "USDC", "confidence": 0.99},
          WETH: {"price": 2000.0, "symbol": "WETH", "confidence": 0.99}}
DECS = {USDC: 6, WETH: 18, "0xabc": 18}


def test_value_amounts_priced():
    usd, miss = st.value_amounts([(USDC, 1000 * 10 ** 6), (WETH, 10 ** 18)], DECS, PRICES)
    assert miss == [] and usd == pytest.approx(3000.0)


def test_unpriced_fee_token_is_unknown_never_zero():
    usd, miss = st.value_amounts([(USDC, 10 ** 6), ("0xabc", 5 * 10 ** 18)], DECS, PRICES)
    assert usd is None and "no price" in miss[0]
    usd, miss = st.value_amounts([("0xdef", 1)], DECS, {"0xdef": {"price": 1.0}})
    assert usd is None and "decimals" in miss[0]
    low = {USDC: {"price": 1.0, "confidence": 0.5}}
    usd, miss = st.value_amounts([(USDC, 10 ** 6)], DECS, low, min_confidence=0.9)
    assert usd is None and "confidence" in miss[0]
    # a zero amount needs no price
    assert st.value_amounts([("0xabc", 0)], DECS, {}) == (0.0, [])


def _row(name, em, fees):
    return {"pool": name, "name": name, "emissions_tokens": em, "emissions_usd": em, "fees_usd": fees, "votes": None}


def test_cover_and_covered_share_arithmetic():
    rows = [_row("a", 100.0, 250.0), _row("b", 300.0, 30.0), _row("c", 0.0, 999.0)]   # c receives nothing: left out
    s = st.summarize(rows)
    assert s["status"] == "ok" and s["pools"] == 2
    assert s["emissions_usd"] == pytest.approx(400.0)
    assert s["cover"] == pytest.approx(280.0 / 400.0)
    assert s["covered_share"] == pytest.approx(100.0 / 400.0)


def test_unvalued_pool_partial_then_refused_never_zero():
    rows = [_row("a", 100.0, 250.0), _row("b", 300.0, 30.0), _row("x", 10.0, None)]
    s = st.summarize(rows, max_unvalued_share=0.05)     # x: 10 / 410 = 2.4% of emissions
    assert s["status"] == "partial" and [r["pool"] for r in s["unvalued"]] == ["x"]
    assert s["unvalued_share"] == pytest.approx(10 / 410)
    assert s["emissions_usd"] == pytest.approx(410.0)                 # emissions do not depend on fees
    assert s["cover"] == pytest.approx(280.0 / 400.0)                 # x out of BOTH sides — not a 0 in the numerator
    assert s["covered_share"] == pytest.approx(100.0 / 400.0)
    s = st.summarize(rows, max_unvalued_share=0.01)
    assert s["status"] == "refused" and s["cover"] is None and s["covered_share"] is None
    assert s["emissions_usd"] == pytest.approx(410.0)


def test_unknown_emissions_refuse_everything():
    rows = [_row("a", 100.0, 250.0), {**_row("y", None, 5.0), "emissions_usd": None}]
    s = st.summarize(rows)
    assert s["status"] == "refused" and s["emissions_usd"] is None and "EMISSIONS" in s["why"]


def test_pendle_nearest_sample_integration_and_gaps():
    S, D = 1792454400, 86400
    pts = [(S + 3600, 10 ** 15, S + 30 * D), (S + 2 * D, 10 ** 15, S + 30 * D)]
    wei, why = st.pendle_emissions(pts, S, S + 3 * D, 2 * D)
    assert why == "" and wei == pytest.approx(10 ** 15 * 3 * D)
    # incentiveEndsAt caps the stream
    wei, _ = st.pendle_emissions([(S + 3600, 10 ** 15, S + D)], S, S + D + 3600, 2 * D)
    assert wei == pytest.approx(10 ** 15 * D)
    # a stretch with no sample beyond max_gap: unknown, never 0
    wei, why = st.pendle_emissions(pts, S, S + 14 * D, 2 * D)
    assert wei is None and "no rate sample" in why


# ===================================================================================================== fakes
class FakeHttp:
    """coins.llama.fi and api-v2.pendle.finance; JSON-RPC goes through FakeRpc."""

    def __init__(self, prices: dict, pendle=None, on_get=None):
        self.prices, self.pendle, self.on_get, self.calls = prices, pendle, on_get, []

    def get(self, url, params=None, headers=None, deadline=None):
        self.calls.append(url)
        if self.on_get:
            self.on_get(url)
        if "coins.llama.fi" in url:
            keys = urllib.parse.urlsplit(url).path.split("/")[-1].split(",")
            return {"coins": {k: self.prices[k.lower()] for k in keys if k.lower() in self.prices}}
        return self.pendle(url, params or {})

    def post(self, *a, **k):
        raise AssertionError("JSON-RPC must go through the injected Rpc")


def _u(x):
    return encode(["uint256"], [x])


def _a(x):
    return encode(["address"], [x])


class FakeChain:
    """Answers eth_calls by selector, at a block (Base: 2 s blocks back from the head)."""

    def __init__(self, now, head_n=10_000_000):
        self.now, self.head_n = now, head_n
        self.sugar_offsets = []
        self.handlers = {}

    def ts(self, block):
        return self.now if block == "latest" else self.now - (self.head_n - int(block, 16)) * 2

    def answer(self, to, data, block="latest"):
        fn = self.handlers.get((to.lower(), bytes(data[:4])))
        return None if fn is None else fn(bytes(data[4:]), self.ts(block))

    # the Rpc surface
    def head(self):
        return self.head_n, self.now

    def call(self, to, data, block="latest"):
        b = self.answer(to, data, block)
        if b is None:
            raise RuntimeError("reverted")
        return b

    def aggregate(self, calls, chunk=500, block="latest"):
        return [self.answer(t, d, block) for t, d in calls]

    def batch_call(self, calls, chunk=10):
        return [self.answer(t, d, b) for t, d, b in calls]


SUGAR, VOTER = "0x" + "51" * 20, "0x" + "70" * 20
XYZ, AERO = "0x" + "a1" * 20, "0x940181a94a35a4569e4529a3cdfb74e38fd98631"
PA, PB, PC, PD, PN = ("0x" + c * 20 for c in ("0a", "0b", "0c", "0d", "0e"))
GA, GB, GC, GN = ("0x" + c * 20 for c in ("ga".replace("g", "1"), "1b", "1c", "1e"))


def aero_chain(now, notify_b):
    """Pools: A (prompt, priced), B (late notify at notify_b, fee token XYZ unpriced), C (prompt, priced), D (no gauge),
    N (a gauge with no pool behind it: every call reverts)."""
    ch = FakeChain(now)
    pools = [PA, PB, PC, PD, PN]
    gauges = {PA: GA, PB: GB, PC: GC, PN: GN}
    final = {GA: 10 ** 15, GB: 10 ** 16, GC: 2 * 10 ** 15}
    fees = {PA: [(USDC, 1000 * 10 ** 6), (WETH, 10 ** 18)], PB: [(XYZ, 7 * 10 ** 18)], PC: [(USDC, 100 * 10 ** 6)]}
    toks = {PA: (USDC, WETH), PB: (XYZ, USDC), PC: (USDC, AERO)}
    sel = st.selector
    ch.handlers[(VOTER, sel("length()"))] = lambda a, t: _u(len(pools))
    ch.handlers[(VOTER, sel("pools(uint256)"))] = lambda a, t: _a(pools[decode(["uint256"], a)[0]])
    ch.handlers[(VOTER, sel("gauges(address)"))] = lambda a, t: _a(gauges.get(decode(["address"], a)[0].lower(),
                                                                              "0x" + "00" * 20))
    ch.handlers[(VOTER, sel("weights(address)"))] = lambda a, t: _u(20 * 10 ** 18)
    ch.handlers[(VOTER, sel("totalWeight()"))] = lambda a, t: _u(10 ** 27)
    for g, r in final.items():
        def rate(a, t, g=g, r=r):
            ep = decode(["uint256"], a)[0]
            return _u(0 if (g == GB and t < notify_b(ep)) else r)
        ch.handlers[(g, sel("rewardRateByEpoch(uint256)"))] = rate

    def sugar(a, t):
        limit, offset, pool = decode(["uint256", "uint256", "address"], a)
        ch.sugar_offsets.append(offset)
        ep = t // WEEK * WEEK - offset * WEEK
        g = gauges[pool.lower()]
        return encode([st.SUGAR_OUT], [[(ep, pool, 5 * 10 ** 18, final[g], [], fees[pool.lower()])]])
    ch.handlers[(SUGAR, sel("epochsByAddress(uint256,uint256,address)"))] = sugar
    for p, (t0, t1) in toks.items():
        ch.handlers[(p, sel("token0()"))] = lambda a, t, x=t0: _a(x)
        ch.handlers[(p, sel("token1()"))] = lambda a, t, x=t1: _a(x)
    for tok, dec, sym in ((USDC, 6, "USDC"), (WETH, 18, "WETH"), (XYZ, 18, "XYZ"), (AERO, 18, "AERO")):
        ch.handlers[(tok, sel("decimals()"))] = lambda a, t, d=dec: _u(d)
        ch.handlers[(tok, sel("symbol()"))] = lambda a, t, s=sym: encode(["string"], [s])
    return ch


LLAMA = {"base:" + USDC: {"price": 1.0, "symbol": "USDC", "confidence": 0.99},
         "base:" + WETH: {"price": 2000.0, "symbol": "WETH", "confidence": 0.99},
         "base:" + AERO: {"price": 1.0, "symbol": "AERO", "confidence": 0.99}}                 # XYZ: no price


def aero_spec(tmp_path, **kw):
    return {"route": "aerodrome_sugar", "sugar": SUGAR, "voter": VOTER, "rpc": "https://rpc.example", "chain": "base",
            "reward_token": "base:" + AERO, "epochs_back": 2, "budget_s": 120, "dir": str(tmp_path / "subsidy"), **kw}


E0 = 1792022400                                          # 2026-10-15, the epoch in progress


def _run(spec, chain, http, name="Aerodrome", clock=None, wall=None):
    out = FetchOutput()
    f = st.SubsidyTargeting(http=http, rpc_factory=lambda url, deadline: chain, clock=clock,
                            wall=wall or (lambda: chain.now))
    f.run([{"name": name, "subsidy_targeting": spec}], None, out)
    return out


def _points(out):
    fr = out.frame()
    return {(str(r.date.date()), r.metric): (r.value, r.source) for r in fr.itertuples()}


# ===================================================================================================== Aerodrome
def test_aerodrome_epoch_end_to_end(tmp_path):
    ch = aero_chain(E0 + 2 * 86400, notify_b=lambda ep: ep + WEEK - 3600)
    out = _run(aero_spec(tmp_path), ch, FakeHttp(LLAMA))
    assert sorted(set(ch.sugar_offsets)) == [1, 2]                     # only complete epochs: offset 0 never asked
    pts = _points(out)
    for day in ("2026-10-08", "2026-10-01"):                           # dated at the epoch START
        emis, src = pts[(day, st.M_EMIT)]
        # A 604.8 + B 36 (late: 3600 s at 1e16/s, NOT 6,048) + C 1209.6 AERO, at $1
        assert emis == pytest.approx(604.8 + 36.0 + 1209.6) and src == "subsidy_targeting:aerodrome_sugar"
        cover, csrc = pts[(day, st.M_COVER)]
        assert cover == pytest.approx((3000.0 + 100.0) / (604.8 + 1209.6))      # B out of both sides
        assert csrc.startswith("subsidy_targeting:aerodrome_sugar:PARTIAL[1 of 3 pools' fees unvalued")
        share, _ = pts[(day, st.M_SHARE)]
        assert share == pytest.approx(604.8 / (604.8 + 1209.6))
    table = st.read_table(tmp_path / "subsidy" / "aerodrome_2026-10-08.csv")
    by = {r["pool"]: r for r in table}
    assert set(by) == {PA, PB, PC}                                     # D has no gauge, N has no pool behind it
    assert by[PB]["fees_usd"] is None and by[PB]["emissions_tokens"] == pytest.approx(36.0)   # blank, never 0
    assert by[PA]["fees_usd"] == pytest.approx(3000.0) and by[PA]["votes"] == pytest.approx(5.0)
    assert by[PA]["name"] == "USDC/WETH"
    raw = (tmp_path / "subsidy" / "aerodrome_2026-10-08.csv").read_text().splitlines()
    assert raw[0] == ",".join(st.CSV_COLUMNS)
    assert any(line.startswith(f"2026-10-08,{PB},XYZ/USDC,36.0,36.0,,,") for line in raw)
    assert any("no pool behind it" in e.message for e in out.log)


def test_aerodrome_unpriced_share_over_cap_refuses_ratios(tmp_path):
    ch = aero_chain(E0 + 86400, notify_b=lambda ep: ep)                # B prompt: 6,048 AERO with unpriced fees
    out = _run(aero_spec(tmp_path, epochs_back=1), ch, FakeHttp(LLAMA))
    pts = _points(out)
    assert ("2026-10-08", st.M_COVER) not in pts and ("2026-10-08", st.M_SHARE) not in pts
    assert pts[("2026-10-08", st.M_EMIT)][0] == pytest.approx(604.8 + 6048.0 + 1209.6)
    assert any(e.status == "failed" and "REFUSED" in e.message for e in out.log)


def test_aerodrome_incremental_skip(tmp_path):
    spec = aero_spec(tmp_path)
    late = (lambda ep: ep + WEEK - 3600)
    first = _points(_run(spec, aero_chain(E0 + 86400, notify_b=late), FakeHttp(LLAMA)))
    ch = aero_chain(E0 + 2 * 86400, notify_b=late)
    http = FakeHttp(LLAMA)
    out = _run(spec, ch, http)
    assert ch.sugar_offsets == [] and http.calls == []                 # nothing read again
    assert _points(out).keys() == first.keys() and len(first) == 6
    for k, (v, src) in first.items():                                  # the summary re-derived from the tables
        assert _points(out)[k][0] == pytest.approx(v) and _points(out)[k][1] == src
    assert any("already tabled" in e.message for e in out.log)


def test_restored_refusal_is_not_a_daily_failure(tmp_path):
    spec = aero_spec(tmp_path, epochs_back=1)
    _run(spec, aero_chain(E0 + 86400, notify_b=lambda ep: ep), FakeHttp(LLAMA))   # refused ratios (B unpriced, 77%)
    out = _run(spec, aero_chain(E0 + 2 * 86400, notify_b=lambda ep: ep), FakeHttp(LLAMA))
    assert not [e for e in out.log if e.status == "failed"]
    assert any(e.status == "skipped" and "REFUSED" in e.message for e in out.log)


def test_aerodrome_budget_stops_cleanly(tmp_path):
    clock = [0.0]
    ch = aero_chain(E0 + 86400, notify_b=lambda ep: ep)
    http = FakeHttp(LLAMA, on_get=lambda url: clock.__setitem__(0, clock[0] + 500))   # the first price read spends it
    out = _run(aero_spec(tmp_path, budget_s=100), ch, http, clock=lambda: clock[0])
    files = sorted(p.name for p in (tmp_path / "subsidy").iterdir())
    assert files == ["aerodrome_2026-10-08.csv"]                       # the newest epoch is complete and kept
    assert any("left for the next run (2026-10-01)" in e.message for e in out.log)


# ===================================================================================================== Pendle
PS, DAY = 1792454400, 86400                              # 2026-10-20 00:00 UTC, an sPENDLE epoch start
GAUGE = "0x" + "9c" * 20
M1, M2, M3, M4 = ("0x" + c * 20 for c in ("e1", "e2", "e3", "e4"))
PENDLE_KEY = "ethereum:0x808507121b80c02388fad14726482e061b8da827"


def _iso(t):
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def pendle_api(now_ref):
    def answer(url, params):
        now = now_ref[0]
        start = PS + (now - PS) // (14 * DAY) * 14 * DAY
        if url.endswith("/markets/active"):
            return {"markets": [{"address": m, "name": n} for m, n in ((M1, "one"), (M2, "two"), (M3, "three"),
                                                                       (M4, "four"))]}
        assert url.endswith("/statistics/market-fees") and params["includeOngoing"] == "true"
        eps = [{"epochStart": _iso(start), "epochEnd": _iso(start + 14 * DAY), "isOngoing": True, "markets": []}]
        for k in range(1, int(params["epochs"]) + 1):
            s = start - k * 14 * DAY
            eps.append({"epochStart": _iso(s), "epochEnd": _iso(s + 14 * DAY), "isOngoing": False, "markets": [
                {"chainId": 1, "address": M1, "name": "one", "expiry": "2027-01-01T00:00:00.000Z", "poolFeeUsd": 5000.0},
                {"chainId": 1, "address": M2, "name": "two", "expiry": "2027-01-01T00:00:00.000Z", "poolFeeUsd": 100.0},
                {"chainId": 42161, "address": M3, "name": "elsewhere", "expiry": "2027-01-01T00:00:00.000Z",
                 "poolFeeUsd": 9.0}]})                                 # M3 has no chain-1 fee row: unknown, not 0
        return {"epochs": eps}
    return answer


def pendle_chain(now_ref):
    ch = FakeChain(0)
    rates = {M1: (10 ** 15, PS + 60 * DAY), M2: (10 ** 15, PS + 7 * DAY), M3: (10 ** 14, PS + 60 * DAY), M4: (0, 0)}

    def rd(a, t):
        ps, ends = rates[decode(["address"], a)[0].lower()]
        return encode(["uint128"] * 4, [ps, 0, now_ref[0], ends])
    ch.handlers[(GAUGE, st.selector("rewardData(address)"))] = rd
    return ch


def pendle_spec(tmp_path, **kw):
    return {"route": "pendle_gauge", "api": "https://api.example/core", "chain_id": 1, "rpc": "https://rpc.example",
            "gauge": GAUGE, "reward_token": PENDLE_KEY, "budget_s": 120, "dir": str(tmp_path / "subsidy"), **kw}


def test_pendle_samples_then_stores_the_completed_epoch(tmp_path):
    now = [PS + 3600]
    http = FakeHttp({PENDLE_KEY: {"price": 2.0, "symbol": "PENDLE", "confidence": 0.99}}, pendle=pendle_api(now))
    ch = pendle_chain(now)
    spec = pendle_spec(tmp_path, max_unvalued_share=0.10)
    out = _run(spec, ch, http, name="Pendle", wall=lambda: now[0])
    assert out.frame().empty                                          # sampling only: nothing complete yet
    assert any("no fully sampled epoch complete yet" in e.message for e in out.log)
    for d in range(2, 14, 2):                                          # a run every two days (gaps ≤ max_gap_s)
        now[0] = PS + d * DAY
        _run(spec, ch, http, name="Pendle", wall=lambda: now[0])
    now[0] = PS + 14 * DAY + 3600                                     # the epoch has ended
    out = _run(spec, ch, http, name="Pendle", wall=lambda: now[0])
    pts = _points(out)
    m1, m2, m3 = 1209.6, 604.8, 120.96                                # M2's incentive ends at day 7; M4 rate 0: out
    emis, src = pts[("2026-10-20", st.M_EMIT)]
    assert emis == pytest.approx((m1 + m2 + m3) * 2.0) and src == "subsidy_targeting:pendle_gauge"
    cover, csrc = pts[("2026-10-20", st.M_COVER)]
    assert cover == pytest.approx((5000.0 + 100.0) / ((m1 + m2) * 2.0))
    assert ":PARTIAL[1 of 3 pools' fees unvalued" in csrc and "three" in csrc
    share, _ = pts[("2026-10-20", st.M_SHARE)]
    assert share == pytest.approx(m1 / (m1 + m2))                    # M1 covers: 5000 >= 2419.2; M2 does not
    rows = {r["pool"]: r for r in st.read_table(tmp_path / "subsidy" / "pendle_2026-10-20.csv")}
    assert set(rows) == {M1, M2, M3} and rows[M3]["fees_usd"] is None and rows[M1]["votes"] is None
    state = json.loads((tmp_path / "subsidy" / "pendle_rates_2026-10-20.json").read_text())
    assert len(state["samples"]) == 7


def test_pendle_sparse_samples_refuse(tmp_path):
    now = [PS + 3600]
    http = FakeHttp({PENDLE_KEY: {"price": 2.0, "confidence": 0.99}}, pendle=pendle_api(now))
    ch = pendle_chain(now)
    spec = pendle_spec(tmp_path)
    _run(spec, ch, http, name="Pendle", wall=lambda: now[0])           # one sample at +1 h, then nothing for 14 days
    now[0] = PS + 14 * DAY + 3600
    out = _run(spec, ch, http, name="Pendle", wall=lambda: now[0])
    pts = _points(out)
    assert not any(day == "2026-10-20" for day, _m in pts)              # every metric refused — never filled with 0
    assert any(e.status == "failed" and "EMISSIONS" in e.message for e in out.log)
