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


_SPAN = __import__("re").compile(r"\[span=(\d+)d\]")


def _span_days(source) -> int:
    """How many days a differenced flow row covers — 1 unless its source says otherwise.

    ** A THREE-DAY DELTA IS NOT A BIG DAY. ** Hyperliquid's rebuilt series compared a one-day
    delta of 17,366 against a three-day delta of 89,954 and the change check reported a 418%
    rise, which is entirely the calendar. Normalising both sides to per-day is the comparison
    that means something; comparing the raw figures is comparing a day with a long weekend.
    """
    m = _SPAN.search(str(source or ""))
    return int(m.group(1)) if m else 1


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

    # ===== THE CURRENT UTC DAY IS DROPPED FOR A DAILY PROVIDER FLOW. Added 2026-09-23. =====
    # Not deferred, not flagged — not stored. A provider publishes the current day from the
    # moment it starts and the figure is near-empty rather than proportional: Sky's fees read
    # $7,586 against a typical $909,801 at 12:40 UTC, past the halfway point. Left in, that row
    # sits inside every trailing-30-day "Now" figure until the next run replaces it.
    #
    # It costs nothing to drop: the providers are called with a TRAILING WINDOW, so tomorrow's
    # run re-requests the same day complete and upserts it. Section S established that past days
    # heal for exactly this reason.
    #
    # REPORTED, NOT SILENT. A row that vanishes without a word is how a real coverage gap gets
    # mistaken for a quiet day, and this file's whole argument is that a skip must say so.
    dropped = df[[bool(config.drop_current_day(r.project, r.metric, r.source))
                  and str(r.date)[:10] >= str(pd.Timestamp.now("UTC").date())
                  for r in df.itertuples(index=False)]]
    if not dropped.empty:
        for (project, metric), g in dropped.groupby(["project", "metric"]):
            out.skipped(str(g["source"].iloc[0]).split(":")[0], project,
                        f"{metric}: the row dated {str(g['date'].iloc[0])[:10]} is TODAY and was "
                        f"NOT STORED. A daily flow from this provider is published from the "
                        f"moment the day starts and is near-empty rather than proportional, so "
                        f"it would sit inside every trailing-window figure until the next run. "
                        f"The window is trailing, so tomorrow's run fetches this day complete.",
                        tier=None if pd.isna(g["tier"].iloc[0]) else int(g["tier"].iloc[0]))
        df = df.drop(dropped.index)
    if df.empty:
        return df

    keep = []
    for row in df.itertuples(index=False):
        lo, hi = config.sanity_bounds(row.project, row.metric)
        v = row.value
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            out.review_item(row.project, row.metric, REASON_BOUNDS, ACTION_REJECTED, value=float(v),
                            prior_value=prior_values.get((row.project, row.metric)),
                            date=row.date, source=row.source,
                            # Python int today, because itertuples happens to unwrap it. Cast
                            # anyway: "happens to" is not a guarantee across pandas versions, and
                            # the sibling path above is proof of what the failure looks like.
                            tier=None if pd.isna(row.tier) else int(row.tier))
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
        # (a) LUMPY BY DESIGN. GEODNET burns weekly and the chain read differences daily, so a
        # burn day carries a week's burn and the days between carry zero. 35,000 -> 105,000 is the
        # mechanism, not a fault, and no adjacent-day threshold distinguishes it from one.
        #
        # ** IT USED TO BE EXEMPTED ENTIRELY, AND THAT IS A CHECK THAT DOES NOT RUN. Changed
        # 2026-09-23. ** A series nobody checks is a series where a scraper redesign lands
        # silently. The lumpiness is in the DAILY shape, not in the weekly total, so comparing
        # two full weeks removes it — and that is a real check rather than a shrug.
        lumpy = config.lumpy_flow(project, metric)

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
            complete = g
            row = g.iloc[-1]
            prior = prior_values.get((project, metric))

        prior_date = None
        basis = ("the previous stored value of this series, whatever date it carries — the store "
                 "holds one number per series and this run did not bring a comparable earlier one")
        if len(complete) >= 2 and prior is not None and complete.iloc[-2]["value"] == prior:
            prior_date = complete.iloc[-2]["date"]
            basis = "the previous complete observation in this run's own series"

        # (c) A DAILY FLOW HAS A WEEK IN IT. Run 20260921T204341Z raised twelve change_threshold
        # flags and every one was a 40-60% drop. 2026-09-20 was a Sunday. Protocol fees, revenue
        # and volume fall by roughly half at the weekend on every chain in this universe, so
        # comparing Sunday against Friday flags the calendar, not the data — and a check that
        # fires every weekend is a check nobody reads by the second weekend.
        #
        # SAME WEEKDAY, ONE WEEK EARLIER. The alternative — a trailing 7-day average — was worked
        # through and does NOT fix this, which is worth writing down because it is the more
        # obvious-looking option: if a weekend day is half a weekday, the trailing mean is
        # (5W + 2*0.5W)/7 = 0.857W, and Sunday at 0.5W still reads as a 42% drop against it.
        # Every weekend would still flag. Sunday against the previous Sunday is flat, which is
        # the point: it removes the weekly cycle instead of averaging over it, and a real step
        # change survives it because a real step change is still there seven days later.
        #
        # SCOPED TO DAILY FLOWS. A stock has no weekly cycle, and a weekly or monthly series has
        # no same-weekday to find. Both keep the adjacent comparison, which is right for them.
        # (c0) ** A WEEK AGAINST A WEEK, WHERE THERE IS A WEEK TO USE. Added 2026-09-23. **
        # Run 20260922 raised 29 change_threshold flags, all 2026-09-21 against 2026-09-14 and
        # all moving the SAME DIRECTION across unrelated projects — the signature of an artefact.
        # The same-weekday comparison below was already working; what it cannot fix is LUMPINESS.
        # Maple books fees at $1,949,229 on one day and $15,855 on another, and last Sunday is
        # exactly as lumpy as this Sunday, so a same-weekday comparison of a lumpy series flags
        # the booking calendar instead of the weekday calendar.
        #
        # A TRAILING 7-DAY SUM AGAINST THE PRIOR 7-DAY SUM FIXES BOTH AT ONCE, and it is NOT the
        # trailing average that was worked through and rejected. That one compared a single day
        # against a mean — (5W + 2*0.5W)/7 = 0.857W, so Sunday at 0.5W still read as a 42% drop
        # and every weekend still flagged. This compares two WINDOWS. Each contains one of every
        # weekday, so the weekly cycle cancels on both sides rather than being averaged over; and
        # a lumpy booking lands inside a window rather than being the whole of one side.
        #
        # IT NEEDS FOURTEEN COMPLETE DAYS AND BOTH WINDOWS FULL. A partial window is a smaller
        # sum, which reads as a fall — exactly the artefact this is removing. Where the run's
        # frame does not carry fourteen days (a chain read brings one point), it falls through to
        # the same-weekday comparison below, which is the right answer for a series with no
        # window to build.
        # ** SCOPED TO LUMPY SERIES, AND THE SCOPING IS THE WHOLE DESIGN. ** A window comparison
        # is deliberately insensitive to ONE day: a collapse from 450,000 to 9,000 on a single
        # day moves a seven-day sum by about 8%, under any threshold worth having. That is
        # exactly the scraper-redesign failure this guard exists to catch, so applying windows
        # everywhere would trade 29 false flags for a blind spot on the one thing that matters.
        # (Caught by test_a_daily_flow_is_compared_against_the_same_weekday_not_the_day_before,
        # which is why this is scoped rather than global.)
        #
        # On a lumpy series the single-day check is USELESS ANYWAY — that is what "lumpy by
        # design" means — so a window comparison there costs nothing and replaces an exemption.
        window_basis = None
        if (lumpy
                and config.METRICS.get(metric, {}).get("kind") == "flow"
                and config.series_granularity(project, metric) == "daily"
                and len(complete) >= 14):
            dated = complete.copy()
            dated["_d"] = pd.to_datetime(dated["date"])
            end = pd.Timestamp(row["date"])
            cur = dated[(dated["_d"] > end - pd.Timedelta(days=7)) & (dated["_d"] <= end)]
            prev = dated[(dated["_d"] > end - pd.Timedelta(days=14))
                         & (dated["_d"] <= end - pd.Timedelta(days=7))]
            if len(cur) == 7 and len(prev) == 7:
                cur_sum, prev_sum = float(cur["value"].sum()), float(prev["value"].sum())
                prior = prev_sum
                prior_date = prev["date"].iloc[-1]
                window_basis = (
                    f"the TRAILING 7-DAY SUM ({cur_sum:,.4f}, {cur['date'].iloc[0]} to "
                    f"{cur['date'].iloc[-1]}) against the PRIOR 7-DAY SUM ({prev_sum:,.4f}, "
                    f"{prev['date'].iloc[0]} to {prev['date'].iloc[-1]}). Two full windows, so "
                    f"each holds one of every weekday and the weekly cycle cancels on both "
                    f"sides; a lumpy booking lands inside a window instead of being one side of "
                    f"the comparison. NOT a day against a trailing average — that was worked "
                    f"through and still flags every weekend")
                row = row.copy()
                row["value"] = cur_sum
                basis = window_basis

        if lumpy and window_basis is None:
            # Not enough history in this run's frame to build two full weeks. The daily check is
            # meaningless here by declaration, so there is nothing to fall back TO — and saying
            # so beats flagging the mechanism.
            continue

        if window_basis is None and (config.METRICS.get(metric, {}).get("kind") == "flow"
                and config.series_granularity(project, metric) == "daily"
                and len(complete) >= 2):
            latest_date = pd.Timestamp(row["date"])
            week_ago = complete[pd.to_datetime(complete["date"]) == latest_date - pd.Timedelta(days=7)]
            if not week_ago.empty:
                prior = float(week_ago.iloc[-1]["value"])
                prior_date = week_ago.iloc[-1]["date"]
                basis = (f"the SAME WEEKDAY one week earlier ({latest_date:%A}), because a daily "
                         f"flow carries a weekly cycle and an adjacent-day comparison flags the "
                         f"calendar rather than the data")
            else:
                basis = (f"the previous complete observation — {latest_date:%A} the "
                         f"{latest_date:%d %b} has NO same-weekday point one week back in this "
                         f"run's series, so the weekly cycle is NOT removed from this comparison "
                         f"and a weekend-against-weekday move may be the calendar")

        if prior is None or prior == 0:
            continue
        threshold = config.change_threshold_pct(project, metric) / 100.0

        # ** PER DAY, WHERE THE TWO SIDES COVER DIFFERENT NUMBERS OF DAYS. ** Only for a
        # DIFFERENCED flow, and only when the spans actually differ — dividing two one-day
        # deltas by 1 changes nothing and would only add noise to every basis line.
        value, span_note = float(row["value"]), ""
        if config.METRICS.get(metric, {}).get("kind") == "flow" and window_basis is None:
            cur_span = _span_days(row["source"])
            prior_src = None
            if prior_date is not None:
                match = complete[complete["date"].astype(str).str[:10] == str(prior_date)[:10]]
                prior_src = match["source"].iloc[-1] if not match.empty else None
            prior_span = _span_days(prior_src)
            if cur_span != prior_span:
                value, prior = value / cur_span, prior / prior_span
                span_note = (f" BOTH SIDES NORMALISED TO PER DAY: this row covers {cur_span} "
                             f"day(s) and the one it is compared against covers {prior_span}. "
                             f"A multi-day delta is not a big day, and comparing the raw figures "
                             f"is comparing a day with a long weekend.")
                basis = basis + span_note

        move = abs(value - prior) / abs(prior)
        if move > threshold:
            # int(row["tier"]), NOT row["tier"]. A Series element from .iloc[] on a mixed-dtype
            # frame is a numpy scalar, and sqlite3 stores numpy.int64 as an 8-byte BLOB in an
            # INTEGER column without complaining — see store._int_or_none. This exact line, added
            # on 2026-09-22 when the change check moved from itertuples() to groupby/iloc,
            # poisoned a review_queue row and killed the workbook build three layers away.
            # float() on value is doing the same job and was already here.
            out.review_item(project, metric, REASON_CHANGE, ACTION_FLAGGED,
                            value=value, prior_value=prior, date=row["date"],
                            prior_date=prior_date, basis=basis, source=row["source"],
                            tier=None if pd.isna(row["tier"]) else int(row["tier"]))
    return df


REASON_LEVEL_BREAK = "level_break"


def check_level_breaks(long: pd.DataFrame, out, asof: pd.Timestamp | None = None) -> None:
    """A flow series whose LEVEL has moved by an order of magnitude and stayed there.

    ** change_threshold COMPARES TWO DAYS, SO A BREAK IS VISIBLE FOR ONE DAY AND THEN INVISIBLE. **
    Morpho's fees ran $500-650K/day to 2026-09-11 and then $21.64, $0, $2.13, $2.99. Borrower
    interest did not stop; the source changed. On the day it happened change_threshold fired once;
    from the next day on every comparison was post-break against post-break, and $2.13 against
    $2.99 is a quiet series. The trailing-30-day sum then decays toward zero over a month and
    reads as a business collapse rather than as a broken feed.

    That is a property of the comparison, not a threshold that needs tuning — so this compares
    the LEVEL against the LEVEL. The ratio is unchanged the day after the break and the month
    after it, which is what makes the flag PERSIST until somebody acknowledges it in config with
    a reason.

    READS THE STORE, NOT THE RUN'S FRAME. A run brings today's points; the break is in the shape
    of the last month. Every other check here works on what just arrived, which is exactly why
    none of them could see this.

    MEDIANS, NOT MEANS OR SUMS: a single $1.9m booking moves a mean by a third and a median not
    at all. The failure being caught is "every day is now three orders of magnitude smaller",
    which no outlier can fake and no median can miss.
    """
    if long is None or long.empty:
        return
    asof = asof or pd.Timestamp(pd.Timestamp.now("UTC").date())
    df = long.copy()
    df["_d"] = pd.to_datetime(df["date"])

    for (project, metric), g in df.groupby(["project", "metric"]):
        if config.METRICS.get(metric, {}).get("kind") != "flow":
            continue
        if config.series_granularity(project, metric) != "daily":
            # A weekly or monthly series has a handful of points in a month; a median of three
            # numbers is not a level. Its breaks are visible in the period reconciliation.
            continue
        recent_days, baseline_days = config.level_break_windows(project, metric)
        recent = g[(g["_d"] > asof - pd.Timedelta(days=recent_days)) & (g["_d"] <= asof)]
        if len(recent) < recent_days * 0.6:
            # A half-empty recent window has a lower median for want of data, which is a coverage
            # gap wearing a level break's clothes.
            continue
        now = float(recent["value"].median())

        # ===== THE BASELINE IS THE LEVEL THE SERIES USED TO RUN AT, NOT THE WINDOW BEFORE LAST. =====
        # ** THE FIRST VERSION HAD THE SAME BLIND SPOT IT WAS BUILT TO REMOVE, JUST DELAYED. **
        # Comparing a 7-day median against the 30 days before it works the week after a break and
        # fails the month after: by then the baseline window is itself post-break, the ratio is
        # 1.0, and the flag goes quiet with nothing fixed. The test caught it — a month after
        # Morpho's break, the check said nothing.
        #
        # So the baseline is the series' own historical level: the rolling median FURTHEST from
        # where it sits now, across everything older than the recent window inside the lookback.
        # That does not decay as post-break days accumulate, which is what makes the flag persist
        # for as long as the store remembers what the series used to do.
        older = g[(g["_d"] > asof - pd.Timedelta(days=config.LEVEL_BREAK_LOOKBACK_DAYS))
                  & (g["_d"] <= asof - pd.Timedelta(days=recent_days))]
        if len(older) < baseline_days * 0.6:
            continue
        rolling = (older.sort_values("_d").set_index("_d")["value"]
                   .rolling(f"{baseline_days}D", min_periods=int(baseline_days * 0.6)).median()
                   .dropna())
        if rolling.empty:
            continue
        # FURTHEST FROM NOW, in whichever direction: the highest past level tests a fall, the
        # lowest tests a rise. A series that ever ran an order of magnitude away from where it
        # sits today has either broken or genuinely changed, and those need different actions —
        # which is why the row asks rather than decides.
        before = float(rolling.max() if rolling.max() - now >= now - rolling.min()
                       else rolling.min())
        if before <= 0:
            continue
        # BOTH DIRECTIONS, stated plainly. A series that grows tenfold and stays there is the
        # same class of event — a slug that started aggregating something new — and is just as
        # wrong to miss as one that collapses.
        factor = config.LEVEL_BREAK_FACTOR
        fell = now * factor < before
        rose = now > before * factor
        if not (fell or rose):
            continue

        ack = config.level_break_ack(project, metric)
        if ack:
            continue
        direction = "FELL" if now < before else "ROSE"
        ratio = (before / now) if now > 0 else float("inf")
        out.review_item(
            project, metric, REASON_LEVEL_BREAK, ACTION_FLAGGED,
            value=now, prior_value=before, date=str(recent["date"].max())[:10],
            prior_date=str(older["date"].max())[:10],
            basis=(f"the {recent_days}-day MEDIAN ({now:,.4f}) against the prior {baseline_days}-day "
                   f"MEDIAN ({before:,.4f}) — the level {direction} by "
                   f"{'more than 1,000' if ratio == float('inf') else f'{ratio:,.1f}'}x and STAYED "
                   f"there. change_threshold compares two days, so a break is visible on the day "
                   f"it happens and invisible afterwards: every later comparison is post-break "
                   f"against post-break. This flag PERSISTS until it is acknowledged in "
                   f"config.LEVEL_BREAK_ACKNOWLEDGED with a reason."),
            source=str(g.sort_values("_d")["source"].iloc[-1]), tier=None)
        out.gap(project, f"[data] {metric} level {direction} by an order of magnitude and stayed",
                reason=(f"the {recent_days}-day median is {now:,.4f} against a historical "
                        f"{baseline_days}-day median of {before:,.4f}. A source that restructures "
                        f"— a slug split into parent and child listings, an adapter that starts "
                        f"returning a residual — looks exactly like this, and so does a protocol "
                        f"genuinely winding down. The two need different actions."),
                tiers_attempted="1",
                suggestion=("Check the source first: fetch the provider's own listing for this "
                            "slug and look at whether it has been split or renamed. If the fall "
                            "is real, acknowledge it in config.LEVEL_BREAK_ACKNOWLEDGED with the "
                            "reason — do NOT widen LEVEL_BREAK_FACTOR, which turns the check off "
                            "for every other series too."))


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
            # THE COMPARAND CAN DEPEND ON THE PROJECT'S SUPPLY CONVENTION. Where total_supply is
            # NET of burn, bounding a cumulative dead-address balance against it compares a number
            # with itself-minus-itself; the contract's own gross figure is the real bound. See
            # config.bound_metric_for.
            lesser = config.bound_metric_for(project, greater, lesser)
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
