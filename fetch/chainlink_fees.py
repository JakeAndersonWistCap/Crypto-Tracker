"""
fetch/chainlink_fees.py — Chainlink's customer-fee lines that BYPASS the fee aggregator. Jake, 2026-10-05.

CUSTOMER REVENUE IS BUILT AROUND THE AGGREGATOR (config Chainlink.chainlink_fee_lines):
  CORE   payments collected at the fee aggregator 0xd6e39d42... = fees_usd (DefiLlama's `chainlink`
         adapter: every token it receives) — CCIP 1.6 fees, CCIP 2.0 network fees, Payment
         Abstraction invoices (data feeds, enterprise).
  PLUS   the lines that never reach it, measured here from each contract's own events:
           CCIP 1.2/1.5   CCIPSendRequested on every EVM2EVMOnRamp: the WHOLE fee (feeToken,
                          feeTokenAmount) — booked to node operators (s_nopFeesJuels), not the
                          aggregator. Gross by Jake's decision (option a).
           VRF v2.5       RandomWordsFulfilled.payment (LINK or native, nativePayment flag) — gross.
           Automation 2.3 UpkeepCharged receipt: premiumInJuels x linkUSD (8-decimal feed) — the
                          premium only, in USD as the registry priced it.
         plus chainlink-requests from DefiLlama (fetch/llama.py, defillama_component_slugs).
  NEVER  CCIP 1.6 / 2.0 fees as a separate line: they are already in the core. The 2.0 events are
         read for two CROSS-CHECKS only — ccip_v2_fees_usd (all receipts) and ccip_v2_premium_usd
         (the network-fee receipt, the one that stays in the OnRamp and reaches the aggregator).

ONRAMPS ARE DISCOVERED, NOT LISTED. The docs list only today's OnRamps; a year of history needs the
retired ones too. Each chain's Router (address from Chainlink's docs) emits OnRampSet(uint64 indexed
destChainSelector, address onRamp) every time a lane's OnRamp changes, so every OnRamp ever used —
and the span over which it was live — comes from the Router's own history. A retired OnRamp is
scanned once over its live span and then marked done; routine runs scan only the live ones.

EXPLORER ROUTES (config.EXPLORER_LOG_ROUTES): Etherscan V2 free tier for Ethereum, Arbitrum and
Polygon; Blockscout for Base and Optimism. BNB and Avalanche are NOT covered: Etherscan's free tier
serves no BSC logs and no Blockscout host exists for BSC; Avalanche (43114) has no route on file.

A DAY IS STORED ONLY WHEN EVERY CHAIN OF THAT LINE IS SCANNED THROUGH IT AND EVERY EVENT ON IT IS
PRICED. Days with no events in a scanned span are measured zeros. Fee tokens and native payments are
priced per day from DefiLlama's coins API (coins.llama.fi, cached); Automation carries its own price.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict

import pandas as pd

import config
from .base import Http, Progress, tidy, today
from .explorer import ExplorerLogs, ExplorerRefused, ExplorerTimeout
from .logcache import LogCache, atomic_write_text, file_lock

log = logging.getLogger("token_metrics.fetch.chainlink_fees")

SOURCE = "chainlink_fees"
TIER = 2

# topic0 = keccak of each event signature, computed from the contract sources (see config).
TOPICS = {
    "onramp_set": "0x1f7d0ec248b80e5c0dde0ee531c4fc8fdb6ce9a2b3d90f560c74acd6a7202f23",
    "ccip_legacy": "0xd0c3c799bf9e2639de44391e7f524d229b2b55f5b1ea94b2bf7da42f7243dddd",
    "ccip_v2": "0x371bc2ff0a006f4ef863b1d27a065d4e9f938b6d883eb154572b4aea593b32cc",
    "vrf": "0xaeb4b4786571e184246d39587f659abf0e26f41f6a3358692250382c0cdb47b7",
    "automation": "0x801ba6ed51146ffe3e99d1dbd9dd0f4de6292e78a9a34c39c0183de17b3f40fc",
}
# which stored metric each line writes
METRIC = {
    "ccip_legacy": "customer_revenue_ccip_legacy_usd",
    "vrf": "customer_revenue_vrf_usd",
    "automation": "customer_revenue_automation_premium_usd",
    "ccip_v2": "ccip_v2_fees_usd",
    "ccip_v2_premium": "ccip_v2_premium_usd",
}
STREAM_BUDGET_S = 120         # routine runs (after the seed): one stream's day or two of logs
SEED_PASS_S = 120             # --seed: each pass of a stream, then the state is saved and it continues
SEED_MAX_STALLS = 3           # --seed: passes in a row that read nothing new before a stream is stopped


def _words(data: str) -> list[int]:
    raw = str(data or "0x")[2:]
    return [int(raw[i:i + 64], 16) for i in range(0, len(raw) - len(raw) % 64, 64)]


def decode_ccip_legacy(entry: dict) -> tuple[str, int]:
    """CCIPSendRequested(Internal.EVM2EVMMessage): data word 0 is the tuple offset, then the struct
    head — sourceChainSelector, sender, receiver, sequenceNumber, gasLimit, strict, nonce, feeToken
    (word 8), feeTokenAmount (word 9). Internal.sol:96-110 @ ccip contracts-ccip/v1.5.0."""
    w = _words(entry["data"])
    return "0x" + f"{w[8]:064x}"[-40:], w[9]


def decode_ccip_v2(entry: dict) -> tuple[str, int, int]:
    """CCIPMessageSent (OnRamp 2.0.0): (feeToken, gross fee = sum of receipts' feeTokenAmount,
    network fee = the LAST receipt's). OnRamp.sol:59-68, 128-146, 371-399 @ contracts-ccip-v2.0.0."""
    from eth_abi import decode
    raw = bytes.fromhex(str(entry["data"])[2:])
    token, _amt, _msg, receipts, _blobs = decode(
        ["address", "uint256", "bytes", "(address,uint32,uint32,uint256,bytes)[]", "bytes[]"], raw)
    fees = [int(r[3]) for r in receipts]
    return str(token).lower(), sum(fees), (fees[-1] if fees else 0)


def decode_vrf(entry: dict) -> tuple[int, bool]:
    """RandomWordsFulfilled (VRFCoordinatorV2_5.sol:71-79): data = outputSeed, payment, nativePayment,
    success, onlyPremium (requestId and subId are indexed). (payment, paid in native?)"""
    w = _words(entry["data"])
    return w[1], bool(w[2])


def decode_automation(entry: dict) -> float:
    """UpkeepCharged(uint256 indexed id, PaymentReceipt) (AutomationRegistryBase2_3.sol:444-457, 494):
    data = gasChargeInBillingToken, premiumInBillingToken, gasReimbursementInJuels, premiumInJuels,
    billingToken, linkUSD, nativeUSD, billingUSD. Premium in USD = premiumInJuels / 1e18 x linkUSD /
    1e8 (the registry's LINK/USD feed, 8 decimals — :697-704)."""
    w = _words(entry["data"])
    return w[3] / 1e18 * w[5] / 1e8


def onramp_spans(sets: list[tuple[int, int, str]]) -> dict[str, tuple[int, int | None]]:
    """{onRamp: (first block it was set, block it stopped being live or None)} from the Router's
    OnRampSet history [(block, destChainSelector, onRamp)]. An OnRamp is live on a lane from its
    set until the next set for that lane; it is retired once no lane points at it."""
    by_lane: dict = defaultdict(list)
    for b, sel, ramp in sorted(sets):
        by_lane[sel].append((b, ramp.lower()))
    first: dict = {}
    ends: dict = defaultdict(list)
    live: set = set()
    for sel, seq in by_lane.items():
        for i, (b, ramp) in enumerate(seq):
            first[ramp] = min(first.get(ramp, b), b)
            if i + 1 < len(seq):
                ends[ramp].append(seq[i + 1][0])
            else:
                live.add(ramp)
    zero = "0x" + "0" * 40
    return {r: (first[r], None if r in live else max(ends[r])) for r in first if r != zero}


class ChainlinkFees:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, explorer: ExplorerLogs | None = None, prices=None, cache_file=None,
                 http: Http | None = None, **_ignored):
        self.explorer = explorer or ExplorerLogs()
        self._prices_injected = prices          # tests: {(day, coin): (price, decimals)}
        self.http = http or Http(min_interval=0.25)
        self.cache_file = cache_file
        self._targets: dict = {}                # seed progress: {stream key: (first block, last block)}

    # ---------------------------------------------------------------- state
    def _path(self, name: str):
        from pathlib import Path
        if self.cache_file:
            p = Path(self.cache_file)
            return p if name == "chainlink-fees.json" else p.with_name(p.stem + "-" + name)
        return LogCache().root / name

    def _load(self, name: str) -> dict:
        try:
            return json.loads(self._path(name).read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, name: str, state: dict) -> None:
        p = self._path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(p):
            atomic_write_text(p, json.dumps(state))

    def seed_complete(self) -> dict | None:
        """The seed's completion record, or None while the year is not yet scanned on every chain."""
        return self._load("chainlink-fees.json").get("seed_complete")

    # ---------------------------------------------------------------- run
    def run(self, projects: list[dict], window_days, out, unbounded: bool = False):
        """unbounded=True is `--seed chainlink_fees`: no time budget, every stream read to the head in
        SEED_PASS_S passes with the state saved after each. A routine run (unbounded=False) reads only
        once the seed is complete — then a day or two of logs per stream."""
        for p in projects:
            spec = p.get("chainlink_fee_lines")
            if spec:
                self._project(p["name"], spec, out, unbounded)

    def _project(self, name: str, spec: dict, out, unbounded: bool = False) -> None:
        state = self._load("chainlink-fees.json")
        if not unbounded and not state.get("seed_complete"):
            out.skipped(SOURCE, name, "seed not complete — run `python token_metrics.py --seed chainlink_fees` "
                                      "(no time budget, resumable); routine runs read only the days after it", TIER)
            return
        streams = state.setdefault("streams", {})
        start_day = (today().normalize() - pd.Timedelta(days=int(spec.get("days", 365))))
        complete: dict = {k: True for k in ("ccip_legacy", "ccip_v2", "vrf", "automation")}
        why: dict = defaultdict(list)
        failed: dict = defaultdict(set)            # line -> chains not read through
        prog = None
        if unbounded:
            prog = Progress(f"--seed chainlink_fees {name}", unit="passes", every_units=1,
                            checkpoint=lambda: self._save("chainlink-fees.json", state),
                            fraction=self._fraction, status=lambda: self._status(streams))
        for chain, c in spec["chains"].items():
            cid = int(c["chain_id"])
            if not self.explorer.configured(cid):
                for k in complete:
                    complete[k] = False
                    failed[k].add(chain)
                    why[k].append(f"{chain}: no explorer key/route ({config.explorer_order(cid) or 'none'})")
                continue
            try:
                wstart = self._window_block(state, chain, cid, start_day)
                head = self._head_block(cid) if unbounded else None
            except ExplorerRefused as e:
                for k in complete:
                    complete[k] = False
                    failed[k].add(chain)
                    why[k].append(f"{chain}: start block not found ({e})")
                continue
            # single-address lines
            for kind, addr_key in (("vrf", "vrf_v2_5"), ("automation", "automation_registry")):
                ok = self._stream(streams, f"{chain}:{kind}:{c[addr_key].lower()}", kind, cid,
                                  c[addr_key], wstart, None, out, name, prog, head)
                if not ok:
                    complete[kind] = False
                    failed[kind].add(chain)
                    why[kind].append(f"{chain} {kind} scan incomplete")
            # CCIP: every OnRamp the Router ever pointed at
            try:
                spans = self._onramps(state, chain, cid, c["ccip_router"], unbounded)
            except (ExplorerRefused, ExplorerTimeout) as e:
                for k in ("ccip_legacy", "ccip_v2"):
                    complete[k] = False
                    failed[k].add(chain)
                    why[k].append(f"{chain}: Router OnRampSet history unreadable ({e})")
                continue
            for ramp, (b0, b1) in spans.items():
                if b1 is not None and b1 < wstart:
                    continue                        # retired before the window opened
                for kind in ("ccip_legacy", "ccip_v2"):
                    ok = self._stream(streams, f"{chain}:{kind}:{ramp}", kind, cid, ramp,
                                      max(wstart, b0), b1, out, name, prog, head)
                    if not ok:
                        complete[kind] = False
                        failed[kind].add(chain)
                        why[kind].append(f"{chain} OnRamp {ramp[:10]}… {kind} scan incomplete")
            self._save("chainlink-fees.json", state)
        self._prune(streams, start_day)
        optional = set(spec.get("optional_chains") or ())
        required_done = all(not (failed[k] - optional) for k in complete)
        if unbounded and required_done:
            # COMPLETE ON THE REQUIRED CHAINS (Jake, 2026-10-05): an optional chain still unread (Base and
            # OP behind Blockscout's limits) leaves its lines stored PARTIAL, not the whole seed open.
            state["seed_complete"] = {"window_start": str(start_day.date()),
                                      "completed_at": pd.Timestamp.now("UTC").isoformat(timespec="seconds"),
                                      "chains": [c for c in spec["chains"]
                                                 if not any(c in failed[k] for k in complete)],
                                      "partial_chains": sorted(set().union(*failed.values()) & optional)}
        self._save("chainlink-fees.json", state)
        if prog is not None:
            prog.flush(final=True)
            out.add(None, SOURCE, name, "seed " + ("COMPLETE — routine runs now read incrementally"
                                                  if state.get("seed_complete") else
                                                  "NOT complete: " + "; ".join(sum(why.values(), [])[:6])
                                                  + " — run the seed again; it resumes"), TIER)
        self._emit(name, spec, streams, complete, why, start_day, out, failed)

    def _window_block(self, state: dict, chain: str, cid: int, start_day: pd.Timestamp) -> int:
        key = f"{chain}:{start_day.date()}"
        cache = state.setdefault("window_blocks", {})
        if key not in cache:
            cache[key] = self.explorer.block_at(cid, int(start_day.timestamp()))
            for k in [k for k in cache if k.startswith(chain + ":") and k != key]:
                del cache[k]                        # yesterday's window start is never read again
        return int(cache[key])

    def _head_block(self, cid: int) -> int | None:
        """The block about two minutes ago — the seed's progress denominator only."""
        try:
            return self.explorer.block_at(cid, int(pd.Timestamp.now("UTC").timestamp()) - 120)
        except ExplorerRefused:
            return None

    def _onramps(self, state: dict, chain: str, cid: int, router: str, unbounded: bool = False) -> dict:
        r = state.setdefault("routers", {}).setdefault(chain, {"next": 0, "sets": []})
        self.explorer.start_budget(SEED_PASS_S if unbounded else STREAM_BUDGET_S)
        try:
            logs, _meta = self.explorer.get_logs(cid, router, [TOPICS["onramp_set"]], r["next"], "latest")
        finally:
            self.explorer.clear_budget()
        for e in logs:
            ramp = "0x" + str(e["data"])[2:].rjust(64, "0")[-40:]
            r["sets"].append([e["blockNumber"], int(e["topics"][1], 16), ramp.lower()])
        if logs:
            r["next"] = max(e["blockNumber"] for e in logs) + 1
        uniq = {tuple(s) for s in r["sets"]}
        r["sets"] = sorted([list(s) for s in uniq])
        return onramp_spans([tuple(s) for s in r["sets"]])

    # ---------------------------------------------------------------- streams
    @staticmethod
    def _migrate(s: dict) -> None:
        """Caches written before 2026-10-05 15:00 kept every decoded event ("ev"); fold them into the
        per-day sums ("agg") the stream keeps now."""
        for ev in s.pop("ev", None) or []:
            day, tok = ev[0], ev[1]
            cell = s.setdefault("agg", {}).setdefault(day, {}).setdefault(tok, ["0", "0"])
            if tok == "usd":
                cell[0] = repr(float(cell[0]) + float(ev[2]))
            else:
                cell[0] = str(int(cell[0]) + int(ev[2]))
                if len(ev) > 4:
                    cell[1] = str(int(cell[1]) + int(ev[3]))

    def _stream(self, streams: dict, key: str, kind: str, cid: int, address: str, start: int,
                end: int | None, out, name: str, prog=None, head: int | None = None) -> bool:
        """Read one (address, event) stream forward from its cursor. True when it is complete through
        the head (or its end block). Decoded fees accumulate as per-day sums in the cache. With `prog`
        (the seed) the stream is read in SEED_PASS_S passes until done, the state saved after each."""
        s = streams.setdefault(key, {"next": int(start), "agg": {}, "done": False})
        self._migrate(s)
        if not s.get("agg") and not s["done"]:
            s["next"] = max(int(s["next"]), int(start))
        self._targets[key] = (int(start), end if end is not None else head)
        if s["done"]:
            return True
        stalls = 0
        while True:
            before = (s["next"], json.dumps(s.get("agg"), sort_keys=True) if prog else None)
            finished = self._pass(s, key, kind, cid, address, end, out, name,
                                  SEED_PASS_S if prog else STREAM_BUDGET_S)
            if finished is None:
                return False                        # refused or undecodable: already reported
            if finished or prog is None:
                return finished
            prog.tick()
            after = (s["next"], json.dumps(s.get("agg"), sort_keys=True))
            stalls = stalls + 1 if after == before else 0
            if stalls >= SEED_MAX_STALLS:
                out.fail(SOURCE, name, f"{key}: {SEED_MAX_STALLS} passes of {SEED_PASS_S}s read nothing new "
                                       f"(cursor block {s['next']:,}) — stopped; the seed resumes here", TIER)
                return False

    def _pass(self, s: dict, key: str, kind: str, cid: int, address: str, end: int | None, out, name: str,
              budget_s: float) -> bool | None:
        """One budgeted get_logs from the stream's cursor. True = read to the end; False = out of time
        (the whole blocks read are kept and the cursor moved past them); None = refused."""
        to = end if end is not None else "latest"
        resume = None
        self.explorer.start_budget(budget_s)
        try:
            logs, _meta = self.explorer.get_logs(cid, address, [TOPICS[kind]], s["next"], to)
            finished = True
        except ExplorerTimeout as e:
            logs, finished, resume = (e.partial or []), False, e.resume_from
        except ExplorerRefused as e:
            out.fail(SOURCE, name, f"{key}: {e}", TIER)
            return None
        finally:
            self.explorer.clear_budget()
        agg = s.setdefault("agg", {})
        for e in logs:
            if int(e["blockNumber"]) < int(s["next"]):
                continue                            # already counted: the cursor is past this block
            if not e.get("timeStamp"):
                out.fail(SOURCE, name, f"{key}: a log without a timestamp (block {e['blockNumber']}) — "
                                       f"refused; the day cannot be assigned", TIER)
                return None
            day = str(pd.Timestamp(int(e["timeStamp"]), unit="s").normalize().date())
            if kind == "automation":
                cell = agg.setdefault(day, {}).setdefault("usd", ["0", "0"])
                cell[0] = repr(float(cell[0]) + decode_automation(e))
                continue
            if kind == "ccip_legacy":
                tok, amt = decode_ccip_legacy(e)
                prem = 0
            elif kind == "ccip_v2":
                tok, amt, prem = decode_ccip_v2(e)
            else:                                   # vrf
                amt, native = decode_vrf(e)
                tok, prem = ("native" if native else "link"), 0
            cell = agg.setdefault(day, {}).setdefault(tok, ["0", "0"])
            cell[0], cell[1] = str(int(cell[0]) + int(amt)), str(int(cell[1]) + int(prem))
        if finished:
            if logs:
                s["next"] = max(int(s["next"]), max(int(e["blockNumber"]) for e in logs) + 1)
            if end is not None:
                s["done"] = True
        else:
            # ExplorerTimeout.partial holds every log in the WHOLE blocks below its resume point
            if logs:
                s["next"] = max(int(s["next"]), max(int(e["blockNumber"]) for e in logs) + 1)
            if resume:
                s["next"] = max(int(s["next"]), int(resume))
        return finished

    @staticmethod
    def _prune(streams: dict, start_day: pd.Timestamp) -> None:
        """Days before the window are never emitted again; drop them so the cache stays a year."""
        cut = str(start_day.date())
        for s in streams.values():
            for d in [d for d in (s.get("agg") or {}) if d < cut]:
                del s["agg"][d]

    def _fraction(self) -> float | None:
        streams = self._load("chainlink-fees.json").get("streams") or {}
        tot = got = 0
        for key, (b0, b1) in self._targets.items():
            if b1 is None or b1 <= b0:
                continue
            s = streams.get(key) or {}
            tot += b1 - b0
            got += (b1 - b0) if s.get("done") else max(0, min(int(s.get("next", b0)), b1) - b0)
        return got / tot if tot else None

    def _status(self, streams: dict) -> str:
        done = sum(1 for k in self._targets if (streams.get(k) or {}).get("done"))
        days = {d for k in self._targets for d in ((streams.get(k) or {}).get("agg") or {})}
        return f"{len(self._targets)} stream(s) found, {done} retired OnRamp(s) finished, {len(days)} day(s) with fees"

    # ---------------------------------------------------------------- pricing
    def _price_map(self, needs: dict[str, set]) -> dict:
        """{(day, coin): (price, decimals)} for every needed (day -> coins), cached."""
        if self._prices_injected is not None:
            return self._prices_injected
        cache = self._load("chainlink-fee-prices.json")
        api = config.PROJECT_BY_NAME["Chainlink"]["chainlink_fee_lines"]["price_api"]
        for day, coins in sorted(needs.items()):
            have = cache.setdefault(day, {})
            missing = sorted(c for c in coins if c not in have)
            if not missing:
                continue
            ts = int((pd.Timestamp(day) + pd.Timedelta(hours=12)).timestamp())
            try:
                j = self.http.get(f"{api}/prices/historical/{ts}/{','.join(missing)}")
            except Exception as e:  # noqa: BLE001 — an unpriced day is refused, not guessed
                log.info("chainlink_fees: prices for %s unavailable (%s)", day, e)
                continue
            for coin, v in ((j or {}).get("coins") or {}).items():
                if isinstance(v, dict) and v.get("price") is not None:
                    have[coin] = [float(v["price"]), int(v.get("decimals") or 18)]
        self._save("chainlink-fee-prices.json", cache)
        return {(d, c): tuple(v) for d, m in cache.items() for c, v in m.items()}

    def _coin(self, spec: dict, chain: str, token: str) -> str:
        c = spec["chains"][chain]
        if token == "native":
            return c["native_coin"]
        if token == "link":
            return spec["link_coin"]
        return f"{c['llama_chain']}:{token}"

    # ---------------------------------------------------------------- emit
    def _emit(self, name, spec, streams, complete, why, start_day, out, failed=None) -> None:
        failed = failed or defaultdict(set)
        optional = set(spec.get("optional_chains") or ())
        last = today().normalize() - pd.Timedelta(days=1)
        days = [str(d.date()) for d in pd.date_range(start_day, last)]
        needs: dict = defaultdict(set)
        for key, s in streams.items():
            chain, kind = key.split(":")[:2]
            if chain not in spec["chains"]:
                continue
            self._migrate(s)
            for day, toks in (s.get("agg") or {}).items():
                for tok in toks:
                    if tok != "usd":
                        needs[day].add(self._coin(spec, chain, tok))
        prices = self._price_map(needs) if needs else {}
        totals: dict = {k: defaultdict(float) for k in METRIC}
        unpriced: dict = defaultdict(set)
        for key, s in streams.items():
            chain, kind = key.split(":")[:2]
            if chain not in spec["chains"] or chain in failed[kind]:
                continue                            # a chain not read through never contributes part-days
            for day, toks in (s.get("agg") or {}).items():
                for tok, (amt, prem) in toks.items():
                    if tok == "usd":
                        totals[kind][day] += float(amt)
                        continue
                    pr = prices.get((day, self._coin(spec, chain, tok)))
                    if pr is None:
                        unpriced[kind].add(day)
                        continue
                    px, dec = pr
                    totals[kind][day] += int(amt) / 10 ** dec * px
                    if kind == "ccip_v2":
                        totals["ccip_v2_premium"][day] += int(prem) / 10 ** dec * px
        for kind, metric in METRIC.items():
            base = "ccip_v2" if kind == "ccip_v2_premium" else kind
            missing = failed[base]
            if missing - optional:
                out.fail(SOURCE, name, f"{metric}: NOT STORED this run — " + "; ".join(why[base][:6]), TIER)
                continue
            bad = unpriced[base]
            rows = [(pd.Timestamp(d), totals[kind].get(d, 0.0)) for d in days if d not in bad]
            if not rows:
                continue
            chains = ",".join(c for c in spec["chains"] if c not in missing)
            src = f"{SOURCE}:{kind}[{chains}]"
            if missing:
                # STORED, MARKED: the optional chains' share is missing and named on the cell.
                src = config.mark_source(src, "PARTIAL") + f"[{', '.join(sorted(missing))} not covered]"
            frame = tidy(rows, name, metric, src, TIER)
            out.add(frame, SOURCE, name, f"{metric}: {len(rows)} day(s) {rows[0][0].date()}..{rows[-1][0].date()} "
                                         f"over {chains}" + (f" — PARTIAL, missing: {', '.join(sorted(missing))}"
                                                             if missing else "")
                                         + (f"; {len(bad)} day(s) refused — an event on them has no price"
                                            if bad else ""), TIER)
