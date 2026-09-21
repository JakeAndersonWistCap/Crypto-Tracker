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

    "Newest" now means newest COMPARABLE, which is not always newest. Two series are exempted for
    reasons that are properties of the data rather than of its quality — a provider's partial
    current day, and a flow that is lumpy by design — because comparing across either produces a
    flag on every run, and a check that always fires is a check that is never read.
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

    # ** COMPARE LIKE WITH LIKE, OR DO NOT COMPARE. ** Run 20260921T100546Z raised 16
    # change_threshold flags and essentially all of them were artefacts of comparing things that
    # are not comparable. A guard that fires every run is one nobody reads.
    today = pd.Timestamp(pd.Timestamp.now("UTC").date())
    for (project, metric), g in df.groupby(["project", "metric"]):
        # (a) LUMPY BY DESIGN. GEODNET burns weekly and the chain read differences daily, so a
        # burn day carries a week's burn and the days between carry zero. 35,000 -> 105,000 is the
        # mechanism, not a fault, and no threshold distinguishes it from one.
        lumpy = config.lumpy_flow(project, metric)
        if lumpy:
            continue

        g = g.sort_values("date")
        # (b) THE CURRENT DAY IS NOT A DAY YET. DefiLlama and CoinGecko publish it from the moment
        # it starts, so the newest point is a few hours against yesterday's twenty-four. Dropping
        # it and comparing the last two COMPLETE days is the whole fix — widening the threshold
        # instead would hide the real step changes this check exists to catch.
        provider = str(g["source"].iloc[-1] or "").split(":")[0]
        if provider in config.PROVIDERS_WITH_INCOMPLETE_CURRENT_PERIOD:
            complete = g[pd.to_datetime(g["date"]) < today]
            if complete.empty:
                continue                  # nothing but the partial day — nothing to compare
            # The prior COMPLETE day from THIS frame where the provider gave us one, rather than
            # prior_values, which may itself hold a partial day stored by an earlier run today.
            if len(complete) >= 2:
                row = complete.iloc[-1]
                prior = float(complete.iloc[-2]["value"])
            else:
                row = complete.iloc[-1]
                prior = prior_values.get((project, metric))
        else:
            row = g.iloc[-1]
            prior = prior_values.get((project, metric))

        if prior is None or prior == 0:
            continue
        threshold = config.change_threshold_pct(project, metric) / 100.0
        move = abs(float(row["value"]) - prior) / abs(prior)
        if move > threshold:
            out.review_item(project, metric, REASON_CHANGE, ACTION_FLAGGED,
                            value=float(row["value"]), prior_value=prior, date=row["date"],
                            source=row["source"], tier=row["tier"])
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


REASON_IMPOSSIBLE = "impossible_relation"

# Relations between two metrics that CANNOT both be right. Each is an identity, not a heuristic:
# no tolerance, no judgement about the project, and true of every token ever issued.
#
# This class of check did not exist. Every validation until now looked at ONE figure — its bounds,
# its movement, its source — so two figures that were each individually plausible could contradict
# each other and both pass. Aerodrome's locked supply exceeded its circulating supply and both
# read GREEN, because nothing ever compared them.
# EXTENDED 2026-09-15 from four relations to nine. The function below already looped over this
# list rather than hand-coding comparisons, so widening the coverage is a data change and not a
# new mechanism — which is why this is an edit to a list and not a second checker.
#
# ** EVERY BOUND IS AGAINST total_supply, NEVER max_supply, WHERE A CHOICE EXISTS. ** total_supply
# is the tighter of the two, so it catches strictly more; and max_supply is frequently ABSENT —
# Ethereum, Near, Uniswap and GEODNET all have no max_supply figure — so a bound written against
# it would silently skip the projects most likely to need it. The one place max_supply is used is
# as a ceiling on total_supply itself, which is the only thing it can bound.
IMPOSSIBLE_RELATIONS = [
    # --- lock identities ------------------------------------------------------------------
    # ** BOUNDED BY total_supply, NEVER BY circulating_supply. CORRECTED 2026-09-16. **
    #
    # "tokens cannot be locked that are not in circulation" READ LIKE AN IDENTITY AND WAS NOT ONE.
    # It assumed circulating_supply INCLUDES locked tokens. For a ve-token protocol CoinGecko's
    # convention is the opposite: circulating EXCLUDES escrowed supply, so locked and circulating
    # are DISJOINT HALVES of total_supply. Under that convention locked tokens sitting outside
    # circulating supply is the correct and expected state, not an anomaly — and the check was
    # flagging Aerodrome every run for being normal.
    #
    # Settled on live data, not argued: Aerodrome's locked 989,752,701 + circulating 988,697,600
    # = total 1,978,450,301, matching to 0.00%, while the include-locked reading is out by 50%.
    #
    # The three relations below are the REPLACEMENTS, not additions alongside the old ones. They
    # hold under EITHER convention, because total_supply counts every token that exists however a
    # provider chooses to slice it — which is exactly what makes them identities and the previous
    # form not one.
    ("locked_tokens", "total_supply",
     "more tokens cannot be locked than exist"),
    ("locked_tokens_underlying", "total_supply",
     "the assets backing a lock cannot exceed the tokens that exist"),
    ("locked_tokens_principal", "total_supply",
     "staked principal cannot exceed the tokens that exist"),
    # --- supply identities ---------------------------------------------------------------
    ("circulating_supply", "total_supply",
     "circulating supply cannot exceed total supply"),
    ("total_supply", "max_supply",
     "total supply cannot exceed a hard cap"),
    # --- holdings ------------------------------------------------------------------------
    ("treasury_holding_tokens", "total_supply",
     "a treasury cannot hold more tokens than exist"),
    ("buyback_fund_balance", "total_supply",
     "a buyback fund cannot hold more tokens than exist"),
    # --- burn ----------------------------------------------------------------------------
    # A FLOW AGAINST A STOCK, and valid for every token including the ones that mint: however
    # much is minted, a single period cannot destroy more than exists at the end of it.
    ("gross_burn_tokens", "total_supply",
     "a single period cannot burn more tokens than exist"),
    # ** NOT AN IDENTITY FOR A TOKEN THAT MINTS. ** A dead-address balance is CUMULATIVE over all
    # time; total_supply is an instantaneous figure. A token that mints and burns continuously
    # therefore accumulates burns beyond any supply it ever held at once, and the two are not
    # comparable. This relation's own rationale gives the game away — "than were ever issued" is
    # cumulative issuance, which is NOT what total_supply measures. Kept, because it is a true
    # identity for a fixed-supply token, and exempted per project below.
    ("burn_address_balance", "total_supply",
     "more tokens cannot have been burned than were ever issued"),
]


def check_impossible_relations(df: pd.DataFrame, out, tolerance: float = 0.0) -> None:
    """Flag pairs of figures that contradict each other, whatever each looks like alone.

    THE TOLERANCE IS ZERO, and that is the whole point. The first version of this had half a
    percent, to absorb the two figures being read at slightly different moments from different
    sources, and it silently passed the very case it was written for — a 0.087% overshoot sitting
    comfortably inside the buffer.

    These are identities, not estimates. A buffer here is not caution, it is a licence for the
    contradiction to sit in the sheet as long as it stays small, and a small contradiction is the
    one nobody notices.

    ** BUT ZERO TOLERANCE ONLY HELPS IF THE RELATION IS ACTUALLY AN IDENTITY. ** The case that
    motivated the zero tolerance — locked_tokens vs circulating_supply — turned out not to be one
    at all, and was withdrawn on 2026-09-16 (see the list above). A non-identity checked at zero
    tolerance does not catch a subtle error; it manufactures a violation every single run and
    teaches the reader to scroll past the Review Queue. Rigour on the threshold is worth nothing
    without rigour on the premise.

    Two caveats on "zero", both real:
      * the float epsilon below is RELATIVE at 1e-9, so it absorbs ~1 token at a 1e9 supply and
        ~1,000 at 1e12 — far above float64 noise, and a tolerance in all but name at that scale;
      * config.relation_exempt() can withdraw one comparison for one project, with a recorded
        reason. That is for a relation which is not an identity THERE, never for one that is
        merely inconvenient.
    """
    epsilon = 1e-9
    if df is None or df.empty:
        return
    latest = (df.sort_values("date").groupby(["project", "metric"], as_index=False).tail(1))
    by_key = {(r.project, r.metric): (r.value, r.date, r.source)
              for r in latest.itertuples(index=False)}
    for project in {p for p, _ in by_key}:
        for greater, lesser, why in IMPOSSIBLE_RELATIONS:
            # A RELATION THAT IS NOT AN IDENTITY FOR THIS PROJECT IS SKIPPED, WITH A REASON ON FILE.
            # Not a tolerance — the tolerance stays zero, because the failure this whole check
            # exists to catch is a small contradiction nobody notices. This is the different case:
            # a comparison that is not valid HERE at all, and would therefore fire on every run
            # and teach the reader to scroll past the Review Queue. The exemption is declared in
            # config with a reason and validated, so it cannot be used to quiet an inconvenience.
            if config.relation_exempt(project, greater, lesser):
                continue
            a, b = by_key.get((project, greater)), by_key.get((project, lesser))
            if a is None or b is None or not b[0]:
                continue
            if a[0] <= b[0] * (1 + max(tolerance, epsilon)):
                continue
            out.review_item(project, greater, REASON_IMPOSSIBLE, ACTION_FLAGGED,
                            value=a[0], prior_value=b[0], date=a[1],
                            source=f"{greater} ({a[0]:,.2f} from {a[2]}) EXCEEDS {lesser} "
                                   f"({b[0]:,.2f} from {b[2]}) — {why}", tier=None)
            out.gap(project, f"[data] {greater} exceeds {lesser}, which is impossible",
                    reason=(f"{greater} reads {a[0]:,.2f} against {lesser} at {b[0]:,.2f}, and {why}. "
                            f"Both figures passed every check that looks at one number at a time — "
                            f"their bounds, their movement, their source — because nothing compared "
                            f"them to each other. One of the two is measuring something other than "
                            f"what its label says. Sources: {a[2]} and {b[2]}."),
                    tiers_attempted="-",
                    suggestion=(f"Establish which. The usual cause is a denominator mismatch: the two "
                                f"figures come from different providers who count different things — an "
                                f"escrow balance that includes tokens the price feed does not treat as "
                                f"circulating, for instance. Fix the label or the read; do not widen "
                                f"the tolerance, which would only hide it."))


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
