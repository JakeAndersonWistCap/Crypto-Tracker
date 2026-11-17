"""R8 item 2 — fetch/aero_v3.py: the epoch scan in its own tier, epochs already stored skipped, the scan cached, blocks
found by timestamp, per-manager calls batched, a failing Multicall3 sub-call named (never a partial figure), the two
non-circulating legs, and the supply backfill. Everything runs against a fake Base chain (no network)."""
from __future__ import annotations

import pandas as pd
import pytest
from eth_abi import decode, encode

import config
from fetch import aero_v3 as a
from fetch.base import FetchOutput
from fetch.logcache import LogCache

WAD = a.WAD
NAME = "Aerodrome"
STATE = config.aerodrome_migration_state()
ADDR = {k: v.lower() for k, v in STATE["addresses"].items()}
TOK, VE, MINTER, MIG = ADDR["token"], ADDR["voting_escrow"], ADDR["minter"], ADDR["migration"]
REG, FACTORY, DEPLOY = STATE["relay_factory_registry"].lower(), STATE["voting_rewards_factory"].lower(), STATE["deploy_block"]
OWNERS = [o.lower() for o in config.PROJECT_BY_NAME[NAME]["ve_managed_holdings"]["owners"]]
LEGACY_VE = config.PROJECT_BY_NAME[NAME]["ve_managed_holdings"]["escrow"].lower()
NONCIRC = [c["address"].lower() for k, c in config.PROJECT_BY_NAME[NAME]["contracts"].items()
           if k.startswith("noncirc_") and c.get("chain") == "base"
           and c.get("metric_override", "noncirculating_holding_tokens") == "noncirculating_holding_tokens"]


def _addr(tag: str) -> str:
    return "0x" + (tag * 20)[:40]


VELO, VOTER, FAC1, FAC2 = _addr("7e"), _addr("4a"), _addr("f1"), _addr("f2")
RELAY_A, RELAY_B, PT_A, PT_B = _addr("a1"), _addr("b2"), _addr("c3"), _addr("d4")
MGRS = [_addr("51"), _addr("52"), _addr("53")]
FEE_TOKEN = _addr("ee")


def ts_of(day: str) -> int:
    return int(pd.Timestamp(day, tz="UTC").timestamp())


# THE FAKE CHAIN'S CLOCK: 2 s blocks from the deploy block (2026-10-21 02:24 UTC) to 2026-11-05 00:00, 0.2 s after
T_DEPLOY, T_SWITCH = ts_of("2026-10-21 02:24"), ts_of("2026-11-05")
K = DEPLOY + (T_SWITCH - T_DEPLOY) // 2                    # the last 2 s block


def block_ts(n: int) -> int:
    return T_DEPLOY + 2 * (n - DEPLOY) if n <= K else T_SWITCH + (n - K) // 5


def first_block_at(ts: int) -> int:
    """The first block with timestamp >= ts, from the clock's own formula (not a search)."""
    if ts <= T_SWITCH:
        return DEPLOY + -(-(ts - T_DEPLOY) // 2)
    return K + 5 * (ts - T_SWITCH)


class Revert(Exception):
    pass


class FakeBase:
    """A Base node: headers by number, logs, eth_call and Multicall3.aggregate3 (allowFailure honoured per call)."""

    def __init__(self, now: int, fail=lambda to, fn, args, blk: False, max_span: int = 20_000):
        self.head = first_block_at(now)
        self.fail, self.max_span = fail, max_span
        self.calls: list = []                               # (method, detail)
        self.by_sel = {a.selector(n): n for n in [*a.CALLS, *a.FILING_CALLS, *a.OBSERVED_CALLS]}
        c0 = DEPLOY + 10
        self.created = [(c0, MGRS[0]), (c0 + 30_000, MGRS[1]), (first_block_at(ts_of("2026-11-06")), MGRS[2])]
        self.fees = [(first_block_at(ts_of("2026-10-30")), MGRS[0], 5_000 * WAD),
                     (first_block_at(ts_of("2026-11-07")), MGRS[0], 1_000 * WAD),
                     (first_block_at(ts_of("2026-11-13")), MGRS[1], 400 * WAD)]

    # ------------------------------------------------------------ what each contract answers
    def answer(self, to: str, fn: str, args: list, blk: int):
        if self.fail(to, fn, args, blk):
            raise Revert(f"{fn} on {to}")
        arg_addr = "0x" + f"{args[0]:040x}" if args else ""
        if to == TOK:
            return {"MIGRATION": [int(MIG, 16)], "VELODROME_MIGRATION": [int(VELO, 16)], "totalSupply": [1_300_000_000 * WAD],
                    "balanceOf": [(50_000_000 + blk) * WAD if arg_addr == VELO else 80_000_000 * WAD if arg_addr == MIG
                                  else 1_000_000 * WAD if arg_addr in NONCIRC else 905 * WAD if arg_addr == VE else 0]}[fn]
        if to == MIG:
            return {"V2_TOKEN": [int(a.LEGACY_TOKEN, 16)], "V2_ESCROW": [int(a.LEGACY_ESCROW, 16)], "V3_TOKEN": [int(TOK, 16)],
                    "MIGRATION_TOKEN_ID": [77], "remaining": [80_000_000 * WAD],
                    "previewConversion": [1_820_000_000 * WAD, 1_820_000_000 * WAD, 0, 1]}[fn]
        if to == a.LEGACY_TOKEN:
            return {"totalSupply": [1_900_000_000 * WAD],
                    "balanceOf": [2_000_000 * WAD if arg_addr in NONCIRC else 5 * WAD if arg_addr == MIG else 0]}[fn]
        if to == MINTER:
            return {"emissionsMinted": [1_500 * WAD], "teamRate": [50_000], "emissionRate": [10 ** 15]}[fn]
        if to == VE:
            if fn == "balanceOf":
                return [1 if arg_addr == OWNERS[0] else 0]
            return {"supply": [900 * WAD], "totalVotingPower": [800 * WAD], "permanentStakeBalance": [300 * WAD],
                    "VOTER": [int(VOTER, 16)], "tokenOfOwnerByIndex": [11], "staked": [500 * WAD, 2_000_000_000, 0]}[fn]
        if to == VOTER:
            return {"totalPoint": [0, 0, 2 ** 40, 1_000 * WAD], "totalSlopeChanges": [0]}[fn]
        if to == LEGACY_VE:
            return {"balanceOf": [0]}[fn]
        if to == REG:
            return ("address[]", [FAC1, FAC2])
        if to in (FAC1, FAC2):
            return [1] if fn == "allRelaysLength" else ("address[]", [RELAY_A if to == FAC1 else RELAY_B])
        if to in (RELAY_A, RELAY_B):
            first = to == RELAY_A
            if fn in ("pendingDeposits", "deposits"):
                if (fn == "pendingDeposits") != first:       # each implementation has ONE of the two getters
                    raise Revert(f"no {fn} on {to}")
                return [int(OWNERS[0], 16), 1_794_000_000, 70 + args[0], (7 if first else 3) * WAD]
            return {"principalToken": [int(PT_A if first else PT_B, 16)], "totalBacking": [1_000 * WAD],
                    "depositList": [1, 2, 2] if first else [5, 6, 2]}[fn]
        if to in (PT_A, PT_B):
            return {"totalSupply": [1_000 * WAD], "balanceOf": [100 * WAD if to == PT_A and arg_addr == OWNERS[0] else 0]}[fn]
        if to in MGRS:
            if fn == "incentiveCount":
                return [1]
            start = ts_of("2026-11-06") if to == MGRS[2] else ts_of("2026-12-25")      # only MGRS[2]'s overlaps
            return [int(FEE_TOKEN, 16), 0, 3 * WAD * WAD, start, start + 100, 0]
        raise AssertionError(f"unexpected call {fn} on {to}")

    def raw(self, to: str, data: str, blk: int) -> bytes:
        fn = self.by_sel[data[:10]]
        v = self.answer(to.lower(), fn, a._words_of(data[10:]), blk)
        return encode([v[0]], [v[1]]) if isinstance(v, tuple) else b"".join(int(x).to_bytes(32, "big") for x in v)

    # ------------------------------------------------------------ JSON-RPC
    def rpc(self, method: str, params: list):
        if method == "eth_blockNumber":
            self.calls.append((method, None))
            return hex(self.head)
        if method == "eth_getCode":
            self.calls.append((method, params[0]))
            return "0x6080"
        if method == "eth_getBlockByNumber":
            n = int(params[0], 16)
            self.calls.append((method, n))
            assert 0 <= n <= self.head
            return {"timestamp": hex(block_ts(n))}
        if method == "eth_getLogs":
            q = params[0]
            lo, hi = int(q["fromBlock"], 16), int(q["toBlock"], 16)
            self.calls.append((method, (q["address"].lower() if isinstance(q["address"], str) else "managers", lo, hi)))
            if hi - lo + 1 > self.max_span:
                raise RuntimeError("query exceeds max block range")
            if q["topics"][0] == a.topic0("VotingRewardsCreated"):
                assert q["address"].lower() == FACTORY
                return [{"blockNumber": hex(b), "address": FACTORY, "data": "0x",
                         "topics": [q["topics"][0], "0x" + "00" * 32, "0x" + m[2:].rjust(64, "0")]}
                        for b, m in self.created if lo <= b <= hi]
            return [{"blockNumber": hex(b), "address": m, "data": hex(amt),
                     "topics": [q["topics"][0], "0x" + "00" * 32, "0x" + FEE_TOKEN[2:].rjust(64, "0")]}
                    for b, m, amt in self.fees if lo <= b <= hi and m in [x.lower() for x in q["address"]]]
        assert method == "eth_call", method
        to, data = params[0]["to"].lower(), params[0]["data"]
        blk = self.head if params[1] == "latest" else int(params[1], 16)
        if to != a.MULTICALL3:
            self.calls.append(("eth_call", (to, self.by_sel[data[:10]])))
            try:
                return "0x" + self.raw(to, data, blk).hex()
            except Revert as e:
                raise RuntimeError(f"execution reverted ({e})") from e
        (subs,) = decode(["(address,bool,bytes)[]"], bytes.fromhex(data[10:]))
        self.calls.append(("multicall", len(subs)))
        res = []
        for sub_to, allow, sub in subs:
            try:
                res.append((True, self.raw(sub_to, "0x" + sub.hex(), blk)))
            except Revert:
                if not allow:
                    raise RuntimeError("execution reverted: Multicall3: call failed") from None
                res.append((False, b""))
        return "0x" + encode(["(bool,bytes)[]"], [res]).hex()

    def made(self, method: str) -> list:
        return [d for m, d in self.calls if m == method]


def _legs_declared(monkeypatch):
    for m in ("aero_issuer_liquid_tokens", "aero_velo_reserve_tokens"):
        if m not in config.METRICS:
            monkeypatch.setitem(config.METRICS, m, {"label": m, "kind": "stock", "unit": "tokens", "archetypes": [3],
                                                    "tiers": [2], "sanity_min": 0, "sanity_max": 2e9})


def _stored(days: list) -> pd.DataFrame:
    return pd.DataFrame([{"date": pd.Timestamp(d), "project": NAME, "metric": "voter_rewards_onchain_usd", "value": 1.0,
                          "source": "aero_v3:epoch[3 managers]"} for d in days],
                        columns=["date", "project", "metric", "value", "source"])


NOW = ts_of("2026-11-17 12:00")


# ---------------------------------------------------------------------------------------------- blocks by timestamp
@pytest.mark.parametrize("when", ["2026-10-22", "2026-10-29 13:07:01", "2026-11-04 23:59:59", "2026-11-05",
                                  "2026-11-05 00:00:01", "2026-11-07", "2026-11-12", "2026-11-17 11:59:59"])
def test_a_block_is_found_by_its_timestamp_across_2s_and_200ms_blocks(when):
    """The first block at or after a timestamp, on a chain that made 2 s blocks and then 0.2 s blocks (five to a
    second): right in both eras and at the change, including boundaries more than 3,456,000 blocks behind the head."""
    chain = FakeBase(NOW)
    rdr = a.AeroV3(rpc=chain.rpc, state=STATE)
    ts = ts_of(when)
    got = rdr._block_at(ts)
    assert got == first_block_at(ts)
    assert block_ts(got) >= ts and block_ts(got - 1) < ts
    assert len(chain.made("eth_getBlockByNumber")) <= 40             # a binary search, not a walk
    if when == "2026-11-07":
        assert chain.head - got > 3_456_000                          # outside the window the old search looked in


def test_a_timestamp_after_the_head_is_refused_not_answered_with_the_head():
    rdr = a.AeroV3(rpc=FakeBase(NOW).rpc, state=STATE)
    with pytest.raises(RuntimeError):
        rdr._block_at(NOW + 3600)


# ---------------------------------------------------------------------------------------------- the epoch tier
def test_an_epoch_already_stored_is_skipped_without_a_log_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-11-17"))
    chain = FakeBase(NOW)
    rdr = a.AeroV3(rpc=chain.rpc, prices=lambda toks, ts: {}, state=STATE, now=NOW, part="epoch",
                   stored_long=_stored(["2026-10-22", "2026-10-29", "2026-11-05"]), cache=LogCache(tmp_path))
    out = FetchOutput()
    rdr.run([config.PROJECT_BY_NAME[NAME]], 30, out)
    assert chain.made("eth_getLogs") == [] and chain.made("multicall") == []
    assert out.frame().empty
    assert {(NAME, m) for m in a.EPOCH_METRICS} <= out.current      # current, not a gap
    assert not [e for e in out.log if e.status == "failed"]


def test_only_the_missing_epoch_is_read_and_the_next_run_scans_only_new_blocks(monkeypatch, tmp_path):
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-11-17"))
    prices = lambda toks, ts: {t: (2.0, 18) for t in toks}  # noqa: E731
    chain = FakeBase(NOW)
    rdr = a.AeroV3(rpc=chain.rpc, prices=prices, state=STATE, now=NOW, part="epoch",
                   stored_long=_stored(["2026-10-22", "2026-10-29"]), cache=LogCache(tmp_path))
    out = FetchOutput()
    rdr.run([config.PROJECT_BY_NAME[NAME]], 30, out)
    f = out.frame()
    assert set(pd.to_datetime(f["date"]).dt.strftime("%Y-%m-%d")) == {"2026-11-05"}
    got = dict(zip(f["metric"], f["value"]))
    assert got["voter_rewards_onchain_fees_usd"] == pytest.approx(2_000.0)          # the one fee log inside the epoch
    assert got["voter_rewards_onchain_bribes_usd"] == pytest.approx(600.0)          # 3 tokens/s x 100 s x $2
    assert got["voter_rewards_onchain_apr"] == pytest.approx(2_600.0 * 52 / (1_000.0 * 2.0))
    assert all(str(s).startswith("aero_v3:") for s in f["source"])
    end1 = first_block_at(ts_of("2026-11-12")) - 1
    factory1 = [(lo, hi) for who, lo, hi in chain.made("eth_getLogs") if who == FACTORY]
    assert min(lo for lo, _ in factory1) == DEPLOY and max(hi for _, hi in factory1) == end1
    # the per-manager reads are batched: 3 managers and their 3 programs in two Multicall3 calls, no single eth_call
    assert chain.made("multicall") == [3, 3]
    assert not [d for d in chain.made("eth_call") if d[1] in ("incentiveCount", "incentive")]

    # a week later, same cache: the manager scan starts after the last block scanned
    later = ts_of("2026-11-24 12:00")
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-11-24"))
    chain2 = FakeBase(later)
    out2 = FetchOutput()
    a.AeroV3(rpc=chain2.rpc, prices=prices, state=STATE, now=later, part="epoch",
             stored_long=_stored(["2026-10-22", "2026-10-29", "2026-11-05"]), cache=LogCache(tmp_path)
             ).run([config.PROJECT_BY_NAME[NAME]], 30, out2)
    factory2 = [(lo, hi) for who, lo, hi in chain2.made("eth_getLogs") if who == FACTORY]
    assert min(lo for lo, _ in factory2) == end1 + 1
    f2 = out2.frame()
    assert set(pd.to_datetime(f2["date"]).dt.strftime("%Y-%m-%d")) == {"2026-11-12"}
    assert dict(zip(f2["metric"], f2["value"]))["voter_rewards_onchain_fees_usd"] == pytest.approx(800.0)


def test_the_epoch_tier_is_separate_from_the_supply_tier(monkeypatch, tmp_path):
    """Two registered tiers with their own budgets. The supply tier never scans logs and stores the day's supply rows
    whatever the epoch scan does; the epoch tier, cut by its budget, holds no supply row for a timeout to drop."""
    import fetch
    _legs_declared(monkeypatch)
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-11-17"))
    build = {n: b for n, _t, b in fetch.TIER_ORDER}
    assert {"aero_v3", "aero_v3_epoch"} <= set(build) and {"aero_v3", "aero_v3_epoch"} <= set(fetch.TIER_BUDGET_S)
    ctx = {"prior_delta": {}, "prior_dates": {}, "stored_long": _stored(["2026-10-22"])}
    supply_metrics = {"total_supply", "noncirculating_holding_tokens", "filing_managed_lock_tokens"}

    chain = FakeBase(NOW)
    tier = build["aero_v3"](ctx)
    tier._rpc, tier.now = chain.rpc, NOW
    out = FetchOutput()
    tier.run([config.PROJECT_BY_NAME[NAME]], 30, out)
    assert chain.made("eth_getLogs") == []
    assert supply_metrics <= set(out.frame()["metric"])
    assert not set(out.frame()["metric"]) & set(a.EPOCH_METRICS)

    chain = FakeBase(NOW)
    ticks = iter(range(0, 10 ** 9, 150))                              # every look at the clock is 150 s later
    epoch = build["aero_v3_epoch"](ctx)
    epoch._rpc, epoch._prices, epoch.now, epoch._cache = chain.rpc, (lambda t, ts: {}), NOW, LogCache(tmp_path)
    epoch._clock = lambda: next(ticks)
    out = FetchOutput()
    epoch.run([config.PROJECT_BY_NAME[NAME]], 30, out)
    assert [e for e in out.log if e.status == "failed"]               # it stopped itself and said so
    assert out.frame().empty or not set(out.frame()["metric"]) & (supply_metrics | set(a.SUPPLY_LEGS))
    from fetch.logcache import stream_id
    saved = LogCache(tmp_path).load(stream_id("base", FACTORY, [a.topic0("VotingRewardsCreated")], DEPLOY))
    assert saved["scanned_to"] is not None and saved["scanned_to"] >= DEPLOY     # the cut scan kept its progress


# ---------------------------------------------------------------------------------------------- the Multicall3 revert
def test_both_relay_implementations_deposit_queues_are_read():
    """Relay B (the second factory's implementation) has no pendingDeposits(uint256): its queue is read through
    deposits(uint256) and the figure is whole — 500 sAERO + 100 Relay principal + 2 x 7 + 2 x 3 pending."""
    chain = FakeBase(NOW)
    total, _msg = a.AeroV3(rpc=chain.rpc, state=STATE)._filing_read(NAME, chain.head, STATE)
    assert total == pytest.approx(620.0)


@pytest.mark.parametrize("to,fn", [(RELAY_B, "deposits"), (RELAY_A, "totalBacking"), (PT_B, "totalSupply")])
def test_a_failing_sub_call_is_named_and_no_partial_figure_is_written(monkeypatch, to, fn):
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-11-17"))
    _legs_declared(monkeypatch)
    chain = FakeBase(NOW, fail=lambda t, f, args, blk: (t, f) == (to, fn))
    rdr = a.AeroV3(rpc=chain.rpc, state=STATE)
    with pytest.raises(RuntimeError) as err:
        rdr._filing_read(NAME, chain.head, STATE)
    assert to in str(err.value) and a.selector(fn) in str(err.value)          # the address and the selector that failed
    out = FetchOutput()
    rdr._supply(NAME, ADDR, out, STATE)
    f = out.frame()
    assert "filing_managed_lock_tokens" not in set(f["metric"])                # no figure — not the readable part, not 0
    assert {"total_supply", "noncirculating_holding_tokens"} <= set(f["metric"])
    fails = [e.message for e in out.log if e.status == "failed"]
    assert any(to in m and a.selector(fn) in m for m in fails)


def test_multi_try_marks_only_the_failed_call():
    chain = FakeBase(NOW, fail=lambda t, f, args, blk: (t, f) == (RELAY_A, "totalBacking"))
    rdr = a.AeroV3(rpc=chain.rpc, state=STATE)
    calls = [(RELAY_A, "principalToken", ()), (RELAY_A, "totalBacking", ()), (RELAY_B, "totalBacking", ())]
    got = rdr.multi_try(calls, chain.head)
    assert got[1] is None and int(got[0], 16) == int(PT_A, 16) and int(got[2], 16) == 1_000 * WAD
    with pytest.raises(RuntimeError):
        rdr.multi(calls, chain.head)


# ---------------------------------------------------------------------------------------------- the two legs
def test_the_supply_read_stores_the_issuer_liquid_and_velodrome_reserve_legs(monkeypatch):
    _legs_declared(monkeypatch)
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-11-17"))
    chain = FakeBase(NOW)
    out = FetchOutput()
    a.AeroV3(rpc=chain.rpc, state=STATE)._supply(NAME, ADDR, out, STATE)
    got = out.frame().set_index("metric")["value"]
    velo_held, new_nc = 50_000_000.0 + chain.head, len(NONCIRC) * 1_000_000.0
    assert got["aero_issuer_liquid_tokens"] == pytest.approx(new_nc)
    assert got["aero_velo_reserve_tokens"] == pytest.approx(velo_held)
    exp = a.supply_once(1_300e6, 80e6, velo_held, new_nc, 1_900e6, len(NONCIRC) * 2e6, 1_820e6 + 5, 1.0)
    assert got["total_supply"] == pytest.approx(exp["total"])                  # supply_once's outputs are unchanged
    assert got["noncirculating_holding_tokens"] == pytest.approx(exp["noncirculating"])


def test_a_leg_that_cannot_be_read_is_not_stored_as_zero(monkeypatch):
    _legs_declared(monkeypatch)
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-11-17"))
    chain = FakeBase(NOW, fail=lambda t, f, args, blk: t == TOK and f == "balanceOf" and args and args[0] == int(VELO, 16))
    out = FetchOutput()
    rdr = a.AeroV3(rpc=chain.rpc, state=STATE, part="state")
    rdr.run([config.PROJECT_BY_NAME[NAME]], 30, out)
    f = out.frame()
    assert not set(f["metric"]) & {"aero_velo_reserve_tokens", "aero_issuer_liquid_tokens", "total_supply",
                                   "noncirculating_holding_tokens"}
    assert [e for e in out.log if e.status == "failed"]


# ---------------------------------------------------------------------------------------------- the backfill
def _days(first: str, last: str) -> list:
    return [str(d.date()) for d in pd.date_range(first, last)]


def test_the_backfill_writes_one_row_per_missing_day_and_skips_stored_days(monkeypatch):
    _legs_declared(monkeypatch)
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-11-17"))
    stored = set(_days("2026-10-22", "2026-10-29")) | {"2026-11-10"}
    have = {m: set(stored) for m in a.SUPPLY_LEGS}
    have["total_supply"].add("2026-11-12")                           # a day holding one leg only
    bad_block = first_block_at(ts_of("2026-11-03"))                  # that day's filing leg cannot be read
    chain = FakeBase(NOW, fail=lambda t, f, args, blk: (t, f) == (RELAY_B, "deposits") and blk == bad_block)
    landed = []
    rows, why = a.supply_history(config.PROJECT_BY_NAME[NAME], have, reader=a.AeroV3(rpc=chain.rpc, state=STATE),
                                 write=landed.append, state=STATE)
    f = pd.DataFrame(rows)
    f["day"] = pd.to_datetime(f["date"]).dt.strftime("%Y-%m-%d")
    missing = set(_days("2026-10-30", "2026-11-16")) - {"2026-11-10"}
    assert not f.duplicated(["day", "metric"]).any()                 # one row a day a leg
    for m in a.SUPPLY_LEGS:
        want = set(missing)
        if m == "total_supply":
            want -= {"2026-11-12"}
        if m == "filing_managed_lock_tokens":
            want -= {"2026-11-03"}                                   # unread: no row, never 0
        assert set(f[f["metric"] == m]["day"]) == want, m
    assert not set(f["day"]) & stored
    assert "2026-11-03" in why
    assert sum(len(x) for x in landed) == len(f) and len(landed) == len(missing)      # written day by day
    assert list(f["day"]) == sorted(f["day"], reverse=True)          # newest first
    # every figure is the state at ITS day's first block, found by timestamp on both sides of the block-time change
    velo = f[f["metric"] == "aero_velo_reserve_tokens"].set_index("day")["value"]
    for day in ("2026-10-30", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-16"):
        assert velo[day] == pytest.approx(50_000_000.0 + first_block_at(ts_of(day)))
    assert f[f["metric"] == "filing_managed_lock_tokens"]["value"].tolist() == pytest.approx([620.0] * 16)
    assert set(f[f["metric"] == "filing_managed_lock_tokens"]["source"]) == {
        config.mark_source("aero_v3:filing_locks", "archive")}
    # no header, call or log is asked for a day already stored: a second pass over a full store reads nothing
    full = {m: set(_days("2026-10-22", "2026-11-16")) for m in a.SUPPLY_LEGS}
    chain2 = FakeBase(NOW)
    rows2, _ = a.supply_history(config.PROJECT_BY_NAME[NAME], full, reader=a.AeroV3(rpc=chain2.rpc, state=STATE),
                                state=STATE)
    assert rows2 == [] and chain2.calls == []


def test_the_backfill_stops_at_its_time_budget_and_at_a_day_it_cannot_read(monkeypatch):
    _legs_declared(monkeypatch)
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-11-17"))
    have = {m: set() for m in a.SUPPLY_LEGS}
    chain = FakeBase(NOW)
    rows, _why = a.supply_history(config.PROJECT_BY_NAME[NAME], have, deadline=10.0, clock=lambda: 11.0,
                                  reader=a.AeroV3(rpc=chain.rpc, state=STATE), state=STATE)
    assert rows == [] and chain.calls == []
    ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0])                       # two days fit, the third look is past the budget
    rows, _why = a.supply_history(config.PROJECT_BY_NAME[NAME], have, deadline=10.0, clock=lambda: next(ticks),
                                  reader=a.AeroV3(rpc=FakeBase(NOW).rpc, state=STATE), state=STATE)
    assert {str(r["date"])[:10] for r in rows} == {"2026-11-16", "2026-11-15"}
    # a day whose supply terms cannot be read (no archive state): nothing is written for it or for any older day
    cut = first_block_at(ts_of("2026-11-14"))
    chain = FakeBase(NOW, fail=lambda t, f, args, blk: t == TOK and f == "totalSupply" and blk <= cut)
    rows, why = a.supply_history(config.PROJECT_BY_NAME[NAME], have, reader=a.AeroV3(rpc=chain.rpc, state=STATE),
                                 state=STATE)
    assert {str(r["date"])[:10] for r in rows} == {"2026-11-16", "2026-11-15"} and "2026-11-14" in why
