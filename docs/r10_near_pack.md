# NEAR research pack — read 2026-11-16 (≈22:15 UTC)

Read-only. No repo file changed, no build, no BigQuery. "RPC" = rpc.mainnet.fastnear.com / archival-rpc.mainnet.fastnear.com (rpc.mainnet.near.org gave the same status). Store = fixtures/real_near.json (as of 2026-10-13); the local metrics.db holds no Near rows, so nothing after 10-13 was seen.

## Three findings that change the tracker

1. **Protocol v87 is LIVE: 100% of gas is burned since 2026-11-04 11:46:29 UTC** (first block 223,012,337; `burnt_gas_reward` 3/10 → 0/1, read at both heights). v88 followed 2026-11-06 12:17:42 UTC (block 223,314,737). Config still says "v87 not active (2026-10-06)", `burn_split.share_of_fees_burned 0.70`, and Credibility `in_revenue` scales its reference by a fixed 0.7.
2. **DefiLlama's NEAR revenue is 30% short for 2026-11-04..11-13**: its methodology says 100% from 4 Nov, but its daily revenue is 0.70 × fees until 11-13 and = fees only from 11-14.
3. **HSP-029 (cut the issuance ceiling 2.5% → 1.6% over 24 months) passed House of Stake**; forum status "Final", first post updated 2026-11-16. Not in force: chain still reads `max_inflation_rate` 1/40.

## (a) Supply

| Fact | Value | Source |
|---|---|---|
| Total supply | 1,311,905,798 NEAR, block 224,875,350 | RPC block header |
| Max inflation | 2.5%/yr (`[1,40]`), protocol 88, epoch 43,200 blocks ≈ 7.4 h | RPC EXPERIMENTAL_protocol_config |
| Original rate | 5% (`[1,20]`) to block 170,351,518 (protocol 80) | archival RPC |
| Halving step | block 170,351,519, 2025-10-29 11:29:03 UTC, protocol 81, nearcore 2.9.0 — re-verified | archival RPC; nearcore CHANGELOG |
| Measured mint | 25,644–27,214 NEAR per epoch (3 epochs, 11-06 and 11-16) ≈ 2.3–2.45%/yr effective | archival RPC |
| Validator / treasury split | 90 / 10 measured: treasury.near received 10.0–10.2% of each of those 3 mints. RPC `protocol_reward_rate` reads [0,1] (not what mainnet pays, as config already records) | archival RPC |
| Gas burn share | 70% burned + 30% to the called contract until v87; **100% burned from 2026-11-04**. Contract-developer share is now 0 | RPC; CHANGELOG 2.14.0; HSP-027 |

Governance history:
- **5% → 2.5%**: proposed on the forum 2025-06-24 (HOT_DAO), stake vote on vote.linearprotocol.org. Press says it missed the two-thirds threshold; the exact tally is **not verified** (The Defiant and DL News disagree on why it failed). The cut shipped anyway in nearcore 2.9.0, activating once 80% of block-producing stake upgraded (NEAR One blog, 2025-10-21).
- **HSP-027** (remove the 30% rebate): created 2026-06-15, ratified 2026-07-03, shipped in 2.14.0 / v87.
- **HSP-029**: `cap(t) = 2.5% − 0.9% × min(t,24)/24`, per epoch, 90/10 split unchanged, activation "one full quarter after ratification", needs a nearcore release and validator adoption. Press: ~99.96% in favour on 2026-11-02, Security Council veto window to 11-16 — tally and veto outcome **not verified** on a primary page (houseofstake.org is a JS app).

Balances (RPC view_account, 2026-11-16, blocks 224,875,351–741; all `locked` 0):

| Account | NEAR |
|---|---|
| treasury.near (protocol treasury) | 1,671,459.70 |
| lockup.near | 171,054.43 |
| contributors.near | 4.70 |
| foundation.near | 869.32 |
| near (registrar) | 69,276.44 |
| nf-payments.near / nf-finance.near | 14,246.83 / 417.26 |
| fefundsadmin.sputnik-dao.near | 2,025,969.55 |
| buybacks.multisignature.near | 2,313,807.54 |
| 1csfundsadmin.sputnik-dao.near | 270.88 |
| ecotreasury4 / 5.multisignature.near | 145.0 / 55.0 |

- Buyback wallets combined: **4,340,048 NEAR** (0.33% of supply; 3,854,384 in the store on 10-13). wNEAR in all three: 0. revenue.near.org shows 4,385,760 "all-time".
- Lockups: latest read on file is 2026-10-11 (block 219,544,718): 1,960 live `*.lockup.near`, LOCKED 2,058,522, UNVESTED 1,999,721 (re-read 10-27, same). Not re-read today.
- **NEAR Foundation treasury holdings: not verified.** No published account list. SPEC D2-4f already traced 30.17M NEAR out of treasury.near (Apr–May 2026) to Foundation-side wallets and unlabelled implicit accounts; nfdn.near does not exist.

## (b) Circulating

How the tracker computes it: `circulating_supply_lockup_rule` = block-header total − lockups LOCKED (weekly read) − lockup.near − contributors.near. Staked NEAR outside lockups stays in; the three buyback wallets stay in circulating and come off float (config line 20021). **This matches Jake's test** (locked lockup balances are out even when staked; holder-chosen stake is float only).

| Figure | NEAR | vs test |
|---|---|---|
| Jake's test today (total − 2,058,522 − 171,059; lockup leg 5 weeks old) | ≈1,309,676,217 | — |
| CoinGecko circulating | 1,311,878,634 (= its total) | +0.17% |
| CoinMarketCap circulating | 1,311,905,811 (= total) | +0.17% |
| NEAR's own (BigQuery table), last stored 2026-10-12 | 1,306,589,287 = total − 2,193,802 | 35.8K NEAR from the rule that day (0.003%) |
| NearBlocks /v3/stats | = total | +0.17% |

- No gap over 2% on any live figure. CoinGecko and CMC count the lockups (2.06M) and lockup.near (171K) as circulating.
- Two stale figures to ignore: CMC "self-reported" 1,209,202,633 (−7.7%, no account list, unexplained) and NearBlocks /v1/stats 1,249,836,992 (−4.6%, flat since 2025-08-27, already retired in SPEC G17-3b).
- Open point: treasury.near (1.67M, 0.13%) is counted circulating by every method including ours. Under the test it could be read as an undistributed allocation.

## (c) S13 — three revenue lines (no `REVENUE_SPLIT["Near"]` exists yet)

Facts:
- Gas fees: $104,823 last 30 days (DefiLlama `near`). Burned share 70% → 100% from 2026-11-04. Burn reaches all holders through supply.
- NEAR Intents: fees $6.75M / 30 days; NEAR's captured revenue $1.70M / 30 days (DefiLlama `near-intents`, measured as NEAR swept into the three wallets). Since 2026-02-23 it buys NEAR on market; the NEAR is **held, not burned** (on chain: no outflows except between the three wallets). revenue.near.org still says "permanently remove NEAR from circulation".
- Chain Signatures: no fee revenue to the protocol or a fund found — **not verified**. (v1.signer holds 3,186 NEAR; HSP-007 pays MPC operators from the treasury — a cost.)
- Emissions: the epoch mint in (a), 90% validators, 10% treasury.near.

Proposed definitions:
- **FEES** = gas fees + Intents gross fees (today's `FEES_TOTAL`). Keep.
- **REVENUE** — option A: gas burn + Intents captured revenue (today's `REVENUE_TOTAL`). Option B: gas only, Intents as a separate Foundation line. **Recommend A**, with the gas leg taken from the chain's header burn (`gross_burn_tokens_bigquery` × price) or fees × share-in-force, not DefiLlama's revenue (finding 2).
- **HOLDER REVENUE** — option A: gas burn only (Intents NEAR is held by multisigs and could return). Option B: gas burn + Intents buyback, credited as held. Option C: as B, measured on chain from the wallets' NEAR intake × price, DefiLlama as cross-check. **Recommend C**, labelled "held, not burned" — the Hyperliquid pattern, and HSP-029 states the bought NEAR "stays removed".
- Other recipients: contract developers (30% of gas, to 2026-11-04 only); solvers and front-end partners (Intents fees less capture).

## (d) MISSING 11 / CHECK 5

I could not enumerate the 16 cells without a build. Identified from config, SPEC and the dump:

Needs history (class C) — likely members:
- References ×3: all-time inflows of the three wallets — wait on BigQuery stage 2 ("valid from 2026-11-08"). Blocked by the ADC failure. Fix: re-auth, run `check_offline_items.py near_wallet_inflow_days`.
- Settlement volume trailing 365d and NRR on settlement (A1 and Trajectory): `p2p_transfer_volume_usd` starts 2026-04-01; the BigQuery backfill is planned for Nov–Dec. Same blocker.
- Short series behind older-quarter cells: `active_addresses` from 2026-08-25, `fees_native_tokens` from 06-29, `tx_count` (NearBlocks) from 06-21, Intents volume (NearBlocks) from 07-06.

No source (class E) — likely members:
- Insider share of stake / leakage (i): no NEAR Foundation account list.
- Flows (b), HSP-007 operator payments: "House of Stake wallets" unnamed.
- Third: not identified (`settlement_volume_usd` has no sources.yaml entry — a candidate).

CHECK — candidates, not confirmed:
- `in_revenue`: fixed 0.7 reference, wrong after 11-04.
- `a4_gross_burn`: fees × share vs header burn across the switch.
- `in_locked`: store on 10-13 has RPC 594.4M vs NearBlocks 557.0M (6.7%, tolerance 2%) after a one-day +36M jump in the RPC figure.
- `in_buyback_wallets` / `in_buyback`: manual readings dated 2026-10-07 (3,786,230 NEAR; $2.08M) against wallets now at 4.34M.
- `in_noncirc_nearblocks`: NearBlocks v3 now reports non-circulating 0 vs ours ≈2.2M.

## Round 9 (Near) — proposed work items

1. Date the v87 switch: set `protocol_burn.switch_on` (2026-11-04, block 223,012,337), update `burn_split`, `in_revenue`'s 0.7 and the "not active" notes; record DefiLlama's 11-04..11-13 shortfall as a named difference.
2. Add `REVENUE_SPLIT["Near"]` per (c).
3. After Jake's re-auth: run the BigQuery stage 1/2 inflows and the p2p backfill; refresh the lockup read (5 weeks old).
4. Record HSP-029 as a pending schedule on `issuance_rate_declared` (2.5% → 1.6%, ramp start unconfirmed), and re-read `max_inflation_rate` every run.
5. Refresh the stale manual references (revenue.near.org wallets, 30-day net revenue) and resolve the `in_locked` 594M reading.

## Decisions for Jake

1. **Gas revenue source.** (a) DefiLlama as is; (b) chain header burn × price; (c) DefiLlama fees × share in force. Recommend (b), with (c) as cross-check.
2. **Mixed day 2026-11-04** (switch at 11:46 UTC). (a) 70% all day; (b) 100% from 11-04; (c) 100% from 11-05, first whole day. Recommend (c), as was done for the inflation step.
3. **Intents buyback in HOLDER REVENUE.** (a) exclude; (b) include as held; (c) include, measured on chain. Recommend (c).
4. **treasury.near in circulating.** (a) keep in (NEAR's own method); (b) take out as undistributed. Recommend (a), shown as a named 1.67M line; 30M left it in 2026, so it is spendable.
5. **HSP-029.** (a) wait for the chain parameter to move; (b) declare the ramp now as pending. Recommend (b), flagged "not active".
6. **CMC self-reported 1.209B.** (a) ignore, recorded as stale; (b) chase the account list. Recommend (a).

## Sources (all read 2026-11-16)

- NEAR RPC: https://rpc.mainnet.fastnear.com , https://archival-rpc.mainnet.fastnear.com , https://rpc.mainnet.near.org (status, block, EXPERIMENTAL_protocol_config, view_account, wrap.near ft_balance_of)
- nearcore CHANGELOG (2.9.0, 2.14.0): https://raw.githubusercontent.com/near/nearcore/master/CHANGELOG.md
- nearcore version.rs (RemoveGasRewards => 87): https://raw.githubusercontent.com/near/nearcore/master/core/primitives-core/src/version.rs
- HSP-027: https://gov.near.org/t/hsp-027-remove-the-near-developer-gas-rebate/42213
- HSP-029: https://gov.near.org/t/hsp-029-establish-a-24-month-issuance-ramp-to-a-1-6-target-rate-of-near-token-emissions/42644
- 2025 inflation proposal: https://gov.near.org/t/reduce-inflation-for-near-protocol/41140
- NEAR One, 2.9.0 halving upgrade: https://blog.nearone.org/announcement/2025/10/21/enhancing-near-tokenomics.html
- revenue.near.org: https://revenue.near.org/ (page says "figures shown are illustrative"; several tiles read 0)
- CoinGecko: https://api.coingecko.com/api/v3/coins/near
- CoinMarketCap: https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail?slug=near-protocol
- NearBlocks: https://api.nearblocks.io/v1/stats , https://api.nearblocks.io/v3/stats
- DefiLlama: https://api.llama.fi/summary/fees/near , https://api.llama.fi/summary/fees/near-intents
- Press, cross-check only (2025 vote result, HSP-029 tally): https://thedefiant.io/news/blockchains/near-protocol-halving-upgrade-community-vote , https://www.theblock.co/news/defi/2026-11-03-near-governance-approves-token-issuance-432378
- Not reachable: https://docs.near.org/protocol/gas (404 — config's `burn_split` source URL is dead); https://blockworks.co/news/near-inflation-reduction-vote (403)
- Repo: /home/user/Crypto-Tracker/config.py (lines 1266–1282, 3800–3830, 4350–4520, 20021, 24087–24115, 25480–25500, 28267–28386, 32176–32181), /home/user/Crypto-Tracker/SPEC.md (rows D2-4f, G17-2b, G17-3b, P2a-D4a, D5-1e), /home/user/Crypto-Tracker/fixtures/real_near.json
