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

LOCK_METRICS = ("locked_tokens", "locked_tokens_underlying", "locked_tokens_principal")
NEEDED = LOCK_METRICS + ("circulating_supply", "total_supply")


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
    placeholders = ",".join("?" for _ in NEEDED)
    rows = conn.execute(f"""
        SELECT m.project, m.metric, m.value, m.date, m.source
          FROM metrics m
          JOIN (SELECT project, metric, MAX(date) AS d FROM metrics GROUP BY project, metric) t
            ON t.project = m.project AND t.metric = m.metric AND t.d = m.date
         WHERE m.metric IN ({placeholders})
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
    verdicts, examined = [], 0
    for name in projects:
        m = by.get(name) or {}
        locks = [k for k in LOCK_METRICS if k in m]
        if not locks or "circulating_supply" not in m or "total_supply" not in m:
            if args.project:
                missing = [k for k in ("circulating_supply", "total_supply") if k not in m]
                print(f"{name}: cannot test — no lock metric stored." if not locks else
                      f"{name}: cannot test — missing {', '.join(missing)}. A missing figure is "
                      f"not a pass.")
            continue

        circ, total = m["circulating_supply"][0], m["total_supply"][0]
        if not total:
            continue
        print(f"=== {name} ===")
        for k in ("circulating_supply", "total_supply", *locks):
            v, d, src = m[k]
            print(f"  {k:<26} {v:>20,.4f}   {str(d)[:10]}  {src}")

        # The discriminator uses the PRIMARY lock metric — the others are components of the same
        # locked population and would double-count if summed in.
        locked = m[locks[0]][0]
        a_err = abs((circ + locked) - total) / total
        b_err = abs(circ - total) / total
        print(f"\n  A. circulating + locked  {circ + locked:>19,.4f}  vs total  off by {a_err:>8.2%}")
        print(f"  B. circulating           {circ:>19,.4f}  vs total  off by {b_err:>8.2%}")

        if a_err < b_err / 2:
            verdict, convention = "A", "excludes_locked"
            print("\n  VERDICT A — circulating EXCLUDES locked. Locked and circulating are DISJOINT "
                  "halves of total supply, so a lock metric must be bounded by total_supply and "
                  "NEVER by circulating_supply.")
        elif b_err < a_err / 2:
            verdict, convention = "B", "includes_locked"
            print("\n  VERDICT B — circulating INCLUDES locked. The two overlap, so locked exceeding "
                  "circulating would be a genuine contradiction.")
        else:
            verdict, convention = "INCONCLUSIVE", None
            print("\n  VERDICT INCONCLUSIVE — neither sum is clearly closer to total_supply. Do not "
                  "pick one.")

        # ** THE NEAR-MISS. ** Under verdict A the withdrawn relation was wrong for this project
        # too — it simply never fired, because the lock rate happens to sit under 50%. That is a
        # coincidence of the current figures, not a property of the project, and it would have
        # started firing the day the lock rate crossed half. Worth knowing even though nothing
        # looks broken today.
        rate = locked / total
        if verdict == "A":
            if locked <= circ:
                print(f"  ** NEAR MISS ** lock rate {rate:.2%} of total supply. Under this convention "
                      f"the withdrawn relation was WRONG here as well, and stayed quiet only because "
                      f"the rate is below 50%. It would have started flagging on the day it crossed.")
            else:
                print(f"  Lock rate {rate:.2%} of total supply — above 50%, which is why this one was "
                      f"visibly flagging.")
        declared = None
        try:
            import config as _c
            declared = (_c.PROJECT_BY_NAME.get(name) or {}).get("circulating_supply_convention")
        except Exception:  # noqa: BLE001
            pass
        if declared and convention and declared != convention:
            print(f"  ** CONFIG DISAGREES ** config declares {declared!r}, the store says "
                  f"{convention!r}. One of them is wrong.")
        elif convention and not declared:
            print(f"  config has no circulating_supply_convention for {name} — record {convention!r}.")
        verdicts.append((name, verdict, convention, rate))
        print()
        examined += 1

    if verdicts:
        print("SUMMARY")
        print(f"  {'project':<14} {'verdict':<14} {'convention':<18} lock rate of total")
        for name, verdict, convention, rate in verdicts:
            print(f"  {name:<14} {verdict:<14} {str(convention):<18} {rate:>8.2%}")
        print()

    if not examined:
        print("No project could be tested — every one is missing a lock metric, circulating_supply "
              "or total_supply. That is NOT a clean result; it means the store lacks the figures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
