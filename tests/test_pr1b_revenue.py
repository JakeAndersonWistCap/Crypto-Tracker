"""PROJECT ROUND 1B, group `revenue` (Jake 2026-11-09, item 9): Uniswap's REVENUE from the TokenJars.

The fetcher (fetch/tokenjar.py) on stubbed RPC / explorer / price answers: ERC-20 Transfers INTO each jar, the native coin
from each chain's first v4 fee day, UNI-V2 LP tokens at reserves ÷ supply, unpriced tokens counted and named (never 0),
a chain not read named (PARTIAL), X Layer declared nil, resumable state, the jars' balance at a day's closing block. The
build: revenue_usd hands over from DefiLlama's released value to the jar inflows (a declared, non-overlapping handover;
DefiLlama kept as revenue_usd_defillama), the overlap check as sums, the Q0 note (unpriced share, balance at the window's
start and end), the holder share on accrued revenue, VALUE_TIMING on the released series. A LIVE-SHAPED fixture (as of
2026-11-09, DefiLlama to 11-08, the jar from 10-11) stands in for Jake's store. No test touches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
from fetch import tokenjar as tj  # noqa: E402
from fetch.base import FetchOutput, _measuring_point  # noqa: E402

UNI = config.PROJECT_BY_NAME["Uniswap"]
JAR = "0x" + "aa" * 20
TOK_A = "0x" + "01" * 20          # priced, 18 decimals, $2
TOK_B = "0x" + "02" * 20          # priced, 6 decimals, $1
TOK_X = "0x" + "03" * 20          # unpriced, not an LP
LP = "0x" + "04" * 20             # UNI-V2 of A/B
LP1 = "0x" + "05" * 20            # UNI-V2 of A/X (one side priced)
V2 = "0x" + "0f" * 20
SENDER = "0x" + "99" * 20


def _log(token, raw, frm=SENDER, to=JAR, tx="0x1", idx="0x1", extra_topic=False, block=100):
    t = [tj.TRANSFER, tj.pad(frm), tj.pad(to)] + ([tj.pad(SENDER)] if extra_topic else [None])
    return {"address": token, "topics": t, "data": hex(raw), "transactionHash": tx, "logIndex": idx,
            "blockNumber": hex(block)}


# ============================================================================================ the decoded switch table
def test_chains_decoded_from_94_95_96_with_their_jars_and_dates():
    sw = config.UNISWAP_FEE_SWITCH_CHAINS["proposals"]
    assert sw["#94"]["chains"] == ("optimism", "base", "arbitrum")
    assert sw["#95"]["chains"] == ("soneium", "xlayer", "worldchain", "zora")
    assert sw["#96"]["chains"] == ("bsc", "polygon", "celo")
    ch = config.TOKENJAR_INFLOWS["Uniswap"]["chains"]
    assert ch["ethereum"]["jar"] == "0xf38521f130fcCF29dB1961597bc5d2B60F995f85"
    assert ch["ethereum"]["from_day"] == "2025-12-27" and ch["ethereum"]["from_block"] == 24_106_378
    assert {c for c, s in ch.items() if s["from_day"] == "2026-03-06"} == {"optimism", "base", "arbitrum"}
    assert ch["bsc"]["jar"] == ch["polygon"]["jar"]                     # one deployer address, two chains
    assert ch["celo"]["v2_factory"].lower() == "0x114a43df6c5f54ebb8a9d70cd1951d3dd68004c7"    # #96's fix
    # every jar named in a decoded action is a jar the fetcher reads
    for c, s in ch.items():
        assert s["jar"][2:6].lower() in json.dumps(sw).lower() or c in ("unichain", "arc")
    # X Layer: not read, with its evidence — named, not a silent 0
    assert ch["xlayer"]["not_read"] and "75,450,951" in ch["xlayer"]["evidence_nil"]
    # the native coin is read only from each chain's first v4 fee day (#100)
    assert ch["ethereum"]["native_from"] == "2026-07-27" and ch["celo"]["native_from"] is None


# ============================================================================================ pure helpers
def test_inflows_keep_erc20_transfers_into_the_jar_once():
    logs = [_log(TOK_A, 10), _log(TOK_A, 10),                            # the same log twice: counted once
            _log(TOK_A, 5, tx="0x2"),
            _log(TOK_B, 7, extra_topic=True, tx="0x3"),                 # ERC-721 shape (4 topics): out
            _log(TOK_B, 7, to=SENDER, tx="0x4"),                        # not into the jar: out
            _log(TOK_B, 7, frm=JAR, tx="0x5"),                          # from the jar itself: out
            _log(TOK_B, 0, tx="0x6"),                                   # zero value: out
            _log(TOK_X, 3, tx="0x7")]
    got = tj.inflows_from_logs(logs, JAR)
    assert got == {TOK_A: [15, 2], TOK_X: [3, 1]}
    assert tj.inflows_from_logs(logs, JAR, skip=[TOK_X]) == {TOK_A: [15, 2]}


def test_native_inflows_from_internal_and_normal_transactions():
    rows = [{"to": JAR, "from": SENDER, "value": "100", "isError": "0", "hash": "0xa", "index": "1"},
            {"to": JAR, "from": SENDER, "value": "100", "isError": "0", "hash": "0xa", "index": "1"},   # duplicate
            {"to": JAR, "from": SENDER, "value": "50", "isError": "1", "hash": "0xb", "index": "1"},    # failed
            {"to": SENDER, "from": JAR, "value": "70", "isError": "0", "hash": "0xc", "index": "2"},    # a release out
            {"to": JAR, "from": SENDER, "value": "30", "isError": "0", "hash": "0xd", "txreceipt_status": "1"}]
    assert tj.native_from_txs(rows, JAR) == (130, 2)


def _price(table):
    return lambda a: table.get(a)


def test_lp_is_valued_at_reserves_over_supply_and_one_sided_lp_is_unpriced_never_zero():
    prices = {TOK_A: (2.0, 18), TOK_B: (1.0, 6)}
    lp = {LP: {"t0": TOK_A, "t1": TOK_B, "r0": str(100 * 10 ** 18), "r1": str(200 * 10 ** 6), "supply": str(10 ** 19)},
          LP1: {"t0": TOK_A, "t1": TOK_X, "r0": str(50 * 10 ** 18), "r1": str(10 ** 30), "supply": str(10 ** 20)}}
    # pool A/B = $200 + $200 = $400 over 10 LP tokens -> $40 each
    assert tj.lp_unit_value(lp[LP], _price(prices)) == pytest.approx(40.0)
    assert tj.lp_unit_value(lp[LP1], _price(prices)) is None
    v = tj.value_tokens({TOK_A: (3 * 10 ** 18, 2), LP: (10 ** 18 // 2, 1), LP1: (10 ** 19, 4), TOK_X: (5, 1)},
                        _price(prices), lp, {TOK_X: "XXX"})
    assert v["usd"] == pytest.approx(6.0 + 20.0) and v["lp_usd"] == pytest.approx(20.0)
    assert v["transfers"] == 8 and v["unpriced_transfers"] == 5
    assert v["unpriced"][0] == [LP1, "UNI-V2", 4] and [TOK_X, "XXX", 1] in v["unpriced"]
    # the one-sided LP's priced reserve share (10% of $100) is a SIZE, never added
    assert v["lp_one_side_usd"] == pytest.approx(10.0)


# ============================================================================================ the fetcher, stubbed
class Chain:
    """A fake chain: 1,000 blocks a day from 2026-11-01 00:00 UTC (block 1,000 at that instant), with logs by block."""

    def __init__(self, logs=None, head_day="2026-11-09", fail_logs=False, cap=None):
        self.t0 = tj.day_ts("2026-11-01")
        self.logs = logs or {}
        self.head = 1000 + 1000 * (pd.Timestamp(head_day) - pd.Timestamp("2026-11-01")).days + 500
        self.fail_logs, self.cap, self.calls = fail_logs, cap, []

    def ts(self, n):
        return self.t0 + (n - 1000) * 86.4

    def __call__(self, url, payload):
        m, p = payload["method"], payload["params"]
        self.calls.append(m)
        if m == "eth_blockNumber":
            return {"result": hex(self.head)}
        if m == "eth_getBlockByNumber":
            n = int(p[0], 16)
            return {"result": {"timestamp": hex(int(self.ts(n)))}} if 0 < n <= self.head else {"result": None}
        if m == "eth_getLogs":
            if self.fail_logs:
                return {"error": {"message": "boom"}}
            lo, hi = int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16)
            if self.cap and hi - lo + 1 > self.cap:
                return {"error": {"message": f"block range too large, max {self.cap}"}}
            return {"result": [x for b, xs in self.logs.items() if lo <= b <= hi for x in xs]}
        if m == "eth_call":
            return {"result": self.multicall(p[0]["data"])}
        if m == "eth_getBalance":
            return {"result": hex(0)}
        raise AssertionError(m)

    def multicall(self, data):
        from eth_abi import decode, encode
        calls = decode(["(address,bool,bytes)[]"], bytes.fromhex(data[10:]))[0]
        out = []
        for target, _ok, cd in calls:
            sel, t = cd[:4].hex(), target.lower()
            if sel == tj.SEL["factory"]:
                out.append((True, bytes.fromhex(tj.pad(V2)[2:])) if t in (LP, LP1) else (False, b""))
            elif sel == tj.SEL["symbol"]:
                out.append((True, encode(["string"], ["UNI-V2" if t in (LP, LP1) else "XXX"])))
            elif sel == tj.SEL["token0"]:
                out.append((True, bytes.fromhex(tj.pad(TOK_A)[2:])))
            elif sel == tj.SEL["token1"]:
                out.append((True, bytes.fromhex(tj.pad(TOK_B if t == LP else TOK_X)[2:])))
            elif sel == tj.SEL["getReserves"]:
                r0, r1 = (100 * 10 ** 18, 200 * 10 ** 6) if t == LP else (50 * 10 ** 18, 10 ** 30)
                out.append((True, encode(["uint112", "uint112", "uint32"], [r0, r1, 0])))
            elif sel == tj.SEL["totalSupply"]:
                out.append((True, encode(["uint256"], [10 ** 19 if t == LP else 10 ** 20])))
            elif sel == tj.SEL["balanceOf"]:
                out.append((True, encode(["uint256"], [4 * 10 ** 18 if t == TOK_A else 0])))
            else:
                out.append((False, b""))
        return "0x" + encode(["(bool,bytes)[]"], [out]).hex()


class Router:
    """rpc_post for every chain URL: rpc://<chain>."""

    def __init__(self, chains):
        self.chains = chains

    def __call__(self, url, payload):
        return self.chains[url.split("//")[1]](url, payload)


class FakeHttp:
    """Blockscout (native) and DefiLlama (prices) answers; records what was asked."""

    def __init__(self, prices=None, native=None, native_fails=False):
        self.prices = prices if prices is not None else {"ethx:" + TOK_A: 2.0, "ethx:" + TOK_B: 1.0,
                                                         "coingecko:ethereum": 3000.0}
        self.native, self.native_fails, self.asked = native or [], native_fails, []
        self.stats = {"calls": 0}

    def get(self, url, params=None):
        self.asked.append(url)
        if "/prices/historical/" in url:
            coins = url.rsplit("/", 1)[1].split(",")
            return {"coins": {c: {"price": self.prices[c], "symbol": "S", "confidence": 0.99,
                                  **({} if c.startswith("coingecko:") else {"decimals": 6 if c.endswith(TOK_B) else 18})}
                              for c in coins if c in self.prices}}
        if params and params.get("module") == "account":
            if self.native_fails:
                raise RuntimeError("HTTP 403 from host")
            lo, hi = int(params["startblock"]), int(params["endblock"])
            rows = [r for r in self.native if lo <= int(r["blockNumber"]) <= hi] if params["action"] == "txlistinternal" else []
            return {"status": "1" if rows else "0", "message": "OK" if rows else "No transactions found", "result": rows}
        raise AssertionError(url)


def _spec(tmp_path, chains):
    return {"metric": "tokenjar_inflow_usd", "count_metric": "tokenjar_inflow_transfers",
            "unpriced_metric": "tokenjar_unpriced_transfers", "balance_metric": "tokenjar_balance_usd",
            "one_side_metric": "tokenjar_lp_one_side_usd", "budget_s": 10 ** 6, "balance_window_days": 3,
            "state_path": str(tmp_path / "state.json"), "prices_path": str(tmp_path / "prices.json"), "chains": chains}


def _cs(chain, from_day, **kw):
    base = {"jar": JAR, "v2_factory": V2, "from_day": from_day, "llama": "ethx", "native_coin": "coingecko:ethereum",
            "logs": [{"kind": "rpc", "url": f"rpc://{chain}", "span": 100_000}], "state_rpc": [f"rpc://{chain}"],
            "native": [{"kind": "blockscout", "url": "https://bs.example/api"}], "native_from": None}
    return {**base, **kw}


def _run(monkeypatch, tmp_path, chains, fakes, http, today="2026-11-09"):
    monkeypatch.setattr(tj, "today", lambda: pd.Timestamp(today))
    monkeypatch.setitem(config.TOKENJAR_INFLOWS, "Uniswap", _spec(tmp_path, chains))
    f = tj.TokenJarInflows(http=http, rpc_post=Router(fakes), root=tmp_path, env_rpcs={})
    out = FetchOutput()
    f.run([UNI], None, out, unbounded=True)
    frames = pd.concat(out.frames, ignore_index=True) if out.frames else pd.DataFrame()
    return f, out, frames


def _day_block(day):
    return 1000 + 1000 * (pd.Timestamp(day) - pd.Timestamp("2026-11-01")).days


def test_fetcher_values_each_day_names_the_unpriced_and_stores_counts(monkeypatch, tmp_path):
    b = _day_block("2026-11-08") + 10
    eth = Chain({b: [_log(TOK_A, 3 * 10 ** 18, tx="0xa"), _log(TOK_B, 5 * 10 ** 6, tx="0xb"),
                     _log(LP, 10 ** 18 // 2, tx="0xc"), _log(LP1, 10 ** 19, tx="0xd"), _log(TOK_X, 9, tx="0xe")]})
    chains = {"eth": _cs("eth", "2026-11-06")}
    f, out, fr = _run(monkeypatch, tmp_path, chains, {"eth": eth}, FakeHttp())
    v = fr[(fr.metric == "tokenjar_inflow_usd")].set_index("date")
    # 3 A x $2 + 5 B x $1 + LP 0.5 x $40 = $31; LP1 (one side unpriced) and X are named, not valued
    assert v.loc[pd.Timestamp("2026-11-08"), "value"] == pytest.approx(31.0)
    src = v.loc[pd.Timestamp("2026-11-08"), "source"]
    assert _measuring_point(src) == "chain:tokenjars:tokenjar_inflow"
    assert "UNPRICED 2 of 5 transfers (40.0%)" in src and "eth:UNI-V2×1" in src and "eth:XXX×1" in src
    assert "PARTIAL" not in src                       # no leg missing; the unpriced share is said, not a hole
    # the days before 11-08 back to the chain's switch day: read, nothing arrived — a measured 0, said
    assert v.loc[pd.Timestamp("2026-11-06"), "value"] == 0.0 and "read, nothing arrived: eth" in \
        v.loc[pd.Timestamp("2026-11-06"), "source"]
    assert pd.Timestamp("2026-11-09") not in v.index and pd.Timestamp("2026-11-05") not in v.index
    cnt = fr[fr.metric == "tokenjar_inflow_transfers"].set_index("date")["value"]
    unp = fr[fr.metric == "tokenjar_unpriced_transfers"].set_index("date")["value"]
    one = fr[fr.metric == "tokenjar_lp_one_side_usd"].set_index("date")["value"]
    assert cnt[pd.Timestamp("2026-11-08")] == 5 and unp[pd.Timestamp("2026-11-08")] == 2
    assert one[pd.Timestamp("2026-11-08")] == pytest.approx(10.0)
    # the stored source is never an orphan (the jar table is configured, not one contract key)
    assert config.orphaned_contract_keys("Uniswap", src) == []


def test_native_leg_from_its_first_v4_day_and_a_chain_not_read_is_named(monkeypatch, tmp_path):
    b = _day_block("2026-11-08") + 5
    native = [{"blockNumber": str(b), "to": JAR, "from": SENDER, "value": str(10 ** 18), "isError": "0",
               "hash": "0xn", "index": "1"}]
    eth = Chain({b: [_log(TOK_A, 10 ** 18)]})
    chains = {"eth": _cs("eth", "2026-11-07", native_from="2026-11-08"),
              "dark": _cs("dark", "2026-11-08", logs=[], not_read="no route", evidence_nil="holds 0"),
              "gone": _cs("gone", "2026-11-08", logs=[], not_read="no route")}
    f, out, fr = _run(monkeypatch, tmp_path, chains, {"eth": eth}, FakeHttp(native=native))
    v = fr[fr.metric == "tokenjar_inflow_usd"].set_index("date")
    s8 = v.loc[pd.Timestamp("2026-11-08"), "source"]
    # 1 A x $2 + 1 ETH (native, v4) x $3,000
    assert v.loc[pd.Timestamp("2026-11-08"), "value"] == pytest.approx(2.0 + 3000.0)
    # a chain with no route is named and the day is PARTIAL; one declared nil with its evidence is named, not PARTIAL
    assert "NOT READ (no route): gone" in s8 and "not read, declared nil: dark" in s8 and s8.endswith(":PARTIAL")
    # 11-07: only eth is live, before its native day — the native leg is not needed, so not PARTIAL
    assert not v.loc[pd.Timestamp("2026-11-07"), "source"].endswith(":PARTIAL")
    assert "not needed" in f.cst("eth")["days"]["2026-11-07"]["native_route"]
    chains.pop("gone")
    f, out, fr = _run(monkeypatch, tmp_path, chains, {"eth": eth}, FakeHttp(native=native))
    assert not fr[fr.metric == "tokenjar_inflow_usd"].set_index("date").loc[pd.Timestamp("2026-11-08"),
                                                                             "source"].endswith(":PARTIAL")


def test_a_native_route_that_fails_marks_the_day_partial_and_names_the_chain(monkeypatch, tmp_path):
    chains = {"eth": _cs("eth", "2026-11-08", native_from="2026-11-08")}
    f, out, fr = _run(monkeypatch, tmp_path, chains, {"eth": Chain({})}, FakeHttp(native_fails=True))
    s8 = fr[fr.metric == "tokenjar_inflow_usd"].iloc[0]["source"]
    assert "native coin NOT READ on eth" in s8 and s8.endswith(":PARTIAL")
    assert f.cst("eth")["days"]["2026-11-08"]["native"] is None


def test_a_native_leg_that_did_not_answer_is_asked_again_and_lifts_the_partial(monkeypatch, tmp_path):
    b = _day_block("2026-11-08") + 5
    native = [{"blockNumber": str(b), "to": JAR, "from": SENDER, "value": str(2 * 10 ** 18), "isError": "0",
               "hash": "0xn", "index": "1"}]
    chains = {"eth": _cs("eth", "2026-11-08", native_from="2026-11-08")}
    eth = Chain({b: [_log(TOK_A, 10 ** 18)]})
    f, out, fr = _run(monkeypatch, tmp_path, chains, {"eth": eth}, FakeHttp(native=native, native_fails=True))
    assert fr[fr.metric == "tokenjar_inflow_usd"].iloc[0]["source"].endswith(":PARTIAL")
    n_logs = eth.calls.count("eth_getLogs")
    f, out, fr = _run(monkeypatch, tmp_path, chains, {"eth": eth}, FakeHttp(native=native))
    row = fr[fr.metric == "tokenjar_inflow_usd"].iloc[0]
    assert row["value"] == pytest.approx(2.0 + 6000.0) and not row["source"].endswith(":PARTIAL")
    assert eth.calls.count("eth_getLogs") == n_logs              # only the native leg was asked again
    assert fr[fr.metric == "tokenjar_inflow_transfers"].iloc[0]["value"] == 2


def test_blockscout_key_goes_to_the_pro_api_first_and_never_into_a_message(monkeypatch, tmp_path):
    monkeypatch.setenv(config.EXPLORERS["blockscout"]["key_env"], "SECRETKEY123")
    cs = _cs("eth", "2026-11-08", chain_id=1)
    routes = tj.TokenJarInflows._bs_routes(cs, cs["native"])
    assert routes[0]["url"] == "https://api.blockscout.com/1/api" and routes[0]["key"] and not routes[1].get("key")
    assert len(tj.TokenJarInflows._bs_routes({**cs, "pro_paid_only": True}, cs["native"])) == 1
    f = tj.TokenJarInflows(http=None, rpc_post=lambda u, p: None, root=tmp_path, env_rpcs={})
    f._deadline = f.clock() + 100
    asked = []

    def get(url, params):
        asked.append((url, dict(params)))
        raise RuntimeError(f"HTTP 500 from {url}?apikey={params.get('apikey')}")
    f._get = get
    nat, why = f.native("eth", cs, 1, 2)
    assert nat is None and asked[0][1]["apikey"] == "SECRETKEY123" and "apikey" not in asked[1][1]
    assert "SECRETKEY123" not in why and "***" in why


def test_a_chain_whose_logs_fail_holds_back_the_total_and_resumes(monkeypatch, tmp_path):
    good, bad = Chain({_day_block("2026-11-08"): [_log(TOK_A, 10 ** 18)]}), Chain({}, fail_logs=True)
    chains = {"eth": _cs("eth", "2026-11-08"), "l2": _cs("l2", "2026-11-08")}
    f, out, fr = _run(monkeypatch, tmp_path, chains, {"eth": good, "l2": bad}, FakeHttp())
    assert fr.empty or fr[fr.metric == "tokenjar_inflow_usd"].empty     # never a total without l2's day
    assert any(e.status == "warn" and "l2" in e.message for e in out.log)
    # the next run: l2 answers; eth's day is NOT read again (resumable state)
    bad.fail_logs = False
    n_eth = len(good.calls)
    f2, out2, fr2 = _run(monkeypatch, tmp_path, chains, {"eth": good, "l2": bad}, FakeHttp())
    assert "eth_getLogs" not in good.calls[n_eth:]
    assert fr2[fr2.metric == "tokenjar_inflow_usd"].iloc[0]["value"] == pytest.approx(2.0)


def test_rpc_log_route_halves_its_span_on_a_range_refusal(monkeypatch, tmp_path):
    eth = Chain({_day_block("2026-11-08") + 900: [_log(TOK_A, 10 ** 18)]}, cap=300)
    chains = {"eth": _cs("eth", "2026-11-08")}
    f, out, fr = _run(monkeypatch, tmp_path, chains, {"eth": eth}, FakeHttp())
    assert fr[fr.metric == "tokenjar_inflow_usd"].iloc[0]["value"] == pytest.approx(2.0)


def test_day_range_is_the_first_block_of_the_day_to_the_block_before_the_next(monkeypatch, tmp_path):
    chains = {"eth": _cs("eth", "2026-11-08")}
    f, _out, _fr = _run(monkeypatch, tmp_path, chains, {"eth": Chain({})}, FakeHttp())
    rec = f.cst("eth")["days"]["2026-11-08"]
    assert (rec["b0"], rec["b1"]) == (_day_block("2026-11-08"), _day_block("2026-11-09") - 1)


def test_blockscout_logs_page_from_the_last_block_without_double_counting(tmp_path):
    f = tj.TokenJarInflows(http=None, rpc_post=lambda u, p: None, root=tmp_path, env_rpcs={})
    f._deadline = f.clock() + 100
    pages = [[{"transactionHash": f"0x{i}", "logIndex": "0x0", "blockNumber": hex(10 + i // 2)} for i in range(4)],
             [{"transactionHash": "0x3", "logIndex": "0x0", "blockNumber": hex(11)},
              {"transactionHash": "0x9", "logIndex": "0x0", "blockNumber": hex(12)}]]
    asked = []

    def get(url, params):
        asked.append(params["fromBlock"])
        res = pages[len(asked) - 1]
        return {"status": "1", "message": "OK", "result": [dict(x) for x in res]}
    f._get = get
    got = f._bs_logs({"url": "https://bs/api", "page": 4}, 10, 20, None, [tj.TRANSFER, None, tj.pad(JAR)])
    assert asked == [10, 11] and sorted(x["transactionHash"] for x in got) == ["0x0", "0x1", "0x2", "0x3", "0x9"]


def test_balance_at_the_closing_block_and_its_stored_stock(monkeypatch, tmp_path):
    b = _day_block("2026-11-08") + 1
    eth = Chain({b: [_log(TOK_A, 10 ** 18)]})
    chains = {"eth": _cs("eth", "2026-11-05")}
    f, out, fr = _run(monkeypatch, tmp_path, chains, {"eth": eth}, FakeHttp())
    bal = fr[fr.metric == "tokenjar_balance_usd"].set_index("date")
    # balanceOf(jar) = 4 A at $2 at 11-08's close (yesterday) and at 11-05's close (3 days before; the walk reached the
    # switch day so the token set is complete)
    assert bal.loc[pd.Timestamp("2026-11-08"), "value"] == pytest.approx(8.0)
    assert set(bal.index) == {pd.Timestamp("2026-11-08"), pd.Timestamp("2026-11-05")}
    assert _measuring_point(bal.iloc[0]["source"]) == "chain:tokenjars:tokenjar_balance"
    assert not bal.loc[pd.Timestamp("2026-11-08"), "source"].endswith(":PARTIAL")


def test_seed_and_tier_are_registered():
    import fetch
    assert any(t[0] == "tokenjar" for t in fetch.TIER_ORDER) and fetch._budget("tokenjar") > 240
    assert fetch.adapter_files()["tokenjar"][0].endswith("tokenjar.py")
    assert '"tokenjar": seed_tokenjar' in (Path(__file__).resolve().parents[1] / "token_metrics.py").read_text()


# ============================================================================================ the build: the handover
ASOF = pd.Timestamp("2026-11-09")


def _rows(metric, days, vals, source, tier=1):
    days = pd.to_datetime(list(days))
    vals = vals if isinstance(vals, (list, tuple)) else [vals] * len(days)
    return pd.DataFrame({"date": days, "project": "Uniswap", "metric": metric, "value": [float(x) for x in vals],
                         "source": source, "tier": tier})


def _live_groups():
    """LIVE-SHAPED (Jake's store as of 2026-11-09): DefiLlama's released value 2026-07-01..11-08 ($300k a day), the jar
    inflows from 2026-10-11 ($310k a day), the jars' balance read at 08-10 and 11-08."""
    dl_days = pd.date_range("2026-07-01", "2026-11-08")
    jar_days = pd.date_range("2026-10-11", "2026-11-08")
    jsrc = "chain:tokenjars:tokenjar_inflow[ethereum $1 | UNPRICED 2 of 100 transfers (2.0%)]"
    return {("Uniswap", "revenue_usd"): _rows("revenue_usd", dl_days, 300_000.0, "defillama"),
            ("Uniswap", "tokenjar_inflow_usd"): _rows("tokenjar_inflow_usd", jar_days, 310_000.0, jsrc, 2),
            ("Uniswap", "tokenjar_inflow_transfers"): _rows("tokenjar_inflow_transfers", jar_days, 100.0, jsrc, 2),
            ("Uniswap", "tokenjar_unpriced_transfers"): _rows("tokenjar_unpriced_transfers", jar_days, 2.0, jsrc, 2),
            ("Uniswap", "tokenjar_lp_one_side_usd"): _rows("tokenjar_lp_one_side_usd", jar_days, 5.0, jsrc, 2),
            ("Uniswap", "tokenjar_balance_usd"): _rows("tokenjar_balance_usd", ["2026-08-10", "2026-11-08"],
                                                       [100_000.0, 390_000.0],
                                                       "chain:tokenjars:tokenjar_balance[x]", 2)}


@pytest.fixture
def uni_only(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    monkeypatch.setattr(config, "TOKENJAR_NOTE", {})
    yield


def test_revenue_hands_over_to_the_jar_inflows_with_no_overlap_and_defillama_kept_beside(uni_only):
    groups = _live_groups()
    bw._tokenjar_revenue_views(groups)
    rev = groups[("Uniswap", "revenue_usd")].assign(date=lambda d: pd.to_datetime(d["date"]))
    pts = rev["source"].map(_measuring_point)
    dl = rev[pts == "defillama"]
    jar = rev[pts == "chain:tokenjars:tokenjar_inflow"]
    assert dl["date"].max() == pd.Timestamp("2026-10-10") and jar["date"].min() == pd.Timestamp("2026-10-11")
    assert rev["date"].is_unique and len(rev) == len(pd.date_range("2026-07-01", "2026-11-08"))
    kept = groups[("Uniswap", "revenue_usd_defillama")]
    assert len(kept) == len(pd.date_range("2026-07-01", "2026-11-08")) and set(kept["metric"]) == {"revenue_usd_defillama"}
    # the declared handover accepts exactly these two legs in this order (Rule 10), and refuses an overlap
    spans = {p: (g["date"].min(), g["date"].max()) for p, g in rev.groupby(pts)}
    assert bw.handover_refusal("Uniswap", "revenue_usd", set(spans), spans) is None
    bad = dict(spans)
    bad["defillama"] = (spans["defillama"][0], pd.Timestamp("2026-10-20"))
    assert "OVERLAP" in bw.handover_refusal("Uniswap", "revenue_usd", set(bad), bad)
    st = config.TOKENJAR_NOTE["Uniswap"]
    assert st["handed_over"] and st["first"] == "2026-10-11" and st["days"] == 29


def test_without_jar_rows_revenue_stays_defillama_and_the_words_do_not_change(uni_only):
    groups = {k: v for k, v in _live_groups().items() if k[1] == "revenue_usd"}
    before = groups[("Uniswap", "revenue_usd")].copy()
    bw._tokenjar_revenue_views(groups)
    pd.testing.assert_frame_equal(groups[("Uniswap", "revenue_usd")], before)
    assert "Uniswap" not in config.TOKENJAR_NOTE
    assert config.revenue_split("Uniswap")["revenue"]["primary"] is False
    assert config.revenue_tier("Uniswap")[1].startswith("REVENUE BY A DECLARED PROXY")


def test_the_q0_note_says_unpriced_share_balance_start_end_and_the_overlap_as_sums(uni_only, monkeypatch):
    groups = _live_groups()
    monkeypatch.setitem(bw._WINDOWS, "q0", ("2026-08-11", "2026-11-09"))
    bw._tokenjar_revenue_views(groups)
    bw._tokenjar_window_notes(groups)
    t = config.TOKENJAR_NOTE["Uniswap"]["text"]
    assert "stored 2026-10-11..2026-11-08 (29 day(s))" in t
    assert "58 of 2,900 transfers in (2.0%) had no price" in t and "$145 of that reserve" in t
    assert "$100,000 at 2026-08-10's close (the window's start) -> $390,000 at 2026-11-08's close (its end)" in t
    # overlap as SUMS over the 29 common days: accrued 29 x 310k vs released 29 x 300k
    assert "accrued (the jars) $8,990,000 vs released (DefiLlama) $8,700,000 (+3.3%)" in t
    ov = bw.tokenjar_overlap(groups, "Uniswap")
    assert ov["common_days"] == 29 and ov["passed"]
    # the words follow: revenue PRIMARY on chain, the build's figures appended; the holder share on accrued revenue
    split = config.revenue_split("Uniswap")
    assert split["revenue"]["primary"] is True and "TokenJar INFLOWS" in split["revenue"]["route"]
    assert "THIS BUILD: the jar inflows are stored" in split["revenue"]["route"]
    assert "ACCRUED revenue" in split["holder_share"]["route"]
    label = config.revenue_tier_label("Uniswap", "Revenue Q0 ($)")
    assert label[0] == " · revenue" and "TokenJar INFLOWS" in label[1] and "(2.0%) had no price" in label[1]
    assert config.revenue_tier_label("Uniswap", "REVENUE Q0 ($) — S13")[1].count("PRIMARY") == 1


def test_aggregate_on_the_live_shaped_fixture_reads_the_jar_leg_in_q0(uni_only):
    bw._SCOPE = [UNI]
    long = pd.concat(_live_groups().values(), ignore_index=True).assign(
        fetched_at="2026-11-09T00:00:00", is_manual=0, entered_on=None, source_note=None)
    data = bw.aggregate(long, pd.DataFrame(), ASOF)
    row = data[(data.project == "Uniswap") & (data.metric == "revenue_usd")].iloc[0]
    # Q0 = (08-11, 11-09]: DefiLlama 08-12..10-10 (60 days x 300k) + the jar 10-11..11-08 (29 days x 310k)
    assert row["q0"] == pytest.approx(60 * 300_000 + 29 * 310_000)
    assert row["status"] not in ("measuring_point_changed",)
    kept = data[(data.project == "Uniswap") & (data.metric == "revenue_usd_defillama")].iloc[0]
    assert kept["q0"] == pytest.approx(89 * 300_000)


def test_value_timing_reads_the_released_series_after_the_handover(uni_only, monkeypatch):
    days = pd.date_range("2026-08-01", "2026-11-08")
    groups = _live_groups()
    groups[("Uniswap", "price_usd")] = _rows("price_usd", days, 10.0, "coingecko")
    # burned 30,000 UNI a day = $300k = what DefiLlama books released (the same tokens)
    groups[("Uniswap", "gross_burn_tokens")] = _rows("gross_burn_tokens", days, 30_000.0, "chain:ethereum:burn_dead:delta")
    bw._tokenjar_revenue_views(groups)
    start, end = pd.Timestamp("2026-08-10"), pd.Timestamp("2026-11-08")
    # accrued − released = 29 x 10k = 290k = the balance change (100k -> 390k): explained within the declared margin
    tex = bw._value_timing(groups, "Uniswap", start, end, 0.05)
    assert tex and tex.startswith("EXPLAINED — timing") and "ACCRUED vs RELEASED" in tex and "+0.0% of the accrual" in tex
    # a balance that FELL 290k leaves 580k unexplained (2.1% of the 27.29M accrual, over the 2% margin): the CHECK stands
    groups[("Uniswap", "tokenjar_balance_usd")] = _rows("tokenjar_balance_usd", ["2026-08-10", "2026-11-08"],
                                                        [390_000.0, 100_000.0], "chain:tokenjars:tokenjar_balance[x]", 2)
    assert bw._value_timing(groups, "Uniswap", start, end, 0.05) is None


def test_flows_carries_the_jar_balance_at_each_quarter_end_and_the_accrued_words(uni_only, monkeypatch):
    groups = _live_groups()
    bw._tokenjar_revenue_views(groups)
    monkeypatch.setitem(bw.GLOBALS, "period_days", 90)
    rows = bw.flow_statements(groups, pd.Timestamp("2026-11-08"))
    memo = [r for r in rows if r["Project"] == "Uniswap" and r["Line"].startswith("memo: TokenJars' undistributed")]
    assert len(memo) == 1 and memo[0]["Series"] == "tokenjar_balance_usd"
    vals = [memo[0][lab] for lab in bw.trajectory.LABELS]
    assert vals[0] == 390_000.0 and vals[1] == 100_000.0 and vals[2] is None     # Q0's end, Q-1's end (= Q0's start)
    rev = next(r for r in rows if r["Project"] == "Uniswap" and r["Line"] == "Revenue (every stream)")
    assert "ACCRUED: the TokenJar inflows from 2026-10-11" in rev["Note"]
    # the dumps' shape (no balance stored): no memo line, no words
    config.TOKENJAR_NOTE.clear()
    g2 = {k: v for k, v in _live_groups().items() if k[1] == "revenue_usd"}
    rows2 = bw.flow_statements(g2, pd.Timestamp("2026-11-08"))
    assert not [r for r in rows2 if r["Line"].startswith("memo: TokenJars'")]


def test_credibility_sets_accrued_beside_released_once_the_jar_is_in_q0():
    import credibility
    spec = config.CREDIBILITY["Uniswap"]["in_revenue"]
    assert spec["ours"] == {"metric": "revenue_usd", "window": "q0"}
    rows = {"Uniswap|revenue_usd_defillama": {"q0": 27_000_000.0, "latest_date": "2026-11-08"}}
    jar = _rows("tokenjar_inflow_transfers", pd.date_range("2026-10-11", "2026-11-08"), 100.0, "chain:tokenjars:x")
    r = credibility.reference("Uniswap", spec["ref"], rows, jar, ASOF)
    assert r["value"] == 27_000_000.0 and r["tol"] == 5.0 and r["mode"] == "independent" and r["verdict"] is None
    # the dumps (no jar row): DefiLlama is the only aggregate, as before
    r0 = credibility.reference("Uniswap", spec["ref"], rows, jar.iloc[0:0], ASOF)
    assert r0["verdict"] == "UNVERIFIABLE" and "--seed tokenjar" in r0["note"]
