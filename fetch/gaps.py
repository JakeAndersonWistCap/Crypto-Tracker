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
P_SUPPRESSED = 5            # [config] splits deliberately suppressed. By design, informational.
P_UNCOVERED = 6             # no source covers it yet, and none is configured to

PRIORITY_LABEL = {
    P_CRITICAL: "P1 critical",
    P_DECISION: "P2 decision",
    P_ACTIONABLE_HEADLINE: "P3 fixable",
    P_ACTIONABLE: "P4 fixable",
    P_SUPPRESSED: "P5 by design",
    P_UNCOVERED: "P6 uncovered",
}

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
    "burn_address_balance": "burn_address_balance",
    "gross_burn_tokens": "burn_address_balance",       # derived by differencing the balance
    "buyback_fund_balance": "buyback_fund_balance",
    "actual_buyback_tokens": "buyback_fund_balance",
    "locked_tokens": "ve_total_supply",
    "total_supply": "erc20_total_supply",
    "circulating_supply": "erc20_total_supply",
}


def _tier_note(project: dict, metric: str, scrape_entries: dict) -> tuple[str, str]:
    """(reason, suggestion) for a metric this project has no data for."""
    name = project["name"]
    m = config.METRICS[metric]
    tiers = m.get("tiers", [])

    if metric in PRO_PAYWALLED:
        return (PRO_PAYWALLED[metric],
                f"Add a sources.yaml entry pointing at the protocol's own emissions page, or a Dune query id "
                f"in config.py under {name}.")

    if 1 in tiers and metric in TIER1_SOURCE:
        api, field = TIER1_SOURCE[metric]
        if not project.get(field):
            if api == "DefiLlama":
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
    node_api = project.get("node_api") or {}
    if node_api and metric in (node_api.get("metric"), "gross_burn_tokens"):
        return (f"node API read ({node_api.get('kind')}) is configured but returned nothing this run",
                f"Check the node_api endpoints, path and response_keys in config.py for {name}. "
                f"Sources on file: {', '.join(node_api.get('source_urls') or []) or 'none'}")

    # A burn that happens at the protocol level has no address to read, so "no contract declared"
    # would be the wrong explanation entirely — the fix is a different SOURCE, not a missing address.
    if metric in ("gross_burn_tokens", "burn_address_balance"):
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

    want_kind = METRIC_CONTRACT_KIND.get(metric)
    if 2 in tiers and want_kind:
        contracts = project.get("contracts") or {}
        matching = [k for k, c in contracts.items() if c.get("kind") == want_kind]
        if not matching:
            if name in NON_EVM:
                return (f"no contract read possible — {name} is not EVM, so the tier 2 web3 path does not apply",
                        f"Use the protocol's own dashboard: add a sources.yaml entry for {name}/{metric}.")
            return (f"no contract of kind {want_kind!r} declared in config",
                    f"Add the contract to contracts in config.py for {name}, with the source URL and the date "
                    f"you verified it on the protocol's own docs.")
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

    if (name, metric) in scrape_entries:
        return (scrape_entries[(name, metric)],
                f"Complete the sources.yaml entry for {name}/{metric}.")

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
            reason, suggestion = _tier_note(p, metric, scrape_entries)
            rows.append({
                "project": name, "metric": metric,
                "tiers_attempted": ", ".join(str(t) for t in config.METRICS[metric].get("tiers", [])),
                "reason": reason, "suggestion": suggestion,
            })

    # Open questions a human must settle. These are not "a metric has no data" — they are
    # decisions and confirmations, and they always appear so they cannot be forgotten.
    for q in getattr(config, "OPEN_QUESTIONS", []):
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

    # Rank every row so the report opens on what actually matters, not on alphabetical order.
    for r in rows:
        r["priority"] = _priority(r["project"], r["metric"], r.get("reason", ""), r.pop("_severity", None))
        r["priority_label"] = PRIORITY_LABEL[r["priority"]]
    rows.sort(key=lambda r: (r["priority"], r["project"], r["metric"]))
    return rows
