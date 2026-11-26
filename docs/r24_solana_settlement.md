# Solana settlement volume: the P2P transfer leg (R24, 2026-11-26)

**Status: `settlement: P2P transfer leg has no free source — Jake decides on billing`.** The settlement cell and the A1 key
stay a labelled gap. Nothing billed was called; no authentication was used; no number below was measured by a query.

Settlement volume (Artemis definition) = DEX volume + NFT trading + P2P transfers.

| Leg | Source | Status |
|---|---|---|
| DEX volume | DefiLlama `/overview/dexs/solana` (free) | wired, `dex_volume_usd` (1,879 daily rows from 2021-10-01) |
| P2P transfers | none free found | GAP |

## Candidates for the P2P leg (all ESTIMATES; the cost basis is stated on each)

Pricing basis: Google BigQuery on-demand list price of about 6.25 USD per TiB scanned, first 1 TiB a month free (the repo's
own R19 plan uses the same shape; confirm the current price before any run). Table sizes are NOT read; no dry run was made
(a dry run needs authentication). A dry run (free) is the first step of any of these and replaces every figure below.

| Candidate | Cheapest query shape | ESTIMATED cost | Estimate basis |
|---|---|---|---|
| BigQuery public Solana dataset (`bigquery-public-data.crypto_solana_mainnet_us`; same family as the NEAR dataset the repo already reads) | one pass per day over the token-transfer table (SPL, filtered to non-program-owned sender and receiver, priced by day) plus the native-SOL system-program transfers from the instruction table; partition-filter on block_timestamp, select only the amount, mint and owner columns | roughly 0.3 to 2 TiB scanned a day, so about 2 to 12 USD a day; a year about 700 to 4,500 USD; the first TiB a month is free | assumed Solana volume of tens of millions of non-vote transactions a day and ~100 to 300 bytes per scanned row-set after column pruning; **UNVERIFIED, could be off by 5x either way** |
| Dune (Solana tables: `tokens_solana.transfers`, `solana.transactions`) | one query per day or per week of `tokens_solana.transfers` with owner-type filter, summed in USD by day | the repo's Dune account is at its 402 credit cap, so cost is a plan upgrade, not a per-query price; a year of daily scans is likely hundreds of credits-equivalent of large-engine executions | basis: the cap the repo already hits (R22 notes); **no price list read this round** |
| Helius (enhanced transactions, `getTransactionsForAddress`) | per-address only; there is no network-wide transfer feed | not workable: network-wide P2P needs every account | basis: the API's per-address shape; **UNVERIFIED** |
| Allium (the source DefiLlama's own Solana fees adapter queries) | one SQL over `solana.raw.transactions` / a transfers table | enterprise quote only, no public price | basis: DefiLlama's adapter marks Allium `isExpensiveAdapter`; **not priced** |
| Artemis data service (`SETTLEMENT_VOLUME?symbols=sol`) | one keyless GET | free, but it is not a documented public API, its terms are not read (config SOURCE_REGISTER), and its P2P series for Ethereum ended 2026-08-25 | **not wired**; the Ethereum precedent says it may stop at the source |
| Public RPC (`getBlock` over the whole day) | read every block of a day | not a billed route but not feasible: ~216,000 blocks a day, each with full transactions; far beyond a free endpoint's limits | basis: ~4.6 slots a second read on 2026-11-26 (getRecentPerformanceSamples) |

## Recommendation (decision for Jake)

Authorise one BigQuery **dry run only** (free) of the shape above on a single recent day, to replace the table with a real
byte count. If a year of it is under the NEAR budget precedent (400 GB a query cap, the monthly ledger), run it the way
`bigquery_backfill.py` runs NEAR and Ethereum. Until then the key reads the label above, never a DEX-only number
presented as settlement.
