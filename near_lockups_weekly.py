#!/usr/bin/env python3
"""
near_lockups_weekly.py — NEAR's still-UNVESTED lockups, refreshed weekly (Jake, 2026-10-11, C on 5b87610).

Under Jake's rule only tokens STILL UNVESTED leave circulating; NEAR's own circulating figure subtracts everything
LOCKED in the lockup contracts. So the circulating the ratios use for NEAR is NEAR's first-party figure + (locked −
unvested), both from ONE read of every live *.lockup.near at one block (check_offline_items.near_lockups: the
contracts' own getters, near/core-contracts@1b0436c9 lockup/src/getters.rs). It runs once a week:

    python near_lockups_weekly.py            read and store when the last stored read is 7+ days old (else says so)
    python near_lockups_weekly.py --force    read and store now

Stores near_lockup_unvested_tokens and near_lockup_locked_tokens at the PINNED BLOCK's UTC date (source names the
block). NOTHING IS STORED when a lockup could not be READ (a network or RPC failure) — never a partial total.

RESUMABLE (Round D2-8, Jake 2026-10-26 — the 10-26 run read 1,367 of 1,956 in ~2.5 h, 673 unreadable after HTTP 429s
and near.org errors, and stored nothing): every lockup is read at ONE pinned block and its result kept (LogCache root
near-lockups-read.json); a rerun within NEAR_LOCKUP_PIN_MAX_H (48 h) — while the RPC still serves that block — reads
only the lockups still missing, at the same block. Calls go through one pacer over rpc.mainnet.fastnear.com and
rpc.mainnet.near.org that honours HTTP 429 / Retry-After (check_offline_items.NEAR_PACER); 4 lockups in flight.
Expected (measured 2026-10-26, D2-8a): ~12,700 calls, ~80 min when neither endpoint throttles, plus ~7 min for the
NearBlocks list (cached a day). Its progress lines (every 100 lockups) reach the daily log as printed. A lockup whose
OWN getter fails inside the contract ("wasm execution failed": the same answer every time, 1 of 1,960 on Jake's
2026-10-11 run) is not a read failure: it is counted, named by count in the log, and left out. A read entered by hand
(manual_overrides.csv, e.g. Jake's 2026-10-11 15:50 totals) counts as the last read. Aggregates only; no account named.
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


CONTRACT_FAULT = "wasm execution failed"


def last_read(db: str, overrides_csv: str | None = None) -> pd.Timestamp | None:
    """The newest stored read — the metrics table, the manual_overrides table, or manual_overrides.csv (loaded into the
    store on the next run)."""
    dates = []
    try:
        con = sqlite3.connect(db)
        try:
            for q in ("SELECT MAX(date) FROM metrics WHERE project='Near' AND metric=?",
                      "SELECT MAX(date) FROM manual_overrides WHERE project='Near' AND metric=?"):
                try:
                    row = con.execute(q, (METRICS[0],)).fetchone()
                    if row and row[0]:
                        dates.append(pd.Timestamp(row[0]))
                except sqlite3.Error:
                    pass
        finally:
            con.close()
    except sqlite3.Error:
        pass
    if overrides_csv:
        try:
            df = pd.read_csv(overrides_csv, dtype=str, comment="#").fillna("")
            hit = df[(df["project"].str.strip() == "Near") & (df["metric"].str.strip() == METRICS[0])]
            dates += [pd.Timestamp(d) for d in hit["date"] if d]
        except (OSError, ValueError, KeyError):
            pass
    return max(dates) if dates else None


def read_failures(got: dict) -> int:
    """Lockups that could not be READ (not those whose own getter fails inside the contract)."""
    errs = got.get("errors") or {}
    if not errs:
        return int(got.get("unreadable") or 0)
    return sum(n for why, n in errs.items() if CONTRACT_FAULT not in why)


def rows_for(got: dict, day: pd.Timestamp) -> pd.DataFrame:
    """The two stored rows for one read (got: check_offline_items.near_lockups' totals)."""
    src = f"near_rpc:lockups@{got['height']}"
    from fetch.base import tidy
    return pd.concat([tidy([(day, float(got["unvested"]))], "Near", METRICS[0], src, 2),
                      tidy([(day, float(got["locked"]))], "Near", METRICS[1], src, 2)], ignore_index=True)


def read_day(got: dict, fallback: pd.Timestamp) -> pd.Timestamp:
    """The UTC date of the pinned block (a resumed read is dated at its block, not at the day it finished)."""
    ts = int(got.get("block_ts") or 0)
    return pd.Timestamp(ts, unit="ns").normalize() if ts else fallback


def pin_expiry(got: dict) -> str:
    import check_offline_items as coi
    try:
        t = coi.lockup_pin_utc(got["pinned_at"]) + pd.Timedelta(
            hours=coi.NEAR_LOCKUP_PIN_MAX_H)
        return f"{t:%Y-%m-%d %H:%M} UTC"
    except (KeyError, ValueError, TypeError):
        return f"{coi.NEAR_LOCKUP_PIN_MAX_H} h from the pin"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    import store as store_mod
    from pathlib import Path
    prev = last_read(store_mod.DB_PATH, str(Path(__file__).resolve().parent / "manual_overrides.csv"))
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
        print("NEAR lockups: no list or no block — nothing stored")
        return 1
    if read_failures(got):
        print(f"NEAR lockups: {read_failures(got):,} of {got['accounts']:,} lockup(s) not yet read at block "
              f"{got['height']:,} — NOTHING STORED (never a partial total). Progress is kept at that block: a rerun "
              f"(python near_lockups_weekly.py --force) before {pin_expiry(got)} reads only the rest; after that a new "
              f"block is pinned and the read starts over")
        return 1
    faults = int(got.get("unreadable") or 0)
    day = read_day(got, today)
    st = store_mod.Store(store_mod.DB_PATH)
    n = st.upsert(rows_for(got, day))
    print(f"NEAR lockups stored for {day.date()} at block {got['height']:,}: unvested {got['unvested']:,.0f}, "
          f"locked {got['locked']:,.0f} NEAR ({got['read']:,} live lockups read; {n} row(s))"
          + (f"; {faults} lockup(s) whose own getter fails ({CONTRACT_FAULT}) left out" if faults else ""))
    return 0


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    sys.exit(main())
