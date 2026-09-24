"""
fetch/gaps.py — work out what could not be resolved, and say precisely why.

Anything unresolved after every tier appears in the Gap Report, not as a blank cell in the
Master tab. A silently stale cell is worse than a visible gap, and a blank one is worse still.

The Gap Report is the to-do list: each row names the project, the metric, the tiers attempted,
why it failed and what would fix it. Jake supplies the Dune link or the source page for
anything that lands there, usually by adding an entry to sources.yaml.
"""
from __future__ import annotations

import logging

import pandas as pd

import config

log = logging.getLogger("token_metrics.fetch.gaps")

# Metrics DefiLlama serves only on its Pro tier. Named explicitly so the report says "paywalled",
# not "missing" — a different problem with a different fix.
PRO_PAYWALLED = {
    "emissions_tokens": "DefiLlama emissions/unlocks is Pro tier only ($300/mo, separate API plan)",
}

# Metrics that are the point of the exercise. A headline metric with no working route at all
# outranks everything else in the Gap Report, because it is a hole in the answer rather than a
# missing nicety.
HEADLINE_METRICS = {
    "gross_burn_tokens", "burn_address_balance", "net_mint_monthly",
    "gross_issuance_tokens", "actual_buyback_tokens", "actual_buyback_usd",
    "buyback_fund_balance", "circulating_supply", "revenue_usd", "fees_usd",
}

# Priority bands, ranked by WHAT CAN BE ACTED ON, lowest number first. The top of this report
# should be a to-do list, not a census of everything that is missing.
P_CRITICAL = 1              # an open question flagged critical: a headline figure with no route at all
P_DECISION = 2              # every other open question — each names a specific action for a human
P_ACTIONABLE_HEADLINE = 3   # a headline metric blocked on something fixable: an address, a read method
P_ACTIONABLE = 4            # the same, on a non-headline metric
P_SUPPRESSED = 5            # ANSWERED, NOT OPEN — see ANSWERED_SIGNALS. Listed, never counted as open.
P_UNCOVERED = 6             # no source covers it yet, and none is configured to

PRIORITY_LABEL = {
    P_CRITICAL: "P1 critical",
    P_DECISION: "P2 decision",
    P_ACTIONABLE_HEADLINE: "P3 fixable",
    P_ACTIONABLE: "P4 fixable",
    P_SUPPRESSED: "P5 answered",
    P_UNCOVERED: "P6 uncovered",
}

# ===== A ROW WHOSE REASON IS ITS OWN CONCLUSION IS ANSWERED, NOT OPEN. 2026-09-24. =====
# Twenty-two rows on the 16-project report carried a finished answer — "checked, not tracked",
# "robots.txt disallows", "deliberately disabled", "see the row that carries it" — and were
# counted as open work beside rows that genuinely are. They stay ON the Gap Report, with their
# reasoning, under P5; gap_count counts them separately and never as open. The test is the
# reason's own words, the same mechanism as ACTIONABLE_SIGNALS, so a new row of the same kind
# classifies itself and nothing is maintained by hand.
ANSWERED_SIGNALS = (
    "deliberately disabled in sources.yaml",      # a human chose to switch the route off
    "not tracked by defillama — checked",         # searched, recorded, absent
    "robots.txt disallows",                       # the site said no; it is not worked around
    "the release is measured on this project",    # emissions: the figure is pool_release_tokens
    "emissions are minting here",                 # emissions: the figure is gross_issuance_tokens
    "answered, not open",                         # a *_blocked record marked answered by a human
)

ACTIONABLE_SIGNALS = (
    "not verified", "unverified", "ambiguous", "not established", "no address", "needs sourcing",
    "symbol mismatch", "no deployed bytecode", "returned nothing this run", "no same-chain",
    "component(s) refused", "every component was refused", "is partial",
)


def _priority(project_name: str, metric: str, reason: str, severity: int | None = None) -> int:
    if metric.startswith("[open]"):
        return P_CRITICAL if severity == 1 else P_DECISION
    if metric.startswith("[data]"):
        # a figure that arrives but cannot be trusted yet: a decision for a human, not a missing row
        return P_DECISION
    if metric.startswith("[config]"):
        return P_SUPPRESSED
    # BY DESIGN, NOT UNCOVERED. A registry entry that is disabled ON PURPOSE, with its page on
    # file and its reason in the entry's note, is a decision someone made and recorded — the same
    # category as a config-suppressed figure, and not the same as a metric nobody has looked at.
    # Filing both at P6 made the to-do list longer than the work it represents.
    if any(k in reason.lower() for k in ANSWERED_SIGNALS):
        return P_SUPPRESSED
    # SETTLED ABSENCE IS NOT AN UNCOVERED METRIC. Added 2026-09-23. "Not tracked by DefiLlama"
    # with a date and the slugs that were tried is a question someone answered — the same
    # category as a config-suppressed figure, and not the same as a metric nobody has looked at.
    # Filing it at P6 puts finished work at the bottom of the to-do list, where the next person
    # repeats the search.
    # "not tracked by defillama — checked" is in ANSWERED_SIGNALS above, with the rest.
    if any(k in reason.lower() for k in ACTIONABLE_SIGNALS):
        return P_ACTIONABLE_HEADLINE if metric in HEADLINE_METRICS else P_ACTIONABLE
    return P_UNCOVERED

NON_EVM = {"Solana", "Tron", "Bitcoin", "Zcash", "Near", "Canton", "Bittensor", "peaq", "Render", "Injective"}

# Which free API serves each tier 1 metric, and the config field it needs.
TIER1_SOURCE = {
    "price_usd": ("CoinGecko", "coingecko_id"), "market_cap_usd": ("CoinGecko", "coingecko_id"),
    "volume_usd": ("CoinGecko", "coingecko_id"), "circulating_supply": ("CoinGecko", "coingecko_id"),
    "circulating_supply_implied": ("CoinGecko", "coingecko_id"), "total_supply": ("CoinGecko", "coingecko_id"),
    "max_supply": ("CoinGecko", "coingecko_id"), "fdv_usd": ("CoinGecko", "coingecko_id"),
    "fees_usd": ("DefiLlama", "defillama_fees_slug"), "revenue_usd": ("DefiLlama", "defillama_fees_slug"),
    "holders_revenue_usd": ("DefiLlama", "defillama_fees_slug"),
    "protocol_tvl_usd": ("DefiLlama", "defillama_protocol"),
    "tvl_usd": ("DefiLlama", "defillama_chain"), "stablecoin_supply_usd": ("DefiLlama", "defillama_chain"),
    "rwa_defillama_usd": ("DefiLlama", "defillama_chain"),
}

# Contract kinds in config that can serve a tier 2 metric. A metric NOT listed here is not
# served by a contract read at all, so a contract problem is never the right explanation for it
# — saying "the token address is ambiguous" about, say, average lock duration would send the
# reader off to fix the wrong thing.
METRIC_CONTRACT_KIND = {
    # The lock-duration proxy's two inputs, so a missing one gets a specific reason rather than
    # "no source configured for this metric".
    "ve_voting_power_tokens": "ve_voting_power",
    "permanent_locked_tokens": "permanent_locked",
    "ve_locked_supply_tokens": "ve_locked_supply",
    "burn_address_balance": "burn_address_balance",
    "gross_burn_tokens": "burn_address_balance",       # derived by differencing the balance
    "buyback_fund_balance": "buyback_fund_balance",
    "actual_buyback_tokens": "buyback_fund_balance",
    "locked_tokens": "ve_total_supply",
    # THE SHARE COUNT OF A COMPOUNDING RECEIPT. Same kind as locked_tokens — it is the same
    # totalSupply() read — and it is here so a project whose lock figure is the ASSETS still gets
    # "no contract of kind 've_total_supply' declared" rather than the generic "no source
    # configured", which would send the reader to write a scraper for a balance read.
    "locked_tokens_shares": "ve_total_supply",
    # The pool's own principal accounting, read by calling getTotalPrincipal() on the pool rather
    # than the token's balance of it. Mapped so a missing one names the contract kind it needs,
    # instead of falling through to "no source configured for this metric".
    "locked_tokens_principal": "stake_principal",
    "locked_tokens_underlying": "stake_underlying",
    # WAS MISSING, and the omission produced the WRONG EXPLANATION on thirteen rows. A treasury
    # holding IS served by a contract read (kind treasury_holding), so a project without one
    # should be told to add the contract — not told "no sources.yaml entry for this metric",
    # which sends the reader off to write a scraper for a figure a balance read already covers.
    # Worse, it hid the opposite case: Near and GEODNET DO have treasury contracts on file, and
    # their rows were reporting a missing registry entry rather than the real reason the read
    # produced nothing.
    "treasury_holding_tokens": "treasury_holding",
    # A governance parameter read from the contract that enforces it, so a missing one names the
    # contract to add rather than falling through to "no source configured".
    "cooldown_days": "cooldown_duration",
    # ===== THE DECOMPOSED BURN SERIES, Sky only. Added 2026-09-22. =====
    # All three come from ONE contract of kind burn_transfer_logs, split by the event's sender.
    # Without these the gap detector reports "no source configured" for series that are read on
    # every run — a to-do list item for work already done, which is how a real gap gets lost in
    # noise. The two FLOWS are derived from their cumulatives and so have no contract of their
    # own; they are mapped to the same kind because the question the detector asks is "is there a
    # route to this figure", and there is.
    "governance_burn_balance": "burn_transfer_logs",
    "governance_burn_tokens": "burn_transfer_logs",
    "other_burn_balance": "burn_transfer_logs",
    "other_burn_tokens": "burn_transfer_logs",
    # No contract serves it — it is DERIVED from the two above. Mapped to neither kind; the
    # tier note below handles it so it cannot fall through to "no source configured".
    "total_supply": "erc20_total_supply",
    # SAME KIND, DIFFERENT METRIC. On a net_of_burn project the token contract carries
    # metric_override="total_supply_gross" (the contract counts tokens at the dead address;
    # CoinGecko does not), so the gross series is served by exactly the kind the net one used to
    # be. The override-aware filter below is what keeps the two apart.
    "total_supply_gross": "erc20_total_supply",
    "circulating_supply": "erc20_total_supply",
}


def _format_blocked(blocked: dict) -> tuple[str, str]:
    """A project's `{metric}_blocked` record as (reason, suggestion). One formatter, two callers."""
    # A human can mark a blocked record ANSWERED: the route is known to be unreachable, not
    # waiting on anything this tool could do. It then ranks P5 and leaves the open count.
    prefix = "ANSWERED, NOT OPEN — " if blocked.get("answered") else ""
    return (f"{prefix}{blocked.get('status', 'blocked')} — WANTED: {blocked.get('wanted')}. "
            f"{blocked.get('why_not_defillama') or blocked.get('why') or ''} "
            f"(established from {blocked.get('source_url')}, read "
            f"{blocked.get('source_date')})",
            blocked.get("route_that_would_work")
            or "See this project's config entry for what would answer it.")


def _buyback_gap_reason(project: dict, metric: str, scrape_entries: dict) -> tuple[str, str] | None:
    """(reason, suggestion) for actual_buyback_tokens / actual_buyback_usd, from the ROUTE.

    ** THE PRINCIPLE, SETTLED: a buyback wallet exists to SPEND, so a differenced balance is inflow
    minus spending. The measurable quantity is the INFLOW, which needs Transfer events. ** So no
    branch here ever suggests declaring a buyback_fund_balance contract to difference.

      burn / split      the buyback IS the burn (or its burn leg) — one event, two names. The row
                        points at gross_burn_tokens and carries THAT row's reason, so a reader
                        is never sent to build a second source for the same event.
      treasury_inflow   the fund is known or not; either way the route is a log scan. The
                        project's actual_buyback_tokens_blocked says which and why.
      distribute        no stock and no burn to read; needs the distributor's outflow history.
      none              no mechanism.

    usd FOLLOWS tokens: same reason, prefixed, so the two rows never disagree.
    """
    name = project["name"]
    if metric == "actual_buyback_usd":
        blocked = project.get("actual_buyback_tokens_blocked")
        if blocked:
            reason, suggestion = _format_blocked(blocked)
        else:
            inner = _buyback_gap_reason(project, "actual_buyback_tokens", scrape_entries)
            if inner is None:
                return None
            reason, suggestion = inner
        return (f"FOLLOWS actual_buyback_tokens — usd is tokens x price_usd on each flow's own "
                f"date, so it exists exactly when tokens does. Tokens: {reason}",
                f"Resolve actual_buyback_tokens; this row fills from it with no separate source. "
                f"({suggestion})")

    route = config.buyback_route(name)
    kind = route.get("route")
    if kind in ("burn", "split"):
        if kind == "burn":
            burn_metric, share_note = route.get("metric") or "gross_burn_tokens", ""
        else:
            legs = [l for l in config.stage_split_legs(name)
                    if l.get("effect") == "supply_reduction" and l.get("cross_check_metric")]
            if not legs:
                return (route["reason"], "See stage_split_legs in config.")
            burn_metric = legs[0]["cross_check_metric"]
            share_note = (f" This is the {legs[0].get('share', 0):.0%} supply-reduction leg ONLY "
                          f"(marked PARTIAL when it fills); the distributed leg has no stock or "
                          f"burn to read.")
        inner_reason, inner_suggestion = _tier_note(project, burn_metric, scrape_entries)
        # ** NEVER START A REASON WITH "=". ** It opened with "= gross_burn_tokens" until
        # 2026-09-24, and a spreadsheet stores such text as a formula: the reason rendered
        # empty on GEODNET and Sky, the only two burn/split routes.
        return (f"SAME EVENT AS {burn_metric} — the bought tokens are the burned tokens, one event under two "
                f"names, so this row fills from that one and never has its own source.{share_note} "
                f"That row's reason: {inner_reason}",
                f"Resolve {burn_metric}; this row re-labels from it. ({inner_suggestion})")
    if kind in ("treasury_inflow", "distribute", "none"):
        return (route["reason"],
                "The route is a Transfer-event scan into the receiving address (an INFLOW), never a "
                "balance read — a buyback wallet spends, so its differenced balance understates the "
                "buyback by the buyback. Where the address is unknown, that is the thing to find.")
    return None


def _tier_note(project: dict, metric: str, scrape_entries: dict) -> tuple[str, str]:
    """(reason, suggestion) for a metric this project has no data for."""
    name = project["name"]
    m = config.METRICS[metric]
    tiers = m.get("tiers", [])

    # ===== A BLOCKED ROUTE IS NOT A MISSING SOURCE. Added 2026-09-22. =====
    # Morpho's utilisation_pct was going to come from DefiLlama's protocol data until the adapter
    # was read and it turned out not to publish the denominator. "no source configured" would
    # send the next reader to do that same search again; the block says what was looked at, why
    # it does not answer, and what would.
    blocked = project.get(f"{metric}_blocked")
    if blocked:
        return _format_blocked(blocked)
    # ===== A RESTATED COLUMN GAPS BECAUSE ITS SOURCE COLUMN DID. Added 2026-09-24. =====
    # It fell through to "no sources.yaml entry", which sends the reader to build a source for a
    # column that is by declaration a copy. It points at the column it copies instead.
    restated = config.metric_restatements(name).get(metric)
    if restated:
        src = restated["equals"]
        slug = project.get("defillama_fees_slug")
        via = (f" via DefiLlama slug {slug!r}" if slug and src in ("fees_usd", "revenue_usd") else "")
        return (f"SAME SERIES AS {src} — this column is a RESTATEMENT of {src}{via}, not a separate "
                f"measurement, and {src} produced nothing this run. {restated.get('why', '')}",
                f"Resolve {src}; this row fills from it with no source of its own. Check the Run "
                f"Log for the {src} fetch{via}.")

    # ===== THE BUYBACK PAIR ANSWERS FROM ITS ROUTE, NEVER FROM "no contract of kind ...". =====
    # Added 2026-09-23. Seven projects gapped on actual_buyback_* with variants of "no contract
    # of kind 'buyback_fund_balance' declared", and for none of them was that the reason: Maple
    # HAS the fund address, Pendle's buyback has no wallet by design, GEODNET's is the burn.
    # The route already knows the answer (config.buyback_route); this makes the row say it.
    #
    # AND actual_buyback_usd NEVER GAPS ON ITS OWN. Where tokens is unavailable, usd inherits the
    # same reason and points at it; where tokens exists, usd is tokens x price on the flow's own
    # date, so a usd gap beside a tokens figure is a PRICE gap and says so.
    if metric in ("actual_buyback_tokens", "actual_buyback_usd"):
        answer = _buyback_gap_reason(project, metric, scrape_entries)
        if answer:
            return answer

    if metric in PRO_PAYWALLED:
        # ===== THE PAYWALL IS THE LAST EXPLANATION, NOT THE FIRST. Fixed 2026-09-23. =====
        # This branch returned "DefiLlama emissions/unlocks is Pro tier only" for every project,
        # and it was the wrong obstacle on nearly all of them: it sends the reader to buy a
        # $300/mo plan for a figure that either has a declared schedule already, is not minting
        # at all, or does not exist. A reason that names the wrong obstacle is worse than none,
        # because it looks actionable and somebody acts on it.
        model = config.emissions_model(name)
        if model:
            caveat = ("" if model.get("sourced") else
                      f" CLASSIFIED, NOT YET SOURCED — declared by "
                      f"{model.get('declared_by', 'review')} and not re-confirmed against "
                      f"{name}'s own material, so treat the model as the current best reading "
                      f"rather than settled.")
            note = model.get("note", "")
            if model["model"] == "minted":
                return (f"EMISSIONS ARE MINTING HERE, so this metric is the issuance route and "
                        f"not a second source: see gross_issuance_tokens. {note}{caveat}",
                        "Nothing to buy. If gross_issuance_tokens is itself unresolved, that is "
                        "the one row to fix; this one follows from it.")
            if model["model"] == "distributed_from_premint":
                # ===== THE RELEASE IS MEASURED ON FIVE PROJECTS, AND THE ROW SAYS SO. 2026-09-23. =====
                # "NOT MINTING" was the whole answer, and on the projects that carry
                # pool_release_tokens it sent the reader away from the one figure that measures
                # the release. It is NOT aliased in: d(circulating) - d(total) carries investor,
                # team and ecosystem unlocks as well as reward distribution, so it is a ceiling on
                # emissions rather than emissions — a restatement would put unlock figures in an
                # emissions cell as a displayed number. The row points; it does not copy.
                # DECIDED, not deferred: config.EMISSIONS_ALIAS_DECLINED (2026-09-24).
                if "pool_release_tokens" in config.metrics_for_project(project):
                    decided = config.EMISSIONS_ALIAS_DECLINED
                    return (f"NOT MINTING — the supply already exists and is being RELEASED, and the "
                            f"release IS measured on this project: pool_release_tokens = "
                            f"d(circulating_supply) - d(total_supply), the tokens leaving pre-minted "
                            f"pools each period. {note} That figure is a CEILING on emissions, not "
                            f"emissions itself — it also carries investor, team and ecosystem "
                            f"unlocks, which is why this column is not aliased to it (DECIDED "
                            f"{decided['decided_on']}, not deferred — see "
                            f"EMISSIONS_ALIAS_DECLINED in config.py). DefiLlama's "
                            f"unlocks feed is the wrong shape for either quantity: the Pro tier "
                            f"would not answer this.{caveat}",
                            "Read pool_release_tokens for the release (if that row is empty, its "
                            "own Gap Report row says why). Emissions proper need the reward pool's "
                            "OUTFLOW history — a Dune query on the distribution wallet, not a "
                            "balance read. Do NOT derive it from total supply.")
                return (f"NOT MINTING — the supply already exists and is being RELEASED. {note} "
                        f"Total supply does not move, so a supply delta cannot see it, and "
                        f"DefiLlama's unlocks feed is the wrong shape for it too: the Pro tier "
                        f"would not answer this question.{caveat}",
                        "The route is the release schedule where one is published, or the "
                        "holding wallet's OUTFLOW history (a Dune query, not a balance read). "
                        "Do NOT derive it from total supply.")
            if model["model"] == "vesting_complete":
                return (f"THE EMISSION HAS FINISHED. {note}{caveat}",
                        "Confirm the schedule's end date from the protocol's own material and "
                        "record it; a zero from then on is a fact about the schedule rather "
                        "than a measurement that failed.")
            if model["model"] == "none":
                return (f"NO EMISSION MECHANISM. {note}{caveat}",
                        "Nothing to source. If this is wrong, the emission schedule is the thing "
                        "to find — not an emissions feed.")
        return (PRO_PAYWALLED[metric] + " (and this project has no emissions_model declared, so "
                "no better explanation is available — classifying it is the first step)",
                f"Add a sources.yaml entry pointing at the protocol's own emissions page, or a Dune query id "
                f"in config.py under {name}.")

    if 1 in tiers and metric in TIER1_SOURCE:
        api, field = TIER1_SOURCE[metric]
        if not project.get(field):
            if api == "DefiLlama":
                # ** "ADD A SLUG IF DEFILLAMA COVERS IT" IS THE WRONG INSTRUCTION ONCE SOMEONE HAS
                # LOOKED. ** It reads as unfinished work on a project that has been checked and is
                # not listed, and the next person repeats the search. Where the check has been
                # done and recorded, the row says so and names what was tried.
                checked = project.get("defillama_listing_checked") or {}
                if checked.get("status") == "absent":
                    return (f"NOT TRACKED BY DEFILLAMA — checked {checked.get('checked_on')}, not "
                            f"a missing config field. DefiLlama's own adapter repositories "
                            f"({', '.join(checked.get('repos') or ())}) carry no adapter under "
                            f"any of {', '.join(checked.get('slugs_tried') or ())}.",
                            f"Nothing to add here. If {name} publishes this figure itself, a "
                            f"sources.yaml entry for {name}/{metric} is the route; otherwise this "
                            f"metric has no source and that is the settled answer.")
                return (f"not tracked by DefiLlama — no {field} in config",
                        f"If the protocol publishes this itself, add a sources.yaml entry for {name}/{metric}. "
                        f"Otherwise set {field} in config.py if DefiLlama does cover it.")
            return (f"no {field} in config",
                    f"Set {field} in config.py for {name}.")
        if 2 not in tiers:
            return (f"{api} returned no data for this series",
                    f"Check the Run Log for the {api} failure, and confirm {field}="
                    f"{project.get(field)!r} is still correct.")

    # A metric served by a node API read (TRON's BURN_TRX) has a source configured; if it produced
    # nothing the adapter has already raised a specific gap naming the failure.
    # ===== A DECLARED TRANSFER-EVENT SCAN THAT PRODUCED NOTHING. 2026-09-24. =====
    # fetch/logscan.py raises its own specific gap on every failure path (no key, refused,
    # unreconciled, held on attribution), and those win over this. This answers only when the
    # scan did not run at all this pass.
    scan = next((sc for sc in project.get("log_scans") or [] if sc.get("metric") == metric), None)
    if scan:
        return (f"read from Transfer events {'INTO' if scan['direction'] == 'in' else 'OUT OF'} "
                f"{', '.join(scan['holders'])} on {scan['chain']} (log_scans.{scan['key']}) via a "
                f"block-explorer API; the scan produced nothing this run and raised no gap of its "
                f"own, so it did not run this pass.",
                "Check that the 'explorer' adapter ran (a --sources filter excludes it) and that "
                "ETHERSCAN_API_KEY / BLOCKSCOUT_API_KEY are in .env.")
    node_api = project.get("node_api") or {}
    # extra_reads share the node and carry their own kind (NEAR's view_account balance).
    extra = {r.get("metric"): r.get("kind") for r in (node_api.get("extra_reads") or [])}
    if node_api and metric in extra:
        return (f"node API read ({extra[metric]}) is configured but returned nothing this run",
                f"Check the node_api endpoints and this read's accounts/field in config.py for "
                f"{name}. Spec on file: {next((r.get('spec_url') for r in node_api.get('extra_reads') or [] if r.get('metric') == metric), None) or 'none'}")
    if node_api and metric in (node_api.get("metric"), "gross_burn_tokens"):
        return (f"node API read ({node_api.get('kind')}) is configured but returned nothing this run",
                f"Check the node_api endpoints, path and response_keys in config.py for {name}. "
                f"Sources on file: {', '.join(node_api.get('source_urls') or []) or 'none'}")

    # A burn that happens at the protocol level has no address to read, so "no contract declared"
    # would be the wrong explanation entirely — the fix is a different SOURCE, not a missing address.
    if metric in ("gross_burn_tokens", "burn_address_balance"):
        # ===== A LOG-SCAN ROUTE EXISTS: SAY WHAT BLOCKS IT, NOT "add a sources.yaml entry". =====
        # Added 2026-09-23. Sky's burn_read_method is protocol_level (no dead address to read),
        # which is TRUE and used to be the whole answer — until contracts.burn_logs was wired
        # to read the burn from Transfer-to-zero logs. With that route on file, the row was still
        # telling the reader to go and find a dashboard, while the actual blocker (the provider's
        # 10-block eth_getLogs cap, recorded on the contract's own scan_blocked_* entry) went
        # unmentioned. This branch runs FIRST so a declared log route always speaks for itself.
        for key, c in (project.get("contracts") or {}).items():
            if c.get("kind") != "burn_transfer_logs":
                continue
            logs = c.get("burn_logs") or {}
            blocked = next((v for k, v in logs.items() if k.startswith("scan_blocked")), None)
            if blocked:
                ways = "; ".join(blocked.get("ways_out") or ())
                return (f"read from Transfer-to-zero LOGS on contracts.{key}, and the scan is "
                        f"BLOCKED: {blocked.get('provider')} caps eth_getLogs at "
                        f"{blocked.get('eth_getLogs_max_range')} blocks per request "
                        f"({blocked.get('evidence')}) — {blocked.get('requests_needed')} would "
                        f"be needed. {blocked.get('do_not')}",
                        f"A logs endpoint above the cap: {ways}. Nothing in config is missing; "
                        f"the route is wired and waiting on the provider. SINCE 2026-09-24 the "
                        f"scan goes to a block-explorer API FIRST (no range cap) when "
                        f"ETHERSCAN_API_KEY or BLOCKSCOUT_API_KEY is in .env — if this row still "
                        f"shows with a key set, the Run Log line for {key} says what the explorer "
                        f"answered.")
            return (f"read from Transfer-to-zero LOGS on contracts.{key}; the scan produced "
                    f"nothing this run — check the Run Log for the chain adapter's row.",
                    f"See contracts.{key} and the chain adapter's Run Log line for the cause.")
        method = project.get("burn_read_method")
        if method in ("protocol_level", "undetermined", "native_balance"):
            note = project.get("burn_read_note", "")
            headline = {
                "protocol_level": "burn happens at the PROTOCOL LEVEL with no transfer — there is no address "
                                  "balance to read",
                "undetermined": "burn MECHANISM is undetermined — it is not established whether the current "
                                "burn is a transfer or a protocol-level destruction",
                "native_balance": "burn total sits in a native (non-ERC-20) balance the EVM adapter cannot read",
            }[method]
            return (f"{headline}. {note}".strip(),
                    f"Add a sources.yaml entry for {name}/{metric} pointing at the chain-data or dashboard "
                    f"source that publishes it. Do NOT add a burn-address contract entry — reading a dead "
                    f"address here returns other people's discarded tokens, not the protocol burn.")

    # A METRIC THAT EXISTS ONLY BECAUSE A CONTRACT WAS REDIRECTED TO IT (metric_override) has no
    # kind of its own to look up, so it fell through to "no source configured" — false: the
    # contract is right there. Maple's treasury_holding_tokens_chain, 2026-09-24.
    redirected_here = [k for k, c in (project.get("contracts") or {}).items()
                       if c.get("metric_override") == metric]
    if redirected_here:
        return (f"contract {redirected_here[0]} serves this metric (metric_override) and wrote "
                f"nothing this run",
                "See the chain adapter's Run Log line for that contract.")

    want_kind = METRIC_CONTRACT_KIND.get(metric)
    if 2 in tiers and want_kind:
        contracts = project.get("contracts") or {}
        # metric_override redirects a contract's kind away from its normal metric (a net_of_burn
        # project's token contract serves total_supply_gross, not total_supply; Maple's treasury
        # read did the same thing for three days in September 2026). Without this check, a kind
        # match alone would credit BOTH
        # metric names with the same contract: "(override or metric) == metric" is true only when
        # there is no override (an ordinary contract serves whatever its kind normally means) or
        # when the override explicitly targets THIS metric.
        matching = [k for k, c in contracts.items()
                   if c.get("kind") == want_kind and (c.get("metric_override") or metric) == metric]
        # A contract of the right kind that exists but is DELIBERATELY redirected elsewhere is a
        # different situation from no contract at all — "no contract of kind X declared" would be
        # false (one is declared) and would send the reader to add a duplicate. This metric
        # genuinely has no tier 2 route BY DESIGN, so fall through to tier 3 below instead of
        # returning a wrong reason.
        redirected_elsewhere = any(
            c.get("kind") == want_kind and c.get("metric_override") and c.get("metric_override") != metric
            for c in contracts.values())
        if not matching and not redirected_elsewhere:
            if name in NON_EVM:
                return (f"no contract read possible — {name} is not EVM, so the tier 2 web3 path does not apply",
                        f"Use the protocol's own dashboard: add a sources.yaml entry for {name}/{metric}.")
            return (f"no contract of kind {want_kind!r} declared in config",
                    f"Add the contract to contracts in config.py for {name}, with the source URL and the date "
                    f"you verified it on the protocol's own docs.")
        if matching:
            ambiguous = [k for k in matching if contracts[k].get("ambiguous")]
            if ambiguous:
                spec = contracts[ambiguous[0]]
                cands = spec.get("candidates") or []
                return (f"contract {ambiguous[0]!r} is AMBIGUOUS — {len(cands)} addresses circulate publicly and "
                        f"none is established as correct, so none is read",
                        f"Resolve from {spec.get('source_url') or 'the protocol docs'} and replace the candidates "
                        f"list with the single confirmed address. Candidates: {', '.join(cands)}")
            unverified = [k for k in matching if not (contracts[k].get("verified"))]
            if unverified:
                return (f"contract {unverified[0]!r} is declared but NOT verified against the protocol's own docs",
                        f"Confirm {contracts[unverified[0]]['address']} on "
                        f"{contracts[unverified[0]].get('source_url') or 'the protocol docs'}, then set verified "
                        f"in config.py. Or set TOKEN_METRICS_ALLOW_UNVERIFIED=1 to read it anyway.")

            # Every matching contract is verified, so the address is not the problem. Either the chain
            # is outside the EVM adapter's reach, or the read itself failed this run.
            off_chain = [k for k in matching if contracts[k].get("chain") not in config.EVM_CHAINS]
            if off_chain:
                chains = sorted({contracts[k]["chain"] for k in off_chain})
                return (f"contract {off_chain[0]!r} is VERIFIED but sits on {', '.join(chains)}, which the EVM "
                        f"adapter cannot read",
                        f"Add a {chains[0]} adapter, or a sources.yaml entry pointing at a page that publishes "
                        f"the figure. The address itself is confirmed, so this is purely a read-path gap.")
            return (f"contract {matching[0]!r} is verified but the tier 2 read returned nothing this run",
                    f"Check the Run Log for the chain read failure, and confirm the RPC endpoints for "
                    f"{contracts[matching[0]].get('chain')} in .env.")
        # else: matching is empty but redirected_elsewhere is True — this metric has no tier 2
        # route BY DESIGN (see the comment above).
        #
        # WHERE TIER 1 IS THE ONLY REMAINING ROUTE, SAY SO INSTEAD OF FALLING THROUGH. Falling
        # through lands on tier 3's "no source configured for this metric", which is false and
        # sends the reader to write a sources.yaml entry for something a free API already
        # serves. This is live for total_supply on every net_of_burn project: the token contract
        # was re-pointed at total_supply_gross on 2026-09-22, so CoinGecko is the whole route
        # now, and a blank means CoinGecko did not answer.
        if not matching and 1 in tiers and metric in TIER1_SOURCE:
            api, field = TIER1_SOURCE[metric]
            if project.get(field):
                return (f"{api} returned no data for this series, and it is now the ONLY route — "
                        f"the tier 2 contract read is deliberately redirected to a different "
                        f"metric for this project (metric_override), so there is no chain "
                        f"fallback here by design",
                        f"Check the Run Log for the {api} failure and confirm {field}="
                        f"{project.get(field)!r}. Do NOT add a sources.yaml entry or a second "
                        f"contract — the redirect is intentional; see config.py.")

    # A DERIVED METRIC HAS NO SOURCE TO CONFIGURE, so "no source configured for this metric" is
    # the wrong answer and sends the reader looking for one. It needs its INPUTS, and naming them
    # is what makes the row actionable.
    ratio = project.get("lock_ratio") or {}
    if ratio.get("metric") == metric:
        return (f"DERIVED, not fetched: {metric} is {ratio['numerator']} / {ratio['denominator']}, "
                f"and at least one of those did not arrive this run. There is no source to add.",
                f"Resolve whichever input is missing — see the Gap Report rows for "
                f"{ratio['numerator']} and {ratio['denominator']}. The ratio computes itself once "
                f"both are present in the same run.")

    # ===== THE SAME ANSWER FOR THE OTHER TWO DERIVATIONS. Added 2026-09-23. =====
    # Both are computed from other columns, so "no source configured" is the wrong instruction —
    # it sends someone to write a scraper for a figure nothing publishes.
    dur = project.get("lock_duration_proxy") or {}
    if dur.get("metric") == metric:
        return (f"DERIVED, not fetched: a PROXY computed as {dur['formula']}, and at least one "
                f"input did not arrive this run. There is no source to add.",
                f"Resolve whichever of {dur['voting_power']}, {dur['locked']} and "
                f"{dur['permanent']} is missing — see their own Gap Report rows. "
                f"{dur['permanent']} is NOT optional: a permanent lock never decays, so without "
                f"it the figure would report positions with no end date as nearly-full-length "
                f"locks.")
    if metric == "pool_release_tokens":
        return ("DERIVED, not fetched: d(circulating_supply) - d(total_supply), the tokens "
                "entering circulation that were NOT newly minted. It needs both stocks in this "
                "run AND an earlier-dated reading of each to difference against. There is no "
                "source to add.",
                "Resolve whichever of circulating_supply and total_supply is missing. If both "
                "are present, the series simply has no prior reading yet and will compute on "
                "the next run — that is not a sourcing problem.")

    entry = scrape_entries.get((name, metric))
    if isinstance(entry, dict):
        # ARMED AND RETURNED NOTHING — a different problem from a missing entry, and the one the
        # old text got wrong. entry_ready() passed, so the url, method and selector are all
        # present; the scrape either did not run this pass or ran and produced no value. Naming
        # the url and the selector is what lets the reader tell those apart at a glance.
        where = f"{entry.get('method')} on {entry.get('url')}"
        sel = entry.get("anchor") or entry.get("json_path")
        return (f"sources.yaml entry EXISTS AND IS ARMED ({where}, selector {sel!r}) but no value "
                f"reached the store this run. The entry is not the problem — either tier 3 did not "
                f"run, or it ran and the page returned nothing usable.",
                f"Check the Run Log for a tier 3 row for {name}. No row at all means the scrape "
                f"never fired; a failed row names the cause (robots.txt, the browser, or the "
                f"selector not matching).")
    if entry is not None:
        return (entry, f"Complete the sources.yaml entry for {name}/{metric}.")

    if 3 in tiers or 5 in tiers:
        return ("no sources.yaml entry for this metric",
                f"Add an entry for {name}/{metric} in sources.yaml pointing at the page that publishes it.")

    if 4 in tiers:
        return ("no Dune query id in config",
                f"Add a dune_queries entry for {name}/{metric} in config.py, or a sources.yaml entry if the "
                f"protocol publishes the figure itself.")

    return ("no source configured for this metric", f"Add a source for {name}/{metric}.")


def detect(projects: list[dict], frame: pd.DataFrame, manual_keys: set[tuple[str, str]],
           scrape_entries: dict, existing_gaps: list[dict]) -> list[dict]:
    """Gap rows for every project x applicable metric with no data from any tier.

    existing_gaps are rows adapters already raised (a failed read, an incomplete registry
    entry). Those are more specific than anything derivable here, so they win.
    """
    have = set()
    if frame is not None and not frame.empty:
        have = set(map(tuple, frame[["project", "metric"]].drop_duplicates().to_numpy()))
    have |= manual_keys
    # A gap raised by an earlier tier is superseded when a later tier resolved the same metric.
    # The Gap Report lists what could not be resolved AT ALL, so an unverified contract address
    # for a figure the protocol's own dashboard already supplied does not belong on the to-do list.
    superseded = [g for g in existing_gaps if (g["project"], g["metric"]) in have]
    existing_gaps = [g for g in existing_gaps if (g["project"], g["metric"]) not in have]
    if superseded:
        log.debug("%d gap rows superseded by a later tier", len(superseded))
    already = {(g["project"], g["metric"]) for g in existing_gaps}

    rows = list(existing_gaps)
    # A DECLARED SCHEDULE THAT HAS RUN OUT is not the same as no source. Render's curve is
    # published a year at a time; the 2025 step ends 2025-12-31 and the Year 3 RNP is not out, so
    # the trailing window sits past the end of everything declared. That is the right outcome —
    # far better than projecting a declining rate forward — but "no source configured" describes
    # a different problem and sends the reader somewhere useless.
    expired = {}
    for p in projects:
        sched = p.get("issuance_schedule") or {}
        steps = sorted(sched.get("steps") or [], key=lambda st: st["from"])
        if steps and steps[-1].get("until"):
            expired[(p["name"], "gross_issuance_tokens")] = steps[-1]["until"]
    for g in rows:
        key = (g["project"], g["metric"])
        if key in expired:
            g["reason"] = (f"SCHEDULE EXPIRED on {expired[key]} — not a missing source. The issuance "
                           f"curve is declared in config and correct up to that date; the next period's "
                           f"figure has not been published, so nothing is projected past it. "
                           f"Deliberately silent rather than carrying the last rate forward, which on a "
                           f"declining curve would report the previous period's higher rate as this one's.")
            g["suggestion"] = (f"Publish-watch: add the next step to issuance_schedule when the figure "
                               f"appears, with its own 'from'. Until then this metric is correctly empty "
                               f"and needs no source hunting.")
    already_expired = set(expired)
    # A figure that was chased and closed is not a to-do item. Leaving it on the list forever
    # trains the reader to skim past the list, which costs more than the row is worth. The
    # closure itself is recorded in config.UNAVAILABLE and rendered on Config & Sources, so it
    # is suppressed here rather than lost.
    closed = {k for k in getattr(config, "UNAVAILABLE_BY_KEY", {})}
    # Figures that move annually and are HAND-ENTERED once a quarter are not unresolved gaps.
    # Reporting them as such every run trains the reader to skim the list, and the fix — automate
    # a number that changes once a year — costs more than typing it. They render in their own
    # "Manual — review quarterly" block on the Gap Report tab instead.
    closed |= {(p["name"], m) for p in projects for m in (p.get("manual_quarterly") or ())}
    rows = [g for g in rows if (g["project"], g["metric"]) not in closed]
    for p in projects:
        name = p["name"]
        for metric in config.metrics_for_project(p):
            if (name, metric) in have or (name, metric) in already or (name, metric) in closed:
                continue
            if (name, metric) in already_expired:
                rows.append({
                    "project": name, "metric": metric, "tiers_attempted": "1",
                    "reason": (f"SCHEDULE EXPIRED on {already_expired and expired[(name, metric)]} — not a "
                               f"missing source. The curve is declared and correct up to that date; the "
                               f"next period's figure has not been published, so nothing is projected "
                               f"past it."),
                    "suggestion": "Add the next step to issuance_schedule when the figure is published.",
                })
                continue
            reason, suggestion = _tier_note(p, metric, scrape_entries)
            rows.append({
                "project": name, "metric": metric,
                "tiers_attempted": ", ".join(str(t) for t in config.METRICS[metric].get("tiers", [])),
                "reason": reason, "suggestion": suggestion,
            })

    # Open questions a human must settle. These are not "a metric has no data" — they are
    # decisions and confirmations, and they always appear so they cannot be forgotten.
    #
    # ** EXCEPT THE ONES THAT ARE NO LONGER OPEN, WHICH USED TO APPEAR ANYWAY. ** Every entry was
    # emitted unconditionally with an "[open]" prefix, so a question that had been settled stayed
    # on the P1/P2 list for ever — four of them were sitting there reading "[open] CLOSED
    # 2026-09-14...", "[open] RESOLVED — the zero was the wrong address" and "[open] ANSWERED
    # 2026-09-14 — sETHFI COMPOUNDS". Closure was recorded in the PROSE and nothing read it.
    #
    # Settled entries are NOT deleted from config. Several say so in their own text — the Uniswap
    # zero-burn entry is kept precisely because the wrong reasoning was nearly convincing — and a
    # deleted question is one that gets asked again. They are skipped HERE, on a structural
    # `status` field, so the record survives and the to-do list does not carry it.
    for q in getattr(config, "OPEN_QUESTIONS", []):
        if (q.get("status") or "open") != "open":
            continue
        rows.append({
            "project": q["project"], "metric": f"[open] {q['topic']}",
            "tiers_attempted": "-", "reason": q["reason"], "suggestion": q["suggestion"],
            "_severity": q.get("severity"),
        })

    # Config gaps: a documented split we have not confirmed suppresses a derived figure by design.
    for p in projects:
        name = p["name"]
        for block, label in (("fee_split", "revenue-to-buyback split"), ("burn_split", "share-of-fees-burned")):
            spec = p.get(block)
            if spec and spec.get("status") == "unconfirmed":
                rows.append({
                    "project": name, "metric": f"[config] {label}",
                    "tiers_attempted": "1",
                    "reason": f"{label} is UNCONFIRMED — the implied figure is suppressed in the workbook by design",
                    "suggestion": (spec.get("note") or "Document the split, then set status to active in config.py "
                                   f"with the source URL and date. Source on file: {spec.get('source_url') or 'none'}"),
                })

    # ===== ONE GAP, NOT TWO. Added 2026-09-23. =====
    # actual_buyback_usd is tokens x price on each flow's own date, so where tokens is a gap the
    # usd row is the same gap under a second name — seven projects carried it twice. The usd row
    # is folded into the tokens row here and the tokens row says so. A usd row survives only
    # where tokens EXISTS and usd does not: that is a price gap, and a different problem.
    tokens_gapped = {r["project"] for r in rows if r["metric"] == "actual_buyback_tokens"}
    folded = {r["project"] for r in rows
              if r["metric"] == "actual_buyback_usd" and r["project"] in tokens_gapped}
    if folded:
        rows = [r for r in rows if not (r["metric"] == "actual_buyback_usd" and r["project"] in folded)]
        for r in rows:
            if r["metric"] == "actual_buyback_tokens" and r["project"] in folded:
                r["suggestion"] = (f"{r['suggestion']} actual_buyback_usd is NOT listed separately: "
                                   f"it is tokens x price_usd on each flow's own date and fills "
                                   f"from this row.")

    # ===== A NULL REASON IS A REPORTING BUG, AND IT SAYS SO ON THE ROW. Added 2026-09-23. =====
    # Every raiser passes a reason, and _tier_note always returns one, but _priority lower-cases
    # the reason and a None here would take the run down — or, worse, render as an empty cell
    # that reads as "nothing to say". It is replaced with a reason that names the bug and logged
    # as an error, so the row is visible and the raiser is findable.
    for r in rows:
        if not r.get("reason"):
            log.error("gap row %s/%s has NO REASON — reporting bug in whichever raiser produced it",
                      r["project"], r["metric"])
            r["reason"] = (f"NO REASON RECORDED for {r['project']}/{r['metric']} — a reporting "
                           f"bug in the code that raised this row, not a sourcing fact; see the "
                           f"run log")
            r["suggestion"] = r.get("suggestion") or "Find the out.gap( call that raised it without a reason."

    # Rank every row so the report opens on what actually matters, not on alphabetical order.
    for r in rows:
        r["priority"] = _priority(r["project"], r["metric"], r.get("reason", ""), r.pop("_severity", None))
        r["priority_label"] = PRIORITY_LABEL[r["priority"]]
    rows.sort(key=lambda r: (r["priority"], r["project"], r["metric"]))
    return rows
