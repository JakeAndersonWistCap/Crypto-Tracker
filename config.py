"""
config.py — the project universe and every parameter that could go stale.

NO LOGIC IN THIS FILE. Data only.

One dict per project. Every fee-split / burn-split / issuance-schedule parameter carries
its own source_url, source_date and status. Where the brief said "confirm" or "verify",
status is "unconfirmed": the workbook greys the cell and suppresses the derived figure.
Never estimate a split we have not documented.

Fields that do real work (do not collapse into constants):
  fee_split.discretionary   — revisable by forum post (True) vs immutable on-chain (False)
  buyback_destination       — burn | distribute | split | hold | disputed
  burn_execution            — protocol | holder_elected | n/a
  materiality               — high | medium | low

Data-source identifiers:
  coingecko_id              — CoinGecko /coins/{id}
  defillama_fees_slug       — DefiLlama /summary/fees/{slug} (protocol OR chain slug)
  defillama_protocol        — DefiLlama /protocol/{slug} (protocol TVL)
  defillama_chain           — DefiLlama chain name (chain TVL, stablecoins, RWA-by-chain)
  dune_queries              — {metric_key: {"query_id": int|None, "date_col": str, "value_col": str}}
                              query_id None => unconfigured, logged in Run Log, never fabricated.
  issuance_schedule         — deterministic issuance written to the store as source "schedule:config"
                              (Bitcoin, Zcash, Bittensor, Venice). Steps are [{"from": date, "tokens_per_day": x}].
"""

BRIEF_DATE = "2026-09-10"   # the date the parameters below were set from the working brief

# ---------------------------------------------------------------------------------------
# Global levers — written to the Config & Sources tab as blue inputs; every formula
# references the cell, not the number.
# ---------------------------------------------------------------------------------------
GLOBALS = {
    "period_days": 90,             # comparison window for the archetype tabs ("trailing quarter")
    "short_days": 30,              # trailing window for the "latest" column
    "days_per_year": 365,
    "stale_after_days": 7,         # a daily series whose last good fetch is older than this is STALE
    "months_in_monthly_sheet": 12,
}

ARCHETYPE_NAMES = {
    1: "Infrastructure",
    2: "Coordination Mechanism",
    3: "Revenue Buyback",
    4: "Permanent Burn",
}

# ---------------------------------------------------------------------------------------
# Metric library. kind: "flow" (summed over a window) or "stock" (point value / window average).
# unit: "usd" | "tokens" | "count" | "pct" | "days" | "units"
# ---------------------------------------------------------------------------------------
METRICS = {
    # --- common (CoinGecko)
    "price_usd":                   {"label": "Price",                          "kind": "stock", "unit": "usd",    "archetypes": [1, 2, 3, 4]},
    "market_cap_usd":              {"label": "Market cap",                     "kind": "stock", "unit": "usd",    "archetypes": [1, 2, 3, 4]},
    "volume_usd":                  {"label": "Trading volume",                 "kind": "flow",  "unit": "usd",    "archetypes": []},
    "circulating_supply":          {"label": "Circulating supply (reported)",  "kind": "stock", "unit": "tokens", "archetypes": [1, 2, 3, 4]},
    "circulating_supply_implied":  {"label": "Circulating supply (mcap/price)", "kind": "stock", "unit": "tokens", "archetypes": [1, 4]},
    "total_supply":                {"label": "Total supply",                   "kind": "stock", "unit": "tokens", "archetypes": [1, 2, 3, 4]},
    "max_supply":                  {"label": "Max supply",                     "kind": "stock", "unit": "tokens", "archetypes": [4]},
    "fdv_usd":                     {"label": "FDV",                            "kind": "stock", "unit": "usd",    "archetypes": [3]},
    # --- DefiLlama
    "fees_usd":                    {"label": "Total fees paid",                "kind": "flow",  "unit": "usd",    "archetypes": [1, 3, 4]},
    "revenue_usd":                 {"label": "Protocol revenue",               "kind": "flow",  "unit": "usd",    "archetypes": [3]},
    "holders_revenue_usd":         {"label": "Holders revenue (DefiLlama)",    "kind": "flow",  "unit": "usd",    "archetypes": [3]},
    "protocol_tvl_usd":            {"label": "Protocol TVL",                   "kind": "stock", "unit": "usd",    "archetypes": [3]},
    "tvl_usd":                     {"label": "Chain TVL",                      "kind": "stock", "unit": "usd",    "archetypes": [1]},
    "stablecoin_supply_usd":       {"label": "Stablecoin supply on chain",     "kind": "stock", "unit": "usd",    "archetypes": [1]},
    "rwa_defillama_usd":           {"label": "RWA on chain (DefiLlama)",       "kind": "stock", "unit": "usd",    "archetypes": [1]},
    # --- manual / RWA.xyz
    "rwa_xyz_usd":                 {"label": "RWA on chain (RWA.xyz)",         "kind": "stock", "unit": "usd",    "archetypes": [1]},
    # --- Dune / protocol-specific
    "tx_count":                    {"label": "Transactions",                   "kind": "flow",  "unit": "count",  "archetypes": [1]},
    "active_addresses":            {"label": "Active addresses (low weight)",  "kind": "stock", "unit": "count",  "archetypes": [1]},
    "gross_issuance_tokens":       {"label": "Gross issuance",                 "kind": "flow",  "unit": "tokens", "archetypes": [1, 4]},
    "gross_burn_tokens":           {"label": "Gross burn",                     "kind": "flow",  "unit": "tokens", "archetypes": [1, 4]},
    "staked_tokens":               {"label": "Staked tokens",                  "kind": "stock", "unit": "tokens", "archetypes": [1, 2]},
    "emissions_tokens":            {"label": "Emissions to suppliers/stakers", "kind": "flow",  "unit": "tokens", "archetypes": [2, 3]},
    "supply_units":                {"label": "Supply units (nodes/hotspots/GPUs)", "kind": "stock", "unit": "units", "archetypes": [2]},
    "utilisation_pct":             {"label": "Capacity utilisation",           "kind": "stock", "unit": "pct",    "archetypes": [2]},
    "customer_revenue_usd":        {"label": "End-user revenue",               "kind": "flow",  "unit": "usd",    "archetypes": [2]},
    "actual_buyback_usd":          {"label": "Actual buyback (observed)",      "kind": "flow",  "unit": "usd",    "archetypes": [3]},
    "actual_buyback_tokens":       {"label": "Actual buyback tokens (observed)", "kind": "flow", "unit": "tokens", "archetypes": [3]},
    "locked_tokens":               {"label": "Tokens locked (ve)",             "kind": "stock", "unit": "tokens", "archetypes": [3]},
    "avg_lock_duration_days":      {"label": "Average lock duration",          "kind": "stock", "unit": "days",   "archetypes": [3]},
    "publisher_conviction_usd":    {"label": "Publisher Conviction (pre-purchased demand)", "kind": "stock", "unit": "usd", "archetypes": [2]},
}

# Metrics that must come from a Dune query (or a manual override) — the DefiLlama/CoinGecko
# adapters never produce them. Listed so the Run Log can say "unconfigured" rather than "missing".
DUNE_ONLY_METRICS = [
    "tx_count", "active_addresses", "gross_issuance_tokens", "gross_burn_tokens", "staked_tokens",
    "emissions_tokens", "supply_units", "utilisation_pct", "customer_revenue_usd",
    "actual_buyback_usd", "actual_buyback_tokens", "locked_tokens", "avg_lock_duration_days",
    "publisher_conviction_usd",
]

MANUAL_ONLY_METRICS = ["rwa_xyz_usd"]   # RWA.xyz has no public API on our tier

DUNE_QUERY_TEMPLATE = {"query_id": None, "date_col": "day", "value_col": "value"}


def _dune(*metrics):
    """Data-only helper: an unconfigured Dune slot per metric. Fill query_id when the Dune page exists."""
    return {m: dict(DUNE_QUERY_TEMPLATE) for m in metrics}


_NO_SPLIT = {
    "share_to_buyback": None,
    "source_url": None,
    "source_date": None,
    "discretionary": None,
    "status": "n/a",
}

# ---------------------------------------------------------------------------------------
# THE UNIVERSE — 30 projects. Primary archetype listed first.
# ---------------------------------------------------------------------------------------
PROJECTS = [
    # ---------------------------------------------------------------- Archetype 1 (+4)
    {
        "name": "Bitcoin", "symbol": "BTC",
        "coingecko_id": "bitcoin",
        "defillama_fees_slug": "bitcoin", "defillama_protocol": None, "defillama_chain": "Bitcoin",
        "archetypes": [1], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": 0.0, "source_url": "https://bitcoin.org/en/developer-guide", "source_date": BRIEF_DATE, "status": "active"},
        "issuance_schedule": {
            "steps": [{"from": "2024-04-20", "tokens_per_day": 3.125 * 144}],   # 3.125 BTC/block x ~144 blocks/day post 4th halving
            "source_url": "https://en.bitcoin.it/wiki/Controlled_supply", "source_date": BRIEF_DATE, "status": "active",
        },
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "materiality": "high",
        "dune_queries": _dune("tx_count", "active_addresses"),
        "notes": "Issuance schedule only. No burn, no buyback.",
    },
    {
        "name": "Ethereum", "symbol": "ETH",
        "coingecko_id": "ethereum",
        "defillama_fees_slug": "ethereum", "defillama_protocol": None, "defillama_chain": "Ethereum",
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://eips.ethereum.org/EIPS/eip-1559", "source_date": BRIEF_DATE, "status": "active",
                       "note": "EIP-1559 burns the base fee; share of total fees is measured, not fixed. Use Dune burn series."},
        "issuance_schedule": None,
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "materiality": "high",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
        "notes": "EIP-1559 base fee burn. Net issuance = validator issuance - base fee burn.",
    },
    {
        "name": "Solana", "symbol": "SOL",
        "coingecko_id": "solana",
        "defillama_fees_slug": "solana", "defillama_protocol": None, "defillama_chain": "Solana",
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://docs.solana.com/economics", "source_date": BRIEF_DATE, "status": "unconfirmed",
                       "note": "Partial fee burn (50% of base fee). Priority-fee burn removed by SIMD-96. Confirm current effective share."},
        "issuance_schedule": None,
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "materiality": "high",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
        "notes": "Partial fee burn — confirm current share before enabling implied burn.",
    },
    {
        "name": "Tron", "symbol": "TRX",
        "coingecko_id": "tron",
        "defillama_fees_slug": "tron", "defillama_protocol": None, "defillama_chain": "Tron",
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://developers.tron.network/docs/resource-model", "source_date": BRIEF_DATE, "status": "unconfirmed",
                       "note": "TRX paid for bandwidth/energy is burned; document the share before enabling."},
        "issuance_schedule": None,
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "materiality": "high",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
        "notes": "",
    },
    {
        "name": "Near", "symbol": "NEAR",
        "coingecko_id": "near",
        "defillama_fees_slug": "near", "defillama_protocol": None, "defillama_chain": "Near",
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://docs.near.org/protocol/economics", "source_date": BRIEF_DATE, "status": "unconfirmed",
                       "note": "Protocol docs describe a 70% burn / 30% contract-developer split. Confirm before enabling."},
        "issuance_schedule": None,
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "materiality": "medium",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
        "notes": "Confirm burn share.",
    },
    {
        "name": "Canton", "symbol": "CC",
        "coingecko_id": "canton-network",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://www.canton.network/", "source_date": BRIEF_DATE, "status": "unconfirmed",
                       "note": "Dune page has a burn series. Traffic fees are burned; document the share."},
        "issuance_schedule": None,
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "materiality": "medium",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "fees_usd", "tx_count", "active_addresses"),
        "notes": "Dune page — has a burn. Not on DefiLlama; all chain metrics via Dune. Verify CoinGecko id.",
    },
    {
        "name": "Plume", "symbol": "PLUME",
        "coingecko_id": "plume",
        "defillama_fees_slug": "plume", "defillama_protocol": None, "defillama_chain": "Plume Mainnet",
        "archetypes": [1, 3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.plume.org/", "source_date": BRIEF_DATE, "discretionary": None, "status": "unconfirmed",
                      "note": "Staking yield; no documented revenue-to-buyback split."},
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "materiality": "medium",
        "dune_queries": _dune("gross_issuance_tokens", "staked_tokens", "emissions_tokens", "tx_count", "active_addresses"),
        "notes": "RWA-on-chain is the key demand metric — pull RWA.xyz (manual) and DefiLlama RWA category and show the divergence.",
    },
    {
        "name": "Injective", "symbol": "INJ",
        "coingecko_id": "injective-protocol",
        "defillama_fees_slug": "injective", "defillama_protocol": None, "defillama_chain": "Injective",
        "archetypes": [1, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://docs.injective.network/learn/injective-hub/burn-auction", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Monthly Community BuyBack + weekly burn auction: committed INJ permanently burned. Burn share measured from Dune, not a fixed %."},
        "issuance_schedule": None,
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "materiality": "medium",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "staked_tokens", "tx_count", "active_addresses"),
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
            "steps": [{"from": "2024-11-23", "tokens_per_day": 1.5625 * 1152}],   # 1.5625 ZEC/block x 1152 blocks/day post 2nd halving
            "source_url": "https://zips.z.cash/protocol/protocol.pdf", "source_date": BRIEF_DATE, "status": "active",
        },
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "materiality": "medium",
        "dune_queries": _dune("tx_count", "active_addresses"),
        "notes": "No burn. Issuance schedule read only.",
    },
    # ---------------------------------------------------------------- Archetype 2 (+3/+4)
    {
        "name": "Chainlink", "symbol": "LINK",
        "coingecko_id": "chainlink",
        "defillama_fees_slug": "chainlink", "defillama_protocol": "chainlink", "defillama_chain": None,
        "archetypes": [2, 3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://blog.chain.link/chainlink-reserve/", "source_date": BRIEF_DATE, "discretionary": True, "status": "unconfirmed",
                      "note": "Payment Abstraction / SVR route revenue to the Chainlink Reserve and staking. Verify split before enabling."},
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "hold", "destination_split": None, "burn_execution": "n/a",
        "materiality": "high",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "staked_tokens", "emissions_tokens", "supply_units", "customer_revenue_usd"),
        "notes": "Payment Abstraction / SVR route revenue to staking — verify split.",
    },
    {
        "name": "World Mobile", "symbol": "WMTX",
        "coingecko_id": "world-mobile-token",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://worldmobile.io/", "source_date": BRIEF_DATE, "discretionary": True, "status": "unconfirmed",
                      "note": "Programmatic revenue buyback. Manual-only until their metrics page returns."},
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "materiality": "low",
        "dune_queries": {},
        "notes": "MANUAL ONLY via manual_overrides.csv while their page is being rebuilt.",
    },
    {
        "name": "GEODNET", "symbol": "GEOD",
        "coingecko_id": "geodnet",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": 0.80, "source_url": "https://geodnet.com/", "source_date": BRIEF_DATE, "status": "active",
                       "note": "80% of console (data) revenue buys back and burns GEOD per GEODNET tokenomics. Confirmed burn; re-check the % against the current docs page."},
        "issuance_schedule": None,
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "materiality": "low",
        "dune_queries": _dune("gross_burn_tokens", "emissions_tokens", "supply_units", "customer_revenue_usd", "utilisation_pct"),
        "notes": "Console revenue burn — confirmed.",
    },
    {
        "name": "peaq", "symbol": "PEAQ",
        "coingecko_id": "peaq-2",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": "peaq",
        "archetypes": [2], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "materiality": "low",
        "dune_queries": _dune("emissions_tokens", "supply_units", "customer_revenue_usd", "tx_count", "active_addresses"),
        "notes": "Dune page. Verify CoinGecko id (peaq-2).",
    },
    {
        "name": "Render", "symbol": "RENDER",
        "coingecko_id": "render-token",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://know.rendernetwork.com/basics/burn-and-mint-equilibrium", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Burn-and-mint equilibrium: customer payments burned, node rewards minted from emissions. Share measured from Dune."},
        "issuance_schedule": None,
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "materiality": "medium",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens", "supply_units", "customer_revenue_usd", "utilisation_pct"),
        "notes": "Burn-and-mint equilibrium.",
    },
    {
        "name": "Bittensor", "symbol": "TAO",
        "coingecko_id": "bittensor",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": "Bittensor",
        "archetypes": [2], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": None,
        "issuance_schedule": {
            "steps": [{"from": "2025-12-14", "tokens_per_day": 0.5 * 7200}],   # 0.5 TAO/block x 7200 blocks/day post-halving
            "source_url": "https://docs.bittensor.com/learn/tokenomics", "source_date": BRIEF_DATE, "status": "active",
        },
        "buyback_destination": "n/a", "destination_split": None, "burn_execution": "n/a",
        "materiality": "medium",
        "dune_queries": _dune("emissions_tokens", "staked_tokens", "supply_units", "customer_revenue_usd"),
        "notes": "NOT archetype 3 — yield is emissions-funded, revenue capture unproven. ~70% staked is a float metric, not demand.",
    },
    {
        "name": "OriginTrail", "symbol": "TRAC",
        "coingecko_id": "origintrail",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 1.0, "source_url": "https://docs.origintrail.io/", "source_date": BRIEF_DATE, "discretionary": False, "status": "active",
                      "note": "Publishing fees in TRAC distributed to nodes from usage, not inflation. Revenue small and not publicly tracked."},
        "burn_split": None,
        "issuance_schedule": {"steps": [{"from": "2018-01-01", "tokens_per_day": 0.0}],
                              "source_url": "https://docs.origintrail.io/", "source_date": BRIEF_DATE, "status": "active"},   # 500m fixed cap, zero inflation
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "materiality": "low",
        "dune_queries": _dune("customer_revenue_usd", "publisher_conviction_usd", "supply_units", "staked_tokens", "emissions_tokens"),
        "notes": "500m fixed cap, zero inflation. Materiality LOW: qualifies mechanically, not comparable to Aave/Hyperliquid. Also pull Publisher Conviction.",
    },
    {
        "name": "Aethir", "symbol": "ATH",
        "coingecko_id": "aethir",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2], "archetypes_held": [3],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.aethir.com/", "source_date": BRIEF_DATE, "discretionary": True, "status": "unconfirmed",
                      "note": "Sources conflict: stated plan to use compute revenue for buybacks vs June 2026 review stating no fee distribution, no buy-and-burn, no revenue sharing. Checker Node buyback is an NFT repurchase, not a token buyback."},
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "disputed", "destination_split": None, "burn_execution": "n/a",
        "materiality": "medium",
        "dune_queries": _dune("emissions_tokens", "supply_units", "customer_revenue_usd", "utilisation_pct", "staked_tokens"),
        "notes": "Hold archetype 3 until documented.",
    },
    {
        "name": "Venice AI", "symbol": "VVV",
        "coingecko_id": "venice-token",
        "defillama_fees_slug": None, "defillama_protocol": None, "defillama_chain": None,
        "archetypes": [2, 3, 4], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://venice.ai/blog", "source_date": BRIEF_DATE, "discretionary": True, "status": "unconfirmed",
                      "note": "Revenue-funded monthly buy-and-burn; the revenue share applied has not been documented as a fixed %. Use actual buyback from Dune."},
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://venice.ai/blog", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Bought-back VVV is burned. Measured from Dune."},
        "issuance_schedule": {
            "steps": [
                {"from": "2025-01-27", "tokens_per_day": 14_000_000 / 365},
                {"from": "2026-02-01", "tokens_per_day": 6_000_000 / 365},
                {"from": "2026-05-01", "tokens_per_day": 5_000_000 / 365},
                {"from": "2026-07-01", "tokens_per_day": 3_000_000 / 365},
            ],
            "source_url": "https://venice.ai/blog", "source_date": BRIEF_DATE, "status": "active",
        },
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "materiality": "medium",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "gross_burn_tokens", "emissions_tokens", "customer_revenue_usd", "staked_tokens"),
        "notes": "100% of emissions to stakers. Emissions stepped 14m -> 6m (Feb 2026) -> 5m (May) -> 3m (Jul) VVV/yr. Even at 3m, issuance runs several multiples of what stated revenue could fund. Clearest case for showing buyback and emissions together.",
    },
    # ---------------------------------------------------------------- Archetype 3 (+4/+1)
    {
        "name": "Maple", "symbol": "SYRUP",
        "coingecko_id": "syrup",
        "defillama_fees_slug": "maple", "defillama_protocol": "maple", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://maple.finance/", "source_date": BRIEF_DATE, "discretionary": True, "status": "unconfirmed",
                      "note": "Buyback programme exists (Drips / SYRUP buybacks); share not documented. No burn — confirmed."},
        "burn_split": {"share_of_fees_burned": 0.0, "source_url": "https://maple.finance/", "source_date": BRIEF_DATE, "status": "active", "note": "No burn — confirmed."},
        "issuance_schedule": None,
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "materiality": "medium",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens"),
        "notes": "No burn — confirmed.",
    },
    {
        "name": "Morpho", "symbol": "MORPHO",
        "coingecko_id": "morpho",
        "defillama_fees_slug": "morpho", "defillama_protocol": "morpho", "defillama_chain": None,
        "archetypes": [2], "archetypes_held": [3],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.morpho.org/", "source_date": BRIEF_DATE, "discretionary": True, "status": "unconfirmed",
                      "note": "Fee switch status needs confirming before the archetype 3 block is enabled."},
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "hold", "destination_split": None, "burn_execution": "n/a",
        "materiality": "high",
        "dune_queries": _dune("emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens"),
        "notes": "Fee switch status needs confirming before the 3 block is enabled.",
    },
    {
        "name": "Hyperliquid", "symbol": "HYPE",
        "coingecko_id": "hyperliquid",
        "defillama_fees_slug": "hyperliquid", "defillama_protocol": "hyperliquid", "defillama_chain": "Hyperliquid L1",
        "archetypes": [3, 1], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 0.98, "source_url": "https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/assistance-fund", "source_date": BRIEF_DATE, "discretionary": False, "status": "active",
                      "note": "Midpoint of stated 97-99% of net protocol fees to the Assistance Fund."},
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "disputed", "destination_split": None, "burn_execution": "protocol",
        "materiality": "high",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens", "tx_count", "active_addresses", "gross_issuance_tokens"),
        "notes": "Assistance Fund absorbs 97-99% of net protocol fees. Own L1 + HyperEVM hence the 1 block. Sources conflict on whether purchased HYPE is burned or held.",
    },
    {
        "name": "Uniswap", "symbol": "UNI",
        "coingecko_id": "uniswap",
        "defillama_fees_slug": "uniswap", "defillama_protocol": "uniswap", "defillama_chain": None,
        "archetypes": [4], "archetypes_held": [],
        "fee_split": dict(_NO_SPLIT),
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://gov.uniswap.org/", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Burn-to-claim via Token Jars / fire pit. Fee routing deterministic; realised burn depends on holder redemption. Post-activation burns ~4-5m UNI/yr, separate from the 100m one-off in Jan 2026."},
        "issuance_schedule": None,
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "holder_elected",
        "materiality": "high",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens"),
        "notes": "Archetype 4 only — no distribution leg. Implied and actual burn will diverge for reasons unrelated to revenue (holder-elected).",
    },
    {
        "name": "Aerodrome", "symbol": "AERO",
        "coingecko_id": "aerodrome-finance",
        "defillama_fees_slug": "aerodrome", "defillama_protocol": "aerodrome", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 1.0, "source_url": "https://aerodrome.finance/docs", "source_date": BRIEF_DATE, "discretionary": False, "status": "active",
                      "note": "100% of trading fees to veAERO voters (distribute, not buyback-and-burn). Yield destination tracked separately from burn."},
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "materiality": "high",
        "dune_queries": _dune("locked_tokens", "avg_lock_duration_days", "emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens"),
        "notes": "veAERO — lock rate and average lock duration are required inputs.",
    },
    {
        "name": "PancakeSwap", "symbol": "CAKE",
        "coingecko_id": "pancakeswap-token",
        "defillama_fees_slug": "pancakeswap", "defillama_protocol": "pancakeswap", "defillama_chain": None,
        "archetypes": [4, 3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.pancakeswap.finance/governance-and-tokenomics/cake-tokenomics", "source_date": BRIEF_DATE, "discretionary": True, "status": "unconfirmed",
                      "note": "Buyback share of fees varies by product; use published monthly burn (actual) rather than an implied split."},
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://docs.pancakeswap.finance/governance-and-tokenomics/cake-tokenomics", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Best-disclosed net burn in the universe. Publishes net mint monthly (May 2026: -1,958,514 CAKE, 33rd consecutive month). Hard cap cut 450m -> 400m Jan 2026."},
        "issuance_schedule": None,
        "buyback_destination": "burn", "destination_split": None, "burn_execution": "protocol",
        "materiality": "high",
        "dune_queries": _dune("gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens", "staked_tokens"),
        "notes": "Template for the archetype 4 tab. Use manual_overrides.csv for the published monthly net mint if Dune is not configured.",
    },
    {
        "name": "Sky", "symbol": "SKY",
        "coingecko_id": "sky",
        "defillama_fees_slug": "sky", "defillama_protocol": "sky", "defillama_chain": None,
        "archetypes": [3, 4], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.sky.money/", "source_date": BRIEF_DATE, "discretionary": True, "status": "unconfirmed",
                      "note": "Smart Burn Engine ~$1m/day is a governance-set parameter, not a fixed % of revenue. Use actual buyback from Dune."},
        "burn_split": {"share_of_fees_burned": None, "source_url": "https://docs.sky.money/", "source_date": BRIEF_DATE, "status": "active",
                       "note": "Repurchased SKY burned OR redistributed to staked SKY per governance parameter. Measured from Dune; never net staking rewards against burn."},
        "issuance_schedule": None,
        "buyback_destination": "split", "destination_split": None, "burn_execution": "protocol",
        "materiality": "high",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "gross_burn_tokens", "gross_issuance_tokens", "emissions_tokens", "staked_tokens"),
        "notes": "Smart Burn Engine ~$1m/day against ~838m SKY staking rewards over 180 days. destination_split is a governance parameter — set it when documented.",
    },
    {
        "name": "Pendle", "symbol": "PENDLE",
        "coingecko_id": "pendle",
        "defillama_fees_slug": "pendle", "defillama_protocol": "pendle", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 1.0, "source_url": "https://docs.pendle.finance/ProtocolMechanics/Mechanisms/vePENDLE", "source_date": BRIEF_DATE, "discretionary": False, "status": "active",
                      "note": "Protocol revenue distributed to vePENDLE holders (distribute, not burn)."},
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "materiality": "high",
        "dune_queries": _dune("locked_tokens", "avg_lock_duration_days", "emissions_tokens", "actual_buyback_usd", "actual_buyback_tokens"),
        "notes": "vePENDLE lock rate required.",
    },
    {
        "name": "Fluid", "symbol": "FLUID",
        "coingecko_id": "instadapp",
        "defillama_fees_slug": "fluid", "defillama_protocol": "fluid", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://docs.fluid.io/", "source_date": BRIEF_DATE, "discretionary": True, "status": "unconfirmed",
                      "note": "Buyback programme governance-approved; share not documented here."},
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "hold", "destination_split": None, "burn_execution": "n/a",
        "materiality": "medium",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens"),
        "notes": "",
    },
    {
        "name": "Aave", "symbol": "AAVE",
        "coingecko_id": "aave",
        "defillama_fees_slug": "aave", "defillama_protocol": "aave", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": 1.0, "source_url": "https://governance.aave.com/", "source_date": BRIEF_DATE, "discretionary": True, "status": "paused",
                      "note": "Aavenomics 3.0: 100% of Aave Protocol and GHO revenue into buybacks. Budget cut ~$50m -> ~$30m March 2026; buybacks PAUSED from 19 April 2026 after the rsETH bridge incident. Discretionary: revisable by governance."},
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "materiality": "high",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens"),
        "notes": "Destination is stakers, not burn. Paused 2026-04-19.",
        "status_changes": [
            {"date": "2026-03-01", "event": "Budget cut ~$50m -> ~$30m", "source_url": "https://governance.aave.com/"},
            {"date": "2026-04-19", "event": "Buybacks paused after rsETH bridge incident", "source_url": "https://governance.aave.com/"},
        ],
    },
    {
        "name": "Ether.fi", "symbol": "ETHFI",
        "coingecko_id": "ether-fi",
        "defillama_fees_slug": "ether.fi", "defillama_protocol": "ether.fi", "defillama_chain": None,
        "archetypes": [3], "archetypes_held": [],
        "fee_split": {"share_to_buyback": None, "source_url": "https://etherfi.gitbook.io/etherfi", "source_date": BRIEF_DATE, "discretionary": True, "status": "unconfirmed",
                      "note": "Buyback confirmed (governance-approved programme); the revenue share is not documented here."},
        "burn_split": None,
        "issuance_schedule": None,
        "buyback_destination": "distribute", "destination_split": None, "burn_execution": "n/a",
        "materiality": "medium",
        "dune_queries": _dune("actual_buyback_usd", "actual_buyback_tokens", "emissions_tokens", "staked_tokens"),
        "notes": "Buyback confirmed.",
    },
]

PROJECT_BY_NAME = {p["name"]: p for p in PROJECTS}
