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
from .validate import (check_cross_checks, check_impossible_relations, check_reference_values,
                       validate_frame)

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
    ("dune", 4, lambda ctx: Dune(has_history=ctx["has_history"])),
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
              manual_keys: set | None = None,
              sources: list[str] | None = None) -> FetchOutput:
    """Run every tier in order and return one FetchOutput carrying frames, log, review and gaps.

    prior_values  (project, metric) -> last stored value. Feeds the change-threshold check and
                  the cumulative-to-flow differencing in tiers 2 and 3.
    prior_dates   (project, metric) -> the DATE that value was observed on. A differenced flow
                  needs an interval, not just a number to subtract: two readings on one date are
                  a single observation, and their difference is 0 whatever the truth is.
    has_history   (project, metric) pairs the store already has history for. Tier 4 skips these,
                  because Dune is a backfill dependency, not an ongoing one.
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
           "known_absent": known_absent or set()}
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
    check_reference_values(out.frame(), out)
    check_cross_checks(out.frame(), out)
    check_impossible_relations(out.frame(), out)

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
