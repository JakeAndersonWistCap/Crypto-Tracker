"""R21 RUNFIX (Jake 2026-11-25, from his 24 Nov store run): World Chain's TokenJar as a named gap with a cap; coins.llama.fi
out of the price cross-check (an on-chain market instead); "no Http client" relabelled; the dispatcher's start order."""
from __future__ import annotations

import threading

import pandas as pd
import pytest

import config
import credibility
import fetch
from fetch import base, price_onchain, tokenjar
from fetch.base import FetchOutput


# ============================================================ World Chain: a named gap with a cap ====================
class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _rec(usd, transfers=1):
    return {"stage": "read", "b0": 1, "b1": 2, "route": "x", "usd": usd, "lp_usd": 0.0, "lp_one_side_usd": 0.0,
            "transfers": transfers, "unpriced_transfers": 0, "unpriced": [], "n_unpriced_kinds": 0, "kinds": 1,
            "native": ["0", 0], "native_route": "x", "native_usd": 0.0}


def _spec(through="2026-11-05", max_tries=3):
    return {**tokenjar.DEFAULTS, "state_path": "state.json", "prices_path": "prices.json", "budget_s": 1000,
            "balance_budget_s": 20, "balance_window_days": 3, "metric": "tokenjar_inflow_usd",
            "count_metric": "tokenjar_inflow_transfers", "unpriced_metric": "tokenjar_unpriced_transfers",
            "balance_metric": "tokenjar_balance_usd",
            "chains": {"a": {"jar": "0x" + "0a" * 20, "from_day": "2026-11-01", "logs": [{"kind": "rpc"}],
                             "state_rpc": ["https://a"], "llama": "a", "native_coin": "coingecko:ethereum"},
                       "wc": {"jar": "0x" + "0b" * 20, "from_day": "2026-11-01", "logs": [{"kind": "blockscout"}],
                              "state_rpc": ["https://b"], "llama": "wc", "native_coin": "coingecko:ethereum",
                              "named_gap": {"through": through, "max_tries": max_tries, "share": "~0.2%",
                                            "label": "World Chain", "measured": "test"}}}}


def test_world_chain_named_gap_is_declared_with_its_measured_size():
    g = config.TOKENJAR_INFLOWS["Uniswap"]["chains"]["worldchain"]["named_gap"]
    assert g["through"] == "2026-07-25" and g["share"] == "~0.2%" and g["max_tries"] == 3
    # the measurement: $46,481 of $20,216,174 (0.23%); 7 releases x 2,000 of 14,672,000 UNI (0.095%)
    assert 46_481 / 20_216_174 == pytest.approx(0.0023, abs=5e-5)
    assert 7 * 2_000 / 14_672_000 == pytest.approx(0.00095, abs=5e-6)
    # every other chain keeps no gap: the cap is World Chain's alone
    assert [c for c, cs in config.TOKENJAR_INFLOWS["Uniswap"]["chains"].items() if cs.get("named_gap")] == ["worldchain"]


def test_capped_days_are_not_asked_and_their_rows_are_stored_with_the_label(tmp_path, monkeypatch):
    clock = _Clock()
    r = tokenjar.TokenJarInflows(rpc_post=lambda u, p: {"result": "0x0"}, clock=clock, root=tmp_path, env_rpcs={})
    monkeypatch.setattr(tokenjar, "today", lambda: pd.Timestamp("2026-11-09"))
    asked = []

    def read_day(chain, cs, day):
        asked.append((chain, day))
        return _rec(10.0 if chain == "a" else 1.0)
    monkeypatch.setattr(r, "read_day", read_day)
    monkeypatch.setattr(r, "read_balance", lambda chain, cs, day: {
        "usd": 1.0, "block": 1, "held_kinds": 0, "unpriced": [], "n_unpriced": 0, "seen_kinds": 0, "seen_from": None})
    out = FetchOutput()
    r._project("Uniswap", _spec(), out, unbounded=False)
    # World Chain is asked only after the cap (11-06..11-08); chain a every day
    assert sorted(d for c, d in asked if c == "wc") == ["2026-11-06", "2026-11-07", "2026-11-08"]
    assert sorted(d for c, d in asked if c == "a") == [f"2026-11-0{i}" for i in range(1, 9)]
    f = out.frame()
    rows = f[f["metric"] == "tokenjar_inflow_usd"].assign(d=lambda x: pd.to_datetime(x["date"]).dt.strftime("%Y-%m-%d"))
    by = dict(zip(rows["d"], rows["value"]))
    assert len(by) == 8                                   # was: 11-01..11-05 not stored (waiting on World Chain)
    assert by["2026-11-03"] == 10.0 and by["2026-11-07"] == 11.0          # a gap day sums the chains read
    src = dict(zip(rows["d"], rows["source"].astype(str)))
    assert "World Chain not read since 2026-11-05: ~0.2% of inflow" in src["2026-11-03"]
    assert ":PARTIAL" not in src["2026-11-03"]                             # declared, not PARTIAL
    assert "World Chain not read" not in src["2026-11-07"]
    # the Run Log line names it too
    assert any("World Chain not read since 2026-11-05: ~0.2% of inflow" in str(e.message) for e in out.log)
    # the balance read counts World Chain complete from the day after its gap
    assert tokenjar.first_read_day(_spec()["chains"]["wc"]) == "2026-11-06"
    assert tokenjar.first_read_day(_spec()["chains"]["a"]) == "2026-11-01"


def test_a_later_day_refused_max_tries_runs_joins_the_gap_and_a_budget_stop_does_not_count():
    cs = _spec()["chains"]["wc"]
    cst = {"days": {}}
    tokenjar.count_gap_try(cst, cs, "2026-11-07", "time budget reached inside the day")
    assert cst["days"] == {}                              # a budget stop is not a refusal
    for _ in range(2):
        tokenjar.count_gap_try(cst, cs, "2026-11-07", "HTTP 429 ... asks for a 3,038s wait")
    assert cst["days"]["2026-11-07"]["stage"] == "unread" and not tokenjar.in_named_gap(cs, "2026-11-07",
                                                                                         cst["days"]["2026-11-07"])
    tokenjar.count_gap_try(cst, cs, "2026-11-07", "HTTP 429 ... asks for a 3,038s wait")
    rec = cst["days"]["2026-11-07"]
    assert rec["stage"] == "gap" and rec["tries"] == 3 and tokenjar.in_named_gap(cs, "2026-11-07", rec)
    assert "1 later day(s) refused on 3 runs, 2026-11-07..2026-11-07" in tokenjar.gap_label(cs, cst)
    # a gap day already read is counted, never dropped; a chain with no gap is never in one
    assert not tokenjar.in_named_gap(cs, "2026-11-02", _rec(1.0))
    assert not tokenjar.in_named_gap(_spec()["chains"]["a"], "2026-11-02", None)


def test_day_total_waits_for_an_unread_day_after_the_cap(tmp_path):
    r = tokenjar.TokenJarInflows(rpc_post=lambda u, p: None, root=tmp_path, env_rpcs={})
    r.spec, r.st = _spec(), {"version": 1, "chains": {}, "balances": {}}
    r.cst("a")["days"]["2026-11-07"] = _rec(5.0)
    assert r.day_total("2026-11-07") is None              # World Chain's 11-07 is after the cap: still owed
    r.cst("a")["days"]["2026-11-02"] = _rec(5.0)
    t = r.day_total("2026-11-02")
    assert t["usd"] == 5.0 and "World Chain not read since 2026-11-05" in t["source"]


# ============================================================ coins.llama.fi out of the price cross-check ===========
def test_price_cross_check_has_no_defillama_row_and_an_onchain_row_where_coinbase_has_none():
    for name in config.CREDIBILITY_PROJECTS:
        rows = credibility.price_inputs(name)
        assert "in_price_llama" not in rows
        assert not any("llama" in str(v) for v in rows.values()), name
        product = (config.CREDIBILITY_XREF.get("coinbase_product") or {}).get(name)
        assert product or "in_price_onchain" in rows, f"{name}: no independent price check"
        for v in rows.values():
            assert v["ref"]["tol"] == 2.0 and not v["ref"].get("same_source")    # the same tolerance; independent
    assert set(credibility.price_inputs("Maple")) == {"in_price_onchain"}
    assert set(credibility.price_inputs("Uniswap")) == {"in_price"}                # Coinbase UNI-USD, as before
    assert "HyperCore spot @107" in credibility.price_inputs("Hyperliquid")["in_price_onchain"]["ref"]["source"]
    assert credibility.ALIASES["@price"] == ("in_price", "in_price_onchain")


def test_xref_no_longer_calls_coins_llama_fi(tmp_path):
    from fetch.xref import CrossRefs
    asked = []

    class _Http:
        def get(self, url, params=None, **_):
            asked.append(url)
            return []
    sl = pd.DataFrame({"project": ["Maple"], "metric": ["price_usd"], "date": [base.today().normalize()],
                       "value": [1.0], "source": ["coingecko"], "fetched_at": [pd.Timestamp.utcnow().isoformat()]})

    class _Daily:
        def due(self, *a):
            return True

        def ever(self, *a):
            return True

        def get(self, *a):
            return None

        def done(self, *a):
            pass

        def set(self, *a):
            pass
    CrossRefs(http=_Http(), daily=_Daily(), robots=lambda u: (True, ""), stored_long=sl).run(
        [config.PROJECT_BY_NAME[n] for n in config.CREDIBILITY_PROJECTS], None, FetchOutput())
    assert asked and not [u for u in asked if "llama" in u]


def test_pool_price_math():
    # USDC/WETH 0.05%: token0 USDC (6), token1 WETH (18); ETH at $3,000 -> 1/3000 WETH per USDC
    sqrtp = int(((1 / 3000) * 10 ** (18 - 6)) ** 0.5 * 2 ** 96)
    assert price_onchain.v3_price(sqrtp, 6, 18, token_is_token0=False) == pytest.approx(3000.0, rel=1e-9)
    assert price_onchain.v3_price(sqrtp, 6, 18, token_is_token0=True) == pytest.approx(1 / 3000 * 1e12 / 1e12, rel=1e-9)
    assert price_onchain.v2_price(2_000 * 10 ** 18, 100 * 10 ** 18, 18, 18) == pytest.approx(0.05)
    with pytest.raises(ValueError):
        price_onchain.v2_price(0, 1, 18, 18)


class _Chain:
    """A fake EVM chain: block n has timestamp T0 + 12 n; a v3 pool (token = token0, 18/18) at 0.0005 WETH; the
    USDC/WETH pool at $3,000; eth_call answers the same at any block."""
    T0 = 1_700_000_000

    def __init__(self, head=1_000_000):
        self.head, self.calls = head, []

    def __call__(self, url, payload):
        m, p = payload["method"], payload["params"]
        self.calls.append(m)
        if m == "eth_blockNumber":
            return {"result": hex(self.head)}
        if m == "eth_getBlockByNumber":
            return {"result": {"timestamp": hex(self.T0 + 12 * int(p[0], 16))}}
        if m == "eth_call":
            to, data = p[0]["to"].lower(), p[0]["data"][2:]
            token = config.PRICE_ONCHAIN["Maple"]["token"].lower()
            if data == price_onchain.SEL["token0"]:
                return {"result": "0x" + (token if to == "0xpool" else "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
                                          ).rjust(64, "0")}
            if to == "0xpool":
                sq = int(0.0005 ** 0.5 * 2 ** 96)
            else:
                sq = int(((1 / 3000) * 1e12) ** 0.5 * 2 ** 96)
            return {"result": "0x" + hex(sq)[2:].rjust(64, "0") + "0" * 64 * 6}
        raise AssertionError(m)


def test_onchain_reader_reads_the_block_of_the_instant_and_values_weth_in_dollars(monkeypatch):
    ch = _Chain()
    r = price_onchain.OnchainPrices(rpc_post=ch, env_rpcs={})
    ts = _Chain.T0 + 12 * 900_000 + 5                    # 5 s after block 900,000
    assert r.block_at("ethereum", ts) == (900_000, _Chain.T0 + 12 * 900_000)
    spec = {**config.PRICE_ONCHAIN["Maple"], "pool": "0xpool"}
    px, src, inst = r.usd_at(spec, ts)
    assert px == pytest.approx(0.0005 * 3000, rel=1e-6) and "block 900000" in src and inst == _Chain.T0 + 12 * 900_000
    n = len(ch.calls)
    r.usd_at(spec, ts)                                   # the block of an instant is found once per run (shared)
    assert ch.calls[n:].count("eth_getBlockByNumber") == 0


def test_onchain_reader_stores_only_days_the_store_lacks_and_names_a_failure(monkeypatch):
    monkeypatch.setattr(price_onchain, "today", lambda: pd.Timestamp("2026-11-24"))
    days = [pd.Timestamp("2026-11-24") - pd.Timedelta(days=k) for k in range(1, 8)]
    sl = pd.DataFrame({"project": "Maple", "metric": "price_usd_onchain", "date": days[:5], "value": 1.0,
                       "source": "onchain:x", "fetched_at": "2026-11-20T00:00:00Z"})
    r = price_onchain.OnchainPrices(stored_long=sl, rpc_post=lambda u, p: {"error": "x"}, env_rpcs={})
    assert [str(d.date()) for d in r.days_wanted("Maple")] == ["2026-11-17", "2026-11-18"]
    asked = []
    monkeypatch.setattr(r, "usd_at", lambda spec, ts: asked.append(ts) or (
        (_ for _ in ()).throw(RuntimeError("no endpoint answered")) if len(asked) == 2 else (1.5, "uniswap_v3:x", ts)))
    out = FetchOutput()
    r.run([config.PROJECT_BY_NAME["Maple"]], None, out)
    f = out.frame()
    assert list(f["metric"]) == ["price_usd_onchain"] and float(f["value"].iloc[0]) == 1.5
    assert any("not read (asked again next run)" in str(e.message) and "no endpoint answered" in str(e.message)
               for e in out.log)
    # a first read takes FIRST_DAYS
    r2 = price_onchain.OnchainPrices(stored_long=None, env_rpcs={})
    assert len(r2.days_wanted("Maple")) == price_onchain.FIRST_DAYS


def test_same_instant_matches_an_onchain_reading_at_its_block():
    day = pd.Timestamp("2026-11-20")
    long = pd.DataFrame({
        "project": ["Maple", "Maple"], "metric": ["price_usd", "price_usd_onchain_at"], "date": [day, day],
        "value": [0.10, 0.101], "source": ["coingecko", "onchain:uniswap_v3:x block 1@2026-11-20T18:41:59Z"],
        "fetched_at": ["2026-11-20T18:42:00Z", "2026-11-21T00:00:00Z"]})
    hit = credibility.same_instant(long, "Maple", day, "price_usd_onchain")
    assert hit and hit[0] == 0.10 and hit[1] == 0.101


# ============================================================ "no Http client" relabelled ===========================
def test_a_tier_with_no_web_calls_reads_as_local_not_as_a_fault():
    t = threading.Thread(target=lambda: None)

    class _Local:
        def run(self, *a):
            pass
    assert fetch._http_summary({"adapter": _Local(), "thread": t}) == "no web calls (local/derived tier)"
    assert fetch._http_summary({"adapter": None, "thread": t}).startswith("FAULT: the adapter was not built")
    assert fetch._http_summary({"thread": t}).startswith("FAULT")

    class _WithHttp:
        http = base.Http()
    assert fetch._http_summary({"adapter": _WithHttp(), "thread": t}) == "no HTTP calls"    # unchanged


def test_web_calls_made_outside_an_http_client_are_counted_against_the_tier(monkeypatch):
    import requests

    class _Resp:
        status_code = 200

    def fake_send(adapter, request, **kw):
        return _Resp()
    monkeypatch.setattr(base, "_ORIGINAL_SEND", fake_send)
    box = {}

    def tier():
        from concurrent.futures import ThreadPoolExecutor
        requests.adapters.HTTPAdapter().send(requests.Request("POST", "https://rpc.example/x").prepare())
        with ThreadPoolExecutor(max_workers=1, **base.pool_owner()) as ex:      # a worker belongs to its tier
            ex.submit(lambda: requests.adapters.HTTPAdapter().send(
                requests.Request("GET", "https://two.example/y").prepare())).result()
        box["done"] = True
    t = threading.Thread(target=tier, name="explorer@Test")
    t.start()
    t.join()

    class _NoHttp:
        pass
    wc = base.web_calls(t)
    assert box["done"] and wc["calls"] == 2 and wc["hosts"] == ["rpc.example", "two.example"]
    line = fetch._http_summary({"adapter": _NoHttp(), "thread": t})
    assert line.startswith("2 web call(s) outside an Http client") and "rpc.example" in line
    assert "no Http client" not in line


# ============================================================ tidy-up: the longest tiers start first ================
def test_start_order_puts_the_longest_budget_first_and_results_keep_the_given_order(monkeypatch):
    budgets = {"a": 10, "b": 900, "c": 300, "d": 900}
    monkeypatch.setattr(fetch, "_budget", lambda n: budgets[n])
    todo = [(n, 1, None) for n in "abcd"]
    assert [t[0] for t in fetch.start_order(todo)] == ["b", "d", "c", "a"]      # stable for ties
    seen = []

    class _A:
        def __init__(self, n):
            self.n = n

        def run(self, *a):
            seen.append(self.n)
    todo = [(n, 1, (lambda n: lambda ctx: _A(n))(n)) for n in "abcd"]
    res = fetch._dispatch(todo, [], None, {})
    assert [r[0] for r in res] == list("abcd")                                   # merged in the order given
    assert sorted(seen) == list("abcd")


def test_start_order_saving_on_the_real_tier_table():
    """The fetch stage's worst case is max(start offset + budget) at the 0.5 s stagger: 939 s -> 900.5 s."""
    stagger = 0.5
    todo = list(fetch.TIER_ORDER)
    worst = lambda order: max(i * stagger + fetch._budget(n) for i, (n, _t, _f) in enumerate(order))  # noqa: E731
    before, after = worst(todo), worst(fetch.start_order(todo))
    assert after == pytest.approx(max(fetch._budget(n) for n, _t, _f in todo) + stagger, abs=stagger)
    assert before - after >= 30.0                        # 939.0 -> 900.5 on this tier table (38.5 s)
