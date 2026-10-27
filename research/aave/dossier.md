# Aave (AAVE) — tracker research dossier

As-of: 27 October 2026. On-chain reads at Ethereum block ~26,283,400 (10:17–10:37 UTC) unless stated otherwise. Every row in `sources.csv` (same folder) was called from the research workspace. Raw downloads are in `raw/`.

Conventions: **ARFC** = forum Request for Final Comment; **Snapshot** = off-chain vote (space `aavedao.eth`); **AIP** = on-chain Governance V3 proposal (Ethereum `0x9AEE0B04504CeF83A65AC3f0e838D0593BCb2BC7`; `getProposalsCount()` = 538). AIP states come from the BGD governance cache (`bgd-labs/v3-governance-cache`) and, for AIPs 519–537 (titles missing from the cache), from the AIP IPFS documents.

---

## 0. Headline findings

1. **Buybacks are paused, and nothing has been specified or voted to restart them.** The pause began 19 April 2026. Snapshot ratified it, closing 1 May 2026 (For 617,169 / Against 4,893 / Abstain 158,478). Primary sources show no forum specification, Snapshot vote or AIP for "Aavenomics 3.0". On-chain, the buyback wallet's AAVE has not moved since 1 July 2026 (detail in §1.9).
2. **Bought AAVE is held, not burned.** It sits as 240,502.34 aAAVE in the Aave Finance Committee (AFC) Safe. It is pledged as collateral against 4,104,119 GHO of variable debt (health factor 5.81). That borrowing helped fund the DAO's 25,000 ETH rsETH donation.
   - TokenLogic's own planning documents describe bought AAVE as a pool for funding Safety Module emissions and service-provider pay. Under the tracker's rule it counts as **held = retired**, but its destination durability is **held-discretionary and encumbered**.
3. **Revenue to the DAO is real and on-chain-legible.** DefiLlama parent slug `aave` shows $4.95m of revenue over 30 days ($60.2m annualised) on $37.55m of fees ($456.8m annualised).
   - Parent `aave` equals the exact sum of `aave-v2` + `aave-v3` + `aave-v4`; GHO and Horizon are inside `aave-v3`. Use the parent alone to avoid double counting.
4. **AAVE outflows** (rates below are per year):
   - Security emissions: stkAAVE at 150 AAVE/day (54,750), read on-chain.
   - Ecosystem Reserve grant streams: 28,750. Aave Labs gets 75,000 over 4 years; LlamaRisk and TokenLogic get 5,000 each.
   - Umbrella rewards are paid in aTokens from the Collector, not in AAVE.
5. **Circulating supply.** CoinGecko's 15,436,717 equals exactly 16,000,000 minus the Ecosystem Reserve balance of 563,283. CoinGecko therefore treats AFC-held buyback AAVE and protocol-owned-liquidity AAVE as circulating.

---

## 1. Mechanics (primary sources)

### 1.1 Revenue routed to the DAO

**Reserve factor (V3).** The Aave V3 Pool docs (aave.com/docs/aave-v3/smart-contracts/pool, undated, read 27 Oct 2026) say `mintToTreasury`:

> "Mints the assets accrued through the reserve factor to the treasury in the form of aTokens for the given list of assets."

The Ethereum Core Collector (treasury) is `0x464C71f6c2F760DdA6093dCB91C24c39e5d6e18c`, listed in the address-book (`AaveV3Ethereum.COLLECTOR`) and confirmed on-chain (proxy code present; holds aAAVE).

Live reserve factors from `AaveProtocolDataProvider.getReserveConfigurationData` (`0x0a16f2FCC0D44FaE41cc54e079281D84A363bECD`), in basis points:

| Reserve | Reserve factor | Implied share to DAO |
|---|---|---|
| USDC | 1,000 | 10% |
| WETH | 1,500 | 15% |
| GHO | 10,000 | 100% |
| AAVE | 0 | 0% |

**Liquidation protocol fee.** Same docs, ReserveConfiguration bits 152–167 hold the "Liquidation protocol fee". `getLiquidationProtocolFee` returns:

| Reserve | Fee (bps) | Share of liquidation bonus |
|---|---|---|
| USDC | 2,000 | 20% |
| WETH | 2,000 | 20% |
| AAVE | 1,000 | 10% |

An ARFC raising the fee for WBTC/WETH/wstETH on Ethereum Core closed on Snapshot on 23 August 2026. It is AIP 534 ("Liquidation Protocol Fee Increase for WBTC, WETH, and wstETH on Aave V3 Ethereum Core"), executed in October 2026.

**Flash loans.** The flash-loan guide (aave.com/docs/aave-v3/guides/flash-loans, undated) says:

> "The flash loan fee is initialized at deployment to 0.05% … Flashloan fee can be shared by the LPs (liquidity providers) and the protocol treasury."

Live Ethereum Core values: `FLASHLOAN_PREMIUM_TOTAL` = 5 bps and `FLASHLOAN_PREMIUM_TO_PROTOCOL` = 10,000 (100% of the premium to the treasury).

**GHO.** GHO interest goes 100% to the treasury (reserve factor 10,000 above). The GHO docs (aave.com/docs/ecosystem/gho, undated) say:

> "Each GSM instance has a FeeStrategy contract that determines a percentage fee for buy and sell conversions that is allocated to the Aave DAO treasury."

**Aave-branded products ("Aave Will Win", AWW).** The ARFC was posted 27 Mar 2026 (https://governance.aave.com/t/arfc-aave-will-win-framework/24352). Snapshot closed 4 Apr 2026 (For 727,572 / Against 174,330). The funding AIP 469 was executed in April 2026.

> "Direct 100% of revenue from all Aave Labs' Aave-branded products to the Aave DAO treasury."

> "All product revenue and deductions will be reported quarterly and verified by an independent third-party such as an auditor."

The same document says, of V4: "As with V3, 100% of Aave V4's protocol revenue will also go to the DAO."

Limit: no product-revenue report and no on-chain receiving address for product revenue was found. This flow cannot be measured today (see §6).

**SVR (Chainlink Smart Value Recapture).** Only secondary evidence was read. DefiLlama's adapter describes 100% of recaptured liquidation MEV as going to the Collector. The primary ARFC (https://governance.aave.com/t/21247) is listed in the forum search but its text was not read for this dossier.

**rsETH rescue encumbrance on revenue.** The ARFC rsETH Incident Funding Update (24 Apr 2026; https://governance.aave.com/t/arfc-rseth-incident-funding-update/24740) passed Snapshot on 1 May 2026:

> "The revenue share directed to repayment will not exceed a pre-defined percentage of total Aave protocol revenue, as determined by Aave service providers, applied on full drawdown and directed onchain to dedicated repayment contracts."

The percentage, the facilities and the repayment contracts were not found in later forum posts.

### 1.2 Buyback history and execution

**1. Aavenomics Part One.** ARFC by ACI, 4 Mar 2025 (https://governance.aave.com/t/arfc-aavenomics-implementation-part-one/21248); Snapshot closed 18 Mar 2025 (YAE 815,167).

> "Mandate the AFC to leverage Treasury management token allowances to acquire AAVE on secondary markets and/or through MM partners, then send acquired tokens to the ecosystem reserve. The program will start with a $1M/week AAVE acquisition for the first 6 months…"

**2. AIP 286, "AAVE Buybacks allocation".** Executed at block 22,225,829 (≈9 Apr 2025). It approved $4m of aEthUSDT from the Collector to the AFC Safe `0x22740deBa78d5a0c24C58C740e3715ec29de1bFa`.

> "allowing for approximately one month of AAVE buybacks … It also gives us time to finalize and deploy the updated Aave Swapper contract".

**3. Buybacks program update, $50m a year.** ARFC by ACI, 22 Oct 2025 (https://governance.aave.com/t/arfc-aave-buybacks-program-an-update/23290); Snapshot closed 31 Oct 2025 (YAE 881,962).

> "This ARFC proposes to enshrine a long-term AAVE buyback program funded by protocol revenue … Annual Budget: $50,000,000 … Weekly Execution Range: $250,000 - $1,750,000 … Funding Sources: Protocol revenue allocation ($50M/year)."

**4. Execution tooling.** MainnetSwapSteward ARFC, 4 Sep 2025 (https://governance.aave.com/t/arfc-steward-deployment-mainnetswapsteward-and-rewardssteward/23070):

> "introducing TWAP Orders on-chain without having to grant an allowance to the AFC, meaning the DAO's funds never leave the DAO's possession".

The guardian is the AFC Safe (3-of-4). The address-book lists `COLLECTOR_SWAP_STEWARD` `0xb7D402138Cb01BfE97d95181C849379d6AD14d19` (13,283 bytes of code). The forum post does not print the steward's address, so this match comes from the address-book name only.

The February 2026 Funding Update (8 Feb 2026; https://governance.aave.com/t/direct-to-aip-february-2026-funding-update/24030; AIP 449) says:

> "AAVE acquired via the Aave Swapper contract will be transferred to the AFC" and "This Allowance enables the AAVE acquired via BuyBacks to be held on the AFC SAFE."

**5. Budget cut to $30m a year.** ARFC by TokenLogic, 4 Mar 2026 (https://governance.aave.com/t/arfc-buyback-program-budget-adjustment/24229); Snapshot closed 15 Mar 2026 (For 363,253).

> "reducing the annual AAVE buyback budget from approximately $50M to $30M … ~292 AAVE/day … The MainnetSwapSteward will be configured to prioritise ETH and ETH-correlated assets".

The same post says the programme had "acquired over 205,000 AAVE (1.28% of total supply)".

**6. Pause.** ARFC by TokenLogic, 22 Apr 2026 (https://governance.aave.com/t/arfc-pause-aave-buybacks/24686); Snapshot closed 1 May 2026.

> "AAVE buybacks are paused effective April 19, 2026, and will remain paused until the situation surrounding the rsETH incident becomes clearer. The cadence will be revisited at a later date, and any resumption will be communicated via the standard funding update."

The later funding updates (May/June 2026 [25000], July 2026 [25277], August/September 2026 [25597]) and AIPs 492, 512 and 529 contain no buyback resumption. They do keep AAVE as an "acquirable asset" in the MainnetSwapSteward budget table:

> "To support the acquisition of GHO, wETH and AAVE replenish token budgets on the MainnetSwapSteward" (25597, created 1 Sep 2026).

That is standing capability, not a buyback instruction.

**On-chain reconstruction.** Free archive `eth_call` via eth.drpc.org, first-of-month blocks (`raw/` + `scripts/t7.py`):

| Date | AFC AAVE | AFC aAAVE | Collector AAVE | Collector aAAVE | Eco Reserve | AFC GHO debt |
|---|---|---|---|---|---|---|
| 2025-04-01 | 0 | 0 | 190 | 1,218 | 586,293 | 0 |
| 2025-07-01 | 55,997 | 0 | 0 | 16 | 510,754 | 0 |
| 2025-10-01 | 94,494 | 0 | 1 | 45 | 446,155 | 0 |
| 2026-01-01 | 130,617 | 0 | 2,154 | 3,966 | 385,364 | 0 |
| 2026-04-01 | 191,565 | 0 | 23,742 | 6,149 | 319,937 | 0 |
| 2026-04-19 | 220,089 | 219 | 10,527 | 6,166 | 309,271 | 0 |
| 2026-05-01 | 220,308 | 0 | 11,240 | 6,167 | 299,144 | 0 |
| 2026-06-01 | 1,566 | 220,308 | 11,240 | 6,216 | 594,179 | 4,038,659 |
| 2026-07-01 | 0 | 240,502 | 0 | 6,584 | 589,463 | 4,051,126 |
| 2026-10-26 | 0 | 240,502 | 0 | 6,593 | 563,340 | 4,103,395 |

What the AFC changes after the pause are (found by binary search on balances, then reading the logs at those blocks):

| Date | AAVE | From | Classification |
|---|---|---|---|
| 26 May 2026 | 1,565.48 | `0x1BDecEAE…b10b1` (address-book `CHAOS_LABS_DAO_WALLET_VESTING`) | Vesting returned during the Chaos Labs offboarding. Not a buyback. |
| 15 Jun 2026 | 11,240.33 | The Collector | Internal move of earlier purchases. |
| 24 Jun 2026 | 1,405.00 + 5,983.00 | Two EOAs, `0xe26acd332dab8c64b70dc95da74b07a5265f3387` and `0xaff94a1706ef11c0ddd1af83d17456423539b342` (no code) | Unidentified. No primary source explains them. They could be late settlement of earlier OTC or market-maker purchases; this is not established. |

DefiLlama's `dailyHoldersRevenue` books $555k on 24 Jun 2026 for these. Its total since 9 Apr 2025 is $46.97m.

**Programme total.** The Funding Insights post (28 Feb 2026; https://governance.aave.com/t/aave-dao-funding-insights/24192) says "$42M has been allocated to acquiring 205,000 AAVE over the 10-month period".

The 250,368 AAVE / ~$45.4m figure attributed to TokenLogic could not be verified. The dashboard (aave.tokenlogic.xyz) is client-rendered behind Clerk with no public API, and the /buybacks route returns 404.

On-chain, AFC plus Collector held about 236.8k AAVE-equivalent at the 19 Apr 2026 pause. The AFC holds 240,502 now, including the Chaos Labs vesting 1,565 and the 7,388 from EOAs.

### 1.3 Where bought AAVE goes

- **Documented destination.** Part One says the Ecosystem Reserve, and aave.com/docs/ecosystem/aave (undated) still says so:

  > "Acquired tokens are sent to the Aave DAO Ecosystem Reserve … The tokens are used through governance-approved programs rather than burned."

  That docs page also states the $50M annual budget. Both points are contradicted by governance (the $30m cut, then the pause) and by chain state. The bought AAVE has never moved to the Ecosystem Reserve.
- **Actual destination.** The AFC Safe, supplied to Aave V3 Core as aAAVE (`0xA700b4eB416Be35b2911fd5Dee80678ff64fF6C9`; AAVE reserve factor 0, so the balance does not accrue).
  - AFC `getUserAccountData`: collateral $31.38m, debt $4.10m, health factor 5.81.
  - The May/June 2026 Funding Update (1 Jun 2026; https://governance.aave.com/t/direct-to-aip-may-june-2026-funding-update/25000) says:

    > "Following the rebalancing of the Ahab and Aave Finance Committee (AFC) SAFEs to facilitate the DeFi United donation, the AFC SAFE now holds AAVE, USDC, and GHO debt. The GHO debt is to be repaid from a combination of revenue derived from the Plasma instance and the USDC held within the AFC SAFE."
  - The July 2026 Funding Update (https://governance.aave.com/t/direct-to-aip-july-2026-funding-update/25277) adds:

    > "With the treasury holding a large AAVE position … this proposal grants the AFC the ability to participate in the AAVE, ETH and BTC derivative markets to earn yield and/or manage asset exposure".
- **Stated purpose.** Funding Insights (28 Feb 2026):

  > "The buybacks represent a way for the Aave DAO to fund SM emissions, SP renumeration and future business initiatives."

  So the bought AAVE is recyclable treasury stock.
- **Burn.** None. `totalSupply()` = 16,000,000.000. `0x…dEaD` holds 0.31 AAVE (incidental).
- **Paid out to stakers.** None. stkAAVE rewards come from the Ecosystem Reserve (`REWARDS_VAULT()` = `0x25F2…6491`), not from the AFC.

Treatment under the tracker's rules:
- RETIRED = HELD for the whole history (burned = 0, distributed = 0).
- Destination durability: "held-discretionary". Governance can move it with allowances via funding updates, and it is encumbered as loan collateral.

### 1.4 stkAAVE (Safety Module)

Contract `0x4da27a545c0c5B758a6BA100e3a049001de870f5` (address-book `AaveSafetyModule.STK_AAVE`). On-chain reads:

| Read | Value |
|---|---|
| `STAKED_TOKEN` = `REWARD_TOKEN` | AAVE |
| `REWARDS_VAULT` | Ecosystem Reserve |
| `assets(stkAAVE).emissionPerSecond` | 1,736,111,111,111,111 wei/s = **150.0 AAVE/day** |
| `getCooldownSeconds` | 172,800 (2 days) |
| `UNSTAKE_WINDOW` | 2 days |
| `getMaxSlashablePercentage` | **0** |
| `getExchangeRate` | 1.0 |
| `totalSupply` | 2,477,437.67 |
| AAVE held by the contract | 2,477,439.24 |
| `DISTRIBUTION_END` | year 2123 |

Emission history (Snapshot and AIPs), all paid in AAVE from the Ecosystem Reserve:

| Rate (AAVE/day) | Source |
|---|---|
| 315 → 260 | Earlier update (referenced in AIP 459) |
| 260 → 220, cooldown 7d → 2d | AIP 459, March 2026 |
| 220 → 150 | ARFC https://governance.aave.com/t/24945 (20 May 2026); Snapshot closed 30 May 2026; AIP 491 |

AIP 491 sets `emissionPerSecond` "to `150 ether / 1 days` (1,736,111,111,111,111 wei/s)". This matches the on-chain value.

- **Slashing.** Part One:

  > "Deprecate the current StkAAVE slashing element entirely, maintain AAVE rewards".

  The on-chain max slashable is 0, consistent with that.
- **Future intent (documented, not executed).** AIPs 439 (Jan 2026) and 486 (May 2026):

  > "**stkAAVE is not being phased out**; it will be **repurposed** into a **no-risk staking (dividend yield) contract**."
- **Claim backlog.** AIP 522, "Safety Module August 2026 – Allowance Update" (https://governance.aave.com/t/25550):

  > "Rewards accrue to stakers continuously, but the allowance is only consumed when a staker claims … At the snapshot (block 25843055, 2026-08-27), the stkAAVE allowance covered only 8.4% of what stakers had already earned."

  It references a "50,500 AAVE backlog gap". The Ecosystem Reserve → stkAAVE allowance today is 26,750.8 AAVE.

  Consequence: **measure emissions as rate × time (accrual), not as claims.** Claims lag accrual and are capped by the allowance.
- **Other Safety Module tokens.** stkAAVE/wstETH BPTv2 (`0x9eDA…8101`), stkABPT v1 (`0xa111…fb47`) and legacy stkGHO (`0x1a88…5d`) all show `emissionPerSecond` = 0.
  - AIP 459 set BPTv2 "Emissions … 40 → 0; Cooldown 20 Days → 0; Slashing 10% → 0%". AAVE/ETH liquidity moved to protocol-owned liquidity.
  - The legacy stkGHO still holds 30.0m GHO with no emissions.

Derived:
- Staking yield in tokens = 54,750 ÷ 2,477,438 = **2.21% a year**. TokenLogic targeted "~2.75%" assuming a smaller stake.
- Lock rate = 2,477,438 ÷ 15,436,717 (CoinGecko circulating) = **16.05%**.

### 1.5 Umbrella

Docs (aave.com/docs/aave-v3/umbrella, undated, read 27 Oct 2026):

> "Umbrella allows users to stake two types of assets: aTokens (supplied tokens such as aUSDC, aUSDT, and aWETH) and GHO … If a deficit occurs in a given asset, Umbrella enables the corresponding staked assets to be burned and offset the bad debt".

> "Staking risk is isolated to the specific asset and network where the aTokens are staked."

> "USDT staking has a 100,000 USDT offset, meaning the Aave DAO covers the first 100,000 USDT of bad debt before any staker assets are affected."

The 100k figure is stale against chain state.

Contracts (address-book `UmbrellaEthereum`, all with code on-chain):

| Contract | Address |
|---|---|
| Umbrella | `0xD400fc38ED4732893174325693a63C30ee3881a8` |
| RewardsController | `0x4655Ce3D625a63d30bA704087E52B4C31E38188B` |

Stake tokens (ERC-4626 over Aave static aTokens):

| Stake token | Address | totalAssets | Value at stated rate | Reward | Current emission |
|---|---|---|---|---|---|
| stkwaUSDC | `0x6bf183243FdD1e306ad2C4450BC7dcf6f0bf8Aa6` | 45,746,923 waUSDC | ≈ $54.5m (1.1913 USDC/share) | aEthUSDC | ≈ 975,976/yr |
| stkwaUSDT | `0xA484Ab92fe32B143AEE7019fC1502b1dAA522D31` | 50,984,917 waUSDT | ≈ $60.2m (1.1804) | aEthUSDT | ≈ 1,023,974/yr |
| stkwaWETH | `0xaAFD07D53A7365D3e9fb6F3a3B09EC19676B73Ce` | 10,390.5 waWETH | ≈ 11,143 ETH ≈ $25.7m at oracle $2,305 | aEthWETH | ≈ 393 ETH/yr |
| stkGHO | `0x4f827A63755855cDf3e8f3bcD20265C833f15033` | 1,182,058 GHO | — | GHO | 0 (sunset in AIP 507) |

- Cooldown is 20 days on all four.
- **Total staked ≈ $141m.** This is not AAVE and is excluded from every float figure.
- **Reward payer.** AIP 507 (July 2026): "The reward payer (`AaveV3Ethereum.COLLECTOR`) and each market's `distributionEnd` remain unchanged".
- AIP 528 (https://governance.aave.com/t/25700) renews Collector allowances to the RewardsController: "500,000 aEthUSDT, 475,000 aEthUSDC and 160 aEthWETH … ending on 2 December 2026".
- So Umbrella is a **revenue-funded security cost paid in aTokens**: about $2.9m a year at current emission. It is not AAVE dilution.

Deficit offsets (`Umbrella.getDeficitOffset`):

| Asset | Offset | Pending deficit |
|---|---|---|
| USDC | 1,300,000 | 0 |
| USDT | 1,600,000 | 0 |
| WETH | 53,041.4 | 0 |

The Ethereum Core WETH reserve deficit is `Pool.getReserveDeficit(WETH)` = 52,964.45 WETH, inside the 53,041 offset. The current WETH deficit therefore sits with the DAO's first-loss layer, not with stkwaWETH stakers.

- **Umbrella on V4.** ARFC 11 Sep 2026 (https://governance.aave.com/t/25623), forum stage only; no Snapshot found. It describes V4 deficit ledgers ("`asset.deficitRay` … `spoke.deficitRay` … publicly readable through getAssetDeficitRay").

### 1.6 Reserve deficits after the rsETH incident (`Pool.getReserveDeficit`)

| Market | Deficit |
|---|---|
| Ethereum Core | 52,964.45 WETH; also DAI 2,700, RPL 1,978 and small dust |
| Arbitrum | 29,835.20 WETH, 821.26 wstETH, 26,768 USD₮0, 2,672 ARB and dust |
| rsETH reserve itself | 0 on both. The deficit is recorded on the debt asset, not the collateral. |

WETH plus wstETH across the two markets ≈ 83,621 ETH-equivalent. This is consistent with the ~83.8k figure in the brief, but on-chain the deficit is not labelled "rsETH". Attribution to the incident rests on the incident reports (https://governance.aave.com/t/24580).

### 1.7 Ecosystem Reserve, Collector and other DAO AAVE

| Wallet | Address | Holds |
|---|---|---|
| Ecosystem Reserve (address-book `MiscEthereum.ECOSYSTEM_RESERVE`) | `0x25F2226B597E8F9514B3F68F00f494cF4f286491` | 563,282.92 AAVE |
| AFC Safe (`MiscEthereum.AFC_SAFE`) | `0x22740deBa78d5a0c24C58C740e3715ec29de1bFa` | 240,502.34 aAAVE; 4,104,118.69 GHO variable debt (vToken `0x786d…ed04B`) |
| Collector | `0x464C…e18c` | 6,593.04 aAAVE; 0 AAVE |
| `BALANCER_RECLAMM_SAFE_DAO_WALLET_TREASURY` | `0xAAA973Fe8A6202947e21D0a3a43d8E83ABE35C23` | 58,343.4 AAVE |

The Balancer ReCLAMM Safe is 2-of-3; its signers are the TokenLogic execution Safe, Aave Labs and LlamaRisk. Its link to "Extend AAVE/wETH Liquidity" (AIP 449: "allowances for a new Aave Liquidity SAFE for the equivalent of 40k AAVE") is inferred from the address-book name and timing. No primary document names this address as that Safe.

Ecosystem Reserve history:
- **May 2026: +295k.** AIP 480, "Winding Down the LEND Migration Contract" (executed May 2026):

  > "redirects the approximately 302,000 AAVE remaining in the migration contract to the Ecosystem Reserve".

  The migrator `0x3176…1de4` holds 0 AAVE. The AIP adds: "A subsequent ARFC will propose distribution of the recovered AAVE for growth and ecosystem incentives." That ARFC was not found.
- **Other outflows since April 2025.** Before that inflow, the reserve fell from 586k to 299k. This includes stkAAVE/stkABPT reward claims, streams and the 40k POL allowance.

Small DAO-adjacent balances, which are service-provider vesting or claims rather than discretionary: Merkle distributor 7,512 AAVE; ACI, TokenLogic, LlamaRisk and Chaos vesting wallets 2,603 / 3,450 / 354 / 430; Certora receiver 4,911; LlamaRisk receiver 5,593.

**Verified discretionary DAO AAVE (Ethereum)** = Ecosystem Reserve + AFC + Collector + ReCLAMM Safe = **868,722 AAVE**. The ~901k figure in the brief could not be reproduced from primary or on-chain sources; the gap is about 32k.

### 1.8 Vesting and streams (Ecosystem Reserve `getStream`, `getNextStreamId` = 100020)

| Stream | Recipient | Total | Period | Remaining at 27 Oct |
|---|---|---|---|---|
| 100017 | Aave Labs `0x488c053F07391dC78b12Da7107eb22aF77A255a1` | 75,000 AAVE | 13 Apr 2026 → 12 Apr 2030 | 66,320.18 |
| 100018 | LlamaRisk funding receiver `0x9eE1…b27c` | 5,000 AAVE | 27 May 2026 → 27 May 2027 | 3,286.35 |
| 100019 | TokenLogic `0xAA08…9894` | 5,000 AAVE | 27 Jun 2026 → 27 Jun 2027 | 5,000 (no withdrawal) |

- Aave Labs' recipient address holds 8,679.8 AAVE (the amount withdrawn so far) and 0 stkAAVE.
- AIP 469: "Stream for 75,000 units AAVE from the Ecosystem Reserve over 48 month duration".
- The Collector also streams 5m and 15m aEthLidoGHO to Aave Labs (streams 100083 and 100084). This is a stablecoin cost, not AAVE dilution.

Tracker categories:
- Outflow (d) ecosystem/grants = 28,750 AAVE a year at current streams.
- Under CoinGecko's convention (circulating = total − Ecosystem Reserve), every Ecosystem Reserve outflow is also an (e) "release". The tracker's FIFO convention ("Staking-funding part taken OUT of (e)") is needed to avoid double counting stkAAVE claims.

### 1.9 Aavenomics 3.0 — governance-record status

| Venue | What exists |
|---|---|
| Forum | Community thread "Restart aave buy backs" (bobgodwinx, 18 May 2026; https://governance.aave.com/t/restart-aave-buy-backs/24936; auto-closed 26 Jul 2026). Reply #16 (25 Jun 2026) quotes a tweet by Stani: "new Aavenomics 3.0 will have immutable and automated buybacks of AAVE / Aave team is working on this". |
| | "[ARFC] Staking in Aavenomics 3.0 - do we want it?" (axieaur, 23 Oct 2026; https://governance.aave.com/t/25905). One paragraph of personal opinion, one post, no specification. Despite the [ARFC] tag it is not a proposal. |
| | "AAVE Needs a Formal Surplus Allocation Framework" (community, 7 Jun 2026; https://governance.aave.com/t/25089). Asks "What exactly happens to bought-back AAVE today?". No service-provider answer. |
| | No post by Aave Labs, TokenLogic or any service provider specifying Aavenomics 3.0 in the latest topics created through 26 Oct 2026. Forum search for "aavenomics 3.0" returns only the two community threads above. |
| Snapshot | Every one of 209 proposals created in `aavedao.eth`/`aave.eth` since 20 Feb 2025 was checked; the latest closed 26 Oct 2026 and none is active. None concerns Aavenomics 3.0, buyback restart, AAVE staking redesign or burn. |
| AIPs | AIPs 380–537 (537 created 26 Oct 2026, "Onboard SPCXc to the Aave V4 Equities Hub on Base") were checked. None concerns buybacks after AIP 286 (the funding updates aside), Aavenomics 3.0 or burn. |
| On-chain | The AFC aAAVE balance is 240,502.34 at 1 Jul, 1 Aug, 1 Sep, 1 Oct and 26 Oct 2026. Collector AAVE is 0. Total supply is unchanged. DefiLlama `dailyHoldersRevenue` is 0 every day after 24 Jun 2026. |
| Press | The Defiant, 27 Jun 2026, "Aave confirms Aavenomics 3.0 live…": "Aavenomics 3.0 is now active, with automated AAVE token buybacks running". It names no forum post, vote or on-chain source. A second Defiant piece the same day ("Aave advances automated AAVE buyback overhaul…") uses "would route all protocol and GHO revenue to AAVE holders by default", citing only Kulechov's X post. Whale Alert's story carries the same "launches" framing. |
| Q3 livestream, 22 Oct 2026 | Not reviewed. No primary transcript or forum summary was found. |

**Finding.** As of 27 Oct 2026, Aavenomics 3.0 has **no specification, no forum proposal from any contributor, no Snapshot vote and no AIP**.
- The "live" press claim is not supported by the governance record. Chain state contradicts it for the known DAO wallets.
- The only governance-binding state is the 1 May 2026 Snapshot that ratified the pause.
- The nearest primary statement of intent is the "no-risk staking (dividend yield)" note in AIPs 439 and 486.
- Limit: a mechanism sending AAVE to a wallet outside the known set would not show in these reads. No such contract appears in the address-book.

### 1.10 Merit and other incentives

- **Merit ARFC** (Marc Zeller/ACI, 16 Feb 2024; https://governance.aave.com/t/16646):

  > "'Merit' is a merkle-tree-based periodic airdrop system … The 'Merit' system employs a booster-based model to reward specific user actions with points, dictating the weekly StkAAVE and GHO distribution."
- **Current funding** is through Collector allowances to the "Merit Ahab" Safe `0xAA2461f0f0A3dE5fEAF3273eAe16DEF861cf594e`. AIP 492 (June 2026) gave "aEthLidoGHO … Amount: 5M Spender: Ahab … to the Merit Ahab address", plus the Incentive Budget Safe `0x66Ac…FabE` and Merkl campaigns. For example, the Base Tokenized Stocks programme "earn rewards in USDC … through Merkl" (TokenLogic, 13 Oct 2026, in 25597).
- **Aave Liquidity Committee:** "Measured liquidity incentive spend is now approximately $125,000 per thirty day period" (25597).
- **AAVE-denominated Merit.** None was found in 2026 primary sources. Supply-side (b) and demand-side (c) incentives are paid in GHO, stablecoins and aTokens out of revenue. They belong in the tracker's $ outflow and subsidy-cover rows, not in AAVE token dilution. The AAVE-denominated amount is not established and is probably 0 (unverified).
- **Docs** (aave.com/docs/aave-v3/markets/incentives): "Merit programs are third-party initiatives. Aave Labs does not guarantee these…"

---

## 2. Archetype proposal (for Jake to decide)

**Candidate primary: A3 Revenue Buyback, with status PAUSED.**
- **Case for.** A documented revenue-to-token conversion exists:
  - 23290: "funded by protocol revenue … Funding Sources: Protocol revenue allocation ($50M/year)".
  - 24229: a $30m budget funded with ETH from liquidation and SVR revenue.
  - It was executed from 9 Apr 2025 to 19 Apr 2026, as the chain shows.
  - It is not emissions-funded: stkAAVE yield comes from the Ecosystem Reserve and is a separate cost.
- **Case against.**
  - The conversion is a discretionary $ budget, not a revenue share. It is revisable by Snapshot plus allowances ("Programmed? = revisable by governance").
  - It is currently paused with no restart condition.
  - Its output is held as encumbered, recyclable treasury stock ("fund SM emissions, SP renumeration").
- **Treatment of paused or announced programmes:**
  - Window-dated config. $1m/week from 9 Apr 2025 (AIP 286 execution); $50m a year from 31 Oct 2025 (Snapshot); $30m a year from 15 Mar 2026 (Snapshot; no dedicated AIP); **$0 from 19 Apr 2026** (pause effective date; ratified 1 May 2026).
  - Implied buyback = min(budget pro-rata, revenue). The "share" = budget ÷ window revenue, displayed as derived.
  - Q0 (trailing 90 days) implied = 0 and actual = 0.
  - Aavenomics 3.0 goes in "CAPTURE STATUS + switch power + dated switch signals" as an announced but unspecified signal, with dates: tweet 25 Jun 2026; AIP 439/486 notes Jan and May 2026. No share is configured until a specification passes Snapshot.
- **Blocks that apply:**
  - Implied and actual buyback, split as burned 0 / held all / distributed 0, and retirement rates.
  - Net absorption against emissions (54,750/yr) and against unlocks (Ecosystem Reserve streams 28,750/yr plus any other Ecosystem Reserve releases).
  - Staking yield: emissions-funded, so not holder value.
  - Lock rate 16.05%.
  - "Umbrella staked $ (Aave only)" exists already as A3 column 109.

**Alternative primary: monitored as latent value capture** (per Jake's 2026-10-13 decision that latent capture sits alongside A1–A4, not as an A5).
- **Case.** Today no revenue converts to AAVE, and the only staker yield is emissions-funded. The documented history and the announced 3.0 make the switch power high. The rows would then appear only in CP, until a restart is specified.

**Not A4.** No burn exists or is specified. Supply is fixed at 16m with no mint function in use.

**Not A1 or A2.**

**CP blocks that apply in either case:**
- Holder value yield: Q0 = 0. The historical windows credit held buybacks under the retirement rule.
- Outflows:

  | Category | Amount | Source |
  |---|---|---|
  | (a) security | 54,750 AAVE/yr | stkAAVE; Umbrella's aToken cost tracked in $ only |
  | (b) supply-side, (c) demand-side | Not in AAVE | Merit, ALC and Merkl paid in GHO, stables and aTokens: $ series from funding updates, manual |
  | (d) grants | 28,750 AAVE/yr | Streams |
  | (e) Ecosystem Reserve releases | — | Under the circulating = 16m − Ecosystem Reserve convention |
- Insider share of stake: not established.
- Discretionary supply: 868,722 (Ecosystem Reserve + AFC + Collector + POL Safe).
- Subsidy cover: revenue ÷ $ incentives.
- Market share: Aave parent is 32.6% of DefiLlama "Lending" category fees over 30 days ($37.55m of $115.29m; 120 protocols).

**Supply convention to decide.** No Aave-published circulating figure was found. CoinGecko's figure equals 16m − Ecosystem Reserve exactly. If the tracker's convention removes treasury from circulating, use 16m − (Ecosystem Reserve + AFC + Collector + POL Safe) = 15,131,278. Tradeable float = that − stkAAVE 2,477,439 = 12,653,839.

---

## 3. Metric map

See `sources.csv`; each row is one metric. Notes on history for STOCK rows:

| Source | Test | Result | Use |
|---|---|---|---|
| publicnode (free) | Historical `eth_call` | Refused beyond ~127 blocks: −64 OK, −127 refused ("Archive requests require a personal token"). Also refused at block 23,660,000 (~1 year back). | Daily live reads only. |
| | `eth_getLogs` | Refused beyond ~10–20k blocks back | |
| eth.drpc.org (free) | Archive `eth_call` | Works: block 23,658,496 (2025-10-26 01:57 UTC) returned the Ecosystem Reserve balance; the monthly table in §1.2 was built this way | Backfill all Ethereum STOCK balances at any past block. |
| | `eth_getLogs` | ≤100-block ranges OK; 500+ refused ("ranges over 10000 blocks are not supported on free plan") | Too narrow for log scans. |
| blastapi (free) | `eth_getLogs` | 10-block limit | |
| Etherscan / Alchemy | Log scans | Not testable here (keys held by the tracker) | Transfer-log FLOW backfills. |

---

## 4. Test reads (summary; full values in `sources.csv`)

All OK unless stated otherwise.

| Source | Status |
|---|---|
| Ethereum `eth_call` via publicnode (latest) | OK |
| Arbitrum publicnode | OK |
| Base, Avalanche, Polygon and Optimism publicnode | Code checks on Collectors OK |
| drpc archive | OK |
| DefiLlama (fees, revenue, holdersRevenue, TVL, protocols) | OK |
| CoinGecko | Coin endpoint and 365-day `market_chart` OK. **History beyond 365 days refused** (401, error 10012, "Public API users are limited…"). |
| Snapshot GraphQL (`hub.snapshot.org/graphql`) | OK |
| Governance forum JSON | OK |
| BGD address-book and governance cache (raw.githubusercontent) | OK |
| IPFS via gateway.pinata.cloud | OK; ipfs.io and dweb.link returned 429 |
| Aave API GraphQL (`api.v3.aave.com/graphql`) | OK. `markets` returns `totalMarketSize`; Ethereum Core = $22.61bn. No treasury, revenue or Umbrella fields in the query root. |
| aave.com/docs | Fetched with curl: HTTP 200. The WebFetch tool's session allowlist rejected the URL; the site itself did not refuse. |
| TokenLogic dashboard | HTML 200 but client-rendered behind Clerk auth: not machine-readable. /buybacks returns 404. |
| GitHub API (`gh`) | Not enabled for this session; raw.githubusercontent was used instead. |

---

## 5. Wiring cost estimate

| Piece | Reuse or new | Size |
|---|---|---|
| DefiLlama fees, revenue and holdersRevenue on parent `aave` (children for the per-product split) | Reuse | Small |
| DefiLlama TVL `aave` | Reuse | Small |
| CoinGecko price, market cap, circulating, volume | Reuse | Small |
| Ethereum `eth_call` balance reads: Ecosystem Reserve, AFC (aAAVE, AAVE, vGHO), Collector, POL Safe, stkAAVE supply, dead address | Reuse balanceOf pattern | Small |
| Backfill of those STOCKs via archive `eth_call` (drpc free) at daily blocks | New RPC endpoint plus block-by-date helper | Small–medium |
| stkAAVE parameters (`assets()` emission, cooldown, slashable, Ecosystem Reserve allowance) | New decoders, simple | Small |
| Ecosystem Reserve stream reads (`getStream` 100017–100019, `getNextStreamId`) | New | Small |
| Transfer-log scans: Ecosystem Reserve outflows by recipient (stkAAVE claimers vs streams vs Safes); AFC and Collector AAVE inflows (actual buyback) | Reuse the Etherscan/Alchemy scanner; new recipient classification | Medium |
| Umbrella: 4 stake tokens `totalAssets` × waToken `convertToAssets` × oracle price; RewardsController `calculateCurrentEmission`; Umbrella `getDeficitOffset`/`getPendingDeficit` | New | Medium |
| Pool deficits, `getReserveDeficit` on Ethereum Core and Arbitrum (plus other V3 chains if wanted) | New, multichain | Small–medium |
| Fee config (reserve factor, liquidation protocol fee, flash premium) as a config audit | New, light | Small |
| Governance signal watcher: Snapshot GraphQL plus forum search for buyback, Aavenomics and staking terms; BGD cache for AIP state | New | Small–medium |
| Multichain Collector balances (all aTokens on ~15 chains) | New | Large. Not needed for AAVE supply; only for treasury valuation. |
| Aave API GraphQL markets, for business series | New | Small, optional |
| V4 TreasurySpoke and Hub fee accrual reads | New; ABI not researched | Medium, deferred (DefiLlama `aave-v4` covers revenue) |

---

## 6. Not found / open questions

1. **Aavenomics 3.0.** No specification, vote or contract was found. The 22 Oct 2026 Q3 livestream content is not in any primary channel. Whether "immutable and automated buybacks" would burn, hold or distribute is unknown.
2. **Total bought.** The 250,368 AAVE / ~$45.4m figure is unverifiable from primary sources: TokenLogic's dashboard is not machine-readable and on-chain balances do not reconcile exactly. The on-chain stock at the pause was ≈236.8k across AFC and Collector.
   - The 24 Jun 2026 inflows to the AFC (1,405 + 5,983 AAVE from two EOAs) are unexplained.
   - Exact execution venues are not established: CoW settlement is referenced in DefiLlama's adapter; the Aave Swapper and MainnetSwapSteward appear in forum posts.
3. **MainnetSwapSteward address.** No forum post prints it. The address-book `COLLECTOR_SWAP_STEWARD` `0xb7D4…4d19` is the probable match by name only.
4. **The ~901k DAO-controlled AAVE figure.** Not reproduced; 868.7k is verified. The source and composition of the 901k figure are not known.
5. **rsETH repayment revenue share.** The percentage, lender facilities and "dedicated repayment contracts" were not published in sources found.
   - The WETH reserve deficits (52,964 on Ethereum Core; 29,835 on Arbitrum) are still recorded on-chain.
   - The Umbrella WETH deficit offset of 53,041 was set by an AIP not identified in this pass (AIP 467, "Umbrella Deficit Updates", predates the incident).
6. **Aave Will Win product revenue.** No quarterly report, receiving address or on-chain flow was found. Unmeasured.
7. **Insider share of staked AAVE.** Not established; it needs a holder breakdown of stkAAVE, which needs logs.
   - Forum allegations that Aave Labs and Stani control "over 600,000 AAVE" of voting power (24936, reply 5) are community claims, unverified.
   - The Aave Labs grant recipient holds 0 stkAAVE.
8. **POL Safe identity.** `0xAAA9…5C23` (58,343 AAVE) is matched to the AIP 449 liquidity Safe by name and timing only. How its AAVE is deployed (direct balance vs pool) was not examined.
9. **Distribution of the 302k LEND-migration AAVE.** AIP 480 promised a follow-up ARFC; none was found.
10. **SVR revenue share.** The primary ARFC text (https://governance.aave.com/t/21247) was not read. The 100%-to-Collector figure rests on DefiLlama's adapter.
11. **aave.com/docs/ecosystem/aave** states a $50M budget and an Ecosystem Reserve destination. The page is undated and conflicts with governance and chain state. Do not use it as a config source.
12. **Umbrella on V4.** The parameters (25623) have not passed Snapshot. No V4 Umbrella contracts were looked up.
13. **Merit paid in AAVE in 2026.** None found; not proven zero. The Merkle distributor `0xa88c…FE10` holds 7,512 AAVE plus 107 stkAAVE from past distributions.
