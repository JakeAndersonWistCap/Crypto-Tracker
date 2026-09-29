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


def why_short(p: dict, m: str, first: str | None) -> str:
    """For a short series: what fills it, or the specific reason nothing can (2026-09-29)."""
    from fetch import archive as ar
    fwd = config.HISTORY_FORWARD_ONLY.get(p["name"]) or {}
    if m in fwd.get("metrics", ()):
        if not first:
            return f"FORWARD-ONLY: {fwd['why']}"
        f0 = pd.Timestamp(first)
        return (f"FORWARD-ONLY — reaches 90 days {(f0 + pd.Timedelta(days=89)).date()}, 365 days "
                f"{(f0 + pd.Timedelta(days=364)).date()}: {fwd['why']}")
    if p.get("near_validators") and m in ("total_supply_protocol", (p["near_validators"] or {}).get("metric")):
        return "archive_backfill.py --run --near fills it (NEAR archival RPC: header total_supply, validators)"
    chains = {"ethereum", "polygon", "arbitrum", "base", "bsc"}
    stocks = [s for s in ar.state_metrics(p) if s == m or config.cumulative_flow_for(p["name"], s) == m]
    for s in stocks:
        ok, why = ar.archivable(p, s, chains, {(p["name"], k) for k, c in (p.get("contracts") or {}).items()
                                                if c["kind"] == "spl_token_account"})
        via = "" if s == m else f" (differenced from {s})"
        return (f"archive_backfill.py --run fills it{via}" if ok else f"not archivable{via}: {why}")
    return ""


def rows(db: str, short_only: bool) -> list[tuple]:
    from fetch import archive as ar
    conn = sqlite3.connect(db)
    span = {(p, m): (a, b, n) for p, m, a, b, n in conn.execute(
        "SELECT project, metric, MIN(date), MAX(date), COUNT(*) FROM metrics GROUP BY project, metric")}
    memo, out = bf._read(), []
    for p in config.PROJECTS:
        # STATE SERIES TOO (2026-09-29): balances, supplies and locks backfilled from archive.
        for m in list(HEADLINE) + sorted(ar.state_metrics(p) - set(HEADLINE)):
            if m not in config.metrics_for_project(p):
                continue
            first, last, n = span.get((p["name"], m), (None, None, 0))
            if first is None:
                out.append((p["name"], m, "", "", 0, 0, "", "EMPTY", why_short(p, m, None)))
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
                        verdict, "" if verdict == "FULL" else why_short(p, m, first)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--db", default="metrics.db")
    ap.add_argument("--short-only", action="store_true")
    ap.add_argument("--forget", metavar="METRIC", help="drop the backfill memo for METRIC")
    ap.add_argument("--cadence", nargs=2, metavar=("PROJECT", "METRIC"),
                    help="how often a stored daily series actually changes (e.g. Ethereum "
                         "circulating_supply_implied)")
    ap.add_argument("--yes", action="store_true", help="with --forget: write the change")
    a = ap.parse_args(argv)
    if a.cadence:
        from fetch import supply_cadence
        proj, met = a.cadence
        conn = sqlite3.connect(a.db)
        df = pd.read_sql_query("SELECT date, value FROM metrics WHERE project=? AND metric=? ORDER BY date",
                               conn, params=(proj, met))
        if df.empty:
            print(f"{proj}/{met}: nothing stored")
            return 0
        s = pd.Series(df["value"].values, index=pd.to_datetime(df["date"]))
        c = supply_cadence(s)
        print(f"{proj}/{met}: {c['days']} daily value(s) {s.index.min().date()}..{s.index.max().date()}, "
              f"{c['distinct']} distinct, {len(c['updates'])} update(s); gap between updates median "
              f"{c['median_gap']} day(s), max {c['max_gap']}")
        print("  update days (last 20): " + ", ".join(str(d.date()) for d in c["updates"][-20:]))
        return 0
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
          f"{'days':>5}  {'source reaches (backfill memo)':<34} verdict / what fills it")
    for r in table:
        print(f"{r[0]:<13} {r[1]:<24} {r[2]:<11} {r[3]:<11} {r[4]:>5} {r[5]:>5}  {r[6]:<34} {r[7]}"
              + (f" — {r[8]}" if r[8] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
