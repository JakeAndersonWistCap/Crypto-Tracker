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
  fee_split.programmed      — True (contract-enforced) | False (revisable by forum post)
                              | "unconfirmed_conflict" (sources genuinely disagree — Aave)
  fee_split.share_to_buyback— a float, OR a dict of per-product shares (PancakeSwap), OR None.
                              A dict is NEVER collapsed to one number; the workbook shows each
                              product separately and suppresses the single implied figure.
  fee_split.history         — splits change. An ordered list of periods, each with its own
                              share, status and source. A period we have not documented is
                              status "unconfirmed" and its derived figure is SUPPRESSED — the
                              current split is never applied retroactively across a backfill.
  buyback_destination       — burn | distribute | split | hold | disputed | unconfirmed
  destination_split         — share going to burn where destination is "split"
  burn_execution            — protocol | holder_elected | n/a
  materiality               — high | medium | low
  self_reported_net_mint    — True where the protocol publishes net mint itself. Per the build
                              spec, the self-reported figure is PREFERRED over the derived
                              calculation wherever both exist.
  revenue_sources           — additional revenue legs with their own status. A leg that has
                              started accruing but has not yet paid carries booked=False and is
                              excluded from revenue, so the model cannot book money that has
                              not landed.

BURNING HAPPENS TWO DIFFERENT WAYS. They need different adapters and must never be conflated:

  TRANSFER BURN   tokens move to an address no one controls. Readable as a balance.
                  burn_read_method "transfer", and the address gets a contract entry.
  PROTOCOL BURN   supply is destroyed at the protocol level with no transfer. NOT readable as
                  an address balance. burn_read_method "protocol_level", burn_address None.
                  Reading a dead address here returns other people's discarded tokens, not the
                  protocol burn. Needs a chain-data or dashboard source instead.
  UNDETERMINED    we do not yet know which mechanism the current burn uses. burn_read_method
                  "undetermined" — nothing is read until it is resolved.

Where a split's status is "unconfirmed" the workbook greys the cell and SUPPRESSES the
derived figure. Never estimate a split we have not documented.
"""

BRIEF_DATE = "2026-09-11"
GEODNET_BURN_QUERY = "https://dune.com/queries/8683175"
TODAY_VERIFIED = "2026-09-11"
UNISWAP_FEE_DEPLOYMENTS = "https://docs.uniswap.org/contracts/protocol-fee/deployments"
# Pendle's own deployment file — the primary source for every Pendle mainnet address.
PENDLE_DEPLOYMENTS_1_CORE = "https://raw.githubusercontent.com/pendle-finance/pendle-core-v2-public/main/deployments/1-core.json"   # date the parameters below were set from the working brief

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
    "staked_tokens":              {"label": "Staked tokens",                   "kind": "stock", "unit": "tokens", "archetypes": [1, 2, 3],    "tiers": [2, 3, 4], "sanity_min": 0,   "sanity_max": 1e15},
    "emissions_tokens":           {"label": "Emissions to suppliers/stakers",  "kind": "flow",  "unit": "tokens", "archetypes": [2, 3],       "tiers": [1, 3, 4], "sanity_min": 0,   "sanity_max": 1e12},
    "actual_buyback_usd":         {"label": "Actual buyback (observed)",       "kind": "flow",  "unit": "usd",    "archetypes": [3],          "tiers": [2, 3, 4], "sanity_min": 0,   "sanity_max": 1e11},
    "actual_buyback_tokens":      {"label": "Actual buyback tokens (observed)", "kind": "flow", "unit": "tokens", "archetypes": [3],          "tiers": [2, 3, 4], "sanity_min": 0,   "sanity_max": 1e12},
    "buyback_fund_balance":       {"label": "Buyback fund balance",            "kind": "stock", "unit": "tokens", "archetypes": [3],          "tiers": [2],    "sanity_min": 0,    "sanity_max": 1e15},
    # The same figure as published on the protocol's own dashboard. Stored SEPARATELY so the two can
    # be compared: the contract read is preferred and the dashboard is a cross-check, with any
    # divergence flagged rather than one being silently picked.
    "buyback_fund_balance_dashboard": {"label": "Buyback fund balance (protocol dashboard, cross-check)", "kind": "stock", "unit": "tokens", "archetypes": [3], "tiers": [3], "sanity_min": 0, "sanity_max": 1e15},
    # Same pattern for lock rates. Where a project has BOTH a contract read and a published page,
    # the page is stored here rather than over the contract read: a tier 3 page must never
    # overwrite a verified tier 2 contract figure, it cross-checks it.
    "locked_tokens_dashboard": {"label": "Tokens locked (protocol dashboard, cross-check)", "kind": "stock", "unit": "tokens", "archetypes": [3], "tiers": [3, 4], "sanity_min": 0, "sanity_max": 1e15},
    "locked_tokens":              {"label": "Tokens locked (ve)",              "kind": "stock", "unit": "tokens", "archetypes": [3],          "tiers": [2, 3, 4], "sanity_min": 0,   "sanity_max": 1e15},
    "avg_lock_duration_days":     {"label": "Average lock duration",           "kind": "stock", "unit": "days",   "archetypes": [3],          "tiers": [2, 3, 4], "sanity_min": 0,   "sanity_max": 1830},
    # Aave's two staking pages are NOT parallel, and treating them as such overstated AAVE float.
    #   app.aave.com/safety-module  = the LEGACY Safety Module. AAVE and ABPT staked on Ethereum,
    #                                 producing stkAAVE and stkABPT. THIS is the AAVE lock rate,
    #                                 and it feeds locked_tokens like every other project's.
    #   app.aave.com/staking        = UMBRELLA. Stakes aTokens (aUSDC, aUSDT, aWETH) and GHO, NOT
    #                                 AAVE. It says nothing about AAVE float, so it is NOT stored as
    #                                 an AAVE staked or lock figure under any label. It is kept only
    #                                 as a separate protocol-risk measure, in dollars, and is
    #                                 excluded from every supply and float calculation.
    "umbrella_staked_usd": {"label": "Umbrella staked (aTokens + GHO) — protocol risk cover, NOT AAVE supply",
                            "kind": "stock", "unit": "usd", "archetypes": [3], "tiers": [3],
                            "sanity_min": 0, "sanity_max": 1e11, "only_projects": ["Aave"],
                            "excluded_from_supply": True},
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
    # Solana reads. NOT served by the EVM adapter — they need a Solana RPC adapter, and the
    # Gap Report says so rather than the read failing obscurely.
    "spl_mint":             {"metric": "total_supply",         "call": "getTokenSupply",        "scale": "decimals"},
    "spl_token_account":    {"metric": "burn_address_balance", "call": "getTokenAccountBalance", "scale": "decimals"},
    "erc20_balance":        {"metric": None,                   "call": "balanceOf",   "scale": "decimals"},
    "burn_address_balance": {"metric": "burn_address_balance", "call": "balanceOf",   "scale": "decimals"},
    "ve_total_supply":      {"metric": "locked_tokens",        "call": "totalSupply", "scale": "decimals"},
    "buyback_fund_balance": {"metric": "buyback_fund_balance", "call": "balanceOf",   "scale": "decimals"},
}

# Some protocols publish their canonical addresses as a file in their own public repository, which
# is a primary source and is usually easier to reach than their docs site. Pendle's
# deployments/1-core.json is the worked example — see PENDLE_DEPLOYMENTS_1_CORE.

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
    "polygon": [
        "https://polygon-bor-rpc.publicnode.com",
        "https://polygon-rpc.com",
    ],
    "unichain": [
        "https://mainnet.unichain.org",
        "https://unichain-rpc.publicnode.com",
    ],
}

# Chains the tier 2 EVM adapter can read. Anything else needs its own adapter, and the gap
# report says so rather than the adapter failing obscurely.
EVM_CHAINS = set(DEFAULT_RPC)

# HOW a lock/escrow figure is read. Getting this wrong produces a number that is orders of
# magnitude off and still looks entirely plausible in a cell, which is the worst kind of error.
#
#   erc20_total_supply   totalSupply() on an ERC-20 staking token. Correct where the staked
#                        position is itself a fungible ERC-20 (stkAAVE-style).
#   escrow_balance_of    TOKEN.balanceOf(<escrow address>) — the amount of the underlying token
#                        held by the escrow. This is the correct read for a vote-escrow whose
#                        position is an NFT.
#   None                 NOT ESTABLISHED. The adapter refuses to read, because assuming ERC-20
#                        semantics for a veNFT is exactly how a position COUNT ends up in a cell
#                        labelled "tokens locked".
LOCK_READ_METHODS = {
    "erc20_total_supply": "totalSupply() on a fungible staking token",
    "escrow_balance_of": "underlying.balanceOf(escrow) — the correct read for an NFT-based vote escrow",
}

# A veNFT is ERC-721: its totalSupply() returns the NUMBER OF POSITIONS, not tokens locked.
# Pairing erc721 with erc20_total_supply is a hard config error and is rejected at load time.
TOKEN_STANDARDS = {"erc20", "erc721"}

BURN_READ_METHODS = {
    "transfer": "Tokens move to an address no one controls. Readable as a balance on the token contract.",
    "protocol_level": "Supply destroyed at the protocol level with no transfer. NOT readable as an address "
                      "balance — needs a chain-data or dashboard source.",
    "native_balance": "A native (non-ERC-20) balance on a chain the EVM adapter does not cover.",
    "protocol_api": "The protocol publishes the balance through its own HTTP API. No chain RPC involved.",
    "undetermined": "Mechanism not yet established. Nothing is read until it is resolved.",
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


def _contract(address, chain, kind, expected_symbol, source_url, verified=UNVERIFIED, note="",
              purpose="", provenance="model-knowledge", candidates=None, ambiguous=False,
              read_method=None, token_standard=None, underlying=None,
              supply_is_partial=False, partial_reason="", holder_has_code=None):
    """Data-only helper. verified=None means NOT checked against the protocol's own docs.

    candidates / ambiguous: where two or more addresses circulate publicly and we have not
    established which is correct, list them all and set ambiguous=True. The adapter then
    REFUSES to read any of them and writes a Gap Report row asking for resolution. Picking one
    on a guess is exactly the failure this design exists to prevent.
    """
    return {
        "address": address,
        "chain": chain,
        "kind": kind,
        "expected_symbol": expected_symbol,
        "source_url": source_url,
        "verified": verified,
        "confidence": "verified" if verified else provenance,
        "provenance": provenance,
        "purpose": purpose,
        "candidates": candidates or ([address] if address else []),
        "ambiguous": bool(ambiguous),
        # read_method / token_standard: see LOCK_READ_METHODS above. read_method None on a lock
        # contract means NOT ESTABLISHED, and the adapter refuses rather than assuming.
        "read_method": read_method,
        "token_standard": token_standard,
        "underlying": underlying,          # contract key whose balanceOf is called, for escrow_balance_of
        "supply_is_partial": bool(supply_is_partial),
        "partial_reason": partial_reason,
        # Is this address SUPPOSED to be a deployed contract? None means "work it out from the kind
        # and the address". A burn or dead address is an EOA nobody controls, so empty bytecode is
        # what CORRECT looks like there — checking for code would reject a perfectly good address.
        "holder_has_code": holder_has_code,
        "note": note,
    }


def _split_period(from_date, to_date, share, status, source_url=None, source_date=None,
                  destination_split=None, note=""):
    """One period of a fee split. from_date None means "everything before to_date"."""
    return {
        "from": from_date, "to": to_date, "share_to_buyback": share,
        "destination_split": destination_split, "status": status,
        "source_url": source_url, "source_date": source_date, "note": note,
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
        "destination_effect": "none",
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
        # PROTOCOL BURN, not transfer burn. Supply is destroyed with no transfer, so there is no
        # address balance to read and burn_address is deliberately None.
        "burn_address": None,
        "burn_read_method": "protocol_level",
        "burn_read_note": "EIP-1559 destroys the base fee at the protocol level. No transfer occurs, so there is NO burn address to read. Needs a chain-data or dashboard source (ultrasound.money publishes it).",
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "destination_effect": "removed_from_supply",
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
        "contracts": {},
        # PROTOCOL BURN, not transfer burn. Supply is destroyed with no transfer, so there is no
        # address balance to read and burn_address is deliberately None.
        "burn_address": None,
        "burn_read_method": "protocol_level",
        "burn_read_note": "Partial fee burn via the SPL burn instruction — supply is destroyed, not sent to a wallet. There is no burn address to read. Needs a chain-data or dashboard source.",   # not EVM — tier 2 web3 path does not apply
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "destination_effect": "removed_from_supply",
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
        # MECHANISM RESOLVED, and every black-hole address is the WRONG APPROACH. TRON burns TRX by
        # protocol rule when bandwidth/energy is insufficient, and TIP proposal #49 moved burned TRX
        # OUT of the black-hole address into DynamicPropertiesStore under the key BURN_TRX, precisely
        # so burns could be counted flexibly. A balance read on T9yD14... does NOT return the fee burn.
        "contracts": {},
        "burn_address": None,
        "burn_read_method": "protocol_level",
        "node_api": {
            "kind": "tron_burn_trx",
            "metric": "burn_address_balance",     # cumulative burned TRX; period burn by differencing
            "endpoints": ["https://api.trongrid.io"],
            "path": "/wallet/getburntrx",
            "response_keys": ["burnTrxAmount", "burnTrxAmountInSun", "amount"],
            "scale": 1e-6,                        # sun -> TRX
            "source_urls": ["https://developers.tron.network/docs/glossary",
                            "https://github.com/tronprotocol/tips/issues/234"],
            "verified": "2026-09-11",
            "note": "Read the BURN_TRX value from a TRON node (TronGrid or any node), NOT an address balance. "
                    "The endpoint path and response key are implementation detail and are configurable here; "
                    "if the first run returns nothing, check them against the node API docs.",
        },
        "burn_read_note": "Protocol-rule burn when bandwidth/energy is insufficient. TIP #49 moved burned TRX out of "
                          "the black-hole address into DynamicPropertiesStore under key BURN_TRX, so the read is a "
                          "node API call for BURN_TRX, not an address balance. The four black-hole addresses that "
                          "circulate are the wrong approach and have been removed.",
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "destination_effect": "removed_from_supply",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
        "materiality": "high",
        "notes": "TVM, not EVM-compatible via web3.py standard JSON-RPC. Burn is read from the node API key BURN_TRX "
                 "(TIP #49), not from any black-hole address.",
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
        # PROTOCOL BURN, not transfer burn. Supply is destroyed with no transfer, so there is no
        # address balance to read and burn_address is deliberately None.
        "burn_address": None,
        "burn_read_method": "protocol_level",
        "burn_read_note": "Execution fee burn at the protocol level, historically c.70% and moving toward 100%. CONFIRM the current share from docs.near.org. No address to read.",
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "destination_effect": "removed_from_supply",
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
        # PROTOCOL BURN, not transfer burn. Supply is destroyed with no transfer, so there is no
        # address balance to read and burn_address is deliberately None.
        "burn_address": None,
        "burn_read_method": "protocol_level",
        "burn_read_note": "Burn-mint equilibrium: CC is burned when Global Synchroniser traffic is purchased, priced in USD. Destroyed at the protocol level, no transfer. Needs the Canton dashboard or a Dune source.",
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "destination_effect": "removed_from_supply",
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
        "destination_effect": "yield_payout",
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
        # PROTOCOL BURN, not transfer burn. Supply is destroyed with no transfer, so there is no
        # address balance to read and burn_address is deliberately None.
        "burn_address": None,
        "burn_read_method": "protocol_level",
        "burn_read_note": "The auction and Community BuyBack modules destroy INJ on-chain with no transfer. DO NOT USE the 0x1111...1111 address that circulates publicly — it is a contribution subaccount, NOT a burn destination, and reading it would return the wrong number entirely.",
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "destination_effect": "removed_from_supply",
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
        "destination_effect": "none",
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
                               "https://docs.chain.link/resources/link-token-contracts",
                               verified="2026-09-11", provenance="protocol docs",
                               purpose="LINK token contract. CONFIRMED. Note this is only a SUPPLY read — it is not "
                                       "the revenue input for the archetype 3 block."),
            # THE address that matters for Chainlink's archetype 3 assignment. Payment Abstraction
            # revenue accumulates here as LINK. Without it the archetype 3 block has NO revenue input
            # at all, which is why it is listed even with no address yet.
            "reserve": _contract(
                "0x9A709B7B69EA42D5eeb1ceBC48674C69E1569eC6", "ethereum", "buyback_fund_balance", "LINK",
                "https://blog.chain.link/chainlink-reserve-strategic-link-reserve/",
                verified="2026-09-11", provenance="supplied and cross-checked against the Chainlink Reserve dashboard",
                token_standard="erc20", underlying="token",
                purpose="Chainlink Reserve — where Payment Abstraction revenue accumulates as LINK. THIS, not the "
                        "token contract, is the archetype 3 revenue input; without it the block had none at all.",
                note="Balance read is LINK.balanceOf(reserve). Cross-checked against the published dashboard at "
                     "https://metrics.chain.link/reserve — see cross_checks below; a divergence is flagged, never "
                     "silently resolved."),
        },
        # HOLD, not burn and not distribute. The Reserve has a multi-day withdrawal timelock and
        # Chainlink states no withdrawals are expected for multiple years, so accumulated LINK is
        # LOCKED SUPPLY. It must never share a formula with a yield payout: a distribution returns
        # tokens to float, a hold removes them from it. Opposite sign on effective float.
        "buyback_destination": "hold", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "locked_supply",
        "destination_source_url": "https://blog.chain.link/chainlink-reserve-strategic-link-reserve/",
        "destination_confirmed_date": TODAY_VERIFIED,
        "cross_checks": [
            {"primary": "buyback_fund_balance", "primary_source": "tier 2 contract read",
             "secondary": "buyback_fund_balance_dashboard", "secondary_source": "https://metrics.chain.link/reserve",
             "tolerance": 0.02, "prefer": "primary",
             "note": "Prefer the contract read; the dashboard is the cross-check. A divergence beyond the "
                     "tolerance is flagged to the Review Queue rather than one figure being silently picked."},
        ],
        "sanity": {
            # Rising series: c.2.17m LINK (Feb 2026) -> c.5.2m LINK (Jul 2026). Bounded generously above
            # so continued growth is not rejected, but a wild value still is.
            "buyback_fund_balance": {"min": 0, "max": 100_000_000, "change_threshold_pct": 40},
            "buyback_fund_balance_dashboard": {"min": 0, "max": 100_000_000, "change_threshold_pct": 40},
        },
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "staked_tokens", "emissions_tokens"),
        "materiality": "high",
        "notes": "The Reserve, not the LINK token contract, is the archetype 3 revenue input. Destination is HOLD: "
                 "a multi-day withdrawal timelock with no withdrawals expected for years, so accumulated LINK is "
                 "locked supply rather than a payout. Reference points for the series: c.2.17m LINK (Feb 2026), "
                 "c.5.2m LINK (Jul 2026).",
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
        "destination_effect": "yield_payout",
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
        # CORRECTED from the working Dune query that actually tracks GEOD burns. The
        # "1nc1nerator111..." address previously here is NOT what that query reads and has been removed.
        # The query UNIONs both chains, which settles the open question: Polygon-era burns DO belong in
        # the series alongside the Solana ones, so both paths are read and summed.
        "contracts": {
            "token_polygon": _contract(
                "0xAC0F66379A6d7801D7726d5a943356A172549Adb", "polygon", "erc20_total_supply", "GEOD",
                GEODNET_BURN_QUERY, verified="2026-09-11",
                provenance="Dune query 8683175 — the original documented source of this address",
                purpose="Polygon GEOD token — balanceOf is called on this for the Polygon burn."),
            "burn_polygon": _contract(
                "0x000000000000000000000000000000000000dEaD", "polygon", "burn_address_balance", "GEOD",
                GEODNET_BURN_QUERY, verified="2026-09-11",
                provenance="Dune query 8683175 — the original documented source of this address",
                purpose="TRANSFER BURN — Polygon burn destination. IN SCOPE for the backfill, not historical-only: "
                        "the working query unions Polygon and Solana burns into one series."),
            "mint_solana": _contract(
                "7JA5eZdCzztSfQbJvS8aVVxMFfd81Rs9VvwnocV1mKHu", "solana", "spl_mint", "GEOD",
                GEODNET_BURN_QUERY, verified="2026-09-11",
                provenance="Dune query 8683175 — the original documented source of this address",
                purpose="Solana GEOD mint."),
            "burn_solana_token_account": _contract(
                "5SBfxBdqsCM1SJZGQkf9Y74EFmUfzs8LGDjBZUjZGnED", "solana", "spl_token_account", "GEOD",
                GEODNET_BURN_QUERY, verified="2026-09-11",
                provenance="Dune query 8683175 — the original documented source of this address",
                purpose="TRANSFER BURN — Solana burn destination. This is a TOKEN-ACCOUNT read, not an "
                        "incinerator balance, so it needs a Solana RPC adapter rather than the EVM one."),
            "buyback_wallet_polygon_historical": _contract(
                "0xc327C048d75398Da9DB5254679bb84a4a9e42010", "polygon", "buyback_fund_balance", "GEOD",
                "https://geodnet.com/tokenomics",
                purpose="Polygon-era buyback wallet, pre-migration.",
                note="UNVERIFIED, and NOT referenced by the working burn query. Left in place but refused; "
                     "confirm whether it is still relevant before enabling."),
        },
        "burn_read_method": "transfer",
        "burn_backfill_spans_chains": True,   # Polygon-era burns belong in the same series as the Solana ones
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "destination_effect": "removed_from_supply",
        "dune_queries": {
            # HISTORICAL BACKFILL for the monthly burn trend. The live tier 2 contract reads remain
            # the source for current values; this fills the history they cannot give.
            "gross_burn_tokens": {
                "query_id": 8683175,
                "date_col": "month",
                # The query reports Polygon and Solana burns in SEPARATE columns; the monthly total
                # is their sum. Taking one alone would report a fraction of the burn as if it were all.
                "value_cols": ["tokens_burned", "sol_tokens_burned"],
                "granularity": "monthly",       # NOT daily — every CTE buckets to month at the final step
                "drop_current_period": True,    # the current month is incomplete AND tier 2 covers the present
                "source_url": GEODNET_BURN_QUERY,
                "provenance": "primary — this is the original source of GEODNET's four verified contract "
                              "addresses, confirmed by Jake, 2026-09-11",
                "note": "Output is MONTHLY. Columns available: month, tokens_burned, cumulative_tokens_burned, "
                        "usd_burned, cumulative_usd_burned, sol_tokens_burned, sol_cumulative_tokens_burned, "
                        "sol_usd_burned, sol_cumulative_usd_burned, total_cumulative_tokens_burned, "
                        "total_cumulative_usd_burned.",
                "fragility": "History before 2026-08-01 comes from a STATIC snapshot table, "
                             "dune.geodnet_console.result_geod_tokens_burned_20260731, unioned in ahead of the "
                             "live query's start date. If that table stops being queryable, GEODNET loses ALL "
                             "history before August 2026 on the next full backfill. Confirm it is reachable "
                             "under the account the API key belongs to.",
            },
            **_dune("emissions_tokens"),
        },
        "materiality": "low",
        "notes": "Console revenue burn — confirmed. Migrated Polygon -> Solana, and BOTH burn paths belong in the "
                 "same series: the working Dune query unions them. Polygon burns via balanceOf on the GEOD token at "
                 "the dead address; Solana via a token-account read, which needs a Solana adapter. "
                 "GEODNET publishes miner counts on its own dashboard (tier 3/5). "
                 "HISTORY FRAGILITY: everything before 2026-08-01 comes from a STATIC Dune snapshot table, "
                 "dune.geodnet_console.result_geod_tokens_burned_20260731, unioned into query 8683175 ahead of "
                 "its live start date. If that table stops being queryable, a full re-backfill loses ALL GEODNET "
                 "history before August 2026 — the live query alone does not reach back that far. Reachability "
                 "has NOT been confirmed from here; it needs a Dune API key.",
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
        "destination_effect": "none",
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
        "destination_effect": "removed_from_supply",
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
        "destination_effect": "none",
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
        "destination_effect": "yield_payout",
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
        "destination_effect": "unresolved",
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
        "contracts": {
            "token": _contract("0xacfE6019Ed1A7Dc6f7B508C02d1b04ec88cC21bf", "base", "erc20_total_supply", "VVV",
                               "https://docs.venice.ai/", verified="2026-09-11",
                               provenance="Venice developer docs (embedded in working integration code)",
                               token_standard="erc20",
                               purpose="VVV token contract on Base."),
            # Venice's own docs describe VVV staked here becoming sVVV. Read as VVV.balanceOf(staking):
            # this is a STAKING CONTRACT, not a token, so totalSupply() on it would be the TokenJar
            # mistake. Reading the VVV it custodies is well-defined for any address and fails safely.
            "staking": _contract("0x321b7ff75154472B18EDb199033fF4D116F340Ff", "base", "ve_total_supply", "VVV",
                                 "https://docs.venice.ai/", verified="2026-09-11",
                                 provenance="Venice developer docs (embedded in working integration code)",
                                 read_method="escrow_balance_of", underlying="token",
                                 purpose="VVV staking contract — VVV staked here becomes sVVV. THE Venice "
                                         "lock-rate metric, which was missing entirely until now.",
                                 note="READ METHOD ASSUMED, NOT DOCUMENTED: escrow_balance_of reads the VVV this "
                                      "contract custodies, which is correct if 'staked here' means it holds the "
                                      "tokens. If Venice forwards custody to another vault the figure would read "
                                      "low. Eyeball the first value against Venice's published staking total "
                                      "before trusting it."),
            # NOT CORROBORATED — a different state from 'not yet checked'. This address could not be
            # found in ANY Venice-authored source, and every description of the Buy and Burn programme
            # describes revenue-funded BEHAVIOUR rather than a named contract. There may be no such
            # contract at all. It stays refused and is NOT to be promoted without positive evidence.
            "buy_and_burn": _contract("0x35Fb3b67C57849Bf57EB24b061EEF0B5E560dc57", "base", "buyback_fund_balance", "VVV",
                                      None, provenance="uncorroborated — not found in any Venice-authored source",
                                      purpose="Claimed buy-and-burn contract.",
                                      note="COULD NOT BE CORROBORATED, which is NOT the same as 'not yet checked'. "
                                           "A search of Venice-authored sources did not find this address, and the "
                                           "Buy and Burn programme is described as a revenue-funded behaviour rather "
                                           "than a named contract — there may be no dedicated contract to read. Do "
                                           "not mark verified without positive evidence from Venice's own material. "
                                           "Deleting it is reasonable once that is settled."),
            "burn_zero": _contract("0x0000000000000000000000000000000000000000", "base", "burn_address_balance", "VVV",
                                   "https://eips.ethereum.org/EIPS/eip-20", verified="2026-09-11",
                                   provenance="universal constant — the EVM zero address, not project-specific",
                                   purpose="TRANSFER BURN destination. The zero address needs no per-project "
                                           "verification; what remains open is whether Venice's burn actually "
                                           "routes here, which is the burn-methodology question, not an address one."),
        },
        "burn_read_method": "transfer",
        "self_reported_burn": True,
        "self_reported_source": {
            "what": "Venice publishes its burns directly",
            "url": "https://venice.ai/token/burns",
            "metric": "gross_burn_tokens",
            "note": "Self-reported, so preferred over any derived calculation — the same rule as PancakeSwap's "
                    "published net mint.",
        },
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "destination_effect": "removed_from_supply",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "gross_burn_tokens", "emissions_tokens", "staked_tokens"),
        "materiality": "medium",
        "notes": "100% of emissions to stakers AND a revenue-funded buy-and-burn. Even at 3m VVV/yr, issuance runs several multiples "
                 "of what stated revenue could fund. The clearest case in the universe for showing buyback and emissions together.",
    },
    # ------------------------------------------------------------------ Archetype 3 (+4/+1)
    {
        "name": "Virtuals", "symbol": "VIRTUAL",
        "coingecko_id": "virtual-protocol",
        "defillama_fees_slug": "virtuals-protocol", "defillama_protocol": "virtuals-protocol", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": None, "source_date": None,
                      "programmed": False, "status": "unconfirmed",
                      "note": "Agent launch and trading fees route to buyback — the split needs documenting. "
                              "No source URL on file yet, so the implied figure stays suppressed."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {},
        "buyback_destination": "unconfirmed", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "unresolved",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens"),
        "materiality": "medium",
        "notes": "Agent launch and trading fees route to buyback — split needs documenting. "
                 "defillama_fees_slug is assumed to match defillama_protocol; confirm on DefiLlama, "
                 "a wrong slug shows up as a 404 in the Run Log.",
    },
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
        "destination_effect": "yield_payout",
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
        "destination_effect": "locked_supply",
        "dune_queries": _dune("emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens"),
        "materiality": "high",
        "notes": "Fee switch status needs confirming before the 3 block is enabled.",
    },
    {
        "name": "Hyperliquid", "symbol": "HYPE",
        "coingecko_id": "hyperliquid",
        "defillama_fees_slug": "hyperliquid", "defillama_protocol": "hyperliquid", "defillama_chain": "Hyperliquid L1",
        "archetypes": [3, 1], "archetypes_held": [],
        "fee_split": {
            "share_to_buyback": 0.99,   # current stated figure; the historical range was 97-99%
            "source_url": "https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/assistance-fund",
            "source_date": BRIEF_DATE,
            "programmed": True,
            "status": "active",
            "history": [
                _split_period(None, "2025-12-26", 0.98, "unconfirmed",
                              source_url="https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/assistance-fund",
                              note="Historical stated range 97-99% of net protocol fees; 0.98 is the midpoint, "
                                   "not a documented single figure, so this period stays unconfirmed."),
                _split_period("2025-12-27", None, 0.99, "active",
                              source_url="https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/assistance-fund",
                              source_date="2025-12-27",
                              note="Current stated figure, 99% of net protocol fees to the Assistance Fund."),
            ],
            "note": "Historical range 97-99%; current stated figure 99%.",
        },
        "burn_split": None,
        "issuance_schedule": None,
        # NO CONTRACTS. HYPE on HyperCore is not an ERC-20 on any chain the EVM adapter covers, so
        # the contract-read route was never viable. Hyperliquid publishes the balance through its own
        # documented info endpoint instead, which REPLACES that approach: a plain HTTPS POST with no
        # RPC, no key and no chain field. See node_api below.
        "contracts": {},
        "burn_address": "0xfefeFEFeFEFEFEFEFeFefefefefeFEfEfefefEfe",
        "burn_read_method": "protocol_api",
        "burn_read_note": "The Assistance Fund balance IS the cumulative burn: since the December 2025 validator "
                          "vote HYPE held there is recognised as permanently burned, and the address has never had "
                          "a private key so nothing can leave. Read from Hyperliquid's own info endpoint, not from "
                          "any chain RPC.",
        "node_api": {
            "kind": "hypercore_info",
            "metric": "burn_address_balance",
            "derive_flow_metric": "gross_burn_tokens",
            "endpoint": "https://api.hyperliquid.xyz/info",
            "request": {"type": "spotClearinghouseState",
                        "user": "0xfefefefefefefefefefefefefefefefefefefefe"},
            # response shape, read from config so a change here is not a code change
            "balances_path": "balances",
            "coin_key": "coin",
            "coin": "HYPE",
            "amount_keys": ["total", "balance", "amount"],
            "source_urls": ["https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint",
                            "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees"],
            "verified": "2026-09-11",
            "note": "Free, unauthenticated, documented. The `user` address is lowercase because it is a JSON "
                    "request parameter to Hyperliquid's API, NOT an EVM call argument — no checksumming applies "
                    "and web3 never sees it.",
        },
        "buyback_destination": "burn",          # resolved — no longer disputed
        "destination_effect": "removed_from_supply",
        "destination_confirmed_date": "2025-12-27",
        "destination_split": None, "burn_execution": "protocol",
        "revenue_sources": [
            {"name": "Protocol fees -> Assistance Fund", "share": 0.99, "status": "active", "booked": True,
             "source_url": "https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/assistance-fund"},
            {"name": "AQAv2 reserve yield on platform USDC", "share": 0.90, "status": "accruing",
             "booked": False,
             "live_from": "2026-08-26", "first_payment_date": "2026-10-03", "accrual_days": 30,
             "source_url": None,
             "note": "Live from 2026-08-26: routes c.90% of cost-adjusted reserve yield on platform USDC "
                     "into the Assistance Fund on 30-day accrual cycles. ACCRUAL HAS STARTED BUT NOTHING "
                     "HAS BEEN PAID — first actual payment expected 2026-10-03. booked=False keeps it out "
                     "of revenue so the model cannot book money that has not landed. Flip booked to True "
                     "only once a payment is observed."},
        ],
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens", "tx_count", "active_addresses", "gross_issuance_tokens"),
        "materiality": "high",
        "notes": "DESTINATION RESOLVED: validators voted 27 Dec 2025, 85% of staked weight in favour, to formally "
                 "recognise all Assistance Fund HYPE — past and future — as permanently burned. Independently "
                 "corroborated by an SEC-filed exhibit from Hyperliquid Strategies Inc dated 7 May 2026. Lifetime "
                 "burned was 48.42m HYPE as of 6 Sep 2026, c.4.84% of max supply. Assistance Fund absorbs 99% of "
                 "net protocol fees (historical range 97-99%). Own L1 + HyperEVM hence the 1 block. "
                 "SECOND REVENUE LEG: AQAv2 from 26 Aug 2026 accrues c.90% of cost-adjusted reserve yield on "
                 "platform USDC, first payment due 3 Oct 2026 — accruing but NOT yet booked.",
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
                               UNISWAP_FEE_DEPLOYMENTS, verified="2026-09-11", provenance="protocol docs",
                               purpose="UNI token contract."),
            "token_jar": _contract("0xf38521f130fcCF29dB1961597bc5d2B60F995f85", "ethereum", "buyback_fund_balance", "UNI",
                                   UNISWAP_FEE_DEPLOYMENTS, verified="2026-09-11", provenance="protocol docs",
                                   purpose="TokenJar (AssetSink), mainnet — where fees accumulate before holders elect to burn."),
            "fire_pit": _contract("0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721", "ethereum", "burn_address_balance", "UNI",
                                  UNISWAP_FEE_DEPLOYMENTS, verified="2026-09-11", provenance="protocol docs",
                                  holder_has_code=True,   # the Releaser is a real contract, unlike a dead address
                                  purpose="Releaser (Firepit), mainnet — TRANSFER BURN destination for holder-elected burns."),
            "v3_fee_adapter": _contract("0x5E74C9f42EEd283bFf3744fBD1889d398d40867d", "ethereum", "buyback_fund_balance", "UNI",
                                        UNISWAP_FEE_DEPLOYMENTS, verified="2026-09-11", provenance="protocol docs",
                                        purpose="V3FeeAdapter, mainnet."),
            # SECOND BURN PATH. Unichain has its own TokenJar and releaser, and Unichain sequencer
            # revenue routes into the UNI burn. Omitting this UNDERSTATES the total, so both paths
            # are read and summed into one burn figure.
            "token_jar_unichain": _contract("0xD576BDF6b560079a4c204f7644e556DbB19140b5", "unichain", "buyback_fund_balance", "UNI",
                                            UNISWAP_FEE_DEPLOYMENTS, verified="2026-09-11", provenance="protocol docs",
                                            purpose="TokenJar, Unichain — the second fee accumulation path."),
            "fire_pit_unichain": _contract("0xe0A780E9105aC10Ee304448224Eb4A2b11A77eeB", "unichain", "burn_address_balance", "UNI",
                                           UNISWAP_FEE_DEPLOYMENTS, verified="2026-09-11", provenance="protocol docs",
                                           holder_has_code=True,   # OptimismBridgedResourceFirepit is a real contract
                                           purpose="OptimismBridgedResourceFirepit, Unichain — the second TRANSFER BURN path. "
                                                   "Summed with the mainnet fire pit; omitting it understates total burn."),
        },
        "burn_read_method": "transfer",
        # The UNI-burn threshold required to call release() is a GOVERNANCE-SETTABLE parameter, not a
        # constant: the Uniswap Governance Timelock holds thresholdSetter and can appoint a different
        # one. No number is hardcoded here — read it on-chain or record it with its source and date.
        "governance_parameters": {
            "release_threshold_uni": {
                "value": None,
                "programmed": False,
                "controller": "Uniswap Governance Timelock (holds thresholdSetter, and can appoint a different setter)",
                "source_url": UNISWAP_FEE_DEPLOYMENTS,
                "source_date": None,
                "note": "The UNI-burn threshold that must be met before release() can be called. Governance-movable, "
                        "so it is never hardcoded. Leaving value None keeps any figure derived from it suppressed.",
            },
        },
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "holder_elected",
        "destination_effect": "removed_from_supply",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens"),
        "materiality": "high",
        "notes": "Archetype 4 only — no distribution leg, no staking yield. Implied and actual burn diverge for reasons unrelated "
                 "to revenue, because the burn is holder-elected. TWO burn paths: mainnet and Unichain, summed. "
                 "The release() threshold is a governance-settable parameter, not a constant.",
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
                               "https://aerodrome.finance/docs", verified="2026-09-11", provenance="deployed source",
                               token_standard="erc20",
                               purpose="AERO token. Deployed source declares ERC20(\"Aerodrome\", \"AERO\")."),
            # veAERO is an ERC-721 veNFT. totalSupply() on it returns the COUNT OF NFT POSITIONS,
            # not AERO locked — a figure orders of magnitude wrong that would still look plausible.
            # The correct read is AERO.balanceOf(escrow).
            "ve": _contract("0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4", "base", "ve_total_supply", "AERO",
                            "https://aerodrome.finance/documents/AERO/legal-disclosures.pdf",
                            verified="2026-09-11", provenance="Aerodrome legal disclosures + BaseScan label",
                            read_method="escrow_balance_of", token_standard="erc721", underlying="token",
                            purpose="veAERO VotingEscrow — the lock-rate input for effective float. Read as "
                                    "AERO.balanceOf(this escrow), NOT any call on the escrow itself.",
                            note="VERIFIED against two independent primaries: Aerodrome's own legal disclosures "
                                 "name the VotingEscrow contract at this address, and BaseScan labels it "
                                 "'Aerodrome: Voting Escrow' with the contract verified. ERC-721 is confirmed by "
                                 "Aerodrome's SPECIFICATION.md — users escrow AERO into an ERC-721 compliant "
                                 "veAERO NFT whose balance represents DECAYING voting weight. THREE separate "
                                 "reasons voting weight diverges from AERO locked: the NFT totalSupply is a "
                                 "position count, the weight decays with time to expiry, and holders receive "
                                 "automatic weekly REBASES that increase their veAERO balance. Only "
                                 "AERO.balanceOf(escrow) gives the tokens actually locked."),
        },
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "yield_payout",
        "dune_queries": {
            "locked_tokens_dashboard": {
                "query_id": 2986047,
                # COLUMNS NOT SUPPLIED, and the SQL was not visible. The adapter runs the query and
                # reports the columns it actually returns rather than guessing which holds the figure.
                "date_col": None,
                "value_col": None,
                "source_url": "https://dune.com/queries/2986047",
                "note": "veAERO locked, as a CROSS-CHECK of the tier 2 read of AERO.balanceOf(escrow), which is "
                        "verified and working. It writes locked_tokens_dashboard, never locked_tokens, so it can "
                        "never overwrite the contract figure. Low stakes if it stays unmapped.",
            },
            **_dune("avg_lock_duration_days", "emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens"),
        },
                "cross_checks": [
            {"primary": "locked_tokens", "primary_source": "tier 2 contract read",
             "secondary": "locked_tokens_dashboard", "secondary_source": "https://dune.com/0xkhmer/aerodrome",
             "tolerance": 0.03, "prefer": "primary",
             "note": "The contract read is authoritative; the page cross-checks it. A divergence beyond "
                     "tolerance is flagged rather than one figure silently replacing the other."},
        ],
        "materiality": "high",
        "notes": "veAERO — lock rate and average lock duration are required inputs.",
    },
    {
        "name": "PancakeSwap", "symbol": "CAKE",
        "coingecko_id": "pancakeswap-token",
        "defillama_fees_slug": "pancakeswap", "defillama_protocol": "pancakeswap", "defillama_chain": None,
        "archetypes": [4, 3], "archetypes_held": [],
        "fee_split": {
            # NOT a single number — the share is per-product. Stored as a dict and never collapsed
            # into one figure; the workbook shows each product and suppresses the single implied column.
            "share_to_buyback": {
                "spot_trading_fees_burned": {"min": 0.15, "max": 0.23},
                "perpetual_trading_profit_burned": 0.20,
            },
            "source_url": "https://docs.pancakeswap.finance/protocol/cake-tokenomics",
            "source_date": "2026-09-11",
            "programmed": True,
            "status": "active",
            "note": "Per-product shares: spot trading fees 15-23% burned, perpetual trading profit 20% burned. "
                    "Self-reported net mint is PREFERRED over any figure derived from these.",
        },
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://docs.pancakeswap.finance/governance-and-tokenomics/cake-tokenomics", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Best-disclosed net burn in the universe. Publishes net mint monthly (May 2026: -1,958,514 CAKE, "
                               "33rd consecutive month). Hard cap cut 450m -> 400m Jan 2026. TAKE THE SELF-REPORTED FIGURE."},
        "issuance_schedule": None,
        "contracts": {
            # CAKE IS A MULTI-CHAIN LayerZero OFT. BSC totalSupply is therefore NOT total supply, and a
            # figure read from BSC alone is wrong while looking complete. Known deployments are summed and
            # the result is marked partial until the full OFT list is confirmed; the self-reported figure
            # remains the source of record.
            "token": _contract("0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", "bsc", "erc20_total_supply", "Cake",
                               "https://docs.pancakeswap.finance/protocol/cake-tokenomics",
                               verified="2026-09-11", provenance="deployed source", token_standard="erc20",
                               supply_is_partial=True,
                               partial_reason="CAKE is a LayerZero OFT with deployments beyond BSC, so this is one "
                                              "deployment's supply, not total supply.",
                               purpose="CAKE token on BSC. Deployed source declares CakeToken is "
                                       "BEP20('PancakeSwap Token', 'Cake') — the symbol casing is 'Cake', not 'CAKE'."),
            "token_base": _contract("0x3055913c90Fcc1A6CE9a358911721eEb942013A1", "base", "erc20_total_supply", "Cake",
                                    "https://docs.pancakeswap.finance/protocol/cake-tokenomics",
                                    token_standard="erc20", supply_is_partial=True,
                                    partial_reason="One of several OFT deployments; the full list is not confirmed.",
                                    purpose="CAKE OFT deployment on Base.",
                                    note="UNVERIFIED. Supplied as a known further deployment; confirm against "
                                         "PancakeSwap's own docs, and see the Gap Report row asking for the "
                                         "complete OFT deployment list."),
            "burn_dead": _contract("0x000000000000000000000000000000000000dEaD", "bsc", "burn_address_balance", "Cake",
                                   "https://docs.pancakeswap.finance/protocol/cake-tokenomics",
                                   verified="2026-09-11", provenance="deployed source",
                                   purpose="TRANSFER BURN — CAKE sent to the standard BNB Chain dead address. "
                                           "balanceOf is called on the confirmed CAKE token holding this address."),
        },
        "burn_read_method": "transfer",
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "destination_effect": "removed_from_supply",
        "self_reported_net_mint": True,
        "supply_is_partial": True,
        "supply_partial_reason": "CAKE is a LayerZero OFT deployed on several chains, so any on-chain supply read "
                                 "covers only the deployments listed in contracts. The self-reported figure is the "
                                 "source of record; the on-chain sum is a partial cross-check and is labelled as such.",
        "self_reported_source": {
            "what": "Monthly CAKE Burn Report blog series",
            "url": "https://blog.pancakeswap.finance/",
            "metric": "net_mint_monthly",
            "note": "Per the build spec, prefer the self-reported figure over the derived calculation "
                    "wherever both exist. Find the specific monthly post URL and add it to sources.yaml.",
        },
        # Published values, for validating whatever the scraper returns. A scraped figure for one of
        # these months that disagrees is a scraper regression, and is flagged to the Review Queue.
        "reference_values": [
            {"period": "2026-05", "metric": "net_mint_monthly", "value": -1_958_514,
             "source": "PancakeSwap monthly CAKE Burn Report (May 2026, 33rd consecutive month of net deflation)"},
            {"period": "2026-06", "metric": "net_mint_monthly", "value": -1_749_587,
             "source": "PancakeSwap monthly CAKE Burn Report (June 2026)"},
        ],
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens", "staked_tokens"),
        "materiality": "high",
        "notes": "TEMPLATE for the archetype 4 tab. Self-reported net mint is the headline and is PREFERRED over "
                 "the derived calculation; totalSupply delta is the independent check. Hard cap cut 450m -> 400m Jan 2026.",
    },
    {
        "name": "Sky", "symbol": "SKY",
        "coingecko_id": "sky",
        "defillama_fees_slug": "sky", "defillama_protocol": "sky", "defillama_chain": None,
        "archetypes": [3, 4], "archetypes_held": [],
        "fee_split": {
            "share_to_buyback": 0.55,
            # PRIMARY SOURCE STILL NEEDED. The change is a Sky governance Executive Proposal
            # approved 2026-08-13 directing 55% of each Smart Burn Engine cycle to SKY buybacks
            # and 45% to LSSKY stakers. The build instruction was to cite the primary governance
            # forum post rather than a secondary article; that URL has not been captured yet, so
            # it is left None and raised in the Gap Report rather than filled with a guess.
            "source_url": None,
            "source_date": "2026-08-13",
            "programmed": False,   # explicitly governance-set and revisable
            "status": "active",
            "destination_split": 0.55,
            "history": [
                _split_period(None, "2026-08-12", None, "unconfirmed",
                              note="Split before the 2026-08-13 Executive Proposal is NOT documented. "
                                   "Do not assume 0.55 applied — the derived figure is suppressed for "
                                   "any period ending before 2026-08-13."),
                _split_period("2026-08-13", None, 0.55, "active", source_url=None, source_date="2026-08-13",
                              destination_split=0.55,
                              note="Executive Proposal approved 2026-08-13: 55% of each Smart Burn Engine "
                                   "cycle to SKY buybacks, 45% to LSSKY stakers. Primary forum URL still needed."),
            ],
            "note": "This split has moved before and will move again. Each historical period is treated as "
                    "potentially different from the current one; undocumented periods are suppressed, never "
                    "backfilled with today's number.",
        },
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://docs.sky.money/", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Repurchased SKY is burned OR redistributed to LSSKY stakers per a GOVERNANCE PARAMETER, "
                               "currently 55% burn / 45% stakers. Never net staking rewards against burn."},
        "issuance_schedule": None,
        "contracts": {
            "token": _contract(
                "0x56072C95FAA701256059aa122697B133aDEd9279", "ethereum", "erc20_total_supply", "SKY",
                "https://developers.skyeco.com/guides/sky/token-governance-upgrade/key-info/",
                verified="2026-09-11", provenance="protocol docs",
                purpose="SKY token contract — needed to read any balance, including the burn.",
                note="RESOLVED. Confirmed against Sky's own developer docs; the contract declares name "
                     "'SKY Governance Token', symbol 'SKY'. Codebase: https://github.com/sky-ecosystem/sky. "
                     "The other address that was circulating is WRONG and has been deleted "
                     "entirely rather than kept as a fallback."),
            "burn_zero": _contract(
                "0x0000000000000000000000000000000000000000", "ethereum", "burn_address_balance", "SKY",
                "https://developers.skyeco.com/guides/sky/token-governance-upgrade/key-info/",
                verified="2026-09-11", provenance="protocol docs",
                purpose="TRANSFER BURN — the Smart Burn Engine sends repurchased SKY to the zero address.",
                note="Unblocked by the SKY token resolution: balanceOf is called on the confirmed token contract."),
            "lssky": _contract(
                "0xf9A9cfD3229E985B91F99Bc866d42938044FFa1C", "ethereum", "ve_total_supply", "lssky",
                "https://developers.skyeco.com/guides/sky/token-governance-upgrade/key-info/",
                verified="2026-09-11", provenance="protocol docs",
                read_method="erc20_total_supply", token_standard="erc20", underlying="token",
                purpose="Staked SKY Token (lssky) — THE lock-rate metric for Sky.",
                note="RESOLVED from the same Sky developer docs page that settled the SKY token address. "
                     "read_method is erc20_total_supply, NOT escrow_balance_of: Sky's own docs describe staking "
                     "with no minimum, no lockup period and no exit fee, so there is no duration-weighted escrow "
                     "here and the lssky balance IS the lock-rate figure. This gives Sky a working tier 2 path "
                     "independent of the info.skyeco.com dashboard, which robots.txt disallows."),
        },
        "burn_read_method": "transfer",
        "buyback_destination": "split", "destination_split": 0.55, "burn_execution": "protocol",
        "destination_effect": "mixed",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens", "staked_tokens"),
                "cross_checks": [
            {"primary": "locked_tokens", "primary_source": "tier 2 contract read",
             "secondary": "locked_tokens_dashboard", "secondary_source": "https://info.skyeco.com/staking",
             "tolerance": 0.03, "prefer": "primary",
             "note": "The contract read is authoritative; the page cross-checks it. A divergence beyond "
                     "tolerance is flagged rather than one figure silently replacing the other."},
        ],
        "materiality": "high",
        "notes": "Smart Burn Engine: 55% of each cycle burned, 45% to LSSKY stakers (Executive Proposal, 13 Aug 2026). "
                 "THE SPLIT HAS MOVED BEFORE AND WILL MOVE AGAIN — treat each historical period as potentially "
                 "different from the current one; 0.55 is NOT applied retroactively across the backfill. "
                 "Tokens paid to LSSKY stakers re-enter float, so that leg is never netted against burn.",
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
                               PENDLE_DEPLOYMENTS_1_CORE,
                               verified="2026-09-11", provenance="Pendle's own deployment file", token_standard="erc20",
                               purpose="PENDLE token. Matches the 'PENDLE' key in Pendle's deployments/1-core.json."),
            # vePENDLE IS DEPRECATED and has been REMOVED as the lock-rate source. Pendle's own
            # tokenomics docs state the contract is winding down and users should migrate to sPENDLE.
            # A vePENDLE balance read would show a FALLING figure that reflects migration, not falling
            # lock-in — a false negative on the exact metric this tool exists to measure.
            "spendle": _contract(
                "0x999999999991E178D52Cd95AFd4b00d066664144", "ethereum", "ve_total_supply", "sPENDLE",
                PENDLE_DEPLOYMENTS_1_CORE,
                verified="2026-09-11", provenance="Pendle's own deployment file",
                read_method="erc20_total_supply", token_standard="erc20", underlying="token",
                purpose="sPENDLE — the CURRENT lock-rate source, replacing the deprecated vePENDLE. A fungible "
                        "ERC-20, so totalSupply() IS the staked amount.",
                note="ADDRESS RESOLVED from Pendle's own deployments/1-core.json, key 'sPendle'. The same file "
                     "lists vePendle under a 'deprecated' block, independently confirming that vePENDLE is not "
                     "the lock source. READ METHOD CONFIRMED ERC-20, so totalSupply() is the correct read. The "
                     "expected symbol is still unconfirmed: the on-chain symbol check compares case-insensitively "
                     "and fails CLOSED, so a mismatch rejects the address with a clear message rather than "
                     "returning a wrong number."),
        },
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "yield_payout",
        "dune_queries": _dune("locked_tokens", "avg_lock_duration_days", "emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens"),
                "cross_checks": [
            {"primary": "locked_tokens", "primary_source": "tier 2 contract read",
             "secondary": "locked_tokens_dashboard", "secondary_source": "https://app.pendle.finance/spendle/stake/in",
             "tolerance": 0.03, "prefer": "primary",
             "note": "The contract read is authoritative; the page cross-checks it. A divergence beyond "
                     "tolerance is flagged rather than one figure silently replacing the other."},
        ],
        "materiality": "high",
        "notes": "LOCK SOURCE MIGRATED: vePENDLE is deprecated — Pendle's own deployments/1-core.json lists it "
                 "under a 'deprecated' block, alongside feeDistributor, feeDistributorV2 and votingController, "
                 "which independently confirms the tokenomics docs. "
                 "and users are moving to sPENDLE. A vePENDLE read would show a falling figure that reflects "
                 "MIGRATION rather than falling lock-in — a false negative on the metric this tool exists to "
                 "measure — so it has been removed rather than kept as a fallback. sPENDLE address still to be "
                 "sourced; the staking page covers the metric meanwhile. "
                 "Source: https://docs.pendle.finance/pendle-v2/ProtocolMechanics/Mechanisms/Tokenomics",
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
        "destination_effect": "locked_supply",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens"),
        "materiality": "medium",
        "notes": "CoinGecko id is still instadapp after the rebrand — verify.",
    },
    {
        "name": "Aave", "symbol": "AAVE",
        "coingecko_id": "aave",
        "defillama_fees_slug": "aave", "defillama_protocol": "aave", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {
            "share_to_buyback": 1.0,
            "source_url": "https://governance.aave.com/",
            "source_date": BRIEF_DATE,
            # SOURCES GENUINELY CONFLICT — do not resolve this to True or False.
            "programmed": "unconfirmed_conflict",
            "status": "paused",
            "note": "The AWW framework (passed April 2026) routes 100% of Aave Protocol, GHO and Aave-branded "
                    "product revenue to the DAO treasury, and Aavenomics 3.0 draws on that. Sources conflict on "
                    "whether the buyback is immutable and non-discretionary or committee-directed: one June 2026 "
                    "report states governance can redirect, pause or resize it without a protocol-level change. "
                    "VERIFY against governance.aave.com directly before treating the mechanism as hard-coded.",
        },
        "paused_since": "2026-04-19",
        # AAVE IS NOT A CLEAN REVENUE-FUNDED NAME — it has a supply leg. stkAAVE rewards are funded
        # by AAVE allowance top-ups at 150 AAVE per day, which is EMISSIONS running alongside the
        # buyback rather than being funded by it. Counting the buyback without this would overstate
        # net absorption.
        "issuance_schedule": {
            "steps": [{"from": "2026-08-28", "tokens_per_day": 150.0}],
            "source_url": "https://governance.aave.com/",
            "source_date": "2026-08-28",
            "status": "active",
            "also_emissions": True,   # the same figure is emissions to stakers, so it feeds both metrics
            "note": "Safety Module August 2026 Allowance Update AIP (governance.aave.com, authored by "
                    "TokenLogic, created 2026-08-28): stkAAVE rewards funded by AAVE allowance top-ups at "
                    "150 AAVE/day. This is the supply leg that sits alongside the buyback.",
        },
        "burn_split": None,
        "contracts": {
            "token": _contract("0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9", "ethereum", "erc20_total_supply", "AAVE",
                               "https://aave.com/docs"),
            "staking": _contract("0x4da27a545c0c5B758a6BA100e3a049001de870f5", "ethereum", "ve_total_supply", "stkAAVE",
                                 "https://app.aave.com/safety-module/",
                                 read_method=None, token_standard=None, underlying="token",
                                 purpose="stkAAVE, the LEGACY Safety Module — AAVE and ABPT staked on Ethereum. "
                                         "THIS is the AAVE lock rate.",
                                 note="stkAAVE — destination is stakers, NOT burn. Not to be confused with Umbrella, "
                                      "which stakes aTokens and GHO rather than AAVE. READ METHOD NOT ESTABLISHED: "
                                      "stkAAVE is LIKELY a fungible ERC-20 whose totalSupply is the staked amount, "
                                      "but that has not been confirmed, so nothing is read. Verify whether it is "
                                      "ERC-20 (erc20_total_supply) or NFT-based (escrow_balance_of)."),
        },
        "buyback_destination": "distribute",   # to stakers — NOT in dispute
        "destination_effect": "yield_payout",
        "destination_split": None, "burn_execution": "n/a",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens"),
                "cross_checks": [
            {"primary": "locked_tokens", "primary_source": "tier 2 contract read",
             "secondary": "locked_tokens_dashboard", "secondary_source": "https://app.aave.com/safety-module/",
             "tolerance": 0.03, "prefer": "primary",
             "note": "The contract read is authoritative; the page cross-checks it. A divergence beyond "
                     "tolerance is flagged rather than one figure silently replacing the other."},
        ],
        "materiality": "high",
        "notes": "HAS A SUPPLY LEG: stkAAVE rewards are AAVE allowance top-ups at 150 AAVE/day (Safety Module "
                 "August 2026 Allowance Update AIP), which is emissions running alongside the buyback, not funded "
                 "by it — so net absorption must net them off. "
                 "Destination is stakers, not burn — that part is not in dispute. Buybacks PAUSED since "
                 "19 April 2026 following the rsETH bridge exploit; an ARFC was filed 22 April formalising the "
                 "pause. No resumption found as of Sept 2026. Roughly $15bn TVL migrated away post-exploit and "
                 "the DAO stated buybacks resume \"when business cashflow permits\". Whether the mechanism is "
                 "immutable or committee-directed is UNRESOLVED — see fee_split.programmed.",
        "status_changes": [
            {"date": "2026-03-01", "event": "Buyback budget cut from ~$50m to ~$30m", "source_url": "https://governance.aave.com/"},
            {"date": "2026-04-19", "event": "Buybacks paused after the rsETH bridge exploit", "source_url": "https://governance.aave.com/"},
            {"date": "2026-04-22", "event": "ARFC filed formalising the pause", "source_url": "https://governance.aave.com/"},
            {"date": "2026-08-28", "event": "Safety Module Allowance Update AIP: stkAAVE rewards at 150 AAVE/day", "source_url": "https://governance.aave.com/"},
            {"date": "2026-09-11", "event": "No resumption found; c.$15bn TVL migrated away post-exploit", "source_url": "https://governance.aave.com/"},
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
        "destination_effect": "yield_payout",
        "dune_queries": {
            "staked_tokens": {
                "query_id": 8683038,
                # COLUMNS NOT SUPPLIED. The query id is unambiguous but the SQL and column mapping
                # did not reach this config, so nothing is read yet: the adapter runs the query,
                # reports the columns it actually returns, and refuses to guess which holds the figure.
                "date_col": None,
                "value_col": None,
                "source_url": "https://dune.com/queries/8683038",
                "note": "Staked sETHFI. Fill date_col and value_col from the column list the first run "
                        "reports in the Gap Report, then re-run. Tier 4 is Ether.fi's ONLY automated route "
                        "for this metric — there is no contract read for it.",
            },
            **_dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens"),
        },
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


def _as_date(value):
    """'2026-08-13' -> a comparable date, None stays None (open-ended)."""
    if value is None:
        return None
    from datetime import date
    y, m, d = (int(x) for x in str(value)[:10].split("-"))
    return date(y, m, d)


def split_for_window(project_name: str, start, end) -> dict:
    """The fee split that applied over the window [start, end] — never today's split by default.

    Splits change. Sky's moved on 2026-08-13; Hyperliquid's stated share moved on 2025-12-27.
    Applying the current number across a whole backfill would silently rewrite history, so:

      * a window sitting inside ONE documented period uses that period's share;
      * a window that SPANS a change is unconfirmed — we cannot attribute one share to it;
      * a window in a period we have not documented is unconfirmed;
      * a project with no history block keeps its single split for every window, which is
        correct for the protocols whose split has not moved.

    Returns a dict with share_to_buyback, status, source_url, source_date and why.
    """
    p = PROJECT_BY_NAME.get(project_name) or {}
    fs = p.get("fee_split") or {}
    history = fs.get("history")
    base = {
        "share_to_buyback": fs.get("share_to_buyback"),
        "destination_split": fs.get("destination_split", p.get("destination_split")),
        "status": fs.get("status", "n/a"),
        "source_url": fs.get("source_url"),
        "source_date": fs.get("source_date"),
        "why": "single split, no documented change",
    }
    if not history:
        return base

    start_d, end_d = _as_date(start), _as_date(end)
    overlapping = []
    for period in history:
        p_from, p_to = _as_date(period.get("from")), _as_date(period.get("to"))
        if p_to is not None and start_d is not None and start_d > p_to:
            continue
        if p_from is not None and end_d is not None and end_d < p_from:
            continue
        overlapping.append(period)

    if not overlapping:
        return {**base, "share_to_buyback": None, "status": "unconfirmed",
                "why": "no documented split covers this window"}
    if len(overlapping) > 1:
        return {**base, "share_to_buyback": None, "status": "unconfirmed",
                "why": f"window spans {len(overlapping)} different splits — no single share applies"}

    period = overlapping[0]
    return {
        "share_to_buyback": period.get("share_to_buyback"),
        "destination_split": period.get("destination_split"),
        "status": period.get("status", "unconfirmed"),
        "source_url": period.get("source_url"),
        "source_date": period.get("source_date"),
        "why": period.get("note", ""),
    }


def share_is_per_product(project_name: str) -> bool:
    """True where share_to_buyback is a dict of per-product shares and must never be collapsed."""
    fs = (PROJECT_BY_NAME.get(project_name) or {}).get("fee_split") or {}
    return isinstance(fs.get("share_to_buyback"), dict)


def per_product_shares(project_name: str) -> list[tuple[str, str, float | None]]:
    """[(product, rendered value, midpoint or None)] for a per-product split. Data only."""
    fs = (PROJECT_BY_NAME.get(project_name) or {}).get("fee_split") or {}
    share = fs.get("share_to_buyback")
    if not isinstance(share, dict):
        return []
    out = []
    for product, value in share.items():
        if isinstance(value, dict):
            lo, hi = value.get("min"), value.get("max")
            out.append((product, f"{lo:.0%}-{hi:.0%}", None))   # a range is never collapsed to a midpoint
        else:
            out.append((product, f"{value:.0%}", float(value)))
    return out


# =======================================================================================
# OPEN QUESTIONS — things a human must resolve that are not "a metric has no data".
#
# These always appear in the Gap Report so they cannot be forgotten. Each names what is
# unresolved, why it matters, and what would settle it. Delete an entry once it is settled
# and the corresponding config change is made.
# =======================================================================================
OPEN_QUESTIONS = [
    {
        "project": "Sky", "topic": "fee-split primary source URL",
        "reason": "The 55/45 split is recorded from a Sky governance Executive Proposal approved 2026-08-13, "
                  "but the PRIMARY governance forum post URL has not been captured, so fee_split.source_url "
                  "is None.",
        "suggestion": "Find the Executive Proposal post on the Sky governance forum and put its URL in "
                      "config.py under Sky fee_split.source_url and in the history entry for 2026-08-13.",
    },
    {
        "project": "Sky", "topic": "pre-2026-08-13 split",
        "reason": "The split that applied BEFORE 2026-08-13 is not documented, so every window ending before "
                  "that date, and every window spanning it, is unconfirmed and its derived figure suppressed. "
                  "The current 55% is deliberately NOT applied retroactively.",
        "suggestion": "Document the earlier split and add it to Sky fee_split.history as its own period.",
    },
    {
        "project": "Venice AI", "topic": "burn methodology — does the burn route to the zero address",
        "reason": "NARROWED, no longer a total blackout. The VVV token and the staking contract are now verified "
                  "from Venice's own developer docs, so Venice has a working lock-rate metric (VVV staked) and a "
                  "readable zero-address balance. What remains open is METHODOLOGY: it is not established that "
                  "Venice's Buy and Burn actually routes tokens to the zero address, and the claimed "
                  "buy_and_burn contract could not be corroborated in any Venice-authored source — the programme "
                  "is described as a revenue-funded behaviour, so there may be no contract to read. The "
                  "zero-address figure may therefore understate or miss the burn entirely.",
        "suggestion": "Establish from Venice's own material where burned VVV actually goes. If it is the zero "
                      "address, the existing read is complete. If it is a different sink, add that address. If "
                      "the burn is executed as supply reduction rather than a transfer, set burn_read_method to "
                      "protocol_level and delete the buy_and_burn entry.",
    },
    {
        "project": "Venice AI", "topic": "DIEM is a second Venice asset, not tracked",
        "reason": "Venice has a second token, DIEM, at 0xF4d97F2da56e8c3098f3a8D538DB630A2606a024. It is NOT "
                  "tracked by this tool and no pipeline has been built for it. Recorded here only so its "
                  "existence is not rediscovered from scratch later, and so that any Venice supply or burn "
                  "figure is understood to cover VVV alone.",
        "suggestion": "Decide whether DIEM belongs in the universe at all. If it does, it needs its own project "
                      "entry rather than being folded into Venice AI's VVV figures.",
    },
    {
        "project": "Uniswap", "topic": "UNI token address on Unichain",
        "reason": "The Unichain TokenJar and Firepit are verified, but there is no UNI token contract declared "
                  "on Unichain to call balanceOf against, and a token address is not valid across chains. Both "
                  "Unichain components are refused, so Uniswap's burn and fee figures are marked PARTIAL — the "
                  "mainnet paths only.",
        "suggestion": "Find the bridged UNI address on Unichain and add it to contracts as a chain 'unichain' "
                      "erc20_total_supply entry. The adapter picks the same-chain token automatically once it "
                      "exists, and the PARTIAL marking clears.",
    },
    {
        "project": "Pendle", "topic": "is there a documented data endpoint robots permits",
        "reason": "app.pendle.finance robots.txt disallows the sPENDLE staking page, and that is not worked "
                  "around. LOW STAKES — the tier 2 read of sPENDLE.totalSupply() is verified and working, so "
                  "this was only ever a cross-check.",
        "suggestion": "If Pendle publishes a documented API or data endpoint distinct from the disallowed page, "
                      "point the sources.yaml entry at that. Otherwise leave it; nothing is lost.",
    },
    {
        "project": "Aave", "topic": "is the buyback immutable or committee-directed",
        "reason": "Sources genuinely conflict, so fee_split.programmed is 'unconfirmed_conflict'. One June 2026 "
                  "report states governance can redirect, pause or resize the buyback without a protocol-level "
                  "change. Buybacks have been paused since 2026-04-19.",
        "suggestion": "VERIFY against governance.aave.com directly. Do not resolve this from a secondary source.",
    },
    {
        "project": "Aave", "topic": "stkAAVE read method",
        "reason": "The stkAAVE address is on file but the read method is NOT established. stkAAVE is likely a "
                  "fungible ERC-20, but assuming that is exactly how a veNFT position count ends up in a cell "
                  "labelled tokens locked, so nothing is read.",
        "suggestion": "Confirm whether stkAAVE is ERC-20 or NFT-based, then set read_method to "
                      "'erc20_total_supply' or 'escrow_balance_of' and token_standard in config.py.",
    },
    {
        "project": "Pendle", "topic": "sPENDLE expected symbol",
        "reason": "Address and read method are both settled: 0x999999999991E178D52Cd95AFd4b00d066664144, ERC-20, "
                  "so totalSupply is the staked amount. Only the expected SYMBOL is unconfirmed. The on-chain "
                  "check compares case-insensitively and fails CLOSED, so a mismatch rejects the address with a "
                  "clear message rather than returning a wrong number — but it would also block a correct one.",
        "suggestion": "Read symbol() on the contract and set expected_symbol in config.py to match.",
    },
    {
        "project": "PancakeSwap", "topic": "complete LayerZero OFT deployment list",
        "reason": "CAKE is a multi-chain LayerZero OFT, so BSC totalSupply is NOT total supply. Two deployments "
                  "are on file (BSC and Base) and their sum is labelled PARTIAL everywhere it appears. The "
                  "self-reported figure remains the source of record.",
        "suggestion": "Get the full OFT deployment list from PancakeSwap's own docs and add each as a contract "
                      "entry. Until then the on-chain figure stays marked partial rather than looking complete.",
    },
    {
        "project": "Fluid", "topic": "does FLUID staking exist at all",
        "reason": "NOT RESOLVED. This machine has no network access and could not open docs.fluid.io. The metric "
                  "was left in place rather than removed on a guess, so the column may be permanently empty.",
        "suggestion": "Check docs.fluid.io. If there is no staking or lock mechanism, remove locked_tokens from "
                      "Fluid's archetype 3 block. If there is one, complete the disabled sources.yaml entry.",
    },
    {
        "project": "Hyperliquid", "topic": "AQAv2 revenue accruing but not yet paid",
        "reason": "AQAv2 went live 2026-08-26 and accrues on 30-day cycles, but the first payment is not expected "
                  "until 2026-10-03. Recorded with booked=False so the model cannot book revenue that has not landed.",
        "suggestion": "After 2026-10-03, confirm the first payment arrived, then set booked=True and add the source URL.",
    },
    {
        "project": "Hyperliquid", "topic": "is there a second Assistance Fund",
        "reason": "A community address directory lists an 'Assistance Fund 2' at "
                  "0xccd69f432ce1d8c9cdc31bd535dd11b37cbea4ea. It does NOT appear in Hyperliquid's own docs, so it "
                  "has not been added. IF IT IS REAL, the burn total is understated — the same failure mode as "
                  "omitting Uniswap's Unichain burn path.",
        "suggestion": "Check Hyperliquid's own documentation and on-chain history. If genuine, add it as another "
                      "burn path; the adapter already sums multiple paths.",
    },
    {
        "project": "Uniswap", "topic": "release() burn threshold value",
        "reason": "The UNI-burn threshold required to call release() is GOVERNANCE-SETTABLE: the Uniswap "
                  "Governance Timelock holds thresholdSetter and can appoint a different setter. No number is "
                  "hardcoded, so anything derived from it stays suppressed.",
        "suggestion": "Read the current threshold on-chain, or record it from governance with its source URL and "
                      "date, under Uniswap governance_parameters.release_threshold_uni.",
    },
    {
        "project": "GEODNET", "topic": "is the pre-August-2026 snapshot table still queryable",
        "severity": 1,
        "reason": "Query 8683175 only reaches back to 2026-08-01. Everything earlier is unioned in from a STATIC "
                  "table, dune.geodnet_console.result_geod_tokens_burned_20260731. That is a dependency, not a "
                  "detail: if it stops being queryable under the account the API key belongs to, a full "
                  "re-backfill silently loses ALL GEODNET burn history before August 2026. Reachability could NOT "
                  "be confirmed from here — it needs a Dune API key, which this environment does not have.",
        "suggestion": "Run the query once with the key set and confirm rows dated before 2026-08 come back. If the "
                      "snapshot has gone, export the history the store already holds BEFORE re-backfilling, "
                      "because a fresh backfill would replace it with the post-August window alone.",
    },
    {
        "project": "Ether.fi", "topic": "Dune query 8683038 column mapping",
        "severity": 1,
        "reason": "The query id is wired, but the column mapping did not reach this config — the SQL was "
                  "referenced as unchanged from an earlier message that is not in the record. Nothing is read "
                  "until date_col and value_col are set, and nothing is guessed. Tier 4 is Ether.fi's ONLY "
                  "automated route for staked_tokens; there is no contract read for it.",
        "suggestion": "Run once with DUNE_API_KEY set: the Gap Report will name every column the query actually "
                      "returns. Put the date column and the value column into config under Ether.fi "
                      "dune_queries.staked_tokens, then re-run.",
    },
    {
        "project": "Aerodrome", "topic": "Dune query 2986047 column mapping",
        "reason": "The query id is wired and the SQL was not visible, so the column mapping is unset and nothing "
                  "is read. LOW STAKES: this writes locked_tokens_dashboard, a cross-check of the tier 2 read of "
                  "AERO.balanceOf(escrow) which is verified and working, so it can never overwrite the contract "
                  "figure and its absence costs only the second opinion.",
        "suggestion": "Same as Ether.fi: one run with a key names the real columns, then set date_col and "
                      "value_col in config.",
    },
    {
        "project": "GEODNET", "topic": "is the Polygon buyback wallet still relevant",
        "reason": "0xc327C048d75398Da9DB5254679bb84a4a9e42010 is on file as a Polygon-era buyback wallet but is "
                  "NOT referenced by the working burn query. It remains unverified and refused.",
        "suggestion": "Confirm whether it still matters post-migration; if not, delete the entry.",
    },
    {
        "project": "Virtuals", "topic": "buyback split and DefiLlama slug",
        "reason": "Agent launch and trading fees route to buyback but the split is not documented, so the implied "
                  "figure is suppressed. defillama_fees_slug is ASSUMED to match defillama_protocol.",
        "suggestion": "Document the buyback split with a source URL and date, and confirm the DefiLlama slug.",
    },
    {
        "project": "Tron", "topic": "confirm the BURN_TRX node endpoint on the first run",
        "reason": "The burn MECHANISM is resolved — TIP #49 moved burned TRX into DynamicPropertiesStore under "
                  "BURN_TRX, so the read is a node API call — but the exact endpoint path and response key were "
                  "not verified live.",
        "suggestion": "On the first run check the Run Log. If the read failed, adjust node_api.path and "
                      "node_api.response_keys. Do NOT substitute a black-hole address balance.",
    },
]


# =======================================================================================
# CONFIG INTEGRITY CHECKS — run at import, so a misconfiguration fails loudly at the top of
# the run rather than producing a plausible-looking wrong number three tabs deep.
# =======================================================================================
class ConfigError(ValueError):
    """A configuration mistake that would produce a wrong number if it reached a cell."""


def _check_lock_contracts() -> list[str]:
    """Every rule that, if broken, yields a figure orders of magnitude wrong but still plausible."""
    errors = []
    for p in PROJECTS:
        for key, c in (p.get("contracts") or {}).items():
            where = f"{p['name']}/{key}"
            std, method, kind = c.get("token_standard"), c.get("read_method"), c.get("kind")

            if std is not None and std not in TOKEN_STANDARDS:
                errors.append(f"{where}: token_standard {std!r} is not one of {sorted(TOKEN_STANDARDS)}")

            if method is not None and method not in LOCK_READ_METHODS:
                errors.append(f"{where}: read_method {method!r} is not one of {sorted(LOCK_READ_METHODS)}")

            # THE check this whole mechanism exists for. A veNFT is ERC-721, so its totalSupply()
            # returns the NUMBER OF POSITIONS, not tokens locked. Reading it as an ERC-20 supply
            # gives a number that is wrong by orders of magnitude and looks entirely normal.
            if std == "erc721" and method == "erc20_total_supply":
                errors.append(
                    f"{where}: token_standard 'erc721' with read_method 'erc20_total_supply' — "
                    f"totalSupply() on a veNFT returns a COUNT OF POSITIONS, not tokens locked. "
                    f"Use read_method 'escrow_balance_of' (underlying.balanceOf(escrow)) instead.")
            if std == "erc721" and kind == "erc20_total_supply":
                errors.append(
                    f"{where}: kind 'erc20_total_supply' on an ERC-721 contract — same problem, "
                    f"totalSupply() on a veNFT counts positions.")

            if method == "escrow_balance_of" and not c.get("underlying"):
                errors.append(f"{where}: read_method 'escrow_balance_of' needs `underlying` naming the "
                              f"contract key whose balanceOf is called")
            if method == "escrow_balance_of" and c.get("underlying") not in (p.get("contracts") or {}):
                errors.append(f"{where}: underlying {c.get('underlying')!r} is not a contract key on this project")
    return errors


def _check_addresses() -> list[str]:
    """Every EVM address must be EIP-55 checksummed.

    web3.py rejects a non-checksummed address, and it rejects it for CALL ARGUMENTS as well as for
    contract addresses — which is how balanceOf(0x...dead) failed on a live run while the contract
    address beside it was fine. The adapter normalises everything before it reaches web3, so this
    check is about keeping the file itself correct and catching a typo that is not a valid address
    at all. Skipped silently where web3 is not installed, so config stays importable without it.
    """
    try:
        from web3 import Web3
    except ImportError:
        return []
    errors = []
    for p in PROJECTS:
        for key, c in (p.get("contracts") or {}).items():
            address = c.get("address")
            if not address or not str(address).startswith("0x"):
                continue          # a Solana address or an unfilled slot
            try:
                canonical = Web3.to_checksum_address(address)
            except Exception:  # noqa: BLE001
                errors.append(f"{p['name']}/{key}: {address!r} is not a valid EVM address")
                continue
            if canonical != address:
                errors.append(f"{p['name']}/{key}: {address} is not EIP-55 checksummed, should be {canonical}")
    return errors


def validate_config(raise_on_error: bool = True) -> list[str]:
    errors = _check_lock_contracts() + _check_addresses()
    if errors and raise_on_error:
        raise ConfigError("config.py has errors that would produce wrong numbers:\n  - " + "\n  - ".join(errors))
    return errors


validate_config()
