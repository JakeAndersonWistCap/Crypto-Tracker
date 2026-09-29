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

from .base import FetchOutput, LONG_COLUMNS, redact, _measuring_point, derive_flow_from_cumulative, today

log = logging.getLogger("token_metrics.fetch.archive")

ARCHIVE_DAYS = 365
DEFAULT_BUDGET_S = 20 * 60
NOT_ARCHIVABLE_KINDS = {"burn_transfer_logs"}


WEEK_S = 7 * 86_400


def weekly(p: dict, metric: str) -> bool:
    return any(s.get("granularity") == "weekly" for _, s in serving_contracts(p, metric))


def read_day(p: dict, metric: str, d: pd.Timestamp) -> bool:
    """Is day d a day to read this metric? Daily series: every day. A WEEKLY epoch series: the day
    AFTER an epoch starts (epochs start on unix-week boundaries, Thursdays 00:00 UTC) — a day in,
    the epoch's checkpoint (Minter.updatePeriod) has run, and the read files under that epoch."""
    if not weekly(p, metric):
        return True
    return int(pd.Timestamp(d).tz_localize("UTC").timestamp()) % WEEK_S == 86_400


def stored_date(p: dict, metric: str, d: pd.Timestamp) -> pd.Timestamp:
    """The date a read on day d is FILED under: d, or for a weekly series the epoch start
    (minus a week for the last-complete-week argument) — fetch.chain.Chain._epoch_start's rule."""
    if not weekly(p, metric):
        return d
    from .chain import Chain
    spec = next(s for _, s in serving_contracts(p, metric) if s.get("granularity") == "weekly")
    return Chain._epoch_start(spec, int(pd.Timestamp(d).tz_localize("UTC").timestamp()))


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
        # A WEEKLY EPOCH READ IS ARCHIVABLE (Jake, 2026-09-30): its argument is the week's start,
        # deterministic for any past date, and the tail branch's legs are state reads at the pin.
        # Any other run-time argument is not.
        if spec.get("call_arg") and spec["call_arg"] != "last_complete_week_unix":
            return False, f"{key} takes a run-time argument ({spec['call_arg']}) with no past equivalent"
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
# ---------------------------------------------------------------------------------------------
# ONE RESOLVER for "which endpoint serves state a year back" — the probe, the backfill and every
# historical read use it. 2026-09-29 (Jake's run: the backfill tested only the FIRST endpoint that
# connected, so publicnode's 403 / "historical state not available" ended every EVM chain, while
# archive_probe had found eth.drpc.org, Alchemy Polygon and mainnet.base.org serving it).
# ---------------------------------------------------------------------------------------------
_RESOLVED: dict[str, tuple] = {}


def load_env() -> None:
    """.env, as token_metrics.py loads it — archive_backfill.py never did, so POLYGON_RPC_URL was
    not in the endpoint list at all."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass


def block_seconds(w3, head: int, span: int = 10_000) -> float:
    """Average block time over the last `span` blocks, from the two timestamps."""
    th = int(w3.eth.get_block(head)["timestamp"])
    tp = int(w3.eth.get_block(max(head - span, 1))["timestamp"])
    return max((th - tp) / max(min(span, head - 1), 1), 1e-3)


def block_days_back(w3, days: float) -> int:
    """A block ~`days` back, estimated from the recent block time — NEVER block 0: Base's pruned
    public node refused "requested 0, earliest available 50500000"."""
    head = int(w3.eth.block_number)
    return max(head - int(days * 86_400 / block_seconds(w3, head)), 1)


def resolve_archive(chain: str, days: float = ARCHIVE_DAYS, attempts: list | None = None,
                    every: bool = False):
    """(w3, host) of the first endpoint serving STATE ~`days` back, or (None, None).

    Order: <CHAIN>_RPC_URL from the environment first, then every configured endpoint in turn
    (fetch.chain.rpc_endpoints); the host that answered last time is tried first. Each is tested
    with eth_getBalance of the zero address at a block ~`days` back. `attempts` collects
    (host, verdict) for every endpoint tried — hosts only, never a URL with a key in it.
    every=True (the probe) tests every endpoint and still returns the first that served."""
    from .chain import ChainReader, rpc_endpoints, rpc_host
    if chain in _RESOLVED and attempts is None:
        return _RESOLVED[chain]
    load_env()
    urls = rpc_endpoints(chain)
    memo = cache_root() / "archive-endpoints.json"
    try:
        pref = json.loads(memo.read_text()).get(chain)
    except (OSError, ValueError):
        pref = None
    urls.sort(key=lambda u: 0 if pref and rpc_host(u) == pref else 1)
    found = (None, None)
    for url in urls:
        host = rpc_host(url)
        try:
            w3 = ChainReader.make_web3(chain, url, 30)
            blk = block_days_back(w3, days)
            ok, why = archive_ok(w3, blk)
            verdict = f"block {blk:,}: {'SERVED' if ok else 'REFUSED — ' + why}"
        except Exception as e:  # noqa: BLE001
            # REDACTED: a provider's exception carries the full URL, key and all (Jake's probe
            # printed an Alchemy key this way, 2026-09-29).
            ok, verdict = False, f"FAILED — {type(e).__name__}: {redact(e)[:140]}"
        if attempts is not None:
            attempts.append((host, verdict))
        if ok and found[0] is None:
            found = (w3, host)
        if ok and not every:
            _RESOLVED[chain] = (w3, host)
            try:
                d = json.loads(memo.read_text()) if memo.exists() else {}
            except (OSError, ValueError):
                d = {}
            d[chain] = host
            memo.parent.mkdir(parents=True, exist_ok=True)
            memo.write_text(json.dumps(d, sort_keys=True))
            return w3, host
    _RESOLVED[chain] = found
    return found


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
        hi = (head, self.ts(head))
        # A LOWER BOUND FROM THE BLOCK TIME, NEVER BLOCK 0 (a pruned node refuses it): step back
        # from an estimate until a block is before the target, doubling the step.
        bt = max((hi[1] - self.ts(max(head - 10_000, 1))) / min(10_000, max(head - 1, 1)), 1e-3)
        guess = max(head - int((hi[1] - target) / bt * 1.02) - 100, 1)
        step = max(int(86_400 / bt), 100)
        while guess > 1 and self.ts(guess) >= target:
            guess, step = max(guess - step, 1), step * 2
        lo = (guess, self.ts(guess))
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
        return False, f"{type(e).__name__}: {redact(e)[:160]}"


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
        self.hosts: dict[str, str] = {}      # chain -> host serving archive state (resolver)
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
            # EVERY HISTORICAL READ GOES THROUGH THE ENDPOINT THAT SERVES OLD STATE (the resolver),
            # not the first one that connects. A reader injected by a test keeps its own.
            if c not in self.hosts and hasattr(self.reader, "_w3"):
                w3r, host = resolve_archive(c)
                if w3r is not None:
                    self.reader._w3[c] = w3r
                    self.hosts[c] = host
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
                ms = {m for m in ms if read_day(p, m, d) and missing(name, m, stored_date(p, m, d))
                      and (name, m) not in refused
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
                rd = pd.Timestamp(r.date)
                ok, why = (True, "") if g is None else neighbours_agree(g, rd, r.source)
                if not ok:
                    refused[(r.project, r.metric)] = why
                    continue
                keep.append(r)
            if keep:
                kf = pd.DataFrame(keep)
                n = _write_new(self.store, kf)
                for r in keep:
                    written[(r.project, r.metric)] = written.get((r.project, r.metric), 0) + 1
                    row = pd.DataFrame([{"date": pd.Timestamp(r.date), "value": r.value, "source": r.source}])
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
                "seconds": round(time.monotonic() - start), "report": self.report,
                "served_by": {k: sorted({self.hosts.get(s["chain"], "solana" if s["chain"] == "solana" else "?")
                                         for _, s in serving_contracts(config.PROJECT_BY_NAME[k[0]], k[1])})
                              for k in written}}


# ---------------------------------------------------------------------------------------------
# NEAR: the block header's total_supply, and validator stake, at the first block of each day
# ---------------------------------------------------------------------------------------------
NEAR_ARCHIVAL_DEFAULT = ("https://archival-rpc.mainnet.fastnear.com",
                         "https://archival-rpc.mainnet.near.org")


def near_archival_endpoints() -> list[str]:
    import os
    env = os.environ.get("NEAR_ARCHIVAL_RPC_URL", "").strip()
    return [u.strip() for u in env.split(",") if u.strip()] or list(NEAR_ARCHIVAL_DEFAULT)


class NearBudgetSpent(RuntimeError):
    """The run's time budget ran out inside a NEAR call; what is written stays, re-run to resume."""


def _near_post(url: str, body: dict):
    import requests
    from .base import USER_AGENT
    r = requests.post(url, json=body, headers={"User-Agent": USER_AGENT}, timeout=(10, 30))
    try:
        j = r.json()
    except ValueError:
        j = None
    return r.status_code, j, dict(r.headers)


class NearArchive:
    """NEAR's archival RPC (free): `block` by height for header.total_supply and timestamp. A
    height with no block (NEAR skips heights) is stepped over. Source strings match the live
    near_rpc reads, marked `archive`.

    PACED TO THE PROVIDERS' LIMITS (Jake's run 2026-09-29: fastnear and near.org both answered 429
    after ~20 calls at 4/s). Calls are spaced by an ADAPTIVE pace, starting from the pace that last
    worked (saved with the block cache): every 429 waits (Retry-After, or 5 -> 15 -> 30 -> 60 ->
    120s) and slows the pace x1.5 (to at most 15s/call); 25 clean calls in a row speed it x0.9
    (to at least 0.5s). The same endpoint is waited out before moving to the next. Boundaries and
    pace persist, and the caller skips days already stored, so a pass stopped by the budget or a
    limit resumes where it stopped. `pace` and `stats` say what worked."""

    PACE_START, PACE_MIN, PACE_MAX = 1.0, 0.5, 15.0
    BACKOFFS = (5, 15, 30, 60, 120)

    def __init__(self, http=None, endpoints: list[str] | None = None, root: Path | None = None,
                 post=None, sleep=None, deadline: float | None = None):
        # `http` (an object with .post(url, json_body)) is kept for callers that pass one.
        if post is None and http is not None:
            def post(url, body, _h=http):
                return 200, _h.post(url, json_body=body), {}
        self.post = post or _near_post
        self.sleep = sleep or time.sleep
        self.deadline = deadline
        self.endpoints = endpoints or near_archival_endpoints()
        self.file = (root or cache_root()) / "archive-blocks-near.json"
        try:
            raw = json.loads(self.file.read_text())
        except (OSError, ValueError):
            raw = {}
        if "known" not in raw:                   # the first format held the boundaries alone
            raw = {"known": raw}
        self.known = {k: int(v) for k, v in (raw.get("known") or {}).items()}
        self.pace = float(raw.get("pace") or self.PACE_START)
        self._hdr: dict[int, dict] = {}
        self._last = 0.0
        self._streak = 0
        self.stats = {"calls": 0, "429s": 0, "waited_s": 0.0, "endpoint": None}

    def save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"known": self.known, "pace": round(self.pace, 3)}, sort_keys=True))
        tmp.replace(self.file)

    def _nap(self, seconds: float) -> None:
        if self.deadline is not None and time.monotonic() + seconds > self.deadline:
            self.save()
            raise NearBudgetSpent(f"time budget spent (a {seconds:.0f}s wait would overrun it)")
        if seconds > 0:
            self.stats["waited_s"] += seconds
            self.sleep(seconds)

    def rpc(self, method: str, params):
        from .chain import rpc_host
        body = {"jsonrpc": "2.0", "id": "tm", "method": method, "params": params}
        errs = []
        for url in self.endpoints:
            for attempt in range(len(self.BACKOFFS) + 1):
                self._nap(self._last + self.pace - time.monotonic())
                self._last = time.monotonic()
                self.stats["calls"] += 1
                try:
                    status, j, headers = self.post(url, body)
                except Exception as e:  # noqa: BLE001 — a transport error: back off, retry
                    errs.append(f"{rpc_host(url)}: {type(e).__name__}")
                    if attempt < len(self.BACKOFFS):
                        self._nap(self.BACKOFFS[attempt])
                    continue
                if status == 429 or (status and status >= 500):
                    self._streak = 0
                    if status == 429:
                        self.stats["429s"] += 1
                        self.pace = min(self.pace * 1.5, self.PACE_MAX)
                    errs.append(f"{rpc_host(url)}: HTTP {status}")
                    if attempt < len(self.BACKOFFS):
                        from .base import retry_after
                        asked = retry_after((headers or {}).get("Retry-After"))
                        self._nap(min(max(asked or 0.0, self.BACKOFFS[attempt]), 300.0))
                    continue
                if isinstance(j, dict) and j.get("error"):
                    raise RuntimeError(f"{rpc_host(url)}: {str(j['error'])[:200]}")
                self._streak += 1
                if self._streak >= 25:
                    self.pace, self._streak = max(self.pace * 0.9, self.PACE_MIN), 0
                self.stats["endpoint"] = rpc_host(url)
                return (j or {}).get("result")
        self.save()
        raise RuntimeError("no NEAR archival endpoint answered — " + "; ".join(errs[-6:]))

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
        """The header of the first block at or after the day's start. Known day boundaries bracket
        the search (newest-first passes make neighbouring days tight), and with none below, a
        lower bound is stepped back from the head at the head's own block rate — never genesis."""
        k = str(day.date())
        if k in self.known:
            return self.header(self.known[k])
        target = int(pd.Timestamp(day).tz_localize("UTC").timestamp()) * 10**9
        head = self.rpc("block", {"finality": "final"})["header"]
        hi_h, hi_t = int(head["height"]), int(head["timestamp"])
        above = [h for d, h in self.known.items()
                 if int(pd.Timestamp(d).tz_localize("UTC").timestamp()) * 10**9 >= target]
        below = [h for d, h in self.known.items()
                 if int(pd.Timestamp(d).tz_localize("UTC").timestamp()) * 10**9 < target]
        if above and min(above) < hi_h:                  # one header each, not one per known day
            hi_h = min(above)
            hi_t = int(self.header(hi_h)["timestamp"])
        lo = None
        if below:
            lo = (max(below), int(self.header(max(below))["timestamp"]))
        else:
            ref = self.header(max(hi_h - 100_000, 1))
            rate = (hi_t - int(ref["timestamp"])) / max(hi_h - int(ref["height"]), 1)   # ns/block
            guess, step = max(hi_h - int((hi_t - target) / rate * 1.02) - 1_000, 1), 100_000
            while True:
                hdr = self.header(guess)
                if int(hdr["timestamp"]) < target:
                    lo = (int(hdr["height"]), int(hdr["timestamp"]))
                    break
                guess, step = max(guess - step, 1), step * 2
        lo_h, lo_t = lo
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
        self.save()
        return self.header(hi_h)

    def rows(self, p: dict, day: pd.Timestamp) -> list[dict]:
        """total_supply_protocol from the header. NOT validator stake: the archival RPC refuses
        `validators` for past blocks (VALIDATOR_INFO_UNAVAILABLE, Jake's archive_probe
        2026-09-30), so staked NEAR is forward-only (config.HISTORY_FORWARD_ONLY)."""
        api = p.get("near_validators") or {}
        exp = int(api.get("yocto_exponent", 24))
        hdr = self.at(day)
        return [{"date": day, "project": p["name"], "metric": "total_supply_protocol",
                 "value": int(hdr["total_supply"]) / 10**exp,
                 "source": config.mark_source("near_rpc:block.header.total_supply", "archive"), "tier": 2}]
