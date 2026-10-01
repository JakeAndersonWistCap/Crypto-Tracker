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
    COMPLETE    short because the SERIES starts there: the contract was not deployed before
                (recorded by archive_backfill.py) or the series is declared to begin then
                (config.ARCHIVE_SERIES_START). Nothing earlier exists to fill.
    EMPTY       nothing stored
    FULL AS READ  short in the store, full as the workbook reads it — a declared read-time history
                (Hyperliquid's buyback and burn: DefiLlama holders revenue / same-day price)
    BY DESIGN   short and meant to be: config.ARCHIVE_NOT_BY_DESIGN (total_supply is CoinGecko's figure,
                read "now" only — FDV and every supply denominator; chain history is total_supply_gross)
    DECIDED     short or empty in the store, but completeness_report.py classifies it as settled —
                COMPLETE (a declared zero, a read-time view such as Pendle's buyback tokens or
                Hyperliquid's DefiLlama history, a mechanism start), N/A, ACCEPTED LIMIT, or WAITING
                ON A DATE. The audit reads the SAME classifications (Jake, 2026-10-01), on the series
                as the workbook reads it, so --short-only lists only real gaps.

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
    if m in config.ARCHIVE_NOT_BY_DESIGN:
        return config.ARCHIVE_NOT_BY_DESIGN[m]
    fwd = config.HISTORY_FORWARD_ONLY.get(p["name"]) or {}
    if m in fwd.get("metrics", ()):
        if not first:
            return f"FORWARD-ONLY: {fwd['why']}"
        f0 = pd.Timestamp(first)
        return (f"FORWARD-ONLY — reaches 90 days {(f0 + pd.Timedelta(days=89)).date()}, 365 days "
                f"{(f0 + pd.Timedelta(days=364)).date()}: {fwd['why']}")
    if p.get("near_validators") and m in ("total_supply_protocol", (p["near_validators"] or {}).get("metric")):
        return "archive_backfill.py --run --near fills it (NEAR archival RPC: header total_supply)"
    chains = {"ethereum", "polygon", "arbitrum", "base", "bsc"}
    stocks = [s for s in ar.state_metrics(p) if s == m or config.cumulative_flow_for(p["name"], s) == m]
    for s in stocks:
        ok, why = ar.archivable(p, s, chains, {(p["name"], k) for k, c in (p.get("contracts") or {}).items()
                                                if c["kind"] == "spl_token_account"})
        via = "" if s == m else f" (differenced from {s})"
        return (f"archive_backfill.py --run fills it{via}" if ok else f"not archivable{via}: {why}")
    return ""


def series_start(p: dict, m: str) -> dict | None:
    """The declared or recorded start of (project, metric), or of the stock it is differenced
    from — whose flow begins the day after (2026-09-29)."""
    from fetch import archive as ar
    starts = ar.series_starts()
    if (p["name"], m) in starts:
        return starts[(p["name"], m)]
    for s in ar.state_metrics(p):
        if config.cumulative_flow_for(p["name"], s) == m and (p["name"], s) in starts:
            st = starts[(p["name"], s)]
            return {"from": str((pd.Timestamp(st["from"]) + pd.Timedelta(days=1)).date()),
                    "why": f"{st['why']} (differenced from {s})"}
    return None


# completeness_report.py verdicts that SETTLE a series — never a gap for the audit.
SETTLED = ("COMPLETE", "COMPLETE (MANUAL)", "N/A", "ACCEPTED LIMIT", "WAITING ON A DATE", "PARKED")


def classifications(db: str):
    """{(project, metric): (verdict, detail)} exactly as completeness_report.py classifies them —
    on the workbook's own aggregate (its read-time views included), for every pair the audit lists."""
    import build_workbook as bw
    import completeness_report as cr
    from store import Store
    scope, parked = cr.portfolio_scope()
    bw._SCOPE = list(scope)
    st = Store(db)
    try:
        long = st.load_long()
        asof = pd.Timestamp.now("UTC").tz_localize(None).normalize()
        data = bw.aggregate(long, st.fetch_status(), asof, gaps=st.gap_report(), review=st.review_queue())
    finally:
        st.close()
    firsts = (long.groupby(["project", "metric"])["date"].min().dt.strftime("%Y-%m-%d").to_dict()
              if not long.empty else {})
    got = {(r["project"], r["metric"]): r for r in data.to_dict("records")}
    out = {}
    for p in config.PROJECTS:
        if p["name"] in parked or p["name"] in cr.PARKED:
            out.update({(p["name"], m): ("PARKED", "not in portfolio.txt / parked", None)
                        for m in config.metrics_for_project(p)})
            continue
        for m in config.metrics_for_project(p):
            row = got.get((p["name"], m)) or {"status": "missing"}
            fd = row.get("first_date")
            first = fd if isinstance(fd, str) and fd else firsts.get((p["name"], m))
            out[(p["name"], m)] = (*cr.classify(p, m, row, first, asof, firsts), first)
    return out


def rows(db: str, short_only: bool, classify: bool = True) -> list[tuple]:
    from fetch import archive as ar
    settled = classifications(db) if classify else {}
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
            cls = settled.get((p["name"], m))
            decided = cls if cls and cls[0] in SETTLED else None
            if first is None:
                if decided:
                    if not short_only:
                        out.append((p["name"], m, "", "", 0, 0, "", "DECIDED", f"{decided[0]}: {decided[1][:200]}"))
                    continue
                out.append((p["name"], m, "", "", 0, 0, "", "EMPTY", why_short(p, m, None)))
                continue
            days = (pd.Timestamp(last) - pd.Timestamp(first)).days + 1
            seen = memo.get(f"{p['name']}|{m}") or {}
            need = PRICE_DAYS if m in bf.PRICE_SERIES else Q0_DAYS
            start = series_start(p, m)
            verdict = ("FULL" if (today() - pd.Timestamp(first)).days >= need - bf.SLACK_DAYS else
                       "COMPLETE" if start and first <= start["from"] else
                       "SOURCE-LIMITED" if seen.get("first") == first else "BACKFILLING")
            # AS READ: a read-time view can carry history the store does not (Hyperliquid's buyback
            # and burn before the live read = DefiLlama holders revenue / same-day price, prepended in
            # build_workbook, never stored — Jake 2026-09-29/30). The audit counts the series the
            # workbook reads.
            as_read = cls[2] if cls else None
            if (verdict not in ("FULL", "COMPLETE") and as_read and as_read < first
                    and (today() - pd.Timestamp(as_read)).days >= need - bf.SLACK_DAYS):
                verdict = "FULL AS READ"
            elif verdict not in ("FULL", "COMPLETE") and m in config.ARCHIVE_NOT_BY_DESIGN:
                verdict = "BY DESIGN"          # total_supply: CoinGecko's, read "now" only (Jake, 2026-10-01)
            elif verdict not in ("FULL", "COMPLETE") and decided:
                verdict = "DECIDED"
            if short_only and verdict in ("FULL", "COMPLETE", "DECIDED", "FULL AS READ", "BY DESIGN"):
                continue
            why = ("" if verdict == "FULL" else
                   f"series starts {start['from']}: {start['why']}" if verdict == "COMPLETE" else
                   f"{decided[0]}: {decided[1][:200]}" if verdict == "DECIDED" else
                   config.ARCHIVE_NOT_BY_DESIGN.get(m, "") if verdict == "BY DESIGN" else
                   f"stored from {first}; the workbook reads it from {as_read} (a read-time view: "
                   f"{config.read_time_history(p['name'], m) or 'see build_workbook'})" if verdict == "FULL AS READ" else
                   why_short(p, m, first))
            out.append((p["name"], m, first, last, n, days,
                        seen.get("first", "") + (f" (checked {seen['checked_on']})" if seen else ""),
                        verdict, why))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--db", default="metrics.db")
    ap.add_argument("--short-only", action="store_true")
    ap.add_argument("--no-classify", action="store_true",
                    help="skip completeness_report.py's classifications (fast; decided items show as short)")
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
    table = rows(a.db, a.short_only, classify=not a.no_classify)
    print(f"{'project':<13} {'metric':<24} {'oldest':<11} {'newest':<11} {'rows':>5} "
          f"{'days':>5}  {'source reaches (backfill memo)':<34} verdict / what fills it")
    for r in table:
        print(f"{r[0]:<13} {r[1]:<24} {r[2]:<11} {r[3]:<11} {r[4]:>5} {r[5]:>5}  {r[6]:<34} {r[7]}"
              + (f" — {r[8]}" if r[8] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
