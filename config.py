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

import re

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
    # THE SPLIT SERIES. Where a cumulative burn is mostly a one-off supply event, the cumulative
    # and the recurring programme are two different figures and only the second is a demand
    # signal. Venice is the case: ~99.5% of its cumulative is a single March 2025 airdrop burn.
    # This metric is the RECURRING half, and it is the flow — the airdrop predates every
    # observation we hold, so the differenced flow contains only revenue-funded burns.
    # It is what belongs in archetype 3 buyback comparisons; the cumulative does not.
    # THE HEADLINE RATIO, where a protocol publishes it itself. Above 1.0, burns outpace issuance
    # and supply is shrinking. Stored as a SOURCED figure and kept separate from anything derived,
    # so the two can be compared: a divergence means our derivation is wrong somewhere, and that is
    # the point of having both.
    # A buyback that lands in a governance-controlled treasury. NOT a burn — the tokens exist and
    # governance can move them — and NOT locked supply like Chainlink's Reserve, which has a
    # timelock and a stated intention not to withdraw. Its own metric so it can never be summed
    # into either: those two have opposite signs and this is neither.
    "treasury_holding_tokens":    {"label": "Treasury holding (governance-controlled, redeployable)",
                                   "kind": "stock", "unit": "tokens", "archetypes": [3, 4],
                                   "tiers": [2, 3], "sanity_min": 0, "sanity_max": 1e15},
    "burn_mint_ratio":            {"label": "Burn ÷ mint ratio (as published by the protocol)",
                                   "kind": "stock", "unit": "count", "archetypes": [1, 4],
                                   "tiers": [3, 4], "sanity_min": 0, "sanity_max": 100,
                                   "only_projects": ["Canton"]},
    "burn_revenue_funded":        {"label": "Revenue-funded burn (recurring programme only)",
                                   "kind": "flow", "unit": "tokens", "archetypes": [3, 4],
                                   "tiers": [2, 3, 4], "sanity_min": 0, "sanity_max": 1e12,
                                   "only_projects": ["Venice AI"]},
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
    # THE PROTOCOL'S OWN ACCOUNTING OF THE SAME THING, stored ALONGSIDE locked_tokens rather than
    # instead of it. locked_tokens is read as TOKEN.balanceOf(pool), which counts every token at
    # the pool address — staked principal plus anything stray or in transit — and is therefore an
    # UPPER BOUND. A pool that exposes its own getTotalPrincipal() can be asked directly, and the
    # two figures are then a real cross-check on each other: agreement confirms the read,
    # divergence says the balance contains something that is not staked principal.
    # only_projects because most escrows expose no such call; the rest keep the single figure.
    "locked_tokens_principal":    {"label": "Tokens locked (protocol's own principal accounting)", "kind": "stock", "unit": "tokens", "archetypes": [3], "tiers": [2], "sanity_min": 0, "sanity_max": 1e15, "only_projects": ["Chainlink"]},
    # Published lock rate, stored AS PUBLISHED. Deliberately not derived from locked_tokens /
    # supply: where a protocol publishes its own lock rate the published figure is the citable
    # one, and a derived percentage sitting next to it would invite the two being confused.
    # Stored as a FRACTION (0.17452 = 17.452%), the same convention as every other pct metric,
    # and displayed with a 0.0% format. The upper bound is 1.0 and that is load-bearing: Dune
    # 8683038 publishes the same measure twice, as perc_staked (0.17452) and perc_staked_cnt
    # (17.452), and mapping the wrong one would put a plausible-looking figure 100x too large in
    # the sheet. At this bound the x100 column is REJECTED to the Review Queue instead.
    # NOT the same measure as locked_tokens / circulating_supply, and the two must not be read as
    # one. This is Ether.fi's own perc_staked from Dune 8683038, over ITS denominator (17.45%);
    # dividing our locked_tokens by CoinGecko's circulating gives 14.65%. Neither is wrong — they
    # count different things as circulating — so the label says which this is.
    "lock_rate_pct":              {"label": "Lock rate, as PUBLISHED by the protocol (own denominator)",
                                   "kind": "stock", "unit": "pct", "archetypes": [3], "tiers": [3, 4],
                                   "sanity_min": 0, "sanity_max": 1.0, "only_projects": ["Ether.fi"]},
    # Holder count. only_projects because Ether.fi is the one project with a source for it today,
    # and the Gap Report is meant to be a to-do list rather than a census of everything missing.
    # Widen the list as soon as a second project gets a route to it.
    "staker_count":               {"label": "Holders / stakers", "kind": "stock", "unit": "count",
                                   "archetypes": [3], "tiers": [3, 4],
                                   "sanity_min": 0, "sanity_max": 1e8, "only_projects": ["Ether.fi"]},
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

# =======================================================================================
# BURN MECHANISM — what the protocol ACTUALLY DOES with the tokens, as distinct from the
# address the tokens go to. This distinction is not academic; missing it produced a wrong
# model of Sky that survived careful address verification.
#
# Sky's addresses were checked against protocol docs and were correct. What was never checked
# was the CLAIM ATTACHED to them: that Sky burns by transferring to a dead address. It does
# not. Per the ChainSecurity Dss Flappers audit (July 2026), a Splitter divides surplus
# between a Flapper and a reward farm; the Flapper trades USDS for the gem on UniswapV2 and
# sends the proceeds to a configurable RECEIVER — and in the FlapperUniV2 variant deposits the
# gem back into the pool as LP tokens. No dead address anywhere. A zero balance at 0x0 was
# therefore the CORRECT reading of a question nobody should have been asking.
#
# The lesson generalises: "burn = transfer to dead address" is an ASSUMPTION, and every
# project carrying it needs that assumption sourced to the protocol's own documentation, not
# inherited from the shape of the config. So burn_mechanism is now REQUIRED on any project
# whose burn_read_method is "transfer" (enforced in validate_config), and its status drives
# what the adapter is willing to report:
#
#   confirmed  the protocol's own docs describe this mechanism. Read and report normally.
#   assumed    plausible, not sourced. Read, but every figure is FLAGGED and a gap names the
#              specific document that would settle it. NOT refused: withdrawing a working
#              figure on a suspicion is its own kind of wrong.
#   refuted    the docs describe something else. Nothing is read; the burn figure does not exist.
# THREE DISTINCT FAILURE MODES, all found within two days, all of which produced a burn figure
# that was wrong in a way nothing in the sheet revealed. They are NOT variations of one problem
# and must not be collapsed into a single "burn is unreliable" caveat — each has a different
# symptom, a different check and a different fix:
#
#   1. WRONG MECHANISM          Sky. The protocol does not burn by transferring to a dead address
#                               at all — it swaps on an AMM and sends proceeds to a configurable
#                               receiver. No address balance can model it. SYMPTOM: a zero that
#                               is genuinely correct and completely beside the point. FIX: remove
#                               the address entirely; do not substitute another.
#                               CHECK: _check_burn_mechanisms (status refuted -> read refused).
#
#   2. RIGHT MECHANISM, WRONG ADDRESS   Uniswap. Tokens really do go to a dead address, and the
#                               config read the balance of the contract that EXECUTES the burn
#                               instead of the destination. SYMPTOM: a permanent zero on a
#                               protocol demonstrably burning 100k+ tokens a day. FIX: read the
#                               destination; keep the executor as kind 'burn_executor'.
#                               CHECK: _check_burn_destinations.
#
#   3. UNDOCUMENTED MECHANISM   PancakeSwap, GEODNET. Neither confirmed nor refuted. SYMPTOM:
#                               nothing — the figure looks fine and may be fine. FIX: read the
#                               protocol's own material. CHECK: none possible; it needs a human.
#                               Status stays 'assumed' and every figure it produces is flagged.
#
#   4. RIGHT MECHANISM, RIGHT ADDRESS, WRONG COMPOSITION   Venice AI, found 2026-09-14 and the
#                               subtlest of the four. The mechanism is confirmed, the address
#                               receives, the number is arithmetically CORRECT — and it still
#                               misleads, because ~99.5% of it is a one-off airdrop burn answering
#                               a different question from the one the column asks. Read as the
#                               scale of a revenue-funded buyback it overstates by ~200x.
#                               SYMPTOM: none at all. Nothing is broken. Every check above passes.
#                               FIX: split the series — the cumulative is a supply event, the flow
#                               is the demand signal. CHECK: burn_composition, declared per project.
#
# Modes 3 and 4 are not milder versions of 1 and 2. Mode 3 is the state the others were in before
# anyone looked. Mode 4 is the one no validation can catch, because nothing is wrong with the
# figure — only with the use it invites.
BURN_MECHANISM_STATUSES = {"confirmed", "assumed", "refuted"}

BURN_MECHANISM_MODELS = {
    "transfer_to_dead_address": "Tokens are sent to an address nobody controls. Cumulative burn is that "
                                "address's balance; it only ever rises.",
    "protocol_level_destruction": "Supply is destroyed with no transfer (a burn() call, a fee sink). No "
                                  "address holds the burned tokens.",
    "amm_swap_to_receiver": "Surplus is swapped for the token on an AMM and the proceeds are sent to a "
                            "CONFIGURABLE receiver. Whether that is destruction, a treasury holding or an "
                            "LP position depends on the receiver and the variant — it is NOT a burn until "
                            "established. A balance read cannot model it.",
    "no_burn": "The project does not destroy or sequester supply at all. Issuance is then the supply "
               "change exactly, with no burn term — the easiest case, and it still has to be DECLARED "
               "rather than inferred from the absence of a burn address, because 'we found no burn' and "
               "'there is no burn' are different statements.",
    "undetermined": "Not established. Nothing is read.",
}


# =======================================================================================
# CONFIDENCE — what a reader can ACT on, which is a different question from what is missing.
#
# The Gap Report says what is absent. It says nothing about whether the numbers that ARE there
# can be trusted, and that is the question someone reading the sheet actually has. Three bands,
# derived mechanically from state already held — never hand-assigned, because a hand-assigned
# confidence is an opinion that goes stale the moment the data changes:
#
#   GREEN  verified source, mechanism confirmed, no PARTIAL marker, read succeeded, more than
#          one observation. Use it.
#   AMBER  a real number, qualified. Directionally useful; read the note before quoting it.
#   RED    not a number at all — suppressed, refused or gapped.
#
# MANUAL_QUARTERLY: some figures move annually and cost four rounds of engineering to automate.
# A number typed in once a quarter is the right answer for those, not a failure to automate. A
# metric carrying this flag leaves the Gap Report for a separate manual list, and goes stale
# after 120 days rather than being reported as unresolved every single run.
MANUAL_QUARTERLY_STALE_DAYS = 120

# NON-COMPARABLE (failure mode 4): the figure is correct and still must not be quoted in the
# column it sits in, because it answers a different question. No validation can catch this —
# nothing is wrong with the number. Only composition reveals it, so it is DECLARED.
def metric_label(project_name: str, metric: str) -> str:
    """The label a metric carries FOR THIS PROJECT.

    Almost always the library's label — a metric means the same thing everywhere, which is what
    makes a column comparable. The exception is where a read is correct but narrower than the
    metric's name claims: Uniswap's buyback_fund_balance is the UNI balance of the TokenJar, and
    the TokenJar holds FEE TOKENS, so calling it the fund balance overstates what was measured.
    Relabelling says what was actually read without redefining the metric for everyone else.
    """
    p = PROJECT_BY_NAME.get(project_name) or {}
    override = (p.get("metric_labels") or {}).get(metric)
    return override or (METRICS.get(metric) or {}).get("label", metric)


def is_non_comparable(project_name: str, metric: str) -> dict | None:
    p = PROJECT_BY_NAME.get(project_name) or {}
    return (p.get("non_comparable") or {}).get(metric)


# Metrics that describe where a buyback's tokens END UP. Only these are affected by a
# destination being indeterminate — the project's revenue and supply figures are unaffected.
DESTINATION_METRICS = ("actual_buyback_tokens", "actual_buyback_usd", "buyback_fund_balance")


def destination_indeterminate(project_name: str, metric: str) -> dict | None:
    """The buyback happens; where the tokens come to rest does not resolve to burn OR to hold."""
    if metric not in DESTINATION_METRICS:
        return None
    p = PROJECT_BY_NAME.get(project_name) or {}
    return p.get("destination_indeterminate")


def is_manual_quarterly(project_name: str, metric: str) -> bool:
    p = PROJECT_BY_NAME.get(project_name) or {}
    return metric in (p.get("manual_quarterly") or ())


def orphaned_contract_keys(project_name: str, source: str) -> list[str]:
    """Contract keys named in a stored row's source that no longer exist in config.

    THE STORE UPSERTS AND NEVER DELETES, so removing a contract stops new rows and leaves every
    old one in place — with its original source string, and reading as current until it ages past
    the stale threshold. Sky's burn_zero rows outlived the contract's removal by days and showed
    as a measured zero on a refuted mechanism, at full confidence.

    A source naming a contract config no longer has is, by definition, measuring something this
    tool has decided not to measure. That is not a low-confidence figure; it is not a figure.
    """
    src = str(source or "")
    if not src.startswith("chain:"):
        return []                       # coingecko, dune, schedule, derived — no contract behind it
    body = ":".join(part for part in src.split(":")[1:] if part not in ("PARTIAL", "delta"))
    if body.startswith("sum(") and body.endswith(")"):
        pieces = body[4:-1].split("+")
    else:
        pieces = [body]
    contracts = (PROJECT_BY_NAME.get(project_name) or {}).get("contracts") or {}
    missing = []
    for piece in pieces:
        bits = piece.split(":")
        key = bits[-1] if bits else ""
        if key and key not in contracts:
            missing.append(key)
    return missing


def metric_addresses_unverified(project_name: str, metric: str) -> list[str]:
    """Contract entries serving this metric that were never checked against protocol docs."""
    p = PROJECT_BY_NAME.get(project_name) or {}
    kinds = {"burn_address_balance": ("burn_address_balance", "spl_token_account"),
             "gross_burn_tokens": ("burn_address_balance", "spl_token_account"),
             "burn_revenue_funded": ("burn_address_balance", "spl_token_account"),
             "total_supply": ("erc20_total_supply", "spl_mint"),
             "locked_tokens": ("ve_total_supply",),
             "buyback_fund_balance": ("buyback_fund_balance",)}.get(metric)
    if not kinds:
        return []
    return [k for k, v in (p.get("contracts") or {}).items()
            if v.get("kind") in kinds and not v.get("verified")]


BURN_METRICS = ("burn_address_balance", "gross_burn_tokens", "burn_revenue_funded")


def burn_mechanism(project: dict) -> dict:
    """The project's burn mechanism block, defaulting to an explicit 'assumed' rather than silence."""
    return project.get("burn_mechanism") or {
        "model": "undetermined", "status": "assumed", "source_url": None, "source_date": None,
        "note": "No burn_mechanism block in config — the model has never been established.",
    }


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
              read_method=None, token_standard=None, underlying=None, call=None,
              supply_is_partial=False, partial_reason="", holder_has_code=None,
              destination_status=None, destination_note=""):
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
        # The function to call on this contract. None means the default for the kind
        # (totalSupply, or balanceOf where a holder is involved). Named explicitly only where the
        # figure lives behind a non-ERC-20 call, e.g. getTotalPrincipal() on a staking pool.
        "call": call,
        "token_standard": token_standard,
        "underlying": underlying,          # contract key whose balanceOf is called, for escrow_balance_of
        "supply_is_partial": bool(supply_is_partial),
        "partial_reason": partial_reason,
        # Is this address SUPPOSED to be a deployed contract? None means "work it out from the kind
        # and the address". A burn or dead address is an EOA nobody controls, so empty bytecode is
        # what CORRECT looks like there — checking for code would reject a perfectly good address.
        "holder_has_code": holder_has_code,
        # destination_status "disputed": the ADDRESS is right and the read works, but the
        # contract's ROLE for this project is in doubt. The adapter reads it, captures the value
        # to staging as evidence, and does NOT store it as a metric — labelling a balance
        # "cumulative burned" asserts a destination we are no longer confident about.
        "destination_status": destination_status,
        "destination_note": destination_note,
        "note": note,
    }


def _split_period(from_date, to_date, share, status, source_url=None, source_date=None,
                  destination_split=None, note="", known_change=None):
    """One period of a fee split. from_date None means "everything before to_date".

    known_change records a change we KNOW happened inside this period but cannot date or size.
    A period carrying one can never resolve to a single share, even if somebody later fills one
    in — see split_for_window. That guard exists because the dangerous moment is not today, when
    the period is unconfirmed and suppressed anyway; it is the day someone documents one number
    for a span that actually contained two regimes, and the suppression lifts on a wrong figure.
    """
    return {
        "from": from_date, "to": to_date, "share_to_buyback": share,
        "destination_split": destination_split, "status": status,
        "source_url": source_url, "source_date": source_date, "note": note,
        "known_change": known_change,
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
        "burn_mechanism": {
            "model": "protocol_level_destruction", "status": "assumed",
            "source_url": "https://eips.ethereum.org/EIPS/eip-1559", "source_date": None,
            "note": "ASSUMED, and the easiest of the six to promote: EIP-1559 burns the base fee at the protocol "
                    "level, which is why totalSupply falls rather than a dead address filling up. The EIP "
                    "is the primary source and is linked here; it is marked assumed only because nothing "
                    "in this repo records anyone having read it to settle this question.",
        },
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
        "burn_mechanism": {
            "model": "protocol_level_destruction", "status": "assumed",
            "source_url": None, "source_date": None,
            "note": "ASSUMED. Solana burns 50% of each transaction fee at the protocol level. No Solana document is "
                    "on file stating it, and the burn/issuance split matters here more than most: SOL "
                    "issues by inflation schedule AND burns fees, so the supply delta nets two large "
                    "opposing flows.",
        },
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
        "burn_mechanism": {
            "model": "protocol_level_destruction", "status": "assumed",
            "source_url": None, "source_date": None,
            "note": "ASSUMED. TRX is burned at the protocol level (energy/bandwidth and account creation), which the "
                    "BURN_TRX node read reflects — that read returns a protocol counter, not an address "
                    "balance, which is itself evidence for this model. No Tron document on file.",
        },
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
        "archetypes": [1, 3, 4], "archetypes_held": [],
        # TWO REVENUE STREAMS THAT MUST NOT BE CONFLATED.
        #   (4) GAS BURN — protocol-level, unrelated to Intents. 70% of every transaction fee is
        #       destroyed with no transfer; 30% goes to the contract developer.
        #   (3) INTENTS BUYBACK — off-chain-ish market buying funded by Intents fees, landing in a
        #       treasury. It DOES NOT BURN. See destination_decision.
        # They have different sources, different destinations and different signs on float.
        "fee_split": {
            "share_to_buyback": None,
            "source_url": "https://docs.near-intents.org/resources/fees",
            "source_date": "2026-09-14",
            "programmed": True,
            "status": "active",
            # THE RETAINED SLICE. Partner fees are set via `appFees` in BASIS POINTS, and the
            # protocol splits each fee 50/50: half routes automatically to the 1Click protocol
            # address, half to the partner's own recipient. ONLY THE 1CLICK HALF IS DURABLE BUY
            # PRESSURE — the partner half is theirs and can be resold immediately. Treating the
            # whole fee as buyback funding would overstate absorption by 2x.
            "retained_slice": {
                "share_retained": 0.50,
                "to": "the 1Click protocol address",
                "remainder_to": "the partner's own recipient, which can be resold — not durable buy pressure",
                "set_by": "appFees, in basis points, per partner",
                "source_url": "https://docs.near-intents.org/resources/fees",
                "source_date": "2026-09-14",
            },
            "note": "NEAR Intents partner fees split 50/50 between the 1Click protocol address and the "
                    "partner recipient. Only the 1Click half funds the buyback. The share of TOTAL "
                    "protocol revenue reaching the buyback is not a single documented number, so "
                    "share_to_buyback stays None.",
        },
        "burn_split": {"share_of_fees_burned": 0.70, "source_url": "https://docs.near.org/protocol/gas", "source_date": "2026-09-14", "status": "active",
                       "note": "GAS BURN ONLY, and nothing to do with Intents: 70% of every transaction fee is "
                               "burned at the protocol level, 30% goes to the contract developer. Do not "
                               "apply this share to Intents revenue — that stream is bought back, not burned."},
        # ISSUANCE: 2.5% MAX ANNUAL, halved from 5% on 2025-10-30 (~32.2m NEAR/yr).
        #
        # NOT DECLARED AS A tokens_per_day STEP, and the reason is the same one that blocks World
        # Mobile: 2.5% is a RATE ON TOTAL SUPPLY, and issuance_schedule takes a fixed daily token
        # count. The ~32.2m/yr figure is that rate applied to a supply base at a moment in time, so
        # hardcoding it would freeze a moving denominator. Recorded here as a declared rate instead.
        "issuance_schedule": None,
        "issuance_rate_declared": {
            "annual_rate_max": 0.025,
            "effective_from": "2025-10-30",
            "supersedes": {"annual_rate_max": 0.05, "note": "halved on 2025-10-30"},
            "approx_tokens_per_year": 32_200_000,
            "approx_note": "~32.2m NEAR/yr is the rate applied to the supply base at the time of the "
                           "change. It is NOT declared as a schedule step because the base moves; a "
                           "fixed tokens_per_day would drift from the rule it came from.",
            "source_url": "https://docs.near.org/protocol/gas",
            "source_date": "2026-09-14",
            # THE SPLIT IS THE UNCERTAIN PART, AND THE UNCERTAINTY IS SPECIFIC.
            # Secondary sources say 90% validators / 10% protocol treasury. Our own LIVE ON-CHAIN
            # READ of protocol_reward_rate returned [0, 1] — exactly ZERO. That is ALSO exactly
            # nearcore's serde default (core/chain-configs/src/genesis_config.rs:168,
            # `#[default(Rational32::from_integer(0))]`), so the reading is consistent with TWO
            # different worlds: a genuine on-chain zero, or a read that fell through to the struct
            # default without ever seeing chain state. Nearcore does NOT vendor mainnet genesis —
            # every path under core/chain-configs/res/ 404s, and the only [1, 10] values in the
            # repo are inside test fixtures using `test.near` with epoch_length 60 — so this
            # cannot be settled from source.
            # THE LIVE READ IS USED, because it is the only direct chain evidence we hold, and it
            # is flagged uncertain rather than quietly overridden by secondary sources.
            "treasury_share": {
                "value": 0.0,
                "source": "live on-chain read of protocol_reward_rate = [0, 1]",
                "source_date": "2026-09-14",
                "status": "uncertain",
                "conflicts_with": "secondary sources stating 90% validators / 10% protocol treasury",
                "why_uncertain": "[0, 1] is byte-identical to nearcore's serde DEFAULT for this field, so a "
                                 "genuine zero and a read that never reached chain state are "
                                 "indistinguishable from the value alone. Mainnet genesis is not vendored "
                                 "in nearcore, so source inspection cannot separate them.",
                "would_settle_it": "a mainnet genesis_config from a NEAR-operated endpoint or archive, or a "
                                   "second independent RPC returning the same field",
            },
        },
        # PROTOCOL BURN, not transfer burn. Supply is destroyed with no transfer, so there is no
        # address balance to read and burn_address is deliberately None.
        "burn_address": None,
        "burn_mechanism": {
            "model": "protocol_level_destruction", "status": "confirmed",
            "source_url": "https://docs.near.org/protocol/gas", "source_date": "2026-09-14",
            "note": "CONFIRMED for the GAS BURN: 70% of each transaction fee is destroyed at the protocol "
                    "level and 30% is paid to the contract developer. No address is involved. This "
                    "mechanism describes the gas stream ONLY — the Intents stream is a market buyback "
                    "that does not burn at all.",
        },
        "burn_read_method": "protocol_level",
        "burn_read_note": "Gas burn at the protocol level, 70% of each transaction fee (30% to the contract "
                          "developer). No address to read; needs a chain-data or dashboard source.",
        # ============ DESTINATION DECISION, APPLIED 2026-09-14 ============
        # THE INTENTS BUYBACK DOES NOT BURN.
        #
        # DefiLlama's own metric definition for near-intents states it explicitly: "Since
        # 2026-02-23, NEAR's captured Intents revenue is used to buy back $NEAR on the open market
        # (NOT BURNED), returning value to holders."
        #
        # NEAR's own dashboard says the buyback "permanently remove[s] NEAR from circulation".
        # THE TWO CONFLICT, and DefiLlama is treated as authoritative here — not because it is a
        # better source in general, but because of WHAT KIND OF CLAIM each is making. DefiLlama's
        # is a precise technical statement about mechanism, written to define a metric. The
        # dashboard's is marketing phrasing that is ALSO true of a treasury hold: tokens bought and
        # held are "removed from circulation" in the loose sense while remaining entirely
        # redeployable. A burn and a hold have opposite signs on permanent supply, so the
        # imprecise phrasing cannot be allowed to decide it.
        #
        # The conflicting wording is recorded so this is not re-litigated without NEW evidence.
        # ==================================================================
        "destination_decision": {
            "decided": "distribute/hold — NOT burn",
            "decided_on": "2026-09-14",
            "authoritative_source": "https://defillama.com/protocol/near-intents",
            "authoritative_quote": "Since 2026-02-23, NEAR's captured Intents revenue is used to buy back "
                                   "$NEAR on the open market (NOT BURNED), returning value to holders.",
            "conflicting_source": "NEAR's own Intents dashboard",
            "conflicting_wording": "permanently remove NEAR from circulation",
            "why_defillama_wins": "a precise technical claim about mechanism beats marketing phrasing that "
                                  "is equally true of a treasury hold. A burn and a hold have opposite "
                                  "signs on permanent supply.",
            "reopen_if": "NEAR publishes a burn transaction or a contract that destroys the repurchased "
                         "NEAR. Do not reopen on dashboard wording alone.",
        },
        "buyback_destination": "hold", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "treasury_redeployable",
        "destination_source_url": "https://defillama.com/protocol/near-intents",
        "destination_confirmed_date": "2026-09-14",
        # NEAR IS NOT AN EVM CHAIN, BUT THE INTENTS TREASURY IS EVM-READABLE.
        # The buyback destination is a Base address, labelled "NEAR Intents: Treasury" on BaseScan,
        # holding ~$43.4m across six chains as of June 2026. Only the Base leg is read: the other
        # five are separate addresses not on file, so the figure is PARTIAL by construction.
        "contracts": {
            "intents_treasury_base": _contract(
                "0x2CfF890f0378a11913B6129B2E97417a2c302680", "base", "treasury_holding", "NEAR",
                "https://basescan.org/address/0x2cff890f0378a11913b6129b2e97417a2c302680",
                verified="2026-09-14", provenance="BaseScan contract label 'NEAR Intents: Treasury'",
                holder_has_code=True, token_standard="erc20", underlying=None,
                purpose="NEAR Intents Treasury on Base — the destination of the Intents buyback. Read as a "
                        "TREASURY HOLDING, never as a burn: the decision above establishes the repurchased "
                        "NEAR is held, not destroyed.",
                note="NO SAME-CHAIN TOKEN IS DECLARED, so this read will be REFUSED by the adapter's "
                     "same-chain guard and will appear in the Gap Report saying exactly that. That is the "
                     "correct outcome and not an oversight: bridged NEAR on Base is a wrapped "
                     "representation whose address is not on file, and pointing balanceOf at a guessed "
                     "wrapper would return a number with no defensible meaning. The address is recorded so "
                     "the gap names something specific. PARTIAL REGARDLESS: the treasury spans six chains "
                     "(~$43.4m total, June 2026) and only the Base leg is on file."),
        },
        # ** MAKES THE protocol_reward_rate UNCERTAINTY VISIBLE ON THE SHEET, not only in the Gap
        # Report. ** emissions_tokens is issuance reaching SUPPLIERS, and how much of NEAR's 2.5%
        # reaches validators rather than the protocol treasury is exactly the number our live read
        # and the secondary sources disagree about. That is a COMPOSITION problem — the right
        # mechanism, a correct total, and an unknown split inside it — which is what non_comparable
        # is for. It forces AMBER with the reason attached, so nobody reads the emissions figure as
        # settled while the split is not.
        "non_comparable": {
            "emissions_tokens": {
                "why": "the validator/treasury SPLIT of NEAR's 2.5% issuance is unresolved. Our live "
                       "on-chain read of protocol_reward_rate returned [0, 1] — zero treasury share, so "
                       "100% to validators — while every secondary source says 90/10. [0, 1] is "
                       "byte-identical to nearcore's serde DEFAULT for that field, so a genuine zero and "
                       "a read that never reached chain state are indistinguishable. The TOTAL issuance "
                       "is unaffected; what is unknown is how much of it is emissions to suppliers.",
                "use_instead": "gross_issuance_tokens, which is the same under either split. Treat "
                               "emissions_tokens as an upper bound until a second independent endpoint "
                               "confirms the rate — see OPEN_QUESTIONS",
            },
        },
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
        "revenue_reference": {
            "source": "DefiLlama", "as_of": "2026-09-14",
            "fees_30d_usd": 4_050_000, "revenue_30d_usd": 911_826,
            "fees_annualised_usd": 44_220_000, "revenue_annualised_usd": 5_770_000,
            "capture_rate": 0.22,
            "note": "NEAR Intents operates across 26 chains; NEAR itself holds 43.8% share. These are "
                    "SANITY BOUNDS for the archetype 3 figures, not stored metrics.",
        },
        "materiality": "medium",
        "notes": "THREE archetypes, TWO revenue streams that must never be conflated. (4) GAS BURN: 70% of "
                 "every transaction fee destroyed at the protocol level, 30% to the contract developer — no "
                 "address, needs a chain-data source. (3) INTENTS BUYBACK: funded by Intents fees and "
                 "DOES NOT BURN — DefiLlama's own metric definition says so explicitly, and it is treated "
                 "as authoritative over NEAR's dashboard wording because a precise mechanism claim beats "
                 "marketing phrasing that is equally true of a treasury hold. Destination is the Base "
                 "Intents Treasury, read as treasury_redeployable. Fees split 50/50 between the 1Click "
                 "protocol address and the partner recipient; only the 1Click half is durable buy pressure. "
                 "ISSUANCE 2.5% max annual (halved from 5% on 2025-10-30); the treasury share is taken from "
                 "our live read of 0% and FLAGGED UNCERTAIN, because [0, 1] is byte-identical to nearcore's "
                 "serde default and mainnet genesis is not vendored anywhere we can read.",
    },
    {
        "name": "Canton", "symbol": "CC",
        "coingecko_id": "canton-network",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://www.canton.network/", "source_date": BRIEF_DATE, "status": "unconfirmed",
                       "note": "Traffic fees are burned. Has both its own dashboard and a Dune page. Document the share."},
        # Pre-set curve, HALVED at the start of 2026: 20bn -> 10bn CC/yr. Long-run equilibrium is
        # ~2.5bn/yr, so this declines again and the current step must not be carried forward for
        # ever once the next reduction is dated.
        # THE HALVING DATE IS APPROXIMATE — "~Jan 2026" is all the source gives, so 2026-01-01 is
        # a placeholder. It sits inside a month either way, and no derived figure turns on the day.
        "issuance_schedule": {
            "steps": [
                {"from": "2025-01-01", "tokens_per_day": 20_000_000_000 / 365},
                {"from": "2026-01-01", "tokens_per_day": 10_000_000_000 / 365},
            ],
            "source_url": None, "source_date": "2026-09-14", "status": "active",
            "note": "20bn CC/yr halving to 10bn/yr at ~Jan 2026 (date approximate). Long-run "
                    "equilibrium ~2.5bn/yr — add that step when it is dated. SV share fell 80% -> 20%, "
                    "to 5% after year ten; Apps rising to 62% until mid-2029; 5% of emissions to the "
                    "Dev Fund. CIP-0096 ended passive liveness rewards on 2026-04-30.",
        },
        "contracts": {},
        # PROTOCOL BURN, not transfer burn. Supply is destroyed with no transfer, so there is no
        # address balance to read and burn_address is deliberately None.
        "burn_address": None,
        # Canton publishes its own burn/mint ratio, which is precisely the figure this tool
        # exists to produce — and the trajectory is the story: 0.16 in Jan 2026 to 0.72 in early
        # Sep 2026, against cumulative burns of 5.01bn CC. Above 1.0 would mean burns outpacing
        # issuance. These are REFERENCE POINTS, not a series: they validate whatever a source
        # eventually returns, and a scraped figure that disagrees with them is a scraper bug.
        # KEYS ARE A CONTRACT: metric / period / value / source, matching what
        # check_reference_values reads. Written with "when" and "source_url" first time round,
        # which crashed the live run — see _check_reference_values() below, which now rejects
        # that at import rather than at tier 4 of a nine-minute run.
        "reference_values": [
            {"metric": "burn_mint_ratio", "period": "2026-01", "value": 0.16,
             "source": "Canton's published weekly burn/mint ratio"},
            {"metric": "burn_mint_ratio", "period": "2026-09", "value": 0.72,
             "source": "Canton's published weekly burn/mint ratio, early Sep 2026"},
        ],
        "burn_mechanism": {
            "model": "protocol_level_destruction", "status": "confirmed",
            "source_url": None, "source_date": "2026-09-14",
            "source_note": "Confirmed in the 2026-09-14 build brief. A PRIMARY URL IS STILL NEEDED — "
                           "this is the one 'confirmed' mechanism in the config with no linkable "
                           "document behind it.",
            "note": "CONFIRMED protocol-level destruction, and the pricing is what makes it so: fees are "
                    "DENOMINATED IN USD, per MB, but PAID BY BURNING CC at the on-chain conversion rate. "
                    "No address receives the CC; supply falls. Canton publishing its own burn/mint ratio "
                    "is further evidence — a transfer burn would not need one, because the dead address "
                    "would be the record.",
        },
        "burn_read_method": "protocol_level",
        "burn_read_note": "Burn-mint equilibrium: CC is burned when Global Synchroniser traffic is purchased, priced in USD. Destroyed at the protocol level, no transfer. Needs the Canton dashboard or a Dune source.",
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "destination_effect": "removed_from_supply",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "fees_usd", "tx_count", "active_addresses"),
        # burn_mint_ratio joins the hand-entered list rather than waiting on a scraper. Canton
        # publishes it weekly; typing it in gets the single most directly relevant published figure
        # in this universe into the sheet now, instead of after a round of scraper work. The
        # reference_values above validate whatever is typed: a hand-entry that disagrees with
        # 0.16/0.72 at those dates is a typo, and will be flagged as one.
        "manual_quarterly": ["supply_units", "utilisation_pct", "burn_mint_ratio"],
        "materiality": "medium",
        "notes": "Not on DefiLlama. Own dashboard (tier 3) + Dune page (tier 4). Verify the CoinGecko id.",
    },
    {
        "name": "Plume", "symbol": "PLUME",
        "coingecko_id": "plume",
        "defillama_fees_slug": "plume", "defillama_protocol": None, "defillama_chain": "Plume Mainnet",
        # ARCHETYPE 1 ONLY. ARCHETYPE 3 REMOVED 2026-09-14.
        # Plume's own staking docs list "earn a share of ecosystem revenue" as a benefit BEING
        # EXPLORED, not a live mechanism. The staking reward that IS live is EMISSIONS-FUNDED, and
        # Plume has zero fee revenue on DefiLlama — so there is no revenue to share even if the
        # mechanism existed. This is the SAME FAILURE MODE AS BITTENSOR: a yield that looks like
        # revenue capture and is actually inflation paid to stakers. Counting it as archetype 3
        # would report dilution as value accrual.
        "archetypes": [1], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 0.0, "source_url": "https://docs.plume.org/",
                      "source_date": "2026-09-14", "programmed": True, "status": "n/a",
                      "note": "ZERO. Plume's own staking docs describe revenue sharing as BEING EXPLORED, "
                              "not live, and DefiLlama shows zero fee revenue for the chain. The live "
                              "staking reward is EMISSIONS-funded — the Bittensor failure mode. Do not "
                              "re-add archetype 3 without a documented, live revenue-to-staker route."},
        "burn_split": None,
        # ============ PLUME IS A NATIVE GAS TOKEN. THERE IS NO ERC-20 CONTRACT. ============
        # Plume's own contract-addresses page (docs.plume.org/plume/developers/contract-addresses)
        # lists PLUME as 0x0000000000000000000000000000000000000000 — the zero address — because
        # PLUME is the NATIVE GAS TOKEN of its own chain, exactly as ETH is on Ethereum.
        #
        # THE ZERO ADDRESS IS CORRECT, NOT MISSING DATA. And it must NOT be wired as a contract:
        # totalSupply() or balanceOf() against 0x0 will fail or return nothing meaningful, and a
        # zero landing in the supply column would look like a read that worked. `contracts` is
        # therefore deliberately EMPTY and supply comes from CoinGecko, which handles native-asset
        # supply correctly and is already the tier 1 source.
        #
        # WRAPPED PLUME (WPLUME) is a real ERC-20 at 0xEa237441c92CAe6FC17Caaf9a7acB3f953be4bd1 on
        # Plume Mainnet. IT IS NOT A SUPPLY PROXY: its totalSupply is only the WRAPPED float, which
        # is a fraction of native circulating supply, and the gap between them is unmeasured. It is
        # recorded here and given no read slot. (Plume Mainnet also has no RPC in DEFAULT_RPC, so
        # a read would be refused at the chain-coverage guard regardless.)
        # ====================================================================================
        "native_gas_token": {
            "address": "0x0000000000000000000000000000000000000000",
            "why": "PLUME is the native gas token of Plume Mainnet, like ETH on Ethereum — there is no "
                   "ERC-20 contract to read",
            "source_url": "https://docs.plume.org/plume/developers/contract-addresses",
            "source_date": "2026-09-14",
            "supply_from": "CoinGecko (tier 1), which reports native-asset supply correctly",
            "never": "do not attempt totalSupply()/balanceOf() on the zero address — it fails or returns "
                     "nothing meaningful, and a zero in the supply column reads as a successful read",
            "wrapped": {"symbol": "WPLUME", "address": "0xEa237441c92CAe6FC17Caaf9a7acB3f953be4bd1",
                        "chain": "plume", "is_supply_proxy": False,
                        "why_not": "captures WRAPPED supply only, not total native circulating; the gap is "
                                   "unmeasured"},
        },
        "issuance_schedule": None,
        # VESTING, NOT INFLATION — and the distinction is the whole point of this tool.
        # 10,000,000,000 PLUME total, 2,000,000,000 (20%) at TGE, 33.45% released in Year 1 and the
        # remaining 66.55% over the following three years. ~6,181,909,938 (61.8%) unlocked today.
        #
        # TOTAL SUPPLY STAYS ~FLAT while CIRCULATING expands. So gross_issuance_tokens derived from
        # delta-total-supply will read near zero and be RIGHT — the sell pressure is entirely in the
        # circulating series, and reading the total-supply line alone would miss it completely.
        "vesting_schedule": {
            "total": 10_000_000_000,
            "tge_unlocked": 2_000_000_000, "tge_pct": 0.20,
            "year_1_released_pct": 0.3345,
            "remainder_pct": 0.6655, "remainder_years": 3,
            "unlocked_now": 6_181_909_938, "unlocked_now_pct": 0.618, "unlocked_as_of": "2026-09-14",
            "source_url": "https://docs.plume.org/",
            "is_inflation": False,
            "note": "VESTING, NOT INFLATION. Total supply stays ~flat; circulating expands. Any issuance "
                    "figure derived from delta-total-supply will correctly read ~zero — the supply "
                    "pressure lives in the CIRCULATING series and must be read there.",
        },
        "contracts": {},
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "none",
        "dune_queries": _dune("gross_issuance_tokens", "staked_tokens", "emissions_tokens", "tx_count", "active_addresses"),
        # ARCHETYPE 1 IS PLUME'S REAL STRENGTH — keep these prominent rather than burying them
        # under a supply story the chain does not have.
        "operating_reference": [
            {"metric": "rwa_xyz_usd", "value": 645_000_000, "as_of": "2026-02",
             "what": "tokenized assets on Plume", "source": "app.rwa.xyz/networks/plume"},
            {"metric": "active_addresses", "value": 280_000, "as_of": "2026-02",
             "what": "RWA wallet holders (280,000+) — largest chain by RWA participants",
             "source": "app.rwa.xyz/networks/plume"},
        ],
        "materiality": "medium",
        "notes": "ARCHETYPE 1 ONLY — archetype 3 removed 2026-09-14: Plume's own staking docs describe "
                 "revenue sharing as BEING EXPLORED, not live, and the live staking reward is "
                 "emissions-funded against zero fee revenue (the Bittensor failure mode). "
                 "PLUME IS A NATIVE GAS TOKEN: its address really is 0x0, there is no ERC-20, and no "
                 "contract read is attempted — supply comes from CoinGecko. WPLUME "
                 "(0xEa237441c92CAe6FC17Caaf9a7acB3f953be4bd1) is a real ERC-20 but captures wrapped "
                 "supply only and is NOT a supply proxy. "
                 "SUPPLY PRESSURE IS VESTING, NOT INFLATION: 10bn total, 2bn at TGE, ~6.18bn (61.8%) "
                 "unlocked — total supply stays flat while circulating expands, so read the circulating "
                 "series, not the delta-total-supply line. "
                 "RWA is the real story: 280,000+ RWA wallet holders and $645m tokenized (Feb 2026), the "
                 "largest chain by RWA participants.",
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
        "burn_mechanism": {
            "model": "protocol_level_destruction", "status": "assumed",
            "source_url": None, "source_date": None,
            "note": "ASSUMED. Injective runs a weekly burn auction that destroys INJ at the protocol level. No "
                    "Injective document on file, and the auction cadence means the burn is lumpy — worth "
                    "knowing before reading any single period's issuance derivation.",
        },
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
                     "silently resolved. FUNDING: 50% of SVR fees (Smart Value Recapture — oracle MEV, e.g. the "
                     "MEV on an Aave liquidation) plus other Payment Abstraction revenue."),
            # THE SECOND DESTINATION, AND IT MUST NOT SHARE A CELL WITH THE FIRST.
            # Chainlink routes revenue to TWO places with OPPOSITE effects on float: the Reserve
            # HOLDS LINK (locked supply) and the staking pools DISTRIBUTE it (yield payout). Summing
            # them answers neither question. They are kept apart by giving them different METRICS,
            # not merely different keys: the Reserve is buyback_fund_balance, the pools are
            # locked_tokens. The adapter sums within a metric, so anything sharing
            # buyback_fund_balance with the Reserve would be silently merged into it.
            #
            # v0.2 pool size is 45,000,000 LINK (~8% of circulating at launch), split across the two
            # pools below. The LOCK RATE is the sum of both pools' LINK balances — which is what
            # these two entries produce, since both serve locked_tokens.
            # ADDRESSES INDEPENDENTLY CORROBORATED 2026-09-14 against Chainlink's own
            # instructions.txt in that repo, which lists the official addresses explicitly:
            # the LINK token, the v0.1 legacy protocol, both v0.2 pools, the reward vault and the
            # price-feed alerts controller. All four we use match exactly, and the two we
            # deliberately do NOT read (v0.1 legacy, reward vault) are confirmed as the separate
            # things we took them for.
            #
            # THE TOTAL IS NOT CORROBORATED, AND THAT IS A DIFFERENT CLAIM. Neither that file nor
            # the repo README states a pool size or a staked total, so the 42,536,190.83 LINK read
            # of 2026-09-14 rests on ONE source. Its plausibility against the 45,000,000 programme
            # size (~94% full) is not confirmation — a wrong address can be plausible too, which is
            # exactly what Maple's 0.51 demonstrated in the same run.
            #
            # AND OUR READ IS AN UPPER BOUND ON STAKED PRINCIPAL. LINK.balanceOf(pool) counts every
            # LINK sitting at the pool address, including anything in transit or stray. The pools'
            # own accounting figure is getTotalPrincipal(); Chainlink's instructions.txt points
            # stakers at getStakerPrincipal for the per-staker equivalent. Reading
            # getTotalPrincipal() on both pools is the second opinion worth having, and it needs a
            # call this adapter does not make today.
            "staking_community": _contract(
                "0xBc10f2E862ED4502144c7d632a3459F49DFCDB5e", "ethereum", "ve_total_supply", "LINK",
                "https://github.com/smartcontractkit/chainlink-staking-v0.2-public-guide",
                verified="2026-09-14", provenance="Chainlink's own staking v0.2 public guide — address "
                                                  "listed explicitly in instructions.txt",
                read_method="escrow_balance_of", token_standard="erc20", underlying="token",
                purpose="Staking v0.2 COMMUNITY pool. Read as LINK.balanceOf(pool) — the LINK actually "
                        "staked, not any share-token supply. Summed with the node operator pool to give "
                        "Chainlink's lock rate."),
            "staking_node_operator": _contract(
                "0xA1d76A7cA72128541E9FCAcafBdA3a92EF94fDc5", "ethereum", "ve_total_supply", "LINK",
                "https://github.com/smartcontractkit/chainlink-staking-v0.2-public-guide",
                verified="2026-09-14", provenance="Chainlink's own staking v0.2 public guide — address "
                                                  "listed explicitly in instructions.txt",
                read_method="escrow_balance_of", token_standard="erc20", underlying="token",
                purpose="Staking v0.2 NODE OPERATOR pool. Read as LINK.balanceOf(pool). Summed with the "
                        "community pool: together they are the 45,000,000 LINK v0.2 programme."),
            # THE SAME TWO POOLS, ASKED THEIR OWN QUESTION. These call getTotalPrincipal() ON THE
            # POOL rather than reading LINK's balance of it, and land in a SEPARATE metric so
            # nothing is replaced: locked_tokens stays the balanceOf sum (the upper bound) and
            # locked_tokens_principal is the protocol's own accounting. The cross_checks entry
            # below compares them and flags a divergence beyond 1% to the Review Queue, which
            # carries AMBER onto locked_tokens.
            #
            # The call is made on the pool; symbol() and decimals() come from LINK via
            # `underlying`, because a staking pool is not an ERC-20 and has neither. Taking 18 on
            # faith instead would be exactly the kind of assumption that produced Maple's 0.51.
            "staking_community_principal": _contract(
                "0xBc10f2E862ED4502144c7d632a3459F49DFCDB5e", "ethereum", "stake_principal", "LINK",
                "https://github.com/smartcontractkit/chainlink-staking-v0.2-public-guide",
                verified="2026-09-14", provenance="Chainlink's own staking v0.2 public guide — address "
                                                  "listed explicitly in instructions.txt; the call is the "
                                                  "pool-wide twin of getStakerPrincipal, which that file "
                                                  "points individual stakers at",
                call="getTotalPrincipal", token_standard="erc20", underlying="token",
                holder_has_code=True,
                purpose="Community pool's OWN accounting of staked principal. Summed with the node "
                        "operator pool and compared against the balanceOf sum."),
            "staking_node_operator_principal": _contract(
                "0xA1d76A7cA72128541E9FCAcafBdA3a92EF94fDc5", "ethereum", "stake_principal", "LINK",
                "https://github.com/smartcontractkit/chainlink-staking-v0.2-public-guide",
                verified="2026-09-14", provenance="Chainlink's own staking v0.2 public guide — address "
                                                  "listed explicitly in instructions.txt",
                call="getTotalPrincipal", token_standard="erc20", underlying="token",
                holder_has_code=True,
                purpose="Node operator pool's OWN accounting of staked principal."),
            # TWO ADDRESSES DELIBERATELY NOT GIVEN READ SLOTS, each for its own reason.
            #
            # STAKING REWARD VAULT  0x996913c8c08472f584ab8834e925b06D0eb1D813
            #   The DISTRIBUTE destination — where rewards accrue before being paid to stakers.
            #   It has no read slot because every kind available to it is wrong: buyback_fund_balance
            #   would SUM IT INTO THE RESERVE, which is exactly the merge this project must not make,
            #   and treasury_holding would label a distribution vault a treasury. Recorded here with
            #   its source rather than mislabelled. Source: smartcontractkit/
            #   chainlink-staking-v0.2-public-guide.
            #
            # LEGACY STAKING v0.1  0x3feB1e09b4bb0E7f0387CeE092a52e85797ab889
            #   Superseded by v0.2. NOT read, and deliberately not summed: v0.1 balances alongside
            #   v0.2 would overstate the current lock rate by whatever has not been migrated out.
            #   Recorded so nobody re-adds it as a "missing pool". Source: as above.
        },
        # HOLD, not burn and not distribute. The Reserve has a multi-day withdrawal timelock and
        # Chainlink states no withdrawals are expected for multiple years, so accumulated LINK is
        # LOCKED SUPPLY. It must never share a formula with a yield payout: a distribution returns
        # tokens to float, a hold removes them from it. Opposite sign on effective float.
        "buyback_destination": "hold", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "locked_supply",
        "destination_source_url": "https://blog.chain.link/chainlink-reserve-strategic-link-reserve/",
        "destination_confirmed_date": TODAY_VERIFIED,
        # TWO DESTINATIONS, RECORDED AS TWO. Collapsing them into one "revenue to holders" figure
        # would average a hold against a payout, which is an average of opposite signs.
        "destination_routes": [
            {"route": "Chainlink Reserve", "address": "0x9A709B7B69EA42D5eeb1ceBC48674C69E1569eC6",
             "effect": "locked_supply", "metric": "buyback_fund_balance",
             "funded_by": "50% of SVR (Smart Value Recapture) fees plus other Payment Abstraction revenue",
             "why_locked": "multi-day withdrawal timelock; Chainlink states no withdrawals are expected "
                           "for multiple years",
             "source_url": "https://blog.chain.link/chainlink-reserve-strategic-link-reserve/"},
            {"route": "Staking v0.2 rewards", "address": "0x996913c8c08472f584ab8834e925b06D0eb1D813",
             "effect": "distribute", "metric": None,
             "funded_by": "Payment Abstraction revenue routed to the staking reward vault",
             "why_no_metric": "no kind fits: buyback_fund_balance would merge it into the Reserve, "
                              "treasury_holding would mislabel a distribution vault",
             "source_url": "https://github.com/smartcontractkit/chainlink-staking-v0.2-public-guide"},
        ],
        "cross_checks": [
            # TWO READS OF THE SAME POOLS, ON THE SAME CHAIN, IN THE SAME RUN — so any divergence
            # is a fact about what the pool balance contains, not about two sources disagreeing.
            # The tolerance is tight (1%) precisely because both come from the same block: unlike
            # a contract-versus-dashboard comparison there is no timing skew to absorb.
            # Flagged on locked_tokens, which is where the headline lock rate is read.
            {"primary": "locked_tokens", "primary_source": "tier 2 LINK.balanceOf(pool), summed — UPPER BOUND",
             "secondary": "locked_tokens_principal", "secondary_source": "tier 2 pool.getTotalPrincipal(), summed",
             "tolerance": 0.01, "prefer": "secondary",
             "note": "PREFER THE SECONDARY, unusually — and that is the point of keeping both. "
                     "locked_tokens is LINK.balanceOf(pool) summed over both v0.2 pools, which counts "
                     "every LINK at those addresses including anything stray or in transit, so it can "
                     "only bound staked principal from above. getTotalPrincipal() is the pool's own "
                     "accounting of what is actually staked. Neither replaces the other: agreement "
                     "confirms the read, and a persistent gap says the pool holds LINK that is not "
                     "principal — which is a finding, not an error. The 42,536,190.83 LINK read of "
                     "2026-09-14 was the balanceOf figure with no second opinion at all."},
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
            # The v0.2 programme is 45,000,000 LINK. A principal figure above that is impossible
            # rather than surprising, so it is rejected rather than flagged; the small headroom
            # absorbs a programme resize without waving through an order-of-magnitude error.
            "locked_tokens_principal": {"min": 0, "max": 60_000_000},
            "locked_tokens": {"min": 0, "max": 60_000_000},
            "buyback_fund_balance_dashboard": {"min": 0, "max": 100_000_000, "change_threshold_pct": 40},
        },
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "staked_tokens", "emissions_tokens"),
        "materiality": "high",
        "notes": "The Reserve, not the LINK token contract, is the archetype 3 revenue input. Destination is HOLD: "
                 "a multi-day withdrawal timelock with no withdrawals expected for years, so accumulated LINK is "
                 "locked supply rather than a payout. Reference points for the series: c.2.17m LINK (Feb 2026), "
                 "c.5.2m LINK (Jul 2026). "
                 "TWO DESTINATIONS, NEVER MERGED: the Reserve HOLDS (locked supply) and the staking v0.2 pools "
                 "DISTRIBUTE (yield payout). They are separated by metric, not just by key — the Reserve is "
                 "buyback_fund_balance, the two staking pools are locked_tokens — because the adapter sums "
                 "within a metric and would otherwise merge them. v0.2 programme size is 45,000,000 LINK "
                 "(~8% of circulating at launch); the lock rate is the SUM of both pools. "
                 "ADDRESSES CORROBORATED, TOTAL NOT: Chainlink's own instructions.txt lists all four "
                 "addresses explicitly and they match, but no Chainlink source on file states a staked "
                 "total, so the 42,536,190.83 LINK read of 2026-09-14 has ONE source. It is also an UPPER "
                 "BOUND on staked principal — balanceOf counts stray or in-transit LINK, where the pools' "
                 "own getTotalPrincipal() would not.",
    },
    # ------------------------------------------------------------------ Archetype 2 (+3/+4)
    {
        "name": "World Mobile", "symbol": "WMTX",
        "coingecko_id": "world-mobile-token",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 3], "archetypes_held": [],
        # MECHANISM CONFIRMED, DESTINATION NOT. Fiat telecom revenue buys WMTx on exchanges — that
        # much is stated. WHERE the bought tokens go is stated NOWHERE: not burned, not locked, not
        # distributed, as far as any World Mobile material on file says. Those are three opposite
        # signs on effective float, so the implied figure is all that can be computed and the
        # ACTUAL buyback metrics are suppressed rather than guessed. See UNAVAILABLE.
        "fee_split": {"share_to_buyback": None, "source_url": "https://worldmobile.io/", "source_date": "2026-09-14",
                      "programmed": False, "status": "unconfirmed",
                      "note": "Revenue buyback CONFIRMED as a mechanism (fiat telecom revenue buys WMTx on "
                              "exchanges). The SHARE is undocumented and so is the DESTINATION — see "
                              "destination_undocumented below. Implied buyback only."},
        "burn_split": None,
        # DESTINATION UNDOCUMENTED — not indeterminate (Maple's SSF, where the uses ARE stated and
        # conflict), not disputed (Sky, where a claim was refuted). Simply: nobody has said. The
        # three possibilities — burn, treasury hold, distribute — have opposite signs, so no
        # default is safe and none is chosen.
        "destination_undocumented": {
            "what": "WMTx repurchased on exchanges with fiat telecom revenue",
            "why": "no World Mobile material on file states whether repurchased WMTx is burned, held "
                   "by the treasury, or redistributed",
            "effect": "actual_buyback_tokens and actual_buyback_usd are suppressed; the implied figure "
                      "from the revenue side is the only one computed",
        },
        # A RATE, NOT A TOKEN COUNT — and declaring it needs a base the sources do not give.
        #
        #   CONFIRMED  2,000,000,000 WMTx hard cap, enforced IN THE CONTRACT ITSELF:
        #              ERC20Capped(2_000_000_000 * 10**decimals()). Not a docs claim — bytecode.
        #   CONFIRMED  29% to Node Operators / Staking IS THE ENTIRE inflationary emission, so
        #              cumulative gross issuance is capped at 0.29 x 2bn = 580,000,000 WMTx ever.
        #   CONFIRMED  11.41% initial ANNUAL inflation (The Block, theblock.co/price/257077/
        #              world-mobile-token), declining to ZERO by ~2030.
        #   ASSUMPTION the DECAY SHAPE. Linear is NOT confirmed from World Mobile's own materials.
        #
        #   STILL MISSING, and why no step is declared: WHAT THE 11.41% IS A PERCENTAGE OF. Not
        #   max supply — 11.41% of 2bn is 228,200,000 in year one, and any declining curve from
        #   there to zero blows through the 580,000,000 ceiling several times over. Ethplorer
        #   reports Ethereum total supply at 1,020,252,845 WMTx (~51% of cap), which is consistent
        #   with the schedule but is a MEASURED stock, not the declared base. Back-solving the
        #   base from the ceiling and an assumed shape would be CONSTRUCTING the number.
        #   Also missing: the emission START DATE (WMT migrated to WMTx; which event starts the
        #   clock is not established).
        #
        #   AND INCONSISTENT: "zero by year 20 (~2030)" puts year one in 2010, before the token
        #   existed. Either the horizon is ~2041 or it is ~9 years; the daily rate differs by more
        #   than 2x depending on which. Three unknowns and a contradiction do not reduce to one
        #   tokens_per_day, so the schedule stays silent and the Gap Report carries the row.
        "issuance_schedule": None,
        "max_supply_declared": {
            "value": 2_000_000_000,
            "source_url": "https://etherscan.io/address/0xDBB5Cf12408a3Ac17d668037Ce289f9eA75439D7#code",
            "source_date": "2026-09-14",
            "note": "ERC20Capped(2_000_000_000 * 10**decimals()) read off the deployed source. The cap is "
                    "enforced by the contract, not merely documented, which is a stronger fact than a "
                    "tokenomics page.",
        },
        # FOUR DEPLOYMENTS, THREE DISTINCT ADDRESSES, ONE READ.
        #
        # Only Ethereum is wired as a metric-bearing contract, and that is deliberate: the adapter
        # SUMS every contract serving the same metric (fetch/chain.py _emit_parts), and summing four
        # deployments is correct ONLY under burn-and-mint bridging. Under lock-and-mint it
        # double-counts the locked float on every remote chain. The bridge model is NOT established
        # — the same open question as GEOD — so the other three addresses are recorded here with
        # their sources and NOT given read slots. They cannot be summed by accident, and they are
        # not lost.
        #
        #   Arbitrum  0xDBB5Cf12408a3Ac17d668037Ce289f9eA75439D7  (same address as Ethereum)
        #   BSC       0xDBB5Cf12408a3Ac17d668037Ce289f9eA75439D7  (same address as Ethereum)
        #   Base      0x3e31966d4f81C72D2a55310A6365A56A4393E98D  (DIFFERENT address)
        #
        # All four confirmed by direct source-code inspection on Etherscan / Arbiscan / BscScan /
        # BaseScan, 2026-09-14. The contract declares MINTER_ROLE and BURNER_ROLE with real
        # mint/burn/burnFrom functions, so remote supply CAN be minted independently — which is
        # exactly why the bridge model has to be settled before summing.
        "contracts": {
            "token": _contract(
                "0xDBB5Cf12408a3Ac17d668037Ce289f9eA75439D7", "ethereum", "erc20_total_supply", "WMTX",
                "https://etherscan.io/address/0xDBB5Cf12408a3Ac17d668037Ce289f9eA75439D7#code",
                verified="2026-09-14", provenance="deployed source code, inspected directly on Etherscan",
                token_standard="erc20", supply_is_partial=True,
                partial_reason="ETHEREUM ONLY. WMTx is deployed on four chains (Ethereum, Arbitrum and BSC "
                               "all at 0xDBB5Cf12408a3Ac17d668037Ce289f9eA75439D7; Base at "
                               "0x3e31966d4f81C72D2a55310A6365A56A4393E98D). The BRIDGE MODEL is not "
                               "established — lock-and-mint would make summing a double-count, "
                               "burn-and-mint would make summing correct — so only Ethereum is read and "
                               "the figure is labelled partial. Same open question as GEOD.",
                purpose="WMTx on Ethereum — the PRIMARY supply read. ERC20Capped at 2,000,000,000."),
        },
        "buyback_destination": "undocumented", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "unresolved",
        "dune_queries": {},
        # ARCHETYPE 2 OPERATING METRICS, World Mobile's own reporting as of Feb 2026.
        #
        # SUPPLIER PAYOUTS ARE NOT ALL EMISSIONS. AirNode operator earnings are frequently FIAT OR
        # STABLECOIN denominated rather than WMTx, so modelling total supplier earnings as token
        # emissions would overstate issuance by whatever share settles off-token. The 29% Node
        # Operators/Staking allocation is the token leg and the only inflationary one.
        "operating_reference": [
            {"metric": "supply_units", "value": 100_000, "as_of": "2026-02",
             "what": "AirNodes deployed (100,000+)", "source": "World Mobile's own reporting"},
            {"metric": "active_addresses", "value": 3_000_000, "as_of": "2026-02",
             "what": "daily active users", "source": "World Mobile's own reporting"},
            {"metric": "utilisation_pct", "value": None, "as_of": "2026-02",
             "what": "600+ TB/day processed — a THROUGHPUT figure, not a percentage. Recorded in words "
                     "rather than stored because utilisation_pct is a FRACTION and 600 would render as "
                     "60,000%. It needs a denominator (network capacity) before it can be a metric.",
             "source": "World Mobile's own reporting"},
        ],
        "manual_quarterly": ["supply_units", "utilisation_pct"],
        "materiality": "low",
        "notes": "Archetype 2 + 3. SUPPLY IS PARTIAL (Ethereum only) until the bridge model is settled. "
                 "ISSUANCE IS SOURCED BUT NOT DECLARABLE: the 2bn contract-enforced cap and the 29% "
                 "(580,000,000 WMTx) lifetime inflationary ceiling are confirmed, and so is an 11.41% "
                 "starting annual rate decaying to zero — but the base the 11.41% applies to, the start "
                 "date and the decay shape are all missing, and the stated horizon (zero by year 20, "
                 "~2030) is self-contradictory. BUYBACK DESTINATION IS UNDOCUMENTED, so the actual "
                 "buyback figures are suppressed and only the implied one is computed. Operator payouts "
                 "are often fiat/stablecoin — do not model all supplier earnings as emissions.",
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
            # POLYGON IS THE HOME CHAIN AND IS NOT DEPRECATED. GIP-7, which would make Solana
            # primary, is PROPOSED and not enacted — treating it as done would move the primary
            # read on the strength of a proposal.
            #
            # SUPPLY IS PARTIAL, and for a reason that is not "we could not read the others".
            # The adapter SUMS every contract serving total_supply. GEODNET bridges Polygon <-> Solana
            # with WORMHOLE NTT, which supports BOTH lock-and-mint and burn-and-mint. Under
            # lock-and-mint, Polygon supply stays outstanding while Solana supply is minted against
            # it and summing DOUBLE-COUNTS; under burn-and-mint, summing is correct. Which one GEODNET
            # runs is NOT established. Until it is, Polygon alone is the supply figure and it is
            # labelled partial. See OPEN_QUESTIONS.
            "token_polygon": _contract(
                "0xAC0F66379A6d7801D7726d5a943356A172549Adb", "polygon", "erc20_total_supply", "GEOD",
                "https://docs.geodnet.com/geod-token/geod-token-introduction", verified="2026-09-11",
                provenance="GEODNET's own token docs; originally sourced from Dune query 8683175",
                token_standard="erc20", supply_is_partial=True,
                partial_reason="POLYGON ONLY, and deliberately not summed with Solana or IoTeX: the Wormhole "
                               "NTT bridge model (lock-and-mint vs burn-and-mint) is not established, and "
                               "the two answers differ by a double-count of the entire remote float.",
                purpose="Polygon GEOD token — the PRIMARY supply read, and the token balanceOf is called "
                        "on for the Polygon burn. Polygon remains the home chain: GIP-7 (Solana primary) "
                        "is PROPOSED, not enacted."),
            # PREVIOUSLY UNTRACKED. Recorded with its own chain so the coverage guard names it
            # precisely in the Gap Report rather than it being invisible. iotex has no RPC endpoint
            # in DEFAULT_RPC, so nothing is read and nothing can be silently summed — which is the
            # correct outcome while the bridge model is open.
            "token_iotex": _contract(
                "0x8E33229206f726993E4A7bF7dA2347F3743Bf8b4", "iotex", "erc20_total_supply", "GEOD",
                "https://docs.geodnet.com/geod-token/geod-token-introduction", verified="2026-09-14",
                provenance="GEODNET's own token docs", token_standard="erc20",
                purpose="IoTeX GEOD deployment, named in GEODNET's own token documentation. Recorded for "
                        "completeness; not summed into supply while the bridge model is unresolved."),
            "burn_polygon": _contract(
                "0x000000000000000000000000000000000000dEaD", "polygon", "burn_address_balance", "GEOD",
                GEODNET_BURN_QUERY, verified="2026-09-11",
                provenance="Dune query 8683175 — the original documented source of this address",
                purpose="TRANSFER BURN — Polygon burn destination. IN SCOPE for the backfill, not historical-only: "
                        "the working query unions Polygon and Solana burns into one series."),
            "mint_solana": _contract(
                "7JA5eZdCzztSfQbJvS8aVVxMFfd81Rs9VvwnocV1mKHu", "solana", "spl_mint", "GEOD",
                "https://docs.geodnet.com/geod-token/geod-token-introduction", verified="2026-09-14",
                provenance="GEODNET's own token docs; originally sourced from Dune query 8683175",
                purpose="Solana GEOD mint. NOT summed into supply while the Wormhole NTT bridge model "
                        "is unresolved — the Solana adapter does not exist, which coincidentally gives "
                        "the correct outcome, but the reason it must not be summed is the bridge model."),
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
                note="STILL RELEVANT — do NOT remove this on migration grounds: Polygon is the home "
                     "chain and GIP-7 is only proposed. It stays UNVERIFIED for a DIFFERENT reason: "
                     "no GEODNET-authored source on file names this address, and it is not referenced "
                     "by the working burn query. Read is refused until a GEODNET source confirms it."),
        },
        "burn_mechanism": {
            "model": "transfer_to_dead_address", "status": "assumed",
            "source_url": "https://dune.com/queries/8683175", "source_date": None,
            "note": "ASSUMED, but the best-evidenced of the four. The Dune query aggregates real ERC-20 "
                    "transfers to 0x...dEaD on Polygon and a Solana burn token account, so transfers to a "
                    "dead address are OBSERVED rather than inferred — that is mechanism evidence, not just "
                    "address evidence. Still not 'confirmed': the query is our own construction, and no "
                    "GEODNET document on file states the mechanism.",
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
        "manual_quarterly": ["supply_units"],
        "materiality": "low",
        "notes": "Own dashboard (machine counts) + Dune page. Verify the CoinGecko id (peaq-2).",
    },
    {
        "name": "Render", "symbol": "RENDER",
        "coingecko_id": "render-token",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 3, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://know.rendernetwork.com/basics/burn-and-mint-equilibrium", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Burn-and-mint equilibrium: customer payments burned, node rewards minted. Render publishes burn/mint per epoch (tier 3)."},
        # Declining published schedule, 107.38m RENDER over ten years. ONLY TWO YEARS ARE ON FILE,
        # so the final step carries "until" and the schedule goes silent after it rather than
        # reporting 2025's higher rate as 2026's. Max supply rose 536.87m -> 644.25m at migration.
        "issuance_schedule": {
            "steps": [
                {"from": "2024-01-01", "tokens_per_day": 9_126_804 / 366, "until": "2024-12-31"},
                {"from": "2025-01-01", "tokens_per_day": 5_900_000 / 365, "until": "2025-12-31"},
            ],
            "source_url": None, "source_date": "2026-09-14", "status": "active",
            "note": "Y1 2024 ~9,126,804 and Y2 2025 ~5.90m. THE 2026 RATE IS NOT ON FILE and is not "
                    "extrapolated — a declining curve cannot be guessed from two points. Verify all "
                    "of it against RNP-001/006/013/015 and add the 2026 step. Y2 split was Foundation "
                    "49.15% / Node Operators 25.42%, which is the archetype 2 emissions-to-suppliers "
                    "decomposition.",
        },
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
        # ARCHETYPE 2 ONLY. ARCHETYPE 3 IS REFUTED, NOT HELD — and the distinction matters because
        # "held" means "pending evidence" and the evidence is in. There is NO revenue-to-token
        # conversion anywhere in Aethir's design: ATH pays for compute DIRECTLY to GPU providers,
        # so revenue never becomes buy pressure on ATH. The only thing resembling a buyback is the
        # Checker Node NFT repurchase, which is paid in eATH and is SUPPLY-ADDITIVE — the opposite
        # sign. archetypes_held is emptied accordingly.
        "archetypes": [2], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 0.0, "source_url": "https://docs.aethir.com/aethir-tokenomics/token-overview",
                      "source_date": "2026-09-14", "programmed": True, "status": "n/a",
                      "note": "ZERO, AND THAT IS A FINDING. ATH pays for compute directly to GPU providers — "
                              "there is no revenue-to-token conversion step anywhere in the design, so no "
                              "revenue reaches ATH holders. A June 2026 review independently states no fee "
                              "distribution, no buy-and-burn and no revenue sharing. The Checker Node "
                              "'buyback' is an NFT repurchase paid in eATH, not a token buyback, and it "
                              "INCREASES circulating ATH — see buyback_is_supply_additive."},
        "burn_split": None,
        # TWO emission streams, declared separately below in this note and summed into one
        # tokens_per_day step because the schedule loop assigns one rate per day rather than
        # accumulating concurrent streams. Only ONE of the two can currently be placed on a
        # calendar, so only that one emits.
        #
        # (a) STAKING POOLS — 1,000,000 ATH/week per pool x 2 pools = 2,000,000/week
        #     = 285,714.29 ATH/day = 104,285,714 ATH/yr (the widely quoted "~104,000,000/yr"
        #     is 2,000,000 x 52 weeks; by 365 days it is 104,285,714). THE PROMO HAS ENDED and
        #     NEITHER ITS START NOR ITS END DATE IS ON FILE, so it CANNOT be placed on the
        #     calendar and is deliberately NOT emitted — a schedule step needs two dates and
        #     inventing them would inject ~104m ATH/yr into whichever window was guessed.
        #     Add {"from": ..., "tokens_per_day": 2_000_000 / 7, "until": ...} once both dates
        #     are sourced. Also confirm no SUCCESSOR promo is currently running.
        #
        # (b) CHECKER NODE BASE REWARDS — the step below. 10% of the 42,000,000,000 max supply
        #     = 4,200,000,000 ATH, emitted daily from TGE 2024-06-12 over four years.
        #     4,200,000,000 / 1461 days = 2,874,743.33 ATH/day (1,049,281,314 ATH/yr).
        #
        #     THE BRIEF'S OWN DAILY FIGURE OF 1,150,684.93 ATH/day IS NOT USED, and the reason is
        #     arithmetic rather than judgement: 4,200,000,000 / 1,150,684.93 = 3,650 — a TEN-year
        #     divisor. Over the four-year window that rate totals 1,681,150,683 ATH, which is
        #     4.00% of supply, not the stated 10%. The three stated quantities (4.2bn total,
        #     four-year window, 1,150,684.93/day) cannot all be true; the total is anchored twice
        #     (10% AND 42bn) and the four-year window is anchored by the instructed 2028 expiry,
        #     so the derived daily rate is the one that goes. IF THE TEN-YEAR WINDOW IS THE TRUTH
        #     INSTEAD, the fix is one line: divisor 3652, "until" 2034-06-11.
        "issuance_schedule": {
            "steps": [
                # 2024-06-12 .. 2028-06-11 inclusive is exactly 1461 days, so the step emits
                # the full 4,200,000,000 and not a day more. Past it the schedule goes silent
                # rather than carrying the rate forward into a pool that has been exhausted.
                {"from": "2024-06-12", "tokens_per_day": 4_200_000_000 / 1461, "until": "2028-06-11"},
            ],
            # PHASE 2, DECLARED AS A BOUND NOT A STEP. Aethir's own token overview puts 55% of total
            # supply (~23.1bn ATH) to Checker Nodes & Compute Providers, with Phase 1 frontloaded
            # and PHASE 2 RUNNING 2028-06-12 to 2032-06-12, monthly and DECAYING. Phases 1+2
            # together are stated as 16.8bn over ~8 years. A decaying monthly curve cannot be
            # expressed as one tokens_per_day and the per-month figures are not on file, so no
            # Phase 2 step is declared — the schedule goes silent after 2028-06-11 and the Gap
            # Report says SCHEDULE EXPIRED rather than projecting a rate nobody published.
            "phase_2": {"from": "2028-06-12", "until": "2032-06-12", "cadence": "monthly",
                        "shape": "decaying", "tokens_per_day": None,
                        "note": "NOT DECLARED. The per-month figures are not on file and a decaying curve "
                                "cannot be guessed from its endpoints."},
            "allocation_reference": {
                "checker_nodes_and_compute_providers_pct": 0.55,
                "checker_nodes_and_compute_providers_tokens": 23_100_000_000,
                "phases_1_and_2_total": 16_800_000_000, "phases_1_and_2_years": 8,
                "source_url": "https://docs.aethir.com/aethir-tokenomics/token-overview"},
            # DEFILLAMA'S OWN CAVEAT, recorded because it points the wrong way from the usual one.
            # Its published unlock figure tracks ATH WITHDRAWN AFTER VESTING, and therefore EXCLUDES
            # rewards that have been earned but not yet claimed. So even DefiLlama's number
            # UNDERSTATES accrued supply — the error is not conservatism in our favour.
            "external_figure_caveat": {
                "source": "DefiLlama unlocks",
                "measures": "ATH withdrawn after vesting",
                "excludes": "rewards earned but not yet claimed",
                "direction": "UNDERSTATES accrued supply",
            },
            "source_url": "https://docs.aethir.com/aethir-tokenomics/token-overview", "source_date": "2026-09-14", "status": "active",
            "note": "Checker Node base rewards ONLY: 10% of 42bn max supply over four years from TGE "
                    "2024-06-12, 2,874,743.33 ATH/day. The staking-pool stream (2,000,000 ATH/week "
                    "across two pools, promo ended) is NOT included because neither its start nor its "
                    "end date is on file — see the comment above the block. GROSS ISSUANCE IS "
                    "THEREFORE UNDERSTATED for any window that overlapped that promo.",
        },
        # THREE CHAINS WITH DIFFERENT PURPOSES — and the purposes decide which one is read for what.
        # Aethir's own token overview (docs.aethir.com/aethir-tokenomics/token-overview) assigns:
        #   Ethereum  CANONICAL — Airdrop, Staking, and the default chain for CEX deposits
        #   Arbitrum  INTERCHAIN — Checker Node rewards AND compute rewards
        #   Solana    a third deployment
        #
        # ** THE CHECKER NODE MECHANISM LIVES ON ARBITRUM, NOT ETHEREUM. ** The supply-additive
        # NFT-repurchase-for-eATH programme runs there; Ethereum is airdrop/staking/CEX only. Any
        # read of the Checker Node mechanism must point at Arbitrum.
        #
        # ONLY ARBITRUM IS GIVEN THE SUPPLY READ, and the 42,000,000,000 total supply is confirmed
        # directly on the Arbitrum contract (Arbiscan). Ethereum and Solana are recorded WITHOUT
        # read slots for the same reason as WMTx and GEOD: the adapter sums contracts serving one
        # metric, and summing three deployments is correct only under burn-and-mint bridging, which
        # is not established here either.
        "contracts": {
            "token_arbitrum": _contract(
                "0xc87B37a581ec3257B734886d9d3a581F5A9d056c", "arbitrum", "erc20_total_supply", "ATH",
                "https://docs.aethir.com/aethir-tokenomics/token-overview",
                verified="2026-09-14", provenance="Aethir's own token overview; total supply confirmed "
                                                  "directly on the Arbitrum contract via Arbiscan",
                token_standard="erc20", supply_is_partial=True,
                partial_reason="ARBITRUM ONLY. ATH is also deployed on Ethereum "
                               "(0xbe0Ed4138121EcFC5c0E56B40517da27E6c5226B, canonical: airdrop/staking/CEX) "
                               "and Solana (Dm5BxyMetG3Aq5PaG1BrG7rBYqEMtnkjvPNMExfacVk7). The bridge model "
                               "is not established, so they are not summed.",
                purpose="ATH on ARBITRUM — the INTERCHAIN deployment carrying Checker Node rewards and "
                        "compute rewards. THE supply read: 42,000,000,000 total supply confirmed here. "
                        "Read this one, not Ethereum, for anything touching the Checker Node mechanism."),
            # RECORDED WITHOUT READ SLOTS, to avoid a cross-chain sum under an unestablished bridge:
            #   Ethereum (CANONICAL — airdrop, staking, default CEX chain)
            #     0xbe0Ed4138121EcFC5c0E56B40517da27E6c5226B
            #   Solana
            #     Dm5BxyMetG3Aq5PaG1BrG7rBYqEMtnkjvPNMExfacVk7
            # Both from docs.aethir.com/aethir-tokenomics/token-overview, 2026-09-14.
        },
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "none",
        "dune_queries": _dune("emissions_tokens", "staked_tokens"),
        # Move annually, and cost more to automate than to type. A quarterly hand-entry is the
        # right answer for these, not a failure to automate one.
        "manual_quarterly": ["supply_units", "utilisation_pct"],
        # NOT A BUYBACK — AN ISSUANCE ITEM, and recorded as one. The "Checker Node Buyback" is an
        # NFT repurchase paid in eATH, locked for one year, with redemption opened 2026-06-13 and a
        # 30-day vest once initiated. eATH CONTINUES EARNING ATH throughout the lockup, so the net
        # effect on circulating ATH is POSITIVE. Netting it against emissions like a burn would get
        # the sign wrong on the one figure this tool exists to produce. eATH also ties to an
        # EigenLayer AVS with a Pendle route for early exit, which ACCELERATES the supply effect.
        "buyback_is_supply_additive": {
            "what": "Checker Node NFT repurchase paid in eATH, 1-year lock, 30-day vest from 2026-06-13",
            "chain": "Arbitrum — the mechanism does NOT run on Ethereum",
            "effect": "increases circulating ATH — eATH keeps earning ATH during the lockup",
            "never": "do not count as a buyback, do not net against emissions",
            # NO LIVE EFFECT TODAY, and that is worth stating so nobody assumes the guard is
            # working. net_absorption() in build_workbook.py reads this flag to FLIP THE SIGN of a
            # buyback term — but Aethir is archetype [2] only, so the net-absorption formula never
            # reaches it and there is no buyback term to flip. The flag is armed for the day
            # archetype 3 is ever added in error, and inert until then.
            "currently_inert": "Aethir is archetype [2] only, so net_absorption() never evaluates a "
                               "buyback term for it. The guard is armed, not active.",
        },
        "materiality": "medium",
        "notes": "ARCHETYPE 2 ONLY, and archetype 3 is REFUTED rather than held: ATH pays for compute "
                 "directly to GPU providers, so no revenue-to-token conversion exists and share_to_buyback "
                 "is 0.0 as a finding. THREE CHAINS WITH DIFFERENT PURPOSES — Ethereum is canonical "
                 "(airdrop/staking/CEX), ARBITRUM is interchain and carries BOTH Checker Node and compute "
                 "rewards, Solana is a third deployment. The supply read is ARBITRUM, where the "
                 "42,000,000,000 total is confirmed on-chain; Ethereum and Solana are recorded without read "
                 "slots because the bridge model is unestablished and summing would double-count. "
                 "The Checker Node 'buyback' is an NFT repurchase paid in eATH that KEEPS EARNING ATH "
                 "through its lockup — net effect on circulating ATH is POSITIVE. ISSUANCE: Phase 1 is "
                 "declared (4.2bn over 1461 days from TGE, expiring 2028-06-11); Phase 2 (2028-06-12 to "
                 "2032-06-12, monthly, decaying) is NOT declared because the per-month figures are not on "
                 "file. DefiLlama's unlock figure tracks ATH withdrawn after vesting and EXCLUDES rewards "
                 "earned but unclaimed, so even that number understates accrued supply.",
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
        "burn_mechanism": {
            "model": "transfer_to_dead_address", "status": "confirmed",
            "source_url": "https://venice.ai/blog/programmatic-vvv-buy-and-burn",
            "source_date": "2026-09-14",
            "note": "CONFIRMED — a dead-address transfer, like Uniswap and unlike Sky. TWO PARALLEL "
                    "CHANNELS, both on-chain. (1) DISCRETIONARY, monthly, running since Nov 2025: Venice "
                    "ops funds a Safe with USDC and CoW Protocol's TWAP engine spends it hourly over ~30 "
                    "days buying VVV on Aerodrome, sending it to the burn address, ~$100k/month. "
                    "(2) PROGRAMMATIC, per-event, since 2026-04-26: each new subscription triggers a "
                    "tier-scaled buy-and-burn — Pro ~$2, Pro+ ~$5, Max ~$10 — and Venice intends to "
                    "migrate most discretionary burns into this engine over time. Second source: "
                    "https://docs.venice.ai/overview/vvv-diem",
        },
        # THE CUMULATIVE BALANCE IS NOT A BUYBACK SIGNAL, and reading it as one overstates Venice's
        # revenue-funded burn by roughly 200x. ~99.5% of it is a single March 2025 event: unclaimed
        # AIRDROP tokens, burned once, not revenue-funded and not repeatable. That is a SUPPLY
        # event. The revenue-funded programme is ~180,000 VVV since Nov 2025 — the demand signal,
        # and about 0.5% of the cumulative figure sitting next to it.
        "burn_composition": {
            "status": "contaminated",
            "one_off": {
                "what": "unclaimed airdrop tokens", "when": "2025-03", "recurring": False,
                "tokens_approx": None,      # see reconciliation below — NOT recorded as a number
            },
            "recurring": {"what": "revenue-funded buy-and-burn", "since": "2025-11",
                          "tokens_to_date_approx": 180_000, "usd_to_date_approx": 1_350_000},
            # The flow measured from our FIRST observation contains only recurring burns, because
            # the airdrop burn predates every observation we hold. So gross_burn_tokens is the clean
            # series and the cumulative is the contaminated one — the opposite of the usual case.
            "flow_is_recurring_only": True,
            "reconciliation": "THE STATED COMPONENTS DO NOT SUM TO THE STORED BALANCE, and the gap is "
                              "informative rather than sloppy. Stored 33,872,423; one-off stated ~33.87m; "
                              "recurring stated ~180,000. Those components sum to 34,050,000 — an "
                              "overshoot of 177,577, which is the recurring figure to within rounding. "
                              "Two readings. (a) '~33.87m' is a rounded statement of the TOTAL, the "
                              "one-off is really ~33,692,423, and the recurring burns ARE arriving here: "
                              "33,692,423 + 180,000 = 33,872,423 exactly. (b) 33.87m IS the one-off "
                              "alone and the recurring burns are NOT reaching this address — the Uniswap "
                              "error again, a confirmed mechanism pointed at the wrong destination. "
                              "NEITHER IS ASSUMED, and the discriminator is free: watch the flow. At "
                              "~$100k/month plus per-subscription burns the balance must visibly rise. "
                              "If it never moves, reading (b) holds and the destination is wrong.",
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
        # FAILURE MODE 4 — correct, and not comparable. ~99.5% of the cumulative is a one-off
        # March 2025 airdrop burn. The number is right; quoting it as buyback scale is not.
        "non_comparable": {
            "burn_address_balance": {
                "why": "~99.5% is a ONE-OFF March 2025 airdrop burn, not the revenue-funded programme. "
                       "Quoting it as the scale of the buyback overstates by roughly 200x.",
                "use_instead": "burn_revenue_funded",
            },
        },
        "materiality": "medium",
        "notes": "100% of emissions to stakers AND a revenue-funded buy-and-burn. Even at 3m VVV/yr, issuance runs several multiples "
                 "of what stated revenue could fund. The clearest case in the universe for showing buyback and emissions together.",
    },
    # ------------------------------------------------------------------ Archetype 3 (+4/+1)
    {
        "name": "Virtuals", "symbol": "VIRTUAL",
        "coingecko_id": "virtual-protocol",
        "defillama_fees_slug": "virtuals-protocol", "defillama_protocol": "virtuals-protocol", "defillama_chain": None,
        # ARCHETYPE 3 REMOVED 2026-09-14 — A SKY-CLASS ERROR, and the same shape exactly: a real
        # buyback-and-burn, correctly observed, attached to the WRONG TOKEN. Virtuals' mechanism
        # burns AGENT TOKENS from agent revenue. VIRTUAL ITSELF HAS NO BURN MECHANISM. The ~13m
        # VIRTUAL figure that put this project in archetype 3 is the amount ROUTED THROUGH the
        # mechanism — it bought and retired 25 agent tokens; not one VIRTUAL was destroyed.
        # So there is no burn, no buyback and no destination for VIRTUAL, and the entries are
        # deleted rather than set to zero: a zero would assert a mechanism that produced nothing,
        # when the truth is that no such mechanism exists for this token.
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "none",
        "archetypes": [2], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": None, "source_date": None,
                      "programmed": False, "status": "unconfirmed",
                      "note": "Agent launch and trading fees route to buyback — the split needs documenting. "
                              "No source URL on file yet, so the implied figure stays suppressed."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {},
        # The buyback slots are gone with the archetype: they measured agent tokens, not VIRTUAL.
        "dune_queries": _dune("emissions_tokens", "staked_tokens"),
        "materiality": "medium",
        "notes": "Agent launch and trading fees fund a buyback-and-burn OF AGENT TOKENS, not of VIRTUAL. "
                 "defillama_fees_slug is assumed to match defillama_protocol; confirm on DefiLlama, "
                 "a wrong slug shows up as a 404 in the Run Log.",
    },
    {
        "name": "Maple", "symbol": "SYRUP",
        "coingecko_id": "syrup",
        "defillama_fees_slug": "maple", "defillama_protocol": "maple", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        # TIERED, NOT FLAT — the 25% on file was STALE. MIP-019 set a flat 25% effective Q4 2025;
        # a later 2026 framework replaced it with a tier schedule on MONTHLY NET REVENUE. Two of
        # the three tiers are sourced; the third is not, and it is left None rather than guessed.
        # A single share_to_buyback cannot express a tiered rule, so the top-level share is None
        # and the schedule lives in fee_tiers. Anything derived from a share stays suppressed
        # while the top tier is unknown, which is the correct outcome: the top tier is where the
        # money is.
        "fee_split": {
            "share_to_buyback": None,
            "source_url": "https://securities.io/",
            "source_date": "2026-09-14",
            "programmed": False,
            "status": "tiered",
            "history": [
                _split_period(None, "2025-09-30", None, "unconfirmed",
                              note="Pre-MIP-019. No documented buyback share."),
                _split_period("2025-10-01", None, None, "unconfirmed",
                              known_change="MIP-019's flat 25% of net revenue (effective Q4 2025) was "
                                           "SUPERSEDED during 2026 by a TIERED framework. The exact MIP "
                                           "number and effective date have NOT been found — a search of "
                                           "community.maple.finance did not produce one, and securities.io "
                                           "(citing Maple's own materials) is the only source on file. So "
                                           "the period from Q4 2025 onward contains at least two regimes "
                                           "with no established boundary between them.",
                              note="DO NOT split this into a 25% period and a tiered period until the "
                                   "changeover date is sourced. Dating it wrongly would apply the wrong "
                                   "share across real months, and known_change keeps the whole span "
                                   "unconfirmed even if somebody later fills in a number."),
            ],
            "note": "TIERED on monthly net revenue. The flat 25% is stale. Top tier unconfirmed — see "
                    "fee_tiers. Interim source is securities.io citing Maple's own materials; the "
                    "primary MIP has not been located.",
        },
        # The tier schedule itself, recorded as data so the unconfirmed top tier is visible rather
        # than rounded away. A revenue month above $2m cannot be converted to a buyback figure at
        # all until that row has a number.
        "fee_tiers": {
            "basis": "monthly net revenue",
            "tiers": [
                {"from_usd": 0, "to_usd": 1_500_000, "share_to_buyback": 0.10, "status": "sourced"},
                {"from_usd": 1_500_000, "to_usd": 2_000_000, "share_to_buyback": 0.20, "status": "sourced"},
                {"from_usd": 2_000_000, "to_usd": None, "share_to_buyback": None, "status": "unconfirmed",
                 "note": "A FURTHER TIER EXISTS above $2m and its rate is not captured in any source on "
                         "file. Left None deliberately: extrapolating the 10 -> 20 step to 30% would be "
                         "inventing the most material number in the schedule."},
            ],
            "source_url": "https://securities.io/",
            "source_date": "2026-09-14",
            "supersedes": "MIP-019 flat 25% of net revenue, effective Q4 2025",
        },
        "burn_split": {"share_of_fees_burned": 0.0, "source_url": "https://maple.finance/", "source_date": BRIEF_DATE, "status": "active", "note": "No burn — confirmed."},
        # STAKING REWARDS ENDED. MIP-019 ended stSYRUP staking rewards in November 2025. Deposited
        # SYRUP remains WITHDRAWABLE but earns nothing further absent a new governance decision.
        # So there is no ongoing staking yield to model, and a rising stSYRUP balance is migration
        # or inertia, not accruing yield.
        "staking_sunset": {
            "what": "stSYRUP staking rewards",
            "ended": "2025-11", "source": "MIP-019",
            "still_true": "deposited SYRUP remains withdrawable; it simply earns nothing further",
            "effect": "do not model ongoing staking yield for Maple",
        },
        "issuance_schedule": None,
        "contracts": {
            "token": _contract(
                "0x643C4E15d7d62Ad0aBeC4a9BD4b001aA3Ef52d66", "ethereum", "erc20_total_supply", "SYRUP",
                "https://syrup.gitbook.io/syrup/syrup-token/syrup_token_faq",
                verified="2026-09-14", provenance="Maple's own SYRUP token FAQ; corroborated by "
                                                  "maple-labs/address-registry (syrupProxy)",
                token_standard="erc20",
                purpose="SYRUP token (MapleTokenProxy). The ticker is SYRUP, not MPL."),
            # ** ERC-4626 VAULT, NOT A RECEIPT TOKEN. ** stSYRUP is "Staked Syrup" with asset =
            # SYRUP and precision 18, per its decoded constructor, and it INCREASES IN VALUE against
            # SYRUP as rewards accrue (Maple's own docs). So totalSupply() on stSYRUP is a count of
            # SHARES, not the SYRUP staked, and the two diverge by the accrued exchange rate — a
            # figure that is wrong by a growing multiple and looks entirely plausible in a cell.
            #
            # This is the SAME CLASS OF ERROR as veAERO (totalSupply counts NFT positions) and the
            # Uniswap TokenJar (a multi-asset sink read as a single-asset fund), on a vault share
            # token instead. The correct read is the vault's assets: escrow_balance_of does exactly
            # SYRUP.balanceOf(stSYRUP), which equals totalAssets() for a vault that custodies its
            # own asset, and needs no new read method.
            "stsyrup": _contract(
                "0xc7E8b36E0766D9B04c93De68A9D47dD11f260B45", "ethereum", "ve_total_supply", "SYRUP",
                "https://syrup.gitbook.io/syrup/syrup-token/syrup_token_faq",
                verified="2026-09-14", provenance="Maple's own SYRUP token FAQ; corroborated by "
                                                  "maple-labs/address-registry (syrupStSyrup)",
                read_method="escrow_balance_of", token_standard="erc20", underlying="token",
                purpose="stSYRUP — the lock-rate input. Read as SYRUP.balanceOf(stSYRUP), NOT "
                        "stSYRUP.totalSupply().",
                note="ERC-4626. Decoded constructor: name 'Staked Syrup', asset = the SYRUP address, "
                     "precision 18. Share price RISES against SYRUP as rewards accrue, so totalSupply() "
                     "is shares outstanding and understates SYRUP staked by the accrued rate. "
                     "escrow_balance_of reads the vault's SYRUP holdings, which is totalAssets() for a "
                     "vault custodying its own asset. Do not 'simplify' this to erc20_total_supply."),
            # THE DESTINATION. The SSF HAS NO SEPARATE ADDRESS — Maple's own transparency page states
            # the Syrup Strategic Fund is "part of the Treasury". Searching for a standalone SSF
            # wallet is closed, not pending.
            # ============ ROLE DISPUTED 2026-09-14 — THE ADDRESS IS REAL, THE CLAIM WAS MINE ============
            # The live read returned 0.51253570332391 SYRUP against ~75,780,000 reported by Maple's
            # own transparency page. THE READ IS CORRECT AND THE ADDRESS IS WRONG FOR THIS QUESTION.
            #
            # Decimals are ruled out arithmetically, not by inspection: 75,780,000 / 0.51253570332391
            # = 1.4785e8, and log10 of that is 8.1698 — NOT AN INTEGER. A decimals mismatch is always
            # an exact power of ten. It is also ruled out structurally: ChainReader.scaled() reads
            # decimals() live from the same token contract the balance came from, with no hardcoded
            # 18 and no fallback, and the identical path returned correct figures for Sky, Uniswap
            # and Pendle in the same run.
            #
            # And the token is right: the symbol gate passed, so the chain confirmed this really is
            # SYRUP. 0.51 SYRUP is therefore the GENUINE balance of this address.
            #
            # WHAT WENT WRONG WAS THE INFERENCE. Maple's transparency page says the SSF is "part of
            # the Treasury". I read "the Treasury" as a contract and took the one entry in
            # maple-labs/address-registry literally named `treasury` — which sits under SINGLETONS,
            # beside globals, feeManager and poolDeployer. That is the PROTOCOL FEE treasury, and
            # Maple's protocol fees are denominated in the POOL ASSET (USDC/USDT/USDG), not SYRUP.
            # It is NOT in the registry's own "SyrupToken" section. A dust SYRUP balance there is
            # exactly what that contract should hold. "The Treasury" on a transparency page means
            # the DAO's holdings in the ACCOUNTING sense, which need not be one address at all.
            #
            # THE SAME ERROR AS THE UNISWAP TOKENJAR, and more dangerous: a name matched, the read
            # worked, and the answer was small, precise and non-zero. A zero would have looked
            # broken. 0.51 would have flowed into every Maple archetype 3 figure untouched.
            #
            # destination_status "disputed" is the mechanism built for exactly this: the adapter
            # READS it, stages the 0.51 as evidence, and REFUSES to store it as a metric, with a gap
            # saying why. Better a gap than a wrong number. Do NOT substitute another address until
            # one is sourced — see OPEN_QUESTIONS.
            # ==========================================================================================
            "treasury": _contract(
                "0xa9466EaBd096449d650D5AEB0dD3dA6F52FD0B19", "ethereum", "treasury_holding", "SYRUP",
                "https://maple.finance/transparency",
                verified="2026-09-14", provenance="maple-labs/address-registry, MapleAddressRegistryETH.sol, "
                                                  "SINGLETONS section — the protocol fee treasury",
                holder_has_code=True, token_standard="erc20", underlying="token",
                destination_status="disputed",
                destination_note="Read correctly at 0.51253570332391 SYRUP on 2026-09-14 against ~75.78m "
                                 "reported by Maple. This is the v2 PROTOCOL FEE treasury, which "
                                 "receives fees in pool assets (USDC/USDT/USDG), not the wallet holding "
                                 "repurchased SYRUP. Evidence, not a figure.",
                purpose="Maple v2 protocol fee treasury. RECORDED AS EVIDENCE ONLY — its role as the "
                        "destination of repurchased SYRUP is DISPUTED and no metric is stored from it.",
                note="Do not delete: the address is real and its near-zero SYRUP balance is itself the "
                     "evidence that repurchased SYRUP is held somewhere else. Do not substitute another "
                     "address on a name match — that is how this happened."),
            # REFERENCE ONLY, no read slot: syrupDrip 0x509712F368255E92410893Ba2E488f40f7E986EA
            # (maple-labs/address-registry). It is the emissions distributor; with staking rewards
            # ended in Nov 2025 there is no ongoing stream to read from it, and giving it a
            # balance kind would sum it into the treasury figure.
        },
        # THE GUARD FOR THE FIX. While destination_status is "disputed" nothing is stored, so this
        # floor does nothing today. It exists for the day somebody clears the dispute or points the
        # entry at a new address: Maple reports the SSF holding in the tens of millions, so anything
        # under a million SYRUP means the address is wrong AGAIN and must be rejected to the Review
        # Queue rather than stored. The Uniswap 100m burn floor exists for the same reason and
        # caught the same class of error.
        "sanity": {
            "treasury_holding_tokens": {"min": 1_000_000, "max": 1_000_000_000},
        },
        "metric_labels": {
            "treasury_holding_tokens": "SYRUP held by the disputed Maple fee treasury (NOT the SSF)",
        },
        "buyback_destination": "hold", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "treasury_redeployable",
        "destination_source_url": "https://maple.finance/transparency",
        "destination_confirmed_date": "2026-09-14",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens"),
        # DESTINATION INDETERMINATE — a sixth type, and it is neither of the two it resembles.
        # The purchased SYRUP goes to the SYRUP Strategic Fund, whose stated uses include working
        # capital, TOKEN LIQUIDITY, capital reserves and further buybacks. "Token liquidity" means
        # bought tokens CAN RETURN TO FLOAT. So it must NOT be netted against emissions like a
        # burn, and must NOT be counted as locked supply like Chainlink's Reserve. Those are
        # opposite signs, and the truth is neither.
        # NOT RESOLVED — AND A PREVIOUS NOTE HERE CLAIMING IT WAS IS WITHDRAWN. That note said the
        # fund sits inside the Treasury "which is now read directly, so the BALANCE is measurable
        # even though its MEANING stays indeterminate". The balance is NOT measurable: the address
        # read holds 0.51 SYRUP. Both the meaning AND the location are open.
        "destination_indeterminate": {
            "fund": "SYRUP Strategic Fund (an accounting label inside the Maple Treasury, not a separate wallet)",
            "stated_uses": ["working capital", "token liquidity", "capital reserves", "further buybacks"],
            "why_indeterminate": "'token liquidity' allows repurchased tokens to return to float",
            "address": None,
            "why_no_address": "NOT RESOLVED, and an earlier claim that it was has been RETRACTED. The "
                              "transparency page's 'part of the Treasury' was read as naming a contract; "
                              "the address taken from it holds 0.51 SYRUP and is the v2 protocol FEE "
                              "treasury. Where repurchased SYRUP actually sits is unsourced.",
            # LABELLED AS A JUDGEMENT, not recorded as a finding: it is an analyst's framing and a
            # reader is entitled to disagree with it.
            "analyst_judgement": "Most buybacks do one of three things: distribute profits, retire "
                                 "supply, or reduce float. Maple's does none of them reliably. That is "
                                 "a view, not an observation.",
            "confirm": "the ticker is SYRUP, not MPL — verify before any figure is quoted",
        },
        "cross_checks": [
            {"primary": "treasury_holding_tokens", "primary_source": "tier 2 contract read",
             "secondary": "buyback_fund_balance_dashboard", "secondary_source": "https://maple.finance/transparency",
             "tolerance": 0.05, "prefer": "primary",
             "note": "NOT ARMED, AND THIS IS WHY THE 0.51 REACHED THE SHEET. Maple publishes live SSF "
                     "SYRUP holdings on its transparency page and this entry names it as the second "
                     "opinion — but the sources.yaml entry is enabled:false, so the secondary never "
                     "arrives and check_cross_checks skips silently when either side is missing. A "
                     "cross-check whose secondary is disabled is documentation, not a guard: had it "
                     "been armed, 0.51 against ~75.78m would have tripped it on the first run. "
                     "Complete the sources.yaml selector to arm it. Tolerance stays wide because the "
                     "page reports the SSF label and the contract read is one wallet."},
        ],
        "materiality": "medium",
        "notes": "Archetype 3. No burn — confirmed. SPLIT IS TIERED, not the stale flat 25%: 10% below "
                 "$1.5m monthly net revenue, 20% from $1.5m-$2m, and a further tier above $2m whose rate "
                 "is NOT sourced and is left None. stSYRUP is an ERC-4626 VAULT — read "
                 "SYRUP.balanceOf(stSYRUP), never stSYRUP.totalSupply(), which counts shares whose price "
                 "rises with accrued rewards. "
                 "THE DESTINATION ADDRESS IS NOT ESTABLISHED, and an earlier claim that it was is "
                 "RETRACTED: 0xa9466EaBd096449d650D5AEB0dD3dA6F52FD0B19 is the v2 PROTOCOL FEE treasury "
                 "(registry SINGLETONS section, fees paid in pool assets) and holds 0.51 SYRUP against "
                 "the ~75.78m Maple reports. Its role is marked DISPUTED, so it is read as evidence and "
                 "no treasury figure is stored. Do NOT derive any archetype 3 destination figure until "
                 "an address is sourced. stSYRUP staking rewards ENDED November 2025 (MIP-019) — do not "
                 "model ongoing staking yield.",
    },
    {
        "name": "Morpho", "symbol": "MORPHO",
        "coingecko_id": "morpho",
        "defillama_fees_slug": "morpho", "defillama_protocol": "morpho", "defillama_chain": None,
        # ARCHETYPE 2 ONLY. The fee switch is OFF, so there is no revenue reaching the token and
        # nothing for an archetype 3 block to measure. archetypes_held is EMPTY, not [3]: holding
        # the archetype implies it is pending evidence, and the evidence is already in — the switch
        # is off, and it is off for legal/tax structuring reasons, not because Morpho decided
        # against paying holders. See reopen_condition.
        "archetypes": [2], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 0.0, "source_url": "https://docs.morpho.org/", "source_date": "2026-09-14",
                      "programmed": True, "status": "off",
                      "note": "FEE SWITCH IS OFF. 0.0 is the correct share, not a missing one — no protocol "
                              "revenue reaches MORPHO holders today. See reopen_condition for what would "
                              "change it."},
        "burn_split": None,
        "issuance_schedule": None,
        # ONE SUPPLY READ, and the choice between the two MORPHO tokens matters.
        # The NEW (wrapped) token is the transferable one: 1,000,000,000 minted to the Wrapper at
        # initialization, with legacy holders migrating 1:1. Reading BOTH would double-count the
        # same billion tokens under two contracts, which is why legacy has no read slot.
        "contracts": {
            "token": _contract(
                "0x58D97B57BB95320F9a05dC918Aef65434969c2B2", "ethereum", "erc20_total_supply", "MORPHO",
                "https://docs.morpho.org/developers/contracts/addresses",
                verified="2026-09-14", provenance="Morpho's own contract addresses page",
                token_standard="erc20",
                purpose="MORPHO (new, TRANSFERABLE) — THE supply read. 1,000,000,000 minted to the Wrapper "
                        "at initialization; legacy holders migrate 1:1."),
            # NOT SUMMED, AND NOT A GAP. Three addresses recorded without read slots:
            #
            #   WRAPPER  0x9D03bb2092270648d7480049d0E58d2FcF0E5123
            #     Holds the minted supply pending migration. Its balance is a SUBSET of the new
            #     token's totalSupply, so reading it as a second supply component would double-count.
            #
            #   LEGACY MORPHO  0x9994E35Db50125E0DF82e4c2dde62496CE330999
            #     NON-TRANSFERABLE and immutable. Its holders are migrating 1:1 into the new token,
            #     so its supply and the new token's supply describe the SAME billion tokens. Summing
            #     them would report two billion.
            #
            #   BASE DEPLOYMENT  0xBAa5CC21fd487B8Fcc2F632f3F4E8D37262a0842
            #     Recorded for completeness. Not summed: the bridge model is not established here
            #     either, and the same lock-and-mint double-count risk applies as for WMTx and GEOD.
            #
            # All three from docs.morpho.org/developers/contracts/addresses, 2026-09-14.
        },
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "none",
        # WHY THE SWITCH IS OFF, recorded because the reason changes what it would take to turn it
        # on. This is NOT "Morpho decided holders should not be paid" — it is unresolved legal and
        # tax structuring, per Morpho's own February 2025 governance proposal. The evidence that the
        # revenue exists: a Berachain licensing fee was routed to the Morpho Association (a French
        # nonprofit) rather than to the DAO treasury, explicitly because direct DAO fee receipt is
        # not legally resolved. Revenue is real and is landing somewhere that is not the token.
        "reopen_condition": {
            "what": "fee switch activation",
            "blocked_on": "legal/tax structuring of DAO fee receipt, per Morpho's own Feb 2025 governance proposal",
            "not_blocked_on": "a value judgement about paying holders",
            "evidence_revenue_exists": "a Berachain licensing fee was routed to the Morpho Association, a "
                                       "French nonprofit, rather than the DAO treasury — explicitly because "
                                       "direct DAO fee receipt is not legally resolved yet",
            "then": "add archetype 3 with a real destination; until then there is nothing to accrue value to",
            "source_url": "https://docs.morpho.org/",
        },
        "dune_queries": _dune("emissions_tokens"),
        "materiality": "high",
        "notes": "ARCHETYPE 2 ONLY — the fee switch is OFF and share_to_buyback is 0.0, which is a fact, not "
                 "a gap. Do not add archetype 3 until the switch flips. Supply is read from the NEW "
                 "(wrapped, transferable) MORPHO at 0x58D97B57BB95320F9a05dC918Aef65434969c2B2; the "
                 "Wrapper, the non-transferable legacy token and the Base deployment are recorded in the "
                 "contracts comment WITHOUT read slots, because each would double-count the same billion "
                 "tokens. The switch is blocked on legal/tax structuring, not on a decision about holders.",
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
        # DECLARED 2026-09-14, AND IT SHOULD HAVE BEEN DECLARED LONG AGO.
        # The destination was resolved in December 2025 and destination_confirmed_date has said so
        # since. But destination_confirmed_date and burn_mechanism.status are DIFFERENT FIELDS
        # answering different questions, and the confidence band reads the second one — so a fact
        # confirmed by two independent primary sources was rendering AMBER as "the burn MECHANISM
        # is assumed, not documented". It was never assumed. It was never written down.
        #
        # MODEL IS transfer_to_dead_address, AND THE CHOICE CHANGES A NUMBER. HYPE is BOUGHT on the
        # market with protocol fees and TRANSFERRED to the Assistance Fund at 0xfefe...fEfe, an
        # address that has never had a private key. Total supply does NOT fall — the tokens still
        # exist, at an address nobody can spend from. So issuance is the supply delta ALONE
        # (ISSUANCE_FROM_SUPPLY_DELTA maps transfer_to_dead_address -> delta_only). Declaring
        # protocol_level_destruction instead would add the burn back on top of the delta and
        # OVERSTATE issuance by the entire cumulative burn — 48.42m HYPE as of 6 Sep 2026.
        "burn_mechanism": {
            "model": "transfer_to_dead_address", "status": "confirmed",
            "source_url": "https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/assistance-fund",
            "source_date": "2025-12-27",
            "source_note": "TWO INDEPENDENT PRIMARY SOURCES. (1) The validator vote of 27 December 2025, "
                           "85% of staked weight in favour, formally recognising all Assistance Fund "
                           "HYPE — past and future — as permanently burned. (2) An SEC-filed exhibit "
                           "from Hyperliquid Strategies Inc dated 7 May 2026, corroborating it and filed "
                           "independently of Hyperliquid itself. Independent corroboration by a filing "
                           "made under a different legal obligation is stronger evidence than a second "
                           "protocol-authored page, which would only be the same claim reaching us twice.",
            "note": "The Assistance Fund address has never had a private key, so nothing can leave it. "
                    "The BALANCE is therefore the cumulative burn, read from Hyperliquid's own info "
                    "endpoint rather than a chain RPC — see burn_read_method 'protocol_api'. "
                    "NOT protocol_level_destruction: total supply does not fall, so issuance must be "
                    "the supply delta alone.",
        },
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
            # THE EXECUTING CONTRACT IS NOT THE DESTINATION. This entry used to be kind
            # "burn_address_balance", which read the balance of the contract you CALL rather than
            # the address the UNI ends up at — so it returned 0 while 100k+ UNI a day was
            # demonstrably being burned. The zero was a BUG, not a finding.
            #
            # Recording the wrong inference too, because it was nearly believed: the zero was read
            # as "plausible, since Uniswap's burn is holder-elected and perhaps nobody elected".
            # That reasoning was WRONG. Burns are publicly reported — 134,000 UNI on 2026-06-05, a
            # record; 106,000 UNI on an ordinary day in July 2026 — on top of the December 2025
            # retroactive burn of 100,000,000 UNI. Holder-election explains a SMALL figure; it can
            # never explain a zero against reported burns. "A plausible story for a suspicious
            # number" is how a wrong address survives.
            "fire_pit": _contract("0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721", "ethereum", "burn_executor", "UNI",
                                  UNISWAP_FEE_DEPLOYMENTS, verified="2026-09-11", provenance="protocol docs",
                                  holder_has_code=True,   # the Releaser is a real contract, unlike a dead address
                                  purpose="Releaser (Firepit), mainnet — the contract release() is CALLED ON. "
                                          "Reference only: no metric is read from it. Claiming TokenJar fees "
                                          "requires burning an equivalent value of UNI through here, and the UNI "
                                          "goes to the dead address, not into this contract."),
            # THE ACTUAL DESTINATION.
            "burn_dead": _contract(BURN_ADDRESSES["dead"], "ethereum", "burn_address_balance", "UNI",
                                   "https://vote.uniswapfoundation.org/proposals/93",
                                   verified="2026-09-14", provenance="protocol governance",
                                   purpose="TRANSFER BURN destination — burned UNI is sent here and permanently "
                                           "removed from circulation, per the UNIfication proposal. THIS IS THE "
                                           "WHOLE FIGURE, not a mainnet slice: OP Stack L2 burns (Unichain and "
                                           "the rest) bridge to L1 and land HERE after the challenge period, so "
                                           "no supply_is_partial flag is set and nothing is understated."),
            "v3_fee_adapter": _contract("0x5E74C9f42EEd283bFf3744fBD1889d398d40867d", "ethereum", "buyback_fund_balance", "UNI",
                                        UNISWAP_FEE_DEPLOYMENTS, verified="2026-09-11", provenance="protocol docs",
                                        purpose="V3FeeAdapter, mainnet."),
            # ================= MAINNET ONLY, BY EXPLICIT DECISION =================
            # THE UNICHAIN ENTRIES WERE REMOVED 2026-09-14. They are recorded here so nobody
            # re-adds them casually:
            #
            #   TokenJar, Unichain                      0xD576BDF6b560079a4c204f7644e556DbB19140b5
            #   Releaser (OptimismBridgedResourceFirepit) 0xe0A780E9105aC10Ee304448224Eb4A2b11A77eeB
            #
            # WHY, from Uniswap's own contracts rather than inference. src/releasers/
            # OptimismBridgedResourceFirepit.sol implements a TWO-STAGE burn: the searcher's bridged
            # UNI is burned on Unichain immediately via the L2StandardBridge, a cross-domain message
            # is queued, and only AFTER THE OP STACK 7-DAY CHALLENGE PERIOD is the L1 UNI transferred
            # to 0xdead. A Unichain burn and its mainnet 0xdead arrival are THE SAME BURN, a week
            # apart. Reading both would count every Unichain burn twice, once on each side of the
            # bridge, with a seven-day offset that makes the duplicate look like fresh activity.
            #
            # The mainnet burn_dead balance below already captures Unichain burns — that is what the
            # bridge withdrawal delivers into it. Adding Unichain does not complete the picture; it
            # double-counts it.
            # ======================================================================
        },
        "burn_mechanism": {
            "model": "transfer_to_dead_address", "status": "confirmed",
            "source_url": "https://vote.uniswapfoundation.org/proposals/93",
            "source_date": "2026-09-14",
            "note": "CONFIRMED. Fees accumulate in TokenJar contracts; claiming them requires burning an "
                    "equivalent value of UNI through Firepit.release(), and the burned UNI is sent to "
                    "Ethereum's 0x...dEaD address, permanently removing it from circulation. The December "
                    "2025 retroactive burn sent 100,000,000 UNI to a dead address. Mechanism documented in "
                    "the UNIfication proposal (vote.uniswapfoundation.org/proposals/93) and in "
                    "docs.uniswap.org/contracts/protocol-fee/guides/best-practices, which describes "
                    "release() and its nonce mechanism. UNLIKE SKY, the dead-address model is right here — "
                    "what was wrong was WHICH ADDRESS we read: the executing contract rather than the "
                    "destination. See the contracts block. "
                    "NOW CONFIRMED IN THE SOURCE ITSELF, not only in the proposal: Firepit.sol is an "
                    "ExchangeReleaser constructed with recipient address(0xdead), and "
                    "ExchangeReleaser.release() executes "
                    "`RESOURCE.safeTransferFrom(msg.sender, RESOURCE_RECIPIENT, threshold)` — the "
                    "caller's UNI goes STRAIGHT to 0xdead and never sits in the Firepit. That is why "
                    "fire_pit is burn_executor (reference only) and burn_dead is the destination.",
        },
        # A burn of this size cannot come back near zero. The December 2025 retroactive burn alone
        # was 100,000,000 UNI, before ~4-5m/yr of ongoing burns, and burns are publicly reported at
        # 100k+ UNI on ordinary days. So a cumulative under 100m means the read is pointing at the
        # wrong thing AGAIN, and the figure is REJECTED to the Review Queue rather than stored —
        # which is exactly what the old fire_pit read would have produced.
        "sanity": {
            "burn_address_balance": {"min": 100_000_000, "max": 1_000_000_000},
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
                "note": "The UNI-burn threshold that must be met before release() can be called. "
                        "CONFIRMED MUTABLE FROM SOURCE: src/base/ResourceManager.sol declares "
                        "`uint256 public threshold;` with `setThreshold()` gated on onlyThresholdSetter — "
                        "it is governance STORAGE, not a constant, so there is no fixed value to record. "
                        "THE ONLY NUMBER PUBLISHED ANYWHERE IS 2000 UNI, and that figure appears in "
                        "Uniswap/protocol-fees README.md under 'Cross-Chain UNI Burn (OP Stack L2s)' — it "
                        "is the BRIDGED-FIREPIT configuration, NOT mainnet's. Do not hardcode it here. "
                        "Getting mainnet's value requires a live threshold() read on "
                        "0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721. Leaving value None keeps any figure "
                        "derived from it suppressed.",
            },
        },
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "holder_elected",
        "destination_effect": "removed_from_supply",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens"),
        # FAILURE MODE 4 — the same shape as Venice, from the other direction. The December 2025
        # retroactive burn was 100,000,000 UNI in one event; ongoing revenue-driven burns run at
        # 100k-134k UNI a day. Both sit in the same cumulative column, and the one-off dominates
        # it, so the column cannot be read as the run-rate of the ongoing programme.
        # RELABELLED, not redefined. buyback_fund_balance means "the project's own token held by
        # the buyback contract" everywhere else, and that definition is what makes the column
        # comparable. For Uniswap the honest reading is narrower: the TokenJar accumulates FEE
        # TOKENS — whatever the pools earned — so its UNI balance is legitimately near zero
        # almost always, and is not the fund's value. Summing the actual holdings would need a
        # price feed per fee token and would make this one cell non-comparable with the rest of
        # its column, so the label is corrected instead and the fuller version left as deliberate
        # future work.
        # SOURCE-CONFIRMED, not inferred. TokenJar.sol's release() iterates `Currency[] assets` and
        # sweeps `asset.balanceOfSelf()` for each — it is a MULTI-ASSET sink by construction, so its
        # UNI balance is one asset among many and is not the fund's value.
        "metric_labels": {
            "buyback_fund_balance": "UNI balance of TokenJar (not the fund total)",
        },
        "non_comparable": {
            "buyback_fund_balance": {
                "why": "the TokenJar accumulates FEE TOKENS (USDC, WETH, whatever the pools earned), "
                       "not UNI. This reads its UNI balance, which is legitimately near zero and is "
                       "NOT the value of the fund. CONFIRMED FROM SOURCE: TokenJar.sol release() "
                       "iterates Currency[] assets and sweeps balanceOfSelf() on each.",
                "use_instead": "nothing yet — no metric holds the fund's multi-asset total. Summing it "
                               "needs a price feed per fee token; until then treat this as a floor, "
                               "not a total",
            },
            "burn_address_balance": {
                "why": "dominated by the 100,000,000 UNI RETROACTIVE treasury burn of December 2025. "
                       "That is a one-off supply event, not the ongoing revenue-driven programme "
                       "(which runs at roughly 100k-134k UNI/day).",
                "use_instead": "gross_burn_tokens (the flow, which is the ongoing programme)",
            },
        },
        "materiality": "high",
        "notes": "Archetype 4 only — no distribution leg, no staking yield. Implied and actual burn diverge for "
                 "reasons unrelated to revenue, because the burn is holder-elected. "
                 "MAINNET ONLY, BY EXPLICIT DECISION: the Unichain TokenJar and Releaser were REMOVED "
                 "2026-09-14. OptimismBridgedResourceFirepit.sol burns bridged UNI on L2 and then bridges to "
                 "L1, where the UNI lands at 0xdead only after the 7-day OP Stack challenge period — so a "
                 "Unichain burn and its mainnet arrival are the SAME burn a week apart, and reading both "
                 "double-counts every one of them. burn_dead is therefore the WHOLE figure, not a slice. "
                 "The release() threshold is governance STORAGE (ResourceManager.setThreshold), not a "
                 "constant; the published 2000 UNI figure is the OP-Stack bridged configuration, not "
                 "mainnet's.",
    },
    {
        "name": "Aerodrome", "symbol": "AERO",
        "coingecko_id": "aerodrome-finance",
        "defillama_fees_slug": "aerodrome", "defillama_protocol": "aerodrome", "defillama_chain": None,
        # ARCHETYPE 3 ONLY, AND ARCHETYPE 4 IS REFUTED RATHER THAN MERELY ABSENT.
        # Aerodrome's own SPECIFICATION.md describes the complete supply mechanics — Minter,
        # emissions, veAERO locking, gauges, rebases — and contains NO BURN OF ANY KIND. There is
        # no burn contract, no dead address, and no market buyback of AERO itself. This is a
        # positive finding from the primary source, not an empty slot waiting to be filled.
        "archetypes": [3], "archetypes_held": [],
        # NOT A BUYBACK PATTERN AT ALL, and share_to_buyback=1.0 must not be read as one. 100% of
        # swap fees route through gauges to the veAERO holders WHO VOTED FOR THAT SPECIFIC POOL,
        # and they are paid IN THE SWAP PAIR'S OWN TOKENS — never converted to AERO. So there is no
        # buy-then-distribute step anywhere: nobody ever buys AERO with the fees. Structurally this
        # is the opposite of Aave's or Hyperliquid's mechanism, and modelling it as buy-pressure
        # would invent demand for AERO that the design does not create.
        "fee_split": {"share_to_buyback": 1.0, "source_url": "https://github.com/aerodrome-finance/contracts",
                      "source_date": "2026-09-14", "programmed": True, "status": "active",
                      "destination_model": "distribute_to_voters",
                      "note": "100% of trading fees to veAERO voters, per-pool, PAID IN THE PAIR'S OWN TOKENS "
                              "and never converted to AERO. There is NO buy-then-distribute step — do not "
                              "model this as buy pressure on AERO. DISTRIBUTE, not buyback."},
        "burn_split": {"share_of_fees_burned": 0.0,
                       "source_url": "https://github.com/aerodrome-finance/contracts",
                       "source_date": "2026-09-14", "status": "n/a",
                       "note": "ZERO, AND CONFIRMED FROM THE PRIMARY SOURCE: Aerodrome's own "
                               "SPECIFICATION.md documents the full supply mechanics and NO BURN EXISTS "
                               "anywhere in the design. 0.0 is a finding, not a missing value."},
        "burn_mechanism": {
            "model": "no_burn", "status": "confirmed",
            "source_url": "https://github.com/aerodrome-finance/contracts",
            "source_date": "2026-09-14",
            "note": "CONFIRMED NO BURN. SPECIFICATION.md covers Minter, Voter, Gauge, VotingEscrow, "
                    "RewardsDistributor, EpochGovernor and the fee routing, and describes no burn "
                    "mechanism of any kind. Supply mechanics are emissions (inflationary) and locking "
                    "(veAERO). Do not add archetype 4.",
        },
        # ============ TWO EMISSION STREAMS, MODELLED AS TWO LINES ============
        # They answer different questions and netting them into one hides both.
        #
        #   (a) POOL EMISSIONS — 15,000,000 AERO per epoch at start, DECAYING 1% PER EPOCH. These
        #       go through the Voter to gauges and are genuine new supply to liquidity providers.
        #
        #   (b) veAERO REBASE — a SEPARATE weekly stream paid ONLY to veAERO holders, whose entire
        #       purpose is to offset the dilution that (a) causes them. Calculated on locked and
        #       unlocked AERO one second before the epoch flip. It is still new supply, but it is
        #       an anti-dilution transfer to lockers, not a payment for liquidity.
        #
        # TAIL-EMISSION MODE ("Aero Fed") activates once (a) falls below 6,000,000 AERO/epoch —
        # ~92 EPOCHS BY DESIGN. The spec gives no date for this and the earlier "epoch 67" figure
        # was never confirmed anywhere; it is DROPPED, not reconciled. In tail mode, weekly
        # emissions become a PERCENTAGE OF CIRCULATING SUPPLY starting at 30 bps (0.003),
        # adjustable by ±1 bp per epoch on an EpochGovernor veNFT plurality vote with no quorum and
        # no proposal threshold.
        #
        # NOT DECLARED AS A tokens_per_day SCHEDULE. Both the decay and the tail mode are
        # EPOCH-INDEXED, not date-indexed, and tail mode is a percentage of a moving supply that
        # governance moves again every epoch. A fixed daily rate cannot express any of that, and
        # the epoch-to-date mapping is not on file. Declared as a rule instead.
        # =====================================================================
        "issuance_schedule": None,
        "emission_streams": [
            {"stream": "pool emissions", "start_per_epoch": 15_000_000, "decay_per_epoch": 0.01,
             "paid_to": "gauges, by veAERO vote", "is_new_supply": True,
             "tail_trigger_per_epoch": 6_000_000, "tail_trigger_epoch_approx": 92,
             "tail_rule": "weekly emissions become a percentage of circulating supply, starting at "
                          "30 bps (0.003), adjustable +/-1 bp per epoch by EpochGovernor plurality vote "
                          "(no quorum, no proposal threshold)",
             "source_url": "https://github.com/aerodrome-finance/contracts",
             "source_file": "SPECIFICATION.md:119-130, 251-266",
             "note": "EPOCH-INDEXED, not date-indexed, and the epoch-to-date mapping is not on file. The "
                     "'~92 epochs' figure is the spec's own and is UNDATED. The previously circulating "
                     "'epoch 67' figure was never confirmed and is dropped rather than reconciled."},
            {"stream": "veAERO rebase", "start_per_epoch": None, "decay_per_epoch": None,
             "paid_to": "veAERO holders only", "is_new_supply": True,
             "purpose": "offsets the dilution that pool emissions cause lockers",
             "calculated_on": "locked and unlocked AERO one second prior to epoch flip",
             "source_url": "https://github.com/aerodrome-finance/contracts",
             "source_file": "SPECIFICATION.md:132-138",
             "note": "A SEPARATE LINE, never netted into pool emissions. Its size is not a declared "
                     "constant — it is computed per epoch from the lock ratio — so no rate is recorded."},
        ],
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
        # NO locked_tokens_dashboard ENTRY, AND NO CROSS-CHECK. Query 2986047 was the only candidate
        # source for the second opinion on veAERO and it is unusable — see UNAVAILABLE below for
        # what was tried. A query_id left in place here would fail on every run forever; a
        # cross_checks entry naming a secondary that can never arrive would report a permanent
        # divergence-unavailable. Both are removed rather than left to generate noise.
        "dune_queries": _dune("avg_lock_duration_days", "emissions_tokens", "actual_buyback_usd",
                              "actual_buyback_tokens"),
        "materiality": "high",
        # THE MERGER HAS NOT SHIPPED. Checked against the primary repository 2026-09-14: no mention
        # of Velodrome, a merge, Ethereum mainnet or Arc in README.md, SPECIFICATION.md,
        # script/README.md or the slipstream README, and script/constants/ contains only Base.json
        # — no Ethereum.json, Mainnet.json or Arc.json. Recorded as a NEGATIVE FINDING so it is not
        # re-checked casually and so no cross-chain or merged structure is modelled on reporting
        # alone.
        "reported_not_shipped": {
            "what": "Dromos Labs merger of Aerodrome and Velodrome into a unified protocol, targeted "
                    "Q2 2026, expanding to Ethereum mainnet and Circle's Arc chain",
            "checked": "aerodrome-finance/contracts README.md, SPECIFICATION.md, script/README.md, "
                       "script/constants/ (only Base.json), and aerodrome-finance/slipstream README.md",
            "checked_on": "2026-09-14",
            "finding": "no reference to Velodrome, a merge, Ethereum or Arc anywhere",
            "effect": "do not model any cross-chain or merged structure; Base remains the only chain",
        },
        "notes": "ARCHETYPE 3 ONLY. NO BURN EXISTS — confirmed from Aerodrome's own SPECIFICATION.md, which "
                 "documents the complete supply mechanics and contains no burn of any kind. Archetype 4 must "
                 "not be added. FEES ARE NOT A BUYBACK: 100% of swap fees go to the veAERO holders who voted "
                 "for each specific pool, paid in the PAIR'S OWN TOKENS and never converted to AERO, so there "
                 "is no buy-then-distribute step and no buy pressure on AERO. TWO emission streams, modelled "
                 "as two lines: pool emissions (15m AERO/epoch decaying 1%/epoch, entering tail mode below "
                 "6m/epoch at ~epoch 92, then 30 bps of circulating supply +/-1 bp per epoch by EpochGovernor "
                 "vote) and the separate veAERO anti-dilution rebase. Both are epoch-indexed, so neither is "
                 "declared as a tokens_per_day schedule. veAERO lock rate is read as AERO.balanceOf(escrow). "
                 "The reported Velodrome merger has NOT shipped into the public contracts.",
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
        "burn_mechanism": {
            "model": "transfer_to_dead_address", "status": "assumed",
            "source_url": "https://docs.pancakeswap.finance/protocol/cake-tokenomics",
            "source_date": None,
            "note": "ASSUMED. The tokenomics page is on file as the address source and is the likeliest "
                    "place the mechanism is also described — but nobody has confirmed that it says CAKE is "
                    "transferred to the dead address rather than destroyed some other way. The monthly CAKE "
                    "Burn Report series would corroborate it. Marked assumed rather than confirmed because "
                    "nothing in this repo records anyone having read it for that purpose.",
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
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {
            "share_to_buyback": 0.55,
            # SOURCE: Messari, citing the Sky governance Executive Proposal approved 2026-08-13
            # directly. The proposal directs 55% of each Smart Burn Engine cycle to SKY buybacks and
            # 45% to LSSKY stakers. The primary governance forum URL is still not captured — Messari
            # is a secondary source that quotes the primary, which is better than nothing and worse
            # than the vote itself, so it is recorded as such rather than promoted.
            "source_url": "https://messari.io/",
            "source_date": "2026-08-13",
            "programmed": False,   # explicitly governance-set and revisable
            "status": "active",
            "destination_split": 0.55,
            "history": [
                # TWO periods before the Executive Proposal, not one. The April 2026 treasury
                # overhaul cut buybacks by a reported ~87% to rebuild stablecoin reserves,
                # explicitly prioritising a $150m solvency buffer over buybacks and staking
                # rewards. Lumping the regimes either side of that into a single undocumented
                # period is safe ONLY while it stays undocumented: the moment somebody fills in
                # one pre-August number, it would be applied across an ~87% cut and the
                # suppression would lift on a figure that looks entirely reasonable.
                _split_period(None, "2026-04-30", None, "unconfirmed",
                              note="Pre-overhaul regime. NOT documented — the Smart Burn Engine ran at a "
                                   "reported order of $1m/day before April 2026. Do not assume 0.55 "
                                   "applied. THE 2026-04-30 BOUNDARY IS A PLACEHOLDER, not a known event "
                                   "date: the overhaul is reported as April 2026 with no day established. "
                                   "Both periods either side are unconfirmed, so no figure depends on "
                                   "where the boundary sits — but it must be corrected before either is."),
                _split_period("2026-05-01", "2026-08-12", None, "unconfirmed",
                              known_change="April 2026 treasury overhaul cut buybacks by a reported ~87% "
                                           "to rebuild stablecoin reserves. Neither the exact date nor the "
                                           "resulting split is documented.",
                              note="Post-overhaul, pre-Executive-Proposal regime. Materially different from "
                                   "the period before it and from the 55/45 after it, so it is its own "
                                   "period. known_change keeps it unconfirmed even if a share is later "
                                   "filled in, until the change itself is documented."),
                _split_period("2026-08-13", None, 0.55, "active", source_url="https://messari.io/",
                              source_date="2026-08-13", destination_split=0.55,
                              note="Executive Proposal approved 2026-08-13: 55% of each Smart Burn Engine "
                                   "cycle to SKY buybacks, 45% to LSSKY stakers. Messari cites the Executive "
                                   "Proposal directly; the primary forum URL is still not captured. THE "
                                   "PRE-2026-08-13 SPLIT REMAINS UNDOCUMENTED and is deliberately NOT "
                                   "backfilled with this figure."),
            ],
            "note": "This split has moved before and will move again. Each historical period is treated as "
                    "potentially different from the current one; undocumented periods are suppressed, never "
                    "backfilled with today's number.",
        },
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://docs.sky.money/", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Repurchased SKY is burned OR redistributed to LSSKY stakers per a GOVERNANCE PARAMETER, "
                               "currently 55% burn / 45% stakers. Never net staking rewards against burn."},
        # THE SAME 13 AUGUST 2026 PROPOSAL that set the 55/45 split also normalised LSSKY-to-SKY
        # rewards: a 96,903,706 SKY stream vesting over 90 DAYS. That is 1,076,707.84 SKY/day, and
        # it is EMISSIONS — SKY newly distributed to stakers — so also_emissions is set and the same
        # figure feeds both gross issuance and emissions rather than counting the buyback alone.
        #
        # THE EXPIRY IS SET NOW, NOT LATER. 2026-08-13 + 90 days inclusive ends 2026-11-10. Without
        # `until` the final step would carry 1.08m SKY/day forward for ever, reporting a finished
        # 90-day stream as a permanent emission — exactly the failure the Render schedule was fixed
        # for. Past the expiry the schedule goes silent and the Gap Report says SCHEDULE EXPIRED.
        "issuance_schedule": {
            "steps": [
                {"from": "2026-08-13", "tokens_per_day": 96_903_706 / 90, "until": "2026-11-10"},
            ],
            "source_url": "https://messari.io/", "source_date": "2026-08-13", "status": "active",
            "also_emissions": True,
            "note": "LSSKY-to-SKY reward normalisation from the 2026-08-13 Executive Proposal: 96,903,706 "
                    "SKY vesting over 90 days = 1,076,707.84 SKY/day, expiring 2026-11-10. This is the "
                    "ONLY declared SKY issuance — it is a governance-directed stream, not a perpetual "
                    "inflation schedule, so nothing is projected past the expiry.",
        },
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
            # NO burn_zero ENTRY, AND NOT BECAUSE THE ADDRESS WAS WRONG.
            # It was removed 2026-09-14 after the ChainSecurity Dss Flappers audit (July 2026)
            # and Sky's own dss-flappers repo established that Sky's burn is NOT a
            # transfer-to-dead-address mechanism at all. A Splitter divides protocol surplus
            # between a Flapper and a reward farm (the 55/45 split already in fee_split); the
            # Flapper trades USDS for the gem on UniswapV2 and sends the proceeds to a
            # CONFIGURABLE RECEIVER — and in the FlapperUniV2 variant deposits the gem back into
            # the pool, minting LP tokens to that receiver. A documented Splitter transaction
            # shows the allocation going to "Burn — MCD Pause Proxy" and "Sky Rewards".
            #
            # So the zero balance at 0x0 was CORRECT AND EXPECTED. It was never evidence of a
            # missing burn, and never evidence of a wrong address — it was the right answer to a
            # question that should not have been asked.
            #
            # DO NOT RE-ADD THIS, AND DO NOT SUBSTITUTE ANOTHER ADDRESS FOR IT. No balance read of
            # any address models this: the receiver is configurable, and it may hold LP tokens
            # rather than SKY. The MCD Pause Proxy is the lead worth following, but the audit
            # warns that if the Pause Proxy is the receiver and governance does not control it,
            # LP tokens can be lost or seized — a warning that only makes sense if the receiver
            # HOLDS tokens rather than destroying them. A Pause Proxy balance may therefore be a
            # TREASURY HOLDING, not a burn. See UNAVAILABLE and OPEN_QUESTIONS.
            # THE RECEIVER, read as a TREASURY HOLDING and never as a burn.
            "pause_proxy": _contract(
                "0xBE8E3e3618f7474F8cB1d074A26afFef007E98FB", "ethereum", "treasury_holding", "SKY",
                "https://vote.makerdao.com/", verified="2026-09-14", provenance="protocol governance",
                holder_has_code=True,
                purpose="MCD Pause Proxy — the Smart Burn Engine's RECEIVER, and Sky's "
                        "governance-controlled treasury. Named as the receiver by the 26 June 2023 "
                        "Smart Burn Engine launch poll and labelled 'Sky: MCD Pause Proxy' on "
                        "Etherscan. Holds ~$130m across SPK/SKY/MKR, with governance votes moving SKY "
                        "OUT of it — which is the proof that it is redeployable, not retired.",
                note="NEVER gross_burn_tokens and never burn_address_balance. Tokens here still exist "
                     "and governance can spend them, so counting this as burned would overstate "
                     "permanent supply destruction by the entire balance."),
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
            # THE SMART BURN ENGINE MACHINERY, all sourced from Sky's own repositories rather than
            # from an aggregator: sky-ecosystem/spells-mainnet src/test/addresses_mainnet.sol (627
            # ChainLog keys), sky-ecosystem/dss-flappers, sky-ecosystem/dss-chain-log.
            #
            # NONE OF THESE IS GIVEN A METRIC-BEARING KIND, and that is deliberate. The Splitter and
            # the Flapper are EXECUTORS — surplus passes THROUGH them, it does not accumulate in
            # them — so a balance read on either would be the Uniswap Firepit mistake again. The
            # SBE BEAM is a rate controller and holds nothing. Only the Pause Proxy, which actually
            # RECEIVES, has a balance worth reading, and it already does.
            "splitter": _contract(
                "0xBF7111F13386d23cb2Fba5A538107A73f6872bCF", "ethereum", "burn_executor", "SKY",
                "https://github.com/sky-ecosystem/spells-mainnet", verified="2026-09-14",
                provenance="ChainLog key MCD_SPLIT, from Sky's own spells-mainnet "
                           "src/test/addresses_mainnet.sol; corroborated by dss-flappers "
                           "deploy/FlapperInit.sol, which reads chainlog.getAddress(\"MCD_SPLIT\") as the splitter",
                holder_has_code=True,
                purpose="Splitter (ChainLog MCD_SPLIT) — withdraws USDS from the Vow and divides it "
                        "between the Flapper (burn engine) and the reward farm. Reference only: "
                        "surplus passes through, it does not accumulate here.",
                note="Configurable parameters, per dss-flappers src/Splitter.sol: `burn` (WAD, the share "
                     "sent to the flapper), `hop` (seconds between kicks, initialised to 1 hours), "
                     "`flapper`, `farm`. kick() computes lot = tot * burn / RAD."),
            "flapper": _contract(
                "0x374D9c3d5134052Bc558F432Afa1df6575f07407", "ethereum", "burn_executor", "SKY",
                "https://github.com/sky-ecosystem/dss-flappers", verified="2026-09-14",
                provenance="ChainLog key MCD_FLAP, from Sky's own spells-mainnet addresses_mainnet.sol",
                holder_has_code=True,
                purpose="Flapper (ChainLog MCD_FLAP) — trades USDS for SKY on UniswapV2 and sends the "
                        "proceeds to its receiver. Reference only: it is the executor, not the "
                        "destination. THE RECEIVER IS ENFORCED — see destination_enforced_by_code."),
            "sbe_beam": _contract(
                "0xc8b61d211D3D03A630Fb09199E17953a8c9749a9", "ethereum", "burn_executor", "SKY",
                "https://github.com/sky-ecosystem/spells-mainnet", verified="2026-09-14",
                provenance="ChainLog key MCD_SBEBEAM, from Sky's own spells-mainnet addresses_mainnet.sol",
                holder_has_code=True,
                purpose="SBE BEAM (ChainLog MCD_SBEBEAM) — lets facilitators reconfigure the Smart Burn "
                        "Engine's rate within governance bounds. Holds no tokens; reference only. This "
                        "is why the 2024 flapper/want/pip values are recorded as point_in_time."),
            "lockstake_engine": _contract(
                "0xCe01C90dE7FD1bcFa39e237FE6D8D9F569e8A6a3", "ethereum", "burn_executor", "SKY",
                "https://github.com/sky-ecosystem/spells-mainnet", verified="2026-09-14",
                provenance="ChainLog key LOCKSTAKE_ENGINE, from Sky's own spells-mainnet addresses_mainnet.sol",
                holder_has_code=True,
                purpose="Lockstake Engine — the staking entry point that mints LSSKY. Reference only: the "
                        "LOCK-RATE FIGURE IS LSSKY's OWN SUPPLY, already read above. Reading the engine's "
                        "SKY balance as well would double-count the same staked SKY."),
        },
        # THE CHAINLOG, recorded as the lookup route rather than as a contract. Every Sky address
        # above is a ChainLog key, and the registry resolves keys to addresses on-chain, so a
        # governance redeploy changes the address the key points at without changing the key. Future
        # lookups should go through this rather than hardcoding a fresh address.
        "registry": {
            "address": "0xdA0Ab1e0017DEbCd72Be8599041a2aa3bA7e740F",
            "what": "MCD ChainLog — getAddress(bytes32) resolves a key to the live address; list() "
                    "returns every key; count() the number of them",
            "source_url": "https://github.com/sky-ecosystem/dss-chain-log",
            "source_date": "2026-09-14",
            "key_list_file": "sky-ecosystem/spells-mainnet src/test/addresses_mainnet.sol (627 entries)",
            "keys_used_here": ["MCD_SPLIT", "MCD_FLAP", "MCD_SBEBEAM", "LOCKSTAKE_SKY",
                               "LOCKSTAKE_ENGINE", "MCD_PAUSE_PROXY", "SKY", "FLAP_SKY_ORACLE"],
        },
        # ============ THE STRONGEST DESTINATION EVIDENCE IN THE WHOLE CONFIG ============
        # Not a document, not a poll, not an inference: the DEPLOY CODE REFUSES TO RUN OTHERWISE.
        # sky-ecosystem/dss-flappers deploy/FlapperInit.sol line 164:
        #
        #   require(flapper.receiver() == dss.chainlog.getAddress("MCD_PAUSE_PROXY"),
        #           "Flapper receiver mismatch");
        #
        # So the Smart Burn Engine's proceeds go to the Pause Proxy — Sky's governance-controlled
        # treasury — and governance cannot silently route them elsewhere without redeploying the
        # initialiser. destination_effect = treasury_redeployable is therefore not a judgement call.
        # ================================================================================
        "destination_enforced_by_code": {
            "requirement": 'require(flapper.receiver() == chainlog.getAddress("MCD_PAUSE_PROXY"), "Flapper receiver mismatch")',
            "file": "sky-ecosystem/dss-flappers deploy/FlapperInit.sol:164",
            "source_date": "2026-09-14",
            "means": "the deployment itself enforces the receiver; governance cannot re-route the Smart "
                     "Burn Engine's proceeds without redeploying",
            "effect": "treasury_redeployable — the SKY is HELD by a treasury that can spend it, not destroyed",
        },
        # THE SBE'S DEPLOYMENT CAP. A ceiling on the rate, not a rate.
        "buyback_max_usd_annual": {
            "value": 350_000_000,
            "currency": "USDS",
            "what": "maximum annual rate on the Smart Burn Engine's deployment",
            "source_date": "2026-09-14",
            "note": "A CAP IS NOT A SPEND. Any figure that traces to this ceiling rather than to an "
                    "executed cycle must be labelled 'authorized, not confirmed executed'.",
        },
        # NOT "transfer" — that was the assumption the Dss Flappers audit refuted. Nothing is read
        # until the Flapper variant question is settled, which is exactly what "undetermined" means.
        # THE FLAPPER VARIANT, ANSWERED — with a shelf life. A Sky governance executive vote
        # initialises FlapperUniV2SwapOnly, and the 2023 ChainSecurity audit of that variant states
        # it sends proceeds to a predefined RECEIVER rather than depositing into the pair as
        # liquidity. So the LP-position concern is RETIRED: SKY is not being parked in a Uniswap
        # pool, and Sky's archetype 4 block is not wrong for that reason.
        #
        # RECORDED AS CONFIGURATION AT THAT TIME, NOT AS CURRENT STATE. The vote is from
        # 2024-09-27, and SBEBeam now lets facilitators reconfigure the splitter within governance
        # bounds — so this says what was set then, not what is set now. Confirming it still holds
        # is an on-chain read, not a documentary question. See OPEN_QUESTIONS.
        "governance_parameters": {
            "flapper": {
                "value": "0x374D9c3d5134052Bc558F432Afa1df6575f07407",
                "what": "FlapperUniV2SwapOnly — converts USDS to the gem and sends it to a receiver",
                "source_url": "https://github.com/sky-ecosystem/community",
                "as_of": "2024-09-27", "status": "point_in_time",
            },
            "want": {"value": 0.98, "what": "0.98 * WAD", "source_url": "https://github.com/sky-ecosystem/community",
                     "as_of": "2024-09-27", "status": "point_in_time"},
            # RECONFIGURED SINCE THE VOTE, and this is the proof that point_in_time was the right
            # label: the live pip is NOT the one the 2024 executive set. pair() still matches, so
            # the reconfiguration was partial rather than a wholesale redeploy.
            "pip": {"value": "0xc2ffbbdccf1466eb8968a846179191cb881ecdff",
                    "what": "live oracle, read on-chain",
                    "source_url": None, "source": "read on-chain 2026-09-14",
                    "as_of": "2026-09-14", "status": "confirmed_current",
                    "superseded": {"value": "0x61A12E5b1d5E9CC1302a32f0df1B5451DE6AE437",
                                   "what": "SWAP_ONLY_FLAP_SKY_ORACLE, per the 2024-09-27 executive vote",
                                   "note": "superseded — the vote no longer describes what is deployed"}},
            # CONFIRMED unchanged on-chain, so this one is current rather than point-in-time.
            "pair": {"value": "0x2621CC0B3F3c079c1Db0E80794AA24976F0b9e3c", "what": "PAIR_USDS_SKY",
                     "source_url": "https://github.com/sky-ecosystem/community",
                     "source": "2024 vote, re-read on-chain 2026-09-14 and unchanged",
                     "as_of": "2026-09-14", "status": "confirmed_current"},
            "receiver": {
                "value": "0xBE8E3e3618f7474F8cB1d074A26afFef007E98FB",
                "what": "ANSWERED — the MCD Pause Proxy, Sky's governance-controlled treasury. The "
                        "26 June 2023 Smart Burn Engine launch poll names MCD_Pause_Proxy as the "
                        "destination of what the engine buys. So the bought SKY is HELD, not "
                        "destroyed, which is why archetype 4 was removed and archetype 3 kept.",
                "source_url": "https://vote.makerdao.com/", "as_of": "2026-09-14",
                "status": "confirmed_current",
            },
        },
        "burn_read_method": "undetermined",
        "burn_read_note": "Splitter -> Flapper -> UniswapV2 -> receiver ENFORCED as MCD_PAUSE_PROXY by "
                          "FlapperInit.sol:164. No dead address is "
                          "involved, so no address balance models it. Which variant is active determines "
                          "whether the gem is even removed from supply: FlapperUniV2SwapOnly converts and "
                          "sends to a receiver, FlapperUniV2 deposits back into the pool as LP tokens.",
        "burn_mechanism": {
            "model": "amm_swap_to_receiver", "status": "refuted",
            "source_url": "https://www.chainsecurity.com/security-audit/makerdao-dss-flappers",
            "source_date": "2026-09-14",
            "note": "REFUTED, not merely unconfirmed: the transfer-to-dead-address model is positively "
                    "contradicted by the protocol's own code and audit (ChainSecurity Dss Flappers, July "
                    "2026, 20260710-ChainSecurity_Sky_Dss_Flappers_audit.pdf; "
                    "https://github.com/sky-ecosystem/dss-flappers). Surplus is split by a Splitter between "
                    "a Flapper and a reward farm; the Flapper trades USDS for the gem on UniswapV2 and "
                    "sends proceeds to a configurable receiver, with the FlapperUniV2 variant depositing "
                    "the gem back into the pool as LP tokens. Newer components — SBEBeam, letting "
                    "facilitators configure splitter, kicker and farms within governance bounds, and a "
                    "Kicker entrypoint for surplus processing — make the destination MORE configurable, "
                    "not less. Until the active variant is established, Sky has no burn figure, and that "
                    "is the correct state.",
        },
        "buyback_destination": "split", "destination_split": 0.55, "burn_execution": "protocol",
        # "mixed" understated it: whether the burn LEG removes supply at all is unresolved. If the
        # active variant is FlapperUniV2, part of what was called burn is SKY in an LP position —
        # not destroyed, not removed from supply, and recoverable.
        # ARCHETYPE 4 REMOVED 2026-09-14 — THE RECEIVER IS THE TREASURY.
        # The Smart Burn Engine's receiver is Sky's MCD Pause Proxy, confirmed three ways: Etherscan
        # labels it "Sky: MCD Pause Proxy"; the 26 June 2023 Smart Burn Engine launch poll sets the
        # receiver to MCD_Pause_Proxy as "the destination address of the LP tokens purchased by the
        # Smart Burn Engine"; and the address holds ~$130m (SPK 80.97%, SKY 18.96%, MKR 0.07%) with
        # governance votes moving SKY OUT of it.
        # A mechanism named "Smart Burn Engine" that sends its purchases to a governance-controlled
        # treasury is not a permanent burn. ARCHETYPE 3 STAYS — a revenue-funded buyback
        # demonstrably happens, and only its destination was wrong.
        "destination_effect": "treasury_redeployable",
        "dune_queries": {
            # RE-SCOPED 2026-09-14, AND BLOCKED. Twice now this slot has been aimed at the wrong
            # question. It was "did anything burn"; then "where does the engine send it"; both
            # assumed a model the Dss Flappers audit has since refuted. DO NOT AUTHOR IT YET.
            #
            # The real question is: what does the Splitter do with the burn leg — where does the
            # SKY end up, and is it DESTROYED or HELD? And that cannot be turned into SQL until
            # the Flapper variant is known, because the variant changes what the query should even
            # look for:
            #   FlapperUniV2SwapOnly -> follow the gem to the receiver, and ask what the receiver
            #                           does with it (destroy, hold, redeploy)
            #   FlapperUniV2         -> follow LP TOKENS, not SKY. The SKY is in a pool. A query
            #                           counting SKY transfers would miss it entirely, or
            #                           double-count the pool's own rebalancing as burn.
            # Writing one query that assumes either variant risks a plausible wrong number, which
            # is the failure this whole tool is built against. See OPEN_QUESTIONS: "WHICH FLAPPER
            # VARIANT IS ACTIVE?" — that is the blocker, and it is a governance question, not a
            # data one.
            "gross_burn_tokens": {
                "query_id": None, "date_col": "month", "value_col": "tokens",
                "granularity": "monthly", "drop_current_period": True,
                "source_url": "https://github.com/sky-ecosystem/dss-flappers",
                "note": "BLOCKED, not merely unauthored. The variant question must be settled first; until "
                        "then Sky has no burn figure at all, which is the correct state.",
            },
            **_dune("actual_buyback_usd", "actual_buyback_tokens", "gross_issuance_tokens",
                    "emissions_tokens", "staked_tokens"),
        },
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
        # TOKENOMICS CHANGED APRIL 2026, and the change supersedes the old emissions model
        # entirely. The buyback is now REVENUE-FUNDED and distributed to sPENDLE holders, replacing
        # gauge-voting emissions. The SHARE of revenue is NOT documented, so programmed=False and
        # share_to_buyback stays None — estimating it from the ~2m PENDLE repurchased in six months
        # would be reverse-engineering a rule from an outcome.
        "fee_split": {"share_to_buyback": None,
                      "source_url": "https://docs.pendle.finance/ProtocolMechanics/Mechanisms/Tokenomics",
                      "source_date": "2026-09-14", "programmed": False, "status": "active",
                      "note": "APRIL 2026 TOKENOMICS: revenue-funded buyback distributed to sPENDLE holders, "
                              "replacing gauge-voting emissions. DISTRIBUTE, not burn. The revenue SHARE is "
                              "not documented — do not estimate it. Sanity bounds only: ~2m PENDLE "
                              "repurchased in the first six months, ~$653,702 of fees over 30 days."},
        "burn_split": None,
        # THE HARD SUPPLY CAP IS GONE. It was 258,446,028 and most trackers still show it, which is
        # stale. April 2026 replaced it with TERMINAL INFLATION OF 2%/YEAR.
        #
        # NOT DECLARED AS A tokens_per_day STEP, for the same reason as NEAR: 2% is a RATE on a
        # moving supply base, and issuance_schedule takes a fixed daily token count. Freezing today's
        # 2% as a constant would drift from the rule the moment supply moves.
        "issuance_schedule": None,
        "issuance_rate_declared": {
            "annual_rate": 0.02,
            "kind": "terminal inflation",
            "effective_from": "2026-04",
            "supersedes": {"max_supply": 258_446_028,
                           "note": "the hard cap was REMOVED in April 2026. Most trackers still display "
                                   "258,446,028 — that figure is stale and must not be used as max_supply."},
            "source_url": "https://docs.pendle.finance/ProtocolMechanics/Mechanisms/Tokenomics",
            "source_date": "2026-09-14",
            "note": "A RATE, not a token count. Not declared as a schedule step because tokens_per_day "
                    "would freeze a moving base.",
        },
        # HISTORICAL, DO NOT APPLY TO CURRENT ISSUANCE. Pendle's own deployments/1-core.json carries
        # `initialPendlePerSec: 826719576719576719` (0.8267 PENDLE/sec). That file describes the
        # PRE-APRIL-2026 gauge-emission model, which the new tokenomics replaced. Applying it now
        # would report a superseded emission schedule as current.
        "historical_parameters": {
            "initialPendlePerSec": {
                "value": 826719576719576719,
                "scaled": 0.826719576719576719,
                "unit": "PENDLE per second",
                "source_url": PENDLE_DEPLOYMENTS_1_CORE,
                "status": "historical",
                "why": "from Pendle's deployments/1-core.json, which describes the pre-April-2026 "
                       "gauge-voting emission model. The April 2026 tokenomics replaced that model with "
                       "terminal 2% inflation. DO NOT apply this to current issuance without confirming "
                       "it was not superseded — every indication is that it was.",
            },
        },
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
                     "expected symbol is CONFIRMED sPENDLE. "
                     "** OPEN RISK ON THE FIGURE ITSELF, NOT ON THE ADDRESS — see boost_risk below. ** "
                     "vePENDLE holders converting received a BOOSTED sPENDLE balance of up to 4x, decaying "
                     "over ~2 years, and Pendle's docs describe a 'virtual sPENDLE balance' for voting "
                     "power. If virtual or boosted balances are included in totalSupply(), this figure "
                     "OVERSTATES real PENDLE staked by up to 4x. The address is right and the read "
                     "succeeds; what is unresolved is what the number means."),
        },
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "yield_payout",
        "destination_source_url": "https://docs.pendle.finance/ProtocolMechanics/Mechanisms/Tokenomics",
        "destination_confirmed_date": "2026-09-14",
        # ** FORCED AMBER ON locked_tokens. ** The read works and the address is Pendle's own; what
        # is unresolved is whether totalSupply() counts REAL staked PENDLE or includes the migration
        # boost and the virtual balance. Up to 4x is not a rounding question — it is the difference
        # between a lock rate that supports the thesis and one that does not. Settling it needs
        # direct contract inspection (does totalSupply include virtual balances?), which is not a
        # documentary question and is NOT guessed at here.
        "non_comparable": {
            "locked_tokens": {
                "why": "vePENDLE holders converting to sPENDLE received a BOOSTED balance of up to 4x, "
                       "decaying over ~2 years, and Pendle's docs describe a separate 'virtual sPENDLE "
                       "balance' used for voting power. If either is included in totalSupply(), this "
                       "figure OVERSTATES real PENDLE staked by as much as 4x. NOT RESOLVED — it needs "
                       "direct contract inspection, not a docs reading.",
                "use_instead": "nothing yet. Treat the figure as an UPPER BOUND on PENDLE staked. The "
                               "dashboard cross-check below would catch a large divergence if "
                               "app.pendle.finance ever becomes fetchable.",
            },
        },
        "cooldown": {
            "unstaking_days": 14,
            "readable_via": "cooldownDuration() on the sPENDLE contract",
            "note": "Recorded, not read. Matters only if the lock-rate figure is ever converted into an "
                    "exit-pressure estimate.",
        },
        "dune_queries": _dune("locked_tokens", "avg_lock_duration_days", "emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens"),
                "cross_checks": [
            {"primary": "locked_tokens", "primary_source": "tier 2 contract read",
             "secondary": "locked_tokens_dashboard", "secondary_source": "https://app.pendle.finance/spendle/stake/in",
             "tolerance": 0.03, "prefer": "primary",
             "note": "The contract read is authoritative; the page cross-checks it. A divergence beyond "
                     "tolerance is flagged rather than one figure silently replacing the other."},
        ],
        "materiality": "high",
        "notes": "LOCK SOURCE MIGRATED: vePENDLE is deprecated — Pendle's own deployments/1-core.json lists "
                 "it under a 'deprecated' block alongside feeDistributor, feeDistributorV2 and "
                 "votingController, independently confirming the tokenomics docs. A vePENDLE read would show "
                 "a falling figure reflecting MIGRATION rather than falling lock-in, so it was removed rather "
                 "than kept as a fallback. sPENDLE (0x9999...4144) is the current source and its address is "
                 "resolved. "
                 "OPEN RISK, UNRESOLVED: sPENDLE's totalSupply() may include the up-to-4x migration BOOST and "
                 "the 'virtual sPENDLE balance' used for voting power, which would overstate real PENDLE "
                 "staked by up to 4x. locked_tokens is forced AMBER for this reason. "
                 "TOKENOMICS CHANGED APRIL 2026: the 258,446,028 hard cap is GONE (still shown stale on most "
                 "trackers), replaced by terminal 2%/yr inflation; the buyback is now revenue-funded and "
                 "distributed to sPENDLE holders, with the share undocumented. The initialPendlePerSec figure "
                 "in deployments/1-core.json is the OLD emission model — historical, not current.",
    },
    {
        "name": "Fluid", "symbol": "FLUID",
        "coingecko_id": "instadapp",
        "defillama_fees_slug": "fluid", "defillama_protocol": "fluid", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.fluid.io/",
                      "source_date": "2026-09-14", "programmed": False, "status": "active",
                      "note": "BUYBACK IS ACTIVE, not pending. It triggered in October 2025 on surpassing "
                              "$10m revenue, launching 'The Fluid Reserve'. The revenue SHARE is not a "
                              "single documented number — the first month was 100% of Ethereum mainnet "
                              "revenue (~$1.7m), which is a launch condition, not a standing rule."},
        "burn_split": None,
        "issuance_schedule": None,
        "contracts": {
            "token": _contract(
                "0x6f40d4A6237C257fff2dB00FA0510DeEECd303eb", "ethereum", "erc20_total_supply", "FLUID",
                "https://etherscan.io/address/0x6f40d4a6237c257fff2db00fa0510deeecd303eb",
                verified="2026-09-14", provenance="Etherscan only — NOT repo-confirmed",
                token_standard="erc20",
                purpose="FLUID token — the supply read.",
                note="PROVENANCE IS ETHERSCAN ONLY, and that is deliberately recorded rather than rounded "
                     "up to 'verified against protocol docs'. Instadapp/fluid-contracts-public's own "
                     "technical docs (docs/docs.md) never mention this address — the only addresses in "
                     "that file are the native-ETH sentinel 0xEeee...EEeE. Usable, but do NOT upgrade its "
                     "confidence without a second independent source."),
        },
        # BUYBACK IS ACTIVE — CORRECTED 2026-09-14. It was previously recorded as
        # threshold-gated-and-unconfirmed, which is now wrong: the threshold was CROSSED in October
        # 2025 and the programme launched. Status moves to "active" and the threshold becomes
        # history rather than a live gate.
        "buyback_threshold": {
            "threshold_usd_annualised": 10_000_000,
            "status": "active",
            "crossed_on": "2025-10",
            "launched": "The Fluid Reserve",
            "first_month": {"share": 1.0, "of": "Ethereum mainnet revenue", "approx_usd": 1_700_000},
            "spent_to_date_usd": 3_200_000, "spent_through": "2025-12",
            "note": "CROSSED AND LAUNCHED. ~$1.7m in the first month (100% of Ethereum mainnet revenue) "
                    "and ~$3.2m total through December 2025. The 100% first-month figure is a LAUNCH "
                    "CONDITION, not a standing share — do not apply it forward.",
        },
        # ============ THE RESERVE ADDRESS IS NEVER PUBLISHED. PERMANENT GAP. ============
        # Checked and closed, not pending: Fluid's own blog announcement, Messari's report, the
        # official X announcement, and BOTH Instadapp/fluid-contracts-public and
        # Instadapp/fluid-governance (README and docs.md) — no address in any of them.
        #
        # So actual_buyback_tokens and actual_buyback_usd are SUPPRESSED (see UNAVAILABLE), and the
        # IMPLIED buyback stays computable from the revenue side. Do not search again this round.
        # ==============================================================================
        "destination_undocumented": {
            "what": "The Fluid Reserve — the destination of repurchased FLUID",
            "why": "the Reserve address is not published in any Fluid-authored source",
            "checked": ["Fluid's own blog announcement", "Messari's report", "the official X announcement",
                        "Instadapp/fluid-contracts-public (README, docs/docs.md)",
                        "Instadapp/fluid-governance (README, docs)"],
            "checked_on": "2026-09-14",
            "effect": "actual_buyback_tokens and actual_buyback_usd are suppressed; the implied figure from "
                      "the revenue side remains computable",
        },
        # TWO REVENUE ROUTES WITH DIFFERENT DESTINATIONS, per DefiLlama's own metric definitions.
        # They are NOT the same mechanism and must not be summed into one buyback figure: one
        # accumulates in a reserve, the other is paid straight out.
        "destination_routes": [
            {"route": "Fluid Lending", "defillama_definition": "Token buyback from the treasury",
             "destination": "The Fluid Reserve", "effect": "hold / treasury-style",
             "address": None, "why_no_address": "never published — see destination_undocumented"},
            {"route": "Fluid DEX / DEX Lite",
             "defillama_definition": "Money going to governance token holders",
             "destination": "FLUID holders directly", "effect": "distribute",
             "address": None,
             "note": "A DIFFERENT DESTINATION ENTIRELY from the Lending route. Track separately where "
                     "the data allows; never merge into a single buyback number."},
        ],
        "buyback_destination": "hold", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "locked_supply",
        # STAKING: FLAGGED, NOT REMOVED. Fluid's own technical docs (docs/docs.md) contain ZERO
        # mentions of stake, lock, veFLUID, governance or emission — consistent with no mechanism
        # existing. But that document SCOPES ITSELF to the Liquidity layer and the Vault protocol,
        # so a staking contract could simply be outside its scope. Absence of evidence is not
        # evidence of absence, and deleting a metric on it would be the stronger claim.
        "metric_unconfirmed": {
            "locked_tokens": {
                "status": "unconfirmed — absence of evidence, not evidence of absence",
                "checked": "Instadapp/fluid-contracts-public docs/docs.md — zero mentions of "
                           "stake / lock / veFLUID / governance / emission",
                "why_weak": "that document scopes itself to the Liquidity layer and Vault protocol, so a "
                            "staking contract could be out of scope rather than nonexistent",
                "would_settle_it": "a positive statement in Fluid's own tokenomics material, or a "
                                   "registry-style contract listing",
                "do_not": "do not remove the metric on this evidence",
            },
        },
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens"),
        # SUPPLY FIGURES CONTRADICT EACH OTHER ON THEIR FACE. Flagged, NOT resolved by picking one:
        # three different circulating figures circulate (77.95m / 78.7m / 83.7m in our own store)
        # ALONGSIDE claims that the unlock schedule "ended in 2025" and the token is "fully
        # unlocked". Fully unlocked against a confirmed 100m max supply cannot coexist with a
        # circulating figure in the high 70s or low 80s — one of those claims is wrong, and
        # choosing between them without a source would be inventing the answer.
        "supply_conflict": {
            "max_supply_confirmed": 100_000_000,
            "circulating_reported": [
                {"value": 77_950_000, "source": "reported"},
                {"value": 78_700_000, "source": "reported"},
                {"value": 83_700_000, "source": "our own store"},
            ],
            "conflicting_claim": "the unlock schedule 'ended in 2025' and the token is 'fully unlocked'",
            "why_contradictory": "fully unlocked against a 100m max supply cannot coexist with a "
                                 "circulating figure of 78-84m",
            "resolution": "NONE — flagged, not resolved. Do not pick one.",
        },
        "scale_reference": {
            "revenue_annualised_usd": 15_000_000, "tvl_usd": 977_790_000, "tvl_change_30d": 0.201,
            "chains": ["Ethereum", "Arbitrum", "Plasma", "Base", "Polygon", "+1"],
            "as_of": "2026-09-14",
            "note": "Sanity bounds for the archetype 3 figures, not stored metrics.",
        },
        "materiality": "medium",
        "notes": "BUYBACK IS ACTIVE — corrected from threshold-gated-and-unconfirmed. The $10m revenue "
                 "threshold was CROSSED in October 2025, launching The Fluid Reserve: ~$1.7m in month one "
                 "(100% of Ethereum mainnet revenue, a launch condition rather than a standing share) and "
                 "~$3.2m through December 2025. "
                 "THE RESERVE ADDRESS IS NEVER PUBLISHED — checked across Fluid's blog, Messari, the "
                 "official X announcement and both Instadapp repos. Accepted as a PERMANENT gap: the actual "
                 "buyback metrics are suppressed and the implied figure stays computable. "
                 "TWO REVENUE ROUTES with DIFFERENT destinations per DefiLlama's own definitions — Lending "
                 "buys back to the Reserve (hold), DEX/DEX Lite pays holders directly (distribute). Never "
                 "merge them. "
                 "STAKING is FLAGGED UNCONFIRMED, not removed: Fluid's technical docs never mention "
                 "staking, but they scope themselves to the Liquidity and Vault layers, so that is absence "
                 "of evidence. "
                 "TOKEN ADDRESS provenance is Etherscan only, NOT repo-confirmed. "
                 "SUPPLY FIGURES CONTRADICT: 77.95m / 78.7m / 83.7m circulating alongside 'fully unlocked' "
                 "against a 100m cap. Flagged, not resolved. "
                 "CoinGecko id is still instadapp after the rebrand — verify.",
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
        # TWO BUYBACK STREAMS, BOTH TO sETHFI HOLDERS, NEVER COLLAPSED INTO ONE.
        # They have different bases, different cadences and different confidence, and a single
        # share_to_buyback cannot express either honestly — so the top-level share stays None and
        # the streams are declared separately.
        "fee_split": {
            "share_to_buyback": None,
            "source_url": "https://etherfi.gitbook.io/etherfi",
            "source_date": "2026-09-14",
            "programmed": False,
            "status": "active",
            "streams": [
                {"stream": "eETH withdrawal fee revenue", "share": 1.0, "of": "eETH withdrawal fees",
                 "cadence": "weekly", "status": "active",
                 "components": ["implicit delayed-exit fee", "explicit instant-exit fee"],
                 "source": "Ether.fi governance gitbook, proposal #11",
                 "source_url": "https://etherfi.gitbook.io/etherfi"},
                {"stream": "share of total protocol revenue", "share": None, "of": "total protocol revenue",
                 "cadence": None, "status": "target_not_commitment",
                 "actual_fy2024": 0.05, "target_fy2025": 0.25,
                 "conditional_on": "profitability — Ether.fi's own wording",
                 "source": "Ether.fi's own Medium post",
                 "note": "5% was ACTUAL in FY2024; 25% is a TARGET for FY2025 explicitly conditioned on "
                         "profitability. Do not use 25% as though it were the operative share — a "
                         "conditional target is not a rule."},
            ],
            "note": "TWO streams to sETHFI holders: 100% of eETH withdrawal fee revenue (weekly), plus a "
                    "share of total protocol revenue (5% actual FY2024, 25% TARGET FY2025 conditional on "
                    "profitability). Do not collapse them; do not treat the target as the rule.",
        },
        "burn_split": {"share_of_fees_burned": 0.0, "source_url": "https://etherfi.gitbook.io/etherfi",
                       "source_date": "2026-09-14", "status": "n/a",
                       "note": "ERC20Burnable IS in ETHFI's inheritance (EtherFiGovernanceToken is ERC20, "
                               "Burnable, Permit, Votes), but NO EXECUTED BURN is evidenced anywhere. An "
                               "available function is not a mechanism — destination stays DISTRIBUTE."},
        "issuance_schedule": None,
        "contracts": {
            "token": _contract(
                "0xFe0c30065B384F05761f15d0CC899D4F9F9Cc0eB", "ethereum", "erc20_total_supply", "ETHFI",
                "https://etherscan.io/address/0xfe0c30065b384f05761f15d0cc899d4f9f9cc0eb#code",
                verified="2026-09-14", provenance="deployed source: EtherFiGovernanceToken (ERC20, Burnable, Permit, Votes)",
                token_standard="erc20",
                purpose="ETHFI governance token — the supply read."),
            # THE DESTINATION: sETHFI, where buybacks are distributed to stakers.
            # PARTIAL BY CONSTRUCTION. sETHFI also exists on Scroll (~$1.35m), Arbitrum (~$717k) and
            # Base (~$128k). Those deployments' addresses are not on file, so the mainnet figure
            # UNDERSTATES total staked — flagged rather than silently presented as the whole.
            "sethfi": _contract(
                "0x86B5780b606940Eb59A062aA85a07959518c0161", "ethereum", "ve_total_supply", "ETHFI",
                "https://etherscan.io/address/0x86B5780b606940Eb59A062aA85a07959518c0161",
                verified="2026-09-14", provenance="Ether.fi staking contract, cross-referenced with the "
                                                  "Dune 8683038 sETHFI series already wired below",
                read_method="escrow_balance_of", token_standard="erc20", underlying="token",
                supply_is_partial=True,
                partial_reason="MAINNET ONLY. sETHFI also exists on Scroll (~$1.35m), Arbitrum (~$717k) and "
                               "Base (~$128k); those addresses are not on file, so this understates total "
                               "staked ETHFI.",
                purpose="sETHFI — the buyback DESTINATION and the lock-rate input. Read as "
                        "ETHFI.balanceOf(sETHFI), not sETHFI.totalSupply(): the same ERC-4626-shaped "
                        "hazard as Maple's stSYRUP, and the assets read is correct either way."),
            # THE HUB. Ether.fi resolves its own contracts by NAME through an on-chain registry,
            # the same pattern as Sky's ChainLog and OriginTrail's Hub. If a buyback EXECUTOR is
            # ever needed, query this for a registered name — do not hardcode a found address.
            # Confirmed inside `if (forkEnum == MAINNET_FORK)` in etherfi-protocol/smart-contracts
            # test/TestSetup.sol:410, which resolves EtherFiNode, WithdrawRequestNFT, Liquifier,
            # EtherFiTimelock, EtherFiAdmin, EtherFiOracle and RoleRegistry through it.
            "address_provider": _contract(
                "0x8487c5F8550E3C3e7734Fe7DCF77DB2B72E4A848", "ethereum", "burn_executor", "ETHFI",
                "https://github.com/etherfi-protocol/smart-contracts", verified="2026-09-14",
                provenance="etherfi-protocol/smart-contracts test/TestSetup.sol:410, inside the "
                           "MAINNET_FORK branch",
                holder_has_code=True,
                purpose="AddressProvider — the mainnet registry exposing getContractAddress(string). "
                        "Reference only, no metric: it holds no tokens. THE ROUTE to any other Ether.fi "
                        "contract, including a buyback executor if one is registered."),
            # ** DELIBERATELY ABSENT: buybackWallet 0x2f5301a3D59388c509C65f8698f521377D41Fd0F. **
            # It appears EXACTLY ONCE in Ether.fi's repository — a contract-scope field in
            # test/TestSetup.sol:294 — and NOTHING references it. No Ether.fi source states what it
            # buys back. It is an unverified LEAD, not an address, and wiring it would put a number
            # in the sheet with no defensible meaning. If a buyback executor is genuinely needed,
            # query the AddressProvider above for a registered name first.
        },
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "destination_effect": "yield_payout",
        "destination_source_url": "https://etherfi.gitbook.io/etherfi",
        "destination_confirmed_date": "2026-09-14",
        # A CAP IS NOT A SPEND. Applies here and generally: a stated "$50m authorized" is a CEILING
        # granted by governance, not a transaction that happened. Any stored figure that traces to
        # an authorisation amount rather than to a confirmed executed buyback must be labelled
        # "authorized, not confirmed executed" — otherwise a permission reads as a purchase.
        "authorization_is_not_execution": {
            "rule": "an authorized programme CAP is not proof of executed spend",
            "effect": "any figure traced to an authorisation is labelled 'authorized, not confirmed "
                      "executed' rather than treated as realised",
            "applies_to": ["actual_buyback_usd", "actual_buyback_tokens"],
        },
        "dune_queries": {
            # Mapped from the probe of query 8683038: THIRTEEN columns, 794 rows, dated by `day`
            # ("2026-09-10 00:00:00.000 UTC"). It is a DAILY HISTORY like any other backfill —
            # an earlier reading of it as a current-state snapshot came from a partial column
            # list and was wrong, so Ether.fi's 3/6/9-month trajectory comes from real history,
            # not from points accumulating forward from install.
            "locked_tokens": {
                "query_id": 8683038,
                "date_col": "day",
                "value_col": "staked_supply",
                "granularity": "daily",
                # No drop_current_period: staked_supply is a STOCK, and a stock read part-way
                # through a day is a valid reading of it. Dropping the current period is for
                # FLOWS, where an incomplete period understates the total.
                # agg_14 and agg_30 are 14- and 30-day aggregates. They are a CANDIDATE for the
                # 30-day trajectory column and are captured to the staging table so the choice can
                # be made on real numbers — but nothing reads them, and the trajectory columns are
                # NOT switched to them. That switch is a decision, not a default.
                "staging_cols": ["agg_14", "agg_30"],
                "staging_note": "14d/30d aggregate from Dune 8683038. Candidate for the 30-day trajectory "
                                "column; NOT used in any figure. Switching to it is a deliberate change.",
                # Not mapped, and deliberately so: deposit_amount, deposit_users, request_amount,
                # request_users, processed_amount and processed_users are withdrawal-queue flows.
                # No metric in the library takes them, they read as inert (see quality_warning),
                # and inventing a metric to hold a column is how a sheet fills up with numbers
                # nobody chose.
                "quality_warning": {
                    "label": "the FLOW half of query 8683038 looks inert",
                    "reason": "In every sample row the deposit/request/processed amount and user columns are 0, "
                              "and agg_14/agg_30 are floating-point noise (-5.8e-10, -2.4e-09) rather than "
                              "values. Either there has genuinely been no vault activity, or the "
                              "get_vault_details CTE is not matching rows — it filters strategy_symbol = "
                              "'sethfi' in lowercase, a plausible case-sensitivity mismatch. Until that is "
                              "settled, agg_14/agg_30 must NOT be promoted to the 30-day trajectory column: "
                              "a trajectory built on noise around zero would read as a flat, healthy series.",
                    "suggestion": "Check the strategy_symbol casing in the get_vault_details CTE on "
                                  "dune.com/queries/8683038 against the underlying table, and confirm whether "
                                  "vault activity is genuinely zero over the period. The LOCK-RATE half of "
                                  "this query (staked_supply, perc_staked, num_holders) is unaffected and is "
                                  "trusted — this warning is about the flow columns only.",
                },
                "source_url": "https://dune.com/queries/8683038",
                "note": "Staked sETHFI, recorded as locked_tokens (not staked_tokens) as instructed. "
                        "141,470,107.5 sETHFI as at 2026-09-10. Tier 4 is Ether.fi's ONLY automated route "
                        "for this figure — there is no contract read for it — so once the backfill has run, "
                        "the standard tier 4 skip leaves the series static until the next explicit re-pull. "
                        "See OPEN_QUESTIONS.",
            },
            "lock_rate_pct": {
                "query_id": 8683038,
                "date_col": "day",
                # perc_staked, NOT perc_staked_cnt. The two are the same measure on different
                # scales — 0.17452 against 17.452 in every sample row — and this project stores
                # percentages as FRACTIONS, displayed with a 0.0% format. Taking the _cnt column
                # would put a figure 100x too large in the sheet while looking entirely plausible.
                # The sanity bound below is the backstop: 17.452 fails it and would be rejected to
                # the Review Queue rather than stored.
                "value_col": "perc_staked",
                "granularity": "daily",
                "source_url": "https://dune.com/queries/8683038",
                "note": "Share of ETHFI staked, as published, stored as a fraction (0.17452 = 17.452%). "
                        "Both earlier ambiguities are resolved: the scale is a fraction, and '_cnt' marks "
                        "the x100 variant of the same measure rather than a holder-count basis.",
            },
            "staker_count": {
                "query_id": 8683038,
                "date_col": "day",
                "value_col": "num_holders",
                "granularity": "daily",
                "source_url": "https://dune.com/queries/8683038",
                "note": "sETHFI holders, 13,011 as at 2026-09-10. A demand-side datapoint, not a supply one: "
                        "it is never used in any float, lock-rate or net-supply calculation.",
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
    # A period with a KNOWN but undocumented change inside it cannot resolve to one share, even
    # if a share has been filled in. Without this, documenting a single number for a span that
    # really contained two regimes would lift the suppression on a wrong figure.
    if period.get("known_change"):
        return {**base, "share_to_buyback": None, "status": "unconfirmed",
                "why": f"a documented change happened inside this period and has not been resolved: "
                       f"{period['known_change']}"}
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
# UNAVAILABLE — figures that were CHASED AND CLOSED. Not gaps, and not silent absences.
#
# There are three different states a missing figure can be in, and collapsing them is how a
# team re-litigates the same dead end every few months:
#
#   a GAP          nobody has sourced it yet. It belongs on the to-do list.
#   UNAVAILABLE    somebody tried, the attempts are recorded, and there is no route. It does
#                  NOT belong on the to-do list — a permanent entry there trains the reader
#                  to skim past the list.
#   silent         the worst of the three: the cell is blank and nobody knows whether that is
#                  a gap, a closure, or a bug.
#
# An entry here SUPPRESSES the Gap Report row for that (project, metric), renders as a closed
# item on Config & Sources, and makes the dependent workbook cells say "none available" rather
# than "n/a" — so an absent second opinion never reads as missing data. `what_was_tried` is the
# load-bearing field: it is what stops the next person repeating the work.
# =======================================================================================
UNAVAILABLE = [
    # ---------------------------------------------------------------- World Mobile
    # SUPPRESSED FOR A DESTINATION REASON, NOT A DATA REASON. The mechanism is confirmed; where
    # the tokens GO is not, and the three candidates have opposite signs on float.
    {
        "project": "World Mobile", "metric": "actual_buyback_tokens",
        "closed_on": "2026-09-14",
        "summary": "Buyback destination is undocumented, so a token figure cannot be given a meaning.",
        "what_was_tried": (
            "The MECHANISM is confirmed: fiat telecom revenue buys WMTx on exchanges. No World Mobile "
            "material on file states what happens to the bought tokens — burned, held by the treasury, or "
            "redistributed. Their own metrics page is being rebuilt and does not cover it."),
        "impact": (
            "The IMPLIED buyback from the revenue side is unaffected and still computes. What is suppressed "
            "is the actual figure, because storing a token count without its destination would let it be "
            "netted against emissions like a burn (removes supply), counted as locked (reduces float), or "
            "ignored (returns to float) — three answers with three different signs."),
        "reopen_if": (
            "World Mobile publishes what happens to repurchased WMTx, or an on-chain destination address "
            "appears. A single sentence naming the destination closes this."),
    },
    {
        "project": "World Mobile", "metric": "actual_buyback_usd",
        "closed_on": "2026-09-14",
        "summary": "Same as actual_buyback_tokens: the destination is undocumented.",
        "what_was_tried": "See the actual_buyback_tokens entry — the same single missing fact blocks both.",
        "impact": "Implied buyback still computes from revenue. The actual USD figure is suppressed.",
        "reopen_if": "World Mobile states the destination of repurchased WMTx.",
    },
    # ---------------------------------------------------------------- Fluid
    # A PERMANENT GAP, ACCEPTED. Five independent sources checked, none publishes the address.
    {
        "project": "Fluid", "metric": "actual_buyback_tokens",
        "closed_on": "2026-09-14",
        "summary": "The Fluid Reserve address has never been disclosed by Fluid, in any source.",
        "what_was_tried": (
            "Five sources, all checked 2026-09-14 and none carrying an address: (1) Fluid's own blog "
            "announcement of The Fluid Reserve; (2) Messari's report on it; (3) the official X "
            "announcement; (4) Instadapp/fluid-contracts-public — README.md and docs/docs.md, whose only "
            "addresses are the native-ETH sentinel 0xEeee...EEeE; (5) Instadapp/fluid-governance — README "
            "and docs. The buyback is real and ACTIVE (~$3.2m spent Oct-Dec 2025); the destination address "
            "simply is not public."),
        "impact": (
            "NONE on whether the buyback exists or its size — both are known from Fluid's own reporting. "
            "What is lost is the on-chain confirmation of it. The IMPLIED buyback from the revenue side is "
            "unaffected and remains the figure used."),
        "reopen_if": (
            "Fluid publishes the Reserve address, or a governance proposal names it. Do NOT re-run the "
            "five sources above — they are closed."),
    },
    {
        "project": "Fluid", "metric": "actual_buyback_usd",
        "closed_on": "2026-09-14",
        "summary": "Same as actual_buyback_tokens: the Reserve address was never disclosed.",
        "what_was_tried": "See the actual_buyback_tokens entry — five sources, none carrying an address.",
        "impact": "Implied buyback still computes. ~$3.2m spent Oct-Dec 2025 is known from Fluid's own "
                  "reporting but cannot be confirmed on-chain.",
        "reopen_if": "Fluid publishes the Reserve address.",
    },
    {
        "project": "Aerodrome", "metric": "locked_tokens_dashboard",
        "closed_on": "2026-09-12",
        "summary": "No second opinion on veAERO locked. The tier 2 contract read is unaffected.",
        "what_was_tried": (
            "Dune query 2986047 was the only candidate. (1) API: the results endpoint returned HTTP 404. "
            "(2) API control comparison: the same query and a control id that certainly does not exist "
            "(999999999) returned the IDENTICAL response — 404 'not found: Query not found or private' — "
            "so the API cannot distinguish deleted from private. (3) Browser, signed in, 2026-09-12: "
            "'This query is private or doesn't exist'. Private and deleted remain indistinguishable and "
            "either way it is unusable."),
        "impact": (
            "NONE on the figure itself. locked_tokens for Aerodrome comes from the tier 2 read of "
            "AERO.balanceOf(escrow) at 0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4 on Base, which is "
            "verified and working. What is lost is the second opinion on it, not the number."),
        "reopen_if": (
            "Somebody publishes a veAERO locked figure on a page we may scrape, or writes a Dune query we "
            "own. Do NOT re-attempt 2986047 itself: three independent checks have closed it."),
    },
    {
        "project": "Aerodrome", "metric": "locked_tokens",
        "kind": "history",          # the figure is fine; its HISTORY is what is unavailable
        "closed_on": "2026-09-12",
        "summary": "Current state only — no lock-rate history, so the 3/6/9-month trajectory accumulates "
                   "forward from today rather than backfilling.",
        "what_was_tried": (
            "A contract read returns present state and nothing else; that is what a contract read is. The "
            "backfill would have come from Dune 2986047, which is closed as unusable (above)."),
        "impact": (
            "The Aerodrome lock-rate trajectory columns fill one run at a time from 2026-09-12. The CURRENT "
            "lock rate is correct from day one. This is a known limitation, deliberately NOT a gap: there is "
            "nothing to chase and nothing is broken."),
        "reopen_if": (
            "Somebody wants the history badly enough to write it: a Dune query aggregating AERO transfers "
            "into the escrow at 0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4 on Base would reconstruct it. "
            "A nice-to-have, not a blocker."),
    },
    {
        "project": "Sky", "metric": "burn_address_balance",
        "closed_on": "2026-09-14",
        "summary": "Sky has no burn ADDRESS to read. Not a wrong address — the wrong kind of question.",
        "what_was_tried": (
            "balanceOf(0x0000...0000) on SKY, from an address verified against Sky's own docs. It read "
            "exactly 0 while every peer on the identical read was non-zero, which looked like evidence of "
            "a wrong destination. It was not. The ChainSecurity Dss Flappers audit (July 2026) and Sky's "
            "dss-flappers repo establish that Sky's burn leg is a Splitter feeding a Flapper that trades "
            "USDS for the gem on UniswapV2 and sends proceeds to a CONFIGURABLE RECEIVER — and in the "
            "FlapperUniV2 variant deposits the gem back into the pool as LP tokens. No dead address is "
            "involved anywhere. The zero was correct and expected."),
        "impact": (
            "Sky has no burn figure, and that is the correct state — better than a zero that looks "
            "measured. It is NOT a permanent loss: a real figure becomes possible once the active Flapper "
            "variant is known, which is an OPEN QUESTION rather than a closed one. Note that the earlier "
            "April-2026 buyback-cut theory never explained this zero and should not be revived: a "
            "reduction in burning cannot produce a zero CUMULATIVE balance where burning once occurred."),
        "reopen_if": (
            "NEVER by substituting another address — no balance read of any address models this. The "
            "receiver is configurable and may hold LP tokens rather than SKY. The MCD Pause Proxy is the "
            "lead worth following (a documented Splitter transaction shows the allocation going to 'Burn — "
            "MCD Pause Proxy' and 'Sky Rewards'), but the audit warns that if the Pause Proxy is the "
            "receiver and governance does not control it, LP tokens can be lost or seized — a warning that "
            "only makes sense if the receiver HOLDS tokens rather than destroying them. So a Pause Proxy "
            "balance may be a TREASURY HOLDING, not a burn, and it is not added here on that basis."),
    },
]

# Two kinds, kept strictly apart. "figure" means the number itself has no route, so the Gap
# Report row is suppressed and dependent cells read "none available". "history" means the
# CURRENT number is fine and only its past is missing — suppressing that metric's gap row would
# hide a real failure if the live read ever broke, so it never does.
UNAVAILABLE_BY_KEY = {(u["project"], u["metric"]): u for u in UNAVAILABLE
                      if u.get("kind", "figure") == "figure"}
LIMITATION_BY_KEY = {(u["project"], u["metric"]): u for u in UNAVAILABLE
                     if u.get("kind") == "history"}


def unavailable_for(project_name: str, metric: str) -> dict | None:
    """The closure record for a FIGURE that was chased and has no route, or None.

    Never returns a history-only limitation: that metric still has a live value, and treating it
    as unavailable would suppress a gap row that must appear if the live read ever fails.
    """
    return UNAVAILABLE_BY_KEY.get((project_name, metric))


def limitation_for(project_name: str, metric: str) -> dict | None:
    """A recorded limitation on a figure that IS available — today's value is fine, its past is not."""
    return LIMITATION_BY_KEY.get((project_name, metric))


# =======================================================================================
# OPEN QUESTIONS — things a human must resolve that are not "a metric has no data".
#
# These always appear in the Gap Report so they cannot be forgotten. Each names what is
# unresolved, why it matters, and what would settle it. Delete an entry once it is settled
# and the corresponding config change is made.
# =======================================================================================
OPEN_QUESTIONS = [
    # ---------------------------------------------------------------- GEODNET
    {
        "project": "GEODNET", "topic": "WORMHOLE NTT BRIDGE MODEL — lock-and-mint or burn-and-mint?",
        "severity": 1,
        "reason": "GEODNET bridges between Polygon and Solana using WORMHOLE NTT, which supports BOTH "
                  "models, and which one is in use decides whether summing chain supplies is correct or a "
                  "double-count of the entire remote float. Under LOCK-AND-MINT the Polygon tokens stay "
                  "outstanding while Solana tokens are minted against them, so Polygon + Solana + IoTeX "
                  "counts the same tokens two or three times. Under BURN-AND-MINT the tokens are destroyed "
                  "on the source chain and summing is correct. "
                  "This is not a small error either way: it is the difference between the right supply "
                  "figure and one inflated by a multiple. Until it is settled, only Polygon is read and "
                  "total_supply is labelled PARTIAL — which understates, but understates KNOWABLY.",
        "suggestion": "Check docs.geodnet.com for the NTT configuration, or read the NTT manager contract "
                      "directly — the mode is on-chain state. Then either add the Solana and IoTeX "
                      "deployments as summed components (burn-and-mint) or keep Polygon as the sole read "
                      "and record why (lock-and-mint). The same question applies to WMTx and to Aethir.",
    },
    {
        "project": "GEODNET", "topic": "P2 — is GEODNET archetype 3? Single-sourced, NOT added.",
        "severity": 2,
        "reason": "A secondary source (Solana Compass) states that 80% of network DATA revenue funds "
                  "buyback-and-burn. If true that is an archetype 3 block with a real revenue share. It is "
                  "NOT ADDED, because it is SINGLE-SOURCED and is not corroborated by GEODNET's own docs or "
                  "by any GIP. The existing burn_split on file carries the same 80% figure against "
                  "docs.geodnet.com, so the two may be the same claim reaching us twice rather than two "
                  "independent confirmations — which is exactly the kind of apparent corroboration that "
                  "should not be treated as evidence.",
        "suggestion": "Confirm from docs.geodnet.com or vote.geodnet.com. If a GEODNET-authored source "
                      "states the revenue share and the buyback-and-burn route, add archetype 3 with that "
                      "source. Do not add it on Solana Compass alone.",
    },
    # ---------------------------------------------------------------- Maple
    {
        "project": "Maple", "topic": "WHERE is repurchased SYRUP actually held? The address on file was wrong.",
        "severity": 1,
        "reason": "The live read of 2026-09-14 returned 0.51253570332391 SYRUP from "
                  "0xa9466EaBd096449d650D5AEB0dD3dA6F52FD0B19, against ~75,780,000 SYRUP that Maple's own "
                  "transparency page reports for the Syrup Strategic Fund. "
                  "THE READ WAS CORRECT AND THE ADDRESS WAS WRONG. Decimals are excluded arithmetically — "
                  "75,780,000 / 0.51253570332391 = 1.4785e8, whose log10 is 8.1698 and therefore not an "
                  "integer, while a decimals mismatch is always an exact power of ten — and structurally, "
                  "since scaled() reads decimals() from the same contract as the balance. The symbol gate "
                  "passed, so the token really is SYRUP. 0.51 is the genuine balance of that address. "
                  "IT IS THE WRONG ADDRESS BECAUSE OF AN INFERENCE, NOT A TYPO: Maple's transparency page "
                  "says the SSF is 'part of the Treasury', which was read as naming a contract, and the "
                  "only registry entry called `treasury` sits under SINGLETONS beside globals and "
                  "feeManager — the v2 PROTOCOL FEE treasury, which takes fees in pool assets "
                  "(USDC/USDT/USDG), not SYRUP. 'The Treasury' on a transparency page means the DAO's "
                  "holdings in the accounting sense and need not be a single address. "
                  "THE DANGER WAS THE SHAPE OF THE NUMBER: 0.51 is small, precise and non-zero. A zero "
                  "would have looked broken; this would have flowed into every Maple archetype 3 "
                  "destination figure without tripping a guard. The contract is now marked "
                  "destination_status 'disputed' so it is read as evidence and stored as nothing.",
        # TWO ROUTES ATTEMPTED 2026-09-14 AND BOTH CLOSED. Recorded so nobody re-walks them.
        #
        # (1) THE REGISTRY'S OWN COMMENTS AND SECTION HEADERS — closed by construction.
        #     MapleAddressRegistryETH.sol opens with "WARNING: File generated automatically, do not
        #     edit manually" and is generated from address-registry.json, which holds only
        #     {name, address} pairs with NO descriptions of any kind. The word "buyback" appears
        #     ZERO times across every maple-labs source reachable; "treasury" appears only as the
        #     bare name in the Singletons section. The registry can say what `treasury` is NOT —
        #     it is not in the syrupToken section — but it cannot say where buybacks land, and it
        #     never will, because it carries no prose at all.
        #     Also checked and carrying nothing: maple-labs/syrup-utils (SyrupDrip, a Merkle
        #     airdrop distributor; SyrupUserActions; SyrupRouter; SyrupRateProvider — no treasury
        #     contract among them) and maple-labs/maple-docs.
        #
        # (2) THE GOVERNANCE FORUM, for the MIP-019 execution transaction — UNREACHABLE, not
        #     closed. community.maple.finance, maple.finance and syrup.gitbook.io all return
        #     HTTP 000 through this environment's proxy; only raw.githubusercontent.com resolves.
        #     This route is still the most likely to work and should be tried from a normal
        #     network. An execution transaction would name the destination directly, which is the
        #     strongest form of answer available.
        "routes_closed": ["maple-labs/address-registry comments and section headers — file is "
                          "auto-generated with no prose, and its JSON source has no descriptions"],
        "routes_blocked": ["community.maple.finance — unreachable from this environment, not "
                           "exhausted. Try it from a normal network."],
        "suggestion": "THE GOVERNANCE FORUM IS THE ROUTE THAT REMAINS: find the MIP-019 execution "
                      "transaction on community.maple.finance, which names the destination directly. "
                      "A SYRUP holder list showing a ~75.78m holder would identify it just as well. "
                      "FOUR CANDIDATES ARE VISIBLE IN THE REGISTRY AND NONE MAY BE CHOSEN ON A NAME "
                      "MATCH — that is precisely how 0.51 got here: daoMultisig "
                      "0xd6d4Bcde6c816F17889f1Dd3000aF0261B03a196 (Actors section, newly surfaced and "
                      "arguably the most plausible, which is exactly why it is not being picked), "
                      "syrupRecapitalizationModule 0x5dfe0460f66fa06bFCbB3211e723556be6B3f69D, "
                      "governorTimelock 0x2eFFf88747EB5a3FF00d4d8d0f0800E306C0426b, and syrupDrip "
                      "0x509712F368255E92410893Ba2E488f40f7E986EA. Whichever is chosen, confirm it "
                      "by reading SYRUP.balanceOf on it FIRST and checking the answer is in the tens "
                      "of millions before wiring it. "
                      "SECOND, AND INDEPENDENTLY WORTH DOING: the maple.finance/transparency entry in "
                      "sources.yaml is now enabled:true but still needs its `anchor` — one label "
                      "string from the page. Until it has one the cross-check cannot fire, and the "
                      "next address would be trusted on plausibility alone.",
    },
    # ---------------------------------------------------------------- Pendle
    {
        "project": "Pendle", "topic": "does sPENDLE.totalSupply() include boosted and virtual balances?",
        "severity": 1,
        "reason": "THE ADDRESS IS RIGHT AND THE READ SUCCEEDS — what is unresolved is what the number "
                  "means. vePENDLE holders who converted received a BOOSTED sPENDLE balance of up to 4x, "
                  "decaying over roughly two years, and Pendle's docs separately describe a 'virtual "
                  "sPENDLE balance' used for voting power. If either is inside totalSupply(), the lock rate "
                  "OVERSTATES real PENDLE staked by as much as 4x. "
                  "A 4x error on the lock rate is not a rounding question: it is the difference between a "
                  "lock rate that supports the thesis and one that does not. locked_tokens is forced AMBER "
                  "until this is settled, and the figure should be read as an UPPER BOUND.",
        "suggestion": "Direct contract inspection — read sPENDLE's totalSupply() implementation and check "
                      "whether it includes boosted or virtual balances, or compare totalSupply() against "
                      "PENDLE.balanceOf(sPENDLE). If the two diverge materially, the balanceOf figure is "
                      "the real staked amount and the read should switch to escrow_balance_of. Do NOT "
                      "guess at this from the docs.",
    },
    # ---------------------------------------------------------------- Near
    {
        "project": "Near", "topic": "protocol_reward_rate reads [0, 1] — genuine zero, or a fallback?",
        "severity": 1,
        "reason": "Our live on-chain read of NEAR's protocol_reward_rate returned [0, 1] — exactly zero, "
                  "against secondary sources that all say 90% validators / 10% protocol treasury. "
                  "THE PROBLEM IS THAT [0, 1] IS BYTE-IDENTICAL TO NEARCORE'S SERDE DEFAULT for that field "
                  "(core/chain-configs/src/genesis_config.rs:168, "
                  "`#[default(Rational32::from_integer(0))]`), so a genuine on-chain zero and a read that "
                  "never reached chain state produce the SAME VALUE and cannot be told apart from the value "
                  "alone. Source inspection cannot settle it either: nearcore does not vendor mainnet "
                  "genesis — every path under core/chain-configs/res/ 404s — and the only [1, 10] values in "
                  "the repository are inside TEST FIXTURES using `test.near` with epoch_length 60. "
                  "THE LIVE READ IS USED, because it is the only direct chain evidence we hold. It is "
                  "flagged uncertain rather than quietly overridden by secondary sources, which would be "
                  "preferring a story to a measurement.",
        "suggestion": "Fetch genesis_config from a NEAR-operated RPC or archive and read the field, or "
                      "repeat the read against a SECOND independent endpoint. Two independent endpoints "
                      "returning zero settles it; one returning [1, 10] means our read was falling through "
                      "to the default and the treasury share is 10%.",
    },
    # ---------------------------------------------------------------- Fluid
    {
        "project": "Fluid", "topic": "does a FLUID staking or lock mechanism exist at all?",
        "severity": 2,
        "reason": "Instadapp/fluid-contracts-public's own technical docs (docs/docs.md) contain ZERO "
                  "mentions of stake, lock, veFLUID, governance or emission — consistent with no mechanism "
                  "existing. But this is ABSENCE OF EVIDENCE, NOT EVIDENCE OF ABSENCE: that document scopes "
                  "itself to the Liquidity layer and the Vault protocol, so a staking contract could simply "
                  "be outside its scope. Removing the locked_tokens metric on this basis would be making "
                  "the STRONGER claim on the WEAKER evidence, so the metric stays and is flagged instead.",
        "suggestion": "Look for a positive statement in Fluid's own tokenomics material, or a "
                      "registry-style contract listing. Either a confirmation that staking exists (wire it) "
                      "or a Fluid-authored statement that it does not (then remove the metric and record "
                      "the closure). Do not remove it on the docs' silence.",
    },
    {
        "project": "Sky", "topic": "FOR REVIEW — do BOTH legs of Sky's 55/45 split return value to the protocol?",
        "severity": 1,
        "reason": "THE RECEIVER QUESTION IS ANSWERED and this is what it leaves behind. The Smart Burn "
                  "Engine's receiver is the MCD Pause Proxy, Sky's governance-controlled treasury — so "
                  "the 55% 'buyback' leg does not retire supply, it moves SKY into a treasury that "
                  "governance can and does spend from (there are votes moving SKY back out of it). The "
                  "45% leg goes to LSSKY stakers, which IS a return to holders. "
                  "SO THE TWO LEGS ARE NOT WHAT THEY LOOK LIKE: one returns value to holders, the other "
                  "returns it to the protocol. Sky's archetype 3 number — revenue times the buyback "
                  "share — measures money routed to the treasury, not money returned to holders, and "
                  "comparing it against protocols that burn or distribute compares different things. "
                  "This is a JUDGEMENT about what the figure means, not a data error, so it is raised "
                  "for review rather than resolved in config.",
        "suggestion": "Decide whether Sky's archetype 3 figure should be presented as a buyback at all, "
                      "or relabelled as treasury accumulation. If the latter, the 55% share stops being "
                      "comparable with Uniswap's or Venice's buyback and should be excluded from any "
                      "cross-project buyback ranking. destination_effect is already "
                      "treasury_redeployable, so the workbook will not net it against emissions — what "
                      "is open is how the archetype 3 number is LABELLED.",
    },
    {
        "project": "Sky", "topic": "the flapper address is 2024 configuration, not confirmed current state",
        "severity": 1,
        "reason": "The vote that set FlapperUniV2SwapOnly is dated 2024-09-27, and SBEBeam now lets "
                  "facilitators reconfigure the splitter within governance bounds. So the recorded "
                  "flapper, want, pip and pair are CONFIGURATION AT THAT TIME and are stored in "
                  "governance_parameters with status 'point_in_time' rather than as current facts. "
                  "Treating a two-year-old vote as current state is the same error as treating a "
                  "verified address as a verified mechanism: the document was accurate and may no longer "
                  "describe what is deployed.",
        "suggestion": "PARTIALLY ANSWERED by the 2026-09-14 on-chain read, and the answer is that "
                      "reconfiguration HAS happened: pair() still matches the vote, but pip() reads "
                      "0xc2ffbbdccf1466eb8968a846179191cb881ecdff where the vote set "
                      "0x61A12E5b1d5E9CC1302a32f0df1B5451DE6AE437. Both are updated with their real "
                      "status. STILL OUTSTANDING: want() and spotter() returned RPC 525 and must be "
                      "retried, and the flapper address itself is still only the 2024 vote's — read the "
                      "SPLITTER's flapper() to confirm it is still 0x374D9c3d..., because a different "
                      "flapper could be FlapperUniV2 and would reopen the LP question.",
    },
    {
        "project": "Venice AI", "topic": "the 20% protocol take on locked sVVV yield is not captured",
        "severity": 2,
        "reason": "Locked sVVV earns 80% of the normal emission yield and Venice retains the remaining "
                  "20% as protocol revenue. That is a revenue line this model does not capture at all, "
                  "and it is structurally different from the fee revenue already tracked: it accrues in "
                  "VVV rather than dollars, and it scales with the locked share rather than with usage. "
                  "Understating revenue understates every derived buyback figure computed from it.",
        "suggestion": "Establish the size of the 20% take from Venice's own material and add it to "
                      "revenue_sources with booked=True only if it is actually realised rather than "
                      "merely accrued. Related figures worth recording at the same time: ~70% of "
                      "circulating VVV is staked, ~25% of that locked for DIEM, and unstaking has a "
                      "7-day cooldown — which belongs in effective float, since tokens in cooldown are "
                      "neither liquid nor locked.",
    },
    {
        "project": "Venice AI", "topic": "is sVVV 1:1 with deposited VVV?",
        "severity": 2,
        "reason": "The lock-rate read assumes the staking contract's balance maps 1:1 to VVV deposited. "
                  "If sVVV is a yield-bearing receipt that accrues — a share price rather than a "
                  "receipt — then its supply overstates the VVV actually locked, and the lock rate with "
                  "it. This is the same class of error as reading a veNFT's totalSupply as tokens "
                  "locked: a plausible number, wrong by a drifting factor.",
        "suggestion": "Confirm from Venice's docs that sVVV is 1:1 with VVV deposited. If it is not, the "
                      "read needs the underlying balance rather than the receipt's supply — see "
                      "LOCK_READ_METHODS and the escrow_balance_of pattern used for veAERO.",
    },
    {
        "project": "Uniswap", "topic": "RESOLVED — the zero was the wrong address, not a genuine absence of burn",
        "severity": 2,
        "reason": "KEPT AS A CORRECTION RATHER THAN DELETED, because the wrong reasoning was nearly "
                  "believed and would otherwise be re-derived. The Fire Pit balance read 0 and that was "
                  "explained as 'plausible, since Uniswap's burn is holder-elected and perhaps nobody "
                  "elected'. THAT REASONING WAS WRONG. The burns are publicly reported — 134,000 UNI on "
                  "2026-06-05, a record; 106,000 UNI on an ordinary day in July 2026 — on top of the "
                  "December 2025 retroactive burn of 100,000,000 UNI. Holder-election explains a SMALL "
                  "figure. It can never explain a zero against reported burns, and reaching for a story "
                  "that makes a suspicious number acceptable is how a wrong address survives. The actual "
                  "cause: balanceOf was called on the Firepit, the contract release() is CALLED ON, "
                  "rather than on 0x...dEaD where the UNI lands. Fixed; the mechanism is now confirmed "
                  "and a config check (_check_burn_destinations) rejects the same mistake anywhere else.",
        "suggestion": "Nothing to do on the mainnet figure. What remains open is UNICHAIN: its burn path "
                      "is separate, its bridged UNI token address is still unknown, and the mainnet "
                      "dead-address figure therefore UNDERSTATES the total and is marked PARTIAL. See the "
                      "open question on the Unichain UNI address — that, not this, is the live item.",
    },
    {
        "project": "Sky", "topic": "the April 2026 buyback reduction is not in the fee-split history",
        "severity": 1,
        "reason": "The history had ONE undocumented period covering everything before 2026-08-13. The "
                  "public record says that span contained at least two materially different regimes: the "
                  "pre-April state, and the post-April state after buybacks were cut by a reported ~87%. "
                  "No figure is wrong TODAY — every window touching that span is already suppressed as "
                  "unconfirmed. The risk is the day somebody documents a single pre-August number: it "
                  "would be applied across the cut and the suppression would lift on a figure that looks "
                  "entirely reasonable. The period is therefore now SPLIT IN TWO, both unconfirmed, and "
                  "the later one carries known_change so it stays unconfirmed even if a share is filled "
                  "in. NO NUMBER HAS BEEN INVENTED, and the 2026-04-30 boundary is a month-end "
                  "placeholder, not an established event date.",
        "suggestion": "Two things to document, from Sky's own governance record: the DATE the April 2026 "
                      "overhaul took effect (replacing the placeholder boundary), and the burn/distribute "
                      "split that applied between then and the 2026-08-13 Executive Proposal. With both, "
                      "clear known_change on that period and set its share. config.validate_config() "
                      "rejects a period that carries a share while known_change is still unresolved.",
    },
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
                  "The current 55% is deliberately NOT applied retroactively. NOTE: that span is now TWO "
                  "periods, not one — see the April 2026 buyback reduction question, which is the harder "
                  "half of this and must be settled first. One number for the whole span would be wrong.",
        "suggestion": "Document each period separately in Sky fee_split.history: pre-overhaul, and "
                      "post-overhaul to 2026-08-12. Do not collapse them into one.",
    },
    {
        "project": "Venice AI", "topic": "RESOLVED — mechanism confirmed; what is left is the burn ADDRESS",
        "severity": 2,
        "reason": "THE MECHANISM QUESTION IS CLOSED. Venice burns by dead-address transfer, confirmed from "
                  "Venice's own material: a discretionary monthly channel (a Safe funded with USDC, spent "
                  "hourly by CoW Protocol's TWAP engine buying VVV on Aerodrome, ~$100k/month since Nov "
                  "2025) and a programmatic per-subscription channel since 2026-04-26. Venice is therefore "
                  "like Uniswap, not like Sky. "
                  "WHAT REMAINS is the address: buy_and_burn (0x35fb3b...) stays UNCORROBORATED, and none "
                  "of the confirming sources names a contract. The Safe-plus-CoW-TWAP architecture "
                  "suggests there may be no single stable burn contract to point at — the buying is done "
                  "by an off-the-shelf execution engine, not by a Venice contract. That is a different "
                  "shape of problem from a wrong address and should not be treated as one.",
        "suggestion": "Two things settle it, and the second is free. (1) Ask Venice, or find in their "
                      "material, which address the TWAP engine sends VVV to. (2) Watch the balance: at "
                      "~$100k/month plus per-subscription burns, the address we already read must "
                      "visibly rise. If it does, it IS the destination and the reconciliation question "
                      "in burn_composition resolves to reading (a). If it never moves, the burns are "
                      "landing somewhere else — the Uniswap error — and the address is wrong.",
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
        "project": "Uniswap", "topic": "CLOSED 2026-09-14 — the Unichain UNI address is no longer wanted",
        "severity": 3,
        "reason": "THIS QUESTION DISSOLVED RATHER THAN BEING ANSWERED, and the reason is worth keeping so it is "
                  "not reopened as a to-do. The ask was for the bridged UNI address on Unichain, so the Unichain "
                  "TokenJar and Firepit could read and the PARTIAL marking could clear. Uniswap's own source says "
                  "that would have been WRONG: src/releasers/OptimismBridgedResourceFirepit.sol burns bridged UNI "
                  "on L2 and bridges a withdrawal to L1, where the UNI reaches 0xdead only after the OP Stack "
                  "7-day challenge period. A Unichain burn and its mainnet arrival are THE SAME BURN a week "
                  "apart, so reading both would have double-counted every one of them with a seven-day offset "
                  "that looks like fresh activity. "
                  "The Unichain entries were REMOVED, the mainnet dead-address balance is the WHOLE figure, and "
                  "the PARTIAL marking was cleared because nothing is missing.",
        "suggestion": "NO ACTION. Do not add Unichain contracts to Uniswap. If a per-chain burn breakdown is ever "
                      "wanted, it needs a model that nets the bridge transit rather than summing both sides — "
                      "that is a new figure, not a completion of this one.",
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
        "project": "Pendle", "topic": "CLOSED 2026-09-14 — sPENDLE symbol confirmed; the OPEN risk moved",
        "severity": 3,
        "reason": "The expected symbol is CONFIRMED sPENDLE, so the symbol gate will no longer block a correct "
                  "address. THE REAL RISK MOVED RATHER THAN CLEARING: the address, the read method and now the "
                  "symbol are all settled, and what is unresolved is what the NUMBER MEANS — see the open "
                  "question on boosted and virtual balances, which forces locked_tokens AMBER.",
        "suggestion": "NO ACTION on the symbol. Work the boost question instead.",
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
        "severity": 3,
        "reason": "NARROWED 2026-09-14, and confirmed from Uniswap's own source rather than inferred. "
                  "src/base/ResourceManager.sol declares `uint256 public threshold;` with `setThreshold()` gated "
                  "on onlyThresholdSetter — it is governance STORAGE, not a constant, so there is no published "
                  "number to record and anything derived from it stays suppressed. "
                  "ONE NUMBER DOES EXIST AND IT IS NOT MAINNET'S: the 2000 UNI figure in "
                  "Uniswap/protocol-fees README.md sits under 'Cross-Chain UNI Burn (OP Stack L2s)' and is the "
                  "BRIDGED-FIREPIT configuration. Hardcoding it as mainnet's threshold would be a plausible "
                  "wrong number, which is the failure this project exists to avoid.",
        "suggestion": "A LIVE threshold() READ on the mainnet Firepit, 0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721 "
                      "— not a documentary search, which has been done. Record the result with its date under "
                      "Uniswap governance_parameters.release_threshold_uni, and re-read it periodically: "
                      "governance can move it at any time.",
    },
    {
        "project": "GEODNET", "topic": "the burn backfill has NEVER RUN — it was skipped, not successful",
        "severity": 1,
        "reason": "CORRECTION to an earlier report that read this as a success because it logged no error. "
                  "The read-back shows 1 row stored, dated today, sourced chain:polygon:burn_polygon — the "
                  "TIER 2 contract read. Dune contributed 0 rows and its run-log line said the store already "
                  "held history, so the backfill was skipped. That is the backfill-only rule working as "
                  "designed (tier 4 is a backfill dependency, and the store did hold a row), but it means "
                  "GEODNET's burn history has never been fetched: the 3/6/9-month trajectory has nothing "
                  "behind it. It also means the question of whether the pre-August-2026 snapshot table is "
                  "still queryable remains COMPLETELY UNTESTED — the 'before 2026-08-01: 0 rows' result "
                  "says only that no Dune call was made.",
        "suggestion": "Run once with TOKEN_METRICS_DUNE_ALWAYS=1 in .env, which forces tier 4 regardless of "
                      "what the store holds. It is additive: the store upserts on (date, project, metric), so "
                      "nothing is lost or overwritten except a row with an identical key, and the tier 2 read "
                      "is protected twice over — the collision guard keeps the earlier tier on a shared date, "
                      "and the period-overlap guard drops a monthly Dune row for any month the contract read "
                      "already covers. Then confirm with `python dune_probe.py --stored "
                      "GEODNET/gross_burn_tokens --before 2026-08-01`: a non-zero 'before' count is the "
                      "snapshot table answering, and 'from Dune: 0' would mean it was skipped again.",
    },
    {
        "project": "Ether.fi", "topic": "a tier-4-only series stops moving after its backfill",
        "severity": 1,
        "reason": "Query 8683038 is a DAILY HISTORY (794 rows dated by `day`), so it backfills like any "
                  "other tier 4 source and the 3/6/9-month trajectory comes from real history. But tier 4 "
                  "is a BACKFILL dependency by design: once the store holds a series, it is skipped. For "
                  "every other project that is right, because a tier 2 contract read or a tier 3 page keeps "
                  "the series current. Ether.fi has NEITHER for locked_tokens and lock_rate_pct — tier 4 is "
                  "the only automated route — so after the backfill both figures stay at their last "
                  "backfilled date and quietly go stale. The workbook marks a stale series, so this shows "
                  "rather than hides, but it is not fixed.",
        "suggestion": "Three options, in order of preference. (1) Find a contract read for staked sETHFI and "
                      "add it at tier 2, which is what every other project relies on. (2) Re-pull Dune on a "
                      "schedule with TOKEN_METRICS_DUNE_ALWAYS=1 — correct but pulls the whole history each "
                      "time. (3) Set \"ongoing\": True on these two config entries, which exempts them from "
                      "the backfill skip; the mechanism already exists and is tested. Do NOT pick (3) by "
                      "default: it makes a paid API a daily dependency, which the tier order exists to avoid.",
    },
    {
        "project": "Aerodrome", "topic": "2986047 — JAKE TO OPEN IN A BROWSER; no further automated attempts",
        "reason": "SETTLED as far as the API can settle it. The control comparison ran: query 2986047 and a "
                  "control id that certainly does not exist (999999999) return the IDENTICAL response, 404 "
                  "'not found: Query not found or private'. The Dune API therefore does NOT distinguish a "
                  "query that is gone from one that is private, so non-existence is not established — only "
                  "unreadability by this key. No amount of further API calls can separate the two, and none "
                  "should be made. For the record: this is only a CROSS-CHECK of the tier 2 "
                  "AERO.balanceOf(escrow) read, which is verified and working. Its absence costs a second "
                  "opinion, never the figure.",
        "suggestion": "Jake opens https://dune.com/queries/2986047 in a browser while signed in. If it "
                      "RENDERS, it exists and the API will not serve it to this key: fork it and supply the "
                      "fork's id, which goes in config under Aerodrome dune_queries.locked_tokens_dashboard. "
                      "If it 404s there too, it is gone and the cross-check is sourced elsewhere or dropped. "
                      "Until then this stays open and nothing further is attempted against the API.",
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


def _check_split_periods() -> list[str]:
    """A period with a known-but-unresolved change inside it must not also carry a share.

    The combination is not a typo, it is a category error: one share cannot describe a span that
    is known to contain two regimes. split_for_window suppresses it at runtime either way, but a
    number sitting in config that nothing uses is a trap for the next reader, who will reasonably
    assume it applies. Splitting the period into two is the fix.
    """
    errors = []
    for p in PROJECTS:
        for period in ((p.get("fee_split") or {}).get("history") or []):
            if period.get("known_change") and period.get("share_to_buyback") is not None:
                errors.append(
                    f"{p['name']}: the fee-split period {period.get('from')}..{period.get('to')} carries a "
                    f"share of {period['share_to_buyback']} AND an unresolved known_change "
                    f"({period['known_change'][:80]}...). One share cannot describe a span known to contain "
                    f"two regimes. Split the period at the change date and document each side, or drop the "
                    f"share and leave it unconfirmed.")
    return errors


def _check_burn_mechanisms() -> list[str]:
    """A transfer burn must SAY where that model came from. The Sky lesson, enforced.

    Sky's addresses passed every check this file makes, because every check was about the
    address. Nothing checked the claim attached to it — that the protocol burns by transferring
    to a dead address — and that claim turned out to be false. A project can no longer inherit
    that assumption by being shaped like the others: declaring burn_read_method "transfer"
    now requires declaring where the model is documented, even if the answer is "not yet".
    """
    errors = []
    for p in PROJECTS:
        # Both burn models now drive a DERIVED figure — issuance is the supply delta plus the burn
        # under a protocol burn and the delta alone under a transfer burn — so both must declare
        # where their model came from. An undeclared model silently picks a formula.
        # "protocol_api" WAS MISSING FROM THIS GATE UNTIL 2026-09-14, and that omission is how
        # Hyperliquid went months with a destination confirmed by two independent primary sources
        # while its mechanism silently read 'assumed'. A burn read through a protocol's own API is
        # still a burn with a model behind it; the READ ROUTE has nothing to do with whether the
        # MODEL is known. Any project reading a burn by any route must declare what it believes
        # the protocol does.
        if p.get("burn_read_method") not in ("transfer", "protocol_level", "protocol_api"):
            continue
        block = p.get("burn_mechanism")
        if not block:
            errors.append(
                f"{p['name']}: burn_read_method is {p.get('burn_read_method')!r} but there is no "
                f"burn_mechanism block. Issuance is DERIVED from it — supply delta plus burn, or delta "
                f"alone — so an undeclared model silently picks a formula. "
                f"'Burn means a transfer to a dead address' is an ASSUMPTION and must be declared as "
                f"one — see BURN_MECHANISM_MODELS. Add the block with status 'assumed' if it is not "
                f"yet sourced; that is a legitimate answer, silence is not.")
            continue
        if block.get("status") not in BURN_MECHANISM_STATUSES:
            errors.append(f"{p['name']}: burn_mechanism status {block.get('status')!r} is not one of "
                          f"{sorted(BURN_MECHANISM_STATUSES)}")
        if block.get("model") not in BURN_MECHANISM_MODELS:
            errors.append(f"{p['name']}: burn_mechanism model {block.get('model')!r} is not one of "
                          f"{sorted(BURN_MECHANISM_MODELS)}")
        if block.get("status") == "confirmed" and not (block.get("source_url") or block.get("source_note")):
            errors.append(f"{p['name']}: burn_mechanism is 'confirmed' with neither a source_url nor a "
                          f"source_note. Confirmed means somebody can say WHERE it came from — a URL "
                          f"normally, or a named non-URL provenance where there is no linkable "
                          f"document. Name one, or mark it 'assumed'.")
    return errors


def _check_burn_destinations() -> list[str]:
    """Under a dead-address model, the address we READ must be a dead address.

    The SECOND axis of the burn problem, and it needs its own check because it is independent of
    the first. Uniswap's mechanism was right — fees are claimed by burning UNI through
    Firepit.release(), and the UNI goes to 0x...dEaD — and the config still read the balance of
    the Firepit, which is the contract you CALL, not the address the tokens land at. A confirmed
    mechanism attached to an executing contract returns zero forever and looks like a protocol
    that never burns.

    So: on a transfer_to_dead_address model, an EVM burn_address_balance entry must point at a
    canonical dead address. Non-EVM sinks (GEODNET's Solana burn token account) are a different
    shape and are skipped; anything else needs destination_confirmed with a source saying why
    this particular address is where tokens come to rest.
    """
    errors = []
    dead = {a.lower() for a in BURN_ADDRESSES.values()}
    for p in PROJECTS:
        if burn_mechanism(p).get("model") != "transfer_to_dead_address":
            continue
        for key, spec in (p.get("contracts") or {}).items():
            if spec.get("kind") != "burn_address_balance":
                continue
            address = str(spec.get("address") or "")
            if not address.startswith("0x"):
                continue                      # a non-EVM sink is not a dead address by construction
            if address.lower() in dead or spec.get("destination_confirmed"):
                continue
            errors.append(
                f"{p['name']}: contract {key!r} is read as burn_address_balance under a "
                f"transfer_to_dead_address model, but {address} is not a canonical dead address. It is "
                f"likely the contract that EXECUTES the burn rather than the address tokens land at — "
                f"the Uniswap Firepit error, which returns 0 forever and reads as a protocol that never "
                f"burns. Point it at the dead address and keep the executing contract as kind "
                f"'burn_executor', or set destination_confirmed with a source if this address really is "
                f"where the tokens come to rest.")
    return errors


def _check_declared_shapes() -> list[str]:
    """Config blocks that CODE READS BY KEY must carry the keys that code reads.

    This exists because of a live crash. Canton's reference_values were written with "when" and
    "source_url" while check_reference_values reads "period" and "source" — a plain typo, invisible
    to every test, and it took down a nine-minute run at tier 4 with a KeyError. The synthetic
    fixture could not catch it: the fixture builds its own reference values, so it agreed with the
    code by construction rather than by checking what config actually holds.

    A dict consumed by key is an interface. These are the interfaces, checked at import.
    """
    errors = []
    for p in PROJECTS:
        name = p["name"]
        for ref in (p.get("reference_values") or []):
            missing = {"metric", "period", "value"} - set(ref)
            if missing:
                errors.append(
                    f"{name}: a reference_values entry is missing {sorted(missing)}. "
                    f"check_reference_values reads metric/period/value/source — 'period' is a "
                    f"YYYY-MM string. Got keys {sorted(ref)}.")
            if "period" in ref and not re.fullmatch(r"\d{4}-\d{2}", str(ref["period"])):
                errors.append(f"{name}: reference_values period {ref['period']!r} is not YYYY-MM, "
                              f"which is what it is compared against.")
        for metric, nc in (p.get("non_comparable") or {}).items():
            missing = {"why", "use_instead"} - set(nc)
            if missing:
                errors.append(f"{name}/{metric}: non_comparable is missing {sorted(missing)}. The "
                              f"confidence band reads both to explain an AMBER cell.")
        ind = p.get("destination_indeterminate")
        if ind:
            missing = {"fund", "stated_uses", "why_indeterminate"} - set(ind)
            if missing:
                errors.append(f"{name}: destination_indeterminate is missing {sorted(missing)}, which "
                              f"the confidence band reads to explain the AMBER cell.")
        thr = p.get("buyback_threshold")
        if thr:
            missing = {"threshold_usd_annualised", "status"} - set(thr)
            if missing:
                errors.append(f"{name}: buyback_threshold is missing {sorted(missing)}, which the "
                              f"workbook reads to decide whether the buyback exists at all.")
            if thr.get("status") not in ("active", "below_threshold", "unconfirmed_which_side"):
                errors.append(f"{name}: buyback_threshold status {thr.get('status')!r} is not one of "
                              f"active / below_threshold / unconfirmed_which_side.")
        add = p.get("buyback_is_supply_additive")
        if add and "effect" not in add:
            errors.append(f"{name}: buyback_is_supply_additive is missing 'effect', which the workbook "
                          f"reads to explain the flipped sign.")
        for cc in (p.get("cross_checks") or []):
            missing = {"primary", "secondary", "tolerance"} - set(cc)
            if missing:
                errors.append(f"{name}: a cross_checks entry is missing {sorted(missing)}, which "
                              f"check_cross_checks reads.")
    return errors


def validate_config(raise_on_error: bool = True) -> list[str]:
    errors = (_check_lock_contracts() + _check_addresses() + _check_split_periods()
              + _check_burn_mechanisms() + _check_burn_destinations() + _check_declared_shapes())
    if errors and raise_on_error:
        raise ConfigError("config.py has errors that would produce wrong numbers:\n  - " + "\n  - ".join(errors))
    return errors


validate_config()
