#!/usr/bin/env python3
"""diagnose_lock_vs_float.py — why does locked_tokens exceed circulating_supply?

    python diagnose_lock_vs_float.py                 # every project with the violation
    python diagnose_lock_vs_float.py Aerodrome       # one project
    python diagnose_lock_vs_float.py --db other.db

READ-ONLY, and it needs no network: the three figures that settle the question are already in
the store. It prints; it changes nothing.

THE QUESTION. locked_tokens (a chain read: AERO.balanceOf(escrow)) exceeds circulating_supply
(CoinGecko) by ~0.106%. Two explanations were proposed, and they make OPPOSITE predictions about
a third number nobody had looked at — total_supply.

  A. CIRCULATING EXCLUDES LOCKED. If CoinGecko's circulating is "supply minus what is locked",
     the two populations are DISJOINT, so together they should account for the whole token:
         circulating + locked  ~=  total_supply
  B. CIRCULATING INCLUDES LOCKED. Then circulating already contains the locked tokens and adding
     them again double-counts:
         circulating           ~=  total_supply  (minus whatever CoinGecko withholds:
                                                  team, treasury, unvested)
         circulating + locked  >>  total_supply

One test, two predictions that cannot both hold. Whichever sum lands near total_supply is the
methodology in use, and it is read off numbers already collected rather than argued about.

WHAT THE ARITHMETIC ALREADY SAYS, before running anything: the two figures differ by one part in
~940. If they measured DISJOINT populations there is no mechanism that would place them that
close together — a disjoint split can land anywhere. Near-equality that tight is the signature of
two measurements of nearly the SAME population differing by a small, specific increment. So B is
expected, and this script is what confirms or refutes it rather than leaving it as an argument.

IF B HOLDS, the remaining question is what that increment IS — a small population inside the
escrow that CoinGecko does not count as circulating. The likeliest candidate is protocol- or
team-held veNFT locks, which CoinGecko excludes from circulating supply by convention while a
balance read cannot distinguish them from anyone else's lock. That would make the violation a
real methodology mismatch, in a different place from where hypothesis A put it.
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

NEEDED = ("locked_tokens", "circulating_supply", "total_supply")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?", default=None)
    ap.add_argument("--db", default="metrics.db")
    args = ap.parse_args()

    path = pathlib.Path(args.db)
    if not path.exists():
        print(f"NO STORE AT {path.resolve()}. Run `python token_metrics.py` first.")
        return 2

    conn = sqlite3.connect(str(path))
    rows = conn.execute("""
        SELECT m.project, m.metric, m.value, m.date, m.source
          FROM metrics m
          JOIN (SELECT project, metric, MAX(date) AS d FROM metrics GROUP BY project, metric) t
            ON t.project = m.project AND t.metric = m.metric AND t.d = m.date
         WHERE m.metric IN (?, ?, ?)
    """, NEEDED).fetchall()
    conn.close()

    by = {}
    for p, me, v, d, s in rows:
        by.setdefault(p, {})[me] = (v, d, s)

    fixture = any(str(s).upper().startswith("FIXTURE")
                  for pm in by.values() for (_, _, s) in pm.values())
    if fixture:
        print("** WARNING: FIXTURE: sources present — this store is SYNTHETIC. A verdict here "
              "says nothing about live data. **\n")

    projects = [args.project] if args.project else sorted(by)
    examined = 0
    for name in projects:
        m = by.get(name) or {}
        if not all(k in m for k in NEEDED):
            if args.project:
                missing = [k for k in NEEDED if k not in m]
                print(f"{name}: cannot test — no stored {', '.join(missing)}. "
                      f"All three are needed and a missing one is not a pass.")
            continue
        locked, circ, total = (m["locked_tokens"][0], m["circulating_supply"][0], m["total_supply"][0])
        if not args.project and locked <= circ:
            continue                       # only the violating projects, unless one was named
        examined += 1

        print(f"=== {name} ===")
        for k in NEEDED:
            v, d, s = m[k]
            print(f"  {k:<20} {v:>20,.4f}   {str(d)[:10]}  {s}")
        excess = locked - circ
        print(f"\n  locked - circulating   {excess:>20,.4f}   "
              f"({excess / circ:.4%} of circulating)" if circ else "")

        a_sum = circ + locked
        print(f"\n  A. circulating + locked {a_sum:>19,.4f}   "
              f"vs total_supply {total:>18,.4f}   off by {abs(a_sum - total) / total:>8.2%}"
              if total else "")
        print(f"  B. circulating          {circ:>19,.4f}   "
              f"vs total_supply {total:>18,.4f}   off by {abs(circ - total) / total:>8.2%}"
              if total else "")

        if total:
            a_err, b_err = abs(a_sum - total) / total, abs(circ - total) / total
            print()
            if a_err < b_err / 2:
                print("  VERDICT: A — circulating EXCLUDES locked. The two are disjoint populations "
                      "and the comparison is not an identity for this project.")
            elif b_err < a_err / 2:
                print("  VERDICT: B — circulating INCLUDES locked. They measure nearly the same "
                      "population, so the excess is a small group inside the escrow that CoinGecko "
                      "does not count as circulating — protocol- or team-held locks being the "
                      "likeliest. A real mismatch, but NOT 'circulating excludes locked'.")
                print(f"           The unexplained slice is {excess:,.2f} tokens; CoinGecko withholds "
                      f"{total - circ:,.2f} from circulating in total, so the slice is "
                      f"{excess / (total - circ):.1%} of what it withholds."
                      if total > circ else "")
            else:
                print("  VERDICT: INCONCLUSIVE — neither sum is clearly closer to total_supply. "
                      "Do not pick one; report it as unresolved.")
        print()

    if not examined:
        print("No project has locked_tokens exceeding circulating_supply with all three figures "
              "stored. That is not the same as no violation existing — name a project explicitly "
              "to see which of the three figures it is missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
