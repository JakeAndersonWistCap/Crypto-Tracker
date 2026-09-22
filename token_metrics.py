#!/usr/bin/env python3
"""
token_metrics.py — orchestrator.

    python token_metrics.py

One command, no arguments. Runs every source tier in order, updates the local store
(metrics.db), loads manual_overrides.csv last, and rebuilds token_metrics.xlsx from scratch.

FOUR FLAGS, ALL NARROWING, NONE CHANGING WHAT A FIGURE MEANS:
    --no-fetch            rebuild the workbook from the store and fetch nothing. Every display
                          fix, every label, every confidence rule is read-time, so this is the
                          loop for working on any of them — and it is the ONLY way to rebuild
                          without spending a day's politeness budget on the free endpoints.
    --project NAME        fetch one project. Repeatable. Names are exact and a wrong one is
                          refused with the list, rather than silently fetching nothing.
    --portfolio           fetch only the projects named in portfolio.txt. This is the DEFAULT
                          when that file exists; --all restores the full 30.
    --all                 every project, whatever portfolio.txt says.

A NARROWED RUN STILL BUILDS THE WHOLE WORKBOOK. The store holds every project's history and the
workbook is built from the store, so the projects that were not fetched render from what they
already have and go stale in the ordinary way. Nothing is blanked for having been skipped, and
nothing is reported as fresh that is not.

    free API  ->  contract read  ->  render the public page  ->  paid API

First run  backfills the entire available history from every source, so the 3/6/9-month
           trajectory columns are populated on run one rather than accumulating from install.
Later runs extend the series and re-fetch a trailing 30-day window to catch source revisions.
           Tier 4 (Dune) is skipped for any series the store already has history for.

** THE 30-DAY WINDOW NARROWS THE REQUEST ON EXACTLY ONE SOURCE. ** Checked, 2026-09-22, because
"incremental" reads like a network saving and is mostly not one:
    coingecko   days=30 goes into the request. Real, and it saves the transfer.
    defillama   the full daily history is downloaded and trimmed to 30 days locally —
                /summary/fees/{slug} has no date parameter to pass. Saves storage and
                validation work, and no network time.
    dune        the query executes in full whatever the window says; a Dune query has no
                incremental mode. A metric being fetched for the first time ignores the window
                deliberately, so the backfill is never truncated.
    chain / hypercore / tron / scrape
                accept the window and ignore it, correctly: they read a current value, not a
                series. There is no window to apply to one number.
This is recorded rather than "fixed" — trimming after the fact is right for a source with no
date parameter, the alternative being to store a year of history every day. It matters because
the way to make a run faster is --portfolio or --project, not a shorter window.

A failed source never kills the run: it is logged to the Run Log, the last known value is
carried forward and marked stale, and anything unresolved lands in the Gap Report.

Useful environment flags (all optional, all in .env):
    DUNE_API_KEY                     tier 4 backfill
    COINGECKO_API_KEY                raises the CoinGecko free-tier rate limit
    RPC_ETHEREUM / RPC_BSC / ...     comma-separated override of the public RPC fallback list
    TOKEN_METRICS_ALLOW_UNVERIFIED=1 read contract addresses not yet verified against protocol docs
    TOKEN_METRICS_DUNE_ALWAYS=1      re-pull Dune even where the store already has history
    TOKEN_METRICS_SOURCES=path       use a different sources.yaml
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

import config
import fetch
import store as store_mod
from build_workbook import build_workbook

ROOT = Path(__file__).resolve().parent
OVERRIDES_CSV = ROOT / "manual_overrides.csv"
PORTFOLIO_TXT = ROOT / config.PORTFOLIO_FILE
WORKBOOK = ROOT / "token_metrics.xlsx"
REFETCH_WINDOW_DAYS = 30


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Fetch supply/demand metrics and rebuild token_metrics.xlsx.",
        epilog="With no flags: fetch every project (or, if portfolio.txt exists, the projects "
               "named in it) and rebuild the workbook.")
    ap.add_argument("--no-fetch", action="store_true",
                    help="rebuild the workbook from the store without fetching anything")
    ap.add_argument("--project", action="append", default=[], metavar="NAME",
                    help="fetch only this project; repeatable. Exact name, as in config.")
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--portfolio", action="store_true",
                       help=f"fetch only the projects named in {config.PORTFOLIO_FILE} "
                            f"(the default when that file exists)")
    scope.add_argument("--all", action="store_true",
                       help="fetch every project, whatever portfolio.txt says")
    return ap.parse_args(argv)


def resolve_scope(args, log) -> list[dict]:
    """Which projects this run fetches. REFUSES on a name it does not recognise.

    A mistyped --project that silently fetched nothing would look identical to a run where every
    source had nothing to add, and the same is true of a typo in portfolio.txt — which is worse,
    because it persists. Both are reported with the list of valid names instead.
    """
    if args.project:
        unknown = [n for n in args.project if n not in config.PROJECT_BY_NAME]
        if unknown:
            raise SystemExit(
                f"unknown project name(s): {', '.join(unknown)}\n"
                f"Names are exact. Valid: {', '.join(p['name'] for p in config.PROJECTS)}")
        picked = [config.PROJECT_BY_NAME[n] for n in args.project]
        log.info("scope: %d project(s) named on the command line — %s",
                 len(picked), ", ".join(p["name"] for p in picked))
        return picked

    names, unknown = config.read_portfolio(PORTFOLIO_TXT)
    if unknown:
        # NOT FATAL, AND NOT IGNORED. A typo here would park a held asset, and a series that
        # stops collecting cannot be backfilled — CoinGecko serves total_supply as a current
        # value only. So the run widens rather than narrows, and says exactly what it could not
        # match.
        log.error("%s: %d name(s) not recognised and IGNORED — %s. Fetching every project this "
                  "run rather than risk parking a held asset on a typo. Valid names: %s",
                  PORTFOLIO_TXT.name, len(unknown), ", ".join(unknown),
                  ", ".join(p["name"] for p in config.PROJECTS))
        return list(config.PROJECTS)
    if args.all:
        log.info("scope: --all — every project (%d)", len(config.PROJECTS))
        return list(config.PROJECTS)
    if names:
        log.info("scope: %s — %d of %d projects (%s). Use --all for the rest.",
                 PORTFOLIO_TXT.name, len(names), len(config.PROJECTS), ", ".join(names))
        return [config.PROJECT_BY_NAME[n] for n in names]
    if args.portfolio:
        raise SystemExit(
            f"--portfolio needs {PORTFOLIO_TXT.name}, which does not exist (or is empty).\n"
            f"Create it with one project name per line — the held ones — and the default run "
            f"will use it. Names are exact:\n  "
            + "\n  ".join(p["name"] for p in config.PROJECTS))
    log.info("scope: every project (%d) — no %s on file",
             len(config.PROJECTS), PORTFOLIO_TXT.name)
    return list(config.PROJECTS)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("token_metrics")
    load_dotenv(ROOT / ".env")

    st = store_mod.Store(store_mod.DB_PATH)

    # ===== BUILD ONLY. =====
    # Every display rule in this project is READ-TIME — the confidence bands, the withheld
    # mechanisms, the labels, the window arithmetic — so all of them are worked on against the
    # store as it stands. Refetching to see a label change is a day's politeness budget spent on
    # free endpoints for nothing, and the standing rule is one run a day.
    if args.no_fetch:
        # THE SAME SCOPE AS A FETCHING RUN. --no-fetch is how every read-time change is checked,
        # so a build that quietly widened to 30 projects would show a workbook nobody is going to
        # get from a real run.
        scope = resolve_scope(args, log)
        log.info("--no-fetch: rebuilding %s from the store, fetching nothing (%d project(s))",
                 WORKBOOK.name, len(scope))
        build_workbook(st, WORKBOOK, only=[p["name"] for p in scope])
        log.info("wrote %s", WORKBOOK)
        st.close()
        return 0

    projects = resolve_scope(args, log)
    run_id = fetch.new_run_id()
    first_run = st.is_empty()
    window = None if first_run else REFETCH_WINDOW_DAYS
    log.info("run %s — %s", run_id, "FIRST RUN: full backfill" if first_run else f"incremental, trailing {window}d re-fetch")

    # Manual overrides load LAST into the store, but the Gap Report needs to know what they
    # cover before the gap detector runs, so read the file's keys up front.
    try:
        n_manual = st.load_overrides_csv(OVERRIDES_CSV)
        manual_keys = set(
            st.conn.execute("SELECT DISTINCT project, metric FROM manual_overrides").fetchall()
        )
        st.record_fetch(run_id, "manual", None, n_manual, "ok", f"{OVERRIDES_CSV.name}: {n_manual} overrides loaded")
    except Exception as e:  # noqa: BLE001
        n_manual, manual_keys = 0, set()
        st.record_fetch(run_id, "manual", None, 0, "failed", f"{OVERRIDES_CSV.name}: {e}")
        log.error("manual overrides failed: %s", e)

    # The change-threshold check compares against the newest figure of any date.
    prior_values = st.latest_values()
    # DIFFERENCING is different: its inputs must come from an EARLIER DAY. Two runs on one day
    # write to the same (date, project, metric) key, so a same-day prior makes the second run
    # overwrite the first's correct flow with a dust delta — which is exactly what happened to
    # PancakeSwap's 59,857,159.01 burn. Anchoring on the last earlier-dated row makes a same-day
    # re-run recompute the identical answer instead of destroying it.
    today_key = fetch.today().date().isoformat()
    before = st.values_before(today_key)
    prior_values_for_delta = {k: v[0] for k, v in before.items()}
    prior_dates = {k: v[1] for k, v in before.items()}
    prior_sources = {k: v[2] for k, v in before.items()}
    has_history = {k for k, _ in prior_values.items()}
    # ENDPOINTS THE STORE HAS WATCHED 404 WITHOUT EVER WORKING. Not called this run, retried
    # automatically once 14 days pass without an attempt. Derived from fetch_status rather than
    # declared in config: a hand-maintained list of absent resources goes stale against reality
    # and nothing reconciles it back.
    absent = st.known_absent()
    if absent:
        log.info("%d source/project pair(s) are KNOWN ABSENT and will not be called: %s",
                 len(absent), ", ".join(f"{s}/{p}" for s, p in sorted(absent)))

    started = time.monotonic()
    out = fetch.fetch_all(
        projects, window,
        prior_values=prior_values,
        prior_values_for_delta=prior_values_for_delta,
        prior_dates=prior_dates,
        prior_sources=prior_sources,
        has_history=has_history,
        last_dates=st.last_dates(),
        known_absent=absent,
        manual_keys=manual_keys,
        # THE STORE'S OWN HISTORY, for the level-break check. Every other check works on what
        # just arrived; a level break is in the shape of the last month, which is why none of
        # them could see Morpho's source change after the day it happened.
        stored_long=st.load_long(),
    )

    written = st.upsert(out.frame())
    log.info("upserted %d rows", written)

    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
    n_review = st.record_review(run_id, out.review)
    n_gaps = st.record_gaps(run_id, out.gaps)
    n_staged = st.record_staging(run_id, out.staged)

    # ===== WHERE THE TIME WENT, PER TIER. =====
    # Not a profile and not trying to be: it answers the one question that decides what to do
    # about a slow run — which TIER is slow. A tier-2 run is contract reads over public RPC and a
    # tier-4 run is Dune; they have nothing in common and the remedy for one does nothing for the
    # other. The row count beside the time is what separates "slow" from "doing a lot".
    elapsed = time.monotonic() - started
    rows_by_source, fails_by_source, skips_by_source = {}, {}, {}
    for e in out.log:
        rows_by_source[e.source] = rows_by_source.get(e.source, 0) + int(e.rows or 0)
        fails_by_source[e.source] = fails_by_source.get(e.source, 0) + int(e.status == "failed")
        skips_by_source[e.source] = skips_by_source.get(e.source, 0) + int(e.status == "skipped")
    log.info("fetch took %.1fs across %d project(s):", elapsed, len(projects))
    for t in sorted(out.timings, key=lambda x: -x["seconds"]):
        src = t["source"]
        log.info("    tier %-3s %-16s %7.1fs  %6d rows  %d failed  %d skipped",
                 t["tier"], src, t["seconds"], rows_by_source.get(src, 0),
                 fails_by_source.get(src, 0), skips_by_source.get(src, 0))
    # SLOWEST FIRST, and the slowest line is the answer. A source taking minutes and returning
    # nothing is the most useful row in a slow run's log, and a rows-only summary cannot show it.

    failures = [e for e in out.log if e.status == "failed"]
    skips = [e for e in out.log if e.status == "skipped"]
    log.info("run summary: %d rows | %d fetch failures | %d SKIPPED (ran nothing) | %d review items "
             "| %d gaps | %d manual overrides | %d staged (captured, used by nothing)",
             written, len(failures), len(skips), n_review, n_gaps, n_manual, n_staged)
    if failures:
        log.warning("%d fetch failures — see the Run Log tab", len(failures))
    # A skip is not a success. It produces no error, no failure and no gap, so without this it
    # reads exactly like a source that ran and had nothing to add — which is how a backfill that
    # never executed gets reported as one that worked.
    if skips:
        log.warning("%d source/metric pair(s) were SKIPPED and fetched nothing:", len(skips))
        for e in skips:
            log.warning("    SKIPPED  tier %s  %s / %s — %s", e.tier, e.source, e.project, e.message)
    if n_gaps:
        log.warning("%d unresolved metrics — see the Gap Report tab, it is the to-do list", n_gaps)

    # THE WORKBOOK IS DRAWN AT THE SCOPE THAT WAS FETCHED. Rendering a project this run never
    # touched puts a frozen cell next to a fresh one with nothing to tell them apart.
    build_workbook(st, WORKBOOK, run_id=run_id, only=[p["name"] for p in projects])
    log.info("wrote %s — %d project(s) rendered%s", WORKBOOK, len(projects),
             "" if len(projects) == len(config.PROJECTS)
             else f" of {len(config.PROJECTS)}; the rest keep their stored history and are "
                  f"redrawn by --all")
    st.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
