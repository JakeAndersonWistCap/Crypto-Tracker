#!/usr/bin/env python3
"""llama_probe.py — ask DefiLlama what a slug actually returns, before changing config.

WHY THIS EXISTS
---------------
Morpho's stored fees ran $500-650K/day to 2026-09-11 and then $21.64, $0, $2.13, $2.99.
Borrower interest did not stop; something about the SOURCE changed. The obvious remedy — swap
the slug for a different one — is exactly the move that must not be made on a guess: a slug is
the definition of what a column means, and changing it silently redefines a series that already
has a year of history under the old definition.

So this asks, and prints. It changes nothing.

WHAT IT SHOWS, AND WHY EACH PART MATTERS
----------------------------------------
  * parentProtocol / childProtocols — the restructure hypothesis. If a protocol has been split,
    the parent carries the aggregate and the child carries a slice, and a slug that used to mean
    one now means the other WITHOUT ANY ERROR ANYWHERE.
  * the daily series around the break — so "it fell" can be told apart from "it stopped", and
    the exact date is on the record rather than inferred.
  * methodology and its breakdown — DefiLlama states what it is counting. Reading it is how
    GEODNET turned out to be burn/0.8 and Chainlink's `chainlink` slug turned out to be the
    parent rather than a child.
  * the OTHER listings whose names contain the slug — the candidates, with their own totals, so
    a replacement is chosen against numbers rather than against a name that sounds right.

USAGE
-----
    python llama_probe.py morpho
    python llama_probe.py chainlink --days 45
    python llama_probe.py morpho --search        also list sibling listings and their totals

NOT REACHABLE FROM EVERY ENVIRONMENT. api.llama.fi is blocked from the sandbox this tool was
written in, which is precisely why it is a tool rather than a finding.
"""
from __future__ import annotations

import argparse
import json
import sys

API = "https://api.llama.fi"


def _get(path: str):
    import requests
    r = requests.get(f"{API}{path}", timeout=60,
                     headers={"User-Agent": "token-metrics/2.0 (research tool)"})
    r.raise_for_status()
    return r.json()


def probe(slug: str, days: int, search: bool) -> int:
    try:
        j = _get(f"/summary/fees/{slug}?dataType=dailyFees")
    except Exception as e:  # noqa: BLE001
        print(f"  /summary/fees/{slug} FAILED: {e}")
        print("  A 404 here is itself the finding: the slug no longer resolves, which is what a")
        print("  restructure looks like from outside.")
        return 1

    print(f"\n  === {slug} ===")
    for field in ("name", "parentProtocol", "category", "chains", "latestFetchIsOk"):
        if field in j:
            print(f"  {field:20} {j[field]}")
    kids = j.get("childProtocols") or []
    print(f"  childProtocols       {len(kids)}"
          + (f" — {', '.join(str(k) for k in kids[:8])}" if kids else ""))
    if j.get("parentProtocol"):
        print("  ** THIS SLUG IS A CHILD. ** Its figure is a slice of the parent's, and the")
        print("     parent is what defillama.com shows on the protocol page.")

    for key in ("total24h", "total7d", "total30d", "total1y"):
        if j.get(key) is not None:
            print(f"  {key:20} {j[key]:,.2f}")

    meth = j.get("methodology")
    if meth:
        print("  methodology:")
        for k, v in (meth.items() if isinstance(meth, dict) else [("", meth)]):
            print(f"    {k}: {v}")

    # THE DAILY SERIES, so a fall can be told from a stop and the date is on the record.
    chart = j.get("totalDataChart") or []
    if chart:
        print(f"\n  last {days} day(s) — the shape is the evidence:")
        import datetime as dt
        for ts, v in chart[-days:]:
            day = dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).date()
            print(f"    {day}  {float(v):>18,.2f}")

    if search:
        # THE CANDIDATES, WITH THEIR NUMBERS. A replacement slug picked by name is how the wrong
        # definition gets adopted; picked against totals, it is a decision.
        try:
            overview = _get("/overview/fees?excludeTotalDataChart=true"
                            "&excludeTotalDataChartBreakdown=true")
        except Exception as e:  # noqa: BLE001
            print(f"\n  sibling search FAILED: {e}")
            return 0
        stem = slug.split("-")[0].lower()
        hits = [p for p in (overview.get("protocols") or [])
                if stem in str(p.get("name", "")).lower() or stem in str(p.get("slug", "")).lower()]
        print(f"\n  {len(hits)} listing(s) whose name or slug contains {stem!r}:")
        for h in sorted(hits, key=lambda x: -(x.get("total30d") or 0)):
            print(f"    {str(h.get('slug') or h.get('name')):34} "
                  f"30d={float(h.get('total30d') or 0):>16,.0f}  "
                  f"parent={h.get('parentProtocol') or '-'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("slug")
    ap.add_argument("--days", type=int, default=30, help="how many daily points to print")
    ap.add_argument("--search", action="store_true",
                    help="also list sibling listings and their 30-day totals")
    ap.add_argument("--json", action="store_true", help="dump the raw response and stop")
    args = ap.parse_args(argv)
    if args.json:
        print(json.dumps(_get(f"/summary/fees/{args.slug}?dataType=dailyFees"), indent=2)[:20000])
        return 0
    return probe(args.slug, args.days, args.search)


if __name__ == "__main__":
    raise SystemExit(main())
