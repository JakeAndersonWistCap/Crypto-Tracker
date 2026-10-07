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
  count_mints          with count_from naming the zero address: MINTS to the holder count (Sky's USDS
                       farm, 2026-10-07 — the Splitter pays it through UsdsJoin.exit, which MINTS USDS
                       straight to the farm; no other path mints to it). Off by default.
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


def hexint(value) -> int:
    """A hex word as an int — "0x" / "" (an EMPTY answer: a zero-value Transfer some tokens emit with no data, a
    reverted or missing call) is NO VALUE, read as 0, never a crash (Jake's run 2026-10-06 18:21: "invalid literal
    for int() with base 16: '0x'" killed the whole explorer tier)."""
    s = str(value if value is not None else "").strip()
    if s in ("", "0x", "0X"):
        return 0
    return int(s, 16)


def _tail_net(explorer, chain_id: int, token: str, holder: str, after_block: int) -> int:
    """in - out for `holder` in the blocks after `after_block`, from the explorer's logs."""
    got = 0
    for sign, topics in ((1, [TRANSFER_TOPIC, None, pad_address(holder)]), (-1, [TRANSFER_TOPIC, pad_address(holder)])):
        logs, _meta = explorer.get_logs(chain_id, token, topics, int(after_block) + 1, "latest")
        got += sign * sum(_amount(e) for e in logs)
    return got


def _amount(entry: dict) -> int:
    """Transfer's value is the DATA word — from and to are the indexed topics."""
    return hexint(entry.get("data"))


def _empty_data(entry: dict) -> bool:
    return str(entry.get("data") if entry.get("data") is not None else "").strip() in ("", "0x", "0X")


def _lookalike(a: str, b: str) -> bool:
    """`a` MIMICS `b`: a different address sharing its first or last four hex digits — what address-poisoning
    vanity addresses are made to do (0x3fb667…, 0x3fb625…, 0x3fb67c… against 0x3fb6…7b78)."""
    a, b = a.lower(), b.lower()
    return a != b and (a[2:6] == b[2:6] or a[-4:] == b[-4:])


def drop_poison(events: list, side: int, known) -> tuple[list, int]:
    """(events, n dropped): a ZERO-VALUE transfer whose counterparty (topic `side`) mimics a known address is
    address-poisoning spam (Jake's etherfi_topup_safe probe, 2026-10-06 17:20) — dropped from every scan and probe.
    It moves no tokens, so no sum changes; it only pollutes the counterparty tables."""
    known = [k.lower() for k in known]
    kept, n = [], 0
    for e in events:
        if _amount(e) == 0:
            cp = topic_address(e["topics"][side])
            if any(_lookalike(cp, k) for k in known):
                n += 1
                continue
        kept.append(e)
    return kept, n


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
                # ONE SCAN'S FAILURE NEVER STOPS THE TIER (Jake's run 2026-10-06 18:21: one bad log crashed the
                # adapter and no later scan ran).
                try:
                    self._scan(p, spec, window_days, out)
                except ExplorerTimeout:
                    raise
                except Exception as e:  # noqa: BLE001
                    from .chain import redact_urls
                    out.fail(SOURCE, p["name"], f"{spec.get('key')}: scan crashed — {type(e).__name__}: "
                                                f"{redact_urls(e)}. The other scans ran.", TIER)
                    log.exception("%s/%s crashed", p["name"], spec.get("key"))

    def _tx_events(self, chain_id: int, rq: dict, to_block: int, topics: list | None = None) -> list:
        """Every log of rq's event on rq's address up to to_block, incremental through the cache."""
        topics = topics or [rq["topic0"]]
        sid = stream_id(chain_id, rq["address"], topics)
        st = self.cache.load(sid)
        start = 0 if st["scanned_to"] is None else int(st["scanned_to"]) + 1
        new = []
        if start <= to_block:
            new, _ = self.explorer.get_logs(chain_id, rq["address"], topics, start, to_block)
        events = _merge(st["events"], new)
        if start <= to_block:
            self.cache.save(sid, events, to_block, st.get("proven_to"))
        return [e for e in events if int(e["blockNumber"]) <= to_block]

    def _decompose(self, p, spec, dec, sm, chain_id, vault, ins, outs, to_block, labels, days, out) -> None:
        """Q0 change in a share vault's assets-per-share, by transaction shape; stores the reconciled reward
        tokens per day (dec["metric"]) — top-ups of every kind + fees and burns left with the holders."""
        from .share_decompose import CLASSES, decompose
        name, key = p["name"], spec["key"]
        zero = pad_address(config.BURN_ADDRESSES["zero"])
        try:
            mints = self._tx_events(chain_id, {"address": sm["token"]}, to_block, [TRANSFER_TOPIC, zero])
            burns = self._tx_events(chain_id, {"address": sm["token"]}, to_block, [TRANSFER_TOPIC, None, zero])
        except ExplorerRefused as e:
            out.fail(SOURCE, name, f"{key}: share burns could not be read for the decomposition — {e}", TIER)
            return
        since = today() - pd.Timedelta(days=int(dec.get("window_days", 90)))
        r = decompose(ins[vault], outs[vault], mints, burns, int(since.timestamp()), set(labels))
        if r["aps_end"] is None or not r["aps_start"]:
            out.fail(SOURCE, name, f"{key}: no shares outstanding — nothing to decompose", TIER)
            return
        by, a0, a1 = r["by_class"], r["aps_start"], r["aps_end"]
        parts = "; ".join(f"{c} {by[c]['aps']:+.6f}/share = {by[c]['tokens']:+,.2f} tokens ({by[c]['txs']} tx)"
                          for c in CLASSES if by[c]["txs"])
        span = max(int(dec.get("window_days", 90)), 1)
        # ONLY THE DAYS INSIDE THE WINDOW (2026-10-07): a day before `since` was not decomposed, so it is not a 0.
        frame = tidy([(d, r["daily"].get(d, 0.0)) for d in days if d >= since.normalize()], name, dec["metric"],
                     f"{SOURCE}:{key}.aps_walk", TIER)
        out.add(frame, SOURCE, name,
                f"{dec['metric']} — assets-per-share {a0:.6f} -> {a1:.6f} since {since.date()} "
                f"({a1 / a0 - 1:+.3%}; {(a1 / a0) ** (365.0 / span) - 1:.2%}/yr), rebuilt from the Transfer logs "
                f"(assets {r['assets_now']:,.2f}, shares {r['shares_now']:,.2f} at block {to_block:,}). BY CLASS: "
                f"{parts}. The classes sum to the change exactly; the reconciled reward tokens per day are stored.",
                TIER)
        # THE REBUILT ASSETS-PER-SHARE, daily (Jake's probes14 2026-10-07: the 365-day reference must be the year's own
        # growth, 0.940728 -> 1.246158, not the ~89-day archive read annualised): each day's close, carried forward
        # over days without a transaction, from the window's first day.
        if dec.get("aps_metric"):
            eod, rows, cur = r.get("aps_eod") or {}, [], a0
            for d in days:
                if d < since.normalize():
                    continue
                cur = eod.get(d, cur)
                rows.append((d, cur))
            out.add(tidy(rows, name, dec["aps_metric"], f"{SOURCE}:{key}.aps_walk", TIER), SOURCE, name,
                    f"{dec['aps_metric']} — assets-per-share rebuilt from the Transfer logs, daily since {since.date()}",
                    TIER)

    def _classify_outside(self, chain_id: int, cls: dict, outside: list, holders: list,
                          to_block: int, spec: dict) -> str:
        """WHAT the transfers outside the named event are, from the pair's own logs (Sky, 2026-09-29).

        990,122,323.69 SKY reached the Pause Proxy from the USDS/SKY pair outside any flapper Exec.
        Sky's source rules out the earlier flapper on this pair (0xc5A9..., 2024-09-13..09-27:
        add-liquidity, so its SKY never reached the Pause Proxy) and finds no spell that swaps or
        removes liquidity to it. The pair's own events name each transaction: Swap(sender, ..., to)
        or Burn(sender, amount0, amount1, to) with `to` = the holder, read by topic so only those
        are fetched. Totals over the whole history, the last 90 and the last 365 days. Nothing is
        counted from this: it is the identification Jake asked for, in the log.
        """
        from eth_utils import keccak
        pair = cls["pair"]
        to_topics = [pad_address(h) for h in holders]
        kinds = {}
        for label, sig in (("swap", "Swap(address,uint256,uint256,uint256,uint256,address)"),
                           ("burn", "Burn(address,uint256,uint256,address)")):
            t0 = "0x" + keccak(text=sig).hex()
            for tt in to_topics:
                try:
                    for e in self._tx_events(chain_id, {"address": pair}, to_block, [t0, None, tt]):
                        kinds[str(e["transactionHash"]).lower()] = (label, topic_address(e["topics"][1]))
                except ExplorerRefused as ex:
                    return f" OUTSIDE {len(outside)} transfer(s): pair {label} logs unreadable ({ex})."
        flappers = {a.lower(): n for a, n in (cls.get("known_senders") or {}).items()}
        verdicts = cls.get("verdicts") or {}
        now = int(pd.Timestamp.now("UTC").timestamp())
        agg: dict = defaultdict(lambda: [0, 0, 0, 0, ""])      # total, 90d, 365d, n, verdict
        txs = []
        for e in outside:
            kind, sender = kinds.get(str(e["transactionHash"]).lower(), ("none", ""))
            what = {"burn": "LP burn to the holder (pair.burn)",
                    "swap": f"swap paying the holder, sender {sender}"
                            + (f" [{flappers[sender]}]" if sender in flappers else ""),
                    "none": "no Swap/Burn to the holder in the tx (skim/sync or direct transfer)"}[kind]
            vkey = kind if kind != "swap" else ("swap_known" if sender in flappers else "swap_unknown")
            a, age = _amount(e), now - int(e.get("timeStamp") or 0)
            row = agg[what]
            row[0] += a
            row[1] += a if age <= 90 * 86_400 else 0
            row[2] += a if age <= 365 * 86_400 else 0
            row[3] += 1
            row[4] = verdicts.get(vkey, "")
            txs.append((a, int(e.get("timeStamp") or 0), str(e["transactionHash"]), kind))
        scale = 10 ** int(cls.get("decimals", 18))
        parts = [f"{w}: {v[0] / scale:,.2f} ({v[3]} tx; last 90d {v[1] / scale:,.2f}, last 365d "
                 f"{v[2] / scale:,.2f})" + (f" => {v[4]}" if v[4] else "")
                 for w, v in sorted(agg.items(), key=lambda kv: -kv[1][0])]
        # THE TRANSACTIONS THEMSELVES (2026-09-29): the largest, so a reader can open each one.
        top = sorted(txs, reverse=True)[:int(cls.get("show_largest", 0))]
        largest = ("; largest: " + ", ".join(
            f"{h} {pd.Timestamp(ts, unit='s').date() if ts else '?'} {k} {a / scale:,.2f}"
            for a, ts, h, k in top)) if top else ""
        return " OUTSIDE THE EVENT, BY THE PAIR'S OWN LOGS — " + "; ".join(parts) + largest + "."

    @staticmethod
    def _since_note(spec: dict, counted: list, labels: dict, scale: int) -> str:
        """Counted transfers ON OR AFTER spec["report_since"], by sender with first/last date — the window a
        reader asked about (Ether.fi: sETHFI top-ups since the new programme passed, 2026-09-03)."""
        since = spec.get("report_since")
        if not since:
            return ""
        t0 = int(pd.Timestamp(since, tz="UTC").timestamp())
        agg = defaultdict(lambda: [0, 0, 10 ** 12, 0])           # amount, n, first, last
        for e in counted:
            t = int(e.get("timeStamp") or 0)
            if t < t0:
                continue
            r = agg[topic_address(e["topics"][1])]
            r[0] += _amount(e)
            r[1] += 1
            r[2], r[3] = min(r[2], t), max(r[3], t)
        tot = sum(v[0] for v in agg.values())
        rows = "; ".join(f"{a} {v[0] / scale:,.2f} over {v[1]} transfer(s) {pd.Timestamp(v[2], unit='s').date()}.."
                         f"{pd.Timestamp(v[3], unit='s').date()}" + (f" [{labels[a]}]" if a in labels else "")
                         for a, v in sorted(agg.items(), key=lambda kv: -kv[1][0])[:8])
        return f" SINCE {since}: counted {tot / scale:,.2f}" + (f" — {rows}." if rows else " (none).")

    # ------------------------------------------------------------------ one scan
    def _balance_at(self, chain: str, chain_id: int, token: str, holder: str, block: int) -> int:
        """balanceOf(holder) at `block` for the reconciliation (Jake's run 2026-10-07). First the archive read, through
        every endpoint (<CHAIN>_RPC_URL first). If EVERY endpoint refuses past-block state, LOGS STAND IN FOR IT:
        balanceOf at the latest block minus the holder's Transfer net after `block` (explorer logs) — the same
        integer, read from the tip. A transfer landing between the two reads makes it differ, and the scan then
        reports a non-reconciliation rather than storing anything."""
        if hasattr(self.reader, "erc20_balance_at"):
            try:
                return self.reader.erc20_balance_at(chain, token, holder, block)
            except Exception as e:  # noqa: BLE001
                archive_err = e
        else:
            try:
                return int(self.reader.erc20(chain, token).functions.balanceOf(
                    self.reader.checksum(holder)).call(block_identifier=block))
            except Exception as e:  # noqa: BLE001
                archive_err = e
        try:
            latest = int(self.reader.erc20(chain, token).functions.balanceOf(
                self.reader.checksum(holder)).call(block_identifier="latest"))
            bal = latest - _tail_net(self.explorer, chain_id, token, holder, block)
        except Exception as e2:  # noqa: BLE001
            raise RuntimeError(f"{archive_err}; and the latest-balance-minus-logged-tail route failed too: {e2}") \
                from e2
        log.info("%s: balanceOf(%s) at %s from the tip (latest - logged tail) — no endpoint served past state: %s",
                 chain, holder, block, archive_err)
        return bal

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
            # FALLS THROUGH THE ENDPOINT LIST (ChainReader.block_number): Jake's run 2026-10-06 lost every
            # Ethereum scan to one public endpoint's -32046 while the keyed one answered elsewhere.
            head = (int(self.reader.block_number(chain)) if hasattr(self.reader, "block_number")
                    else int(self.reader.web3(chain).eth.block_number))
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

        # 2b. ADDRESS-POISONING SPAM OUT (2026-10-06 17:20): zero-value transfers from/to lookalikes of a holder or of
        # a real (non-zero) counterparty. They move nothing, so reconciliation is unaffected either way.
        known = set(holders) | {topic_address(e["topics"][1]) for h in holders for e in ins[h] if _amount(e)} \
            | {topic_address(e["topics"][2]) for h in holders for e in outs[h] if _amount(e)}
        poisoned = 0
        empty = sum(1 for h in holders for e in ins[h] + outs[h] if _empty_data(e))
        if empty:
            log.info("%s/%s: %d Transfer log(s) with EMPTY data ('0x') read as zero value (token %s)",
                     name, key, empty, token)
        for h in holders:
            ins[h], n_in = drop_poison(ins[h], 1, known)
            outs[h], n_out = drop_poison(outs[h], 2, known)
            poisoned += n_in + n_out

        # 3. RECONCILE, PER HOLDER, TO THE WEI.
        for h in holders:
            net = sum(_amount(e) for e in ins[h]) - sum(_amount(e) for e in outs[h])
            try:
                bal = self._balance_at(chain, chain_id, token, h, to_block)
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
        counted, uncounted, uncounted_ev = [], defaultdict(int), []
        # SWAP ATTRIBUTION (Jake, 2026-10-06 16:33): "bought" means ANY DEX fill, not CoW only. An inflow counts when
        # its sender is a declared swap venue (CoW GPv2Settlement, the Uniswap v4 PoolManager — native-ETH swaps
        # pay no ERC-20 out) OR the holder paid ANOTHER token out in the same transaction (every router and
        # aggregator: the wallet sells one token and receives this one). Read from the holder's outgoing Transfer
        # events of every contract (a topic-only query, cached like any stream).
        swap = spec.get("attribution") == "swap" and direction == "in"
        paid_txs: dict = {}
        if swap:
            venues = {a.lower() for a in spec.get("swap_venues") or ()}
            try:
                for h in holders:
                    sent = self._tx_events(chain_id, {"address": None}, to_block, [TRANSFER_TOPIC, pad_address(h)])
                    paid_txs[h] = {str(e["transactionHash"]).lower() for e in sent
                                   if str(e.get("address") or "").lower() != token.lower() and _amount(e) > 0}
            except ExplorerRefused as e:
                out.fail(SOURCE, name, f"{key}: the holders' outgoing transfers (any token) could not be read — {e}", TIER)
                out.gap(name, metric, reason=f"the {key} scan counts an inflow as BOUGHT when the holder paid another "
                        f"token out in the same transaction, and those transfers could not be read: {e}",
                        tiers_attempted="2", suggestion="Re-run; the stream is cached like any other.")
                return
        per_holder = defaultdict(lambda: [0, 0])                 # counted wei, last timestamp
        if direction == "in":
            for h in holders:
                for e in ins[h]:
                    frm = topic_address(e["topics"][1])
                    if frm in internal:
                        continue
                    bought = (not swap) or frm in venues or str(e["transactionHash"]).lower() in paid_txs.get(h, ())
                    if (frm in excluded or (frm in MINT_SENDERS and not spec.get("count_mints")) or not bought
                            or (count_from is not None and frm not in count_from)):
                        uncounted[frm] += _amount(e)
                        uncounted_ev.append((frm, e))
                    else:
                        counted.append(e)
                        per_holder[h][0] += _amount(e)
                        per_holder[h][1] = max(per_holder[h][1], int(e.get("timeStamp") or 0))
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
        # 4a. DEPOSITS vs TOP-UPS INTO A SHARE VAULT (Ether.fi sETHFI, Jake's run 2026-10-06 15:33). An inflow in
        # a transaction where the vault's SHARE token was minted (Transfer from address(0)) is a staking DEPOSIT:
        # it buys shares and leaves the share price where it was. An inflow with no share minted in its
        # transaction is a TOP-UP: assets rise, shares do not, and every holder's share price rises — the
        # reward. The mint events are read from the share token's own log, cached like any other stream.
        sm = spec.get("share_mint")
        sm_note = ""
        if sm and direction == "in":
            try:
                mints = self._tx_events(chain_id, {"address": sm["token"]}, to_block,
                                        [TRANSFER_TOPIC, pad_address(config.BURN_ADDRESSES["zero"])])
            except ExplorerRefused as e:
                out.fail(SOURCE, name, f"{key}: the share token's mint events could not be read — {e}", TIER)
                out.gap(name, metric, reason=f"the {key} scan tells deposits from top-ups by the share mints in "
                        f"each transaction, and those could not be read: {e}", tiers_attempted="2",
                        suggestion="Re-run; the mint stream is cached like any other.")
                return
            minted_in = {str(e["transactionHash"]).lower() for e in mints}
            dep = [e for e in counted if str(e["transactionHash"]).lower() in minted_in]
            counted = [e for e in counted if str(e["transactionHash"]).lower() not in minted_in]
            for e in dep:
                uncounted["(deposits: shares minted in the same tx)"] += _amount(e)
            sm_note = (f" Deposits (shares minted in the same transaction) not counted: {len(dep):,} transfer(s); "
                       f"counted = top-ups, no shares minted ({len(mints):,} mint event(s) on {sm['token']}).")
        # 4b. ONLY TRANSFERS IN A TRANSACTION THAT EMITS A NAMED EVENT (Sky, 2026-09-29).
        # FlapperUniV2SwapOnly swaps on the Uniswap pair, so the SKY it buys reaches the Pause
        # Proxy FROM THE PAIR — and so does any other pair -> Pause Proxy transfer. A transfer is
        # the flapper's purchase only inside a transaction where the flapper emitted Exec(lot,
        # bought); and the counted total must EQUAL the sum of Exec.bought, to the wei, or nothing
        # is stored (the receiver is immutable, so every Exec's SKY lands here).
        rq = spec.get("require_tx_event")
        rq_note = ""
        if rq and direction == "in":
            try:
                evs = self._tx_events(chain_id, rq, to_block)
            except ExplorerRefused as e:
                out.fail(SOURCE, name, f"{key}: the {rq['event']} events could not be read — {e}", TIER)
                out.gap(name, metric, reason=f"the {key} scan counts only transfers inside a "
                        f"transaction that emits {rq['event']} on {rq['address']}, and those events "
                        f"could not be read: {e}", tiers_attempted="2",
                        suggestion="Re-run; the events stream is cached like any other.")
                return
            txs = {str(e["transactionHash"]).lower() for e in evs}
            w = int(rq["amount_word"])
            declared = sum(int(str(e.get("data") or "0x")[2 + 64 * w: 2 + 64 * (w + 1)] or "0", 16)
                           for e in evs)
            kept = [e for e in counted if str(e["transactionHash"]).lower() in txs]
            outside = [e for e in counted if str(e["transactionHash"]).lower() not in txs]
            for e in outside:
                uncounted[topic_address(e["topics"][1])] += _amount(e)
            counted = kept
            got = sum(_amount(e) for e in counted)
            if got != declared:
                out.fail(SOURCE, name, f"{key}: counted transfers {got} wei != sum of {rq['event']} "
                                       f"amounts {declared} wei ({len(evs)} event(s)). NOTHING STORED.", TIER)
                out.gap(name, metric,
                        reason=(f"the {key} scan does not reconcile to {rq['event']}: transfers "
                                f"counted {got} wei, the events declare {declared} wei — "
                                f"{declared - got} wei apart. Not stored."),
                        tiers_attempted="2",
                        suggestion="A difference means a purchase routed elsewhere or a missed "
                                   "page; re-run once, and a second difference is real.")
                return
            rq_note = (f" Restricted to transactions emitting {rq['event']} on {rq['address']} "
                       f"({len(evs)} event(s)); counted total equals the events' declared amount "
                       f"to the wei.")
            if rq.get("classify_outside") and outside:
                rq_note += self._classify_outside(chain_id, rq["classify_outside"], outside,
                                                  holders, to_block, spec)
        try:
            dec = int(self.reader.erc20(chain, token).functions.decimals().call())
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{key}: decimals() failed: {e}", TIER)
            return
        scale = 10 ** dec
        c_total = sum(_amount(e) for e in counted) / scale
        # LABELLED where config names the sender (2026-09-28): an excluded inflow is recorded as
        # "other inflow, not counted" with what it is, not left as a bare address.
        labels = {a.lower(): l for a, l in {**(spec.get("not_counted_labels") or {}),
                                            **(spec.get("sender_labels") or {})}.items()}
        u_table = ", ".join(f"{a} {v / scale:,.2f}" + (f" [{labels[a]}]" if a in labels else "")
                            for a, v in sorted(uncounted.items(), key=lambda kv: -kv[1])[:8]) or "none"
        by_party = defaultdict(int)
        for e in counted:
            party = topic_address(e["topics"][1 if direction == "in" else 2])
            by_party[party] += _amount(e)
        c_table = ", ".join(f"{a} {v / scale:,.2f}" + (f" [{labels[a]}]" if a in labels else "") for a, v in
                            sorted(by_party.items(), key=lambda kv: -kv[1])[:8]) or "none"
        # WHEN THE COUNTED FLOW LAST MOVED, so a zero in the trailing window can be told apart
        # from a scan that put its transfers somewhere else. Ether.fi, 2026-09-24: 18,982,711.90
        # ETHFI counted and a 30-day cell of 0 — which is either "nothing arrived in 30 days" or a
        # dating fault, and the log gave no way to say which.
        dated = [e["timeStamp"] for e in counted if int(e.get("timeStamp") or 0) > 0]
        last_moved = (pd.Timestamp(max(dated), unit="s").date().isoformat() if dated else "never")
        # NOT-COUNTED INFLOW AFTER THE LAST COUNTED ONE (Jake's credibility run, 2026-10-05): Ether.fi's
        # CoW-only count went SILENT on 2026-04-01 while DefiLlama — every aggregator trade by this wallet —
        # kept booking buybacks. Inflow from other senders since then, by sender with its last date, says
        # whether purchases moved to another route rather than stopped.
        cut = max(dated) if dated else 0
        late = defaultdict(lambda: [0, 0])
        for frm, e in uncounted_ev:
            t = int(e.get("timeStamp") or 0)
            if t > cut:
                late[frm][0] += _amount(e)
                late[frm][1] = max(late[frm][1], t)
        late_table = ", ".join(
            f"{a} {v[0] / scale:,.2f} (last {pd.Timestamp(v[1], unit='s').date()})" + (f" [{labels[a]}]" if a in labels else "")
            for a, v in sorted(late.items(), key=lambda kv: -kv[1][0])[:8]) if late else "none"
        summary = (f"{key}: RECONCILED to the wei for {len(holders)} holder(s) at block "
                   f"{to_block:,}; served by {via} in {requests} request(s), {increment}"
                   + (f" after refusal(s): {'; '.join(refused)}" if refused else "")
                   + f". Counted {direction}flow {c_total:,.4f} over {len(counted)} transfer(s), "
                     f"last on {last_moved} — top counterparties: {c_table}. Other inflow, not counted: "
                     f"{u_table}. Not-counted inflow AFTER the last counted transfer: {late_table}." + sm_note
                   + (" BY HOLDER: " + "; ".join(
                       f"{hh} {v[0] / scale:,.2f}" + (f" (last {pd.Timestamp(v[1], unit='s').date()})" if v[1] else "")
                       + (f" [{labels[hh]}]" if hh in labels else "") for hh, v in per_holder.items()) + "."
                      if len(holders) > 1 and per_holder else "")
                   + (f" Bought = a fill from a swap venue ({len(venues)}) or a transaction in which the holder paid "
                      f"another token out." if swap else "")
                   + (f" {poisoned} zero-value lookalike (address-poisoning) transfer(s) ignored." if poisoned else "")
                   + (f" {empty} log(s) carried EMPTY data ('0x') — read as no value." if empty else "")
                   + rq_note + self._since_note(spec, counted, labels, scale))
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

        # A HOLDER'S ALL-TIME BOUGHT SHARE (Jake, 2026-10-06 17:20): of everything the holder ever received (not a
        # hop between holders, not a mint, not address-poisoning spam), the share counted as BOUGHT on-chain — the
        # top-up Safe's ~6% (1.30M of ~20.95M since 2024-07). Stored as a ratio dated today.
        bs = spec.get("bought_share")
        if bs:
            hb = bs["holder"].lower()
            got_in = sum(_amount(e) for e in ins.get(hb, []) if topic_address(e["topics"][1]) not in internal
                         and topic_address(e["topics"][1]) not in MINT_SENDERS)
            if got_in > 0:
                share = per_holder[hb][0] / got_in
                out.add(tidy([(today(), share)], name, bs["metric"], f"{SOURCE}:{key}", TIER), SOURCE, name,
                        f"{bs['metric']} = {share:.2%} — {hb} received {got_in / scale:,.2f} in all, "
                        f"{per_holder[hb][0] / scale:,.2f} of it bought on-chain (the rest transferred in)", TIER)

        # WHY THE SHARE PRICE MOVED (Jake, 2026-10-06: realised 10.01% vs top-up-based 4.18%). The vault's
        # assets and shares are rebuilt from the full Transfer history (fetch/share_decompose.py) and every
        # transaction's change in assets-per-share is attributed by its shape; the parts sum to the change.
        dec = spec.get("decompose")
        if dec and sm and len(holders) == 1:
            self._decompose(p, spec, dec, sm, chain_id, holders[0], ins, outs, to_block, labels, days, out)

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
