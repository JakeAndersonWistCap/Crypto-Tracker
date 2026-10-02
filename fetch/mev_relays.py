"""
fetch/mev_relays.py — Ethereum's EXECUTION-LAYER staking reward, MEV included. Jake, 2026-10-02.

Execution yield = what proposers received for their blocks:
  RELAY BLOCKS      the value the relay reports delivered to the proposer — the builder's payment, which
                    ALREADY INCLUDES the block's priority fees (the builder collects them and pays the
                    proposer); so a relay block's priority fees are NEVER added on top.
  NON-RELAY BLOCKS  (built locally by the proposer) the block's priority fees, sum over its receipts of
                    gasUsed x (effectiveGasPrice - baseFeePerGas).
Never both for one block.

THE RELAY DATA API (flashbots/mev-boost-relay services/api/service.go, the common data API every listed
relay implements): GET {relay}/relay/v1/data/bidtraces/proposer_payload_delivered?cursor=<slot>&limit=200
returns delivered payloads with slot <= cursor, newest first (database.go: "slot <= :cursor", ORDER BY slot
DESC); 200 is the maximum limit — 100 on bloXroute's fork (bloXroute-Labs/mev-relay server/service.go
`Limit: 100`, "maximum limit is %d"; flashbots/relayscan cmd/core/data-api-backfill.go `pageLimit = 100 //
100 is max on bloxroute`), so each relay carries its own `page_limit`. Each row: slot, block_number, block_hash, value (wei, string), ...
A slot can appear on SEVERAL relays (the same payload delivered through more than one): rows are
DE-DUPLICATED BY SLOT, one value per slot; when two relays report different values for one slot the
larger is kept and the day says how many conflicts there were.

COVERAGE: the relays in config (Ethereum.mev_relays, from eth-educators/ethstaker-guides MEV-relay-list.md).
A block delivered by a relay NOT in the list is indistinguishable here from a locally built one and is
counted by its priority fees — which UNDERSTATES that block (the proposer was paid the builder's bid). The
day records blocks, relay blocks per relay and the covered share; execution_rewards_eth is PARTIAL when the
non-relay leg is not read (no ETHEREUM_RPC_URL, or the run's block budget spent).

A RELAY THAT FAILS does not stop the day (probes6, 2026-10-02): the day is kept with the relays that
answered, the missing ones NAMED and the day PARTIAL; it is re-read whole on the next runs (up to
`relay_retries`). A relay that fails on `disable_after` days running in one run is skipped for the rest of it.

THE NON-RELAY LEG has two routes (config `nonrelay_leg`):
  estimate   (default; ~0 extra calls) the day's total priority fees — DefiLlama fees - burn (fees_usd -
             revenue_usd), already stored, over the day's price — times the NON-RELAY share of the day's
             SLOTS (1 - relay blocks / slots; the missed-slot rate is not read, so the share is slightly
             high). Done at READ TIME in build_workbook (_mev_estimate_views); labelled `estimate`.
  per-block  receipts of every non-relay block on ETHEREUM_RPC_URL (~2 calls a block) — only for the
             newest `per_block_days` days (the forward top-up), 0 = never.

DAYS: UTC days; a slot's time is genesis 1606824023 + 12 x slot. The day's block range comes from the
execution RPC (first block at or after 00:00, bisected). State: <logcache>/mev-relays.json — per-day
aggregates only (never per-slot history), checkpointed after each day.
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

import config

from .base import Http, Progress, limit_wait, tidy, today

log = logging.getLogger("token_metrics.fetch.mev_relays")

SOURCE = "mev_relays"
TIER = 1
GENESIS = 1606824023          # beacon chain genesis, mainnet
PATH = "/relay/v1/data/bidtraces/proposer_payload_delivered"


def day_slots(day: pd.Timestamp) -> tuple[int, int]:
    """(first, last) slot whose start time falls in the UTC day."""
    t0 = int(pd.Timestamp(day).timestamp())
    first = max(-(-(t0 - GENESIS) // 12), 0)
    last = (t0 + 86400 - GENESIS - 1) // 12
    return first, last


class MevRelays:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http=None, rpc=None, cache_file: Path | None = None, max_seconds: float | None = 240,
                 clock=time.monotonic, sleep=time.sleep, days: int | None = None, **_ignored):
        self.days = days                  # --seed-days: a shorter first seed (Jake: offer 90 first)
        self._http = http                 # tests inject one fake for every relay
        self._rpc = rpc                   # tests inject callable(body) -> json
        self.cache_file, self.max_seconds, self.clock, self.sleep = cache_file, max_seconds, clock, sleep
        self._clients: dict = {}

    # --- state ----------------------------------------------------------------------------------
    def _path(self) -> Path:
        if self.cache_file:
            return Path(self.cache_file)
        from .logcache import LogCache
        return LogCache().root / "mev-relays.json"

    def _load(self) -> dict:
        try:
            return json.loads(self._path().read_text())
        except (OSError, ValueError):
            return {"days": {}}

    def _save(self, st) -> None:
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st))

    def _over(self, t0, unbounded) -> bool:
        return not unbounded and self.max_seconds is not None and self.clock() - t0 > self.max_seconds

    # --- relay pages ------------------------------------------------------------------------------
    def _client(self, name: str, spec: dict):
        if self._http is not None:
            return self._http
        if name not in self._clients:
            self._clients[name] = Http(min_interval=1.0 / float(spec.get("relay_rate_per_s", 1)), retries=3)
        return self._clients[name]

    def _relay_day(self, spec, relay: dict, first: int, last: int) -> dict:
        """{slot: (value_wei, block_number)} delivered by ONE relay in [first, last], newest first."""
        c = self._client(relay["name"], spec)
        got, cursor, lim = {}, last, int(relay.get("page_limit", 200))
        while cursor >= first:
            rows = c.get(relay["url"].rstrip("/") + PATH, params={"cursor": cursor, "limit": lim})
            w = limit_wait(getattr(c, "last_headers", None))
            if w > 0:
                log.warning("RATE LIMIT %s: waiting %.0fs", relay["name"], w)
                self.sleep(w)
            if not isinstance(rows, list):
                raise RuntimeError(f"{relay['name']}: answered {str(rows)[:160]}")
            if not rows:
                break
            low = None
            for r in rows:
                s = int(r["slot"])
                low = s if low is None else min(low, s)
                if first <= s <= last:
                    got[s] = (int(r["value"]), int(r["block_number"]))
            if len(rows) < lim or low is None or low < first:
                break
            cursor = low - 1
        return got

    # --- execution RPC ----------------------------------------------------------------------------
    def _rpc_url(self, spec) -> str | None:
        return os.environ.get(spec.get("rpc_env", "ETHEREUM_RPC_URL"), "").strip() or None

    def _call(self, spec, body):
        if self._rpc is not None:
            return self._rpc(body)
        url = self._rpc_url(spec)
        c = self._client("rpc", {"relay_rate_per_s": spec.get("rpc_rate_per_s", 10)})
        return c.post(url, json_body=body)

    def _batch(self, spec, calls):
        out, n = [], int(spec.get("rpc_batch", 20))
        for i in range(0, len(calls), n):
            chunk = calls[i:i + n]
            got = self._call(spec, [{"jsonrpc": "2.0", "id": k, "method": m, "params": p}
                                    for k, (m, p) in enumerate(chunk)])
            res = {int(r.get("id")): r for r in got if isinstance(r, dict)} if isinstance(got, list) else {}
            for k, (m, p) in enumerate(chunk):
                r = res.get(k) or {}
                if "result" not in r:
                    raise RuntimeError(f"{m} {str(p)[:60]}: {str(r.get('error') or got)[:160]}")
                out.append(r["result"])
        return out

    def _first_block_at(self, spec, ts: int, head: int, cache: dict) -> int:
        lo, hi = 0, head + 1
        while lo < hi:
            mid = (lo + hi) // 2
            if mid not in cache:
                blk = self._batch(spec, [("eth_getBlockByNumber", [hex(mid), False])])[0]
                cache[mid] = int(blk["timestamp"], 16) if blk else 2 ** 62
            if cache[mid] >= ts:
                hi = mid
            else:
                lo = mid + 1
        return lo

    def _priority_fees_eth(self, spec, blocks: list[int]) -> float:
        """Sum of priority fees over `blocks`: receipts' gasUsed x (effectiveGasPrice - baseFee)."""
        total = 0
        heads = self._batch(spec, [("eth_getBlockByNumber", [hex(b), False]) for b in blocks])
        receipts = self._batch(spec, [("eth_getBlockReceipts", [hex(b)]) for b in blocks])
        for h, rs in zip(heads, receipts):
            base = int(h.get("baseFeePerGas") or "0x0", 16)
            for r in rs or []:
                total += int(r["gasUsed"], 16) * max(int(r["effectiveGasPrice"], 16) - base, 0)
        return total / 1e18

    # --- the run ----------------------------------------------------------------------------------
    def run(self, projects: list[dict], window_days, out, unbounded: bool = False):
        for p in projects:
            spec = p.get("mev_relays")
            if spec:
                self._project(p["name"], spec, out, unbounded)

    def _project(self, name: str, spec: dict, out, unbounded: bool) -> None:
        t0 = self.clock()
        st = self._load()
        yday = (today() - pd.Timedelta(days=1)).normalize()
        days = [yday - pd.Timedelta(days=k) for k in range(int(self.days or spec.get("days", 365)))]
        retries = int(spec.get("relay_retries", 3))

        # a relay since DROPPED from config (bloXroute Max Profit, probes7) is not "missing": its days are
        # complete for the relays configured now, and are neither re-read nor marked PARTIAL for it
        configured = {r["name"] for r in spec["relays"]}
        for dd in st["days"].values():
            if dd.get("missing"):
                dd["missing"] = [m for m in dd["missing"] if m in configured]
                if not dd["missing"]:
                    dd.pop("missing")

        def due(d):
            dd = st["days"].get(str(d.date())) or {}
            return not dd.get("relay_done") or (dd.get("missing") and dd.get("attempts", 1) < retries)
        todo = [d for d in days if due(d)]
        have_rpc = self._rpc is not None or bool(self._rpc_url(spec))
        relays = spec["relays"]
        streak, disabled = {}, set()
        prog = Progress(f"mev_relays {name}", total=len(todo) or None, unit="days", every_units=5,
                        checkpoint=lambda: self._save(st),
                        status=lambda: f"{sum(1 for v in st['days'].values() if v.get('relay_done'))} day(s) of relay "
                                       f"data held; {sum(1 for v in st['days'].values() if v.get('nonrelay_done'))} "
                                       f"with the non-relay leg")
        notes, head, tcache = [], None, {}
        for d in todo:                                   # newest first
            if self._over(t0, unbounded):
                break
            first, last = day_slots(d)
            live = [r for r in relays if r["name"] not in disabled]

            def one(r):
                try:
                    return self._relay_day(spec, r, first, last)
                except Exception as e:  # noqa: BLE001 — this relay is named missing; the day goes on
                    return e
            with ThreadPoolExecutor(max_workers=max(len(live), 1)) as ex:
                per = list(ex.map(one, live))
            failed = {r["name"]: f"{type(g).__name__}: {str(g).split('?')[0][:120]}"
                      for r, g in zip(live, per) if isinstance(g, Exception)}
            if live and len(failed) == len(live):        # nothing answered: the network, not a relay
                notes.append(f"{d.date()}: every relay failed — {'; '.join(f'{k} {v}' for k, v in failed.items())[:240]}")
                break
            for r in live:
                streak[r["name"]] = streak.get(r["name"], 0) + 1 if r["name"] in failed else 0
                if streak[r["name"]] >= int(spec.get("disable_after", 3)) and r["name"] not in disabled:
                    disabled.add(r["name"])
                    notes.append(f"{r['name']} failed {streak[r['name']]} day(s) running ({failed[r['name']]}) — "
                                 f"skipped for the rest of this run; its days are PARTIAL and re-read next run")
            missing = sorted(set(failed) | disabled)
            for k, v in failed.items():
                log.warning("mev_relays %s %s: %s — the day is kept without it (PARTIAL)", d.date(), k, v)
            slots, conflicts, by_relay = {}, 0, {}
            for r, got in zip(live, per):
                if isinstance(got, Exception):
                    continue
                by_relay[r["name"]] = len(got)
                for s, (v, b) in got.items():
                    if s in slots and slots[s][0] != v:
                        conflicts += 1
                    if s not in slots or v > slots[s][0]:
                        slots[s] = (v, b)
            prev = st["days"].get(str(d.date())) or {}
            day = {"relay_done": True, "relay_value_eth": sum(v for v, _ in slots.values()) / 1e18,
                   "relay_blocks": len(slots), "slots": last - first + 1, "by_relay": by_relay,
                   "conflicts": conflicts, "relay_block_numbers": sorted(b for _, b in slots.values())}
            if missing:
                day.update(missing=missing, attempts=int(prev.get("attempts", 0)) + 1)
            st["days"][str(d.date())] = day
            prog.tick()
        prog.flush(final=not notes)
        # THE NON-RELAY LEG: each day's block range on the RPC, its non-relay blocks' priority fees
        budget = None if unbounded else int(spec.get("max_blocks_per_run", 2000))
        if have_rpc:
            try:
                head = int(self._call(spec, {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber",
                                             "params": []})["result"], 16)
            except Exception as e:  # noqa: BLE001
                notes.append(f"RPC: {str(e).split('?')[0][:160]}")
                have_rpc = False
        pb_days = int(spec.get("per_block_days", 0)) if spec.get("nonrelay_leg", "estimate") == "estimate" else None
        pb_from = str((yday - pd.Timedelta(days=pb_days - 1)).date()) if pb_days else None
        for key in sorted(st["days"], reverse=True):
            dd = st["days"][key]
            if not have_rpc or dd.get("nonrelay_done") or not dd.get("relay_done") or self._over(t0, unbounded):
                continue
            if pb_days is not None and (not pb_days or key < pb_from):
                continue                                  # the estimate covers it (read time)
            ts0 = int(pd.Timestamp(key).timestamp())
            try:
                b0 = self._first_block_at(spec, ts0, head, tcache)
                b1 = self._first_block_at(spec, ts0 + 86400, head, tcache) - 1
                relay_set = set(dd.get("relay_block_numbers") or ())
                local = [b for b in range(b0, b1 + 1) if b not in relay_set]
                if budget is not None and len(local) > budget:
                    break
                dd["nonrelay_eth"] = self._priority_fees_eth(spec, local) if local else 0.0
                dd.update(blocks=b1 - b0 + 1, nonrelay_blocks=len(local), nonrelay_done=True)
                dd.pop("relay_block_numbers", None)          # no longer needed: aggregates only
                if budget is not None:
                    budget -= len(local)
            except Exception as e:  # noqa: BLE001
                notes.append(f"non-relay {key}: {str(e).split('?')[0][:160]}")
                break
            self._save(st)
        self._save(st)
        self._store(name, spec, st, out, have_rpc, notes)

    def _store(self, name, spec, st, out, have_rpc, notes) -> None:
        relay_rows, exe_rows, share_rows, partial = [], [], [], 0
        for key, dd in sorted(st["days"].items()):
            if not dd.get("relay_done"):
                continue
            day = pd.Timestamp(key)
            relay_rows.append((day, dd["relay_value_eth"]))
            miss = tuple(dd.get("missing") or ())
            if dd.get("nonrelay_done"):
                exe_rows.append((day, dd["relay_value_eth"] + dd["nonrelay_eth"], False, miss))
                if dd.get("blocks"):
                    share_rows.append((day, dd["relay_blocks"] / dd["blocks"], False, miss))
            else:
                exe_rows.append((day, dd["relay_value_eth"], True, miss))
                partial += 1
                if dd.get("slots"):                       # the estimate's basis: share of SLOTS
                    share_rows.append((day, dd["relay_blocks"] / dd["slots"], True, miss))
        if not relay_rows:
            out.skipped(SOURCE, name, "no relay day complete yet" + (f"; {'; '.join(notes[:2])}" if notes else ""), TIER)
            return
        names = "+".join(r["name"] for r in spec["relays"])
        by_miss: dict = {}
        for d, v in relay_rows:
            by_miss.setdefault(tuple(st["days"][str(d.date())].get("missing") or ()), []).append((d, v))
        for miss, rows in sorted(by_miss.items()):
            src = f"{SOURCE}:delivered[{names}]" + (_missing_tag(miss) if miss else "")
            out.add(tidy(rows, name, "mev_relay_value_eth", src, TIER), SOURCE, name,
                    f"mev_relay_value_eth: {len(rows)} day(s), de-duplicated by slot"
                    + (f"; relays NOT read: {', '.join(miss)}" if miss else f", relays {names}"), TIER)
        leg = spec.get("nonrelay_leg", "estimate")
        groups_e: dict = {}
        for d, v, p, miss in exe_rows:
            groups_e.setdefault((p, miss), []).append((d, v))
        for (p, miss), rows in sorted(groups_e.items()):
            src = f"{SOURCE}:relay_value+nonrelay_priority_fees"
            if p:
                why = ("non-relay leg by ESTIMATE at read time (DefiLlama fees - burn x non-relay share of slots); "
                       "relay value only as stored" if leg == "estimate" else
                       "no ETHEREUM_RPC_URL — the non-relay blocks' priority fees are not read" if not have_rpc
                       else "the non-relay leg is not read yet (block budget)")
                src = config.mark_source(src, "PARTIAL") + f"[{why}]"
            if miss:
                src = (src if p else config.mark_source(src, "PARTIAL")) + _missing_tag(miss)
            out.add(tidy(rows, name, "execution_rewards_eth", src, TIER), SOURCE, name,
                    f"execution_rewards_eth{' PARTIAL' if p or miss else ''} on {len(rows)} day(s)"
                    + (" — relay value only, the non-relay leg is estimated at read time" if p and leg == "estimate"
                       else "") + (f"; relays NOT read: {', '.join(miss)}" if miss else ""), TIER)
        shares: dict = {}
        for d, v, est, miss in share_rows:
            shares.setdefault((est, miss), []).append((d, v))
        for (est, miss), rows in sorted(shares.items()):
            src = f"{SOURCE}:relay_blocks/blocks[{names}]"
            if est:
                src = config.mark_source(src, "estimate") + "[of SLOTS: the day's block count is not read]"
            if miss:
                src += _missing_tag(miss)
            out.add(tidy(rows, name, "mev_relay_block_share", src, TIER), SOURCE, name,
                    f"relay share of {'slots' if est else 'blocks'}: {len(rows)} day(s), latest "
                    f"{rows[-1][1]:.1%}; the rest is counted by priority fees — a block from a relay NOT covered "
                    f"is understated", TIER)
        if notes:
            out.skipped(SOURCE, name, "; ".join(notes[:3]) + " — resumes next run", TIER)


def _missing_tag(miss) -> str:
    return f"[relays not read: {', '.join(miss)} — their blocks count as non-relay (understated)]"


def seed_cost(spec: dict, relay_share: dict, days: int, covered: float, rpc: bool = False) -> dict:
    """What a seed of `days` costs (probes6 1b): relay pages from each relay's measured share of recent
    slots and its page limit (relays run side by side at relay_rate_per_s, so wall time is the busiest
    relay's), and the per-block non-relay leg's RPC calls (a block header + its receipts per non-relay block,
    batched rpc_batch to a request) against the estimate's ~0."""
    spd = 7200
    pages = {r["name"]: int(-(-relay_share.get(r["name"], 0.0) * spd // int(r.get("page_limit", 200))) + 1)
             for r in spec["relays"]}
    rate = float(spec.get("relay_rate_per_s", 1))
    nonrelay = round(spd * (1 - covered))
    calls_pb = 2 * nonrelay + 2 * 25                     # header + receipts per block, + the day's two bisections
    return {"days": days, "relay_calls_per_day": sum(pages.values()), "relay_calls": sum(pages.values()) * days,
            "busiest": max(pages, key=pages.get), "wall_s_per_day": max(pages.values()) / rate,
            "wall_h": max(pages.values()) / rate * days / 3600, "nonrelay_blocks_per_day": nonrelay,
            "per_block_rpc_calls_per_day": calls_pb, "per_block_rpc_calls": calls_pb * days,
            "per_block_http_requests": -(-calls_pb // int(spec.get("rpc_batch", 20))) * days,
            "estimate_extra_calls": 0}
