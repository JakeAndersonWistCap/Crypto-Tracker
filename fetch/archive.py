"""
fetch/archive.py — a year of history for every STATE-BASED series, read at past blocks. 2026-09-29.

WHY. Balance and supply series (a burn address, a staking pool, a treasury, a ve lock) were
built from point-in-time reads that started in September, so history_audit.py called them
SOURCE-LIMITED and every window over them was part-filled. The state they read exists at every
past block. GEODNET's balance flow proved the route (366 daily archive balances from Alchemy
Polygon in 222s); this generalises it (Jake, 2026-09-29):

  * THE SAME READ, AT A PAST BLOCK. fetch.chain.Chain runs in archive mode with the reader
    pinned to the FIRST BLOCK OF EACH UTC DAY (DayBlocks, the boundary search BalanceFlow uses).
    Same contracts, same sums, same source strings — marked `archive` (a registered source
    marker, stripped by _measuring_point), so a backfilled row never reads as a change of
    measuring point. Dated that day.
  * STOCKS ONLY, THEN FLOWS FROM THEM. A cumulative stock's flow (burn_address_balance ->
    gross_burn_tokens, etc.) is the difference of consecutive stored readings, through the same
    guard as the live path (derive_flow_from_cumulative).
  * NEVER OVERWRITES. Only (date, project, metric) keys with no row are written. A date whose
    stored neighbours were read from a DIFFERENT measuring point is refused with that reason —
    history of the current point before an older leg would break a declared handover.
  * WHOLE OR NOT AT ALL. A metric-day with any component unreadable is not stored.
  * RESUMABLE, BOUNDED. Newest days first (Q0 fills first), a time budget per invocation; the
    store and the block cache carry the progress. Re-run until nothing is missing.

WHAT IS NOT ARCHIVABLE, and why (archivable()): event-log reads (burn_transfer_logs), weekly
epoch reads (a call argument computed at run time, or an emission-tail branch), a chain with no
archive endpoint configured, and a Solana component with no daily history provider. The one
Solana provider is GEODNET's burn token account (SolanaAccountHistory: every transaction's
post-balance from getSignaturesForAddress + getTransaction).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pandas as pd

import config

from .base import FetchOutput, LONG_COLUMNS, _measuring_point, derive_flow_from_cumulative, today

log = logging.getLogger("token_metrics.fetch.archive")

ARCHIVE_DAYS = 365
DEFAULT_BUDGET_S = 20 * 60
NOT_ARCHIVABLE_KINDS = {"burn_transfer_logs"}


def cache_root() -> Path:
    import os
    return Path(os.environ.get("TOKEN_METRICS_LOGCACHE", ".cache/logscan"))


# ---------------------------------------------------------------------------------------------
# Which series can be backfilled
# ---------------------------------------------------------------------------------------------
def serving_contracts(p: dict, metric: str) -> list[tuple[str, dict]]:
    from .chain import KIND_METRIC, REFERENCE_ONLY_KINDS
    out = []
    for key, spec in (p.get("contracts") or {}).items():
        if spec["kind"] in REFERENCE_ONLY_KINDS:
            continue
        if (spec.get("metric_override") or KIND_METRIC.get(spec["kind"])) == metric:
            out.append((key, spec))
    return out


def state_metrics(p: dict) -> set[str]:
    """Every metric a contract read serves for this project."""
    from .chain import KIND_METRIC, REFERENCE_ONLY_KINDS
    return {spec.get("metric_override") or KIND_METRIC.get(spec["kind"])
            for spec in (p.get("contracts") or {}).values()
            if spec["kind"] not in REFERENCE_ONLY_KINDS} - {None}


def archivable(p: dict, metric: str, archive_chains: set[str], solana_keys: set) -> tuple[bool, str]:
    """(can every component of this metric be read at a past block, why not)."""
    comps = serving_contracts(p, metric)
    if not comps:
        return False, "no contract read serves it"
    for key, spec in comps:
        chain, kind = spec["chain"], spec["kind"]
        if kind in NOT_ARCHIVABLE_KINDS:
            return False, f"{key} is an event-log read ({kind}); its history is the log scan's, not state"
        if spec.get("call_arg") or spec.get("emission_tail") or spec.get("granularity") == "weekly":
            return False, f"{key} is a weekly epoch read (argument or branch computed at run time)"
        if kind in ("spl_token_account", "spl_mint"):
            if (p["name"], key) not in solana_keys:
                return False, f"{key} is a Solana {kind} with no daily history provider"
            continue
        if chain not in archive_chains:
            return False, (f"{key} is on {chain}, which has no archive endpoint configured "
                           f"({chain.upper()}_RPC_URL to an archive provider, checked by "
                           f"check_offline_items archive_probe)")
    return True, ""


# ---------------------------------------------------------------------------------------------
# First block of each UTC day
# ---------------------------------------------------------------------------------------------
class DayBlocks:
    """First block with timestamp >= each UTC day's start, cached per chain across runs."""

    def __init__(self, w3, chain: str, root: Path | None = None):
        self.w3, self.chain = w3, chain
        self.file = (root or cache_root()) / f"archive-blocks-{chain}.json"
        try:
            self.known = {k: int(v) for k, v in json.loads(self.file.read_text()).items()}
        except (OSError, ValueError):
            self.known = {}
        self._ts: dict[int, int] = {}

    def ts(self, n: int) -> int:
        if n not in self._ts:
            self._ts[n] = int(self.w3.eth.get_block(n)["timestamp"])
        return self._ts[n]

    def save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.known, sort_keys=True))
        tmp.replace(self.file)

    def at(self, day: pd.Timestamp, head: int) -> int:
        k = str(day.date())
        if k in self.known:
            return self.known[k]
        target = int(pd.Timestamp(day).tz_localize("UTC").timestamp())
        lo, hi = (0, self.ts(0)), (head, self.ts(head))
        for d, b in self.known.items():          # known boundaries narrow the search
            t = int(pd.Timestamp(d).tz_localize("UTC").timestamp())
            if t < target and b - 1 > lo[0]:
                lo = (b - 1, self.ts(b - 1))
            if t >= target and b < hi[0]:
                hi = (b, self.ts(b))
        if hi[1] < target:
            raise ValueError(f"{self.chain}: {day.date()} is after the head block")
        step = 0
        while hi[0] - lo[0] > 1:
            step += 1
            if step % 3 and hi[1] > lo[1]:
                guess = lo[0] + int((target - lo[1]) * (hi[0] - lo[0]) / (hi[1] - lo[1]))
            else:
                guess = (lo[0] + hi[0]) // 2
            guess = min(max(guess, lo[0] + 1), hi[0] - 1)
            t = self.ts(guess)
            lo, hi = ((guess, t), hi) if t < target else (lo, (guess, t))
        self.known[k] = hi[0]
        return hi[0]


def archive_ok(w3, block: int) -> tuple[bool, str]:
    """Does this endpoint serve STATE at `block`? eth_getBalance there is the cheapest test."""
    try:
        w3.eth.get_balance("0x0000000000000000000000000000000000000000", block_identifier=block)
        return True, "state served"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:160]}"


# ---------------------------------------------------------------------------------------------
# Solana: an SPL token account's balance at each day start, from its own transactions
# ---------------------------------------------------------------------------------------------
class SolanaAccountHistory:
    """Every transaction touching `account` in the window, with the account's balance AFTER it.

    getSignaturesForAddress pages back (1,000 at a time) to the window start; getTransaction
    (jsonParsed) gives pre/post token balances, read for this account by its index in the
    message's account keys — exactly as check_offline_items geod_solana_burn_account does. Cached
    per signature, so a run stopped by the budget or a 429 resumes where it stopped.
    """

    def __init__(self, rpc=None, account: str = "", decimals: int = 9, root: Path | None = None,
                 pace_s: float = 0.35):
        from .solana import SolanaRPC
        self.rpc, self.account, self.decimals, self.pace = rpc or SolanaRPC(), account, decimals, pace_s
        self.file = (root or cache_root()) / f"solana-history-{account[:12]}.json"
        try:
            self.state = json.loads(self.file.read_text())
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("sigs", {})          # sig -> [blockTime, pre_raw, post_raw]
        self.state.setdefault("listed_to", None)   # oldest blockTime listed

    def save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state))
        tmp.replace(self.file)

    def _bal(self, tx: dict, which: str):
        keys = [k.get("pubkey") if isinstance(k, dict) else k
                for k in ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []]
        for b in (tx.get("meta") or {}).get(which) or []:
            i = b.get("accountIndex")
            if i is not None and i < len(keys) and keys[i] == self.account:
                return int((b.get("uiTokenAmount") or {}).get("amount") or 0)
        return None

    def fill(self, since_ts: int, deadline: float) -> tuple[bool, str]:
        """List and read transactions back to since_ts. (complete, note)."""
        sigs, before = [], None
        while time.monotonic() < deadline:
            opts = {"limit": 1000, "commitment": "finalized"}
            if before:
                opts["before"] = before
            page = self.rpc.call("getSignaturesForAddress", [self.account, opts]) or []
            time.sleep(self.pace)
            if not page:
                break
            sigs += [s for s in page if not s.get("err")]
            before = page[-1]["signature"]
            if int(page[-1].get("blockTime") or 0) < since_ts:
                break
        todo = [s for s in sigs if s["signature"] not in self.state["sigs"]
                and int(s.get("blockTime") or 0) >= since_ts - 86_400]
        done = 0
        for s in todo:
            if time.monotonic() >= deadline:
                self.save()
                return False, f"budget spent: {done} of {len(todo)} transaction(s) read this run"
            tx = self.rpc.call("getTransaction", [s["signature"], {"encoding": "jsonParsed",
                                                                   "maxSupportedTransactionVersion": 0,
                                                                   "commitment": "finalized"}]) or {}
            time.sleep(self.pace)
            self.state["sigs"][s["signature"]] = [int(s.get("blockTime") or tx.get("blockTime") or 0),
                                                  self._bal(tx, "preTokenBalances"),
                                                  self._bal(tx, "postTokenBalances")]
            done += 1
        self.save()
        return True, f"{len(sigs)} signature(s) listed, {done} transaction(s) read this run"

    def daily(self, days: list[pd.Timestamp]) -> dict[pd.Timestamp, float]:
        """Balance at each day's START: after the last transaction before it. A day before the
        earliest transaction read takes that transaction's PRE balance (0 if the account did not
        exist). A fall is impossible for a burn sink and refuses the whole series."""
        rows = sorted((v for v in self.state["sigs"].values() if v[0]), key=lambda v: v[0])
        if not rows:
            return {}
        scale = 10 ** self.decimals
        out, i, last = {}, 0, (rows[0][1] or 0)
        prev_post = None
        for v in rows:
            if prev_post is not None and v[2] is not None and v[2] < prev_post:
                raise ValueError(f"{self.account}: balance FELL {prev_post} -> {v[2]} — not a burn sink")
            prev_post = v[2] if v[2] is not None else prev_post
        for d in sorted(days):
            t = int(pd.Timestamp(d).tz_localize("UTC").timestamp())
            while i < len(rows) and rows[i][0] < t:
                if rows[i][2] is not None:
                    last = rows[i][2]
                i += 1
            out[d] = last / scale
        return out


# ---------------------------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------------------------
def _write_new(store, frame: pd.DataFrame) -> int:
    """Insert rows whose (date, project, metric) key holds nothing. Never an overwrite."""
    if frame.empty:
        return 0
    have = set(store.conn.execute(
        "SELECT date, project, metric FROM metrics WHERE project IN (%s)" %
        ",".join("?" * frame["project"].nunique()), tuple(frame["project"].unique())).fetchall())
    f = frame.copy()
    f["d"] = pd.to_datetime(f["date"]).dt.strftime("%Y-%m-%d")
    f = f[[(d, p, m) not in have for d, p, m in zip(f["d"], f["project"], f["metric"])]]
    return store.upsert(f[LONG_COLUMNS]) if not f.empty else 0


def neighbours_agree(stored: pd.DataFrame, day: pd.Timestamp, source: str) -> tuple[bool, str]:
    """The nearest stored rows either side were read from the same measuring point."""
    mp = _measuring_point(source)
    before = stored[stored["date"] < day].tail(1)
    after = stored[stored["date"] > day].head(1)
    for side, r in (("before", before), ("after", after)):
        if len(r) and _measuring_point(str(r["source"].iloc[0])) != mp:
            return False, (f"the stored row {side} {day.date()} ({r['date'].iloc[0].date()}) was read "
                           f"from {_measuring_point(str(r['source'].iloc[0]))}, not {mp}")
    return True, ""


def derive_flows(store, project: str, stock: str) -> int:
    """A cumulative stock's flow for every consecutive pair of stored readings whose later date
    has no flow row yet — the live path's guard, so a change of point or a fall is refused."""
    flow = config.cumulative_flow_for(project, stock)
    if not flow:
        return 0
    rows = store.conn.execute("SELECT date, value, source FROM metrics WHERE project=? AND metric=? "
                              "ORDER BY date", (project, stock)).fetchall()
    have = {d for (d,) in store.conn.execute("SELECT date FROM metrics WHERE project=? AND metric=?",
                                             (project, flow))}
    out, frames = FetchOutput(), []
    for (d0, v0, s0), (d1, v1, s1) in zip(rows, rows[1:]):
        if d1 in have:
            continue
        f = derive_flow_from_cumulative(v1, v0, project, flow, config.mark_source(s1, "delta"), 2,
                                        pd.Timestamp(d1), prior_date=d0, stock_metric=stock, out=out,
                                        prior_source=s0, source_base=s1)
        if not f.empty:
            frames.append(f)
    return _write_new(store, pd.concat(frames)) if frames else 0


class ArchiveBackfill:
    def __init__(self, store, projects: list[dict], budget_s: float = DEFAULT_BUDGET_S,
                 reader=None, solana: dict | None = None, days: int = ARCHIVE_DAYS):
        from .chain import ChainReader
        self.store, self.projects, self.budget_s, self.days = store, projects, budget_s, days
        self.reader = reader or ChainReader()
        # {(project, contract key): SolanaAccountHistory}
        self.solana = solana or {}
        self.report: list[str] = []

    def targets(self, archive_chains: set[str]) -> tuple[dict, list[tuple[str, str, str]]]:
        """({project: {metric}}, [(project, metric, why not)])."""
        ok, no = {}, []
        for p in self.projects:
            for m in sorted(state_metrics(p)):
                good, why = archivable(p, m, archive_chains, set(self.solana))
                if good:
                    ok.setdefault(p["name"], set()).add(m)
                else:
                    no.append((p["name"], m, why))
        return ok, no

    def run(self, archive_chains: set[str]) -> dict:
        start = time.monotonic()
        deadline = start + self.budget_s
        targets, skipped = self.targets(archive_chains)
        long = self.store.load_long()
        long = long[~long["is_manual"]] if "is_manual" in long else long
        stored = {k: g.sort_values("date") for k, g in long.groupby(["project", "metric"])}
        end = today()
        days = [end - pd.Timedelta(days=i) for i in range(1, self.days + 1)]      # newest first

        def missing(name, m, d):
            g = stored.get((name, m))
            return g is None or not (g["date"] == d).any()

        chains = {spec["chain"] for p in self.projects if p["name"] in targets
                  for m in targets[p["name"]] for _, spec in serving_contracts(p, m)
                  if spec["chain"] != "solana"}
        clocks, heads = {}, {}
        for c in sorted(chains):
            w3 = self.reader.web3(c)
            heads[c] = int(w3.eth.block_number)
            clocks[c] = DayBlocks(w3, c)
        # Solana histories first: their daily balances are components of the sums.
        sol_daily = {}
        for key, hist in self.solana.items():
            done, note = hist.fill(int(pd.Timestamp(days[-1]).tz_localize("UTC").timestamp()), deadline)
            self.report.append(f"solana {key[0]}/{key[1]}: {note}" + ("" if done else " — INCOMPLETE; "
                               "its sums wait for the next run"))
            if done:
                sol_daily[key] = hist.daily(days)
        written, refused = {}, {}
        from .chain import Chain
        for d in days:
            if time.monotonic() >= deadline:
                self.report.append(f"budget spent at {d.date()}; re-run to continue")
                break
            want = {}
            for name, ms in targets.items():
                p = config.PROJECT_BY_NAME[name]
                ms = {m for m in ms if missing(name, m, d) and (name, m) not in refused
                      and all((name, k) in sol_daily for k, s in serving_contracts(p, m)
                              if s["kind"] in ("spl_token_account", "spl_mint"))}
                if ms:
                    want[name] = ms
            if not want:
                continue
            for c in chains:
                self.reader.at_block[c] = clocks[c].at(d, heads[c])
            ch = Chain(backfill={"day": d, "metrics": want,
                                 "solana": {k: v.get(d) for k, v in sol_daily.items()}})
            ch.reader = self.reader
            out = FetchOutput()
            ch.run([config.PROJECT_BY_NAME[n] for n in want], None, out)
            frame = out.frame()
            keep = []
            for r in frame.itertuples(index=False):
                lo, hi = config.sanity_bounds(r.project, r.metric)
                if (lo is not None and r.value < lo) or (hi is not None and r.value > hi):
                    refused[(r.project, r.metric)] = (f"{r.value:,.4f} on {d.date()} is outside the "
                                                      f"declared bound [{lo}, {hi}]")
                    continue
                g = stored.get((r.project, r.metric))
                ok, why = (True, "") if g is None else neighbours_agree(g, d, r.source)
                if not ok:
                    refused[(r.project, r.metric)] = why
                    continue
                keep.append(r)
            if keep:
                kf = pd.DataFrame(keep)
                n = _write_new(self.store, kf)
                for r in keep:
                    written[(r.project, r.metric)] = written.get((r.project, r.metric), 0) + 1
                    row = pd.DataFrame([{"date": d, "value": r.value, "source": r.source}])
                    g = stored.get((r.project, r.metric))
                    stored[(r.project, r.metric)] = (row if g is None else
                                                     pd.concat([g, row]).sort_values("date"))
                log.info("archive %s: %d row(s)", d.date(), n)
        self.reader.at_block.clear()
        for c in clocks.values():
            c.save()
        flows = {}
        for (name, m) in written:
            n = derive_flows(self.store, name, m)
            if n:
                flows[(name, config.cumulative_flow_for(name, m))] = n
        return {"written": written, "flows": flows, "refused": refused, "skipped": skipped,
                "seconds": round(time.monotonic() - start), "report": self.report}


# ---------------------------------------------------------------------------------------------
# NEAR: the block header's total_supply, and validator stake, at the first block of each day
# ---------------------------------------------------------------------------------------------
NEAR_ARCHIVAL_DEFAULT = ("https://archival-rpc.mainnet.fastnear.com",
                         "https://archival-rpc.mainnet.near.org")


def near_archival_endpoints() -> list[str]:
    import os
    env = os.environ.get("NEAR_ARCHIVAL_RPC_URL", "").strip()
    return [u.strip() for u in env.split(",") if u.strip()] or list(NEAR_ARCHIVAL_DEFAULT)


class NearArchive:
    """NEAR's archival RPC (free). `block` by height for header.total_supply and timestamp;
    `validators` by block for the stake of the epoch that block is in. A height with no block
    (NEAR skips heights) is stepped over. Source strings match the live near_rpc reads, marked
    `archive`."""

    def __init__(self, http=None, endpoints: list[str] | None = None, root: Path | None = None):
        from .base import Http
        self.http = http or Http(min_interval=0.25, retries=1)
        self.endpoints = endpoints or near_archival_endpoints()
        self.file = (root or cache_root()) / "archive-blocks-near.json"
        try:
            self.known = {k: int(v) for k, v in json.loads(self.file.read_text()).items()}
        except (OSError, ValueError):
            self.known = {}
        self._hdr: dict[int, dict] = {}

    def rpc(self, method: str, params):
        errs = []
        for url in self.endpoints:
            try:
                j = self.http.post(url, json_body={"jsonrpc": "2.0", "id": "tm", "method": method,
                                                   "params": params})
            except Exception as e:  # noqa: BLE001
                errs.append(f"{url}: {e}")
                continue
            if isinstance(j, dict) and j.get("error"):
                errs.append(f"{url}: {str(j['error'])[:160]}")
                continue
            return j["result"]
        raise RuntimeError("; ".join(errs))

    def header(self, height: int) -> dict:
        """The header at `height`, or at the next height that has a block (max 50 steps)."""
        for h in range(height, height + 50):
            if h in self._hdr:
                return self._hdr[h]
            try:
                hdr = self.rpc("block", {"block_id": h})["header"]
            except RuntimeError as e:
                if "UNKNOWN_BLOCK" in str(e) or "DB Not Found" in str(e) or "not found" in str(e).lower():
                    continue
                raise
            self._hdr[h] = hdr
            return hdr
        raise RuntimeError(f"no NEAR block in heights {height}..{height + 49}")

    def at(self, day: pd.Timestamp) -> dict:
        """The header of the first block at or after the day's start."""
        k = str(day.date())
        if k in self.known:
            return self.header(self.known[k])
        target = int(pd.Timestamp(day).tz_localize("UTC").timestamp()) * 10**9
        head = self.rpc("block", {"finality": "final"})["header"]
        lo_h, lo_t = 9_820_210, None          # genesis height of NEAR mainnet
        lo_t = int(self.header(lo_h)["timestamp"])
        hi_h, hi_t = int(head["height"]), int(head["timestamp"])
        while hi_h - lo_h > 1:
            guess = lo_h + max(1, min(hi_h - lo_h - 1, int((target - lo_t) * (hi_h - lo_h) / max(hi_t - lo_t, 1))))
            hdr = self.header(guess)
            h, t = int(hdr["height"]), int(hdr["timestamp"])
            if h >= hi_h:
                hi_h = guess
                continue
            if t < target:
                lo_h, lo_t = h, t
            else:
                hi_h, hi_t = h, t
        self.known[k] = hi_h
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(self.known, sort_keys=True))
        return self.header(hi_h)

    def rows(self, p: dict, day: pd.Timestamp) -> list[dict]:
        """total_supply_protocol and (if the node answers for that epoch) locked_tokens."""
        api = p.get("near_validators") or {}
        exp = int(api.get("yocto_exponent", 24))
        hdr = self.at(day)
        out = [{"date": day, "project": p["name"], "metric": "total_supply_protocol",
                "value": int(hdr["total_supply"]) / 10**exp,
                "source": config.mark_source("near_rpc:block.header.total_supply", "archive"), "tier": 2}]
        try:
            v = self.rpc("validators", {"block_id": int(hdr["height"])})
            stake = sum(int(x["stake"]) for x in v.get("current_validators") or [])
            if stake > 0:
                out.append({"date": day, "project": p["name"], "metric": api.get("metric", "locked_tokens"),
                            "value": stake / 10**exp,
                            "source": config.mark_source("near_rpc:validators", "archive"), "tier": 2})
        except RuntimeError as e:
            log.info("near validators at %s: %s", day.date(), e)
        return out
