"""
fetch/balance_flow.py — tier 2: a wallet group's daily OUTFLOW from its daily balances. Added
2026-09-28 (B1, GEODNET's mining wallets).

** WHY NOT THE OUTFLOW EVENTS. ** GEODNET's two mining wallets pay out 300K+ Transfer events. The
full-history event scan (log_scans.mining_wallets_outflow) never finished: --seed geodnet ran
447s and stored nothing (Etherscan "server too busy", Blockscout 429) and the routine run reached
55% of the chain. The INFLOWS are few (7 and 215 events so far), and a balance at a past block is
one archive eth_call. For the group taken together, internal hops cancel, so exactly:

    external outflow(D) = external inflow(D) - (balance at end of D - balance at start of D)
    release(D)          = external outflow(D) - sent to an excluded counterparty (burn/zero) in D

IN WEI, WITH NO TOLERANCE. Every term is an integer. An external outflow below zero is impossible
and means an inflow event is missing — that refuses the whole series and names the day.

DAY BOUNDARIES ARE BLOCKS. B(D) is the first block whose timestamp is at or after D 00:00 UTC; the
balance at the start of D is balanceOf at B(D) - 1, and an event belongs to D when
B(D) <= block < B(D+1). The boundaries are found by an interpolation search on block timestamps
(Polygon's block time is regular, so a handful of eth_getBlockByNumber calls each).

ONCE, THEN ONE DAY A RUN. Boundaries and balances are kept in
<TOKEN_METRICS_LOGCACHE>/balance-flow-<key>.json; a run fills whatever days are missing, newest
first, inside its budget, and saves after every day, so a seed that stops loses nothing. The
inflow and burn-send events are read incrementally through the same LogCache as the log scans.

ARCHIVE STATE IS REQUIRED for any balance older than the node's retained window. A keyed Polygon
endpoint (POLYGON_RPC_URL — Alchemy's free tier serves archive) is tried first; a node that
refuses a historical call leaves a named gap, and check_offline_items geod_archive_probe says
which endpoints serve it.
"""
from __future__ import annotations

import bisect
import json
import logging
import os
import time
from pathlib import Path

import pandas as pd

import config
from .base import LogEntry, tidy, today, Progress
from .logcache import LogCache, stream_id
from .explorer import ExplorerLogs, ExplorerRefused, TRANSFER_TOPIC, pad_address, topic_address
from .logcache import atomic_write_text

log = logging.getLogger("token_metrics.fetch.balance_flow")

SOURCE = "balance_flow"
TIER = 2
ROUTINE_BUDGET_S = 60.0


def _amount(e: dict) -> int:
    from .logscan import hexint
    return hexint(e.get("data"))          # "0x" = no value, never a crash (2026-10-06 18:21)


class BalanceFlow:
    """Runs every project's `balance_flows` spec (GEODNET's mining wallets)."""

    def __init__(self, reader=None, explorer: ExplorerLogs | None = None,
                 cache: LogCache | None = None, unbounded: bool = False,
                 budget_s: float = ROUTINE_BUDGET_S):
        if reader is None:
            from .chain import ChainReader
            reader = ChainReader()
        self.reader = reader
        self.explorer = explorer or ExplorerLogs()
        self.cache = cache or LogCache()
        self.unbounded = unbounded
        self.budget_s = budget_s
        self._blocks: dict[int, int] = {}

    # ------------------------------------------------------------------ state
    def _state_file(self, key: str) -> Path:
        return self.cache.root / f"balance-flow-{key}.json"

    def _load(self, key: str) -> dict:
        try:
            st = json.loads(self._state_file(key).read_text())
        except (OSError, ValueError):
            st = {}
        st.setdefault("boundary", {})
        st.setdefault("balance", {})
        return st

    def _save(self, key: str, st: dict) -> None:
        f = self._state_file(key)
        f.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(f, json.dumps(st, sort_keys=True))

    # ------------------------------------------------------------------ blocks
    def _ts(self, w3, n: int) -> int:
        if n not in self._blocks:
            self._blocks[n] = int(w3.eth.get_block(n)["timestamp"])
        return self._blocks[n]

    def _block_at(self, w3, ts: int, head: int, known: list[tuple[int, int]]) -> int:
        """First block with timestamp >= ts. `known` is (block, timestamp) pairs already read."""
        lo, hi = (0, self._ts(w3, 0)), (head, self._ts(w3, head))
        # A known boundary b is the first block at or after its day's start t: b-1 is certainly
        # before t, and b certainly at or after it. Their exact timestamps are read when used.
        for b, t in known:
            if t < ts and b - 1 > lo[0]:
                lo = (b - 1, self._ts(w3, b - 1))
            if t >= ts and b < hi[0]:
                hi = (b, self._ts(w3, b))
        if hi[1] < ts:
            raise ValueError(f"timestamp {ts} is after the head block {head}")
        step = 0
        while hi[0] - lo[0] > 1:
            step += 1
            if step % 3 and hi[1] > lo[1]:           # interpolate, with a bisection every third
                guess = lo[0] + int((ts - lo[1]) * (hi[0] - lo[0]) / (hi[1] - lo[1]))
            else:
                guess = (lo[0] + hi[0]) // 2
            guess = min(max(guess, lo[0] + 1), hi[0] - 1)
            t = self._ts(w3, guess)
            if t < ts:
                lo = (guess, t)
            else:
                hi = (guess, t)
        return hi[0]

    # ------------------------------------------------------------------ run
    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            for spec in p.get("balance_flows") or []:
                try:
                    self._flow(p, spec, out)
                except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                    out.fail(SOURCE, p["name"], f"{spec['key']}: {e}", TIER)
                    out.gap(p["name"], spec["metric"], reason=f"the {spec['key']} balance-flow "
                            f"read failed: {e}", tiers_attempted="2",
                            suggestion="Run check_offline_items.py geod_archive_probe.")

    def _flow(self, p: dict, spec: dict, out) -> None:
        name, key, metric = p["name"], spec["key"], spec["metric"]
        chain, token = spec["chain"], spec["token"]
        holders = [h.lower() for h in spec["holders"]]
        excluded = [a.lower() for a in spec.get("exclude_counterparties") or []]
        chain_id = config.CHAIN_IDS[chain]
        deadline = None if self.unbounded else time.monotonic() + self.budget_s
        # HISTORICAL BALANCES NEED AN ARCHIVE ENDPOINT: the shared resolver (fetch.archive),
        # not the first endpoint that connects. A reader injected by a test keeps its own.
        if hasattr(self.reader, "_w3") and chain not in self.reader._w3:
            from .archive import resolve_archive
            w3r, host = resolve_archive(chain)
            if w3r is not None:
                self.reader._w3[chain] = w3r
                out.log.append(LogEntry(SOURCE, name, 0, "ok", f"{key}: archive reads via {host}", TIER))
        w3 = self.reader.web3(chain)
        head = int(w3.eth.block_number)
        st = self._load(key)
        erc = self.reader.erc20(chain, token)
        scale = 10 ** int(erc.functions.decimals().call())

        # 1. BOUNDARIES AND BALANCES, newest day first, saved after each.
        t_today = today()
        days = [t_today - pd.Timedelta(days=i) for i in range(int(spec.get("days", 365)) + 1)]
        known = [(int(b), int(pd.Timestamp(d).timestamp())) for d, b in st["boundary"].items()]
        filled, archive_err = 0, None
        prog = Progress(f"balance_flow {key}", total=len(days), unit="days", every_units=25,
                        status=lambda: f"{filled} day(s) read this run; {len(st['balance'])} day(s) held; "
                                       f"state saved after each day")
        for d in days:
            prog.tick()
            if deadline and time.monotonic() > deadline:
                break
            ds = str(d.date())
            if ds not in st["boundary"]:
                b = self._block_at(w3, int(d.timestamp()), head, known)
                st["boundary"][ds] = b
                known.append((b, int(d.timestamp())))
            if ds not in st["balance"]:
                b = int(st["boundary"][ds]) - 1
                try:
                    st["balance"][ds] = {h: str(int(erc.functions.balanceOf(
                        self.reader.checksum(h)).call(block_identifier=b))) for h in holders}
                except Exception as e:  # noqa: BLE001
                    archive_err = f"balanceOf at block {b:,} ({ds}) refused: {e}"
                    break
                filled += 1
            self._save(key, st)

        # 2. EVENTS: external inflows, and sends to excluded counterparties, up to today's boundary.
        to_block = int(st["boundary"].get(str(t_today.date()), 0)) - 1
        if to_block <= 0:
            out.gap(name, metric, reason=f"the {key} balance flow has no boundary for today yet",
                    tiers_attempted="2", suggestion="It fills on the next run.")
            return
        streams = [("in", h, [TRANSFER_TOPIC, None, pad_address(h)]) for h in holders]
        streams += [("burn", h, [TRANSFER_TOPIC, pad_address(h), pad_address(x)])
                    for h in holders for x in excluded]
        budget = getattr(self.explorer, "start_budget", None)
        if budget and not self.unbounded:
            budget(config.EXPLORER_SCAN_BUDGET_S)
        events = {"in": [], "burn": []}
        try:
            for way, h, topics in streams:
                sid = stream_id(chain_id, token, topics)
                c = self.cache.load(sid)
                start = 0 if c["scanned_to"] is None else int(c["scanned_to"]) + 1
                new = []
                if start <= to_block:
                    new, _ = self.explorer.get_logs(chain_id, token, topics, start, to_block)
                seen, merged = set(), []
                for e in list(c["events"]) + list(new):
                    k = (e["transactionHash"], int(e["logIndex"]))
                    if k not in seen:
                        seen.add(k)
                        merged.append(e)
                if start <= to_block:
                    self.cache.save(sid, merged, to_block, c.get("proven_to"))
                events[way] += [e for e in merged if int(e["blockNumber"]) <= to_block]
        except ExplorerRefused as e:
            out.fail(SOURCE, name, f"{key}: the inflow/burn-send events could not be read — {e}", TIER)
            out.gap(name, metric, reason=f"the {key} balance flow needs the inflow events and no "
                    f"explorer served them: {e}", tiers_attempted="2",
                    suggestion="Check ETHERSCAN_API_KEY / BLOCKSCOUT_API_KEY.")
            return
        finally:
            clear = getattr(self.explorer, "clear_budget", None)
            if clear:
                clear()

        # 3. PER DAY, IN WEI.
        dated = sorted((pd.Timestamp(ds), int(b)) for ds, b in st["boundary"].items())
        bounds = [b for _, b in dated]
        dates = [d for d, _ in dated]

        def day_of(block: int):
            i = bisect.bisect_right(bounds, block) - 1
            return dates[i] if 0 <= i < len(dates) - 1 and dates[i + 1] - dates[i] == pd.Timedelta(days=1) else None

        # ADDRESS-POISONING SPAM OUT (Jake's sign-off round, 2026-10-07: the filter applies everywhere): zero-value
        # inflows from a lookalike of a holder move nothing, but they are not inflows either
        from .logscan import drop_poison
        events["in"], _poisoned = drop_poison(events["in"], 1, holders)
        ext_in, burned = {}, {}
        for e in events["in"]:
            if topic_address(e["topics"][1]) in holders:
                continue                                   # internal hop
            d = day_of(int(e["blockNumber"]))
            if d is not None:
                ext_in[d] = ext_in.get(d, 0) + _amount(e)
        for e in events["burn"]:
            d = day_of(int(e["blockNumber"]))
            if d is not None:
                burned[d] = burned.get(d, 0) + _amount(e)
        rows, negative = [], []
        for d in dates:
            nxt = d + pd.Timedelta(days=1)
            ds, ns = str(d.date()), str(nxt.date())
            if ds not in st["balance"] or ns not in st["balance"]:
                continue
            start_bal = sum(int(v) for v in st["balance"][ds].values())
            end_bal = sum(int(v) for v in st["balance"][ns].values())
            ext_out = ext_in.get(d, 0) - (end_bal - start_bal)
            if ext_out < 0:
                negative.append(f"{ds} ({ext_out} wei)")
                continue
            rows.append((d, (ext_out - burned.get(d, 0)) / scale))
        if negative:
            out.fail(SOURCE, name, f"{key}: external outflow below zero on {len(negative)} day(s): "
                                   f"{', '.join(negative[:5])}. NOTHING STORED.", TIER)
            out.gap(name, metric,
                    reason=(f"the {key} balance flow does not add up: on {', '.join(negative[:5])} "
                            f"the wallets' balance rose by more than every external inflow the "
                            f"explorer returned — an inflow event is missing. Nothing stored."),
                    tiers_attempted="2",
                    suggestion="Re-run once; a second failure is real and is NOT to be absorbed "
                               "by a tolerance.")
            return
        if archive_err and not rows:
            out.fail(SOURCE, name, f"{key}: {archive_err}", TIER)
            out.gap(name, metric, reason=(f"the {key} balance flow needs historical balances and "
                                          f"the {chain} node refused: {archive_err}"),
                    tiers_attempted="2",
                    suggestion=f"Set {chain.upper()}_RPC_URL to an archive endpoint (Alchemy's "
                               f"free tier serves Polygon archive), then run "
                               f"check_offline_items.py geod_archive_probe.")
            return
        if not rows:
            out.gap(name, metric, reason=f"the {key} balance flow has no complete day yet "
                    f"({len(st['balance'])} balance(s) read)", tiers_attempted="2",
                    suggestion="It fills as runs read more boundaries; --seed geodnet does it in one go.")
            return
        frame = tidy(rows, name, metric, f"{SOURCE}:{key}", TIER)
        wk = sum(v for d, v in rows if d > t_today - pd.Timedelta(days=8))
        # THE RECONCILIATION LINE (2026-09-29): release over 30d / 365d beside the combined
        # balance fall and the outside inflow over the same days, and the seed reference if any.
        recon = []
        for n in (30, 365):
            since = t_today - pd.Timedelta(days=n)
            ds = str(since.date())
            if ds in st["balance"] and str(t_today.date()) in st["balance"]:
                fall = (sum(int(v) for v in st["balance"][ds].values())
                        - sum(int(v) for v in st["balance"][str(t_today.date())].values())) / scale
                rel = sum(v for d, v in rows if d >= since)
                inflow = sum(v for d, v in ext_in.items() if d >= since) / scale
                ref = ((spec.get("seed_reference") or {}).get("implied_release_if_no_inflow") or {}).get(f"{n}d")
                recon.append(f"{n}d release {rel:,.0f} = balance fall {fall:,.0f} + outside inflow "
                             f"{inflow:,.0f} - burn sends" + (f" (seed reference {ref:,.0f})" if ref else ""))
        msg = (f"{metric} = {key}: external inflow - d(balance) - sends to "
               f"{len(excluded)} excluded address(es), in wei, {len(rows)} day(s) "
               f"{rows[0][0].date()}..{rows[-1][0].date()}; last 7 days {wk:,.0f}; {filled} "
               f"balance(s) read this run, {len(st['balance'])} held"
               + (f"; STOPPED: {archive_err}" if archive_err else "")
               + (f". {'; '.join(recon)}" if recon else ""))
        out.add(frame, SOURCE, name, msg, TIER)
        out.log.append(LogEntry(SOURCE, name, 0, "ok", msg, TIER))
