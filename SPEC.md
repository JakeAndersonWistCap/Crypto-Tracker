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
   yield; releases ≥ 0; issuance ≥ 0. A flow statement whose residual exceeds 0.5% carries a CHECK note with its named cause, or is BROKEN (FR2 T1).
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
| fdv | FDV | price × the total supply used (on-chain first; CoinGecko's FDV before our first total, a declared handover) | price_usd × total_supply | $ | CONTEXT | 0 | stock-now | Master,A3 | 1 | BUILT B6 (SERIES BREAK until archive) | fdv_usd,price_usd,total_supply |
| circulating | Circulating supply (used) | total − treasury − team/investor unvested − foundation − operating reserves − burned; staked included; per-project basis section 5 | config.circulating_primary_metric | tokens | CONTEXT | 0 | stock-now | Master,A1,A2,A3,A4,CP | 2 | BUILT (restatement: section 7) | circulating_supply_used,circulating_supply,total_supply,max_supply |
| stake_inside | Stake / locks inside circulating | the stake (or lock) LESS the stake held by wallets already outside circulating | locked_tokens (+ declared extra legs) − locked_excluded_metrics | tokens | CONTEXT | 0 | stock-now | A1,A2,A3,CP | 2 | BUILT B1 | locked_tokens,staked_noncirculating_tokens,beacon_chain_eth,ve_locked_supply_tokens |
| float | Tradeable float | circulating − stake inside circulating | circulating_supply_used − stake_inside | tokens | CONTEXT | 0 | stock-now | A1,A2,A3,CP | 2 | BUILT B1 | circulating_supply_used,locked_tokens,staked_noncirculating_tokens |
| lock_rate | Lock rate | stake inside circulating ÷ circulating (PROPOSED axis CONTEXT) | stake_inside ÷ circulating | % | CONTEXT | 1 | stock-now | A1,A3,CP | 2 | BUILT B1 | locked_tokens,staked_noncirculating_tokens,circulating_supply_used |
| tbill | 3-month T-bill | US Treasury 13-week coupon equivalent | tbill_3m_yield | % | CONTEXT | 0 | stock-now | A1,A3,CP | 1 | BUILT | tbill_3m_yield |
| ssr | Sky Savings Rate | sUSDS ssr() on-chain, annual | sky_savings_rate | % | CONTEXT | 0 | stock-now | A1,A3,CP | 2 | BUILT | sky_savings_rate |
| etf_holdings | ETF / ETP holdings | latest dated issuer reading per product, summed; ÷ circulating beside | etf_dat_holdings.json | tokens | CONTEXT | 0 | stock-now | CP | 3 | BUILT | |
| dat_holdings | Treasury-company holdings | latest dated filing per company, summed; ÷ circulating beside | etf_dat_holdings.json | tokens | CONTEXT | 0 | stock-now | CP | 3 | BUILT | |
| nrr | NRR on settlement / throughput volume | market cap ÷ trailing-365d volume (PROPOSED axis CONTEXT) | market_cap_usd ÷ settlement_volume_365d_usd | x | CONTEXT | -1 | 4Q log-linear | A1 | 1 | BUILT | market_cap_usd,settlement_volume_365d_usd,trading_throughput_365d_usd,network_reserve_ratio |
| ff_arr | Free float ÷ ARR | free-float market value ÷ annualised customer revenue (PROPOSED axis CONTEXT) | (float × price) ÷ customer_revenue_usd annualised | x | CONTEXT | -1 | 4Q log-linear | A2 | 1 | BUILT | customer_revenue_usd,price_usd,circulating_supply_used,locked_tokens |
| fees_fdv | Fees ÷ FDV | annualised fees ÷ FDV (PROPOSED axis CONTEXT) | fees_usd annualised ÷ fdv_usd | % | CONTEXT | 1 | 4Q log-linear | Master,A3 | 1 | BUILT | fees_usd,fdv_usd |
| fees | Fees Q0 | fees paid by users in Q0 | Σ fees_usd over Q0 | $ | BUSINESS | 1 | 4Q log-linear | Master,A1,A3 | 1 | BUILT | fees_usd |
| revenue | Revenue Q0 | protocol revenue on the project's documented base; NEAR = gas + Intents fee switch (C10) | Σ revenue_usd (config.revenue_base_metric) | $ | BUSINESS | 1 | 4Q log-linear | Master,A3 | 1 | BUILT C10 | revenue_usd,revenue_usd_reported,near_intents_revenue_usd,revenue_total_usd,intents_revenue_usd,net_protocol_surplus_usd,net_revenue_pre_distribution_usd |
| customer_revenue | Customer revenue Q0 | what customers paid (A2) | Σ customer_revenue_usd | $ | BUSINESS | 1 | 4Q log-linear | A2 | 1 | BUILT | customer_revenue_usd,fees_usd |
| tvl | TVL | chain TVL (A1) or protocol TVL; Plume's DefiLlama reclassifications are breaks | tvl_usd / protocol_tvl_usd now | $ | BUSINESS | 1 | stock-now | Master,A1 | 1 | BUILT B7 | tvl_usd,protocol_tvl_usd |
| tvl_native | TVL in native units | the TVL's underlying units (ETH staked for Ether.fi; USDS for Sky) so business trends are not price | per project section 5 | native | BUSINESS | 1 | stock-now | CP | 1 | BUILT R3 W1 (Ether.fi eETH supply; protocols: DefiLlama tokens at the latest prices; Sky USDS) | eth_staked_etherfi_tokens,usds_supply_tokens,protocol_tvl_constant_price_usd |
| settlement_volume | Settlement / throughput volume | value settled on the chain | settlement_volume_usd | $ | BUSINESS | 1 | 4Q log-linear | A1 | 1 | BUILT | settlement_volume_usd,trading_throughput_usd,network_reserve_ratio |
| business_n | Business 1–4 | the usage / customer series each business is judged on (config.BUSINESS_SLOTS); a stock reads now, a flow Q0 annualised | per slot | per series | BUSINESS | 1 | 4Q log-linear | CP | 1 | BUILT | |
| market_share | Market share | own ÷ category on one measure, peer set written down (config.MARKET_SHARE); second basis chain fees for ETH / NEAR | market_share_pct, market_share_2_pct | % | BUSINESS | 1 | stock-now | CP | 1 | BUILT | market_share_pct,market_share_2_pct |
| boros | Pendle Boros fees / volume | Boros on its own lines; Pendle's revenue base stays per docs | boros_fees_usd, boros_volume_usd | $ | BUSINESS | 1 | 4Q log-linear | CP | 1 | BUILT C16 (fees) + R3 W2 (volume: Boros API) | boros_fees_usd,boros_volume_usd |
| fees_issuance | Fees ÷ issuance | fees per $ of issuance at the Q0 price (PROPOSED axis BUSINESS) | fees_usd rate ÷ (gross_issuance_tokens rate × price) | x | BUSINESS | 1 | 4Q log-linear | Master,A1 | 1 | BUILT | fees_usd,gross_issuance_tokens,price_usd |
| validator_yield | Validator staking yield | consensus + execution (ETH: d Eth2Staking + MEV-incl. execution ÷ beacon stake; others per config.VALIDATOR_YIELD) (PROPOSED axis CAPTURE) | config.VALIDATOR_YIELD | % | CAPTURE | 0 | 4Q log-linear | A1,CP | 2 | BUILT B3 | consensus_rewards_tokens,execution_rewards_eth,beacon_chain_eth,emissions_tokens,locked_tokens,reward_emission_rate_annual,staking_yield_net_pct,staking_yield_pct,staker_yield_community_pool |
| total_yield | Total Yield — stakers / non-stakers | validator yield − (net inflation + issuer releases); non-stakers − (net inflation + releases) (PROPOSED axis CAPTURE) | vyield − (net_issuance_pct + releases ÷ circulating) | % | CAPTURE | 1 | 4Q log-linear | A1 | 2 | BUILT C8 (HL / NEAR: on Jake's next run) | gross_issuance_tokens,gross_burn_tokens,pool_release_tokens,emissions_tokens,unlock_release_tokens,unlock_release_counted_tokens,circulating_supply_used |
| protocol_yield | Protocol staking yield | tokens paid to stakers per year ÷ reward-bearing stake | config.PROTOCOL_YIELD | % | CAPTURE | 1 | 4Q log-linear | A3,CP | 2 | BUILT | |
| whole_token_yield | Whole-token yield | staker yield × stake inside circulating ÷ circulating (≤ staker yield) | yield × stake_inside ÷ circulating | % | CAPTURE | 1 | 4Q log-linear | CP | 2 | BUILT B1 | locked_tokens,staked_noncirculating_tokens,circulating_supply_used |
| gross_burn | Gross burn Q0 | every burn the supply sees (fee-market burns included) | Σ config.a1_burn_metric | tokens | CAPTURE | 1 | 4Q log-linear | Master,A1,A4 | 2 | BUILT | gross_burn_tokens,core_burn_tokens,total_burn_tokens,sky_stage2_burn_tokens,burn_address_balance,max_supply,one_off_burn_tokens |
| actual_buyback | Actual buyback Q0 | tokens / $ bought by the programme | Σ actual_buyback_tokens / _usd | $ | CAPTURE | 1 | rolling T12M | Master,A3 | 2 | BUILT B11 (Aerodrome: seed command) | actual_buyback_tokens,actual_buyback_usd,buyback_burned_tokens,buyback_held_tokens,buyback_distributed_tokens,retired_buyback_tokens,buyback_bought_share_alltime |
| implied_buyback | Implied buyback Q0 | revenue × the split that applied to the window | revenue × share | $ | CAPTURE | 1 | 4Q log-linear | Master,A3 | 1 | BUILT | revenue_usd,actual_buyback_usd,fees_usd,price_usd,net_protocol_surplus_usd |
| retirement | Retirement rates | retired (burned + held) buyback tokens annualised ÷ circulating / ÷ FDV supply | retired_buyback_tokens ÷ supply | % | CAPTURE | 1 | rolling T12M | A3 | 2 | BUILT | retired_buyback_tokens,circulating_supply_used,total_supply,buyback_fund_balance,actual_buyback_usd,retired_buyback_usd,market_cap_usd,fdv_usd |
| burn_yield | Permanent burn yield | burn annualised ÷ circulating | a4 burn ÷ circulating | % | CAPTURE | 1 | 4Q log-linear | A4 | 2 | BUILT | gross_burn_tokens,total_burn_tokens,sky_stage2_burn_tokens |
| crossover | Burn ÷ issuance | burn ÷ issuance (releases for a pre-minted supply) | burn rate ÷ issuance-basis rate | x | CAPTURE | 1 | 4Q log-linear | A4 | 2 | BUILT B2 | gross_burn_tokens,gross_issuance_tokens,pool_release_tokens |
| net_absorption | Net absorption | retired buyback − emissions − rebase (A3) | retired − dilution legs | tokens | CAPTURE | 1 | rolling T12M | A3 | 2 | BUILT | |
| hvy | Holder value yield | revenue-funded $ retired + $ distributed + declared payouts (liquid bribes) annualised ÷ market cap | holder_value_revenue_usd ÷ market_cap_usd | % | CAPTURE | 1 | 4Q log-linear | CP | 2 | BUILT B9 | holder_value_revenue_usd,market_cap_usd |
| nhy | Net holder yield | HVY − dilution (outflows a+b+c+d+e at each day's price) ÷ market cap; names a starved leg (PROPOSED axis CAPTURE) | hvy − Σ outflow_c_usd ÷ market_cap | % | CAPTURE | 1 | 4Q log-linear | CP | 2 | BUILT C1 | holder_value_revenue_usd,outflow_a_usd,outflow_b_usd,outflow_c_usd,outflow_d_usd,outflow_e_usd,market_cap_usd |
| payout_ratio | Payout ratios | credited holder value ÷ fees (gross) and ÷ revenue (net basis); NEAR per stream (C10) | holder_value_revenue_usd ÷ fees_usd / revenue base | % | CAPTURE | 1 | 4Q log-linear | CP | 2 | BUILT C10; R3 Q2 (Sky: pre-distribution base) | holder_value_revenue_usd,fees_usd,revenue_usd,net_protocol_surplus_usd,actual_buyback_usd,net_revenue_pre_distribution_usd |
| capture_status | Capture status, switch power, destination, certainty | attributes (phase2_attributes.json), never penalties | text | text | CAPTURE | 0 | — | CP | 3 | BUILT C17 | |
| gross_issuance | Gross issuance Q0 | tokens minted (0 — fixed / pre-minted supply; Sky 0 verified on-chain, B4) | Σ gross_issuance_tokens | tokens | ALIGNMENT | -1 | 4Q log-linear | Master,A1,A4 | 2 | BUILT | gross_issuance_tokens,pool_release_tokens |
| issuer_releases | Issuer releases Q0 | pre-minted supply released into circulation; NOT issuance | Σ pool_release_tokens (≥ 0) | tokens | ALIGNMENT | -1 | rolling T12M | Master,A1,A4 | 2 | BUILT B2/B8 | pool_release_tokens |
| net_issuance | Net issuance after burn | mint − burn (Q0 and annualised ÷ circulating); BLOCKED on unequal coverage | gross_issuance − gross_burn | tokens | ALIGNMENT | -1 | 4Q log-linear | Master,A1,A4 | 2 | BUILT B5/B8 | gross_issuance_tokens,gross_burn_tokens,net_supply_change_tokens,net_mint_monthly,total_burn_tokens,sky_stage2_burn_tokens |
| emissions | Emissions Q0 | tokens paid out as incentives (supplier / security) | Σ emissions_tokens | tokens | ALIGNMENT | -1 | 4Q log-linear | A2,A3,CP | 2 | BUILT | emissions_tokens |
| unlocks | Unlocks Q0 | releases from non-circulating wallets (falls of their stock, ≥ 0) | Σ unlock_release_tokens | tokens | ALIGNMENT | -1 | rolling T12M | A2,A3,A4,CP | 2 | BUILT; R3 B2/W7 (legs counted elsewhere taken out of e) | unlock_release_tokens,staking_funding_release_tokens,issuer_to_gauges_tokens,staking_path_in_tokens,staking_path_out_tokens,staking_funding_tokens |
| outflow_a | Outflow (a) security emissions | rewards for securing the chain / service, to CIRCULATING stake | per project section 6 | $ | ALIGNMENT | -1 | 4Q log-linear | CP,Flows | 2 | BUILT C1 | outflow_a_tokens,outflow_a_usd,emissions_circulating_stake_tokens |
| outflow_b | Outflow (b) supply-side incentives | rewards to suppliers (LPs, miners, lenders) | per project section 6 | $ | ALIGNMENT | -1 | 4Q log-linear | CP,Flows | 2 | BUILT C1 | outflow_b_tokens,outflow_b_usd,emissions_tokens,merkl_campaigns_non_ethereum_tokens,drips_rewards_tokens |
| outflow_c | Outflow (c) demand-side incentives | rewards to users / holders for using or locking (rebase to lockers, user programmes) | per project section 6 | $ | ALIGNMENT | -1 | 4Q log-linear | CP,Flows | 2 | BUILT C1 | outflow_c_tokens,outflow_c_usd,cashback_unfunded_tokens,cashback_paid_tokens,programmatic_buyback_tokens |
| outflow_d | Outflow (d) ecosystem / grants | treasury growth budgets and grants | per project section 6 | $ | ALIGNMENT | -1 | rolling T12M | CP,Flows | 2 | BUILT C1 | outflow_d_tokens,outflow_d_usd,growth_budget_release_tokens |
| outflow_e | Outflow (e) unlocks | team / investor / foundation releases not in a–d | per project section 6 | $ | ALIGNMENT | -1 | rolling T12M | CP,Flows | 2 | BUILT C1 | outflow_e_tokens,outflow_e_usd,unlock_release_vesting_tokens |
| total_dilution | Total dilution | outflows a+b+c+d+e annualised ÷ circulating (and ÷ float) | Σ outflows ÷ supply | % | ALIGNMENT | -1 | 4Q log-linear | A2,A3,A4,CP | 2 | BUILT | |
| supply_trajectory | Supply trajectory (A2) | annualised growth of circulating from emissions + unlocks | (emissions + unlocks) ÷ float | % | ALIGNMENT | -1 | 4Q log-linear | A2 | 2 | BUILT | emissions_tokens,unlock_release_tokens |
| subsidy_cover | Subsidy cover | revenue ÷ $ of incentives, ÷(b) and ÷(b+c), per covered day | revenue rate ÷ outflow rate | x | ALIGNMENT | 1 | 4Q log-linear | A2,CP | 2 | BUILT C3 | outflow_b_usd,outflow_c_usd,customer_revenue_usd,fees_usd,revenue_usd,emissions_tokens |
| supplier_payout | Supplier payout per $ of fees | (fees to suppliers + incentive $ to suppliers) ÷ fees; the fee-only ratio beside it | ((fees − revenue) + outflow_b_usd) ÷ fees | x | ALIGNMENT | 0 | 4Q log-linear | CP | 1 | BUILT C4 | fees_usd,revenue_usd,outflow_b_usd,net_revenue_pre_distribution_usd |
| insider_share | Insider share | of staked / locked supply and of circulating, from the issuer-wallet lists | insider stake ÷ stake; insider holdings ÷ circulating | % | ALIGNMENT | -1 | stock-now | CP | 2 | BUILT C5; R3 W6 (share-token projects) | team_wallet_tokens,staked_noncirculating_tokens,locked_tokens,filing_managed_lock_tokens,ve_locked_supply_tokens,insider_stake_shares,stsyrup_legacy_tokens,locked_tokens_shares |
| discretionary | Discretionary supply | issuer-side wallets with no published release schedule (0 with reason where none) | Σ config.DISCRETIONARY_SUPPLY legs | tokens | ALIGNMENT | -1 | stock-now | CP | 2 | BUILT C6 | |
| leakage | Leakage (PROPOSED) | (i) insider share of distributed holder value; (ii) max(0, 1 − subsidy cover); (iii) buyback execution: $ spent ÷ (tokens × market price) − 1 | section 9 | % | ALIGNMENT | -1 | 4Q log-linear | CP | 2 | BUILT C12 (ii); R3 W5 (i) and (iii) | holder_value_revenue_usd,retired_buyback_usd,buyback_spending_usd_ba,actual_buyback_usd,insider_stake_shares,staked_noncirculating_tokens,locked_tokens,filing_managed_lock_tokens,ve_locked_supply_tokens,fees_usd,customer_revenue_usd,revenue_usd,outflow_b_usd,outflow_c_usd,emissions_tokens |
| uniformity | Uniformity (PROPOSED) | whole-token yield ÷ staker yield; do non-stakers receive value (yes/no) | whole ÷ staker | x | ALIGNMENT | 1 | 4Q log-linear | CP | 2 | BUILT C12 | |
| lock_terms | Lock terms, payout currency, destination durability | attributes | text | text | ALIGNMENT | 0 | — | CP | 3 | BUILT | |
| token_flow | Token-flow statement | opening circulating + mint + releases − burns − net new locks = closing circulating; residual | section 8 | tokens | ALIGNMENT | 0 | — | Flows | 2 | BUILT C11 | |
| value_flow | Value-flow statement | fees → suppliers / revenue → retired / distributed / treasury / insiders; incentives beside; residual | section 8 | $ | CAPTURE | 0 | — | Flows | 2 | BUILT C11 | |
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
| Chainlink | Reward Vault payouts (the vault's funder 0x7e10a625… is fed 0x1661f928 / 0x8716f86f ← 0x7A28b8cF ← 0xD50f074E ← the 27 wallets) — R3 B2: that LINK is taken out of (e) per transfer (FIFO through 0xD50f074E…, staking_funding_release_tokens — an ATTRIBUTION CONVENTION, ACCEPTED by Jake 2026-10-14), so it is counted once, here | — | — | — | 27-wallet releases (the 18.75M tranches stay, C23) |
| GEODNET | — | miner rewards (the two MINING wallets' release) | — | — | releases of the five other filed wallets (Team, Investor, Ecosystem, Vendor/Marketing, Public sale) — never a mining wallet (FR2 G1/G2/F2) |
| Aethir | — | pool emissions | — | — | — |
| Maple | stSYRUP inflation (0 after MIP-019) | Drips (SYRUP out of syrupDrip, drips_rewards_tokens; ended 2025-12-31, last payout 2026-02-18 — R3 W4) | — | — | wallet releases; recapitalization mints to the treasury are their own line (issuer_mint_tokens), a release when they leave (FR2 S3) |
| Morpho | — | MORPHO rewards: Ethereum claims (Merkl distributor + URD) + the DAO multisig's Merkl campaigns PAID off Ethereum (Base, Katana, Arbitrum — R3 B3; Arc / Tempo / Unichain pay on Ethereum, so are in the claims) | — | — | wallet releases |
| Hyperliquid | staking rewards × (stake inside circulating ÷ total stake) — rewards to issuer-side stake are not circulating until they move | — | — | — | Foundation / Grants / team releases |
| Uniswap | — | — | — | ecosystem / grants / treasury operating spend (FR2 S7) = every Timelock fall: the UNIfication growth budget (20M UNI/yr, 5M a quarter via UNIVesting to Labs' recipient 0xaBA63748…, proposal 93) and any other grant; the 100M treasury burn is taken out on its day | the treasury vesting contracts (ended Sep 2024) + unclaimed-airdrop claims — ~0 a quarter after the move |
| Aerodrome | — (no security budget) | gauge emissions | rebase to veAERO lockers (own line) | — | wallet releases; the team emission (Minter teamRate, 228 bps of the frozen weekly) is its own insider line, minted into a filing wallet (FR2 S5) |
| Sky | — | — | rewards paid from MINTED SKY only (to 2025-06-02) | — | rewards paid from Pause Proxy RESERVES (2025-06-02..2026-08-16: a treasury release); rewards paid from SKY BOUGHT BACK out of revenue (from 2026-08-17) are a distribution of a buyback already credited — not dilution (FR2 S1, config.REWARD_FUNDING); and MKR→SKY conversion releases (the converter's pre-minted balance into circulating; 149.6M SKY to users in Q0) — on the Flows tab; not dilution of SKY holders' share of MKR-era claims (PROPOSED) |
| Pendle | — | gauge emissions (~0.2%/yr after the ~92% cut) | — | — | the Governance / Team / Ecosystem multisigs' falls, LESS what they sent straight to the GaugeController / externalRewardsDistributor (issuer_to_gauges_tokens — paid on as (b); R3 W7). Classes since 2025-07-01: Binance (e), bridged / unlabelled (e by rule), ops EOAs (e by rule) — config.PENDLE_OUTFLOW_CLASSES |
| Ether.fi | — | — | Cash cashback NOT covered by the programme's market purchases (the rise in max(0, Σ paid − Σ bought), R3 W3; FR2 S2: bought-and-paid is not dilution); Season 5 paid in KING; Triple Dip no distributor | — | wallet releases |
| Fluid | — | rewards | — | — | wallet releases |

---------------------------------------------------------------------------------------------------------------------
## 7. Series breaks and restatement (C7)

Circulating breaks inside the last quarter: Ethereum (2026-09-29), Hyperliquid (2026-09-30, 2026-10-11), NEAR
(2026-10-11), Sky (2026-10-12), Aethir (2026-09-30), Plume (2026-10-07); FDV for 9 projects (2026-10-11 total supply
restatement). For each the route to restate history on the current basis (archive reads of the same wallet set; first
party history) or, where none exists, the date the trajectory becomes valid, is on the Coverage tab and in RUNBOOK
11bv. FDV is restored by a declared handover (CoinGecko's FDV → price × our total) and is a SERIES BREAK until the
archive restates total supply.

| Break | Restatement route (research of 2026-10-13) | Valid without restatement: Q0 / QoQ / 4Q growth / no flag |
|---|---|---|
| Ethereum 2026-09-29 | BUILT (FR2 H1/H3): ultrasound.money supply-over-time (daily 00:00Z, free, no licence found; CoinMetrics NOT used: CC BY-NC) as total_supply_protocol before Etherscan's first read + issuance = d(supply) + burn; PROPOSED same basis (Etherscan 4,118–4,298 ETH / 0.0035% above on the overlap) | 2026-12-27 / 2027-03-27 / 2027-09-23 / 2027-12-22 |
| Hyperliquid 2026-09-30 and 2026-10-11 | 10-11 now declared a composition change (it was read as a trend); per-wallet ledger replay from genesis (userNonFundingLedgerUpdates + delegatorHistory + fills) is exact for the team / Foundation / Grants wallets; futureEmissions history only modelled (~1%) | 2026-12-28 / 2027-03-28 / 2027-09-24 / 2027-12-23 (10-11: 2027-01-08 …) |
| NEAR 2026-10-11 | the lockup rule and NEAR's own figure agree within 0.05% (RUNBOOK) — declare one basis; exact route: weekly lockup reads at past blocks (archival RPC, >160 h) | 2027-01-08 / 2027-04-08 / 2027-10-05 / 2028-01-03 |
| Sky 2026-10-12 | archive reads of the same contracts (archive_backfill.py --project Sky) + sky_mkr.read at past blocks (needs the vest read split out: the vest contract has no code ~400 days back) | 2027-01-09 / 2027-04-09 / 2027-10-06 / 2028-01-04 |
| Aethir 2026-09-30 | BUILT (FR2 H1): read-time history from the published monthly table (re-read 2026-10-13, zero differences) + a "none" handover — no break left | 2026-12-28 / 2027-03-28 / 2027-09-24 / 2027-12-23 |
| Plume 2026-10-07 | BUILT (FR2 H1): CoinGecko's figure as first party from 2026-01-01 (read-time, a "none" handover); the break moves to 2026-01-01; before 2026 not verifiable | 2027-01-04 / 2027-04-04 / 2027-10-01 / 2027-12-30 |
| FDV (8 on-chain-first projects) 2026-10-11 | archive the token totalSupply at past blocks: run_sql.py CJ / CM, then archive_backfill.py --run | 2027-01-08 / 2027-04-08 / 2027-10-05 / 2028-01-03 |

Fix round 2 builds the Ethereum, Aethir and Plume restatements as read-time views (no stored row changes); NEAR, Sky,
FDV and Hyperliquid still need Jake's run (archive / ledger replay — commands in RUNBOOK 11bx).

## 8. Flow statements (C11) — tab "Flows", Q0..Q-4 per project

* **Token flow (as built, FR2 F1/T1):** opening circulating + mint (the issuance basis; 0 on a pre-minted token) +
  releases (pool_release_tokens on a pre-minted token, else the non-circulating wallets' falls; GEODNET: the mining
  release AND the other wallets' release, disjoint; where circulating IS a published schedule — Aethir — the schedule's
  own monthly step, "+ scheduled releases") − returned to issuer-side wallets (the rises of the same stock: recalls,
  purchases into a Safe; a mint into the set is NOT a return) − burns = expected closing. A memo line shows mints
  straight into issuer-side wallets (not circulating until released). Residual = closing − expected; |residual| > 0.5%
  of opening with a NAMED cause (a leg short of the quarter, a basis change, a third party's circulating figure, a
  declared unwired flow) → CHECK; with NO named cause → BROKEN on the Coverage tab.
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
| Chains' TVL in native units | USD ÷ the native price only (the per-token breakdown is paid) — not built: it would restate price, not deposits |
| Market share LINK / PLUME / ATH / GEOD | oracle TVS (paid) / RWA-by-chain rebuild / no comparable category |
| Pendle latent supply (S4) | live read of the token's emission state (accrued, unminted: 9,837,598.56 PENDLE on 2026-10-13, config.LATENT_SUPPLY, pending Jake's review) |
| Insider share of the stake — Ether.fi, Maple, Pendle (review W6) | the share token's holders crossed with the issuer-wallet list: the issuer-side wallets checked (Ether.fi 8, Maple 8, Pendle 3 — config.INSIDER_SHARE_MONITOR) hold 0 shares; the team / investor wallets are not identified. Read on as a monitor: the cell turns into the ratio once a checked wallet holds shares |

## 11. LATER (C19) — Proven / Building / Fading / Failing grid

Inputs it will need, all already on the workbook after this round: per row its axis (section 2), level (Q0 value) and
trajectory (growth, direction, quarters improved). Nothing built.

## 12. PROPOSED — for Jake (Jake's decisions of 2026-10-13 recorded under each; FR2 = fix round 2)

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
8. Supplier payout counts the supply-side incentive $ (b) with the fees paid to suppliers; the fee-only ratio is beside
   it (Aerodrome 1.14 / 0.24 on the 2026-10-09 dump).
9. Pendle: the Governance multisig's falls (the incentives reserve) belong in (b), with the days it funds the
   GaugeController taken out (else counted twice with emissions) — needs a per-transfer series; kept in (e) until then.
10. Ether.fi: the Q0 sETHFI top-ups (1,137,473 ETHFI) are TREASURY-funded (FIFO trace: 28% earlier treasury buyback
    stock, 25% Treasury, 24% investor / core-contributor custodian, 23% Binance withdrawal; 0% revenue) — recorded, not
    credited; HVY 0 stands (ASSUMED pending Jake).
11. NEAR revenue = gas revenue + NEAR Intents fee-switch revenue (DefiLlama near-intents dailyRevenue); the payout and
    coverage ratios divide by both.
12. ~~GEODNET discretionary supply = the treasury wallets only~~ — REJECTED by Jake (addendum G2): every non-mining
    issuer wallet (Team, Investor, Ecosystem, Vendor/Marketing, Public sale — 20M unsold public-sale Safe included) is
    discretionary; the mining wallets are (b) only. No dated release schedule exists (filing §8/§15 give durations
    without start dates — research 2026-10-13).
13. Hyperliquid insider share of stake = the stake of every wallet outside circulating (team + Foundation + Grants) ÷
    total staked, not the team alone.
14. Divergence: a revenue-vs-price growth gap within ±10pp reads "in line" (B14) — ACCEPTED as a convention; each
    Trajectory row now shows the standard error of its 4Q slope (annual, delta method) so the band can be calibrated. Growth: a quarter outside 10%–10x of
    Q0 ends the regime in either direction (B13).
15. Uniswap (d) = every fall of the governance Timelock (the growth budget and any other grant), the treasury-funded
    100M burn excepted.

## 13. FIX ROUND 2 (2026-10-13) — amendments, old → new

Built against the 15 real dumps (fixtures/real_*.json). PROPOSED where Jake's call is open. ACCEPTED by Jake on
2026-10-13 (round 3): qualified sources ("secondary", "primary-ish", "primary-derived", "derived") are cross-checks
only, never VERIFIED (item 17); Sky vest 10 is treated as treasury-funded (the conservative reading); ETH supply is on
one basis across sources (item 19).

| # | Item | Old | New |
|---|---|---|---|
| 1 | §6 Sky outflows (S1) | all SKY rewards = (c) | split by funding period (config.REWARD_FUNDING, every boundary on-chain / an executive): minted (to 2025-06-02) = (c); Pause Proxy reserves (2025-06-02..2026-08-16) = (e) treasury release; bought back out of revenue (from 2026-08-17) = a distribution of the buyback, credited once (as the buyback), not dilution. 2026-03-02..13 (vest 10, sized from prior buybacks) kept treasury — the conservative reading, PROPOSED |
| 2 | §6 GEODNET (e) (G1/G2/F2) | the falls of treasury_holding_tokens (Mining + Mining distribution + Ecosystem) + the four candidate wallets — the mining payout counted in (b) AND (e) | the five non-mining filed wallets only (the Ecosystem wallet read on its own, ecosystem_holding_tokens); mining wallets (b) only; disjoint by test |
| 3 | §6 Aerodrome (S5) | team emission not shown | its own insider line: teamRate × (rebase + frozen weekly 8,969,149.54) ÷ (10,000 − teamRate), rate history 500 / 0 (2025-10-09) / 228 (2026-03-05) bps with Safe txs; minted into a filing wallet, so not circulating until released (e) |
| 4 | Aerodrome tail rate (S5) | 21 bps "from 2025-10-24" | from the 2025-10-23 flip (Nudge 22→21 at block 37,192,832) |
| 5 | Aerodrome rebase after the migration (decision 2) | "ends at the 21 Oct migration" | CONTRADICTED by aero.xyz/aero-economic-case (2026-03-30): old veAERO "will continue to receive rebases"; only sAERO has none. The (c) line keeps reading the RewardsDistributor while it pays; every legacy read dated on/after 2026-10-22 is refused LOUDLY until aero_migration.json says launched (RUNBOOK 11bx) |
| 6 | Maple supply (S3) | "pre-minted, issuance 0"; 44.4M of claims recorded | five RecapitalizationModule claims since 2025-07-01 (net 78,241,999.94; 2025-07-03 netted by a same-tx burn) minted to mapleTreasury — their own line (issuer_mint_tokens); gross issuance reads "n/a — minted only into the issuer's treasury: <Q0> …"; released (e) when they leave; MIP-021 tiers are FLAT bands (10/20/30%) |
| 7 | Hyperliquid FDV (S6) | price × CoinGecko total | price × (tokenDetails totalSupply − the Assistance Fund's balance), labelled: the docs say the Fund is burned, the API total still holds it |
| 8 | Uniswap (d) label (S7) | ecosystem / grants | ecosystem / grants / treasury operating spend (#90 DUNI legal/tax 1.58M, UF operations in #83, UAC / delegate pay); the #97 recall (12,500,001.19) and the 113,137.83 Safe inflow are returns (F1) |
| 9 | Plume staker yield source (S11) | unlabelled ~4.5% | on-chain getRewardRate × (1 − commission); the app's APY is computed in the browser from the same reads |
| 10 | Aethir DAT (S10) | Axe Compute 2,700,000,000 (rounded) | 2,655,414,499 (10-Q, 2026-06-30) |
| 11 | Ether.fi programmatic buybacks (S2) | "not executed" (r9) | the buyer is Safe 0x43F4cA92… (CoW: 1,467,688.39 ETHFI for $1,022,633.79 to 10-13), its own series; all of it paid on as OP cashback — none held, so nothing retired or credited; cashback from bought ETHFI is (c), not dilution; the 20M buffer has paid 0 |
| 12 | Pendle (S4) | PROPOSED 9 (Governance falls → (b) minus the gauge days) | REPLACED: per-transfer classes (gauges = (b) already; CEX = (e); unlabelled = (e)) — NOT WIRED, route in §10; latent overhang 9,837,598.56 accrued-unminted recorded (config.LATENT_SUPPLY) |
| 13 | GEODNET revenue (G3) | revenue as stored | labelled "implied from the burn (not independent)" (DefiLlama fees = burn ÷ 0.8) and EXCLUDED from the divergence row; burn-policy conflict recorded (filing 40% of gross vs X post 80%); no independent first-party figure exists |
| 14 | Chainlink (C20/C22) | Reward Vault "confirmed not inside e" | a known double count (§6); holders ÷ revenue ≈ 1 by construction (DefiLlama revenue = LINK to the Reserve) — labelled |
| 15 | §8 Token flow (F1/T1) | opening + mint + releases − burns; residual > 0.5% = CHECK | + "returned to issuer-side wallets", + "scheduled releases" where circulating is a schedule, memo of issuer mints; a residual > 0.5% without a named cause = BROKEN |
| 16 | NEAR (N1–N4) | v3 /stats circulating could be read; one v1 parity call | v3 circulating (== total) never used (test); the lockup history adopted (R10/N2); NearBlocks v3 intents volume stored beside DefiLlama's (near_intents_volume_usd_nearblocks; Business 1 reads DefiLlama's, NEAR's own Dune-based figure: revenue.near.org $31.40B through 2026-09-21); the v1 call removed |
| 17 | Coverage (T2–T5) | presence and declared inputs only | + independent references (independent_references.csv: VERIFIED / BROKEN for primary, CHECK for cross-checks — "secondary", "primary-ish", "primary-derived", "derived" — never VERIFIED; PROPOSED reading); + regression guard (a headline lost since the previous run = BROKEN unless on config.REGRESSION_ALLOWLIST); + VERIFIED share per project; + MATCHES-SPEC on OUTPUTS for Master price, market cap, circulating, FDV, fees Q0, revenue Q0 |
| 18 | Trajectory (decision 14) | growth only | + the 4Q slope's standard error (annual) |
| 19 | ETH supply history (S12/H1/H3) | CoinGecko mcap ÷ price, flat ~120.7M for a year | ultrasound supply-over-time before Etherscan's first read; issuance = d(supply) + burn (unblocks Total Yield and net issuance once stored) |
| 20 | Fixed supply text (R9) | mixed 0 / "n/a not applicable" | "0 — fixed supply: <why>" everywhere a project mints nothing (Maple no longer: item 6) |

## 14. FIX ROUND 3 (2026-10-13) — amendments, old → new

Built against the real dumps plus the real API responses of 2026-10-13 for the series the dumps predate
(tests/test_fix_round_3.py). PROPOSED where Jake's call is open (the conservative reading is built).

| # | Item | Old | New |
|---|---|---|---|
| 1 | Sky ratio base (Q2) | payout ratio (net basis), supplier payout, coverage ratio and the Flows value statement divided by NPS (after the revenue distribution) | divided by revenue − expense BEFORE distributions (net_revenue_pre_distribution_usd, Block Analitica, restated from 2025-01 every run; config.revenue_ratio_base_metric); the implied buyback stays on NPS (Stage 2's shares are % of NPS). Security and Maintenance (BA files it under revenue_distribution; Sky's financials count it as an expense) stays IN the base — DECIDED by the Sky Atlas TMF text (§15 row 5) |
| 2 | Hyperliquid history (B1 / H2) | (e), NHY, dilution, Total Yield BLOCKED before 2026-10-11 | the team / Foundation / Grants wallets replayed from their own ledgers (exact; end levels = live to 1e-8) prepended as the stocks' history; the rewards before the first stored read MODELLED (rate = k/√S, k from the Grants wallet's daily rate) under its own name, excluded from scoring; futureEmissions history a separately named MODELLED series |
| 3 | Chainlink (B2) | the staking funding LINK counted in (e) AND (a) (FR2 C20, NOT WIRED) | taken out of (e) per transfer: the lumps the 27 wallets send to 0xD50f074E… queue FIFO, each forward's staking share (what reached 0x1661f928… / 0x8716f86f…) comes out of its lump's release (FIFO — an attribution convention: ACCEPTED, Jake 2026-10-14; §15 row 4). Community-pool rate stored apart (staker_yield_community_pool: communityBase ÷ the community pool's principal; 4.323% vs Chainlink's 4.32%); the reference row now reads it |
| 4 | Morpho (b) (B3) | Ethereum claims only (~661/day) | + the DAO multisig's Merkl campaigns PAID off Ethereum (distributionChainId: Base, Katana, Arbitrum), each campaign's amount spread over its own window (~7,328/day in all; reference 7.5K, VERIFIED). The Arc / Tempo / Unichain campaigns pay on Ethereum and stay in the claims — never twice. PROPOSED: all of it (b) (the b/c split is not in the data) |
| 5 | Aerodrome dating (B4) | weekly rows filed under the epoch they were READ in (one flip early); the team formula shifted its rate by 7 days | every row of gross_issuance_tokens and emissions_tokens re-dated to the flip that PAID it (+7 days, read-time, whole history; config.EPOCH_PAID_AT); the epoch in progress is not a paid flow and is left out; the team emission takes its own date's rate (flip_offset_days retired) |
| 6 | NEAR revenue wallets (B5) | "no outflow except between themselves" | two outflows left the three, both BRIDGED off NEAR via one-time accounts to bridge-mng.near: 45,010 (1csfundsadmin, 2026-04-22) and 12,000 (buybacks.multisignature, 2026-07-10) — NOT retired (config.NEAR_REVENUE_OUTFLOWS, named on their days). buybacks.multisignature holds 1,960,118 NEAR (2026-10-12), not ~396K |
| 7 | TVL in native units (W1, C15) | NOT WIRED | Ether.fi: ETH staked = eETH.totalSupply() (= LiquidityPool.getTotalPooledEther()); Maple, Morpho, Aerodrome, Pendle, Fluid: DefiLlama's daily token amounts x the latest day's prices (PROPOSED basket); Sky: USDS supply. Business slots. Chains: not built (USD ÷ price restates price) |
| 8 | Boros volume (W2, C16) | NOT WIRED | every Boros market's daily traded units x its COLLATERAL's price — Boros's own daily price where the collateral is the underlying, $1 for USD₮0 (Q0 $3.043B, Q-4 $2.36B) |
| 9 | Ether.fi cashback (W3) | NOT WIRED | paid series (OP CashbackDistributor); (c) = the rise in max(0, Σ paid − Σ bought) — what the programme's purchases had not covered (404 ETHFI of 1,378,244 since 2026-09-03). PROPOSED: running maximum |
| 10 | Maple Drips (W4) | NOT WIRED history | (b) = SYRUP out of syrupDrip (no Reclaimed event exists; 4,097,369.65 in 2025-07-01..2026-02-18) |
| 11 | Leakage (W5) | (i), (iii) NOT WIRED | (i) = insider share of the stake x (credited − retired) ÷ credited holder value, Q0; (iii) = $ spent ÷ (tokens x the day's price) − 1, only where the spend is measured (Sky: Block Analitica Buyback Spending); elsewhere "n/a — tokens x price by construction" |
| 12 | Insider share of stake (W6) | Aerodrome, Hyperliquid | + Ether.fi, Maple, Pendle: the share token's balanceOf for every issuer-side wallet ÷ its totalSupply (0 on 2026-10-13) — superseded by §15 row 3 (NOT WIRED while no insider is identified) |
| 13 | Pendle (e) (W7) | every issuer-wallet fall (gauge transfers counted twice) | less the transfers straight to the GaugeController / externalRewardsDistributor (issuer_to_gauges_tokens, config.RELEASE_LESS); classes recorded (config.PENDLE_OUTFLOW_CLASSES) |
| 14 | References (B7) | Uniswap recall 12,500,000; Chainlink Reserve 3.06M dated end-Q1 | 12,500,001.19; 3.06M dated 2026-04-15 (Chainlink's figure) + 2,795,491 end-Q1 on-chain — all VERIFIED |
| 15 | Regression guard | — | the new legs (Morpho b, Maple b, Ether.fi c, Sky's ratio base) allow-listed to 2026-10-27, until their first read |
| 16 | Subsidy cover (V1) | a number whatever the denominator covers (Morpho 344.8x on Ethereum-only Merkl) | BLOCKED — unequal coverage, naming the missing part, where the incentive series is declared incomplete (config.OUTFLOW_INCOMPLETE): Fluid (Base / Plasma distributors unscanned), Aethir (Checker Node base schedule only), Pendle (six chains' gaugeControllers unscanned). Morpho is complete as known after #4 (33.7x on the dump with the campaigns) |
| 17 | Regression guard (V2) | "0 — fixed supply" read as a lost headline | a stated number ("<n> — <why>") is a value (sanity.stated_number) |
| 18 | Chainlink quarterly release reference (V5) | sum of pool_release_tokens over calendar Q3 (read 0) | the Flows tab's own "+ released from issuer wallets" line, Q0 — the same series and window (references route "flows") |
| 19 | Flows status (V6) | a leg short of the quarter: "residual … is unexplained" + the coverage note | the lead names a COVERAGE gap (the expected closing lacks the uncovered days), not a measured difference |
| 20 | Reference causes (V3 / V4) | — | Maple dao_safe_balance: the 2026-10-13 18:10 UTC treasury move named (tx 0xd628a262…); ETH burn_30d: the two-source basis named per leg |

## 15. ROUND 3 REVIEW (2026-10-14) — amendments, old → new

Jake's round 3 review: H1, H2, W6 and the decisions on P1 / P2. Built and tested against the real dumps
(tests/test_fix_round_3.py).

| # | Item | Old | New |
|---|---|---|---|
| 1 | Hyperliquid burn total (H1) | total_burn_tokens' history leg = the DefiLlama rows only (to 2026-09-11); the Fund's own reads 2026-09-12..09-28 dropped out, so the total had a 17-day hole and its coverage counted from the Core leg's first day: Q0 2,146,959 HYPE "over 18 days" → net supply −18.25%/yr, Total Yield non-stakers +9.0%, stakers +11.2% | the history leg keeps the Fund's own reads between its declared history and the total's first day (same component; build_workbook._burn_total_views). Q0 2,467,817 HYPE over 90 covered days → net supply −4.19%/yr; Total Yield non-stakers −5.06%, stakers −2.83%; HVY 3.38% → 3.97% and NHY −6.40% → −5.81% (the hole also dropped retired buyback) |
| 2 | Hyperliquid Q0 unlocks (H2) | — | reconciled, no change: 4,719,444 HYPE in (2026-07-15, 2026-10-13] = team 4,616,444 (433,025 + 433,419 + 3,750,000 on 08-06 / 09-06 / 10-07) + Hyper Foundation 100,000 + Community Grants 3,000; every token was undelegated, withdrawn and sent out of the wallet the same day — nothing stayed spot. The team wallet's stake earned 1,294,345 HYPE in Q0 that stayed staked (issuer-side). The published schedule (238M, one-year lock-up from 2024-11-29; "most vesting schedules completing between 2027 and 2028") publishes no monthly amount — what VESTED per month is n/a; only what LEFT is measured |
| 3 | Insider share of stake (W6) | Ether.fi, Maple, Pendle: 0 (no checked wallet holds shares) | NOT WIRED with the route, naming the wallets checked (config.INSIDER_SHARE_MONITOR; the wallet list is in "what is divided"); the reads stay on and the cell shows the ratio once a checked wallet holds shares. Leakage (i) follows it |
| 4 | Chainlink FIFO (P1) | PROPOSED: FIFO matching of the staking-vault funding to the releases | ACCEPTED (Jake 2026-10-14), labelled an ATTRIBUTION CONVENTION on both series' sources (staking_funding_release_tokens; unlock_release_tokens "-staking_funding_release_tokens (ATTRIBUTION CONVENTION …)") — LINK is fungible, so which release funded the vault is a convention, not a measurement |
| 5 | Sky S&M in the ratio base (P2) | PROPOSED: Security and Maintenance stays in the base | DECIDED by the Sky Atlas: A.2.3.1.2.2 "Step 1: Security And Maintenance" — "All of the Net Revenue from Step 0 ... becomes Step 1 Capital. Twenty percent (20%) of Step 1 Capital is allocated for security and maintenance of the Sky Ecosystem"; Step 0 "performs no allocations", and its Expenses (A.2.3.1.2.1.3: savings rate, Integration Boost, Distribution / Reimbursement / Pioneer rewards) do not include S&M (sky-ecosystem/next-gen-atlas@7df3d63, read 2026-10-14). S&M is a step in distributing Net Revenue → it stays in the base. Before → after: unchanged (September 15,379,185; payout ratio net basis 23.4%, supplier 44.4%, coverage 0.140) |
| 6 | NEAR buyback wallet | ~396K NEAR (secondary article) vs our on-chain read | withdrawn by Jake 2026-10-14; the on-chain 1,960,118 NEAR (2026-10-12) stands |
| 7 | Hyperliquid buyback vs revenue (sanity, follows #1) | — (the hole kept Q0 retirement under DefiLlama's revenue) | Q0 retirement $189.3M = 114% of DefiLlama revenue_usd $166.7M: documented funding (config buyback_funding, Hyperliquid docs Fees, read 2026-10-14) — the Assistance Fund converts trading fees to HYPE and receives the aligned-quote-asset reserve yield; auction and HyperEVM fees are burned. DefiLlama's own holders revenue ($178.7M) already exceeds its revenue; fees $213.7M |
| 8 | Log-scan reconciliation (run X1) | a window that does not reconcile to the wei stores nothing (Ether.fi's OP cashback: in − out 19.808964 ETHFI above the balanceOf change → (c), total dilution and NHY blank) | cause found: Blockscout (explorer.optimism.io and optimism.blockscout.com, every API) has NO logs for OP block 158,047,200 (2026-10-11), where the chain has the CashbackDistributor's Transfer of exactly 19.808964390546887 ETHFI (tx 0xb09bfbe3…). Still zero tolerance: when an explorer read does not reconcile, balanceOf is bisected against the running in − out to the first block where they part, that block's Transfers are read by RPC eth_getLogs, and the scan must then reconcile to the wei (at most 5 holes; a block with nothing new stays a refusal). The holes are named in the stored source line |
| 9 | Boros (run X2) | one full read per run inside a 120s tier (timed out with 0 rows) | resumable: each market's daily $ kept in .cache/boros/state.json with the day it is read through; the stalest first; the adapter stops at 420s (tier 480s), saving after every market; nothing stored until every market is read through a day, then only the days up to the stalest. First read of 232 markets ≈ 4 runs; then ~40 live markets a day |
| 10 | near_revenue_wallet_flows probe (run X3) | a 429 or an empty read fell through to "every outflow … is on the list" | paced by fetch.nearblocks' own pacer (6 credits a minute), a 429 waited out (61s) up to three times per read; any read that did not complete, or a revenue wallet with 0 receipts, gives VERDICT: INCONCLUSIVE naming the reads |
| 11 | NearBlocks tier budget (run X4) | 60s — the 7th of 9 daily one-credit calls waits ~54s for the free plan's minute | 180s: the day's calls (fetch.nearblocks_daily_calls(), 9) need two pacing windows; one more of slack |
| 12 | Explorer-read series without a balance reconciliation (X1 follow-up) | the 23 log_scans reconcile per holder to the wei; Pendle's three direct counts, Sky's burn scan and the X1 hole repair did not | ADDED: the direct counts (Pendle mainnet / Arbitrum / Optimism) read the inflows too and reconcile against balanceOf at both ends, holes repaired, else nothing stored; Sky's burn_logs reconcile against totalSupply (Σ mints − Σ burns, a mint stream added; holes found by bisection on totalSupply and read from the chain), else nothing stored; the hole read is RPC-only (scan_logs rpc_only) — the explorer that lacks a block is never asked for it again. NOT POSSIBLE by balanceOf: VRF / Automation fees (charged inside the coordinator / registry — no token moves), Plume network settlement and Blockscout stats (no holder: network-wide aggregates), Plume staking payouts (native PLUME funding arrives without logs), Etherscan ethsupply2 (a state read, not a scan). PARTIAL: GEODNET balance_flow (balance-anchored; a missed inflow shows only when a day turns negative). POSSIBLE, NOT BUILT — PROPOSED, Jake's call: Chainlink CCIP legacy / 2.0 fees and the aggregator intake (per OnRamp / aggregator and fee token: an outflow stream + balanceOf at the span ends; the streams keep per-day sums pruned at 365 days, so it is a rebuild of chainlink-fees state and a reseed) |
