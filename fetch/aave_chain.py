"""
fetch/aave_chain.py — tier 2: AAVE'S TREASURY, HELD BUYBACK, STAKE, EMISSION RATES AND BUYS, FROM ETHEREUM (Round 22,
Jake 2026-11-25). The spec is config.AAVE_CHAIN (every address from bgd-labs/aave-address-book, each read on chain
2026-11-25: code present, the token it holds, its role); the evidence is in docs/r22_aave.md. Keyless public RPC, once a
day, User-Agent token-metrics-check.

AT ONE BLOCK (AAVE = 0x7Fc6…DaE9, aAAVE = Aave V3 Core's aEthAAVE 0xA700…F6C9, 1:1 with AAVE):
  treasury_holding_tokens      the DAO's AAVE: Ecosystem Reserve + Collector (AAVE + aAAVE) + V4 TreasurySpoke + the
                               AAVE_SWAPPER + TokenLogic's execution safe — in circulating, OFF the float (Jake R22)
  aave_ecosystem_reserve_tokens the Ecosystem Reserve alone (it also pays the stkAAVE rewards: REWARDS_VAULT())
  aave_held_buyback_tokens     the Aave Finance Committee Safe's AAVE + aAAVE: where the bought AAVE is held (AIP 449:
                               "AAVE acquired via BuyBacks to be held on the AFC SAFE") — off the float as held buybacks
  burn_address_balance         AAVE at 0x…dEaD (burned: out of circulating and of the total)
  locked_tokens                AAVE staked: AAVE.balanceOf(stkAAVE) + the AAVE inside the BPT staked in stkABPT and in
                               stkAAVE/wstETH BPTv2 (the pool's AAVE x the stake token's share of the BPT supply)
  aave_emission_rate_tokens    Σ emissionPerSecond x 86,400 of every AAVE-paying stake token (assets(self)) — the rate
                               config's issuance_schedule must match; a mismatch is logged WARN (the schedule is the
                               series, the rate its check)
THE BUYS (actual_buyback_tokens, per UTC day): every AAVE / aAAVE Transfer INTO the DAO's wallets (Collector, AFC Safe,
Ecosystem Reserve, TreasurySpoke) FROM a swap venue (config.AAVE_CHAIN["buy_sources"]: CoW Protocol's GPv2Settlement,
which filled the Swap Steward's TWAP orders into the Collector until 19 Apr 2026, and the AAVE_SWAPPER). Read from
`scan_from_block` on the first run, then the last `rescan_days`; a day is stored only when every block of it was
scanned; a scanned day with no buy is a measured 0. A transfer into those wallets from anyone else is not a buy (logged).

A line that does not read stores nothing (never 0) and is logged FAILED with the reason.

    python -m fetch.aave_chain          the live figures, nothing stored
"""
from __future__ import annotations

import logging
import time

import pandas as pd

import config
from .base import LogEntry, point, tidy, today

log = logging.getLogger("token_metrics.fetch.aave_chain")

SOURCE = "aave_chain"
TIER = 2
PROJECT = "Aave"
WEI = 10 ** 18
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
# function selectors (keccak of the signature; tests/test_r22_aave.py recomputes each)
SEL = {"balanceOf(address)": "70a08231", "assets(address)": "f11b8188", "totalSupply()": "18160ddd",
       "getPoolTokens(bytes32)": "f94d4668", "getPoolId()": "38fff2d0", "bPool()": "b64ef17b"}


def _pad(a: str) -> str:
    return a.lower()[2:].rjust(64, "0")


def _addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def buys_by_day(logs: list[dict], sinks: dict, sources: dict) -> tuple[dict, list]:
    """({day: AAVE bought}, [other inflows]) from Transfer logs (each {ts, from, to, value, tx}) into `sinks`."""
    sinks_l = {a.lower() for a in sinks.values()}
    src_l = {a.lower() for a in sources.values()}
    days, other = {}, []
    for e in logs:
        if e["to"] not in sinks_l or e["value"] <= 0:
            continue
        day = str(pd.Timestamp(e["ts"], unit="s").date())
        if e["from"] in src_l:
            days[day] = days.get(day, 0.0) + e["value"] / WEI
        else:
            other.append({"day": day, "from": e["from"], "to": e["to"], "tokens": e["value"] / WEI, "tx": e.get("tx", "")})
    return days, other


def scanned_days(lo_ts: int, hi_ts: int) -> list[str]:
    """The UTC days wholly inside [lo_ts, hi_ts] (the scan's first block's time .. its last block's time)."""
    lo = pd.Timestamp(lo_ts, unit="s")
    first = lo.normalize() if lo == lo.normalize() else lo.normalize() + pd.Timedelta(days=1)
    last = pd.Timestamp(hi_ts, unit="s").normalize() - pd.Timedelta(days=1)
    return [str(d.date()) for d in pd.date_range(first, last)] if last >= first else []


def pool_share(pool_aave: float, bpt_held: float, bpt_supply: float) -> float:
    return pool_aave * bpt_held / bpt_supply if bpt_supply else 0.0


class AaveChain:
    """Aave's stocks, stake, emission rates and buys, once a day (R22)."""
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, session=None, daily=None, last_dates=None, clock=time.time, **_ignored):
        self.session = session
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self.last_dates = last_dates or {}
        self.clock = clock
        self.spec = config.AAVE_CHAIN

    def _sess(self):
        if self.session is None:
            import requests
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": config.R22_USER_AGENT})
        return self.session

    def rpc(self, url: str, method: str, params: list):
        from .base import CONNECT_TIMEOUT_S, READ_TIMEOUT_S, mask_url
        r = self._sess().post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                              timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} from {mask_url(url)}")
        out = r.json()
        if not isinstance(out, dict) or "result" not in out or out["result"] is None:
            raise RuntimeError(f"{method}: {str((out or {}).get('error') if isinstance(out, dict) else out)[:160]}")
        return out["result"]

    def first(self, urls, fn):
        from .base import mask_url
        errs = []
        for u in urls:
            try:
                return fn(u)
            except Exception as e:  # noqa: BLE001 — one endpoint's failure is said, the next is tried
                errs.append(f"{mask_url(u).split('/')[2]}: {str(e)[:120]}")
        raise RuntimeError("; ".join(errs) or "no endpoint declared")

    def call(self, url: str, to: str, sig: str, arg: str = "", tag: str = "latest") -> str:
        return self.rpc(url, "eth_call", [{"to": to, "data": "0x" + SEL[sig] + arg}, tag])

    # ------------------------------------------------------------------------------------------ the stocks
    def stocks(self, url: str) -> dict:
        s = self.spec
        blk = self.rpc(url, "eth_getBlockByNumber", ["finalized", False])
        tag = blk["number"]
        bal = lambda tok, a: int(self.call(url, tok, "balanceOf(address)", _pad(a), tag)[:66], 16) / WEI  # noqa: E731
        w = s["wallets"]
        both = {k: bal(s["aave"], w[k]) + bal(s["aaave"], w[k]) for k in w}
        got = {"block": int(tag, 16), "ts": int(blk["timestamp"], 16), "wallets": both}
        got["treasury"] = sum(both[k] for k in s["treasury_wallets"])
        got["reserve"] = both["ecosystem_reserve"]
        got["held"] = sum(both[k] for k in s["held_buyback_wallets"])
        got["dead"] = bal(s["aave"], s["dead"])
        st = s["stake"]
        got["stk_aave"] = bal(s["aave"], st["stkAAVE"])
        bpt = {}
        for key, (stk, pool) in st["bpt"].items():
            if key in st.get("bpt_v1", ()):               # a Balancer v1 smart pool: the AAVE sits in its bPool()
                core = "0x" + self.call(url, pool, "bPool()", "", tag)[-40:]
                pool_aave = bal(s["aave"], core)
            else:                                           # Balancer v2: the vault holds the pool's tokens
                pid = self.call(url, pool, "getPoolId()", "", tag)[:66]
                r = self.call(url, s["balancer_vault"], "getPoolTokens(bytes32)", pid[2:], tag)
                words = [r[2 + 64 * i:2 + 64 * (i + 1)] for i in range((len(r) - 2) // 64)]
                n = int(words[3], 16)                       # (tokens[], balances[], lastChangeBlock): tokens[] length
                toks = ["0x" + x[-40:] for x in words[4:4 + n]]
                bals = [int(x, 16) for x in words[4 + n + 1:4 + n + 1 + n]]
                pool_aave = next((b for t, b in zip(toks, bals) if t.lower() == s["aave"].lower()), 0) / WEI
            held = int(self.call(url, pool, "balanceOf(address)", _pad(stk), tag)[:66], 16) / WEI
            supply = int(self.call(url, pool, "totalSupply()", "", tag)[:66], 16) / WEI
            bpt[key] = {"pool_aave": pool_aave, "bpt_staked": held, "bpt_supply": supply,
                        "aave": pool_share(pool_aave, held, supply)}
        got["bpt"] = bpt
        got["locked"] = got["stk_aave"] + sum(b["aave"] for b in bpt.values())
        rate = 0.0
        for key, a in st["reward_assets"].items():
            r = self.call(url, a, "assets(address)", _pad(a), tag)
            rate += int(r[2:66], 16) * 86_400 / WEI
        got["rate"] = rate
        return got

    # ------------------------------------------------------------------------------------------ the buys
    def logs(self, url: str, token: str, sinks: list[str], lo: int, hi: int, step: int) -> list[dict]:
        out, stamps = [], {}
        tops = ["0x" + _pad(a) for a in sinks]
        while lo <= hi:
            top = min(lo + step - 1, hi)
            for e in self.rpc(url, "eth_getLogs", [{"address": token, "fromBlock": hex(lo), "toBlock": hex(top),
                                                    "topics": [TRANSFER, None, tops]}]):
                bn = int(e["blockNumber"], 16)
                ts = int(str(e.get("blockTimestamp") or "0x0"), 16)
                if not ts:
                    if bn not in stamps:
                        stamps[bn] = int(self.rpc(url, "eth_getBlockByNumber", [hex(bn), False])["timestamp"], 16)
                    ts = stamps[bn]
                out.append({"block": bn, "ts": ts, "from": _addr(e["topics"][1]), "to": _addr(e["topics"][2]),
                            "value": int(e["data"], 16) if e["data"] not in ("0x", "") else 0,
                            "tx": e.get("transactionHash", "")})
            lo = top + 1
        return out

    def buys(self, got: dict) -> tuple[dict, list, list, int, int]:
        s = self.spec
        last = self.last_dates.get((PROJECT, "actual_buyback_tokens"))
        if last is not None and not pd.isna(last):
            lo_day = pd.Timestamp(last).normalize() - pd.Timedelta(days=int(s["rescan_days"]))
            lo = max(int(s["scan_from_block"]),
                     got["block"] - int((pd.Timestamp(got["ts"], unit="s") - lo_day).total_seconds() // 12) - 7200)
        else:
            lo = int(s["scan_from_block"])
        hi = got["block"]
        url = s["log_rpc"][0]

        def read(u):
            logs = []
            for tok in (s["aave"], s["aaave"]):
                logs += self.logs(u, tok, list(s["buy_sinks"].values()), lo, hi, int(s["log_step"]))
            lo_ts = int(self.rpc(u, "eth_getBlockByNumber", [hex(lo), False])["timestamp"], 16)
            return logs, lo_ts
        logs, lo_ts = self.first(s["log_rpc"], read) if len(s["log_rpc"]) > 1 else read(url)
        days, other = buys_by_day(logs, s["buy_sinks"], s["buy_sources"])
        covered = scanned_days(lo_ts, got["ts"])
        return days, other, covered, lo, hi

    # ------------------------------------------------------------------------------------------ the tier
    def run(self, projects: list[dict], window_days, out):
        if not any(p["name"] == PROJECT for p in projects):
            return
        day = str(today().date())
        key = f"{SOURCE}:{PROJECT}"
        if not self.daily.due(key, day):
            out.mark_current(SOURCE, PROJECT, "treasury_holding_tokens", "the Ethereum reads ran today already", TIER)
            return
        s = self.spec
        failed = False
        try:
            got = self.first(s["rpc"], self.stocks)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, PROJECT, f"stocks: NOT STORED — {str(e)[:300]}", TIER)
            return
        at = f"block {got['block']}"
        w = got["wallets"]
        for metric, v, src in (
                ("treasury_holding_tokens", got["treasury"],
                 "aave_chain:dao_wallets[" + " + ".join(f"{k} {w[k]:,.2f}" for k in s["treasury_wallets"]) + f"; {at}]"),
                ("aave_ecosystem_reserve_tokens", got["reserve"], f"aave_chain:ecosystem_reserve.balanceOf[{at}]"),
                ("aave_held_buyback_tokens", got["held"],
                 "aave_chain:afc_safe[AAVE + aAAVE " + " + ".join(f"{k} {w[k]:,.2f}" for k in s["held_buyback_wallets"])
                 + f"; {at}]"),
                ("burn_address_balance", got["dead"], f"aave_chain:dead.balanceOf[{at}]"),
                ("locked_tokens", got["locked"],
                 f"aave_chain:stake[stkAAVE {got['stk_aave']:,.2f} + " + " + ".join(
                     f"{k} {b['aave']:,.2f} ({b['bpt_staked']:,.2f} of {b['bpt_supply']:,.2f} BPT x pool "
                     f"{b['pool_aave']:,.2f} AAVE)" for k, b in got["bpt"].items()) + f"; {at}]"),
                ("aave_emission_rate_tokens", got["rate"], f"aave_chain:assets(self).emissionPerSecond x 86400[{at}]")):
            out.add(point(PROJECT, metric, v, src, TIER), SOURCE, PROJECT, f"{metric}: {v:,.6f}", TIER)
        sched = config.aave_schedule_rate(day)
        if sched is None or abs(sched - got["rate"]) > 1e-6:
            out.log.append(LogEntry(SOURCE, PROJECT, 0, "warn", f"emission rate on chain {got['rate']:,.6f} AAVE/day "
                                    f"vs the declared schedule {sched} — the schedule needs a new step", TIER))
        try:
            days, other, covered, lo, hi = self.buys(got)
            rows = [(d, days.get(d, 0.0)) for d in covered]
            if rows:
                out.add(tidy(rows, PROJECT, "actual_buyback_tokens",
                             f"aave_chain:buys[AAVE/aAAVE into the DAO wallets from a swap venue; blocks {lo}..{hi}]",
                             TIER), SOURCE, PROJECT,
                        f"actual_buyback_tokens: {len(rows)} day(s) scanned, {sum(v for _d, v in rows):,.4f} AAVE bought",
                        TIER)
            big = [o for o in other if o["tokens"] >= float(s["other_inflow_log_min"])]
            if big:
                out.log.append(LogEntry(SOURCE, PROJECT, 0, "warn", "inflows to the DAO wallets that are NOT buys (not "
                                        "counted): " + "; ".join(f"{o['day']} {o['tokens']:,.2f} from {o['from']}"
                                                                 for o in big[-6:]), TIER))
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, PROJECT, f"actual_buyback_tokens: NOT STORED — {str(e)[:300]}", TIER)
            failed = True
        if not failed:
            self.daily.done(key, day)


if __name__ == "__main__":                                   # pragma: no cover — a live look, nothing stored
    a = AaveChain()
    g = a.first(config.AAVE_CHAIN["rpc"], a.stocks)
    print({k: v for k, v in g.items() if k != "wallets"})
    print(g["wallets"])
