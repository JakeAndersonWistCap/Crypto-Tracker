# R8 Hyperliquid follow-ups (read-only; export hyperliquid-2026-11-16-220059-4b587de; checked 2026-11-17)

No repo file was touched and nothing was built. Scripts and the day-by-day table (`a_daily.csv`) are in the scratchpad.

## (a) Why HOLDER REVENUE 226,492,770 > REVENUE 224,456,021

**Cause: the as-of day is in one line only.** Both totals reproduce to the dollar from the export rows.

| Series, 2026-08-19..11-16 | Days covered | Sum ($) | Per covered day ($) |
|---|---|---|---|
| REVENUE (`revenue_total_usd`) | 89 (no 11-16: DefiLlama had not published it) | 224,456,021 | 2,521,978 |
| HOLDER REVENUE (`holder_value_revenue_usd`) | 90 (74 rows; multi-day `[span=Nd]` rows cover the rest) | 226,492,770 | 2,516,586 |
| Both, 08-19..11-15 (same 89 days) | 89 | 224,456,021 vs 224,323,456 | share 0.9994 |

- Holder revenue's 11-16 row is 2,169,315 (31,009.21 HYPE × 69.96). Without it, holder revenue is 132,565 below revenue.
- HOLDER SHARE 0.998 = 2,516,586 ÷ 2,521,978, so the ratio cell is already right; only the two $ totals sit on different days.
- DefiLlama now publishes 11-16 revenue (2,606,189, read today). With it, 90-day revenue is 227,062,210 and the share is 0.9975.
- A row-by-row "common days" sum (73 days: 194,092,988 vs 224,323,456 = 1.156) is an artefact. A span row holds 2–4 days of buys on one date, so compare on the span, not row dates.

**How each line is built**
- Revenue = DefiLlama `revenue_usd`; the AQA leg is 0 in the whole window (0 before 08-26, zeroed from 08-27).
- Holder revenue, 08-19..09-11 (24 days) = DefiLlama `holders_revenue_usd` (the `buyback_history` prepend). It equals `revenue_usd` on every stored day: 71,236,286 both. DefiLlama's adapter builds the two from the same terms (`dexs/hyperliquid-perp/index.ts`: Hyperliquid revenue × 0.99 + AQAv2 yield), so the share is 1.000 there by construction, not measured.
- Holder revenue, 09-12..11-15 (65 days) = Fund HYPE intake (`gross_burn_tokens`) × day price: 153,087,170 vs revenue 153,219,735 (0.9991).

**The near-equality on measured days is not like-for-like**
- The measured buys include the Fund spending two AQAv2 USDC lumps: 14,580,777 received 10-03/04 and 15,916,964 on 11-02/03 (`aqa_revenue_usd`, 30,497,740 in the window). Buys were 21.2M on the 10-05 span row and 30.1M over 11-02..04, against 2–3M on a normal day.
- DefiLlama's code adds AQAv2 as a daily accrual from its private indexer. Whether its figures actually carry about 30.5M over these days is not verified. Revenue ÷ fees was 0.815 on 08-19..26 and 0.814 on 08-27..09-11, i.e. no visible step.
- Price basis: tokens × CoinGecko day price, not $ spent. On 11-16 the Fund's fills (info `userFillsByTime`, read today) were 30,596.83 HYPE for $2,049,116 (average 66.97), against the stored 2,169,315 (+5.9%).
- Not causes: HIP-3, builder and HLP shares are all out of DefiLlama revenue (its code books "HIP-3 Deployer Distribution" as supply-side). `HL_HOLDER_SHARE_GAP`'s "conversion timing" wording does not explain this inversion.

**Correct presentation:** show both $ lines over the same days (to revenue's last day, 11-15) or print each line's covered days beside it; keep the per-covered-day share. Label the 24 history days as DefiLlama's model, and state the measured-days share separately.

## (b) stats-data fee paths

Export run_log, run 20261116T214614Z, source `hl_stats`, all four identical in form:
`hl_fees_daily_usd: stats-data.hyperliquid.xyz HTTP 403 from stats-data.hyperliquid.xyz — <Error><Code>AccessDenied</Code>… — NOTHING STORED` (same for `hl_fees_perps_usd`, `hl_fees_spot_usd`, `hl_hip3_deployer_fees_usd`). The same run stored `hl_exchange_volume_usd` (15 days) and `hl_hip3_dex_count=10`.

**No first-party published endpoint gives daily fees, a perps/spot fee split, or the HIP-3 deployer share. Switch the four off.**

- The real path list is in github.com/hyperliquid-dex/hyperliquid-stats-web: `constants/api.ts`, with base `NEXT_PUBLIC_DAT_URL = https://d2v1fiwobg9w6.cloudfront.net` in `.env`.
- All 42 listed paths were tested from here. 38 return 200, e.g. `daily_usd_volume` {time, daily_usd_volume}, `cumulative_inflow`, `daily_inflow` {time, inflow}, `cumulative_new_users`, `largest_users_by_usd_volume` {name, value}, `open_interest`, `funding_rate`.
- The only fee path in the list, `total_accrued_fees`, returns 403. So do `total_usd_volume`, `cumulative_users` and `liquidity_per_symbol`.
- The four guesses plus `fees`, `daily_accrued_fees`, `cumulative_fees`, `total_fees`, `daily_revenue` all return 403 on CloudFront.
- The CloudFront data is stale: most series end 2026-04-03.
- `stats-data.hyperliquid.xyz/Mainnet/` serves only `leaderboard` and `vaults` (200). Every stats path there returns 403, which is the bucket's answer for a path that does not exist, not a block on the machine.
- `https://stats.hyperliquid.xyz/` now 301-redirects to `https://hyperscreener.asxn.xyz/home` (ASXN, third party).

**Info API (POST api.hyperliquid.xyz/info), tested today**
- `exchangeFees`, `totalFees`, `feeStats`, `accruedFees`, `dailyFees` → 422 (no such type).
- `globalStats` → 200 {totalVolume, dailyVolume, nUsers, oi}: no fees.
- `userFees` → 200: `dailyUserVlm[].exchange` (15 days of volume) and the `feeSchedule` rates: no fee amounts.
- `perpDexs` → 200: 10 HIP-3 DEXs with `feeRecipient` (3 are null).
- First-party measurable: the Fund's `userFillsByTime` (px × sz = $ actually spent on HYPE, about 9 days deep) and its `userNonFundingLedgerUpdates`. That measures holder revenue in $, not exchange-wide fees.
- The deployer share via each `feeRecipient`'s ledger was not tested.
- DefiLlama's fee and HIP-3 split come from its private indexer (`LLAMA_HL_INDEXER`), which is not first-party.

## (c) Net supply change: will it clear on 2026-12-29?

**What forms it** (build_workbook `_released_staked_views` → `_market_cap_views` → `_circulating_change_views`)
- `circulating_change_tokens` = day-on-day change of `circulating_supply_used`, on consecutive days with the same basis. The cell needs 90 covered days.
- `circulating_supply_used` = `circulating_supply_first_party`, restated per day with `excluded_wallets_tokens`, `staked_noncirculating_tokens` and `team_wallet_tokens`. Their days before 10-12/13 come from the stored `*_replay` series (`HISTORY_PREPEND`).
- It is carried forward up to 3 days over `price_usd` days (`credibility.now_max_age`), with no CoinGecko fallback.
- `total_supply` is not used.

**circ_legs' "total_supply 0 of 450"** is irrelevant. `circ_legs.legs()` defaults to a `total_supply` leg when the spec has no `total` key, and Hyperliquid's spec is `first_party`. Hyperliquid stores `total_supply_gross` and `total_supply_coingecko`, never `total_supply`; 450 is the catch-up window 2025-08-23..2026-11-15.

**Days missing, 2026-09-30..11-16 (48 days)**

| Series | Missing days |
|---|---|
| `circulating_supply_first_party` (37 rows) | 10-02..10-04, 10-17, 10-22, 10-24, 10-30..11-01, 11-07..11-08 (11) |
| The three legs, after prepend | 10-17, 10-22, 10-24, 10-30..11-01, 11-07..11-08 (8, all inside the above) |
| `price_usd` | none |

- The restatement drops 0 of the 37 days.
- The longest hole is 3 days (twice), exactly the carry limit, so `circulating_supply_used` has a value on all 48 days on one basis.
- Simulated with daily rows from 11-17: as-of 12-28 gives 89 of 90 (opening 09-30 missing); **as-of 12-29 gives 90 of 90, so the cell forms.** No hole between 09-30 and today blocks it. This is simulated from the code path, not from a build.

**Risk:** a hole of 4 or more consecutive days from now on (e.g. a holiday gap) leaves a day uncovered and the cell n/a for the next 90 days. `tokenDetails.circulatingSupply` is a current read and cannot be backfilled. The fix is to keep runs no more than 3 days apart, including on 12-29 itself.

`git status` showed pre-existing uncommitted changes (SPEC.md, config.py, project_freeze.py, tests/test_r8_round.py); they are not from this analysis.
