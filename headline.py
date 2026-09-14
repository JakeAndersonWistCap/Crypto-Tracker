#!/usr/bin/env python3
"""
headline.py — the Master figures, printed from the store. No Excel, no LibreOffice.

    python headline.py

Every derived column in the workbook is a formula with no cached result, so without a
recalculation pass the .xlsx carries the arithmetic but not the answers — Excel computes them on
open, and nothing else can read them. This prints the same headline figures straight out of
metrics.db, with the confidence band beside each, so a number can be checked in a terminal.

It reads the store and computes the windows the same way the workbook does. It writes nothing.

    python headline.py --project Uniswap     one project in full
    python headline.py --band GREEN          only what is safe to act on
    python headline.py --metric gross_burn_tokens
"""
from __future__ import annotations

import argparse

import pandas as pd

import config
from build_workbook import aggregate
from store import Store

# The figures worth checking without opening the workbook: supply and demand, and the ratio
# between them that the whole tool exists to produce.
HEADLINE = ["circulating_supply", "total_supply", "gross_burn_tokens", "gross_issuance_tokens",
            "burn_revenue_funded", "treasury_holding_tokens", "burn_mint_ratio",
            "revenue_usd", "fees_usd", "locked_tokens", "price_usd"]

BAND_MARK = {"GREEN": "[ok   ]", "AMBER": "[check]", "RED": "[  -  ]"}


def fmt(value, unit: str) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a".rjust(18)
    if unit == "usd":
        return f"${value:,.0f}".rjust(18)
    if unit == "pct":
        return f"{value:.2%}".rjust(18)
    return f"{value:,.2f}".rjust(18)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="one project only")
    ap.add_argument("--metric", help="one metric only")
    ap.add_argument("--band", choices=["GREEN", "AMBER", "RED"], help="only this confidence band")
    ap.add_argument("--all-metrics", action="store_true", help="every metric, not just the headline set")
    args = ap.parse_args()

    st = Store()
    try:
        long = st.load_long()
        if long.empty:
            print("The store is empty. Run token_metrics.py first.")
            return
        asof = pd.Timestamp.now("UTC").tz_localize(None).normalize()
        data = aggregate(long, st.fetch_status(), asof,
                         gaps=st.gap_report(), review=st.review_queue())
    finally:
        st.close()

    wanted = set(HEADLINE) if not args.all_metrics else None
    rows = data.to_dict("records")
    if args.project:
        rows = [r for r in rows if r["project"].lower() == args.project.lower()]
    if args.metric:
        rows = [r for r in rows if r["metric"] == args.metric]
    if args.band:
        rows = [r for r in rows if r["confidence"] == args.band]
    if wanted:
        rows = [r for r in rows if r["metric"] in wanted]
    rows = [r for r in rows if r["confidence"] != "RED" or args.band == "RED"]

    print(f"as of {asof:%Y-%m-%d}   [ok] usable · [check] qualified, read the reason · [-] not a number\n")
    tally = {"GREEN": 0, "AMBER": 0, "RED": 0}
    current = None
    for r in sorted(rows, key=lambda x: (x["project"], HEADLINE.index(x["metric"])
                                         if x["metric"] in HEADLINE else 99)):
        tally[r["confidence"]] = tally.get(r["confidence"], 0) + 1
        if r["project"] != current:
            current = r["project"]
            print(f"\n{current}")
        mark = BAND_MARK.get(r["confidence"], "     ")
        print(f"  {mark} {r['label'][:42]:44}{fmt(r['now'], r['unit'])}   {r['n_points']:>4} pts  {r['source'][:30]}")
        if r["confidence"] == "AMBER" and r["why_amber"]:
            print(f"          why: {r['why_amber'][:150]}")
    print(f"\n{sum(tally.values())} figures shown — "
          + " · ".join(f"{b} {n}" for b, n in tally.items() if n))
    print("Full detail is in the workbook; this is the same aggregation, printed.")


if __name__ == "__main__":
    main()
