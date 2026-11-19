# Research pack: Ether.fi (ETHFI, staked form sETHFI)

Read 2026-11-19, 14:05–14:35 UTC. Read-only: no code, config, test or other doc changed. One workbook build from the repo's 15 fixture dumps, for (f).

- On-chain figures are my own `eth_call` reads at **Ethereum block 26,449,731 (2026-11-19 14:29:23 UTC)** via ethereum-rpc.publicnode.com unless another block is given. L2 reads are at each chain's head at 14:21 UTC. "M" = million ETHFI.
- Transfer histories are from Blockscout (eth.blockscout.com, explorer.optimism.io), read the same day.
- PUBLISHED = named in an ether.fi document. INFERRED = not named anywhere; the evidence is given.

## Read this first

1. **CoinGecko now reports circulating = total supply = 998,535,999.** It was 965,350,000 in the repo's dump (2026-10-13). CoinMarketCap still shows 965,350,000, a self-reported figure last refreshed 2026-09-16.
2. **The repo's circulating (810.7M on the dump) breaks Jake's rule.** It subtracts 13 treasury / foundation wallets. All are multisigs or keyed wallets, so under the rule they are in circulating and off the float. Ether.fi is not in `config.RULE_IN`.
3. **The "roughly 65M unvested team" figure is not reproducible from a primary source.** The filing and docs give about 24–29M unvested today. No vesting contract exists: team tokens sit in 32 plain custodian wallets holding 150.7M, most of it already vested. None of the 32 holds sETHFI on any chain.
4. **Staked ETHFI is 138.0M, not 88M.** The repo reads mainnet sETHFI shares only. Shares on all five chains × the vault's own rate = 138.03M, and that matches the ETHFI actually held (vaults + Symbiotic collateral) to 0.01%.
5. **No ETHFI has been bought for stakers since 2026-04-01.** The repo's whole Q0 "actual buyback" (493,032 ETHFI) is three Arrakis liquidity-pool withdrawals on 2026-09-02, the same shape as a fourth the repo already excludes.
6. **The new programme (from 2026-09-08) buys about $225–250K of ETHFI a week, and all of it is paid to card users as cashback.** Nothing is held by the Foundation, and cashback paid (5.0M) exceeds ETHFI bought (2.9M).
7. **Since July 2026 ether.fi wallets sent 9.3M ETHFI to FalconX- and Binance-tagged addresses** (9.1M from the Foundation's Operating Safe, 0.2M from the deployer key). Purpose not established. Stakers received 1.29M in the same months.
8. **Insiders are large stakers.** At least 34.3M sETHFI (39.9% of mainnet shares) sits in nine wallets funded only by investor / team custodian wallets (INFERRED). The repo's cell says "no insider wallet identified".

## What the repo already holds

| Item | State | Rests on | Check today |
|---|---|---|---|
| `CIRCULATING_ONCHAIN["Ether.fi"]` | "established": total − `treasury_holding_tokens` − `noncirculating_holding_tokens` (13 wallets) | Blockworks filing; allocations page; Jake's sweep 2026-10-07 | Wallets confirmed. The subtraction conflicts with the rule of 2026-11-17 |
| `RULE_IN`, `REVENUE_SPLIT`, `DECLARED_NA_REASONS`, `FROZEN_PROJECTS` | no Ether.fi entry | — | — |
| `allocation_table` | Investors 32.5%, Treasury 27.24%, Team 23.26%, Airdrop 11%… | "gitbook via DropsTab", 2026-09-23 | **Stale.** The docs page (updated 2025-08-02) now reads differently, see (a) |
| Total supply | `totalSupply()` 998,535,999 | on-chain | Confirmed |
| Bridge | OFT adapter `0xe0080d2F…` is a lockbox, counted once | Jake 2026-10-07 | Confirmed: L2 supply 35,288,594 vs lockbox 35,289,335 |
| `locked_tokens` | mainnet sETHFI shares; L2 "not summed" (`_SETHFI_L2_E4`) | reads of 2026-10-14 | Can be summed: shares × Accountant rate reconciles, see (a) |
| `actual_buyback_tokens` | DEX fills into `0x2f5301a3…` and `0x3FB6784E…`; one tx excluded | log scan | 17,984,520 of CoW fills confirmed to the token. Three more txs need excluding, see (c) |
| `programmatic_buyback_tokens`, `cashback_paid_tokens` | wired 2026-10-13 | Snapshot `0xed61adff…` | Confirmed; route changed 2026-10-07 (via the deployer EOA) |
| `INSIDER_SHARE_MONITOR` | 13 issuer wallets, 0 sETHFI | read 2026-10-26 | Still 0. The custodian recipients were never crossed, see (e) |
| Buyback Safe threshold | config text says "1-of-5" in one place | — | Reads 2-of-5 today |

Fixture: `fixtures/real_etherfi.json`, as of 2026-10-13. It is not Jake's live store.

## (a) Supply

| Fact | Value | Source |
|---|---|---|
| Total supply | 998,535,999 ETHFI | `ETHFI.totalSupply()`, block 26,449,731 |
| Max supply | 1,000,000,000, minted once | allocations page: "fully minted with a fixed supply of 1B. There will be no further issuance" |
| Mint function | none: selector `mint(address,uint256)` is absent from the runtime code (7,890 bytes); `burn` and `burnFrom` are present | `eth_getCode` |
| Burned | 1,464,001 (7 calls from `0x2f5301a3…`, 2025-08-08..09-19) | supply difference; Blockscout transfers to 0x0 |
| Token launch | 2024-03-18 | Season 1 page (claims live 2024-03-18) |

### Allocation: three versions

| Bucket | Docs page, updated 2025-08-02 | Filing, "at genesis" | Config (DropsTab) | On chain |
|---|---|---|---|---|
| Investors | 33.74% | 33.7% | 32.5% | 325,000,010 sent to custodian origin `0x1b577834…` (2024-07-24..29) |
| Core contributors | 21.47% | 21.4% | 23.26% | 232,600,010 sent to custodian origin `0x66FCfC15…` (2024-07-24..27) |
| Treasury | 21.62% | 31.5% | 27.24% | 166.77M in the four docs wallets today (16.70%) |
| User airdrops | 19.27% | 7.5% (community) | 11% | all 11 mainnet claim contracts hold 0 |
| Partnerships & liquidity (incl. 1% Protocol Guild) | 3.9% | 5.9% | 3% + 2% Launchpool + 1% Guild | Protocol Guild contract `0x25941dC7…` holds 3,923,215 |

- Airdrops by season (Season 4 page): S1 7.5%, S2 5.8%, S3 2.7%, S4 1.3% = 17.3%. The remaining 1.97% to reach 19.27% is not itemised on the docs. Snapshot records Season 5 (#10, 2025-02-01) and Member Rewards (7.5M ETHFI, June–August 2025).
- The on-chain custody split (23.26% / 32.5%) matches the launch figures, not today's docs (21.47% / 33.74%). The 1.2–1.8 point reclassification is **not explained in any source read**.

### Vesting

| Bucket | Schedule as published | Where held | On-chain evidence |
|---|---|---|---|
| Investors | Docs: 2-year schedule, 1-year cliff. Filing: locked 12 months, "unlock over months 13–36", ends 2027-02-18 | "numerous unique qualified custodian addresses" (filing Q9); custodian not named | 108 plain wallets funded from `0x1b577834…` on 2024-12-19, 2025-02-02, 2025-03-17, 2025-04-21. One of them (`0xe3C5eBeE…`, 36.3M) paid out monthly from 2025-06-04, last on **2026-03-18**. That fits the docs' 24 months, not the filing's 36 |
| Core contributors | Docs: 3-year schedule, 1-year cliff. Filing: as above | same | 32 plain wallets funded from `0x66FCfC15…` on the same four dates |
| Post-TGE employees | 12-month cliff, 36-month vest from each grant date; about 6M unvested (filing, 2025-08) | not disclosed | not located |
| Treasury | none | Safes, see (e) | — |
| Airdrops | S2: four tranches 30 days apart; S3: three tranches (day 0, 30, 90) | claim contracts | all empty |
| Market-maker loans | about 11M lent to Amber, Pulsar, Selini for 1–1.5 years from 2025-08-06 (filing Q16) | not disclosed | not located |

There is **no vesting contract**. The lock is held by a custodian, off chain.

Unvested today, by each reading of the team schedule:

| Reading | Unvested share | On 214.7M (docs %) | On 232.6M (sent on chain) |
|---|---|---|---|
| Docs: 36 months including the cliff, a third at the cliff, monthly to 2027-03-18 | 4/36 | 23.86M | 25.84M |
| Filing: 24 equal monthly steps, months 13–36, last 2027-02-18 | 3/24 | 26.84M | 29.08M |

- INFERRED: the docs reading fits the chain better. Wallet `0x3CE6B172…` had moved 34.5% by 2025-05-17. The filing reading allows 8.3% by then (two of 24 steps); the docs reading allows 36.1% (13 of 36 months).
- Investors are fully vested since 2026-03-18 on the docs reading and on the chain evidence.
- The repo's "Team 232.6M, 151.46M unlocked, 318K a day into mid-2027" (which gives about 63M unvested today) comes from DropsTab. No ether.fi source read today supports that schedule.

### Who holds what (block 26,449,731)

| Group | ETHFI | % of total | sETHFI |
|---|---|---|---|
| Issuer side, 17 wallets (table in (e)) | 182,710,017 | 18.30% | 0 |
| Team custodian wallets, 32 (INFERRED) | 150,700,905 | 15.09% | 0 |
| Wallets funded only by team custodian wallets, 7 | 7,758,680 | 0.78% | 11,876,403 |
| Investor custodian wallets, 108 (INFERRED) | 20,047,466 | 2.01% | 0 |
| Wallets funded only by investor custodian wallets, 13 | 156,735,241 | 15.70% | 22,445,073 |
| sETHFI vault, Ethereum `0x86B5780b…` | 109,018,488 | 10.92% | — |
| Bridge lockbox `0xe0080d2F…` (backs all L2 ETHFI) | 35,289,335 | 3.53% | — |
| Symbiotic collateral `0x21DbBA98…` (the vault's strategy position) | 4,633,561 | 0.46% | — |
| Exchange-tagged wallets, 47 (Blockscout tags) | 154,678,608 | 15.49% | — |
| Market-maker-tagged (FalconX `0x1157A207…`, Wintermute) | 3,588,461 | 0.36% | — |
| Protocol Guild `0x25941dC7…` | 3,923,215 | 0.39% | — |
| Everyone else | about 169.5M | 17.0% | — |

Blockscout's top 400 holders cover 978.8M of the 998.5M.

### Other chains (lockbox, not a second mint)

| Chain | ETHFI token | ETHFI supply | sETHFI shares | ETHFI held by sETHFI |
|---|---|---|---|---|
| Ethereum | `0xFe0c3006…` | 998,535,999 | 86,035,153 | 109,018,488 |
| Arbitrum (block 525,639,782) | `0x7189fb5B…` | 7,593,962 | 2,271,032 | 2,644,147 |
| Base (59,538,491) | `0x6C240DDA…` | 814,152 | 315,167 | 350,003 |
| Optimism (159,749,648) | `0xe0080d2F…` | 26,709,205 | 25,752,893 | 21,400,910 |
| Scroll (35,784,590) | `0x056A5FA5…` | 171,274 | 5,756 | 0 |
| L2 total | | 35,288,594 | 28,344,848 | 24,395,060 |

- L2 supply 35,288,594 vs lockbox 35,289,335: 741 ETHFI apart (messages in flight). Bridged ETHFI is inside Ethereum's total and must never be added.
- **Staked, all chains** = 114,380,001 shares × Accountant rate 1.20677615 (`0x05A1552c…getRate()`) = **138,031,058 ETHFI**. ETHFI found: 133,413,548 in the five vaults + 4,633,561 at Symbiotic = 138,047,108. Difference 16,051 (0.01%).
- Unstaking: DelayedWithdraw `0x1509b1fd…` reads a 3-day delay and a 7-day completion window; docs say "up to 10 days".

## (b) Circulating under Jake's rule

U = not yet vested or released. M = somebody can move it. E = earns.

| Leg | Balance | U | M | E | Rule |
|---|---|---|---|---|---|
| Burned | 1,464,001 | — | — | — | out of circulating and of total (already) |
| Treasury Safes `0x7A6A41F3…`, `0x7D4bBE47…`, `0x5f0E7A42…`, `0xD022d6bb…`, `0x5EC5e6b4…` | 166,768,386 | undistributed | yes: Safes 4-of-6, 3-of-7, 3-of-7, 1-of-2, 3-of-7 | no (0 sETHFI, plain balance) | IN, off float |
| Foundation Safe `0xe4439b1d…` (the 20M cashback buffer) | 11,499,990 | no | yes: 3-of-7 | no | IN, off float |
| Operating Safe `0x3FB6784E…` | 4,347,510 | no | yes: 2-of-5 | no | IN, off float |
| Other issuer wallets (`0x01e42ad3…`, `0x43F4cA92…`; eight more hold 0) | 94,131 | no | yes | no | IN, off float |
| sETHFI, all chains | 138,031,058 | no | holders, after the 3-day delay | yes | IN, off float (staked) |
| Bridge lockbox | 35,289,335 | no | yes: it backs L2 tokens held by users | — | IN; no float deduction (the L2 stake is already in the line above) |
| Team custodian wallets, vested part | about 122–127M | no | yes | no | IN; float is decision 3 |
| **Team custodian wallets, unvested part** | **23.9–29.1M by schedule (not readable on chain)** | **yes** | **yes on chain: plain wallets with a key; transfers out continue (2.3M from `0x6630fF17…` on 2026-08-21). The lock is the custodian's undertaking** | **no: 0 sETHFI on Ethereum, Arbitrum, Base, Optimism** | **decision 1** |
| Investor wallets | 176.8M | no (vested 2026-03-18) | yes | no at these wallets | IN; float is decision 3 |
| Post-TGE grants, about 6M | not located | yes | not known | not known | not established |

### The team custodian wallets, one by one

All were funded by `0x66FCfC15…`. "sETHFI" is the sum on Ethereum, Arbitrum, Base and Optimism.

| Wallet (INFERRED core contributor) | Received | Held now | Held % | sETHFI | Earning |
|---|---|---|---|---|---|
| `0x95Bf94e06D5BE1000C23cd99968eff414Aa4995e` | 101,547,168 | 101,547,168 | 100% | 0 | no |
| `0x6630fF17CF5357c5f9C3d2A21AC98D2fF1070739` | 29,013,477 | 26,713,477 | 92% | 0 | no |
| `0x1a8d277E6D2eCa5AE1Ce5B2054a4F51ec6aEbe90` | 12,287,975 | 9,233,468 | 75% | 0 | no |
| `0xdBA8c05555b3A23A15251d5ca3E60e4840012b09` | 7,501,709 | 4,375,997 | 58% | 0 | no |
| `0xc00a8059F7C656B827462D4450D407dd15BFda0d` | 6,620,449 | 3,706,817 | 56% | 0 | no |
| `0xd65F151aC6A7A85e85543c0cC0563b2b922b8c1d` | 1,932,167 | 1,811,407 | 94% | 0 | no |
| `0x792A79290F4911204a357450b19f18AC4527d12D` | 12,078,140 | 1,404,851 | 12% | 0 | no |
| `0x0338D8909De5B2666159f220477B498317542393` | 6,620,449 | 982,669 | 15% | 0 | no |
| `0x417c1EdD3AFCF1f3994C864D1070AF1Ab91B2AE6` | 406,504 | 406,504 | 100% | 0 | no |
| `0x4DF1BE9e97dc187eC609020663A5216fcAB1D195` | 1,175,081 | 244,809 | 21% | 0 | no |
| `0x4497c8e5…`, `0xE6E90CD1…`, `0xcfbD0271…`, `0x1ba116Ce…` | 2,396,030 | 273,726 | 11% | 0 | no |
| 18 more wallets (e.g. `0x3CE6B172…` 14.5M, `0x66a7Cdd3…` 7.5M) | 48,299,745 | 11 | 0% | 0 | — |
| **32 wallets** | **229,878,894** | **150,700,905** | **65.6%** | **0** | |

- A plain ETHFI balance earns nothing: the token does not rebase and pays no holder distribution. So no unvested team token is staked or earning **at a custodian wallet**.
- The filing agrees: "locked tokens are not eligible for ETHFI staking" (Q11).
- What these wallets hold (150.7M) is far more than the unvested amount (24–29M). Most of it is vested and simply not moved.
- Once released, team tokens are staked: 11.88M sETHFI sits in four wallets funded only by team custodian wallets. Those are released, so they are circulating and off the float as stake.

### Result

| | Literal (custodian wallets count as movable) | Schedule exclusion (unvested team modelled out) |
|---|---|---|
| Total supply | 998,535,999 | 998,535,999 |
| − unvested team (4/36 × 214.7M) | 0 | 23,855,556 |
| **Circulating** | **998,535,999** | **974,680,443** |
| − staked, all chains | 138,031,058 | 138,031,058 |
| − issuer, foundation, treasury | 182,710,017 | 182,710,017 |
| − locked (unvested team, modelled) | 23,855,556 | 0 |
| **Free float** | **653,939,368** | **653,939,368** |

- Free float is 65.5% of total either way. With the filing reading (26.84M) it is 650,957,424.
- Not deducted, and why: the ETHFI in the liquidETH vault (5,100,467), the Uniswap v4 PoolManager (740,474) and Protocol Guild (3,923,215) are third-party or user positions. The 11M of market-maker loans cannot be located.
- The dump's figures for comparison (2026-10-13): circulating 810,696,040, tradeable float 722,641,284 (it deducts 88.05M mainnet shares only).

### Reconciliation

| Source | Circulating | vs literal 998,535,999 | vs schedule 974,680,443 |
|---|---|---|---|
| CoinGecko API, 14:33 UTC (total 998,535,999; max 1,000,000,000) | 998,535,999 | 0.0% | −2.4%: the unvested team tokens in the custodian wallets above |
| CoinMarketCap data API (total 1,000,000,000; source `app.ether.fi/api/ethfi/coinmarketcap`, last update 2026-09-16) | 965,350,000 | +3.44% (33,185,999) | +0.97% |
| ether.fi's own figure | none published other than the CMC endpoint | — | — |

- **The CoinMarketCap gap cannot be named by wallet.** The figure is static and self-reported, CMC lists no excluded wallets for ETHFI, and the endpoint is behind a bot check from here. CMC also still uses 1,000,000,000 as total, so 1,464,001 of the gap is the burn. Its implied 34.65M of non-circulating tokens is close to "unvested team + post-TGE grants" (30–35M) — an inference, not established.

## (c) Buyback

### As documented

| Date | Source | Says |
|---|---|---|
| 2024-06-24 | Snapshot `0x287dffb9…` | buyback and liquidity-pool seeding |
| 2024-12-20 | Snapshot #8 `0x1f268f3b…` | 5% of protocol revenue buys ETHFI each month, distributed to ETHFI stakers |
| 2025-04-29 | Snapshot #11 `0x6a9bdf1b…` | withdrawal revenue (0.3% fast-exit fee + yield forfeited on normal exits) to a "Buyback Treasury" wallet; bought ETHFI "to be allocated to ETHFI stakers or Buyback and LP program"; a registry of ETHFI "purchased and burned or held" |
| 2025-11-03 | Snapshot `0xe5b64c77…` | treasury may spend up to $50M on buybacks while ETHFI is below $3 |
| 2026-09-03 | Snapshot `0xed61adff…` (1,141,999 for, 0 against) | Labs sends weekly USDC to the Foundation; TWAP buys on CoW. Example fee split: 40% Labs, 30% "buys ETHFI, held in the Foundation treasury", 30% "buys ETHFI, delivered to the user as cashback". Up to 20M Foundation ETHFI as a buffer |
| current | docs buyback page | weekly (100% of eETH withdrawal fees) and monthly buybacks, "remitted directly to sETHFI holders"; Foundation wallet `0x2f5301a3…` |

### As executed (ETHFI, calendar quarters; 2026 Q4 is to 11-19)

| | 2025 Q4 | 2026 Q1 | 2026 Q2 | 2026 Q3 | 2026 Q4 |
|---|---|---|---|---|---|
| Bought on CoW into `0x2f5301a3…` | 7,166,148 | 5,767,208 | 62,354 (last 2026-04-01) | 0 | 0 |
| Bought on CoW into `0x43F4cA92…` (programme) | — | — | — | 841,413 | 2,035,229 |
| Sent to the sETHFI vault by ether.fi wallets (the repo's scan classes these as rewards: no shares minted; the senders hold 0 sETHFI) | 1,850,323 | 2,134,844 | 2,624,605 | 1,137,473 | 150,000 |
| — sent by | `0x2f53…` | `0x2f53…` | `0x2f53…` | `0x3FB6…` | `0x3FB6…` 100,000; `0x01e4…` 50,000 |
| Moved from `0x2f53…` to ether.fi Safe `0x01e42ad3…` (held) | 4,600,100 | 3,010,000 | 0 | 0 | 0 |
| Burned | 0 | 0 | 0 | 0 | 0 |
| Operating Safe to FalconX-tagged `0x1157A207…` (via `0x80381dc3…`) | 0 | 0 | 0 | 6,100,010 | 1,000,010 |
| Operating Safe to Binance hot wallet (via `0x188C7b3C…`) | 0 | 0 | 0 | 2,000,000 | 200,000 (from the deployer EOA) |

Life of the old wallet `0x2f5301a3…` (2025-05-09 to today, all 1,201 transfers):

- In: 18,982,712 = 17,984,520 from CoW settlements + 998,192 from other wallets (Safe `0x01e42ad3…` 600,000; the deployer EOA 396,511; one unlabelled sender 1,681).
- Out: 9,389,953 to the sETHFI vault (49%), 7,610,100 to Safe `0x01e42ad3…` (40%), 1,464,000 burned (8%), 518,659 to the Operating Safe and the deployer EOA.
- The 7.61M moved to `0x01e42ad3…` went on to: the Operating Safe 3,000,000; Foundation Safe `0xe4439b1d…` 2,000,000; deployer EOA 776,117; Treasury `0x5f0E7A42…` 600,000; back to `0x2f53…` 600,000; an unlabelled wallet `0x4d24a544…` 538,691 (it holds 0 now); voter rewards 272,802; Treasury `0xD022d6bb…` 100,000; the sETHFI vault 50,000.
- So about half of what the old programme bought reached stakers. The docs say all of it does.

Funding:

- Old wallet: ETH wrapped in the wallet and spent on CoW (1,882.11 WETH, 2025 Q2–Q4), then stablecoins under the 2025-11 treasury vote (per config; the stablecoin legs were not re-read today). The repo's `HOLDER_VALUE` records all periods as treasury-funded; nothing read today changes that.
- Withdrawal fees since April 2026: eETH still arrives at `0x2f5301a3…` from the withdrawal contract (306.09 eETH in 2026 Q2, 12.19 in Q3) and leaves to the deployer EOA `0x9eaC7114…` (307.95 and 58.14). It is not swapped for ETHFI in this wallet. Where it goes next is not established.
- Stakers' rewards since 2026-07: paid from the Operating Safe, which is fed by treasury wallets, the team custodian origin (2,721,116 on 2026-07-28), a Binance withdrawal (5,000,000) and the Foundation Safe (7,000,000). No purchase.
- New programme: Safe `0xf40bcc08…` (2-of-5) sent 2,505,229 USDC in 11 transfers of 50,000–305,219 (2026-09-09..11-16). 1,864,784 USDC bought 2,876,642 ETHFI on CoW; 620,200 USDC went to `0xa7b1d3a0…` (not identified).

Where the programme's ETHFI goes:

- Mainnet: `0x43F4cA92…` holds 3,835. It sent 994,450 to the LayerZero bridge helper directly and 2,054,374 through the deployer EOA (the route since 2026-10-07).
- Optimism: the CashbackDistributor `0x38F2fBb2…` received 5,173,017 since September (4,209,671 from `0x43F4cA92…` on OP, 928,146 minted to it by the bridge) and holds 158,156. About 5.0M has been paid out as cashback against 2.88M bought.
- **Held by the Foundation from the programme: 0.** The "30% held" leg of the proposal's example is not visible on chain.

### The repo's read against this

- `actual_buyback_tokens` on the dump shows 493,032 ETHFI ($277,513) in Q0. That is exactly 5,174.71 + 242,697.10 + 245,159.94 from the Uniswap v4 PoolManager on 2026-09-02 (txs `0xb1acc8c3…`, `0x61a48f70…`, `0x79f94c16…`). In each, only ETHFI moves, between the Safe, the PoolManager and Arrakis vault `0xE8FD76BF…`; no other token is paid. They are liquidity withdrawals, like the fourth tx (`0x3b52e19b…`, 554,726) that config already excludes.
- The correct Q0 figure for this series is 0. "Buying pressure on float" (0.28% a year) and the buyback trend cells rest on the wrong rows.
- `BUYBACK_DESTINATION_SHARES` says 100% distributed. Measured on the old wallet: 49% distributed, 40% moved to an ether.fi Safe, 8% burned.
- `programmatic_buyback_tokens` measures the right thing (CoW fills into `0x43F4cA92…`), and "paid on as cashback, not credited to holders" is confirmed. The dump has no rows for it.
- DefiLlama's holders revenue is $0 for the last 90 days, by its own stated method: it excludes the cashback purchases.

## (d) Fees, revenue, holder revenue (S13)

DefiLlama, 90 days 2026-08-21..2026-11-18 (`api.llama.fi/summary/fees/<slug>`):

| Listing | Fees | Revenue | Supply side | Holders revenue |
|---|---|---|---|---|
| ether.fi Stake | $30,136,235 | $5,526,719 | $24,609,507 | $0 |
| ether.fi Liquid | $251,453 | $247,134 | $4,321 | $0 |
| EtherFi Borrowing Market | $107,915 | $63,166 | $44,750 | $0 |
| EtherFi Cash Liquid | $10,946,698 | $5,531,804 | $5,414,890 | $0 |
| **ether.fi (parent)** | **$41,442,301** | **$11,368,823** | **$30,073,468** | **$0** ($6,650,020 over 365 days) |

What each includes:

- Stake fees are all staking and restaking rewards. Revenue is the protocol's cut (about 10% of staking rewards, 11% of EigenLayer rewards, 2.5% on stETH) plus eETH withdrawal fees.
- Cash revenue is an **estimate**: 1.38% of card spend, before card-network and issuer costs, which are private. Cash fees add the cashback paid on top of that estimate; cashback is a reward, not a fee a user pays.
- Holders revenue counts only trades by the old wallet, plus hand-entered buybacks to April 2025.

| Line | Candidate | 90-day figure | Includes / misses |
|---|---|---|---|
| FEES | F1: DefiLlama parent fees | $41,442,301 | counts $5.41M of cashback as fees |
| FEES | **F2: F1 less Cash supply side** | **$36,027,411** | gross rewards to stakers and vault depositors + protocol cuts + card fee estimate |
| FEES | F3: only what users are charged (= revenue) | $11,368,823 | treats staking rewards as not being fees; breaks comparison with the lending projects |
| REVENUE | **R1: DefiLlama parent revenue** | **$11,368,823** | half of it is the Cash estimate |
| REVENUE | R2: USDC Labs sends to the Foundation, on chain | $2,505,229 (71 days) | a share of revenue chosen by Labs, not revenue |
| REVENUE | R3: ether.fi's Dune dashboard `ether_fi/ethfi-dao` (first-party; cited in the filing) | not read (needs a Dune key) | — |
| HOLDER REVENUE | H1: DefiLlama | $0 | sees only the old wallet |
| HOLDER REVENUE | H2: ETHFI paid into sETHFI × the day's price, on chain | 412,000 ETHFI = $260,723 | reaches stakers, but funded from Foundation stock, not from revenue |
| HOLDER REVENUE | H3: programme purchases | $1,853,784 spent | funded by revenue, but paid to card users |
| HOLDER REVENUE | H4: Accountant rate growth × stake | not computed | mixes in strategy yield |

Recommendation, in the shape of `REVENUE_SPLIT`:

- **FEES = F2**, per day from DefiLlama (parent fees − Cash supply-side). Cross-check: F1.
- **REVENUE = R1**, labelled "Cash leg estimated at 1.38% of spend". Cross-check: R2 beside it.
- **HOLDER REVENUE = revenue-funded value reaching holders, measured on chain: $0 for this window.** Show H2 ($260,723) beside it as "treasury-funded distribution, recorded, not credited" — the same split `HOLDER_VALUE["Ether.fi"]` already makes. If the Foundation starts holding programme purchases, count them as held (the Near pattern).
- **Other recipients:** eETH holders and vault depositors (supply side); card users (cashback, outflow (c)).
- **Holder share** = 0 ÷ $11.37M = 0%.

## (e) Insider wallets

Issuer side (all balances at block 26,449,731; all hold 0 sETHFI on Ethereum, Arbitrum, Base, Optimism):

| Wallet | Role | Tag | Type | ETHFI |
|---|---|---|---|---|
| `0x7A6A41F353B3002751d94118aA7f4935dA39bB53` | Treasury | PUBLISHED (allocations page; filing Q9, Q18) | Safe 4-of-6 | 153,200,599.83 |
| `0x7D4bBE471369a066186c18bAF33622796A08d5Cd` | Treasury | PUBLISHED (same) | Safe 3-of-7 | 12,883,024.42 |
| `0x5f0E7A424d306e9E310be4f5Bb347216e473Ae55` | Treasury | PUBLISHED (allocations page) | Safe 3-of-7 | 684,761.46 |
| `0xD022d6bb8B6C1C357ec77D930Dc6A0aD40FFC90b` | Treasury | PUBLISHED (allocations page) | Safe 1-of-2 | 0 |
| `0x5EC5e6b4eb6827914CA8BC3AE02C39417242ADDE` | Treasury | PUBLISHED (filing) | Safe 3-of-7 | 0 |
| `0x3FB6784E263643656f386A0371644931133D7b78` | Operating | PUBLISHED (filing) | Safe 2-of-5 | 4,347,510.39 |
| `0xe4439b1d150Ab2Febd72D699954c7b4ddE2b66e2` | Foundation assets | PUBLISHED (filing Q18) | Safe 3-of-7 | 11,499,990.00 |
| `0xe67a6CCc7Ded59ef055194eD6402465E738339a8` | Foundation assets | PUBLISHED (filing Q18) | EOA | 0 |
| `0xbe2cfe1a304B6497E6f64525D0017AbaB7a5E8Cb` | Foundation assets | PUBLISHED (filing Q18) | EOA | 0 (85,118 on Arbitrum) |
| `0x2f5301a3D59388c509C65f8698f521377D41Fd0F` | Foundation buyback wallet | PUBLISHED (buyback page) | Safe 2-of-5 | 0 |
| `0x0c83EAe1FE72c390A02E426572854931EefF93BA` | protocol Treasury contract | PUBLISHED (deployed-contracts page) | contract | 0 |
| `0x66FCfC15a40F22Fad40fd6b6B9741EEf4De85721` | custodian origin, core contributors | PUBLISHED (filing Q9); "core contributors" INFERRED from the 232.6M it received | Safe 4-of-6 | 0 |
| `0x1b577834c6C64B38Bcf1eB2319fBAb6f6E3915c1` | custodian origin, investors | PUBLISHED (filing Q9); "investors" INFERRED from the 325.0M | Safe 4-of-6 | 0 |
| `0x43F4cA92F7F378bCdcF1508b866b5B6c47D6408C` | programme buyer | INFERRED: weekly USDC in, CoW buys out; both owners sit on the other Safes | Safe 1-of-2 | 3,834.67 |
| `0x01e42ad3acd58584ffc1d1982ecbbe758996d601` | ether.fi Safe | INFERRED: owners overlap the buyback Safe; received 7.61M from it | Safe 2-of-6 | 90,295.97 |
| `0x9eaC7114D1a1EaBc4732A886795cFD9E6E35843f` | deployer / operations key | INFERRED: an owner of every Safe above except `0xf40bcc08…` | EOA | 0 |
| `0xf40bcc0845528873784F36e5C105E62a93ff7021` | Labs USDC sender | INFERRED: sole source of the programme's weekly USDC; no owner shared with the others | Safe 2-of-5 | 0 |

- Team and investor custodian wallets (INFERRED: each was funded directly by a filing-named origin, and the filing says the origins were "distributed out to individual addresses per investor / core contributor"): 32 team wallets in (b); 108 investor wallets, 29 with a balance, 20,047,466 in total (largest `0xA73D268D…` 5,350,641).
- Their largest onward wallets, funded by nothing else (INFERRED investor custody): `0xf10873355B209B401262CAdc753d654E2b7e41aC` 54,533,029; `0xF619ab1F33cDc8a0123E093DE92Ea4D7399De39f` 35,877,782; `0x7A93190d436436BbB79fa2c0d24ca93AfB59B3fb` 27,633,150; `0x1331669cdfD56a5a865FdB38d825451FdE8F13Bf` 11,899,980; `0xb82DBD58d8F6705D13a493b1d81BE1e94f25b79e` 8,666,547; `0x0AcA738068D512D7Be1b0ddE7E7587D43052468A` 8,130,091.
- Of what left the custodian wallets and the wallets they fed: 145.6M went to exchange- or market-maker-tagged addresses (98.1M investors, 47.5M team) and 64.3M straight into the sETHFI vault (50.3M investors, 14.0M team).
- Market makers: Amber, Pulsar and Selini are disclosed by name (filing Q16, about 11M lent); no address is published. FalconX appears only as a Blockscout tag on `0x1157A207…`.

Insider stakers (INFERRED; "clean" = every ETHFI the wallet ever received came from investor / team custodian wallets):

| Wallet | Group | sETHFI | Basis |
|---|---|---|---|
| `0x8B0E5095caB7C83674B330C8e06E856835EC1209` | investor | 8,523,471 | clean |
| `0x9C2F1182267aB2e9Ba8A20FB6e876A02E87538Ec` | investor | 5,440,587 | clean |
| `0x476df75820d7F66436E47d395c711Eba6af388df` | team | 4,099,343 | clean (fed by `0x792A7929…` and `0x1a8d277E…`) |
| `0xE61894033Eec8CE33862F8c6B8C1aeC56FD386e8` | investor | 3,953,431 | clean |
| `0x73ED1B6713299EF9F0D91923aA0E4c2E06AD1511` | team | 3,284,213 | clean |
| `0x6eba48529ADfdf55506Ea19AE2FC4453C22E6589` | team | 3,265,026 | clean |
| `0x3b937cF62a829b7Aa4C9713F71032918aCaFaC5b` | investor | 2,713,926 | clean |
| `0x31DAF145D209e48e58df7F42BBA2C8495f30a732` | investor | 1,813,650 | clean |
| `0xdc7074b79bE30eBC7A96f63177B95Dd538b443aB` | team | 1,227,822 | clean |
| `0x777DBc5C7Edfa5E474709b5F7951A501824c04b2` | investor | 9,219,207 | mixed: 12.97M of its 19.02M came from the tree |

- Clean total: **34,321,476 sETHFI** = 39.9% of mainnet shares, 30.0% of shares on all chains. With the mixed wallets: 43.77M (50.9% of mainnet).
- This is a floor. The trace stops at three hops, at transfers under 500,000 and at exchanges, and it does not cover L2 holders.

## (f) done_check

Built from the repo's 15 dumps (as of 2026-10-13); `done_check.py --verbose` gives **Ether.fi: NOT READY, 9 lines**. This is the fixture, not Jake's live store: some lines may already be clear there.

| # | Line | Cause | Proposed fix |
|---|---|---|---|
| 1 | audit CP Comparability, BUSINESS 3: MISSING [D — ROUTE NOT FILLED] `eth_staked_etherfi_tokens` | Wired 2026-10-13 as `eETH.totalSupply()`; the dump has no rows ("Data: no rows stored") | A live run plus `archive_backfill.py` for its history, then re-dump the fixture |
| 2 | Comparison, Subsidy cover (x): n/a, rule-3 suspect (+8 cells: three CP Comparability columns, five Trajectory quarters) | "no supply-side incentive (b) series on file". Config classes every ETHFI user programme as demand side (c) | Decision 8, then a `DECLARED_NA_REASONS["Ether.fi"]` entry with source and read date |
| 3 | Comparison, Leakage (ii): n/a, rule-3 suspect | same | same entry |
| 4 | Comparison, Staking yield minus T-bill: n/a, no allowed reason | `tbill_3m_yield (now): no rows stored`. A Macro series, absent from the test store | Not an Ether.fi fault. Carry the Macro rows into the fixture build; check the live store has them |
| 5 | Comparison, Insider share of stake: n/a, "MISSING E — needs issuer wallet list" | Only the 13 issuer wallets are crossed with sETHFI holders, and they hold 0 | Wire the nine clean wallets of (e), tagged INFERRED (decision 6); the cell then reads about 39.9% |
| 6 | Comparison, Fees (S13), 90 days: "not yet split" | no `REVENUE_SPLIT["Ether.fi"]` | add it per (d): F2 |
| 7 | Comparison, Revenue (S13), 90 days: "not yet split" | same | R1 |
| 8 | Comparison, Holder revenue (S13), 90 days: "not yet split" | same | on-chain, revenue-funded; H2 beside it |
| 9 | Comparison, Holder share (S13): "not yet split" | same | follows from 7 and 8 |

Not on the list today, but will be on a later as-of date: "Total dilution", "Net holder yield" (Summary and Comparison) read `blocked` because `cashback_unfunded_tokens` has no rows in the dump. They pass only through a `REGRESSION_ALLOWLIST` entry that ran to 2026-10-27.

## Round 14 proposal

1. **Put Ether.fi on the rule.** Add `RULE_IN["Ether.fi"]`: circulating = `totalSupply()`; the issuer wallets of (e) off the float; stake = shares on five chains × the Accountant rate; the unvested team leg per decision 1. Replace the DropsTab allocation table with the docs and filing versions. *Unblocks:* circulating, market cap, lock rate, float and every ratio over them; the CoinGecko / CoinMarketCap row.
2. **Repair the buyback read.** Exclude the three other 2026-09-02 transactions; better, require that a PoolManager inflow pays another token out before it counts as a buy. Replace "100% distributed" with the measured destinations. Add the Operating Safe's transfers to exchange-tagged addresses as a named series. *Unblocks:* A3 buyback cells, buying pressure, the trend arrows.
3. **Add `REVENUE_SPLIT["Ether.fi"]`** as in (d), with the two cross-checks. *Unblocks:* done_check lines 6–9.
4. **Insider list and insider stake.** Store the custodian recipients and the clean staker wallets as INFERRED, with the trace as evidence. *Unblocks:* line 5, Leakage (i), insider share of circulating.
5. **Clear the rest and freeze.** eETH history backfill (line 1), the declared n/a for (b) (lines 2–3), Macro rows in the fixture (line 4), the cashback series on a live run. Re-dump the fixture, run done_check, then add Ether.fi to `FROZEN_PROJECTS`.

## Decisions for Jake

1. **Unvested team tokens in custodian wallets.** (a) Literal, as for Fluid's IGP-137 wallet: a key can move them, so they are in circulating and off the float as locked; circulating 998,535,999. (b) Model them out, as for Morpho's relock: circulating about 974.7M. **Recommend (a).** There is no on-chain lock, the wallets do send tokens out, and CoinGecko matches to the token. Float is the same either way.
2. **Which schedule sizes that locked leg.** (a) Docs: 4/36 of 214.7M = 23.86M, ending 2027-03-18. (b) Filing: 3/24 = 26.84M, ending 2027-02-18. **Recommend (a)**; it fits the chain. Label it MODELLED.
3. **Vested tokens still in investor / team wallets (about 310M).** (a) In the float, with "insider share of circulating" shown beside it. (b) Off the float as insider-held. **Recommend (a).** The rule takes issuer, foundation and treasury holdings off the float, not individuals' vested tokens.
4. **Stake figure.** (a) All chains × Accountant rate, 138.0M. (b) Mainnet shares, 86.0M. **Recommend (a).**
5. **Holder revenue.** (a) Strict: only revenue-funded value reaching holders, $0 this window, with the treasury-funded top-ups ($260,723) shown beside it. (b) Count the top-ups. (c) Count the programme purchases ($1.85M). **Recommend (a).**
6. **Accept INFERRED insider stakers.** (a) Yes, the nine clean wallets only (34.3M sETHFI). (b) Keep the cell as MISSING E until ether.fi publishes a list. **Recommend (a).**
7. **Fees.** (a) DefiLlama as is, $41.44M. (b) Less Cash cashback, $36.03M. **Recommend (b).**
8. **Supply-side incentives (b).** (a) Declare none: ETHFI rewards go to users (cashback, seasons), not to suppliers. (b) Class the 2025 season and Member Rewards drops as (b) and backfill them. **Recommend (a)**, matching `OUTFLOW_CATEGORIES`.
9. **Foundation transfers to FalconX / Binance (9.3M since July).** (a) Record as a named float outflow, purpose "not established". (b) Leave inside the general unlock leg. **Recommend (a).**

## Not established

- The source of the "roughly 65M unvested team" figure, beyond DropsTab's model in config.
- The custodian's name, its lock terms, and whether it enforces them. Only the wallets' behaviour was read.
- Which team schedule is right; the two published versions disagree with each other, and the filing's version disagrees with the chain for investors.
- Why the docs' split (21.47% / 33.74%) differs from the on-chain custody split (23.26% / 32.5%).
- Where the roughly 6M of post-TGE employee grants sit.
- What CoinMarketCap's 965,350,000 leaves out, wallet by wallet.
- The purpose of the 7.1M sent to the FalconX-tagged address and the 2.2M to Binance (sale, loan or collateral), and where the 11M of market-maker loans are.
- Where withdrawal-fee eETH goes after the deployer EOA; what the 620,200 USDC sent to `0xa7b1d3a0…` paid for.
- Buybacks before 2025-05-09 (off-chain, hand-entered by DefiLlama): not re-checked.
- Insider stakers on L2s and beyond three hops; 26.8M of custodian outflow went to small or untraced wallets. Four trace nodes had more than 300 transfers and were read in part.
- First-party revenue: the Dune dashboard was not read; the Cash card fee rate is DefiLlama's estimate.
- Vesting terms of Protocol Guild's 3.92M. The governance forum threads were not opened (the Snapshot texts were).
- Whether Jake's live store already clears done_check lines 1 and 4.

## Sources (all read 2026-11-19)

- Docs: https://etherfi.gitbook.io/gov/ethfi-allocations , /gov/ethfi-buyback-program , /gov/ethfi/ethfi-staking , /gov/seasons/airdrop-season-1 … -4 , https://etherfi.gitbook.io/etherfi/developers/contracts-and-integrations/deployed-contracts
- Filing: https://blockworks.com/token-transparency/filing/ether-fi (v1.0, filed 2025-08-19, "Partial — 4 gaps")
- Snapshot space `etherfi-dao.eth` via https://hub.snapshot.org/graphql : `0xed61adff…`, `0xe5b64c77…`, `0x6a9bdf1b…`, `0x1f268f3b…`, `0xdfba006b…`, `0x287dffb9…`
- Chain: ethereum-rpc.publicnode.com (block 26,449,731; Safe owners at 26,449,744), arbitrum-one-rpc, base-rpc, optimism-rpc and scroll-rpc.publicnode.com
- Blockscout: https://eth.blockscout.com/api/v2 (holders, token transfers, transactions), https://explorer.optimism.io/api/v2
- CoinGecko: https://api.coingecko.com/api/v3/coins/ether-fi . CoinMarketCap: https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail?slug=ether-fi-ethfi
- DefiLlama: https://api.llama.fi/summary/fees/ether.fi (and `ether.fi-stake`, `ether.fi-liquid`, `etherfi-borrowing-market`, `etherfi-cash-liquid`; dataType dailyFees, dailyRevenue, dailyHoldersRevenue, dailySupplySideRevenue); prices https://coins.llama.fi/chart
- Not reachable: https://app.ether.fi/api/ethfi/coinmarketcap (bot check)
- Repo: `config.py` (`PROJECT_BY_NAME["Ether.fi"]`, `CIRCULATING_ONCHAIN`, `CIRCULATING_RULE`, `_NONCIRC_WALLETS_FIRST_PARTY`, `HOLDER_VALUE`, `REVENUE_SPLIT`, `REGRESSION_ALLOWLIST`), `docs/r8_rule_audit.md`, `fixtures/real_etherfi.json`, `done_check.py`
