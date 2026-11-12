"""circ_legs.py [PROJECT ...] — READ-ONLY: for each leg of a project's on-chain circulating figure (the total, every leg
it subtracts or adds, and a switch's legs), the days of the catch-up window the store holds, the days MISSING, and the
days held on ANOTHER wallet set (a switch leg counts only rows on the set the config reads now). Opens metrics.db
read-only; prints no key and no URL. PR4D.3 (Jake 2026-11-12: "one command that prints, per Morpho circulating leg, the
days missing on Jake's store").

    python circ_legs.py Morpho
"""
from __future__ import annotations

import sqlite3
import sys

import pandas as pd

import config
from fetch.archive import _ranges, catch_up_bounds


def legs(project: str) -> list[tuple[str, str]]:
    """[(metric, role)] — the legs of `project`'s on-chain circulating, in order."""
    spec = config.circulating_onchain(project) or {}
    out = [(spec.get("total", "total_supply"), "total")]
    out += [(m, "subtract") for m in spec.get("subtract") or ()]
    out += [(m, "add") for m in spec.get("add") or ()]
    swc = spec.get("subtract_when_complete") or {}
    if swc:
        out.append((swc["metric"], "switch (" + ("added" if swc.get("sign") == 1 else "subtracted") + " when held)"))
        out += [(m, "switch also needs") for m in swc.get("also_need") or ()]
    return out


def report(conn, project: str) -> list[str]:
    lo, yesterday = catch_up_bounds()
    swc = config.circulating_swc(project)
    lines = [f"{project}: window {lo.date()}..{yesterday.date()} (the catch-up window, which the switch needs)"]
    # the days the circulating series itself exists: every stored base leg (total, subtract, add) held that day
    base_days = None
    for metric, role in legs(project):
        if role.startswith("switch"):
            continue
        held = {d for (d,) in conn.execute("SELECT DISTINCT substr(date,1,10) FROM metrics WHERE project=? AND metric=? "
                                           "AND substr(date,1,10)>=? AND substr(date,1,10)<=?",
                                           (project, metric, str(lo.date()), str(yesterday.date())))}
        if held:
            base_days = held if base_days is None else base_days & held
    base_days = base_days or set()
    for metric, role in legs(project):
        stored = conn.execute("SELECT count(*) FROM metrics WHERE project=? AND metric=?", (project, metric)).fetchone()[0]
        if not stored and (config.METRICS.get(metric) or {}).get("view_only"):
            lines.append(f"  {metric} [{role}]: built at read time (no stored rows) — "
                         + ("MODELLED from the published schedule" if metric == "morpho_relocked_tokens" else "a view"))
            continue
        rows = conn.execute("SELECT substr(date,1,10), source FROM metrics WHERE project=? AND metric=? AND "
                            "substr(date,1,10)>=? AND substr(date,1,10)<=?",
                            (project, metric, str(lo.date()), str(yesterday.date()))).fetchall()
        check_set = role.startswith("switch") and swc.get("current_set_only")
        on, other = set(), set()
        for d, src in rows:
            (other if check_set and not config.on_current_set(project, metric, src) else on).add(d)
        other -= on
        first = conn.execute("SELECT min(substr(date,1,10)) FROM metrics WHERE project=? AND metric=?",
                             (project, metric)).fetchone()[0]
        if role.startswith("switch"):
            want = sorted(base_days)          # the switch needs exactly the days the circulating series holds
        else:
            start = lo if not first else max(lo, pd.Timestamp(first))
            want = [str(d.date()) for d in pd.date_range(start, yesterday)]
        missing = [d for d in want if d not in on and d not in other]
        lines.append(f"  {metric} [{role}]: {len(on)} of {len(want)} day(s) held"
                     + (f"; MISSING {len(missing)}: {_ranges(missing)[:300]}" if missing else "; none missing")
                     + (f"; ON ANOTHER WALLET SET {len(other)}: {_ranges(sorted(other))[:200]} (removed and re-read by "
                        "the next run, CLEAN-UP PR4D.3)" if other else ""))
    state = swc.get("state")
    lines.append("  switch: " + ("comes into force once every switch leg above has none missing and none on another set"
                                 if swc else "none declared") + (f" (last build: {state})" if state else ""))
    return lines


def main(argv=None) -> int:
    projects = (argv if argv is not None else sys.argv[1:]) or ["Morpho"]
    conn = sqlite3.connect("file:metrics.db?mode=ro", uri=True)
    try:
        for p in projects:
            print("\n".join(report(conn, p)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    import progress
    progress.line_buffered()
    sys.exit(main())
