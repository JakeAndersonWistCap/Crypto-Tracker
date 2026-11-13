"""circ_legs.py [PROJECT ...] — READ-ONLY: for each leg of a project's on-chain circulating figure (the total, every leg
it subtracts or adds, and a switch's legs), the days of the catch-up window the store holds, the days MISSING, and the
days held on ANOTHER wallet set (a switch leg counts only rows on the set the config reads now). Opens metrics.db
read-only; prints no key and no URL. PR4D.3 (Jake 2026-11-12: "one command that prints, per Morpho circulating leg, the
days missing on Jake's store").

    python circ_legs.py Morpho
    python circ_legs.py --release Morpho      # PR4E.2: what makes up the latest quarter's Unlocks, leg by leg
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


def release_parts(conn, project: str, period: int = 90) -> list[str]:
    """PR4E.2 (Jake 2026-11-13: "list what makes up Morpho's Unlocks Q0"): per leg of the release, the latest quarter's
    falls and rises in its stored stock (consecutive stored days at most 3 apart, the switch leg on the current wallet
    set only), the modelled relock's fall, and the claims taken out of (e) (they are (b), counted in Emissions). The
    workbook nets each DAY before clipping, so its Unlocks Q0 can sit below Σ falls − claims by the same-day rises."""
    swc = config.circulating_swc(project)
    legs = [m for m in ((config.CIRCULATING_ONCHAIN.get(project) or {}).get("subtract") or ())]
    if swc.get("unlock_legs"):
        legs.append(swc["metric"])
    asof = conn.execute("SELECT max(substr(date,1,10)) FROM metrics WHERE project=? AND metric='total_supply'",
                        (project,)).fetchone()[0]
    if not asof:
        return [f"{project}: no total_supply stored"]
    end = pd.Timestamp(asof)
    start = end - pd.Timedelta(days=period)
    lines = [f"{project}: release legs over ({start.date()}, {end.date()}] — tokens"]
    tot = 0.0
    for m in legs:
        if m == "morpho_relocked_tokens":
            fall = config.relocked_on(str(start.date())) - config.relocked_on(str(end.date()))
            lines.append(f"  {m}: fall {fall:,.2f} (MODELLED from the published schedule)")
            tot += fall
            continue
        rows = conn.execute("SELECT substr(date,1,10), value, source FROM metrics WHERE project=? AND metric=? "
                            "ORDER BY date", (project, m)).fetchall()
        if m == swc.get("metric") and swc.get("current_set_only"):
            rows = [r for r in rows if config.on_current_set(project, m, r[2])]
        s = pd.Series({pd.Timestamp(d): float(v) for d, v, _s in rows}, dtype=float).sort_index()
        gap = s.index.to_series().diff().dt.days
        d = s.diff()[(gap <= 3) & (s.index > start) & (s.index <= end)]
        fall, rise = float(-d[d < 0].sum()), float(d[d > 0].sum())
        lines.append(f"  {m}: fall {fall:,.2f}; rise {rise:,.2f} (returned / refilled)")
        tot += fall
    less = 0.0
    for m in config.release_less(project)[0] or (swc.get("release_less") or ()):
        v = conn.execute("SELECT sum(value) FROM metrics WHERE project=? AND metric=? AND substr(date,1,10)>? AND "
                         "substr(date,1,10)<=?", (project, m, str(start.date()), str(end.date()))).fetchone()[0] or 0.0
        lines.append(f"  less {m}: {v:,.2f} (claims: (b), in Emissions — out of (e))")
        less += float(v)
    lines.append(f"  falls − claims = {tot - less:,.2f} (compare the workbook's Unlocks Q0)")
    return lines


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    release = "--release" in args
    projects = [a for a in args if a != "--release"] or ["Morpho"]
    conn = sqlite3.connect("file:metrics.db?mode=ro", uri=True)
    try:
        for p in projects:
            print("\n".join(release_parts(conn, p) if release else report(conn, p)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    import progress
    progress.line_buffered()
    sys.exit(main())
