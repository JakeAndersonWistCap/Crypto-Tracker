"""
metrics_dictionary.py — what every headline column on Master and A1-A4 means (Jake, 2026-10-11, D1: "the scoring theory
will be built on it, so it must match the code exactly").

ENTRIES maps a column HEADER PREFIX (exactly as build_workbook writes it) to its plain-language record:
    what      plain definition
    formula   the computation, in words, naming the Data-tab series (config.METRICS keys) it reads
    window    the period (Q0 = the trailing 90 days; "now" = the latest stored value within its age limit)
    unit      tokens / $ / % / x / text
    counts    what is in it; excludes: what is deliberately not
    inputs    the config.METRICS series it reads (their labels, units and source tiers are printed from config)
    decided   [(who, date, what)] — the decision behind the definition
generate_metrics_doc.py renders METRICS.md from this, from config (labels, tiers, per-project bases) and from a real
build's column list; tests/test_progress.py::test_metrics_doc_covers_every_headline fails when a headline on a tab has
no entry here, or METRICS.md is stale.
"""
from __future__ import annotations

J = "Jake"

# THE CONVENTIONS EVERY COLUMN SHARES — printed once at the top of METRICS.md
CONVENTIONS = [
    ("Trajectory", "Every headline on Master and A1-A4 is re-computed over FIVE consecutive trailing 90-day blocks "
                   "(Q0, Q-1 .. Q-4) by its own formula, each Data reference moved k quarters back (trajectory.py; "
                   "Trajectory tab). A trend cell beside each headline shows arrow · QoQ · 4-quarter log-linear growth "
                   "(annual) · quarters improved (direction-aware). T12M sums the four quarters of a window total and "
                   "averages a rate. Years to benchmark at the trailing growth, n/a with the reason (Jake 2026-10-12). "
                   "THE BASIS RULE (decision 7 on 6f8c86c): a change of basis never reads as a trend — history is "
                   "restated on the current basis where it can be (archive reads at past blocks); where not, the row "
                   "says SERIES BREAK <date> and no QoQ, growth or direction is computed across it. LUMPY FLOWS "
                   "(decision 8): a row including a discrete flow (unlocks, a declared lumpy or event-annualised "
                   "series, a monthly series) takes direction and growth from the ROLLING T12M (Q0..Q-3 vs Q-1..Q-4); "
                   "single quarters are shown, never used for direction. Forward-looking rows use the emission rate "
                   "IN FORCE where one is declared (decision 2)."),
    ("Windows", "Q0 = the trailing 90 days ending yesterday (UTC); Q1/Q2/Q3 the 90 days before each; a monthly series' "
                "Q0 is its three latest complete months. A flow's window figure is its SUM; a stock's 'now' is its latest "
                "stored value within its age limit (credibility.now_max_age: prices 2 days, daily stocks 3, weekly / "
                "monthly their own staleness)."),
    ("Annualisation", "A continuous flow is annualised over the days its series ACTUALLY covered (sum ÷ covered days × "
                      "365); a discrete flow with a declared cadence as mean event × events a year (Jake 2026-09-28, "
                      "2026-09-29). Never ×365/90 on a young series."),
    ("Coverage guard", "A ratio or difference of two flows is BLOCKED (the cell says so, with each side's covered days) "
                       "when either side covers under 7 days of the window, and — for a difference or a net rate — when "
                       "their covered days differ by more than a tenth of the window. A DECLARED ZERO (config "
                       "issuance_declared_zero) counts as full coverage (Jake's review of 97b29ae, 2026-10-11, item 2)."),
    ("Sources and tiers", "Each series is read from the first source that answers, in tier order: 1 free API, 2 "
                          "contract / chain read, 3 rendered page or manual entry (dated, sourced). Two tiers writing "
                          "one figure on one day keep the EARLIER tier, except total_supply for the projects in "
                          "config.TOTAL_SUPPLY_ONCHAIN_FIRST, where the chain read wins (Jake 2026-10-11, decision A4). "
                          "A change of measuring point blanks a series unless a handover is declared "
                          "(config series_handover)."),
    ("Circulating convention", "circulating = total − treasury − team/investor unvested − foundation − operating "
                               "reserves − burned; STAKED TOKENS ARE CIRCULATING (out only for free float); protocol-held "
                               "counts as non-circulating. The figure in use is the project's own figure, adjusted to "
                               "Jake's convention (e.g. Hyperliquid Foundation + Grants out; NEAR total − lockups "
                               "LOCKED), wherever one exists; our on-chain set is the cross-check, CoinGecko only where "
                               "the project publishes nothing (Jake 2026-10-07; wording 2026-10-12)."),
    ("Price", "Market cap, FDV and the supply ratios use the spot price (price_usd 'now'); fee ÷ issuance and implied "
              "buyback tokens use the Q0 average price; protocol staking yield converts each payment at its payment "
              "day's price (Jake 2026-10-09)."),
    ("Statuses", "Buyback / burn status labels are DERIVED FROM THE DATA (Jake's review of 97b29ae, item 13): halted "
                 "since / SILENT since / active (measured Q0) / none measured in Q0 / none (declared 0%) / "
                 "(declared; no series measured)."),
]

ENTRIES = {
    # ------------------------------------------------------------------ Master
    "Archetypes (primary first)": dict(
        what="The project's archetypes (1 infrastructure, 2 coordination, 3 revenue buyback, 4 permanent burn), "
             "primary first.", formula="config archetypes", window="—", unit="text"),
    "Held": dict(what="Archetypes the project also fits but which are NOT enabled for it (config archetypes_held).",
                 formula="config archetypes_held", window="—", unit="text"),
    "Materiality": dict(what="The Config & Sources tab's Materiality for the project.",
                        formula="Config & Sources 'Materiality'", window="—", unit="text"),
    "Buyback status": dict(
        what="Whether a buyback is running, from the data.",
        formula="halted since (closure on file) / SILENT since (cadence broken) / active (measured Q0 > 0) / none "
                "measured in Q0 / none (declared 0%) / <config status> (declared; no buyback series measured)",
        window="Q0", unit="text", inputs=["actual_buyback_usd", "actual_buyback_tokens"],
        decided=[(J, "2026-10-11", "statuses derived from the data (review of 97b29ae, item 13)")]),
    "Burn status": dict(
        what="Whether a burn is running, from the data.",
        formula="as Buyback status, on the A4 burn series; a declared 0% split reads 'none (declared: 0% of fees burned)'",
        window="Q0", unit="text", inputs=["gross_burn_tokens"],
        decided=[(J, "2026-10-11", "statuses derived from the data (item 13); 0% split = none (sanity run, decision A6)")]),
    "Price — spot ($)": dict(what="Latest price.", formula="price_usd now", window="now", unit="$",
                             inputs=["price_usd"]),
    "Price — 90d average ($)": dict(what="Mean daily price over Q0.", formula="price_usd q0 (mean)", window="Q0",
                                    unit="$", inputs=["price_usd"]),
    "Market cap ($)": dict(
        what="Spot price × the circulating supply the ratios use, on one day.",
        formula="price_usd × circulating_supply_used (our figure carried forward within its age limit, else the "
                "CoinGecko-based fallback)", window="now", unit="$",
        inputs=["price_usd", "circulating_supply_used"],
        decided=[(J, "2026-10-05", "market cap = price × the circulating the ratios use"),
                 (J, "2026-10-11", "one basis on every day (review of 97b29ae, item 8)")]),
    "Market cap date (shown only when not the spot price's date)": dict(
        what="The day the market cap's newest row is dated, printed only when it is not the spot price's day — market "
             "cap is price × the circulating used on ONE day, so an older day says which day the figure is.",
        formula="'as of <market_cap_usd date> (spot price <price_usd date>)' when the dates differ, else blank",
        window="now", unit="text", inputs=["market_cap_usd", "price_usd"],
        decided=[(J, "2026-10-19", "print the date beside market cap when its inputs' dates differ (G5 item 8d)")]),
    "Circulating supply (used)": dict(
        what="The circulating supply every ratio divides by.",
        formula="per project: config.circulating_primary_metric — the project's own figure, adjusted to Jake's "
                "convention (e.g. Hyperliquid Foundation + Grants out), our on-chain set "
                "(total − the documented non-circulating wallets), CoinGecko + staked, or CoinGecko (see the per-project "
                "table)", window="now", unit="tokens", inputs=["circulating_supply_used"],
        counts="staked tokens (circulating)", excludes="treasury, team/investor unvested, foundation, operating "
                                                       "reserves, burned",
        decided=[(J, "2026-10-07", "the circulating policy and convention"),
                 (J, "2026-10-11", "Hyperliquid: Foundation + Community Grants wallets out (A1)"),
                 (J, "2026-10-12", "NEAR: everything not yet transferable out — total − lockups LOCKED (unvested + "
                                   "lockup-locked; buggy lockups recomputed as NEAR's lakehouse does); released-not-"
                                   "withdrawn stays in")]),
    "Circulating supply — CoinGecko (cross-check)": dict(
        what="CoinGecko's circulating figure, shown beside ours.", formula="circulating_supply now", window="now",
        unit="tokens", inputs=["circulating_supply"]),
    "Market cap — CoinGecko": dict(what="CoinGecko's market cap, the cross-check.", formula="market_cap_usd_coingecko",
                                   window="now", unit="$", inputs=["market_cap_usd_coingecko"]),
    "Trading volume, 30d": dict(
        what="CoinGecko's spot trading volume over the trailing 30 days, all venues — liquidity, beside the market cap "
             "(G12 B1).", formula="volume_usd now (a flow: the trailing 30-day sum)", window="now (30 days)", unit="$",
        inputs=["volume_usd"]),
    "Turnover = 30d volume": dict(
        what="How much of the market cap traded in the last 30 days — liquidity, not demand for the token's cash flows "
             "(G12 B1).", formula="volume_usd now ÷ market_cap_usd now", window="now (30 days)", unit="x",
        inputs=["volume_usd", "market_cap_usd"]),
    "FDV ($)": dict(
        what="Spot price × the total supply we use, on one day.",
        formula="price_usd × total_supply (on-chain first where declared; carried forward within its age limit); "
                "CoinGecko's FDV on days with no total of ours", window="now", unit="$",
        inputs=["price_usd", "total_supply"],
        decided=[(J, "2026-10-11", "Ether.fi FDV on the on-chain total (review item 6); every project on one price "
                                   "and day (sanity run: FDV < market cap); on-chain total first for 8 projects (A4)")]),
    "Fees Q0 ($)": dict(what="Fees paid by users over Q0 (the project's archetype tab).", formula="fees_usd q0",
                        window="Q0", unit="$", inputs=["fees_usd"]),
    "Revenue Q0 ($)": dict(what="Protocol revenue over Q0.", formula="revenue_usd q0", window="Q0", unit="$",
                           inputs=["revenue_usd"]),
    "TVL ($)": dict(what="Chain TVL for A1 names, protocol TVL otherwise.", formula="tvl_usd / protocol_tvl_usd now",
                    window="now", unit="$", inputs=["tvl_usd", "protocol_tvl_usd"]),
    "Implied buyback Q0 ($)": dict(
        what="What the documented split would buy: revenue × the share that applied to THIS window.",
        formula="revenue base (revenue_usd; Sky: net protocol surplus) q0 × the window's share (Config & Sources)",
        window="Q0", unit="$", inputs=["revenue_usd"],
        counts="the split in force for the window", excludes="a current split applied retroactively; unconfirmed "
                                                             "splits (suppressed)"),
    "Actual buyback Q0 ($)": dict(
        what="Tokens actually bought over Q0, in dollars at each day's price.",
        formula="actual_buyback_usd q0 (per project route: a burn route = the burn; a held route = inflows to the "
                "fund; Aerodrome = bought + compounded, see B)", window="Q0", unit="$",
        inputs=["actual_buyback_usd"],
        excludes="Aerodrome: the rebase and withdrawManaged realisations; PancakeSwap: the dead-address inflow (99% "
                 "MasterChef mint-and-burn) — n/a until the product burn is a series",
        decided=[(J, "2026-10-11", "Aerodrome basis = AERO bought and locked + compounded voting rewards on accrual (B)")]),
    "Gross burn Q0 (tokens)": dict(
        what="Tokens permanently destroyed over Q0 (the A4 burn series).", formula="config.a4_burn_metric q0",
        window="Q0", unit="tokens", inputs=["gross_burn_tokens"],
        decided=[(J, "2026-10-08", "NEAR's burn is the chain's record (every total-supply fall)")]),
    "Gross issuance Q0 (tokens)": dict(
        what="New tokens minted over Q0 (pool release where the supply is pre-minted).",
        formula="gross_issuance_tokens q0, or pool_release_tokens (config.issuance_basis)", window="Q0",
        unit="tokens", inputs=["gross_issuance_tokens", "pool_release_tokens"],
        decided=[(J, "2026-10-12", "Phase 2 amendment 1: also a headline on A1, judged there")]),
    "NET SUPPLY CHANGE Q0 (tokens)": dict(
        what="Issuance minus burn over Q0 (A4's figure).",
        formula="self-reported net mint where published, else issuance − burn on the A4 basis; BLOCKED on unequal "
                "coverage", window="Q0", unit="tokens",
        inputs=["gross_issuance_tokens", "gross_burn_tokens", "net_mint_monthly"]),
    "Net supply change, annualised % of circulating": dict(
        what="Net inflation rate (+) or deflation (−) per year, of circulating.",
        formula="(issuance annualised − burn annualised) ÷ circulating used; BLOCKED with the net total when the two "
                "sides' coverage differs", window="Q0 → year", unit="%",
        decided=[(J, "2026-10-11", "the same equal-coverage guard as the net total (sanity run, mixed_windows)")]),
    "Fees ÷ issuance": dict(
        what="Fees per dollar of new issuance (A1's key ratio).",
        formula="fees per day ÷ (issuance per day × Q0 average price)", window="Q0", unit="x",
        inputs=["fees_usd", "gross_issuance_tokens", "price_usd"]),
    "Fees ÷ FDV (annualised)": dict(what="Annualised fees as % of FDV.", formula="fees_usd annualised ÷ fdv_usd now",
                                    window="Q0 → year", unit="%", inputs=["fees_usd", "fdv_usd"]),
    "Buyback % of supply": dict(
        what="The implied buyback (the documented split) annualised, as % of circulating (A3's figure).",
        formula="revenue annualised × the window's share ÷ Q0 average price ÷ circulating used", window="Q0 → year",
        unit="%", inputs=["revenue_usd", "price_usd", "circulating_supply_used"]),
    "Fees Δ": dict(what="Change in fees between windows.", formula="fees window ÷ the earlier window − 1",
                   window="30d / 3m / 6m / 9m", unit="%", inputs=["fees_usd"]),
    "Latest data date (core series)": dict(what="The newest stored date among price, fees, revenue and TVL.",
                                           formula="max latest_date of price_usd, fees_usd, revenue_usd, tvl_usd",
                                           window="—", unit="date"),
    "Notes": dict(what="Free text from config.", formula="config notes", window="—", unit="text"),
    # ------------------------------------------------------------------ A1
    "VALIDATOR STAKING YIELD": dict(
        what="What securing the chain pays a staker per year — NOT a protocol revenue share.",
        formula="per chain (config.VALIDATOR_YIELD): rewards paid ÷ stake over the window, net of commission where the "
                "chain publishes it", window="Q0 or the chain's own rate", unit="%"),
    "Gross burn Q0 (tokens) — every burn": dict(
        what="Every token the supply lost to burns over Q0 — fee-market burns included (they feed net issuance here).",
        formula="config.a1_burn_metric: the A4 burn, or where A4 is narrower (Hyperliquid: the Assistance Fund) every burn "
                "(total_burn_tokens)", window="Q0", unit="tokens",
        decided=[(J, "2026-10-12", "Phase 2 amendment 1: fee-market burns (ETH, NEAR gas, Tron, Solana, Canton, HyperEVM "
                                   "gas) come off A4 entirely and feed net issuance on A1")]),
    # ===== CP — the comparability layer (Jake 2026-10-12, Phase 2) =====
    "TRADEABLE FLOAT": dict(
        what="The tokens that can trade: circulating (our convention) less staked / locked tokens. Protocol- and "
             "treasury-held tokens are already outside circulating under the convention, so they are subtracted there.",
        formula="circulating_supply_used − the lock inside it (A2's free-float lock; Ethereum: beacon_chain_eth, "
                "config.CP_STAKE_METRIC)", window="now", unit="tokens",
        inputs=["circulating_supply_used", "locked_tokens"], decided=[(J, "2026-10-12", "Phase 2 item 1")]),
    "HOLDER VALUE YIELD": dict(
        what="Value delivered to holders out of REVENUE, per year, as a share of market cap.",
        formula="holder_value_revenue_usd (each day's buyback $ where its programme was revenue-funded that day, plus "
                "the declared payouts to holders) annualised over its covered days ÷ market cap",
        window="Q0 → year", unit="%",
        counts="revenue-funded buybacks (retired and distributed), declared payouts (Aerodrome voter fees, Sky USDS) "
               "and Aerodrome's LIQUID bribes to voters (decision 4 of 2026-10-12 evening: emissions count as dilution, so "
               "the bribes paid to direct them count as holder income; the illiquid-token rule applies; fees-only beside)",
        excludes="treasury- or emissions-funded buybacks (recorded beside it, never credited — LITMUS A); fee-market "
                 "burns (on A1 net issuance, amendment 1)",
        inputs=["holder_value_revenue_usd", "market_cap_usd"],
        decided=[(J, "2026-10-12", "Phase 2 item 3 and LITMUS A"),
                 (J, "2026-10-12", "liquid bribes credited (Aerodrome), one-off treasury burns never (Uniswap)")]),
    "NET HOLDER YIELD": dict(
        what="Holder value yield less dilution: what holders gain net of new tokens entering circulation.",
        formula="holder value yield − (outflow_a/b/c/d/e_usd, each annualised over its covered days) ÷ market cap; "
                "BLOCKED where any leg covers under a week", window="Q0 → year", unit="%",
        inputs=["holder_value_revenue_usd", "outflow_a_usd", "outflow_b_usd", "outflow_c_usd", "outflow_d_usd",
                "outflow_e_usd", "market_cap_usd"],
        decided=[(J, "2026-10-12", "Phase 2 item 3 (the sell-rate-weighted version follows Phase 5)")]),
    "Issuer releases Q0 (tokens)": dict(
        what="Pre-minted supply released into circulation by the issuer in Q0 — a RELEASE, not issuance (nothing is "
             "minted on a pre-minted token).",
        formula="Σ pool_release_tokens over Q0 (−d(the named non-circulating wallets) per consecutive day on the same "
                "wallet set, or the project's first-party release); a day the wallets RECEIVED reads 0, never negative",
        window="Q0", unit="tokens", counts="Chainlink, GEODNET, Aethir, Maple, Hyperliquid (config.issuance_basis)",
        excludes="minted issuance (net issuance on a pre-minted token is mint − burn = −burn)",
        inputs=["pool_release_tokens"],
        decided=[(J, "2026-10-13", "accuracy pass on 1e702bb, item 9; alignment round B8")]),
    "SUBSIDY COVER": dict(
        what="How many dollars of revenue each dollar of supply-side incentives buys.",
        formula="revenue (customer_revenue_usd where the project has it, else fees_usd) per covered day ÷ "
                "outflow_b_usd per covered day; beside it ÷ (b + c)", window="Q0", unit="x",
        inputs=["fees_usd", "customer_revenue_usd", "outflow_b_usd", "outflow_c_usd"],
        decided=[(J, "2026-10-12", "Phase 2 item 5, amendment 3")]),
    "BUSINESS 1 — core usage": dict(
        what="The first usage / customer series the project's business is judged on (config.BUSINESS_SLOTS): DEX "
             "volume, loans outstanding, USDS supply, CCIP messages, stations, intents volume, L2 activity, TVL …",
        formula="a STOCK reads its latest value (a shifted quarter: that quarter's mean); a FLOW its Q0 total ÷ covered "
                "days × 365, BLOCKED below the minimum coverage. The column to its left names the series, its unit, its "
                "source kind (DASHBOARD where it is an aggregator's reconstruction) and what an on-chain replacement "
                "would need", window="now / Q0", unit="per series ($, count, tokens)",
        counts="one series per slot, per project", excludes="nothing is summed across series or projects",
        inputs=["protocol_dex_volume_usd", "dex_volume_v2_usd", "dex_volume_v3_usd", "dex_volume_v4_usd",
                "open_interest_usd", "near_intents_volume_usd", "stake_tvl_usd", "cash_volume_usd",
                "loans_outstanding_usd", "l2_tx_count", "stations_total", "ccip_messages", "usds_supply_tokens"],
        decided=[(J, "2026-10-12", "overnight review of ab5f2a9, Phase 4")]),
    "MARKET SHARE, SECOND BASIS": dict(
        what="A second market-share basis beside the first (config.MARKET_SHARE_2): Ethereum's and NEAR's chain FEES ÷ "
             "the 13-chain set — fees are what a chain sells; the chain-TVL share stays beside it.",
        formula="own chain's daily fees ÷ the sum over ethereum, solana, tron, bsc, bitcoin, base, arbitrum, polygon, "
                "avalanche, sui, aptos, near, hyperliquid-l1 (DefiLlama dailyFees per chain); a day only where every "
                "chain reported (0 before a chain's first day)", window="now", unit="%",
        inputs=["market_share_2_pct", "market_share_2_category_usd"],
        decided=[(J, "2026-10-13", "decision 5 on 3500042")]),
    "BUSINESS 2 — core usage": dict(
        what="The second usage series (config.BUSINESS_SLOTS), as BUSINESS 1.", formula="as BUSINESS 1",
        window="now / Q0", unit="per series", decided=[(J, "2026-10-12", "overnight review of ab5f2a9, Phase 4")]),
    "BUSINESS 3 — core usage": dict(
        what="The third usage series (config.BUSINESS_SLOTS), as BUSINESS 1.", formula="as BUSINESS 1",
        window="now / Q0", unit="per series", decided=[(J, "2026-10-12", "overnight review of ab5f2a9, Phase 4")]),
    "BUSINESS 4 — core usage": dict(
        what="The fourth usage series (config.BUSINESS_SLOTS), as BUSINESS 1.", formula="as BUSINESS 1",
        window="now / Q0", unit="per series", decided=[(J, "2026-10-12", "overnight review of ab5f2a9, Phase 4")]),
    "MARKET SHARE vs its category": dict(
        what="The project's share of its category on one measure, against a peer set written down per project "
             "(config.MARKET_SHARE; the basis column beside it).",
        formula="own ÷ category per day (fetch/business.py): DEX volume ÷ DefiLlama's Dexs category (Uniswap, "
                "Aerodrome); perp open interest ÷ Derivatives (Hyperliquid); borrowed ÷ 11 lenders (Maple, Morpho, "
                "Fluid); Ethereum TVL ÷ 9 ETH LST/LRT peers (Ether.fi); TVL ÷ 4 yield-tokenization peers (Pendle); "
                "USDS + DAI ÷ USD stablecoins (Sky); chain TVL ÷ all chains (Ethereum, NEAR). A day a launched peer "
                "did not report is dropped, never read as 0", window="now", unit="%",
        excludes="Chainlink, Plume, Aethir, GEODNET: n/a with the reason (no free comparable basis)",
        inputs=["market_share_pct", "market_share_category_usd"],
        decided=[(J, "2026-10-12", "overnight review of ab5f2a9, Phase 3")]),
    "NET ISSUANCE AFTER BURN Q0 (tokens)": dict(
        what="Issuance less every burn over Q0 — the supply's net change (+ inflation).",
        formula="the A4 net-change expression on every burn: common days where declared, self-reported net mint where "
                "published, else issuance − burn under the equal-coverage guard", window="Q0", unit="tokens",
        decided=[(J, "2026-10-12", "Phase 2 amendment 1")]),
    "NET ISSUANCE AFTER BURN, annualised": dict(
        what="Net issuance per year as a fraction of circulating (+ inflation) — the net inflation Total Yield subtracts.",
        formula="(issuance − every burn), each annualised over its covered days, ÷ circulating", window="Q0", unit="%",
        decided=[(J, "2026-10-12", "Phase 2 amendment 1")]),
    "TOTAL YIELD — stakers": dict(
        what="What a staker earns in real terms: the validator staking yield less the dilution of net inflation.",
        formula="validator staking yield − net inflation, where net inflation is A4's 'NET SUPPLY CHANGE, annualised % "
                "of circulating' (the figure Master shows): issuance (or pool release) − burn, each annualised over its "
                "covered days, ÷ circulating; burn only where issuance is declared zero",
        window="Q0 / the chain's own rate", unit="%",
        excludes="nothing paid from issuer reserves: staking rewards and foundation releases paid out of issuer-held "
                 "wallets are dilution too (config.TOTAL_YIELD_RELEASES, per project with why nothing counts twice)",
        inputs=["gross_issuance_tokens", "gross_burn_tokens", "circulating_supply_used", "emissions_tokens",
                "unlock_release_tokens", "unlock_release_counted_tokens"],
        decided=[(J, "2026-10-12", "decision 9 on 6f8c86c: stakers = validator yield − net inflation; non-stakers = "
                                   "− net inflation; benchmark the 3-month T-bill (Phase 3)"),
                 (J, "2026-10-12", "overnight item 17: issuer releases (rewards and unlocks from issuer reserves) are "
                                   "subtracted beside net inflation")]),
    "Total yield — non-stakers": dict(
        what="What a holder who does not stake earns: minus net inflation and issuer releases (a net burn is a "
             "positive yield).",
        formula="− (net supply change annualised ÷ circulating + issuer releases annualised ÷ circulating)",
        window="Q0", unit="%",
        decided=[(J, "2026-10-12", "decision 9 on 6f8c86c; overnight item 17")]),
    "NRR ON SETTLEMENT VOLUME": dict(
        what="Market cap ÷ trailing-365d settlement volume (Artemis definition: DEX + NFT + P2P).",
        formula="market_cap_usd ÷ settlement_volume (365d sum)", window="365d", unit="x",
        inputs=["network_reserve_ratio"], decided=[(J, "2026-09-30", "NRR on Artemis settlement volume")]),
    "NRR ON TRADING THROUGHPUT": dict(
        what="Market cap ÷ trailing-365d DEX + perps volume (DefiLlama).", formula="market cap ÷ 365d volume",
        window="365d", unit="x", inputs=["network_reserve_ratio_throughput"]),
    "TVL — chain ($)": dict(what="Chain TVL, A1's primary demand metric.", formula="tvl_usd now", window="now",
                            unit="$", inputs=["tvl_usd"]),
    "FEES ÷ ISSUANCE (x) — key ratio": dict(
        what="Fees per dollar of new issuance.", formula="fees per day ÷ (issuance per day × Q0 average price); "
                                                         "BLOCKED on short coverage",
        window="Q0", unit="x", inputs=["fees_usd", "gross_issuance_tokens", "price_usd"]),
    # ------------------------------------------------------------------ A2
    "FREE FLOAT = circulating − locked (tokens)": dict(
        what="Circulating supply not locked or staked.",
        formula="circulating used − the locked tokens INSIDE it (config.free_float_lock_metrics)", window="now",
        unit="tokens", inputs=["circulating_supply_used", "locked_tokens"],
        decided=[(J, "2026-09-30", "locked tokens already out of circulating are subtracted once")]),
    "FREE FLOAT ÷ ARR (x)": dict(
        what="Free-float market cap ÷ annual recurring customer revenue.",
        formula="free float × spot ÷ the project's own ARR where published, else customer revenue annualised",
        window="now / Q0 → year", unit="x", inputs=["customer_revenue_usd", "arr_usd"],
        decided=[(J, "2026-10-01", "the project's own ARR is the headline where it publishes one")]),
    "SUPPLY TRAJECTORY": dict(
        what="Annual dilution from emissions, % of free float.", formula="emissions_tokens annualised ÷ free float",
        window="Q0 → year", unit="%", inputs=["emissions_tokens"],
        excludes="unlocks (see TOTAL DILUTION)", decided=[(J, "2026-09-24", "releases are not emissions")]),
    "Customer revenue Q0 ($)": dict(what="What customers paid over Q0.", formula="customer_revenue_usd q0",
                                    window="Q0", unit="$", inputs=["customer_revenue_usd"]),
    "Emissions to suppliers Q0 (tokens)": dict(what="Tokens paid to suppliers over Q0.", formula="emissions_tokens q0",
                                               window="Q0", unit="tokens", inputs=["emissions_tokens"]),
    "CUSTOMER REVENUE PER TOKEN EMITTED": dict(
        what="Customer revenue per token emitted.", formula="customer revenue per day ÷ emissions per day",
        window="Q0", unit="$/token", inputs=["customer_revenue_usd", "emissions_tokens"]),
    "Customer revenue ÷ emissions value (x)": dict(
        what="Customer revenue ÷ the dollar value of emissions.", formula="revenue per day ÷ (emissions per day × "
                                                                          "Q0 average price)",
        window="Q0", unit="x", inputs=["customer_revenue_usd", "emissions_tokens", "price_usd"]),
    # ------------------------------------------------------------------ A3
    "CIRCULATING RETIREMENT RATE (tokens)": dict(
        what="Tokens RETIRED by buybacks per year, % of circulating — primary.",
        formula="retired buyback tokens (burned + held) annualised ÷ circulating used", window="Q0 → year", unit="%",
        inputs=["retired_buyback_tokens", "actual_buyback_tokens"], counts="burned + held (treasury, reserve, "
                                                                           "foundation-locked)",
        excludes="buybacks paid out to stakers (staking yield)",
        decided=[(J, "2026-10-10", "retirement = burned + held; distributed excluded")]),
    "FDV RETIREMENT RATE (tokens)": dict(
        what="The same retired tokens per year, % of the FDV supply.", formula="retired annualised ÷ the total supply FDV uses (FDV = price x total_supply); each quarter on its own total",
        window="Q0 → year", unit="%", inputs=["retired_buyback_tokens", "fdv_usd"],
        decided=[(J, "2026-10-10", "retirement = burned + held")]),
    "BUYBACK AS % OF SUPPLY (annualised, implied)": dict(
        what="What the documented split would retire per year, % of circulating.",
        formula="revenue base annualised × the window's share ÷ Q0 average price ÷ circulating used",
        window="Q0 → year", unit="%", inputs=["revenue_usd", "price_usd"]),
    "Actual buyback as % of supply (annualised)": dict(
        what="Retired buyback per year, % of circulating.", formula="retired buyback tokens annualised ÷ circulating",
        window="Q0 → year", unit="%", inputs=["retired_buyback_tokens"]),
    "Net absorption Q0 (tokens)": dict(
        what="Tokens retired minus tokens emitted over Q0.",
        formula="retired buyback tokens − emissions (config.absorption_emission_metrics); BLOCKED on unequal "
                "coverage; a supply-ADDITIVE buyback (Aethir) is added to issuance instead", window="Q0",
        unit="tokens", inputs=["retired_buyback_tokens", "emissions_tokens", "gross_issuance_tokens"],
        excludes="unlocks (see Net absorption incl. unlocks)",
        decided=[(J, "2026-10-11", "Aerodrome subtracts gauge emissions (Minter) + the rebase, as Pendle (A3)")]),
    "PROTOCOL STAKING YIELD": dict(
        what="What a real staker earns per staked token from the protocol's revenue share — NOT a validator yield.",
        formula="tokens paid to stakers per year ÷ reward-bearing stake (real + virtual); dollars converted at each "
                "payment day's price", window="Q0 → year", unit="%",
        decided=[(J, "2026-10-09", "payment-day price convention")]),
    "Buyback to LOCKED supply": dict(
        what="The held buyback balance where the destination is a hold.", formula="buyback_fund_balance now",
        window="now", unit="tokens", inputs=["buyback_fund_balance"]),
    # ------------------------------------------------------------------ A4
    "PERMANENT BURN YIELD": dict(
        what="Tokens burned per year, % of circulating.", formula="A4 burn annualised ÷ circulating used",
        window="Q0 → year", unit="%", inputs=["gross_burn_tokens"],
        excludes="PancakeSwap: closed (the dead-address inflow is ~99% mint-and-burn, 2026-10-11)"),
    "BURN ÷ ISSUANCE (x)": dict(
        what="Burn per token issued (the crossover: > 1 is net deflation).", formula="burn per day ÷ issuance per day "
                                                                                     "(pool release where pre-minted)",
        window="Q0", unit="x", inputs=["gross_burn_tokens", "gross_issuance_tokens", "pool_release_tokens"]),
    "Q0 WINDOW CAVEAT": dict(what="Why the yield and crossover beside it are not yet a rate, with numbers.",
                             formula="text", window="Q0", unit="text"),
    "NET SUPPLY CHANGE Q0 (tokens) — self-reported": dict(
        what="Issuance minus burn over Q0.", formula="self-reported net mint where published, else issuance − burn; "
                                                     "BLOCKED on unequal coverage", window="Q0", unit="tokens"),
    "NET SUPPLY CHANGE, annualised % of circulating": dict(
        what="Net inflation (+) or deflation (−) per year, % of circulating.",
        formula="each side annualised over its covered days, difference ÷ circulating; BLOCKED with the total on "
                "unequal coverage", window="Q0 → year", unit="%",
        decided=[(J, "2026-10-11", "same equal-coverage guard as the total (sanity run)")]),
    "GROSS BURN Q0 (tokens)": dict(what="Tokens destroyed over Q0.", formula="config.a4_burn_metric q0", window="Q0",
                                   unit="tokens", inputs=["gross_burn_tokens"]),
    "GROSS ISSUANCE Q0 (tokens)": dict(what="Tokens minted over Q0.", formula="gross_issuance_tokens q0", window="Q0",
                                       unit="tokens", inputs=["gross_issuance_tokens"]),
    "Pool release Q0 (tokens)": dict(what="Pre-minted tokens released from distribution pools over Q0.",
                                     formula="pool_release_tokens q0", window="Q0", unit="tokens",
                                     inputs=["pool_release_tokens"]),
    # ------------------------------------------------------------------ the unlock block (A2, A3, A4)
    "UNLOCK DILUTION Q0 (tokens)": dict(
        what="Tokens released from the project's non-circulating wallets over Q0 — NOT emissions.",
        formula="per day, the fall in the summed stock of config.unlock_stock_metrics since the previous read by the "
                "SAME wallet set (a rise releases 0; a weekly read's fall is spread over its days)", window="Q0",
        unit="tokens", inputs=["unlock_release_tokens"],
        counts="releases from team / investor / foundation / treasury / reserve wallets",
        excludes="burns, the buyback fund (Chainlink's Reserve), a wallet added to the set (a change of measuring "
                 "point, not an unlock), inflows",
        decided=[(J, "2026-09-24", "releases are not emissions (EMISSIONS_ALIAS_DECLINED)"),
                 (J, "2026-10-11", "unlock dilution as its own row; TOTAL DILUTION = emissions + unlocks (A2)")]),
    "Unlock dilution, annualised % of free float": dict(
        what="Unlocks per year, % of free float.", formula="unlock_release_tokens annualised ÷ free float",
        window="Q0 → year", unit="%", inputs=["unlock_release_tokens"]),
    "TOTAL DILUTION = emissions + unlocks": dict(
        what="Emissions plus unlocks per year, % of free float.",
        formula="(each absorption emissions series annualised + unlocks annualised) ÷ free float", window="Q0 → year",
        unit="%", inputs=["emissions_tokens", "unlock_release_tokens"],
        decided=[(J, "2026-10-11", "decision A2")]),
    "Net absorption incl. unlocks Q0 (tokens)": dict(
        what="Retired buyback minus emissions minus unlocks over Q0.",
        formula="net absorption − unlock_release_tokens; BLOCKED on unequal coverage", window="Q0", unit="tokens",
        inputs=["retired_buyback_tokens", "emissions_tokens", "unlock_release_tokens"],
        decided=[(J, "2026-10-11", "decision A2")]),
    "Emissions — the rate IN FORCE, annual (tokens)": dict(
        what="The emission rate the current schedule pays, annualised — beside the Q0 measured blend.",
        formula="Sky: DssVest.awards(VestedRewardsDistribution.vestId()).tot ÷ (fin − bgn) x 365 days (fetch/sky_mkr.py)",
        window="now", unit="tokens / year", inputs=["emissions_rate_current_annual_tokens"],
        decided=[(J, "2026-10-12", "decision 2 on 6f8c86c: emissions shown as the Q0 blend AND the rate in force")]),
    "RETIREMENT ÷ DILUTION at the emission rate IN FORCE (x)": dict(
        what="The crossover ratio with emissions at the rate in force; forward-looking rows (years to crossover) use it.",
        formula="retired_buyback_tokens per covered day ÷ (the rate in force ÷ 365 + other emission legs + unlocks, per "
                "covered day)", window="Q0 / now", unit="x",
        inputs=["retired_buyback_tokens", "emissions_rate_current_annual_tokens", "unlock_release_tokens"],
        decided=[(J, "2026-10-12", "decision 2 on 6f8c86c")]),
    "RETIREMENT ÷ DILUTION (x)": dict(
        what="Retired buyback against dilution as a ratio: 1.0 is the crossover where buybacks absorb what emissions "
             "and unlocks add.",
        formula="retired_buyback_tokens per covered day ÷ (the tab's absorption emissions + unlock_release_tokens, "
                "each per its own covered day); unlocks only where the project has a non-circulating wallet set; a "
                "structural 0 retirement reads 0; BLOCKED where a side covers under a week",
        window="Q0", unit="x",
        inputs=["retired_buyback_tokens", "emissions_tokens", "unlock_release_tokens"],
        decided=[(J, "2026-10-12", "theory layer Phase 1: the retirement-vs-dilution benchmark's series")]),
}


def entry_for(header: str) -> tuple[str, dict] | None:
    """The longest ENTRIES prefix the header starts with."""
    best = None
    for k, v in ENTRIES.items():
        if header.startswith(k) and (best is None or len(k) > len(best[0])):
            best = (k, v)
    return best
