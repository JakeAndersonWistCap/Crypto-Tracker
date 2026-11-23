"""
fetch/aero_leaf.py — tier 2: Aero's STAKER REVENUE ON THE CHAINS OTHER THAN BASE (R20, Jake 2026-11-24: "Holder value:
all-chain on-chain staker revenue; until the other seven chains are stored, DefiLlama's holder revenue").

fetch/aero_v3.py reads Base only. Aero pays stakers on eight chains (docs/r19_aerodrome_research.md: the seven others
paid $203k-$397k a week, 15%-22% of the total). This reader points THE SAME METHOD at each other chain
(config.AERO_LEAF_CHAINS):
  * the chain's reward managers: <locator>.factoryRegistry() -> gaugeFactories() -> factoryToGauges(factory) ->
    gaugeToRewards(gauge) — the FactoryRegistry, as the R19 scratch read (no VotingRewardsFactory log scan is needed);
  * FEES: every NotifyFeesAmount a manager emitted in the epoch (topic-only eth_getLogs over the epoch's blocks,
    filtered to the managers), exactly aero_v3.epoch_rewards;
  * INCENTIVES: each IncentiveStreaming programme's rate x its overlap with the epoch (incentiveCount / incentive(id));
  * priced by DefiLlama's coins API at the epoch's END (<llama chain>:<token>); a token with no price is left out of the
    $ and COUNTED (aero_leaf_unpriced_count_<chain>), never valued at 0 silently.
One COMPLETE epoch (Thursday 00:00 UTC, Unix-week aligned) per chain is three rows dated the epoch's start:
    aero_leaf_fees_usd_<chain>, aero_leaf_incentives_usd_<chain>, aero_leaf_unpriced_count_<chain>
ALL OR NO ROW per chain-epoch: a chain-epoch that could not be read whole stores nothing and is named. An epoch the store
holds is never read again; the tier stops ITSELF at its budget (config budget_s) between chain-epochs and inside the
log read, and the next run continues with the chain-epochs still owed. The history (every epoch since the merger) is
read by archive_backfill.py with the run's budget.

The build forms the all-chain series only for an epoch EVERY chain holds and switches holder value to it only when the
window is hole-free (build_workbook.allchain_epochs).
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import config
from .aero_v3 import CALLS, WAD, WEEK, _address_array, _keccak, _words_of, decode_words, epoch_rewards, topic0  # noqa: F401
from .base import Http, point, today

log = logging.getLogger("token_metrics.fetch.aero_leaf")

SOURCE = "aero_leaf"
TIER = 2
SPAN_MIN = 500
RESULT_CAP = 10_000
SIGS = {"factoryRegistry": "factoryRegistry()", "gaugeFactories": "gaugeFactories()",
        "factoryToGauges": "factoryToGauges(address)", "gaugeToRewards": "gaugeToRewards(address)",
        "incentiveCount": CALLS["incentiveCount"][0], "incentive": CALLS["incentive"][0]}
RANGE_WORDS = ("range", "too many", "limit", "exceed", "10000", "results", "entity too large", "response size")


class LeafBudgetSpent(RuntimeError):
    """The tier's own budget ran out; nothing partial is stored and the next run reads the chain-epoch."""


def metrics_of(chain: str) -> tuple:
    return (f"aero_leaf_fees_usd_{chain}", f"aero_leaf_incentives_usd_{chain}", f"aero_leaf_unpriced_count_{chain}")


def epochs_due(now: int, spec: dict, chain: str, have: set) -> list:
    """[(start, end)] of the complete epochs this chain still owes, newest first: the last `epochs_back` complete
    Unix weeks, none before the chain's first epoch (its own `from_epoch`, else the spec's), none in `have`
    ({'YYYY-MM-DD'} of the epoch starts stored)."""
    cs = spec["chains"][chain]
    first = int(pd.Timestamp(cs.get("from_epoch") or spec["from_epoch"]).timestamp())
    last = int(now) // WEEK * WEEK
    out = []
    for k in range(int(spec.get("epochs_back") or 8)):
        s = last - (k + 1) * WEEK
        if s >= first and str(pd.Timestamp(s, unit="s").date()) not in have:
            out.append((s, s + WEEK))
    return out


def value_epoch(amounts: dict, px: dict) -> tuple[float, float, int]:
    """(fees $, incentives $, unpriced tokens) of {token: (fees raw, incentives raw)} at {token: (price, decimals)}."""
    fee = inc = 0.0
    unpriced = 0
    for tok, (f, i) in amounts.items():
        if tok not in px:
            unpriced += 1
            continue
        p, dec = px[tok]
        fee += f / 10 ** dec * p
        inc += i / 10 ** dec * p
    return fee, inc, unpriced


class AeroLeafEpochs:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, stored_long=None, rpc=None, prices=None, now: int | None = None, clock=time.monotonic,
                 budget_s: float | None = None, state: dict | None = None, workers: int = 1,
                 epochs_back: int | None = None, **_ignored):
        self.http = Http(min_interval=0.25, retries=2)       # tenderly gateways answer 429 to bursts (read 2026-11-23)
        self.stored = stored_long
        self._rpc = rpc                  # tests: (chain, method, params) -> result
        self._prices = prices            # tests: (chain, tokens, ts) -> {token: (price, decimals)}
        self.now = now
        self._clock = clock
        self.budget_s = budget_s
        self.epochs_back = epochs_back   # archive_backfill: every epoch since the merger, not the daily tier's last 8
        self.state = state
        # one call at a time by default (the shared Http paces per host; measured 2026-11-23: one epoch on the seven
        # chains = 1,650 calls in 323 s — Elysium 663 calls / 122 s at 5,000 blocks a call)
        self.workers = max(1, int(workers))
        self.deadline = None
        self._ts: dict = {}
        self._head: dict = {}

    # ---------------------------------------------------------------- transport
    def rpc(self, chain: str, method: str, params: list):
        if self._rpc is not None:
            return self._rpc(chain, method, params)
        errs = []
        for url in self.spec["chains"][chain]["rpc"]:
            try:
                r = self.http.post(url, json_body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                if isinstance(r, dict) and "result" in r:
                    return r["result"]
                errs.append(f"{url.split('/')[2]}: {str((r or {}).get('error'))[:120]}")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{url.split('/')[2]}: {str(e)[:120]}")
        raise RuntimeError("; ".join(errs))

    def call(self, chain: str, to: str, sig: str, *args, block="latest") -> str:
        data = _keccak(sig)[:10] + "".join(f"{int(a, 16) if isinstance(a, str) else a:064x}" for a in args)
        res = self.rpc(chain, "eth_call", [{"to": to, "data": data}, hex(block) if isinstance(block, int) else block])
        if not res or res == "0x":
            raise RuntimeError(f"{sig} on {to} answered nothing on {chain}")
        return res

    def prices(self, chain: str, tokens: list, ts: int) -> dict:
        if self._prices is not None:
            return self._prices(chain, tokens, ts)
        out, ll = {}, self.spec["chains"][chain]["llama"]
        for i in range(0, len(tokens), 30):
            keys = ",".join(f"{ll}:{t}" for t in tokens[i:i + 30])
            body = self.http.get(f"https://coins.llama.fi/prices/historical/{ts}/{keys}")
            for k, v in ((body or {}).get("coins") or {}).items():
                out[k.split(":", 1)[1].lower()] = (float(v["price"]), int(v.get("decimals") or 18))
        return out

    def _spent(self) -> bool:
        return self.deadline is not None and self._clock() > self.deadline

    def _map(self, fn, items: list) -> list:
        if self.workers == 1 or len(items) < 2:
            return [fn(x) for x in items]
        with ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(fn, items))

    # ---------------------------------------------------------------- blocks
    def _block_ts(self, chain: str, n: int) -> int:
        k = (chain, n)
        if k not in self._ts:
            b = self.rpc(chain, "eth_getBlockByNumber", [hex(n), False])
            if not b or "timestamp" not in b:
                raise RuntimeError(f"{chain} block {n:,} has no header on this endpoint")
            self._ts[k] = int(b["timestamp"], 16)
        return self._ts[k]

    def block_at(self, chain: str, ts: int) -> int:
        """The first block of `chain` whose timestamp is >= ts (bisection on headers from the chain's start block)."""
        if chain not in self._head:
            self._head[chain] = int(self.rpc(chain, "eth_blockNumber", []), 16)
        lo, hi = int(self.spec["chains"][chain].get("start_block") or 0), self._head[chain]
        if self._block_ts(chain, hi) < ts:
            raise RuntimeError(f"{chain}: timestamp {ts} is after the head block {hi:,}")
        for (c, b), t in list(self._ts.items()):
            if c != chain:
                continue
            if t < ts and b + 1 > lo:
                lo = b + 1
            elif t >= ts and b < hi:
                hi = b
        while lo < hi:
            mid = (lo + hi) // 2
            if self._block_ts(chain, mid) >= ts:
                hi = mid
            else:
                lo = mid + 1
        return lo

    # ---------------------------------------------------------------- the chain's managers and programmes
    def managers(self, chain: str) -> list:
        reg = "0x" + f"{_words_of(self.call(chain, self.spec['locator'], SIGS['factoryRegistry']))[0]:040x}"[-40:]
        gauges = []
        for gf in _address_array(self.call(chain, reg, SIGS["gaugeFactories"])):
            gauges += _address_array(self.call(chain, reg, SIGS["factoryToGauges"], gf))
        got = self._map(lambda g: self.call(chain, reg, SIGS["gaugeToRewards"], g), gauges)
        return sorted({"0x" + f"{_words_of(x)[0]:040x}"[-40:] for x in got} - {"0x" + "0" * 40})

    def programs(self, chain: str, mgrs: list) -> list:
        counts = [_words_of(x)[0] for x in self._map(lambda m: self.call(chain, m, SIGS["incentiveCount"]), mgrs)]
        ids = [(m, i) for m, n in zip(mgrs, counts) for i in range(1, n + 1)]
        return [decode_words(x, CALLS["incentive"][1])
                for x in self._map(lambda mi: self.call(chain, mi[0], SIGS["incentive"], mi[1]), ids)]

    # ---------------------------------------------------------------- logs
    def fee_logs(self, chain: str, mgrs: set, b0: int, b1: int) -> list:
        """Every NotifyFeesAmount of the managers in [b0, b1]: topic-only eth_getLogs in spans the endpoint serves (a
        refused or capped span is halved, down to SPAN_MIN), each span read whole or the read fails."""
        t0 = topic0("NotifyFeesAmount")
        span = int(self.spec["chains"][chain].get("span") or 9_000)

        def one(ab, span_=None):
            x, y = ab
            if self._spent():
                raise LeafBudgetSpent(f"budget spent inside {chain}'s log read at block {x:,}")
            try:
                res = self.rpc(chain, "eth_getLogs", [{"topics": [t0], "fromBlock": hex(x), "toBlock": hex(y)}]) or []
            except LeafBudgetSpent:
                raise
            except Exception as e:  # noqa: BLE001
                if y - x + 1 <= SPAN_MIN or not any(w in str(e).lower() for w in RANGE_WORDS):
                    raise
                res = None
            if res is None or (len(res) >= RESULT_CAP and y > x):
                m = (x + y) // 2
                return one((x, m)) + one((m + 1, y))
            return res
        ranges = [(a, min(a + span - 1, b1)) for a in range(b0, b1 + 1, span)]
        out = []
        for part in self._map(one, ranges):
            out += part
        seen, keep = set(), []
        for lg in out:
            k = (str(lg.get("transactionHash")), str(lg.get("logIndex")))
            if str(lg.get("address", "")).lower() in mgrs and k not in seen and len(lg.get("topics") or ()) >= 3:
                seen.add(k)
                keep.append({"topics": list(lg["topics"]), "data": lg.get("data", "0x")})
        return keep

    # ---------------------------------------------------------------- run
    def _stored(self, name: str, chain: str) -> set:
        f = self.stored
        if f is None or not len(f):
            return set()
        m = metrics_of(chain)[0]
        rows = f[(f["project"] == name) & (f["metric"] == m) & f["source"].astype(str).str.startswith(SOURCE + ":")]
        return set(pd.to_datetime(rows["date"]).dt.strftime("%Y-%m-%d"))

    def run(self, projects: list[dict], window_days, out):
        spec = getattr(config, "AERO_LEAF_CHAINS", None)
        m = config.AERODROME_MIGRATION
        name = m["project"]
        if not spec or name not in {p["name"] for p in projects}:
            return
        st = config.aerodrome_migration_state() if self.state is None else self.state
        if pd.Timestamp(today()) < pd.Timestamp(m["cutover_utc"]) or st.get("launched") is not True:
            return
        if self.epochs_back:
            spec = {**spec, "epochs_back": int(self.epochs_back)}
        self.spec = spec
        self.deadline = self._clock() + float(spec["budget_s"] if self.budget_s is None else self.budget_s)
        now = self.now or int(time.time())
        left = []
        for chain in spec["chains"]:
            ms = metrics_of(chain)
            if any(x not in config.METRICS for x in ms):
                continue
            due = epochs_due(now, spec, chain, self._stored(name, chain))
            if not due:
                out.mark_current(SOURCE, name, ms[0], f"{chain}: every complete epoch is stored — not read again", TIER)
                continue
            if self._spent():
                left.append(f"{chain} ({len(due)} epoch(s))")
                continue
            try:
                mgrs = self.managers(chain)
                progs = self.programs(chain, mgrs) if mgrs else []
                for start, end in due:
                    if self._spent():
                        raise LeafBudgetSpent(f"budget spent before {chain}'s epoch "
                                              f"{pd.Timestamp(start, unit='s').date()}")
                    self._epoch(name, chain, mgrs, progs, start, end, out)
            except LeafBudgetSpent as e:
                left.append(f"{chain}: {e}")
            except Exception as e:  # noqa: BLE001 — this chain waits for the next run; the others are still read
                out.fail(SOURCE, name, f"{chain}: staker revenue NOT read ({str(e)[:240]}) — nothing stored for its "
                                       f"owed epoch(s) (unknown, never 0); the next run reads them", TIER)
        if left:
            out.warn(SOURCE, name, "the tier's budget was spent with chain-epoch(s) still owed (read next run, or by "
                                   "archive_backfill.py): " + "; ".join(left), TIER)

    def _epoch(self, name: str, chain: str, mgrs: list, progs: list, start: int, end: int, out) -> None:
        b0, b1 = self.block_at(chain, start), self.block_at(chain, end) - 1
        fees = self.fee_logs(chain, set(mgrs), b0, b1) if mgrs else []
        amounts = epoch_rewards(fees, progs, start, end)
        px = self.prices(chain, sorted(amounts), end) if amounts else {}
        fee_usd, inc_usd, unpriced = value_epoch(amounts, px)
        when = pd.Timestamp(start, unit="s").normalize()
        src = f"{SOURCE}:{chain}:epoch[{len(mgrs)} managers]"
        fm, im, um = metrics_of(chain)
        frames = [point(name, fm, fee_usd, src, TIER, when), point(name, im, inc_usd, src, TIER, when),
                  point(name, um, float(unpriced), src, TIER, when)]
        out.add(pd.concat(frames, ignore_index=True), SOURCE, name,
                f"{chain} epoch {when.date()} (blocks {b0:,}-{b1:,}): fees ${fee_usd:,.2f} + incentives ${inc_usd:,.2f} "
                f"from {len(fees)} fee log(s), {len(progs)} programme(s); {unpriced} token(s) unpriced", TIER)
