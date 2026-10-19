"""
spec.py — SPEC.md, read by the code (Jake's alignment round, 2026-10-13).

SPEC.md is the single definition of "correct": its concept registry (between the SPEC:CONCEPTS markers) is parsed here
and every workbook column is mapped to one concept by COLUMN_MAP (tab, header prefix -> concept key). The Trajectory
takes each row's AXIS and DIRECTION from the concept; the Coverage tab (coverage_audit.py) checks every column against
its concept; tests/test_alignment.py fails the build when a column maps to nothing, a concept is undefined, or a row has
no direction.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

SPEC_PATH = Path(__file__).with_name("SPEC.md")
AXES = ("BUSINESS", "CAPTURE", "ALIGNMENT", "CONTEXT")


@lru_cache(maxsize=1)
def concepts() -> dict[str, dict]:
    """{key: {concept, definition, formula, unit, axis, dir, trajectory, tabs, tier, status, inputs}} from SPEC.md."""
    text = SPEC_PATH.read_text(encoding="utf-8")
    block = text.split("<!-- SPEC:CONCEPTS -->", 1)[1].split("<!-- /SPEC:CONCEPTS -->", 1)[0]
    rows = [ln for ln in block.splitlines() if ln.startswith("|")]
    head = [c.strip() for c in rows[0].strip("|").split("|")]
    out = {}
    for ln in rows[2:]:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) != len(head):
            raise ValueError(f"SPEC.md concept row has {len(cells)} cells, not {len(head)}: {ln[:80]}")
        row = dict(zip(head, cells))
        row["dir"] = int(row["dir"]) if row["dir"] not in ("", "—") else 0
        row["inputs"] = tuple(x for x in row["inputs"].split(",") if x)
        row["tabs"] = tuple(x for x in row["tabs"].split(",") if x and x != "—")
        out[row["key"]] = row
    return out


# (tab prefix or "*", header prefix, concept key). First match wins; "*" matches every tab. Support concepts
# (identity, notes, flags, deltas, cross-checks, components) are in SUPPORT below — real columns, not headlines.
COLUMN_MAP: tuple[tuple[str, str, str], ...] = (
    # ---- identity / support ----
    ("*", "Project", "identity"), ("*", "Symbol", "identity"), ("*", "Archetypes", "identity"), ("*", "Held", "identity"),
    ("*", "Materiality", "identity"), ("*", "Notes", "notes"), ("*", "Data flags", "notes"),
    ("*", "Latest data date", "notes"), ("*", "Trend — ", "trend"),
    # ---- CONTEXT ----
    ("*", "Price — spot", "price"), ("*", "Price — 90d average", "price"), ("*", "Market cap date", "notes"), ("*", "Market cap — CoinGecko", "cross_check"),
    ("*", "Market cap", "market_cap"), ("*", "Free float market cap", "float"), ("*", "FDV RETIREMENT RATE", "retirement"),
    ("*", "FDV retirement rate", "retirement"), ("*", "FDV", "fdv"),
    ("*", "Circulating supply — CoinGecko", "cross_check"), ("*", "Circulating supply", "circulating"),
    ("*", "Max supply", "circulating"), ("*", "Total supply", "circulating"), ("*", "Supply figure complete", "notes"),
    ("*", "Staked / locked inside circulating", "stake_inside"), ("*", "Staked tokens", "stake_inside"),
    ("*", "Tokens locked — cross-check", "cross_check"), ("*", "Tokens locked", "stake_inside"),
    ("*", "TRADEABLE FLOAT", "float"), ("*", "Tradeable float", "float"), ("*", "FREE FLOAT ÷ ARR", "ff_arr"),
    ("*", "FREE FLOAT", "float"), ("*", "Resulting float", "float"), ("*", "Effective float", "float"),
    ("*", "Destination effect on float", "lock_terms"), ("*", "Staking rate", "lock_rate"),
    ("*", "Lock rate", "lock_rate"), ("*", "Lock: contract vs dashboard", "cross_check"),
    ("*", "Holding demand — lock rate", "lock_rate"), ("*", "Holding demand — components", "lock_terms"),
    ("*", "Average lock duration", "lock_terms"), ("*", "3-MONTH T-BILL", "tbill"),
    ("*", "Spread over the 3-month T-bill", "tbill"), ("*", "Sky Savings Rate", "ssr"),
    ("*", "ETF / ETP and DAT holders", "etf_holdings"), ("*", "ETF / ETP holdings", "etf_holdings"),
    ("*", "Treasury-company (DAT) holdings", "dat_holdings"), ("*", "NRR", "nrr"), ("*", "Which NRR", "nrr"),
    ("*", "Fees ÷ FDV", "fees_fdv"),
    # ---- BUSINESS ----
    ("*", "Fees ÷ issuance", "fees_issuance"), ("*", "FEES ÷ ISSUANCE", "fees_issuance"),
    ("*", "Fees Δ", "delta"), ("*", "TVL Δ", "delta"), ("*", "Stablecoins Δ", "delta"), ("*", "Revenue Δ", "delta"),
    ("*", "Customer revenue Δ", "delta"), ("*", "Supply units Δ", "delta"), ("*", "Emissions Δ", "delta"),
    ("*", "Burn Δ", "delta"), ("*", "Issuance Δ", "delta"),
    ("*", "Fees Q0", "fees"), ("*", "Fee per transaction", "business_n"), ("*", "Transactions Q0", "business_n"),
    ("*", "Active addresses", "business_n"), ("*", "Stablecoin supply", "business_n"), ("*", "Revenue Q0", "revenue"),
    ("*", "Revenue required for crossover", "crossover"), ("*", "Revenue per supply unit", "customer_revenue"),
    ("*", "Base for the implied buyback", "revenue"), ("*", "TVL in native units", "tvl_native"),
    ("*", "TVL", "tvl"), ("*", "RWA on chain", "business_n"), ("*", "Settlement volume", "settlement_volume"),
    ("*", "Trading throughput", "settlement_volume"), ("*", "Customer revenue ÷ emissions", "subsidy_cover"),
    ("*", "CUSTOMER REVENUE PER TOKEN EMITTED", "subsidy_cover"), ("*", "Rev ÷ emission", "subsidy_cover"),
    ("*", "Customer revenue", "customer_revenue"), ("*", "  of which payments", "customer_revenue"),
    ("*", "  of which Direct", "customer_revenue"), ("*", "  of which CCIP", "customer_revenue"),
    ("*", "  of which VRF", "customer_revenue"), ("*", "  of which Automation", "customer_revenue"),
    ("*", "  Customer revenue", "customer_revenue"), ("*", "  Reserve inflow", "actual_buyback"),
    ("*", "Supply units", "business_n"), ("*", "Capacity utilisation", "business_n"),
    ("*", "Publisher Conviction", "business_n"), ("*", "Supplier earnings from emissions", "emissions"),
    ("*", "Supplier earnings from customers", "customer_revenue"), ("*", "Supplier earnings", "supplier_payout"),
    ("*", "Customer share of supplier", "subsidy_cover"), ("*", "Business ", "business_n"),
    ("*", "BUSINESS ", "business_n"), ("*", "Market share", "market_share"), ("*", "MARKET SHARE", "market_share"),
    ("*", "Boros", "boros"), ("*", "Post-incentive retention", "business_n"),
    # ---- CAPTURE ----
    ("*", "VALIDATOR STAKING YIELD", "validator_yield"), ("*", "Validator yield", "validator_yield"),
    ("*", "Consensus part", "validator_yield"), ("*", "Execution part", "validator_yield"),
    ("*", "Issuance ÷ protocol maximum", "validator_yield"), ("*", "Staker yield — protocol", "protocol_yield"),
    ("*", "Staker yield", "validator_yield"),
    ("*", "TOTAL YIELD", "total_yield"), ("*", "Total yield", "total_yield"),
    ("*", "Whole-token yield", "whole_token_yield"), ("*", "PROTOCOL STAKING YIELD", "protocol_yield"),
    ("*", "Protocol staking yield", "protocol_yield"), ("*", "VOTER APR", "protocol_yield"),
    ("*", "Staking APR as the protocol publishes", "cross_check"), ("*", "Share of staker distributions", "protocol_yield"),
    ("*", "PENDLE still in the OLD", "stake_inside"), ("*", "sPENDLE + old vePENDLE", "cross_check"),
    ("*", "Umbrella staked", "notes"), ("*", "CIRCULATING RETIREMENT RATE", "retirement"),
    ("*", "Circulating retirement rate", "retirement"), ("*", "Retirement rate", "retirement"),
    ("*", "RETIREMENT ÷ DILUTION", "retirement"), ("*", "Buyback status", "capture_status"),
    ("*", "Burn status", "capture_status"), ("*", "Programmed?", "capture_status"), ("*", "Buyback destination", "capture_status"),
    ("*", "Destination split", "capture_status"), ("*", "Share applied to this window", "implied_buyback"),
    ("*", "Status of that window's split", "implied_buyback"), ("*", "Current documented share", "capture_status"),
    ("*", "Per-product split", "capture_status"), ("*", "Implied buyback", "implied_buyback"),
    ("*", "Implied − actual", "implied_buyback"), ("*", "Implied SKY purchased", "implied_buyback"),
    ("*", "Implied BURN", "implied_buyback"), ("*", "Implied burn", "implied_buyback"),
    ("*", "BUYBACK AS % OF SUPPLY", "implied_buyback"), ("*", "Buyback % supply", "implied_buyback"),
    ("*", "Buyback % of supply", "implied_buyback"), ("*", "Actual buyback as %", "retirement"),
    ("*", "Actual buyback", "actual_buyback"), ("*", "  of which BOUGHT", "actual_buyback"),
    ("*", "Coverage ratio", "payout_ratio"), ("*", "Reserve balance", "cross_check"),
    ("*", "Contract vs dashboard", "cross_check"), ("*", "Buyback to LOCKED", "retirement"),
    ("*", "Buyback to YIELD PAYOUT", "actual_buyback"), ("*", "Net absorption", "net_absorption"),
    ("*", "PERMANENT BURN YIELD", "burn_yield"), ("*", "BURN ÷ ISSUANCE", "crossover"),
    ("*", "Release ÷ burn", "crossover"), ("*", "Governance lever", "crossover"), ("*", "Issuance basis for", "crossover"),
    ("*", "Q0 WINDOW CAVEAT", "notes"), ("*", "Burn execution", "capture_status"),
    ("*", "Documented share of fees burned", "capture_status"), ("*", "Burn as share of fees", "gross_burn"),
    ("*", "Cumulative burn", "gross_burn"), ("*", "Burn split", "gross_burn"), ("*", "Burn rate", "gross_burn"),
    ("*", "GROSS BURN", "gross_burn"), ("*", "Gross burn", "gross_burn"), ("*", "Actual − implied burn", "implied_buyback"),
    ("*", "One-off burn", "gross_burn"), ("*", "ONE-OFF BURN", "gross_burn"),
    ("*", "HOLDER VALUE YIELD", "hvy"), ("*", "Holder value", "hvy"), ("*", "NET HOLDER YIELD", "nhy"),
    ("*", "Payout ratio", "payout_ratio"), ("*", "Payout currency", "lock_terms"),
    ("*", "Destination durability", "lock_terms"), ("*", "Mechanism certainty", "capture_status"),
    ("*", "CAPTURE STATUS", "capture_status"), ("*", "Flow demand", "actual_buyback"),
    ("*", "Attributes NOT established", "notes"), ("*", "Subsidy targeting", "notes"),
    # ---- ALIGNMENT ----
    ("*", "Issuer releases", "issuer_releases"), ("*", "Pool release", "issuer_releases"),
    ("*", "GROSS ISSUANCE", "gross_issuance"), ("*", "Gross issuance", "gross_issuance"),
    ("*", "Issuance as % of supply", "gross_issuance"), ("*", "NET ISSUANCE", "net_issuance"),
    ("*", "NET SUPPLY CHANGE", "net_issuance"), ("*", "Net supply change", "net_issuance"),
    ("*", "Net mint Q0", "net_issuance"), ("*", "Self-reported", "net_issuance"), ("*", "Derived net supply", "net_issuance"),
    ("*", "Cross-check: Δ implied circulating", "cross_check"),
    ("*", "SUPPLY TRAJECTORY", "supply_trajectory"), ("*", "PUBLISHED UNLOCK SCHEDULE", "unlocks"),
    ("*", "UNLOCK DILUTION", "unlocks"), ("*", "Unlock dilution", "unlocks"), ("*", "TOTAL DILUTION", "total_dilution"),
    ("*", "Emissions to suppliers", "emissions"), ("*", "  Emissions", "emissions"), ("*", "  of which", "emissions"),
    ("*", "Emissions", "emissions"), ("*", "Dilution (a)", "outflow_a"), ("*", "Dilution (b)", "outflow_b"),
    ("*", "Dilution (c)", "outflow_c"), ("*", "Dilution (d)", "outflow_d"), ("*", "Dilution (e)", "outflow_e"),
    ("*", "Outflow (a)", "outflow_a"), ("*", "Outflow (b)", "outflow_b"), ("*", "Outflow (c)", "outflow_c"),
    ("*", "Outflow (d)", "outflow_d"), ("*", "Outflow (e)", "outflow_e"), ("*", "Outflow categories", "outflow_e"),
    ("*", "Subsidy cover", "subsidy_cover"), ("*", "SUBSIDY COVER", "subsidy_cover"),
    ("*", "Supplier payout", "supplier_payout"), ("*", "Insider share", "insider_share"),
    ("*", "Discretionary supply", "discretionary"), ("*", "Treasury governance could redeploy", "discretionary"),
    ("*", "Lock terms", "lock_terms"), ("*", "Leakage", "leakage"), ("*", "LEAKAGE", "leakage"),
    ("*", "Uniformity", "uniformity"), ("*", "UNIFORMITY", "uniformity"), ("*", "Non-stakers receive", "uniformity"),
)

# Columns that are part of the workbook but are not a theory concept (labels, notes, trend cells, cross-checks,
# change-over-period columns): axis CONTEXT, direction neutral, never a headline.
SUPPORT = {"identity": "row identity", "notes": "notes / flags / caveats", "trend": "the trend cell beside a headline",
           "cross_check": "an independent cross-check of a figure beside it",
           "delta": "change of a series over 30d / 3m / 6m / 9m"}


def concept_for(tab: str, header: str) -> str | None:
    """The SPEC concept key a column shows, or None (the alignment test fails on None)."""
    for t, prefix, key in COLUMN_MAP:
        if (t == "*" or tab.startswith(t)) and str(header).startswith(prefix):
            return key
    return None


def axis_of(key: str | None) -> str:
    if not key:
        return ""
    if key in SUPPORT:
        return "CONTEXT"
    return (concepts().get(key) or {}).get("axis", "")


def direction_of(key: str | None) -> int | None:
    if not key:
        return None
    if key in SUPPORT:
        return 0
    c = concepts().get(key)
    return None if c is None else int(c["dir"])


def trajectory_method(key: str | None) -> str:
    return (concepts().get(key) or {}).get("trajectory", "") if key else ""


_SLUG = re.compile(r"[^a-z0-9]+")
