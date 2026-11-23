# Round 19, item 4 — NEAR and Ethereum on BigQuery's free tier

Research only. No code, rule or config changed. No BigQuery query or dry run was made (not signed in here).
Every GB figure is one the repo already records, with its source, or is marked ESTIMATE. Read 2026-11-23.

## Answer first

- **The allowance is bigger than the repo assumes.** Google's free tier is 1 TiB a month = 1,099.5 GB in the
  ledger's units (decimal GB). The repo budgets 900 GB "of the free 1 TB". The untouched margin is 199.5 GB, not 100.
- **Daily top-ups are small.** NEAR about 88 GB a month, Ethereum about 41 GB a month, small NEAR reads about 4 GB.
  About 133 GB a month in all, 12% of the allowance. The repo reserves 162 GB for them (it adds a 1.2 margin and
  uses an older Ethereum estimate).
- **About 740 GB a month is left for history.** That is 3 months of NEAR history (about 230 GB each), or 18 months of
  Ethereum (about 41 GB each), or a mix: 2 NEAR months plus 6-7 Ethereum months.
- **One plan, both chains:** NEAR's key forms **2027-03-31**. Ethereum holds 24 months on the BigQuery definition
  after the **February 2027** run. NEAR alone could be one month faster (built 2027-03-01); that would leave Ethereum
  almost nothing until April.
- **Nothing needs to wait for that.** Both chains hold enough today for a labelled short window. On the fixtures:
  NEAR's last 90 days against the 90 before is **+80.6%** per token; Ethereum's last half against the half before
  is **−28.1%**. The 1-year rule reads n/a on both.
- **Ethereum's backfill moves no workbook figure as the code stands.** Before 2026-08-26 the workbook reads the
  Artemis CSV, not BigQuery. The backfill only serves Jake's 2026-11-04 decision ("the BigQuery definition is the
  series"), which is not merged and whose budget approval (`extend_approved`) is still empty.

## 1. The allowance and every consumer

**Google's terms** (read 2026-11-23):

| Term | Text | Source |
|---|---|---|
| Free queries | "The first 1 TiB of query data processed per month is free." | cloud.google.com/bigquery/pricing (page text fetched by curl) |
| Minimum | "minimum 10 MB data processed per table referenced by the query, and … per query" | same page |
| Sandbox | 1 TiB of processed query data each month, no credit card or billing account; 10 GiB storage; tables expire after 60 days | docs.cloud.google.com/bigquery/docs/sandbox (read through a page summariser, not the raw page) |
| Dry runs on clustered tables | "the estimation of the number of bytes billed for a query is an upper bound" | docs.cloud.google.com/bigquery/docs/best-practices-costs (same caveat) |

1 TiB = 1,099,511,627,776 bytes = 1,099.5 GB as the repo's ledger counts (bytes ÷ 1e9). The repo's budget is
900 GB (`near_bigquery.monthly_budget_bytes`). Jake's project `near-data-510309` has no billing, so the free
allowance is a hard stop (config.py, near_bigquery comment, Jake 2026-10-01). Both chains bill one ledger.

**Every BigQuery consumer in the repo**

| Consumer | Where | GB | Basis |
|---|---|---|---|
| NEAR P2P daily top-up | fetch/near_bigquery.py | 2.84 a day = 85-88 a month; reserved at ×1.2 = 102-106 | Jake's dry run 2026-11-17 (`NEAR_P2P_HISTORY.topup_gb_per_day`). It was 7.16 on 2026-10-01 (fixture run log). |
| NEAR circulating supply | same | 0.01 a run = 0.3 a month | fixture run log 2026-10-01 |
| NEAR token census | same, every 30 days | 1.59 | fixture run log 2026-10-01 |
| NEAR transactions ex Kai-Ching | same | about 0.07 a day = 2.1 a month | config comment 2026-10-02, an expectation, not a measured bill |
| NEAR header burn and mint | same | MBs a day (84 days of history billed 0.29) | SPEC R12.5, Jake's dry run 2026-11-19 |
| Ethereum P2P weekly top-up | fetch/eth_settlement.py | 9.48 a week = 41 a month. The ledger plan uses 13 a week = 56 a month. | 777 GB over 82 weekly pieces, Jake's dry runs (RUNBOOK P5-1.9 step 3). 13 is an agent estimate from table sizes, 2026-10-19. |
| Ethereum P2P read daily instead | not wired | 5.7 a day = 170 a month | ESTIMATE from table sizes, 2026-10-19 (config G7-8). Never measured. |
| **One-off:** NEAR December 2025 chunk (2025-12-01..28) | the daily run does it by itself on 12-01 | 211.1 | ESTIMATE: 28 days × 7.54. The 31 days to 2025-12-28 dry-ran 233.7 (Jake 2026-11-17). |
| **One-off:** NEAR history, per calendar month | near_p2p_history.py | 211-234 a month of history (7.54 a day); 2,684 back to the 2024-12-10 floor | ESTIMATE before November 2025; each chunk's own free dry run decides |
| **One-off:** Ethereum history 2024-11-04..2026-05-31 | settlement_extend.py backfill | 777 (82 pieces) = 41 a month of history | Jake's dry runs, total only; per-piece figures are not on file |
| **One-off:** Ethereum gap 2026-08-26..today | the weekly read | 0 if the weekly read has kept up; at most 13 weeks × 9.48 = 123 | Not verified: Jake's store is not here |
| **One-off:** staked-ETH rebuild | staked_eth_rebuild.py | NOT ON RECORD. Capped at 200 a query. | SPEC P5-1.12: "NOT VERIFIED here: … the query's cost". Each rerun bills again. |
| Optional: NEAR wallet inflows, stage 1 | check_offline_items.py | 242.37 | Jake's dry run 2026-10-18. Whether it ran in November is not on file. Stage 2 needs its own approval. |
| Optional: Morpho claims split | morpho_claims.py | not on record; refuses above 300 | on demand only |

Why NEAR costs 5.6 times Ethereum per month of history: 88% of NEAR's bill is the `args` text column, which the
table does not cluster (config `NEAR_P2P_HISTORY` comment; 2,001.57 GB with `args` against 242.37 GB without, G2-A6).

## 2. One schedule, from December 2026

**The split rule.** Each month, in this order:

1. **Top-ups first.** The repo's reserve: NEAR's days left × 2.84 × 1.2, plus 56 GB for Ethereum's weekly read.
2. **Anything owed inside the live window.** NEAR's December 2025 chunk (the daily run takes it). Ethereum weeks
   since 2026-08-26, if any are missing.
3. **Until both chains hold 12 months on BigQuery's definition, the chain with less goes first.** In December that
   is Ethereum (6 months held against NEAR's 12).
4. **Then NEAR takes whole months, newest first; Ethereum's weekly pieces fill what is left.** A NEAR chunk is
   kept whole or not at all (R18.N3) and costs about 230 GB. An Ethereum piece costs about 9.5 GB, so it fits any
   remainder. Once Ethereum is done, NEAR takes three chunks a month.

Why: Ethereum's history is cheap (the whole two years cost 777 GB, three NEAR months), so finishing it barely
slows NEAR. NEAR's chunks are lumpy, so the small Ethereum pieces use the room a NEAR chunk cannot.

**The schedule** (GB; "room" = 900 − NEAR reserve − 56, as `near_p2p_history.plan` computes it; history held is
counted to the month's last day)

| Run month | Room for history | NEAR history read | GB | Ethereum history read | GB | Left | NEAR held from (months) | Ethereum held from (months) |
|---|---|---|---|---|---|---|---|---|
| 2026-12 | 530.7 (after the December chunk, 211.1) | Nov 2025 | 226.2 | 31 weeks, back to 2025-10-27 | 293.7 | 10.8 | 2025-11-01 (14.0) | 2025-10-27 (14.2) |
| 2027-01 | 741.8 | Oct, Sep 2025 | 459.9 | 29 weeks, back to 2025-04-07 | 274.8 | 7.1 | 2025-09-01 (17.0) | 2025-04-07 (21.8) |
| 2027-02 | 752.0 | Aug, Jul 2025 | 467.4 | 22 weeks, back to 2024-11-04 (done) | 208.5 | 76.1 | 2025-07-01 (20.0) | 2024-11-04 (27.8) |
| 2027-03 | 741.8 | Jun, May, Apr 2025 | 686.1 | — | 0 | 55.7 | 2025-04-01 (24.0) | 2024-11-04 (28.8) |
| 2027-04 (background) | 745.2 | Mar, Feb, Jan 2025 | 678.5 | — | 0 | 66.7 | 2025-01-01 (27.9) | — |
| 2027-05 (background) | 741.8 | 2024-12-10..31 (the floor) | 165.9 | — | 0 | 575.9 | 2024-12-10 (29.7) | — |

Totals: NEAR 2,895 GB (the December chunk 211.1 + history 2,684), Ethereum 777 GB, over six months.

Notes on the schedule:

- Every NEAR figure before November 2025 is an estimate at 7.54 GB a day. December is tight (10.8 GB left). If a
  dry run comes in higher, Ethereum pieces give way first; nothing overruns, because the ledger refuses.
- The April and May rows are not needed for any key. They fill the key's four trajectory quarters and reach the
  floor. They can run later or never.
- If Ethereum's weeks since 2026-08-26 are missing on the store (up to 123 GB), they come first in December and
  Ethereum's older pieces slip into the 76 GB spare in February. No NEAR date moves.
- A NEAR history chunk still needs Jake's dated approval each month (`NEAR_P2P_HISTORY_APPROVAL`, with `chunks`
  set to 1, 2, 2, 3). Ethereum's backfill needs `extend_approved` declared once.

## 3. When each chain reaches 6, 12 and 24 months

"Held" = the daily settlement series hole-free back from its newest day. The 365-day sum forms at 12 months
(r0 + 364). The key, its 1-year change, forms at 24 months (r0 + 729). This is `reader._settlement_forms_on`.

| Chain, basis | 6 months | 12 months (365-day sum forms) | 24 months (key forms) |
|---|---|---|---|
| **NEAR**, with this plan | held now (from 2025-12-29, SPEC R18.N1) | **2026-12-01** run (the December chunk; data to 11-30) | **2027-03-31** |
| NEAR, daily run only, no history | held now | 2026-12-01 | 2027-11-30 |
| NEAR, R18's one chunk a month | held now | 2026-12-01 | 2027-05-31 |
| NEAR alone, three chunks a month | held now | 2026-12-01 | 2027-03-01 run (data to 02-28) |
| **Ethereum, BigQuery definition only**, with this plan | 2026-11-26 (held from 2026-05-28; no backfill needed) | **December 2026** run | **February 2027** run |
| Ethereum, BigQuery only, no backfill | 2026-11-26 | 2027-05-27 | 2028-05-26 |
| **Ethereum, as the code reads it today** (Artemis CSV to 2026-08-25, rebuild after) | held | held, once the days since 2026-08-26 are stored | **between today and 2027-08-25**: it depends on the CSV's first day, which is not in the repo (below) |

Ethereum's CSV. The file is on Jake's machine and is not committed. The fixture holds it from 2025-09-08 only
because a dump is cut to 400 days. `ARTEMIS_SETTLEMENT["check"]` records a 365-day sum to 2026-08-25, so the file
reaches back to 2025-08-26 at least, which puts the key no later than 2027-08-25. If the file starts on or before
2024-11-25, Ethereum holds two years now and the key forms as soon as the days since 2026-08-26 are stored, with
no backfill at all. One read-only check on Jake's store settles it:
`SELECT MIN(date), MAX(date), COUNT(*) FROM metrics WHERE project='Ethereum' AND metric='settlement_volume_usd';`

Two things under the current rule that a date alone hides:

- The stitched Ethereum series changes measuring point on 2026-08-26 (the rebuild read +6.8% against Artemis on
  the overlap, G10-4). A key formed across it compares a rebuild year with an Artemis year.
- Ethereum's P2P is read once a week, so the series' newest day runs 1 to 8 days behind. The reader gives no
  "forms on" date once it is more than 7 days behind (`SETTLEMENT_LIVE_DAYS`). INFERRED from the code, not seen on
  the store.

## 4. What each key would read today on shorter windows

**On fixture data, as of 2026-10-13** (fixtures/real_near.json, fixtures/real_ethereum.json, Jake's store dump).
NEAR's settlement there runs 2026-04-01..2026-10-12 (195 days, no hole). Ethereum's is the Artemis CSV,
2025-09-08..**2026-08-25** (352 days, no hole); it stops there. These are not today's store.

Formula, the key's own with a shorter window: per token = Σ settlement over the window ÷ circulating supply on the
window's last day. Change = per token (later window) ÷ per token (earlier window) − 1. Annualised level = per token
× 365 ÷ days. Circulating is the stored `circulating_supply` (CoinGecko); the workbook's `circulating_supply_used`
view is not in a dump and may differ slightly.

| Chain | Window | Later window | Earlier window | Per token, annualised: later / earlier | Change |
|---|---|---|---|---|---|
| NEAR | 90 v 90 days | 2026-07-15..10-12: $23.54bn | 2026-04-16..07-14: $12.96bn | $72.96 / $40.40 | **+80.6%** |
| NEAR | all held, in halves (97 v 97) | 2026-07-08..10-12: $24.37bn | 2026-04-02..07-07: $13.54bn | $70.07 / $39.20 | +78.8% |
| NEAR | 6 months v 6 before | cannot form: 195 days held, 360 needed | | | n/a |
| NEAR | 90 days v the same 90 a year earlier | cannot form: nothing held before 2026-04-01 | | | n/a |
| NEAR | **the 1-year rule** | cannot form: no 365-day sum (fixture: sum 2027-03-31, key 2028-03-30) | | | **n/a** |
| Ethereum | 90 v 90 days | 2026-05-28..08-25: $1,439.2bn | 2026-02-27..05-27: $1,391.5bn | $48,365 / $46,761 | **+3.4%** |
| Ethereum | all held, in halves (176 v 176) | 2026-03-03..08-25: $2,777.4bn | 2025-09-08..2026-03-02: $3,863.3bn | $47,728 / $66,384 | **−28.1%** |
| Ethereum | 6 months v 6 before (180 v 180) | cannot form: 352 days held, 360 needed | | | n/a (the 176-day halves are the nearest) |
| Ethereum | 90 days v the same 90 a year earlier | cannot form: 455 days needed | | | n/a |
| Ethereum | **the 1-year rule** | cannot form: 352 days, 365 needed for one sum, 730 for the key; the series stopped 49 days before the dump | | | **n/a** |

Reading them:

- NEAR's +80.6% is mostly one month. Daily settlement averaged $142M in August and $405M in September 2026. Both
  legs rose: DEX +64.3%, P2P +104.2% per token (90 v 90).
- Ethereum's two windows disagree in sign because the fall was early: $28.9bn a day in October 2025, $14.9bn in
  March 2026, flat since. The 90-day window sees only the flat part.
- "Annualised" here scales the levels to a year. Compounding the change itself to a year is not sound on short
  windows (NEAR's +78.8% over 97 days would compound to about +790%).

**The DEX leg alone, live** (DefiLlama `/overview/dexs/{chain}`, read 2026-11-23, to 2026-11-22; dollars, not per
token; a leg, not the key). DEX is 57% of NEAR's settlement on the fixture and 9.6% of Ethereum's.

| Chain | Last 90 days | v the 90 before | v the same 90 a year earlier | Last 180 v the 180 before |
|---|---|---|---|---|
| NEAR | $14.62bn | +72.6% | +75.6% | +55.0% |
| Ethereum | $131.80bn | +32.5% | −54.2% | −18.7% |

So a year-on-year view is already possible on the free leg, and for NEAR it agrees with the short window in sign
and size. For Ethereum it shows what a short window misses: up on the quarter, down by half on the year.

**What becomes available on the store, and when**

| Window | Days needed | NEAR | Ethereum (BigQuery only) | Ethereum (CSV + rebuild) |
|---|---|---|---|---|
| 90 v 90 | 180 | now | 2026-11-26 | now |
| 6 months v 6 before | 360 | 2026-12-01 run | December 2026 run (with the plan) | now |
| 90 v the same 90 a year earlier | 455 | January 2027 run | December 2026 run | now if the CSV starts by 2025-08-26 (it does, by the config check) |
| The 1-year rule | 730 | 2027-03-31 | February 2027 run | see section 3 |

## 5. Decisions for Jake

**D1. A shorter labelled window until two years exist?**
Options: (a) none: both keys read n/a until 2027; (b) 90 v 90: forms today, but swings (NEAR +80.6% on one month);
(c) 6 months v 6 before, annualised levels: forms for both chains at the December run; (d) 90 days v the same 90 a
year earlier: closest in meaning to the rule, forms for both by January 2027.
**Recommend (c), with (d) shown beside it from January.** Label it "interim — 6 months on 6 months, not the
1-year rule", in its own column beside the rule's cell, not ranked. This is the pattern already used for
"Fees ÷ issuance, Q0 (interim)" (SPEC P2b-4). Drop it per chain on the day the rule forms: NEAR 2027-03-31.
Ranking on it is a separate decision; do not rank on it while the two chains sit on different measuring points.

**D2. How to split the monthly allowance.**
Options: (a) this plan: NEAR 2, 2, 2, 3 chunks with Ethereum filling: NEAR key 2027-03-31, Ethereum two years by
February, 3,672 GB over six months; (b) NEAR only at three a month: NEAR's key one month sooner (built 2027-03-01),
Ethereum about 5 weeks of history a month; (c) R18's one NEAR chunk a month: 2027-05-31.
**Recommend (a).** It costs NEAR at most a month and finishes Ethereum.

**D3. Does Ethereum need the BigQuery backfill at all?**
Options: (a) declare `extend_approved` and run it as scheduled (777 GB), which only pays off with the 2026-11-04
decision to make BigQuery the whole series; (b) keep the CSV as history and spend nothing; the key then forms on a
stitched series with a +6.8% step at 2026-08-26.
**Recommend: first run the one SQL line in section 3.** If the CSV starts by 2024-11-25, (b) gives a key now and
(a) becomes a clean-up that can run in the spare room. Otherwise (a).

**D4. The budget figure.**
Options: (a) keep 900 GB; (b) raise it to 1,000 GB: 99.5 GB still unused, 100 GB more history a month, about ten
Ethereum weeks or the slack that makes December safe.
**Recommend (b).** The free tier is 1 TiB = 1,099.5 GB, not 1,000. This is a budget setting, not a metric rule.

**D5. Cutting the `args` cost without changing a rule.**
Options: (a) leave it: R18 found no material cut inside the frozen rules (the dates already prune, only the needed
columns are named, one query serves both legs); (b) read TRANSFER from `transaction_actions`: cheaper, but it
drops transfers made by receipts, a change of measuring point; (c) never read a day twice, which the state file
already ensures.
**Recommend (a).** The saving that is free is D4, plus using Ethereum's small pieces to fill each month's remainder.

**D6. Ethereum's weekly piece size (cost only).**
The P2P query reads the whole `contracts` table on every piece, so each piece carries a fixed cost (INFERRED from
sql/ethereum/bigquery_p2p_daily.sql; the two estimates on file, 5.7 GB for a day and 13 GB for a week, imply about
4.5 GB fixed). Four-week pieces would stay under the 60 GB cap.
**Recommend: one free dry run of a four-week piece beside four weekly ones before the backfill starts.** If it is
materially cheaper, backfill in four-week pieces. The result is the same days and the same SQL.

**D7. The staked-ETH rebuild.**
Its cost is not on record and each rerun bills again (cap 200 GB). After R18.E4 it stores nothing unless the overlap
passes. **Recommend: no rerun in a month with history chunks unless the dry run is read first.**

## Could not verify

- **Jake's store.** What it holds today for either chain: NEAR "from 2025-12-29" is SPEC R18.N1's statement;
  whether Ethereum's weeks since 2026-08-26 are stored is not on file. November's ledger use so far is unknown.
- **The Artemis CSV's first day.** The file is not in the repo. Known only: it reaches 2025-08-26 or earlier.
- **Ethereum's per-piece bytes.** Only the total, "about 777 GB" over 82 pieces, is recorded (RUNBOOK). A daily
  cadence has never been measured. The 4.5 GB fixed cost per query is inferred, not measured.
- **The staked-ETH rebuild's bytes.** Never recorded.
- **NEAR's GB a day before November 2025.** An estimate at 7.54. The year to September 2026 dry-ran 1,909.8 GB
  (fixture run log, 2026-10-01), which fits it back to October 2025 only.
- **NEAR's top-up size now.** 2.84 GB a day is from 2026-11-17. It was 7.16 on 2026-10-01; why is not on file.
- **Google's terms.** The pricing sentences were read from the page's own text. The sandbox and cost-practice pages
  were read through a summariser, so their quotes are second-hand. Not established: whether the free 1 TiB in a
  no-billing project is counted per project, and at what hour the month resets.
- **Fixture figures.** All of section 4's settlement numbers are the 2026-10-13 dumps (400 days each), computed
  with stored `circulating_supply`, not the workbook's `circulating_supply_used` view. No build was run.
- **Whether a key formed across Ethereum's 2026-08-26 handover would be accepted.** The proposed break rule
  (docs/p2f_a1_key_break_rule.md) is not merged; under the code as it is, it forms.
