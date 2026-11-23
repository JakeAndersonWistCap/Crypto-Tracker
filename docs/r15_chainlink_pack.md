# Research pack: Chainlink (LINK)

Read 2026-11-20, 09:15–10:30 UTC. Read-only: no code, config, test or other doc changed. One workbook build from the repo's 15 fixture dumps, for (g).

- On-chain figures are my own `eth_call` reads at **Ethereum block 26,455,357 (2026-11-20 09:17:11 UTC)** via ethereum-rpc.publicnode.com unless another block is given. Dated history (quarter ends, the 90-day window) is `eth_call` at archive blocks via eth.drpc.org.
- Transfer histories are from Blockscout (eth.blockscout.com, its `tokentx` and `/api/v2` endpoints) and, where Blockscout rate-limited, Ethplorer's free API, read the same day.
- PUBLISHED = named in a Chainlink document (chain.link, blog.chain.link, the staking v0.2 public guide, the supply API). INFERRED = not named anywhere; the evidence is given.

## Read this first

1. **Chainlink's own figure is readable now, and it equals CoinGecko to the token.** `supply.chain.link/supply` (the "Link to API" on chain.link/circulating-supply) returns circulating 766,849,970.42 = 1,000,000,000 − the 33 listed wallets (233,150,029.58). CoinGecko: 766,849,970.42. The repo's note that the page is "unreadable here" is stale.
2. **Chainlink does not subtract the Reserve; the repo does.** The repo's circulating (1B − Reserve − 33 wallets) is 760,045,210 today, 0.89% below Chainlink's figure. CoinMarketCap (748,099,970.42) is exactly the figure before the 2026-10-09 release: it is one release (18,750,000) stale.
3. **Under Jake's rule, circulating = total supply = 1,000,000,000.** Every non-circulating wallet is a 3-of-5 Safe (or a legacy multisig); the Reserve is a timelock whose bypasser multisig can move it without delay. Nothing is burned. None of the 33 wallets meets all three tests. Free float = **703.6M (70.4%)**.
4. **Releases run on a fixed yearly cycle of 70M = 7% of supply**, the rate chain.link states ("Token release schedule is currently 7% of the total supply per year"). Since mid-2023 every year is 21.0M (June) + 18.75M (Sep/Oct) + 11.25M (December) + 19.0M (Mar/Apr), on one day each quarter. Next, by the pattern: about 2026-12-18, 11.25M (INFERRED).
5. **86% of every release goes the same day, through one EOA, to an exchange hot wallet.** 253.8M of the 296.25M released since 2022-08-26 went from the 33 wallets to EOA `0x9b1c1752…` and on to `0x28C6c062…` (the address commonly labelled Binance 14; the label could not be re-read today). The other 42.45M goes to Safe `0xD50f074E…`, which funds staking rewards and node-operator payments.
6. **Staking rewards are funded by released supply, not by fees.** The RewardVault's only funding is 607,500-LINK tranches from Safe `0x7e10a625…`, which traces back to the 33 wallets. No Payment Abstraction LINK reaches the vault or the pools. The vault was topped up on 2026-10-14: the reward end moved from 2026-11-27 (the repo's date) to **2027-03-17**.
7. **The Reserve is fed only by revenue conversions, and DefiLlama's revenue reproduces on chain to 0.4%.** 1,223,041.53 LINK in from the Payment Abstraction contract over 2026-08-22..11-19 = $14,202,208 at same-day prices; DefiLlama `chainlink` revenue for the same 90 days is $14,150,084. Chainlink's Q3 2026 review figure (1,543,330.47 LINK accumulated) matches the on-chain Q3 inflow to the token.
8. **There is an unmeasured supply-side incentive.** Release LINK reaches node operators through the same Payment Abstraction contract (63 recipients; 1.46M LINK in 2026 Q3). Today this sits inside the (e) release. It is a (b) outflow, and it is why Subsidy cover and Leakage (ii) read n/a.

## What the repo already holds

| Item | State | Rests on | Check today |
|---|---|---|---|
| `CIRCULATING_ONCHAIN["Chainlink"]` | "established": total − `buyback_fund_balance` − `noncirculating_holding_tokens` (33 wallets) | Jake 2026-10-07; D2-4a 2026-10-26 (33 wallets) | The 33 wallets match `supply.chain.link/supply` one for one. Subtracting the Reserve and the 33 conflicts with the rule of 2026-11-17 |
| `RULE_IN`, `REVENUE_SPLIT`, `DECLARED_NA_REASONS`, `FROZEN_PROJECTS` | no Chainlink entry | — | — |
| `UNLOCK_STOCKS` | none declared; falls back to the `subtract` legs less `UNLOCK_NOT_A_RELEASE` | `unlock_stock_metrics` | resolves to `noncirculating_holding_tokens` only (the 33 wallets); the Reserve is excluded as not a release. Correct |
| Total supply | 1,000,000,000, no mint function | LinkToken.sol | Confirmed |
| Staking v0.2 | both pools read (balanceOf and `getTotalPrincipal`) | public guide | Confirmed. `lock_model.cooldown_days` is still None: it reads 28 days |
| Reward vault | rate read; "EVERY REWARD BUCKET ENDS 2026-11-27" | read 2026-09-29 / 10-13 | **Stale:** ends 2027-03-17 after the 2026-10-14 top-up |
| `CL_STAKING_PATH` | 33 wallets → `0xD50f074E…` → `0x7A28b8cF…` → hops → `0x7e10a625…` → vault; `0x9d6a86fa…` "not staking" | Blockscout, 2026-10-13 | Confirmed. `0x9d6a86fa…` pays node operators through Payment Abstraction (see (c)) |
| Reserve | `buyback_fund_balance`; inflow scan from `0x5680681E…` | DefiLlama adapter | Confirmed; one LINK has ever left (a 1-LINK test) |
| `fee_split` | only SVR 50% documented; implied buyback suppressed | blog 2025-08-07 | unchanged |
| `INSIDER_SHARE_ROUTES` | 33 wallets, 0 principal | 2026-10-26 | Still 0; eight more issuer-side wallets read today, all 0 |
| CMC / CoinGecko note | "CoinGecko ~748M" | 2026-10-07 | CoinGecko now 766.85M; CMC still 748.10M |

Fixture: the 15 dumps, as of 2026-10-13. It is not Jake's live store.

## (a) Supply and every unlock

| Fact | Value | Source |
|---|---|---|
| Total supply = max supply | 1,000,000,000 LINK | `LINK.totalSupply()` at block 26,455,357; `supply.chain.link/supply` `totalSupply` |
| Minting | none possible (constant supply in LinkToken.sol, no mint selector) | repo record, SPEC; not re-read today |
| Burned | 0 | no burn function or address; supply unchanged |
| Genesis | 2017-09-16: 350M to `0x98C63b7B…`, 50M each to `0x75398564…`, `0x5560d001…`, `0xbe6977E0…`, `0xdAD22a85…` (from `0xf5503773…`) | Blockscout transfers |
| Release rate as published | "Token release schedule is currently 7% of the total supply per year" | chain.link/circulating-supply, read 09:24 UTC |

### The non-circulating wallets (block 26,455,357)

All 33 are listed by `supply.chain.link/supply` (balances there match mine to the wei). 24 carry Etherscan's "Chainlink: Noncirculating Supply n" label; three are in the 2022 blog post; six are the page's "Legacy Wallet Addresses".

| Wallet | Type | LINK |
|---|---|---|
| `0x7594Eb0ca0a7f313bEFD59AfE9e95c2201a443e4` | Safe 3-of-5 | 30,000,000.00 |
| `0x9BBb46637A1Df7CADec2AFcA19C2920CdDCc8Db8` | Safe 3-of-5 | 30,000,000.00 |
| `0x8652Fb672253607c0061677bDCaFb77a324DE081` | Safe 3-of-5 | 30,000,000.00 |
| `0x5A8e77bC30948cc9A51aE4E042d96e145648BB4C` | Safe 3-of-5 | 30,000,000.00 |
| `0x76287e0F7b107d1C9f8f01D5aFac314Ea8461a04` | Safe 3-of-5 | 30,000,000.00 |
| `0xe0b66bFc7344a80152BfeC954942E2926A6FcA80` | Safe 3-of-5 | 29,400,000.00 |
| `0x0DFfD343C2D3460a7EAD2797a687304Beb394ce0` | Safe 3-of-5 | 28,750,000.00 |
| `0x157235A3cc6011d9C26A010875c2550246aAbcCA` | Safe 3-of-5 | 22,000,000.00 |
| `0x5Eab1966D5F61E52C22D0279F06f175e36A7181E` | Safe 3-of-5 | 3,000,000.00 |
| 18 more Safes 3-of-5 (emptied by releases) | Safe 3-of-5 | 0.08 together |
| `0x98C63b7B…`, `0x75398564…`, `0x5560d001…`, `0xe0362F74…` (legacy) | multisig contracts, 6 owners each (`getOwners`) | 29.48 together |
| `0xbe6977E0…`, `0xdAD22a85…` (legacy) | EOAs | 0 |
| **33 wallets** | | **233,150,029.58** |

- Each of the 27 Safes has its own set of five owners (no owner set repeats). All are 3-of-5.
- The legacy wallets are the 2017 genesis recipients; in June 2022 they seeded the 27 Safes (30M lots), which is when the current scheme started.

Other issuer-side LINK outside the 33 (not subtracted by Chainlink, nor by the repo):

| Wallet | Role | Type | LINK | Tag |
|---|---|---|---|---|
| `0x9A709B7B69EA42D5eeb1ceBC48674C69E1569eC6` | Chainlink Reserve | RBACTimelock | 6,804,760.10 | PUBLISHED (Reserve blog) |
| `0x996913c8c08472f584ab8834e925b06D0eb1D813` | staking RewardVault | contract | 3,101,347.24 | PUBLISHED (public guide) |
| `0xD50f074Ec5c7997cE1DaacB513Ec194C480878Af` | first hop of releases (staking / payments path) | Safe 4-of-7 | 9,822,500.00 | INFERRED: funded only by the 33 |
| `0x9d6a86facdcf24859a38e6b9a2ef87610a4fc157` | node-operator payments wallet | Safe 3-of-8 | 900,576.13 | INFERRED: funded by `0x7A28b8cF…` and earlier by `0xb9b012ca…` (one of the 33) and `0x6781a24c…` |
| `0x6781a24ccc819941b7a4c9cc7b0dc32666e587e4` | older distribution Safe (54.5M from the legacy wallets, 2021) | Safe 2-of-n | 31,465.48 | INFERRED |
| `0x3feB1e09b4bb0E7f0387CeE092a52e85797ab889` | staking v0.1 (inactive) | contract | 443,341.50 | PUBLISHED; stakers' unclaimed LINK |
| `0x7A28b8cF…`, `0x1661f928…`, `0x8716F86F…`, `0x7e10A625…`, `0x9b1c1752…` | forwarders | Safes / EOA | 0 each | INFERRED |

### Release history

Balances of the 33 at quarter ends (archive `eth_call`, eth.drpc.org):

| Date | 33 wallets | Fall |
|---|---|---|
| 2025-01-01 | 361,900,029.55 | |
| 2025-04-01 | 342,900,029.55 | 19.0M |
| 2025-07-01 | 321,900,029.55 | 21.0M |
| 2025-10-01 | 321,900,029.55 | 0 |
| 2026-01-01 | 291,900,029.55 | 30.0M (two releases) |
| 2026-04-01 | 291,900,029.55 | 0 |
| 2026-07-01 | 251,900,029.58 | 40.0M (two releases) |
| 2026-10-13 | 233,150,029.58 | 18.75M |
| 2026-11-19 | 233,150,029.58 | 0 |

Every dated outflow from the 33 since the scheme began (Blockscout + Ethplorer; each release is a set of transfers on one day; 1-LINK test transfers folded in):

| Release day | LINK | To EOA `0x9b1c1752…` (then exchange) | To Safe `0xD50f074E…` |
|---|---|---|---|
| 2022-07-01..08-18 | 3,500,000 (7 × 500,000) | — | — (to `0x9d6a86fa…`) |
| 2022-08-26 | 21,000,000 | 17,500,000 | 3,500,000 |
| 2022-11-22 | 16,400,000 | 14,550,000 | 1,850,000 |
| 2023-03-03 | 9,100,000 | 6,100,000 | 3,000,000 |
| 2023-06-16 | 21,000,000 | 17,950,000 | 3,050,000 |
| 2023-09-15 | 18,750,000 | 15,700,000 | 3,050,000 |
| 2023-12-15 | 11,250,000 | 8,200,000 | 3,050,000 |
| 2024-02-02 | 19,000,000 | 15,950,000 | 3,050,000 |
| 2024-06-21 | 21,000,000 | 18,750,000 | 2,250,000 |
| 2024-09-20 | 18,750,000 | 18,125,000 | 625,000 |
| 2024-12-20 | 11,250,000 | 10,625,000 | 625,000 |
| 2025-03-14 | 19,000,000 | 14,875,000 | 4,125,000 |
| 2025-06-20 | 21,000,000 | 17,875,000 | 3,125,000 |
| 2025-10-10 | 18,750,000 | 18,750,000 | 0 |
| 2025-12-19 | 11,250,000 | 9,225,000 | 2,025,000 |
| 2026-04-03 | 19,000,000 | 14,375,000 | 4,625,000 |
| 2026-06-19 | 21,000,000 | 18,375,000 | 2,625,000 |
| 2026-10-09 | 18,750,000 | 16,875,000 (incl. 400,000 from `0x37398A32…`) | 1,875,000 |

- **Pattern:** quarterly, one day each, on a Friday in the last weeks of a calendar quarter or just after it. Since 2023-06 the amounts repeat exactly every four releases: 21.0M, 18.75M, 11.25M, 19.0M = **70.0M a year = 7%**, as published. Earlier tranches (2022–early 2023) were irregular.
- **What drives it:** Chainlink's stated 7% a year release schedule. No vesting contract: the lock is the Safes' signers. No published per-quarter schedule; the amounts are read from the chain.
- **Where it goes:** `0x9b1c1752…` is an EOA holding 0. It received 253.8M from the 33 and sent all 253.8M to `0x28C6c06298d514Db089934071355E5743bf21d60` the same day each time (no code; commonly labelled Binance 14, but Etherscan was behind a bot check and Blockscout shows no tag today: exchange identity NOT VERIFIED). Purpose (sale, OTC, custody) not established.
- **At this rate the 33 empty in about 3.3 years** (233.15M ÷ 70M, INFERRED; about 2030-Q1). Next by the pattern: about 2026-12-18, 11.25M.
- Before 2022: the legacy wallets paid out to `0x1f9e26f1…` (12.6M, 2019, EOA) and `0x6781a24c…` (54.5M, 2021, Safe), which in turn fed `0x9d6a86fa…`.

## (b) Circulating under Jake's rule

U = not yet vested or released. M = somebody can move it. E = earns.

| Leg | Balance | U | M | E | Rule |
|---|---|---|---|---|---|
| Burned | 0 | — | — | — | nothing to take out |
| 33 non-circulating wallets | 233,150,029.58 | yes (released at 7%/yr) | **yes: 27 Safes 3-of-5; four legacy multisigs; two EOAs (0 LINK)** | no (0 principal in either pool, block 26,278,435 and re-read today for the path wallets) | IN, off float |
| Reserve | 6,804,760.10 | no (bought with revenue) | **yes:** RBACTimelock; proposer MCMS `0x20d64e2a…` (22 signers) + 2-day delay, executor `0x41a46711…`; **bypasser MCMS `0x177a2884…` (22 signers) executes with no delay** | no | IN, off float (held buyback) |
| Staked v0.2 principal | 42,569,261.49 | no | stakers, after 28 days | yes | IN, off float (staked) |
| RewardVault | 3,101,347.24 (648,102 not yet emitted) | the unemitted part | admin timelock `0xf5df3d27…`; stakers claim the rest | — | IN, off float |
| `0xD50f074E…`, `0x9d6a86fa…`, `0x6781a24c…` | 10,754,541.61 | no (already released) | yes (Safes) | no | IN, off float |
| v0.1 legacy pool | 443,341.50 | no | holders, any time, no cooldown | no (v0.1 inactive) | IN, in float |

**No wallet meets all three tests.** Every LINK not released is in a multisig that its signers can move, so under the rule nothing leaves circulating. Implication: circulating is a constant 1,000,000,000; the release is not an unlock of circulating but a move from the issuer's off-float stock into the float — the same reading Jake took for Ether.fi (R14 decision 1 (a)).

### Result

| | Literal (the rule) | Schedule exclusion (Chainlink's own method) |
|---|---|---|
| Total supply | 1,000,000,000.00 | 1,000,000,000.00 |
| − the 33 wallets | 0 | 233,150,029.58 |
| **Circulating** | **1,000,000,000.00** | **766,849,970.42** |
| − staked (v0.2 principal) | 42,569,261.49 | 42,569,261.49 |
| − issuer, foundation, treasury (33 wallets, Reserve, vault, three path wallets) | 253,810,678.53 | 20,660,648.95 |
| **Free float** | **703,620,059.98 (70.4%)** | **703,620,059.98** |

- Not deducted, and why: ETF / ETP holdings (about 18M; third-party products), exchange wallets, v0.1 unclaimed LINK (users'), the operator and community pools' 5,243.64 LINK above principal (stray).
- Repo for comparison, today: circulating 760,045,210.32; float about 717.47M (it deducts the pools only). Its float is 13.85M higher than the rule's: the vault, `0xD50f074E…`, `0x9d6a86fa…`, `0x6781a24c…`.

### Reconciliation (all read 2026-11-20 09:2x UTC)

| Source | Circulating | vs literal 1B | vs schedule 766,849,970 |
|---|---|---|---|
| Chainlink, `supply.chain.link/supply` (and `/supply-basic`) | 766,849,970.42 | −23.3%: the 33 wallets, 233,150,029.58 (nine with a balance, listed in (a)) | 0.0% |
| CoinGecko API (total 1B, max 1B) | 766,849,970.42 | −23.3%: same 33 wallets | 0.0% |
| CoinMarketCap data API (total 1B, max 1B; no wallet list, `supplyDetails` gives max only) | 748,099,970.42 | −25.2%: the 33 wallets + 18,750,000 | −2.45%: **the 2026-10-09 release** (16,875,000 via `0x9b1c1752…`, 1,875,000 to `0xD50f074E…`; wallets `0x4a87ecE3…` 4.0M, `0x35a5dc3F…` 4.6M, `0xfB682b0d…` 4.0M, `0xa42D0A18…` 3.4M, `0x0DFfD343…` 1.25M, `0xe0b66bFc…` 0.6M, `0x8d34d66b…` 0.5M, `0x37398A32…` 0.4M). CMC's figure is the 33 wallets' pre-release balance to the token |
| Repo on-chain (1B − Reserve − 33) | 760,045,210.32 | −24.0%: 33 wallets + Reserve | −0.89%: the Reserve (under 2%; named anyway) |

## (c) Staking v0.2

Read at block 26,455,357 on the contracts named in Chainlink's public guide (`smartcontractkit/chainlink-staking-v0.2-public-guide`, instructions.txt).

| | Community pool `0xBc10f2E8…` | Operator pool `0xA1d76A7c…` |
|---|---|---|
| typeAndVersion | CommunityStakingPool 1.0.0 | OperatorStakingPool 1.0.0 |
| Cap (`getMaxPoolSize`) | 40,875,000 | 4,125,000 |
| Principal (`getTotalPrincipal`) | **40,875,000.00 (full)** | **1,694,261.49 (41.1%)** |
| LINK balance | 40,875,543.64 | 1,698,961.49 |
| Per-staker limits | 1 – 15,000 | 1,000 – 75,000 |
| Unbonding / claim window | 28 days / 7 days | 28 days / 7 days |
| Reward bucket (LINK/yr) | communityBase 1,767,011 | operatorBase 185,752 + operatorDelegated 73,625 |
| Rate | **4.323%** (Chainlink: 4.32%) | **15.31%** (underfilled pool) |
| Slashable | no | yes |

- **Blended rate:** 2,026,389 LINK/yr ÷ 42,569,261 = **4.760%**. Delegation rate 4% (`getDelegationRate` 400/10,000); ramp-up 90 days. All three buckets end **2027-03-17 04:55 UTC**.
- Owner of both pools, the vault and the alerts controller: timelock `0xf5df3d27…` (2-day `getMinDelay`). Chainlink's FAQ: "ministerial functions, such as adding rewards, can be called without a time delay".
- **Who funds the rewards: emissions from the 33 non-circulating wallets.** The vault's only funder is Safe `0x7e10a625…` (5-of-9), which received every LINK from `0x1661f928…` / `0x8716F86F…` ← `0x7A28b8cF…` ← `0xD50f074E…` ← the 33. Totals: 6,682,500 to the v0.2 vault (11 tranches of 607,500, 2023-11..2026-10-14) and 1,445,000 to v0.1. Latest: 607,500 forwarded 2026-10-07, paid in 2026-10-14. No Payment Abstraction recipient is the vault or a pool (checked against all 63 PAL recipients). Chainlink's own words: "emissions-based rewards will eventually trend toward zero as external sources … such as user fees, grow" (chain.link/economics/staking). So today 0% of rewards are fee-funded.
- Vault: 3,101,347.24 LINK = 648,102 not yet emitted (`getUnvestedRewards` 565,145 + 59,409 + 23,548) + about 2.45M accrued and unclaimed (INFERRED by difference).
- **Alerting and slashing:** PriceFeedAlertsController 1.0.0 `0x27484ba1…` secures the ETH/USD feed on Ethereum. A valid alert (feed down more than 3 hours; operators get 20 minutes' priority) slashes 700 LINK per operator and pays the alerter 7,000 LINK (FAQ). On chain: slasher config for the controller reads slashCapacity 21,700 LINK; the operator pool's `getAlerterRewardFunds` reads **0**. Whether an alert could be paid in full today is not established.
- Legacy v0.1 `0x3feB1e09…`: inactive since 2023-12, migration target set; still holds 443,341.50 LINK of unclaimed principal and rewards.

## (d) The LINK Reserve

| Item | Value | Source |
|---|---|---|
| Address | `0x9A709B7B69EA42D5eeb1ceBC48674C69E1569eC6`, RBACTimelock (Blockscout verified source) | Reserve blog links it |
| Balance | 6,804,760.10 LINK (block 26,455,357) | `balanceOf` |
| First inflow | 2025-07-31 | Blockscout |
| Outflows ever | one, 1 LINK (`0x176c2ee1…`, a test) | Blockscout |
| Held or distributed | **held**: "We do not expect any withdrawals from the Reserve for multiple years" | blog.chain.link/chainlink-reserve-strategic-link-reserve |
| Who can move it | proposer MCMS + 2-day delay; bypasser MCMS with no delay; three cancellers | `getRoleMember` reads |

What buys:

- Revenue arrives at the Fee Aggregator `0xd6e39d42…` ("Fee Aggregator 1.0.0") in fee tokens (CCIP fees, Payment Abstraction invoices, SVR share). It is swapped to LINK and paid into `0x5680681E…` ("Reserves 1.0.0", the Payment Abstraction contract), which forwards it to the Reserve.
- The converters, by LINK delivered into `0x5680681E…`: the Uniswap V3 LINK/WETH pool `0xa6Cc3C25…` (driven by "Uniswap V3 Swap Automator 1.0.0" `0x36e827ba…`) 4,447,721 to 2026 Q3, then "GPV2CompatibleAuction v1.0.0" `0x8f3b89e0…` 1,864,500 from 2026 Q3. Both owned by timelock `0xaa236c2b…`, which also owns the aggregator.
- Blog (2025-08-07): revenue from enterprise integrations and on-chain services, plus "50% of fees from staking-secured SVR services". No share for any other revenue is published.

Inflow, by quarter (LINK from `0x5680681E…`; dust excluded):

| 2025 Q3 | 2025 Q4 | 2026 Q1 | 2026 Q2 | 2026 Q3 | 2026 Q4 (to 11-19) | Total |
|---|---|---|---|---|---|---|
| 370,997 | 951,092 | 1,473,379 | 1,708,677 | 1,543,330 | 757,262 | 6,804,737 |

- Chainlink's Q3 2026 review (chain.link/blog/quarterly-review-q3-2026, 2026-10-21): "accumulated 1,543,330.47 LINK in Q3, bringing total Reserve holdings to 6,047,498.3150 LINK". My archive read on 2026-09-30: 6,047,498.39. **Matches.**
- Weekly on Thursdays; the last six: 89,162 / 88,444 / 92,789 / 94,524 / 106,253 / 128,908 LINK (2026-10-15..11-19).
- The Reserve takes only converter output. A second stream through the same `0x5680681E…` (from `0x9d6a86fa…`, from RBACTimelock `0x08558d8b…` and earlier Safe `0x77dd1a9b…`) goes to 63 other addresses: 2026 Q1 1,087,126; Q2 1,366,130; Q3 1,462,067; Q4 to date 601,753. The amounts in equal the amounts out each quarter. INFERRED: node-operator payments; `0x9d6a86fa…` is fed by released supply. Where `0x08558d8b…` gets its LINK was not traced.
- The dashboard metrics.chain.link/reserve renders in JavaScript; no figure readable from here.

## (e) Fees, revenue, holder revenue (S13)

DefiLlama, 90 days 2026-08-22..2026-11-19 (`api.llama.fi/summary/fees/<slug>`):

| Listing | Fees | Revenue | Holders revenue | Supply side |
|---|---|---|---|---|
| `chainlink` (parent) | $14,701,698 | $14,150,084 | $14,150,084 | $763,193 |
| `chainlink-staking` (the fee aggregator) | $14,127,332 | $14,150,084 | $14,150,084 | $188,827 |
| `chainlink-requests` | $574,366 | $0 | — | — |

What each includes (DefiLlama's methodology text): fees = "all the tokens received by the fee aggregator contract" + request payments; revenue = "LINK tokens transferred from the PaymentAbstractionLayer to the Reserve"; holders revenue = the same, tagged buyback. 365 days: fees $63.27M, revenue $59.78M. Token Terminal's page renders in JavaScript; no figure read.

| Line | Candidate | 90-day figure | Includes / misses |
|---|---|---|---|
| FEES | F1: DefiLlama parent fees | $14,701,698 | Ethereum aggregator only; misses CCIP 1.2/1.5, VRF, Automation (bypass the aggregator), Data Streams, off-chain |
| FEES | **F2: repo `customer_revenue_usd`** (aggregator core + the bypass lines, on chain, 5 chains) | not computed today (fixture ends 2026-10-12) | the repo's own A2 measure; still misses Data Streams, BNB/Avalanche, off-chain |
| REVENUE | R1: DefiLlama revenue | $14,150,084 | — |
| REVENUE | **R2: Reserve inflow from `0x5680681E…` × same-day price, on chain** (= repo `actual_buyback_usd`) | **$14,202,208** (1,223,041.53 LINK) | R1 to 0.4%; all revenue converted to LINK; misses revenue not yet converted and anything paid out before conversion |
| HOLDER REVENUE | H1: DefiLlama holders revenue (= revenue) | $14,150,084 | counts a held buyback |
| HOLDER REVENUE | **H2: R2, labelled "held by the issuer (Reserve), not distributed"** | $14,202,208 | revenue-funded LINK taken off the market and held |
| HOLDER REVENUE | H3: strict, revenue-funded LINK paid to holders | $0 | stakers are paid from released supply (c) |

Recommendation, in the shape of `REVENUE_SPLIT`:

- **FEES = F2** (customer revenue), cross-check F1 on the same days.
- **REVENUE = R2**, on chain; cross-check R1 (0.4% today).
- **HOLDER REVENUE = H2**, the held Reserve inflow (the repo's own Near pattern for held buybacks; `HOLDER_VALUE` already holds Chainlink at hold 1.0), with H3 = $0 stated beside it. Decision 4.
- **Holder share** = H2 ÷ R2 = 100% by construction (the repo's `DIVERGENCE` note already says so).
- **Other recipients:** node operators (request payments; and the release-funded payments of (d), which are not fees); stakers (paid from releases, emissions).

## (f) Insider wallets

Issuer side (all read at block 26,455,357; every one stakes 0 LINK in either pool — `getStakerPrincipal`, today for the rows marked *, block 26,278,435 for the 33):

| Wallet | Role | Tag | Type | LINK |
|---|---|---|---|---|
| the 33 of (a) | non-circulating supply | PUBLISHED (supply API) | 27 Safes 3-of-5, 4 multisigs, 2 EOAs | 233,150,029.58 |
| `0x9A709B7B…` * | Reserve | PUBLISHED | RBACTimelock | 6,804,760.10 |
| `0x996913c8…` | RewardVault | PUBLISHED | contract | 3,101,347.24 |
| `0xD50f074E…` * | release hop to staking / payments | INFERRED (funded only by the 33) | Safe 4-of-7 | 9,822,500.00 |
| `0x7A28b8cF…` * | forwarder (same-day) | INFERRED | Safe 3-of-6 | 0 |
| `0x9d6a86fa…` * | node-operator payments | INFERRED | Safe 3-of-8 | 900,576.13 |
| `0x1661f928…`, `0x8716F86F…` | staking hops | INFERRED | Safes 3-of-5, 3-of-6 | 0 |
| `0x7e10A625…` * | RewardVault funder; owner of v0.1 | INFERRED (owner of the PUBLISHED v0.1 contract) | Safe 5-of-9 | 0 |
| `0x9b1c1752…` * | release forwarder to the exchange wallet | INFERRED (receives only from the 33) | EOA | 0 |
| `0x6781a24c…` * | 2021 distribution Safe | INFERRED | Safe | 31,465.48 |
| `0x1f9e26f1…` * | 2019 distribution EOA | INFERRED | EOA | 0 |
| `0xf5df3d27…`, `0xaa236c2b…`, MCMS `0x20d64e2a…` / `0x177a2884…` / `0xf99af744…` | admin timelocks and multisigs | read from the contracts | contracts | not read |

- **Insider stake: 0 found.** None of the issuer-side wallets stakes. Node operators stake in the operator pool, but no source ties an operator address to Chainlink Labs. Not established.
- Market makers: none disclosed; none identified.

## (g) done_check on the fixture build

Built from the repo's 15 dumps (as of 2026-10-13); `done_check.py --verbose` gives **Chainlink: NOT READY, 8 lines**. This is the fixture, not Jake's live store.

| # | Line | Cause | Proposed fix |
|---|---|---|---|
| 1 | audit CP Comparability, BUSINESS 4: MISSING [D — ROUTE NOT FILLED] `ccip_token_value_usd` | Built 2026-10-19 (G8-4b, `fetch/ccip_value.py`); the fixture stops at 2026-10-13 and holds no rows | Re-dump the fixture from a store that has run `--seed ccip_value` (history capped at 270 days by the API). Nothing to change in code |
| 2 | Comparison, Subsidy cover (x): n/a, rule-3 suspect (+8 cells) | "no supply-side incentive (b) series on file" | Not a declared n/a: there is a (b) flow — release LINK paid to node operators through `0x5680681E…` (d). Wire it (decision 6), taken out of (e) per transfer as the staking funding already is |
| 3 | Comparison, Leakage (ii): n/a, rule-3 suspect | same | same |
| 4 | Comparison, Insider share of stake: n/a, "MISSING E — needs issuer wallet list" | the 33 wallets are crossed with the pools and hold 0 | Add the eight issuer-side wallets read today (all 0) and show a measured **0%** with "floor: no source ties a node operator to Chainlink Labs" (decision 5) |
| 5 | Comparison, Fees (S13), 90 days: "not yet split" | no `REVENUE_SPLIT["Chainlink"]` | add it per (e): F2 |
| 6 | Comparison, Revenue (S13), 90 days | same | R2 |
| 7 | Comparison, Holder revenue (S13), 90 days | same | H2, H3 beside |
| 8 | Comparison, Holder share (S13) | same | follows from 6 and 7 |

## Round 16 proposal

1. **Put Chainlink on the rule.** `RULE_IN["Chainlink"]`: circulating = `totalSupply()`; off the float the 33 wallets, the Reserve, the RewardVault and the three path wallets (`0xD50f074E…`, `0x9d6a86fa…`, `0x6781a24c…`, INFERRED); stake = both pools' principal. Add `supply.chain.link/supply` as the first-party reference row (it is readable). *Unblocks:* circulating, market cap, float and every ratio over them; the CoinGecko / CMC / Chainlink rows.
2. **Add `REVENUE_SPLIT["Chainlink"]`** as in (e). *Unblocks:* done_check lines 5–8.
3. **Name the two release legs that leave the issuer.** (i) Node-operator payments through `0x5680681E…` (the non-Reserve stream) as outflow (b), out of (e) per transfer like `STAKING_FUNDING_SPLIT`; (ii) the `0x9b1c1752…` → exchange-wallet leg as a named float outflow, purpose "not established". *Unblocks:* lines 2–3, Subsidy cover, Leakage (ii).
4. **Refresh the staking and release records.** Reward end 2027-03-17 and the 2026-10-14 top-up (`reward_vault_topup`); `lock_model.cooldown_days = 28` (on-chain `getUnbondingParams`); the 7%-a-year cycle as a MODELLED forward release (next about 2026-12-18, 11.25M); insider-stake wallets extended (line 4); CMC marked stale by one release.
5. **Clear the fixture and freeze.** Re-dump with `ccip_token_value_usd` rows (line 1), re-run done_check, then add Chainlink to `FROZEN_PROJECTS`.

## Decisions for Jake

1. **The 33 wallets.** (a) Literal: 3-of-5 Safes can move them, so they are in circulating and off the float; circulating 1,000,000,000. (b) Schedule exclusion, as Chainlink and CoinGecko: 766,849,970. **Recommend (a)**, as for Ether.fi (R14 decision 1 (a)), with Chainlink's figure shown beside it; float is identical either way.
2. **The Reserve.** (a) In circulating, off the float (held buyback; the bypasser multisig can move it with no delay). (b) Keep it out of circulating as locked, on Chainlink's "no withdrawals for multiple years". **Recommend (a)**: the rule names held buybacks as off-float, not out of circulating, and the lock is a statement, not a contract.
3. **Path wallets and the vault off the float** (`0xD50f074E…` 9.82M, `0x9d6a86fa…` 0.90M, `0x6781a24c…` 0.03M INFERRED; vault 3.10M PUBLISHED). (a) Off the float as issuer-held. (b) Leave in the float until Chainlink names them. **Recommend (a)**; the trace is complete (each is funded only by the 33 or by each other).
4. **Holder revenue.** (a) The held Reserve inflow ($14.20M, 90 days), labelled held. (b) Strict: $0, nothing reaches holders. **Recommend (a)** with (b) stated beside, matching the Near pattern and `HOLDER_VALUE`.
5. **Insider share of stake.** (a) Measured 0% on the issuer wallets on file, labelled a floor. (b) Keep MISSING E until Chainlink names its own staker / operator addresses. **Recommend (a).**
6. **Node-operator payments from released supply.** (a) A (b) outflow (supply-side incentive), out of (e) per transfer. (b) Leave inside (e). **Recommend (a)**; it is what those payments are, and it gives Subsidy cover a denominator.
7. **Fees.** (a) DefiLlama parent, $14.70M. (b) The repo's `customer_revenue_usd` (aggregator + bypass lines). **Recommend (b)**, (a) as the cross-check.
8. **The exchange leg (86% of each release to `0x28C6c062…` via `0x9b1c1752…`).** (a) Record as a named float outflow, purpose "not established". (b) Leave inside the general release. **Recommend (a)**, as for Ether.fi decision 9.

## Not established

- The identity of `0x28C6c062…` as Binance (Etherscan behind a bot check; Blockscout no tag) and what happens to LINK sent there (sale, OTC, custody).
- Who controls `0x9b1c1752…`, `0xD50f074E…` and `0x9d6a86fa…`; no Chainlink source names them.
- Whether the 63 recipients of the non-Reserve stream are node operators (INFERRED from amounts and timing), and where `0x08558d8b…` gets its LINK.
- Whether a valid alert could be paid today (`getAlerterRewardFunds` reads 0).
- Off-chain revenue not converted to LINK, Data Streams revenue, SVR's share by month: no public series.
- A per-quarter release schedule from Chainlink: only "7% a year" is published; the cycle is read from the chain.
- Node-operator stake by Chainlink Labs affiliates.
- Whether Jake's live store already clears done_check line 1.

## Sources (all read 2026-11-20)

- Chain: ethereum-rpc.publicnode.com (block 26,455,357: balances, Safe `getThreshold` / `getOwners`, staking reads, RBACTimelock roles, MCMS `getConfig`); eth.drpc.org (archive `eth_call` at quarter ends and 2026-08-22, 2026-09-30, 2026-10-13, 2026-11-19)
- Blockscout: https://eth.blockscout.com/api (`module=account&action=tokentx`, LINK, the 33 wallets and the path wallets, the Reserve, `0x5680681E…`) and `/api/v2/addresses`, `/api/v2/smart-contracts/0x9A709B7B…` (RBACTimelock source); Ethplorer `getAddressHistory` for `0x9b1c1752…`
- Chainlink: https://chain.link/circulating-supply ; https://supply.chain.link/supply and /supply-basic ; https://chain.link/economics/staking ; https://blog.chain.link/chainlink-reserve-strategic-link-reserve/ ; https://chain.link/blog/quarterly-review-q3-2026 ; https://raw.githubusercontent.com/smartcontractkit/chainlink-staking-v0.2-public-guide/main/instructions.txt
- CoinGecko: https://api.coingecko.com/api/v3/coins/chainlink . CoinMarketCap: https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail?slug=chainlink
- DefiLlama: https://api.llama.fi/summary/fees/chainlink (and `chainlink-staking`, `chainlink-requests`; dataType dailyFees, dailyRevenue, dailyHoldersRevenue, dailySupplySideRevenue); prices https://coins.llama.fi/chart/coingecko:chainlink
- Not readable: metrics.chain.link/reserve (JavaScript), Token Terminal (JavaScript), etherscan.io (bot check); publicnode and the free tiers of other RPCs refuse wide `eth_getLogs` ranges
- Repo: `config.py` (`PROJECT_BY_NAME["Chainlink"]`, `CIRCULATING_ONCHAIN`, `_NONCIRC_WALLETS`, `_NONCIRC_WALLETS_FIRST_PARTY`, `CL_STAKING_PATH`, `STAKING_FUNDING_SPLIT`, `INSIDER_SHARE_ROUTES`, `HOLDING_CAPS`, `REVENUE_TIER`), `SPEC.md`, `done_check.py`, `project_freeze._build`

## Round 18 check against Chainlink's 2026 announcements (read 2026-11-23, 12:01–12:20 UTC)

Nothing below changes a decision. Decisions 1–8 were wired as recommended (SPEC "Round 18 — Chainlink"; `config.py` "R18 — CHAINLINK").

| Source (fetched 2026-11-23) | What it says | Against the pack |
|---|---|---|
| https://supply.chain.link/supply | circulating 766,849,970.42; total 1,000,000,000; 33 wallets | unchanged, to the token |
| https://chain.link/economics | "767 M+" circulating, "42 M+" staked, "5 M+ LINK in Reserve" (the page lags: 6.80M on chain); lists Reserve, Payment Abstraction, Staking, Scale, SVR — Build is no longer listed | no change |
| https://chain.link/economics/staking | emissions-based rewards "will eventually trend toward zero as external sources … such as user fees, grow"; 28-day cooldown, 7-day claim window | unchanged: stakers are paid from released supply |
| https://chain.link/blog/quarterly-review-q1-2026 (2026-04-28) | "The Chainlink Reserve added 1,473,379 LINK in Q1, bringing the total now to 3.06M LINK" | Q1 inflow matches the chain to the token (1,473,379.36); "3.06M" is the balance at the review's date, not at quarter end (2,795,468) |
| https://chain.link/blog/quarterly-review-q2-2026 (2026-07-24) | "accumulated 1.44M+ LINK in Q2, bringing total Reserve holdings to 4.5M LINK"; "The Build program is evolving" | the total matches (4,504,145 on chain at 06-30); the chain's calendar-Q2 inflow is 1,708,676.65 — Chainlink's "1.44M+" is 4.50M less the Q1 review's 3.06M. The pack's on-chain figure stands |
| https://chain.link/blog/quarterly-review-q3-2026 (2026-10-21) | "accumulated 1,543,330.47 LINK in Q3, bringing total Reserve holdings to 6,047,498.3150 LINK"; SVR $24.4M+ recaptured in total | matches the chain to the token |
| https://metrics.chain.link/api/reserve/latest (the Reserve page's own API; the page itself renders in JavaScript) | balance 6,804,760.0955 LINK; 98 inflows, every one from 0x5680681E…; first 2025-08-07; cost basis $11.2044 a LINK | equals `balanceOf` at block 26,477,750. The pack's "first inflow 2025-07-31" is wrong: the first is 2025-08-07 (the flag's window start) |
| https://chain.link/blog/build-program-evolution (2026-06-26) | Build payments in project tokens end; "the most recent season of Chainlink Rewards will mark the conclusion of Build-related token rewards, with claims ending on July 7, 2026"; about $20M of Build tokens were made available to stakers in all; new agreements pay "fees paid in LINK or other liquid assets that can be readily converted to LINK", proceeds "expected to be programmatically converted to LINK and used to support the growth of the network through programs such as the Chainlink Reserve" | strengthens decision 4's strict reading (nothing but released LINK now reaches stakers). On chain no LINK has reached the Reserve except through 0x5680681E… (6,804,737.24) and 24.8 LINK of dust from 17 senders, so deals paid in LINK are inside the measured inflow or have not arrived. "Expected" and "such as" commit no share |
| https://chain.link/circulating-supply | "Token release schedule is currently 7% of the total supply per year" | unchanged |

Could not be fetched: https://chain.link/blog/quarterly-review-q4-2025 (404), https://chain.link/reserve (404; the link is reserve.chain.link → metrics.chain.link/reserve), chain.link/economics/build, /economics/scale, /economics/payment-abstraction (404).

On chain the same day (block 26,477,750, 2026-11-23 12:11 UTC): the 33 wallets 233,150,029.58; Reserve 6,804,760.10; RewardVault 3,093,999.14; 0xD50f074E… 9,822,500.00; 0x9d6A86Fa… 900,576.13; 0x6781a24C… 31,465.48; principal 42,569,261.49; free float 703,627,408 (70.4%); `getStakerPrincipal` 0 for the 33 and nine other issuer-side wallets; reward buckets end 2027-03-17.

Three "not established" items of the pack that are now read:

- **0x9B1c1752… and 0x28C6c062….** Blockscout's token-transfer metadata tags 0x9B1c1752… "Binance: Deposit Address" and 0x28C6c062… "Binance: Hot Wallet" / "Binance 14". A label is evidence, not proof; whose Binance account it is and what happens to the LINK is still not established.
- **Where RBACTimelock 0x08558d8b… gets its LINK.** All of it (1,307,309.20) from 0x9d6A86Fa…; it has paid 1,242,495.28 into 0x5680681E…. Safe 0x77dd1a9b… took 2,412,435 of its 2,762,902 LINK from 0x9d6A86Fa… and, beside the 613,880 it paid into 0x5680681E…, paid 2,082,892 directly to the same recipient addresses (not in outflow (b); a decision for Jake).
- **The Reserve page.** Readable through its API (above).

Still not established: that the 63 recipients are node operators (labelled INFERRED); 0x9d6A86Fa…'s full outflow history (Blockscout's account API stops at 10,000 rows: 2021-08..2022-08 only).
