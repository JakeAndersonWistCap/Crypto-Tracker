#!/usr/bin/env python3
"""rederive.py — rebuild a differenced flow series from the stock it was differenced FROM.

WHY THIS EXISTS INSTEAD OF A DELETE
-----------------------------------
Hyperliquid's gross_burn_tokens was written by four runs that landed on one date. Each run
differenced against the newest reading of ANY date — this morning's, after the first run — while
taking the date that guards the subtraction from the last reading BEFORE today. So each run wrote
only the increment since the previous run, and because `metrics` is keyed (date, project, metric)
each one overwrote the last. The day's figure came out roughly a third of the truth.

The obvious remedy was to delete the eight bad flow rows and let the next run difference across
the gap. That throws away the history: one figure spanning ten days replaces ten daily figures,
and the daily shape never comes back.

IT DOES NOT HAVE TO BE THROWN AWAY. The bug corrupted the FLOW rows. The STOCK rows —
burn_address_balance, one surviving reading per date — were never differenced and are sound.
A flow derived from a stock is redundant information: delta(d) = stock(d) - stock(previous
stored d). So the whole series can be rebuilt from readings that were never wrong, and the daily
shape is recovered rather than lost.

WHAT IT REFUSES TO DO
---------------------
Differencing is only a flow when both readings measured the same thing, and only a flow when the
quantity can move in the direction it moved. Three refusals, each reported per pair rather than
skipped:

  * THE MEASURING POINT CHANGED between two readings. This is Uniswap's Firepit: the burn address
    was re-pointed, the next run differenced 4,000 against 111,341,581, and reported 111,337,581
    UNI burned in a month against a real rate of ~120k a day. Same rule as
    fetch.base.derive_flow_from_cumulative, same helper, so the two cannot drift.
  * THE STOCK FELL. A cumulative that falls means the source rebased or the measuring point moved.
    A negative burn is not a burn.
  * FEWER THAN TWO READINGS. A difference needs an interval; with one reading there is no flow,
    and no zero either.

SELECT FIRST, ALWAYS
--------------------
Plain mode computes the plan and prints it — every existing row, every proposed row, the
difference between them, and every refusal. Nothing is written. Replacing takes --apply and a
typed confirmation, and happens in ONE transaction: the old differenced rows go and the new ones
land together, or neither does.

USAGE
-----
    python rederive.py Hyperliquid gross_burn_tokens
    python rederive.py Hyperliquid gross_burn_tokens --apply
    python rederive.py Hyperliquid gross_burn_tokens --db other.db
    python rederive.py Hyperliquid gross_burn_tokens --stock burn_address_balance
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys
from dataclasses import dataclass, field

import config
from fetch.base import _measuring_point

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_DB = HERE / "metrics.db"

# A rebuilt row says so in its source. The marker matters beyond provenance: every parser that
# resolves contract keys out of a source string filters the colon-delimited pieces against
# config.SOURCE_MARKERS and treats what is left as a contract key, and _measuring_point does the
# same. An unregistered piece would make a rebuilt row read as a DIFFERENT measuring point from
# the live one — so the next run would refuse to difference against it, reporting a change of
# address that never happened. See the SOURCE_MARKERS entry in config.py.
REDERIVED = "rederived"


@dataclass
class Pair:
    """One consecutive pair of stock readings, and what it yields."""
    date: str
    prior_date: str
    value: float | None
    prior_value: float
    stock_value: float
    source: str
    tier: int | None
    refusal: str | None = None


@dataclass
class Plan:
    project: str
    flow_metric: str
    stock_metric: str
    pairs: list[Pair] = field(default_factory=list)
    existing: dict[str, tuple[float, str]] = field(default_factory=dict)   # date -> (value, source)
    untouched: dict[str, tuple[float, str]] = field(default_factory=dict)  # non-differenced rows
    blocked: str | None = None

    @property
    def proposed(self) -> list[Pair]:
        return [p for p in self.pairs if p.refusal is None]

    @property
    def refusals(self) -> list[Pair]:
        return [p for p in self.pairs if p.refusal is not None]

    @property
    def new_total(self) -> float:
        return sum(p.value for p in self.proposed)

    @property
    def old_total(self) -> float:
        return sum(v for v, _ in self.existing.values())


def rederive_flow_from_stock(conn: sqlite3.Connection, project: str, flow_metric: str,
                             stock_metric: str | None = None) -> Plan:
    """Compute the flow series (project, flow_metric) implies from its stock. Reads only.

    stock_metric defaults to whatever config declares this flow was differenced from, so the
    caller cannot pair a flow with a cumulative it never came from. Pass it explicitly only to
    rebuild against a stock the config does not (yet) declare.
    """
    stock_metric = stock_metric or config.stock_for_flow(project, flow_metric)
    plan = Plan(project=project, flow_metric=flow_metric, stock_metric=stock_metric or "")
    if not stock_metric:
        plan.blocked = (f"config declares no cumulative that {project}/{flow_metric} is "
                        f"differenced from, so there is nothing to rebuild it from. If it is "
                        f"differenced from one, declare it in cumulative_flow; if it is not a "
                        f"difference at all, this tool does not apply to it.")
        return plan

    for date, value, source in conn.execute(
            "SELECT date, value, source FROM metrics WHERE project=? AND metric=? ORDER BY date",
            (project, flow_metric)):
        # Scoped to DIFFERENCED rows. A Dune backfill or a hand-entered period figure is a real
        # total, not a difference, and rebuilding it from a stock would silently replace evidence
        # with arithmetic. GEODNET's stitched series is exactly this shape.
        if ":delta" in str(source):
            plan.existing[date] = (value, source)
        else:
            plan.untouched[date] = (value, source)

    stock = conn.execute(
        "SELECT date, value, source, tier FROM metrics WHERE project=? AND metric=? ORDER BY date",
        (project, stock_metric)).fetchall()
    if len(stock) < 2:
        plan.blocked = (f"{project}/{stock_metric} holds {len(stock)} reading(s). A difference "
                        f"needs an interval, so fewer than two readings yields no flow — and no "
                        f"zero either.")
        return plan

    for (d0, v0, s0, _), (d1, v1, s1, t1) in zip(stock, stock[1:]):
        pair = Pair(date=d1, prior_date=d0, value=None, prior_value=v0, stock_value=v1,
                    source=f"{s1}:delta:{REDERIVED}", tier=t1)
        if _measuring_point(s0) != _measuring_point(s1):
            pair.refusal = (f"the measuring point CHANGED — {d0} read {_measuring_point(s0)!r} "
                            f"and {d1} read {_measuring_point(s1)!r}. Differencing across that "
                            f"reports the change of address as though it were a flow")
        elif v1 < v0:
            pair.refusal = (f"the cumulative FELL, {v0:,.4f} -> {v1:,.4f} ({v1 - v0:,.4f}). A "
                            f"flow derived from it would be negative, which this metric cannot "
                            f"be, so none is proposed")
        else:
            pair.value = v1 - v0
        plan.pairs.append(pair)
    return plan


def apply_plan(conn: sqlite3.Connection, plan: Plan) -> tuple[int, int]:
    """Replace the differenced rows with the rebuilt ones, in ONE transaction.

    Non-differenced rows are left exactly where they are — see the scoping in
    rederive_flow_from_stock. A partial application would leave the series half-rebuilt and
    telescoping against nothing, so both halves commit together or neither does.
    """
    if plan.blocked:
        raise ValueError(f"refusing to apply a blocked plan: {plan.blocked}")
    from store import utcnow
    now = utcnow()
    with conn:                                   # BEGIN ... COMMIT, ROLLBACK on any exception
        removed = conn.execute(
            "DELETE FROM metrics WHERE project=? AND metric=? AND source LIKE '%:delta%'",
            (plan.project, plan.flow_metric)).rowcount
        conn.executemany(
            """INSERT INTO metrics(date, project, metric, value, source, tier, fetched_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(date, project, metric) DO UPDATE SET
                 value=excluded.value, source=excluded.source, tier=excluded.tier,
                 fetched_at=excluded.fetched_at""",
            [(p.date, plan.project, plan.flow_metric, float(p.value), p.source, p.tier, now)
             for p in plan.proposed])
        written = len(plan.proposed)
    return removed, written


def runs_per_date(conn: sqlite3.Connection, project: str, metric: str) -> dict[str, int]:
    """How many runs wrote on each date of a series — from run_log, NOT from metrics.

    ** metrics CANNOT ANSWER THIS AND A QUERY THAT ASKS IT RETURNS NOTHING, FOR EVER. ** The
    table is keyed (date, project, metric), so a GROUP BY those three columns has exactly one row
    in every group and COUNT(DISTINCT fetched_at) is 1 by construction. The same-day re-run is
    invisible in `metrics` precisely because the upsert is what destroyed the evidence.

    run_log keeps one row per run per source per project, so the run count is recoverable there.
    Dates with no run_log rows are absent from the result rather than reported as zero: the log
    is per-run and does not reach back before the store had one.
    """
    rows = conn.execute(
        """SELECT substr(ts, 1, 10) AS day, COUNT(DISTINCT run_id) AS runs
             FROM run_log WHERE project=? GROUP BY day""", (project,)).fetchall()
    counts = {day: runs for day, runs in rows}
    dates = [d for (d,) in conn.execute(
        "SELECT date FROM metrics WHERE project=? AND metric=? ORDER BY date", (project, metric))]
    return {d: counts[d] for d in dates if d in counts}


def _print_plan(plan: Plan, conn: sqlite3.Connection) -> None:
    print(f"\n  {plan.project} / {plan.flow_metric}  <-  {plan.stock_metric}")
    if plan.blocked:
        print(f"  BLOCKED: {plan.blocked}\n")
        return

    runs = runs_per_date(conn, plan.project, plan.stock_metric)
    dates = sorted(set(plan.existing) | {p.date for p in plan.pairs})
    print(f"  {'date':<12} {'stored now':>18} {'rebuilt':>18} {'change':>18}  runs  note")
    for d in dates:
        old = plan.existing.get(d)
        pair = next((p for p in plan.pairs if p.date == d), None)
        new = pair.value if pair and pair.value is not None else None
        chg = (new - old[0]) if (new is not None and old) else None
        note = pair.refusal if (pair and pair.refusal) else ("NEW" if old is None else "")
        r = runs.get(d)
        print(f"  {d:<12} {('' if old is None else f'{old[0]:,.4f}'):>18} "
              f"{('' if new is None else f'{new:,.4f}'):>18} "
              f"{('' if chg is None else f'{chg:+,.4f}'):>18}  "
              f"{('?' if r is None else str(r)):>4}  {note}")

    print(f"\n  stored total  {plan.old_total:>20,.4f}   ({len(plan.existing)} differenced rows)")
    print(f"  rebuilt total {plan.new_total:>20,.4f}   ({len(plan.proposed)} rows)")
    # "difference", NOT "recovered": the rebuild can come out lower than what is stored, and on a
    # series where only one date was corrupted it usually will — the stored total over-counts by
    # whatever the double-differenced rows added. A label that presumes the direction would read
    # as a loss when it is a correction.
    print(f"  difference    {plan.new_total - plan.old_total:>20,.4f}")
    if plan.untouched:
        print(f"\n  LEFT ALONE — {len(plan.untouched)} row(s) that are not differences and are "
              f"not rebuilt from a stock:")
        for d, (v, s) in sorted(plan.untouched.items()):
            print(f"    {d}  {v:,.4f}  {s}")
    if plan.refusals:
        print(f"\n  {len(plan.refusals)} pair(s) yield NO row, each for a stated reason — listed "
              f"above, not skipped in silence.")
    multi = {d: n for d, n in runs.items() if n > 1}
    print(f"\n  RUNS PER DATE (from run_log): {len(runs)} date(s) with a log, "
          f"{len(multi)} with more than one run"
          + (f" — {', '.join(f'{d} x{n}' for d, n in sorted(multi.items()))}" if multi else ""))
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project")
    ap.add_argument("flow_metric")
    ap.add_argument("--stock", default=None,
                    help="the cumulative to rebuild from; defaults to what config declares")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true",
                    help="replace the stored differenced rows, after showing them and asking")
    args = ap.parse_args(argv)

    db = pathlib.Path(args.db)
    if not db.exists():
        print(f"no such database: {db}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(db)
    try:
        plan = rederive_flow_from_stock(conn, args.project, args.flow_metric, args.stock)
        _print_plan(plan, conn)
        if not args.apply:
            print("  LOOKED ONLY — nothing was written. Re-run with --apply to replace.\n")
            return 0
        if plan.blocked:
            print("  nothing to apply.\n")
            return 1
        typed = input(f"  type REPLACE to swap {len(plan.existing)} stored row(s) for "
                      f"{len(plan.proposed)} rebuilt one(s): ")
        if typed.strip() != "REPLACE":
            print("  not confirmed — nothing was written.\n")
            return 1
        removed, written = apply_plan(conn, plan)
        print(f"  replaced: {removed} row(s) deleted, {written} written, one transaction.\n")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
