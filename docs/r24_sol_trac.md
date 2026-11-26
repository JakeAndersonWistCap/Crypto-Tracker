# Round 24 — Solana (SOL) and OriginTrail (TRAC)

Per project: cell | value | source | status, then the open gaps. Detail: docs/r24_solana_settlement.md.

## Solana (SOL) — R24, A1 Infrastructure + A4 Permanent burn (A3 does not apply)

Read 2026-11-26 from the keyless public RPC (`api.mainnet-beta.solana.com`, via `fetch/solana.SolanaRPC`, methods added; tier
`solana_rpc`) and DefiLlama. Dump: `fixtures/real_solana.json` (flag `asof_exempt`: a later read than the 2026-10-13 vintage the
frozen projects are pinned to, so it does not set the build's as-of; `tests/real_dump.build_asof`). **Every chain read is
FORWARD-ONLY from 2026-11-26** (the public RPC serves no past supply, stake or rewards); nothing was back-filled or invented.
The burn has a year of history (DefiLlama burned-fee revenue / price).

| Cell | Value | Source | Status |
|---|---|---|---|
| Circulating supply | 592,183,208 SOL | the issuer's own figure = `getSupply.circulating` (Agave non-circulating rule); CoinGecko carries the same number to 7 digits (592,183,197.8 on chain) | measured (issuer figure) |
| Total supply | 638,224,669 SOL | `getSupply.total` | measured, FORWARD-ONLY |
| Non-circulating | 46,041,471 SOL in 3,194 accounts | `getSupply.nonCirculating`, account list (`excludeNonCirculatingAccountsList=false`) | measured, FORWARD-ONLY |
| Staked (all) | 423,675,927 SOL (657 current + 31 delinquent vote accounts) | Σ `activatedStake`, `getVoteAccounts` | measured, FORWARD-ONLY |
| Staked inside the non-circulating accounts | 44,795,667 SOL | `getMultipleAccounts` (jsonParsed), active delegations only | measured, FORWARD-ONLY |
| Staked and counted in circulating | 378,880,260 SOL = 63.98% of circulating | the two lines above, a token comes off once | derived |
| **Free float** | **213,302,948 SOL** = circulating − staked in circulating | `free_float.py`, one series | derived; Foundation leg GAP (below) |
| Inflation rate in force | 3.5397% a year (epoch 1097); governor 8% initial, 15% taper, 1.5% terminal | `getInflationRate`, `getInflationGovernor` | measured |
| Gross issuance | **57,574 SOL/day** (21.0M a year, 3.29% of total) | EpochRewards sysvar `total_rewards` 61,874.88 SOL for epoch 1096 ÷ its measured 1.0747 days (first-block times) — ACTUAL rewards the chain created, not rate × supply | measured, FORWARD-ONLY (1 day stored) |
| Issuance, rate × supply / 365 (the brief's computation) | 61,900 SOL/day | 3.5397% × 638.22M / 365 | computed from the chain's reported rate; NOT stored — the 7% gap is the nominal 400 ms slot the rate assumes against the ~215 ms slots measured |
| Gross burn | 466 SOL/day on 2026-11-25 (715/day mean of the last 90 days: 64,324 SOL) | DefiLlama burned-fee revenue (`fees/solana.ts`: 50% of base fees; priority fee all to the leader since SIMD-0096, 2025-02-12) ÷ the day's price; 364 days (price starts 2025-11-27) | derived from a sourced series; the Ethereum pattern |
| Burn observed on chain | 349 SOL/day | fall of `getSupply.total` over a 45 s window inside one epoch, scaled to a day | **MODELLED** to a day; one window; cross-check only (same order as the 466) |
| Burn ÷ issuance | 0.8% (466 ÷ 57,574); 1.2% on the 90-day burn mean | the two lines above | the workbook cell reads **blocked** (unequal coverage: issuance 1 of 90 days) until the daily reads accumulate |
| Burn share of fees | 11.0% (90 days) | burn ÷ fees_usd | derived |
| Fees / revenue | fees $61.2M, revenue (the burn) $6.7M, 90 days | DefiLlama `solana` chain adapter | measured |
| DEX volume | 1,878 days from 2021-10-02; trailing 365 days $951.2bn | DefiLlama `/overview/dexs/solana` | measured |
| Settlement (A1 key) | — | DEX leg wired; P2P transfer leg has no free source | **GAP: settlement: P2P transfer leg has no free source — Jake decides on billing** (`docs/r24_solana_settlement.md`) |
| Archetype key / rank | n/a / unranked | needs settlement | GAP, as above |
| Net holder yield | 0.047% (= holder value yield; total dilution n/a) | A4 burn yield on market cap | dilution blocked while issuance covers 1 of 90 days |

### The non-circulating set, by what it is (Agave `non_circulating_supply.rs`, anza-xyz/agave master, read 2026-11-26)
The rule: a fixed list of 214 accounts, plus every stake account whose lockup is in force or whose withdrawer is one of 114 listed
"autostake" withdraw authorities. The 3,194 accounts on chain split exactly into three classes (no third-party labels used):

| Class | Accounts | SOL | What it is |
|---|---|---|---|
| A. fixed list | 214 (97 hold SOL) | 2,163,633 | system-named accounts in the client's list; largest `EMAY24Pr…3Wup` 537,553, `5D5NxsNV…hQrhNW` 433,661, `EziVYi3S…VEn7` 427,967, `Br3aeVGa…Mtmt` 401,331, `6o5v1HC7…2Suy` 240,690 |
| B. autostake withdraw authority | 2,242 | 27,387,072 | withdrawer `4ZJhPQAg…kbPY` 669 accounts 27,383,419; `HXdYQ5gi…RWE` 1,572 accounts 3,653; `4e6KwQpy…1EA` 1 account 1 |
| C. lockup in force | 738 | 16,490,766 | custodians `3XdBZcUR…Gxg` 467 accounts 8,994,887 (unlocks monthly 2026-12-07 to 2028-01-07); `APfu848V…5Bh` 228 accounts 5,390,039 (2026-11-28 to 2028-09-13); `BfFYshuf…zo` 10 accounts 2,044,402 (2026-12-11 to 2027-09-11); `8DE8fqPf…jC` 4 accounts 61,234; others 11 accounts |

By Jake's rule: C is unvested/locked and stays OUT (it releases as the lockups expire; each release is new supply when it moves).
A and B (29.55M SOL) are in the issuer's non-circulating figure, and nothing on chain or in a Foundation document identifies them
as Foundation-held and transferable, so they stay out as the issuer states. Decision 2 below.

### Open gaps (never zero)
1. **Foundation liquid holdings off the float**: the Solana Foundation publishes no wallet list (its transparency page is dated 2020, no
   wallet attribution; searched 2026-11-26). Not subtracted from the free float; labelled gap (`config.SOLANA_FOUNDATION_GAP`).
2. **FTX estate unlocks**: no primary source says which lockups are the estate's. On chain, the 738 lockups above unlock monthly to 2028
   (`config.SOLANA_FTX_GAP`); who owns them is not on chain.
3. **Settlement P2P leg** (A1 key): docs/r24_solana_settlement.md.
4. **History of every chain series** (supply, stake, issuance, rate, non-circulating): forward-only from 2026-11-26; Burn ÷ issuance
   and Net issuance after burn are blocked until the issuance covers the window.
5. **Proposals** (label only, nothing wired): SIMD-0411 (30% taper), SIMD-0228, SIMD-547 — none live: the chain's governor taper reads
   0.15 on 2026-11-26; SIMD-0228 failed its March 2025 vote (61.6% < 66.67%); the SIMD repository's own status fields could not be read
   from this host (GitHub API 403), so any status beyond "not enacted" for 0411 and 547 is NOT VERIFIED. SIMD-0096 is live (2025-02-12).
6. Active addresses and transaction count: no free daily source wired (n/a).

### Decisions for Jake
1. **Settlement P2P billing** — recommend authorising ONE free BigQuery dry run to price a day, then deciding; until then the key stays a gap.
2. **A and B (29.55M SOL)**: keep out (issuer's figure) — recommended; if you want Jake's rule applied to them (Foundation-held,
   transferable → circulating, off float) a primary source naming the withdrawers as Foundation-controlled is needed first.
3. **Burn ÷ issuance**: accept "blocked" until ~30 days of issuance accumulate — recommended — versus a MODELLED issuance history
   (rate × supply, from the governor's schedule), which mixes two measuring points and is not recommended.

# R24 - OriginTrail (TRAC), A2 Coordination

Read 2026-11-26 from keyless public RPCs (Ethereum, Base, Gnosis, NeuroWeb), Sourcify, OriginTrail's docs and the
DefiLlama / CoinGecko public APIs. The workbook cells are on the dump's as-of (2026-10-13, the 15 dumps' common date);
"live" values are the 2026-11-26 reads (`fixtures/real_origintrail.json` meta.live). Code: `fetch/origintrail.py`
(`python -m fetch.origintrail --dump`), config block "ROUND 24 - ORIGINTRAIL". Tag A2 only; no buyback, no burn.

| Cell | Value | Source | Status |
|---|---|---|---|
| Total supply | 500,000,000 (Ethereum `totalSupply`; the one supply, never summed over chains) | eth_call TracToken 0xaA7a9CA8...0A6F (Sourcify full match: name Trace Token, TRAC, 18 decimals, `TOTAL_NUM_TOKENS` 5e26) | measured |
| Ethereum ERC-20 address | 0xaA7a9CA87d3694B5755f213B5D04094b8d0F0A6F confirmed | chain read of name / symbol / decimals / totalSupply, Sourcify verified source, tracdeepdive.gitbook.io lists the same address | measured |
| Minting | finished: `mintingFinished()` = 1; `mint`/`mintMany` are onlyOwner and need `!mintingFinished`; no reversal; founder tranches `founderAmounts(0..7)` and `preicoAndAdvisorsAmounts(0..1)` all 0 | eth_call + Sourcify source | measured; declared in `config.ZERO_NEW_SUPPLY` |
| Bridge model | LOCK-AND-MINT, all three. Base: OptimismMintableERC20 (`remoteToken()` = Ethereum TRAC, `bridge()` = 0x4200...0010); Gnosis: Omnibridge token (`bridgeContract()` 0xf6a7...); NeuroWeb: Snowbridge asset (docs.neuroweb.ai/ethereum-neuroweb-trac-bridge) | eth_call; docs.origintrail.io/general/bridging-trac | measured |
| Escrow vs copy supply (live) | Base 32,785,432.37 vs L1StandardBridge 32,785,641.41 (+209.04); Gnosis 68,182,807.77 vs Omnibridge mediator 68,213,647.41 (+30,839.64); NeuroWeb 18,027,598.45 vs Snowbridge Agent 0xd803...46a8 19,093,284.18 (+1,065,685.73) | eth_call balanceOf on Ethereum; Blockscout holders list | measured; Base/Gnosis difference is in-flight withdrawals, NeuroWeb Agent also serves other Snowbridge flows (not separated) |
| Circulating, issuer | 447,274,118 (CoinGecko's `circulating_supply`, the figure the market sites quote as 447.27M); 500,000,000 - 447,274,118 = 52,725,882 | api.coingecko.com coins/markets, read 2026-11-26 | issuer's OWN page not found: relayed figure, labelled |
| Circulating, ours | 447,274,118 (the figure in use; mcap/price on the as-of day) | dump rows `circulating_supply` (coingecko:mcap/price, implied) | follows the issuer figure; no wallet found that Jake's rule takes out |
| "20% locked for utility" wallets | NOT FOUND. No issuer wallet list exists; the top-400 Ethereum holders (449.2M of 500M) were read: bridge escrows 120.1M, then unlabelled EOAs. Nearest candidate 0x786b0902...A0C5 holds 60,000,000 (two 30M transfers from the Snowbridge Agent on 2026-04-13, no transaction ever sent, unlabelled) - it exceeds the 52.7M gap, so it is not the gap and is NOT classified. 308,761 TRAC sit in the token contract itself with no function that can move them (vesting tranches all 0) | eth.blockscout.com holders and transfers; eth_call | labelled GAP; nothing removed from circulating |
| Staked (live) | 95,335,226.20 = Base V10 30,449,268.36 + Base V8 67,858.05 + Gnosis V10 64,529,541.02 + Gnosis V8 288,558.78 | balanceOf(TRAC) at ConvictionStakingStorage / StakingStorage, addresses resolved through each Hub | measured; PARTIAL: NeuroWeb V8 stake NOT READ (no StakingStorage found behind the NeuroWeb Hub 0x0957e25B...; V10 is not live there) |
| Staked (workbook, as-of 2026-10-13) | 94,767,673.17 (21.19% of circulating) | same, at the day's closing block | measured, PARTIAL as above |
| Free float (as-of) | 352,506,445 = circulating - staked; free float market cap $135.1M | build | derived |
| Market cap / FDV / price (as-of) | $171.4M / $191.6M / $0.3833. Live: $115.3M / $128.9M / $0.2578 | CoinGecko market_chart (day) and coins/markets (live); DefiLlama coins relay for the price rows | measured |
| Publishing fees, fee volume | live 2026-11-22..25: 0 TRAC. 2026-06-23..2026-10-13: Gnosis 11,000,052.01 TRAC of which 10,999,945.89 is ONE event (PCA #11 settlement, tx 0x73a8bc5d...f4619, 2026-07-09); the rest 106.12. Base 2026-09-09..10-13: 6.47; 2026-11-09..26: 3.24. Q0 (90 days to 10-13): 41.39 TRAC = $12.03 | EpochStorageV8 (0xde319588...2005) `TokensAddedToEpochRange` tokenAmount, Base + Gnosis | measured; history forward-only (Gnosis from V10 deploy 2026-06-23, Base from the keyless RPC's horizon 2026-09-07), PARTIAL before; no DefiLlama listing; DKG explorer API not used |
| Revenue / fees ($) | Q0 $12.03; annualised $48.80; fees / FDV 2.5e-7 | fees TRAC x day price | measured x price; the PCA settlement is outside Q0 |
| Holder value (to stakers) | Q0 404,976 TRAC = $158,365 reached stakers (Gnosis 337,165 + Base 67,813 on claim days since 2026-07-08; all-time claims 765,306) | StakingV10 `RewardsClaimed` (after node operator fee and treasury fee, compounded into the position) | measured; CLAIMS lag accrual, so a lower bound; accrual not read |
| Holder value yield / Net holder yield | 0.440% / 0.440% (dilution 0: zero new supply). Payout ratio 13,161x (claims vs fees booked: timing, `PAYOUT_ABOVE_ONE`) | build | measured; key = net holder yield only |
| Protocol treasury fee | default 300 bps, cap 10%; recipient = zero address on Base and Gnosis (dormant, nothing skimmed) | ParametersStorage `protocolTreasury()` / `protocolTreasuryFee()` via the Hub | measured |
| Node operator fee | per node, NOT READ | - | labelled GAP (the claims already net it) |
| Lock tiers | 1x none, 1.5x 30d, 2x 90d, 3.5x 180d, 6x 365d (ERC-721 positions, `DKGStakingConvictionNFT`) | docs.origintrail.io/concepts/conviction-and-economics; docs/active-now/staking-migration | documented, not read on chain |
| V10 live dates | NOT the mid-April 2026 plan (the docs now call it "historical"). Gnosis: first staking-position mint 2026-06-22 22:07 UTC (block 46,834,155), first PCA 2026-06-22 23:25, first fee event 2026-06-23; 587 position mints, 11 PCA mints. Base: contracts exist by 2026-06-30 (code absent at 2026-03-29); first position mint readable 2026-09-13 (older logs not readable on keyless RPCs), 518 positions and 4 PCAs outstanding at block 54.8M (2026-11-08). NeuroWeb: not live ("not yet selectable") | Transfer-from-zero logs on the two NFT contracts; eth_getCode by block; docs networks page | measured; Base first mint may be earlier than 2026-09-13 |
| Buyback / burn | none documented, none found | docs; fee_split | declared n/a |

## Open gaps

- Issuer's own circulating page (only CoinGecko's relay of 447.27M); the 52.73M "locked for utility" wallets.
- NeuroWeb V8 stake (staked is Base + Gnosis only); NeuroWeb Agent escrow vs supply difference (+1.07M) not split by use.
- Fee history before 2026-06-23 (Gnosis) and 2026-09-07 (Base): the V8-era EpochStorage is no longer behind the Hub. Node operator fee per node.
- Holder value is claims, not accrual. Base first-mint date before 2026-09-13.
- Bybit / The Block quote 447.27M; CoinMarketCap 401M and the TRAC GitBook 402M disagree (not used).

## Decisions for Jake

1. The 11,000,000 TRAC PCA settlement (one commitment) drives every reward claim: keep it as fees (read as is), or declare it a one-off outside revenue. Recommend keep, labelled, since stakers are paid from it.
2. Holder value on claims (lower bound) or on epoch accrual. Recommend accrual once read; claims until then.
3. Circulating: keep 447.27M. Recommend keep until the issuer lists the locked wallets; do not remove the 60M unlabelled EOA.
