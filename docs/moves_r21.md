# Round 21 — frozen moves (one table for the round)

Every cell of a frozen project that moved on the 15-dump fixture build (as of 2026-10-13) at the Round 21 code, with its reason. The machine-readable copy `project_freeze.py --compare all` reads is `tests/fixtures/frozen_moves_r21.json`. Values are the fixture's, not Jake's store's. `<absent>` = the header did not exist before, or no longer exists.

## Counts by project and reason

| Project | Reason | Cells |
|---|---|---|
| Aerodrome | Item 2: Aerodrome's burns line needs foundation_aero_burned_other_tokens, which the dumps do not hold (the store does): net float pressure is unknown, so the parked project shows 'parked' — never a 0 | 2 |
| Aerodrome | the share of green cells is recounted over the Summary row's new and relabelled cells | 1 |
| Aerodrome | Item 2/3: new lines in net float pressure — burns on float (every burn, any source), bought tokens returned to the float (F1/F2/F7), the burn-source memo, and buying pressure on float on the A3 tab; new rows, read from the same float | 8 |
| Ether.fi | the share of green cells is recounted over the Summary row's new and relabelled cells | 1 |
| Ether.fi | Item 3 (F4) / decision 5: a paused / off programme shows its label and 'unranked'; its key is what the data reads (blocked here: a new-supply input has no rows on the dumps), never a forced 0 | 10 |
| Ether.fi | Item 2/3: new lines in net float pressure — burns on float (every burn, any source), bought tokens returned to the float (F1/F2/F7), the burn-source memo, and buying pressure on float on the A3 tab; new rows, read from the same float | 8 |
| Fluid | the share of green cells is recounted over the Summary row's new and relabelled cells | 1 |
| Fluid | Item 3 (F4) / decision 5: a paused / off programme shows its label and 'unranked'; its key is what the data reads (blocked here: a new-supply input has no rows on the dumps), never a forced 0 | 8 |
| Fluid | Item 2/3: new lines in net float pressure — burns on float (every burn, any source), bought tokens returned to the float (F1/F2/F7), the burn-source memo, and buying pressure on float on the A3 tab; new rows, read from the same float | 8 |
| Hyperliquid | Item 6: Hyperliquid's default until Jake decides — the Assistance Fund is a held buyback (inside circulating, +48.0M; off the free float; A4 off); 'burn' now counts only true burns (HyperCore supply fall and zero-address fees), so gross burn, net issuance and every per-circulating ratio move | 118 |
| Maple | Item 2/3: new lines in net float pressure — burns on float (every burn, any source), bought tokens returned to the float (F1/F2/F7), the burn-source memo, and buying pressure on float on the A3 tab; new rows, read from the same float | 8 |
| Morpho | the share of green cells is recounted over the Summary row's new and relabelled cells | 1 |
| Morpho | Item 3 (F4) / decision 5: a paused / off programme shows its label and 'unranked'; its key is what the data reads (blocked here: a new-supply input has no rows on the dumps), never a forced 0 | 9 |
| Morpho | Item 2/3: new lines in net float pressure — burns on float (every burn, any source), bought tokens returned to the float (F1/F2/F7), the burn-source memo, and buying pressure on float on the A3 tab; new rows, read from the same float | 8 |
| Pendle | Item 2/3: new lines in net float pressure — burns on float (every burn, any source), bought tokens returned to the float (F1/F2/F7), the burn-source memo, and buying pressure on float on the A3 tab; new rows, read from the same float | 8 |
| Pendle | Item 5: Pendle's buying pressure reads its own per-epoch buyback from the buyback contract on chain (DefiLlama the cross-check); the fixture's DefiLlama line had stopped, so it read 0 | 3 |
| Sky | Item 2/3: new lines in net float pressure — burns on float (every burn, any source), bought tokens returned to the float (F1/F2/F7), the burn-source memo, and buying pressure on float on the A3 tab; new rows, read from the same float | 8 |
| Sky | Item 3 (F3): staking rewards covered by USDS-funded SKY purchases are not new supply — only the unfunded part is outflow (c); dilution, the A3 key and both nets move with it; Sky's Stage 2 treasury burns join the burns line (F7 returns: the purchased SKY paid out to stakers leaves the holding wallet) | 26 |
| Uniswap | the share of green cells is recounted over the Summary row's new and relabelled cells | 1 |
| Uniswap | Item 2/3: new lines in net float pressure — burns on float (every burn, any source), bought tokens returned to the float (F1/F2/F7), the burn-source memo, and buying pressure on float on the A3 tab; new rows, read from the same float | 8 |
| Uniswap | Item 2: net float pressure = revenue-funded buying + burns − new supply − bought tokens returned (all ÷ free float); burns now count, as a named supply-reduction line separate from buying | 2 |
| Uniswap | Item 2: Uniswap's burn-to-claim is a burn, shown on the burns line, not buying; buying pressure reads a measured 0 (no revenue-funded buying held or paid out), no longer 'by design' | 3 |

Total: 250 cells.

## Cells whose VALUE changed (not a rename, not a new or removed column)

| Project | Cell | Before | After | Reason |
|---|---|---|---|---|
| Aerodrome | reader / Comparison / Net float pressure | -0.269054 | parked | Item 2 |
| Aerodrome | reader / Summary / Confidence (share green) | 0.214286 | 0.153846 | the share of green cells is recounted over the Summary row's new and relabelled cells |
| Aerodrome | reader / Summary / Net float pressure | -0.269054 | parked | Item 2 |
| Ether.fi | audit / A3 Revenue Buyback / REVENUE TO HOLDERS ÷ NEW SUPPLY (x) = revenue sent to holders ($) ÷ value of new | 0 | n/a — outflow_c_usd (q0): BLOCKED — BLOCKED —  | Item 3 (F4) / decision 5 |
| Ether.fi | reader / A3 Revenue Buyback / Rank in A3 | 5 | unranked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / A3 Revenue Buyback / Revenue to holders ÷ new supply (x) | 0 | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / A3 Revenue Buyback / To holders ÷ new supply, latest | 0 | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / A3 Revenue Buyback / To holders ÷ new supply, −1 qtr | n/a | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / A3 Revenue Buyback / To holders ÷ new supply, −2 qtrs | n/a | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / A3 Revenue Buyback / To holders ÷ new supply, −3 qtrs | n/a | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / Summary / Archetype key | 0 | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / Summary / Archetypes | A3 paused | A3 not executing since 1 Apr | Item 3 (F4) / decision 5 |
| Ether.fi | reader / Summary / Confidence (share green) | 0.777778 | 0.75 | the share of green cells is recounted over the Summary row's new and relabelled cells |
| Ether.fi | reader / Summary / Rank in archetype | 5 | unranked | Item 3 (F4) / decision 5 |
| Fluid | audit / A3 Revenue Buyback / REVENUE TO HOLDERS ÷ NEW SUPPLY (x) = revenue sent to holders ($) ÷ value of new | 0 | n/a — outflow_b_usd (q0): BLOCKED — BLOCKED —  | Item 3 (F4) / decision 5 |
| Fluid | reader / A3 Revenue Buyback / Rank in A3 | 4 | unranked | Item 3 (F4) / decision 5 |
| Fluid | reader / A3 Revenue Buyback / Revenue to holders ÷ new supply (x) | 0 | blocked | Item 3 (F4) / decision 5 |
| Fluid | reader / A3 Revenue Buyback / To holders ÷ new supply, latest | 0 | blocked | Item 3 (F4) / decision 5 |
| Fluid | reader / A3 Revenue Buyback / To holders ÷ new supply, −1 qtr | n/a | blocked | Item 3 (F4) / decision 5 |
| Fluid | reader / Summary / Archetype key | 0 | blocked | Item 3 (F4) / decision 5 |
| Fluid | reader / Summary / Archetypes | A3 paused | A3 paused, no restart condition stated | Item 3 (F4) / decision 5 |
| Fluid | reader / Summary / Confidence (share green) | 0.888889 | 0.875 | the share of green cells is recounted over the Summary row's new and relabelled cells |
| Fluid | reader / Summary / Rank in archetype | 4 | unranked | Item 3 (F4) / decision 5 |
| Hyperliquid | audit / A1 Infrastructure / Detail — Supply overhang, % of total supply (reserve ÷ total supply, latest) | 0.432156 | 0.411378 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / Gross burn Q0 (tokens) — every burn, fee-market included | 2.47441e+06 | 52,260.6 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / NET ISSUANCE AFTER BURN Q0 (tokens) — issuance − every burn, on the basis A4 use | -2.47441e+06 | -52,260.6 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / NET ISSUANCE AFTER BURN, annualised % of circulating (+ is net inflation; fee-ma | -0.0185918 | -0.00190903 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / NRR HEADROOM (trading throughput) = M2/GDP ÷ NRR — valuation headroom, not a tar | 45.5347 | 41.8154 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / NRR ON TRADING THROUGHPUT — market cap ÷ trailing-365d DEX + perps volume (DefiL | 0.0155925 | 0.0169794 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / Staking rate = staked inside circulating ÷ circulating | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / Trend — a1_gross_burn (arrow · QoQ · 4Q growth/yr · quarters improved; Trajector | SERIES BREAK 2026-09-25 · – · STEP CHANGE at Q | – · 4Q n/a · QoQ n/a — Q0 is part-held | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / Trend — a1_net_issuance (arrow · QoQ · 4Q growth/yr · quarters improved; Traject | SERIES BREAK 2026-09-25 · – · STEP CHANGE at Q | – · 4Q n/a · QoQ n/a — Q0 is part-held | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / Trend — a1_net_issuance_pct (arrow · QoQ · 4Q growth/yr · quarters improved; Tra | SERIES BREAK 2026-09-25 · – · STEP CHANGE at Q | SERIES BREAK 2026-09-24 · – · STEP CHANGE at Q | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Actual buyback Q0 BURNED (tokens) | 2.38027e+06 | 0 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Actual buyback Q0 HELD (tokens) — treasury, reserve, foundation-locked | 0 | 2.38027e+06 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Actual buyback as % of supply (annualised) — RETIRED (burned + held) only | 0.0178845 | 0.0164237 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / BURNED ÷ (EMISSIONS + UNLOCKS) (x) = BURNED buyback ÷ (emissions + unlocks), per | 0.296592 | 0 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / BUYBACK AS % OF SUPPLY (annualised, implied) | 0.0181199 | 0.0166398 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback % supply at Q1 (−3m window, split as at Q1) | 0.0192119 | 0.0176426 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback % supply at Q2 (−6m, split as at Q2) | 0.0359936 | 0.0330536 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback destination | burn | hold — the Assistance Fund 0xfefe…fefe holds w | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback to LOCKED supply (destination = hold) — off the free float while held | n/a — buyback_fund_balance (now): not applicab | n/a — Data: not applicable to this project — T | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback to YIELD PAYOUT (destination = distribute) — re-enters float, never nett | n/a — Destination effect on float is 'removed_ | n/a — Destination effect on float is 'locked_s | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / CIRCULATING RETIREMENT RATE (tokens) = RETIRED buyback tokens (burned + held, an | 0.0178845 | 0.0164237 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Circulating retirement rate ($) = RETIRED buyback $ (burned + held, annualised)  | 0.0169115 | 0.0155301 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Circulating supply | 5.39759e+08 | 5.87768e+08 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Contract vs dashboard divergence (flagged when beyond tolerance) | n/a — buyback_fund_balance (now): not applicab | n/a — buyback_fund_balance (now): not applicab | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Destination effect on float | removed_from_supply | locked_supply | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / FDV ($) | 7.69623e+10 | 8.08495e+10 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / FDV RETIREMENT RATE (tokens) = RETIRED buyback tokens (burned + held, annualised | 0.0101556 | 0.00966732 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / FDV retirement rate ($) = RETIRED buyback $ (burned + held, annualised) ÷ FDV —  | 0.00960305 | 0.00914135 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Fees ÷ FDV (annualised) | 0.0120706 | 0.0114902 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Lock rate = locked inside circulating ÷ circulating (stake held by wallets outsi | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Market cap ($) | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | audit / CP Comparability / Burn rate (every burn, fee-market included — A1's basis, annualised) ÷ circulati | 0.0185918 | 0.00190903 | Item 6 |
| Hyperliquid | audit / CP Comparability / Burn rate (every burn, fee-market included — A1's basis, annualised) ÷ free floa | 0.100893 | 0.0112813 | Item 6 |
| Hyperliquid | audit / CP Comparability / Circulating supply (tokens) — our convention, the figure in use | 5.39759e+08 | 5.87768e+08 | Item 6 |
| Hyperliquid | audit / CP Comparability / DILUTION, % OF MARKET CAP = outflows a+b+c+d+e+f at each day's price (annualised | 0.0471392 | 0.0433037 | Item 6 |
| Hyperliquid | audit / CP Comparability / Destination durability (burn / held-timelocked / held-discretionary) | burn — Assistance Fund (system address 0xfefe. | held (keyless; a hard fork could move it) — th | Item 6 |
| Hyperliquid | audit / CP Comparability / Dilution (a) security emissions (annualised) ÷ circulating | 0.00841335 | 0.00772614 | Item 6 |
| Hyperliquid | audit / CP Comparability / Dilution (e) unlocks (annualised) ÷ circulating | 0.0416085 | 0.0382098 | Item 6 |
| Hyperliquid | audit / CP Comparability / Discretionary supply ÷ circulating | 0.000288093 | 0.000264562 | Item 6 |
| Hyperliquid | audit / CP Comparability / ETF / ETP holdings ÷ circulating | 0.0102403 | 0.00940386 | Item 6 |
| Hyperliquid | audit / CP Comparability / Flow demand — buyback $ (every funding source, annualised) ÷ market cap | 0.0169115 | 0.0155301 | Item 6 |
| Hyperliquid | audit / CP Comparability / Free float ÷ circulating | 0.184272 | 0.16922 | Item 6 |
| Hyperliquid | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid bribes to | 0.0155919 | 0.0143232 | Item 6 |
| Hyperliquid | audit / CP Comparability / Holding demand — lock rate = locked ÷ circulating | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | audit / CP Comparability / Insider share of circulating = insider-held tokens counted inside circulating ÷  | 0.558515 | 0.512895 | Item 6 |
| Hyperliquid | audit / CP Comparability / Market cap ($) | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | audit / CP Comparability / NET HOLDER YIELD = holder value yield − dilution (outflows a+b+c+d+e+f at each d | -0.0315474 | -0.0289805 | Item 6 |
| Hyperliquid | audit / CP Comparability / Retirement rate (RETIRED buyback tokens, burned + held, annualised) ÷ circulatin | 0.0178845 | 0.0164237 | Item 6 |
| Hyperliquid | audit / CP Comparability / TOTAL DILUTION = outflows a+b+c+d+e+f (annualised) ÷ circulating | 0.0500218 | 0.045936 | Item 6 |
| Hyperliquid | audit / CP Comparability / Total supply (tokens) | 9.50544e+08 | 9.98553e+08 | Item 6 |
| Hyperliquid | audit / CP Comparability / Treasury-company (DAT) holdings ÷ circulating | 0.0755762 | 0.069403 | Item 6 |
| Hyperliquid | audit / CP Comparability / UNIFORMITY = whole-token yield ÷ staker yield (1 = every holder alike) (PROPOSED | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | audit / CP Comparability / Whole-token yield — validator = staker yield × staked share (locked ÷ circulatin | 0.00858739 | 0.00788596 | Item 6 |
| Hyperliquid | audit / Master / Buyback % of supply (annualised, implied, split as at this window) | 0.0181199 | 0.0166398 | Item 6 |
| Hyperliquid | audit / Master / Circulating supply (used) | 5.39759e+08 | 5.87768e+08 | Item 6 |
| Hyperliquid | audit / Master / FDV ($) | 7.69623e+10 | 8.08495e+10 | Item 6 |
| Hyperliquid | audit / Master / Fees ÷ FDV (annualised) | 0.0120706 | 0.0114902 | Item 6 |
| Hyperliquid | audit / Master / Gross burn Q0 (tokens) | 2.47441e+06 | 52,260.6 | Item 6 |
| Hyperliquid | audit / Master / Issuance − burn Q0 (tokens) — a component of the supply change, NOT the net supp | -2.47441e+06 | -52,260.6 | Item 6 |
| Hyperliquid | audit / Master / Issuance − burn, annualised % of circulating — a component, NOT the net supply c | -0.0185918 | -0.00190903 | Item 6 |
| Hyperliquid | audit / Master / Market cap ($) | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | audit / Master / Trend — master_gross_burn_q0_tokens (arrow · QoQ · 4Q growth/yr · quarters impro | SERIES BREAK 2026-09-25 · – · STEP CHANGE at Q | – · 4Q n/a · QoQ n/a — Q0 is part-held | Item 6 |
| Hyperliquid | audit / Master / Turnover = 30d volume ÷ market cap (x) | 0.629964 | 0.578507 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Circulating supply | 5.39759e+08 | 5.87768e+08 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / FDV | 7.69623e+10 | 8.08495e+10 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Fees ÷ FDV, a year | 0.0120706 | 0.0114902 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Gross burn, 90 days (tokens) | 2.47441e+06 | 52,260.6 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Market cap | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / NRR headroom (throughput) | 45.5347 | 41.8154 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / NRR on trading throughput | 0.0155925 | 0.0169794 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Net issuance after burn (tokens) | -2.47441e+06 | -52,260.6 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Net issuance after burn, % a year | -0.0185918 | -0.00190903 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Quarter change [45] | blocked | n/a | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Staking rate | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Total yield, non-stakers | -0.03143 | -0.0440269 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Total yield, stakers | -0.0085078 | -0.0211048 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Bought back and burned (tokens) | 2.38027e+06 | 0 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Bought back and held (tokens) | 0 | 2.38027e+06 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Burned ÷ (emissions + unlocks) | 0.296592 | 0 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Buyback destination | burn | hold — the Assistance Fund 0xfefe…fefe holds w | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Circulating supply | 5.39759e+08 | 5.87768e+08 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Effect on float | removed_from_supply | locked_supply | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / FDV | 7.69623e+10 | 8.08495e+10 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Fees ÷ FDV, a year | 0.0120706 | 0.0114902 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Holder value yield | 0.0155919 | 0.0143232 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Implied buyback %, 3 months ago | 0.0192119 | 0.0176426 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Implied buyback %, 6 months ago | 0.0359936 | 0.0330536 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Implied buyback, % of supply a year | 0.0181199 | 0.0166398 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Lock rate, % of circulating | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Market cap | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Retired, % of supply a year | 0.0178845 | 0.0164237 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Retirement rate ($), % of FDV | 0.00960305 | 0.00914135 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Retirement rate ($), % of mkt cap | 0.0169115 | 0.0155301 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Retirement rate, % of FDV supply | 0.0101556 | 0.00966732 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Retirement rate, % of circulating | 0.0178845 | 0.0164237 | Item 6 |
| Hyperliquid | reader / Business / Burn change, 30d vs prior 30d | -0.102373 | n/a | Item 6 |
| Hyperliquid | reader / Comparison / Dilution, % of market cap | 0.0471392 | 0.0433037 | Item 6 |
| Hyperliquid | reader / Comparison / Fees ÷ FDV, a year | 0.0120706 | 0.0114902 | Item 6 |
| Hyperliquid | reader / Comparison / Holder value yield | 0.0155919 | 0.0143232 | Item 6 |
| Hyperliquid | reader / Comparison / Lock rate, % of circulating | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | reader / Comparison / Net float pressure | -0.174401 | -0.16312 | Item 6 |
| Hyperliquid | reader / Comparison / Net holder yield | -0.0315474 | -0.0289805 | Item 6 |
| Hyperliquid | reader / Comparison / Revenue ÷ market cap, a year | 0.0167816 | 0.0154108 | Item 6 |
| Hyperliquid | reader / Summary / Dilution, % of market cap | 0.0471392 | 0.0433037 | Item 6 |
| Hyperliquid | reader / Summary / FDV | 7.69623e+10 | 8.08495e+10 | Item 6 |
| Hyperliquid | reader / Summary / Holder value yield | 0.0155919 | 0.0143232 | Item 6 |
| Hyperliquid | reader / Summary / Market cap | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | reader / Summary / Net float pressure | -0.174401 | -0.16312 | Item 6 |
| Hyperliquid | reader / Summary / Net holder yield | -0.0315474 | -0.0289805 | Item 6 |
| Morpho | audit / A3 Revenue Buyback / REVENUE TO HOLDERS ÷ NEW SUPPLY (x) = revenue sent to holders ($) ÷ value of new | 0 | n/a — outflow_b_usd (q0): BLOCKED — BLOCKED —  | Item 3 (F4) / decision 5 |
| Morpho | reader / A3 Revenue Buyback / Rank in A3 | 3 | unranked | Item 3 (F4) / decision 5 |
| Morpho | reader / A3 Revenue Buyback / Revenue to holders ÷ new supply (x) | 0 | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / A3 Revenue Buyback / To holders ÷ new supply, latest | 0 | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / A3 Revenue Buyback / To holders ÷ new supply, −1 qtr | n/a | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / A3 Revenue Buyback / To holders ÷ new supply, −2 qtrs | n/a | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / A3 Revenue Buyback / To holders ÷ new supply, −3 qtrs | n/a | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / Summary / Archetype key | 0 | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / Summary / Confidence (share green) | 0.5 | 0.428571 | the share of green cells is recounted over the Summary row's new and relabelled cells |
| Morpho | reader / Summary / Rank in archetype | 3 | unranked | Item 3 (F4) / decision 5 |
| Pendle | reader / A3 Revenue Buyback / Buying pressure on float, % a year | 0 | 0.0239766 | Item 5 |
| Pendle | reader / Comparison / Buying pressure on float, % a year | 0 | 0.0239766 | Item 5 |
| Pendle | reader / Summary / Buying pressure on float, % a year | 0 | 0.0239766 | Item 5 |
| Sky | audit / A3 Revenue Buyback / REVENUE TO HOLDERS ÷ NEW SUPPLY (x) = revenue sent to holders ($) ÷ value of new | 0.909856 | 1.44725 | Item 3 (F3) |
| Sky | audit / A3 Revenue Buyback / Trend — a3_revenue_vs_new_supply (arrow · QoQ · 4Q growth/yr · quarters improved | – · NEW since Q-3 · 1/2 improved · QoQ +260.9% | – · NEW since Q-3 · 1/2 improved · QoQ +474.1% | Item 3 (F3) |
| Sky | audit / CP Comparability / DILUTION, % OF MARKET CAP = outflows a+b+c+d+e+f at each day's price (annualised | 0.0259918 | 0.0163405 | Item 3 (F3) |
| Sky | audit / CP Comparability / Dilution (c) demand-side incentives (annualised) ÷ circulating | 0.0318675 | 0.0216067 | Item 3 (F3) |
| Sky | audit / CP Comparability / Dilution (c) demand-side incentives (annualised) ÷ free float | 0.126553 | 0.085805 | Item 3 (F3) |
| Sky | audit / CP Comparability / NET HOLDER YIELD = holder value yield − dilution (outflows a+b+c+d+e+f at each d | -0.002343 | 0.00730832 | Item 3 (F3) |
| Sky | audit / CP Comparability / Outflow (c) demand-side incentives — $ a year (each day's tokens x that day's pr | 4.75149e+07 | 2.98716e+07 | Item 3 (F3) |
| Sky | audit / CP Comparability / Outflow categories — basis and what has no series yet (config OUTFLOW_CATEGORIES | (c) emissions_tokens, (d) sky_ecosystem_out_to | (c) sky_staking_rewards_unfunded_tokens, (d) s | Item 3 (F3) |
| Sky | audit / CP Comparability / TOTAL DILUTION = outflows a+b+c+d+e+f (annualised) ÷ circulating | 0.0318675 | 0.0216067 | Item 3 (F3) |
| Sky | audit / CP Comparability / TOTAL DILUTION = outflows a+b+c+d+e+f (annualised) ÷ free float | 0.126553 | 0.085805 | Item 3 (F3) |
| Sky | reader / A3 Revenue Buyback / Buying pressure on float, % a year | 0.0236813 | 0.0559575 | Item 3 (F3) |
| Sky | reader / A3 Revenue Buyback / Key: change vs previous quarter | 2.60928 | 4.74106 | Item 3 (F3) |
| Sky | reader / A3 Revenue Buyback / Revenue to holders ÷ new supply (x) | 0.909856 | 1.44725 | Item 3 (F3) |
| Sky | reader / A3 Revenue Buyback / To holders ÷ new supply, latest | 0.909856 | 1.44725 | Item 3 (F3) |
| Sky | reader / Comparison / Buying pressure on float, % a year | 0.0236813 | 0.0559575 | Item 3 (F3) |
| Sky | reader / Comparison / Dilution, % of market cap | 0.0259918 | 0.0163405 | Item 3 (F3) |
| Sky | reader / Comparison / Net float pressure | -0.102872 | -0.0601542 | Item 3 (F3) |
| Sky | reader / Comparison / Net holder yield | -0.002343 | 0.00730832 | Item 3 (F3) |
| Sky | reader / Comparison / Total dilution, % of float | 0.126553 | 0.085805 | Item 3 (F3) |
| Sky | reader / Summary / Archetype key | 0.909856 | 1.44725 | Item 3 (F3) |
| Sky | reader / Summary / Buying pressure on float, % a year | 0.0236813 | 0.0559575 | Item 3 (F3) |
| Sky | reader / Summary / Dilution, % of market cap | 0.0259918 | 0.0163405 | Item 3 (F3) |
| Sky | reader / Summary / Net float pressure | -0.102872 | -0.0601542 | Item 3 (F3) |
| Sky | reader / Summary / Net holder yield | -0.002343 | 0.00730832 | Item 3 (F3) |
| Sky | reader / Summary / Total dilution, % of float | 0.126553 | 0.085805 | Item 3 (F3) |
| Sky | reader / Summary / Total dilution, tokens a year | 7.4418e+08 | 5.04566e+08 | Item 3 (F3) |
| Uniswap | reader / A3 Revenue Buyback / Buying pressure on float, % a year | by design | 0 | Item 2 |
| Uniswap | reader / Comparison / Buying pressure on float, % a year | by design | 0 | Item 2 |
| Uniswap | reader / Comparison / Net float pressure | -0.0334923 | 0.00355283 | Item 2 |
| Uniswap | reader / Summary / Buying pressure on float, % a year | by design | 0 | Item 2 |
| Uniswap | reader / Summary / Confidence (share green) | 0.615385 | 0.642857 | the share of green cells is recounted over the Summary row's new and relabelled cells |
| Uniswap | reader / Summary / Net float pressure | -0.0334923 | 0.00355283 | Item 2 |

## Every moved cell

| Project | Cell | Before | After | Reason |
|---|---|---|---|---|
| Aerodrome | audit / A3 Revenue Buyback / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0 | Item 2/3 |
| Aerodrome | audit / CP Comparability / BOUGHT TOKENS RETURNED TO THE FLOAT, % a year = bought tokens paid out liquid, sold or tra | <absent> | 0 | Item 2/3 |
| Aerodrome | audit / CP Comparability / BURNS ON FLOAT, % a year = every burn, whatever its source (tokens, annualised) ÷ free flo | <absent> | n/a — foundation_aero_burned_other_tokens (q0) | Item 2/3 |
| Aerodrome | audit / CP Comparability / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0 | Item 2/3 |
| Aerodrome | audit / CP Comparability / Burns — where they came from (memo: from float / staked / treasury, Q0) | <absent> | burns 2026-07-14..2026-10-13: 0 burned — buyba | Item 2/3 |
| Aerodrome | audit / CP Comparability / NET FLOAT PRESSURE, % a year = buying pressure on float + burns on float − total dilution  | <absent> | n/a — foundation_aero_burned_other_tokens (q0) | Item 2/3 |
| Aerodrome | reader / Comparison / Burns on float, % a year | <absent> | n/a | Item 2/3 |
| Aerodrome | reader / Comparison / Net float pressure | -0.269054 | parked | Item 2 |
| Aerodrome | reader / Comparison / Returned to float, % a year | <absent> | 0 | Item 2/3 |
| Aerodrome | reader / Summary / Confidence (share green) | 0.214286 | 0.153846 | the share of green cells is recounted over the Summary row's new and relabelled cells |
| Aerodrome | reader / Summary / Net float pressure | -0.269054 | parked | Item 2 |
| Ether.fi | audit / A3 Revenue Buyback / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0 | Item 2/3 |
| Ether.fi | audit / A3 Revenue Buyback / REVENUE TO HOLDERS ÷ NEW SUPPLY (x) = revenue sent to holders ($) ÷ value of new supply ($ | 0 | n/a — outflow_c_usd (q0): BLOCKED — BLOCKED —  | Item 3 (F4) / decision 5 |
| Ether.fi | audit / CP Comparability / BOUGHT TOKENS RETURNED TO THE FLOAT, % a year = bought tokens paid out liquid, sold or tra | <absent> | n/a — float_returned_tokens (q0): no rows stor | Item 2/3 |
| Ether.fi | audit / CP Comparability / BURNS ON FLOAT, % a year = every burn, whatever its source (tokens, annualised) ÷ free flo | <absent> | 0 | Item 2/3 |
| Ether.fi | audit / CP Comparability / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0 | Item 2/3 |
| Ether.fi | audit / CP Comparability / Burns — where they came from (memo: from float / staked / treasury, Q0) | <absent> | no burn — no burn mechanism: bought ETHFI is p | Item 2/3 |
| Ether.fi | audit / CP Comparability / NET FLOAT PRESSURE, % a year = buying pressure on float + burns on float − total dilution  | <absent> | n/a — outflow_c_tokens (q0): BLOCKED — BLOCKED | Item 2/3 |
| Ether.fi | reader / A3 Revenue Buyback / Rank in A3 | 5 | unranked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / A3 Revenue Buyback / Revenue to holders ÷ new supply (x) | 0 | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / A3 Revenue Buyback / To holders ÷ new supply, latest | 0 | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / A3 Revenue Buyback / To holders ÷ new supply, −1 qtr | n/a | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / A3 Revenue Buyback / To holders ÷ new supply, −2 qtrs | n/a | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / A3 Revenue Buyback / To holders ÷ new supply, −3 qtrs | n/a | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / Comparison / Burns on float, % a year | <absent> | 0 | Item 2/3 |
| Ether.fi | reader / Comparison / Returned to float, % a year | <absent> | n/a | Item 2/3 |
| Ether.fi | reader / Summary / Archetype key | 0 | blocked | Item 3 (F4) / decision 5 |
| Ether.fi | reader / Summary / Archetypes | A3 paused | A3 not executing since 1 Apr | Item 3 (F4) / decision 5 |
| Ether.fi | reader / Summary / Confidence (share green) | 0.777778 | 0.75 | the share of green cells is recounted over the Summary row's new and relabelled cells |
| Ether.fi | reader / Summary / Rank in archetype | 5 | unranked | Item 3 (F4) / decision 5 |
| Fluid | audit / A3 Revenue Buyback / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0 | Item 2/3 |
| Fluid | audit / A3 Revenue Buyback / REVENUE TO HOLDERS ÷ NEW SUPPLY (x) = revenue sent to holders ($) ÷ value of new supply ($ | 0 | n/a — outflow_b_usd (q0): BLOCKED — BLOCKED —  | Item 3 (F4) / decision 5 |
| Fluid | audit / CP Comparability / BOUGHT TOKENS RETURNED TO THE FLOAT, % a year = bought tokens paid out liquid, sold or tra | <absent> | 0 | Item 2/3 |
| Fluid | audit / CP Comparability / BURNS ON FLOAT, % a year = every burn, whatever its source (tokens, annualised) ÷ free flo | <absent> | 0 | Item 2/3 |
| Fluid | audit / CP Comparability / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0 | Item 2/3 |
| Fluid | audit / CP Comparability / Burns — where they came from (memo: from float / staked / treasury, Q0) | <absent> | no burn — no burn mechanism: bought FLUID went | Item 2/3 |
| Fluid | audit / CP Comparability / NET FLOAT PRESSURE, % a year = buying pressure on float + burns on float − total dilution  | <absent> | n/a — outflow_b_tokens (q0): BLOCKED — BLOCKED | Item 2/3 |
| Fluid | reader / A3 Revenue Buyback / Rank in A3 | 4 | unranked | Item 3 (F4) / decision 5 |
| Fluid | reader / A3 Revenue Buyback / Revenue to holders ÷ new supply (x) | 0 | blocked | Item 3 (F4) / decision 5 |
| Fluid | reader / A3 Revenue Buyback / To holders ÷ new supply, latest | 0 | blocked | Item 3 (F4) / decision 5 |
| Fluid | reader / A3 Revenue Buyback / To holders ÷ new supply, −1 qtr | n/a | blocked | Item 3 (F4) / decision 5 |
| Fluid | reader / Comparison / Burns on float, % a year | <absent> | 0 | Item 2/3 |
| Fluid | reader / Comparison / Returned to float, % a year | <absent> | 0 | Item 2/3 |
| Fluid | reader / Summary / Archetype key | 0 | blocked | Item 3 (F4) / decision 5 |
| Fluid | reader / Summary / Archetypes | A3 paused | A3 paused, no restart condition stated | Item 3 (F4) / decision 5 |
| Fluid | reader / Summary / Confidence (share green) | 0.888889 | 0.875 | the share of green cells is recounted over the Summary row's new and relabelled cells |
| Fluid | reader / Summary / Rank in archetype | 4 | unranked | Item 3 (F4) / decision 5 |
| Hyperliquid | audit / A1 Infrastructure / Detail — Supply overhang, % of total supply (reserve ÷ total supply, latest) | 0.432156 | 0.411378 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / Gross burn Q0 (tokens) — every burn, fee-market included | 2.47441e+06 | 52,260.6 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / NET ISSUANCE AFTER BURN Q0 (tokens) — issuance − every burn, on the basis A4 uses | -2.47441e+06 | -52,260.6 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / NET ISSUANCE AFTER BURN, annualised % of circulating (+ is net inflation; fee-market burns | -0.0185918 | -0.00190903 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / NRR HEADROOM (trading throughput) = M2/GDP ÷ NRR — valuation headroom, not a target | 45.5347 | 41.8154 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / NRR ON TRADING THROUGHPUT — market cap ÷ trailing-365d DEX + perps volume (DefiLlama) | 0.0155925 | 0.0169794 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / Staking rate = staked inside circulating ÷ circulating | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / Trend — a1_gross_burn (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab) | SERIES BREAK 2026-09-25 · – · STEP CHANGE at Q | – · 4Q n/a · QoQ n/a — Q0 is part-held | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / Trend — a1_net_issuance (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab) | SERIES BREAK 2026-09-25 · – · STEP CHANGE at Q | – · 4Q n/a · QoQ n/a — Q0 is part-held | Item 6 |
| Hyperliquid | audit / A1 Infrastructure / Trend — a1_net_issuance_pct (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory ta | SERIES BREAK 2026-09-25 · – · STEP CHANGE at Q | SERIES BREAK 2026-09-24 · – · STEP CHANGE at Q | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Actual buyback Q0 BURNED (tokens) | 2.38027e+06 | 0 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Actual buyback Q0 HELD (tokens) — treasury, reserve, foundation-locked | 0 | 2.38027e+06 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Actual buyback as % of supply (annualised) — RETIRED (burned + held) only | 0.0178845 | 0.0164237 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / BURNED ÷ (EMISSIONS + UNLOCKS) (x) = BURNED buyback ÷ (emissions + unlocks), per covered d | 0.296592 | 0 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / BUYBACK AS % OF SUPPLY (annualised, implied) | 0.0181199 | 0.0166398 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0.097055 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback % supply at Q1 (−3m window, split as at Q1) | 0.0192119 | 0.0176426 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback % supply at Q2 (−6m, split as at Q2) | 0.0359936 | 0.0330536 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback destination | burn | hold — the Assistance Fund 0xfefe…fefe holds w | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback to LOCKED supply (destination = hold) — off the free float while held | n/a — buyback_fund_balance (now): not applicab | n/a — Data: not applicable to this project — T | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback to YIELD PAYOUT (destination = distribute) — re-enters float, never netted against | n/a — Destination effect on float is 'removed_ | n/a — Destination effect on float is 'locked_s | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / CIRCULATING RETIREMENT RATE (tokens) = RETIRED buyback tokens (burned + held, annualised)  | 0.0178845 | 0.0164237 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Circulating retirement rate ($) = RETIRED buyback $ (burned + held, annualised) ÷ market c | 0.0169115 | 0.0155301 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Circulating supply | 5.39759e+08 | 5.87768e+08 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Contract vs dashboard divergence (flagged when beyond tolerance) | n/a — buyback_fund_balance (now): not applicab | n/a — buyback_fund_balance (now): not applicab | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Destination effect on float | removed_from_supply | locked_supply | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / FDV ($) | 7.69623e+10 | 8.08495e+10 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / FDV RETIREMENT RATE (tokens) = RETIRED buyback tokens (burned + held, annualised) ÷ FDV su | 0.0101556 | 0.00966732 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / FDV retirement rate ($) = RETIRED buyback $ (burned + held, annualised) ÷ FDV — alongside | 0.00960305 | 0.00914135 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Fees ÷ FDV (annualised) | 0.0120706 | 0.0114902 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Lock rate = locked inside circulating ÷ circulating (stake held by wallets outside circula | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | audit / A3 Revenue Buyback / Market cap ($) | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | audit / CP Comparability / BOUGHT TOKENS RETURNED TO THE FLOAT, % a year = bought tokens paid out liquid, sold or tra | <absent> | 0 | Item 6 |
| Hyperliquid | audit / CP Comparability / BURNS ON FLOAT, % a year = every burn, whatever its source (tokens, annualised) ÷ free flo | <absent> | 0.0112813 | Item 6 |
| Hyperliquid | audit / CP Comparability / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0.097055 | Item 6 |
| Hyperliquid | audit / CP Comparability / Burn rate (every burn, fee-market included — A1's basis, annualised) ÷ circulating | 0.0185918 | 0.00190903 | Item 6 |
| Hyperliquid | audit / CP Comparability / Burn rate (every burn, fee-market included — A1's basis, annualised) ÷ free float | 0.100893 | 0.0112813 | Item 6 |
| Hyperliquid | audit / CP Comparability / Burns — where they came from (memo: from float / staked / treasury, Q0) | <absent> | burns 2026-07-14..2026-10-13: from float 52,26 | Item 6 |
| Hyperliquid | audit / CP Comparability / Circulating supply (tokens) — our convention, the figure in use | 5.39759e+08 | 5.87768e+08 | Item 6 |
| Hyperliquid | audit / CP Comparability / DILUTION, % OF MARKET CAP = outflows a+b+c+d+e+f at each day's price (annualised) ÷ averag | 0.0471392 | 0.0433037 | Item 6 |
| Hyperliquid | audit / CP Comparability / Destination durability (burn / held-timelocked / held-discretionary) | burn — Assistance Fund (system address 0xfefe. | held (keyless; a hard fork could move it) — th | Item 6 |
| Hyperliquid | audit / CP Comparability / Dilution (a) security emissions (annualised) ÷ circulating | 0.00841335 | 0.00772614 | Item 6 |
| Hyperliquid | audit / CP Comparability / Dilution (e) unlocks (annualised) ÷ circulating | 0.0416085 | 0.0382098 | Item 6 |
| Hyperliquid | audit / CP Comparability / Discretionary supply ÷ circulating | 0.000288093 | 0.000264562 | Item 6 |
| Hyperliquid | audit / CP Comparability / ETF / ETP holdings ÷ circulating | 0.0102403 | 0.00940386 | Item 6 |
| Hyperliquid | audit / CP Comparability / Flow demand — buyback $ (every funding source, annualised) ÷ market cap | 0.0169115 | 0.0155301 | Item 6 |
| Hyperliquid | audit / CP Comparability / Free float ÷ circulating | 0.184272 | 0.16922 | Item 6 |
| Hyperliquid | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid bribes to voters (a | 0.0155919 | 0.0143232 | Item 6 |
| Hyperliquid | audit / CP Comparability / Held buyback inside circulating (tokens) — off the free float while held (R20; P4-Q5b) | <absent> | 4.80096e+07 | Item 6 |
| Hyperliquid | audit / CP Comparability / Holding demand — lock rate = locked ÷ circulating | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | audit / CP Comparability / Insider share of circulating = insider-held tokens counted inside circulating ÷ circulatin | 0.558515 | 0.512895 | Item 6 |
| Hyperliquid | audit / CP Comparability / Market cap ($) | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | audit / CP Comparability / NET FLOAT PRESSURE, % a year = buying pressure on float + burns on float − total dilution  | <absent> | -0.16312 | Item 6 |
| Hyperliquid | audit / CP Comparability / NET HOLDER YIELD = holder value yield − dilution (outflows a+b+c+d+e+f at each day's price | -0.0315474 | -0.0289805 | Item 6 |
| Hyperliquid | audit / CP Comparability / Retirement rate (RETIRED buyback tokens, burned + held, annualised) ÷ circulating | 0.0178845 | 0.0164237 | Item 6 |
| Hyperliquid | audit / CP Comparability / TOTAL DILUTION = outflows a+b+c+d+e+f (annualised) ÷ circulating | 0.0500218 | 0.045936 | Item 6 |
| Hyperliquid | audit / CP Comparability / Total supply (tokens) | 9.50544e+08 | 9.98553e+08 | Item 6 |
| Hyperliquid | audit / CP Comparability / Treasury-company (DAT) holdings ÷ circulating | 0.0755762 | 0.069403 | Item 6 |
| Hyperliquid | audit / CP Comparability / UNIFORMITY = whole-token yield ÷ staker yield (1 = every holder alike) (PROPOSED) | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | audit / CP Comparability / Whole-token yield — validator = staker yield × staked share (locked ÷ circulating, now) | 0.00858739 | 0.00788596 | Item 6 |
| Hyperliquid | audit / Master / Buyback % of supply (annualised, implied, split as at this window) | 0.0181199 | 0.0166398 | Item 6 |
| Hyperliquid | audit / Master / Circulating supply (used) | 5.39759e+08 | 5.87768e+08 | Item 6 |
| Hyperliquid | audit / Master / FDV ($) | 7.69623e+10 | 8.08495e+10 | Item 6 |
| Hyperliquid | audit / Master / Fees ÷ FDV (annualised) | 0.0120706 | 0.0114902 | Item 6 |
| Hyperliquid | audit / Master / Gross burn Q0 (tokens) | 2.47441e+06 | 52,260.6 | Item 6 |
| Hyperliquid | audit / Master / Issuance − burn Q0 (tokens) — a component of the supply change, NOT the net supply change | -2.47441e+06 | -52,260.6 | Item 6 |
| Hyperliquid | audit / Master / Issuance − burn, annualised % of circulating — a component, NOT the net supply change | -0.0185918 | -0.00190903 | Item 6 |
| Hyperliquid | audit / Master / Market cap ($) | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | audit / Master / Trend — master_gross_burn_q0_tokens (arrow · QoQ · 4Q growth/yr · quarters improved; Traje | SERIES BREAK 2026-09-25 · – · STEP CHANGE at Q | – · 4Q n/a · QoQ n/a — Q0 is part-held | Item 6 |
| Hyperliquid | audit / Master / Turnover = 30d volume ÷ market cap (x) | 0.629964 | 0.578507 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Circulating supply | 5.39759e+08 | 5.87768e+08 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / FDV | 7.69623e+10 | 8.08495e+10 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Fees ÷ FDV, a year | 0.0120706 | 0.0114902 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Gross burn, 90 days (tokens) | 2.47441e+06 | 52,260.6 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Market cap | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / NRR headroom (throughput) | 45.5347 | 41.8154 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / NRR on trading throughput | 0.0155925 | 0.0169794 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Net issuance after burn (tokens) | -2.47441e+06 | -52,260.6 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Net issuance after burn, % a year | -0.0185918 | -0.00190903 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Quarter change [45] | blocked | n/a | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Staking rate | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Total yield, non-stakers | -0.03143 | -0.0440269 | Item 6 |
| Hyperliquid | reader / A1 Infrastructure / Total yield, stakers | -0.0085078 | -0.0211048 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Bought back and burned (tokens) | 2.38027e+06 | 0 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Bought back and held (tokens) | 0 | 2.38027e+06 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Burned ÷ (emissions + unlocks) | 0.296592 | 0 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Buyback destination | burn | hold — the Assistance Fund 0xfefe…fefe holds w | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Circulating supply | 5.39759e+08 | 5.87768e+08 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Effect on float | removed_from_supply | locked_supply | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / FDV | 7.69623e+10 | 8.08495e+10 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Fees ÷ FDV, a year | 0.0120706 | 0.0114902 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Holder value yield | 0.0155919 | 0.0143232 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Implied buyback %, 3 months ago | 0.0192119 | 0.0176426 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Implied buyback %, 6 months ago | 0.0359936 | 0.0330536 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Implied buyback, % of supply a year | 0.0181199 | 0.0166398 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Lock rate, % of circulating | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Market cap | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Retired, % of supply a year | 0.0178845 | 0.0164237 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Retirement rate ($), % of FDV | 0.00960305 | 0.00914135 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Retirement rate ($), % of mkt cap | 0.0169115 | 0.0155301 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Retirement rate, % of FDV supply | 0.0101556 | 0.00966732 | Item 6 |
| Hyperliquid | reader / A3 Revenue Buyback / Retirement rate, % of circulating | 0.0178845 | 0.0164237 | Item 6 |
| Hyperliquid | reader / Business / Burn change, 30d vs prior 30d | -0.102373 | n/a | Item 6 |
| Hyperliquid | reader / Comparison / Burns on float, % a year | <absent> | 0.0112813 | Item 6 |
| Hyperliquid | reader / Comparison / Dilution, % of market cap | 0.0471392 | 0.0433037 | Item 6 |
| Hyperliquid | reader / Comparison / Fees ÷ FDV, a year | 0.0120706 | 0.0114902 | Item 6 |
| Hyperliquid | reader / Comparison / Holder value yield | 0.0155919 | 0.0143232 | Item 6 |
| Hyperliquid | reader / Comparison / Lock rate, % of circulating | 0.374632 | 0.344032 | Item 6 |
| Hyperliquid | reader / Comparison / Net float pressure | -0.174401 | -0.16312 | Item 6 |
| Hyperliquid | reader / Comparison / Net holder yield | -0.0315474 | -0.0289805 | Item 6 |
| Hyperliquid | reader / Comparison / Returned to float, % a year | <absent> | 0 | Item 6 |
| Hyperliquid | reader / Comparison / Revenue ÷ market cap, a year | 0.0167816 | 0.0154108 | Item 6 |
| Hyperliquid | reader / Summary / Dilution, % of market cap | 0.0471392 | 0.0433037 | Item 6 |
| Hyperliquid | reader / Summary / FDV | 7.69623e+10 | 8.08495e+10 | Item 6 |
| Hyperliquid | reader / Summary / Holder value yield | 0.0155919 | 0.0143232 | Item 6 |
| Hyperliquid | reader / Summary / Market cap | 4.37024e+10 | 4.75896e+10 | Item 6 |
| Hyperliquid | reader / Summary / Net float pressure | -0.174401 | -0.16312 | Item 6 |
| Hyperliquid | reader / Summary / Net holder yield | -0.0315474 | -0.0289805 | Item 6 |
| Maple | audit / A3 Revenue Buyback / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | n/a — free float: a component is not read — ma | Item 2/3 |
| Maple | audit / CP Comparability / BOUGHT TOKENS RETURNED TO THE FLOAT, % a year = bought tokens paid out liquid, sold or tra | <absent> | n/a — free float: a component is not read — ma | Item 2/3 |
| Maple | audit / CP Comparability / BURNS ON FLOAT, % a year = every burn, whatever its source (tokens, annualised) ÷ free flo | <absent> | 0 | Item 2/3 |
| Maple | audit / CP Comparability / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | n/a — free float: a component is not read — ma | Item 2/3 |
| Maple | audit / CP Comparability / Burns — where they came from (memo: from float / staked / treasury, Q0) | <absent> | no burn — no burn mechanism: bought SYRUP live | Item 2/3 |
| Maple | audit / CP Comparability / NET FLOAT PRESSURE, % a year = buying pressure on float + burns on float − total dilution  | <absent> | n/a — free float: a component is not read — ma | Item 2/3 |
| Maple | reader / Comparison / Burns on float, % a year | <absent> | 0 | Item 2/3 |
| Maple | reader / Comparison / Returned to float, % a year | <absent> | n/a | Item 2/3 |
| Morpho | audit / A3 Revenue Buyback / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0 | Item 2/3 |
| Morpho | audit / A3 Revenue Buyback / REVENUE TO HOLDERS ÷ NEW SUPPLY (x) = revenue sent to holders ($) ÷ value of new supply ($ | 0 | n/a — outflow_b_usd (q0): BLOCKED — BLOCKED —  | Item 3 (F4) / decision 5 |
| Morpho | audit / CP Comparability / BOUGHT TOKENS RETURNED TO THE FLOAT, % a year = bought tokens paid out liquid, sold or tra | <absent> | 0 | Item 2/3 |
| Morpho | audit / CP Comparability / BURNS ON FLOAT, % a year = every burn, whatever its source (tokens, annualised) ÷ free flo | <absent> | 0 | Item 2/3 |
| Morpho | audit / CP Comparability / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0 | Item 2/3 |
| Morpho | audit / CP Comparability / Burns — where they came from (memo: from float / staked / treasury, Q0) | <absent> | no burn — no burn mechanism: fixed supply, the | Item 2/3 |
| Morpho | audit / CP Comparability / NET FLOAT PRESSURE, % a year = buying pressure on float + burns on float − total dilution  | <absent> | n/a — outflow_b_tokens (q0): BLOCKED — BLOCKED | Item 2/3 |
| Morpho | reader / A3 Revenue Buyback / Rank in A3 | 3 | unranked | Item 3 (F4) / decision 5 |
| Morpho | reader / A3 Revenue Buyback / Revenue to holders ÷ new supply (x) | 0 | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / A3 Revenue Buyback / To holders ÷ new supply, latest | 0 | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / A3 Revenue Buyback / To holders ÷ new supply, −1 qtr | n/a | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / A3 Revenue Buyback / To holders ÷ new supply, −2 qtrs | n/a | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / A3 Revenue Buyback / To holders ÷ new supply, −3 qtrs | n/a | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / Comparison / Burns on float, % a year | <absent> | 0 | Item 2/3 |
| Morpho | reader / Comparison / Returned to float, % a year | <absent> | 0 | Item 2/3 |
| Morpho | reader / Summary / Archetype key | 0 | blocked | Item 3 (F4) / decision 5 |
| Morpho | reader / Summary / Confidence (share green) | 0.5 | 0.428571 | the share of green cells is recounted over the Summary row's new and relabelled cells |
| Morpho | reader / Summary / Rank in archetype | 3 | unranked | Item 3 (F4) / decision 5 |
| Pendle | audit / A3 Revenue Buyback / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0.0239766 | Item 2/3 |
| Pendle | audit / CP Comparability / BOUGHT TOKENS RETURNED TO THE FLOAT, % a year = bought tokens paid out liquid, sold or tra | <absent> | 0 | Item 2/3 |
| Pendle | audit / CP Comparability / BURNS ON FLOAT, % a year = every burn, whatever its source (tokens, annualised) ÷ free flo | <absent> | 0 | Item 2/3 |
| Pendle | audit / CP Comparability / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0.0239766 | Item 2/3 |
| Pendle | audit / CP Comparability / Burns — where they came from (memo: from float / staked / treasury, Q0) | <absent> | no burn — no burn mechanism: bought PENDLE is  | Item 2/3 |
| Pendle | audit / CP Comparability / NET FLOAT PRESSURE, % a year = buying pressure on float + burns on float − total dilution  | <absent> | n/a — outflow_b_tokens (q0): BLOCKED — BLOCKED | Item 2/3 |
| Pendle | reader / A3 Revenue Buyback / Buying pressure on float, % a year | 0 | 0.0239766 | Item 5 |
| Pendle | reader / Comparison / Burns on float, % a year | <absent> | 0 | Item 2/3 |
| Pendle | reader / Comparison / Buying pressure on float, % a year | 0 | 0.0239766 | Item 5 |
| Pendle | reader / Comparison / Returned to float, % a year | <absent> | 0 | Item 2/3 |
| Pendle | reader / Summary / Buying pressure on float, % a year | 0 | 0.0239766 | Item 5 |
| Sky | audit / A3 Revenue Buyback / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0.0559575 | Item 2/3 |
| Sky | audit / A3 Revenue Buyback / REVENUE TO HOLDERS ÷ NEW SUPPLY (x) = revenue sent to holders ($) ÷ value of new supply ($ | 0.909856 | 1.44725 | Item 3 (F3) |
| Sky | audit / A3 Revenue Buyback / Trend — a3_revenue_vs_new_supply (arrow · QoQ · 4Q growth/yr · quarters improved; Trajecto | – · NEW since Q-3 · 1/2 improved · QoQ +260.9% | – · NEW since Q-3 · 1/2 improved · QoQ +474.1% | Item 3 (F3) |
| Sky | audit / CP Comparability / BOUGHT TOKENS RETURNED TO THE FLOAT, % a year = bought tokens paid out liquid, sold or tra | <absent> | 0.0407481 | Item 2/3 |
| Sky | audit / CP Comparability / BURNS ON FLOAT, % a year = every burn, whatever its source (tokens, annualised) ÷ free flo | <absent> | 0.0104414 | Item 2/3 |
| Sky | audit / CP Comparability / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0.0559575 | Item 2/3 |
| Sky | audit / CP Comparability / Burns — where they came from (memo: from float / staked / treasury, Q0) | <absent> | burns 2026-07-14..2026-10-13: from treasury 10 | Item 2/3 |
| Sky | audit / CP Comparability / DILUTION, % OF MARKET CAP = outflows a+b+c+d+e+f at each day's price (annualised) ÷ averag | 0.0259918 | 0.0163405 | Item 3 (F3) |
| Sky | audit / CP Comparability / Dilution (c) demand-side incentives (annualised) ÷ circulating | 0.0318675 | 0.0216067 | Item 3 (F3) |
| Sky | audit / CP Comparability / Dilution (c) demand-side incentives (annualised) ÷ free float | 0.126553 | 0.085805 | Item 3 (F3) |
| Sky | audit / CP Comparability / NET FLOAT PRESSURE, % a year = buying pressure on float + burns on float − total dilution  | <absent> | -0.0601542 | Item 2/3 |
| Sky | audit / CP Comparability / NET HOLDER YIELD = holder value yield − dilution (outflows a+b+c+d+e+f at each day's price | -0.002343 | 0.00730832 | Item 3 (F3) |
| Sky | audit / CP Comparability / Outflow (c) demand-side incentives — $ a year (each day's tokens x that day's price, annua | 4.75149e+07 | 2.98716e+07 | Item 3 (F3) |
| Sky | audit / CP Comparability / Outflow categories — basis and what has no series yet (config OUTFLOW_CATEGORIES) | (c) emissions_tokens, (d) sky_ecosystem_out_to | (c) sky_staking_rewards_unfunded_tokens, (d) s | Item 3 (F3) |
| Sky | audit / CP Comparability / TOTAL DILUTION = outflows a+b+c+d+e+f (annualised) ÷ circulating | 0.0318675 | 0.0216067 | Item 3 (F3) |
| Sky | audit / CP Comparability / TOTAL DILUTION = outflows a+b+c+d+e+f (annualised) ÷ free float | 0.126553 | 0.085805 | Item 3 (F3) |
| Sky | reader / A3 Revenue Buyback / Buying pressure on float, % a year | 0.0236813 | 0.0559575 | Item 3 (F3) |
| Sky | reader / A3 Revenue Buyback / Key: change vs previous quarter | 2.60928 | 4.74106 | Item 3 (F3) |
| Sky | reader / A3 Revenue Buyback / Revenue to holders ÷ new supply (x) | 0.909856 | 1.44725 | Item 3 (F3) |
| Sky | reader / A3 Revenue Buyback / To holders ÷ new supply, latest | 0.909856 | 1.44725 | Item 3 (F3) |
| Sky | reader / Comparison / Burns on float, % a year | <absent> | 0.0104414 | Item 2/3 |
| Sky | reader / Comparison / Buying pressure on float, % a year | 0.0236813 | 0.0559575 | Item 3 (F3) |
| Sky | reader / Comparison / Dilution, % of market cap | 0.0259918 | 0.0163405 | Item 3 (F3) |
| Sky | reader / Comparison / Net float pressure | -0.102872 | -0.0601542 | Item 3 (F3) |
| Sky | reader / Comparison / Net holder yield | -0.002343 | 0.00730832 | Item 3 (F3) |
| Sky | reader / Comparison / Returned to float, % a year | <absent> | 0.0407481 | Item 2/3 |
| Sky | reader / Comparison / Total dilution, % of float | 0.126553 | 0.085805 | Item 3 (F3) |
| Sky | reader / Summary / Archetype key | 0.909856 | 1.44725 | Item 3 (F3) |
| Sky | reader / Summary / Buying pressure on float, % a year | 0.0236813 | 0.0559575 | Item 3 (F3) |
| Sky | reader / Summary / Dilution, % of market cap | 0.0259918 | 0.0163405 | Item 3 (F3) |
| Sky | reader / Summary / Net float pressure | -0.102872 | -0.0601542 | Item 3 (F3) |
| Sky | reader / Summary / Net holder yield | -0.002343 | 0.00730832 | Item 3 (F3) |
| Sky | reader / Summary / Total dilution, % of float | 0.126553 | 0.085805 | Item 3 (F3) |
| Sky | reader / Summary / Total dilution, tokens a year | 7.4418e+08 | 5.04566e+08 | Item 3 (F3) |
| Uniswap | audit / A3 Revenue Buyback / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0 | Item 2/3 |
| Uniswap | audit / CP Comparability / BOUGHT TOKENS RETURNED TO THE FLOAT, % a year = bought tokens paid out liquid, sold or tra | <absent> | 0 | Item 2/3 |
| Uniswap | audit / CP Comparability / BURNS ON FLOAT, % a year = every burn, whatever its source (tokens, annualised) ÷ free flo | <absent> | 0.0370451 | Item 2/3 |
| Uniswap | audit / CP Comparability / BUYING PRESSURE ON FLOAT, % a year = revenue-funded buying, held or paid out (tokens, annu | <absent> | 0 | Item 2/3 |
| Uniswap | audit / CP Comparability / Burns — where they came from (memo: from float / staked / treasury, Q0) | <absent> | burns 2026-07-14..2026-10-13: from float 5,864 | Item 2/3 |
| Uniswap | audit / CP Comparability / NET FLOAT PRESSURE, % a year = buying pressure on float + burns on float − total dilution  | <absent> | 0.00355283 | Item 2/3 |
| Uniswap | reader / A3 Revenue Buyback / Buying pressure on float, % a year | by design | 0 | Item 2 |
| Uniswap | reader / Comparison / Burns on float, % a year | <absent> | 0.0370451 | Item 2/3 |
| Uniswap | reader / Comparison / Buying pressure on float, % a year | by design | 0 | Item 2 |
| Uniswap | reader / Comparison / Net float pressure | -0.0334923 | 0.00355283 | Item 2 |
| Uniswap | reader / Comparison / Returned to float, % a year | <absent> | 0 | Item 2/3 |
| Uniswap | reader / Summary / Buying pressure on float, % a year | by design | 0 | Item 2 |
| Uniswap | reader / Summary / Confidence (share green) | 0.615385 | 0.642857 | the share of green cells is recounted over the Summary row's new and relabelled cells |
| Uniswap | reader / Summary / Net float pressure | -0.0334923 | 0.00355283 | Item 2 |
