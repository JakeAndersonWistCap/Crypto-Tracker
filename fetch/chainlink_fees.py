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
from .base import Http, tidy, today
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
STREAM_BUDGET_S = 120


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

    # ---------------------------------------------------------------- run
    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("chainlink_fee_lines")
            if spec:
                self._project(p["name"], spec, out)

    def _project(self, name: str, spec: dict, out) -> None:
        state = self._load("chainlink-fees.json")
        streams = state.setdefault("streams", {})
        start_day = (today().normalize() - pd.Timedelta(days=int(spec.get("days", 365))))
        complete: dict = {k: True for k in ("ccip_legacy", "ccip_v2", "vrf", "automation")}
        why: dict = defaultdict(list)
        for chain, c in spec["chains"].items():
            cid = int(c["chain_id"])
            if not self.explorer.configured(cid):
                for k in complete:
                    complete[k] = False
                    why[k].append(f"{chain}: no explorer key/route ({config.explorer_order(cid) or 'none'})")
                continue
            try:
                wstart = self._window_block(state, chain, cid, start_day)
            except ExplorerRefused as e:
                for k in complete:
                    complete[k] = False
                    why[k].append(f"{chain}: start block not found ({e})")
                continue
            # single-address lines
            for kind, addr_key in (("vrf", "vrf_v2_5"), ("automation", "automation_registry")):
                ok = self._stream(streams, f"{chain}:{kind}:{c[addr_key].lower()}", kind, cid,
                                  c[addr_key], wstart, None, out, name)
                if not ok:
                    complete[kind] = False
                    why[kind].append(f"{chain} {kind} scan incomplete")
            # CCIP: every OnRamp the Router ever pointed at
            try:
                spans = self._onramps(state, chain, cid, c["ccip_router"])
            except (ExplorerRefused, ExplorerTimeout) as e:
                for k in ("ccip_legacy", "ccip_v2"):
                    complete[k] = False
                    why[k].append(f"{chain}: Router OnRampSet history unreadable ({e})")
                continue
            for ramp, (b0, b1) in spans.items():
                if b1 is not None and b1 < wstart:
                    continue                        # retired before the window opened
                for kind in ("ccip_legacy", "ccip_v2"):
                    ok = self._stream(streams, f"{chain}:{kind}:{ramp}", kind, cid, ramp,
                                      max(wstart, b0), b1, out, name)
                    if not ok:
                        complete[kind] = False
                        why[kind].append(f"{chain} OnRamp {ramp[:10]}… {kind} scan incomplete")
            self._save("chainlink-fees.json", state)
        self._save("chainlink-fees.json", state)
        self._emit(name, spec, streams, complete, why, start_day, out)

    def _window_block(self, state: dict, chain: str, cid: int, start_day: pd.Timestamp) -> int:
        key = f"{chain}:{start_day.date()}"
        cache = state.setdefault("window_blocks", {})
        if key not in cache:
            cache[key] = self.explorer.block_at(cid, int(start_day.timestamp()))
        return int(cache[key])

    def _onramps(self, state: dict, chain: str, cid: int, router: str) -> dict:
        r = state.setdefault("routers", {}).setdefault(chain, {"next": 0, "sets": []})
        self.explorer.start_budget(STREAM_BUDGET_S)
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

    def _stream(self, streams: dict, key: str, kind: str, cid: int, address: str, start: int,
                end: int | None, out, name: str) -> bool:
        """Read one (address, event) stream forward from its cursor. True when it is complete
        through the head (or its end block); the decoded events accumulate in the cache."""
        s = streams.setdefault(key, {"next": int(start), "ev": [], "done": False})
        if s["done"]:
            return True
        if not s["ev"]:
            s["next"] = max(int(s["next"]), int(start))
        seen = {ev[-1] for ev in s["ev"]}
        to = end if end is not None else "latest"
        self.explorer.start_budget(STREAM_BUDGET_S)
        try:
            logs, meta = self.explorer.get_logs(cid, address, [TOPICS[kind]], s["next"], to)
            finished = True
        except ExplorerTimeout as e:
            logs, finished = (e.partial or []), False
            if e.resume_from:
                s["next"] = int(e.resume_from)
        except ExplorerRefused as e:
            out.fail(SOURCE, name, f"{key}: {e}", TIER)
            return False
        finally:
            self.explorer.clear_budget()
        for e in logs:
            uid = f"{e['transactionHash']}:{e['logIndex']}"
            if uid in seen:
                continue                            # a re-read block: each log counts once
            seen.add(uid)
            if not e.get("timeStamp"):
                out.fail(SOURCE, name, f"{key}: a log without a timestamp (block {e['blockNumber']}) — "
                                       f"refused; the day cannot be assigned", TIER)
                return False
            day = str(pd.Timestamp(int(e["timeStamp"]), unit="s").normalize().date())
            if kind == "ccip_legacy":
                tok, amt = decode_ccip_legacy(e)
                s["ev"].append([day, tok, str(amt), uid])
            elif kind == "ccip_v2":
                tok, gross, prem = decode_ccip_v2(e)
                s["ev"].append([day, tok, str(gross), str(prem), uid])
            elif kind == "vrf":
                pay, native = decode_vrf(e)
                s["ev"].append([day, "native" if native else "link", str(pay), uid])
            elif kind == "automation":
                s["ev"].append([day, "usd", repr(decode_automation(e)), uid])
        if finished:
            if logs:
                s["next"] = max(e["blockNumber"] for e in logs) + 1
            if end is not None:
                s["done"] = True
        return finished

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
    def _emit(self, name, spec, streams, complete, why, start_day, out) -> None:
        last = today().normalize() - pd.Timedelta(days=1)
        days = [str(d.date()) for d in pd.date_range(start_day, last)]
        needs: dict = defaultdict(set)
        for key, s in streams.items():
            chain, kind = key.split(":")[:2]
            if chain not in spec["chains"]:
                continue
            for ev in s["ev"]:
                if ev[1] != "usd":
                    needs[ev[0]].add(self._coin(spec, chain, ev[1]))
        prices = self._price_map(needs) if needs else {}
        totals: dict = {k: defaultdict(float) for k in METRIC}
        unpriced: dict = defaultdict(set)
        for key, s in streams.items():
            chain, kind = key.split(":")[:2]
            if chain not in spec["chains"]:
                continue
            for ev in s["ev"]:
                day = ev[0]
                if ev[1] == "usd":
                    totals[kind][day] += float(ev[2])
                    continue
                coin = self._coin(spec, chain, ev[1])
                pr = prices.get((day, coin))
                if pr is None:
                    unpriced[kind].add(day)
                    continue
                px, dec = pr
                totals[kind][day] += int(ev[2]) / 10 ** dec * px
                if kind == "ccip_v2":
                    totals["ccip_v2_premium"][day] += int(ev[3]) / 10 ** dec * px
        chains = ",".join(spec["chains"])
        for kind, metric in METRIC.items():
            base = "ccip_v2" if kind == "ccip_v2_premium" else kind
            if not complete[base]:
                out.fail(SOURCE, name, f"{metric}: NOT STORED this run — " + "; ".join(why[base][:6]), TIER)
                continue
            bad = unpriced[base]
            rows = [(pd.Timestamp(d), totals[kind].get(d, 0.0)) for d in days if d not in bad]
            if not rows:
                continue
            frame = tidy(rows, name, metric, f"{SOURCE}:{kind}[{chains}]", TIER)
            out.add(frame, SOURCE, name, f"{metric}: {len(rows)} day(s) {rows[0][0].date()}..{rows[-1][0].date()} "
                                         f"over {chains}" + (f"; {len(bad)} day(s) refused — an event on "
                                                             f"them has no price" if bad else ""), TIER)
