"""
fetch/pendle_maturity.py — tier 1: Pendle's TVL split by TIME TO EXPIRY of its markets, one row per bucket per UTC day
(G8, 2026-10-20). Pendle's own open API, no key (api-v2.pendle.finance, OpenAPI read 2026-10-20):

    GET /core/v2/markets/all?limit=100&skip=N              every market ever listed (830 on 2026-10-20, 97 unexpired
                                                           across 13 chains), expired ones included — paged by 100
    GET /core/v3/{chainId}/markets/{address}/historical-data?time_frame=day&fields=totalTvl&timestamp_start=…
                                                           {results: [{timestamp, totalTvl}]}, one point per UTC day,
                                                           ≤ 1440 points; an expired market serves up to the day
                                                           BEFORE its expiry (dUSDC, Arbitrum, expiry 2025-12-18: last
                                                           point 2025-12-17, read 2026-10-20)

WHAT IS SUMMED. `totalTvl` — "market total TVL (including floating PT that are not in the AMM)" (the OpenAPI field
table). NOT `tvl`, which is the pool's liquidity only: reUSD read totalTvl $268M against tvl $13M on 2026-10-20 — the
AMM is a small part of what a market holds, and the maturity profile is about all of it.

THE BUCKETS. On each day d (00:00 UTC), a market is LIVE from its first history point until its expiry (d < expiry).
Its days to expiry = ceil((expiry - d) / 1 day), so a market expiring at 00:00 the next day is 1 day out:
    tvl_maturity_le_30d_usd    1-30 days      tvl_maturity_91_180d_usd   91-180 days
    tvl_maturity_31_90d_usd    31-90 days     tvl_maturity_gt_180d_usd   > 180 days
    tvl_maturity_total_usd     the sum of the four — a check against Pendle's (and DefiLlama's) total
A market past its expiry is not counted at all: its leftover (unredeemed PT) is not "maturing" TVL, and the history
route serves nothing after expiry anyway. Pendle's headline TVL may still include it.

UNKNOWN IS NEVER 0 (the business.strict_sum rule, per market span): a day on which a LIVE market has no point is
REFUSED for every bucket — never summed as 0 (a missing $200M market would move a bucket by $200M). A market that was
read and served no point at all counts from the day after its listing (`timestamp`), so a listed market Pendle has no
history for refuses its days rather than vanishing. The refusal names the markets.

RESUMABLE (the Boros lesson, fetch/boros.py, 2026-10-14). A first read is every market alive in the window — a few
hundred history calls — so each market's points are kept in a JSON state file (spec state_path) with the day it is
read THROUGH, saved after EVERY market; a run stops itself at spec budget_s and the next run resumes. A day is stored
only once every market live that day has been read through it: a partial first run stores nothing for the days still
missing markets and says how many markets remain. After that, a daily run reads only the markets not yet read through
yesterday — the live ones, from their last read day (1 CU each). The market list itself is read in full every run
(9 calls, 18 CU): a market listed and expired between two runs must not drop out unseen.

THE API'S BUDGET (headers read 2026-10-20): x-ratelimit-limit 200 computing units a window, x-ratelimit-reset the
window's end in epoch SECONDS (09:04:08 -> 09:05:00, 09:19:31 -> 09:20:00: a one-minute window), weekly 200,000
(x-ratelimit-weekly-*, resetting Thursday 00:00 UTC). Each call's cost is x-computing-unit: markets/all 2,
historical-data ceil(points / 300) — ≤ 2 for a 400-day read. x-ratelimit-remaining counted down 2 a call through a
burst at 09:24 but read 199 on two calls 31 s apart at 09:19 (same window), so it is not trusted alone: the pacer
also counts the units this run spent in the window and takes the lower, and sleeps to the reset before a call that
would bring the window within HEADROOM_CU of the limit — never a 429 by design. Below the
weekly reserve (spec weekly_reserve_cu) it stops reading: other Pendle sources share the week. Complete UTC days only
(today's point is the running day). Keyless: only hosts are ever logged.
"""
from __future__ import annotations

import datetime as _dt
import functools
import json
import logging
import math
import time
from pathlib import Path

from .base import BudgetExhausted, Http, Progress, tidy, today
from .logcache import atomic_write_text

log = logging.getLogger("token_metrics.fetch.pendle_maturity")

SOURCE = "pendle_maturity"
TIER = 1
API = "https://api-v2.pendle.finance/core"
SRC = "pendle_api:markets.totalTvl by expiry"           # the stored source string — keep it stable
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE = ".cache/pendle_maturity/state.json"      # under .cache (gitignored), beside boros's state

BUCKETS = (("tvl_maturity_le_30d_usd", 1, 30),
           ("tvl_maturity_31_90d_usd", 31, 90),
           ("tvl_maturity_91_180d_usd", 91, 180),
           ("tvl_maturity_gt_180d_usd", 181, None))
TOTAL = "tvl_maturity_total_usd"
METRICS = tuple(b[0] for b in BUCKETS) + (TOTAL,)

PAGE = 100                    # markets/all caps limit at 100 (OpenAPI)
MAX_PAGES = 30                # 830 markets = 9 pages on 2026-10-20
MAX_POINTS = 1440             # historical-data returns at most 1440 points
OVERLAP_DAYS = 2              # an incremental read restates the last days read (the newest point can move)
HEADROOM_CU = 10              # never run a window down past this
CALL_COST_MARKETS = 2
MAX_CONSECUTIVE_FAILS = 5     # a host that fails this often in a row is left for the next run
DAY_S = 86_400


# ===================================================================================================== pure helpers
def _date(s) -> _dt.date:
    return _dt.date.fromisoformat(str(s)[:10])


@functools.lru_cache(maxsize=8192)
def _epoch(s: str) -> int:
    """Epoch seconds of an ISO timestamp ("2026-12-10T00:00:00.000Z"); a bare date is its 00:00 UTC."""
    t = _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return int(t.timestamp())


def _day_s(d: _dt.date) -> int:
    return int(_dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc).timestamp())


def days_to_expiry(expiry: str, day: _dt.date) -> int:
    """Whole days from day d (00:00 UTC) to the expiry, rounded up; ≤ 0 means expired on d."""
    return math.ceil((_epoch(expiry) - _day_s(day)) / DAY_S)


def bucket_of(dte: int) -> str | None:
    """The bucket metric for a number of days to expiry, None when expired (dte ≤ 0)."""
    for metric, lo, hi in BUCKETS:
        if dte >= lo and (hi is None or dte <= hi):
            return metric
    return None


def span_start(m: dict) -> _dt.date | None:
    """The first day a READ market counts: its first point; a read market with no point counts from the day after
    its listing (a listed market with no history refuses its days — it is not 0); None if never read."""
    if not m.get("through"):
        return None
    if m.get("first"):
        return _date(m["first"])
    return _date(m["created"]) + _dt.timedelta(days=1)


def split_day(markets: dict, day: _dt.date) -> tuple[str, object]:
    """One day's verdict over every market:
        ("ok", {metric: usd})       every market live that day served a point — the buckets and their total
        ("refused", [names])        a live market read through the day has NO point on it — unknown, never 0
        ("pending", [names])        a market that may be live that day is not read through it yet
    A refusal outranks pending: the day cannot be stored however the pending markets turn out."""
    sums = {b[0]: 0.0 for b in BUCKETS}
    refused, pending = [], []
    ds = str(day)
    for mid, m in markets.items():
        dte = days_to_expiry(m["expiry"], day)
        if dte <= 0:
            continue                                            # expired before the day: not counted
        label = f"{m.get('name') or '?'} ({mid})"
        start = span_start(m)
        if start is None:                                       # never read: blocks every day since its listing
            if _date(m["created"]) <= day:
                pending.append(label)
            continue
        if day < start:
            continue                                            # not live yet
        if ds > m["through"]:
            pending.append(label)
            continue
        v = (m.get("pts") or {}).get(ds)
        if v is None:
            refused.append(label)
            continue
        sums[bucket_of(dte)] += float(v)
    if refused:
        return "refused", refused
    if pending:
        return "pending", pending
    sums[TOTAL] = sum(sums[b[0]] for b in BUCKETS)
    return "ok", sums


def parse_history(body) -> dict | str:
    """{YYYY-MM-DD: totalTvl} from a historical-data response, or why it is refused. A null totalTvl is left out (a
    missing day, refused where the market is live — never 0); a value that is not a number refuses the market."""
    rows = body.get("results") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        keys = sorted(body)[:12] if isinstance(body, dict) else type(body).__name__
        return f"no `results` list in the response (keys {keys})"
    pts = {}
    for r in rows:
        if not isinstance(r, dict) or not r.get("timestamp"):
            return f"a point without a timestamp: {str(r)[:120]}"
        v = r.get("totalTvl")
        if v is None:
            continue
        try:
            pts[str(r["timestamp"])[:10]] = round(float(v), 2)
        except (TypeError, ValueError):
            return f"a totalTvl that is not a number: {v!r} on {r['timestamp']}"
    return pts


class CuPacer:
    """Pendle's computing-unit budget, from its response headers. after() reads each response's x-ratelimit-* and
    x-computing-unit; wait(cost) is the seconds to sleep before a call costing `cost`: 0 while the window has room,
    else to its reset (+1 s). Counts its own spend too, since x-ratelimit-remaining was seen not to move."""

    def __init__(self, limit: int = 200, headroom: int = HEADROOM_CU, weekly_reserve: int = 20_000,
                 wall=time.time):
        self.limit, self.headroom, self.weekly_reserve, self.wall = limit, headroom, weekly_reserve, wall
        self.reset: float | None = None
        self.spent = 0                 # units this run spent in the current window
        self.server_remaining: int | None = None
        self.weekly_remaining: int | None = None
        self.total_cu = 0              # units this run spent, all windows
        self.calls = 0
        self.costs: dict[int, int] = {}       # cost -> calls, for the log

    @staticmethod
    def _int(h: dict, k: str) -> int | None:
        try:
            return int(float(h[k]))
        except (KeyError, TypeError, ValueError):
            return None

    def after(self, headers: dict | None) -> None:
        h = {str(k).lower(): v for k, v in (headers or {}).items()}
        cost = self._int(h, "x-computing-unit")
        reset = self._int(h, "x-ratelimit-reset")
        if self._int(h, "x-ratelimit-limit"):
            self.limit = self._int(h, "x-ratelimit-limit")
        if reset is not None and reset != self.reset:
            self.reset, self.spent = float(reset), 0             # a new window
        self.calls += 1
        if cost is not None:
            self.spent += cost
            self.total_cu += cost
            self.costs[cost] = self.costs.get(cost, 0) + 1
        self.server_remaining = self._int(h, "x-ratelimit-remaining")
        wk = self._int(h, "x-ratelimit-weekly-remaining")
        if wk is not None:
            self.weekly_remaining = wk

    def remaining(self) -> int:
        """Units left in the window: the lower of the server's count and the limit less what this run spent."""
        now = self.wall()
        if self.reset is None or now >= self.reset:
            return self.limit                                    # the window has turned over
        own = self.limit - self.spent
        return own if self.server_remaining is None else min(own, self.server_remaining)

    def wait(self, cost: int) -> float:
        if self.remaining() - cost >= self.headroom:
            return 0.0
        return max(0.0, self.reset - self.wall()) + 1.0

    def weekly_low(self) -> bool:
        return self.weekly_remaining is not None and self.weekly_remaining < self.weekly_reserve

    def summary(self) -> str:
        per = ", ".join(f"{n}x{c} CU" for c, n in sorted(self.costs.items()))
        wk = f"; weekly remaining {self.weekly_remaining:,}" if self.weekly_remaining is not None else ""
        return f"{self.calls} call(s), {self.total_cu} CU ({per or 'no cost header'}){wk}"


# ===================================================================================================== the fetcher
class PendleMaturity:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, clock=time.monotonic, wall=time.time,
                 sleep=time.sleep, root: Path | None = None, **_ignored):
        self.http = http or Http(min_interval=0.2, retries=2, rate_limit_wait=61.0)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self.root = Path(root) if root else ROOT

    # ------------------------------------------------------------------ state
    def _path(self, spec: dict) -> Path:
        p = Path(spec.get("state_path") or DEFAULT_STATE)
        return p if p.is_absolute() else self.root / p

    @staticmethod
    def _load(path: Path) -> dict:
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
            return st if st.get("version") == 1 else {"version": 1, "markets": {}}
        except (OSError, ValueError):
            return {"version": 1, "markets": {}}

    @staticmethod
    def _save(path: Path, state: dict) -> None:
        atomic_write_text(path, json.dumps(state, separators=(",", ":")))

    # ------------------------------------------------------------------ paced calls
    def _get(self, pacer: CuPacer, url: str, params: dict, cost: int, deadline: float):
        """One paced call: sleeps to the window's reset if the call would run it down, but never past the deadline
        (BudgetExhausted instead — the caller resumes next run)."""
        w = pacer.wait(cost)
        if w > 0:
            if self.clock() + w >= deadline:
                raise BudgetExhausted(f"the CU window needs a {w:.0f}s wait, past the time budget")
            log.info("pendle_maturity: %d CU left in the window — sleeping %.0fs to its reset (api-v2.pendle.finance)",
                     pacer.remaining(), w)
            self.sleep(w)
        self.http.last_headers = {}
        try:
            return self.http.get(url, params=params, deadline=deadline)
        finally:
            pacer.after(getattr(self.http, "last_headers", None))

    def _markets(self, pacer: CuPacer, deadline: float) -> list[dict]:
        rows, seen, total = [], set(), None
        for page in range(MAX_PAGES):
            j = self._get(pacer, f"{API}/v2/markets/all", {"limit": PAGE, "skip": page * PAGE},
                          CALL_COST_MARKETS, deadline)
            total = int(j.get("total") or 0) if total is None else total
            got = j.get("results") or []
            for m in got:
                key = f"{m.get('chainId')}-{str(m.get('address') or '').lower()}"
                if key not in seen:
                    seen.add(key)
                    rows.append(m)
            if not got or (page + 1) * PAGE >= total:
                break
        else:
            raise RuntimeError(f"more than {MAX_PAGES} pages of markets — not read in full")
        # A MARKET MISSED BY PAGING IS A MARKET COUNTED AS 0: the list must hold every market it says it has
        if len(rows) < total:
            raise RuntimeError(f"markets/all says {total} markets, paging returned {len(rows)} distinct — the list "
                               f"moved while it was read; nothing new trusted, retried next run")
        return rows

    # ------------------------------------------------------------------ run
    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("pendle_maturity")
            if spec:
                self._project(p["name"], spec, out)

    def _project(self, name: str, spec: dict, out) -> None:
        day = str(today().date())
        key = f"pendle_maturity:{name}"
        if not self.daily.due(key, day):
            for m in METRICS:
                out.mark_current(SOURCE, name, m, f"{m}: every market read through yesterday already today ({day})",
                                 TIER)
            return
        hist_days = int(spec.get("history_days", 400))
        if hist_days + OVERLAP_DAYS + 2 > MAX_POINTS:
            out.fail(SOURCE, name, f"history_days {hist_days}: one read serves at most {MAX_POINTS} daily points — "
                                   f"NOTHING READ", TIER)
            return
        budget = float(spec.get("budget_s", 120))
        t0 = self.clock()
        deadline = t0 + budget
        last = today().date() - _dt.timedelta(days=1)          # the last complete UTC day
        first = today().date() - _dt.timedelta(days=hist_days)
        pacer = CuPacer(weekly_reserve=int(spec.get("weekly_reserve_cu", 20_000)), wall=self.wall)
        path = self._path(spec)
        state = self._load(path)
        mk = state.setdefault("markets", {})

        try:
            listed = self._markets(pacer, deadline)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run; the state is kept
            out.fail(SOURCE, name, f"tvl_maturity_*: the market list could not be read — {e}; NOTHING STORED, "
                                   f"resumes next run ({pacer.summary()})", TIER)
            return
        now_live = {"n": 0, "usd": 0.0}
        for m in listed:
            try:
                exp, created = str(m["expiry"]), str(m.get("timestamp") or m["expiry"])
                exp_s = _epoch(exp)
            except (KeyError, TypeError, ValueError):
                out.fail(SOURCE, name, f"tvl_maturity_*: a market without a readable expiry: {str(m)[:160]} — "
                                       f"NOTHING STORED (it cannot be bucketed, and leaving it out reads it as 0)",
                         TIER)
                return
            if exp_s > self.wall():                               # markets/all's own current figure, for the check
                now_live["n"] += 1
                now_live["usd"] += float((m.get("details") or {}).get("totalTvl") or 0)
            if exp_s <= _day_s(first):
                continue                                          # expired before the window: never live in it
            mid = f"{m['chainId']}-{str(m['address']).lower()}"
            rec = mk.setdefault(mid, {})
            rec.update({"name": m.get("name"), "chain": int(m["chainId"]), "address": str(m["address"]).lower(),
                        "expiry": exp, "created": created})
        # the window moves forward: drop markets that expired before it, and points before it
        for mid in [k for k, v in mk.items() if _epoch(v["expiry"]) <= _day_s(first)]:
            del mk[mid]
        fs = str(first)
        for v in mk.values():
            v["pts"] = {d: x for d, x in (v.get("pts") or {}).items() if d >= fs}
            if v.get("first") and v["first"] < fs:
                v["first"] = fs

        # WHAT TO READ: every market not read through min(yesterday, its last live day). Never-read markets first,
        # latest expiry first — so a cut first run completes the RECENT days first; then the stalest.
        todo = []
        for mid, rec in mk.items():
            end = min(last, _date(rec["expiry"]) - _dt.timedelta(days=0 if _epoch(rec["expiry"]) % DAY_S else 1))
            if rec.get("through") and rec["through"] >= str(end):
                continue
            todo.append((mid, end))
        todo.sort(key=lambda t: (mk[t[0]].get("through") or "", -_epoch(mk[t[0]]["expiry"])))
        read = failed = fails_row = 0
        cut = ""
        last_err = ""
        prog = Progress(f"pendle_maturity {name}", total=len(todo), unit="markets", every_units=50,
                        status=lambda: f"{read} read, {failed} failed; {pacer.summary()}")
        for mid, end in todo:
            rec = mk[mid]
            if pacer.weekly_low():
                cut = (f"weekly CU reserve reached ({pacer.weekly_remaining:,} left < "
                       f"{pacer.weekly_reserve:,} kept for other Pendle sources)")
                break
            if self.clock() >= deadline:
                cut = f"the {budget:.0f}s budget"
                break
            if fails_row >= MAX_CONSECUTIVE_FAILS:
                cut = f"{fails_row} failed reads in a row (last: {last_err})"
                break
            created = _date(rec["created"])
            if rec.get("through"):
                frm = max(first, _date(rec["through"]) - _dt.timedelta(days=OVERLAP_DAYS))
            else:
                frm = max(first, created)
            npts = (today().date() - frm).days + 1
            cost = max(1, math.ceil(npts / 300))
            try:
                body = self._get(pacer, f"{API}/v3/{rec['chain']}/markets/{rec['address']}/historical-data",
                                 {"time_frame": "day", "fields": "totalTvl",
                                  "timestamp_start": f"{frm}T00:00:00Z"}, cost, deadline)
            except BudgetExhausted as e:
                cut = f"the {budget:.0f}s budget ({e})"
                break
            except Exception as e:  # noqa: BLE001 — this market resumes next run; its days stay unstored
                failed += 1
                fails_row += 1
                last_err = f"{rec.get('name')} ({mid}): {type(e).__name__}: {str(e)[:120]}"
                log.warning("pendle_maturity %s: market %s not read — %s; resumes next run", name, mid, last_err)
                continue
            pts = parse_history(body)
            if isinstance(pts, str):
                failed += 1
                fails_row += 1
                last_err = f"{rec.get('name')} ({mid}): {pts}"
                continue
            fails_row = 0
            pts = {d: v for d, v in pts.items() if fs <= d <= str(end)}
            if not rec.get("first") and pts:                     # its first point is where its span starts — a
                rec["first"] = min(pts)                          # market listed with no point yet gains one later
            rec["pts"] = {**(rec.get("pts") or {}), **pts}
            rec["through"] = str(end)
            read += 1
            prog.tick()
            # CHECKPOINT PER MARKET: a run killed by its tier budget loses at most the market in flight
            self._save(path, state)
        self._save(path, state)
        if read:
            prog.flush(final=not cut)
        left = sum(1 for mid, end in todo if (mk[mid].get("through") or "") < str(end))
        never = sum(1 for v in mk.values() if not v.get("through"))
        run_note = (f"{read} market(s) read this run, {failed} failed, {left} still to read"
                    + (f" ({never} never read)" if never else "") + (f" — stopped by {cut}" if cut else "")
                    + f"; {pacer.summary()}; {self.clock() - t0:.0f}s")

        # ---------------------------------------------------------- the days
        ok, refused, pending = {}, {}, {}
        d = first
        while d <= last:
            verdict, val = split_day(mk, d)
            {"ok": ok, "refused": refused, "pending": pending}[verdict][d] = val
            d += _dt.timedelta(days=1)
        if not ok:
            why = (f"FIRST READ IN PROGRESS — {len(mk) - never} of {len(mk)} market(s) in the window read"
                   if never else "no day on which every live market served a point")
            msg = (f"tvl_maturity_*: {why}; nothing stored until every market live on a day is read through it "
                   f"({len(pending)} day(s) waiting on markets, {len(refused)} refused for a missing point"
                   + (f", e.g. {min(refused)}: {', '.join(refused[min(refused)][:3])}" if refused else "")
                   + f"). {run_note}")
            if refused and not pending:
                out.fail(SOURCE, name, msg, TIER)
            else:
                out.skipped(SOURCE, name, msg, TIER)
            return
        days = sorted(ok)
        newest = ok[days[-1]]
        split = ", ".join(f"{b[0].replace('tvl_maturity_', '').replace('_usd', '')} ${newest[b[0]] / 1e9:,.3f}B"
                          for b in BUCKETS)
        note = (f"tvl_maturity_*: latest {days[-1]}: {split}, total ${newest[TOTAL] / 1e9:,.3f}B (markets/all's "
                f"current totalTvl over the {now_live['n']} unexpired markets: ${now_live['usd'] / 1e9:,.3f}B). "
                f"{len(refused)} day(s) REFUSED for a live market's missing point"
                + (f" (latest {max(refused)}: {', '.join(refused[max(refused)][:3])})" if refused else "")
                + f", {len(pending)} waiting on markets not yet read. {run_note}")
        for m in METRICS:
            out.add(tidy([(_dt.datetime(x.year, x.month, x.day), ok[x][m]) for x in days], name, m, SRC, TIER),
                    SOURCE, name, f"{m}: {len(days)} day(s) {days[0]}..{days[-1]}" + (f" — {note}" if m == TOTAL
                                                                                      else ""), TIER)
        if days[-1] >= last:
            self.daily.done(key, day)
        elif refused and max(refused) == last:
            out.warn(SOURCE, name, f"tvl_maturity_*: yesterday ({last}) REFUSED — a live market served no point: "
                                   f"{', '.join(refused[last][:5])}", TIER)
