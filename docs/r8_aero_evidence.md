# Aero (AERO): evidence under the owner's final rule

Read 2026-11-17. On-chain reads are my own `eth_call`s at **Base block 58,602,907 (10:22 UTC)** and **Optimism block 159,656,128 (10:23 UTC)** via publicnode. "M" = million AERO. Builds on `pack_aerodrome.md`; nothing in the repo was changed.

## Headline

- **Circulating 2,141,501,028.60. Free float 983,406,269.50 (45.9%).**
- **Nothing issuer-side is OUT.** No wallet meets all three conditions; every issuer position is circulating and off free float.
- **Burned: 407,815.83 AERO, verified** (the pack said none). Two `burnFees` burns from the sAERO escrow; they equal the supply shortfall the pack could not explain, to the wei.
- **Issuer stake 503,094,190.55 = 46.67% of all sAERO** (1,078,003,471.89). It earns: 1,445,923 USDC was paid into the issuer Relays since launch.
- **CoinMarketCap changed its basis.** It now shows 753.02M (total − sAERO − AERO reserve), not the 1,065.8M the owner read.
- **The unstaked-LP share is not ~29% on DefiLlama's figures:** 21.5% (30 days), 23.3% (90 days).

## Part 1: issuer positions, wallet by wallet

| Wallet | Liquid AERO | Staked (direct + Relay principal) | Earns? | Class |
|---|---|---|---|---|
| Team / Foundation `0xBDE0…EA5a` | 4,064,090.02 | 208,468,750.36 (Relay `0x6B1C…119C`; 29 empty sAERO) | Yes: 689,031.97 USDC notified, all claimed, tx `0xfa67d915…5855`, block 58,354,101 | circulating, off float |
| Public Goods / Momentum Fund `0x834C…Da52` | 1,668,440.75 (+537,615.33 legacy) | 194,738,593.59 = Relays 193,780,936.11 + direct 957,657.48 | Relays yes: 548,707.80 USDC claimed (tx `0x4ef23713…6c4c`, `0xc8288c36…726b`, `0x2027e41e…7115`, blocks 58,368,922 to 58,369,661). Direct stakes no (see below) | circulating, off float |
| Flight School `0x51E1…88E4` | 2,031,043.47 | 67,358,370.77 (three Relays) + 1 legacy veNFT, 2,016.88 | Yes: 206,972.79 USDC claimable, not yet claimed (`claimable()` at the block) | circulating, off float |
| Velodrome Foundation airdrop `0x5b18…1431` | 278,732.01 | 32,528,475.83 = direct 30,830,808.16 + Relay 1,697,667.68 | Relay yes: 1,210.48 USDC notified, 2,969.85 AERO compounded (tx `0x9723c7b0…4f9b`). Direct: 29.34M allocated 2026-10-22 on up to 5 chains, no claim dispatched yet; 1.49M parked | circulating, off float |
| TGE incentives `0x7269…9dDb` | 28,941,560.34 | 0 | Spent as incentives: 119 programmes, about 303K AERO since launch | circulating, off float |
| Buyback / Locked Funds `0x623C…57a1` | 0 (741.21 legacy) | 0 | n/a | empty |
| AirdropDistributor `0xE4c6…382F` | 0 | 0 | n/a | empty |
| Buyer Safe `0x10b42e92…fa30` | 0 | 0 (2 empty sAERO) | n/a | empty |
| Splitter `0x9beb95cc…1156` | 826,287.42 | 0 | Unclaimed 5% team share; `claim()` is open to its two recipients | circulating, off float |
| **Sum** | **37,810,154.01** | **503,094,190.55** | | |

- **Splitter recipients** (`allRecipients()`): 50% to `0xc59b7390…ea5f` (a contract, holds nothing) and 50% to the Public Goods Fund; 413,143.71 each is claimable. `0xc59b…` is an issuer-side address not yet in config.
- **Relays.** 18 Relays in 1 factory. Seven hold issuer principal; each owns one permanent sAERO, allocated between 2026-10-23 and 2026-11-16 (`Voter.tokenStates.lastAllocated`). No pending deposits name an issuer wallet.
- **Two public (Maxi) Relays** hold 17.77M and 62.30M with no issuer principal. They are holders' stakes and sit inside sAERO.
- **Protocol-owned buyback locks.** None separate. Bought AERO sits inside the Public Goods Fund's stakes. sAERO token 0 (the protocol accumulator) holds 0.
- **Parked stakes, the one borderline case: 2,444,830.34.** PGF stakes #23940 and #23941 (957,657.48, to 2030) and Velodrome Foundation #23938 and #23939 (1,487,172.89) show `lastAllocated` 0 and are parked on chain 0. They cannot be moved and are not allocated today. I kept them circulating and off float because the holder can allocate them at will and they were released tokens. If the owner reads them as OUT, circulating falls by 2.44M (0.11%); free float is unchanged.

**Burned.** `VotingEscrow.burnFees`: 174,600.636 AERO (tx `0xe00ba4ce…6a33`, block 53,215,285) and 233,215.198 AERO (tx `0xf800fc31…caf8`, block 53,299,338). Total 407,815.834. Check: 2,127,000,000 + 16,525,748.34 minted × 1.05 − 407,815.83 = total supply exactly. 0xdead holds 0.0014.

**Legacy veNFTs not upgraded** (legacy VotingEscrow `0xeBf4…e6B4`, all 132,768 ids read):
- 64,514 veNFTs remain, held by 38,176 owners.
- Locked: 41,834,329.10 by the escrow's ledger; 38,503,486.44 by its token balance net of the migration's own lock (1,697,059,711.94). I use the token balance. The 3.33M difference is the known ledger-versus-balance gap.
- Issuer-side: one, Flight School #17385, 2,016.88 AERO, permanent.
- Largest holder `0xc9814f18…f14f`, 9 veNFTs, 10,488,135.00. Identity not verified.

## Part 2: the numbers at one block

| Line | AERO |
|---|---|
| New total supply (burns already removed) | 2,143,944,219.92 |
| less AERO-side swap reserve `0x22a2…6721` | −312,940,289.06 |
| less Velodrome-side swap reserve `0x6dae…66c0` | −20,467,891.56 |
| plus unswapped legacy AERO, non-issuers, ×1 | +310,497,097.74 |
| plus unswapped VELO, ×0.044, capped at its reserve | +20,467,891.56 |
| **Circulating** | **2,141,501,028.60** |
| less all sAERO (issuer 503.09M + others 574.91M) | −1,078,003,471.89 |
| less legacy veAERO locks, non-issuer | −38,501,469.56 |
| less legacy veVELO locks ×0.044 | −3,779,663.65 |
| less issuer liquid (table above) | −37,810,154.01 |
| **Free float** | **983,406,269.50** |

- **Unswapped legacy AERO.** Legacy total 2,008,099,184.24 − migrated 1,697,059,711.94 (`previewConversion` basis of the migration's lock) = 311,039,472.30. Less issuer-held 540,373.42 (PGF 537,615.33, Buyback 741.21, Flight School veNFT 2,016.88) and 2,001.14 at 0xdead = 310,497,097.74. The reserve exceeds this by 2,443,191.32, which stays out.
- **Unswapped VELO.** VELO total on Optimism 2,663,160,472.60 − migrated 2,193,911,556.47 = 469,248,916.13 VELO, ×0.044 = 20,646,952.31. That is 179,060.75 above the reserve, so I capped it at the reserve.
- **VELO coverage.** Covered: the canonical VELO token and veVELO on Optimism. Not covered: issuer-held VELO (wallets not identified, so the figure is gross), VELO v1 (2,019,010,601.88 supply), and bridged VELO on other Superchain chains (assumed backed by Optimism supply; not verified).
- **Held buybacks:** none liquid. They are inside the PGF stakes.

**Reconciliation**

| Source (read 2026-11-17) | Figure | Their formula, tied to my reads | Ours minus theirs |
|---|---|---|---|
| CoinGecko `aero` | 724,166,549.93 (owner read 723.4M) | its total 2,135,578,125 − sAERO − both reserves = 724.17M | +1,417.33M |
| CoinMarketCap, now | 753,016,672.62 | total − sAERO − AERO reserve = 753.00M | +1,388.48M |
| CoinMarketCap, owner's read | 1,065.8M | total − sAERO = 1,065.94M | +1,075.56M |

Gap legs (each sums to the gap):
- **CoinGecko, +1,417.33M:** sAERO +1,078.00M (issuer 503.09M, others 574.91M); unswapped legacy AERO +310.50M; unswapped VELO +20.47M; CoinGecko's stale total +8.37M.
- **CoinMarketCap now, +1,388.48M:** sAERO +1,078.00M; unswapped legacy AERO +310.50M; their later total −0.02M. The Velodrome leg nets to zero.
- **CoinMarketCap at 1,065.8M, +1,075.56M:** sAERO +1,078.00M; reserve above non-issuer legacy −2.44M.

Every gap is a basis difference; no wallet is missing.

## Part 3: S13 revenue lines

**DefiLlama, merged basis** (parent `parent#aero-protocol`; slugs `aero-protocol`, `aero` and `aerodrome` return the same series from 2022-06-01), days to 2026-11-16:

| Window | Fees | Revenue | Holders revenue | Supply side | Supply side ÷ fees |
|---|---|---|---|---|---|
| Last 30 days | $9,860,590 | $7,741,737 | $7,446,428 | $2,118,866 | 21.5% |
| Last 90 days | $40,066,189 | $30,746,715 | $30,451,406 | $9,319,493 | 23.3% |

- **Children:** Velodrome V1 `velodrome-v1` (1799), Velodrome V2 `velodrome-v2` (3302), Velodrome V3 `velodrome-v3` (4249), Aerodrome V1 `aerodrome-v1` (3450), Aerodrome Slipstream `aerodrome-slipstream` (4524), Aero Slipstream `aero-slipstream` (8691), Aero Basic `aero-basic` (9172). Aerodrome Ignition (6942, launchpad) has no fees series. The children sum to the parent in both windows.
- **Ordering:** fees ≥ revenue ≥ holders revenue on all 90 days.
- **Unstaked-LP share:** 20.2% since 2026-10-22, 24.1% in the 64 days before. On the new pools alone: Aero Slipstream 8.6%, Aero Basic 3.3%. The denominator includes incentives, so the share of swap fees alone is slightly higher. ~29% is not confirmed.
- **Revenue minus holders revenue, 30 days: $295,309.** These are fees locked in the dead legacy gauges.
- **Caveat:** DefiLlama counts all incentives, including Foundation-funded ones.

**Foundation-funded incentives, measured on Base** (`incentive()` on all 313 reward managers): 244 programmes, $415,996 at today's prices (1 unpriced). The TGE wallet `0x7269…` created 119 of them, $132,757 (31.9%). These come out of FEES and REVENUE. Leaf chains not read.

**How to measure each line on-chain (v3)**

| Line | Source |
|---|---|
| Fees to stakers | `NotifyFeesAmount(gauge, token, amount)` on each reward manager; managers from `VotingRewardsCreated` on factory `0x8fbA…7110` |
| Incentives | `IncentiveCreated(programId, token, creator, amount, start, duration)`, or `incentiveCount()` / `incentive(id)`; `creator` gives the Foundation split |
| Unstaked-LP fees | Not emitted on the staker side; needs pool-level fee reads or DefiLlama supply side |
| Insider share | Relay principal + direct stakes ÷ sAERO supply (46.67% today) |
| Emissions (b) | `Minter.emissionsMinted()` delta × price |
| Leaf chains | Same events on each chain's LeafVoter stack; not read today |

I did not total one fee epoch today: publicnode limits `eth_getLogs` to 10 addresses and 50,000 blocks, and refuses topic-only scans. Not verified on-chain: the fee total, the unstaked-fee cut.

**What the store export holds**

| Line | Metric | Range | State |
|---|---|---|---|
| FEES | `fees_usd` (DefiLlama slug `aerodrome`) | 2022-06-01 to 2026-11-16, 1,630 rows | Already the merged, restated series; equals the parent to the dollar. Includes Foundation incentives |
| REVENUE | `revenue_usd` | same | Same basis |
| HOLDER REVENUE | `holders_revenue_usd` | same | Gross; no insider share stored |
| On-chain check | `voter_rewards_onchain_usd`, `_fees_usd`, `_bribes_usd`, `_apr`, `_unpriced_count` | 16 weekly rows, 2026-07-02 to 2026-10-15 | Legacy only; no v3 epoch stored; 4 gap rows today |
| Emissions (b) | `gross_issuance_tokens`, `aero_v3_emissions_minted_cumulative`, `issuer_mint_tokens` | to 2026-11-17 | Present |

Missing: a Foundation-funded incentive series; the unstaked-LP fee line; insider share (`filing_managed_lock_tokens` stops 2026-10-29); `REVENUE_SPLIT["Aerodrome"]`; a v3 epoch row. `defillama_protocol_parts` still excludes Velodrome V1 to V3 and omits Aero Basic (9172), so TVL and DEX volume are not on the merged basis while fees are.

**Other store facts**
- `circulating_supply` (CoinGecko) still follows the legacy listing: 272,551,970.81.
- `total_supply`, `noncirculating_holding_tokens` and `locked_tokens` stop at 2026-11-11.
- Today's run logged `filing_managed_lock_tokens` failed at block 58,582,561: "Multicall3: call failed". Which call reverts is not verified.

## Not verified

Issuer-held VELO; VELO on other chains; the identity of `0xc59b…` and `0xc9814f…`; accrued rewards on the Velodrome Foundation's direct stakes; on-chain fee totals and the unstaked-fee cut; leaf-chain incentives; why the two public Relays' backing is below their stake (1.89M and 2.38M).

## Sources (all read 2026-11-17)

- Base `https://base-rpc.publicnode.com`, block 58,602,907; archive blocks 52,918,950 / 53,165,286 / 55,360,321; logs from block 52,880,070.
- Optimism `https://optimism-rpc.publicnode.com`, block 159,656,128.
- Contract sources: `Dromos-Labs/metadex-public@8b506b45` (IVoter, IVotingEscrow, IRelay, IIncentiveStreaming, IFeeDistribution, Splitter, Migration, VelodromeMigration, deployment-addresses).
- `https://api.coingecko.com/api/v3/coins/aero`; `https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail?slug=aerodrome-finance`.
- `https://api.llama.fi/protocols`; `https://api.llama.fi/summary/fees/<slug>?dataType=dailyFees|dailyRevenue|dailyHoldersRevenue|dailySupplySideRevenue`; `https://coins.llama.fi/prices/current`.
- Store export `/tmp/claude-0/-home-user-Crypto-Tracker/e84ff2c6-8546-5133-82ff-742643596bbf/scratchpad/ex8/aerodrome-2026-11-17-092512-7340372.json.gz`.
- Repo: `/home/user/Crypto-Tracker/fetch/aero_v3.py`, `/home/user/Crypto-Tracker/aero_migration.json`, `/home/user/Crypto-Tracker/config.py` (lines 12958 to 13140, 18852, 19401, 36695 to 36742).
- Working files: `/tmp/claude-0/-home-user-Crypto-Tracker/e84ff2c6-8546-5133-82ff-742643596bbf/scratchpad/r8/` (s1.json, s2.json, s3.json, s5.json, s7.json, logs_*.json).
