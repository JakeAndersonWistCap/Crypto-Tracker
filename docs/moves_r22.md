# Round 22 — frozen moves (one table for the round)

Declared as a later step of the same cells in `tests/fixtures/frozen_moves_r22.json`. Built on the 15 fixture dumps (as of 2026-10-13); values are the fixture's, not Jake's store's.

| Project | Reason | Cells |
|---|---|---|
| Ether.fi | R22: the share of green cells is recounted | 1 |
| Ether.fi | R22 item 1(a) (Jake 2026-11-25): Ether.fi's core-contributor tokens not yet vested (MODELLED from the docs' schedule) are OUT of circulating until they vest; the vesting steps enter as net supply | 35 |
| Hyperliquid | R22 item 1(a) (Jake 2026-11-25): Hyperliquid's core-contributor HYPE (the team wallet 0x43e9abea…, 238.36M, all staked) is OUT of circulating until it vests — each payout out of the wallet enters as it vests; the Foundation and Grants stay in, off the float; the futureEmissions pot (409.6M) was already out; the Fund stays burned | 70 |
| Hyperliquid | R22 item 2 (Jake 2026-11-25): Hyperliquid's A4 is scored on every burn (total_burn_tokens: the Fund, the Core burns and the HyperEVM zero address; the base fee stays inferred and unsubtracted), and its ratios move with circulating (item 1(a)) | 40 |
| Morpho | R22 item 3 (Jake 2026-11-25): every Morpho vesting contract — a claim of vested tokens is not new supply; vesting counts on its schedule (the Labs vester's); the founders' modelled relock tranche is out of the model (their MIP1 awards vested on chain 2025-06-11 and circulate, R21b); Cohort 3's relock stays modelled | 34 |

Total: 180 cells.

| Project | Cell | Before | After | Reason |
|---|---|---|---|---|
| Ether.fi | audit / A3 Revenue Buyback / Circulating supply | 9.98536e+08 | 9.62753e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / A3 Revenue Buyback / Lock rate = locked inside circulating ÷ circulating (stake held by wallets outside circula | 0.0881839 | 0.0914615 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / A3 Revenue Buyback / Market cap ($) | 6.87369e+08 | 6.62737e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / A3 Revenue Buyback / Trend — a3_actual_buyback_pct (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory  | – · 4Q n/a · 1/4 improved · QoQ n/a | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · – · 4Q n/a · | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / A3 Revenue Buyback / Trend — a3_circ_retirement (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab | – · 4Q n/a · 1/4 improved · QoQ n/a | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · – · 4Q n/a · | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / A3 Revenue Buyback / Trend — a3_implied_buyback_pct (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory | – · 4Q n/a · QoQ n/a | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · – · 4Q n/a · | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / CP Comparability / Circulating supply (tokens) — our convention, the figure in use | 9.98536e+08 | 9.62753e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / CP Comparability / Dilution (e) unlocks (annualised) ÷ circulating | 0.0831747 | 0.0862661 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / CP Comparability / Flow demand — buyback $ (every funding source, annualised) ÷ market cap | 0.00163736 | 0.00169821 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / CP Comparability / Free float ÷ circulating | 0.687865 | 0.713431 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / CP Comparability / Holder value RECORDED, NOT CREDITED (treasury / emissions-funded) ÷ average market cap ove | 0.00203313 | 0.00212079 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / CP Comparability / Holding demand — lock rate = locked ÷ circulating | 0.0881839 | 0.0914615 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / CP Comparability / Issuer, foundation and treasury holdings inside circulating (tokens) — off the free float  | 2.23623e+08 | 1.8784e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / CP Comparability / Market cap ($) | 6.87369e+08 | 6.62737e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / CP Comparability / UNIFORMITY = whole-token yield ÷ staker yield (1 = every holder alike) (PROPOSED) | 0.110111 | 0.114204 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / CP Comparability / Whole-token yield — protocol = staker yield × reward-bearing stake ÷ circulating (now) | 0.00867733 | 0.00899984 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / Master / Circulating supply (used) | 9.98536e+08 | 9.62753e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / Master / Market cap ($) | 6.87369e+08 | 6.62737e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / Master / NET SUPPLY CHANGE Q0 (tokens) = closing − opening circulating supply | 0 | 1.78917e+07 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / Master / Net supply change, annualised % of circulating | 0 | 0.0753679 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / Master / Trend — master_buyback_of_supply_annualised_implied_split_as_at_this_window (arrow · QoQ · | – · 4Q n/a · QoQ n/a | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · – · 4Q n/a · | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / Master / Trend — master_circulating_supply_used (arrow · QoQ · 4Q growth/yr · quarters improved; Tr | → · +0%/yr (4Q) · QoQ +0.0% | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · ↑ · +8%/yr ( | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / Master / Trend — master_market_cap (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab) | ▼ · -25%/yr (4Q) · 1/4 improved · QoQ +74.9% | ▼ · -19%/yr (4Q) · 1/4 improved · QoQ +78.2% | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / Master / Trend — master_net_supply_change_annualised_of_circulating (arrow · QoQ · 4Q growth/yr · q | – · 4Q n/a · 0/3 improved · QoQ +0.0 pp | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · ▼ · +51%/yr  | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / Master / Trend — master_net_supply_change_q0_tokens_closing_opening_circulating_supp (arrow · QoQ · | – · 4Q n/a · 0/3 improved · QoQ n/a | ▼ · +63%/yr (4Q) · 1/3 improved · QoQ +0.0% | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | audit / Master / Turnover = 30d volume ÷ market cap (x) | 2.25996 | 2.34396 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | reader / A3 Revenue Buyback / Circulating supply | 9.98536e+08 | 9.62753e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | reader / A3 Revenue Buyback / Lock rate, % of circulating | 0.0881839 | 0.0914615 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | reader / A3 Revenue Buyback / Market cap | 6.87369e+08 | 6.62737e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | reader / Comparison / Lock rate, % of circulating | 0.0881839 | 0.0914615 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | reader / Comparison / Revenue ÷ market cap, a year | 0.0604764 | 0.0627242 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | reader / Summary / Confidence (share green) | 0.75 | 0.5 | R22 |
| Ether.fi | reader / Summary / Market cap | 6.87369e+08 | 6.62737e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | reader / Summary / Net supply change, % a year | 0 | 0.0753679 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | reader / Summary / Net supply change, 90 days | 0 | 1.78917e+07 | R22 item 1(a) (Jake 2026-11-25) |
| Ether.fi | reader / Summary / What changed | over 90D: Price +75% · Market cap +75% · TVL + | over 90D: Price +75% · Market cap +78% · TVL + | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A1 Infrastructure / Issuance as % of supply (annualised) | 0.039038 | 0.699407 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A1 Infrastructure / Issuer releases Q0 (tokens) — pre-minted supply released into circulation, NOT issuance | 214,122 | 3.83622e+06 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A1 Infrastructure / NET ISSUANCE AFTER BURN, annualised % of circulating (+ is net inflation; fee-market burns | -0.0185918 | -0.0332476 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A1 Infrastructure / NRR HEADROOM (trading throughput) = M2/GDP ÷ NRR — valuation headroom, not a target | 45.5347 | 81.4294 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A1 Infrastructure / NRR ON TRADING THROUGHPUT — market cap ÷ trailing-365d DEX + perps volume (DefiLlama) | 0.0155925 | 0.00871921 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A1 Infrastructure / Staking rate = staked inside circulating ÷ circulating | 0.374632 | 0.669952 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A3 Revenue Buyback / Actual buyback as % of supply (annualised) — RETIRED (burned + held) only | 0.0178845 | 0.0319828 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A3 Revenue Buyback / BUYBACK AS % OF SUPPLY (annualised, implied) | 0.0181199 | 0.0324037 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback % supply at Q1 (−3m window, split as at Q1) | 0.0192119 | 0.0343565 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A3 Revenue Buyback / Buyback % supply at Q2 (−6m, split as at Q2) | 0.0359936 | 0.0643672 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A3 Revenue Buyback / CIRCULATING RETIREMENT RATE (tokens) = RETIRED buyback tokens (burned + held, annualised)  | 0.0178845 | 0.0319828 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A3 Revenue Buyback / Circulating retirement rate ($) = RETIRED buyback $ (burned + held, annualised) ÷ market c | 0.0169115 | 0.0302427 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A3 Revenue Buyback / Circulating supply | 5.39759e+08 | 3.01829e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A3 Revenue Buyback / Lock rate = locked inside circulating ÷ circulating (stake held by wallets outside circula | 0.374632 | 0.669952 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A3 Revenue Buyback / Market cap ($) | 4.37024e+10 | 2.44381e+10 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / BURN ÷ ISSUANCE (x) — crossover, on the basis to the left | 0.305018 | 0.308787 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Burn as share of fees (measured) | 0.818847 | 0.828841 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Burn split Q0 (tokens): every burn − the A4 burn | 30,198.7 | <absent> | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Burn split — by source | A4 burn = burn_ex_chain_fee_tokens; every burn | A4 burn = total_burn_tokens; every burn (A1) = | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Burn Δ30d | n/a — SERIES BREAK 2026-09-25: burn_ex_chain_f | n/a — SERIES BREAK 2026-09-25: total_burn_toke | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Burn Δ3m | n/a — SERIES BREAK 2026-09-25: burn_ex_chain_f | n/a — SERIES BREAK 2026-09-25: total_burn_toke | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Burn Δ6m | n/a — SERIES BREAK 2026-09-25: burn_ex_chain_f | n/a — SERIES BREAK 2026-09-25: total_burn_toke | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Burn Δ9m | n/a — SERIES BREAK 2026-09-25: burn_ex_chain_f | n/a — SERIES BREAK 2026-09-25: total_burn_toke | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Circulating supply (used, tokens) | 5.39759e+08 | 3.01829e+08 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Fees required for crossover ($ a year) = fees × issuance ÷ burn, per covered day — at the  | 3.04565e+09 | 3.00847e+09 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / GROSS BURN Q0 (tokens) | 2.44421e+06 | 2.47441e+06 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Gross burn Q0 ($, each day's burn × that day's price) | 1.87567e+08 | 1.89857e+08 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / ISSUANCE − BURN Q0 (tokens) — self-reported net mint where published, else issuance − burn | -2.44421e+06 | -2.47441e+06 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / ISSUANCE − BURN, annualised % of circulating (signed; + is net inflation; the cell says 'b | -0.0183649 | -0.0332476 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Issuance as % of supply (annualised) | 0.039038 | 0.699407 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Market cap ($) | 4.37024e+10 | 2.44381e+10 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / PERMANENT BURN YIELD = burn as % of supply (annualised, tokens) | 0.0183649 | 0.0332476 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Pool release Q0 (tokens) — measured, premint distribution (where issuance is n/a) | 214,122 | 3.83622e+06 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / A4 Permanent Burn / Release ÷ burn (x) — premint projects, in place of burn ÷ issuance | BLOCKED — unequal coverage: release (pool_rele | BLOCKED — unequal coverage: release (pool_rele | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Burn rate (every burn, fee-market included — A1's basis, annualised) ÷ circulating | 0.0185918 | 0.0332476 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Circulating supply (tokens) — our convention, the figure in use | 5.39759e+08 | 3.01829e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / DILUTION, % OF MARKET CAP = outflows a+b+c+d+e+f at each day's price (annualised) ÷ averag | 0.0471392 | 0.0848055 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Dilution (a) security emissions (annualised) ÷ circulating | 0.00841335 | 0.0150455 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Dilution (e) unlocks (annualised) ÷ circulating | 0.0416085 | 0.0744082 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Discretionary supply ÷ circulating | 0.000288093 | 0.000515196 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / ETF / ETP holdings ÷ circulating | 0.0102403 | 0.0183127 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Flow demand — buyback $ (every funding source, annualised) ÷ market cap | 0.0169115 | 0.0302427 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Free float ÷ circulating | 0.184272 | 0.329533 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid bribes to voters (a | 0.0155919 | 0.0280504 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Holding demand — lock rate = locked ÷ circulating | 0.374632 | 0.669952 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Insider share of circulating = insider-held tokens counted inside circulating ÷ circulatin | 0.558515 | 0.210494 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Issuer, foundation and treasury holdings inside circulating (tokens) — off the free float  | 2.38086e+08 | 155,501 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Market cap ($) | 4.37024e+10 | 2.44381e+10 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / NET HOLDER YIELD = holder value yield − dilution (outflows a+b+c+d+e+f at each day's price | -0.0315474 | -0.056755 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Retirement rate (RETIRED buyback tokens, burned + held, annualised) ÷ circulating | 0.0178845 | 0.0319828 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / TOTAL DILUTION = outflows a+b+c+d+e+f (annualised) ÷ circulating | 0.0500218 | 0.0894537 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Treasury-company (DAT) holdings ÷ circulating | 0.0755762 | 0.135152 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / UNIFORMITY = whole-token yield ÷ staker yield (1 = every holder alike) (PROPOSED) | 0.374632 | 0.669952 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / CP Comparability / Whole-token yield — validator = staker yield × staked share (locked ÷ circulating, now) | 0.00858739 | 0.0153568 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / Master / Buyback % of supply (annualised, implied, split as at this window) | 0.0181199 | 0.0324037 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / Master / Circulating supply (used) | 5.39759e+08 | 3.01829e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / Master / Issuance − burn, annualised % of circulating — a component, NOT the net supply change | -0.0185918 | -0.0332476 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / Master / Issuer releases Q0 (tokens) — pre-minted supply released into circulation, NOT issuance | 214,122 | 3.83622e+06 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / Master / Market cap ($) | 4.37024e+10 | 2.44381e+10 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | audit / Master / Turnover = 30d volume ÷ market cap (x) | 0.629964 | 1.12656 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A1 Infrastructure / Circulating supply | 5.39759e+08 | 3.01829e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A1 Infrastructure / Issuance, % of supply a year | 0.039038 | 0.699407 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A1 Infrastructure / Issuer releases, 90 days (tokens) | 214,122 | 3.83622e+06 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A1 Infrastructure / Market cap | 4.37024e+10 | 2.44381e+10 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A1 Infrastructure / NRR headroom (throughput) | 45.5347 | 81.4294 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A1 Infrastructure / NRR on trading throughput | 0.0155925 | 0.00871921 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A1 Infrastructure / Net issuance after burn, % a year | -0.0185918 | -0.0332476 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A1 Infrastructure / Staking rate | 0.374632 | 0.669952 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A1 Infrastructure / Total yield, non-stakers | -0.03143 | -0.0562061 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A1 Infrastructure / Total yield, stakers | -0.0085078 | -0.0332839 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A3 Revenue Buyback / Circulating supply | 5.39759e+08 | 3.01829e+08 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A3 Revenue Buyback / Holder value yield | 0.0155919 | 0.0280504 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A3 Revenue Buyback / Implied buyback %, 3 months ago | 0.0192119 | 0.0343565 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A3 Revenue Buyback / Implied buyback %, 6 months ago | 0.0359936 | 0.0643672 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A3 Revenue Buyback / Implied buyback, % of supply a year | 0.0181199 | 0.0324037 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A3 Revenue Buyback / Lock rate, % of circulating | 0.374632 | 0.669952 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A3 Revenue Buyback / Market cap | 4.37024e+10 | 2.44381e+10 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A3 Revenue Buyback / Retired, % of supply a year | 0.0178845 | 0.0319828 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A3 Revenue Buyback / Retirement rate ($), % of mkt cap | 0.0169115 | 0.0302427 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A3 Revenue Buyback / Retirement rate, % of circulating | 0.0178845 | 0.0319828 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Burn share of fees | 0.818847 | 0.828841 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Burn split (tokens) | 30,198.7 | by design | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Burn split by source | A4 burn = burn_ex_chain_fee_tokens; every burn | A4 burn = total_burn_tokens; every burn (A1) = | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Burn ÷ issuance (x) | 0.305018 | 0.308787 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Burn ÷ issuance (x), latest | 0.305018 | 0.308787 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Burn ÷ issuance (x), −1 qtr | 0.460671 | 0.466302 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Burn ÷ issuance (x), −2 qtrs | 0.98009 | 0.98943 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Burn ÷ issuance (x), −3 qtrs | 0.859098 | 0.869047 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Circulating supply | 5.39759e+08 | 3.01829e+08 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Fees needed for crossover | 3.04565e+09 | 3.00847e+09 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Gross burn, 90 days ($) | 1.87567e+08 | 1.89857e+08 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Gross burn, 90 days (tokens) | 2.44421e+06 | 2.47441e+06 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Issuance − burn (tokens), latest | -2.44421e+06 | -2.47441e+06 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Issuance − burn (tokens), −1 qtr | -2.64799e+06 | -2.68036e+06 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Issuance − burn (tokens), −2 qtrs | -5.02248e+06 | -5.07035e+06 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Issuance − burn (tokens), −3 qtrs | -5.82989e+06 | -5.8974e+06 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Issuance − burn, % a year | -0.0183649 | -0.0332476 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Issuance − burn, 90 days | -2.44421e+06 | -2.47441e+06 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Issuance, % of supply a year | 0.039038 | 0.699407 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Market cap | 4.37024e+10 | 2.44381e+10 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / A4 Permanent Burn / Permanent burn yield | 0.0183649 | 0.0332476 | R22 item 2 (Jake 2026-11-25) |
| Hyperliquid | reader / Comparison / Dilution, % of market cap | 0.0471392 | 0.0848055 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / Comparison / Holder value yield | 0.0155919 | 0.0280504 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / Comparison / Lock rate, % of circulating | 0.374632 | 0.669952 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / Comparison / Net holder yield | -0.0315474 | -0.056755 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / Comparison / Revenue ÷ market cap, a year | 0.0167816 | 0.0300104 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / Summary / Dilution, % of market cap | 0.0471392 | 0.0848055 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / Summary / Holder value yield | 0.0155919 | 0.0280504 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / Summary / Market cap | 4.37024e+10 | 2.44381e+10 | R22 item 1(a) (Jake 2026-11-25) |
| Hyperliquid | reader / Summary / Net holder yield | -0.0315474 | -0.056755 | R22 item 1(a) (Jake 2026-11-25) |
| Morpho | audit / A3 Revenue Buyback / Circulating supply | 8.788e+08 | 9.62921e+08 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / A3 Revenue Buyback / FREE FLOAT (tokens) = circulating − staked/locked/earning − issuer/foundation/treasury hol | 4.26052e+08 | 5.10173e+08 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / A3 Revenue Buyback / Market cap ($) | 2.09834e+09 | 2.29919e+09 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / A3 Revenue Buyback / UNLOCK DILUTION Q0 (tokens) — released from the non-circulating wallets, NOT emissions | 2.7e+07 | 8.8772e+06 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / A3 Revenue Buyback / Unlock dilution, annualised % of free float | 0.257011 | 0.0705682 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / Circulating supply (tokens) — our convention, the figure in use | 8.788e+08 | 9.62921e+08 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / Dilution (e) unlocks (annualised) ÷ circulating | 0.124602 | 0.0373883 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / Dilution (e) unlocks (annualised) ÷ free float | 0.257011 | 0.0705682 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / Discretionary supply ÷ circulating | 0.515188 | 0.470182 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / ETF / ETP holdings ÷ circulating | 0.000608955 | 0.000555757 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / FREE FLOAT (tokens) = circulating − staked/locked/earning − issuer/foundation/treasury hol | 4.26052e+08 | 5.10173e+08 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / Free float ÷ circulating | 0.484812 | 0.529818 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / Insider share of circulating = insider-held tokens counted inside circulating ÷ circulatin | 0.515188 | 0.470182 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / Market cap ($) | 2.09834e+09 | 2.29919e+09 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / Outflow (e) unlocks — $ a year (each day's tokens x that day's price, annualised) | 2.50281e+08 | 8.22856e+07 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / Trend — cp_float (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab) | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · ↑ · +199%/yr | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · ↑ · +82%/yr  | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / CP Comparability / Unlocks Q0 (tokens) — releases from the non-circulating wallets | 2.7e+07 | 8.8772e+06 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / Master / Circulating supply (used) | 8.788e+08 | 9.62921e+08 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / Master / Market cap ($) | 2.09834e+09 | 2.29919e+09 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / Master / NET SUPPLY CHANGE Q0 (tokens) = closing − opening circulating supply | 2.7e+07 | 8.26027e+06 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / Master / Net supply change, annualised % of circulating | 0.124602 | 0.03479 | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / Master / Trend — master_circulating_supply_used (arrow · QoQ · 4Q growth/yr · quarters improved; Tr | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · ↑ · +14%/yr  | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · ↑ · +4%/yr ( | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / Master / Trend — master_market_cap (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab) | ▲ · +115%/yr (4Q) · 3/4 improved · QoQ +19.5% | ▲ · +96%/yr (4Q) · 3/4 improved · QoQ +16.8% | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / Master / Trend — master_net_supply_change_annualised_of_circulating (arrow · QoQ · 4Q growth/yr · q | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · ▼ · +55%/yr  | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · ▼ · +71%/yr  | R22 item 3 (Jake 2026-11-25) |
| Morpho | audit / Master / Turnover = 30d volume ÷ market cap (x) | 0.42889 | 0.391423 | R22 item 3 (Jake 2026-11-25) |
| Morpho | reader / A3 Revenue Buyback / Circulating supply | 8.788e+08 | 9.62921e+08 | R22 item 3 (Jake 2026-11-25) |
| Morpho | reader / A3 Revenue Buyback / Free float (tokens) | 4.26052e+08 | 5.10173e+08 | R22 item 3 (Jake 2026-11-25) |
| Morpho | reader / A3 Revenue Buyback / Market cap | 2.09834e+09 | 2.29919e+09 | R22 item 3 (Jake 2026-11-25) |
| Morpho | reader / A3 Revenue Buyback / Unlocks, % of free float a year | 0.257011 | 0.0705682 | R22 item 3 (Jake 2026-11-25) |
| Morpho | reader / A3 Revenue Buyback / Unlocks, last 90 days (tokens) | 2.7e+07 | 8.8772e+06 | R22 item 3 (Jake 2026-11-25) |
| Morpho | reader / Summary / Market cap | 2.09834e+09 | 2.29919e+09 | R22 item 3 (Jake 2026-11-25) |
| Morpho | reader / Summary / Net supply change, % a year | 0.124602 | 0.03479 | R22 item 3 (Jake 2026-11-25) |
| Morpho | reader / Summary / Net supply change, 90 days | 2.7e+07 | 8.26027e+06 | R22 item 3 (Jake 2026-11-25) |
| Morpho | reader / Summary / What changed | over 90D: Price +16% · Market cap +20% · Fees  | over 90D: Price +16% · Market cap +17% · Fees  | R22 item 3 (Jake 2026-11-25) |
