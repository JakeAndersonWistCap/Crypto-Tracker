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

from .base import AdaptivePacer, FetchOutput, LONG_COLUMNS, Progress, RateLimitedTooLong, redact, _measuring_point, derive_flow_from_cumulative, today
from .logcache import atomic_write_text

log = logging.getLogger("token_metrics.fetch.archive")

ARCHIVE_DAYS = int(config.TRAJECTORY["history_days"])     # five quarters for the trajectory (2026-10-12; was 365)
DEFAULT_BUDGET_S = 20 * 60
NOT_ARCHIVABLE_KINDS = {"burn_transfer_logs"}
# Kinds read by ChainReader.raw_call (a governance parameter: a plain call on spec["address"]). P2d-6: only these take the
# "not available before" rule for an empty return (ArchiveBackfill._absent_before), and only these can be probed.
RAW_CALL_KINDS = {"cooldown_duration"}


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
        if metric in ((spec.get("metric_override") or KIND_METRIC.get(spec["kind"])), spec.get("component_metric")):
            out.append((key, spec))
    return out


def state_metrics(p: dict) -> set[str]:
    """Every metric a contract read serves for this project."""
    from .chain import KIND_METRIC, REFERENCE_ONLY_KINDS
    specs = [spec for spec in (p.get("contracts") or {}).values() if spec["kind"] not in REFERENCE_ONLY_KINDS]
    # a component stored on its own (component_metric, Round G 2.8) has its own history to fill
    return ({spec.get("metric_override") or KIND_METRIC.get(spec["kind"]) for spec in specs}
            | {spec.get("component_metric") for spec in specs}) - {None}


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

    def __init__(self, w3, chain: str, root: Path | None = None, pacer=None):
        self.pacer = pacer
        self.w3, self.chain = w3, chain
        self.file = (root or cache_root()) / f"archive-blocks-{chain}.json"
        try:
            self.known = {k: int(v) for k, v in json.loads(self.file.read_text()).items()}
        except (OSError, ValueError):
            self.known = {}
        self._ts: dict[int, int] = {}

    def ts(self, n: int) -> int:
        if n not in self._ts:
            get = lambda: self.w3.eth.get_block(n)  # noqa: E731
            self._ts[n] = int((self.pacer.run(get) if self.pacer else get())["timestamp"])
        return self._ts[n]

    def save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.file, json.dumps(self.known, sort_keys=True))

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
        atomic_write_text(self.file, json.dumps(self.state))

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
        sigs, before, reached = [], None, False
        while time.monotonic() < deadline:
            opts = {"limit": 1000, "commitment": "finalized"}
            if before:
                opts["before"] = before
            page = self.rpc.call("getSignaturesForAddress", [self.account, opts]) or []
            time.sleep(self.pace)
            if not page:
                reached = True                    # the account's whole history is listed
                break
            sigs += [s for s in page if not s.get("err")]
            before = page[-1]["signature"]
            if int(page[-1].get("blockTime") or 0) < since_ts:
                reached = True
                break
        if not reached:
            # A LISTING CUT BY THE BUDGET IS NOT COMPLETE (Phase 1 review 2026-10-12, item 1): daily() would give every
            # day before the oldest listed transaction that transaction's pre-balance — a flat, wrong history
            self.save()
            return False, f"budget spent while listing ({len(sigs)} signature(s) listed, the window start not reached)"
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
        # "0 READ" IS NOT "NOTHING READ" (Jake's review of the ef6dc86 run, item 13c): every signature inside the window
        # is cached from earlier runs; the listing reaches back past the window, so most of it is older than the window
        inwin = sum(1 for s in sigs if int(s.get("blockTime") or 0) >= since_ts - 86_400)
        return True, (f"{len(sigs)} signature(s) listed, {inwin} inside the window — {done} read this run, "
                      f"{inwin - done} already cached (complete)")

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


def breaks_handover_order(project: str, metric: str, stored: pd.DataFrame | None, day, source: str) -> str:
    """Why writing `source` on `day` would put a DECLARED handover's legs out of order, else ''. (Jake's Phase 1 review
    2026-10-12, item 1: GEODNET's gross burn, burn yield, crossover and net change all read n/a after
    `history_gaps.py --run-fixes`.) The archive reads the CURRENT measuring point; on a series with a declared
    handover (GEODNET: the Polygon dead address, then Polygon + the Solana burn account) a current-point reading dated
    BEFORE a stored row of an earlier leg interleaves the legs, the build's handover check (handover_refusal) sees them
    OVERLAP and withholds the whole series. neighbours_agree looks at the nearest rows only and misses a leg that
    is further away. A monthly lead leg (Dune) is resolved by month at read time (_monthly_leg_views) and is not checked
    here."""
    decl = config.declared_handover(project, metric) or {}
    pts = tuple(decl.get("ordered_points") or ())
    mp = _measuring_point(source)
    if stored is None or stored.empty or mp not in pts:
        return ""
    lead = config.handover_monthly_leg(project, metric)
    i, day = pts.index(mp), pd.Timestamp(day)
    for d, src in zip(pd.to_datetime(stored["date"]), stored["source"].astype(str)):
        q = _measuring_point(src)
        if q == mp or q not in pts or (lead and q == lead[0]):
            continue
        j = pts.index(q)
        if (j < i and d >= day) or (j > i and d <= day):
            return (f"{metric} on {day.date()} from {mp} would interleave the declared handover: a stored row of the "
                    f"{'earlier' if j < i else 'later'} leg {q} is dated {d.date()}")
    return ""


def derive_flows(store, project: str, stock: str, straddled: list | None = None, owed=None) -> int:
    """A cumulative stock's flow for every consecutive pair of stored readings whose later date
    has no flow row yet — the live path's guard, so a change of point or a fall is refused.

    ** NEVER INSIDE A STORED DELTA'S SPAN (2026-09-29, Jake). ** Uniswap's gross_burn_tokens came
    out 116,000 UNI above the stock's move: a live delta dated D with [span=3d] already covered
    D-2 and D-1, the archive then filled those days' readings, and a per-day delta for each was
    written beside it — the same burns twice. A pair whose interval overlaps a stored flow row's
    interval is not written; it is listed in `straddled`, and rederive.py rebuilds the whole flow
    from the stock (preview first, --apply to replace)."""
    flow = config.cumulative_flow_for(project, stock)
    if not flow:
        return 0
    from .validate import _span_days
    rows = store.conn.execute("SELECT date, value, source FROM metrics WHERE project=? AND metric=? "
                              "ORDER BY date", (project, stock)).fetchall()
    held = store.conn.execute("SELECT date, source FROM metrics WHERE project=? AND metric=?",
                              (project, flow)).fetchall()
    have = {d for d, _ in held}
    spans = [(pd.Timestamp(d) - pd.Timedelta(days=_span_days(s)), pd.Timestamp(d)) for d, s in held]
    out, frames = FetchOutput(), []
    held_df = pd.DataFrame(held, columns=["date", "source"])
    owed = sorted(str(d)[:10] for d in (owed or ()))
    for (d0, v0, s0), (d1, v1, s1) in zip(rows, rows[1:]):
        if d1 in have:
            continue
        # P4-Q6: a day still owed by the catch-up lies between the two readings — wait for it (never a lump meanwhile)
        if owed and any(str(d0)[:10] < d < str(d1)[:10] for d in owed):
            continue
        # the flow's own declared handover, in order (Phase 1 review item 1): never a current-leg delta dated inside an
        # earlier leg's stored span
        if breaks_handover_order(project, flow, held_df, d1, config.mark_source(s1, "delta")):
            continue
        a, b = pd.Timestamp(d0), pd.Timestamp(d1)
        hit = next((f"{e.date()} (spans {s.date()}..{e.date()})" for s, e in spans if s < b and a < e), None)
        if hit:
            if straddled is not None:
                straddled.append(f"{project}/{flow} {str(d0)[:10]}..{str(d1)[:10]} lies inside the stored "
                                 f"delta dated {hit}")
            continue
        f = derive_flow_from_cumulative(v1, v0, project, flow, config.mark_source(s1, "delta"), 2,
                                        pd.Timestamp(d1), prior_date=d0, stock_metric=stock, out=out,
                                        prior_source=s0, source_base=s1)
        if not f.empty:
            frames.append(f)
    return _write_new(store, pd.concat(frames)) if frames else 0


def _state(name: str) -> Path:
    return cache_root() / name


def _read_json(name: str) -> dict:
    try:
        return json.loads(_state(name).read_text())
    except (OSError, ValueError):
        return {}


def _write_json(name: str, d: dict) -> None:
    f = _state(name)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, sort_keys=True, indent=1))


def series_starts() -> dict:
    """{(project, metric): {"from", "why"}} — declared in config, or recorded by a pass that found
    the contract not yet deployed. Where both exist the later start wins."""
    out = {k: dict(v) for k, v in config.ARCHIVE_SERIES_START.items()}
    for key, v in _read_json("archive-series-start.json").items():
        name, m = key.split("|", 1)
        if _start_from_codeless_holder(name, str(v.get("why") or "")):
            continue                     # P2b-2: recorded off an EOA's empty code — not a start; ignored, re-read
        if (name, m) not in out or v["from"] > out[(name, m)]["from"]:
            out[(name, m)] = v
    return out


def _start_from_codeless_holder(name: str, why: str) -> bool:
    """A recorded start whose reason is the empty code of a holder that is never meant to have code (P2b-2: before the
    fix in _undeployed, an EOA wallet's "no code at block N" ended a series' history after one failed day)."""
    from .chain import address_is_called, holder_should_have_code
    bits = why.split(" ")
    spec = ((config.PROJECT_BY_NAME.get(name) or {}).get("contracts") or {}).get(bits[0])
    if (not spec or len(bits) < 2 or " has no code at block " not in why or holder_should_have_code(spec)
            or address_is_called(spec)):     # P2d-6: a CALLED contract's empty code dates its deployment (sPENDLE)
        return False
    return bits[1].rstrip(".").lower() == str(spec.get("address") or "")[:10].lower()   # the holder, not its token


def record_series_start(name: str, metric: str, begin: str, why: str) -> None:
    d = _read_json("archive-series-start.json")
    d[f"{name}|{metric}"] = {"from": begin, "why": why}
    _write_json("archive-series-start.json", d)


def saved_pace(chain: str) -> float:
    return float(_read_json("archive-pace.json").get(chain) or 0.0)


def save_pace(chain: str, pace: float) -> None:
    d = _read_json("archive-pace.json")
    d[chain] = round(pace, 3)
    _write_json("archive-pace.json", d)


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
        # P3-4 CATCH-UP (catch_up below): {(project, metric): {"YYYY-MM-DD", ...}} — read ONLY these days of ONLY these
        # series, NEWEST FIRST since P4-Q6 (was oldest first; the flows wait for the days still owed — derive_owed_flows — so
        # they difference day by day), at most `max_days` distinct days a run. None: the backfill as before (every series, every day of the
        # window, newest first, the time budget only). The reads, the skip of stored days, the refusals and the series
        # starts are the backfill's own — the same code path.
        self.only: dict | None = None
        self.max_days: int | None = None

    def _undeployed(self, p: dict, metric: str) -> str | None:
        """A component contract with NO CODE at the pinned block: "<key> 0x... has no code at block N
        (deployed at block M)", else None. The deployment block is found once and recorded."""
        for key, spec in serving_contracts(p, metric):
            chain = spec["chain"]
            if chain == "solana" or chain not in getattr(self.reader, "at_block", {}):
                continue
            # `underlying` is an ADDRESS on some contracts and a contract KEY on others (the W6 insider-stake reads name
            # their share token by key, "spendle"): a key resolves to its address. G2 addendum A5: unresolved, the
            # code check raised on "spendle", was skipped, and 180 days before sPENDLE's deployment (2026-01-16) read
            # NOT WRITTEN instead of before deployment (cooldown_days, read from sPENDLE itself, was already right)
            und = spec.get("underlying")
            if isinstance(und, str) and not und.startswith("0x"):
                und = ((p.get("contracts") or {}).get(und) or {}).get("address")
            # P2b-2 (found reading Chainlink's 33 wallets at past blocks, 2026-10-27): a holder that is NOT meant to have
            # code (holder_has_code False — an EOA such as 0xbe6977E0…, one of chain.link's listed wallets) has none at
            # EVERY block, so its empty code is no deployment date. Read here, one rate-limited day recorded the whole
            # series as starting the day after and never retried the restatement. Only an address that should carry
            # code (fetch.chain.holder_should_have_code) or the token read through it can date a series' start.
            # P2d-6 (Jake's backfill 2026-10-28): that rule is about HOLDERS. An address the read CALLS (sPENDLE for
            # cooldownDuration(); fetch.chain.address_is_called) dates its series by its code, as before P2b-2 — skipped,
            # Pendle/cooldown_days read 170 days NOT WRITTEN before sPENDLE's deployment (block 24,237,456, 2026-01-15).
            from .chain import address_is_called, holder_should_have_code
            addrs = (([spec["address"]] if (holder_should_have_code(spec) or address_is_called(spec)) else [])
                     + ([und] if isinstance(und, str) and und.startswith("0x") else []))
            for a in addrs:
                try:
                    w3 = self.reader.web3(chain)
                    blk = self.reader.at_block[chain]
                    code = w3.eth.get_code(self.reader.checksum(a), block_identifier=blk)
                except Exception:  # noqa: BLE001 — cannot tell: not "undeployed"
                    continue
                if code in (b"", b"0x", "0x") or not code:
                    dep = ""
                    try:
                        dep = f" (deployed at block {int(self.reader.deployment_block(chain, a)):,})"
                    except Exception:  # noqa: BLE001
                        pass
                    return f"{key} {a[:10]}... has no code at block {blk:,}{dep}"
        return None

    def _absent_before(self, p: dict, metric: str, d: pd.Timestamp, msgs: list[str], stored: pd.DataFrame | None,
                       oldest: pd.Timestamp, probe) -> tuple[str, str, str] | None:
        """P2d-6: a CALL (cooldownDuration(), through raw_call) that answers EMPTY ("0x" — no data; not a revert, not an
        RPC error) on the day just before the series' FIRST answered day is "not available before" that day: the
        function is not in the contract version of that block. (begin, why, report line), else None.

        STRICT, all of:
          * every failure message of the metric-day is an empty return (fetch.chain.is_empty_return) on a raw-call kind;
          * the series' earliest stored row is exactly the next day — the first day the call answered, from the data;
          * the oldest day the run would read (`oldest`, the window's first or the series' start) ALSO answers empty
            (`probe`, one read at that day's first block): absence before a version is monotone, so an answer there
            means this empty day is a hole INSIDE the history, not before it.
        Otherwise None and the day stays a failure, logged as before: an empty return on or after the first answered
        day, a revert, an RPC error, a probe that answers or errs. Nothing is written for the days before — never 0."""
        from .chain import is_empty_return
        specs = serving_contracts(p, metric)
        msgs = [x for x in msgs if not str(x).startswith("every component was refused")]   # Chain's summary of them
        if (not msgs or not all(is_empty_return(x) for x in msgs) or weekly(p, metric) or not specs
                or any(s["kind"] not in RAW_CALL_KINDS for _, s in specs)):
            return None
        if stored is None or stored.empty:
            return None
        first = pd.Timestamp(stored["date"].min()).normalize()
        if first != stored_date(p, metric, d) + pd.Timedelta(days=1):
            return None
        key, spec = specs[0]
        call = spec.get("call") or "cooldownDuration"
        what = spec.get("expected_symbol") or key
        blk = getattr(self.reader, "at_block", {}).get(spec["chain"])
        at = f" at block {blk:,}" if isinstance(blk, int) else ""
        checked = ""
        if oldest < d:
            got = [probe(s, oldest) for _, s in specs]
            if any(g != "empty" for g in got):
                return None
            checked = f"; {oldest.date()} answered 0x too"
        begin = str(first.date())
        why = (f"{key} {call}() returned empty data (0x){at} on {d.date()}{checked}, the day before its first answer "
               f"{begin} — absent before the {what} version that has it")
        line = (f"not available before {begin} — {call}() returns empty before the {what} version that has it "
                f"(a pre-deployment absence, not a failure); {d.date()}{at} answered 0x{checked}; earlier days not "
                f"retried")
        return begin, why, line

    def derive_owed_flows(self, series, owed: dict | None = None) -> dict:
        """Each stock's cumulative flow (derive_flows) for `series` [(project, stock)]: {(project, flow): rows}. STRADDLED
        days are reported. P4-Q6: `owed` {(project, stock): {"YYYY-MM-DD"}} — a pair of readings with a day still owed
        between them is NOT differenced yet (the catch-up fills newest first: differenced now, the pair would store a
        multi-day lump the per-day fill could never split); it is differenced once the day is filled or given up."""
        flows, straddled = {}, []
        for (name, m) in series:
            flow = config.cumulative_flow_for(name, m)
            if not flow:
                continue
            k = len(straddled)
            n = derive_flows(self.store, name, m, straddled, owed=(owed or {}).get((name, m)))
            if n:
                flows[(name, flow)] = n
            if len(straddled) > k:
                self.report.append(
                    f"STRADDLED {name}/{flow}: {len(straddled) - k} "
                    f"backfilled day(s) lie inside a stored multi-day delta and were NOT differenced "
                    f"(the burns are already in that delta), e.g. {straddled[k]}. For per-day rows: "
                    f"python rederive.py {name} {flow} (preview), "
                    f"then --apply")
        return flows

    def targets(self, archive_chains: set[str]) -> tuple[dict, list[tuple[str, str, str]]]:
        """({project: {metric}}, [(project, metric, why not)])."""
        ok, no = {}, []
        for p in self.projects:
            for m in sorted(state_metrics(p)):
                why_not = config.archive_not_by_design(p["name"], m)
                if why_not:
                    no.append((p["name"], m, why_not))
                    continue
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
        if self.only is not None:
            targets = {n: {m for m in ms if (n, m) in self.only} for n, ms in targets.items()}
            targets = {n: ms for n, ms in targets.items() if ms}
            # P4-Q6: NEWEST FIRST (was P3-4's oldest first) — the cells' Q0 window (and its Now) fills first; a flow is
            # never differenced across a day still owed (derive_owed_flows), so no multi-day lump is written meanwhile
            days = sorted({pd.Timestamp(d) for ds in self.only.values() for d in ds} & set(days), reverse=True)
        first_day = min(days) if days else end - pd.Timedelta(days=1)

        starts = series_starts()

        def missing(name, m, d):
            s0 = starts.get((name, m))
            if s0 and d < pd.Timestamp(s0["from"]):
                return False                      # before the series exists: complete, not missing
            if self.only is not None and str(d.date()) not in self.only.get((name, m), ()):
                return False                      # P3-4: not one of the catch-up's days for this series
            g = stored.get((name, m))
            return g is None or not (g["date"] == d).any()
        failures: dict = {}

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
            if hasattr(self.reader, "pacers"):
                self.reader.pacers[c] = AdaptivePacer(start=saved_pace(c), deadline=deadline)
            heads[c] = int(w3.eth.block_number)
            clocks[c] = DayBlocks(w3, c, pacer=getattr(self.reader, "pacers", {}).get(c))
        # Solana histories first: their daily balances are components of the sums.
        sol_daily = {}
        for key, hist in self.solana.items():
            done, note = hist.fill(int(pd.Timestamp(first_day).tz_localize("UTC").timestamp()), deadline)
            self.report.append(f"solana {key[0]}/{key[1]}: {note}" + ("" if done else " — INCOMPLETE; "
                               "its sums wait for the next run"))
            if done:
                sol_daily[key] = hist.daily(days)
        written, refused = {}, {}
        from .chain import Chain
        # PROGRESS (Jake, 2026-10-01): rows are written to the store day by day; the line saves the
        # block-time caches too, so a cut run re-resolves nothing it already found.
        prog = Progress("archive_backfill", total=len(days), unit="days", every_units=10,
                        checkpoint=lambda: [c.save() for c in clocks.values()],
                        status=lambda: (f"{sum(written.values())} row(s) written over {len(written)} series; "
                                        f"{len(refused)} series refused"))
        def wanted(d):
            want = {}
            for name, ms in targets.items():
                p = config.PROJECT_BY_NAME[name]
                ms = {m for m in ms if read_day(p, m, d) and missing(name, m, stored_date(p, m, d))
                      and (name, m) not in refused
                      and all((name, k) in sol_daily for k, s in serving_contracts(p, m)
                              if s["kind"] in ("spl_token_account", "spl_mint"))}
                if ms:
                    want[name] = ms
            return want
        def probe(spec, day):
            """P2d-6: one raw call at `day`'s first block — "value", "empty" (0x) or "error"; the pinned block restored."""
            from .chain import is_empty_return
            c = spec["chain"]
            keep = self.reader.at_block.get(c)
            try:
                self.reader.at_block[c] = clocks[c].at(day, heads[c])
                self.reader.raw_call(c, spec["address"], spec.get("call") or "cooldownDuration")
                return "value"
            except Exception as e:  # noqa: BLE001 — classified, never swallowed into a value
                return "empty" if is_empty_return(f"{e}") else "error"
            finally:
                if keep is None:
                    self.reader.at_block.pop(c, None)
                else:
                    self.reader.at_block[c] = keep
        read_days, read_s = 0, 0.0
        for i, d in enumerate(days):
            prog.tick()
            if time.monotonic() >= deadline:
                # HOW MUCH IS LEFT, AND HOW MANY RUNS IT TAKES (Jake's review of the ef6dc86 run, item 13c). A re-run
                # continues here by itself: every stored (day, series) is skipped without a read.
                left = sum(1 for dd in days[i:] if wanted(dd))
                per = (read_s / read_days) if read_days else None
                if per:
                    need_min = left * per / 60
                    runs = max(1, -(-int(need_min * 60) // int(self.budget_s)))
                    self.report.append(
                        f"budget spent at {d.date()}; {left} day(s) still have something to read, at {per:.1f}s a day "
                        f"(this run) ~{need_min:.0f} min: about {runs} more run(s) at this budget "
                        f"({self.budget_s / 60:.0f} min), or one with --budget-min {int(need_min * 1.2) + 5}. "
                        f"Re-run to continue — stored days are skipped")
                else:
                    self.report.append(f"budget spent at {d.date()}; {left} day(s) still to read; re-run to continue")
                break
            want = wanted(d)
            if not want:
                continue
            if self.max_days is not None and read_days >= self.max_days:
                # P3-4: A DAY BUDGET PER RUN — a long gap is filled over several runs; the next run continues here
                left = sum(1 for dd in days[i:] if wanted(dd))
                self.report.append(f"day budget spent at {d.date()} ({self.max_days} day(s) a run); {left} day(s) "
                                   f"still to read — the next run continues (stored days are skipped)")
                break
            t_day = time.monotonic()
            for c in chains:
                self.reader.at_block[c] = clocks[c].at(d, heads[c])
            ch = Chain(backfill={"day": d, "metrics": want,
                                 "solana": {k: v.get(d) for k, v in sol_daily.items()}})
            ch.reader = self.reader
            out = FetchOutput()
            try:
                ch.run([config.PROJECT_BY_NAME[n] for n in want], None, out)
            except RateLimitedTooLong as e:
                self.report.append(f"rate-limited past the budget at {d.date()} ({e}); re-run to continue")
                break
            frame = out.frame()
            # EVERY METRIC-DAY NOT WRITTEN SAYS WHY (Jake, pass 2: Uniswap's stayed 276 -> 276 with
            # no line). A read failure is checked for "not deployed yet" first: no code at the
            # pinned block means the series starts after this day, recorded and never retried.
            got = {(r.project, r.metric) for r in frame.itertuples(index=False)}
            for name, ms in want.items():
                p = config.PROJECT_BY_NAME[name]
                for m in ms:
                    if (name, m) in got:
                        continue
                    msgs = [e.message for e in out.log if e.status == "failed" and e.project == name
                            and any(e.message.startswith(k) for k, _ in serving_contracts(p, m))]
                    # A REFUSAL IS A GAP, NOT A FAILED LOG LINE (Jake's run on 3500042, item 25: Aerodrome's tail
                    # emission was refused on 7 epochs and the line read "no row and no error from the read")
                    msgs += [g["reason"] for g in out.gaps if g.get("project") == name and g.get("metric") == m]
                    undeployed = self._undeployed(p, m)
                    if undeployed:
                        begin = d + pd.Timedelta(days=1)
                        record_series_start(name, m, str(begin.date()),
                                            f"{undeployed} — not deployed before {begin.date()}")
                        starts[(name, m)] = {"from": str(begin.date())}
                        self.report.append(f"{name}/{m}: not available before {begin.date()} — {undeployed}; the "
                                           f"series starts {begin.date()} (a pre-deployment absence, not a failure) — "
                                           f"COMPLETE from there, earlier days not retried")
                        continue
                    s0 = starts.get((name, m))
                    oldest = max(first_day, pd.Timestamp(s0["from"])) if s0 else first_day
                    absent = self._absent_before(p, m, d, msgs, stored.get((name, m)), oldest, probe)
                    if absent:
                        begin, why, line = absent
                        record_series_start(name, m, begin, why)
                        starts[(name, m)] = {"from": begin, "why": why}
                        self.report.append(f"{name}/{m}: {line}")
                        continue
                    f = failures.setdefault((name, m), {"days": 0, "first": "", "dates": []})
                    f["days"] += 1
                    f["dates"].append(str(d.date()))
                    f["first"] = f["first"] or (msgs[0] if msgs else "no row and no error from the read")
            keep = []
            for r in frame.itertuples(index=False):
                lo, hi = config.sanity_bounds(r.project, r.metric, r.date)
                if (lo is not None and r.value < lo) or (hi is not None and r.value > hi):
                    refused[(r.project, r.metric)] = (f"{r.value:,.4f} on {d.date()} is outside the "
                                                      f"declared bound [{lo}, {hi}]")
                    continue
                g = stored.get((r.project, r.metric))
                rd = pd.Timestamp(r.date)
                ok, why = (True, "") if g is None else neighbours_agree(g, rd, r.source)
                if ok and g is not None:
                    order = breaks_handover_order(r.project, r.metric, g, rd, r.source)
                    ok, why = (False, order) if order else (True, "")
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
            read_days, read_s = read_days + 1, read_s + (time.monotonic() - t_day)
        self.reader.at_block.clear()
        for c in clocks.values():
            c.save()
        # P4-Q6: in the catch-up (self.only) the flows wait for the catch-up's ledger — which days stay owed — and are
        # differenced by derive_owed_flows; the backfill differences here, as before
        flows = {} if self.only is not None else self.derive_owed_flows(list(written))
        for c, pc in getattr(self.reader, "pacers", {}).items():
            save_pace(c, pc.pace)
            self.report.append(f"pace {c}: {pc.pace:.2f}s/call at the end ({pc.stats['calls']} call(s), "
                               f"{pc.stats['429s']} x 429, {pc.stats['waited_s']:.0f}s waited) — saved")
        for (name, m), f in sorted(failures.items()):
            self.report.append(f"NOT WRITTEN {name}/{m}: {f['days']} day(s) — {f['first'][:200]}")
        for (name, m), s0 in sorted(starts.items()):
            if name in targets and m in targets[name]:
                # P2d-6: a declared start with its absence reason says "not available before <date> (...)" — one line
                dec = config.ARCHIVE_SERIES_START.get((name, m)) or {}
                why = (config.series_absent_before(name, m) if dec.get("from") == s0["from"] else None) \
                    or s0.get("why", "")
                self.report.append(f"COMPLETE FROM {s0['from']}: {name}/{m} — {why}".rstrip(" —"))
        return {"written": written, "flows": flows, "refused": refused, "skipped": skipped, "failures": failures,
                "read_days": read_days,
                "seconds": round(time.monotonic() - start), "report": self.report,
                "served_by": {k: sorted({self.hosts.get(s["chain"], "solana" if s["chain"] == "solana" else "?")
                                         for _, s in serving_contracts(config.PROJECT_BY_NAME[k[0]], k[1])})
                              for k in written}}


# ---------------------------------------------------------------------------------------------
# P3-4 CATCH-UP: the days missed between daily runs, filled by the backfill's own reads (Jake, 2026-11-02)
# ---------------------------------------------------------------------------------------------
# WHAT. For every DAILY on-chain series the archive can read at a past block (targets() + archivable(), the backfill's
# own test; weekly epoch reads, parked projects and by-design exclusions are left out), the days after the series' LAST
# STORED DAY up to yesterday are read at each day's first block by ArchiveBackfill.run — the same contracts, sums,
# source strings (`archive` marker), refusals, series starts ("not before" rules) and flows. Never a different
# measuring point, never an overwrite; a day the archive cannot read stays a named hole (never 0, never carried).
#
# WHEN. token_metrics.py runs it inside its RunLock BEFORE today's tiers (so today's delta differences yesterday's
# reading, not a multi-day lump the per-day fill could no longer split); `token_metrics.py --catch-up` runs it alone.
#
# BUDGETED AND RESUMABLE. A time budget (minutes) and a day budget (distinct days a run), newest day first (P4-Q6). The days
# still owed are kept in `.cache/logscan/catch-up-pending.json` (per series: how many runs
# each unreadable day has been tried, kept after it is given up — P4-Q6; the owed days themselves are found by the scan).
# A stored day is skipped by the archive's own check. A day tried on CATCH_UP_TRIES runs is left to archive_backfill.py
# (named in the log). Deleting the file loses nothing stored: the days still owed become ordinary holes the backfill
# fills.
CATCH_UP_BUDGET_MIN = 10.0     # minutes a run: TOKEN_METRICS_CATCH_UP_MIN or --budget-min; 0 turns the catch-up off
CATCH_UP_DAYS = 7              # distinct days read a run: TOKEN_METRICS_CATCH_UP_DAYS or --catch-up-days
CATCH_UP_TRIES = 3             # runs an unreadable day is retried before it is left to archive_backfill.py
CATCH_UP_LEDGER = "catch-up-pending.json"

# ---------------------------------------------------------------------------------------------
# P4-Q6 ALWAYS-BACKFILL (Jake, 2026-11-03): EVERY missing day inside the longest window any cell reads, not only the
# days after a series' last stored day
# ---------------------------------------------------------------------------------------------
# THE WINDOW W. The build's windows (build_workbook.aggregate / _period_windows, config.TRAJECTORY): Now = 30 days, Prior
# 30d = the 30 before (60 back), Q0..Q3 = four 90-day blocks and the trajectory's Q-4 the fifth (QUARTERS q0..q4 —
# 5 x 90 = 450 back: Q-4 is the 90 days ending 360 days before the build), Y1 = 365 days. Y1 is never shifted by the
# trajectory (build_workbook._SHIFT_NA) and no cell sums a 365-day window ending a year back, so nothing reads 730.
# W = max(400, 5 x 90, 365, 2 x 30) = 450 days: [today - 450, yesterday].
# OWED. Every day in [max(today - W, the series' first stored day, its declared / recorded start), yesterday] with no
# row — a day before a series exists is not a hole; a day inside a stored multi-day row ([span=Nd]) is held by that row
# (the STRADDLED guard); a day where the series' current measuring point would interleave a declared handover's earlier
# leg is that leg's, not owed. Never an overwrite (only keys with no row are written).
# ORDER: NEWEST FIRST. The headline cells read the Q0 window (and the Now window inside it) first; a 400-day backlog
# read oldest first would spend ~57 runs (7 days a run) before reaching the days the headlines read. Newest first fills
# the Q0 window in its first runs and works back through Q1..Q-4. A cumulative flow is not differenced across a day
# still owed (derive_owed_flows), so filling from the new end never stores a multi-day lump.
# THE LEDGER keeps each unreadable day's tries — also after it is given up (CATCH_UP_TRIES runs), so the full-window scan
# does not read a given-up day again; days that leave the window are dropped from it.
CATCH_UP_MIN_WINDOW = 400


def catch_up_window_days() -> int:
    """W, the longest window any cell reads (see above): 450 with the configured windows, never under 400."""
    tr, g = config.TRAJECTORY, config.GLOBALS
    return max(CATCH_UP_MIN_WINDOW, int(tr["quarters"]) * int(tr["period_days"]), int(g["days_per_year"]),
               2 * int(g["short_days"]))


def catch_up_bounds() -> tuple[pd.Timestamp, pd.Timestamp]:
    """(first day, yesterday) of the window the catch-up scans: [today - W, yesterday]."""
    end = today()
    return end - pd.Timedelta(days=catch_up_window_days()), end - pd.Timedelta(days=1)


def owed_days(name: str, metric: str, rows, lo, yesterday=None, skip=()) -> list[str]:
    """The days the series `rows` [(YYYY-MM-DD, source), ...] owes in [lo, yesterday], NEWEST FIRST: not before its first
    stored day, not held by a row or inside a stored multi-day row's span, not interleaving a declared handover's earlier
    leg, not in `skip` (given up)."""
    from .validate import _span_days
    if not rows:
        return []
    yesterday = catch_up_bounds()[1] if yesterday is None else pd.Timestamp(yesterday)
    rows = sorted((str(d)[:10], str(s)) for d, s in rows)
    held, covered = {d for d, _ in rows}, set()
    for d, src in rows:
        k = _span_days(src)
        if k > 1:
            e = pd.Timestamp(d)
            covered.update(str((e - pd.Timedelta(days=i)).date()) for i in range(1, k))
    lo = max(pd.Timestamp(lo), pd.Timestamp(rows[0][0]))
    if lo > yesterday:
        return []
    skip = set(skip or ())
    out = [str(d.date()) for d in pd.date_range(lo, yesterday)]
    out = [d for d in out if d not in held and d not in covered and d not in skip]
    pts = tuple((config.declared_handover(name, metric) or {}).get("ordered_points") or ()) if out else ()
    cur = _measuring_point(rows[-1][1])
    if pts and cur in pts:
        # A DECLARED HANDOVER: a day whose next stored row is an EARLIER leg's lies in that leg's era — the current point
        # is never written there (breaks_handover_order), and the earlier leg is not read back: not owed
        import bisect
        dates = [d for d, _ in rows]
        mps = [_measuring_point(s) for _, s in rows]

        def earlier_era(d: str) -> bool:
            i = bisect.bisect_right(dates, d)
            return i < len(dates) and mps[i] in pts and pts.index(mps[i]) < pts.index(cur)
        out = [d for d in out if not earlier_era(d)]
    return sorted(out, reverse=True)


def _series_rows(store, name: str, metric: str) -> list[tuple[str, str]]:
    return [(d, s) for d, s in store.conn.execute(
        "SELECT substr(date,1,10), source FROM metrics WHERE project=? AND metric=? ORDER BY date", (name, metric))]


def _ledger_tries(ent: dict, lo: pd.Timestamp) -> dict:
    """The ledger entry's tries {day: runs}, inside the window only."""
    return {d: int(k) for d, k in ((ent or {}).get("tries") or {}).items() if d >= str(lo.date())}


def _carried_tries(store, ledger: dict, series, entries: dict) -> dict:
    """P4-Q6: the ledger entries of `series` that this run did not rewrite (no day owed this run — e.g. every unreadable
    day given up), carried over: their tries inside the window, minus days that now hold a row. Without this a given-up
    day would be forgotten and read again by the next full-window scan."""
    lo, out = catch_up_bounds()[0], {}
    for n, m in series:
        key = f"{n}|{m}"
        if key in entries:
            continue
        t = _ledger_tries(ledger.get(key), lo)
        if t:
            have = {d for d, _ in _series_rows(store, n, m)}
            t = {d: k for d, k in t.items() if d not in have}
            if t:
                out[key] = {"tries": t}
    return out


def catch_up_defaults() -> tuple[float, int]:
    """(budget minutes, days a run) from the environment, else the defaults."""
    import os

    def num(name, default, cast):
        try:
            return cast(os.environ.get(name, "").strip() or default)
        except ValueError:
            return default
    return (num("TOKEN_METRICS_CATCH_UP_MIN", CATCH_UP_BUDGET_MIN, float),
            num("TOKEN_METRICS_CATCH_UP_DAYS", CATCH_UP_DAYS, int))


def catch_up_parked() -> dict:
    """{project: why} — parked projects (config.DETAIL_PARKED, config.CREDIBILITY_PARKED) are never touched."""
    out: dict = {}
    for d in (getattr(config, "DETAIL_PARKED", None) or {}, getattr(config, "CREDIBILITY_PARKED", None) or {}):
        out.update(d)
    return out


def solana_keys(projects: list[dict]) -> set:
    """(project, contract key) of every SPL TOKEN ACCOUNT read — the components SolanaAccountHistory serves."""
    return {(p["name"], k) for p in projects for k, s in (p.get("contracts") or {}).items()
            if s["kind"] == "spl_token_account"}


def solana_providers(projects: list[dict], keys: set | None = None) -> dict:
    """(project, key) -> SolanaAccountHistory for every SPL TOKEN ACCOUNT read (GEODNET's burn), or only `keys`."""
    out = {}
    for p in projects:
        for key, spec in (p.get("contracts") or {}).items():
            if spec["kind"] == "spl_token_account" and (keys is None or (p["name"], key) in keys):
                out[(p["name"], key)] = SolanaAccountHistory(account=spec["address"],
                                                             decimals=int(spec.get("decimals", 9)))
    return out


def catch_up_gaps(store, projects: list[dict], ledger: dict | None = None) -> tuple[dict, list, set]:
    """({(project, metric): [owed days, NEWEST first]}, [(project, metric, why not covered)], {series checked}).

    Owed (P4-Q6, owed_days): every day in the window [today - W, yesterday] with no row, ANYWHERE — not only after the
    last stored day — not before the series' first stored day or its start (config.ARCHIVE_SERIES_START / a recorded
    start), not inside a stored multi-day row, not a day given up after CATCH_UP_TRIES runs."""
    ledger = _read_json(CATCH_UP_LEDGER) if ledger is None else ledger
    window, yesterday = catch_up_bounds()
    starts, parked, sk = series_starts(), catch_up_parked(), solana_keys(projects)
    gaps, excluded, checked = {}, [], set()
    for p in projects:
        name = p["name"]
        for m in sorted(state_metrics(p)):
            if name in parked:
                excluded.append((name, m, f"parked — {parked[name]}"))
                continue
            why = config.archive_not_by_design(name, m)
            if why:
                excluded.append((name, m, why))
                continue
            if weekly(p, m):
                excluded.append((name, m, "a weekly epoch read, not a daily series — archive_backfill.py fills epochs"))
                continue
            # every chain assumed served here; the endpoint is resolved (and a chain without one named) at read time
            ok, why = archivable(p, m, {s["chain"] for _, s in serving_contracts(p, m)}, sk)
            if not ok:
                excluded.append((name, m, why))
                continue
            rows = _series_rows(store, name, m)
            if not rows:
                excluded.append((name, m, "nothing stored yet — a first history is archive_backfill.py's job"))
                continue
            checked.add((name, m))
            ent = ledger.get(f"{name}|{m}") or {}
            frm = window
            s0 = starts.get((name, m))
            if s0:
                frm = max(frm, pd.Timestamp(s0["from"]))
            given = {d for d, k in _ledger_tries(ent, window).items() if k >= CATCH_UP_TRIES}
            owed = owed_days(name, m, rows, frm, yesterday, given)
            if owed:
                gaps[(name, m)] = owed
    return gaps, excluded, checked


def _ranges(days) -> str:
    """'2026-10-30..11-01, 2026-11-03' — consecutive days folded into one range."""
    out, run = [], []
    for d in sorted(pd.Timestamp(x) for x in days):
        if run and d - run[-1] == pd.Timedelta(days=1):
            run.append(d)
            continue
        if run:
            out.append(run)
        run = [d]
    if run:
        out.append(run)
    return ", ".join(str(r[0].date()) if len(r) == 1 else f"{r[0].date()}..{r[-1].strftime('%m-%d')}" for r in out)


# ---------------------------------------------------------------------------------------------
# P3-4 A4: THE DAILY API SERIES, AND WHAT IS DERIVED FROM THE CAUGHT-UP DAYS (Jake, 2026-11-02)
# ---------------------------------------------------------------------------------------------
# WHICH API SERIES OWE A CATCH-UP, AND WHICH DO NOT (read from the adapters, 2026-11-02):
#   CoinGecko market_chart (price_usd, market_cap_usd, volume_usd, circulating_supply_implied) — INCREMENTAL: a routine
#     run asks only for the days since the newest stored price (+ OVERLAP_DAYS). Its own read fills a missed day, but
#     inside the tiers, after the stored-price readers have started: Ethereum's and NEAR's P2P legs value each day at
#     the price STORED before the run (stored_long), so a missed day waited a run for its price, and `--catch-up`
#     alone left the price hole and everything valued at it. CAUGHT UP HERE, through the adapter's own helper
#     (CoinGecko.market_chart), before the on-chain days.
#   Re-read in full each run — owe nothing: DefiLlama fees / revenue / TVL / RWA share (the whole daily history is
#     downloaded and the trailing window kept: fetch/llama.py), Hyperliquid candles (hl_candles), Boros volume, the
#     Pendle maturity read (it restates its last days), DefiLlama coins price history (price_history: only the days
#     BEFORE CoinGecko's first price — never inside the CoinGecko leg, rule 10).
#   Incremental from their own last stored day, nothing derived from them before the tiers — owe nothing: growthepie,
#     Blockscout stats, NearBlocks, the logscan event-log reads (Sky's burn), Dune (a backfill, refreshed by its own
#     rule). The next plain run's read starts at the series' last stored day, so no day is skipped.
# A day CoinGecko does not serve stays a NAMED HOLE (never 0, never carried), retried on CATCH_UP_TRIES runs. A series
# whose newest stored row is not CoinGecko's own (another measuring point) is NOT filled from CoinGecko: that would be
# a basis change (rule 10) — the line says so.
API_CATCH_UP_LEDGER = "catch-up-api-pending.json"
# THE DERIVATIONS FOR THE CAUGHT-UP DAYS: fetch.derive_written (the run's write-time derivations, in their order) for
# each caught-up day with that day's stored rows as the frame and the store's values BEFORE that day as the priors,
# then fetch.history_derive.derive_from_history once, then the P2P legs' valuation at the stored same-day price. Only
# rows dated on a caught-up day whose key holds nothing are written (never an overwrite), and never a day inside a
# stored multi-day row's span (it already counts that day). A day is replayed on CATCH_UP_TRIES runs, because an input
# can land later (Sky's burn is an event-log read the tiers fill after the catch-up).
DERIVE_LEDGER = "catch-up-derive-pending.json"


def api_series(p: dict) -> dict:
    """{metric: source} — the daily API series of `p` the catch-up can read for a past day, with the source the series
    is read from: CoinGecko's market_chart for a project with a coingecko_id."""
    from .coingecko import HISTORY_METRICS
    return {**HISTORY_METRICS, **COINGECKO_PAST_DAY_METRICS} if p.get("coingecko_id") else {}


def api_catch_up_gaps(store, projects: list[dict], ledger: dict | None = None) -> tuple[dict, dict, list, set]:
    """({(project, metric): [owed days]}, {(project, metric): why not filled}, [(project, metric, why not covered)],
    {series checked}) for the daily API series (api_series). Owed (P4-Q6, owed_days): every day with no row ANYWHERE in
    the window [today - W, yesterday] that CoinGecko's free 365 days reach, from the series' first stored day, newest
    first, not given up."""
    from .coingecko import FREE_MAX_DAYS, SOURCE as CG
    ledger = _read_json(API_CATCH_UP_LEDGER) if ledger is None else ledger
    lo, yesterday = catch_up_bounds()
    window = max(lo, today() - pd.Timedelta(days=FREE_MAX_DAYS - 1))
    parked = catch_up_parked()
    try:
        absent = store.known_absent()
    except Exception:  # noqa: BLE001 — a store without the fetch_status history
        absent = set()
    gaps, refused, excluded, checked = {}, {}, [], set()
    for p in projects:
        name = p["name"]
        for m, src in sorted(api_series(p).items()):
            if name in parked:
                excluded.append((name, m, f"parked — {parked[name]}"))
                continue
            if (CG, name) in absent:
                excluded.append((name, m, "CoinGecko KNOWN ABSENT for this coin id"))
                continue
            row = store.conn.execute("SELECT substr(date,1,10), source FROM metrics WHERE project=? AND metric=? "
                                     "ORDER BY date DESC LIMIT 1", (name, m)).fetchone()
            if row is None:
                excluded.append((name, m, "nothing stored yet — the daily run seeds it"))
                continue
            checked.add((name, m))
            ent = ledger.get(f"{name}|{m}") or {}
            given = {d for d, k in _ledger_tries(ent, window).items() if k >= CATCH_UP_TRIES}
            owed = owed_days(name, m, _series_rows(store, name, m), window, yesterday, given)
            if not owed:
                continue
            gaps[(name, m)] = owed
            mp, want = _measuring_point(str(row[1])), _measuring_point(src)
            if mp != want:
                refused[(name, m)] = (f"its newest stored row ({row[0]}) was read from {mp}, not {want} — a past day "
                                      f"from {want} would change the series' basis (rule 10); left to the daily run")
    return gaps, refused, excluded, checked


def api_catch_up(store, projects: list[dict], deadline: float | None = None, max_days: int | None = None,
                 api=None) -> dict:
    """Fill the daily API series' owed days (api_catch_up_gaps) from the series' own read — CoinGecko.market_chart, one
    call per project, paced by the adapter (2 s with a key, 6 s without) — newest first (P4-Q6), at most `max_days` days per
    series, nothing read after `deadline`. Writes only keys that hold nothing, at the source string the daily run
    writes, refused where the day's stored neighbours or a declared handover say another measuring point. Returns
    {"lines", "gaps", "filled", "left", "holes", "checked", "excluded", "warn"}."""
    from .coingecko import FREE_MAX_DAYS
    ledger = _read_json(API_CATCH_UP_LEDGER)
    gaps, basis, excluded, checked = api_catch_up_gaps(store, projects, ledger)
    lines: list[tuple[str, str]] = []
    res = {"lines": lines, "gaps": gaps, "filled": {}, "left": {}, "holes": {}, "checked": checked,
           "excluded": excluded, "warn": False}

    def save_ledger(entries: dict) -> None:
        keep = {k: v for k, v in ledger.items() if tuple(k.split("|", 1)) not in checked}
        keep.update(_carried_tries(store, ledger, checked, entries))     # P4-Q6: given-up days stay given up
        keep.update(entries)
        if keep or ledger:
            _write_json(API_CATCH_UP_LEDGER, keep)

    if not gaps:
        save_ledger({})
        return res
    by_name = {p["name"]: p for p in projects}
    if api is None:
        from .coingecko import CoinGecko
        api = CoinGecko()
    max_days = CATCH_UP_DAYS if max_days is None else int(max_days)
    entries = {}
    for name in sorted({n for n, _ in gaps}):
        p, series = by_name[name], api_series(by_name[name])
        mine = {m: days for (n, m), days in gaps.items() if n == name}
        todo = {m: days[:max_days] for m, days in mine.items() if (name, m) not in basis}
        served, err, late = {}, None, False
        if todo:
            if deadline is not None and time.monotonic() >= deadline:
                late = True
            else:
                first = pd.Timestamp(min(min(v) for v in todo.values()))
                try:
                    for _what, f in api.market_chart(p["coingecko_id"], name,
                                                     min(FREE_MAX_DAYS, (today() - first).days + 1)):
                        for r in f.itertuples(index=False):
                            served[(r.metric, str(pd.Timestamp(r.date).date()))] = r
                            if r.metric == "circulating_supply_implied":
                                # P5-1.11: the past day's circulating supply = market cap ÷ price (CoinGecko's own
                                # definition), at CoinGecko's measuring point, the route named on the row
                                served[("circulating_supply", str(pd.Timestamp(r.date).date()))] = r._replace(
                                    metric="circulating_supply", source=CG_CIRCULATING_PAST_SOURCE)
                except Exception as e:  # noqa: BLE001 — one coin failing leaves the others; the days stay owed
                    err = f"the market_chart read failed: {type(e).__name__}: {redact(e)}"[:200]
        for m, days in sorted(mine.items()):
            stored = pd.read_sql_query("SELECT date, source FROM metrics WHERE project=? AND metric=? ORDER BY date",
                                       store.conn, params=(name, m))
            stored["date"] = pd.to_datetime(stored["date"]).dt.normalize()
            keep, why = [], {}
            for d in ([] if late else todo.get(m, [])):
                r = served.get((m, d))
                if r is None:
                    why[d] = err or "CoinGecko's market_chart served no point for the day"
                    continue
                lo, hi = config.sanity_bounds(name, m, r.date)
                if (lo is not None and r.value < lo) or (hi is not None and r.value > hi):
                    why[d] = f"{r.value:,.4f} is outside the declared bound [{lo}, {hi}]"
                    continue
                ok, bad = neighbours_agree(stored, pd.Timestamp(d), r.source)
                bad = bad if not ok else breaks_handover_order(name, m, stored, d, r.source)
                if bad:
                    why[d] = bad
                    continue
                keep.append(r)
            if keep:
                _write_new(store, pd.DataFrame(keep)[LONG_COLUMNS])
            have = {d for (d,) in store.conn.execute(
                "SELECT DISTINCT substr(date,1,10) FROM metrics WHERE project=? AND metric=? AND substr(date,1,10)>=?",
                (name, m, min(days)))}
            filled = [d for d in days if d in have]
            left = [d for d in days if d not in have]
            holes = [d for d in left if d in why]
            tries = {d: k for d, k in ((ledger.get(f"{name}|{m}") or {}).get("tries") or {}).items() if d not in filled}
            for d in holes:
                tries[d] = int(tries.get(d, 0)) + 1
            given = [d for d in holes if tries[d] >= CATCH_UP_TRIES]
            # P4-Q6: the tries stay in the ledger after a day is given up (the full-window scan would read it again)
            tries = {d: k for d, k in tries.items() if d >= str(catch_up_bounds()[0].date())}
            if tries and (name, m) not in basis:
                entries[f"{name}|{m}"] = {"tries": tries}
            res["filled"][(name, m)], res["left"][(name, m)], res["holes"][(name, m)] = filled, left, holes
            txt = (f"CATCH-UP {name}/{m}: {len(filled)} day(s) filled" + (f" ({_ranges(filled)})" if filled else "")
                   + f", {len(left)} left" + (f" ({_ranges(left)})" if left else "")
                   + f" — CoinGecko market_chart for {p['coingecko_id']}, the series' own read ({series[m]})")
            if (name, m) in basis:
                txt += f"; NOT FILLED: {basis[(name, m)]}"
            if holes:
                first_why = why[holes[0]]
                txt += (f"; HOLE(S) {_ranges(holes)} — {first_why} (never 0, never carried; tried on "
                        f"{max(tries[d] for d in holes)} of {CATCH_UP_TRIES} runs)")
            if given:
                txt += f"; given up after {CATCH_UP_TRIES} runs: {_ranges(given)} — a named hole: CoinGecko serves no price"
            rest = [d for d in left if d not in holes and (name, m) not in basis]
            if rest:
                txt += f"; not reached this run (budget): {_ranges(rest)} — the next run continues"
            warn = bool(holes) or (name, m) in basis
            res["warn"] = res["warn"] or warn
            lines.append(("warning" if warn else "info", txt))
    save_ledger(entries)
    return res


def default_valuers() -> list:
    """The stored-price valuations replayed for caught-up days: Ethereum's and NEAR's P2P legs (each value_only())."""
    from .eth_settlement import EthP2PBigQuery
    from .near_bigquery import NearBigQuery
    return [lambda sl: EthP2PBigQuery(stored_long=sl), lambda sl: NearBigQuery(stored_long=sl)]


def derive_caught_up(store, projects: list[dict], days_by_name: dict, valuers=None) -> dict:
    """The series derived from the caught-up days, by the run's own code (see DERIVE_LEDGER above): {"lines",
    "written": {(project, metric): [days]}, "warn"}. Days owed from earlier runs (the ledger) are replayed too."""
    from . import derive_written
    from .history_derive import derive_from_history
    from .validate import _span_days
    ledger = _read_json(DERIVE_LEDGER)
    by = {p["name"]: p for p in projects}
    want: dict = {}
    for n, ds in (days_by_name or {}).items():
        if n in by and ds:
            want.setdefault(n, set()).update(str(d)[:10] for d in ds)
    for n, ent in ledger.items():
        if n in by:
            want.setdefault(n, set()).update(ent)
    res = {"lines": [], "written": {}, "warn": False}
    if not want:
        return res
    long = store.load_long()
    long = long[~long["is_manual"]] if "is_manual" in long else long
    long = long.assign(date=pd.to_datetime(long["date"]).dt.normalize())
    have = set(zip(long["date"].dt.strftime("%Y-%m-%d"), long["project"], long["metric"]))
    spans: dict = {}
    for r in long[long["source"].astype(str).str.contains("span=", regex=False)].itertuples(index=False):
        k = _span_days(r.source)
        if k > 1:
            spans.setdefault((r.project, r.metric), []).append((r.date - pd.Timedelta(days=k), r.date))
    written: dict = {}
    sources: dict = {}
    straddled: dict = {}
    refused: dict = {}

    def write(frame: pd.DataFrame) -> None:
        nonlocal long
        if frame is None or frame.empty:
            return
        f = frame.assign(date=pd.to_datetime(frame["date"]).dt.normalize())
        f = f.assign(d=f["date"].dt.strftime("%Y-%m-%d"))
        f = f[[p in want and d in want[p] and (d, p, m) not in have
               for d, p, m in zip(f["d"], f["project"], f["metric"])]]
        f = f.drop_duplicates(subset=["d", "project", "metric"], keep="last")
        rows = []
        for r in f.itertuples(index=False):
            hit = next((e for s, e in spans.get((r.project, r.metric), ()) if s < r.date < e), None)
            if hit is not None:
                straddled.setdefault((r.project, r.metric), {}).setdefault(str(hit.date()), []).append(r.d)
                continue
            # a DECLARED handover's legs stay in order (the archive's own guard): never an earlier leg's row dated
            # inside a later leg (Sky's issuance: d(supply) before 2026-10-06, the declared schedule from it)
            g = long[(long["project"] == r.project) & (long["metric"] == r.metric)]
            why = breaks_handover_order(r.project, r.metric, g, r.date, str(r.source))
            if why:
                refused.setdefault((r.project, r.metric), []).append((r.d, why))
                continue
            rows.append(r)
        if not rows:
            return
        new = pd.DataFrame(rows)[LONG_COLUMNS]
        _write_new(store, new)
        for r in rows:
            have.add((r.d, r.project, r.metric))
            written.setdefault((r.project, r.metric), []).append(r.d)
            sources.setdefault((r.project, r.metric), str(r.source))
        long = pd.concat([long, new.assign(is_manual=False)], ignore_index=True)

    # 1. the write-time derivations, day by day, oldest first (each day differenced against the store before it), on
    # that day's stored rows plus the config declarations for that day (the schedule tier: a declared zero is written
    # for the run's own day only, so a missed day held none) — as fetch_all has the tiers' rows in the frame
    from .schedule import Schedule
    for d in sorted(set().union(*want.values())):
        names = sorted(n for n in want if d in want[n])
        try:
            sched = FetchOutput()
            Schedule(asof=d).run([by[n] for n in names], None, sched)
            sf = sched.frame()
            sf = sf[pd.to_datetime(sf["date"]).dt.normalize() == pd.Timestamp(d)] if not sf.empty else sf
            write(sf)
        except Exception as e:  # noqa: BLE001
            res["lines"].append(("warning", f"CATCH-UP: declarations for {d} not written — {type(e).__name__}: "
                                            f"{redact(e)[:200]}"))
            res["warn"] = True
        frame = long[(long["project"].isin(names)) & (long["date"] == pd.Timestamp(d))]
        if frame.empty:
            continue
        before = store.values_before(d)
        prior = {k: v[0] for k, v in before.items()}
        out = FetchOutput()
        out.frames.append(frame[LONG_COLUMNS].reset_index(drop=True))
        try:
            derive_written(out, [by[n] for n in names],
                           {"prior_values": prior, "prior_delta": prior,
                            "prior_dates": {k: v[1] for k, v in before.items()}, "stored_long": long})
        except Exception as e:  # noqa: BLE001 — a derivation failing never stops the catch-up
            res["lines"].append(("warning", f"CATCH-UP: derivations for {d} not run — {type(e).__name__}: "
                                            f"{redact(e)[:200]}"))
            res["warn"] = True
            continue
        write(out.frame())
    # 2. the history derivations over the store as it now stands (fetch_all's next step), quietly: their own lines are
    # the daily run's
    hd = logging.getLogger("token_metrics.fetch.history_derive")
    level = hd.level
    hd.setLevel(logging.WARNING)
    try:
        out = FetchOutput()
        derive_from_history(out, projects, long)
        write(out.frame())
    except Exception as e:  # noqa: BLE001
        res["lines"].append(("warning", f"CATCH-UP: history derivations not run — {type(e).__name__}: "
                                        f"{redact(e)[:200]}"))
        res["warn"] = True
    finally:
        hd.setLevel(level)
    # 3. the P2P legs, valued at the stored same-day price, for the caught-up days that now hold one
    px = long[long["metric"] == "price_usd"]
    priced = {d for n, ds in want.items() for d in ds
              if ((px["project"] == n) & (px["date"] == pd.Timestamp(d))).any()}
    if priced:
        for make in (default_valuers() if valuers is None else valuers):
            out = FetchOutput()
            try:
                make(long).value_only([by[n] for n in sorted(want)], out, days=priced)
            except Exception as e:  # noqa: BLE001
                res["lines"].append(("warning", f"CATCH-UP: a P2P valuation not run — {type(e).__name__}: "
                                                f"{redact(e)[:200]}"))
                res["warn"] = True
                continue
            write(out.frame())
    for (n, m), ds in sorted(written.items()):
        res["lines"].append(("info", f"CATCH-UP {n}/{m}: {len(ds)} day(s) derived ({_ranges(ds)}) — "
                                     f"{_measuring_point(sources[(n, m)])}, the run's own derivation replayed over the "
                                     f"caught-up days"))
    for (n, m), bad in sorted(refused.items()):
        res["lines"].append(("info", f"CATCH-UP {n}/{m}: {_ranges({d for d, _ in bad})} NOT derived — {bad[0][1]}"))
    for (n, m), hits in sorted(straddled.items()):
        for e, ds in sorted(hits.items()):
            res["lines"].append(("info", f"CATCH-UP: STRADDLED {n}/{m}: {_ranges(ds)} lie inside the stored multi-day "
                                         f"row dated {e}, which already counts them — not derived again (never twice)"))
    res["written"] = {k: sorted(v) for k, v in written.items()}
    # THE LEDGER: each day is replayed on CATCH_UP_TRIES runs (an input can land after the catch-up)
    keep = {n: v for n, v in ledger.items() if n not in by}
    for n, ds in want.items():
        runs = {d: int((ledger.get(n) or {}).get(d, 0)) + 1 for d in ds}
        runs = {d: k for d, k in runs.items() if k < CATCH_UP_TRIES}
        if runs:
            keep[n] = runs
    if keep or ledger:
        _write_json(DERIVE_LEDGER, keep)
    return res


# ---------------------------------------------------------------------------------------------
# P4-Q6: WHERE EACH DAILY SERIES' MISSING DAY COMES BACK FROM — by the measuring point of the series' newest row
# ---------------------------------------------------------------------------------------------
# (read from each adapter, 2026-11-03; the route names are the summary's)
#   archive        a daily state read on chain (catch_up_gaps: Ethereum, BSC, Base, Polygon, GEODNET's Solana burn
#                  account): read at the day's first block (ArchiveBackfill.run).
#   near_archive   NEAR's header supply and staked NEAR (near_rpc): NEAR's archival RPC at the day's first block, the
#                  stake by the day's epoch (NearArchive — archive_backfill.py --near's read).
#   market_chart   CoinGecko price / market cap / volume / implied circulating: market_chart over the range (A4's read).
#   replay         an adapter that re-derives past days from its own cache when run (balance_flow: the day balances at
#                  each day's boundary block, GEODNET's mining-wallet release): run for the project in the catch-up, only
#                  the owed keys written.
#   tiers          the series' own tier reads the day back today: DefiLlama (the whole daily history is downloaded;
#                  window() keeps the owed days beside the trailing 30, and the fee family keeps every day anyway),
#                  growthepie (one full-history document; no longer skipped while a day is owed), NearBlocks (txn /
#                  address stats, the last `limit` days; no longer skipped while a day is owed), Blockscout stats (a
#                  from/to range: widened to the oldest owed day, only owed days kept before the routine 30), Dune
#                  (window() on its query's full result when the query is refreshed by its own rule), Hyperliquid
#                  candles, Boros, the Pendle maturity read and DefiLlama's price history (re-read in full every run).
#   derived        a series computed from others (`derived:` / `derive_history` / `schedule:config` / `declared:` / a
#                  `:delta` of a stock): recomputed for the filled days (derive_caught_up, derive_owed_flows).
#   not_recoverable  a PAGE SCRAPE: the page shows today's figure only, so a missed day cannot be read back. The cells
#                  name those days "not recoverable (<source> keeps no history)" (build_workbook, the Q0 holes note);
#                  the windows stay annualised over their covered days (D6-1, unchanged).
#   unwired        any other source: no past-day read is wired for it (logscan event reads continue from their own last
#                  block, never re-reading a day; point reads of other APIs). Named in the log as a hole, never 0.
NO_HISTORY_SOURCES = {
    "scrape": "the protocol's page, rendered (fetch/scrape.py)",
    "aethir_page": "Aethir's dashboard page (fetch/aethir_pages.py)",
    "browser_capture": "a page rendered in the browser (fetch/browser_capture.py)",
    "maple_page": "maple.finance/transparency (fetch/maple_transparency.py)",
}
TIER_REREAD_SOURCES = ("defillama", "growthepie", "nearblocks", "blockscout_stats", "dune", "hl_candles", "boros_api",
                       "pendle_maturity", "price_history",
                       # P5-1.11: each now widens its own read to the catch-up's owed days (fetch.base.catch_up_owed):
                       # Coinbase daily candles and DefiLlama coin prices (fetch/xref.py), the Ethereum P2P BigQuery
                       # leg (a dated query per run of owed days, fetch/eth_settlement.py)
                       "coinbase_exchange", "defillama_coins", "eth_settlement")
REPLAY_SOURCES = ("balance_flow",)
# P5-1.11 (Jake 2026-11-04): POINT READS OF A CONTRACT, READ AGAIN AT A PAST DAY'S FIRST BLOCK through an archive
# endpoint (resolve_archive) — {measuring-point head: the metrics its adapter's rows_at() returns}. The value is the
# contract's state at that block, the same call the live run makes at the head: the series' own measuring point.
CONTRACT_REPLAY_SOURCES = {
    "rewardvault": ("reward_emission_rate_annual", "emissions_tokens", "reward_rate_ends_unix",
                    "staker_yield_community_pool"),
    "share_vault": ("sethfi_accountant_rate", "sethfi_share_price_onchain"),
}
NEAR_ARCHIVE_METRICS = ("total_supply_protocol", "locked_tokens")
DERIVED_PREFIXES = ("derived", "derive_history", "schedule", "declared")


# P5-1.10 (Jake 2026-11-04): endpoints that answer TODAY's value only, by measuring point — a day no run read is
# permanently missing (there is nothing to read it back from). Hyperliquid's tokenDetails (total supply, futureEmissions,
# circulating and the P4-Q7 components) and validatorSummaries (total stake).
NO_HISTORY_POINTS = {
    "hypercore_info:tokenDetails": "Hyperliquid's tokenDetails answers today's value only (no history endpoint): a day "
                                   "with no run is permanently missing",
    "hypercore_info:validatorSummaries": "Hyperliquid's validatorSummaries answers today's stake only (no history "
                                         "endpoint): a day with no run is permanently missing",
    # P5-1.11: Etherscan's ethsupply2 (supply, BurntFees, Eth2Staking) takes no date and answers today's figures, and
    # native ETH supply is not contract state, so no archive read at a past block exists for it
    "etherscan:ethsupply2": "Etherscan's ethsupply2 answers today's figures only, and native ETH supply is not "
                            "contract state (no read at a past block exists): a day with no run is permanently missing",
}


# P5-1.11 (Jake 2026-11-04): COINGECKO SERVES NO PAST DAY OF THESE. Checked in CoinGecko's API reference (read
# 2026-11-04): /coins/{id}/history answers market_data with current_price, market_cap and total_volume only, and
# /coins/{id}/circulating_supply_chart and /total_supply_chart are "exclusively available to Enterprise plan". So a past
# day of circulating supply is market cap ÷ price (market_chart; CoinGecko's own definition, market cap = price x
# circulating supply), and a past day of FDV, total supply or max supply cannot be read: a named hole, never carried.
COINGECKO_PAST_DAY_METRICS = {"circulating_supply": "coingecko"}
COINGECKO_NO_HISTORY_METRICS = ("fdv_usd", "total_supply_coingecko", "max_supply", "total_supply")
COINGECKO_NO_HISTORY_WHY = ("CoinGecko serves today's value only for supply and FDV: /coins/{id}/history carries price, "
                            "market cap and volume, and the supply charts are Enterprise-only (API reference, read "
                            "2026-11-04)")
# the past-day circulating row: CoinGecko's own measuring point, the route in brackets (stripped by _measuring_point)
CG_CIRCULATING_PAST_SOURCE = "coingecko[past day: market cap ÷ price from market_chart — P5-1.11]"


def retired_why(name: str, metric: str, source: str = "") -> str:
    """P5-1.11: why a stored series is RETIRED (no adapter reads it any more), else ''. Its missing days are not holes
    any run could fill: they are counted apart, never as owed. config.RETIRED_SERIES, and a retired Dune query's
    metrics (a project's retired_dune_queries: Ether.fi's 8683038, retired 2026-09-24 by Jake)."""
    why = (getattr(config, "RETIRED_SERIES", None) or {}).get((name, metric))
    if why:
        return str(why)
    rq = (config.PROJECT_BY_NAME.get(name) or {}).get("retired_dune_queries") or {}
    if metric in (rq.get("metrics_it_served") or {}) and (not source
                                                           or f"dune:{rq.get('query_id')}" in str(source)):
        return (f"Dune {rq.get('query_id')} retired {rq.get('retired_on')} by {rq.get('retired_by')} — "
                f"{str(rq.get('why'))[:120]}")
    return ""


def _no_history_point(source, metric: str = "") -> str:
    mp = _measuring_point(str(source or ""))
    if mp.split(":")[0] == "coingecko" and metric in COINGECKO_NO_HISTORY_METRICS:
        return COINGECKO_NO_HISTORY_WHY
    return next((why for k, why in NO_HISTORY_POINTS.items() if mp == k or mp.startswith(k + ".")), "")


def _no_history_why(src, metric: str = "", name: str = "") -> str:
    """The catch-up's line for a not-recoverable hole: the endpoint's own sentence (NO_HISTORY_POINTS) or the page's;
    for CoinGecko's supply / FDV, why no rebuild exists for the project either (P5-1C.A3)."""
    why = _no_history_point(src, metric)
    if why:
        nr = (getattr(config, "SUPPLY_REBUILD_NOT_RECOVERABLE", {}) or {}).get((name, metric))
        return f"{why}; and no rebuild: {nr}" if nr else why
    head = str(src).split(":")[0]
    return f"{not_recoverable(src)}: {NO_HISTORY_SOURCES.get(head, head)} shows today's figure only"


def not_recoverable(source, metric: str = "") -> str:
    """'<source> keeps no history' for a series read from a page scrape (NO_HISTORY_SOURCES) or an endpoint that answers
    today's value only (NO_HISTORY_POINTS, P5-1.10; CoinGecko's supply and FDV, P5-1.11), else ''."""
    why = _no_history_point(source, metric)
    if why:
        return why
    head = _measuring_point(str(source or "")).split(":")[0]
    return f"{head} keeps no history" if head in NO_HISTORY_SOURCES else ""


def hole_route(name: str, metric: str, source: str, archive_series: set, p: dict | None = None) -> str:
    """The route a missing day of this series comes back by (see above)."""
    src = str(source or "")
    head = _measuring_point(src).split(":")[0]
    if retired_why(name, metric, src):
        return "retired"                             # P5-1.11: no adapter reads it any more — not a hole
    if (name, metric) in archive_series:
        return "archive"
    if head in CONTRACT_REPLAY_SOURCES and metric in CONTRACT_REPLAY_SOURCES[head]:
        return "contract_archive"                    # P5-1.11: the contract read again at the day's first block
    if head == "coingecko" and metric in COINGECKO_NO_HISTORY_METRICS and config.supply_rebuild_kind(name, metric):
        return "supply_rebuild"                      # P5-1C.A3: rebuilt from the chain / a fixed constant
    if _no_history_point(src, metric):
        return "not_recoverable"                     # P5-1.10: today-only endpoint (a :delta flow of one included)
    if head == "near_rpc" and metric in NEAR_ARCHIVE_METRICS:
        return "near_archive"
    if head == "coingecko" and p is not None and metric in api_series(p):
        return "market_chart"
    if head in REPLAY_SOURCES:
        return "replay"
    if head in DERIVED_PREFIXES or ":delta" in src or src.startswith("derived"):
        return "derived"
    if head in TIER_REREAD_SOURCES:
        return "tiers"
    if head in NO_HISTORY_SOURCES:
        return "not_recoverable"
    return "unwired"


def _observed_daily(days: list[str]) -> bool:
    """The series' own spacing is daily: the median gap of its last seven distinct days is under 6 days (the build's
    _observed_cadence)."""
    d = pd.Series(pd.to_datetime(sorted(set(days))[-7:]))
    return len(d) < 3 or float(d.diff().dt.days.dropna().median()) < 6


def scan_holes(store, projects: list[dict], archive_series: set) -> dict:
    """{(project, metric): {"route", "source", "days": [owed, newest first]}} — every DAILY series of `projects` (parked
    projects, weekly / monthly series, a scan read from the chain's start and declared zeros left out) with a missing
    day in [max(today - W, its first stored day, its start), yesterday]."""
    lo, yesterday = catch_up_bounds()
    parked, starts = catch_up_parked(), series_starts()
    by = {p["name"]: p for p in projects if p["name"] not in parked}
    if not by:
        return {}
    q = ("SELECT project, metric, substr(date,1,10), source FROM metrics WHERE project IN (%s) ORDER BY project, metric, "
         "date" % ",".join("?" * len(by)))
    groups: dict = {}
    for n, m, d, s in store.conn.execute(q, tuple(by)):
        groups.setdefault((n, m), []).append((d, str(s)))
    out = {}
    for (n, m), rows in groups.items():
        measured = [d for d, s in rows if not s.startswith("declared:zero")] or [d for d, _ in rows]
        if config.series_granularity(n, m) != "daily" or config.scanned_from_genesis(n, m) \
                or not _observed_daily(measured):
            continue
        frm = lo
        s0 = starts.get((n, m))
        if s0:
            frm = max(frm, pd.Timestamp(s0["from"]))
        # a DECLARED source hole (config.declared_source_hole: the source itself never reported those days — Morpho's
        # fees) is not read again every run; the build names it already
        dh = config.declared_source_hole(n, m) or {}
        skip = ({str(x.date()) for x in pd.date_range(dh["from"], dh["to"])} if dh.get("from") and dh.get("to")
                else set())
        days = owed_days(n, m, rows, frm, yesterday, skip)
        if days:
            src = rows[-1][1]
            out[(n, m)] = {"route": hole_route(n, m, src, archive_series, by[n]), "source": _measuring_point(src),
                           "days": days}
    return out


def _guarded_rows(store, frame: pd.DataFrame, owed: dict) -> tuple[pd.DataFrame, dict]:
    """The rows of `frame` on an owed day of their series ({(project, metric): {days}}) that may be written: inside the
    declared bound, not beside a stored row of another measuring point, not interleaving a declared handover. ({kept
    frame}, {(project, metric): {day: why not}})."""
    keep, why = [], {}
    if frame is None or frame.empty:
        return pd.DataFrame(columns=LONG_COLUMNS), why
    f = frame.assign(d=pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d"))
    stored: dict = {}
    for r in f.itertuples(index=False):
        k = (r.project, r.metric)
        if r.d not in (owed.get(k) or ()):
            continue
        if k not in stored:
            g = pd.read_sql_query("SELECT date, source FROM metrics WHERE project=? AND metric=? ORDER BY date",
                                  store.conn, params=k)
            g["date"] = pd.to_datetime(g["date"]).dt.normalize()
            stored[k] = g
        lo, hi = config.sanity_bounds(r.project, r.metric, r.date)
        if (lo is not None and r.value < lo) or (hi is not None and r.value > hi):
            why.setdefault(k, {})[r.d] = f"{r.value:,.4f} is outside the declared bound [{lo}, {hi}]"
            continue
        ok, bad = neighbours_agree(stored[k], pd.Timestamp(r.d), str(r.source))
        bad = bad if not ok else breaks_handover_order(r.project, r.metric, stored[k], r.d, str(r.source))
        if bad:
            why.setdefault(k, {})[r.d] = bad
            continue
        keep.append(r)
    kf = pd.DataFrame(keep).drop(columns=["d"]) if keep else pd.DataFrame(columns=LONG_COLUMNS)
    return (kf[LONG_COLUMNS] if not kf.empty else kf), why


def near_catch_up(store, projects: list[dict], ledger: dict, deadline: float | None, max_days: int,
                  near=None) -> dict:
    """NEAR's header supply and staked NEAR (route near_archive): the owed days, newest first, at most `max_days`, each
    by NearArchive.rows at the day's first block (the stake by the day's epoch) — archive_backfill.py --near's read,
    source strings marked `archive`. {"lines", "gaps", "filled", "left", "holes", "entries", "checked"}."""
    res = {"lines": [], "gaps": {}, "filled": {}, "left": {}, "holes": {}, "entries": {}, "checked": set()}
    p = next((x for x in projects if x["name"] == "Near"), None)
    if p is None or "Near" in catch_up_parked():
        return res
    lo, yesterday = catch_up_bounds()
    for m in NEAR_ARCHIVE_METRICS:
        rows = _series_rows(store, "Near", m)
        if not rows or not _measuring_point(rows[-1][1]).startswith("near_rpc"):
            continue
        res["checked"].add(("Near", m))
        given = {d for d, k in _ledger_tries(ledger.get(f"Near|{m}"), lo).items() if k >= CATCH_UP_TRIES}
        days = owed_days("Near", m, rows, lo, yesterday, given)
        if days:
            res["gaps"][("Near", m)] = days
    if not res["gaps"]:
        return res
    todo = sorted({d for ds in res["gaps"].values() for d in ds}, reverse=True)
    na = near or NearArchive(deadline=deadline)
    errs: dict = {}
    read, stop = [], ""
    for d in todo:
        if len(read) >= max_days:
            stop = f"day budget spent at {d} ({max_days} day(s) a run)"
            break
        if deadline is not None and time.monotonic() >= deadline:
            stop = f"budget spent at {d}"
            break
        want = tuple(m for (_n, m), ds in res["gaps"].items() if d in ds)
        try:
            got = pd.DataFrame(na.rows(p, pd.Timestamp(d), want))
        except NearBudgetSpent as e:
            stop = f"budget spent at {d} ({e})"
            break
        except Exception as e:  # noqa: BLE001 — a day the archival node does not serve stays a named hole
            for m in want:
                errs.setdefault(("Near", m), {})[d] = f"{type(e).__name__}: {redact(e)}"[:200]
            read.append(d)
            continue
        read.append(d)
        kept, why = _guarded_rows(store, got, {k: {d} for k in res["gaps"]})
        for k, w in why.items():
            errs.setdefault(k, {}).update(w)
        if not kept.empty:
            _write_new(store, kept)
        for m in want:
            if not ((got["metric"] == m).any() if not got.empty else False) and d not in errs.get(("Near", m), {}):
                errs.setdefault(("Near", m), {})[d] = ("the archival node kept no epoch info for the day (UNKNOWN_EPOCH)"
                                                       if m == "locked_tokens" else "no row from the read")
    if hasattr(na, "save"):
        na.save()
    for (n, m), days in sorted(res["gaps"].items()):
        have = {d for d, _ in _series_rows(store, n, m)}
        filled = [d for d in days if d in have]
        left = [d for d in days if d not in have]
        holes = [d for d in left if d in errs.get((n, m), {})]
        tries = {d: k for d, k in _ledger_tries(ledger.get(f"{n}|{m}"), lo).items() if d not in filled}
        for d in holes:
            tries[d] = int(tries.get(d, 0)) + 1
        given = [d for d in holes if tries[d] >= CATCH_UP_TRIES]
        if tries:
            res["entries"][f"{n}|{m}"] = {"tries": tries}
        res["filled"][(n, m)], res["left"][(n, m)], res["holes"][(n, m)] = filled, left, holes
        host = (getattr(na, "stats", {}) or {}).get("endpoint") or "NEAR's archival RPC"
        txt = (f"CATCH-UP {n}/{m}: {len(filled)} day(s) filled" + (f" ({_ranges(filled)})" if filled else "")
               + f", {len(left)} left" + (f" ({_ranges(left)})" if left else "")
               + f" — NEAR archive read at each day's first block via {host}")
        if holes:
            txt += (f"; HOLE(S) {_ranges(holes)} — {errs[(n, m)][holes[0]]} (never 0, never carried; tried on "
                    f"{max(tries[d] for d in holes)} of {CATCH_UP_TRIES} runs)")
        if given:
            txt += f"; given up after {CATCH_UP_TRIES} runs: {_ranges(given)} — left to archive_backfill.py --near"
        rest = [d for d in left if d not in holes]
        if rest:
            txt += f"; not reached this run (budget): {_ranges(rest)} — the next run continues"
        res["lines"].append(("warning" if holes else "info", txt))
    if stop:
        res["lines"].append(("info", f"CATCH-UP: NEAR archive: {stop}; the next run continues"))
    return res


def tier_fill_guard(store, frame: pd.DataFrame, owed: dict, window_days, keep_pairs=()) -> pd.DataFrame:
    """P4-Q6, NEVER AN OVERWRITE from the tiers' read-back: of today's frame, a row dated BEFORE the routine window on a
    day the catch-up asked a tier to read back (fetch.base.CATCH_UP_OWED, `owed`) whose key already holds a row is
    dropped — such a row is there only because the window was widened (a derivation from the read-back day). The
    series that keep their whole history by their own rule (the run's backfill pairs, DefiLlama's fee family,
    config.FULL_HISTORY_SERIES) are left as they were."""
    if frame is None or frame.empty or not owed or window_days is None:
        return frame
    cut = today() - pd.Timedelta(days=int(window_days))
    days = {d for v in owed.values() for d in v}
    frame = frame.reset_index(drop=True)
    f = frame.assign(day_=pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d"))
    cand = f[(pd.to_datetime(f["date"]) < cut) & f["day_"].isin(days)]
    if cand.empty:
        return frame
    have = set(store.conn.execute(
        "SELECT substr(date,1,10), project, metric FROM metrics WHERE project IN (%s)" % ",".join(
            "?" * cand["project"].nunique()), tuple(cand["project"].unique())).fetchall())
    own = set(keep_pairs or ()) | set(config.FULL_HISTORY_SERIES)
    drop = [i for i, r in zip(cand.index, cand.itertuples(index=False))
            if (r.day_, r.project, r.metric) in have and (r.project, r.metric) not in own
            and not (str(r.source).startswith("defillama") and r.metric in config.DEFILLAMA_REREAD_METRICS)]
    return frame.drop(index=drop)


def tier_fill_line(store, owed: dict) -> tuple[str, str] | None:
    """P4-Q6: after today's tiers, what they read back of the owed days: ("info"|"warning", line), or None."""
    if not owed:
        return None
    filled = left = 0
    worst = []
    by_src: dict = {}
    for (n, m), days in sorted(owed.items()):
        rows = _series_rows(store, n, m)
        have = {d for d, _ in rows}
        got = [d for d in days if d in have]
        filled, left = filled + len(got), left + len(days) - len(got)
        # P5-1.11: per source (the series' measuring-point head)
        head = _measuring_point(rows[-1][1]).split(":")[0] if rows else "?"
        b = by_src.setdefault(head, {"series": 0, "found": 0, "filled": 0, "left": 0})
        b["series"], b["found"] = b["series"] + 1, b["found"] + len(days)
        b["filled"], b["left"] = b["filled"] + len(got), b["left"] + len(days) - len(got)
        if len(got) < len(days):
            worst.append(f"{n}/{m} {_ranges([d for d in days if d not in have])}")
    line = (f"CATCH-UP (today's tiers): {filled} of {filled + left} owed day(s) read back over {len(owed)} API series "
            f"from their own history, {left} left" + (f" — not served: {'; '.join(worst[:8])}"
                                                      + (f" and {len(worst) - 8} more" if len(worst) > 8 else "")
                                                      + " (never 0, never carried)" if worst else "")
            + "; " + by_source_line(by_src, "BY SOURCE (owed / read back / left)"))
    # INFO: a day the source does not serve is named here every run until it is (a hole, never 0) — not a run failure
    return ("info", line)


def default_replays() -> dict:
    """{source: factory(budget_s) -> adapter} for the replay route."""
    def balance_flow(budget_s):
        from .balance_flow import BalanceFlow
        return BalanceFlow(budget_s=budget_s)
    return {"balance_flow": balance_flow}


def replay_catch_up(store, projects: list[dict], scan: dict, deadline: float | None, replays: dict | None = None) -> dict:
    """The replay route: the adapter run for each project with owed days (its own budgeted read; its cache keeps the
    progress), and of what it returns ONLY the owed keys written (guarded). {"lines", "filled", "left"}."""
    res = {"lines": [], "filled": {}, "left": {}}
    want = {k: v for k, v in scan.items() if v["route"] == "replay"}
    if not want:
        return res
    replays = default_replays() if replays is None else replays
    by = {p["name"]: p for p in projects}
    for name in sorted({n for n, _ in want}):
        mine = {(n, m): set(v["days"]) for (n, m), v in want.items() if n == name}
        heads = sorted({want[k]["source"].split(":")[0] for k in mine})
        frames, err = [], ""
        for head in heads:
            left_s = None if deadline is None else deadline - time.monotonic()
            if left_s is not None and left_s <= 5:
                err = "not reached this run (budget) — the next run continues"
                break
            try:
                ad = replays[head](max(5.0, left_s) if left_s is not None else 600.0)
                o = FetchOutput()
                ad.run([by[name]], None, o)
                frames.append(o.frame())
            except Exception as e:  # noqa: BLE001 — a failed read leaves the days owed
                err = f"the {head} read failed: {type(e).__name__}: {redact(e)}"[:200]
        frame = pd.concat([f for f in frames if not f.empty]) if any(not f.empty for f in frames) else None
        kept, why = _guarded_rows(store, frame, mine)
        if not kept.empty:
            _write_new(store, kept)
        for (n, m), days in sorted(mine.items()):
            have = {d for d, _ in _series_rows(store, n, m)}
            days = sorted(days, reverse=True)
            filled = [d for d in days if d in have]
            left = [d for d in days if d not in have]
            res["filled"][(n, m)], res["left"][(n, m)] = filled, left
            src = want[(n, m)]["source"]
            txt = (f"CATCH-UP {n}/{m}: {len(filled)} day(s) filled" + (f" ({_ranges(filled)})" if filled else "")
                   + f", {len(left)} left" + (f" ({_ranges(left)})" if left else "")
                   + f" — {src.split(':')[0]}'s own read, the series' own source ({src})")
            bad = why.get((n, m)) or {}
            if bad:
                d0 = sorted(bad)[0]
                txt += f"; NOT WRITTEN {_ranges(bad)} — {bad[d0]}"
            if left and not bad:
                txt += f"; {err or 'the read served no row for them'} (never 0, never carried; the next run tries again)"
            res["lines"].append(("warning" if left else "info", txt))
    return res


def by_source_line(by_src: dict, title: str = "CATCH-UP BY SOURCE (holes found / filled / left / not recoverable)") \
        -> str:
    """P5-1.11: one line, every source head with its series count and its holes, the most holes first."""
    parts = []
    for head, b in sorted(by_src.items(), key=lambda kv: (-kv[1]["found"], kv[0])):
        parts.append(f"{head} {b['series']} series: {b['found']} found, {b['filled']} filled, {b['left']} left"
                     + (f", {b['not_recoverable']} not recoverable" if b.get("not_recoverable") else "")
                     + (f" [{', '.join(sorted(b['routes']))}]" if b.get("routes") else ""))
    return f"{title}: " + "; ".join(parts)


def default_contract_readers() -> dict:
    """{head: (chain of the project's read, rows_at(project, w3, block, day))} for the contract_archive route."""
    from .reward_vault import rows_at as rv_rows
    from .share_price import SharePrice

    def sv_chain(p):
        return (p.get("accountant_rate") or p.get("share_price_onchain") or {}).get("chain", "ethereum")
    return {"rewardvault": (lambda p: (p.get("reward_vault_rates") or {}).get("chain", "ethereum"), rv_rows),
            "share_vault": (sv_chain, SharePrice.rows_at)}


def contract_catch_up(store, projects: list[dict], scan: dict, deadline: float | None, max_days: int | None = None,
                      readers: dict | None = None, resolve=None, day_blocks=None) -> dict:
    """P5-1.11, THE contract_archive ROUTE: each owed day of a contract point read (CONTRACT_REPLAY_SOURCES) read again
    at that day's first block through an archive endpoint, newest first, at most `max_days` days per project and head,
    nothing started after `deadline`. Of what the read returns ONLY the owed keys are written, guarded as every
    catch-up write is (inside the declared bound, beside rows of the same measuring point, never an overwrite).
    {"lines", "filled", "left"}."""
    res = {"lines": [], "filled": {}, "left": {}}
    want = {k: v for k, v in scan.items() if v["route"] == "contract_archive"}
    if not want:
        return res
    readers = default_contract_readers() if readers is None else readers
    max_days = CATCH_UP_DAYS if max_days is None else int(max_days)
    by = {p["name"]: p for p in projects}
    for name, head in sorted({(n, want[(n, m)]["source"].split(":")[0]) for n, m in want}):
        mine = {(n, m): set(v["days"]) for (n, m), v in want.items() if n == name and v["source"].split(":")[0] == head}
        days = sorted(set().union(*mine.values()), reverse=True)[:max_days]
        p, err, rows = by[name], "", []
        chain_of, rows_at = readers[head]
        chain = chain_of(p)
        oldest = (today() - pd.Timestamp(days[-1])).days + 1 if days else 1
        try:
            w3, host = resolve(chain) if resolve else resolve_archive(chain, days=float(oldest))
        except Exception as e:  # noqa: BLE001
            w3, host, err = None, None, f"the archive endpoint lookup failed: {type(e).__name__}: {redact(e)}"[:200]
        if w3 is None:
            err = err or f"no endpoint serves {chain} state {oldest} day(s) back — the next run tries again"
        else:
            try:
                db = day_blocks(w3, chain) if day_blocks else DayBlocks(w3, chain)
                top = int(w3.eth.block_number)
                for d in days:
                    if deadline is not None and time.monotonic() >= deadline:
                        err = "not reached this run (budget) — the next run continues"
                        break
                    day = pd.Timestamp(d)
                    for m, v, src in rows_at(p, w3, db.at(day, top), day):
                        rows.append({"date": day, "project": name, "metric": m, "value": float(v), "source": src,
                                     "tier": 2})
                if hasattr(db, "save"):
                    db.save()
            except Exception as e:  # noqa: BLE001 — a failed read leaves the days owed
                err = f"the {head} read at a past block failed: {type(e).__name__}: {redact(e)}"[:200]
        frame = pd.DataFrame(rows)[LONG_COLUMNS] if rows else None
        kept, why = _guarded_rows(store, frame, mine)
        if not kept.empty:
            _write_new(store, kept)
        for (n, m), owed in sorted(mine.items()):
            have = {d for d, _ in _series_rows(store, n, m)}
            ds = sorted(owed, reverse=True)
            filled, left = [d for d in ds if d in have], [d for d in ds if d not in have]
            res["filled"][(n, m)], res["left"][(n, m)] = filled, left
            txt = (f"CATCH-UP {n}/{m}: {len(filled)} day(s) filled" + (f" ({_ranges(filled)})" if filled else "")
                   + f", {len(left)} left" + (f" ({_ranges(left)})" if left else "")
                   + f" — the {head} contract read again at each day's first block"
                   + (f" via {host}" if w3 is not None and host else ""))
            bad = why.get((n, m)) or {}
            if bad:
                txt += f"; NOT WRITTEN {_ranges(bad)} — {bad[sorted(bad)[0]]}"
            if left and not bad:
                txt += f"; {err or 'the read served no row for them'} (never 0, never carried; the next run tries again)"
            res["lines"].append(("warning" if left else "info", txt))
    return res


# ---------------------------------------------------------------------------------------------
# P5-1C.A3 (Jake 2026-11-04): COINGECKO'S TOTAL / MAX SUPPLY AND FDV, REBUILT FOR A DAY COINGECKO HAS NO ROW
# ---------------------------------------------------------------------------------------------
# config.SUPPLY_REBUILD declares, per project, the same-quantity total (stored on-chain legs: each a contract or NEAR
# block-header read at the day's first block, a missed day of a leg filled by the archive route earlier in the same
# catch-up), the max supply a contract or the project's own source fixes, and CoinGecko's FDV basis. The rebuilt row is
# its own measuring point (supply_rebuild:<what>), written only on an owed day, and only while the declared check
# (config.SUPPLY_REBUILD_TOL, stated before any overlap was measured) passes on CoinGecko's own stored days.
def _daily(rows: pd.DataFrame) -> pd.Series:
    if rows is None or rows.empty:
        return pd.Series(dtype=float)
    g = rows.assign(date=pd.to_datetime(rows["date"]).dt.normalize()).sort_values("date")
    return g.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)


def _coingecko_own(rows: pd.DataFrame) -> pd.DataFrame:
    """CoinGecko's own rows of a series (its measuring point), the rebuilt days left out."""
    if rows is None or rows.empty:
        return rows
    mp = rows["source"].astype(str).map(_measuring_point)
    return rows[mp.str.split(":").str[0] == "coingecko"]


def supply_rebuild_values(name: str, metric: str, series: dict) -> pd.Series:
    """The rebuilt value per day where every input is held ON that day ({metric: rows} in `series`): the total formula,
    the declared max (every day of `series['__days__']`), or price x CoinGecko's FDV basis. Never carried, never 0."""
    spec = config.SUPPLY_REBUILD.get(name) or {}
    kind = config.supply_rebuild_kind(name, metric)

    def total() -> pd.Series:
        t = _daily(series.get(spec.get("total")))
        for leg in spec.get("less") or ():
            t = (t - _daily(series.get(leg))).dropna()
        return t.dropna()
    if kind == "total":
        return total()
    if kind == "max":
        days = series.get("__days__")
        return pd.Series(float(spec["max"]), index=pd.DatetimeIndex(days if days is not None else []))
    if kind == "fdv":
        # THE PRICE CLOSING THE DAY (Jake: "at the block closing each missing day"): market_chart's 00:00 UTC point of
        # the NEXT day, stored as price_usd dated d+1 (for yesterday: today's read) — SUPPLY_HISTORY's next-day rule
        px = _daily(series.get("price_usd"))
        px.index = px.index - pd.Timedelta(days=1)
        if spec.get("fdv_basis") == "max":
            return (px * float(spec["max"])).dropna()
        return (px * total()).dropna()
    return pd.Series(dtype=float)


def supply_rebuild_inputs(name: str, metric: str) -> tuple:
    """The stored series a rebuild reads (beside CoinGecko's own series)."""
    spec = config.SUPPLY_REBUILD.get(name) or {}
    kind = config.supply_rebuild_kind(name, metric)
    legs = (spec.get("total"), *(spec.get("less") or ()))
    if kind == "total":
        return legs
    if kind == "fdv":
        return ("price_usd",) if spec.get("fdv_basis") == "max" else ("price_usd", *legs)
    return ()


def supply_rebuild_check(name: str, metric: str, cg: pd.Series, rebuilt: pd.Series) -> tuple[bool, str]:
    """(passes, how) — the declared same-quantity check (config.SUPPLY_REBUILD_TOL) of the rebuild against CoinGecko's
    OWN stored days: the most recent `min_days` common days, median and worst |rebuilt / CoinGecko − 1| within the
    declared bounds. Fewer common days than `min_days` fails (unchecked is never passed)."""
    kind = config.supply_rebuild_kind(name, metric)
    tol = config.SUPPLY_REBUILD_TOL.get(kind) or {}
    common = cg.index.intersection(rebuilt.index).sort_values()
    if kind != "max":
        common = common[-int(tol.get("min_days") or 1):]
    if len(common) < int(tol.get("min_days") or 1):
        return False, (f"{len(common)} common day(s) with CoinGecko's own, fewer than the "
                       f"{int(tol.get('min_days') or 1)} the check needs")
    dev = (rebuilt[common] / cg[common].replace(0, float("nan")) - 1).abs()
    if dev.isna().any():
        return False, "CoinGecko's figure is 0 on a common day"
    med, worst = float(dev.median()), float(dev.max())
    how = (f"median |diff| {med:.4%}, worst {worst:.4%} over {len(common)} common day(s) "
           f"{common.min().date()}..{common.max().date()}")
    bad = [f"median > {tol['median']:.2%}" for _ in [0] if tol.get("median") is not None and not med <= tol["median"]]
    bad += [f"worst > {tol['worst']:.4%}" for _ in [0] if tol.get("worst") is not None and not worst <= tol["worst"]]
    return (not bad), how + (f" — FAILED ({', '.join(bad)})" if bad else "")


def supply_rebuild_source(name: str, metric: str, how: str) -> str:
    """The rebuilt row's source: its own measuring point (supply_rebuild:<what>) and, in the bracket, what was read and
    the check that let it stand in for CoinGecko's figure."""
    spec = config.SUPPLY_REBUILD.get(name) or {}
    kind = config.supply_rebuild_kind(name, metric)
    formula = "-".join(x for x in (spec.get("total"), *(spec.get("less") or ())) if x)
    if kind == "total":
        what, label = formula, f"rebuilt day of CoinGecko's total: {spec.get('label')}"
    elif kind == "max":
        what, label = "declared_max", f"rebuilt day of CoinGecko's max supply: {float(spec['max']):,.0f} — {spec['max_cite']}"
    else:
        basis = "max_supply" if spec.get("fdv_basis") == "max" else formula
        what = f"price_usd_x_{basis}"
        label = ("rebuilt day of CoinGecko's FDV: the day's price_usd (00:00 UTC) x "
                 + (f"the declared max {float(spec['max']):,.0f}" if basis == "max_supply" else spec.get("label")))
    txt = f"{label}; check vs CoinGecko's stored days: {how} — P5-1C.A3".replace("[", "(").replace("]", ")")
    return f"{config.SUPPLY_REBUILD_POINT}:{what}[{txt}]"


def supply_rebuild_catch_up(store, projects: list[dict], scan: dict) -> dict:
    """P5-1C.A3, THE supply_rebuild ROUTE: each owed day of CoinGecko's total / max supply or FDV rebuilt from the stored
    on-chain legs, the declared max or price x CoinGecko's basis — when the declared check passes on CoinGecko's own
    days. Only owed keys are written (never an overwrite), inside the declared bound. A failed check writes nothing and
    names the series NOT RECOVERABLE with the measured reason. {"lines", "filled", "left", "unrebuildable"}."""
    res = {"lines": [], "filled": {}, "left": {}, "unrebuildable": {}}
    want = {k: v for k, v in scan.items() if v["route"] == "supply_rebuild"}
    names = {p["name"] for p in projects}
    for (n, m), v in sorted(want.items()):
        if n not in names:
            continue
        own = _coingecko_own(pd.read_sql_query(
            "SELECT date, value, source FROM metrics WHERE project=? AND metric=?", store.conn, params=(n, m)))
        series = {x: pd.read_sql_query("SELECT date, value, source FROM metrics WHERE project=? AND metric=?",
                                       store.conn, params=(n, x)) for x in supply_rebuild_inputs(n, m) if x}
        cg = _daily(own)
        owed = sorted(v["days"], reverse=True)
        series["__days__"] = pd.to_datetime(sorted(set(map(str, cg.index.date)) | set(owed)))
        rebuilt = supply_rebuild_values(n, m, series)
        ok, how = supply_rebuild_check(n, m, cg, rebuilt)
        if not ok:
            res["unrebuildable"][(n, m)] = f"the declared rebuild does not measure CoinGecko's figure: {how}"
            res["left"][(n, m)] = owed
            continue
        src = supply_rebuild_source(n, m, how)
        rows, why = [], {}
        for d in owed:
            day = pd.Timestamp(d)
            if day not in rebuilt.index:
                why[d] = "an input is not stored for the day: " + ", ".join(
                    x for x in supply_rebuild_inputs(n, m) if x and day not in _daily(series.get(x)).index)
                continue
            val = float(rebuilt[day])
            lo, hi = config.sanity_bounds(n, m, day)
            if (lo is not None and val < lo) or (hi is not None and val > hi):
                why[d] = f"{val:,.4f} is outside the declared bound [{lo}, {hi}]"
                continue
            rows.append({"date": day, "project": n, "metric": m, "value": val, "source": src, "tier": 2})
        if rows:
            _write_new(store, pd.DataFrame(rows)[LONG_COLUMNS])
        have = {d for d, _ in _series_rows(store, n, m)}
        filled, left = [d for d in owed if d in have], [d for d in owed if d not in have]
        res["filled"][(n, m)], res["left"][(n, m)] = filled, left
        txt = (f"CATCH-UP {n}/{m}: {len(filled)} day(s) rebuilt" + (f" ({_ranges(filled)})" if filled else "")
               + f", {len(left)} left" + (f" ({_ranges(left)})" if left else "")
               + f" — {src.split('[')[0]} (P5-1C.A3; check vs CoinGecko's stored days: {how})")
        if left:
            d0 = next((d for d in left if d in why), None)
            txt += (f"; NOT REBUILT {_ranges([d for d in left if d in why])} — {why[d0]}" if d0 else "") + \
                   " (never 0, never carried; the next run tries again)"
        res["lines"].append(("info" if not left else "warning", txt))
    return res


def catch_up(store, projects: list[dict], budget_s: float | None = None, max_days: int | None = None, reader=None,
             solana: dict | None = None, archive_chains: set | None = None, resolve=None, api=None,
             derive: bool = True, valuers=None, near=None, replays: dict | None = None, tiers: bool = True,
             contracts: dict | None = None, contract_resolve=None, day_blocks=None) -> dict:
    """Fill every owed day, within the budgets: FIRST the daily API series (api_catch_up: CoinGecko's market_chart, the
    series' own read — P3-4 A4), THEN the daily on-chain series (catch_up_gaps, by ArchiveBackfill.run), THEN the
    series derived from them for the caught-up days (derive_caught_up: the run's own derivations, replayed). The caller
    holds the store's RunLock (token_metrics.py) — this takes none. Returns {"lines": [(level, text)], "gaps",
    "filled", "left", "holes", "result", "api", "derived"}: one line per series that had owed days (API, then
    on-chain), one per derived series stored, then the summary line ("info" or "warning")."""
    t0 = time.monotonic()
    dflt_min, dflt_days = catch_up_defaults()
    budget_s = dflt_min * 60 if budget_s is None else float(budget_s)
    max_days = dflt_days if max_days is None else int(max_days)
    ledger = _read_json(CATCH_UP_LEDGER)
    gaps, excluded, checked = catch_up_gaps(store, projects, ledger)
    # P4-Q6: EVERY daily series' missing days in the window, before anything is read (the summary's "found")
    scan = scan_holes(store, projects, checked)
    # A4: THE API SERIES FIRST, so whatever reads a stored price (Ethereum's and NEAR's P2P legs, a burn or buyback
    # valued at the day's price) finds the missed days' prices when the on-chain days and the derivations are done
    api_res = api_catch_up(store, projects, deadline=t0 + budget_s, max_days=max_days, api=api)
    by_name = {p["name"]: p for p in projects}
    near_res = {"lines": [], "gaps": {}, "filled": {}, "left": {}, "holes": {}, "entries": {}, "checked": set()}
    replay_res = {"lines": [], "filled": {}, "left": {}}
    contract_res = {"lines": [], "filled": {}, "left": {}}
    supply_res = {"lines": [], "filled": {}, "left": {}, "unrebuildable": {}}
    kinds = {"parked": 0, "weekly": 0, "not archivable or by design": 0, "nothing stored yet": 0}
    for _, _, why in excluded:
        k = ("parked" if why.startswith("parked") else "weekly" if why.startswith("a weekly epoch")
             else "nothing stored yet" if why.startswith("nothing stored") else "not archivable or by design")
        kinds[k] += 1
    notcov = ", ".join(f"{k} {v}" for k, v in kinds.items() if v)
    lines: list[tuple[str, str]] = list(api_res["lines"])
    out = {"lines": lines, "gaps": gaps, "filled": {}, "left": {}, "holes": {}, "excluded": excluded, "result": None,
           "api": api_res, "derived": {}, "scan": scan, "near": near_res, "replay": replay_res, "tier_owed": {},
           "holes_found": {}, "holes_filled": {}, "holes_left": {}, "not_recoverable": {}, "retired": {},
           "by_source": {}, "contract": contract_res, "supply": supply_res}

    def other_routes() -> None:
        """P4-Q6: NEAR's archive and the replay adapters, inside what is left of the one budget; P5-1.11: the contract
        point reads at each owed day's first block."""
        nonlocal near_res, replay_res, contract_res, supply_res
        left_s = max(0.0, budget_s - (time.monotonic() - t0))
        near_res = near_catch_up(store, projects, ledger, t0 + budget_s if left_s else time.monotonic(), max_days,
                                 near=near)
        lines.extend(near_res["lines"])
        out["near"] = near_res
        replay_res = replay_catch_up(store, projects, scan, t0 + budget_s, replays=replays)
        lines.extend(replay_res["lines"])
        out["replay"] = replay_res
        contract_res = contract_catch_up(store, projects, scan, t0 + budget_s, max_days=max_days, readers=contracts,
                                         resolve=contract_resolve, day_blocks=day_blocks)
        lines.extend(contract_res["lines"])
        out["contract"] = contract_res
        # P5-1C.A3: LAST, so the on-chain legs (archive, NEAR) and the day's price (market_chart) are filled first
        supply_res = supply_rebuild_catch_up(store, projects, scan)
        lines.extend(supply_res["lines"])
        out["supply"] = supply_res

    def tally() -> str:
        """P4-Q6: every scanned series' missing days, after the fills — found / filled / left / not recoverable — with a
        line for each series no read of the catch-up fills (today's tiers, derived, not recoverable, unwired)."""
        lo, yesterday = catch_up_bounds()
        found = filled = left = nr = to_tiers = 0
        starts_now, named, cap = series_starts(), 0, 40
        by_src = out["by_source"]
        retired_n = 0
        for (n, m), v in sorted(scan.items()):
            route, src = v["route"], v["source"]
            # P5-1C.A3: a declared rebuild whose check failed on CoinGecko's own days is not recoverable — measured why
            nr_why = supply_res["unrebuildable"].get((n, m)) if route == "supply_rebuild" else None
            if nr_why:
                route = "not_recoverable"
            if route == "retired":
                # P5-1.11: no adapter reads it any more (a retired query or series) — its missing days are not holes
                retired_n += len(v["days"])
                out["retired"][(n, m)] = v["days"]
                lines.append(("info", f"CATCH-UP {n}/{m}: RETIRED, not counted as holes ({len(v['days'])} day(s) "
                                      f"since its last row) — {retired_why(n, m, src)}"))
                continue
            rows = _series_rows(store, n, m)
            frm = lo
            s0 = starts_now.get((n, m))
            if s0:
                frm = max(frm, pd.Timestamp(s0["from"]))
            # still owed; a day no longer owed only because a start was found this run ("not before") was never a hole
            still = set(owed_days(n, m, rows, frm, yesterday))
            days = [d for d in v["days"] if d >= str(frm.date())]
            got = [d for d in days if d not in still]
            rest = [d for d in days if d in still]
            found += len(days)
            filled += len(got)
            out["holes_found"][(n, m)], out["holes_filled"][(n, m)] = days, got
            # P5-1.11: PER SOURCE (the measuring point's head) — found / filled / left / not recoverable, and the route
            b = by_src.setdefault(_measuring_point(src).split(":")[0] or "?",
                                  {"series": 0, "found": 0, "filled": 0, "left": 0, "not_recoverable": 0, "routes": set()})
            b["series"] += 1
            b["found"] += len(days)
            b["filled"] += len(got)
            b["routes"].add(route)
            if route == "not_recoverable":
                b["not_recoverable"] += len(rest)
            else:
                b["left"] += len(rest)
            if rest and route in ("derived", "unwired"):
                named += 1
                if named > cap:          # one line each for the first `cap`; the rest counted in one line below
                    left += len(rest)
                    out["holes_left"][(n, m)] = rest
                    continue
            if route == "not_recoverable":
                nr += len(rest)
                out["not_recoverable"][(n, m)] = rest
                if rest:
                    lines.append(("info", f"CATCH-UP {n}/{m}: {len(rest)} day(s) not recoverable ({_ranges(rest)}) — "
                                          f"not recoverable ({_no_history_why(src, m, n)}"
                                          + (f"; {nr_why}" if nr_why else "") + "); named in the cell comment, its "
                                          "windows annualised over their covered days"))
                continue
            left += len(rest)
            out["holes_left"][(n, m)] = rest
            if not rest:
                continue
            if route == "tiers":
                to_tiers += len(rest)
                out["tier_owed"][(n, m)] = rest
                lines.append(("info", f"CATCH-UP {n}/{m}: {len(rest)} day(s) owed ({_ranges(rest)}) — read back by "
                                      + ("today's" if tiers else "the next plain run's")
                                      + f" {src.split(':')[0]} tier from its own history ({src}); only the owed days "
                                        f"are kept (never an overwrite)"))
            elif route == "derived":
                lines.append(("info", f"CATCH-UP {n}/{m}: {len(rest)} day(s) missing ({_ranges(rest)}) — derived "
                                      f"({src}): recomputed for the days its inputs are caught up"))
            elif route == "unwired":
                why = next((w for nn, mm, w in excluded if (nn, mm) == (n, m)), "")
                lines.append(("info", f"CATCH-UP {n}/{m}: {len(rest)} day(s) missing ({_ranges(rest)}) — no past-day "
                                      f"read is wired for {src}" + (f" ({why})" if why else "")
                                      + "; a named hole (never 0, never carried)"))
        if named > cap:
            lines.append(("info", f"CATCH-UP: {named - cap} more series with missing days that are derived or have no "
                                  f"past-day read — not listed one by one"))
        if by_src:
            lines.append(("info", by_source_line(by_src)))
        lo_s = f"{lo.date()}..{yesterday.date()}"
        n_ret = len(out["retired"])
        return (f"; HOLES in the {catch_up_window_days()} days {lo_s} (every daily series): {found} found over "
                f"{len(scan) - n_ret} series — {filled} filled, {left} left"
                + (f" ({to_tiers} to " + ("today's tiers" if tiers else "the next plain run's tiers") + ")"
                   if to_tiers else "")
                + f", {nr} not recoverable (no history)"
                + (f"; {retired_n} day(s) of {n_ret} RETIRED series not counted (no adapter reads them)"
                   if n_ret else ""))

    def finish(level: str, summary: str) -> dict:
        """The derivations for every day caught up (API or on-chain, this run or owed from an earlier one), then the one
        summary line, extended with the API part, the derived rows and the window's holes."""
        days: dict = {}
        for part in (api_res["filled"], out["filled"], near_res["filled"], replay_res["filled"], contract_res["filled"]):
            for (n, _m), ds in part.items():
                days.setdefault(n, set()).update(ds)
        der = derive_caught_up(store, projects, days, valuers=valuers) if derive else \
            {"lines": [], "written": {}, "warn": False}
        out["derived"] = der["written"]
        lines.extend(der["lines"])
        a = api_res
        if a["checked"]:
            summary += (f"; API: {len(a['gaps'])} of {len(a['checked'])} daily API series had missed days — "
                        f"{sum(map(len, a['filled'].values()))} day(s) filled, "
                        f"{sum(map(len, a['left'].values()))} left, {sum(map(len, a['holes'].values()))} hole(s) "
                        f"named (CoinGecko market_chart)" if a["gaps"] else
                        f"; API: {len(a['checked'])} daily API series checked — none has a missed day")
        if der["written"]:
            summary += (f"; derived: {sum(map(len, der['written'].values()))} row(s) stored over "
                        f"{len(der['written'])} series for the caught-up days")
        summary += tally()
        lines.append(("warning" if a["warn"] or der["warn"] else level, summary))
        return out

    res = {"written": {}, "flows": {}, "refused": {}, "failures": {}, "report": [], "read_days": 0, "served_by": {}}
    bf = None
    no_endpoint: dict = {}
    if gaps:
        need = {s["chain"] for (n, m) in gaps for _, s in serving_contracts(by_name[n], m)} - {"solana"}
        if solana is None:
            solana = solana_providers([by_name[n] for n in sorted({n for n, _ in gaps})],
                                      {(n, k) for (n, m) in gaps for k, s in serving_contracts(by_name[n], m)
                                       if s["kind"] == "spl_token_account"})
        # the time the API part left: one budget for the whole catch-up
        bf = ArchiveBackfill(store, [by_name[n] for n in sorted({n for n, _ in gaps})],
                             budget_s=max(0.0, budget_s - (time.monotonic() - t0)), reader=reader, solana=solana)
        if archive_chains is None:
            # THE BACKFILL'S RESOLVER: the endpoint that serves old state, per chain (hosts only in any line)
            archive_chains = set()
            for c in sorted(need):
                w3, host = (resolve or resolve_archive)(c)
                if w3 is not None:
                    if hasattr(bf.reader, "_w3"):
                        bf.reader._w3[c] = w3
                    bf.hosts[c] = host
                    archive_chains.add(c)
        only = {}
        for (n, m), days in gaps.items():
            miss = sorted({s["chain"] for _, s in serving_contracts(by_name[n], m)} - {"solana"} - set(archive_chains))
            if miss:
                no_endpoint[(n, m)] = miss
            else:
                only[(n, m)] = set(days)
        bf.only, bf.max_days = only, max_days
        if only:
            res = bf.run(set(archive_chains))
    out["result"] = res

    entries, n_fill, n_left, n_holes = {}, 0, 0, 0
    starts = series_starts()                  # a start found by this run ("not before") makes earlier days not owed
    lo = catch_up_bounds()[0]
    stats, owed_after = {}, {}
    for (n, m), days in sorted(gaps.items()):
        have = {d for d, _ in _series_rows(store, n, m)}
        s0 = (starts.get((n, m)) or {}).get("from")
        filled = [d for d in days if d in have]
        left = [d for d in days if d not in have and not (s0 and d < s0)]
        fail = (res["failures"].get((n, m)) or {})
        refused = res["refused"].get((n, m))
        holes = [d for d in left if d in set(fail.get("dates") or ())] or (left if refused else [])
        tries = {d: k for d, k in _ledger_tries(ledger.get(f"{n}|{m}"), lo).items() if d not in filled}
        for d in holes:
            tries[d] = int(tries.get(d, 0)) + 1
        given = [d for d in holes if tries[d] >= CATCH_UP_TRIES]
        if tries:
            # P4-Q6: a given-up day's tries are KEPT (inside the window), or the full-window scan reads it again
            entries[f"{n}|{m}"] = {"tries": tries}
        owed_after[(n, m)] = {d for d in left if d not in given}
        stats[(n, m)] = (filled, left, holes, tries, given, fail, refused)
    # THE FLOWS (P4-Q6): differenced only between readings with no day still owed between them — never a lump
    if bf is not None:
        res["flows"] = bf.derive_owed_flows(sorted(set(res["written"]) | set(gaps)), owed_after)
    for (n, m), (filled, left, holes, tries, given, fail, refused) in sorted(stats.items()):
        out["filled"][(n, m)], out["left"][(n, m)], out["holes"][(n, m)] = filled, left, holes
        n_fill, n_left, n_holes = n_fill + len(filled), n_left + len(left), n_holes + len(holes)
        via = ", ".join(sorted({(bf.hosts if bf else {}).get(s["chain"], s["chain"])
                                for _, s in serving_contracts(by_name[n], m)}))
        txt = (f"CATCH-UP {n}/{m}: {len(filled)} day(s) filled" + (f" ({_ranges(filled)})" if filled else "")
               + f", {len(left)} left" + (f" ({_ranges(left)})" if left else "")
               + f" — archive read at each day's first block via {via}")
        flow = config.cumulative_flow_for(n, m)
        if flow and res["flows"].get((n, flow)):
            txt += f"; {res['flows'][(n, flow)]} {flow} row(s) differenced from it"
        if (n, m) in no_endpoint:
            txt += f"; NOT READ: no endpoint serves archive state on {', '.join(no_endpoint[(n, m)])} — the next run tries again"
        if holes:
            why = redact(refused or fail.get("first") or "no row and no error from the read")[:200]
            txt += (f"; HOLE(S) {_ranges(holes)} — the archive could not read them: {why} (never 0, never carried; "
                    f"tried on {max(tries[d] for d in holes)} of {CATCH_UP_TRIES} runs)")
        if given:
            txt += f"; given up after {CATCH_UP_TRIES} runs: {_ranges(given)} — left to archive_backfill.py"
        rest = [d for d in left if d not in holes and (n, m) not in no_endpoint]
        if rest:
            txt += f"; not reached this run (budget): {_ranges(rest)} — the next run continues"
        lines.append(("warning" if holes or (n, m) in no_endpoint else "info", txt))
    for x in res["report"]:
        if x.startswith(("budget spent", "day budget spent", "rate-limited", "STRADDLED", "solana ")) \
                or ": not available before " in x:
            lines.append(("info", f"CATCH-UP: {x}"))
    other_routes()
    # THE LEDGER: this scope's on-chain and NEAR series rewritten, other scopes' entries kept
    mine = set(checked) | set(near_res["checked"])
    keep = {k: v for k, v in ledger.items() if tuple(k.split("|", 1)) not in mine}
    keep.update(_carried_tries(store, ledger, mine, {**entries, **near_res["entries"]}))
    keep.update(entries)
    keep.update(near_res["entries"])
    if keep or ledger:
        _write_json(CATCH_UP_LEDGER, keep)
    secs = round(time.monotonic() - t0)
    lo_s, y_s = (str(x.date()) for x in catch_up_bounds())
    if not gaps:
        head = (f"CATCH-UP: {len(checked)} daily on-chain series checked — none has a missed day in the "
                f"{catch_up_window_days()} days {lo_s}..{y_s}; nothing read" + (f" (not covered: {notcov})" if notcov else ""))
    else:
        head = (f"CATCH-UP: {len(gaps)} of {len(checked)} daily on-chain series had missed days — {n_fill} day(s) "
                f"filled, {n_left} left, {n_holes} hole(s) named; {res.get('read_days', 0)} day(s) read in {secs}s "
                f"(budget {budget_s / 60:g} min, {max_days} day(s) a run), newest first"
                + (f" (not covered: {notcov})" if notcov else ""))
    nr = near_res
    if nr["gaps"]:
        head += (f"; NEAR archive: {sum(map(len, nr['filled'].values()))} day(s) filled, "
                 f"{sum(map(len, nr['left'].values()))} left, {sum(map(len, nr['holes'].values()))} hole(s) named")
    rp = replay_res
    if rp["filled"]:
        head += (f"; replayed reads: {sum(map(len, rp['filled'].values()))} day(s) filled, "
                 f"{sum(map(len, rp['left'].values()))} left")
    cr = contract_res
    if cr["filled"] or cr["left"]:
        head += (f"; contract reads at past blocks: {sum(map(len, cr['filled'].values()))} day(s) filled, "
                 f"{sum(map(len, cr['left'].values()))} left")
    sr = supply_res
    if sr["filled"] or sr["left"]:
        head += (f"; CoinGecko supply / FDV rebuilt (P5-1C.A3): {sum(map(len, sr['filled'].values()))} day(s), "
                 f"{sum(len(v) for k, v in sr['left'].items() if k not in sr['unrebuildable'])} left, "
                 f"{len(sr['unrebuildable'])} series failed the declared check")
    warn = n_holes or no_endpoint or any(nr["holes"].values()) or any(rp["left"].values())
    return finish("warning" if warn else "info", head)


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
        atomic_write_text(self.file, json.dumps({"known": self.known, "pace": round(self.pace, 3)}, sort_keys=True))

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

    def rows(self, p: dict, day: pd.Timestamp, want: tuple = ("total_supply_protocol", "locked_tokens")) -> list[dict]:
        """total_supply_protocol from the header, and STAKED NEAR from `validators` at the day's EPOCH. The archival
        RPC refuses `validators` by block (VALIDATOR_INFO_UNAVAILABLE / UNKNOWN_EPOCH) but answers it by epoch_id
        (Jake's Phase 1 follow-up 2026-10-12, item 3: archival-rpc.mainnet.fastnear.com served 400+ epochs back) —
        current_validators[].stake summed, the same field and scaling as the live read (fetch/near.py), so the source
        is the live read's plus the epoch and the archive marker. A stake outside (0, total supply] is not written."""
        api = p.get("node_api") or p.get("near_validators") or {}
        exp = int(api.get("yocto_exponent", 24))
        hdr = self.at(day)
        supply = int(hdr["total_supply"]) / 10**exp
        out = []
        if "total_supply_protocol" in want:
            out.append({"date": day, "project": p["name"], "metric": "total_supply_protocol", "value": supply,
                        "source": config.mark_source("near_rpc:block.header.total_supply", "archive"), "tier": 2})
        if "locked_tokens" in want:
            try:
                v = self.rpc("validators", {"epoch_id": hdr["epoch_id"]})
            except RuntimeError as e:
                # the archival node keeps epoch info ~a year (UNKNOWN_EPOCH beyond: 2025-10-16 served, 2025-07-14 not,
                # read 2026-10-12): the day's supply is still written, its stake is not
                if "UNKNOWN_EPOCH" not in str(e):
                    raise
                v = None
            cur = (v or {}).get("current_validators") if isinstance(v, dict) else None
            if cur:
                stake = sum(int(x["stake"]) for x in cur) / 10**exp
                if 0 < stake <= supply:
                    out.append({"date": day, "project": p["name"], "metric": "locked_tokens", "value": stake,
                                "source": config.mark_source(
                                    f"near_rpc:validators[epoch {hdr['epoch_id'][:8]}…, start "
                                    f"{v.get('epoch_start_height')}, {len(cur)} validators]", "archive"),
                                "tier": 2})
        return out
