# Research pack: Aerodrome / Aero (AERO, Base)

Read 2026-11-16. Read-only: no repo file changed, no build or test run.
On-chain figures are my own `eth_call` reads at **Base block 58,384,321 (2026-11-16 22:13 UTC)** via base-rpc.publicnode.com unless another block is given. "M" = million AERO.

## The three findings that matter most

1. **Why `aero_v3` times out (c).** The weekly voter-epoch scan runs inside the same tier as the supply read, and a timed-out tier has its stock rows thrown away. So every timeout also deletes that day's supply rows, and circulating reads n/a.
2. **Base block time is now 0.2 s, not 2 s** (verified). The scan's block maths assumes 2 s, so it is now roughly ten times too slow and can pick the wrong start block.
3. **The tracker's CoinGecko id points at the legacy token.** `aerodrome-finance` is now the old AERO listing (circulating 272.8M). The new token is CoinGecko id `aero` (723.9M). That is the "about 290M" Jake saw.

## What the store holds vs what is missing

- **Held** (`fixtures/real_aerodrome.json`, as of 2026-10-13, 8,583 rows): price, CoinGecko supply and market cap, DefiLlama fees / revenue / holders revenue / TVL / DEX volume, legacy total supply, veAERO lock series, minter issuance, rebase, filing-wallet managed locks, Foundation locked AERO, weekly voter rewards (fees and bribes apart, 14 epochs to 2026-10-01).
- **Missing in the dump** (14 gap rows): the seven Foundation buyback series, six voter-reward series for the latest epoch, and `max_supply`.
- **Not in the dump at all:** any post-migration (`aero_v3`) row, and any run log after 2026-10-13. `metrics.db` in the repo is empty, so I could not see Jake's actual timeout messages.

## (a) Supply

**Genesis (legacy Aerodrome, 2023-08-28): 500M.** Source: aerodrome-finance/docs `tokenomics.mdx`.

| Allocation | Amount | Form |
|---|---|---|
| Airdrop to veVELO lockers | 200M (40%) | veAERO, locked |
| Public Goods Fund | 105M (21%) | veAERO, auto max-locked |
| Development team | 95M (19%) | veAERO, auto max-locked |
| Flight School | 50M (10%) | veAERO, auto max-locked |
| Voter incentives | 40M (8%) | liquid |
| Genesis liquidity incentives | 10M (2%) | liquid |

There was no sale and no time-based vesting. The docs say of the ecosystem wallets: "The veAERO in these wallets are max-locked. None of the underlying AERO has ever been sold."

**Legacy emissions.** 10M a week at start, +3% for 14 epochs, then −1% a week, then tail mode from epoch 67 (config `minter_constants`). `tailEmissionRate()` reads 17 bps today; the store's last label was 21 bps on 2026-10-08. 5% went to the team wallet, compounded into its max-lock. The weekly rebase went to veAERO holders.

**Migration to Aero (2026-10-22 00:00 UTC, aero.xyz/aero-upgrade).**
- New token `0x3f5bF3Ed…a4Ee` was minted at 2,127,000,000: about 2,010M for AERO holders at 1:1 and 117M for VELO holders at 0.044 (5.5%).
- Migration reserve still unclaimed: **313.43M**, so about 84% of the AERO side has migrated. Velodrome allocation still unclaimed: **20.52M**.
- The docs say unmigrated tokens "will never reach circulation" and that there is no deadline.

**The legacy token is still minting** (verified by archive reads). Legacy total supply was 2,002.38M at the cutover block and is 2,008.10M now: +4.49M at the 10-22 flip, then +0.14M, +0.61M, +0.47M. The legacy Minter's `activePeriod` reads 2026-11-12. Aero's economic-case post says old veAERO "will continue to receive rebases". Whether these mints are all rebase is not verified.

**New emission schedule** (app.aero.xyz/docs/about/emissions, plus Minter reads):
- No fixed curve. AERO is minted only when LPs claim, and each pool is capped by a gauge cap tied to its projected revenue.
- Hard ceiling: `emissionRate()` = 8,158,356 a week, which is 20% a year. The docs expect about 12% realised.
- Actually minted: `emissionsMinted()` = 16,126,977 since launch. Over the last 7 days it was 5.56M, about 13.5% annualised, or 14.2% with the team share.
- Contributor share: `teamRate()` = 50,000 pips = 5% on top, minted to `SPLITTER()` `0x9beb95cc…1156`. The docs say it goes to the Foundation as a Perpetual Stake, fixed in the contract and capped at 5%.
- The "AERO Fed" (quarterly rules-based recommendations, with Vending Machine) is announced; I found no published framework. Not verified.
- New total supply: 2,143,525,510.

**Rebase.** sAERO has none; the economic-case post says the rebase is removed. Only unmigrated legacy veAERO still gets one.

**Staking terms (sAERO).** Up to 4 years, weight decays linearly, no early exit, and a Perpetual Stake holds maximum weight. sAERO total: 1,077.71M, of which 964.98M is perpetual.

**Who holds what** (new AERO, same block):

| Wallet | Liquid | Staked (direct sAERO + Relay principal) |
|---|---|---|
| Team / Foundation multisig `0xBDE0…EA5a` | 4.06M | 208.47M |
| Public Goods Fund / Momentum Fund `0x834C…Da52` | 1.67M (+0.54M legacy) | 194.74M |
| Flight School `0x51E1…88E4` | 2.03M | 67.36M (+1 legacy veNFT, not read) |
| Velodrome Foundation airdrop `0x5b18…1431` | 0.28M | 32.53M |
| TGE incentives `0x7269…9dDb` | 28.94M | 0 |
| Buyback / Locked Funds `0x623C…57a1` | 0 | 0 |
| AirdropDistributor `0xE4c6…382F` | 0 | 0 |
| **Sum** | **36.98M (+0.54M legacy)** | **503.09M** |

The 503.09M is 46.7% of all sAERO. Pending Relay deposits were not read. The Relay registry now lists 2 factories and 18 Relays; `aero_migration.json`'s note still says 1 and 9 (the code handles several).

## (b) Circulating by Jake's test

**Result on the tracker's current basis: about 1,580.5M** (my arithmetic from the reads above, not a build figure). Pending Relay deposits and Flight School's remaining legacy veNFT are not read; they would lower it by a few million.

How it is built:
- New total 2,143.53M
- less the migration reserve 313.43M
- less the unclaimed Velodrome allocation 20.52M
- less issuer liquid 36.98M
- plus legacy AERO still out, at 1:1: 310.99M
- less issuer-wallet stakes 503.09M

Holder-chosen locks stay in circulating and come off free float: other holders' sAERO is 574.6M and unmigrated legacy veAERO about 38.7M, so **free float is about 967M**.

**Against the aggregators** (all read 2026-11-16):

| Source | Figure | How it is built (ties to my reads) | Ours minus theirs |
|---|---|---|---|
| CoinMarketCap | 1,065.80M | new total − all sAERO | +514.7M (48%) |
| CoinGecko `aero` (new) | 723.92M | its total 2,135.58M − sAERO − reserve − Velodrome unclaimed | +856.6M |
| CoinGecko `aerodrome-finance` (legacy) | 272.79M | legacy total − legacy escrow balance | n/a (old token only) |
| Project (aero.xyz) | "~1.01B to max ~1.07B", no definition | CMC self-reported: 1,076.94M | about +504M |

Gaps named by wallet:
- **vs CMC (+514.7M):** +1,077.71M all sAERO, which we count; −503.09M issuer-wallet stakes; −36.98M issuer liquid; −20.52M Velodrome migration contract `0x6dae95fe…66c0`; −2.44M reserve in excess of legacy AERO out.
- **vs CoinGecko new (+856.6M):** +1,077.71M sAERO; −503.09M issuer stakes; +310.99M legacy AERO still out; −36.98M issuer liquid; +7.95M because CoinGecko's total is stale.

Every gap over 2% is a basis difference (locks counted or not), not a missing wallet.

**DECISION FOR JAKE: allocations handed out already locked.**
- What the docs say: PGF 105M, team 95M and Flight School 50M were issued as auto max-locked veAERO at genesis and never sold. The team also gets 5% of emissions, compounded into its lock. Over 184M more was "acquired and locked" by the PGF and Flight School. On migration a permanent lock became an sAERO at maximum duration.
- Treatment A (today's): all 503.09M in the six filing wallets is out. Circulating about 1,580M.
- Treatment B: only never-released tokens are out (the 250M genesis locks plus the team's emission share). AERO the Foundation bought on market and locked counts as circulating, off free float. Circulating rises by roughly 180M to 250M. The split is not verified on chain; it needs a per-wallet trace.
- Treatment C: all in. Circulating about 2,084M.
- **Recommendation: A.** These are issuer-controlled wallets and their stakes were never released to an outside holder. Bought-back AERO held by the issuer is treasury stock. Show the genesis, emission-share and bought parts as a memo once traced.
- The 200M airdrop to veVELO lockers was also handed out locked, but to outside holders: in circulating, off free float. No change.

**Second decision: the unclaimed Velodrome allocation (20.52M).** The tracker counts unmigrated legacy AERO as circulating but excludes unmigrated VELO's claim. I recommend keeping it out and writing the reason down: the VELO is not AERO until converted.

## (c) Why circulating stopped on 2026-10-29 and why `aero_v3` times out

**Verified from code:**
- `AeroV3.run` does the state reads, then the supply, then `_epoch`, all in one tier with a 240 s budget (`fetch/__init__.py` `TIER_BUDGET_S`).
- On a timeout, `_abandon` calls `_drop_stocks`: every stock row from that tier is discarded. `noncirculating_holding_tokens` and `filing_managed_lock_tokens` are `kind: stock`. Circulating is total minus those two, so it reads n/a.
- Legacy rows for these metrics are refused after the cutover, and there is no CoinGecko fallback (`no_coingecko_fallback`), so nothing else fills the day.
- `_epoch` returns early until one complete v3 epoch exists. The first one completed 2026-10-29 00:00 UTC, the day the series stopped.
- `_epoch` has no "already stored" check, so it repeats the full scan every run. It re-scans `VotingRewardsCreated` from `deploy_block` in 5,000-block chunks, scans fee logs for the week, then makes one `incentiveCount` call per manager (288 managers on 10-27) plus one per programme, all sequential at 0.2 s minimum spacing.
- `_block_at` searches only the last 3,456,000 blocks, with the comment "Base: 2 s blocks".

**Verified on chain:** Base now makes a block every 0.2 s (blocks 55,000,000 to 56,000,000 took 200,000 s). The average was 1.82 s between 10-27 and 11-05, so the change came around 11-05. `mainnet.base.org` answered a `getLogs` call with "request limit reached".

**Inferred** (no run log after 10-13 to confirm the phase):
- From 10-29 the scan needed roughly 520 or more calls (about 260 s with latency), just over budget.
- Since about 11-05 it needs about 1,100 factory-scan calls plus about 600 fee-scan calls, far over budget.
- 3,456,000 blocks is now only 8 days, less than the 7 to 14 days back an epoch starts, so `_block_at` would return a wrong start block silently.
- RUNBOOK 11be notes Alchemy's free tier allows 10 blocks per `getLogs`. If Jake's keyed endpoint is that tier, every chunk fails over to the public list, which is slower still.

**Proposed fix:**
1. Move the epoch into its own tier (or run it last, with the supply rows committed first) so an epoch timeout cannot drop supply stocks.
2. Skip the epoch when it is already stored.
3. Cache the manager list and the last scanned block (`fetch/logcache.py` exists).
4. Batch `incentiveCount` / `incentive` through Multicall3.
5. Find blocks by timestamp over the whole range, never by an assumed block time. Check the repo for other "2 s" assumptions.
6. Backfill 2026-10-30 to 11-16 with archive `eth_call`s. The public node answered calls at the cutover block today, so the supply can be re-read at each day's block.

## (d) S13: the three revenue lines (decisions)

- **Stored today:** DefiLlama fees, revenue and holders revenue to 2026-10-11, plus on-chain voter fees and bribes weekly to the 10-01 epoch. `REVENUE_TIER` marks the existing revenue cell as holder revenue. Aerodrome has no `REVENUE_SPLIT` entry.
- **DefiLlama today:** the parent "Aero" now sums Velodrome V1/V2/V3, Aerodrome V1/Slipstream, Aero Slipstream (id 8691) and Aero Basic (id 9172). 30 days: fees $9.75M, revenue $7.62M, holders revenue $7.33M, supply side $2.13M.
- **Legacy gauges** last paid fees on 2026-10-15. Fees since then sit locked in the gauges "until the Aero Foundation reclaims them" (DefiLlama methodology; aero.xyz says the same).

| Line | Options | Recommendation |
|---|---|---|
| FEES | A: all swap fees (Basic + Slipstream, every Aero chain) plus protocol-captured MEV. B: A plus third-party incentives (DefiLlama's basis). | **B**, but exclude incentives the Foundation funds itself. aero.xyz says it is depositing liquid AERO from its TGE budget; `0x7269…` still holds 28.94M. That is an issuer subsidy, not a user payment. |
| REVENUE | Fees minus what unstaked (fee-earning) LPs keep. | Staked-liquidity fees + the unstaked-fee cut (10% default on Slipstream per legacy docs) + third-party incentives. Do not deduct emissions here. |
| HOLDER REVENUE | A: everything paid to sAERO stakers (`NotifyFeesAmount` + incentive streams), gross. B: net of the issuer wallets' share. | **A**, with the insider share shown beside it. Fees locked in dead gauges are revenue but not holder revenue; when the Foundation reclaims them they go to the Foundation. |
| Third-party bribes | Count in fees and revenue, or holder revenue only. | Count in all three, so holder share cannot exceed 100%. Keep fees and incentives as separate sub-lines; the tracker already stores them apart. |
| Emissions to LPs | Deduct from revenue, or show as supply-side incentive (b). | **(b)**, at each day's price, with a memo line "revenue ÷ emissions". This matches Jake's principle that emissions go to activity with attributable revenue. |
| Momentum Fund buyback-and-burn | Add to holder revenue, or not. | Do not add. It is funded from the Fund's own stake revenue, already inside holder revenue. Count it as retirement only. |

Also decide whether the lines cover Aero as a whole (Velodrome chains included, one token now) or Base only. I recommend the whole, declared as a series break at 2026-10-22.

## (e) Buybacks, burns, issuer wallets

- **Legacy:** buy and max-lock, no burn. Docs: "Over 184M AERO has been acquired and locked to date via the PGF, Flight School, and other initiatives." Config holds six traced purchases: 1,906,696 AERO for 829,828 USDC, July to August 2026, by Safe `0x10b42e92…fa30`, forwarded to the PGF.
- **New:** the Momentum Fund's mandate is "buyback-and-burn AERO"; it "can also acquire sAERO stakes" (app.aero.xyz/docs/about/foundation).
- **Burn on chain: none verified.** 0xdead holds 0.0014 AERO and the zero address 0.
- Supply check: 2,127,000,000 + 16,126,977 minted + 5% team = 2,143,933,326, against a total supply of 2,143,525,510. The difference is 407,816. xAERO receipt tokens on Ethereum, OP and Arbitrum total 195,810 and may explain part (inferred; Arc, Ink and Robinhood not read). The rest is unexplained.
- Gauge-cap "burn" is non-minting ("tracked as unissued emissions and never minted"), not a burn of existing tokens.
- **Issuer-side wallets for insider share:** the six filing wallets in the table above, the buyer Safe `0x10b4…` (owns 2 sAERO with 0 staked), and the Splitter `0x9beb…1156`. The stakes sit in Relays, so insider share must be read as Relay principal, not as sAERO owned.

## (f) BROKEN / MISSING / NOT WIRED cells

**I could not enumerate the 7 / 19 / 3 / 5 cells without a build.** These are the causes I can identify from config, code and live sources:

| # | Cells | Cause | Fix |
|---|---|---|---|
| 1 | `circulating_supply_onchain`, `circulating_supply_used`, `market_cap_usd` and everything divided by market cap (the `mcap_stale` hit) | (c) | (c) fix + backfill |
| 2 | `voter_rewards_onchain_*` (fees, bribes, usd, apr, unpriced) | The epoch never finishes; the legacy `aero_voter` is refused after the cutover | (c) fix |
| 3 | CoinGecko series: circulating, `total_supply_coingecko`, FDV, volume, CoinGecko market cap | `coingecko_id` "aerodrome-finance" is now the legacy listing | Switch to id `aero` with a declared handover |
| 4 | `price_usd_llama` | Keyed to the legacy address `0x9401…` | Re-key to the new token. Check which token Coinbase AERO-USD now is (not verified). |
| 5 | `protocol_dex_volume_usd`, market share | Uses ids 3450, 4524, 8691; "Aero Basic" (9172) is missing and the legacy children are winding down | Add 9172 |
| 6 | Fees and revenue | DefiLlama's parent now includes Velodrome. I did not locate which slug the tracker reads. | Check the slug; declare the basis |
| 7 | Seven Foundation buyback series (`actual_buyback_tokens` and others) | The legacy managed-lock read ended at launch (RUNBOOK 11be); the wallets' stakes are now in Relays | Round 8 item 3 |
| 8 | Emissions by pool (subsidy targeting) | Reads legacy gauges, now deactivated | Needs a v3 per-gauge read |
| 9 | Insider share (likely NOT WIRED) | No v3 read of the issuer wallets' share of stake | Use the Relay principal already read by `_filing_locks` |
| 10 | A1 to A4 / CP detail cells | Aerodrome is in `DETAIL_PARKED`. The remaining blocker is "no primary source publishes circulating on this basis". This probably accounts for many of the 17 "route not filled" (not verified). | Decision 3 below |
| 11 | `max_supply` | AERO has none | n/a by design |

Legacy rebase rows (`emissions_tokens`): the legacy distributor still pays unmigrated veAERO, so the series is small but not zero.

## Round 8 (Aerodrome): proposed work items

1. **Unblock `aero_v3`:** own tier for the epoch, skip-if-stored, cached manager list, Multicall, timestamp-based block search. Backfill 10-30 to 11-16 from archive reads.
2. **Re-point market data to the new token:** CoinGecko `aero`, DefiLlama coins key, DefiLlama children (add 9172), each as a declared handover at 2026-10-22.
3. **Write `REVENUE_SPLIT["Aerodrome"]`** from the on-chain v3 fee and incentive reads, with DefiLlama as cross-check, Foundation-funded incentives excluded, and insider share from Relay principal.
4. **Momentum Fund watch:** read burns (Transfer to zero on the new token) and Fund purchases; reconcile the 0.41M supply difference; replace the legacy buy-and-lock series.
5. **Unpark:** record the circulating basis against CMC's formula (total − sAERO), which ties exactly, and close the "no primary source" blocker.

## Decisions for Jake

| # | Decision | Options | Recommendation |
|---|---|---|---|
| 1 | Issuer stakes handed out locked or bought and locked (503.09M) | A all out / B only never-released out / C all in | A, with a memo split once traced |
| 2 | Unclaimed Velodrome allocation (20.52M) | out / in | Out |
| 3 | Unpark condition | Wait for a project figure on our basis / accept reconciliation to CMC's formula | Accept the reconciliation: CMC = total − sAERO ties to the token, and our gap is fully named |
| 4 | FEES: include third-party incentives? | A no / B yes | B, excluding Foundation-funded incentives |
| 5 | HOLDER REVENUE gross or net of issuer wallets | gross / net | Gross, insider share shown |
| 6 | Fees locked in dead legacy gauges | revenue only / also holder revenue | Revenue only |
| 7 | Scope of the revenue lines | Base only / all of Aero including former Velodrome chains | All of Aero, series break at 10-22 |
| 8 | xAERO on other chains (about 0.2M) | add to total / leave out | Leave out until it passes 0.1% of supply; re-read monthly |
| 9 | Legacy token still minting (+5.7M since the cutover) | count via the capped old term (as now) / freeze at the cutover | As now. Add a watch: the reserve exceeds legacy AERO out by only about 1.9M. |

## Sources (all read 2026-11-16)

- Base chain reads: https://base-rpc.publicnode.com at block 58,384,321, plus archive blocks 52,918,950 / 52,983,750 / 53,286,150 / 53,880,000 / 55,360,321 / 56,904,000. xAERO supply via ethereum-rpc.publicnode.com, optimism-rpc.publicnode.com, arbitrum-one-rpc.publicnode.com.
- https://aero.xyz/aero-upgrade (launch, conversion, supply, emissions, Foundation incentives)
- https://aero.xyz/aero-economic-case (rebase removed, AERO Fed, Momentum Fund, old veAERO keeps rebases)
- https://app.aero.xyz/docs/about/emissions , https://app.aero.xyz/docs/about/aero-and-saero , https://app.aero.xyz/docs/about/foundation , https://app.aero.xyz/docs/security
- https://raw.githubusercontent.com/aerodrome-finance/docs/main/content/tokenomics.mdx , about.mdx (genesis allocation, legacy emissions, ecosystem wallets)
- https://api.coingecko.com/api/v3/coins/aero and https://api.coingecko.com/api/v3/coins/aerodrome-finance (cross-check)
- https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail?slug=aerodrome-finance (cross-check)
- https://api.llama.fi/summary/fees/aerodrome with dataType dailyFees / dailyRevenue / dailyHoldersRevenue / dailySupplySideRevenue (cross-check and methodology)
- Repo: `/home/user/Crypto-Tracker/fetch/aero_v3.py`, `/home/user/Crypto-Tracker/fetch/__init__.py` (`_abandon`, `_drop_stocks`, `TIER_BUDGET_S`), `/home/user/Crypto-Tracker/config.py` (lines 12958 onward, 19190 to 19360, 24030, 36682 onward), `/home/user/Crypto-Tracker/aero_migration.json`, `/home/user/Crypto-Tracker/fixtures/real_aerodrome.json`, `/home/user/Crypto-Tracker/RUNBOOK.md` 11be.

**Not verified:** the AERO Fed framework; what the legacy post-cutover mints are; the bought vs genesis split of issuer stakes; any Momentum Fund burn; which token Coinbase's AERO-USD is; the exact cells behind the 7 / 19 / 3 / 5 scoreboard; Jake's actual timeout phase messages.
