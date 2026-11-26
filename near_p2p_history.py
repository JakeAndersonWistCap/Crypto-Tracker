#!/usr/bin/env python3
"""near_p2p_history.py - NEAR's P2P settlement history BEFORE the stored year, one calendar month a run-month (R18).

    python near_p2p_history.py plan     [--chunks-per-month 1]   the plan table from the state and config. No network.
    python near_p2p_history.py dry-run                           the NEXT chunk's BigQuery dry run: bytes only, free
    python near_p2p_history.py run                               the NEXT chunk - only under this month's dated approval
    python near_p2p_history.py gaps [--db metrics.db] [--dry-run]  R23, READ-ONLY: the missing P2P days since 2025-12-29,
                                                                 what they cost, the dry-run commands, settlement ranges

JAKE, 2026-11-24: "the archetype key and rank must not wait until late 2027 ... a backfill that runs in monthly chunks
from 2026-12-01 within the 900 GB budget (stage 2 never automatic)". A1's key is the 1-year change of the trailing-365-day
settlement volume per circulating token: the daily series must be hole-free for 730 days, so held from r0 it forms on
r0 + 729. The daily run stops at the trailing 365 days; each month of history read here moves r0 back a month.

NEVER AUTOMATIC. `run` is the only thing that bills, and it starts only when ALL hold: it was typed by name; today is
in or after 2026-12; config.NEAR_P2P_HISTORY_APPROVAL is approved True with approved_by, an approved_on date in THIS
calendar month (an approval covers its own month only, as R14 decided for stage 2) and max_gb; the chunk's estimate is
within max_gb; this month has not already taken the chunk(s) the approval covers; the daily run's own 365-day window has
no day left to read; the store holds NEAR's price and the DEX leg on every day of the chunk (else the chunk would bill
and form no settlement day). A refusal makes NO BigQuery call and says what to approve and how many GB it will scan.
Then the adapter (fetch/near_bigquery.NearBigQuery.history_chunk) dry-runs it and applies the standing rules: the
400 GB per-query cap, the month's 900 GB budget, the top-up reserve, maximum_bytes_billed - and max_gb.

RESUMABLE, NO DAY TWICE, NO PARTIAL DAY. The state is the days held (<logcache>/near-bq.json): the next chunk is always
the days just before the hole-free run that ends at the newest held day, back to the first of that calendar month
(a stub under 7 days is taken with the month before; never before the floor 2024-12-10, where the DEX leg starts). A
chunk naming a held day is refused; a chunk is kept only whole - every day with its native-NEAR row - or not at all.
Nothing here writes metrics.db: the next `python token_metrics.py --project Near` values and stores the days.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# ----- dates ----------------------------------------------------------------------------------------------------------
def _day(x) -> dt.date | None:
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    try:
        return dt.date.fromisoformat(str(x)[:10]) if x is not None and len(str(x)) >= 10 else None
    except ValueError:
        return None


def _month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def _next_month(d: dt.date) -> dt.date:
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def _days(d0: dt.date, d1: dt.date) -> list:
    return [d0 + dt.timedelta(days=i) for i in range((d1 - d0).days + 1)]


# ----- the planner (pure: no network, no state) -----------------------------------------------------------------------
def run_start(held) -> dt.date | None:
    """r0: the first day of the hole-free run of held days that ends at the newest held day. None when nothing is held."""
    days = sorted({_day(d) for d in held} - {None})
    if not days:
        return None
    r0 = days[-1]
    for prev in reversed(days[:-1]):
        if (r0 - prev).days != 1:
            break
        r0 = prev
    return r0


def key_forms_on(held, key_days: int = 729) -> dt.date | None:
    """The day A1's key first forms from a daily series held as `held`: r0 + 729 (the 365-day sum needs r0..r0+364, its
    1-year change the sum of a year earlier) - the same arithmetic as reader._settlement_forms_on."""
    r0 = run_start(held)
    return None if r0 is None else r0 + dt.timedelta(days=int(key_days))


def window_holes(held, today, window_days: int = 365) -> list:
    """Days the DAILY RUN still owes inside its own trailing window: [yesterday - 364, the newest held day] not held.
    Its backfill is automatic and comes first; history never takes a day the daily run would."""
    have = {_day(d) for d in held} - {None}
    now = _day(today)
    if not have or now is None:
        return []
    lo = now - dt.timedelta(days=int(window_days))
    return [d for d in _days(lo, max(have)) if d not in have] if lo <= max(have) else []


def chunk_before(r0: dt.date, floor: dt.date, min_days: int = 7, not_before: dt.date | None = None):
    """(d0, d1) the chunk that ends the day before r0: back to the first of that calendar month; a stub of fewer than
    `min_days` days is taken with the month before it; never before `floor`, never onto `not_before` or earlier (a day
    already held). None when r0 is at or before the floor - the history is complete."""
    end = r0 - dt.timedelta(days=1)
    if end < floor:
        return None
    start = _month_start(end)
    if (end - start).days + 1 < int(min_days):
        start = _month_start(start - dt.timedelta(days=1))
    start = max(start, floor)
    if not_before is not None:
        start = max(start, not_before + dt.timedelta(days=1))
    return (start, end) if start <= end else None


def next_chunk(held, floor, min_days: int = 7):
    """The next chunk from the days held: the days just before the stored run, never one already held."""
    r0 = run_start(held)
    if r0 is None:
        return None
    older = [d for d in ({_day(x) for x in held} - {None}) if d < r0]
    return chunk_before(r0, _day(floor), min_days, max(older) if older else None)


def est_gb(d0: dt.date, d1: dt.date, gb_per_day: float) -> float:
    return ((d1 - d0).days + 1) * float(gb_per_day)


def reserve_gb(today: dt.date, topup_gb_per_day: float, margin: float) -> float:
    """The month's top-up reserve as fetch/near_bigquery._reserve computes it: days left after today x GB a day x margin."""
    last = calendar.monthrange(today.year, today.month)[1]
    return (last - today.day) * float(topup_gb_per_day) * float(margin)


def refusal(explicit_command: bool, today, approval: dict, chunk, gb: float, done_this_month: int,
            cfg: dict) -> str | None:
    """None when the chunk may start, else why not (one line, with what to approve and the GB). `explicit_command` is
    True only for `python near_p2p_history.py run`; `gb` is the chunk's estimate (or its dry run); `done_this_month`
    the chunks this calendar month has already taken."""
    cmd = cfg["command"]
    now = _day(today)
    if chunk is None:
        return "nothing to read: the history is held back to the floor " + str(cfg["floor"])
    d0, d1 = chunk
    what = f"{d0}..{d1} ({(d1 - d0).days + 1} days, about {gb:,.1f} GB)"
    ask = (f"to approve: in config.NEAR_P2P_HISTORY_APPROVAL set approved True, approved_by, approved_on (a date in "
           f"{now:%Y-%m}) and max_gb (at least {int(gb) + 1}), then `{cmd}`") if now else ""
    if not explicit_command:
        return f"the chunk {what} starts only from its own command `{cmd}` — never from a scheduled or daily run"
    if now is None:
        return "today's date is not known"
    if f"{now:%Y-%m}" < str(cfg["first_run_month"]):
        return f"the first chunk runs in {cfg['first_run_month']}; today is {now} — the chunk {what} waits"
    a = approval or {}
    if a.get("approved") is not True:
        return f"NOT APPROVED — the chunk {what} waits for Jake's dated approval; {ask}"
    on = _day(a.get("approved_on"))
    if not str(a.get("approved_by") or "").strip() or on is None:
        return f"the approval is not complete (approved_by and approved_on, a YYYY-MM-DD date) — the chunk {what}; {ask}"
    if on > now:
        return f"the approval is dated {on}, after today ({now})"
    # R21b (Jake 2026-11-25): an approval may be given ahead for ONE named month (`for_month`, 'YYYY-MM', not before
    # the month it is dated in); without it, the approval covers the month it is dated in
    month = str(a.get("for_month") or f"{on:%Y-%m}")
    if month < f"{on:%Y-%m}":
        return f"the approval is dated {on} but names {month}, a month before it — not an approval for {now:%Y-%m}"
    if month != f"{now:%Y-%m}":
        return (f"the approval (dated {on}) covers {month} only, and today is {now} — the chunk {what} needs a "
                f"new dated approval; {ask}")
    cap = a.get("max_gb")
    if not isinstance(cap, (int, float)) or isinstance(cap, bool) or cap <= 0:
        return f"the approval carries no max_gb — the chunk {what} is not run without a byte condition; {ask}"
    if gb > float(cap):
        return (f"the chunk {what} is over the {float(cap):,.1f} GB the approval allows (max_gb) — a new decision "
                f"for Jake; {ask}")
    allowed = a.get("chunks", cfg.get("chunks_per_month", 1))
    if allowed is None:
        # R20 (Jake 2026-11-24): an approval that names no count covers the COMBINED SCHEDULE's count for its month
        # (config.BIGQUERY_SCHEDULE: 1, 2, 2, 3 ...); a month outside the schedule keeps chunks_per_month
        allowed = scheduled_chunks(f"{now:%Y-%m}", cfg)
    allowed = int(allowed) if isinstance(allowed, int) and not isinstance(allowed, bool) and allowed > 0 else 1
    if int(done_this_month) >= allowed:
        return (f"{now:%Y-%m} has already taken {int(done_this_month)} chunk(s) and its approval covers {allowed} — the "
                f"chunk {what} waits for next month's approval (or `chunks` raised by Jake)")
    return None


def scheduled_chunks(month: str, cfg: dict) -> int:
    """R20: the NEAR history chunks the combined schedule gives the run month 'YYYY-MM' (config.BIGQUERY_SCHEDULE),
    else cfg's chunks_per_month."""
    import config                                              # noqa: PLC0415
    n = (getattr(config, "BIGQUERY_SCHEDULE", {}).get("months") or {}).get(month, {}).get("near_chunks")
    return int(n) if isinstance(n, int) and n > 0 else int(cfg.get("chunks_per_month", 1))


def combined_plan(r0, cfg: dict, margin: float = 1.2, max_query_gb: float = 400.0) -> dict:
    """R20 (Jake 2026-11-24): THE COMBINED SCHEDULE of docs/r19_bigquery_plan.md as plan() computes it — the budget
    config.BIGQUERY_SCHEDULE declares (1,000 GB), each month's NEAR chunk count from the schedule, and Ethereum's
    scheduled history GB taken out of the month's room beside what the ledger already carries (cfg.other_gb)."""
    import config                                              # noqa: PLC0415
    sched = config.BIGQUERY_SCHEDULE
    other = dict(cfg.get("other_gb") or {})
    for m, row in sched["months"].items():
        other[m] = float(other.get(m, 0.0)) + float(row.get("eth_gb") or 0.0)
    by_month = {m: int(row["near_chunks"]) for m, row in sched["months"].items()}
    return plan(r0, {**cfg, "other_gb": other, "chunks_by_month": by_month}, float(sched["budget_gb"]), margin,
                max_query_gb)


def plan(r0, cfg: dict, budget_gb: float = 900.0, margin: float = 1.2, max_query_gb: float = 400.0,
         chunks_per_month: int | None = None, months_cap: int = 60) -> dict:
    """The schedule from a stored run that starts at r0: one row a run-month from cfg.first_run_month, the run on the 1st.
    A month takes up to `chunks_per_month` chunks while each fits what the month has left: budget - the top-up reserve -
    cfg.other_gb (what else the ledger is known to carry: every month, and named months) - the chunks already taken, and the per-query cap. It
    stops once the key has formed. Returns {"rows", "formed_on", "without", "total_gb"}; a row is {"run_month", "run_on",
    "chunks": [(d0, d1, days, gb)], "gb", "room_gb", "left_gb", "r0", "key_on"}."""
    r0 = _day(r0)
    floor, kd = _day(cfg["floor"]), int(cfg["key_days"])
    per = int(cfg["chunks_per_month"] if chunks_per_month is None else chunks_per_month)
    R = dt.date.fromisoformat(str(cfg["first_run_month"]) + "-01")
    without = r0 + dt.timedelta(days=kd)
    formed, rows = without, []
    for _ in range(int(months_cap)):
        if r0 + dt.timedelta(days=kd) <= R - dt.timedelta(days=1):
            break                                              # formed on the data the run of R already holds
        other = cfg.get("other_gb") or {}
        room = (float(budget_gb) - reserve_gb(R, cfg["topup_gb_per_day"], margin) - float(other.get("every_month", 0))
                - float(other.get(f"{R:%Y-%m}", 0)))
        took, spent = [], 0.0
        # R20: a month's count from the combined schedule (cfg.chunks_by_month) unless the caller fixed one
        per_m = per if chunks_per_month is not None else int((cfg.get("chunks_by_month") or {}).get(f"{R:%Y-%m}", per))
        while len(took) < per_m and r0 + dt.timedelta(days=kd) > R - dt.timedelta(days=1):
            c = chunk_before(r0, floor, int(cfg["min_chunk_days"]))
            if c is None:
                break
            gb = est_gb(c[0], c[1], cfg["est_gb_per_day"])
            if gb > room - spent or gb > float(max_query_gb):
                break
            took.append((c[0], c[1], (c[1] - c[0]).days + 1, gb))
            spent += gb
            r0 = c[0]
        key_on = max(r0 + dt.timedelta(days=kd), R - dt.timedelta(days=1))
        formed = min(formed, key_on)
        rows.append({"run_month": f"{R:%Y-%m}", "run_on": R, "chunks": took, "gb": spent, "room_gb": room,
                     "left_gb": room - spent, "r0": r0, "key_on": key_on})
        if not took and chunk_before(r0, floor, int(cfg["min_chunk_days"])) is None:
            break                                              # the floor is reached: nothing more can be read
        R = _next_month(R)
    while rows and not rows[-1]["chunks"]:
        rows.pop()                                             # trailing months that read nothing
    return {"rows": rows, "formed_on": formed, "without": without, "total_gb": sum(r["gb"] for r in rows)}


def plan_lines(p: dict) -> list:
    out = ["  run month   history fetched                 days     GB   month's room   left   stored run from   key forms"]
    for r in p["rows"]:
        span = ", ".join(f"{a}..{b}" for a, b, _n, _g in r["chunks"]) or "-"
        out.append(f"  {r['run_month']}     {span:<30} {sum(c[2] for c in r['chunks']):>4} {r['gb']:>7,.1f} "
                   f"{r['room_gb']:>10,.1f} {r['left_gb']:>8,.1f}   {r['r0']}        {r['key_on']}")
    out.append(f"  TOTAL {p['total_gb']:,.1f} GB over {sum(len(r['chunks']) for r in p['rows'])} chunk(s); the key forms "
               f"on {p['formed_on']} (without the backfill: {p['without']})")
    return out


# ----- the state and the store (read-only) ----------------------------------------------------------------------------
def _near():
    import config
    from fetch.near_bigquery import NearBigQuery
    p = config.PROJECT_BY_NAME["Near"]
    return config, NearBigQuery(), p, p["near_bigquery"]


def done_in_month(st: dict, today) -> int:
    m = f"{_day(today):%Y-%m}"
    # R22: a chunk the one-shot billed history read (bigquery_backfill.py) is not one of the month's approved chunks
    return sum(1 for r in ((st.get("history") or {}).get("runs") or []) if r.get("month") == m and not r.get("oneshot"))


def store_gaps(db: Path, d0: dt.date, d1: dt.date, metrics=("price_usd", "dex_volume_usd"), project: str = "Near") -> dict:
    """{metric: [days of the chunk the store holds no value on]} - read-only. A P2P day is valued at the store's
    same-day NEAR price and a settlement day forms only with the DEX leg, so a chunk over days without them would bill
    and form nothing."""
    want = _days(d0, d1)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = {}
        for m in metrics:
            have = {r[0] for r in con.execute(
                "SELECT substr(date,1,10) FROM metrics WHERE project=? AND metric=? AND value IS NOT NULL "
                "AND substr(date,1,10) BETWEEN ? AND ?", (project, m, str(d0), str(d1)))}
            miss = [d for d in want if str(d) not in have]
            if miss:
                out[m] = miss
        return out
    finally:
        con.close()


def _today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _status(cfg: dict, st: dict, today: dt.date) -> tuple:
    held = list((st.get("days") or {}))
    c = next_chunk(held, cfg["floor"], int(cfg["min_chunk_days"]))
    gb = est_gb(c[0], c[1], cfg["est_gb_per_day"]) if c else 0.0
    return held, c, gb


# ----- commands -------------------------------------------------------------------------------------------------------
def cmd_plan(per: int | None) -> int:
    config, nb, _p, spec = _near()
    cfg = config.NEAR_P2P_HISTORY
    st = nb._load()
    today = _today()
    held, c, gb = _status(cfg, st, today)
    r0 = run_start(held)
    print("NEAR P2P HISTORY — the plan (no network, nothing billed)")
    if r0 is None:
        r0 = dt.date(2025, 12, 1)
        print(f"  no P2P day is held in the state here: planned from {r0}, where the daily run of 2026-12-01 leaves it")
    else:
        print(f"  held: {len(held)} day(s); the hole-free run starts {r0}; the key forms on {key_forms_on(held)} as it stands")
    budget = int(spec["monthly_budget_bytes"]) / 1e9
    if not per:
        print(f"\n  THE COMBINED SCHEDULE (R20, config.BIGQUERY_SCHEDULE; budget "
              f"{config.BIGQUERY_SCHEDULE['budget_gb']:,.0f} GB, Ethereum's scheduled history taken out of each month):")
        for line in plan_lines(combined_plan(r0, cfg, float(spec["topup_reserve_margin"]),
                                             int(spec["max_bytes_per_query"]) / 1e9)):
            print(line)
    for n in ([per] if per else [1, 2, 3]):
        print(f"\n  {n} chunk(s) a month — {cfg['est_gb_per_day']} GB a day of history ({cfg['est_source']}); top-up "
              f"{cfg['topup_gb_per_day']} GB a day x {spec['topup_reserve_margin']}; budget {budget:,.0f} GB:")
        for line in plan_lines(plan(r0, cfg, budget, float(spec["topup_reserve_margin"]),
                                    int(spec["max_bytes_per_query"]) / 1e9, n)):
            print(line)
    if not held:
        print("\n  NEXT: the daily run's own P2P read comes first (`python token_metrics.py --project Near`)")
        return 0
    why = refusal(True, today, config.NEAR_P2P_HISTORY_APPROVAL, c, gb, done_in_month(st, today), cfg)
    print(f"\n  NEXT: {'the chunk ' + str(c[0]) + '..' + str(c[1]) + f' (about {gb:,.1f} GB) may start' if why is None else why}")
    return 0


def cmd_dry_run() -> int:
    config, nb, _p, spec = _near()
    cfg = config.NEAR_P2P_HISTORY
    st = nb._load()
    held, c, gb = _status(cfg, st, _today())
    if c is None:
        print("NOTHING TO DRY-RUN — no P2P day is held yet, or the history is held back to the floor " + cfg["floor"])
        return 0
    client, why = nb._client(spec)
    if client is None:
        print(f"NOT RUN — {why}")
        return 2
    from fetch.near_bigquery import _sql
    toks = (st.get("tokens") or {}).get("list") or list(spec["seed_tokens"])
    n = nb._dry(client, _sql(cfg["sql"]), {"d0": c[0], "d1": c[1], "tokens": toks}, spec)
    print(f"DRY RUN (free, nothing billed) p2p HISTORY {c[0]}..{c[1]}: {n / 1e9:,.2f} GB (the estimate was {gb:,.1f} GB)")
    print("  " + nb._quota_line(spec, st))
    print(f"  to run it: approve at least {int(n / 1e9) + 1} GB (config.NEAR_P2P_HISTORY_APPROVAL max_gb), then `{cfg['command']}`")
    return 0


def cmd_run(db: Path, explicit: bool = True) -> int:
    config, nb, p, spec = _near()
    cfg = config.NEAR_P2P_HISTORY
    st = nb._load()
    st.setdefault("ledger", {})
    st.setdefault("days", {})
    today = _today()
    held, c, gb = _status(cfg, st, today)
    if not held:
        print("NEAR P2P HISTORY NOT RUN — no P2P day is held in the state: the daily run's own read comes first "
              "(`python token_metrics.py --project Near`). Nothing billed.")
        return 0
    why = refusal(explicit, today, config.NEAR_P2P_HISTORY_APPROVAL, c, gb, done_in_month(st, today), cfg)
    if why is not None:
        print(f"NEAR P2P HISTORY NOT RUN — {why}. Nothing billed; no BigQuery call was made.")
        return 0
    holes = window_holes(held, today, int(spec["days"]))
    if holes:
        print(f"NEAR P2P HISTORY NOT RUN — the daily run still owes {len(holes)} day(s) inside its own 365-day window "
              f"({holes[0]}..{holes[-1]}); it reads them first (`python token_metrics.py --project Near`). Nothing billed.")
        return 0
    try:
        gaps = store_gaps(db, c[0], c[1])
    except sqlite3.Error as e:
        print(f"NEAR P2P HISTORY NOT RUN — the store {db} could not be read ({e}): the chunk's days cannot be checked "
              f"for NEAR's price and the DEX leg. Nothing billed.")
        return 0
    if gaps:
        print("NEAR P2P HISTORY NOT RUN — the store holds no "
              + " and no ".join(f"{m} on {len(v)} of the chunk's days ({v[0]}..{v[-1]})" for m, v in gaps.items())
              + f": the chunk {c[0]}..{c[1]} would bill about {gb:,.1f} GB and form no settlement day there. Run "
                "`python token_metrics.py --project Near` and look again. Nothing billed.")
        return 0
    a = config.NEAR_P2P_HISTORY_APPROVAL
    print(f"APPROVED by {a['approved_by']} on {a['approved_on']} up to {float(a['max_gb']):,.1f} GB a chunk "
          f"(config.NEAR_P2P_HISTORY_APPROVAL) and started by its command: p2p HISTORY {c[0]}..{c[1]}")
    client, why = nb._client(spec)
    if client is None:
        print(f"NOT RUN — {why}")
        return 2
    from fetch.base import FetchOutput
    out = FetchOutput()
    res = nb.history_chunk(client, spec, st, out, p["name"], c[0], c[1], int(float(a["max_gb"]) * 1e9))
    for e in out.log:
        print(f"  {e.status}: {e.message}")
    print("  " + nb._quota_line(spec, st))
    if res is None:
        return 1
    held2 = list(st["days"])
    print(f"KEPT {res['days']} day(s) {res['d0']}..{res['d1']}, {res['billed'] / 1e9:,.2f} GB billed. The stored run "
          f"now starts {run_start(held2)}; the key forms on {key_forms_on(held2, cfg['key_days'])} once "
          f"`python token_metrics.py --project Near` has valued and stored these days.")
    return 0


# ----- gaps (R23, read-only) ------------------------------------------------------------------------------------------
def _store_days(con, project: str, metric: str) -> dict:
    """{day: (value, source)} of the store's rows for project/metric, value not null. Read-only."""
    return {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT substr(date,1,10), value, source FROM metrics WHERE project=? AND metric=? AND value IS NOT NULL",
        (project, metric))}


def _ranges(days: list, max_len: int = 31) -> list:
    """Contiguous runs of `days` (sorted dates), split at max_len days: [(d0, d1)]."""
    out = []
    for d in sorted(days):
        if out and (d - out[-1][1]).days == 1 and (out[-1][1] - out[-1][0]).days + 1 < max_len:
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def bq_dry_run_line(project: str, d0, d1, tokens) -> str:
    """The `bq` command that dry-runs the daily P2P query over d0..d1 (free: --dry_run bills nothing). Run from the repo
    root; the SQL file is read on stdin. The project is the BigQuery project that runs the job, never a secret."""
    toks = ",".join(tokens)
    return (f"bq query --project_id={project} --use_legacy_sql=false --dry_run --parameter=d0:DATE:{d0} "
            f"--parameter=d1:DATE:{d1} --parameter='tokens:ARRAY<STRING>:{toks}' < sql/near/bigquery_p2p_daily.sql")


def gaps_report(db: Path, cfg: dict, spec: dict, state: dict | None, today: dt.date, project: str,
                gap_from: str | None = None) -> dict:
    """Everything `gaps` prints, as data (pure given the store and the state; no network). Keys: holes, ranges,
    not_in_state, in_state, gb, p2p, dex, settle_stored, derived, r0, before_r0, eth."""
    f0 = _day(gap_from or cfg.get("gap_from") or "2025-12-29")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        p2p = _store_days(con, "Near", "p2p_transfer_volume_usd")
        dex = _store_days(con, "Near", "dex_volume_usd")
        px = _store_days(con, "Near", "price_usd")
        nset = _store_days(con, "Near", "settlement_volume_usd")
        eth = _store_days(con, "Ethereum", "settlement_volume_usd")
    finally:
        con.close()
    P = {_day(k) for k in p2p} - {None}
    D = {_day(k) for k in dex} - {None}
    newest = max(P) if P else None
    holes = [d for d in _days(f0, newest) if d not in P] if newest and newest >= f0 else []
    held_state = {_day(k) for k in ((state or {}).get("days") or {})} - {None}
    not_in_state = [d for d in holes if d not in held_state] if state is not None else list(holes)
    in_state = [d for d in holes if d in held_state]
    derived = sorted(P & D)
    r0 = run_start(derived)
    before = None
    if r0 is not None:
        b = r0 - dt.timedelta(days=1)
        before = {"day": b, "p2p": b in P, "dex": b in D, "price_usd": str(b) in px}
    first = min(P) if P else None
    return {"from": f0, "newest": newest, "holes": holes,
            "lead": [d for d in holes if first and d < first], "inside": [d for d in holes if first and d > first],
            "first": first, "ranges": _ranges(not_in_state), "not_in_state": not_in_state,
            "in_state": in_state, "gb": len(not_in_state) * float(cfg["est_gb_per_day"]),
            "p2p": (min(P), max(P), len(P)) if P else None, "dex": (min(D), max(D), len(D)) if D else None,
            "settle_stored": (_day(min(nset)), _day(max(nset)), len(nset)) if nset else None,
            "derived": (derived[0], derived[-1], len(derived)) if derived else None, "r0": r0, "before_r0": before,
            "eth": (_day(min(eth)), _day(max(eth)), len(eth), eth[min(eth)][1]) if eth else None,
            "have_state": state is not None}


def cmd_gaps(db: Path, dry: bool = False) -> int:
    """R23: READ-ONLY. NEAR's missing P2P days since gap_from, what filling them costs, the exact dry-run commands,
    Ethereum's and NEAR's stored settlement ranges, and where NEAR's unbroken settlement run starts and why. No network
    unless --dry-run (then BigQuery DRY RUNS only: free, nothing billed)."""
    config, nb, p, spec = _near()
    cfg = config.NEAR_P2P_HISTORY
    today = _today()
    try:
        st = nb._load()
        st = st if (st.get("days") or st.get("ledger")) else None
    except Exception:                                          # noqa: BLE001 - the state is optional here
        st = None
    try:
        g = gaps_report(db, cfg, spec, st, today, spec["project"])
    except sqlite3.Error as e:
        print(f"NEAR GAPS NOT READ - the store {db} could not be opened read-only ({e}). Nothing changed.")
        return 2
    gb_day = float(cfg["est_gb_per_day"])
    print(f"NEAR P2P GAPS - read-only (store {db}; BigQuery not called{'' if not dry else ' except free dry runs'})")
    print(f"  (a) MISSING DAYS of Near p2p_transfer_volume_usd, {g['from']} .. newest stored day {g['newest']}:")
    if g["p2p"] is None:
        print("      the store holds no p2p_transfer_volume_usd for Near at all")
    elif not g["holes"]:
        print("      none - every day from " + str(g["from"]) + " to " + str(g["newest"]) + " is stored")
    else:
        print(f"      {len(g['holes'])} day(s): " + ", ".join(str(d) for d in g["holes"][:60])
              + (f" ... (+{len(g['holes']) - 60} more)" if len(g["holes"]) > 60 else ""))
        lead = (f"{len(g['lead'])} of these day(s) lie BEFORE it ({g['lead'][0]}..{g['lead'][-1]}: the series simply "
                f"starts later than {g['from']} - the newest-first backfill's frontier, not a hole inside a run) and "
                if g["lead"] else "none lies before it; ")
        print(f"      SEEN: the stored series' first day is {g['first']}; {lead}{len(g['inside'])} are holes INSIDE the run.")
        if g["have_state"]:
            print(f"      of them {len(g['not_in_state'])} were never read from BigQuery (cost GB) and "
                  f"{len(g['in_state'])} are held in the BigQuery state but not valued/stored (waiting on NEAR's "
                  f"price_usd: cost 0 GB, `python token_metrics.py --project Near` stores them)")
            un = (st or {}).get("unvalued") or {}
            for d in g["in_state"]:
                if str(d) in un:
                    print(f"        {d}: NOT valued by the last run - {un[str(d)]}")
        else:
            print("      (no BigQuery state file here: every missing day is counted as unread)")
    budget = int(spec["monthly_budget_bytes"]) / 1e9
    print(f"  (b) COST TO FILL JUST THESE DAYS: {len(g['not_in_state'])} unread day(s) x {gb_day} GB = {g['gb']:,.1f} GB "
          f"(ESTIMATE, Jake's dry run of 2026-11-17: 233.7 GB / 31 days; the ledger budget is {budget:,.0f} GB a month, one top-up day "
          f"{cfg['topup_gb_per_day']} GB). Billed equivalent at ${config.BIGQUERY_ONESHOT['price_usd_per_tib']}/TiB, "
          f"for comparison only: ${g['gb'] * 1e9 / 1024 ** 4 * float(config.BIGQUERY_ONESHOT['price_usd_per_tib']):,.2f} "
          f"(the free tier covers it).")
    print(f"      The daily run reads them itself (`python token_metrics.py --project Near`: up to "
          f"{cfg.get('hole_chunks_per_run', 3)} hole chunk(s) of <= {spec['chunk_days']} days a run, newest first, under "
          f"the 400 GB per-query cap, the month's budget and the top-up reserve).")
    if g["ranges"]:
        toks = list(((st or {}).get("tokens") or {}).get("list") or spec["seed_tokens"])
        print("      DRY-RUN COMMANDS (free; one per range the daily run would read; tokens = "
              + ("the census list in the state" if (st or {}).get("tokens") else "the seed tokens - the census list is not in this state") + "):")
        for d0, d1 in g["ranges"]:
            print(f"        # {d0}..{d1}: {(d1 - d0).days + 1} day(s), ~{((d1 - d0).days + 1) * gb_day:,.1f} GB (estimate)")
            print("        " + bq_dry_run_line(spec["project"], d0, d1, toks))
        print("      or with the repo's own adapter (same SQL, same guard): python near_p2p_history.py gaps --dry-run")
    if dry and g["ranges"]:
        client, why = nb._client(spec)
        if client is None:
            print(f"      --dry-run NOT RUN - {why}")
        else:
            from fetch.near_bigquery import _sql
            toks = list(((st or {}).get("tokens") or {}).get("list") or spec["seed_tokens"])
            tot = 0
            for d0, d1 in g["ranges"]:
                n = nb._dry(client, _sql(cfg["sql"]), {"d0": d0, "d1": d1, "tokens": toks}, spec)
                tot += n
                print(f"      DRY RUN {d0}..{d1}: {n / 1e9:,.2f} GB")
            print(f"      DRY RUN TOTAL {tot / 1e9:,.2f} GB (estimate was {g['gb']:,.1f} GB)")
    # (c) settlement ranges
    print("  (c) STORED SETTLEMENT RANGES (MIN, MAX, COUNT of settlement_volume_usd):")
    e = g["eth"]
    if e is None:
        print("      Ethereum: none stored")
    else:
        need = e[1] - dt.timedelta(days=int(cfg["key_days"]))
        ok = e[0] <= need
        print(f"      Ethereum: {e[0]} .. {e[1]}, {e[2]} day(s) (first day's source: {e[3]})")
        print(f"        A1's key needs 730 days to the newest day: first day needed {need}. "
              + ("The store already reaches back that far -> NO Ethereum fill is needed."
                 if ok else f"The store starts {(e[0] - need).days} day(s) later -> Ethereum would need a fill back to {need}."))
        print(f"        The repo records only that the Artemis CSV (config.ARTEMIS_SETTLEMENT) reaches back to 2025-08-26 "
              f"at least (check: 365-day sum to 2026-08-25) and that the fixture dump holds it from 2025-09-08 (a 400-day "
              f"cut); its first day is NOT recorded. Whether it covers 2024-11-25 is settled by the MIN above.")
    n = g["settle_stored"]
    print("      NEAR stored settlement_volume_usd: " + (f"{n[0]} .. {n[1]}, {n[2]} day(s)" if n else
                                                       "none stored (NEAR's settlement is derived at read time, DEX + P2P)"))
    for lab, k in (("NEAR p2p_transfer_volume_usd", "p2p"), ("NEAR dex_volume_usd", "dex"),
                   ("NEAR derived settlement (days with BOTH legs)", "derived")):
        v = g[k]
        print(f"      {lab}: " + (f"{v[0]} .. {v[1]}, {v[2]} day(s)" if v else "none"))
    r0, b = g["r0"], g["before_r0"]
    if r0 is not None:
        print(f"  THE BREAK: the unbroken run of NEAR's derived settlement starts {r0} (r0): 365-day sum forms "
              f"{r0 + dt.timedelta(days=364)}, key forms {r0 + dt.timedelta(days=int(cfg['key_days']))} (reader._settlement_forms_on).")
        print(f"      The day before ({b['day']}) holds: p2p {'yes' if b['p2p'] else 'NO'}, DEX {'yes' if b['dex'] else 'NO'}, "
              f"NEAR price_usd {'yes' if b['price_usd'] else 'NO'}  [SEEN on this store].")
        why = [m for m, k in (("p2p_transfer_volume_usd", "p2p"), ("dex_volume_usd", "dex")) if not b[k]]
        starts = g["first"] == r0 and "p2p_transfer_volume_usd" in why
        print("      -> " + (("the P2P series STARTS on r0 (nothing stored before it): the break is the backfill's "
                              "frontier. The daily backfill reads newest first, one <=31-day chunk a run, and holds a "
                              "chunk the month's room after the top-up reserve cannot take (INFERRED as the cause; "
                              "SEEN: the series' first stored day).") if starts else
                             ("the leg(s) missing on that day: " + ", ".join(why) + ". A hole in a leg, not a rule.")
                             if why else "both legs are held that day, so the run is not broken there: the break is "
                                          "elsewhere (see the derived range above)."))
    print("  RULES (from the code): settlement = DEX leg (DefiLlama, from 2024-12-10) + P2P leg (BigQuery), a day only where "
          "BOTH hold (build_workbook._rebuilt_settlement_view); a P2P day is stored only with NEAR's same-day price_usd "
          "(NearBigQuery._value); no break rule in reader._settlement_forms_on beyond a missing day.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("plan", "dry-run", "run", "gaps"))
    ap.add_argument("--chunks-per-month", type=int, default=None, help="plan: show this schedule only")
    ap.add_argument("--db", default=str(ROOT / "metrics.db"))
    ap.add_argument("--dry-run", action="store_true", dest="gaps_dry",
                    help="gaps: also BigQuery-dry-run each missing range (free; needs Jake's login)")
    a = ap.parse_args(argv)
    if a.mode == "gaps":
        return cmd_gaps(Path(a.db), a.gaps_dry)
    if a.mode == "plan":
        return cmd_plan(a.chunks_per_month)
    if a.mode == "dry-run":
        return cmd_dry_run()
    return cmd_run(Path(a.db), explicit=True)


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
