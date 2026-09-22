"""
fetch — source adapters, one module per tier.

    free API  ->  contract read  ->  render the public page  ->  paid API

Tier 1  llama.py, coingecko.py, schedule.py   free APIs and documented rules
Tier 2  chain.py                              web3 contract reads (build this out first)
Tier 3  scrape.py                             the protocol's own dashboard, via sources.yaml
Tier 4  dune.py                               historical backfill only
Tier 5  scrape.py                             off-chain operational metrics, same registry

Every adapter returns tidy long frames: date, project, metric, value, source, tier.
Every frame passes through validate.validate_frame before it reaches the store, so a value
outside its sanity bounds is rejected and a value that moved more than its change threshold is
flagged. A failed source is logged and reported as a gap; it never kills the run.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

import config

from .base import FetchOutput, LogEntry, new_run_id, point, today  # noqa: F401
from .chain import Chain
from .coingecko import CoinGecko
from .dune import Dune
from .gaps import detect as detect_gaps
from .hypercore import HyperCoreInfo
from .llama import DefiLlama
from .schedule import Schedule
from .tron import TronNode
from .scrape import Scrape, entry_ready, load_registry
from .validate import (REASON_CHANGE, check_cross_checks, check_impossible_relations,
                       check_level_breaks, check_reference_values, validate_frame)

log = logging.getLogger("token_metrics.fetch")

TIER_ORDER = [
    ("schedule:config", 1, lambda ctx: Schedule()),
    ("defillama", 1, lambda ctx: DefiLlama(known_absent=ctx["known_absent"])),
    ("coingecko", 1, lambda ctx: CoinGecko(known_absent=ctx["known_absent"])),
    ("hypercore_info", 1, lambda ctx: HyperCoreInfo(prior_values=ctx["prior_values"], prior_dates=ctx["prior_dates"],
                                                    prior_delta=ctx["prior_delta"])),
    ("chain", 2, lambda ctx: Chain(prior_values=ctx["prior_values"], prior_dates=ctx["prior_dates"],
                                   prior_sources=ctx["prior_sources"],
                                   prior_delta=ctx["prior_delta"])),
    ("tron_node", 2, lambda ctx: TronNode(prior_values=ctx["prior_values"], prior_dates=ctx["prior_dates"],
                                          prior_delta=ctx["prior_delta"])),
    ("scrape", 3, lambda ctx: Scrape(prior_values=ctx["prior_values"], prior_dates=ctx["prior_dates"],
                                     prior_delta=ctx["prior_delta"])),
    ("dune", 4, lambda ctx: Dune(has_history=ctx["has_history"], last_dates=ctx["last_dates"])),
]


def _resolve_tier_collisions(out: FetchOutput) -> None:
    """Stop a later tier silently overwriting an earlier one for the same figure.

    Frames are concatenated in tier order and the store upserts on (date, project, metric), so
    without this the LAST writer wins — meaning a tier 3 page scrape would overwrite a verified
    tier 2 contract read, and the verified address would never reach the sheet.

    Two sources for one figure is usually a mistake in the registry: the deliberate case is a
    cross-check, which is stored under its own metric name and never collides. So a collision is
    resolved in favour of the tier that ran FIRST (contract before page) and flagged to the
    Review Queue, because one of the two entries is pointing at the wrong metric.
    """
    frame = out.frame()
    if frame.empty:
        return
    key = ["date", "project", "metric"]
    dupes = frame[frame.duplicated(subset=key, keep=False)]
    if dupes.empty:
        return
    # only a collision ACROSS tiers is a problem; within one adapter the last value is intended
    clashing = (dupes.groupby(key)["tier"].nunique() > 1)
    clashing = set(clashing[clashing].index)
    if not clashing:
        return

    for (date, project, metric), group in dupes.groupby(key):
        if (date, project, metric) not in clashing:
            continue
        ordered = group.sort_index()
        kept, dropped = ordered.iloc[0], ordered.iloc[1:]
        for _, row in dropped.iterrows():
            log.warning("tier collision on %s/%s: keeping tier %s (%s), dropping tier %s (%s)",
                        project, metric, kept["tier"], kept["source"], row["tier"], row["source"])
            out.review_item(project, metric, "tier_collision", "rejected",
                            value=row["value"], prior_value=kept["value"], date=date,
                            source=f"dropped {row['source']} (tier {row['tier']}) in favour of "
                                   f"{kept['source']} (tier {kept['tier']})",
                            tier=int(row["tier"]) if pd.notna(row["tier"]) else None)
            out.gap(project, metric,
                    reason=f"two sources wrote the same figure: {kept['source']} (tier {kept['tier']}) and "
                           f"{row['source']} (tier {row['tier']}). The earlier tier was kept.",
                    tiers_attempted=f"{kept['tier']},{row['tier']}",
                    suggestion="One of these is pointing at the wrong metric. A deliberate second source should "
                               "be a cross-check stored under its own metric name (see cross_checks in config.py), "
                               "not written over the primary.")

    # ===== A ROW THAT LOSES A COLLISION MUST NOT ALSO BE THRESHOLD-CHECKED. Added 2026-09-23. =====
    # validate_frame runs per TIER, inside the fetch loop, and collisions are only resolved here
    # afterwards — so the losing row has already been compared against the stored series and
    # flagged. Aethir's rejected chain read (3.45bn against CoinGecko's 42bn) arrived in the
    # Review Queue as a -92% change_threshold, which reads as "this series collapsed" when what
    # actually happened is "two sources disagree and we kept the other one".
    #
    # That is doubly wrong: the number is not going to be stored, so a flag about how much it
    # moved is a flag about a value nobody will see — and it is filed under the wrong reason, so
    # the real finding (the disagreement, already recorded as tier_collision above) gets one row
    # while the artefact gets another beside it.
    #
    # REMOVED HERE RATHER THAN PREVENTED EARLIER, because this is the first point at which the
    # collision is known. The tier_collision row itself stays: that IS the finding.
    losers = {(row["project"], row["metric"], str(row["date"])[:10], int(row["tier"]))
              for (date, project, metric), group in dupes.groupby(key)
              if (date, project, metric) in clashing
              for _, row in group.sort_index().iloc[1:].iterrows()
              if pd.notna(row["tier"])}
    before = len(out.review)
    out.review = [r for r in out.review
                  if not (r["reason"] == REASON_CHANGE
                          and (r["project"], r["metric"], r["date"], r["tier"]) in losers)]
    if len(out.review) < before:
        log.info("dropped %d change_threshold flag(s) on rows that lost a tier collision — the "
                 "disagreement is recorded as tier_collision, which is the actual finding",
                 before - len(out.review))

    deduped = frame.drop_duplicates(subset=key, keep="first")
    out.frames = [deduped]


def _resolve_period_overlaps(out: FetchOutput) -> None:
    """Stop two tiers double counting the same FLOW over the same month.

    The collision guard above catches two sources writing the same DATE. A period aggregate and a
    running delta never share a date, and still double count: GEODNET's Dune backfill writes one
    monthly total dated at the month start, while the tier 2 contract read writes a delta on each
    run day. Both inside a 90-day window sums the month twice, in gross burn — the headline
    figure of the whole exercise — and nothing about the result looks wrong.

    Resolved the same way as a same-date collision, and for the same reason: the earlier tier is
    the verified primary, the later tier is backfill for periods the primary does not cover. So
    the later tier's rows are dropped for MONTHS THE EARLIER TIER ALREADY COVERS, never for the
    months it does not — a backfill still fills the history in front of the contract read.
    """
    frame = out.frame()
    if frame.empty:
        return
    flows = {m for m, spec in config.METRICS.items() if spec.get("kind") == "flow"}
    mask = frame["metric"].isin(flows) & frame["tier"].notna()
    if not mask.any():
        return
    f = frame[mask].assign(_month=frame.loc[mask, "date"].dt.to_period("M"))

    drop = []
    for (project, metric, month), g in f.groupby(["project", "metric", "_month"], sort=True):
        tiers = sorted({int(t) for t in g["tier"]})
        if len(tiers) < 2:
            continue
        keep_tier, losers = tiers[0], g[g["tier"].astype(int) != tiers[0]]
        drop.extend(losers.index.tolist())
        kept_total = float(g[g["tier"].astype(int) == keep_tier]["value"].sum())
        for lose_tier in tiers[1:]:
            lost = losers[losers["tier"].astype(int) == lose_tier]
            log.warning("period overlap on %s/%s in %s: keeping tier %d, dropping %d row(s) from tier %d",
                        project, metric, month, keep_tier, len(lost), lose_tier)
            out.review_item(project, metric, "period_overlap", "rejected",
                            value=float(lost["value"].sum()), prior_value=kept_total,
                            date=str(month), source=f"dropped {len(lost)} tier {lose_tier} row(s) covering "
                                                    f"{month}, already covered by tier {keep_tier}",
                            tier=lose_tier)
        out.gap(project, f"[data] {metric} double counted in {month}",
                reason=f"tier {keep_tier} and tier {', '.join(str(t) for t in tiers[1:])} both reported "
                       f"{metric} for {month} on different dates. A flow counted twice inside the trailing "
                       f"window overstates it, so the later tier's rows for that month were dropped.",
                tiers_attempted=", ".join(str(t) for t in tiers),
                suggestion=f"Expected where a tier 4 backfill reaches into months the live read already "
                           f"covers — nothing to fix, the overlap is removed. Investigate only if tier "
                           f"{keep_tier} is NOT the right source for {metric} in {month}.")
    if drop:
        out.frames = [frame.drop(index=drop)]


# How a burn mechanism relates issuance to the change in total supply. This is the whole reason
# issuance is derived per-model rather than with one formula: the two models give DIFFERENT
# answers from identical inputs, and picking the wrong one is not a small error — it is the burn
# counted twice, or not at all.
#
#   PROTOCOL BURN destroys supply, so totalSupply falls by the burn:
#       d(totalSupply) = issuance - burn    =>   issuance = d(totalSupply) + burn
#
#   TRANSFER BURN moves tokens to an address nobody controls. They still EXIST, and ERC-20
#   totalSupply still counts them, so the burn never appears in the supply change at all:
#       d(totalSupply) = issuance           =>   issuance = d(totalSupply)
#   Adding burn here would invent issuance that never happened. The dead-address balance is
#   subtracted separately, where effective/economic float is what is wanted.
#   NO BURN at all: nothing is destroyed, so the supply change is issuance outright.
#       d(totalSupply) = issuance
#   Declared, never inferred. A project with no burn address in config might have no burn, or
#   might have one nobody has found yet — and those produce the same empty config.
# MOVED TO config.issuance_supply_rule, 2026-09-21, AND THE MOVE IS THE FIX.
#
# The table used to live here and key on the burn mechanism alone, with
# transfer_to_dead_address -> "delta_only" on the reasoning that a transfer burn does not reduce
# the contract's totalSupply. True of the CONTRACT's figure; false of the PROVIDER's, and the
# provider's is what the store holds: CoinGecko's total_supply for these tokens is contract
# totalSupply MINUS the dead-address balance, confirmed to the token on GEODNET and within read
# timing on Uniswap. Differencing a net-of-burn figure and calling the result gross understates
# issuance by exactly the burn — which for a non-minting token is roughly -burn, and is how
# Uniswap came to report -242,000.
#
# The formula follows the convention of the SUPPLY FIGURE, not the burn mechanism. See
# config.issuance_supply_rule and the per-project total_supply_convention fields.
ISSUANCE_FROM_SUPPLY_DELTA = config.ISSUANCE_FROM_SUPPLY_DELTA_BY_MECHANISM


REASON_RATIO_FELL = "accrued_rate_fell"
# Same "derived:" prefix the issuance derivation uses, so the source string says at a glance
# that this figure was computed here rather than read from anywhere.
SOURCE_DERIVED = "derived"


def _derive_lock_ratio(out: FetchOutput, projects: list[dict], prior_values: dict) -> None:
    """Assets per share for a COMPOUNDING stake, and a flag on the ONE direction that matters.

    Ether.fi's sETHFI compounds — settled on-chain 2026-09-14 at block 25,982,077, where
    89,748,241.267610 shares claimed 111,163,214.703019 ETHFI. So a gap between the share
    figure and the asset figure is EXPECTED AND INFORMATIVE, which is the opposite of Chainlink's
    cross-check, where the two readings should agree and a gap is the finding.

    That inverts what a check should do. Flagging divergence would flag healthy accrual on every
    single run and train the reader to ignore the Review Queue. What carries information is the
    DIRECTION: a rising ratio is rewards accruing, and a FALLING one means rewards stopped or
    holders are exiting at a discount. The LEVEL is never the finding — 1.24 is not "too high",
    and neither would 3.0 be — so nothing here compares against a threshold.

    Config-driven rather than hardcoded to Ether.fi: a project declares `lock_ratio` naming its
    numerator, denominator and output metric, or gets no ratio at all.
    """
    frame = out.frame()
    if frame.empty:
        return
    latest = {(r.project, r.metric): (r.value, r.date)
              for r in frame.sort_values("date")[["project", "metric", "value", "date"]]
                            .itertuples(index=False)}

    for p in projects:
        spec = p.get("lock_ratio")
        if not spec:
            continue
        name = p["name"]
        num = latest.get((name, spec["numerator"]))
        den = latest.get((name, spec["denominator"]))
        if num is None or den is None:
            # ** "ABSENT" WAS THE WRONG WORD, AND IT SENT THE READER TO THE WRONG PLACE. **
            # A ratio from one number is not a ratio, so skipping is right — but the reason
            # mattered: Ether.fi's lock_assets_per_share has NEVER produced a value, and the
            # message said its denominator was absent. It is not absent. locked_tokens has 
            # rows in the store; they are a tier-4 Dune series, which runs as a BACKFILL and is
            # skipped once history exists, so it never appears in a run's frame and this
            # derivation never sees it. Not "did not arrive this run" — structurally cannot,
            # on every run, for as long as that source stays backfill-only.
            #
            # The distinction is exactly the one prior_values can make, so it is made here
            # rather than described in a comment nobody reads at 9am.
            missing = spec["numerator"] if num is None else spec["denominator"]
            side = "numerator" if num is None else "denominator"
            in_store = prior_values.get((name, missing)) is not None
            if in_store:
                why = (f"the store HOLDS {missing}, but nothing refreshed it this run — a "
                       f"backfill-only source (tier 4 Dune) is skipped once history exists, so "
                       f"this ratio cannot fire on any ordinary run. NOT a missing series: see "
                       f"the stale flag on {missing} itself. Deriving from the stored value "
                       f"instead would divide a fresh {spec['numerator']} by a frozen "
                       f"{spec['denominator']}, and this check reads DIRECTION — the drift would "
                       f"be the numerator's alone and would look exactly like real accrual")
            else:
                why = f"{missing} has no value in the store either — it has never been fetched"
            out.skipped(SOURCE_DERIVED, name,
                        f"{spec['metric']}: needs both {spec['numerator']} and "
                        f"{spec['denominator']} in the same run; {side} {missing} is not in this "
                        f"run's frame. {why}",
                        tier=2)
            continue
        if not den[0]:
            out.skipped(SOURCE_DERIVED, name,
                        f"{spec['metric']}: denominator {spec['denominator']} is zero — no ratio",
                        tier=2)
            continue

        ratio = float(num[0]) / float(den[0])
        when = max(num[1], den[1])
        out.add(point(name, spec["metric"], ratio, f"{SOURCE_DERIVED}:ratio", 2, when),
                SOURCE_DERIVED, name,
                f"{spec['metric']}={ratio:.8f} ({spec['numerator']} / {spec['denominator']})", 2)

        # THE ONLY FLAG, AND ONLY IN ONE DIRECTION.
        prior = prior_values.get((name, spec["metric"]))
        if prior is None or prior <= 0:
            continue
        tol = float(spec.get("decrease_tolerance", 0.0))
        fall = (prior - ratio) / prior
        if fall > tol:
            out.review_item(name, spec["metric"], REASON_RATIO_FELL, "stored_flagged",
                            value=ratio, prior_value=prior, date=when,
                            source=f"{SOURCE_DERIVED}:ratio", tier=2)


def _derive_buyback(out: FetchOutput, projects: list[dict]) -> None:
    """actual_buyback_tokens where the destination makes it a re-labelling, and its USD twin.

    TWO DERIVATIONS, BOTH OF WHICH WERE BEING ASKED FOR AS SOURCES.

    (1) A BURN-DESTINATION BUYBACK IS THE BURN. Where a protocol buys its token and destroys it,
    the buyback flow and the burn flow are ONE EVENT under two names. It was gapping as
    "no buyback_fund_balance contract", which is the wrong instruction — there is no fund,
    because the tokens no longer exist. Taken from gross_burn_tokens rather than sourced again:
    two reads of one event can disagree, and then the sheet shows a protocol that burned more
    than it bought.

    (2) THE USD FIGURE IS THE TOKEN FIGURE PRICED. actual_buyback_usd had ten gaps asking for a
    source. It is not a separate observation — a buyback is one event with a token amount and a
    price — so sourcing it separately invites a USD figure that disagrees with its own tokens.

    ** A SOURCED SERIES ALWAYS WINS, AND THE DERIVATION IS SKIPPED RATHER THAN RANKED. ** GEODNET
    publishes both legs through Dune 8683175. Emitting a derived row beside a measured one would
    put the collision guard in charge of which survives, which is a rule about tiers, not about
    evidence. Where the frame already holds the metric, nothing is derived and the skip says so.

    PRICED ON THE FLOW'S OWN DATE, not on today's price. A burn that happened in July valued at
    September's price is not what was spent, and for a monthly series the error compounds across
    the whole window.
    """
    frame = out.frame()
    if frame.empty:
        return
    have = set(map(tuple, frame[["project", "metric"]].drop_duplicates().to_numpy()))
    price_on = {(r.project, str(r.date)[:10]): r.value
                for r in frame[frame.metric == "price_usd"].itertuples(index=False)}
    latest_price = {}
    for r in frame[frame.metric == "price_usd"].sort_values("date").itertuples(index=False):
        latest_price[r.project] = (r.value, str(r.date)[:10])

    for p in projects:
        name = p["name"]
        route = config.buyback_route(name)

        # ===== A DECLARED SOURCE OWNS THE COLUMN, WHATEVER RAN TODAY. Fixed 2026-09-22. =====
        # The `have` test below asks what is in THIS RUN's frame, and tier 4 is a backfill that
        # does not run every day. So on the days GEODNET's Dune query did not run, the derivation
        # wrote actual_buyback_usd from tokens x price into a column that already had a measured
        # series — and the two sources alternating read as MEASURING_POINT_CHANGED, which blanked
        # the column. Whether a source exists is a fact about config, not about which tiers
        # happened to run this morning, so it is asked of config.
        sourced = {m for m in ("actual_buyback_tokens", "actual_buyback_usd")
                   if config.dune_query_declared(name, m)}
        for m in sorted(sourced):
            out.skipped(SOURCE_DERIVED, name,
                        f"{m}: NOT derived — Dune query "
                        f"{config.dune_query_declared(name, m)} sources this column. A derivation "
                        f"beside a measurement is a second measuring point, and the two "
                        f"alternating blank the series.", tier=2)
        # NO AUTOMATIC CROSS-CHECK AGAINST IT, and that is a refusal rather than an omission.
        # GEODNET's sourced figure is MONTHLY and priced per transaction inside the query; the
        # derivation would price a month's tokens at one day's price. The two disagreeing would
        # say nothing about either, so a comparison here would generate a flag a human has to
        # dismiss every month — which is how real flags stop being read.

        # (1) THE BURN ROUTE.
        if (route["route"] == "burn" and "actual_buyback_tokens" not in sourced
                and "actual_buyback_tokens" not in {m for n, m in have if n == name}):
            src = frame[(frame.project == name) & (frame.metric == route["metric"])]
            if src.empty:
                out.skipped(SOURCE_DERIVED, name,
                            f"actual_buyback_tokens: the buyback burns, so it equals "
                            f"{route['metric']} — which produced nothing this run, so there is "
                            f"nothing to re-label. Not a separate gap: see {route['metric']}.",
                            tier=2)
            else:
                rows = src.copy()
                rows["metric"] = "actual_buyback_tokens"
                rows["source"] = rows["source"].astype(str).map(
                    lambda s: config.mark_source(s, "as-buyback"))
                out.add(rows, SOURCE_DERIVED, name,
                        f"actual_buyback_tokens = {route['metric']} ({len(rows)} row(s)) — one "
                        f"event, two names: the bought tokens are the burned tokens", 2)
                have |= {(name, "actual_buyback_tokens")}

        # (2) THE USD TWIN, for whatever token series now exists.
        if ("actual_buyback_usd" in sourced
                or "actual_buyback_usd" in {m for n, m in have if n == name}
                or "actual_buyback_tokens" not in {m for n, m in have if n == name}):
            continue
        toks = out.frame()
        toks = toks[(toks.project == name) & (toks.metric == "actual_buyback_tokens")]
        if toks.empty:
            continue
        priced, unpriced = [], []
        for r in toks.itertuples(index=False):
            day = str(r.date)[:10]
            px = price_on.get((name, day))
            if px is None:
                unpriced.append(day)
                continue
            priced.append((r.date, float(r.value) * float(px)))
        if unpriced:
            # NOT PRICED AT TODAY'S PRICE. A July burn valued in September is not what was spent,
            # and on a monthly series the error compounds across the whole window. Saying which
            # dates could not be priced is the honest answer.
            out.skipped(SOURCE_DERIVED, name,
                        f"actual_buyback_usd: {len(unpriced)} of {len(toks)} buyback row(s) have "
                        f"no price_usd on their own date ({', '.join(unpriced[:5])}"
                        f"{'...' if len(unpriced) > 5 else ''}). Valuing them at the latest price "
                        f"would report what they would cost today, not what was spent.", tier=2)
        if priced:
            out.add(pd.concat([point(name, "actual_buyback_usd", v, f"{SOURCE_DERIVED}:tokens*price", 2, d)
                               for d, v in priced], ignore_index=True),
                    SOURCE_DERIVED, name,
                    f"actual_buyback_usd = actual_buyback_tokens x price_usd on each flow's own "
                    f"date ({len(priced)} row(s))", 2)


def _derive_issuance(out: FetchOutput, projects: list[dict], prior_values: dict, prior_dates: dict) -> None:
    """Derive gross_issuance_tokens from the supply change, keyed on the burn mechanism.

    Issuance is DERIVED, not fetched, wherever the store already holds what it needs. Every
    project has total_supply from tier 1, so this costs no call and no paid query — where a burn
    figure is also needed, it is the one already computed this run.

    Three things it refuses to do, each of which would produce a plausible wrong number:
      * derive from a single observation. A supply delta needs two dated readings, exactly like
        a burn flow — see derive_flow_from_cumulative. One reading gives 0, which reads as "no
        issuance" on a chain that mints every block.
      * derive under an unestablished mechanism. Sky swaps on an AMM and sends proceeds to a
        configurable receiver: until the receiver's behaviour is known, its supply effect is
        unknown, so no formula applies and none is guessed.
      * overwrite a measured figure. A real source beats a derivation, always.
    """
    frame = out.frame()
    have = set()
    if not frame.empty:
        have = {(r.project, r.metric) for r in frame[["project", "metric"]].itertuples(index=False)}

    supply = {}
    burn = {}
    gross = {}
    if not frame.empty:
        wanted = ("total_supply", "gross_burn_tokens", "total_supply_gross")
        for row in frame[frame["metric"].isin(wanted)].itertuples(index=False):
            bucket = {"total_supply": supply, "gross_burn_tokens": burn,
                      "total_supply_gross": gross}[row.metric]
            bucket[row.project] = (row.value, row.date)

    for p in projects:
        name = p["name"]
        if (name, "gross_issuance_tokens") in have:
            continue                       # a measured figure is already here; never overwrite it
        if "gross_issuance_tokens" not in config.metrics_for_project(p):
            continue

        # SUPPRESSED BY CONFIG, because the supply delta is the WRONG SHAPE for this project's
        # issuance — not merely imprecise. GEODNET emits per-miner on a halving schedule, so no
        # sampling interval recovers it from a supply difference. Left alone the derivation returns
        # a plausible zero, and a false zero is worse than a gap: it renders as measured, feeds the
        # burn/issuance ratio as a denominator, and carries a confidence band it has not earned.
        supp = p.get("issuance_derivation") or {}
        if supp.get("suppressed"):
            out.gap(name, "gross_issuance_tokens",
                    reason=("the supply-delta derivation is SUPPRESSED for this project: "
                            + supp.get("why", "no reason recorded in config.")),
                    tiers_attempted="1, 2",
                    suggestion=supp.get("resolves_when", "see this project's config entry."))
            continue

        # THE PROVIDER IS SERVING THE CAP. Refused before the mechanism is even looked at, and
        # with its own reason rather than the generic one — "the burn mechanism's supply effect
        # is not established" would send the reader to settle a question that is already settled
        # and is not the problem. The problem is that the input does not move.
        cap = config.supply_denominator_unusable(name)
        if cap:
            out.gap(name, "gross_issuance_tokens",
                    reason=f"cannot be derived: {cap}",
                    tiers_attempted="1, 2",
                    suggestion=("Sum this project's token deployments into total_supply_gross — a "
                                "contract's own totalSupply() is the minted amount and moves as it "
                                "mints. Until then neither figure exists, which is correct: a "
                                "derived 0 would render as measured and feed every downstream "
                                "ratio as though nothing were being issued."))
            continue

        mech = config.burn_mechanism(p)
        rule = config.issuance_supply_rule(p, mech.get("model"))
        # A TRANSFER BURN WITH AN UNDECLARED SUPPLY CONVENTION REFUSES, and says which test to
        # run. The two candidate formulas differ by the entire burn, so there is no safe default
        # — picking one is picking a number that is either right or wrong by 100% of the burn.
        if (rule is None and mech.get("model") == "transfer_to_dead_address"
                and mech.get("status") != "refuted"):
            out.gap(name, "gross_issuance_tokens",
                    reason=("cannot be derived: this project burns by TRANSFER to a dead address, and "
                            "whether the stored total_supply is NET of that burn is not established. "
                            "CoinGecko's total_supply is contract totalSupply minus the dead-address "
                            "balance for every project tested so far (GEODNET exactly, Uniswap within "
                            "read timing), and the two readings imply formulas that differ by the "
                            "ENTIRE burn: d(supply) under a gross figure, d(supply)+burn under a net "
                            "one. Deriving without knowing which would produce a figure wrong by 100% "
                            "of the burn, in the direction that looks like negative issuance."),
                    tiers_attempted="1, 2",
                    suggestion=("Run the test recorded in this project's total_supply_convention_untested "
                                "block: contract totalSupply minus the provider's total_supply, compared "
                                "against burn_address_balance. Equal means net_of_burn; a zero difference "
                                "means gross. Then set total_supply_convention in config.py and the "
                                "derivation follows automatically."))
            continue
        if rule is None or mech.get("status") == "refuted":
            out.gap(name, "gross_issuance_tokens",
                    reason=f"cannot be derived: the burn mechanism is {mech.get('model')!r} "
                           f"(status {mech.get('status')!r}), and how that mechanism affects total supply "
                           f"is not established. A protocol burn reduces totalSupply and a transfer burn "
                           f"does not, so the two need different arithmetic — and a mechanism that is "
                           f"neither has no formula at all until its supply effect is known.",
                    tiers_attempted="1, 2",
                    suggestion="Settle the mechanism (see this project's OPEN_QUESTIONS), then declare "
                               "burn_mechanism.model in config.py. The derivation follows automatically.")
            continue

        now = supply.get(name)
        if now is None:
            continue                       # no supply read this run; the metric's own gap covers it
        value, when = now
        prior = prior_values.get((name, "total_supply"))
        prior_date = prior_dates.get((name, "total_supply"))
        if prior is None or (prior_date and str(prior_date)[:10] >= str(when)[:10]):
            out.gap(name, "gross_issuance_tokens",
                    reason=("cannot be derived yet: it is the CHANGE in total supply, and the store holds "
                            "only one observation of total supply" + (f" (dated {str(prior_date)[:10]})"
                                                                      if prior_date else "") +
                            ". A delta needs two dated readings. Deliberately not reported as 0, which on "
                            "a chain that mints every block would be a plausible and entirely wrong number."),
                    tiers_attempted="1, 2",
                    suggestion="It resolves itself on the next run on a later day. AND IT WILL NEVER "
                               "BACKFILL: CoinGecko serves total_supply as a current value only, and "
                               "/coins/{id}/history was confirmed on a live call (2026-09-14) to return "
                               "current_price, market_cap and total_volume and NO supply at all. So the "
                               "series accumulates from first run and there is no route to history for "
                               "any project without a live issuance endpoint or a declared schedule. "
                               "Settled — see RUNBOOK, not a question to re-ask.")
            continue

        # ===== ONE SOURCE, ONE READ, NO DRIFT. Added 2026-09-22. =====
        # d(total_supply_gross) IS gross issuance for a transfer burn, directly: the contract's
        # totalSupply counts the tokens at the dead address, so a burn does not move it and only
        # minting does. No burn term, no second source.
        #
        # THE NET+BURN ROUTE IS EXPOSED TO READ-TIMING DRIFT between two providers sampled at
        # different moments, and it showed: Uniswap derived 219,999.99 of issuance on a run where
        # total_supply_gross was EXACTLY 1,000,000,000 both times. UNI minted nothing. The figure
        # was the gap between CoinGecko's net number and the chain's burn delta, not a mint.
        #
        # The net+burn figure is still computed, and disagreement between the two is raised to the
        # Review Queue rather than discarded — that is the cross-check, and it costs nothing
        # because both inputs are already in hand.
        gross_now, gross_prior = gross.get(name), prior_values.get((name, "total_supply_gross"))
        if gross_now is not None and gross_prior is not None and rule == "add_burn":
            gross_delta = gross_now[0] - gross_prior
            crosscheck = None
            got = burn.get(name)
            if got is not None:
                crosscheck = (value - prior) + got[0]
                # A real mint shows in both. A divergence is read timing between two providers,
                # and it is worth seeing rather than silently preferring one.
                if abs(crosscheck - gross_delta) > max(1.0, abs(gross_delta) * 0.01):
                    out.review_item(name, "gross_issuance_tokens", "issuance_route_divergence",
                                    "stored_flagged", value=float(gross_delta),
                                    prior_value=float(crosscheck), date=gross_now[1],
                                    source="derived:d_supply_gross vs derived:d_supply+burn", tier=2)
            if gross_delta < 0:
                out.review_item(name, "gross_issuance_tokens", "negative_derived_issuance",
                                "rejected", value=float(gross_delta), prior_value=float(gross_prior),
                                date=gross_now[1], source="derived:d_supply_gross", tier=2)
                out.gap(name, "gross_issuance_tokens",
                        reason=(f"the gross-supply delta came out NEGATIVE ({gross_delta:,.4f}) and was "
                                f"rejected. A contract's totalSupply cannot fall on a transfer burn, so "
                                f"this is a read fault, not a supply event."),
                        tiers_attempted="1, 2",
                        suggestion="Check the two total_supply_gross readings in the store.")
                continue
            out.add(point(name, "gross_issuance_tokens", float(gross_delta),
                          "derived:d_supply_gross", 2, gross_now[1]),
                    SOURCE_DERIVED, name,
                    f"gross_issuance_tokens={gross_delta:,.4f} from d(total_supply_gross)"
                    + (f"; net+burn route says {crosscheck:,.4f}" if crosscheck is not None else ""),
                    2)
            continue

        delta = value - prior
        if rule == "add_burn":
            got = burn.get(name)
            if got is None:
                # WHY the burn has to be added back differs by project, and saying the wrong one
                # sends the reader to check the wrong thing: a protocol burn destroys supply at
                # the contract, while a transfer burn leaves the contract alone and the PROVIDER
                # subtracts it. Both end at the same formula and at different explanations.
                why_net = ("the stored total_supply is NET OF BURN (see this project's "
                           "total_supply_convention) — the provider subtracts the dead-address "
                           "balance, so the burn has to be added back"
                           if p.get("total_supply_convention") == "net_of_burn" else
                           "this project's burn DESTROYS supply at the contract")
                out.gap(name, "gross_issuance_tokens",
                        reason=(f"cannot be derived: {why_net}, so issuance is the supply change PLUS "
                                f"the burn — and no burn figure was produced this run. Deriving from "
                                f"the supply delta alone would report issuance NET of burn while "
                                f"labelling it gross, understating it by exactly the burn."),
                        tiers_attempted="1, 2",
                        suggestion="Source gross_burn_tokens for this project first; issuance follows from "
                                   "it automatically. Until then neither figure exists, which is correct.")
                continue
            basis = ("provider supply is net of burn" if p.get("total_supply_convention") == "net_of_burn"
                     else "protocol burn reduces supply")
            issued, how = delta + got[0], f"d(total_supply)={delta:,.4f} + burn={got[0]:,.4f} ({basis})"
        else:
            issued, how = delta, f"d(total_supply)={delta:,.4f} (supply figure is gross of burn)"

        if issued < 0:
            out.review_item(name, "gross_issuance_tokens", "negative_derived_issuance", "rejected",
                            value=issued, prior_value=prior, date=when,
                            source=f"derived:{mech.get('model')}", tier=2)
            out.gap(name, "gross_issuance_tokens",
                    reason=f"the derivation came out NEGATIVE ({issued:,.4f}) and was rejected. Issuance "
                           f"cannot be below zero, so one of its inputs is wrong: {how}. The likeliest "
                           f"causes are a supply figure that was restated between observations, or a burn "
                           f"that belongs to a different period than the supply delta.",
                    tiers_attempted="1, 2",
                    suggestion="Check total_supply's last two observations on the Data tab, and whether "
                               "the burn figure covers the same interval.")
            continue

        src = f"derived:{'d_supply+burn' if rule == 'add_burn' else 'd_supply'}"
        if mech.get("status") == "assumed":
            src += ":MECHANISM_ASSUMED"
            out.review_item(name, "gross_issuance_tokens", "derived_on_assumed_mechanism", "stored_flagged",
                            value=issued, prior_value=None, date=when, source=src, tier=2)
        out.add(point(name, "gross_issuance_tokens", issued, src, 2, when), "derive", name,
                f"gross_issuance_tokens={issued:,.4f} from {how}", 2)


def fetch_all(projects: list[dict], window_days: int | None, *,
              prior_values: dict | None = None,
              prior_values_for_delta: dict | None = None,
              prior_dates: dict | None = None,
              prior_sources: dict | None = None,
              has_history: set | None = None,
              known_absent: set | None = None,
              last_dates: dict | None = None,
              manual_keys: set | None = None,
              stored_long=None,
              sources: list[str] | None = None) -> FetchOutput:
    """Run every tier in order and return one FetchOutput carrying frames, log, review and gaps.

    prior_values  (project, metric) -> last stored value. Feeds the change-threshold check and
                  the cumulative-to-flow differencing in tiers 2 and 3.
    prior_dates   (project, metric) -> the DATE that value was observed on. A differenced flow
                  needs an interval, not just a number to subtract: two readings on one date are
                  a single observation, and their difference is 0 whatever the truth is.
    has_history   (project, metric) pairs the store already has history for. Tier 4 skips these,
                  because Dune is a backfill dependency, not an ongoing one.
    last_dates    (project, metric) -> the newest stored date, for tier 4's refresh_days. A
                  backfill's freshness is a property of the SERIES, not of when the query last
                  ran: a query that executed yesterday and returned nothing new refreshed nothing.
    known_absent  (source, project) pairs whose endpoint 404'd and has never worked — see
                  store.known_absent. Not called at all, and logged as SKIPPED rather than
                  failed: no call was made, so there is no error to report.
    manual_keys   (project, metric) pairs covered by manual_overrides.csv, so the Gap Report
                  does not list something Jake has already entered by hand.
    """
    # prior_values is the newest figure of ANY date, for the change-threshold check.
    # prior_values_for_delta is the last EARLIER-DATED figure, for differencing — see
    # store.values_before. Defaulting the second to the first keeps older callers working.
    ctx = {"prior_values": prior_values or {},
           "prior_delta": prior_values_for_delta if prior_values_for_delta is not None else (prior_values or {}),
           "prior_dates": prior_dates or {},
           "prior_sources": prior_sources or {},
           "has_history": has_history or set(),
           "known_absent": known_absent or set(),
           "last_dates": last_dates or {}}
    out = FetchOutput()

    for name, tier, build in TIER_ORDER:
        if sources and name not in sources:
            continue
        log.info("tier %d — %s (window=%s)", tier, name, window_days or "full history")
        before = len(out.frames)
        # WALL CLOCK PER SOURCE, recorded whether it succeeds, crashes or does nothing. A source
        # that takes two minutes and returns nothing is the single most useful line in a slow
        # run's log, and it is exactly the one a rows-only summary cannot show.
        t0 = time.monotonic()
        try:
            build(ctx).run(projects, window_days, out)
        except Exception as e:  # noqa: BLE001 — never let one source kill the run
            out.fail(name, None, f"adapter crashed: {e}", tier)
        finally:
            out.timings.append({"source": name, "tier": tier,
                                "seconds": time.monotonic() - t0,
                                "frames": len(out.frames) - before})
        # validate each tier's own output, so a rejection names the tier that produced it
        for i in range(before, len(out.frames)):
            out.frames[i] = validate_frame(out.frames[i], ctx["prior_values"], out)
        out.frames = [f for f in out.frames if f is not None and not f.empty]

    _resolve_tier_collisions(out)
    _resolve_period_overlaps(out)
    # AFTER the collision guards, so a derivation can never displace a measured figure, and so the
    # burn it consumes is the deduped one rather than a double-counted month.
    _derive_issuance(out, projects, ctx["prior_values"], ctx["prior_dates"])
    # AFTER issuance, so both lock figures are certainly in the frame by now.
    _derive_lock_ratio(out, projects, ctx["prior_values"])
    # AFTER the burn derivation above, so a burn-destination buyback re-labels the deduped burn
    # rather than a figure that is about to be superseded.
    _derive_buyback(out, projects)
    check_reference_values(out.frame(), out)
    check_cross_checks(out.frame(), out)
    check_impossible_relations(out.frame(), out)

    # ===== THE LEVEL CHECK READS THE STORE, NOT THIS RUN. Added 2026-09-23. =====
    # Every other check here works on what just arrived, which is exactly why none of them could
    # see Morpho's break: the shape of the last month is not in a run's frame. The store's rows
    # are concatenated with this run's so today's points are included — a break that happens
    # today is caught today, not tomorrow.
    if stored_long is not None and not getattr(stored_long, "empty", True):
        fresh = out.frame()
        cols = [c for c in ("date", "project", "metric", "value", "source", "tier")
                if c in stored_long.columns]
        history = pd.concat([stored_long[cols], fresh[cols]], ignore_index=True)
        history = history.sort_values("date").drop_duplicates(
            subset=["date", "project", "metric"], keep="last")
        check_level_breaks(history, out)
    else:
        log.info("no stored history passed — the level-break check did not run. It compares a "
                 "recent median against an older one, so it has nothing to say on a first run.")

    # BOTH STATES, NOT JUST THE BROKEN ONE. This used to record only the NOT-READY entries, so a
    # registry entry that was complete and armed looked, to the gap reporter, exactly like an
    # entry that had never been written — and Maple's armed cross-check was reported as "no
    # sources.yaml entry for this metric" when there plainly was one. A ready entry that returned
    # nothing is a different problem from a missing entry and has to say so.
    registry_reasons = {}
    for e in load_registry():
        ok, why = entry_ready(e)
        if not (e.get("project") and e.get("metric")):
            continue
        registry_reasons[(e["project"], e["metric"])] = (
            {"ready": True, "url": e.get("url"), "anchor": e.get("anchor"), "method": e.get("method")}
            if ok else why)

    out.gaps = detect_gaps(projects, out.frame(), manual_keys or set(), registry_reasons, out.gaps)
    return out
