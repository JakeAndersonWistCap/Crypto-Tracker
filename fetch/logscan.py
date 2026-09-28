"""
fetch/logscan.py — tier 2: token FLOWS into or out of named holders, from Transfer events.

** THE QUANTITY A BALANCE CANNOT GIVE. ** A buyback wallet exists to spend, so its differenced
balance is inflow MINUS spending and understates the buyback by the buyback. A mining wallet is
topped up and paid out, so its balance delta is negative on top-up days. The flow is only in the
Transfer events, one direction at a time — and those were unreadable while every Ethereum log
scan went through an RPC capped at 10 blocks per request. The explorer APIs (fetch/explorer.py)
page by record count, which is what makes this module possible.

** EVERY SCAN MUST RECONCILE TO THE WEI BEFORE ANYTHING IS STORED. ** For each holder:

        sum(every Transfer IN) - sum(every Transfer OUT)  ==  balanceOf(holder)

at one pinned block. ERC-20 balances move only by Transfer events (a mint is a Transfer from
address(0)), so the identity is exact for a standard token, and it is the one check that proves
the record-count pagination returned EVERYTHING. A dropped page, an explorer that indexed up to an
earlier block than claimed, or a token with a non-standard balance all show up here as a
non-zero difference. The balance is read from the RPC AT THE SAME BLOCK the scan stops at
(head minus a confirmation margin, well inside a non-archive node's retained state), so no race
between the two reads can produce a false difference. NO TOLERANCE: a difference of one wei
refuses the scan and says by how much.

** WHAT COUNTS IS DECLARED, NOT INFERRED. ** Reconciliation proves the scan is COMPLETE; it
cannot say which inflows are a buyback. Each scan declares its attribution with a source:
  count_from           only transfers from these senders count (Chainlink: the Payment
                       Abstraction layer, as DefiLlama's own adapter counts it)
  dedicated_wallet     the holder exists only for this purpose, so every inflow counts except a
                       mint (Ether.fi's buyback wallet, named by three sources)
  none established     the scan runs and reconciles, the counterparty table is printed, and
                       NOTHING is stored (Maple's treasury receives SYRUP for other reasons too)
Transfers between the scan's own holders are internal hops and never count in either direction.

THE SERIES IS DAILY AND ITS ZEROS ARE MEASURED. The scan covers every block from the holder's
first activity to the pinned block, so a day with no counted transfer is an observed zero, not a
missing reading — every day in that span gets a row. Today is left out: it is not over.
"""
from __future__ import annotations

import logging
from collections import defaultdict

import pandas as pd

import config
from .base import LogEntry, tidy, today, window
from .logcache import LogCache, stream_id
from .explorer import ExplorerLogs, ExplorerRefused, ExplorerTimeout, TRANSFER_TOPIC, pad_address, topic_address

log = logging.getLogger("token_metrics.fetch.logscan")

SOURCE = "explorer"
TIER = 2
CONFIRMATIONS = 20       # blocks behind the head the scan stops at; also where balanceOf is read
MINT_SENDERS = {config.BURN_ADDRESSES["zero"].lower()}


def _amount(entry: dict) -> int:
    """Transfer's value is the DATA word — from and to are the indexed topics."""
    data = entry.get("data") or "0x0"
    return int(str(data), 16)


def _merge(cached: list, new: list) -> list:
    """Cached + new, one copy of each log by (transaction, logIndex), in chain order."""
    seen, merged = set(), []
    for e in list(cached) + list(new):
        k = (e["transactionHash"], int(e["logIndex"]))
        if k not in seen:
            seen.add(k)
            merged.append(e)
    merged.sort(key=lambda e: (int(e["blockNumber"]), int(e["logIndex"])))
    return merged


class LogScan:
    """Runs every project's `log_scans`, each against one token and one or more holders."""

    def __init__(self, explorer: ExplorerLogs | None = None, reader=None,
                 cache: LogCache | None = None, unbounded: bool = False):
        # unbounded: no per-scan budget — `token_metrics.py --seed geodnet` (2026-09-28) finishes
        # a first read in one sitting instead of 120s a run.
        self.unbounded = unbounded
        self.explorer = explorer or ExplorerLogs()
        self.cache = cache or LogCache()
        if reader is None:
            from .chain import ChainReader
            reader = ChainReader()
        self.reader = reader

    def _save_progress(self, streams, e, to_block) -> str:
        """Out of budget mid-scan: keep what was read, so the seed resumes. Nothing here is
        proven — proven_to is left where it was."""
        done = []
        dated = lambda evs: all(int(x.get("timeStamp") or 0) > 0 for x in evs)  # noqa: E731
        for sid, st, new, _, _ in streams:
            if new and dated(new):
                self.cache.save(sid, _merge(st["events"], new), to_block, st["proven_to"])
                done.append(len(new))
        cur = getattr(e, "stream", None)
        partial = getattr(e, "partial", None)
        if cur and partial and dated(partial):
            sid, st, start = cur
            upto = int(e.resume_from) - 1
            if upto >= start:
                self.cache.save(sid, _merge(st["events"], partial), upto, st["proven_to"])
                return (f"{len(streams)} stream(s) read in full and {len(partial):,} event(s) of "
                        f"the next cached, to block {upto:,} of {to_block:,} "
                        f"({upto / max(to_block, 1):.0%} of the chain); the next run resumes there")
        if done:
            return (f"{len(done)} stream(s) read in full and cached; the next run resumes at the "
                    f"stream that ran out")
        return ""

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            for spec in p.get("log_scans") or []:
                self._scan(p, spec, window_days, out)

    # ------------------------------------------------------------------ one scan
    def _scan(self, p: dict, spec: dict, window_days, out) -> None:
        name, key, metric = p["name"], spec["key"], spec["metric"]
        chain, token = spec["chain"], spec["token"]
        holders = [h.lower() for h in spec["holders"]]
        direction = spec["direction"]
        chain_id = config.CHAIN_IDS.get(chain)
        if chain_id is None:
            out.unconfigured(SOURCE, name, f"{key}: no chain id for {chain!r} in config.CHAIN_IDS", TIER)
            return
        if not self.explorer.configured(chain_id):
            routed = config.explorer_order(chain_id)
            why = (f"no explorer serves logs free on {chain} (chain {chain_id}) per "
                   f"EXPLORER_LOG_ROUTES" if not routed else
                   f"none of {', '.join(routed)} has its key in .env "
                   f"({', '.join(config.EXPLORERS[n]['key_env'] for n in routed)})")
            out.unconfigured(SOURCE, name, f"{key}: {why}", TIER)
            out.gap(name, metric,
                    reason=f"the {key} Transfer-event scan cannot run: {why}. Deliberately NOT "
                           f"replaced by a balance read — a differenced balance nets out the "
                           f"very flow this measures.",
                    tiers_attempted="2",
                    suggestion="Set the explorer key in .env (see .env.example).")
            return

        # 1. PIN THE UPPER BLOCK. Everything below is read at or before it.
        try:
            head = int(self.reader.web3(chain).eth.block_number)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{key}: no RPC answered eth_blockNumber on {chain}: {e}", TIER)
            out.gap(name, metric, reason=f"the {key} scan needs a pinned block from a {chain} RPC "
                                         f"and none answered: {e}",
                    tiers_attempted="2", suggestion=f"Check the {chain} RPC endpoints.")
            return
        to_block = head - CONFIRMATIONS

        # 2. BOTH DIRECTIONS FOR EVERY HOLDER — reconciliation needs both even when one counts.
        # INCREMENTAL (2026-09-28, fetch/logcache.py): each stream's events already read are
        # loaded from the cache and only blocks after its `scanned_to` are asked for. The first
        # run seeds from block 0; a seed too big for the budget is saved as far as it got and
        # resumed next run. Reconciliation below still covers the WHOLE history, cached + new.
        ins, outs, served, requests, refused = {}, {}, set(), 0, []
        streams, fetched_new = [], 0     # (sid, cached state, new events, holder, direction)
        # ONE 120s budget for the whole scan, both providers included (config.EXPLORER_SCAN_BUDGET_S).
        budget = getattr(self.explorer, "start_budget", None)
        if budget and not self.unbounded:
            budget(config.EXPLORER_SCAN_BUDGET_S)
        try:
            for h in holders:
                for way, topics in (("in", [TRANSFER_TOPIC, None, pad_address(h)]),
                                    ("out", [TRANSFER_TOPIC, pad_address(h), None])):
                    sid = stream_id(chain_id, token, topics)
                    st = self.cache.load(sid)
                    start = 0 if st["scanned_to"] is None else int(st["scanned_to"]) + 1
                    if start > to_block:
                        new, meta = [], {"explorer": "cache", "requests": 0, "refused": []}
                    else:
                        try:
                            new, meta = self.explorer.get_logs(chain_id, token, topics, start, to_block)
                        except ExplorerTimeout as e:
                            e.stream = (sid, st, start)
                            raise
                    streams.append((sid, st, new, h, way))
                    fetched_new += len(new)
                    events = _merge(st["events"], new)
                    (ins if way == "in" else outs)[h] = events
                    if meta["explorer"] != "cache":
                        served.add(meta["explorer"])
                    requests += meta["requests"]
                    refused += meta["refused"]
        except ExplorerTimeout as e:
            seeded = self._save_progress(streams, e, to_block)
            out.fail(SOURCE, name, f"{key}: {e}" + (f" — {seeded}" if seeded else ""), TIER)
            out.gap(name, metric,
                    reason=str(e) + (f". SEEDING, RESUMABLE: {seeded}" if seeded else ""),
                    tiers_attempted="2",
                    suggestion=("The first run reads full history and may need several runs "
                                "inside the budget; each resumes where the last stopped "
                                "(fetch/logcache.py). Nothing is stored until the whole history "
                                "reconciles." if seeded else
                                "The providers' own errors are above. Re-runs are cheap now — "
                                "the scan stops at the budget instead of retrying for most of "
                                "an hour."))
            return
        except ExplorerRefused as e:
            out.fail(SOURCE, name, f"{key}: no explorer served the scan — {e}", TIER)
            out.gap(name, metric,
                    reason=f"the {key} Transfer-event scan was REFUSED by every explorer routed "
                           f"for {chain}: {e}. Not replaced by a balance read.",
                    tiers_attempted="2",
                    suggestion="Read the explorer's own message above. If it says logs are not "
                               "served free on this chain, correct EXPLORER_LOG_ROUTES — the "
                               "coverage table is researched, not confirmed.")
            return
        clear = getattr(self.explorer, "clear_budget", None)
        if clear:
            clear()
        via = "+".join(sorted(served)) or "cache only"
        cached_n = sum(len(st["events"]) for _, st, _, _, _ in streams)
        resumed = [st["scanned_to"] for _, st, _, _, _ in streams if st["scanned_to"] is not None]
        increment = (f"incremental: {cached_n:,} cached event(s) to block {min(resumed):,}, "
                     f"{fetched_new:,} new to block {to_block:,}"
                     if len(resumed) == len(streams) else
                     f"full history read ({fetched_new:,} event(s)) and cached"
                     if not resumed else
                     f"seed completed: {cached_n:,} cached + {fetched_new:,} new event(s)")

        # 3. RECONCILE, PER HOLDER, TO THE WEI.
        for h in holders:
            net = sum(_amount(e) for e in ins[h]) - sum(_amount(e) for e in outs[h])
            try:
                bal = int(self.reader.erc20(chain, token).functions.balanceOf(
                    self.reader.checksum(h)).call(block_identifier=to_block))
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"{key}: balanceOf({h}) at block {to_block} failed: {e}", TIER)
                out.gap(name, metric,
                        reason=f"the {key} scan could not be RECONCILED: balanceOf({h}) at block "
                               f"{to_block:,} did not answer ({e}), and an unreconciled scan is "
                               f"not stored.",
                        tiers_attempted="2", suggestion=f"Check the {chain} RPC.")
                return
            if net != bal:
                out.fail(SOURCE, name,
                         f"{key}: SCAN DOES NOT RECONCILE for {h} at block {to_block:,}: in-out = "
                         f"{net} wei, balanceOf = {bal} wei, difference {bal - net} wei. NOTHING "
                         f"STORED. Served by {via} in {requests} request(s).", TIER)
                out.gap(name, metric,
                        reason=(f"the {key} scan is INCOMPLETE OR WRONG and was not stored: for "
                                f"{h}, sum(in) - sum(out) = {net} wei but balanceOf at the same "
                                f"block ({to_block:,}) = {bal} wei — {bal - net} wei unaccounted "
                                f"for. Served by {via}."),
                        tiers_attempted="2",
                        suggestion=("A difference means a page was dropped, the explorer had not "
                                    "indexed to the pinned block, or the token moves balances "
                                    "without Transfer events. Re-run once; a second difference is "
                                    "real and is NOT to be absorbed by a tolerance."))
                # THE CACHE IS NOT ADVANCED, and anything in it no reconciliation has vouched for
                # is dropped, so an unproven segment cannot keep this scan failing.
                for sid, _, _, _, _ in streams:
                    log.info("%s/%s: logcache %s", name, key,
                             self.cache.cut_back(sid, lambda e: int(e["blockNumber"])))
                return

        # RECONCILED: every stream is proven to the pinned block. Saved before attribution, which
        # reads the events but does not change them.
        # A stream holding an UNDATED event is not cached: step 5b refuses a counted one, and a
        # cached copy would refuse every later run too. It is re-read until the source dates it.
        for sid, st, new, h, way in streams:
            events = (ins if way == "in" else outs)[h]
            if all(int(e.get("timeStamp") or 0) > 0 for e in events):
                self.cache.save(sid, events, to_block, to_block)

        # 4. WHAT COUNTS.
        internal = set(holders)
        excluded = {a.lower() for a in spec.get("exclude_counterparties") or []}
        count_from = {a.lower() for a in spec["count_from"]} if spec.get("count_from") else None
        counted, uncounted = [], defaultdict(int)
        if direction == "in":
            for h in holders:
                for e in ins[h]:
                    frm = topic_address(e["topics"][1])
                    if frm in internal:
                        continue
                    if frm in excluded or frm in MINT_SENDERS or (count_from is not None and frm not in count_from):
                        uncounted[frm] += _amount(e)
                    else:
                        counted.append(e)
        else:
            for h in holders:
                for e in outs[h]:
                    to = topic_address(e["topics"][2])
                    if to in internal:
                        continue
                    if to in excluded:
                        uncounted[to] += _amount(e)
                    else:
                        counted.append(e)
        try:
            dec = int(self.reader.erc20(chain, token).functions.decimals().call())
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{key}: decimals() failed: {e}", TIER)
            return
        scale = 10 ** dec
        c_total = sum(_amount(e) for e in counted) / scale
        # LABELLED where config names the sender (2026-09-28): an excluded inflow is recorded as
        # "other inflow, not counted" with what it is, not left as a bare address.
        labels = {a.lower(): l for a, l in (spec.get("not_counted_labels") or {}).items()}
        u_table = ", ".join(f"{a} {v / scale:,.2f}" + (f" [{labels[a]}]" if a in labels else "")
                            for a, v in sorted(uncounted.items(), key=lambda kv: -kv[1])[:8]) or "none"
        by_party = defaultdict(int)
        for e in counted:
            party = topic_address(e["topics"][1 if direction == "in" else 2])
            by_party[party] += _amount(e)
        c_table = ", ".join(f"{a} {v / scale:,.2f}" for a, v in
                            sorted(by_party.items(), key=lambda kv: -kv[1])[:8]) or "none"
        # WHEN THE COUNTED FLOW LAST MOVED, so a zero in the trailing window can be told apart
        # from a scan that put its transfers somewhere else. Ether.fi, 2026-09-24: 18,982,711.90
        # ETHFI counted and a 30-day cell of 0 — which is either "nothing arrived in 30 days" or a
        # dating fault, and the log gave no way to say which.
        dated = [e["timeStamp"] for e in counted if int(e.get("timeStamp") or 0) > 0]
        last_moved = (pd.Timestamp(max(dated), unit="s").date().isoformat() if dated else "never")
        summary = (f"{key}: RECONCILED to the wei for {len(holders)} holder(s) at block "
                   f"{to_block:,}; served by {via} in {requests} request(s), {increment}"
                   + (f" after refusal(s): {'; '.join(refused)}" if refused else "")
                   + f". Counted {direction}flow {c_total:,.4f} over {len(counted)} transfer(s), "
                     f"last on {last_moved} — top counterparties: {c_table}. Other inflow, not counted: "
                     f"{u_table}.")
        out.log.append(LogEntry(SOURCE, name, 0, "ok", summary, TIER))
        log.info("%s/%s", name, summary)

        # 5. THE ATTRIBUTION GATE.
        if not spec.get("store"):
            out.gap(name, metric,
                    reason=(f"THE SCAN WORKS AND RECONCILES; ATTRIBUTION IS NOT ESTABLISHED, so "
                            f"nothing is stored. {spec.get('hold_reason', '')} This run's "
                            f"counterparties: {c_table}."),
                    tiers_attempted="2",
                    suggestion=spec.get("hold_suggestion") or
                    "Declare count_from (with a source) for the senders that are purchases.")
            return

        # 5b. A TRANSFER THAT CANNOT BE DATED IS NOT STORED AS A ZERO ELSEWHERE.
        # The daily series below buckets by timeStamp and zero-fills every other day. An event
        # with no timestamp (0 after normalise) would land on 1970-01-01, outside any window, and
        # leave the window reading a confident 0 while the total sat in the log — the exact
        # "0 means nothing happened" misreading. Refused and gapped instead, with the count.
        undated = [e for e in counted if int(e.get("timeStamp") or 0) <= 0]
        if undated:
            out.fail(SOURCE, name, f"{key}: {len(undated)} counted transfer(s) carry no "
                                   f"timestamp — nothing stored. {summary}", TIER)
            out.gap(name, metric,
                    reason=(f"the {key} scan reconciles but {len(undated)} of {len(counted)} "
                            f"counted transfer(s) came back with no timestamp from {via}, so "
                            f"they cannot be put on a day. Stored nothing rather than a daily "
                            f"series whose zeros would stand in for them."),
                    tiers_attempted="2",
                    suggestion="Check the explorer response's timeStamp field for these logs.")
            return

        # 6. THE DAILY SERIES, ZEROS INCLUDED.
        first = min((e["timeStamp"] for h in holders for e in ins[h] + outs[h]), default=None)
        if first is None:
            out.gap(name, metric, reason=f"the {key} holders have no Transfer history at all",
                    tiers_attempted="2", suggestion="Check the holder addresses.")
            return
        by_day = defaultdict(float)
        for e in counted:
            by_day[pd.Timestamp(e["timeStamp"], unit="s").normalize()] += _amount(e) / scale
        start = pd.Timestamp(first, unit="s").normalize()
        if spec.get("from_date"):
            start = max(start, pd.Timestamp(spec["from_date"]))
        last = today() - pd.Timedelta(days=1)
        days = pd.date_range(start, last, freq="D")
        frame = tidy([(d, by_day.get(d, 0.0)) for d in days], name, metric,
                     f"{SOURCE}:{key}", TIER)
        # THE WHOLE RECONCILED SERIES, EVERY RUN (2026-09-28). Storing only the run's window left
        # days outside it as an EARLIER run counted them: Ether.fi's 2026-06-30 inflow was stored
        # before attribution became CoW-only and stayed in the store (and in Q0, Y1 and the
        # silence date) after the scan stopped counting it. The scan covers every block anyway,
        # so re-writing every day is an idempotent upsert that keeps history on today's rules.
        # ** SAY WHICH DATE IS WHICH. Corrected 2026-09-28. ** "30 daily row(s) from 2025-05-09"
        # read as thirty rows starting then. It meant: the series STARTS at the holders' first
        # Transfer in EITHER direction (not necessarily an inflow, and not a counted one), and
        # the run STORED only its window. Both are now named, with the first counted inflow.
        first_in = min((e["timeStamp"] for e in counted if int(e.get("timeStamp") or 0) > 0),
                       default=None)
        first_in_s = (pd.Timestamp(first_in, unit="s").date().isoformat() if first_in else "none")
        stored = (f"{frame['date'].min().date()}..{frame['date'].max().date()}"
                  if not frame.empty else "none")
        out.add(frame, SOURCE, name,
                f"{metric} = {key}: series starts {start.date()} (the holders' first Transfer in "
                f"either direction" + (", clamped to from_date" if spec.get("from_date") else "")
                + f"; first counted inflow {first_in_s}), zero-filled daily to {last.date()}; "
                f"this run STORED {len(frame)} daily row(s) covering {stored}"
                + " (the full reconciled series, every run)"
                + f". Zeros are observed — the scan covers every block. {summary}", TIER)

        # THE LAST COUNTED INFLOW, STORED AS A VALUE. Added 2026-09-28. A window of zeros says
        # nothing about WHEN the program last moved; the run log did, as prose. Excel date serial
        # (days since 1899-12-30), dated the run day, for buyback scans only.
        if metric == "actual_buyback_tokens" and dated:
            last_day = pd.Timestamp(max(dated), unit="s").normalize()
            serial = float((last_day - pd.Timestamp("1899-12-30")).days)
            out.add(tidy([(today(), serial)], name, "buyback_last_inflow_date",
                         f"{SOURCE}:{key}", TIER), SOURCE, name,
                    f"buyback_last_inflow_date = {last_day.date()} (serial {serial:.0f}) from "
                    f"{key}", TIER)
