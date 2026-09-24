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


class LogScan:
    """Runs every project's `log_scans`, each against one token and one or more holders."""

    def __init__(self, explorer: ExplorerLogs | None = None, reader=None):
        self.explorer = explorer or ExplorerLogs()
        if reader is None:
            from .chain import ChainReader
            reader = ChainReader()
        self.reader = reader

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
        ins, outs, served, requests, refused = {}, {}, set(), 0, []
        # ONE 120s budget for the whole scan, both providers included (config.EXPLORER_SCAN_BUDGET_S).
        budget = getattr(self.explorer, "start_budget", None)
        if budget:
            budget(config.EXPLORER_SCAN_BUDGET_S)
        try:
            for h in holders:
                ins[h], m1 = self.explorer.get_logs(chain_id, token, [TRANSFER_TOPIC, None, pad_address(h)], 0, to_block)
                outs[h], m2 = self.explorer.get_logs(chain_id, token, [TRANSFER_TOPIC, pad_address(h), None], 0, to_block)
                for m in (m1, m2):
                    served.add(m["explorer"])
                    requests += m["requests"]
                    refused += m["refused"]
        except ExplorerTimeout as e:
            out.fail(SOURCE, name, f"{key}: {e}", TIER)
            out.gap(name, metric, reason=str(e), tiers_attempted="2",
                    suggestion="The providers' own errors are above. Re-runs are cheap now — the "
                               "scan stops at the budget instead of retrying for most of an hour.")
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
        via = "+".join(sorted(served))

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
                return

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
        u_table = ", ".join(f"{a} {v / scale:,.2f}" for a, v in
                            sorted(uncounted.items(), key=lambda kv: -kv[1])[:8]) or "none"
        by_party = defaultdict(int)
        for e in counted:
            party = topic_address(e["topics"][1 if direction == "in" else 2])
            by_party[party] += _amount(e)
        c_table = ", ".join(f"{a} {v / scale:,.2f}" for a, v in
                            sorted(by_party.items(), key=lambda kv: -kv[1])[:8]) or "none"
        summary = (f"{key}: RECONCILED to the wei for {len(holders)} holder(s) at block "
                   f"{to_block:,}; served by {via} in {requests} request(s)"
                   + (f" after refusal(s): {'; '.join(refused)}" if refused else "")
                   + f". Counted {direction}flow {c_total:,.4f} over {len(counted)} transfer(s) — "
                     f"top counterparties: {c_table}. NOT counted: {u_table}.")
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
        frame = window(frame, window_days)
        out.add(frame, SOURCE, name,
                f"{metric} = {key}, {len(frame)} daily row(s) from {start.date()} (zeros are "
                f"observed, the scan covers every block). {summary}", TIER)
