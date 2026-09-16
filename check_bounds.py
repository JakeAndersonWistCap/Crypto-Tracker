#!/usr/bin/env python3
"""check_bounds.py — run every declared impossible-relation against the WHOLE store.

    python check_bounds.py                 # the 16 portfolio projects
    python check_bounds.py --all           # every project in config
    python check_bounds.py --db other.db   # a different store

WHY THIS EXISTS ALONGSIDE THE AUTOMATIC CHECK, which is the point worth reading.

check_impossible_relations() already runs on every run, inside fetch_all. But it is handed
`out.frame()` — THE ROWS FETCHED ON THAT RUN — so two figures are only ever compared when both
arrive in the same pass. A metric that came from a Dune backfill months ago, or from a tier that
skipped today, is simply absent from the comparison, and a contradiction involving it is
invisible. That is the same write-time blind spot that let Maple's 0.51 and Ether.fi's stale
locked_tokens survive their config fixes.

This script reads the STORE instead: every project, every metric, newest row of each, whenever it
was written. It imports the same IMPOSSIBLE_RELATIONS list and the same config.relation_exempt()
the automatic check uses, so the two cannot disagree about what a violation is — the only
difference is how much data each can see.

Read-only. It prints; it never writes to the store.
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import config  # noqa: E402
from fetch.validate import IMPOSSIBLE_RELATIONS  # noqa: E402

SIXTEEN = ("Ethereum", "World Mobile", "GEODNET", "Chainlink", "Maple", "Morpho", "Hyperliquid",
           "Uniswap", "Near", "Sky", "Pendle", "Aerodrome", "Ether.fi", "Aethir", "Plume", "Fluid")
EPSILON = 1e-9


def latest_rows(db: str) -> dict:
    """Newest row per (project, metric). The store is keyed (date, project, metric)."""
    conn = sqlite3.connect(db)
    rows = conn.execute("""
        SELECT m.project, m.metric, m.value, m.date, m.source
          FROM metrics m
          JOIN (SELECT project, metric, MAX(date) AS d
                  FROM metrics GROUP BY project, metric) t
            ON t.project = m.project AND t.metric = m.metric AND t.d = m.date
    """).fetchall()
    conn.close()
    return {(p, me): (v, d, s) for p, me, v, d, s in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="metrics.db")
    ap.add_argument("--all", action="store_true", help="every project, not just the 16")
    args = ap.parse_args()

    path = pathlib.Path(args.db)
    if not path.exists():
        print(f"NO STORE AT {path.resolve()}.\n"
              f"Run `python token_metrics.py` first, or pass --db with the right path.")
        return 2

    by_key = latest_rows(str(path))
    if not by_key:
        print(f"{path} has no rows — nothing to check. This is an EMPTY store, not a clean bill "
              f"of health.")
        return 2

    # SYNTHETIC-DATA GUARD. A clean report off fixture rows is worthless and looks identical to a
    # clean report off real ones, so say which this was.
    sources = {s for (_, _), (_, _, s) in by_key.items()}
    fixture = [s for s in sources if str(s).upper().startswith("FIXTURE")]
    projects = set(SIXTEEN) if not args.all else {p for p, _ in by_key}

    print(f"store          : {path.resolve()}")
    print(f"series         : {len(by_key)} (project, metric) pairs, newest row of each")
    print(f"projects scoped: {len(projects)}{'' if args.all else ' (the 16 portfolio assets)'}")
    print(f"relations      : {len(IMPOSSIBLE_RELATIONS)}")
    if fixture:
        print(f"** WARNING: {len(fixture)} distinct FIXTURE: sources present — this store is "
              f"SYNTHETIC, in whole or in part. A clean result here says nothing about live data. **")
    print()

    violations, exempted, skipped = [], [], 0
    for project in sorted(projects):
        for greater, lesser, why in IMPOSSIBLE_RELATIONS:
            reason = config.relation_exempt(project, greater, lesser)
            a, b = by_key.get((project, greater)), by_key.get((project, lesser))
            if reason:
                if a and b:
                    exempted.append((project, greater, lesser, a, b, reason))
                continue
            if a is None or b is None or not b[0]:
                skipped += 1
                continue
            if a[0] <= b[0] * (1 + EPSILON):
                continue
            violations.append((project, greater, lesser, a, b, why))

    if violations:
        print(f"VIOLATIONS: {len(violations)}\n")
        for project, greater, lesser, a, b, why in violations:
            ratio = a[0] / b[0] if b[0] else float("inf")
            print(f"  {project} — {greater} EXCEEDS {lesser} by {ratio:,.2f}x")
            print(f"      {greater:<26} {a[0]:>22,.4f}   {a[1][:10]}  {a[2]}")
            print(f"      {lesser:<26} {b[0]:>22,.4f}   {b[1][:10]}  {b[2]}")
            print(f"      why it is impossible: {why}\n")
    else:
        print("VIOLATIONS: none.")
        print(f"  {skipped} comparisons were SKIPPED because one side had no row in the store — "
              f"those are not passes, they are untested.\n")

    if exempted:
        print(f"EXEMPTED (declared in config, not failures): {len(exempted)}\n")
        for project, greater, lesser, a, b, reason in exempted:
            ratio = a[0] / b[0] if b[0] else float("inf")
            print(f"  {project} — {greater} vs {lesser}: {ratio:,.2f}x, exempt")
            print(f"      {reason[:150]}...\n")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
