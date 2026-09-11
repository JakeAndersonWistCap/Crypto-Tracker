"""
config.py — the project universe and every parameter that could go stale.

NO LOGIC IN THIS FILE. Data only.

Design principle: minimise paid APIs. Prefer, in order:
    free API -> contract read -> rendering the public web page -> paid API.
Where a protocol publishes a figure on its own dashboard, take the self-reported number
and cite it rather than reconstructing it.

Known paywalls (do NOT design around these as if free):
  * DefiLlama emissions/unlocks    — Pro tier only, separate API plan. Issuance comes from tiers 2-4.
  * DefiLlama per-version fees     — Pro tier only. Affects Uniswap (needs fees split by version).
  * Token Terminal                 — no API on our tier. Manual override column only.

Fields that do real work (never collapse into constants):
  fee_split.programmed      — contract-enforced (True) vs revisable by forum post (False)
  buyback_destination       — burn | distribute | split | hold | disputed
  destination_split         — where a protocol splits between the two
  burn_execution            — protocol | holder_elected | n/a
  materiality               — high | medium | low

Where a split's status is "unconfirmed" the workbook greys the cell and SUPPRESSES the
derived figure. Never estimate a split we have not documented.
"""

BRIEF_DATE = "2026-09-11"   # date the parameters below were set from the working brief

# =======================================================================================
# Source tiers. Every stored value carries its tier; the workbook shows it.
# =======================================================================================
TIERS = {
    1: {
        "name": "Free API x documented rule",
        "detail": "DefiLlama (fees, revenue, TVL, stablecoins, RWA category) and CoinGecko "
                  "(price, supply, FDV), optionally multiplied by a rule documented in this file. "
                  "Also covers deterministic issuance schedules declared here.",
        "cost": "free, no key",
    },
    2: {
        "name": "Contract read",
        "detail": "web3.py against free public RPC endpoints: burn-address balances, totalSupply "
                  "deltas, vote-escrow totalSupply for lock rates, buyback-contract balances.",
        "cost": "free (public RPC)",
    },
    3: {
        "name": "Protocol's own dashboard",
        "detail": "Playwright headless Chromium. Intercept the page's own JSON endpoint where one "
                  "exists, DOM extraction anchored on label text otherwise. Registry: sources.yaml.",
        "cost": "free (be polite: one run a day, cache, honest user agent)",
    },
    4: {
        "name": "Dune (historical backfill only)",
        "detail": "Event-log aggregation for month-by-month history that a point-in-time contract "
                  "read cannot give. Once the store holds history, tier 2 snapshots keep it current.",
        "cost": "DUNE_API_KEY",
    },
    5: {
        "name": "Off-chain operational",
        "detail": "Archetype 2 supply units, utilisation, customer revenue. Dune indexes chains, not "
                  "business operations. Protocol API where one exists (taostats for Bittensor), "
                  "otherwise the tier 3 path via sources.yaml.",
        "cost": "free where an API exists",
    },
}

# =======================================================================================
# Global levers — written to Config & Sources as blue inputs. Formulas reference the cell.
# =======================================================================================
GLOBALS = {
    "period_days": 90,
    "short_days": 30,
    "days_per_year": 365,
    "stale_after_days": 7,
    "months_in_monthly_sheet": 12,
    "default_change_threshold_pct": 50,   # period-on-period move that sends a value to the Review Queue
}

ARCHETYPE_NAMES = {
    1: "Infrastructure",
    2: "Coordination Mechanism",
    3: "Revenue Buyback",
    4: "Permanent Burn",
}

# =======================================================================================
# Metric library.
#   kind          flow (summed over a window) | stock (point value / window average)
#   unit          usd | tokens | count | pct | days | units
#   tiers         source tiers that can resolve this metric, in preference order
#   sanity_min/max  plausibility bounds. A value outside them is REJECTED, not stored,
#                   and goes to the Review Queue. None = unbounded on that side.
# Per-project overrides live in PROJECTS[...]["sanity"].
# =======================================================================================
METRICS = {
    # --- free API: price & supply (CoinGecko)
    "price_usd":                  {"label": "Price",                           "kind": "stock", "unit": "usd",    "archetypes": [1, 2, 3, 4], "tiers": [1],    "sanity_min": 0,    "sanity_max": 1e7},
    "market_cap_usd":             {"label": "Market cap",                      "kind": "stock", "unit": "usd",    "archetypes": [1, 2, 3, 4], "tiers": [1],    "sanity_min": 0,    "sanity_max": 1e13},
    "volume_usd":                 {"label": "Trading volume",                  "kind": "flow",  "unit": "usd",    "archetypes": [],           "tiers": [1],    "sanity_min": 0,    "sanity_max": 1e12},
    "circulating_supply":         {"label": "Circulating supply (reported)",   "kind": "stock", "unit": "tokens", "archetypes": [1, 2, 3, 4], "tiers": [1, 2], "sanity_min": 0,    "sanity_max": 1e15},
    "circulating_supply_implied": {"label": "Circulating supply (mcap/price)", "kind": "stock", "unit": "tokens", "archetypes": [1, 4],       "tiers": [1],    "sanity_min": 0,    "sanity_max": 1e15},
    "total_supply":               {"label": "Total supply",                    "kind": "stock", "unit": "tokens", "archetypes": [1, 2, 3, 4], "tiers": [1, 2], "sanity_min": 0,    "sanity_max": 1e15},
    "max_supply":                 {"label": "Max supply",                      "kind": "stock", "unit": "tokens", "archetypes": [4],          "tiers": [1],    "sanity_min": 0,    "sanity_max": 1e15},
    "fdv_usd":                    {"label": "FDV",                             "kind": "stock", "unit": "usd",    "archetypes": [3],          "tiers": [1],    "sanity_min": 0,    "sanity_max": 1e13},
    # --- free API: DefiLlama
    "fees_usd":                   {"label": "Total fees paid",                 "kind": "flow",  "unit": "usd",    "archetypes": [1, 3, 4],    "tiers": [1, 3], "sanity_min": 0,    "sanity_max": 1e11},
    "revenue_usd":                {"label": "Protocol revenue",                "kind": "flow",  "unit": "usd",    "archetypes": [3],          "tiers": [1, 3], "sanity_min": 0,    "sanity_max": 1e11},
    "holders_revenue_usd":        {"label": "Holders revenue (DefiLlama)",     "kind": "flow",  "unit": "usd",    "archetypes": [3],          "tiers": [1],    "sanity_min": 0,    "sanity_max": 1e11},
    "protocol_tvl_usd":           {"label": "Protocol TVL",                    "kind": "stock", "unit": "usd",    "archetypes": [3],          "tiers": [1],    "sanity_min": 0,    "sanity_max": 1e12},
    "tvl_usd":                    {"label": "Chain TVL",                       "kind": "stock", "unit": "usd",    "archetypes": [1],          "tiers": [1],    "sanity_min": 0,    "sanity_max": 1e12},
    "stablecoin_supply_usd":      {"label": "Stablecoin supply on chain",      "kind": "stock", "unit": "usd",    "archetypes": [1],          "tiers": [1],    "sanity_min": 0,    "sanity_max": 1e12},
    "rwa_defillama_usd":          {"label": "RWA on chain (DefiLlama)",        "kind": "stock", "unit": "usd",    "archetypes": [1],          "tiers": [1],    "sanity_min": 0,    "sanity_max": 1e12},
    "rwa_xyz_usd":                {"label": "RWA on chain (RWA.xyz)",          "kind": "stock", "unit": "usd",    "archetypes": [1],          "tiers": [3],    "sanity_min": 0,    "sanity_max": 1e12},
    # --- chain state / burn / issuance
    "tx_count":                   {"label": "Transactions",                    "kind": "flow",  "unit": "count",  "archetypes": [1],          "tiers": [3, 4], "sanity_min": 0,    "sanity_max": 1e10},
    "active_addresses":           {"label": "Active addresses (low weight)",   "kind": "stock", "unit": "count",  "archetypes": [1],          "tiers": [3, 4], "sanity_min": 0,    "sanity_max": 1e9},
    "gross_issuance_tokens":      {"label": "Gross issuance",                  "kind": "flow",  "unit": "tokens", "archetypes": [1, 4],       "tiers": [1, 2, 3, 4], "sanity_min": 0, "sanity_max": 1e12},
    "gross_burn_tokens":          {"label": "Gross burn",                      "kind": "flow",  "unit": "tokens", "archetypes": [1, 4],       "tiers": [2, 3, 4], "sanity_min": 0,   "sanity_max": 1e12},
    "burn_address_balance":       {"label": "Cumulative burned (burn address)", "kind": "stock", "unit": "tokens", "archetypes": [4],         "tiers": [2],    "sanity_min": 0,    "sanity_max": 1e15},
    "staked_tokens":              {"label": "Staked tokens",                   "kind": "stock", "unit": "tokens", "archetypes": [1, 2],       "tiers": [2, 3, 4], "sanity_min": 0,   "sanity_max": 1e15},
    "emissions_tokens":           {"label": "Emissions to suppliers/stakers",  "kind": "flow",  "unit": "tokens", "archetypes": [2, 3],       "tiers": [1, 3, 4], "sanity_min": 0,   "sanity_max": 1e12},
    "actual_buyback_usd":         {"label": "Actual buyback (observed)",       "kind": "flow",  "unit": "usd",    "archetypes": [3],          "tiers": [2, 3, 4], "sanity_min": 0,   "sanity_max": 1e11},
    "actual_buyback_tokens":      {"label": "Actual buyback tokens (observed)", "kind": "flow", "unit": "tokens", "archetypes": [3],          "tiers": [2, 3, 4], "sanity_min": 0,   "sanity_max": 1e12},
    "buyback_fund_balance":       {"label": "Buyback fund balance",            "kind": "stock", "unit": "tokens", "archetypes": [3],          "tiers": [2],    "sanity_min": 0,    "sanity_max": 1e15},
    "locked_tokens":              {"label": "Tokens locked (ve)",              "kind": "stock", "unit": "tokens", "archetypes": [3],          "tiers": [2, 3, 4], "sanity_min": 0,   "sanity_max": 1e15},
    "avg_lock_duration_days":     {"label": "Average lock duration",           "kind": "stock", "unit": "days",   "archetypes": [3],          "tiers": [2, 3, 4], "sanity_min": 0,   "sanity_max": 1830},
    # --- off-chain operational (archetype 2)
    "supply_units":               {"label": "Supply units (nodes/hotspots/GPUs)", "kind": "stock", "unit": "units", "archetypes": [2],        "tiers": [5, 3], "sanity_min": 0,    "sanity_max": 1e8},
    "utilisation_pct":            {"label": "Capacity utilisation",            "kind": "stock", "unit": "pct",    "archetypes": [2],          "tiers": [5, 3], "sanity_min": 0,    "sanity_max": 1.0},
    "customer_revenue_usd":       {"label": "End-user revenue",                "kind": "flow",  "unit": "usd",    "archetypes": [2],          "tiers": [5, 3, 1], "sanity_min": 0,  "sanity_max": 1e11},
    "publisher_conviction_usd":   {"label": "Publisher Conviction (pre-purchased demand)", "kind": "stock", "unit": "usd", "archetypes": [2], "tiers": [5, 3], "sanity_min": 0,   "sanity_max": 1e10, "only_projects": ["OriginTrail"]},
    # --- self-reported headline figures (tier 3, taken as published and cited)
    "net_mint_monthly":           {"label": "Net mint, self-reported monthly", "kind": "flow",  "unit": "tokens", "archetypes": [4],          "tiers": [3],    "sanity_min": -1e10, "sanity_max": 1e10},
    # only_projects restricts a metric to named projects even when its archetypes match.
}

MANUAL_ONLY_METRICS: list[str] = []   # nothing is manual-only by design; unresolved metrics go to the Gap Report

# ERC-20 / vote-escrow read kinds the tier 2 adapter understands.
# Every read self-checks symbol() against expected_symbol before accepting a value.
CONTRACT_KINDS = {
    "erc20_total_supply":   {"metric": "total_supply",         "call": "totalSupply", "scale": "decimals"},
    "erc20_balance":        {"metric": None,                   "call": "balanceOf",   "scale": "decimals"},
    "burn_address_balance": {"metric": "burn_address_balance", "call": "balanceOf",   "scale": "decimals"},
    "ve_total_supply":      {"metric": "locked_tokens",        "call": "totalSupply", "scale": "decimals"},
    "buyback_fund_balance": {"metric": "buyback_fund_balance", "call": "balanceOf",   "scale": "decimals"},
}

# Public RPC endpoints. Fallback list per chain so one provider going down doesn't kill the run.
# Override per chain in .env, e.g. RPC_ETHEREUM=https://...  (comma-separated for a custom list).
DEFAULT_RPC = {
    "ethereum": [
        "https://ethereum-rpc.publicnode.com",
        "https://eth.llamarpc.com",
        "https://rpc.ankr.com/eth",
        "https://cloudflare-eth.com",
    ],
    "bsc": [
        "https://bsc-rpc.publicnode.com",
        "https://bsc-dataseed.binance.org",
        "https://bsc-dataseed1.defibit.io",
    ],
    "base": [
        "https://base-rpc.publicnode.com",
        "https://mainnet.base.org",
        "https://base.llamarpc.com",
    ],
    "arbitrum": [
        "https://arbitrum-one-rpc.publicnode.com",
        "https://arb1.arbitrum.io/rpc",
    ],
    "optimism": [
        "https://optimism-rpc.publicnode.com",
        "https://mainnet.optimism.io",
    ],
}

# The standard EVM dead addresses. Tokens sent here are unrecoverable.
BURN_ADDRESSES = {
    "dead": "0x000000000000000000000000000000000000dEaD",
    "zero": "0x0000000000000000000000000000000000000000",
}

# ---------------------------------------------------------------------------------------
# Contract-address confidence.
#
# The brief is explicit: verify every address against the PROTOCOL'S OWN documentation
# before use; do not accept an address from a third-party aggregator.
#
# Addresses below carry verified: None and confidence: "model-knowledge" — they came from
# the assistant's training data, which is effectively a third-party aggregator of uncertain
# vintage. They are NOT verified.
#
# Default behaviour: the tier 2 adapter REFUSES to read from an unverified address and
# writes a Gap Report row naming it instead. Set TOKEN_METRICS_ALLOW_UNVERIFIED=1 to read
# from them anyway; every resulting value is then flagged address_unverified in the
# workbook and listed in the Review Queue.
#
# Regardless of that flag, every ERC-20 read calls symbol() and rejects the address if the
# symbol does not match expected_symbol — an on-chain self-check that catches a wrong
# address before a plausible-looking wrong number reaches the sheet.
#
# To promote an address: confirm it on the protocol's own docs, set source_url to that page
# and verified to the date you checked.
# ---------------------------------------------------------------------------------------
UNVERIFIED = None


def _contract(address, chain, kind, expected_symbol, source_url, verified=UNVERIFIED, note=""):
    """Data-only helper. verified=None means NOT checked against protocol docs."""
    return {
        "address": address,
        "chain": chain,
        "kind": kind,
        "expected_symbol": expected_symbol,
        "source_url": source_url,
        "verified": verified,
        "confidence": "verified" if verified else "model-knowledge",
        "note": note,
    }


DUNE_QUERY_TEMPLATE = {"query_id": None, "date_col": "day", "value_col": "value"}


def _dune(*metrics):
    """Data-only: a backfill slot per metric. Fill query_id when the Dune query exists.

    Tier 4 is BACKFILL ONLY — these run on first sight of a series and are skipped once the
    store holds its history. A slot with query_id None is reported in the Gap Report, never
    silently ignored and never fabricated.
    """
    return {m: dict(DUNE_QUERY_TEMPLATE) for m in metrics}


_NO_SPLIT = {
    "share_to_buyback": None,
    "source_url": None,
    "source_date": None,
    "programmed": None,
    "status": "n/a",
}

# =======================================================================================
# THE UNIVERSE. Primary archetype listed first. Assets appear on every tab they subscribe to.
# =======================================================================================
PROJECTS = [
    # ------------------------------------------------------------------ Archetype 1 (+4)
    {
        "name": "Bitcoin", "symbol": "BTC",
        "coingecko_id": "bitcoin",
        "defillama_fees_slug": "bitcoin", "defillama_protocol": None, "defillama_chain": "Bitcoin",
        "archetypes": [1], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": 0.0, "source_url": "https://bitcoin.org/bitcoin.pdf", "source_date": BRIEF_DATE, "status": "active"},
        "issuance_schedule": {
            "steps": [{"from": "2024-04-20", "tokens_per_day": 3.125 * 144}],
            "source_url": "https://en.bitcoin.it/wiki/Controlled_supply", "source_date": BRIEF_DATE, "status": "active",
            "note": "3.125 BTC/block x ~144 blocks/day, 4th halving 2024-04-20.",
        },
        "contracts": {},
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("tx_count", "active_addresses"),
        "materiality": "high",
        "notes": "Issuance schedule only. No burn, no buyback.",
    },
    {
        "name": "Ethereum", "symbol": "ETH",
        "coingecko_id": "ethereum",
        "defillama_fees_slug": "ethereum", "defillama_protocol": None, "defillama_chain": "Ethereum",
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://eips.ethereum.org/EIPS/eip-1559", "source_date": BRIEF_DATE, "status": "active",
                       "note": "EIP-1559 destroys the base fee — it is not sent to an address, so there is no burn-address "
                               "balance to read. Gross burn needs tier 3 (ultrasound.money publishes it) or tier 4."},
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
        "materiality": "high",
        "notes": "EIP-1559 base fee burn. Net issuance = validator issuance - base fee burn.",
    },
    {
        "name": "Solana", "symbol": "SOL",
        "coingecko_id": "solana",
        "defillama_fees_slug": "solana", "defillama_protocol": None, "defillama_chain": "Solana",
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://solana.com/docs/economics", "source_date": BRIEF_DATE, "status": "unconfirmed",
                       "note": "Partial fee burn (historically 50% of the base fee). SIMD-96 changed priority-fee handling. "
                               "CONFIRM the current effective share before enabling the tier 1 rule."},
        "issuance_schedule": None,
        "contracts": {},   # not EVM — tier 2 web3 path does not apply
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
        "materiality": "high",
        "notes": "Partial fee burn — confirm current share. Non-EVM, so no tier 2 contract read.",
    },
    {
        "name": "Tron", "symbol": "TRX",
        "coingecko_id": "tron",
        "defillama_fees_slug": "tron", "defillama_protocol": None, "defillama_chain": "Tron",
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://developers.tron.network/docs/resource-model", "source_date": BRIEF_DATE, "status": "unconfirmed",
                       "note": "TRX paid for bandwidth/energy is burned. Document the share before enabling the tier 1 rule."},
        "issuance_schedule": None,
        "contracts": {},   # TVM, not EVM-compatible via web3.py's standard JSON-RPC
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
        "materiality": "high",
        "notes": "Tronscan publishes a running burn total — a tier 3 candidate.",
    },
    {
        "name": "Near", "symbol": "NEAR",
        "coingecko_id": "near",
        "defillama_fees_slug": "near", "defillama_protocol": None, "defillama_chain": "Near",
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://docs.near.org/protocol/gas", "source_date": BRIEF_DATE, "status": "unconfirmed",
                       "note": "Protocol docs describe a 70% burn / 30% contract-developer split of gas. CONFIRM before enabling."},
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
        "materiality": "medium",
        "notes": "Confirm burn share.",
    },
    {
        "name": "Canton", "symbol": "CC",
        "coingecko_id": "canton-network",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://www.canton.network/", "source_date": BRIEF_DATE, "status": "unconfirmed",
                       "note": "Traffic fees are burned. Has both its own dashboard and a Dune page. Document the share."},
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "fees_usd", "tx_count", "active_addresses"),
        "materiality": "medium",
        "notes": "Not on DefiLlama. Own dashboard (tier 3) + Dune page (tier 4). Verify the CoinGecko id.",
    },
    {
        "name": "Plume", "symbol": "PLUME",
        "coingecko_id": "plume",
        "defillama_fees_slug": "plume", "defillama_protocol": None, "defillama_chain": "Plume Mainnet",
        "archetypes": [1, 3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.plume.org/", "source_date": BRIEF_DATE, "programmed": None, "status": "unconfirmed",
                      "note": "Staking yield; no documented revenue-to-buyback split."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("gross_issuance_tokens", "staked_tokens", "emissions_tokens", "tx_count", "active_addresses"),
        "materiality": "medium",
        "notes": "RWA-on-chain is the key demand metric. Pull RWA.xyz and the DefiLlama RWA category and show the divergence.",
    },
    {
        "name": "Injective", "symbol": "INJ",
        "coingecko_id": "injective-protocol",
        "defillama_fees_slug": "injective", "defillama_protocol": None, "defillama_chain": "Injective",
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://injective.com/burn/", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Monthly Community BuyBack plus the weekly burn auction; committed INJ permanently burned. "
                               "Injective publishes the running burn total — take the self-reported figure (tier 3)."},
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
        "materiality": "medium",
        "notes": "Monthly Community BuyBack: committed INJ permanently burned.",
    },
    {
        "name": "Zcash", "symbol": "ZEC",
        "coingecko_id": "zcash",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [1], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": 0.0, "source_url": "https://z.cash/", "source_date": BRIEF_DATE, "status": "active"},
        "issuance_schedule": {
            "steps": [{"from": "2024-11-23", "tokens_per_day": 1.5625 * 1152}],
            "source_url": "https://zips.z.cash/protocol/protocol.pdf", "source_date": BRIEF_DATE, "status": "active",
            "note": "1.5625 ZEC/block x 1152 blocks/day, 2nd halving 2024-11-23.",
        },
        "contracts": {},
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("tx_count", "active_addresses"),
        "materiality": "medium",
        "notes": "No burn. Issuance schedule read only.",
    },
    {
        "name": "Chainlink", "symbol": "LINK",
        "coingecko_id": "chainlink",
        "defillama_fees_slug": "chainlink", "defillama_protocol": "chainlink", "defillama_chain": None,
        "archetypes": [1, 3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://blog.chain.link/chainlink-reserve/", "source_date": BRIEF_DATE, "programmed": False, "status": "unconfirmed",
                      "note": "Payment Abstraction / SVR route revenue to the Chainlink Reserve and to staking. VERIFY the split."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {
            "token": _contract("0x514910771AF9Ca656af840dff83E8264EcF986CA", "ethereum", "erc20_total_supply", "LINK",
                               "https://docs.chain.link/resources/link-token-contracts"),
        },
        "buyback_destination": "hold", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "staked_tokens", "emissions_tokens"),
        "materiality": "high",
        "notes": "Chainlink Reserve balance is a tier 2/3 candidate once the address is documented.",
    },
    # ------------------------------------------------------------------ Archetype 2 (+3/+4)
    {
        "name": "World Mobile", "symbol": "WMTX",
        "coingecko_id": "world-mobile-token",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://worldmobile.io/", "source_date": BRIEF_DATE, "programmed": False, "status": "unconfirmed",
                      "note": "Programmatic revenue buyback. Their metrics page is being rebuilt — Token Terminal or manual meanwhile."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": {},
        "materiality": "low",
        "notes": "Page being rebuilt. Everything routes through manual_overrides.csv until it returns; all gaps listed in the Gap Report.",
    },
    {
        "name": "GEODNET", "symbol": "GEOD",
        "coingecko_id": "geodnet",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": 0.80, "source_url": "https://geodnet.com/tokenomics", "source_date": BRIEF_DATE, "status": "active",
                       "note": "80% of console (data) revenue buys back and burns GEOD. Confirmed burn; re-check the share against the current docs page."},
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("gross_burn_tokens", "emissions_tokens"),
        "materiality": "low",
        "notes": "Console revenue burn — confirmed. GEODNET publishes miner counts and burn on its own dashboard (tier 3/5).",
    },
    {
        "name": "peaq", "symbol": "PEAQ",
        "coingecko_id": "peaq-2",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": "peaq",
        "archetypes": [2], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("emissions_tokens", "tx_count", "active_addresses"),
        "materiality": "low",
        "notes": "Own dashboard (machine counts) + Dune page. Verify the CoinGecko id (peaq-2).",
    },
    {
        "name": "Render", "symbol": "RENDER",
        "coingecko_id": "render-token",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://know.rendernetwork.com/basics/burn-and-mint-equilibrium", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Burn-and-mint equilibrium: customer payments burned, node rewards minted. Render publishes burn/mint per epoch (tier 3)."},
        "issuance_schedule": None,
        "contracts": {},   # migrated to Solana SPL — no EVM read
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens"),
        "materiality": "medium",
        "notes": "Burn-and-mint equilibrium. Now Solana-native, so no tier 2 EVM read; use the published epoch figures.",
    },
    {
        "name": "Bittensor", "symbol": "TAO",
        "coingecko_id": "bittensor",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": "Bittensor",
        "archetypes": [2], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": None,
        "issuance_schedule": {
            "steps": [{"from": "2025-12-14", "tokens_per_day": 0.5 * 7200}],
            "source_url": "https://docs.bittensor.com/learn/bittensor-building-blocks", "source_date": BRIEF_DATE, "status": "active",
            "note": "0.5 TAO/block x 7200 blocks/day post-halving. CONFIRM the halving date against the protocol docs.",
        },
        "contracts": {},
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("emissions_tokens", "staked_tokens"),
        "materiality": "medium",
        "notes": "NOT archetype 3 — yield is emissions-funded, revenue capture unproven. ~70% staked is a float metric, not demand. "
                 "taostats has an API — the one archetype 2 name with a real programmatic source.",
    },
    {
        "name": "OriginTrail", "symbol": "TRAC",
        "coingecko_id": "origintrail",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 1.0, "source_url": "https://docs.origintrail.io/", "source_date": BRIEF_DATE, "programmed": True, "status": "active",
                      "note": "Publishing fees in TRAC distributed to nodes from usage, not inflation. Revenue is small and not publicly tracked."},
        "burn_split": None,
        "issuance_schedule": {"steps": [{"from": "2018-01-01", "tokens_per_day": 0.0}],
                              "source_url": "https://docs.origintrail.io/", "source_date": BRIEF_DATE, "status": "active",
                              "note": "500m fixed cap, zero inflation."},
        "contracts": {
            "token": _contract("0xaA7a9CA87d3694B5755f213B5D04094b8d0F0A6F", "ethereum", "erc20_total_supply", "TRAC",
                               "https://docs.origintrail.io/"),
        },
        "dune_queries": _dune("emissions_tokens", "staked_tokens"),
        "materiality": "low",
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "notes": "Materiality LOW: qualifies mechanically, not comparable to Aave or Hyperliquid. Also pull Publisher Conviction.",
    },
    {
        "name": "Aethir", "symbol": "ATH",
        "coingecko_id": "aethir",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2], "archetypes_held": [3],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.aethir.com/", "source_date": BRIEF_DATE, "programmed": False, "status": "unconfirmed",
                      "note": "Sources conflict: a stated plan to use compute revenue for buybacks against a June 2026 review stating "
                              "no fee distribution, no buy-and-burn and no revenue sharing. The Checker Node buyback is an NFT "
                              "repurchase, not a token buyback."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "disputed", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("emissions_tokens", "staked_tokens"),
        "materiality": "medium",
        "notes": "HOLD archetype 3 until documented.",
    },
    {
        "name": "Venice AI", "symbol": "VVV",
        "coingecko_id": "venice-token",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 3, 4], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://venice.ai/blog", "source_date": BRIEF_DATE, "programmed": False, "status": "unconfirmed",
                      "note": "Revenue-funded monthly buy-and-burn; the revenue share applied is not documented as a fixed %. "
                              "Use the observed buyback rather than an implied split."},
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://venice.ai/blog", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Bought-back VVV is burned."},
        "issuance_schedule": {
            "steps": [
                {"from": "2025-01-27", "tokens_per_day": 14_000_000 / 365},
                {"from": "2026-02-01", "tokens_per_day": 6_000_000 / 365},
                {"from": "2026-05-01", "tokens_per_day": 5_000_000 / 365},
                {"from": "2026-07-01", "tokens_per_day": 3_000_000 / 365},
            ],
            "source_url": "https://venice.ai/blog", "source_date": BRIEF_DATE, "status": "active",
            "note": "Stepped 14m -> 6m (Feb 2026) -> 5m (May) -> 3m (Jul) VVV/yr.",
        },
        "contracts": {},
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "gross_burn_tokens", "emissions_tokens", "staked_tokens"),
        "materiality": "medium",
        "notes": "100% of emissions to stakers AND a revenue-funded buy-and-burn. Even at 3m VVV/yr, issuance runs several multiples "
                 "of what stated revenue could fund. The clearest case in the universe for showing buyback and emissions together.",
    },
    # ------------------------------------------------------------------ Archetype 3 (+4/+1)
    {
        "name": "Maple", "symbol": "SYRUP",
        "coingecko_id": "syrup",
        "defillama_fees_slug": "maple", "defillama_protocol": "maple", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://maple.finance/", "source_date": BRIEF_DATE, "programmed": False, "status": "unconfirmed",
                      "note": "Buyback programme exists; the share of revenue is not documented."},
        "burn_split": {"share_of_fees_burned": 0.0, "source_url": "https://maple.finance/", "source_date": BRIEF_DATE, "status": "active", "note": "No burn — confirmed."},
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens"),
        "materiality": "medium",
        "notes": "No burn — confirmed.",
    },
    {
        "name": "Morpho", "symbol": "MORPHO",
        "coingecko_id": "morpho",
        "defillama_fees_slug": "morpho", "defillama_protocol": "morpho", "defillama_chain": None,
        "archetypes": [2], "archetypes_held": [3],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.morpho.org/", "source_date": BRIEF_DATE, "programmed": False, "status": "unconfirmed",
                      "note": "Fee switch status needs confirming before the archetype 3 block is enabled."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "hold", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens"),
        "materiality": "high",
        "notes": "Fee switch status needs confirming before the 3 block is enabled.",
    },
    {
        "name": "Hyperliquid", "symbol": "HYPE",
        "coingecko_id": "hyperliquid",
        "defillama_fees_slug": "hyperliquid", "defillama_protocol": "hyperliquid", "defillama_chain": "Hyperliquid L1",
        "archetypes": [3, 1], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 0.98, "source_url": "https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/assistance-fund", "source_date": BRIEF_DATE, "programmed": True, "status": "active",
                      "note": "Midpoint of the stated 97-99% of net protocol fees to the Assistance Fund."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {},   # Assistance Fund address not known to a verifiable standard — see Gap Report
        "buyback_destination": "disputed", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens", "tx_count", "active_addresses", "gross_issuance_tokens"),
        "materiality": "high",
        "notes": "Assistance Fund absorbs 97-99% of net protocol fees. Own L1 + HyperEVM hence the 1 block. "
                 "Sources conflict on whether purchased HYPE is burned or held — destination disputed. "
                 "The Assistance Fund address is the single highest-value tier 2 read in the universe; it needs documenting.",
    },
    {
        "name": "Uniswap", "symbol": "UNI",
        "coingecko_id": "uniswap",
        "defillama_fees_slug": "uniswap", "defillama_protocol": "uniswap", "defillama_chain": None,
        "archetypes": [4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://gov.uniswap.org/", "source_date": BRIEF_DATE, "status": "unconfirmed",
                       "note": "Burn-to-claim via Token Jars / fire pit. Fee routing is deterministic; realised burn depends on holder "
                               "redemption. Post-activation burns ~4-5m UNI/yr, separate from the 100m one-off in Jan 2026. "
                               "An implied figure also needs fees SPLIT BY VERSION, which is DefiLlama Pro — see the Gap Report."},
        "issuance_schedule": None,
        "contracts": {
            "token": _contract("0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", "ethereum", "erc20_total_supply", "UNI",
                               "https://docs.uniswap.org/contracts/v3/reference/deployments"),
        },
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "holder_elected",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens"),
        "materiality": "high",
        "notes": "Archetype 4 only — no distribution leg, no staking yield. Implied and actual burn diverge for reasons unrelated "
                 "to revenue, because the burn is holder-elected.",
    },
    {
        "name": "Aerodrome", "symbol": "AERO",
        "coingecko_id": "aerodrome-finance",
        "defillama_fees_slug": "aerodrome", "defillama_protocol": "aerodrome", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 1.0, "source_url": "https://aerodrome.finance/docs", "source_date": BRIEF_DATE, "programmed": True, "status": "active",
                      "note": "100% of trading fees to veAERO voters — DISTRIBUTE, not buyback-and-burn. Yield destination tracked separately."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {
            "token": _contract("0x940181a94A35A4569E4529A3CDfB74e38FD98631", "base", "erc20_total_supply", "AERO",
                               "https://aerodrome.finance/docs"),
            "ve": _contract("0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4", "base", "ve_total_supply", "veAERO",
                            "https://aerodrome.finance/docs", note="veAERO lock rate — the required input for effective float."),
        },
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("locked_tokens", "avg_lock_duration_days", "emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens"),
        "materiality": "high",
        "notes": "veAERO — lock rate and average lock duration are required inputs.",
    },
    {
        "name": "PancakeSwap", "symbol": "CAKE",
        "coingecko_id": "pancakeswap-token",
        "defillama_fees_slug": "pancakeswap", "defillama_protocol": "pancakeswap", "defillama_chain": None,
        "archetypes": [4, 3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.pancakeswap.finance/governance-and-tokenomics/cake-tokenomics", "source_date": BRIEF_DATE, "programmed": False, "status": "unconfirmed",
                      "note": "Buyback share varies by product. Use the PUBLISHED monthly net mint rather than an implied split."},
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://docs.pancakeswap.finance/governance-and-tokenomics/cake-tokenomics", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Best-disclosed net burn in the universe. Publishes net mint monthly (May 2026: -1,958,514 CAKE, "
                               "33rd consecutive month). Hard cap cut 450m -> 400m Jan 2026. TAKE THE SELF-REPORTED FIGURE."},
        "issuance_schedule": None,
        "contracts": {
            "token": _contract("0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", "bsc", "erc20_total_supply", "Cake",
                               "https://docs.pancakeswap.finance/governance-and-tokenomics/cake-tokenomics"),
            "burn_dead": _contract("0x000000000000000000000000000000000000dEaD", "bsc", "burn_address_balance", "Cake",
                                   "https://docs.pancakeswap.finance/governance-and-tokenomics/cake-tokenomics",
                                   note="CAKE sent to the standard dead address. balanceOf is called on the CAKE token, not this address."),
        },
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens", "staked_tokens"),
        "materiality": "high",
        "notes": "TEMPLATE for the archetype 4 tab. Self-reported net mint is the headline; totalSupply delta is the independent check.",
    },
    {
        "name": "Sky", "symbol": "SKY",
        "coingecko_id": "sky",
        "defillama_fees_slug": "sky", "defillama_protocol": "sky", "defillama_chain": None,
        "archetypes": [3, 4], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.sky.money/", "source_date": BRIEF_DATE, "programmed": False, "status": "unconfirmed",
                      "note": "Smart Burn Engine ~$1m/day is a governance-set spend, not a fixed % of revenue. Use the observed buyback."},
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://docs.sky.money/", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Repurchased SKY is burned OR redistributed to staked SKY per a GOVERNANCE PARAMETER. "
                               "Never net staking rewards against burn — set destination_split when documented."},
        "issuance_schedule": None,
        "contracts": {
            "token": _contract("0x56072C95FAA701256059aa122697B133aDEd9279", "ethereum", "erc20_total_supply", "SKY",
                               "https://docs.sky.money/"),
        },
        "buyback_destination": "split", "destination_split": None, "burn_execution": "protocol",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens", "staked_tokens"),
        "materiality": "high",
        "notes": "Smart Burn Engine ~$1m/day against ~838m SKY staking rewards over 180 days. The two legs must stay separate.",
    },
    {
        "name": "Pendle", "symbol": "PENDLE",
        "coingecko_id": "pendle",
        "defillama_fees_slug": "pendle", "defillama_protocol": "pendle", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 1.0, "source_url": "https://docs.pendle.finance/ProtocolMechanics/Mechanisms/vePENDLE", "source_date": BRIEF_DATE, "programmed": True, "status": "active",
                      "note": "Protocol revenue distributed to vePENDLE holders — DISTRIBUTE, not burn."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {
            "token": _contract("0x808507121B80c02388fAd14726482e061B8da827", "ethereum", "erc20_total_supply", "PENDLE",
                               "https://docs.pendle.finance/Developers/Contracts/Deployments"),
            "ve": _contract("0x4f30A9D41B80ecC5B94306AB4364951AE3170210", "ethereum", "ve_total_supply", "vePENDLE",
                            "https://docs.pendle.finance/Developers/Contracts/Deployments", note="vePENDLE lock rate — required input."),
        },
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("locked_tokens", "avg_lock_duration_days", "emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens"),
        "materiality": "high",
        "notes": "vePENDLE lock rate required.",
    },
    {
        "name": "Fluid", "symbol": "FLUID",
        "coingecko_id": "instadapp",
        "defillama_fees_slug": "fluid", "defillama_protocol": "fluid", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.fluid.io/", "source_date": BRIEF_DATE, "programmed": False, "status": "unconfirmed",
                      "note": "Governance-approved buyback programme; the revenue share is not documented here."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "hold", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens"),
        "materiality": "medium",
        "notes": "CoinGecko id is still instadapp after the rebrand — verify.",
    },
    {
        "name": "Aave", "symbol": "AAVE",
        "coingecko_id": "aave",
        "defillama_fees_slug": "aave", "defillama_protocol": "aave", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 1.0, "source_url": "https://governance.aave.com/", "source_date": BRIEF_DATE, "programmed": True, "status": "paused",
                      "note": "Aavenomics 3.0: 100% of Aave Protocol and GHO revenue into immutable non-discretionary buybacks. "
                              "BUT the budget was cut ~$50m -> ~$30m in March 2026 and buybacks were PAUSED from 19 April 2026 "
                              "after the rsETH bridge incident. programmed=True describes the routing rule; the BUDGET is "
                              "governance-set, which is how an immutable mechanism still got paused."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {
            "token": _contract("0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9", "ethereum", "erc20_total_supply", "AAVE",
                               "https://aave.com/docs"),
            "staking": _contract("0x4da27a545c0c5B758a6BA100e3a049001de870f5", "ethereum", "ve_total_supply", "stkAAVE",
                                 "https://aave.com/docs", note="stkAAVE — destination is stakers, NOT burn."),
        },
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens"),
        "materiality": "high",
        "notes": "Destination is stakers, not burn. Paused 2026-04-19.",
        "status_changes": [
            {"date": "2026-03-01", "event": "Buyback budget cut from ~$50m to ~$30m", "source_url": "https://governance.aave.com/"},
            {"date": "2026-04-19", "event": "Buybacks paused after the rsETH bridge incident", "source_url": "https://governance.aave.com/"},
        ],
    },
    {
        "name": "Ether.fi", "symbol": "ETHFI",
        "coingecko_id": "ether-fi",
        "defillama_fees_slug": "ether.fi", "defillama_protocol": "ether.fi", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://etherfi.gitbook.io/etherfi", "source_date": BRIEF_DATE, "programmed": False, "status": "unconfirmed",
                      "note": "Buyback confirmed as a governance-approved programme; the revenue share is not documented here."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens"),
        "materiality": "medium",
        "notes": "Buyback confirmed.",
    },
]

PROJECT_BY_NAME = {p["name"]: p for p in PROJECTS}


def metrics_for_project(project: dict) -> list[str]:
    """Metric keys that apply to a project, from its archetypes. Data-only derivation."""
    arch = set(project["archetypes"])
    out = []
    for key, m in METRICS.items():
        only = m.get("only_projects")
        if only and project["name"] not in only:
            continue
        if not m["archetypes"] or arch & set(m["archetypes"]):
            out.append(key)
    return out


def sanity_bounds(project_name: str, metric: str) -> tuple[float | None, float | None]:
    """Per-project override, else the metric-library default."""
    m = METRICS.get(metric, {})
    lo, hi = m.get("sanity_min"), m.get("sanity_max")
    p = PROJECT_BY_NAME.get(project_name) or {}
    override = (p.get("sanity") or {}).get(metric)
    if override:
        lo = override.get("min", lo)
        hi = override.get("max", hi)
    return lo, hi


def change_threshold_pct(project_name: str, metric: str) -> float:
    p = PROJECT_BY_NAME.get(project_name) or {}
    override = (p.get("sanity") or {}).get(metric) or {}
    return float(override.get("change_threshold_pct", GLOBALS["default_change_threshold_pct"]))
