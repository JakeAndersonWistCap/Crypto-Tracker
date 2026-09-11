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
REASON_REFERENCE = "disagrees_with_published_reference"
REASON_CROSSCHECK = "cross_check_divergence"

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


def check_reference_values(df: pd.DataFrame, out, tolerance: float = 0.005) -> None:
    """Compare fetched values against figures the protocol has already published.

    PancakeSwap publishes net mint monthly, and two of those months are recorded in config. If
    a scraper returns something different for one of those months, the scraper is wrong, not
    history — so it is flagged. This is a regression test on the extraction, running against
    live data every time.
    """
    if df is None or df.empty:
        return
    d = df.copy()
    d["period"] = pd.to_datetime(d["date"]).dt.to_period("M").astype(str)
    for project in config.PROJECTS:
        refs = project.get("reference_values") or []
        if not refs:
            continue
        for ref in refs:
            rows = d[(d["project"] == project["name"]) & (d["metric"] == ref["metric"])
                     & (d["period"] == ref["period"])]
            if rows.empty:
                continue
            got = float(rows["value"].sum())
            expected = float(ref["value"])
            if expected == 0:
                continue
            if abs(got - expected) / abs(expected) > tolerance:
                out.review_item(project["name"], ref["metric"], REASON_REFERENCE, ACTION_FLAGGED,
                                value=got, prior_value=expected, date=f"{ref['period']}-01",
                                source=ref.get("source", ""), tier=None)


def check_cross_checks(df: pd.DataFrame, out) -> None:
    """Compare two independent sources for the same figure and FLAG any divergence.

    Chainlink's Reserve balance can be read from the contract and from the protocol's own
    dashboard. Where both return a value the contract read is preferred, but a disagreement is
    surfaced rather than silently resolved: if the two sources disagree, one of them is wrong and
    the reader needs to know which figure they are looking at.
    """
    if df is None or df.empty:
        return
    latest = (df.sort_values("date")
                .groupby(["project", "metric"], as_index=False)
                .tail(1)
                .set_index(["project", "metric"]))
    for project in config.PROJECTS:
        for check in project.get("cross_checks") or []:
            name = project["name"]
            key_a = (name, check["primary"])
            key_b = (name, check["secondary"])
            if key_a not in latest.index or key_b not in latest.index:
                continue
            a = float(latest.loc[key_a, "value"])
            b = float(latest.loc[key_b, "value"])
            if a == 0:
                continue
            divergence = abs(a - b) / abs(a)
            if divergence > float(check.get("tolerance", 0.02)):
                out.review_item(name, check["primary"], REASON_CROSSCHECK, ACTION_FLAGGED,
                                value=a, prior_value=b,
                                date=latest.loc[key_a, "date"],
                                source=f"{check.get('primary_source', check['primary'])} vs "
                                       f"{check.get('secondary_source', check['secondary'])}",
                                tier=None)


def flagged_keys(review_items: list[dict]) -> set[tuple[str, str]]:
    """(project, metric) pairs the workbook should mark as needing review."""
    return {(i["project"], i["metric"]) for i in review_items}
