"""
tests/test_g8_ccip_value.py — G8, CCIP token value (fetch/ccip_value.py), 2026-10-20. No network: a fake Http serves
api.ccip.chain.link (messages list by cursor, message detail, token directory) and coins.llama.fi from dicts, and
advances a fake clock per call so the time budget is exercised, not waited on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fetch import ccip_value as cv  # noqa: E402
from fetch.base import FetchOutput, HttpError  # noqa: E402

ETH, ARB, BASE, POLY, SOL = ("5009297550715157269", "4949039107694359620", "15971525489660198786",
                             "4051577828743386545", "124615329519749607")
BITLAYER, HYPER, UNMAPPED = "7937294810946806131", "2442541497099098535", "2135107236357186872"   # bittensor

# A RECORDED PAGE (api.ccip.chain.link/v2/messages?limit=1000&environment=mainnet, read 2026-10-20 09:18 UTC), trimmed
# to the fields read: the first two records are the samples in g8/routes/q3_ccip_samples.json, the rest the last
# seven messages of 2026-10-20 and the first two of 2026-10-19 on the same page — newest first, as served.
RECORDED = [
    {"messageId": "0x657359fc0d61567ca10f1b63883650e801c30c248fde537628ddefb6b3c85d67", "sendTimestamp": "2026-10-20T09:01:59.000Z", "sourceNetworkInfo": {"name": "solana-mainnet", "chainSelector": "124615329519749607", "chainFamily": "SVM"}, "destNetworkInfo": {"name": "binance_smart_chain-mainnet", "chainSelector": "11344663589394136015"}, "sourceTokenAmount": {"tokenAddress": "Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk", "tokenAmount": "70466445"}},  # noqa: E501
    {"messageId": "0x99451d8ce08e7556f57be3f10ff26ba81d3af56925350238776df822ba8cc9e6", "sendTimestamp": "2026-10-20T09:01:07.000Z", "sourceNetworkInfo": {"name": "polygon-mainnet", "chainSelector": "4051577828743386545", "chainFamily": "EVM"}, "destNetworkInfo": {"name": "ethereum-mainnet-base-1", "chainSelector": "15971525489660198786"}, "sourceTokenAmount": {"tokenAddress": "0xB5C064F955D8e7F38fE0460C556a72987494eE17", "tokenAmount": "10822429188040000000000"}},  # noqa: E501
    {"messageId": "0xf19b6c4e19656d04c9f7632f804f8c295619b2a8b9d5068c9579ec31b683bbe5", "sendTimestamp": "2026-10-20T00:07:43.000Z", "sourceNetworkInfo": {"name": "solana-mainnet", "chainSelector": "124615329519749607", "chainFamily": "SVM"}, "destNetworkInfo": {"name": "ethereum-mainnet-ink-1", "chainSelector": "3461204551265785888"}, "sourceTokenAmount": None},  # noqa: E501
    {"messageId": "0xa27fd40f7586782f51b5827124f71e5a8142cda9da1acc84a6be63f58a60ff61", "sendTimestamp": "2026-10-20T00:07:08.000Z", "sourceNetworkInfo": {"name": "bitcoin-mainnet-bitlayer-1", "chainSelector": "7937294810946806131", "chainFamily": "EVM"}, "destNetworkInfo": {"name": "binance_smart_chain-mainnet", "chainSelector": "11344663589394136015"}, "sourceTokenAmount": {"tokenAddress": "0x0E4cF4Affdb72b39Ea91fA726D291781cBd020bF", "tokenAmount": "279306000000000000000"}},  # noqa: E501
    {"messageId": "0x527fe3892ffd089afeab9d3215ee33c5296c92912ec0adb40add34ac66878c6c", "sendTimestamp": "2026-10-20T00:05:55.000Z", "sourceNetworkInfo": {"name": "solana-mainnet", "chainSelector": "124615329519749607", "chainFamily": "SVM"}, "destNetworkInfo": {"name": "ethereum-mainnet-ink-1", "chainSelector": "3461204551265785888"}, "sourceTokenAmount": None},  # noqa: E501
    {"messageId": "0xbcc0bb65c0e958d481f7b8bf6c983b2656eacc98457f13d9fb9b7a1e9c8be1d7", "sendTimestamp": "2026-10-20T00:04:42.000Z", "sourceNetworkInfo": {"name": "hyperliquid-mainnet", "chainSelector": "2442541497099098535", "chainFamily": "EVM"}, "destNetworkInfo": {"name": "ethereum-mainnet", "chainSelector": "5009297550715157269"}, "sourceTokenAmount": {"tokenAddress": "0x77146784315Ba81904d654466968e3a7c196d1f3", "tokenAmount": "11733604528818958658376"}},  # noqa: E501
    {"messageId": "0xe6658ca7bb883f50e20a7d1b1e2d5a23dd99c654515a5a566b7a995ad50bdd66", "sendTimestamp": "2026-10-20T00:03:25.000Z", "sourceNetworkInfo": {"name": "ethereum-mainnet-base-1", "chainSelector": "15971525489660198786", "chainFamily": "EVM"}, "destNetworkInfo": {"name": "solana-mainnet", "chainSelector": "124615329519749607"}, "sourceTokenAmount": {"tokenAddress": "0xE2f9db0186b13668AeC9fe0e15dbD13004ed8d6f", "tokenAmount": "8009259208"}},  # noqa: E501
    {"messageId": "0x9052e8dca486b122965eeb689a936b8fc5b43c7420c7565315fa7fc29c6dc19b", "sendTimestamp": "2026-10-20T00:01:23.000Z", "sourceNetworkInfo": {"name": "ethereum-mainnet", "chainSelector": "5009297550715157269", "chainFamily": "EVM"}, "destNetworkInfo": {"name": "solana-mainnet", "chainSelector": "124615329519749607"}, "sourceTokenAmount": None},  # noqa: E501
    {"messageId": "0x0cb22d5b25f3918c354af6d9e7b22a02692276887849bb9dc257752466468606", "sendTimestamp": "2026-10-20T00:01:23.000Z", "sourceNetworkInfo": {"name": "ethereum-mainnet", "chainSelector": "5009297550715157269", "chainFamily": "EVM"}, "destNetworkInfo": {"name": "solana-mainnet", "chainSelector": "124615329519749607"}, "sourceTokenAmount": None},  # noqa: E501
    {"messageId": "0xe41138211c3a36e59082f37398273db08471439123bf080b3f59421fdb2c078d", "sendTimestamp": "2026-10-19T23:59:59.000Z", "sourceNetworkInfo": {"name": "ethereum-mainnet", "chainSelector": "5009297550715157269", "chainFamily": "EVM"}, "destNetworkInfo": {"name": "solana-mainnet", "chainSelector": "124615329519749607"}, "sourceTokenAmount": None},  # noqa: E501
    {"messageId": "0x98b53854eb6896f62b9f9d918cccb857f056bdeda04fbf164170f9bd3f3d2368", "sendTimestamp": "2026-10-19T23:59:59.000Z", "sourceNetworkInfo": {"name": "ethereum-mainnet", "chainSelector": "5009297550715157269", "chainFamily": "EVM"}, "destNetworkInfo": {"name": "solana-mainnet", "chainSelector": "124615329519749607"}, "sourceTokenAmount": None},  # noqa: E501
]

# (selector, address, decimals, symbol, groupId) — the directory's answer for the recorded tokens and the synthetic ones
DIRECTORY = [
    (SOL, "Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk", 6, "SOLTOK", None),
    (POLY, "0xB5C064F955D8e7F38fE0460C556a72987494eE17", 18, "QUICK", "g-quick"),
    (BITLAYER, "0x0E4cF4Affdb72b39Ea91fA726D291781cBd020bF", 18, "BLT", None),
    (HYPER, "0x77146784315Ba81904d654466968e3a7c196d1f3", 18, "HYP", None),
    (BASE, "0xE2f9db0186b13668AeC9fe0e15dbD13004ed8d6f", 8, "BTOK", None),
    (ETH, "0x00000000000000000000000000000000000000a1", 6, "USDC", "g-usdc"),
    (ARB, "0x00000000000000000000000000000000000000a2", 6, "USDC", "g-usdc"),
    (ETH, "0x00000000000000000000000000000000000000b1", 18, "WETH", None),
    (UNMAPPED, "0x00000000000000000000000000000000000000c1", 18, "TAO-X", None),
    (ARB, "0x00000000000000000000000000000000000000d1", 18, "LINK", None),
]

# coins.llama.fi: coin -> (price, decimals). Absent = DefiLlama answers with no price. The Bitlayer token is absent.
PRICES = {
    "solana:Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk": (1.0, 6),
    "polygon:0xb5c064f955d8e7f38fe0460c556a72987494ee17": (0.02, 18),
    "hyperliquid:0x77146784315ba81904d654466968e3a7c196d1f3": (0.5, 18),
    "base:0xe2f9db0186b13668aec9fe0e15dbd13004ed8d6f": (1.0, 8),
    "ethereum:0x00000000000000000000000000000000000000a1": (1.0, 6),
    "ethereum:0x00000000000000000000000000000000000000b1": (2000.0, 18),
    "arbitrum:0x00000000000000000000000000000000000000d1": (10.0, 18),
}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeCcip:
    """api.ccip.chain.link: the list pages `page` records at a time from `messages` (newest first) with the offset as
    the cursor; details by id; the token directory in one page. Every call advances the clock by `step`."""

    def __init__(self, messages, details=None, clock=None, page=4, step=1.0, refuse_cursor=False):
        self.messages, self.details, self.clock, self.page, self.step = messages, details or {}, clock, page, step
        self.calls, self.refuse_cursor = [], refuse_cursor

    def get(self, url, params=None, headers=None, deadline=None):
        params = dict(params or {})
        self.calls.append((url, params))
        if self.clock is not None:
            self.clock.t += self.step
        assert url.startswith("https://api.ccip.chain.link/v2/"), url
        if url.endswith("/v2/messages"):
            assert params["environment"] == "mainnet" and params["limit"] == 1000
            if self.refuse_cursor and params.get("cursor"):
                raise HttpError(400, url, "The filter conflicts with the cursor")
            i = int(params.get("cursor") or 0)
            chunk = self.messages[i:i + self.page]
            nxt = i + len(chunk)
            more = nxt < len(self.messages)
            return {"data": chunk, "pagination": {"limit": 1000, "hasNextPage": more, "cursor": str(nxt) if more else None}}
        if "/v2/messages/" in url:
            mid = url.rsplit("/", 1)[1]
            return self.details[mid]
        if url.endswith("/v2/tokens"):
            assert params["reviewedOnly"] == "false"
            return {"data": [{"chainSelector": s, "address": a, "decimals": d, "symbol": y, "groupId": g}
                             for s, a, d, y, g in DIRECTORY], "pagination": {"hasNextPage": False, "cursor": None}}
        raise AssertionError(url)

    def list_calls(self):
        return [p for u, p in self.calls if u.endswith("/v2/messages")]

    def detail_calls(self):
        return [u.rsplit("/", 1)[1] for u, _ in self.calls if "/v2/messages/" in u]


class FakeLlama:
    def __init__(self, prices=PRICES, fail=False):
        self.prices, self.fail, self.calls = prices, fail, []

    def get(self, url, params=None, headers=None, deadline=None):
        assert url.startswith("https://coins.llama.fi/prices/historical/") and params == {"searchWidth": "12h"}
        ts, coins = url[len("https://coins.llama.fi/prices/historical/"):].split("/", 1)
        self.calls.append((int(ts), coins.split(",")))
        if self.fail:
            raise RuntimeError("HTTP 502 from https://coins.llama.fi/prices/historical/***")
        out = {}
        for c in coins.split(","):
            p = self.prices.get(c) or self.prices.get(c.lower())
            if p:
                out[c] = {"price": p[0], "decimals": p[1], "symbol": "X", "timestamp": int(ts), "confidence": 0.99}
        return {"coins": out}


@pytest.fixture
def at(monkeypatch):
    """Pin 'today' (UTC)."""
    def pin(day):
        monkeypatch.setattr(cv, "today", lambda: pd.Timestamp(day))
    return pin


def make(tmp_path, ccip, llama=None, clock=None, **spec):
    f = cv.CcipValue(http=ccip, price_http=llama or FakeLlama(), clock=clock or Clock(), root=tmp_path)
    base = {"history_days": 3, "budget_s": 1e9, "detail_sample": 0.0, "seed_detail_sample": 0.0,
            "max_unpriced_share": 0.10}
    base.update(spec)
    return f, [{"name": "Chainlink", "ccip_value": base}]


def rows(out, metric):
    df = out.frame()
    df = df[df["metric"] == metric]
    return {str(d.date()): (v, s) for d, v, s in zip(df["date"], df["value"], df["source"])}


def msg(mid, ts, src=ETH, dst=ARB, token=None, amount=None):
    return {"messageId": mid, "sendTimestamp": ts, "sourceNetworkInfo": {"chainSelector": src, "chainFamily": "EVM"},
            "destNetworkInfo": {"chainSelector": dst},
            "sourceTokenAmount": {"tokenAddress": token, "tokenAmount": str(amount)} if token else None}


def detail(version, *tokens):
    return {"version": version, "tokenAmounts": [{"sourceTokenAddress": a, "destTokenAddress": "0xd",
                                                  "sourcePoolAddress": "0xp", "amount": str(n)} for a, n in tokens]}


USDC_E, WETH, LINK_A, USDC_A = ("0x00000000000000000000000000000000000000a1", "0x00000000000000000000000000000000000000b1",
                                "0x00000000000000000000000000000000000000d1", "0x00000000000000000000000000000000000000a2")


# ============================================================================================ day aggregation
def test_day_aggregation_from_recorded_page(tmp_path, at):
    at("2026-10-21")
    ccip, llama = FakeCcip(RECORDED, page=1000), FakeLlama()
    f, projects = make(tmp_path, ccip, llama, history_days=1, max_unpriced_share=0.25)
    out = FetchOutput()
    f.run(projects, None, out)

    st = json.loads((tmp_path / ".cache/ccip/state.json").read_text())
    rec = st["days"]["2026-10-20"]
    assert rec["stage"] == "closed" and rec["n_msgs"] == 9 and rec["n_token_msgs"] == 5
    assert rec["tokens"] == {
        f"{SOL}|Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk": ["70466445", 1],          # base58 kept as served
        f"{POLY}|0xb5c064f955d8e7f38fe0460c556a72987494ee17": ["10822429188040000000000", 1],
        f"{BITLAYER}|0x0e4cf4affdb72b39ea91fa726d291781cbd020bf": ["279306000000000000000", 1],
        f"{HYPER}|0x77146784315ba81904d654466968e3a7c196d1f3": ["11733604528818958658376", 1],
        f"{BASE}|0xe2f9db0186b13668aec9fe0e15dbd13004ed8d6f": ["8009259208", 1]}
    assert "2026-10-19" not in st["days"], "the day the walk only touched is not closed"
    assert st["walks"] == [], "the walk passed below its lower bound: done"

    # one page read, one DefiLlama call at 12:00 UTC of the day
    assert len(ccip.list_calls()) == 1 and len(llama.calls) == 1
    assert llama.calls[0][0] == int(pd.Timestamp("2026-10-20 12:00").timestamp())
    assert "btr:0x0e4cf4affdb72b39ea91fa726d291781cbd020bf" in llama.calls[0][1]

    v = rows(out, cv.VALUE)
    want = 70.466445 * 1.0 + 10822.42918804 * 0.02 + 11733.604528818958658376 * 0.5 + 80.09259208 * 1.0
    value, src = v["2026-10-20"]
    assert value == pytest.approx(want)
    # the Bitlayer token has no price: 1 of 5 transfers, 20% of messages — PARTIAL (under 25%), not valued at 0
    assert src == "ccip_api:messages.sourceTokenAmount x llama price:PARTIAL[1 of 5 transfers unpriced, 20.0% of messages]"
    assert rows(out, cv.TRANSFERS) == {"2026-10-20": (5.0, cv.SRC_COUNT)}
    assert all("?" not in e.message for e in out.log), "no query string (or key) in a log line"


def test_decimals_scaling_and_llama_decimals_fallback():
    rec = {"n_token_msgs": 3, "multi": [], "tokens": {
        f"{ETH}|{USDC_E}": [str(1_500_000), 1],                 # 1.5 USDC (6 dp)
        f"{ETH}|{WETH}": [str(2 * 10 ** 18), 1],                # 2 WETH (18 dp)
        f"{BASE}|0xe2f9": [str(123_456_789), 1]}}               # 1.23456789 (8 dp, from DefiLlama)
    px = {f"{ETH}|{USDC_E}": (1.0, 6), f"{ETH}|{WETH}": (2000.0, 18), f"{BASE}|0xe2f9": (10.0, 8)}
    v = cv.day_value(rec, lambda k: px[k])
    assert v["value"] == pytest.approx(1.5 + 4000.0 + 12.3456789)
    assert v["by_token"][f"{ETH}|{WETH}"] == pytest.approx(4000.0)
    assert v["unpriced_msgs"] == 0 and v["share"] == 0.0

    # the module's price_of: directory decimals first, DefiLlama's where the directory has none
    f = cv.CcipValue(http=object(), price_http=object())
    f.spec = dict(cv.DEFAULTS)
    f._tok = {"tokens": {f"{ETH}|{USDC_E}": [6, "USDC", None], f"{BASE}|0xe2f9": [None, "B", None]}}
    f._groups = {}
    f.prices = {"2026-10-20": {f"ethereum:{USDC_E}": [1.0, 18], "base:0xe2f9": [10.0, 8]}}
    p = f.price_of("2026-10-20")
    assert p(f"{ETH}|{USDC_E}") == (1.0, 6), "the CCIP directory's decimals win over DefiLlama's"
    assert p(f"{BASE}|0xe2f9") == (10.0, 8)
    assert p(f"{ETH}|0xnew") == "pending", "a coin not yet asked is pending, not unpriced"


# ============================================================================================ unknown is never 0
def _ten_messages(day="2026-10-19", unpriced=1, unmapped=0):
    ms = [msg(f"0x{i:064x}", f"{day}T12:{i:02d}:00.000Z", token=USDC_E, amount=1_000_000) for i in range(10)]
    for i in range(unpriced):
        ms[i] = msg(f"0x{i:064x}", f"{day}T12:{i:02d}:00.000Z", src=BITLAYER, token="0xdead", amount=10 ** 18)
    for i in range(unpriced, unpriced + unmapped):
        ms[i] = msg(f"0x{i:064x}", f"{day}T12:{i:02d}:00.000Z", src=UNMAPPED,
                    token="0x00000000000000000000000000000000000000c1", amount=10 ** 18)
    ms.sort(key=lambda m: m["sendTimestamp"], reverse=True)
    return ([msg("0xtoday", "2026-10-20T01:00:00.000Z")] + ms
            + [msg("0xolder", "2026-10-18T23:00:00.000Z")])


def test_unpriced_token_marks_partial_never_zero(tmp_path, at):
    at("2026-10-20")
    f, projects = make(tmp_path, FakeCcip(_ten_messages(unpriced=1)), history_days=1, max_unpriced_share=0.10)
    out = FetchOutput()
    f.run(projects, None, out)
    value, src = rows(out, cv.VALUE)["2026-10-19"]
    assert value == pytest.approx(9.0), "9 priced USDC; the unpriced token adds nothing and is said"
    assert src.endswith(":PARTIAL[1 of 10 transfers unpriced, 10.0% of messages]")
    assert rows(out, cv.TRANSFERS)["2026-10-19"][0] == 10


def test_unpriced_share_above_limit_is_refused(tmp_path, at):
    at("2026-10-20")
    llama = FakeLlama()
    f, projects = make(tmp_path, FakeCcip(_ten_messages(unpriced=1, unmapped=2)), llama, history_days=1,
                       max_unpriced_share=0.10)
    out = FetchOutput()
    f.run(projects, None, out)
    assert "2026-10-19" not in rows(out, cv.VALUE), "30% unpriced > 10%: refused, not stored"
    assert rows(out, cv.TRANSFERS)["2026-10-19"][0] == 10, "the count needs no price and is stored"
    refusal = [e for e in out.log if "REFUSED" in e.message]
    assert refusal and "30.0% of messages unpriced" in refusal[0].message
    # an unmapped chain is never asked of DefiLlama
    assert not any(c.startswith("None") or "0x00000000000000000000000000000000000000c1" in c
                   for _, cs in llama.calls for c in cs)


def test_all_unpriced_day_is_refused_not_zero(tmp_path, at):
    at("2026-10-20")
    f, projects = make(tmp_path, FakeCcip(_ten_messages(unpriced=10)), history_days=1, max_unpriced_share=0.10)
    out = FetchOutput()
    f.run(projects, None, out)
    assert rows(out, cv.VALUE) == {}
    assert any(e.status == "failed" and "REFUSED" in e.message for e in out.log), \
        "nothing stored and a day refused: a failure, not a quiet zero"


def test_price_call_failure_leaves_day_pending_then_prices_it(tmp_path, at):
    at("2026-10-20")
    ccip = FakeCcip(_ten_messages(unpriced=0))
    f, projects = make(tmp_path, ccip, FakeLlama(fail=True), history_days=1)
    out = FetchOutput()
    f.run(projects, None, out)
    assert rows(out, cv.VALUE) == {}, "a failed CALL is not a 'no price': nothing stored, nothing valued 0"
    assert any("awaiting prices" in e.message for e in out.log)
    f2, _ = make(tmp_path, ccip, FakeLlama(), history_days=1)
    out2 = FetchOutput()
    n_list = len(ccip.list_calls())
    f2.run(projects, None, out2)
    assert len(ccip.list_calls()) == n_list, "the closed day is not walked again — only priced"
    assert rows(out2, cv.VALUE)["2026-10-19"] == (pytest.approx(10.0), cv.SRC)


def test_group_sibling_prices_an_unpriced_chain(tmp_path, at):
    at("2026-10-20")
    ms = [msg("0xtoday", "2026-10-20T01:00:00.000Z"),
          msg("0x1", "2026-10-19T10:00:00.000Z", src=ARB, dst=ETH, token=USDC_A, amount=5_000_000),
          msg("0xolder", "2026-10-18T10:00:00.000Z")]
    llama = FakeLlama()
    f, projects = make(tmp_path, FakeCcip(ms), llama, history_days=1)
    out = FetchOutput()
    f.run(projects, None, out)
    # arbitrum USDC has no DefiLlama price here; its CCIP group sibling on Ethereum does — 5 USDC (Arbitrum's 6 dp)
    assert rows(out, cv.VALUE)["2026-10-19"] == (pytest.approx(5.0), cv.SRC)
    asked = [c for _, cs in llama.calls for c in cs]
    assert asked == [f"arbitrum:{USDC_A}", f"ethereum:{USDC_E}"]


# ============================================================================================ multi-token messages
def _lanes_day():
    """2026-10-19: lane ETH>ARB on 2.0.0 (two messages), lane ARB>ETH on 1.6.0 (three, one moving two tokens)."""
    ms = [msg("0xtoday", "2026-10-20T01:00:00.000Z"),
          msg("0xv2b", "2026-10-19T20:00:00.000Z", ETH, ARB, USDC_E, 2_000_000),
          msg("0xv1c", "2026-10-19T18:00:00.000Z", ARB, ETH, LINK_A, 10 ** 18),
          msg("0xv1b", "2026-10-19T16:00:00.000Z", ARB, ETH, LINK_A, 2 * 10 ** 18),     # + 3 USDC in the detail
          msg("0xv2a", "2026-10-19T09:00:00.000Z", ETH, ARB, USDC_E, 1_000_000),
          msg("0xv1a", "2026-10-19T08:00:00.000Z", ARB, ETH, LINK_A, 10 ** 18),
          msg("0xolder", "2026-10-18T22:00:00.000Z")]
    details = {"0xv2a": detail("2.0.0", (USDC_E, 1_000_000)), "0xv2b": detail("2.0.0", (USDC_E, 2_000_000)),
               "0xv1a": detail("1.6.0", (LINK_A, 10 ** 18)), "0xv1c": detail("1.6.0", (LINK_A, 10 ** 18)),
               "0xv1b": detail("1.6.0", (LINK_A, 2 * 10 ** 18), (USDC_A, 3_000_000))}
    return ms, details


def test_multi_token_message_detected_and_counted(tmp_path, at):
    at("2026-10-20")
    ms, details = _lanes_day()
    ccip = FakeCcip(ms, details)
    f, projects = make(tmp_path, ccip, history_days=1, detail_sample=1.0)
    out = FetchOutput()
    f.run(projects, None, out)
    st = json.loads((tmp_path / ".cache/ccip/state.json").read_text())
    rec = st["days"]["2026-10-19"]
    # the 2.0 lane: only its OLDEST message read (OnRamp 2.0.0 cannot carry two tokens); the 1.6 lane: all read
    assert sorted(ccip.detail_calls()) == ["0xv1a", "0xv1b", "0xv1c", "0xv2a"]
    assert rec["check"] == {"rate": 1.0, "lanes": 2, "lanes_v2": 1, "lanes_v1": 1, "v1_msgs": 3, "v1_read": 3,
                            "multi_lane_days": 1}
    assert rec["multi"] == [[f"{ARB}|{LINK_A}", f"{ARB}|{USDC_A}"]]
    assert rec["tokens"][f"{ARB}|{USDC_A}"] == ["3000000", 1], "the second token the list did not show"
    assert rec["n_token_msgs"] == 5
    assert st["detail"] == {}, "detail reads are dropped once the day is closed"
    value, _src = rows(out, cv.VALUE)["2026-10-19"]
    assert value == pytest.approx(3.0 + 4 * 10.0 + 3.0), "USDC 1+2, LINK 4 x $10, the hidden 3 USDC (sibling price)"
    assert rows(out, cv.TRANSFERS)["2026-10-19"][0] == 5, "transfers = token-moving MESSAGES"
    assert "lanes 2.x" in next(e.message for e in out.log if e.message.startswith(cv.VALUE + ":"))


def test_lane_version_knowledge_skips_probes_the_next_day(tmp_path, at):
    at("2026-10-20")
    ms, details = _lanes_day()
    ccip = FakeCcip(ms, details)
    f, projects = make(tmp_path, ccip, history_days=1, detail_sample=1.0)
    f.run(projects, None, FetchOutput())
    st = json.loads((tmp_path / ".cache/ccip/state.json").read_text())
    assert st["lanes"]["v2_since"] == {f"{ETH}>{ARB}": "2026-10-19T09:00:00.000Z"}
    # the next day: the 2.0 lane is known 2.0 since yesterday — no probe for it
    at("2026-10-21")
    ms2 = [msg("0xt2", "2026-10-21T01:00:00.000Z"),
           msg("0xn1", "2026-10-20T05:00:00.000Z", ETH, ARB, USDC_E, 1_000_000)] + ms
    ccip2 = FakeCcip(ms2, details)
    f2, _ = make(tmp_path, ccip2, history_days=2, detail_sample=1.0)
    out = FetchOutput()
    f2.run(projects, None, out)
    assert ccip2.detail_calls() == [] and "2026-10-20" in rows(out, cv.VALUE)


def test_sampled_multi_token_reads_the_whole_lane_day(tmp_path, at):
    at("2026-10-20")
    # 30 messages on a 1.6 lane; at a 10% sample, one sampled message moves two tokens -> the lane-day is read in full
    ids = [f"0x{i:064x}" for i in range(30)]
    sampled = [m for m in ids if cv.picked(m, 0.1)]
    assert 0 < len(sampled) < 30
    multi = sampled[0]
    ms = [msg("0xtoday", "2026-10-20T01:00:00.000Z")]
    ms += [msg(m, f"2026-10-19T{23 - i // 2:02d}:{30 if i % 2 == 0 else 0:02d}:00.000Z", ARB, ETH, LINK_A, 10 ** 18)
           for i, m in enumerate(ids)]
    ms += [msg("0xolder", "2026-10-18T22:00:00.000Z")]
    details = {m: detail("1.6.0", (LINK_A, 10 ** 18)) for m in ids}
    details[multi] = detail("1.6.0", (LINK_A, 10 ** 18), (USDC_A, 7_000_000))
    ccip = FakeCcip(ms, details, page=100)
    f, projects = make(tmp_path, ccip, history_days=1, detail_sample=0.1, recent_days=1)
    f.run(projects, None, FetchOutput())
    rec = json.loads((tmp_path / ".cache/ccip/state.json").read_text())["days"]["2026-10-19"]
    assert sorted(set(ccip.detail_calls())) == sorted(ids)
    assert rec["check"]["v1_read"] == 30 and rec["check"]["multi_lane_days"] == 1

    # no multi-token message in the sample: only the sample (and the oldest-message probe) is read
    details[multi] = detail("1.6.0", (LINK_A, 10 ** 18))
    ccip2 = FakeCcip(ms, details, page=100)
    f2, projects2 = make(tmp_path / "b", ccip2, history_days=1, detail_sample=0.1, recent_days=1)
    f2.run(projects2, None, FetchOutput())
    assert sorted(set(ccip2.detail_calls())) == sorted(set(sampled) | {ids[-1]})


def test_seed_days_use_the_seed_sample(tmp_path, at):
    at("2026-10-20")
    ms, details = _lanes_day()
    ccip = FakeCcip(ms, details)
    # recent_days=0: no day is 'recent', so 2026-10-19 takes the seed rate — here 0 (the list figure only)
    f, projects = make(tmp_path, ccip, history_days=1, detail_sample=1.0, seed_detail_sample=0.0, recent_days=0)
    f.run(projects, None, FetchOutput())
    rec = json.loads((tmp_path / ".cache/ccip/state.json").read_text())["days"]["2026-10-19"]
    assert ccip.detail_calls() == [] and rec["check"]["rate"] == 0.0
    assert rec["tokens"].get(f"{ARB}|{USDC_A}") is None, "list figure only, as configured"


# ============================================================================================ complete days only
def test_only_fully_covered_complete_days_are_stored(tmp_path, at):
    at("2026-10-20")
    ms = [msg("0xt1", "2026-10-20T03:00:00.000Z", token=USDC_E, amount=9_000_000),     # today: never stored
          msg("0xa1", "2026-10-19T03:00:00.000Z", token=USDC_E, amount=1_000_000),
          # 2026-10-18: no message at all — a gap, refused
          msg("0xb1", "2026-10-17T23:00:00.000Z", token=USDC_E, amount=2_000_000),
          msg("0xb2", "2026-10-17T05:00:00.000Z", token=USDC_E, amount=3_000_000),
          msg("0xc1", "2026-10-16T23:00:00.000Z", token=USDC_E, amount=4_000_000)]    # the API's history ends here
    ccip = FakeCcip(ms, page=2)
    f, projects = make(tmp_path, ccip, history_days=5)
    out = FetchOutput()
    f.run(projects, None, out)
    v = rows(out, cv.VALUE)
    assert set(v) == {"2026-10-19", "2026-10-17"}
    assert v["2026-10-17"][0] == pytest.approx(5.0) and v["2026-10-19"][0] == pytest.approx(1.0)
    st = json.loads((tmp_path / ".cache/ccip/state.json").read_text())
    assert st["days"]["2026-10-18"] == {"stage": "gap"}
    assert "2026-10-16" not in st["days"] and st["floor"] == "2026-10-16", \
        "the oldest day served may continue below what the API serves: not complete, and the floor"
    assert any("2026-10-18 (gap" in e.message for e in out.log if "REFUSED" in e.message)
    # the next run, same day: no walk needed, nothing read again
    f2, _ = make(tmp_path, ccip, history_days=5)
    n = len(ccip.calls)
    f2.run(projects, None, FetchOutput())
    assert len(ccip.calls) == n


def test_daily_run_walks_only_down_to_the_newest_closed_day(tmp_path, at):
    at("2026-10-20")
    older = [msg(f"0xo{i}", f"2026-10-{19 - i // 4:02d}T{20 - i % 4:02d}:00:00.000Z", token=USDC_E, amount=10 ** 6)
             for i in range(12)]                         # 10-19, 10-18, 10-17: four each
    ms = [msg("0xt", "2026-10-20T01:00:00.000Z")] + older + [msg("0xend", "2026-10-16T01:00:00.000Z")]
    ccip = FakeCcip(ms, page=4)
    f, projects = make(tmp_path, ccip, history_days=3)
    f.run(projects, None, FetchOutput())
    # a day later: three new messages on 10-20, then the old ones
    at("2026-10-21")
    new = [msg("0xt2", "2026-10-21T01:00:00.000Z")] + [
        msg(f"0xn{i}", f"2026-10-20T{10 + i:02d}:00:00.000Z", token=USDC_E, amount=10 ** 6) for i in range(3)]
    ccip2 = FakeCcip(new + ms, page=4)
    f2, _ = make(tmp_path, ccip2, history_days=3)
    out = FetchOutput()
    f2.run(projects, None, out)
    assert len(ccip2.list_calls()) == 2, "the head walk stops once it passes below 10-19 (2 pages, not the seed)"
    v = rows(out, cv.VALUE)
    assert v["2026-10-20"][0] == pytest.approx(3.0), "the three new token messages (0xt moved no token)"
    assert set(v) == {"2026-10-20", "2026-10-19", "2026-10-18", "2026-10-17"}


# ============================================================================================ checkpoint / resume
def _seed_messages(days=6, per_day=5):
    out = [msg("0xt", "2026-10-20T01:00:00.000Z")]
    for d in range(days):
        day = pd.Timestamp("2026-10-19") - pd.Timedelta(days=d)
        for i in range(per_day):
            out.append(msg(f"0x{d:02d}{i:02d}", f"{day.date()}T{20 - i:02d}:00:00.000Z", token=WETH, amount=10 ** 18))
    out.append(msg("0xend", "2026-10-10T01:00:00.000Z"))
    return out


def test_checkpoint_and_resume_across_two_budget_limited_runs(tmp_path, at):
    at("2026-10-20")
    ms = _seed_messages()
    # reference: one unlimited run
    ref = FakeCcip(ms, page=3)
    fr, pr = make(tmp_path / "ref", ref, history_days=6)
    out_ref = FetchOutput()
    fr.run(pr, None, out_ref)
    want = rows(out_ref, cv.VALUE)
    assert len(want) == 6 and all(v == pytest.approx(10_000.0) for v, _ in want.values())
    n_pages = len(ref.list_calls())

    # two runs on a 6-second budget, one second a call
    clock = Clock()
    ccip = FakeCcip(ms, page=3, clock=clock, step=1.0)
    f1, p1 = make(tmp_path / "run", ccip, clock=clock, history_days=6, budget_s=6)
    out1 = FetchOutput()
    f1.run(p1, None, out1)
    st = json.loads((tmp_path / "run/.cache/ccip/state.json").read_text())
    assert len(st["walks"]) == 1 and st["walks"][0]["cursor"], "stopped mid-walk with its cursor saved"
    got1 = rows(out1, cv.VALUE)
    assert 0 < len(got1) < 6 and all(want[d][0] == pytest.approx(v) for d, (v, _) in got1.items())
    first_run_calls = len(ccip.list_calls())
    cursor = st["walks"][0]["cursor"]

    clock2 = Clock()
    ccip.clock = clock2
    f2, p2 = make(tmp_path / "run", ccip, clock=clock2, history_days=6, budget_s=1e9)
    out2 = FetchOutput()
    f2.run(p2, None, out2)
    resumed = ccip.list_calls()[first_run_calls]
    assert resumed.get("cursor") == cursor, "the second run resumes from the saved cursor, not the newest message"
    assert len(ccip.list_calls()) == n_pages, "no page read twice across the two runs"
    got2 = rows(out2, cv.VALUE)
    assert {d: v for d, (v, _) in got2.items()} == pytest.approx({d: v for d, (v, _) in want.items()})


def test_refused_cursor_restarts_from_newest_without_double_counting(tmp_path, at):
    at("2026-10-20")
    ms = _seed_messages()
    clock = Clock()
    ccip = FakeCcip(ms, page=3, clock=clock)
    f1, p1 = make(tmp_path, ccip, clock=clock, history_days=6, budget_s=6)
    f1.run(p1, None, FetchOutput())
    ccip.refuse_cursor, ccip.clock = True, None
    f2, p2 = make(tmp_path, ccip, history_days=6)
    out = FetchOutput()
    f2.run(p2, None, out)
    assert any("cursor refused" in e.message for e in out.log)
    st = json.loads((tmp_path / ".cache/ccip/state.json").read_text())
    assert st["walks"] and st["walks"][0]["cursor"] is None, "restarted from the newest message"
    ccip.refuse_cursor = False
    f3, p3 = make(tmp_path, ccip, history_days=6)
    out3 = FetchOutput()
    f3.run(p3, None, out3)
    got = rows(out3, cv.VALUE)
    assert len(got) == 6 and all(v == pytest.approx(10_000.0) for v, _ in got.values()), \
        "the open day restarted from zero: 5 WETH a day, never 5 + a partial recount"


def test_unbounded_seed_run_ignores_the_budget(tmp_path, at):
    at("2026-10-20")
    clock = Clock()
    ccip = FakeCcip(_seed_messages(), page=3, clock=clock, step=1.0)
    f, projects = make(tmp_path, ccip, clock=clock, history_days=6, budget_s=2)
    out = FetchOutput()
    f.run(projects, None, out, unbounded=True)
    assert len(rows(out, cv.VALUE)) == 6 and json.loads((tmp_path / ".cache/ccip/state.json").read_text())["walks"] == []
