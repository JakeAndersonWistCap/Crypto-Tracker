# Round 22 — circulating lined up with the industry, on Jake's rule (CIRC lane)

Jake, 2026-11-25, item 1. The reading used throughout:

- **(a) unvested tokens wrongly counted** — tokens not yet vested under a published vesting or release schedule are OUT
  of circulating until they vest, whoever holds the key and whether or not they are staked. A staked unvested balance is
  reported, never counted.
- **(b) an unreleased emissions reserve** — a pre-minted pot that pays rewards, or that the issuer releases on a
  published schedule, is OUT like unminted supply. What it pays out is new supply on the day it is paid. A distribution
  wallet the reserve has already paid into counts as released.
- **(c) a genuine rule difference** — treasuries, foundations, issuer holdings and locks that earn stay IN circulating
  and off the free float. These are reported, not changed.

Sources and read dates:

- **ours before**: the 15 fixture dumps (as of 2026-10-13), built at 840733d (Round 21b final).
- **ours after**: the same rows on the R22 basis (config `# ===== R22 — CIRC` block; `tests/test_r22_circ.py`).
- **CG dump**: the dump's stored CoinGecko `circulating_supply` (2026-10-13).
- **CG now**: `api.coingecko.com/api/v3/coins/markets`, read 2026-11-25 11:59 UTC.
- **CMC now**: `api.coinmarketcap.com/data-api/v3/cryptocurrency/detail`, read 2026-11-25 12:00 UTC. CMC has no history
  in the store, so its dated figures are the reads named in config.
- The gap is ours ÷ reference − 1.

## The table (tokens)

| Project | Ours before (dump) | Ours after (dump) | CG dump | Gap before → after | CG now | CMC now | Ours now (dated) | Gap now vs CG / CMC | Class of each gap > 10% |
|---|---|---|---|---|---|---|---|---|---|
| Aerodrome | 1,997,645,622 | 1,997,645,622 | 1,000,197,079 | +99.7% → +99.7% | 770,227,722 | 797,461,594 | 2,141,501,029 (R19/R20 read) | +178% / +169% | (c) |
| Ether.fi | 998,535,999 | **962,752,666** | 965,350,000 | +3.4% → −0.3% | 998,535,999 | 965,350,000 | 974,680,443 (2026-11-25) | −2.4% / +1.0% | — (a) applied |
| Fluid | 100,000,000 | 100,000,000 | 83,696,997 | +19.5% → +19.5% | 83,696,997 | 83,696,997 | 100,000,000 | +19.5% / +19.5% | (c) |
| Hyperliquid | 539,758,896 | **301,828,851** | 222,445,714 | +142.6% → +35.7% | 237,661,044 | 252,059,822 | 301,047,827 (2026-11-25) | +26.7% / +19.4% | (a) applied; rest (c) and CMC's double count |
| Maple | 1,267,875,000 | 1,267,875,000 | 1,167,271,959 | +8.6% → +8.6% | 1,167,271,959 | 1,191,009,307 | 1,267,875,000 | +8.6% / +6.5% | under 10% |
| Morpho | 878,800,000 | 878,800,000 | 700,529,195 | +25.4% → +25.4% | 706,250,154 | 632,060,093 | 878,800,000 | +24.4% / +39.0% | (c) + MORPHO lane |
| Pendle | 281,527,448 | 281,527,448 | 173,833,547 | +62.0% → +62.0% | 174,177,059 | 174,177,067 | 281,527,448 | +61.6% / +61.6% | (c) |
| Sky | 23,352,337,673 | 23,352,337,673 | 23,432,032,004 | −0.3% → −0.3% | 23,316,118,073 | 23,315,847,375 | 23,352,337,673 | +0.2% / +0.2% | under 10% |
| Uniswap | 887,142,419 | 887,142,419 | 624,898,422 | +42.0% → +42.0% | 607,165,896 | 623,178,422 | 887,142,419 | +46.1% / +42.4% | (c) |
| Chainlink | 1,000,000,000 | **766,850,000** | 766,849,970 | +30.4% → 0.0% | 766,849,970 | 766,849,970 | 766,849,970 | 0.0% / 0.0% | — (b) applied |
| GEODNET | 930,406,064 | **834,982,730** | 460,548,400 | +102.0% → +81.3% | 753,948,399 | 482,087,634 | 836,152,718 (2026-11-23) | +10.9% / +73.4% | (b) applied; rest (c) / not established |
| Ethereum | 122,130,710 | 122,130,710 | 122,130,710 | 0.0% → 0.0% | 122,250,721 | 122,250,721 | — | 0.0% / 0.0% | under 10% |
| Near | 1,306,829,704 | 1,306,829,704 | 1,308,888,204 | −0.2% → −0.2% | 1,312,652,869 | 1,312,652,849 | 1,310,266,393 (store, 2026-11-19) | −0.1% / −0.1% | under 10%; (a) applied on the store |
| Aethir | 24,053,550,151 | 24,053,550,151 | 20,128,764,593 | +19.5% → +19.5% | 20,128,764,593 | 20,128,764,593 | 24,053,550,151 | +19.5% / +19.5% | not established |
| Plume | 6,606,142,167 | 6,606,142,167 | 6,606,142,167 | 0.0% → 0.0% | 7,030,374,396 | 7,030,374,396 | — | — | under 10% (Plume's own figure = the references') |

Notes on the "Ours now" column:

- **Hyperliquid**: `tokenDetails.circulatingSupply` 301,369,106 − the HyperEVM zero address 321,279.
- **Ether.fi**: total − 4/36 of 214.7M.
- **GEODNET**: the R17 read of 2026-11-23 less the two Mining wallets.
- **Near**: Jake's store, the 2026-11-19 lockup read (total 1,312,139,293 − LOCKED 1,872,900.53).
- **Aerodrome**: `config.AERO_RULE_READ`.

Where no newer figure was read, the dump's figure stands.

## Hyperliquid (items a–e)

All reads are from `api.hyperliquid.xyz/info`, 2026-11-25 ~12:00 UTC.

| Wallet | Spot | Staked | Locked / unlocked | Treatment |
|---|---|---|---|---|
| Team 0x43e9abea… (core contributors) | 0.0033 | **238,360,840.87** on 23 validators | The delegation lock expired on 2026-06-07 for all 23, so the stake is unlocked as staking goes. The API has **no vesting state**: no call splits vested from unvested. | **(a) OUT.** The unvested balance shows as staked: reported, not counted. |
| Hyper Foundation 0xd57ecca4… | 0.00000044 | 60,663,959.56 | — | (c) IN, off the float |
| Community Grants 0xa20fcfa0… | 105,001.00 | 2,875,904.21 | — | (c) IN, off the float |
| Assistance Fund 0xfefe…fefe | 49,310,020.83 | 0 | — | (d) burned (R21b), out |
| futureEmissions (no wallet; genesis 0xdddd…dddd) | 409,635,985.37 | — | — | (b) OUT already; its payouts are `emissions_tokens`, new supply on the day paid |

**The team wallet's payouts.** The team wallet has paid out 9,722,176 HYPE since 2025-11 (`userNonFundingLedgerUpdates`):
it undelegates, then sends to 132 addresses, mostly on the 6th–7th of the month.

| Month | HYPE paid out |
|---|---|
| 2025-11 | 1,745,745 |
| 2026-01 | 1,125,766 |
| 2026-02 | 140,333 |
| 2026-03 | 173,217 |
| 2026-04 | 333,335 |
| 2026-05 | 421,879 |
| 2026-06 | 533,752 |
| 2026-07 | 452,000 |
| 2026-08 | 433,024 |
| 2026-09 | 433,419 |
| 2026-10 | 3,750,000 |
| 2026-11 | 179,706 |

Each payout is a vested tranche entering circulation (`unlock_release_outside_tokens`). Vested-but-unpaid HYPE cannot be
told apart on chain, so the whole wallet stays out, as `tokenDetails` itself counts it.

**The pot.** At genesis, 0xdddd…dddd held 428,062,211.29 HYPE:

- 388,880,000: the 38.888% future-emissions and community-rewards allocation.
- 39,182,211: the rest of the genesis distribution. That is 310M less the 270,937,788.71 airdropped, plus HIP-2's 0.12M.

18,426,226 has been paid as staking rewards since genesis. The pot is the gap between Jake's ~388.9M and the API's
409.6M.

**Against the references (e)**, on 2026-11-25. Ours is 301,047,827.

| Reference | Figure | Gap | Named by wallet |
|---|---|---|---|
| tokenDetails | 301,369,106 | −0.11% | The HyperEVM zero address, −321,279 (burned, out of ours). |
| CoinGecko | 237,661,044 | **+26.7%** | Hyper Foundation stake +60,663,960 and Community Grants (stake + spot) +2,980,905. Both are (c): the rule counts issuer holdings in, off the float. Not named: −258,082 (0.11%). |
| CoinMarketCap | 252,059,822 | **+19.4%** | The Assistance Fund +49,310,021: CMC's figure equals tokenDetails' circulating less the Fund to 737 HYPE, though that circulating already leaves the Fund out. This is INFERRED from the arithmetic; CMC publishes no method. Also the HyperEVM zero address −321,279. Not named: −737. |

On the dump (2026-10-13), CoinGecko read 222,445,714. That is 16.16M below tokenDetails − Foundation − Grants
(238.60M), and the wallet behind it was not established. CoinGecko has since moved to exactly
tokenDetails − Foundation − Grants.

## Every gap over 10%, by wallet

**Aerodrome — (c).** CoinGecko and CMC leave three things out:

- sAERO, 1,078.0M: locked and earning, so in ours and off the float.
- The migration reserve of unswapped legacy AERO (310.5M) and VELO (20.5M). It stands for legacy tokens that
  non-issuers already hold.

CoinGecko's total is also stale by 8.4M (`config.AERO_RULE_READ`, R19/R20). There is no vesting schedule, and the
emissions are minted weekly, so there is no reserve: neither (a) nor (b).

**Fluid — (c).** Both references leave out the Treasury DSA 0x28849D2b…, 16,303,003. It is a governance treasury.

The IGP-137 wallet 0xCaBebC7f… holds 5,000,000. It is "legally locked … until 2030" for custodial partners. Both
references count it in, and so do we. It has no vesting schedule and is not an emissions pot, so it is named, not moved.

**Hyperliquid — (a) applied: the team, 238.36M.** The rest is set out above: (c) against CoinGecko, and CMC's double count
of the Fund.

**Morpho — (c) + MORPHO lane.**

- The DAO 0xcBa28b38…, 283.7M: a Safe 6-of-10, idle. It is (c).
- The founders' relock, 110.7M MODELLED, is already out of ours.
- The Contributor Grants Safes 0x53051Ef9… / 0x1590e7F4… (52.8M) may be (a) unvested contributor grants.
- The Rewards Safe 0xF057afeE… (2.57M) may be (b) an emissions reserve.

Neither is applied here: the MORPHO lane owns Morpho's vesting. Neither reference publishes its wallet set, so the
remaining split is not established.

**Pendle — (c).** The references leave out:

| Wallet | PENDLE |
|---|---|
| vePENDLE (locked, earning) | 63,159,346 |
| Ecosystem Fund 0x399Be606… | 16,120,440 |
| Governance multisig 0x8119EC16… | 19,751,622 |
| Team multisig 0x918cF6b1… + fee wallet | 7,746,793 |
| Not named | 585,422 |

PENDLE emissions are minted, so there is no pre-minted reserve. No vesting schedule is running.

**Uniswap — (c).** The references leave out:

| Wallet | UNI |
|---|---|
| The governance Timelock 0x1a9C8182… (vesting ended Sep 2024) | 262,247,996 |
| Uniswap Foundation 0x5069A64B… (CoinGecko only) | 13,314,112 |
| The merkle distributor (CoinGecko only) | 216,772 |
| Not named (CoinGecko) | 2,465,643 |

**Chainlink — (b) applied.**

- The 33 non-circulating wallets that Chainlink's supply page lists hold 233,150,029.58 LINK. They release on
  Chainlink's published 7%-a-year schedule, in quarterly tranches of 21.0M, 18.75M, 11.25M and 19.0M
  (`docs/r15_chainlink_pack.md`).
- They are OUT now. Each tranche enters circulating on its day.
- The Reserve (6.80M, a held buyback), the RewardVault and the path wallets stay IN, off the float.

**GEODNET — (b) applied: the Mining pot.**

- The Mining Safe 0xfa5fEd5c… holds 89,000,000 and the Mining distribution wallet 0x8FB9dd00… holds 1,063,344.64 (both
  on 2026-11-23). These are the reserve that pays miners. On the dump they held 95,423,334.
- They are OUT now. What they pay to miners (`pool_release_tokens`) is new supply on the day paid.
- The subtracted-wallet basis: `treasury_holding_tokens` is subtracted again and `ecosystem_holding_tokens` is added back.

Gaps that remain:

| Against | Gap | What it is |
|---|---|---|
| CoinGecko now | +10.9% | Ecosystem 0x3A6906E4… (+43,022,654) and the EOA 0x47c8ebec… (+40,000,010, funded only by the Investor Safe; INFERRED, not in our set); less the Mining distribution wallet 0x8FB9dd00… (−1,063,344.64), which CoinGecko counts and ours leaves out with the pot. To the token on the 2026-11-23 read. |
| CoinGecko, dump date | +81.3% | CoinGecko's figure then was a static aggregator number (462.4M, frozen since about mid-2025). |
| CMC now | +73.4% | CMC still serves that static basis (482.1M). It is not namable by wallet. |

Neither the Ecosystem nor the Team is moved. GEODNET's tokenomics page says "team, investors, and ecosystem will undergo a
multi-year unlocking process". It publishes no dates or amounts (its chart is the allocation split only), so no
unvested amount is established.

**Aethir — not established.** Ours is Aethir's own figure, the published schedule (athCirculatingSupply). It is
3.92bn above CoinGecko's on the dump. Aethir publishes no wallet address, and no reconciliation by wallet exists.

The candidates are all read in `config.PROJECT_BY_NAME['Aethir']['aethir_chain']`, 2026-11-23:

- The VestingWallet's unvested 11.07bn.
- The three MerkleVesters' 6.09bn.
- The issuer Safes.

Which of these Aethir's figure counts is not established. Nothing was moved.

## Moves declared

| Project | Class | Before → after (dump) | How (config R22 block) |
|---|---|---|---|
| Hyperliquid (frozen) | (a) | 539,758,896 → 301,828,851 | `RULE_IN["Hyperliquid"]["legs"]` = Grants' spot only. `_released_staked_views` adds the team only while it is a leg. The new point `derived:released_staked[R22: …]` is a declared handover point. The flows: `emissions_circulating_stake_tokens` in; the team's payouts are `unlock_release_outside_tokens`. |
| Ether.fi (frozen) | (a) | 998,535,999 → 962,752,666 | `etherfi_team_unvested_modelled_tokens` joins the subtract set and leaves the off-float set. Circulating is now on a MODELLED leg. Its vesting is `unlock_release_outside_tokens`. |
| Chainlink | (b) | 1,000,000,000 → 766,850,000 | `noncirculating_holding_tokens` is no longer a rule leg or off the float. Its falls are the flow-statement release. |
| GEODNET | (b) | 930,406,064 → 834,982,730 | `treasury_holding_tokens` is no longer a rule leg. `ecosystem_holding_tokens` is added back. The off float is Ecosystem + the five wallets. The flows: `pool_release_tokens`. |
| Near | (a) | no move on the dump (it holds no lockup read); on the store from 2026-11-19: −1,084,482.68 | `RULE_IN["Near"]["locked_all_out"]`: all of LOCKED is out, staked or not. The new point is declared (`NEAR_CIRC_RULE_POINTS`). |

## Frozen cells expected to move (dump build: 840733d → R22, one-project builds of the fixture dumps)

### Hyperliquid — 81 cells

Reason, for every cell: R22 (a): the team wallet's unvested HYPE (237,930,044 on the dump) leaves circulating — circulating 539,758,896 → 301,828,851; every cell on circulating or on market cap (price × circulating) follows; the free float does not move

| Cell | Before | After |
|---|---|---|
| audit › A1 Infrastructure › Issuance as % of supply (annualised) | 0.0390380275 | 0.699406541 |
| audit › A1 Infrastructure › Issuer releases Q0 (tokens) — pre-minted supply released into circulation, NOT issuance | 214122.129 | 3836218.87 |
| audit › A1 Infrastructure › NET ISSUANCE AFTER BURN, annualised % of circulating (+ is net inflation; fee-market burns included) | -0.0185918181 | -0.0332476473 |
| audit › A1 Infrastructure › NRR HEADROOM (trading throughput) = M2/GDP ÷ NRR — valuation headroom, not a target | 45.5346796 | 81.4294202 |
| audit › A1 Infrastructure › NRR ON TRADING THROUGHPUT — market cap ÷ trailing-365d DEX + perps volume (DefiLlama) | 0.0155925112 | 0.00871920736 |
| audit › A1 Infrastructure › Staking rate = staked inside circulating ÷ circulating | 0.374631896 | 0.669952186 |
| audit › A3 Revenue Buyback › Actual buyback as % of supply (annualised) — RETIRED (burned + held) only | 0.0178845249 | 0.0319827987 |
| audit › A3 Revenue Buyback › BUYBACK AS % OF SUPPLY (annualised, implied) | 0.0181198683 | 0.032403662 |
| audit › A3 Revenue Buyback › Buyback % supply at Q1 (−3m window, split as at Q1) | 0.0192118917 | 0.0343565216 |
| audit › A3 Revenue Buyback › Buyback % supply at Q2 (−6m, split as at Q2) | 0.0359936238 | 0.064367202 |
| audit › A3 Revenue Buyback › CIRCULATING RETIREMENT RATE (tokens) = RETIRED buyback tokens (burned + held, annualised) ÷ circulating — primary | 0.0178845249 | 0.0319827987 |
| audit › A3 Revenue Buyback › Circulating retirement rate ($) = RETIRED buyback $ (burned + held, annualised) ÷ market cap — alongside | 0.0169114779 | 0.0302427042 |
| audit › A3 Revenue Buyback › Circulating supply | 539758896.0 | 301828851.0 |
| audit › A3 Revenue Buyback › Lock rate = locked inside circulating ÷ circulating (stake held by wallets outside circulating excluded) | 0.374631896 | 0.669952186 |
| audit › A3 Revenue Buyback › Market cap ($) | 43702446000.0 | 24438057800.0 |
| audit › A4 Permanent Burn › Circulating supply (used, tokens) | 539758896.0 | 301828851.0 |
| audit › A4 Permanent Burn › ISSUANCE − BURN, annualised % of circulating (signed; + is net inflation; the cell says 'burn only' where nothing is minte | -0.0183649161 | -0.0328418797 |
| audit › A4 Permanent Burn › Issuance as % of supply (annualised) | 0.0390380275 | 0.699406541 |
| audit › A4 Permanent Burn › Market cap ($) | 43702446000.0 | 24438057800.0 |
| audit › A4 Permanent Burn › PERMANENT BURN YIELD = burn as % of supply (annualised, tokens) | 0.0183649161 | 0.0328418797 |
| audit › A4 Permanent Burn › Pool release Q0 (tokens) — measured, premint distribution (where issuance is n/a) | 214122.129 | 3836218.87 |
| audit › CP Comparability › Burn rate (every burn, fee-market included — A1's basis, annualised) ÷ circulating | 0.0185918181 | 0.0332476473 |
| audit › CP Comparability › Circulating supply (tokens) — our convention, the figure in use | 539758896.0 | 301828851.0 |
| audit › CP Comparability › DILUTION, % OF MARKET CAP = outflows a+b+c+d+e+f at each day's price (annualised) ÷ average market cap over the window — wh | 0.047139246 | 0.0848054608 |
| audit › CP Comparability › Dilution (a) security emissions (annualised) ÷ circulating | 0.00841334785 | 0.0150455443 |
| audit › CP Comparability › Dilution (e) unlocks (annualised) ÷ circulating | 0.0416084658 | 0.0744081934 |
| audit › CP Comparability › Discretionary supply ÷ circulating | 0.000288093451 | 0.000515195955 |
| audit › CP Comparability › ETF / ETP holdings ÷ circulating | 0.010240303 | 0.0183126783 |
| audit › CP Comparability › Flow demand — buyback $ (every funding source, annualised) ÷ market cap | 0.0169114779 | 0.0302427042 |
| audit › CP Comparability › Free float ÷ circulating | 0.184272001 | 0.329532618 |
| audit › CP Comparability › HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid bribes to voters (annualised) ÷ average market cap o | 0.0155918832 | 0.0280504452 |
| audit › CP Comparability › Holding demand — lock rate = locked ÷ circulating | 0.374631896 | 0.669952186 |
| audit › CP Comparability › Insider share of circulating = insider-held tokens counted inside circulating ÷ circulating | 0.558514731 | 0.210494292 |
| audit › CP Comparability › Issuer, foundation and treasury holdings inside circulating (tokens) — off the free float (R20) | 238085545.0 | 155501.003 |
| audit › CP Comparability › Market cap ($) | 43702446000.0 | 24438057800.0 |
| audit › CP Comparability › NET HOLDER YIELD = holder value yield − dilution (outflows a+b+c+d+e+f at each day's price, annualised) ÷ average market ca | -0.0315473628 | -0.0567550156 |
| audit › CP Comparability › Retirement rate (RETIRED buyback tokens, burned + held, annualised) ÷ circulating | 0.0178845249 | 0.0319827987 |
| audit › CP Comparability › TOTAL DILUTION = outflows a+b+c+d+e+f (annualised) ÷ circulating | 0.0500218136 | 0.0894537377 |
| audit › CP Comparability › Treasury-company (DAT) holdings ÷ circulating | 0.0755761513 | 0.135152421 |
| audit › CP Comparability › UNIFORMITY = whole-token yield ÷ staker yield (1 = every holder alike) (PROPOSED) | 0.374631896 | 0.669952186 |
| audit › CP Comparability › Whole-token yield — validator = staker yield × staked share (locked ÷ circulating, now) | 0.00858738527 | 0.0153567744 |
| audit › Master › Buyback % of supply (annualised, implied, split as at this window) | 0.0181198683 | 0.032403662 |
| audit › Master › Circulating supply (used) | 539758896.0 | 301828851.0 |
| audit › Master › Issuance − burn, annualised % of circulating — a component, NOT the net supply change | -0.0185918181 | -0.0332476473 |
| audit › Master › Issuer releases Q0 (tokens) — pre-minted supply released into circulation, NOT issuance | 214122.129 | 3836218.87 |
| audit › Master › Market cap ($) | 43702446000.0 | 24438057800.0 |
| audit › Master › Turnover = 30d volume ÷ market cap (x) | 0.629963507 | 1.12656032 |
| reader › A1 Infrastructure › Circulating supply | 539758896.0 | 301828851.0 |
| reader › A1 Infrastructure › Issuance, % of supply a year | 0.0390380275 | 0.699406541 |
| reader › A1 Infrastructure › Issuer releases, 90 days (tokens) | 214122.129 | 3836218.87 |
| reader › A1 Infrastructure › Market cap | 43702446000.0 | 24438057800.0 |
| reader › A1 Infrastructure › NRR headroom (throughput) | 45.5346796 | 81.4294202 |
| reader › A1 Infrastructure › NRR on trading throughput | 0.0155925112 | 0.00871920736 |
| reader › A1 Infrastructure › Net issuance after burn, % a year | -0.0185918181 | -0.0332476473 |
| reader › A1 Infrastructure › Staking rate | 0.374631896 | 0.669952186 |
| reader › A1 Infrastructure › Total yield, non-stakers | -0.0314299956 | -0.0562060904 |
| reader › A1 Infrastructure › Total yield, stakers | -0.00850780088 | -0.0332838957 |
| reader › A3 Revenue Buyback › Circulating supply | 539758896.0 | 301828851.0 |
| reader › A3 Revenue Buyback › Holder value yield | 0.0155918832 | 0.0280504452 |
| reader › A3 Revenue Buyback › Implied buyback %, 3 months ago | 0.0192118917 | 0.0343565216 |
| reader › A3 Revenue Buyback › Implied buyback %, 6 months ago | 0.0359936238 | 0.064367202 |
| reader › A3 Revenue Buyback › Implied buyback, % of supply a year | 0.0181198683 | 0.032403662 |
| reader › A3 Revenue Buyback › Lock rate, % of circulating | 0.374631896 | 0.669952186 |
| reader › A3 Revenue Buyback › Market cap | 43702446000.0 | 24438057800.0 |
| reader › A3 Revenue Buyback › Retired, % of supply a year | 0.0178845249 | 0.0319827987 |
| reader › A3 Revenue Buyback › Retirement rate ($), % of mkt cap | 0.0169114779 | 0.0302427042 |
| reader › A3 Revenue Buyback › Retirement rate, % of circulating | 0.0178845249 | 0.0319827987 |
| reader › A4 Permanent Burn › Circulating supply | 539758896.0 | 301828851.0 |
| reader › A4 Permanent Burn › Issuance − burn, % a year | -0.0183649161 | -0.0328418797 |
| reader › A4 Permanent Burn › Issuance, % of supply a year | 0.0390380275 | 0.699406541 |
| reader › A4 Permanent Burn › Market cap | 43702446000.0 | 24438057800.0 |
| reader › A4 Permanent Burn › Permanent burn yield | 0.0183649161 | 0.0328418797 |
| reader › Comparison › Dilution, % of market cap | 0.047139246 | 0.0848054608 |
| reader › Comparison › Holder value yield | 0.0155918832 | 0.0280504452 |
| reader › Comparison › Lock rate, % of circulating | 0.374631896 | 0.669952186 |
| reader › Comparison › Net holder yield | -0.0315473628 | -0.0567550156 |
| reader › Comparison › Revenue ÷ market cap, a year | 0.0167815547 | 0.0300103631 |
| reader › Summary › Dilution, % of market cap | 0.047139246 | 0.0848054608 |
| reader › Summary › Holder value yield | 0.0155918832 | 0.0280504452 |
| reader › Summary › Market cap | 43702446000.0 | 24438057800.0 |
| reader › Summary › Net holder yield | -0.0315473628 | -0.0567550156 |

### Ether.fi — 36 cells

Reason, for every cell: R22 (a): the unvested core-contributor leg (MODELLED, 35,783,333 on the dump) leaves circulating — 998,535,999 → 962,752,666; cells on circulating / market cap follow, the vesting enters as net supply change, the trends say MODELLED; the free float does not move

| Cell | Before | After |
|---|---|---|
| audit › A3 Revenue Buyback › Circulating supply | 998535999.0 | 962752666.0 |
| audit › A3 Revenue Buyback › Lock rate = locked inside circulating ÷ circulating (stake held by wallets outside circulating excluded) | 0.0881838571 | 0.0914614511 |
| audit › A3 Revenue Buyback › Market cap ($) | 687369310.0 | 662736883.0 |
| audit › A3 Revenue Buyback › Trend — a3_actual_buyback_pct (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab) | – · 4Q n/a · 1/4 improved · QoQ n/a | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · – · 4Q n/a · 1/4 improved · QoQ n/a |
| audit › A3 Revenue Buyback › Trend — a3_circ_retirement (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab) | – · 4Q n/a · 1/4 improved · QoQ n/a | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · – · 4Q n/a · 1/4 improved · QoQ n/a |
| audit › A3 Revenue Buyback › Trend — a3_implied_buyback_pct (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab) | – · 4Q n/a · QoQ n/a | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · – · 4Q n/a · QoQ n/a |
| audit › CP Comparability › Circulating supply (tokens) — our convention, the figure in use | 998535999.0 | 962752666.0 |
| audit › CP Comparability › Dilution (e) unlocks (annualised) ÷ circulating | 0.0831746626 | 0.0862660762 |
| audit › CP Comparability › Flow demand — buyback $ (every funding source, annualised) ÷ market cap | 0.00163735672 | 0.00169821356 |
| audit › CP Comparability › Free float ÷ circulating | 0.687864985 | 0.713431367 |
| audit › CP Comparability › Holder value RECORDED, NOT CREDITED (treasury / emissions-funded) ÷ average market cap over the window | 0.00203312581 | 0.00212079033 |
| audit › CP Comparability › Holding demand — lock rate = locked ÷ circulating | 0.0881838571 | 0.0914614511 |
| audit › CP Comparability › Issuer, foundation and treasury holdings inside circulating (tokens) — off the free float (R20) | 223623293.0 | 187839959.0 |
| audit › CP Comparability › Market cap ($) | 687369310.0 | 662736883.0 |
| audit › CP Comparability › UNIFORMITY = whole-token yield ÷ staker yield (1 = every holder alike) (PROPOSED) | 0.110111222 | 0.114203807 |
| audit › CP Comparability › Whole-token yield — protocol = staker yield × reward-bearing stake ÷ circulating (now) | 0.00867732507 | 0.00899984156 |
| audit › Master › Circulating supply (used) | 998535999.0 | 962752666.0 |
| audit › Master › Market cap ($) | 687369310.0 | 662736883.0 |
| audit › Master › NET SUPPLY CHANGE Q0 (tokens) = closing − opening circulating supply | 0.0 | 17891666.7 |
| audit › Master › Net supply change, annualised % of circulating | 0.0 | 0.0753679016 |
| audit › Master › Trend — master_buyback_of_supply_annualised_implied_split_as_at_this_window (arrow · QoQ · 4Q growth/yr · quarters improved; Trajecto | – · 4Q n/a · QoQ n/a | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · – · 4Q n/a · QoQ n/a |
| audit › Master › Trend — master_circulating_supply_used (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab) | → · +0%/yr (4Q) · QoQ +0.0% | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · ↑ · +8%/yr (4Q) · QoQ +1.9% |
| audit › Master › Trend — master_market_cap (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab) | ▼ · -25%/yr (4Q) · 1/4 improved · QoQ +74.9% | ▼ · -19%/yr (4Q) · 1/4 improved · QoQ +78.2% |
| audit › Master › Trend — master_net_supply_change_annualised_of_circulating (arrow · QoQ · 4Q growth/yr · quarters improved; Trajectory tab) | – · 4Q n/a · 0/3 improved · QoQ +0.0 pp | MODELLED Q0, Q-1, Q-2, Q-3, Q-4 · ▼ · +51%/yr (4Q) · 2/3 improved · QoQ -0.1 pp |
| audit › Master › Trend — master_net_supply_change_q0_tokens_closing_opening_circulating_supp (arrow · QoQ · 4Q growth/yr · quarters improved; Trajecto | – · 4Q n/a · 0/3 improved · QoQ n/a | ▼ · +63%/yr (4Q) · 1/3 improved · QoQ +0.0% |
| audit › Master › Turnover = 30d volume ÷ market cap (x) | 2.25995822 | 2.34395574 |
| reader › A3 Revenue Buyback › Circulating supply | 998535999.0 | 962752666.0 |
| reader › A3 Revenue Buyback › Lock rate, % of circulating | 0.0881838571 | 0.0914614511 |
| reader › A3 Revenue Buyback › Market cap | 687369310.0 | 662736883.0 |
| reader › Comparison › Lock rate, % of circulating | 0.0881838571 | 0.0914614511 |
| reader › Comparison › Revenue ÷ market cap, a year | 0.0604763947 | 0.0627241652 |
| reader › Summary › Confidence (share green) | 0.75 | 0.5 |
| reader › Summary › Market cap | 687369310.0 | 662736883.0 |
| reader › Summary › Net supply change, % a year | 0.0 | 0.0753679016 |
| reader › Summary › Net supply change, 90 days | 0.0 | 17891666.7 |
| reader › Summary › What changed | over 90D: Price +75% · Market cap +75% · TVL +68% ·  | over 90D: Price +75% · Market cap +78% · TVL +68% ·  |

No other frozen project (Aerodrome, Fluid, Maple, Morpho, Pendle, Sky, Uniswap) is expected to move: their circulating sets are unchanged (class (c), reported only).
