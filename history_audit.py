#!/usr/bin/env python3
"""history_audit.py — how much history each headline series holds, against the 90-day Q0. 2026-09-28.

Read-only. For every in-scope (project, metric) feeding a headline cell it prints the oldest and
newest stored date, the days stored, and what the backfill has learned about the source
(history-depth.json, written by fetch/backfill.py after each run that re-read the series):

    FULL        at least 90 days stored — Q0 is a whole window. PRICES NEED 365 (Jake,
                2026-09-29): every USD valuation, Y1 included, prices each row on its own day.
    BACKFILLING short, and the next run re-reads the full year (the source has not yet been
                found to stop there)
    SOURCE-LIMITED  short because the source itself starts there: the last backfill found nothing
                older. Its Q0 is annualised over the days it covers (build_workbook._annualise).
    EMPTY       nothing stored

Usage:  python history_audit.py [--db metrics.db] [--short-only]
        python history_audit.py --forget price_usd [--yes]
            drop the backfill memo for a metric (all projects), so the next run re-asks the source
            for the year. Prints what it would drop; writes only with --yes. The memo is a
            disposable cache beside the log cache, never the store. Needed once after 2026-09-29:
            before then a run whose source FAILED was remembered as "the source has nothing older".
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

import pandas as pd

import config
from fetch import backfill as bf
from fetch.base import today

HEADLINE = ("fees_usd", "revenue_usd", "customer_revenue_usd", "holders_revenue_usd",
            "gross_burn_tokens", "actual_buyback_tokens", "actual_buyback_usd",
            "gross_issuance_tokens", "emissions_tokens", "pool_release_tokens", "price_usd")
Q0_DAYS = 90
PRICE_DAYS = 365          # prices: every row of every USD valuation is priced on its own day


def rows(db: str, short_only: bool) -> list[tuple]:
    conn = sqlite3.connect(db)
    span = {(p, m): (a, b, n) for p, m, a, b, n in conn.execute(
        "SELECT project, metric, MIN(date), MAX(date), COUNT(*) FROM metrics GROUP BY project, metric")}
    memo, out = bf._read(), []
    for p in config.PROJECTS:
        for m in HEADLINE:
            if m not in config.metrics_for_project(p):
                continue
            first, last, n = span.get((p["name"], m), (None, None, 0))
            if first is None:
                out.append((p["name"], m, "", "", 0, 0, "", "EMPTY"))
                continue
            days = (pd.Timestamp(last) - pd.Timestamp(first)).days + 1
            seen = memo.get(f"{p['name']}|{m}") or {}
            need = PRICE_DAYS if m in bf.PRICE_SERIES else Q0_DAYS
            verdict = ("FULL" if (today() - pd.Timestamp(first)).days >= need - bf.SLACK_DAYS else
                       "SOURCE-LIMITED" if seen.get("first") == first else "BACKFILLING")
            if short_only and verdict == "FULL":
                continue
            out.append((p["name"], m, first, last, n, days,
                        seen.get("first", "") + (f" (checked {seen['checked_on']})" if seen else ""),
                        verdict))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--db", default="metrics.db")
    ap.add_argument("--short-only", action="store_true")
    ap.add_argument("--forget", metavar="METRIC", help="drop the backfill memo for METRIC")
    ap.add_argument("--yes", action="store_true", help="with --forget: write the change")
    a = ap.parse_args(argv)
    if a.forget:
        memo = bf._read()
        drop = sorted(k for k in memo if k.split("|", 1)[-1] == a.forget)
        for k in drop:
            print(f"{'dropping' if a.yes else 'would drop'} {k}: {memo[k]}")
        if a.yes and drop:
            for k in drop:
                memo.pop(k)
            f = bf._depth_file()
            f.write_text(__import__("json").dumps(memo, sort_keys=True, indent=1))
        print(f"{len(drop)} memo entr{'y' if len(drop) == 1 else 'ies'} for {a.forget}"
              + ("" if a.yes else " — nothing written; add --yes"))
        return 0
    table = rows(a.db, a.short_only)
    print(f"{'project':<13} {'metric':<24} {'oldest':<11} {'newest':<11} {'rows':>5} "
          f"{'days':>5}  {'source reaches (backfill memo)':<34} verdict")
    for r in table:
        print(f"{r[0]:<13} {r[1]:<24} {r[2]:<11} {r[3]:<11} {r[4]:>5} {r[5]:>5}  {r[6]:<34} {r[7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
