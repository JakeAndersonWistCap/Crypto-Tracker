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
