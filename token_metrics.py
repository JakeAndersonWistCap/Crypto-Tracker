#!/usr/bin/env python3
"""
token_metrics.py — orchestrator.

    python token_metrics.py

One command, no arguments. Runs every source tier in order, updates the local store
(metrics.db), loads manual_overrides.csv last, and rebuilds token_metrics.xlsx from scratch.

    free API  ->  contract read  ->  render the public page  ->  paid API

First run  backfills the entire available history from every source, so the 3/6/9-month
           trajectory columns are populated on run one rather than accumulating from install.
Later runs extend the series and re-fetch a trailing 30-day window to catch source revisions.
           Tier 4 (Dune) is skipped for any series the store already has history for.

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

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

import config
import fetch
import store as store_mod
from build_workbook import build_workbook

ROOT = Path(__file__).resolve().parent
OVERRIDES_CSV = ROOT / "manual_overrides.csv"
WORKBOOK = ROOT / "token_metrics.xlsx"
REFETCH_WINDOW_DAYS = 30


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("token_metrics")
    load_dotenv(ROOT / ".env")

    st = store_mod.Store(store_mod.DB_PATH)
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

    out = fetch.fetch_all(
        config.PROJECTS, window,
        prior_values=prior_values,
        prior_values_for_delta=prior_values_for_delta,
        prior_dates=prior_dates,
        prior_sources=prior_sources,
        has_history=has_history,
        manual_keys=manual_keys,
    )

    written = st.upsert(out.frame())
    log.info("upserted %d rows", written)

    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message, e.tier)
    n_review = st.record_review(run_id, out.review)
    n_gaps = st.record_gaps(run_id, out.gaps)
    n_staged = st.record_staging(run_id, out.staged)

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

    build_workbook(st, WORKBOOK, run_id=run_id)
    log.info("wrote %s", WORKBOOK)
    st.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
