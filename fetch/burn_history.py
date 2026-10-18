"""
fetch/burn_history.py — a burn-log project's DAILY gross burn before its first stored row (Round G 2.3, Jake 2026-10-18).

Sky's gross_burn_tokens is the day-on-day change of burn_address_balance — the running sum of every SKY Transfer to
address(0) (fetch/chain.py `_burn_logs`). The series started when the tool started differencing it (2026-09-25), so the
reference for the 2026-09-13 spell burn (2,860,943.76 SKY) had nothing to check. The events themselves are all held
(the incremental log cache, from the token's deployment block): grouped by UTC day they ARE the same quantity — the
change of the running sum between two day boundaries — read from the chain's own records, nothing estimated.

DAY BOUNDARIES are the first block at or after each day's 00:00 UTC, from the explorer's block-by-time endpoint (Etherscan
`getblocknobytime`, closest=after) — one call per day, kept in `<logcache>/day-blocks-<chain>.json` so it is asked once.
A day's burn = the events with boundary(d) <= block < boundary(d + 1).

ONLY DAYS BEFORE THE SERIES' FIRST STORED ROW, and not the day before it (the first stored delta may span from a reading
taken during that day): stored rows are never touched. Source: the live rows' measuring point
(`chain:<chain>:burn_logs`) with `[events by UTC day]` and the `record` marker — one basis with the live deltas.
`token_metrics.py --seed burn_history` runs it.
"""
from __future__ import annotations

import json
import logging

import pandas as pd

import config

from .base import tidy

log = logging.getLogger("token_metrics.fetch.burn_history")

SOURCE = "chain"
TIER = 2


def day_bounds(days, block_at, cache: dict | None = None) -> dict:
    """{day: first block at or after day 00:00 UTC} for `days` and the day after the last. `block_at(ts)` -> block."""
    cache = {} if cache is None else cache
    want = sorted({pd.Timestamp(d).normalize() for d in days})
    if want:
        want.append(want[-1] + pd.Timedelta(days=1))
    out = {}
    for d in want:
        k = str(d.date())
        if k not in cache:
            cache[k] = int(block_at(int(d.tz_localize("UTC").timestamp())))
        out[d] = int(cache[k])
    return out


def daily_sums(events: list[dict], bounds: dict, scale: float) -> dict:
    """{day: Σ event value / scale} for every day with a boundary and a next boundary (0 for a day with none)."""
    days = sorted(bounds)
    out = {}
    blocks = sorted((int(e["block"]), float(e["value"])) for e in events)
    import bisect
    keys = [b for b, _ in blocks]
    for d, nxt in zip(days, days[1:]):
        lo, hi = bisect.bisect_left(keys, bounds[d]), bisect.bisect_left(keys, bounds[nxt])
        out[d] = sum(v for _, v in blocks[lo:hi]) / scale
    return out


def history_rows(project: dict, events: list[dict], first_stored, block_at, scale: float, cache: dict,
                 chain: str = "ethereum", history_days: int | None = None) -> tuple[pd.DataFrame | None, str]:
    """(gross_burn_tokens rows for the history window's days before `first_stored` − 1, a log line). Days before the
    first held burn event's day are left out (no burn existed to sum); a later day with no event is a measured 0."""
    if not events:
        return None, "no burn events held"
    first_ev = min(int(e["block"]) for e in events)
    end = pd.Timestamp(first_stored).normalize() - pd.Timedelta(days=1)       # the day before the first delta: left out
    start = end - pd.Timedelta(days=int(history_days or config.TRAJECTORY["history_days"]))
    days = list(pd.date_range(start, end - pd.Timedelta(days=1)))
    if not days:
        return None, "the stored series already reaches back to the history window"
    bounds = day_bounds(days, block_at, cache)
    sums = daily_sums(events, bounds, scale)
    rows = [(d, v) for d, v in sorted(sums.items()) if bounds[d + pd.Timedelta(days=1)] > first_ev]
    if not rows:
        return None, f"no day before {end.date()} on or after the first burn event (block {first_ev:,})"
    src = config.mark_source(f"{SOURCE}:{chain}:burn_logs[events by UTC day]", "record")
    total = sum(v for _, v in rows)
    return (tidy(rows, project["name"], "gross_burn_tokens", src, TIER),
            f"gross_burn_tokens: {len(rows)} day(s) {rows[0][0].date()}..{rows[-1][0].date()} from {len(events):,} held "
            f"burn event(s) grouped by UTC day ({total:,.2f} in all); the series' first stored row is "
            f"{pd.Timestamp(first_stored).date()}, the day before it is left out")


def load_cache(root, chain: str) -> tuple[dict, object]:
    path = root / f"day-blocks-{chain}.json"
    try:
        return json.loads(path.read_text()), path
    except (OSError, ValueError):
        return {}, path
