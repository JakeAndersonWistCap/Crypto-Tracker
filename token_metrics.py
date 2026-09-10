#!/usr/bin/env python3
"""
token_metrics.py — orchestrator.

    python token_metrics.py

One command, no arguments. Fetches every source, updates the local store (metrics.db),
loads manual_overrides.csv last, and rebuilds token_metrics.xlsx from scratch.

First run: full backfill of every source's available history.
Subsequent runs: extend the series and re-fetch a trailing 30-day window to catch revisions.
A failed source is logged to the Run Log tab and the last known values are carried forward,
marked stale in the workbook.
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

    out = fetch.fetch_all(config.PROJECTS, window)
    frame = out.frame()
    written = st.upsert(frame)
    log.info("upserted %d rows", written)

    for e in out.log:
        st.record_fetch(run_id, e.source, e.project, e.rows, e.status, e.message)

    try:
        n = st.load_overrides_csv(OVERRIDES_CSV)
        st.record_fetch(run_id, "manual", None, n, "ok", f"{OVERRIDES_CSV.name}: {n} overrides loaded")
    except Exception as e:  # noqa: BLE001
        st.record_fetch(run_id, "manual", None, 0, "failed", f"{OVERRIDES_CSV.name}: {e}")
        log.error("manual overrides failed: %s", e)

    failures = [e for e in out.log if e.status == "failed"]
    if failures:
        log.warning("%d fetch failures — see Run Log tab", len(failures))

    build_workbook(st, WORKBOOK, run_id=run_id)
    log.info("wrote %s", WORKBOOK)
    st.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
