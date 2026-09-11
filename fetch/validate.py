"""
fetch/validate.py — the anti-brittleness layer. Every adapter's output passes through here.

A scraper's dangerous failure is not the site going down, which is obvious. It is a redesign
where the selector still matches but now points at a different number. Two defences here
(the third, anchoring on label text rather than position, lives in scrape.py):

  * Sanity bounds      — a value outside the plausible range for its metric is REJECTED, not
                         stored, and goes to the Review Queue.
  * Change threshold   — a value moving more than the configured percentage against the last
                         stored value is STORED BUT FLAGGED, and goes to the Review Queue.

Rejection beats storage for out-of-bounds because a wrong number in the sheet is worse than a
gap; flagging beats rejection for a large move because a genuine step change (a halving, a
one-off 100m burn) must not be silently dropped.
"""
from __future__ import annotations

import pandas as pd

import config

REASON_BOUNDS = "out_of_bounds"
REASON_CHANGE = "change_threshold"
REASON_UNVERIFIED = "address_unverified"

ACTION_REJECTED = "rejected"
ACTION_FLAGGED = "stored_flagged"


def validate_frame(df: pd.DataFrame, prior_values: dict[tuple[str, str], float], out) -> pd.DataFrame:
    """Return the frame with out-of-bounds rows removed, recording every judgement on `out`.

    prior_values maps (project, metric) -> last stored value, used for the change check. Only
    the newest row per (project, metric) is change-checked: a backfill legitimately walks a
    series through large moves, and flagging every historical point would bury the signal.
    """
    if df is None or df.empty:
        return df

    df = df.copy()
    keep = []
    for row in df.itertuples(index=False):
        lo, hi = config.sanity_bounds(row.project, row.metric)
        v = row.value
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            out.review_item(row.project, row.metric, REASON_BOUNDS, ACTION_REJECTED, value=v,
                            prior_value=prior_values.get((row.project, row.metric)),
                            date=row.date, source=row.source, tier=row.tier)
            keep.append(False)
            continue
        keep.append(True)
    df = df[pd.Series(keep, index=df.index)]
    if df.empty:
        return df

    newest = df.sort_values("date").groupby(["project", "metric"], as_index=False).tail(1)
    for row in newest.itertuples(index=False):
        prior = prior_values.get((row.project, row.metric))
        if prior is None or prior == 0:
            continue
        threshold = config.change_threshold_pct(row.project, row.metric) / 100.0
        move = abs(row.value - prior) / abs(prior)
        if move > threshold:
            out.review_item(row.project, row.metric, REASON_CHANGE, ACTION_FLAGGED, value=row.value,
                            prior_value=prior, date=row.date, source=row.source, tier=row.tier)
    return df


def flagged_keys(review_items: list[dict]) -> set[tuple[str, str]]:
    """(project, metric) pairs the workbook should mark as needing review."""
    return {(i["project"], i["metric"]) for i in review_items}
