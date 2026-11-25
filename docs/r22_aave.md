# Round 22 — Aave (AAVEPYTH lane, branch `claude/aave-pyth`)

Jake 2026-11-25: "AAVE (Ethereum). A3, label 'paused since 19 Apr 2026'. A4 off." Every on-chain figure below was read
on 2026-11-25 (Ethereum block 26,492,057 unless stated; keyless `ethereum-rpc.publicnode.com`, logs from
`mainnet.gateway.tenderly.co`), User-Agent `token-metrics-check/1.0`. Earlier research: `research/aave/dossier.md`
(27 Oct 2026).

## 1. Addresses — each checked on chain and against bgd-labs/aave-address-book (main, read 2026-11-25)

| Role | Address | Address book | On chain 2026-11-25 |
|---|---|---|---|
| AAVE | 0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9 | AaveV3Ethereum.AAVE_UNDERLYING | totalSupply 16,000,000.000 |
| aAAVE (V3 Core) | 0xA700b4eB416Be35b2911fd5Dee80678ff64fF6C9 | — | symbol aEthAAVE, UNDERLYING = AAVE |
| Ecosystem Reserve | 0x25F2226B597E8F9514B3F68F00f494cF4f286491 | MiscEthereum.ECOSYSTEM_RESERVE | 558,394.92 AAVE |
| Collector | 0x464C71f6c2F760DdA6093dCB91C24c39e5d6e18c | AaveV3Ethereum.COLLECTOR | 0 AAVE, 6,663.43 aAAVE |
| V4 TreasurySpoke | 0xB9B0b8616f6Bf6841972a52058132BE08d723155 | AaveV4Ethereum.TREASURY_SPOKE | code 1,419 B; 0 AAVE |
| AFC Safe | 0x22740deBa78d5a0c24C58C740e3715ec29de1bFa | MiscEthereum.AFC_SAFE | 0 AAVE, **240,502.34 aAAVE** (the bought AAVE) |
| AAVE_SWAPPER | 0x3ea64b1C0194524b48F9118462C8E9cd61a243c7 | MiscEthereum.AAVE_SWAPPER | 0 |
| TokenLogic safe | 0x9DE1d45e2786b03498289959203F25b29B4D1193 | MiscEthereum.TOKENLOGIC_EXECUTION_SAFE | 0 |
| stkAAVE | 0x4da27a545c0c5B758a6BA100e3a049001de870f5 | AaveSafetyModule.STK_AAVE | 2,464,339.74 AAVE held; 150 AAVE/day; slashable 0 |
| stkABPT | 0xa1116930326D21fB917d5A27F1E9943A9595fb47 | AaveSafetyModule.STK_ABPT | emission 0 (DISTRIBUTION_END 2025-01-17) |
| stkAAVE/wstETH BPTv2 | 0x9eDA81C21C273a82BE9Bbc19B6A6182212068101 | AaveSafetyModule.STK_AAVE_WSTETH_BPTV2 | emission 0 since 2026-03-24 |
| stkGHO (legacy) | 0x1a88Df1cFe15Af22B3c4c783D4e6F7F9e0C1885d | AaveSafetyModule.STK_GHO | reward AAVE, emission 0 since 2025-06-05 |
| Umbrella | 0xD400fc38ED4732893174325693a63C30ee3881a8 | UmbrellaEthereum.UMBRELLA | 4 stake tokens; rewards aEthUSDC / aEthUSDT / aEthWETH / GHO — **no AAVE** |
| "Umbrella" #2 | 0x5AC6C5C70934939d7Ad4aAB253aFD760Ad5E5313 | **UmbrellaBaseSepolia** (a testnet) | **no code** on Ethereum, Base, Arbitrum, Avalanche, Optimism, Gnosis — NOT a mainnet address; not used |

Every stake token's REWARDS_VAULT() is the Ecosystem Reserve; its REWARD_TOKEN() is AAVE.

## 2. Buybacks — paused since 19 Apr 2026, verified on chain

- Route actually used: the Swap Steward's TWAP orders filled by **CoW Protocol (GPv2Settlement 0x9008D19f…ab41) into
  the Collector** — about four fills of 200–243 AAVE a day. Fills per day (aave_chain scan, AAVE / aAAVE into the
  Collector, AFC Safe, Ecosystem Reserve and TreasurySpoke from a swap venue): 11 Apr 934.77, 12 Apr 943.91, 13 Apr
  899.50, 14 Apr 909.27, 15 Apr 878.99, 16 Apr 638.68, 17 Apr 600.14, 18 Apr 824.75, **19 Apr 713.67 (the last)**;
  **0 on every day from 20 Apr to 24 Nov 2026** (219 days).
- Brief's route "AAVE into the Ecosystem Reserve from the AFC Safe or the swapper": nothing on chain. Bought AAVE was
  never sent to the Ecosystem Reserve; it was swept from the Collector to the AFC Safe (11,240.33 on 15 Jun 2026) and
  supplied to Aave as aAAVE (220,308.38 minted to the AFC on 8 May 2026). AIP 449 (Feb 2026): "This Allowance enables the
  AAVE acquired via BuyBacks to be held on the AFC SAFE." The only Ecosystem Reserve inflow since April is the LEND
  migrator's 302,384.33 (9 May 2026, AIP 480) — not a buy. **Decision for Jake: the held buyback is the AFC Safe, not
  the Reserve** (wired so).
- Inflows that are NOT buys (logged by the reader, not counted): 24 Jun 2026 1,405.00 and 5,983.00 AAVE into the AFC
  from two EOAs (0xe26acd33…3f387, 0xaff94a17…9b342; no primary source explains them — DefiLlama books $555k of holders
  revenue that day for them); 26 May 1,565.48 from the Chaos Labs vesting wallet.
- Programme size: DefiLlama `aave` dailyHoldersRevenue, all time = **$46,418,335** (read 2026-11-25; first non-zero 9 Apr
  2025, 0 every day after 24 Jun 2026). Matches the brief's "about $46.4M".
- Pause: ARFC "Pause AAVE Buybacks" (TokenLogic, 22 Apr 2026, https://governance.aave.com/t/arfc-pause-aave-buybacks/24686;
  Snapshot closed 1 May 2026): "AAVE buybacks are paused effective April 19, 2026".
- Aavenomics 3.0: announced (Stani's post quoted 25 Jun 2026; The Defiant 27 Jun 2026 "live"). No contract in the
  address book, no AIP, no buy on chain as of 2026-11-25. **The label clears itself** when the reader stores a positive
  buy dated after 19 Apr 2026 (config.STATUS_CLEARS_ON_BUYS; tests/test_r22_aave.py). If 3.0 ships a NEW buyer contract
  or sink, add it to `config.AAVE_CHAIN["buy_sources"]` / `["buy_sinks"]` — until then a buy through it is not seen.

## 3. Emissions — measured schedule (new supply, paid from the Ecosystem Reserve)

`AssetConfigUpdated(asset, emission)` on each AAVE-paying stake token since block 21,500,000:

| From | stkAAVE | BPTv2 | stkGHO | Total AAVE/day |
|---|---|---|---|---|
| 2025-06-05 | 315 | 216 | 0 | 531 |
| 2025-07-29 | 260 | 130 | 0 | 390 |
| 2026-01-20 | 260 | 40 | 0 | 300 |
| 2026-03-24 (AIP 459) | 220 | 0 | 0 | 220 |
| 2026-06-13 (AIP 491) | 150 | 0 | 0 | **150** |

Current rate on chain: 1,736,111,111,111,111 wei/s on stkAAVE = 150.0 AAVE/day (54,750/yr); all others 0. Umbrella pays
no AAVE (RewardsController 0x4655…188B getAllRewards on its four stake tokens). The config's schedule was "150/day from
2026-08-28" (the Allowance-Update AIP's date, not the rate's) — replaced with the table above.

## 4. Circulating (Round 22 rule) vs CoinGecko and CoinMarketCap (read 2026-11-25)

- Ours: 16,000,000 − 0.31 (0x…dEaD) = **15,999,999.69**. The Ecosystem Reserve, Collector, TreasurySpoke, swapper and
  TokenLogic safe are IN circulating, off the float (Jake's brief: "Ecosystem Reserve … (circulating, off float)").
- CoinGecko 15,441,604.87; CoinMarketCap 15,441,605.08 — both = 16M − the Ecosystem Reserve.
- Gap **558,394.92 (3.6%) = exactly the Ecosystem Reserve 0x25F2…6491**.
- Free float = circulating − staked (2,481,379.77: stkAAVE 2,464,339.74 + the AAVE inside staked BPT 17,040.03) −
  issuer/treasury (565,058.36) − held buybacks (AFC 240,502.34) ≈ 12.71M.
- **Decision for Jake:** the brief also says "an unreleased emissions reserve is OUT … what it pays out is new supply",
  and the Ecosystem Reserve IS the stakers' reward vault. I followed the address-specific line (IN). Taking it OUT
  would match CoinGecko / CMC to 0.3 AAVE and make each staker payout new circulating supply.

## 5. Research answers

- **Is the 50% GHO-revenue share to stakers live?** NOT established as live; on the evidence, no. The ARFC "Aavenomics
  implementation: Part one" (ACI, 4 Mar 2025, https://governance.aave.com/t/arfc-aavenomics-implementation-part-one/21248)
  proposes "Anti-GHO" at "50% of GHO revenue" (80% stkAAVE / 20% stkBPT) but says it "might require additional
  development and audit time" and would likely come in "Aavenomics Part Two". On chain the only reward token of every
  stake token is AAVE; no Anti-GHO contract is in the address book (read 2026-11-25); DefiLlama's token-rights page
  (https://defillama.com/protocol/token-rights/aave, read 2026-11-25) says no direct revenue distribution to holders is
  active. A 0.25%-of-revenue staker share was only a TEMP CHECK (governance.aave.com/t/24395, Apr 2026).
- **Current emission rates:** 150 AAVE/day to stkAAVE; 0 to stkABPT, BPTv2, stkGHO; Umbrella in aTokens / GHO (§3).
- **Revenue ($402M vs >$140M):** neither figure matches DefiLlama `aave` (parent = v2 + v3 + v4; read 2026-11-25):
  revenue all time **$329.3M**, last 365 days **$85.2M**, last 30 days $6.41M; fees all time $2.37B, 365 days $590.6M;
  holders revenue all time $46.4M. Revenue cross-check against the Collector / TreasurySpoke inflows: NOT done this
  round (the Collector receives dozens of aTokens; a full inflow valuation is its own read) — a gap.

## 6. What the reader stores (fetch/aave_chain.py, tier `aave_chain`)

treasury_holding_tokens, aave_ecosystem_reserve_tokens, aave_held_buyback_tokens, burn_address_balance, locked_tokens,
aave_emission_rate_tokens (daily, one block) and actual_buyback_tokens (per UTC day from 2026-04-11; a scanned day with
no buy is a measured 0). History of the stocks before the first run is NOT reconstructed (forward only).
