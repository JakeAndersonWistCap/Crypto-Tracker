"""
fetch/origintrail.py — R24: OriginTrail (TRAC) from its own contracts. Keyless public RPCs only (no key, no login).

WHAT TRAC IS ON CHAIN (read 2026-11-26; docs/r24_trac_section.md):
  * ONE supply. The Ethereum ERC-20 (TracToken, Sourcify full match) has totalSupply 500,000,000 and mintingFinished() =
    true: nothing more can ever be minted. Base TRAC is an OptimismMintableERC20 (remoteToken() = the Ethereum token,
    bridge() = the L2 standard bridge), Gnosis TRAC is an Omnibridge token, NeuroWeb TRAC is a Snowbridge asset: all
    LOCK-AND-MINT. The Ethereum escrow (L1StandardBridge, Omnibridge mediator, the Snowbridge Agent) holds at least the
    supply of each copy, so the copies are claims on tokens already inside the 500,000,000 — never added to it.
  * DKG V10 on Base and Gnosis: the Hub (config) resolves every contract. Staked TRAC sits in the V8 StakingStorage and
    the V10 ConvictionStakingStorage (positions are ERC-721 NFTs with lock tiers). Publishers' fees reach the stakers'
    reward pool as EpochStorageV8 TokensAddedToEpochRange(shardId, startEpoch, endEpoch, tokenAmount, remainder); the
    rewards that reach a staker, net of the node operator fee and the protocol treasury fee, are StakingV10
    RewardsClaimed(tokenId, amount) (compounded into the position). The treasury fee is skimmed only when
    ParametersStorage.protocolTreasury() is set; it reads the zero address (fee dormant).

STORED (tier 2, project OriginTrail), per UTC day:
    locked_tokens                  TRAC held by the V8 StakingStorage + V10 ConvictionStakingStorage, Base + Gnosis (a day
                                   is stored only when every leg was read; NeuroWeb's V8 stake is NOT read — said in the
                                   source text, never counted as 0)
    trac_publish_fees_tokens       publishers' TRAC credited to the staker reward pool (sum of tokenAmount)
    fees_usd / revenue_usd         that x the day's price (DefiLlama's relay of CoinGecko's price)
    trac_staker_rewards_tokens     rewards that reached stakers (RewardsClaimed)
    trac_staker_rewards_usd        that x the day's price — HOLDER VALUE paid to stakers out of fees (config.HOLDER_VALUE)
The same reads build fixtures/real_origintrail.json (`python -m fetch.origintrail --dump`), as of the build's common
as-of date.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

import config

from .base import point, tidy, today

log = logging.getLogger("token_metrics.fetch.origintrail")

SOURCE = "origintrail_chain"
TIER = 2
UA = "token-metrics-check/1.0"
RETRIES = 6


# ============================================================================================== selectors (keccak)
def _keccak(text: str) -> bytes:
    from web3 import Web3                                   # noqa: PLC0415 — the repo's dependency
    return bytes(Web3.keccak(text=text))


def sel(sig: str) -> str:
    return "0x" + _keccak(sig)[:4].hex()


def topic(sig: str) -> str:
    return "0x" + _keccak(sig).hex()


# ============================================================================================== RPC
class Rpc:
    """JSON-RPC over HTTPS with retries. `urls` are tried in order; no key is ever added or printed."""

    def __init__(self, urls, pace: float = 0.0, post=None):
        self.urls = [urls] if isinstance(urls, str) else list(urls)
        self.pace = pace
        self._post = post or self._http_post

    @staticmethod
    def _http_post(url: str, body: dict):
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json", "User-Agent": UA})
        return json.load(urllib.request.urlopen(req, timeout=60))

    def call(self, method: str, params: list):
        last = None
        for i in range(RETRIES):
            for url in self.urls:
                try:
                    r = self._post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                    if "error" in r:
                        msg = str(r["error"])
                        if "revert" in msg.lower() or "execution" in msg.lower():
                            raise RuntimeError("reverted: " + msg[:120])
                        raise ConnectionError(msg[:120])
                    if self.pace:
                        time.sleep(self.pace)
                    return r["result"]
                except RuntimeError:
                    raise
                except Exception as e:  # noqa: BLE001 — retried, then raised
                    last = e
            time.sleep(1.5 * (i + 1))
        raise ConnectionError(f"{method}: {str(last)[:160]}")

    def head(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def ts(self, block: int) -> int:
        return int(self.call("eth_getBlockByNumber", [hex(block), False])["timestamp"], 16)

    def eth_call(self, to: str, sig: str, args=(), block=None) -> str:
        data = sel(sig)
        for a in args:
            data += (a.lower().replace("0x", "").rjust(64, "0") if isinstance(a, str) else hex(a)[2:].rjust(64, "0"))
        return self.call("eth_call", [{"to": to, "data": data}, hex(block) if block is not None else "latest"])

    def balance_of(self, token: str, holder: str, block=None) -> float:
        return int(self.eth_call(token, "balanceOf(address)", [holder], block), 16) / 1e18

    def logs(self, address: str, topics: list, lo: int, hi: int, step: int) -> list:
        out, a = [], lo
        while a <= hi:
            b = min(a + step - 1, hi)
            out += self.call("eth_getLogs", [{"address": address, "topics": topics,
                                              "fromBlock": hex(a), "toBlock": hex(b)}])
            a = b + 1
        return out

    def hub(self, hub: str, name: str) -> str:
        """The Hub's address for a contract name: getContractAddress, else getAssetStorageAddress (the storages)."""
        data = (hex(32)[2:].rjust(64, "0") + hex(len(name))[2:].rjust(64, "0")
                + name.encode().hex().ljust(((len(name) + 31) // 32) * 64, "0"))
        for fn in ("getContractAddress(string)", "getAssetStorageAddress(string)"):
            try:
                r = self.call("eth_call", [{"to": hub, "data": sel(fn) + data}, "latest"])
                a = "0x" + r[-40:]
                if int(a, 16):
                    return a
            except RuntimeError:
                continue
        raise LookupError(f"Hub {hub[:10]}… has no {name}")


class Clock:
    """Block <-> UTC time for a chain whose block time changes (Base moved from 2 s to 0.2 s blocks in 2026): timestamps
    are read at anchor blocks every `every` blocks and interpolated between them (error well under a minute)."""

    def __init__(self, rpc: Rpc, lo: int, hi: int, every: int):
        self.rpc, self.anchors = rpc, {}
        for b in list(range(lo, hi, every)) + [hi]:
            self.anchors[b] = rpc.ts(b)
        self.keys = sorted(self.anchors)

    def at(self, block: int) -> int:
        ks = self.keys
        lo = max((k for k in ks if k <= block), default=ks[0])
        hi = min((k for k in ks if k >= block), default=ks[-1])
        if lo == hi:
            return self.anchors[lo]
        return int(self.anchors[lo] + (self.anchors[hi] - self.anchors[lo]) * (block - lo) / (hi - lo))

    def day(self, block: int) -> str:
        return datetime.fromtimestamp(self.at(block), tz=timezone.utc).strftime("%Y-%m-%d")

    def block_at(self, ts: int) -> int:
        """The first block at or after `ts` (UTC seconds): the anchors bracket it, then bisection on the real chain."""
        ks = self.keys
        lo = max((k for k in ks if self.anchors[k] < ts), default=ks[0])
        hi = min((k for k in ks if self.anchors[k] >= ts), default=ks[-1])
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.rpc.ts(mid) < ts:
                lo = mid
            else:
                hi = mid
        return hi


# ============================================================================================== price
def llama_prices(coin: str, start: str, end: str) -> dict:
    """{YYYY-MM-DD: usd} — DefiLlama's relay of CoinGecko's daily price (the 00:00 UTC stamp), as fetch.price_history."""
    t0 = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days + 1
    url = f"https://coins.llama.fi/chart/coingecko:{coin}?start={t0}&span={min(days, 499)}&period=1d&searchWidth=600"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    body = json.load(urllib.request.urlopen(req, timeout=60))
    out = {}
    for p in body["coins"][f"coingecko:{coin}"]["prices"]:
        if abs(p["timestamp"] % 86400) <= 600 or abs(p["timestamp"] % 86400 - 86400) <= 600:
            d = datetime.fromtimestamp(round(p["timestamp"] / 86400) * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
            out[d] = float(p["price"])
    return out


# ============================================================================================== the reads
def spec_of(project: dict) -> dict | None:
    return project.get("origintrail_chain")


def resolve(spec: dict, rpcs: dict) -> dict:
    """{chain: {contract name: address}} through each chain's Hub — nothing is hard-coded but the Hub."""
    out = {}
    for ch, c in spec["chains"].items():
        out[ch] = {n: rpcs[ch].hub(c["hub"], n) for n in c["contracts"]}
    return out


def fee_events(spec: dict, rpcs: dict, addrs: dict, ch: str, lo: int, hi: int, clock: Clock) -> dict:
    """{day: TRAC credited to the staker reward pool} from EpochStorageV8 TokensAddedToEpochRange."""
    c = spec["chains"][ch]
    t = topic("TokensAddedToEpochRange(uint256,uint256,uint256,uint96,uint96)")
    ev = rpcs[ch].logs(addrs[ch][c["fee_storage"]], [t], lo, hi, c["log_step"])
    days: dict = {}
    for x in ev:
        d = x["data"][2:]
        amount = int(d[128:192], 16) / 1e18                  # tokenAmount (third data word)
        day = clock.day(int(x["blockNumber"], 16))
        days[day] = days.get(day, 0.0) + amount
    return days


def reward_events(spec: dict, rpcs: dict, addrs: dict, ch: str, lo: int, hi: int, clock: Clock) -> dict:
    """{day: TRAC that reached stakers} from StakingV10 RewardsClaimed(tokenId, amount)."""
    c = spec["chains"][ch]
    t = topic("RewardsClaimed(uint256,uint96)")
    days: dict = {}
    for x in rpcs[ch].logs(addrs[ch][c["staking"]], [t], lo, hi, c["log_step"]):
        day = clock.day(int(x["blockNumber"], 16))
        days[day] = days.get(day, 0.0) + int(x["data"], 16) / 1e18
    return days


def staked_at(spec: dict, rpcs: dict, addrs: dict, blocks: dict) -> dict:
    """{chain: {contract: TRAC}} at each chain's block (None = latest)."""
    out = {}
    for ch, c in spec["chains"].items():
        out[ch] = {n: rpcs[ch].balance_of(c["token"], addrs[ch][n], blocks.get(ch)) for n in c["stake_contracts"]}
    return out


def treasury(spec: dict, rpcs: dict, addrs: dict) -> dict:
    out = {}
    for ch, c in spec["chains"].items():
        ps = addrs[ch][c["parameters"]]
        out[ch] = {"recipient": "0x" + rpcs[ch].eth_call(ps, "protocolTreasury()")[-40:],
                   "fee_bps": int(rpcs[ch].eth_call(ps, "protocolTreasuryFee()"), 16)}
    return out


def supply_model(spec: dict, rpcs: dict) -> dict:
    """The lock-and-mint check: each copy's totalSupply against the Ethereum escrow that backs it."""
    eth = rpcs["ethereum"]
    tok = spec["ethereum"]["token"]
    out = {"ethereum_total_supply": int(eth.eth_call(tok, "totalSupply()"), 16) / 1e18,
           "minting_finished": bool(int(eth.eth_call(tok, "mintingFinished()"), 16)), "copies": {}}
    for ch, c in spec["copies"].items():
        sup = int(rpcs[ch].eth_call(c["token"], "totalSupply()"), 16) / 1e18
        esc = eth.balance_of(tok, c["escrow"])
        out["copies"][ch] = {"supply": sup, "escrow": esc, "escrow_minus_supply": esc - sup, "escrow_is": c["escrow_is"]}
    return out


# ============================================================================================== the adapter
class OriginTrailChain:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, rpcs=None, prices=None, window=None, **_ignored):
        self._rpcs, self._prices, self._window = rpcs, prices, window

    def _rpc_map(self, spec: dict) -> dict:
        if self._rpcs:
            return self._rpcs
        m = {"ethereum": Rpc(spec["ethereum"]["rpc"])}
        for ch, c in spec["chains"].items():
            m[ch] = Rpc(c["rpc"])
        for ch, c in spec["copies"].items():
            m.setdefault(ch, Rpc(c["rpc"]))
        return m

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = spec_of(p)
            if spec:
                try:
                    self._project(p["name"], spec, int(self._window or min(window_days or 14, 30)), out)
                except Exception as e:  # noqa: BLE001 — said on the Run Log, never kills the run
                    out.fail(SOURCE, p["name"], f"OriginTrail chain read: NOTHING STORED — {str(e)[:300]}", TIER)

    def _project(self, name: str, spec: dict, window: int, out) -> None:
        rpcs = self._rpc_map(spec)
        addrs = resolve(spec, rpcs)
        day0 = today()
        # 1. THE STAKE: one read of four balances; stored only when every leg answered
        stk = staked_at(spec, rpcs, addrs, {})
        total = sum(v for c in stk.values() for v in c.values())
        legs = "; ".join(f"{ch} {n} {v:,.2f}" for ch, c in stk.items() for n, v in c.items())
        out.add(point(name, "locked_tokens", total, f"{SOURCE}:balanceOf[{legs}]:PARTIAL[NeuroWeb V8 stake not read]",
                      TIER, day0), SOURCE, name, f"locked_tokens = {total:,.2f} TRAC ({legs}); NeuroWeb V8 stake not read", TIER)
        # 2. THE FEES AND THE STAKERS' REWARDS: both chains or nothing (a day with one chain missing is not a day read)
        try:
            self._flows(name, spec, rpcs, addrs, window, out)
        except Exception as e:  # noqa: BLE001 — the stake above is kept
            out.fail(SOURCE, name, f"OriginTrail fee / reward logs: NOT STORED — {str(e)[:300]}", TIER)
        # 3. THE FACTS BESIDE THEM, on the Run Log: the treasury recipient and the lock-and-mint check
        try:
            tr = treasury(spec, rpcs, addrs)
            sm = supply_model(spec, rpcs)
            out.skipped(SOURCE, name, "protocol treasury " + "; ".join(
                f"{ch}: {v['recipient']} fee {v['fee_bps']} bps" for ch, v in tr.items()) + " | supply " + json.dumps(sm), TIER)
        except Exception as e:  # noqa: BLE001
            out.skipped(SOURCE, name, f"treasury / supply check not read: {str(e)[:200]}", TIER)

    def _flows(self, name: str, spec: dict, rpcs: dict, addrs: dict, window: int, out) -> None:
        day0 = today()
        end = str((day0 - pd.Timedelta(days=1)).date())
        start = str((day0 - pd.Timedelta(days=window)).date())
        prices = self._prices if self._prices is not None else llama_prices(spec["coingecko_id"], start, end)
        rows = {"fees": {}, "rewards": {}}
        first = None
        for ch, c in spec["chains"].items():
            r = rpcs[ch]
            head = r.head()
            lo = max(head - int(c["window_blocks"]), c.get("first_readable_block", 1))
            clock = Clock(r, lo, head, c["anchor_every"])
            first = max(first or clock.day(lo), clock.day(lo))
            for key, fn in (("fees", fee_events), ("rewards", reward_events)):
                for d, v in fn(spec, rpcs, addrs, ch, lo, head, clock).items():
                    rows[key][d] = rows[key].get(d, 0.0) + v
        cal = pd.date_range(max(first, start), end)         # the days every chain's window covers
        for key, metric, what in (("fees", "trac_publish_fees_tokens", "EpochStorageV8.TokensAddedToEpochRange"),
                                  ("rewards", "trac_staker_rewards_tokens", "StakingV10.RewardsClaimed")):
            vals = [(d, rows[key].get(str(d.date()), 0.0)) for d in cal]
            if not vals:
                continue
            out.add(tidy(vals, name, metric, f"{SOURCE}:{what}[base,gnosis]", TIER), SOURCE, name,
                    f"{metric}: {len(vals)} days to {end}", TIER)
            for um in (("fees_usd", "revenue_usd") if key == "fees" else ("trac_staker_rewards_usd",)):
                uv = [(d, v * prices[str(d.date())]) for d, v in vals if str(d.date()) in prices]
                if uv:
                    out.add(tidy(uv, name, um, f"{SOURCE}:{metric}*price[defillama_coins coingecko:{spec['coingecko_id']}]",
                                 TIER), SOURCE, name, f"{um}: {len(uv)} days", TIER)


# ============================================================================================== the dump
def _cg(path: str, tries: int = 6):
    """CoinGecko's keyless API (api.coingecko.com, no key, no login), with the long waits its rate limit needs."""
    url = "https://api.coingecko.com/api/v3/" + path
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60))
        except Exception as e:  # noqa: BLE001
            log.info("coingecko %s: %s", path.split("?")[0], str(e)[:80])
            time.sleep(25 + 15 * i)
    return None


def _eth_block_at(rpc: Rpc, ts: int) -> int:
    """Ethereum: 12 s blocks — an estimate from the head, then exact reads to the first block at or after ts."""
    head = rpc.head()
    t_head = rpc.ts(head)
    b = max(1, head - int((t_head - ts) / 12))
    for _ in range(30):
        t = rpc.ts(b)
        if t < ts:
            b += max(1, int((ts - t) / 12))
        elif rpc.ts(b - 1) >= ts:
            b -= max(1, int((t - ts) / 12))
        else:
            return b
    return b


def build_dump(asof: str, path: str, base_first_day: str = "2026-09-08") -> dict:
    """fixtures/real_origintrail.json from reads made NOW: every row is dated on or before `asof` (the 15 dumps' common
    as-of — a later row would move every frozen project's cells), read from the chain at that day's close, or from the
    event logs, or from DefiLlama's price relay. Values for the day of the read (today) go in meta.live, not in rows."""
    p = config.PROJECT_BY_NAME["OriginTrail"]
    spec = p["origintrail_chain"]
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    rpcs = {"ethereum": Rpc(spec["ethereum"]["rpc"])}
    for ch, c in spec["chains"].items():
        rpcs[ch] = Rpc(c["rpc"])
    for ch, c in spec["copies"].items():
        rpcs.setdefault(ch, Rpc(c["rpc"]))
    addrs = resolve(spec, rpcs)
    end = int(datetime.strptime(asof, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 86399
    rows: list = []

    def add(metric, day, value, source, tier=TIER):
        rows.append({"date": str(day), "metric": metric, "value": float(value), "source": source, "tier": tier,
                     "fetched_at": stamp})

    # ---- clocks, event windows
    clocks, bounds = {}, {}
    for ch, c in spec["chains"].items():
        r = rpcs[ch]
        head = r.head()
        first = c["first_readable_block"] if ch == "base" else 46_800_000
        clk = Clock(r, first, head, c["anchor_every"] * 2)
        clocks[ch] = clk
        bounds[ch] = (first, clk.block_at(end))
    # ---- fees and rewards
    import os
    from pathlib import Path
    cache = Path(os.environ.get("TOKEN_METRICS_OT_CACHE", "")) if os.environ.get("TOKEN_METRICS_OT_CACHE") else None
    if cache and cache.exists():                           # a re-run after a failed phase reuses the event reads
        c = json.loads(cache.read_text())
        fees, rewards, start_day = c["fees"], c["rewards"], c["start_day"]
    else:
        fees = {ch: fee_events(spec, rpcs, addrs, ch, *bounds[ch], clocks[ch]) for ch in spec["chains"]}
        rewards = {ch: reward_events(spec, rpcs, addrs, ch, *bounds[ch], clocks[ch]) for ch in spec["chains"]}
        start_day = {ch: clocks[ch].day(bounds[ch][0]) for ch in spec["chains"]}
        if cache:
            cache.write_text(json.dumps({"fees": fees, "rewards": rewards, "start_day": start_day}))
    prices = llama_prices(spec["coingecko_id"], "2025-10-14", asof)
    day0 = min(min(d) for d in list(fees.values()) + list(rewards.values()) if d)
    cal = pd.date_range(day0, asof)
    for key, metric, ev in (("fees", "trac_publish_fees_tokens", fees), ("rewards", "trac_staker_rewards_tokens", rewards)):
        what = "EpochStorageV8.TokensAddedToEpochRange" if key == "fees" else "StakingV10.RewardsClaimed"
        for d in cal:
            ds = str(d.date())
            part = [ch for ch in ("gnosis", "base") if start_day[ch] > ds]
            if "gnosis" in part:
                continue                                   # before V10's first event on Gnosis: nothing was read
            v = sum(ev[ch].get(ds, 0.0) for ch in spec["chains"])
            src = f"{SOURCE}:{what}[base,gnosis]" + (f":PARTIAL[base not read before {start_day['base']}]" if part else "")
            add(metric, ds, v, src)
            if ds in prices:
                if key == "fees":
                    add("fees_usd", ds, v * prices[ds], f"{SOURCE}:{metric}*price[defillama_coins coingecko:origintrail]")
                    add("revenue_usd", ds, v * prices[ds], f"{SOURCE}:{metric}*price[defillama_coins coingecko:origintrail]")
                else:
                    add("trac_staker_rewards_usd", ds, v * prices[ds],
                        f"{SOURCE}:{metric}*price[defillama_coins coingecko:origintrail]")
    # ---- staked, at each day's close, only where every leg is readable
    for d in pd.date_range(base_first_day, asof):
        ds = str(d.date())
        t = int(datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 86399
        try:
            blocks = {ch: clocks[ch].block_at(t) for ch in spec["chains"]}
            stk = staked_at(spec, rpcs, addrs, blocks)
        except ConnectionError as e:                       # a day any leg could not be read is not stored (a gap, never 0)
            log.info("locked_tokens %s not read: %s", ds, str(e)[:100])
            continue
        total = sum(v for c in stk.values() for v in c.values())
        legs = "; ".join(f"{ch} {n} {v:,.2f}" for ch, c in stk.items() for n, v in c.items())
        add("locked_tokens", ds, total, f"{SOURCE}:balanceOf[{legs}]:PARTIAL[NeuroWeb V8 stake not read]")
    # ---- supply: the Ethereum token at each day's close (fixed: constant, but each day is its own read)
    eth = rpcs["ethereum"]
    tok = spec["ethereum"]["token"]
    for d in pd.date_range(pd.Timestamp(asof) - pd.Timedelta(days=29), asof):
        ds = str(d.date())
        t = int(datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 86399
        b = _eth_block_at(eth, t)
        add("total_supply", ds, int(eth.eth_call(tok, "totalSupply()", block=b), 16) / 1e18, "chain:ethereum:token")
    add("max_supply", asof, int(eth.eth_call(tok, "TOTAL_NUM_TOKENS()"), 16) / 1e18,
        "chain:ethereum:TracToken.TOTAL_NUM_TOKENS()", 1)
    for ds, px in prices.items():
        if ds <= asof:
            add("price_usd", ds, px, "defillama_coins:coingecko:origintrail (CoinGecko's price, relayed)", 1)
    # ---- market data from CoinGecko where it answers
    chart = _cg("coins/origintrail/market_chart?vs_currency=usd&days=365&interval=daily")
    got_cg = False
    if chart:
        got_cg = True

        def day_of(t):
            return datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        mc = {day_of(t): v for t, v in chart["market_caps"]}
        vol = {day_of(t): v for t, v in chart["total_volumes"]}
        px = {day_of(t): v for t, v in chart["prices"]}
        for ds in sorted(mc):
            if ds <= asof and px.get(ds):
                add("market_cap_usd", ds, mc[ds], "coingecko", 1)
                add("circulating_supply_implied", ds, mc[ds] / px[ds], "coingecko:mcap/price", 1)
                # CoinGecko serves its reported circulating figure for today only; a past day's is its market cap
                # over its price, labelled as that
                add("circulating_supply", ds, mc[ds] / px[ds],
                    "coingecko:mcap/price (implied: the reported figure is not served for past days)", 1)
        for ds in sorted(vol):
            if ds <= asof:
                add("volume_usd", ds, vol[ds], "coingecko", 1)
    live = {"read_at": stamp}
    cur = _cg("coins/markets?vs_currency=usd&ids=origintrail")
    if cur:
        c0 = cur[0]
        live["coingecko"] = {k: c0.get(k) for k in ("circulating_supply", "total_supply", "max_supply",
                                                      "fully_diluted_valuation", "market_cap", "current_price",
                                                      "last_updated")}
    live["staked"] = staked_at(spec, rpcs, addrs, {})
    live["treasury"] = treasury(spec, rpcs, addrs)
    live["supply_model"] = supply_model(spec, rpcs)
    live["hub_addresses"] = addrs
    payload = {"meta": {"project": "OriginTrail", "asof": asof, "dumped_at": now.isoformat(), "git_commit": "",
                        "db": "none: built from public-chain reads by fetch/origintrail.py --dump",
                        "days": 400, "metrics_filter": [], "coingecko_market_chart": got_cg, "live": live,
                        "counts": {"metrics": len(rows), "review_queue": 0, "manual_overrides": 0, "run_log": 0,
                                   "gap_report": 0, "manual_references": 0}},
               "metrics": rows, "review_queue": [], "manual_overrides": [], "run_log": [], "gap_report": [],
               "manual_references": []}
    from pathlib import Path
    Path(path).write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="OriginTrail (TRAC): write fixtures/real_origintrail.json from public-chain reads")
    ap.add_argument("--dump", metavar="PATH", default="fixtures/real_origintrail.json")
    ap.add_argument("--asof", default="2026-10-13", help="the 15 dumps' common as-of date")
    a = ap.parse_args()
    out = build_dump(a.asof, a.dump)
    print(a.dump, out["meta"]["counts"])
