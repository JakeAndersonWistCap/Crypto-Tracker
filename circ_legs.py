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
    """PR4E.2 / PR4F.4 (Jake 2026-11-13: "list what makes up Morpho's Unlocks Q0"; "Unlocks Q0 must equal circ_legs"):
    the latest quarter's release, from the store, two ways that must meet.
      per leg   each stored stock leg's falls and rises (consecutive stored days at most 3 apart; the switch leg on the
                current wallet set only) and the modelled relock's fall;
      per day   the workbook's method: each day the legs' changes are summed — a day the set falls releases that fall,
                a day it rises releases 0 and the rise is a RETURN — then each day's claims out of a distributor inside
                the set are taken out (on that day or the next stored one, never below 0: they are (b), in Emissions).
    Unlocks Q0 = per-day release − claims taken. It sits below Σ leg falls − Σ claims by the rises netted on days the
    set fell and by any claim with no release left to take it from — both printed."""
    swc = config.circulating_swc(project)
    legs = list((config.CIRCULATING_ONCHAIN.get(project) or {}).get("subtract") or ())
    if swc.get("unlock_legs"):
        legs.append(swc["metric"])
    asof = conn.execute("SELECT max(substr(date,1,10)) FROM metrics WHERE project=? AND metric='total_supply'",
                        (project,)).fetchone()[0]
    if not asof:
        return [f"{project}: no total_supply stored"]
    end = pd.Timestamp(asof)
    start = end - pd.Timedelta(days=period)
    days = pd.date_range(start + pd.Timedelta(days=1), end)
    lines = [f"{project}: release legs over ({start.date()}, {end.date()}] — tokens"]
    net = pd.Series(0.0, index=days)
    falls_total = 0.0
    for m in legs:
        if m == "morpho_relocked_tokens":
            ch = pd.Series({d: config.relocked_on(str((d - pd.Timedelta(days=1)).date())) - config.relocked_on(str(d.date()))
                            for d in days})
            lines.append(f"  {m}: fall {ch.sum():,.2f} (MODELLED from the published schedule)")
            falls_total += float(ch.sum())
            net = net.add(ch, fill_value=0.0)
            continue
        rows = conn.execute("SELECT substr(date,1,10), value, source FROM metrics WHERE project=? AND metric=? "
                            "ORDER BY date", (project, m)).fetchall()
        if m == swc.get("metric") and swc.get("current_set_only"):
            rows = [r for r in rows if config.on_current_set(project, m, r[2])]
        s = pd.Series({pd.Timestamp(d): float(v) for d, v, _s in rows}, dtype=float).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        gap = s.index.to_series().diff().dt.days
        fall_by_day = (-s.diff())[(gap <= 3) & (s.index > start) & (s.index <= end)]
        fall, rise = float(fall_by_day[fall_by_day > 0].sum()), abs(float(fall_by_day[fall_by_day < 0].sum()))
        lines.append(f"  {m}: fall {fall:,.2f}; rise {rise:,.2f} (returned / refilled)")
        falls_total += fall
        net = net.add(fall_by_day, fill_value=0.0)
    released, returned = net.clip(lower=0.0), (-net).clip(lower=0.0)
    lines.append(f"  per day (the legs summed each day): released {released.sum():,.2f}; returned {returned.sum():,.2f}")
    claims_total = taken_total = 0.0
    rel = released.copy()
    less_legs = config.release_less(project)[0] or tuple(swc.get("release_less") or ())
    for m in less_legs:
        rows = conn.execute("SELECT substr(date,1,10), sum(value) FROM metrics WHERE project=? AND metric=? AND "
                            "substr(date,1,10)>? AND substr(date,1,10)<=? GROUP BY substr(date,1,10)",
                            (project, m, str(start.date()), str(end.date()))).fetchall()
        tot = taken = 0.0
        for d, v in rows:
            left = float(v or 0.0)
            tot += left
            for day in (pd.Timestamp(d), pd.Timestamp(d) + pd.Timedelta(days=1)):
                if left > 1e-9 and day in rel.index:
                    t = min(left, float(rel.loc[day]))
                    rel.loc[day] -= t
                    left -= t
                    taken += t
        lines.append(f"  less {m}: {tot:,.2f} claimed; {taken:,.2f} taken out of (e) ((b), in Emissions)")
        claims_total += tot
        taken_total += taken
    unlocks = float(released.sum()) - taken_total
    lines.append(f"  falls − claims = {falls_total - claims_total:,.2f} (per leg, gross)")
    lines.append(f"  Unlocks Q0 (per day: released − claims taken) = {unlocks:,.2f} — the workbook's figure")
    lines.append(f"  the difference {falls_total - claims_total - unlocks:,.2f} = rises netted on days the set fell "
                 f"{falls_total - float(released.sum()):,.2f} − claims with no release left to take {claims_total - taken_total:,.2f}")
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
