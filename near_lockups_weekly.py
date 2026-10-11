#!/usr/bin/env python3
"""
near_lockups_weekly.py — NEAR's still-UNVESTED lockups, refreshed weekly (Jake, 2026-10-11, C on 5b87610).

Under Jake's rule only tokens STILL UNVESTED leave circulating; NEAR's own circulating figure subtracts everything
LOCKED in the lockup contracts. So the circulating the ratios use for NEAR is NEAR's first-party figure + (locked −
unvested), both from ONE read of every live *.lockup.near at one block (check_offline_items.near_lockups: the
contracts' own getters, near/core-contracts@1b0436c9 lockup/src/getters.rs). The read takes ~2.5 hours, so it runs
once a week:

    python near_lockups_weekly.py            read and store when the last stored read is 7+ days old (else says so)
    python near_lockups_weekly.py --force    read and store now

Stores near_lockup_unvested_tokens and near_lockup_locked_tokens at the read's UTC date (source names the block).
NOTHING IS STORED when any lockup was unreadable — never a partial total. Aggregates only; no account is named.
windows/daily_run.ps1 calls it after the run; build_workbook._near_lockup_views applies it. NearBlocks' total −
circulating (fetch/nearblocks.py, daily) is the cross-check.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

import pandas as pd

METRICS = ("near_lockup_unvested_tokens", "near_lockup_locked_tokens")
EVERY_DAYS = 7


def last_read(db: str) -> pd.Timestamp | None:
    try:
        con = sqlite3.connect(db)
        try:
            row = con.execute("SELECT MAX(date) FROM metrics WHERE project='Near' AND metric=?", (METRICS[0],)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return pd.Timestamp(row[0]) if row and row[0] else None


def rows_for(got: dict, day: pd.Timestamp) -> pd.DataFrame:
    """The two stored rows for one read (got: check_offline_items.near_lockups' totals)."""
    src = f"near_rpc:lockups@{got['height']}"
    from fetch.base import tidy
    return pd.concat([tidy([(day, float(got["unvested"]))], "Near", METRICS[0], src, 2),
                      tidy([(day, float(got["locked"]))], "Near", METRICS[1], src, 2)], ignore_index=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    import store as store_mod
    prev = last_read(store_mod.DB_PATH)
    today = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)
    if prev is not None and not a.force and (today - prev).days < EVERY_DAYS:
        print(f"NEAR lockups: last read {prev.date()} ({(today - prev).days} day(s) ago) — next due "
              f"{(prev + pd.Timedelta(days=EVERY_DAYS)).date()}; nothing read")
        return 0
    import check_offline_items as coi
    import progress
    with progress.Heartbeat("near_lockups_weekly"):
        got = coi.near_lockups()
    if not got:
        print("NEAR lockups: no list — nothing stored")
        return 1
    if got["unreadable"]:
        print(f"NEAR lockups: {got['unreadable']:,} of {got['accounts']:,} lockup(s) unreadable — NOTHING STORED "
              f"(never a partial total); rerun with --force")
        return 1
    st = store_mod.Store(store_mod.DB_PATH)
    n = st.upsert(rows_for(got, today))
    print(f"NEAR lockups stored for {today.date()} at block {got['height']:,}: unvested {got['unvested']:,.0f}, "
          f"locked {got['locked']:,.0f} NEAR ({got['read']:,} live lockups read; {n} row(s))")
    return 0


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    sys.exit(main())
