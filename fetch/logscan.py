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
import threading
from collections import defaultdict

from pathlib import Path

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


def _takes_transfer(explorer) -> bool:
    """A test's stand-in explorer may not know the `transfer` keyword (Round G 2.7)."""
    import inspect
    try:
        return "transfer" in inspect.signature(explorer.configured).parameters
    except (TypeError, ValueError):
        return False


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


def _sum_after(events: list, block: int) -> tuple[int, int]:
    """(summed value, count) of the transfers after `block`, read from the end of a block-ordered list (G6 item 0b)."""
    total = n = 0
    for e in reversed(events):
        if int(str(e["blockNumber"]), 0) <= block:
            break
        total += _amount(e)
        n += 1
    return total, n


def drop_poison(events: list, side: int, known) -> tuple[list, int]:
    """(events, n dropped): a ZERO-VALUE transfer whose counterparty (topic `side`) mimics a known address is
    address-poisoning spam (Jake's etherfi_topup_safe probe, 2026-10-06 17:20) — dropped from every scan and probe.
    It moves no tokens, so no sum changes; it only pollutes the counterparty tables."""
    # INDEXED BY THE FOUR DIGITS A LOOKALIKE SHARES (Round G6 item 0b): the scan over every known address per zero-value
    # transfer was quadratic — 400 zero-value transfers against Pendle's ~570k counterparties took 8.9 s
    pre: dict = defaultdict(set)
    suf: dict = defaultdict(set)
    for k in known:
        k = k.lower()
        pre[k[2:6]].add(k)
        suf[k[-4:]].add(k)
    kept, n = [], 0
    for e in events:
        if _amount(e) == 0:
            cp = topic_address(e["topics"][side]).lower()
            if any(k != cp for k in pre.get(cp[2:6], ())) or any(k != cp for k in suf.get(cp[-4:], ())):
                n += 1
                continue
        kept.append(e)
    return kept, n



def _hex(v) -> str:
    """A topic / hash / data value as a 0x-hex string, whether it came as str, bytes or HexBytes."""
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()
    s = str(v.hex() if hasattr(v, "hex") and not isinstance(v, str) else v)
    return s if s.startswith("0x") else "0x" + s


# the most explorer holes one window's reconciliation will look for (each costs ~log2(window blocks) balance reads)
EXPLORER_HOLE_MAX = 5
# the full-history span repair (run review D1): seconds of chain reads per run, and the first range asked per call
REPAIR_BUDGET_S = 120.0
REPAIR_CHUNK = 2_000_000


def _norm_log(x) -> dict:
    """An explorer- or RPC-shaped log in the shape the scan reads (ints for block, index and time)."""
    g = (lambda k: x.get(k)) if hasattr(x, "get") else (lambda k: getattr(x, k, None))       # noqa: E731
    as_int = lambda v: int(v, 16) if isinstance(v, str) and v.startswith("0x") else int(v or 0)  # noqa: E731
    return {"address": _hex(g("address") or "").lower(), "topics": [_hex(t).lower() for t in g("topics") or []],
            "data": _hex(g("data") or "0x"), "blockNumber": as_int(g("blockNumber")),
            "timeStamp": as_int(g("timeStamp")), "transactionHash": _hex(g("transactionHash")).lower(),
            "logIndex": as_int(g("logIndex"))}


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



WINDOW_BUDGET_S = 120.0     # the reconciled-window fallback's own budget (fix round 2, R8)

TIER_MARGIN_S = 15.0        # the scans stop this long before their tier's budget, to store what they read (G4 item 1)
WINDOW_FLOOR_S = 30.0       # a window read with less time than this is not started (G4 item 1)
_RECON_LOCK = threading.Lock()   # reconciled.json is shared by every per-project tier (G6 item 0a/0b)
RESERVE_SHARE = 0.6         # at most this share of a tier is held back for headline scans (G5 item 2)


class LogScan:
    """Runs every project's `log_scans`, each against one token and one or more holders."""

    unbounded = False               # defaults for an instance built without __init__ (tests); __init__ sets both
    _scan_deadline = None

    def _share(self, want: float, reserve: float = 0.0) -> float:
        """`want` seconds, capped at what is left of this scan's share of the tier's budget less `reserve` (G4 item 1)."""
        if self._scan_deadline is None:
            return float(want)
        import time as _time
        return max(1.0, min(float(want), self._scan_deadline - _time.monotonic() - reserve))

    def __init__(self, explorer: ExplorerLogs | None = None, reader=None,
                 cache: LogCache | None = None, unbounded: bool = False, own_tier: str | None = None,
                 last_dates: dict | None = None):
        # own_tier: run ONLY the scans declaring that tier (Pendle's gauge scan, Jake's run 2026-10-08 11:27: a long
        # first seed starved the explorer tier into its 300s timeout); without it, those scans are skipped here.
        self.own_tier = own_tier
        self.planned: list[tuple[str, str]] = []     # (project, metric) this run means to serve — named on a timeout
        # (project, metric) -> newest stored date (fetch ctx last_dates): a seeded history's live read starts there
        self.last_dates = last_dates or {}
        self._scan_deadline: float | None = None     # this scan's share of the tier's budget (G4 item 1)
        # unbounded: no per-scan budget — `token_metrics.py --seed geodnet` (2026-09-28) finishes
        # a first read in one sitting instead of 120s a run.
        self.unbounded = unbounded
        self.explorer = explorer or ExplorerLogs()
        self.cache = cache or LogCache()
        if reader is None:
            from .chain import ChainReader
            reader = ChainReader()
        self.reader = reader

    def _recon_state(self) -> dict:
        """{chain:token:holder: {"block", "net", "key"}} — the last block each holder reconciled to the wei at (G6 0b)."""
        import json
        f = Path(self.cache.root) / "reconciled.json"
        try:
            return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except (OSError, ValueError):
            return {}

    def _recon_save(self, state: dict) -> None:
        import json
        from .logcache import atomic_write_text
        try:
            with _RECON_LOCK:
                cur = self._recon_state()
                cur.update(state)
                atomic_write_text(Path(self.cache.root) / "reconciled.json", json.dumps(cur, sort_keys=True))
        except OSError as e:
            log.info("reconcile state not saved (%s) — the next run sums the full history", e)

    def _date_undated(self, chain: str, events: list) -> list:
        """Events a route returned WITHOUT a timestamp (Alchemy's Transfers API off its metadata chains — Berachain, BNB,
        HyperEVM; Round G5 item 2) get their block's time from the chain: timestamps never decrease with the block, so a
        run of blocks whose first and last share a UTC day is dated without reading the blocks between (the Katana
        seed's bracketing, fetch.katana_seed.date_blocks). An event is given its block's exact time where read, else
        its day's midnight — only the day is used downstream. Nothing is dated without a chain reader; a failed read
        leaves the events undated (refused later, never guessed)."""
        undated = [e for e in events if not int(e.get("timeStamp") or 0)]
        if not undated or not hasattr(self.reader, "block_timestamp"):
            return events
        from .katana_seed import date_blocks
        seen: dict[int, int] = {}

        def ts(b):
            if b not in seen:
                seen[b] = int(self.reader.block_timestamp(chain, int(b)))
            return seen[b]
        try:
            day_of = date_blocks(sorted({int(e["blockNumber"]) for e in undated}), ts)
        except Exception as ex:  # noqa: BLE001 — left undated, said once
            from .chain import redact_urls
            log.info("dating %d undated event(s) on %s stopped: %s", len(undated), chain, redact_urls(ex))
            return events
        for e in undated:
            b = int(e["blockNumber"])
            e["timeStamp"] = seen.get(b) or int(day_of[b].timestamp())
        log.info("dated %d event(s) on %s from %d block-time read(s) (the route returned no timestamps)",
                 len(undated), chain, len(seen))
        return events

    def _save_progress(self, streams, e, to_block) -> str:
        """Out of budget mid-scan: keep what was read, so the seed resumes. Nothing here is
        proven — proven_to is left where it was."""
        if getattr(e, "partial", None) and getattr(e, "stream", None):
            self._date_undated(getattr(self, "_chain_now", "ethereum"), e.partial)
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
        own = getattr(self, "own_tier", None)
        todo = [(p, spec) for p in projects for spec in p.get("log_scans") or [] if spec.get("own_tier") == own]
        # A DECLARED 0 IS WRITTEN, NOT READ (Pendle Optimism, Jake's run on ab3c7fe 2026-10-11): first, so a timed-out
        # tier cannot gap it, and never planned as a read
        for p, spec in [t for t in todo if t[1].get("declared_zero")]:
            self._declared_zero(p, spec, out)
        todo = [t for t in todo if not t[1].get("declared_zero")]
        self.planned = [(p["name"], spec.get("metric")) for p, spec in todo]
        import re as _re
        import threading as _th
        import time as _time
        from .base import set_phase
        from .chain import BREAKER, ChainStalled, redact_urls
        stalled: dict[str, str] = {}
        gapped: dict[str, list] = {}
        src_stalled: dict[str, list] = {}               # explorer / API host -> [(project, spec)] (G4 item 1)
        # A FAIR SHARE OF THE TIER'S BUDGET PER SCAN (Round G4 item 1): a scan's own budgets (full history 120-140 s,
        # then the window 120 s) added up to more than its tier's 240 s, so the tier was cut off mid-scan — the window
        # never stored and the scans after it never started ("kept 0 frames"). Each scan now gets the time left
        # divided by the scans left, so every one ends inside the tier and saves what it read.
        tier_deadline = None
        if not self.unbounded:
            from . import TIER_BUDGET_S, _budget          # noqa: PLC0415
            tname = _th.current_thread().name
            if tname in TIER_BUDGET_S:
                tier_deadline = _time.monotonic() + _budget(tname) - TIER_MARGIN_S
        # A HEADLINE SCAN'S RESERVE (Round G5 item 2, Jake's 06:30 run: merkl_morpho_out_base got 45 s of a 240 s tier
        # and Chainlink's noncirc scans 14 s each of the explorer tier's 300 s): while a scan feeding a headline is still
        # to run, its reserve (config.scan_reserve_s) is held back from every other scan's share, so they cannot eat it.
        reserve = {id(spec): config.scan_reserve_s(p["name"], spec) for p, spec in todo}
        if tier_deadline is not None and sum(reserve.values()):
            # never more than RESERVE_SHARE of the tier held back in all: the other scans keep a share too
            cap = RESERVE_SHARE * max(0.0, tier_deadline - _time.monotonic())
            k = min(1.0, cap / sum(reserve.values()))
            reserve = {sid: r * k for sid, r in reserve.items()}
        retry: list[tuple[int, dict, dict, int, int, int]] = []    # (index, p, spec, log/gap/frame marks) of pass 1

        def deadline_for(i: int, seq: list) -> None:
            if tier_deadline is None:
                return
            left = max(0.0, tier_deadline - _time.monotonic())
            spec_i = seq[i][1]
            held = sum(reserve[id(sp)] for _p, sp in seq[i + 1:])
            fair = max(0.0, left - held) / (len(seq) - i)
            mine = max(fair, min(reserve[id(spec_i)], left)) if reserve[id(spec_i)] else fair
            self._scan_deadline = _time.monotonic() + mine

        for i, (p, spec) in enumerate(todo):
            chain = spec.get("chain") or "ethereum"
            deadline_for(i, todo)
            set_phase(f"scan {spec.get('key')} ({i + 1}/{len(todo)}, {chain}): starting")
            n_log, n_gap, n_frame = len(out.log), len(out.gaps), len(out.frames)
            # A STALLED CHAIN IS NOT ASKED AGAIN THIS RUN (Round G3 item 1): its remaining scans are gapped at once and
            # named in ONE line below, and the other chains' scans (their own tiers) are untouched.
            why = stalled.get(chain) or BREAKER.chain_stalled(chain)
            if why:
                stalled[chain] = why
                gapped.setdefault(chain, []).append((p["name"], spec))
                continue
            # ONE SCAN'S FAILURE NEVER STOPS THE TIER (Jake's run 2026-10-06 18:21: one bad log crashed the
            # adapter and no later scan ran).
            try:
                if self._scan(p, spec, window_days, out) == "budget" and tier_deadline is not None:
                    retry.append((i, p, spec, n_log, n_gap, n_frame))
            except ExplorerTimeout:
                raise
            except ChainStalled as e:
                stalled[chain] = BREAKER.chain_stalled(chain) or str(e)
                gapped.setdefault(chain, []).append((p["name"], spec))
                continue
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, p["name"], f"{spec.get('key')}: scan crashed — {type(e).__name__}: "
                                            f"{redact_urls(e)}. The other scans ran.", TIER)
                log.exception("%s/%s crashed", p["name"], spec.get("key"))
            # A SOURCE THAT STALLED during this scan and left it with nothing (G4 item 1): named once, below
            for e in out.log[n_log:]:
                m = _re.search(r"HOST STALLED this run: ([^\s(]+)", str(e.message)) if e.status == "failed" else None
                if m:
                    src_stalled.setdefault(m.group(1), []).append((p["name"], spec))
                    break
            # it stalled during this scan (STALL_AFTER timeouts on every host, or every host's last call timed out):
            # its series did not land, and nothing more is asked of the chain this run
            why = BREAKER.chain_stalled(chain) or (BREAKER.timed_out_all(chain) and BREAKER.mark_stalled(
                chain, "every endpoint's last call timed out"))
            if why:
                stalled[chain] = why
                gapped.setdefault(chain, []).append((p["name"], spec))
        # PASS 2 — THE TIER'S LEFTOVER TIME (Round G5 item 2: explorer_l2:base finished in 84 s of its 240 s with
        # Morpho's Base claims cut at 45 s). A scan that stopped on its share, not on an error, runs again with the time
        # the others did not use; it resumes from its cache. When it completes, its pass-1 failure lines and gap rows are
        # withdrawn (one line says so); when it stops again, pass 2's own lines stand.
        if retry and tier_deadline is not None:
            seq = [(p, spec) for _i, p, spec, *_m in retry]
            for j, (i, p, spec, n_log, n_gap, n_frame) in enumerate(retry):
                if tier_deadline - _time.monotonic() < WINDOW_FLOOR_S:
                    break
                chain = spec.get("chain") or "ethereum"
                if stalled.get(chain) or BREAKER.chain_stalled(chain):
                    continue
                deadline_for(j, seq)
                set_phase(f"scan {spec.get('key')} (pass 2, {chain}): resuming with the tier's leftover time")
                first = (out.log[n_log:], out.gaps[n_gap:])
                m_log, m_gap, m_frame = len(out.log), len(out.gaps), len(out.frames)
                try:
                    again = self._scan(p, spec, window_days, out)
                except ExplorerTimeout:
                    raise
                except ChainStalled as e:
                    stalled[chain] = BREAKER.chain_stalled(chain) or str(e)
                    gapped.setdefault(chain, []).append((p["name"], spec))
                    continue
                except Exception as e:  # noqa: BLE001
                    out.fail(SOURCE, p["name"], f"{spec.get('key')}: pass 2 crashed — {type(e).__name__}: "
                                                f"{redact_urls(e)}", TIER)
                    continue
                if again != "budget" and len(out.frames) > m_frame:
                    drop_log = {id(e) for e in first[0] if e.status == "failed"}
                    drop_gap = {id(g) for g in first[1]}
                    out.log[:] = [e for e in out.log if id(e) not in drop_log]
                    out.gaps[:] = [g for g in out.gaps if id(g) not in drop_gap]
                    out.log.append(LogEntry(SOURCE, p["name"], 0, "ok",
                                            f"{spec.get('key')}: completed in PASS 2 on the tier's leftover time (pass 1 "
                                            f"stopped on its share; its failure line is withdrawn)", TIER))
                del m_log, m_gap
        for host, items in src_stalled.items():
            from .base import host_stalled as _hs
            series = [f"{n}/{m}" for n, sp in items for m in (sp.get("metric"), sp.get("direct_metric")) if m]
            out.fail(SOURCE, items[0][0], f"SOURCE STALLED THIS RUN: {host} — {_hs(host) or 'timed out'}. Series "
                                          f"gapped: {', '.join(series)} (each fell back where another route serves "
                                          f"it; these did not land). Other series are unaffected.", TIER)
        for chain, why in stalled.items():
            series = [f"{n}/{m}" for n, sp in gapped.get(chain, []) for m in (sp.get("metric"), sp.get("direct_metric"))
                      if m]
            out.fail(SOURCE, gapped[chain][0][0] if gapped.get(chain) else None,
                     f"CHAIN STALLED THIS RUN: {chain} — {redact_urls(why)}. Series gapped: {', '.join(series)}. Other "
                     f"chains' series are unaffected (each chain has its own tier).", TIER)
            have = {(g["project"], g["metric"]) for g in out.gaps}
            for n, sp in gapped.get(chain, []):
                for m in (sp.get("metric"), sp.get("direct_metric")):
                    if m and (n, m) not in have:
                        out.gap(n, m, tiers_attempted=str(TIER),
                                reason=f"chain {chain} STALLED this run ({redact_urls(why)[:160]})",
                                suggestion="re-run; if it persists, check the chain's RPC (RUNBOOK 11cd)")

    def _declared_zero(self, p, spec, out) -> None:
        """0 for each day of the scan's window (from `declared_zero.since`), to `metric` and `direct_metric`, under the
        scan's own source plus the declared-zero marker — the same measuring point, labelled. No network call."""
        name, key, decl = p["name"], spec["key"], spec["declared_zero"]
        start = max(pd.Timestamp(decl["since"]), today() - pd.Timedelta(days=int(spec.get("window_days") or 100)))
        days = pd.date_range(start + pd.Timedelta(days=1), today() - pd.Timedelta(days=1), freq="D")
        msg = (f"{key}: NOT FETCHED — declared 0 since {decl['since']} (read_by {decl['read_by']} {decl['read_on']}: "
               f"{decl['evidence']}; {decl['source_url']}). {len(days)} day(s) written as 0.")
        for metric, src in ((spec["metric"], f"{SOURCE}:{key}"), (spec.get("direct_metric"), f"{SOURCE}:direct:{key}")):
            if metric:
                out.add(tidy([(d, 0.0) for d in days], name, metric, config.mark_source(src, "declared-zero"), TIER),
                        SOURCE, name, f"{metric}: {msg}", TIER)
        out.log.append(LogEntry(SOURCE, name, 0, "ok", msg, TIER))

    def _window_logs(self, chain, chain_id, token, topics, b0, to_block):
        """(logs, served by): a fresh windowed read — the routed explorers first, then RPC eth_getLogs through the
        chain reader's chunked traversal. No cache."""
        clear = getattr(self.explorer, "clear_budget", None)
        if clear:
            clear()
        try:
            logs, meta = self.explorer.get_logs(chain_id, token, topics, b0, to_block)
            return [_norm_log(x) for x in logs], (meta or {}).get("explorer", "explorer")
        except (ExplorerRefused, ExplorerTimeout) as e:
            why = str(e)[:160]
        if not hasattr(self.reader, "scan_logs"):
            raise ExplorerRefused(f"explorers refused ({why}) and no RPC log reader")
        raw, chunks, _used = self.reader.scan_logs(chain, {"address": self.reader.checksum(token)
                                                           if hasattr(self.reader, "checksum") else token,
                                                           "topics": topics}, b0, to_block)
        return [_norm_log(x) for x in raw], f"RPC eth_getLogs ({chunks} chunk(s); explorers: {why})"

    def _patch_explorer_holes(self, chain, chain_id, token, holders, b0, to_block, ins, outs, bal0):
        """AN EXPLORER THAT DOES NOT INDEX A BLOCK (round 3 run X1, Jake 2026-10-14). Ether.fi's OP cashback window was
        19.808964 ETHFI short: Blockscout (explorer.optimism.io and optimism.blockscout.com, tokentx, getLogs and v2 alike)
        has NO logs for OP block 158,047,200, while the chain has the distributor's Transfer of exactly that amount
        (tx 0xb09bfbe3…). Not a tolerance: the block is found and read from the chain. balanceOf is bisected against the
        running in − out to the first block where they part; that block's Transfers in and out of the holders are read
        FROM THE CHAIN (ChainReader.logs_in_block: eth_getLogs over that one block on every endpoint, else the block's
        receipts — never the explorer that lacks it) and the ones the explorer lacked are added. Repeated for at most
        EXPLORER_HOLE_MAX holes; a block that yields nothing new stops it (the gap is not a missing log).
        Returns (ins, outs, [(block, transfers added)], trace) — `trace` says what was tried, in order, for the log and
        for the failure line (run Y1, Jake 2026-10-14: on his machine the repair left no trace at all)."""
        internal = {h.lower() for h in holders}
        ins, outs, holes, trace = list(ins), list(outs), [], []
        reads = [0]

        def mis(b):
            reads[0] += len(holders)
            net = sum(_amount(e) for e in ins if int(e["blockNumber"]) <= b and topic_address(e["topics"][1])
                      not in internal) - sum(_amount(e) for e in outs if int(e["blockNumber"]) <= b and
                                             topic_address(e["topics"][2]) not in internal)
            # ARCHIVE STATE ONLY (Round G 1.2, Jake's Ether.fi run 2026-10-18): with no endpoint serving past state, the
            # tip-minus-logged-tail stand-in answered every probe — and since it is made of the explorer's own logs, the
            # difference it measures is the same at every block before the hole, so the bisection halved 79,180,853 ->
            # 19,331 at 13-25 s a step and never found anything. No archive state: the search stops at once and says so.
            return sum(self._balance_at(chain, chain_id, token, h, b, allow_tail=False) for h in holders) - bal0 - net

        def block_logs(topics, blk):
            if hasattr(self.reader, "logs_in_block"):
                got, tried = self.reader.logs_in_block(chain, token, topics, blk)
                trace.extend(tried)
                return got
            addr = self.reader.checksum(token) if hasattr(self.reader, "checksum") else token
            raw, _c, _u = self.reader.scan_logs(chain, {"address": addr, "topics": topics}, blk, blk, rpc_only=True)
            trace.append(f"RPC eth_getLogs block {blk:,} -> {len(raw)} log(s)")
            return raw

        try:
            for _ in range(EXPLORER_HOLE_MAX):
                if mis(to_block) == 0:
                    break
                lo, hi = b0 - 1, to_block
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    lo, hi = (mid, hi) if mis(mid) == 0 else (lo, mid)
                trace.append(f"bisected on balanceOf ({reads[0]} read(s)) to block {hi:,}: the first where the "
                             f"balance parts from the explorer's transfers")
                self._last_divergence = hi
                if getattr(self, "_first_divergence", None) is None:
                    self._first_divergence = hi           # the span repair starts here: everything after is re-read
                have = {(e["transactionHash"], int(e["logIndex"])) for e in ins + outs}
                added = 0
                # ONE READ OF THE BLOCK: every Transfer of the token in it, split into in / out here (run Y1 live: read
                # side by side, the inflow side was legitimately empty and an empty answer sent it through every
                # endpoint and 70 receipts before the outflow side was asked)
                for x in block_logs([TRANSFER_TOPIC], hi):
                    e = _norm_log(x)
                    if len(e["topics"]) < 3 or (e["transactionHash"], int(e["logIndex"])) in have:
                        continue
                    frm, to = topic_address(e["topics"][1]), topic_address(e["topics"][2])
                    if (to in internal or frm in internal) and not int(e.get("timeStamp") or 0) \
                            and hasattr(self.reader, "block_timestamp"):
                        e["timeStamp"] = int(self.reader.block_timestamp(chain, hi))   # a counted event needs its day
                    if to in internal or frm in internal:
                        (ins if to in internal else outs).append(e)
                        have.add((e["transactionHash"], int(e["logIndex"])))
                        added += 1
                if not added:
                    trace.append(f"block {hi:,}: the chain has no Transfer the explorer lacked — not a missing log")
                    break
                holes.append((hi, added))
        except Exception as e:  # noqa: BLE001 — the repair failing leaves the refusal, with what was tried
            from .chain import redact_urls
            trace.append(f"stopped: {redact_urls(e)}"[:400])
        for t in trace:
            log.info("explorer-hole search %s: %s", chain, t)
        return ins, outs, holes, trace

    def _repair_history(self, spec, chain, chain_id, token, h, ins_h, outs_h, to_block, base=(0, 0)):
        """THE FULL HISTORY, REPAIRED FROM THE CHAIN (run review D1, Jake 2026-10-14: Pendle's Arbitrum gauge scan has
        never reconciled — every completed Etherscan seed, 10-09 to 10-14, read in − out = 812,025.60 PENDLE against a
        balance near 78,400; the chain's own record, read 2026-10-14 over all 936,270 Transfer logs, closes to the wei).
          1. the explorer-hole repair: bisect balanceOf to the first block where it parts from the scan, read that block
             from the chain (never the explorer), up to EXPLORER_HOLE_MAX holes;
          2. if a gap remains, the SPAN from the first divergent block to the pinned block is re-read from the chain —
             RPC only, every log dated (ChainReader.dated_logs) — resumable: state in the log cache, REPAIR_BUDGET_S a
             run, the explorer's streams kept meanwhile;
          3. explorer events before the span + the chain's inside it + the explorer's after it must reconcile TO THE WEI.
        `base` = (block, balance) the history starts from: (0, 0) for a full history — nothing is held before any
        Transfer, so the baseline is NOT the explorer's first event (a dropped first event would fall outside it).
        Returns (ins, outs, status, trace): status "repaired" | "in progress" | "failed"."""
        import gzip
        import json
        import time
        trace: list = []
        path = self.cache.root / f"repair-{spec['key']}-{h[2:12]}.json.gz"
        net = lambda I, O: sum(_amount(e) for e in I) - sum(_amount(e) for e in O)    # noqa: E731
        try:
            state = json.loads(gzip.decompress(path.read_bytes()))
        except (OSError, ValueError):
            state = None
        first, bal0 = int(base[0]) + 1, int(base[1])
        if state is None:
            self._last_divergence = self._first_divergence = None
            ins_h, outs_h, holes, t1 = self._patch_explorer_holes(chain, chain_id, token, [h], first, to_block,
                                                                  ins_h, outs_h, bal0)
            trace += t1
            if self._balance_at(chain, chain_id, token, h, to_block) - bal0 == net(ins_h, outs_h):
                return ins_h, outs_h, "repaired", trace
            b = self._first_divergence              # the FIRST divergent block: the span re-reads every patch too
            if b is None:
                return ins_h, outs_h, "failed", trace + ["no divergent block found — nothing to re-read"]
            state = {"from": int(b), "to": int(to_block), "next": int(b), "in": [], "out": [],
                     "chunk": int(spec.get("repair_chunk_blocks") or REPAIR_CHUNK)}
            trace.append(f"a gap remains after {len(holes)} single-block repair(s): re-reading blocks {b:,}..{to_block:,} "
                         f"from the chain (resumable)")
        t0 = time.monotonic()
        budget = float(spec.get("repair_budget_s") or REPAIR_BUDGET_S)
        first_pass = True                          # at least one range a run, whatever the budget, so it always advances
        while state["next"] <= state["to"] and (first_pass or self.unbounded or time.monotonic() - t0 < budget):
            first_pass = False
            lo = state["next"]
            got_out, upper = self.reader.dated_logs(chain, token, [TRANSFER_TOPIC, pad_address(h), None], lo,
                                                    min(lo + state["chunk"] - 1, state["to"]), state["chunk"], trace)
            got_in, cur = [], lo
            while cur <= upper:
                part, up2 = self.reader.dated_logs(chain, token, [TRANSFER_TOPIC, None, pad_address(h)], cur, upper,
                                                   upper - cur + 1, trace)
                got_in += part
                cur = up2 + 1
            for x in got_out:
                e = _norm_log(x)
                e["timeStamp"] = int(str(x["blockTimestamp"]), 16) if str(x["blockTimestamp"]).startswith("0x") \
                    else int(x["blockTimestamp"])
                state["out"].append(e)
            for x in got_in:
                e = _norm_log(x)
                e["timeStamp"] = int(str(x["blockTimestamp"]), 16) if str(x["blockTimestamp"]).startswith("0x") \
                    else int(x["blockTimestamp"])
                state["in"].append(e)
            state["next"] = upper + 1
            self.cache.root.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(json.dumps(state).encode()))
        if state["next"] <= state["to"]:
            done = (state["next"] - state["from"]) / max(1, state["to"] - state["from"] + 1)
            trace.append(f"span re-read IN PROGRESS: blocks {state['from']:,}..{state['next'] - 1:,} of "
                         f"..{state['to']:,} ({done:.0%}), {len(state['out']):,} out / {len(state['in']):,} in so far — "
                         f"resumes next run")
            return ins_h, outs_h, "in progress", trace
        b, t = state["from"], state["to"]
        ins2 = [e for e in ins_h if int(e["blockNumber"]) < b] + state["in"] + \
            [e for e in ins_h if int(e["blockNumber"]) > t]
        outs2 = [e for e in outs_h if int(e["blockNumber"]) < b] + state["out"] + \
            [e for e in outs_h if int(e["blockNumber"]) > t]
        bal = self._balance_at(chain, chain_id, token, h, to_block)
        path.unlink(missing_ok=True)
        replaced = (sum(1 for e in outs_h if b <= int(e["blockNumber"]) <= t),
                    sum(1 for e in ins_h if b <= int(e["blockNumber"]) <= t))
        trace.append(f"span {b:,}..{t:,} re-read from the chain: {len(state['out']):,} out / {len(state['in']):,} in "
                     f"(the explorer had {replaced[0]:,} / {replaced[1]:,})")
        if bal - bal0 == net(ins2, outs2):
            return ins2, outs2, "repaired", trace
        trace.append(f"still does not reconcile after the span: difference {bal - bal0 - net(ins2, outs2)} wei")
        return ins_h, outs_h, "failed", trace

    def _block_at_ts(self, chain, chain_id, ts, head):
        """The first block at or after unix `ts`: the explorer's getblocknobytime, else bisection on RPC headers."""
        try:
            return int(self.explorer.block_at(chain_id, int(ts)))
        except Exception:  # noqa: BLE001 — Blockscout 500s on Optimism (Jake's seed 2026-10-09 15:33)
            pass
        # GALLOP BACK FROM THE HEAD, THEN BISECT (run Y1, 2026-10-14): bisecting from block 1 asked OP's public node
        # for block 79,093,726, before Bedrock, which it does not serve ("Block with id 0x4b6dfde not found")
        hi, step, lo = int(head), 1_000, None
        while lo is None:
            cand = max(1, int(head) - step)
            if self.reader.block_timestamp(chain, cand) < ts or cand == 1:
                lo = cand
            else:
                hi, step = cand, step * 2
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if self.reader.block_timestamp(chain, mid) < ts else (lo, mid)
        return hi

    def _window_scan(self, p, spec, chain, chain_id, token, holders, to_block, why, out) -> bool:
        """THE QUARTER, RECONCILED ON ITS OWN (Jake's seed 2026-10-09 15:33: Arbitrum's full history did not reconcile
        — 733,307.6 PENDLE of outflow missing — and Optimism's explorer answered HTTP 500). When the full-history scan
        cannot reconcile or cannot run, read only the last `window_days` days: every Transfer into and out of the
        holders, fresh (explorer, else RPC eth_getLogs), and require balanceOf(end) - balanceOf(start) = in - out TO
        THE WEI. Reconciled -> the window's daily series is stored (zeros observed), with the direct count from the same
        read; not -> nothing, and the numbers are named. Once a day. True when stored."""
        from .logcache import DailyChecks
        name, key, metric = p["name"], spec["key"], spec["metric"]
        days_n = int(spec.get("window_days") or 0)
        if not days_n:
            return False
        daily = DailyChecks(self.cache.root)
        day = str(today().date())
        if not daily.due(f"window:{key}", day):
            return False
        start = today() - pd.Timedelta(days=days_n)
        # A SEEDED HISTORY READS ONLY THE DAYS AFTER ITS LAST STORED ROW (Round G4 item 1, Katana: --seed katana_claims
        # stored the whole history; reading 100 days a day put a stalled RPC in front of every one of them). The window
        # starts at the newest stored day, so a stall gaps today only.
        last = self.last_dates.get((name, metric)) if spec.get("incremental_after_seed") else None
        if last is not None and pd.Timestamp(last).normalize() > start:
            start = pd.Timestamp(last).normalize()
            if start >= today().normalize() - pd.Timedelta(days=1):
                # CURRENT, NOT A GAP (G5 item 2: the Katana tier read 0 rows and the Gap Report opened a P4 row)
                out.mark_current(SOURCE, name, metric, f"{key}: up to date — stored through {start.date()} "
                                                       f"(seeded history; nothing newer to read)", TIER)
                daily.done(f"window:{key}", day)
                return True
        # ITS OWN BUDGET (fix round 2, R8: Pendle's Arbitrum gauge read "etherscan time budget exhausted before the first
        # attempt" — the fallback ran on the full-history scan's spent budget): a fresh window_budget_s for the window,
        # within this scan's share of the tier's budget (G4 item 1)
        clear, budget = getattr(self.explorer, "clear_budget", None), getattr(self.explorer, "start_budget", None)
        if clear:
            clear()
        if budget and not self.unbounded:
            left = self._share(float(spec.get("window_budget_s") or WINDOW_BUDGET_S))
            if left < WINDOW_FLOOR_S:
                out.fail(SOURCE, name, f"{key}: the Q0 window NOT read — {left:.0f}s left of this scan's share of its "
                                       f"tier's budget (needs >= {WINDOW_FLOOR_S:.0f}s); the next run reads it", TIER)
                return False
            budget(left)
        from .base import set_phase
        set_phase(f"scan {key} ({chain}): Q0 window from {start.date()} — finding the start block")
        try:
            b0 = self._block_at_ts(chain, chain_id, int(start.tz_localize("UTC").timestamp()), to_block)
            ins, outs, via = [], [], set()
            for h in holders:
                got, v = self._window_logs(chain, chain_id, token, [TRANSFER_TOPIC, None, pad_address(h)], b0, to_block)
                ins += got
                via.add(v)
                got, v = self._window_logs(chain, chain_id, token, [TRANSFER_TOPIC, pad_address(h), None], b0, to_block)
                outs += got
                via.add(v)
            # THE OPENING BALANCE IS A REAL PAST-STATE READ when the window's logs came from an explorer (Round G 1.2):
            # tip-minus-logged-tail at BOTH ends makes bal1 - bal0 the explorer's own window sum — a check that cannot fail
            tail_ok = all(str(v).startswith("RPC") for v in via)
            bal0 = sum(self._balance_at(chain, chain_id, token, h, b0 - 1, allow_tail=tail_ok) for h in holders)
            bal1 = sum(self._balance_at(chain, chain_id, token, h, to_block) for h in holders)
        except Exception as e:  # noqa: BLE001
            from .chain import redact_urls
            out.fail(SOURCE, name, f"{key}: the Q0-window reconciliation could not be read — {redact_urls(e)}", TIER)
            return False
        uniq = lambda evs: list({(e["transactionHash"], e["logIndex"]): e for e in evs}.values())  # noqa: E731
        ins, outs = uniq(ins), uniq(outs)
        internal = set(holders)
        net = sum(_amount(e) for e in ins if topic_address(e["topics"][1]) not in internal) \
            - sum(_amount(e) for e in outs if topic_address(e["topics"][2]) not in internal)
        holes, trace = [], []
        if bal1 - bal0 != net and not all(str(v).startswith("RPC") for v in via):
            ins, outs, holes, trace = self._patch_explorer_holes(chain, chain_id, token, holders, b0, to_block, ins,
                                                                 outs, bal0)
            if holes:
                net = sum(_amount(e) for e in ins if topic_address(e["topics"][1]) not in internal) \
                    - sum(_amount(e) for e in outs if topic_address(e["topics"][2]) not in internal)
                via.add("EXPLORER HOLE(S) read from the chain: " + ", ".join(
                    f"block {b:,} ({n} Transfer(s) the explorer does not index)" for b, n in holes))
        if bal1 - bal0 != net:
            out.fail(SOURCE, name, f"{key}: the Q0 WINDOW does not reconcile: blocks {b0:,}..{to_block:,}, "
                                   f"in-out {net} wei vs balanceOf change {bal1 - bal0} wei (difference "
                                   f"{bal1 - bal0 - net}). Read by {'; '.join(sorted(via))}. "
                                   + (f"EXPLORER-HOLE SEARCH: {' | '.join(trace)}. " if trace else
                                      "No explorer-hole search (the read was RPC only). ")
                                   + "Nothing stored.", TIER)
            return False
        excluded = {a.lower() for a in spec.get("exclude_counterparties") or []}
        counted = [e for e in outs if topic_address(e["topics"][2]) not in internal | excluded]
        try:
            dec = int(self.reader.erc20(chain, token).functions.decimals().call())
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{key}: decimals() failed: {e}", TIER)
            return False
        scale = 10 ** dec
        by_day = defaultdict(float)
        for e in counted:
            ts = int(e.get("timeStamp") or 0) or int(self.reader.block_timestamp(chain, int(e["blockNumber"])))
            by_day[pd.Timestamp(ts, unit="s").normalize()] += _amount(e) / scale
        days = pd.date_range(start + pd.Timedelta(days=1), today() - pd.Timedelta(days=1), freq="D")
        msg = (f"{key}: the full history could not be used ({why[:200]}); the Q0 WINDOW RECONCILED to the wei instead — "
               f"blocks {b0:,}..{to_block:,} ({start.date()}..), balanceOf change {(bal1 - bal0) / scale:,.4f} = in "
               f"{sum(_amount(e) for e in ins) / scale:,.4f} - out {sum(_amount(e) for e in outs) / scale:,.4f}; "
               f"{len(counted)} payout(s) to markets, {sum(by_day.values()):,.4f} in total; read by "
               f"{'; '.join(sorted(via))}")
        out.add(tidy([(d, by_day.get(d, 0.0)) for d in days], name, metric, f"{SOURCE}:{key}", TIER), SOURCE, name,
                f"{metric}: {msg}", TIER)
        if spec.get("direct_metric"):
            out.add(tidy([(d, by_day.get(d, 0.0)) for d in days], name, spec["direct_metric"],
                         f"{SOURCE}:direct:{key}", TIER), SOURCE, name,
                    f"{spec['direct_metric']}: the same fresh windowed read, reconciled to the wei — {msg}", TIER)
        out.log.append(LogEntry(SOURCE, name, 0, "ok", msg, TIER))
        daily.done(f"window:{key}", day)
        return True

    def _direct_count(self, name, spec, chain_id, token, holders, excluded, to_block, scale, out) -> None:
        from .logcache import DailyChecks
        key, metric = spec["key"], spec["direct_metric"]
        days_n = int(spec.get("direct_days", 100))
        daily = DailyChecks(self.cache.root)
        day = str(today().date())
        if not daily.due(f"direct:{key}", day):
            return
        start = today() - pd.Timedelta(days=days_n)
        try:
            b0 = self._block_at_ts(spec["chain"], chain_id, int(start.tz_localize("UTC").timestamp()), to_block)
            evs, ins, via = [], [], set()
            for h in holders:
                # explorer first, else RPC eth_getLogs (Optimism's Blockscout answered HTTP 500, 2026-10-09 15:33)
                got, v = self._window_logs(spec["chain"], chain_id, token, [TRANSFER_TOPIC, pad_address(h)], b0,
                                           to_block)
                evs += got
                via.add(v)
                # THE DIRECT COUNT RECONCILES TOO (X1 follow-up, Jake 2026-10-14: it read the outflows only, so an
                # explorer hole would have lowered it silently): the inflows over the same window, and balanceOf at
                # both ends — in − out must equal the change TO THE WEI, or nothing is stored
                got, v = self._window_logs(spec["chain"], chain_id, token, [TRANSFER_TOPIC, None, pad_address(h)],
                                           b0, to_block)
                ins += got
                via.add(v)
            tail_ok = all(str(v).startswith("RPC") for v in via)       # see _window_scan (Round G 1.2)
            bal0 = sum(self._balance_at(spec["chain"], chain_id, token, h, b0 - 1, allow_tail=tail_ok) for h in holders)
            bal1 = sum(self._balance_at(spec["chain"], chain_id, token, h, to_block) for h in holders)
        except Exception as e:  # noqa: BLE001
            from .chain import redact_urls
            out.fail(SOURCE, name, f"{key}: the direct count ({metric}) could not be read — {redact_urls(e)}", TIER)
            return
        internal = set(holders)
        uniq = lambda xs: list({(x["transactionHash"], int(x["logIndex"])): x for x in xs}.values())  # noqa: E731
        evs, ins = uniq(evs), uniq(ins)

        def _net():
            return sum(_amount(e) for e in ins if topic_address(e["topics"][1]) not in internal) \
                - sum(_amount(e) for e in evs if topic_address(e["topics"][2]) not in internal)
        holes, trace = [], []
        if bal1 - bal0 != _net() and not all(str(v).startswith("RPC") for v in via):
            ins, evs, holes, trace = self._patch_explorer_holes(spec["chain"], chain_id, token, holders, b0, to_block,
                                                                ins, evs, bal0)
        if bal1 - bal0 != _net():
            out.fail(SOURCE, name, f"{key}: the direct count ({metric}) does NOT RECONCILE: blocks {b0:,}..{to_block:,}, "
                                   f"in-out {_net()} wei vs balanceOf change {bal1 - bal0} wei (difference "
                                   f"{bal1 - bal0 - _net()}). Read by {'; '.join(sorted(via))}. "
                                   + (f"EXPLORER-HOLE SEARCH: {' | '.join(trace)}. " if trace else "")
                                   + "Nothing stored.", TIER)
            return
        if holes:
            via.add("EXPLORER HOLE(S) read from the chain: " + ", ".join(
                f"block {b:,} ({n} Transfer(s) the explorer does not index)" for b, n in holes))
        seen, by_day, n = set(), defaultdict(float), 0
        for e in evs:
            k = (str(e.get("transactionHash")), str(e.get("logIndex")))
            to = topic_address(e["topics"][2])
            if k in seen or to in internal or to in excluded:
                continue
            seen.add(k)
            n += 1
            ts = int(e.get("timeStamp") or 0) or int(self.reader.block_timestamp(spec["chain"], int(e["blockNumber"])))
            by_day[pd.Timestamp(ts, unit="s").normalize()] += _amount(e) / scale
        days = pd.date_range(start + pd.Timedelta(days=1), today() - pd.Timedelta(days=1), freq="D")
        frame = tidy([(d, by_day.get(d, 0.0)) for d in days], name, metric,
                     f"{SOURCE}:direct:{key}", TIER)
        out.add(frame, SOURCE, name,
                f"{metric} = {key} counted DIRECTLY: one windowed query from block {b0:,} (the first at or after "
                f"{start.date()}) to {to_block:,}, no cache, RECONCILED to the wei against balanceOf at both ends, "
                f"read by {'; '.join(sorted(via))}, {n:,} transfer(s) to "
                f"markets, {sum(by_day.values()):,.4f} in total; {len(frame)} complete day(s) {days[0].date()}..{days[-1].date()} stored — the reference "
                f"for the reconciled scan's series", TIER)
        daily.done(f"direct:{key}", day)

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
        r = decompose(ins[vault], outs[vault], mints, burns, int(since.timestamp()), set(labels),
                      bridge_shapes=bool(dec.get("bridge_shapes")), strategy=set(dec.get("strategy_counterparties") or ()),
                      oft=set(dec.get("oft_adapters") or ()))
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
        # THE REWARD-ONLY WALK (Jake's probes15/16): the same daily close with cross-chain share moves, strategy
        # deployments and OFT bridging held still (decompose.bridge_shapes / strategy_counterparties / oft_adapters).
        if dec.get("aps_reward_metric"):
            eod, rows, cur = r.get("aps_reward_eod") or {}, [], a0
            for d in days:
                if d < since.normalize():
                    continue
                cur = eod.get(d, cur)
                rows.append((d, cur))
            ar = r.get("aps_reward_end") or a1
            out.add(tidy(rows, name, dec["aps_reward_metric"], f"{SOURCE}:{key}.aps_reward_walk", TIER), SOURCE, name,
                    f"{dec['aps_reward_metric']} — REWARD-ONLY assets-per-share {a0:.6f} -> {ar:.6f} since "
                    f"{since.date()} ({ar / a0 - 1:+.3%}; all classes {a1 / a0 - 1:+.3%}): bridge, strategy and "
                    f"oft transactions held still", TIER)

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
    def _balance_at(self, chain: str, chain_id: int, token: str, holder: str, block: int,
                    allow_tail: bool = True) -> int:
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
        if not allow_tail:
            raise RuntimeError(f"{archive_err} — and the latest-minus-logged-tail stand-in is not used here: it is "
                               f"built from the same explorer logs being checked, so it cannot find their hole")
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

    def _scan(self, p: dict, spec: dict, window_days, out) -> str | None:
        name, key, metric = p["name"], spec["key"], spec["metric"]
        chain, token = spec["chain"], spec["token"]
        holders = [h.lower() for h in spec["holders"]]
        direction = spec["direction"]
        chain_id = config.CHAIN_IDS.get(chain)
        if chain_id is None:
            out.unconfigured(SOURCE, name, f"{key}: no chain id for {chain!r} in config.CHAIN_IDS", TIER)
            return
        # every query below is a Transfer of one token from / to a holder: the chain's opened transfer route counts
        if not (self.explorer.configured(chain_id, transfer=True) if _takes_transfer(self.explorer)
                else self.explorer.configured(chain_id)) and spec.get("rpc_window"):
            # NO EXPLORER, THE CHAIN'S OWN RPC (round G2 D1, Katana): a scan declared rpc_window reads only the window —
            # eth_getLogs on the chain's RPC and balanceOf at both ends, reconciled to the wei (_window_scan). There is
            # no full history to cache, so nothing else runs; an unreconciled window stores nothing, as everywhere.
            try:
                head = (int(self.reader.block_number(chain)) if hasattr(self.reader, "block_number")
                        else int(self.reader.web3(chain).eth.block_number))
            except Exception as e:  # noqa: BLE001
                from .chain import redact_urls
                out.fail(SOURCE, name, f"{key}: no RPC answered eth_blockNumber on {chain}: {redact_urls(e)}", TIER)
                return
            from .logcache import DailyChecks
            if not DailyChecks(self.cache.root).due(f"window:{key}", str(today().date())):
                out.mark_current(SOURCE, name, metric, f"{key}: the RPC window was read and stored today already "
                                                       f"(once a day)", TIER)
                return
            if not self._window_scan(p, spec, chain, chain_id, token, holders, head - CONFIRMATIONS,
                                     f"no explorer serves {chain} (chain {chain_id}); the RPC window is the route "
                                     f"({spec['rpc_window']})", out):
                out.gap(name, metric, reason=f"the {key} RPC window did not store today (see the log line above; "
                                             f"once a day)", tiers_attempted="2",
                        suggestion=f"Check the {chain} RPC ({', '.join(config.DEFAULT_RPC.get(chain) or [])}).")
            return
        if not (self.explorer.configured(chain_id, transfer=True) if _takes_transfer(self.explorer)
                else self.explorer.configured(chain_id)):
            routed = config.explorer_order(chain_id) + config.transfer_route(chain_id)
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
        self._chain_now = chain
        streams, fetched_new = [], 0     # (sid, cached state, new events, holder, direction)
        # ONE 120s budget for the whole scan, both providers included (config.EXPLORER_SCAN_BUDGET_S).
        budget = getattr(self.explorer, "start_budget", None)
        if budget and not self.unbounded:
            budget(self._share(float(spec.get("scan_budget_s") or config.EXPLORER_SCAN_BUDGET_S), WINDOW_FLOOR_S
                               if spec.get("window_days") else 0.0))
        from .base import set_phase
        set_phase(f"scan {key} ({chain}): reading new Transfer logs after the cached history")
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
                        new = self._date_undated(chain, new)
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
            if self._window_scan(p, spec, chain, chain_id, token, holders, to_block,
                                 f"seeding: {e}", out):
                return
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
            return "budget"                      # stopped on its share: LogScan.run's pass 2 may resume it (G5 item 2)
        except ExplorerRefused as e:
            if self._window_scan(p, spec, chain, chain_id, token, holders, to_block,
                                 f"refused: {e}", out):
                return
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

        import time as _t_rec
        t_rec0 = _t_rec.monotonic()                           # G6 item 0b: poison filter + reconcile + cache save, timed
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
        set_phase(f"scan {key} ({chain}): reconciling each holder's balanceOf to the wei")
        repairing = False
        recon = self._recon_state()
        for h in holders:
            # INCREMENTAL (Round G6 item 0b): from the last block this holder reconciled at, only the transfers after it
            # are summed onto the net proven there; with none, the stored net stands. balanceOf at the pinned block is
            # still read — it is the proof — and a mismatch falls back to the full sum before anything is refused.
            rkey = f"{chain}:{token.lower()}:{h.lower()}"
            prev = recon.get(rkey) or {}
            (a_in, n_in), (a_out, n_out) = ((_sum_after(ins[h], int(prev["block"])), _sum_after(outs[h], int(prev["block"])))
                                            if prev.get("block") is not None else ((0, 0), (0, 0)))
            # valid only while the events up to that block are the ones counted there (same number on each side)
            if (prev.get("block") is not None and int(prev["block"]) <= int(to_block)
                    and len(ins[h]) - n_in == int(prev.get("n_in", -1)) and len(outs[h]) - n_out == int(prev.get("n_out", -1))):
                pb = int(prev["block"])
                net = int(prev["net"]) + a_in - a_out
                how_net = f"incremental from block {pb:,} ({n_in + n_out} new transfer(s))"
            else:
                net = sum(_amount(e) for e in ins[h]) - sum(_amount(e) for e in outs[h])
                how_net = "full history"
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
            if net != bal and how_net != "full history":
                net = sum(_amount(e) for e in ins[h]) - sum(_amount(e) for e in outs[h])
                how_net = "full history (the incremental net disagreed)"
            if net == bal:
                recon[rkey] = {"block": int(to_block), "net": str(net), "key": key, "n_in": len(ins[h]),
                               "n_out": len(outs[h])}
            rtrace, rstatus = [], None
            if net != bal and hasattr(self.reader, "dated_logs"):
                # THE FULL HISTORY, REPAIRED FROM THE CHAIN (run review D1) — before falling back to the window
                try:
                    ins[h], outs[h], rstatus, rtrace = self._repair_history(spec, chain, chain_id, token, h, ins[h],
                                                                          outs[h], to_block)
                except Exception as e:  # noqa: BLE001 — the repair failing leaves the refusal, with what was tried
                    from .chain import redact_urls
                    rstatus, rtrace = "failed", rtrace + [f"stopped: {redact_urls(e)}"[:300]]
                for t in rtrace:
                    log.info("%s/%s: history repair: %s", name, key, t)
                net = sum(_amount(e) for e in ins[h]) - sum(_amount(e) for e in outs[h])
                repairing = repairing or rstatus == "in progress"
            if net != bal:
                why_full = (f"full history does not reconcile for {h} at block {to_block:,}: in-out {net} wei, balanceOf "
                            f"{bal} wei" + (f"; HISTORY REPAIR {rstatus.upper()}: {' | '.join(rtrace[-3:])}"
                                            if rstatus else ""))
                def keep_unproven():
                    # a repair in progress keeps the explorer's streams (unproven) to merge into next run
                    for sid, st, _new, h2, way in streams:
                        evs = (ins if way == "in" else outs)[h2]
                        if all(int(e.get("timeStamp") or 0) > 0 for e in evs):
                            self.cache.save(sid, evs, to_block, st.get("proven_to"))
                if self._window_scan(p, spec, chain, chain_id, token, holders, to_block, why_full, out):
                    if repairing:
                        keep_unproven()
                    else:
                        for sid, _, _, _, _ in streams:
                            log.info("%s/%s: logcache %s", name, key,
                                     self.cache.cut_back(sid, lambda e: int(e["blockNumber"])))
                    return
                out.fail(SOURCE, name,
                         f"{key}: SCAN DOES NOT RECONCILE for {h} at block {to_block:,}: in-out = "
                         f"{net} wei, balanceOf = {bal} wei, difference {bal - net} wei. NOTHING "
                         f"STORED. Served by {via} in {requests} request(s)."
                         + (f" HISTORY REPAIR {rstatus.upper()}: {' | '.join(rtrace)}" if rstatus else ""), TIER)
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
                # is dropped, so an unproven segment cannot keep this scan failing — UNLESS a history repair is in
                # progress, which needs the explorer's streams to merge into (run review D1)
                if repairing:
                    keep_unproven()
                    return
                for sid, _, _, _, _ in streams:
                    log.info("%s/%s: logcache %s", name, key,
                             self.cache.cut_back(sid, lambda e: int(e["blockNumber"])))
                return

        self._recon_save(recon)
        # RECONCILED: every stream is proven to the pinned block. Saved before attribution, which
        # reads the events but does not change them.
        # A stream holding an UNDATED event is not cached: step 5b refuses a counted one, and a
        # cached copy would refuse every later run too. It is re-read until the source dates it.
        for sid, st, new, h, way in streams:
            events = (ins if way == "in" else outs)[h]
            if all(int(e.get("timeStamp") or 0) > 0 for e in events):
                self.cache.save(sid, events, to_block, to_block)

        rec_note = f" Reconcile {_t_rec.monotonic() - t_rec0:.1f}s, net {how_net}."
        # 4. WHAT COUNTS.
        set_phase(f"scan {key} ({chain}): classifying the counted transfers")
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
        # TRANSACTIONS DECLARED NOT TO BE PURCHASES (config log_scans exclude_txs, each with its reason — Ether.fi's
        # 2026-09-02 Arrakis LP withdrawal, Jake 2026-10-12): never counted, listed under uncounted with the reason
        not_bought = {str(k).lower(): v for k, v in (spec.get("exclude_txs") or {}).items()}
        if direction == "in":
            for h in holders:
                for e in ins[h]:
                    frm = topic_address(e["topics"][1])
                    if frm in internal:
                        continue
                    if str(e.get("transactionHash") or "").lower() in not_bought:
                        uncounted[f"(excluded tx: {not_bought[str(e['transactionHash']).lower()][:60]})"] += _amount(e)
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
                   + rec_note
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
        set_phase(f"scan {key} ({chain}): dating the counted transfers into days")
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

        # THE DIRECT COUNT BESIDE IT (Pendle in_emissions, Jake's run 2026-10-09 ~14:10: the row compared the mainnet scan
        # with itself). Once a day: the same transfers queried afresh over the last direct_days days — one windowed
        # getLogs from the block at the window's start, no cache, reconciled against balanceOf at both ends (X1
        # follow-up, 2026-10-14) — bucketed by UTC day with the same
        # exclusions, stored as spec["direct_metric"]. pendle_emissions_q0's computation, as a stored reference.
        if spec.get("direct_metric") and direction == "out":
            self._direct_count(name, spec, chain_id, token, holders, excluded, to_block, scale, out)

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
