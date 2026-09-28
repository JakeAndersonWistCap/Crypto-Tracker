#!/usr/bin/env python3
"""history_audit.py — how much history each headline series holds, against the 90-day Q0. 2026-09-28.

Read-only. For every in-scope (project, metric) feeding a headline cell it prints the oldest and
newest stored date, the days stored, and what the backfill has learned about the source
(history-depth.json, written by fetch/backfill.py after each run that re-read the series):

    FULL        at least 90 days stored — Q0 is a whole window
    BACKFILLING short, and the next run re-reads the full year (the source has not yet been
                found to stop there)
    SOURCE-LIMITED  short because the source itself starts there: the last backfill found nothing
                older. Its Q0 is annualised over the days it covers (build_workbook._annualise).
    EMPTY       nothing stored

Usage:  python history_audit.py [--db metrics.db] [--short-only]
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
            verdict = ("FULL" if (today() - pd.Timestamp(first)).days >= Q0_DAYS else
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
    a = ap.parse_args(argv)
    table = rows(a.db, a.short_only)
    print(f"{'project':<13} {'metric':<24} {'oldest':<11} {'newest':<11} {'rows':>5} "
          f"{'days':>5}  {'source reaches (backfill memo)':<34} verdict")
    for r in table:
        print(f"{r[0]:<13} {r[1]:<24} {r[2]:<11} {r[3]:<11} {r[4]:>5} {r[5]:>5}  {r[6]:<34} {r[7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
