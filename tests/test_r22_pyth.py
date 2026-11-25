"""Round 22 (Jake 2026-11-25, AAVEPYTH): Pyth joins as a project — A3 (held buyback), not A2. Its Solana reader
(fetch/pyth_chain.py) is tested with fake RPC answers: the stocks mapped onto the build's series, the stake inside
circulating (voting / OIS overlap and unvested taken off once, with the proof against the program's own totals), the
Council buyback scan and its reconciliation to the monthly reports; circulating on the R22 rule (locked tokens out)."""
from __future__ import annotations

import base64
import hashlib
import struct

import pandas as pd
import pytest

import config
from fetch import pyth_chain as pc
from fetch.base import FetchOutput

PROG = "pytS9TjG1qyAZypk7n8rw8gfW9sUaqqYyMhJQ4E7JCQ"


# ---------------------------------------------------------------------------------------------------------- config
def test_pyth_is_a_project_a3_held_not_a2():
    p = config.PROJECT_BY_NAME["Pyth"]
    assert p["archetypes"] == [3] and p["coingecko_id"] == "pyth-network" and p["defillama_fees_slug"] == "pyth"
    assert "Pyth" not in config.STORE_ONLY_PROJECTS
    assert 2 in config.ARCHETYPE_OFF["Pyth"]
    assert config.archetype_status("Pyth", 3) == ""                       # active, not paused
    assert config.held_buyback_float_metric("Pyth") == "pyth_held_buyback_tokens"


def test_circulating_locked_out_dao_in():
    import fetch.circulating as circ
    spec = config.CIRCULATING_ONCHAIN["Pyth"]
    assert spec["subtract"][0] == "pyth_unvested_tokens"
    assert "pyth_unvested_tokens" not in config.RULE_IN["Pyth"]["legs"]
    rows = (("total_supply", 9_999_958_541.892647), ("pyth_unvested_tokens", 2_342_229_555.902461),
            ("treasury_holding_tokens", 56_874_185.15), ("pyth_operator_tokens", 16_019_821.17),
            ("pyth_issuer_inferred_tokens", 57_810_213.0))
    h = pd.DataFrame([{"date": "2026-11-25", "project": "Pyth", "metric": m, "value": v, "source": "t", "tier": 2}
                      for m, v in rows])
    # read 2026-11-25: 7,657,728,985.99 — CoinGecko / CMC 7,874,958,541.89 (supply − 2.125B); the gap is the PPR /
    # grant locks (187.65M) and the genesis lockup's 29.58M above the docs' 2.125B
    assert circ.series(h, "Pyth").iloc[-1] == pytest.approx(7_657_728_985.990186)
    assert config.unlock_stock_metrics("Pyth") == ("pyth_unvested_tokens",)


# ---------------------------------------------------------------------------------------------------------- PDAs
def test_base58_round_trip_and_the_voting_target_pda():
    for s in (PROG, "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3", "11111111111111111111111111111111"):
        assert pc.b58encode(pc.b58decode(s)) == s
    # the voting target account the D7-1 reader decodes is the PDA ["target", "voting"] of the staking program
    assert pc.find_pda([b"target", b"voting"], PROG) == config.PYTH_STOCKS["voting_target"]


def test_on_curve():
    # a real wallet key (the DAO Treasury's owner is a PDA; the Council vault too) — a PDA is never on the curve
    assert not pc.on_curve(pc.b58decode(config.PYTH_STOCKS["voting_target"]))
    assert pc.on_curve(pc.b58decode("2ru31e9g8RF2mSSNgTQ11QMb166NE6LJccmBqGJM8xxy"))     # Douro's paying wallet (EOA)


# ---------------------------------------------------------------------------------------------------------- positions
def _slot(amount: int, act: int, unl: int | None, tgt: int) -> bytes:
    b = b"\x01" + struct.pack("<QQ", amount, act) + ((b"\x01" + struct.pack("<Q", unl)) if unl is not None else b"\x00")
    b += bytes([tgt]) + (b"\x07" * 32 if tgt == 1 else b"")
    return b.ljust(200, b"\x00")


def _positions(*slots) -> bytes:
    return b"\x00" * 8 + b"\x09" * 32 + b"".join(slots) + b"\x00" * 200


def test_position_states():
    assert pc.position_state(10, None, 9) == "LOCKING"
    assert pc.position_state(10, None, 10) == "LOCKED"
    assert pc.position_state(10, 12, 11) == "LOCKED"
    assert pc.position_state(10, 12, 12) == "UNLOCKING"
    assert pc.position_state(10, 12, 13) == "UNLOCKED"
    assert pc.position_state(12, 11, 20) == "UNLOCKED"          # unlocked before it activated


def test_stake_inside_circulating_takes_overlap_and_unvested_off_once():
    e = 100
    a1 = "So11111111111111111111111111111111111111112"
    a2 = "Vote111111111111111111111111111111111111111"
    acc = [(a1, _positions(_slot(500, 90, None, 0), _slot(300, 90, None, 1), _slot(70, 80, 95, 0))),  # unlocked 70
           (a2, _positions(_slot(1_000, 90, None, 0), _slot(1_000, 90, None, 1), _slot(5, 101, None, 0)))]
    meta2 = pc.find_pda([b"stake_metadata", pc.b58decode(a2)], PROG)
    t = pc.staked_inside_circulating(acc, {meta2: 800}, PROG, e)
    assert t["vote_locked"] == 1_500 and t["ois_locked"] == 1_300   # what the target and the pool count
    assert t["union"] == 500 + 1_005                                 # max(voting, OIS) per account, LOCKING included
    assert t["staked_circ"] == 500 + (1_005 - 800)                   # account 2's unvested 800 comes off first
    assert t["with_vesting"] == 1 and t["bad"] == 0


# ---------------------------------------------------------------------------------------------------------- buyback
ACCT = config.PYTH_CHAIN["council_pyth_account"]
TREAS = config.PYTH_CHAIN["treasury_pyth_account"]
SWAPS = set(config.PYTH_CHAIN["swap_programs"])


def _tx(t: int, pre: dict, post: dict, programs: list[str]) -> dict:
    keys = sorted(set(pre) | set(post))
    tb = lambda d: [{"accountIndex": keys.index(k), "uiTokenAmount": {"amount": str(v)}} for k, v in d.items()]  # noqa: E731
    return {"blockTime": t, "meta": {"err": None, "preTokenBalances": tb(pre), "postTokenBalances": tb(post),
                                     "innerInstructions": []},
            "transaction": {"message": {"accountKeys": [{"pubkey": k} for k in keys],
                                        "instructions": [{"programId": p} for p in programs]}}}


def test_classify():
    dca = "DCA265Vj8a9CEuX1eb1LWRnDT7uK6q1xMipnNyatn23M"
    assert pc.classify(_tx(1, {ACCT: 0}, {ACCT: 12_850_647_230}, [dca]), ACCT, TREAS, SWAPS) == ("bought", 12_850.64723)
    assert pc.classify(_tx(1, {ACCT: 1_803_872_741_588, TREAS: 0}, {ACCT: 0, TREAS: 1_803_872_741_588},
                           ["SQDS4ep65T869zMMBKyuUq6aD6EgTu8psMjkvj52pCf"]), ACCT, TREAS, SWAPS) == \
        ("returned", 1_803_872.741588)
    assert pc.classify(_tx(1, {ACCT: 0}, {ACCT: 5}, ["Some111"]), ACCT, TREAS, SWAPS)[0] == "other_in"
    assert pc.classify(_tx(1, {ACCT: 5}, {ACCT: 5}, [dca]), ACCT, TREAS, SWAPS) is None


def test_reconcile_returns_to_the_monthly_reports():
    rep = config.PYTH_CHAIN["reported_returns"]
    days = {"2026-07-06": {"returned": 1_803_872.741588}, "2026-10-01": {"returned": 2_582_433.550131},
            "2026-08-04": {"returned": 1_144_629.131118}}
    r = pc.reconcile_returns(days, rep)
    assert r["2026-06"] == "2026-07-06" and r["2026-09"] == "2026-10-01" and r["2026-07"] == "2026-08-04"
    assert r["2025-12"] is None                                           # not scanned: never matched on a guess
    assert sum(rep.values()) == pytest.approx(20_549_288.42, abs=0.01)    # the V1 total of the reports


def test_covered_days():
    d = {"scanned_to_ts": int(pd.Timestamp("2026-11-25 10:00").timestamp()), "complete": True}
    days = pc.PythChain.covered_days(d, "2025-12-01", "2026-11-25")
    assert days[0] == "2025-12-01" and days[-1] == "2026-11-24"
    d = {"scanned_to_ts": int(pd.Timestamp("2026-11-25 10:00").timestamp()), "complete": False,
         "oldest_ts": int(pd.Timestamp("2026-11-20 13:00").timestamp())}
    assert pc.PythChain.covered_days(d, "2025-12-01", "2026-11-25") == ["2026-11-21", "2026-11-22", "2026-11-23",
                                                                         "2026-11-24"]
    assert pc.PythChain.covered_days({}, "2025-12-01", "2026-11-25") == []


# ---------------------------------------------------------------------------------------------------------- the tier
def _disc(n: str) -> bytes:
    return hashlib.sha256(f"account:{n}".encode()).digest()[:8]


class FakeSolana:
    """A fake Http: .post(url, body) answers the JSON-RPC methods the D7-1 reader and pyth_chain use."""

    def __init__(self, now: int):
        self.now, self.stats = now, {"calls": 0, "throttled": 0}
        st = config.PYTH_STOCKS
        self.balances = {a: (m, o) for _x, a, m, o, _w in st["token_accounts"]}
        self.amount = {"pyth_treasury_pyth_tokens": 51_523_842_590_210, "pyth_community_council_pyth_tokens": 5_350_342_563_871,
                       "pyth_council_pyth_tokens": 10_785_571_859_155, "pyth_douro_pyth_tokens": 16_019_810_141_000,
                       "pyth_ppr_distribution_pyth_tokens": 11_032_000, "pyth_funder_cewsb_pyth_tokens": 34_416_632_001_000,
                       "pyth_funder_e6nab_pyth_tokens": 23_393_581_000_000}
        self.metric_of = {a: x for x, a, _m, _o, _w in st["token_accounts"]}
        self.list_time = 1_684_591_200
        self.pos = [("Pos1111111111111111111111111111111111111111", _positions(_slot(900_000_000, 1, None, 0),
                                                                              _slot(600_000_000, 1, None, 1)))]
        self.sigs = [{"signature": "s2", "blockTime": now - 86_400, "err": None},
                     {"signature": "s1", "blockTime": now - 3 * 86_400, "err": None}]

    def post(self, url, body):
        self.stats["calls"] += 1
        m, p = body["method"], body["params"]
        ctx = {"context": {"slot": 1}}
        st = config.PYTH_STOCKS
        if m == "getTokenSupply":
            return {"result": {**ctx, "value": {"amount": "9999958541892647", "decimals": 6}}}
        if m == "getAccountInfo":
            a = p[0]
            if a in self.balances:
                mint, owner = self.balances[a]
                return {"result": {**ctx, "value": {"data": {"parsed": {"info": {
                    "mint": mint, "owner": owner,
                    "tokenAmount": {"amount": str(self.amount.get(self.metric_of[a], 0)), "decimals": 6}}}}}}}
            if a == st["staking_config"]:
                d = bytearray(188)
                d[179] = 1
                struct.pack_into("<q", d, 180, self.list_time)
                return {"result": {**ctx, "value": {"data": [base64.b64encode(bytes(d)).decode()]}}}
            if a == st["voting_target"]:
                d = bytearray(41)
                struct.pack_into("<QQQq", d, 9, 2968, 0, 900_000_000, 0)
                return {"result": {**ctx, "value": {"data": [base64.b64encode(bytes(d)).decode()]}}}
            if a == st["ois_pool_data"]:
                d = bytearray(pc.pyth_stocks.OIS_SLICE)
                struct.pack_into("<Qq", d, pc.pyth_stocks.OIS_DEL_AT, 600_000_000, 0)
                return {"result": {**ctx, "value": {"data": [base64.b64encode(bytes(d)).decode()]}}}
            if a == st["ois_pool_config"]:
                return {"result": {**ctx, "value": {"data": [base64.b64encode(b"\x00" * 8).decode()]}}}
            raise AssertionError(a)
        if m == "getProgramAccounts":
            prog, opts = p
            flt = opts.get("filters") or []
            if prog == st["dca_program"]:
                return {"result": {**ctx, "value": []}}
            disc = base64.b64decode(next(f["memcmp"]["bytes"] for f in flt if "memcmp" in f and f["memcmp"]["offset"] == 0))
            if disc == _disc("PositionData"):
                fb = [f for f in flt if "memcmp" in f and f["memcmp"]["offset"] == 8]
                sel = self.pos if not fb or base64.b64decode(fb[0]["memcmp"]["bytes"])[0] == self.pos[0][1][8] else []
                return {"result": {**ctx, "value": [{"pubkey": k, "account": {"data": [base64.b64encode(d).decode()]}}
                                                     for k, d in sel]}}
            tag = base64.b64decode(next(f["memcmp"]["bytes"] for f in flt if "memcmp" in f and f["memcmp"]["offset"] == 44))[0]
            # one genesis lock of 2,000 PYTH x 4 periods (1 period left at `now`) and nothing else
            if tag == 2:
                b = bytearray(72)
                b[32] = 2
                struct.pack_into("<QQQ", b, 33, 2_000_000_000, 31_536_000, 4)
                v = [{"pubkey": "Meta111111111111111111111111111111111111111", "account": {"data": [base64.b64encode(bytes(b)).decode()]}}]
            else:
                v = []
            return {"result": v if not opts.get("withContext") else {**ctx, "value": v}}
        if m == "getSignaturesForAddress":
            return {"result": [] if p[1].get("before") or p[1].get("until") else self.sigs}
        if m == "getTransaction":
            t = {"s2": _tx(self.now - 86_400, {ACCT: 0}, {ACCT: 7_000_000_000}, ["DCA265Vj8a9CEuX1eb1LWRnDT7uK6q1xMipnNyatn23M"]),
                 "s1": _tx(self.now - 3 * 86_400, {ACCT: 5_000_000, TREAS: 0}, {ACCT: 0, TREAS: 5_000_000}, ["SQDS"])}
            return {"result": t[p[0]]}
        raise AssertionError(m)


class _Daily:
    def __init__(self):
        self.marks = {}

    def due(self, k, d):
        return self.marks.get(k) != d

    def done(self, k, d):
        self.marks[k] = d


def test_tier_end_to_end(monkeypatch, tmp_path):
    now = int(pd.Timestamp("2026-11-25 10:00").timestamp())
    monkeypatch.setattr(pc, "today", lambda: pd.Timestamp("2026-11-25"))
    monkeypatch.setitem(config.PYTH_CHAIN, "scan_from", "2026-11-20")
    monkeypatch.setitem(config.PYTH_STOCKS, "rpc_min_interval_s", 0.0)
    fake = FakeSolana(now)
    out = FetchOutput()
    pc.PythChain(http=fake, daily=_Daily(), state_dir=str(tmp_path), clock=lambda: now, budget_s=60).run(
        [config.PROJECT_BY_NAME["Pyth"]], 30, out)
    fr = out.frame()
    v = {m: float(g["value"].iloc[-1]) for m, g in fr.groupby("metric")}
    fails = [e.message for e in out.log if e.status == "failed"]
    assert v["total_supply"] == pytest.approx(9_999_958_541.892647)
    assert v["treasury_holding_tokens"] == pytest.approx(51_523_842.590210 + 5_350_342.563871)
    assert v["pyth_held_buyback_tokens"] == pytest.approx(10_785_571.859155)
    assert v["pyth_operator_tokens"] == pytest.approx(16_019_810.141 + 11.032)
    assert v["pyth_issuer_inferred_tokens"] == pytest.approx(34_416_632.001 + 23_393_581.0)
    # the vesting: one genesis account, 2,000 PYTH over 4 x 365 d from 2023-05-20 → 500 left on 2026-11-25
    assert v["pyth_unvested_tokens"] == pytest.approx(500.0)
    # the stake: one account, voting 900 + OIS 600 → 900 staked (max), its metadata is not that PDA → nothing off
    assert v["locked_tokens"] == pytest.approx(900.0)
    # the buyback scan: 7,000 bought on 11-24, 5 returned on 11-22; 11-20..11-24 covered, the rest measured 0
    b = fr[fr.metric == "actual_buyback_tokens"].set_index(fr[fr.metric == "actual_buyback_tokens"]["date"].astype(str).str[:10])
    assert b["value"].to_dict() == {"2026-11-20": 0.0, "2026-11-21": 0.0, "2026-11-22": 0.0, "2026-11-23": 0.0,
                                    "2026-11-24": 7_000.0}
    r = fr[fr.metric == "pyth_buyback_returned_tokens"]
    assert r["value"].sum() == pytest.approx(5.0)
    assert not [f for f in fails if "pyth_unvested" in f or "locked" in f or "total" in f], fails


def test_stake_proof_refuses_a_mismatch(monkeypatch, tmp_path):
    now = int(pd.Timestamp("2026-11-25 10:00").timestamp())
    fake = FakeSolana(now)
    monkeypatch.setitem(config.PYTH_CHAIN, "position_partitions", 1)
    with pytest.raises(RuntimeError, match="proof failed"):
        pc.PythChain(http=fake, daily=_Daily(), state_dir=str(tmp_path), clock=lambda: now).stake({"vote": 1.0, "ois": 600.0})


def test_stake_read_once_per_epoch_and_its_staleness(monkeypatch, tmp_path):
    now = int(pd.Timestamp("2026-11-25 10:00").timestamp())
    monkeypatch.setattr(pc, "today", lambda: pd.Timestamp("2026-11-25"))
    monkeypatch.setitem(config.PYTH_CHAIN, "position_partitions", 1)
    d = _Daily()
    d.done("pyth_chain:Pyth:stake", f"epoch-{now // 604_800}")
    out = FetchOutput()
    pc.PythChain(http=FakeSolana(now), daily=d, state_dir=str(tmp_path), clock=lambda: now, budget_s=5).run(
        [config.PROJECT_BY_NAME["Pyth"]], 30, out)
    assert "locked_tokens" not in set(out.frame()["metric"]) and ("Pyth", "locked_tokens") in out.current
    assert config.stale_after_days("Pyth", "locked_tokens", 3) == 9


def test_outflow_categories():
    assert config.outflow_metrics("Aave", "a") == ("emissions_tokens",)
    assert config.outflow_metrics("Pyth", "e") == ("unlock_release_tokens",)
    assert not config.outflow_metrics("Pyth", "b")             # PPR: no series stored yet (a named gap, never 0)


def test_scan_keeps_its_progress_when_the_rpc_cuts_it_off(tmp_path):
    now = int(pd.Timestamp("2026-11-25 10:00").timestamp())
    fake = FakeSolana(now)
    real = fake.post

    def post(url, body):
        if body["method"] == "getTransaction" and body["params"][0] == "s1":
            raise RuntimeError("HTTP 413 — You have used your data allowance")
        return real(url, body)
    fake.post = post
    p = pc.PythChain(http=fake, daily=_Daily(), state_dir=str(tmp_path), clock=lambda: now, budget_s=60)
    with pytest.raises(RuntimeError, match="413"):
        p.scan(now + 60)
    import json
    st = json.loads((tmp_path / "council_scan.json").read_text())
    assert st["seen"] == ["s2"] and st["days"]["2026-11-24"]["bought"] == pytest.approx(7_000.0)
    assert st["oldest"] == "s2" and not st.get("complete")
