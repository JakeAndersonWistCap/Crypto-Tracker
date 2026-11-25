"""Round 22 (Jake 2026-11-25, AAVEPYTH): Aave — A3 'paused since 19 Apr 2026' that clears itself when buys appear, A4
off; the treasury, held buyback, stake and buys read from Ethereum (fetch/aave_chain.py, fake RPC answers); the
measured emission schedule; circulating on the R22 rule (the Ecosystem Reserve IN, off the float)."""
from __future__ import annotations

import pandas as pd
import pytest

import config
from fetch import aave_chain as ac
from fetch.base import FetchOutput


# ---------------------------------------------------------------------------------------------------------- config
def test_selectors_are_the_keccak_of_their_signatures():
    from eth_utils import keccak
    for sig, sel in ac.SEL.items():
        assert keccak(text=sig).hex()[:8] == sel, sig


def test_archetypes_label_and_a4_off():
    assert config.PROJECT_BY_NAME["Aave"]["archetypes"] == [3]
    assert 4 in config.ARCHETYPE_OFF["Aave"]
    assert config.STATUS_CLEARS_ON_BUYS["Aave"]["label"] == "paused since 19 Apr 2026"


def test_emission_schedule_is_the_measured_one():
    # AssetConfigUpdated on the AAVE-paying stake tokens, summed per day (read 2026-11-25). Was 150/day from 2026-08-28.
    r = config.aave_schedule_rate
    assert r("2025-06-04") is None
    assert r("2025-06-05") == 531.0          # stkAAVE 315 + BPTv2 216
    assert r("2025-12-01") == 390.0          # 260 + 130
    assert r("2026-02-01") == 300.0          # 260 + 40
    assert r("2026-04-19") == 220.0          # 220 + 0 (AIP 459)
    assert r("2026-06-13") == 150.0          # AIP 491
    assert r("2026-11-25") == 150.0


def test_circulating_rule_reserve_in_dead_out():
    import fetch.circulating as circ
    spec = config.CIRCULATING_ONCHAIN["Aave"]
    assert spec["subtract"] == ("burn_address_balance", "treasury_holding_tokens")
    assert config.RULE_IN["Aave"]["legs"] == ("treasury_holding_tokens",)
    h = pd.DataFrame([{"date": "2026-11-25", "project": "Aave", "metric": m, "value": v, "source": "t", "tier": 2}
                      for m, v in (("total_supply", 16_000_000.0), ("burn_address_balance", 0.31),
                                   ("treasury_holding_tokens", 565_058.36))])
    s = circ.series(h, "Aave")
    assert s.iloc[-1] == pytest.approx(16_000_000.0 - 0.31)                  # the Reserve counts IN
    assert circ.series(h, "Aave", rule=False).iloc[-1] == pytest.approx(16_000_000.0 - 0.31 - 565_058.36)


def test_held_buyback_is_the_afc_safe():
    assert config.held_buyback_float_metric("Aave") == "aave_held_buyback_tokens"
    assert config.AAVE_CHAIN["held_buyback_wallets"] == ("afc_safe",)


# ---------------------------------------------------------------------------------------------------------- logic
COW = config.AAVE_CHAIN["buy_sources"]["cow_settlement"].lower()
COLL = config.AAVE_CHAIN["buy_sinks"]["collector"].lower()
AFC = config.AAVE_CHAIN["buy_sinks"]["afc_safe"].lower()


def _ts(day: str, h: int = 12) -> int:
    return int(pd.Timestamp(day).timestamp()) + h * 3600


def test_buys_by_day_counts_only_swap_venues():
    logs = [{"ts": _ts("2026-04-19"), "from": COW, "to": COLL, "value": 229 * 10 ** 18},
            {"ts": _ts("2026-04-19", 15), "from": COW, "to": COLL, "value": 14 * 10 ** 18},
            {"ts": _ts("2026-06-24"), "from": "0xe26acd332dab8c64b70dc95da74b07a5265f3387", "to": AFC,
             "value": 1405 * 10 ** 18}]
    days, other = ac.buys_by_day(logs, config.AAVE_CHAIN["buy_sinks"], config.AAVE_CHAIN["buy_sources"])
    assert days == {"2026-04-19": 243.0}
    assert len(other) == 1 and other[0]["tokens"] == 1405.0


def test_scanned_days_are_whole_days_only():
    lo, hi = _ts("2026-04-10", 5), _ts("2026-04-14", 9)
    assert ac.scanned_days(lo, hi) == ["2026-04-11", "2026-04-12", "2026-04-13"]
    assert ac.scanned_days(int(pd.Timestamp("2026-04-10").timestamp()), hi)[0] == "2026-04-10"


def test_pool_share():
    assert ac.pool_share(15_000.0, 9_000.0, 12_000.0) == pytest.approx(11_250.0)
    assert ac.pool_share(15_000.0, 1.0, 0.0) == 0.0


# ---------------------------------------------------------------------------------------------------------- the tier
def _word(x: int) -> str:
    return hex(x)[2:].rjust(64, "0")


class _Resp:
    def __init__(self, j):
        self._j, self.status_code = j, 200

    def json(self):
        return self._j


class FakeEth:
    """eth_call / eth_getLogs / eth_getBlockByNumber answered from tables."""

    def __init__(self, balances: dict, buys: list, head_ts: int, head_block: int = 26_492_057):
        self.balances, self.buys, self.head_ts, self.head = balances, buys, head_ts, head_block
        self.headers = {}

    def post(self, url, json=None, timeout=None):
        m, p = json["method"], json["params"]
        s = config.AAVE_CHAIN
        if m == "eth_getBlockByNumber":
            n = self.head if p[0] in ("finalized", "latest") else int(p[0], 16)
            ts = self.head_ts - (self.head - n) * 12
            return _Resp({"result": {"number": hex(n), "timestamp": hex(ts)}})
        if m == "eth_getLogs":
            f = p[0]
            lo, hi = int(f["fromBlock"], 16), int(f["toBlock"], 16)
            out = [e for e in self.buys if e["token"].lower() == f["address"].lower() and lo <= e["block"] <= hi]
            return _Resp({"result": [{"blockNumber": hex(e["block"]), "blockTimestamp": hex(e["ts"]),
                                      "topics": [ac.TRANSFER, "0x" + ac._pad(e["from"]), "0x" + ac._pad(e["to"])],
                                      "data": "0x" + _word(e["wei"]), "transactionHash": "0xabc"} for e in out]})
        assert m == "eth_call"
        to, data = p[0]["to"].lower(), p[0]["data"][2:]
        sel, arg = data[:8], data[8:]
        if sel == ac.SEL["balanceOf(address)"]:
            who = "0x" + arg[-40:]
            return _Resp({"result": "0x" + _word(int(self.balances.get((to, who), 0) * 10 ** 18))})
        if sel == ac.SEL["assets(address)"]:
            eps = {s["stake"]["reward_assets"]["stkAAVE"].lower(): 1_736_111_111_111_111}.get(to, 0)
            return _Resp({"result": "0x" + _word(eps) + _word(0) + _word(0)})
        if sel == ac.SEL["totalSupply()"]:
            return _Resp({"result": "0x" + _word(int(self.balances.get((to, "supply"), 0) * 10 ** 18))})
        if sel == ac.SEL["bPool()"]:
            return _Resp({"result": "0x" + _word(int("b001", 16))})
        if sel == ac.SEL["getPoolId()"]:
            return _Resp({"result": "0x" + to[2:].ljust(64, "0")})
        if sel == ac.SEL["getPoolTokens(bytes32)"]:
            aave = int(s["aave"], 16)
            words = [0x60, 0x100, 0, 2, aave, 1, 2, int(9_000 * 10 ** 18), int(1e18)]
            return _Resp({"result": "0x" + "".join(_word(w) for w in words)})
        raise AssertionError(f"unexpected call {sel} to {to}")


class _Daily:
    def __init__(self):
        self.marks = {}

    def due(self, k, d):
        return self.marks.get(k) != d

    def done(self, k, d):
        self.marks[k] = d


def _balances():
    s = config.AAVE_CHAIN
    a, aa, w = s["aave"].lower(), s["aaave"].lower(), {k: v.lower() for k, v in s["wallets"].items()}
    bpt_v2_pool = s["stake"]["bpt"]["stkAAVEwstETHBPTv2"][1].lower()
    bpt_v1_pool = s["stake"]["bpt"]["stkABPT"][1].lower()
    return {(a, w["ecosystem_reserve"]): 558_394.92, (aa, w["collector"]): 6_663.43, (aa, w["afc_safe"]): 240_502.34,
            (a, s["dead"].lower()): 0.31, (a, s["stake"]["stkAAVE"].lower()): 2_464_339.74,
            (a, "0x" + "b001".rjust(40, "0")): 0.0,
            (bpt_v2_pool, s["stake"]["bpt"]["stkAAVEwstETHBPTv2"][0].lower()): 11_008.10, (bpt_v2_pool, "supply"): 12_190.60,
            (bpt_v1_pool, s["stake"]["bpt"]["stkABPT"][0].lower()): 2_794_425.12, (bpt_v1_pool, "supply"): 3_381_494.30}


def _run(buys, monkeypatch, day="2026-11-25"):
    import fetch.aave_chain as mod
    monkeypatch.setattr(mod, "today", lambda: pd.Timestamp(day))
    monkeypatch.setitem(config.AAVE_CHAIN, "scan_from_block", 26_492_057 - 7200 * 20)
    eth = FakeEth(_balances(), buys, head_ts=_ts(day, 10))
    out = FetchOutput()
    ac.AaveChain(session=eth, daily=_Daily()).run([config.PROJECT_BY_NAME["Aave"]], 30, out)
    fr = out.frame()
    return {m: g for m, g in fr.groupby("metric")}, out


def test_tier_reads_every_stock(monkeypatch):
    got, out = _run([], monkeypatch)
    v = {m: float(g["value"].iloc[-1]) for m, g in got.items()}
    assert v["treasury_holding_tokens"] == pytest.approx(558_394.92 + 6_663.43)
    assert v["aave_ecosystem_reserve_tokens"] == pytest.approx(558_394.92)
    assert v["aave_held_buyback_tokens"] == pytest.approx(240_502.34)
    assert v["burn_address_balance"] == pytest.approx(0.31)
    # stkAAVE + the BPTv2 pool's 9,000 AAVE x 11,008.10 / 12,190.60 (the v1 pool's bPool holds none)
    assert v["locked_tokens"] == pytest.approx(2_464_339.74 + 9_000 * 11_008.10 / 12_190.60)
    assert v["aave_emission_rate_tokens"] == pytest.approx(150.0)
    assert not any(e.status == "failed" for e in out.log)


def test_no_buys_is_a_measured_zero_and_the_label_stays(monkeypatch):
    got, _out = _run([], monkeypatch)
    b = got["actual_buyback_tokens"]
    assert len(b) >= 18 and (b["value"] == 0.0).all()
    long = b.assign(project="Aave")
    config.refresh_status_on_buys(long)
    assert config.archetype_status("Aave", 3) == "paused since 19 Apr 2026"


def test_a_buy_clears_the_label_then_its_absence_restores_it(monkeypatch):
    s = config.AAVE_CHAIN
    head_ts = _ts("2026-11-25", 10)
    blk = 26_492_057 - 7200 * 3
    buys = [{"token": s["aave"], "block": blk, "ts": head_ts - 7200 * 3 * 12, "from": COW, "to": COLL,
             "wei": 812 * 10 ** 18}]
    got, _out = _run(buys, monkeypatch)
    b = got["actual_buyback_tokens"]
    assert b["value"].sum() == pytest.approx(812.0)
    try:
        cleared = config.refresh_status_on_buys(b)
        assert config.archetype_status("Aave", 3) == ""
        assert cleared[("Aave", 3)] == str(pd.Timestamp(head_ts - 7200 * 3 * 12, unit="s").date())
        # the same buy from a non-venue source (or before the pause) does not clear it
        config.refresh_status_on_buys(b.assign(source="manual"))
        assert config.archetype_status("Aave", 3) == "paused since 19 Apr 2026"
        config.refresh_status_on_buys(b.assign(date=pd.Timestamp("2026-04-19")))
        assert config.archetype_status("Aave", 3) == "paused since 19 Apr 2026"
    finally:
        config.refresh_status_on_buys(None)
    assert config.archetype_status("Aave", 3) == "paused since 19 Apr 2026"


def test_runs_once_a_day(monkeypatch):
    import fetch.aave_chain as mod
    monkeypatch.setattr(mod, "today", lambda: pd.Timestamp("2026-11-25"))
    d = _Daily()
    d.done("aave_chain:Aave", "2026-11-25")
    out = FetchOutput()
    ac.AaveChain(session=FakeEth({}, [], 0), daily=d).run([config.PROJECT_BY_NAME["Aave"]], 30, out)
    assert out.frame().empty and ("Aave", "treasury_holding_tokens") in out.current
