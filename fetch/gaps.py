"""
fetch/gaps.py — work out what could not be resolved, and say precisely why.

Anything unresolved after every tier appears in the Gap Report, not as a blank cell in the
Master tab. A silently stale cell is worse than a visible gap, and a blank one is worse still.

The Gap Report is the to-do list: each row names the project, the metric, the tiers attempted,
why it failed and what would fix it. Jake supplies the Dune link or the source page for
anything that lands there, usually by adding an entry to sources.yaml.
"""
from __future__ import annotations

import pandas as pd

import config

# Metrics DefiLlama serves only on its Pro tier. Named explicitly so the report says "paywalled",
# not "missing" — a different problem with a different fix.
PRO_PAYWALLED = {
    "emissions_tokens": "DefiLlama emissions/unlocks is Pro tier only ($300/mo, separate API plan)",
}

# Chains where the tier 2 web3 path does not apply at all.
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

# Contract kinds in config that can serve a tier 2 metric.
METRIC_CONTRACT_KIND = {
    "burn_address_balance": "burn_address_balance",
    "buyback_fund_balance": "buyback_fund_balance",
    "locked_tokens": "ve_total_supply",
    "total_supply": "erc20_total_supply",
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

    if 2 in tiers:
        contracts = project.get("contracts") or {}
        want_kind = METRIC_CONTRACT_KIND.get(metric)
        matching = [k for k, c in contracts.items() if c.get("kind") == want_kind] if want_kind else list(contracts)
        if not matching:
            if name in NON_EVM:
                return (f"no contract read possible — {name} is not EVM, so the tier 2 web3 path does not apply",
                        f"Use the protocol's own dashboard: add a sources.yaml entry for {name}/{metric}.")
            kind_note = f" of kind {want_kind!r}" if want_kind else ""
            return (f"no contract{kind_note} declared in config",
                    f"Add the contract to contracts in config.py for {name}, with the source URL and the date "
                    f"you verified it on the protocol's own docs.")
        unverified = [k for k in matching if not (contracts[k].get("verified"))]
        if unverified:
            return (f"contract {unverified[0]!r} is declared but NOT verified against the protocol's own docs",
                    f"Confirm {contracts[unverified[0]]['address']} on "
                    f"{contracts[unverified[0]].get('source_url') or 'the protocol docs'}, then set verified "
                    f"in config.py. Or set TOKEN_METRICS_ALLOW_UNVERIFIED=1 to read it anyway.")

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
    already = {(g["project"], g["metric"]) for g in existing_gaps}

    rows = list(existing_gaps)
    for p in projects:
        name = p["name"]
        for metric in config.metrics_for_project(p):
            if (name, metric) in have or (name, metric) in already:
                continue
            reason, suggestion = _tier_note(p, metric, scrape_entries)
            rows.append({
                "project": name, "metric": metric,
                "tiers_attempted": ", ".join(str(t) for t in config.METRICS[metric].get("tiers", [])),
                "reason": reason, "suggestion": suggestion,
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
    return rows
