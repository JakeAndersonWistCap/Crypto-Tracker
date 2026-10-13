# SPEC — the single definition of "correct" (alignment round, 2026-10-13)

Everything the workbook shows is built and tested against this file. A figure that does not match its row here is a
bug; a figure with no row here is a gap in this file. Written before the code changes of the alignment round (Jake's
"FULL ALIGNMENT ROUND", 2026-10-13); the Coverage tab and `coverage_audit.csv` check the built workbook against it.

**Status legend.** `BUILT` = built and matches this row · `FIX` = built, wrong, fixed this round (the item named) ·
`NEW` = built this round · `NOT WIRED` = no route wired yet (the route is named) · `LATER` = deliberately not this
round. **PROPOSED** marks a definition or mapping that is Jake's to confirm; the conservative reading is built and the
cell note says PROPOSED.

---------------------------------------------------------------------------------------------------------------------
## 1. Rules the code must follow

1. **ONE COMPUTATION PER CONCEPT.** Float, lock rate, stake inside circulating, emissions, unlocks, issuance, burn,
   net issuance, HVY, NHY, FDV and dilution are computed ONCE (a read-time view or one shared formula builder in
   `build_workbook.py`) and every tab READS that value. No tab-local versions. The cross-tab test fails the build when
   a concept shown on more than one tab differs between tabs.
2. **NO UNEXPLAINED BLANKS.** Every headline cell is one of: a value · `n/a — <reason>` (the quantity does not exist
   for this project) · `0 — <reason>` (it exists and is genuinely zero: a programme that ended, a fixed supply that
   mints nothing) · `BLOCKED — <reason>` (it exists but the inputs cannot support it yet — unequal coverage, a series
   break) · `NOT WIRED — <route>` (it exists, no source is wired; the route is named). A bare `n/a` is a bug.
3. **0 IS NOT n/a.** Where the quantity is genuinely zero the cell reads 0 with the reason (Maple emissions after
   MIP-019 / Drips' end, NEAR unlocks where no lockup released, Fluid holder value after the 2026-05-11 halt).
4. **EXPLICIT DIRECTION ON EVERY ROW** (+1 higher is better · −1 lower is better · 0 neutral). The Trajectory never
   prints "direction not declared".
5. **AXIS ON EVERY ROW.** BUSINESS · CAPTURE · ALIGNMENT · CONTEXT (section 2). Shown as a column on CP and Trajectory.
6. **RANGES THAT FAIL THE BUILD.** Lock rate and float ÷ circulating in 0..1; float ≥ 0; whole-token yield ≤ staker
   yield; releases ≥ 0; issuance ≥ 0. A flow statement whose residual exceeds 0.5% carries a CHECK note.
7. **NET ISSUANCE = MINT − BURN.** A pre-minted supply mints nothing: its issuer RELEASES are shown on their own line
   and are never inside net issuance (Total Yield subtracts them explicitly).
8. **STAKE INSIDE CIRCULATING.** Every float, lock rate and whole-token yield reads the stake LESS the stake held by
   wallets already outside circulating (`_stake_inside` / `_ff_lock`; Hyperliquid's team, Foundation and Grants stake).
9. **COVERAGE.** A flow annualised over the days it covered; two sides of a net or ratio over materially different
   spans are BLOCKED (`_coverage_guard same_span`). A series whose older leg stops short of its current leg has a hole
   between them, not zeros (coverage counts from the current leg).
10. **BASIS.** A change of measuring point is a SERIES BREAK on the Trajectory unless declared as a handover; history is
    restated on the current basis where a route exists (section 7). A provider's reclassification is a break
    (`config.SOURCE_RECLASSIFICATIONS`).
11. **MODELLED legs** are labelled, never inside Q0, excluded from scoring.
12. **SOURCES.** Primary first (protocol docs, contracts, governance, on-chain); a dashboard source says so on its row
    with what an on-chain replacement needs. Nothing filled from inference.

---------------------------------------------------------------------------------------------------------------------
## 2. Axes (mapping; PROPOSED where marked)

| Axis | Holds |
|---|---|
| BUSINESS | usage, customers, fees, revenue, volume, TVL, market share, Business 1–4, fees ÷ issuance (PROPOSED) |
| CAPTURE | holder value yield, net holder yield (PROPOSED), payout ratios, buyback / burn, retirement, protocol yield, validator yield and Total Yield (PROPOSED), capture status, switch power |
| ALIGNMENT | dilution (a–e), unlocks, gross / net issuance, net supply change, discretionary supply, insider share, subsidy cover, supplier payout, leakage, uniformity, lock terms, destination durability |
| CONTEXT | price, market cap, FDV, circulating, float, lock rate (PROPOSED), valuation multiples (NRR, free float ÷ ARR, fees ÷ FDV — PROPOSED), T-bill, SSR, ETF / DAT holdings |

## 3. Trajectory methods

* `4Q log-linear` — five consecutive 90-day blocks (Q0, Q-1..Q-4), the row's own formula each; growth = log-linear
  slope over the quarters on one basis.
* `rolling T12M` — a discrete / lumpy flow (unlocks, buybacks with a cadence, monthly series): direction and growth
  from Q0..Q-3 vs Q-1..Q-4 sums.
* `stock-now` — a stock: Q0 = the latest value, earlier quarters = each quarter's mean; growth log-linear on those.

---------------------------------------------------------------------------------------------------------------------
## 4. Concept registry (machine-readable — parsed by `spec.py`)

Columns: key | concept | definition | formula | unit | axis | dir | trajectory | tabs | tier | status | inputs.
`dir` is +1 / −1 / 0. `tier` is the best source tier the concept reads (1 aggregator/API, 2 on-chain, 3 page /
manual). `inputs` are the config.METRICS series the concept may read (the Coverage audit's MATCHES-SPEC test).

<!-- SPEC:CONCEPTS -->
| key | concept | definition | formula | unit | axis | dir | trajectory | tabs | tier | status | inputs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| price | Price | latest market price | price_usd now (Q-k: quarter mean) | $ | CONTEXT | 0 | stock-now | Master,A1,A2,A3,A4,CP | 1 | BUILT | price_usd |
| market_cap | Market cap | price × the circulating used | price_usd × circulating_supply_used | $ | CONTEXT | 0 | stock-now | Master,A1,A2,A3,A4,CP | 1 | BUILT | market_cap_usd,price_usd,circulating_supply_used |
| fdv | FDV | price × the total supply used (on-chain first; CoinGecko's FDV before our first total, a declared handover) | price_usd × total_supply | $ | CONTEXT | 0 | stock-now | Master,A3 | 1 | FIX B6 | fdv_usd,price_usd,total_supply |
| circulating | Circulating supply (used) | total − treasury − team/investor unvested − foundation − operating reserves − burned; staked included; per-project basis section 5 | config.circulating_primary_metric | tokens | CONTEXT | 0 | stock-now | Master,A1,A2,A3,A4,CP | 2 | FIX C7 | circulating_supply_used,circulating_supply |
| stake_inside | Stake / locks inside circulating | the stake (or lock) LESS the stake held by wallets already outside circulating | locked_tokens (+ declared extra legs) − locked_excluded_metrics | tokens | CONTEXT | 0 | stock-now | A1,A2,A3,CP | 2 | FIX B1 | locked_tokens,staked_noncirculating_tokens,beacon_chain_eth,ve_locked_supply_tokens |
| float | Tradeable float | circulating − stake inside circulating | circulating_supply_used − stake_inside | tokens | CONTEXT | 0 | stock-now | A1,A2,A3,CP | 2 | FIX B1 | circulating_supply_used,locked_tokens,staked_noncirculating_tokens |
| lock_rate | Lock rate | stake inside circulating ÷ circulating (PROPOSED axis CONTEXT) | stake_inside ÷ circulating | % | CONTEXT | 1 | stock-now | A1,A3,CP | 2 | FIX B1 | locked_tokens,staked_noncirculating_tokens,circulating_supply_used |
| tbill | 3-month T-bill | US Treasury 13-week coupon equivalent | tbill_3m_yield | % | CONTEXT | 0 | stock-now | A1,A3,CP | 1 | BUILT | tbill_3m_yield |
| ssr | Sky Savings Rate | sUSDS ssr() on-chain, annual | sky_savings_rate | % | CONTEXT | 0 | stock-now | A1,A3,CP | 2 | BUILT | sky_savings_rate |
| etf_holdings | ETF / ETP holdings | latest dated issuer reading per product, summed; ÷ circulating beside | etf_dat_holdings.json | tokens | CONTEXT | 0 | stock-now | CP | 3 | BUILT | |
| dat_holdings | Treasury-company holdings | latest dated filing per company, summed; ÷ circulating beside | etf_dat_holdings.json | tokens | CONTEXT | 0 | stock-now | CP | 3 | BUILT | |
| nrr | NRR on settlement / throughput volume | market cap ÷ trailing-365d volume (PROPOSED axis CONTEXT) | market_cap_usd ÷ settlement_volume_365d_usd | x | CONTEXT | -1 | 4Q log-linear | A1 | 1 | BUILT | market_cap_usd,settlement_volume_365d_usd,trading_throughput_365d_usd |
| ff_arr | Free float ÷ ARR | free-float market value ÷ annualised customer revenue (PROPOSED axis CONTEXT) | (float × price) ÷ customer_revenue_usd annualised | x | CONTEXT | -1 | 4Q log-linear | A2 | 1 | BUILT | customer_revenue_usd,price_usd,circulating_supply_used,locked_tokens |
| fees_fdv | Fees ÷ FDV | annualised fees ÷ FDV (PROPOSED axis CONTEXT) | fees_usd annualised ÷ fdv_usd | % | CONTEXT | 1 | 4Q log-linear | Master,A3 | 1 | BUILT | fees_usd,fdv_usd |
| fees | Fees Q0 | fees paid by users in Q0 | Σ fees_usd over Q0 | $ | BUSINESS | 1 | 4Q log-linear | Master,A1,A3 | 1 | BUILT | fees_usd |
| revenue | Revenue Q0 | protocol revenue on the project's documented base; NEAR = gas + Intents fee switch (C10) | Σ revenue_usd (config.revenue_base_metric) | $ | BUSINESS | 1 | 4Q log-linear | Master,A3 | 1 | FIX C10 | revenue_usd,revenue_usd_reported,near_intents_revenue_usd |
| customer_revenue | Customer revenue Q0 | what customers paid (A2) | Σ customer_revenue_usd | $ | BUSINESS | 1 | 4Q log-linear | A2 | 1 | BUILT | customer_revenue_usd |
| tvl | TVL | chain TVL (A1) or protocol TVL; Plume's DefiLlama reclassifications are breaks | tvl_usd / protocol_tvl_usd now | $ | BUSINESS | 1 | stock-now | Master,A1 | 1 | FIX B7 | tvl_usd,protocol_tvl_usd |
| tvl_native | TVL in native units | the TVL's underlying units (ETH staked for Ether.fi; USDS for Sky) so business trends are not price | per project section 5 | native | BUSINESS | 1 | stock-now | CP | 1 | NEW C15 | eth_staked_etherfi_tokens,usds_supply_tokens |
| settlement_volume | Settlement / throughput volume | value settled on the chain | settlement_volume_usd | $ | BUSINESS | 1 | 4Q log-linear | A1 | 1 | BUILT | settlement_volume_usd,trading_throughput_usd |
| business_n | Business 1–4 | the usage / customer series each business is judged on (config.BUSINESS_SLOTS); a stock reads now, a flow Q0 annualised | per slot | per series | BUSINESS | 1 | 4Q log-linear | CP | 1 | BUILT | |
| market_share | Market share | own ÷ category on one measure, peer set written down (config.MARKET_SHARE); second basis chain fees for ETH / NEAR | market_share_pct, market_share_2_pct | % | BUSINESS | 1 | stock-now | CP | 1 | BUILT | market_share_pct,market_share_2_pct |
| boros | Pendle Boros fees / volume | Boros on its own lines; Pendle's revenue base stays per docs | boros_fees_usd, boros_volume_usd | $ | BUSINESS | 1 | 4Q log-linear | CP | 1 | NEW C16 | boros_fees_usd,boros_volume_usd |
| fees_issuance | Fees ÷ issuance | fees per $ of issuance at the Q0 price (PROPOSED axis BUSINESS) | fees_usd rate ÷ (gross_issuance_tokens rate × price) | x | BUSINESS | 1 | 4Q log-linear | Master,A1 | 1 | BUILT | fees_usd,gross_issuance_tokens,price_usd |
| validator_yield | Validator staking yield | consensus + execution (ETH: d Eth2Staking + MEV-incl. execution ÷ beacon stake; others per config.VALIDATOR_YIELD) (PROPOSED axis CAPTURE) | config.VALIDATOR_YIELD | % | CAPTURE | 0 | 4Q log-linear | A1,CP | 2 | FIX B3 | consensus_rewards_tokens,execution_rewards_eth,beacon_chain_eth,emissions_tokens,locked_tokens,reward_emission_rate_annual |
| total_yield | Total Yield — stakers / non-stakers | validator yield − (net inflation + issuer releases); non-stakers − (net inflation + releases) (PROPOSED axis CAPTURE) | vyield − (net_issuance_pct + releases ÷ circulating) | % | CAPTURE | 1 | 4Q log-linear | A1 | 2 | FIX C8 | gross_issuance_tokens,gross_burn_tokens,pool_release_tokens,emissions_tokens,unlock_release_tokens,unlock_release_counted_tokens,circulating_supply_used |
| protocol_yield | Protocol staking yield | tokens paid to stakers per year ÷ reward-bearing stake | config.PROTOCOL_YIELD | % | CAPTURE | 1 | 4Q log-linear | A3,CP | 2 | BUILT | |
| whole_token_yield | Whole-token yield | staker yield × stake inside circulating ÷ circulating (≤ staker yield) | yield × stake_inside ÷ circulating | % | CAPTURE | 1 | 4Q log-linear | CP | 2 | FIX B1 | locked_tokens,staked_noncirculating_tokens,circulating_supply_used |
| gross_burn | Gross burn Q0 | every burn the supply sees (fee-market burns included) | Σ config.a1_burn_metric | tokens | CAPTURE | 1 | 4Q log-linear | Master,A1,A4 | 2 | BUILT | gross_burn_tokens,core_burn_tokens |
| actual_buyback | Actual buyback Q0 | tokens / $ bought by the programme | Σ actual_buyback_tokens / _usd | $ | CAPTURE | 1 | rolling T12M | Master,A3 | 2 | FIX B11 | actual_buyback_tokens,actual_buyback_usd |
| implied_buyback | Implied buyback Q0 | revenue × the split that applied to the window | revenue × share | $ | CAPTURE | 1 | 4Q log-linear | Master,A3 | 1 | BUILT | revenue_usd |
| retirement | Retirement rates | retired (burned + held) buyback tokens annualised ÷ circulating / ÷ FDV supply | retired_buyback_tokens ÷ supply | % | CAPTURE | 1 | rolling T12M | A3 | 2 | BUILT | retired_buyback_tokens,circulating_supply_used,total_supply |
| burn_yield | Permanent burn yield | burn annualised ÷ circulating | a4 burn ÷ circulating | % | CAPTURE | 1 | 4Q log-linear | A4 | 2 | BUILT | gross_burn_tokens |
| crossover | Burn ÷ issuance | burn ÷ issuance (releases for a pre-minted supply) | burn rate ÷ issuance-basis rate | x | CAPTURE | 1 | 4Q log-linear | A4 | 2 | FIX B2 | gross_burn_tokens,gross_issuance_tokens,pool_release_tokens |
| net_absorption | Net absorption | retired buyback − emissions − rebase (A3) | retired − dilution legs | tokens | CAPTURE | 1 | rolling T12M | A3 | 2 | BUILT | |
| hvy | Holder value yield | revenue-funded $ retired + $ distributed + declared payouts (liquid bribes) annualised ÷ market cap | holder_value_revenue_usd ÷ market_cap_usd | % | CAPTURE | 1 | 4Q log-linear | CP | 2 | FIX B9 | holder_value_revenue_usd,market_cap_usd |
| nhy | Net holder yield | HVY − dilution (outflows a+b+c+d+e at each day's price) ÷ market cap; names a starved leg (PROPOSED axis CAPTURE) | hvy − Σ outflow_c_usd ÷ market_cap | % | CAPTURE | 1 | 4Q log-linear | CP | 2 | FIX C1 | holder_value_revenue_usd,outflow_a_usd,outflow_b_usd,outflow_c_usd,outflow_d_usd,outflow_e_usd,market_cap_usd |
| payout_ratio | Payout ratios | credited holder value ÷ fees (gross) and ÷ revenue (net basis); NEAR per stream (C10) | holder_value_revenue_usd ÷ fees_usd / revenue base | % | CAPTURE | 1 | 4Q log-linear | CP | 2 | FIX C10 | holder_value_revenue_usd,fees_usd,revenue_usd |
| capture_status | Capture status, switch power, destination, certainty | attributes (phase2_attributes.json), never penalties | text | text | CAPTURE | 0 | — | CP | 3 | FIX C17 | |
| gross_issuance | Gross issuance Q0 | tokens minted (0 — fixed / pre-minted supply; Sky 0 verified on-chain, B4) | Σ gross_issuance_tokens | tokens | ALIGNMENT | -1 | 4Q log-linear | Master,A1,A4 | 2 | BUILT | gross_issuance_tokens |
| issuer_releases | Issuer releases Q0 | pre-minted supply released into circulation; NOT issuance | Σ pool_release_tokens (≥ 0) | tokens | ALIGNMENT | -1 | rolling T12M | Master,A1,A4 | 2 | FIX B2/B8 | pool_release_tokens |
| net_issuance | Net issuance after burn | mint − burn (Q0 and annualised ÷ circulating); BLOCKED on unequal coverage | gross_issuance − gross_burn | tokens | ALIGNMENT | -1 | 4Q log-linear | Master,A1,A4 | 2 | FIX B5/B8 | gross_issuance_tokens,gross_burn_tokens,net_supply_change_tokens,net_mint_monthly |
| emissions | Emissions Q0 | tokens paid out as incentives (supplier / security) | Σ emissions_tokens | tokens | ALIGNMENT | -1 | 4Q log-linear | A2,A3,CP | 2 | BUILT | emissions_tokens |
| unlocks | Unlocks Q0 | releases from non-circulating wallets (falls of their stock, ≥ 0) | Σ unlock_release_tokens | tokens | ALIGNMENT | -1 | rolling T12M | A2,A3,A4,CP | 2 | BUILT | unlock_release_tokens |
| outflow_a | Outflow (a) security emissions | rewards for securing the chain / service, to CIRCULATING stake | per project section 6 | $ | ALIGNMENT | -1 | 4Q log-linear | CP,Flows | 2 | FIX C1 | outflow_a_tokens,outflow_a_usd |
| outflow_b | Outflow (b) supply-side incentives | rewards to suppliers (LPs, miners, lenders) | per project section 6 | $ | ALIGNMENT | -1 | 4Q log-linear | CP,Flows | 2 | FIX C1 | outflow_b_tokens,outflow_b_usd |
| outflow_c | Outflow (c) demand-side incentives | rewards to users / holders for using or locking (rebase to lockers, user programmes) | per project section 6 | $ | ALIGNMENT | -1 | 4Q log-linear | CP,Flows | 2 | FIX C1 | outflow_c_tokens,outflow_c_usd |
| outflow_d | Outflow (d) ecosystem / grants | treasury growth budgets and grants | per project section 6 | $ | ALIGNMENT | -1 | rolling T12M | CP,Flows | 2 | NEW C1 | outflow_d_tokens,outflow_d_usd |
| outflow_e | Outflow (e) unlocks | team / investor / foundation releases not in a–d | per project section 6 | $ | ALIGNMENT | -1 | rolling T12M | CP,Flows | 2 | FIX C1 | outflow_e_tokens,outflow_e_usd |
| total_dilution | Total dilution | outflows a+b+c+d+e annualised ÷ circulating (and ÷ float) | Σ outflows ÷ supply | % | ALIGNMENT | -1 | 4Q log-linear | A2,A3,A4,CP | 2 | BUILT | |
| supply_trajectory | Supply trajectory (A2) | annualised growth of circulating from emissions + unlocks | (emissions + unlocks) ÷ float | % | ALIGNMENT | -1 | 4Q log-linear | A2 | 2 | BUILT | emissions_tokens,unlock_release_tokens |
| subsidy_cover | Subsidy cover | revenue ÷ $ of incentives, ÷(b) and ÷(b+c), per covered day | revenue rate ÷ outflow rate | x | ALIGNMENT | 1 | 4Q log-linear | A2,CP | 2 | FIX C3 | outflow_b_usd,outflow_c_usd,customer_revenue_usd,fees_usd,revenue_usd |
| supplier_payout | Supplier payout per $ of fees | (fees to suppliers + incentive $ to suppliers) ÷ fees; the fee-only ratio beside it | ((fees − revenue) + outflow_b_usd) ÷ fees | x | ALIGNMENT | 0 | 4Q log-linear | CP | 1 | FIX C4 | fees_usd,revenue_usd,outflow_b_usd |
| insider_share | Insider share | of staked / locked supply and of circulating, from the issuer-wallet lists | insider stake ÷ stake; insider holdings ÷ circulating | % | ALIGNMENT | -1 | stock-now | CP | 2 | NEW C5 | team_wallet_tokens,staked_noncirculating_tokens,locked_tokens |
| discretionary | Discretionary supply | issuer-side wallets with no published release schedule (0 with reason where none) | Σ config.DISCRETIONARY_SUPPLY legs | tokens | ALIGNMENT | -1 | stock-now | CP | 2 | FIX C6 | |
| leakage | Leakage (PROPOSED) | (i) insider share of distributed holder value; (ii) max(0, 1 − subsidy cover); (iii) buyback execution: $ spent ÷ (tokens × market price) − 1 | section 9 | % | ALIGNMENT | -1 | 4Q log-linear | CP | 2 | NEW C12 | |
| uniformity | Uniformity (PROPOSED) | whole-token yield ÷ staker yield; do non-stakers receive value (yes/no) | whole ÷ staker | x | ALIGNMENT | 1 | 4Q log-linear | CP | 2 | NEW C12 | |
| lock_terms | Lock terms, payout currency, destination durability | attributes | text | text | ALIGNMENT | 0 | — | CP | 3 | BUILT | |
| token_flow | Token-flow statement | opening circulating + mint + releases − burns − net new locks = closing circulating; residual | section 8 | tokens | ALIGNMENT | 0 | — | Flows | 2 | NEW C11 | |
| value_flow | Value-flow statement | fees → suppliers / revenue → retired / distributed / treasury / insiders; incentives beside; residual | section 8 | $ | CAPTURE | 0 | — | Flows | 2 | NEW C11 | |
| sell_rate | Sell rate by recipient cohort | Phase 5 | — | % | ALIGNMENT | -1 | — | — | — | LATER C18 | |
| exchange_balances | Exchange balances | paid labelled-wallet source — Jake's decision | — | tokens | CONTEXT | 0 | — | — | — | NOT WIRED C18 | |
| quadrant | Proven / Building / Fading / Failing | axis level × trajectory per row | — | text | — | 0 | — | — | — | LATER C19 | |
<!-- /SPEC:CONCEPTS -->

---------------------------------------------------------------------------------------------------------------------
## 5. Per-project derivations (where a project differs from the registry formula)

| Project | Circulating basis | Issuance basis | Stake (inside circulating) | Revenue base | Native unit (C15) |
|---|---|---|---|---|---|
| Ethereum | first-party supply (ultrasound / Etherscan ethsupply2) | d Eth2Staking (consensus rewards) | beacon_chain_eth (validatorqueue / beaconcha.in) | burned + priority fees | ETH (chain) |
| NEAR | NEAR's own figure less lockup-locked (Jake's rule) | declared rate × total (5%→2.5%), derived cross-check | total stake | gas (burned 70%) + Intents fee switch (C10) | NEAR |
| Plume | issuer rule (first party) | 0 (pre-minted; staking from pre-mint is unmeasured) | stake | n/a | PLUME |
| Chainlink | 1,000,000,000 − 27 non-circulating wallets | 0 — pre-minted; releases on their own line | v0.2 pools principal | Payment Abstraction / service fees | LINK |
| GEODNET | issuer rule; CoinGecko reference while partial | 0 — pre-minted; releases | staking wallet | customer revenue (fiat) | stations |
| Aethir | first-party page | 0 — pre-minted (emissions from pools) | veATH | compute revenue | compute hours |
| Maple | issuer rule (DAO multisig, registry, admins, Safe 0x58BE out) | 0 — pre-minted | stSYRUP | protocol revenue | loans outstanding |
| Morpho | issuer rule (8 wallets) | 0 | — | — | borrowed |
| Hyperliquid | tokenDetails.circulatingSupply less Foundation + Grants | 0 — pre-minted (Future Emissions) | 440.1M total − 301.3M issuer-side = inside (2026-10-13) | AF buyback | HYPE / OI |
| Uniswap | issuer rule | 0 after the 1bn cap (2% inflation not activated) | — | Firepit fee switch | volume by version |
| Aerodrome | on-chain (veAERO counted) | Minter weekly emission (tail) | veAERO less the filing wallets' managed locks | voter fees + liquid bribes | volume |
| Sky | issuer rule; MKR→SKY conversions release the converter's pre-minted balance into circulating | 0 — VERIFIED (B4): no SKY minted since 2025-06-30; the minting vest MCD_VEST_SKY's streams ended 2025-06-02 (yank(2), 2025-05-29 executive); farm rewards since June 2025 are TRANSFERS from MCD_VEST_SKY_TREASURY. Q0 total supply −10,233,231.76 = the two spell burns, to the wei | lsSKY / farms | Net Protocol Surplus | USDS supply |
| Pendle | on-chain | 0 after the emission cut (measured d mainnet totalSupply) | sPENDLE | revenue to sPENDLE | TVL in underlying (C15) |
| Ether.fi | on-chain (issuer rule) | 0 | sETHFI | withdrawal fees + programme revenue | ETH staked (C15) |
| Fluid | issuer rule | 0 | — | revenue (halted 2026-05-11) | TVL |

---------------------------------------------------------------------------------------------------------------------
## 6. Outflow categories a–e (C1) — every project

(a) security · (b) supply-side incentives · (c) demand-side incentives · (d) ecosystem / grants · (e) unlocks. Each
series in exactly one category; a project's assignment and its basis are in `config.OUTFLOW_CATEGORIES`.

| Project | a | b | c | d | e |
|---|---|---|---|---|---|
| Ethereum | issuance (consensus) | — | — | — | — |
| NEAR | issuance (90% validators) | — | — | — | lockup releases |
| Plume | NOT WIRED (staking paid from pre-mint; reward rate × stake would be modelled) | NOT WIRED (Merkl WPLUME) | one-off airdrops (no series) | — | — |
| Chainlink | Reward Vault payouts (confirmed not inside e: the vault is outside the 27 wallets; its one inflow in the last 5,000 transfers came from 0x7e10a625…, not one of them) | — | — | — | 27-wallet releases |
| GEODNET | — | miner rewards | — | — | treasury / non-circulating releases (same legs as A2) |
| Aethir | — | pool emissions | — | — | — |
| Maple | stSYRUP inflation (0 after MIP-019) | Drips (ended 2025-12-31; NOT WIRED history) | — | — | wallet releases |
| Morpho | — | MORPHO rewards | — | — | wallet releases |
| Hyperliquid | staking rewards × (stake inside circulating ÷ total stake) — rewards to issuer-side stake are not circulating until they move | — | — | — | Foundation / Grants / team releases |
| Uniswap | — | — | — | UNIfication growth budget (Timelock → UNIVesting) | what remains of the wallet releases after (d) |
| Aerodrome | — (no security budget) | gauge emissions | rebase to veAERO lockers (own line) | — | wallet releases |
| Sky | — | — | SKY farm rewards (treasury-funded transfers via MCD_VEST_SKY_TREASURY since June 2025; ~2.04M SKY/day in Q0) | — | MKR→SKY conversion releases (the converter's pre-minted balance into circulating; 149.6M SKY to users in Q0) — on the Flows tab; not dilution of SKY holders' share of MKR-era claims (PROPOSED) |
| Pendle | — | gauge emissions (~0.2%/yr after the ~92% cut) | — | — | per wallet identification (C1) |
| Ether.fi | — | — | user programmes (Season 5, Triple Dip, Cash cashback): distributor wallets or NOT WIRED | — | wallet releases |
| Fluid | — | rewards | — | — | wallet releases |

---------------------------------------------------------------------------------------------------------------------
## 7. Series breaks and restatement (C7)

Circulating breaks inside the last quarter: Ethereum (2026-09-29), Hyperliquid (2026-09-30, 2026-10-11), NEAR
(2026-10-11), Sky (2026-10-12), Aethir (2026-09-30), Plume (2026-10-07); FDV for 9 projects (2026-10-11 total supply
restatement). For each the route to restate history on the current basis (archive reads of the same wallet set; first
party history) or, where none exists, the date the trajectory becomes valid, is on the Coverage tab and in RUNBOOK
11bv. FDV is restored by a declared handover (CoinGecko's FDV → price × our total) and is a SERIES BREAK until the
archive restates total supply.

## 8. Flow statements (C11) — tab "Flows", Q0..Q-4 per project

* **Token flow:** opening circulating + mint (gross issuance) + releases (unlocks + pool releases) − burns − net new
  locks inside circulating = closing circulating. Residual = closing − that sum; |residual| > 0.5% of opening → CHECK.
* **Value flow:** fees → supplier share (fees − revenue) + protocol revenue; revenue → retired (burned + held
  buybacks) + distributed (to holders) + treasury (the rest); incentives $ (outflows a–e) beside. Residual = revenue −
  retired − distributed − treasury (0 by construction for treasury as the remainder; shown so a negative treasury —
  value paid out beyond revenue — is visible).

## 9. Leakage and uniformity (C12 — PROPOSED definitions)

* Leakage (i): share of distributed holder value received by insider / issuer-side wallets (their share of the
  reward-bearing stake). (ii): incentive spend not returned as revenue = max(0, 1 − subsidy cover). (iii): buyback
  execution = $ spent ÷ (tokens bought × the day's market price) − 1.
* Uniformity: whole-token yield ÷ staker yield (1 = every holder treated alike); non-stakers receive value: yes / no.

## 10. NOT WIRED this round (C18) — with route

| Item | Route |
|---|---|
| Phase 5 sell rate by recipient cohort | LATER — recipient cohorts from the outflow scans, then their onward transfers to exchanges |
| Exchange balances | paid labelled-wallet source (Nansen / CoinGlass / Arkham) or DefiLlama CEX pages (secondary) — Jake's decision |
| Subsidy targeting by pool | Pendle gauge votes per pool; Aerodrome RewardsSugar per-epoch per-pool (routes probed) |
| Pendle TVL by maturity | api-v2.pendle.finance core/v2/markets/all + historical-data fields=totalTvl per market |
| CCIP token value | CCIP API sourceTokenAmount × token decimals × daily price |
| GEODNET customers | no public source (console needs a wallet login) |
| Aethir business series beyond revenue / compute hours | dashboard.aethir.com demand metrics (current only) |
| Hyperliquid ASXN series | browser capture (ASXN hyperscreener) — dashboard |
| Market share LINK / PLUME / ATH / GEOD | oracle TVS (paid) / RWA-by-chain rebuild / no comparable category |

## 11. LATER (C19) — Proven / Building / Fading / Failing grid

Inputs it will need, all already on the workbook after this round: per row its axis (section 2), level (Q0 value) and
trajectory (growth, direction, quarters improved). Nothing built.

## 12. PROPOSED — for Jake

1. Axis of lock rate, valuation multiples (NRR, free float ÷ ARR, fees ÷ FDV) → CONTEXT; NHY, validator yield and Total
   Yield → CAPTURE; fees ÷ issuance → BUSINESS.
2. Aerodrome's rebase to veAERO lockers is a demand-side incentive (c), its own line; gauge emissions are (b); no (a).
3. Hyperliquid (a) counts only rewards to stake inside circulating (pro-rata to stake, the protocol's own rule).
4. Leakage (i)–(iii) and uniformity as defined in section 9.
5. Supplier payout = (fees − revenue + supply-side incentive $) ÷ fees.
6. Lock rate direction +1 (more locked inside circulating is better).
7. B4 (Sky) needs no issuance change: gross issuance 0 is verified on-chain. The +151.4M QoQ circulating rise is MKR→SKY
   conversions out of the converter's pre-minted balance. PROPOSED: shown on the Flows tab as a release, and NOT
   counted as dilution in (e). Each conversion retires the equivalent MKR (24,000 SKY per MKR), so holders' claims are
   unchanged.
