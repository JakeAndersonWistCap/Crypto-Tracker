# Round 23 - NEAR's P2P history: the query shapes and the four paths

Written 2026-11-25. Research and a plan; no BigQuery query or dry run was made (not signed in here, nothing authenticated).
Every GB below is a figure the repo records (source named) or is marked ESTIMATE. Dates in the paths table were computed
with Python (`near_p2p_history.chunk_before`, `est_gb`, and `reader._settlement_forms_on`'s rule: the 365-day sum forms on
r0+364, the key on r0+729).

## 1. What the repo records about the bytes

| Fact | Figure | Source |
|---|---|---|
| P2P query, 31 days 2025-11-28..12-28 | 233.7 GB = 7.54 GB a day | Jake's dry run 2026-11-17 (`NEAR_P2P_HISTORY.est_gb_per_day`) |
| One day's top-up now | 2.84 GB | Jake 2026-11-17, after the April 2026 fall in activity (receipt_actions rows 516.1M in 2026-03, 104.3M in 2026-04) |
| `args` share of what the query bills | ~88% | config comment, G2-A6: the same column over 2025-09-22..2026-10-17 dry-ran 2,001.57 GB against 242.37 GB for the narrow columns alone |
| Table layout | `receipt_actions`: day-partitioned on block_date, NOT clustered. `ft_events`: day-partitioned, clustered on contract_account_id | config R18 block (Jake's metadata run 2026-10-01) |
| Table sizes (2024 copy, logical) | receipt_actions 5,937.9 GB; transaction_actions 2,543.9; execution_outcomes 4,570.5 | third-party copy of `bq show`, lastModified 2024-11-27 (config R18 block) |

Where the bytes go in S1 (ESTIMATE from the 88% figure): of 7.54 GB a day, ~6.6 GB is `receipt_actions.args` (read by the
native-NEAR TRANSFER leg only) and ~0.9 GB is every other column of both scans (`contracts` and `native` CTEs, plus
`ft_events`). NEP-141 transfers do NOT read `args`: they come from `ft_events`, already clustered.

BigQuery on-demand billing, as used here: bytes billed = the full size of every column the query touches, over the
partitions the `block_date` filter keeps; a WHERE on any other column prunes nothing in a table that is not clustered on it;
a table named twice is scanned (and its columns counted) twice; the minimum is 10 MB per table.

## 2. The four shapes

| Shape | What it reads | GB a day, 2025-era activity | GB a day, today's activity | GB a month (30.4 d), 2025-era / today | Basis |
|---|---|---|---|---|---|
| **S1** current query (`sql/near/bigquery_p2p_daily.sql`) | receipt_actions twice (narrow, then narrow + args), ft_events | 7.54 | 2.84 | 229 / 86 | MEASURED (Jake 2026-11-17) |
| **S2** only the needed columns selected | the same columns | ~7.5 | ~2.8 | ~228 / ~86 | ESTIMATE: no saving. S1 already names only the 5 + 7 columns it uses (config: "the query already names only the 5 + 7 columns"); `SELECT *` is not in play. At most the second narrow scan could be merged (<= ~0.1 GB a day, ESTIMATE) |
| **S3** ft_transfer `EVENT_JSON` from `execution_outcomes` logs instead of `args` | adds the `logs` column of execution_outcomes | >= 7.5 | >= 2.8 | >= 229 / >= 86 | ESTIMATE, a premise problem: the NEP-141 leg never read `args` (ft_events carries it, clustered), and a native-NEAR TRANSFER leaves no EVENT_JSON in the logs, so the logs cannot replace the `args` read; they would be an extra scan. Size of `logs` is not on record: the dry run in section 4 settles it |
| **S4** receipt_actions filtered by `receiver_id` for the token contracts | receipt_actions `args` in full | ~7.5 | ~2.8 | ~228 / ~86 | ESTIMATE: the table is not clustered on the receiver, so the filter prunes nothing; `args` is still read for every row of every kept day. And it would parse `ft_transfer` calls out of `args`, which is what ft_events already gives cheaply |
| S5 (not asked; noted in config, NOT wired) native TRANSFER from `transaction_actions` | transaction_actions `args` | ~3.8 | ~1.4 | ~115 / ~43 | ESTIMATE: config says transaction_actions is 43% of receipt_actions' bytes (2024 copy): 0.9 + 6.6 x 0.43. It drops transfers made by receipts (delegate actions, wallet contracts): a change of measuring point, a decision for Jake |

"Today's activity" scales the 2025-era rate by 2.84 / 7.54 = 0.377 (ESTIMATE for every row but S1). The days that matter
for the holes and for the history (Dec 2025 - Mar 2026 and before) are the 2025-era rate.

**Reading: on this evidence no listed shape beats S1.** S2 and S4 read the same `args`; S3 adds a scan. The only route that
could cut the bill, S5, changes what is measured. The dry runs below are what could overturn that.

## 3. The dry runs Jake must run (free, nothing billed)

Placeholders: `<PROJECT>` = the BigQuery project that runs the job (config: `near-data-510309`); `<TOKENS>` = the census
list, comma separated (print it with the first command; the seed list is `usdt.tether-token.near,wrap.near,17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1`). Run from the repo root. Each prints "this query will process N bytes".

```
# 0. the token list in the state, and that receipt_actions is not clustered (metadata only, free)
python -c "import json,sys; from fetch.logcache import LogCache; print(','.join(json.load(open(LogCache().root/'near-bq.json'))['tokens']['list']))"
bq show --format=prettyjson bigquery-public-data:crypto_near_mainnet_us.receipt_actions | grep -i -A3 clustering

# D1  S1, one current day (expect ~2.84 GB)
bq query --project_id=<PROJECT> --use_legacy_sql=false --dry_run --parameter=d0:DATE:2026-11-10 --parameter=d1:DATE:2026-11-10 --parameter='tokens:ARRAY<STRING>:<TOKENS>' < sql/near/bigquery_p2p_daily.sql

# D2  S1, one day in the window the holes sit in (expect ~7.5 GB; this is the figure the gap cost rests on)
bq query --project_id=<PROJECT> --use_legacy_sql=false --dry_run --parameter=d0:DATE:2026-01-20 --parameter=d1:DATE:2026-01-20 --parameter='tokens:ARRAY<STRING>:<TOKENS>' < sql/near/bigquery_p2p_daily.sql

# D3  S2/S4 split: the narrow columns alone, then `args` alone, one day (their sum should be ~D1; args ~88%)
bq query --project_id=<PROJECT> --use_legacy_sql=false --dry_run 'SELECT block_date, action_kind, receipt_predecessor_account_id, receipt_receiver_account_id FROM `bigquery-public-data.crypto_near_mainnet_us.receipt_actions` WHERE block_date = DATE "2026-11-10"'
bq query --project_id=<PROJECT> --use_legacy_sql=false --dry_run 'SELECT block_date, args FROM `bigquery-public-data.crypto_near_mainnet_us.receipt_actions` WHERE block_date = DATE "2026-11-10"'

# D4  S4: the same `args` read WITH the receiver filter (equal to the args line of D3 means the filter prunes nothing)
bq query --project_id=<PROJECT> --use_legacy_sql=false --dry_run --parameter='tokens:ARRAY<STRING>:<TOKENS>' 'SELECT block_date, receipt_receiver_account_id, args FROM `bigquery-public-data.crypto_near_mainnet_us.receipt_actions` WHERE block_date = DATE "2026-11-10" AND receipt_receiver_account_id IN UNNEST(@tokens)'

# D5  S3: what the logs column costs for one day (column name `logs` per NEAR's docs; check with `bq show` if refused)
bq query --project_id=<PROJECT> --use_legacy_sql=false --dry_run 'SELECT block_date, logs FROM `bigquery-public-data.crypto_near_mainnet_us.execution_outcomes` WHERE block_date = DATE "2026-11-10"'

# D6  S5: transaction_actions args, one day (changes the measuring point; only to size it)
bq query --project_id=<PROJECT> --use_legacy_sql=false --dry_run 'SELECT block_date, action_kind, args FROM `bigquery-public-data.crypto_near_mainnet_us.transaction_actions` WHERE block_date = DATE "2026-11-10"'
```

Decision rule: a shape wins only if its dry-run bytes for the same day are at least 30% under D1 (and, for S3/S5, if it
reproduces the stored day within the tolerance in section 4). If D4 equals the args line of D3 and D5 is above ~1 GB, S2-S4
are closed and S1 stays.

## 4. One real-day validation recipe (for whichever shape the dry runs favour; written for S1 as the control)

**The day: 2026-11-10.** It is inside the stored unbroken run (Jake's store: r0 2026-02-19 to now), so
`p2p_transfer_volume_usd` for it is in `metrics.db`; at today's activity it bills ~2.84 GB (0.3% of the 1,000 GB month).

1. Dry run (D1 above, shape's own SQL) - note the bytes. Then run it capped just over the dry run:
```
bq query --project_id=<PROJECT> --use_legacy_sql=false --nouse_cache --maximum_bytes_billed=<dry_bytes*1.05+10485760> --format=json --parameter=d0:DATE:2026-11-10 --parameter=d1:DATE:2026-11-10 --parameter='tokens:ARRAY<STRING>:<TOKENS>' < sql/near/bigquery_p2p_daily.sql > /tmp/near_2026-11-10.json
bq ls -j -n 1 --project_id=<PROJECT>        # the job id
bq show --format=prettyjson -j <JOB_ID> | grep -i "totalBytes"     # totalBytesProcessed vs totalBytesBilled
```
Compare: `totalBytesBilled` should be the dry-run bytes rounded up to the next MB (the repo's `billing_cap` allows
x1.05 + 10 MB; Jake's 2026-10-01 census dry-ran 1,592,724,227 and BigQuery wanted "1592786944 or higher"). Billed above
dry run x 1.05 means the dry run under-reports for this shape: do not wire it.

2. Tie the day to the store. The stored value is `native NEAR x the store's price_usd that day + wNEAR x the same + each
other token at DefiLlama's price (the state's cached prices)`; this re-does it from the job's rows:
```python
import json, sqlite3, decimal
from fetch.logcache import LogCache
rows = json.load(open("/tmp/near_2026-11-10.json"))           # [{day, token, amount, n}, ...]
st = json.load(open(LogCache().root / "near-bq.json"))
day = "2026-11-10"
con = sqlite3.connect("file:metrics.db?mode=ro", uri=True)
near_px = con.execute("select value from metrics where project='Near' and metric='price_usd' and date like ?", (day + "%",)).fetchone()[0]
stored = con.execute("select value from metrics where project='Near' and metric='p2p_transfer_volume_usd' and date like ?", (day + "%",)).fetchone()[0]
px = st["prices"].get(day, {})
D = decimal.Decimal
usd = 0.0
for r in rows:
    t, a = r["token"], D(r["amount"])
    if t == "NEAR":
        usd += float(a) * near_px
    elif t == "wrap.near":
        usd += float(a / D(10) ** 24) * near_px
    else:
        p = px.get("near:" + t)
        if p and p[0] is not None and p[1] is not None:
            usd += float(a / D(10) ** int(p[1])) * p[0]
print(day, "query", round(usd, 2), "stored", round(stored, 2), "diff %", round(100 * (usd / stored - 1), 4))
```
**Tie-out tolerance.** Same SQL (S1 as control): within 0.01% (it is the same rows and the same prices; any larger
difference is a price cache or a late-arriving row and is explained before anything is wired). A different shape on the same
definition (S2, S4): within 0.1%. A shape that changes what is counted (S3, S5): no tolerance applies - the difference in %
is reported to Jake as a change of measuring point, not wired by default. The native-NEAR row's `amount` string must be equal
to the S1 row's to the last digit for S2 and S4.

## 5. The four paths (dates computed with Python)

Common: today 2026-11-25; Jake's store r0 = 2026-02-19 (the workbook reads NRR 2027-02-18, key 2028-02-18, which reproduces).
"NRR" = the 365-day sum, forms r0+364. "Key" = r0+729. The history figures use 7.54 GB a day (ESTIMATE before 2025-11-28; each
chunk's own dry run replaces it).

| Path | What it costs | r0 after it | NRR (365-day sum) forms | Key forms | Notes |
|---|---|---|---|---|---|
| **(i) daily run only** (what Jake's workbook shows) | 0 extra GB | 2026-02-19 | 2027-02-18 | 2028-02-18 | The R22 reading; unchanged. |
| **(ii) holes only, via `near_p2p_history.py gaps` + the R23 daily top-up** | The days `gaps` prints x 7.54 GB. If the store's P2P simply starts 2026-02-19 and the whole stretch 2025-12-29..2026-02-18 is unread: 52 days = **392 GB** (a month's room; free, the ledger spreads it). One isolated hole day: 7.5 GB | 2025-12-29 (best case, all 52 days read; with fewer days read r0 stops at the first day still missing) | **2026-12-28** | **2027-12-28** | Free tier. The daily run reads it itself, newest first, at most 3 hole chunks of <= 31 days a run, cut to the month's room. |
| **(iii) a cheaper shape on the free tier** (history on top of (ii), ~740 GB a month spare, from the 2026-12 run, 3 chunks a month) | S1 at 7.54: 3 months of history a month (~230 GB each). A shape at ~3.8 GB a day (the S5 ESTIMATE): 6 months a month. At 2.84 (today's activity, only if older months were that cheap - they are not): 8 months | S1: 2025-10-01 (2026-12 run), 2025-07-01 (2027-01), 2025-04-01 (2027-02), **2025-01-01 (2027-03)**, floor 2024-12-10 (2027-04). ~3.8 GB/day: 2025-07-01 (2026-12), **2025-01-01 (2027-01)**, floor (2027-02) | NRR forms at the first build after the 2026-12 run (r0+364 <= 2026-12-01 from 2025-10-01) | S1: r0 2025-01-01 -> 2026-12-31, so **the 2027-03-01 run** (data to 02-28). ~3.8 GB/day: the 2027-01-01 run. At the floor: 2026-12-09 | No listed shape is cheaper than S1 on today's evidence (section 2), so the S1 row is the realistic one. December's own chunk (2025-12-01..12-28, ~211 GB) comes out of the same room if the daily window has not read it. |
| **(iv) billed one-shot, `bigquery_backfill.py` - OPTIONAL, OFF** | From the store's r0 back to the floor 2024-12-10: 436 days x 7.54 = **3,287 GB = 2.99 TiB = $18.69** at $6.25/TiB (upper bound, no free tier taken off; **$12.44** after the month's free 1 TiB). From 2025-11-28 (the figure config calls ~2.7 TB): 2,662 GB, $15.13 ($8.88). Under the 4,000 GB cap. Ethereum's part is separate (~0.8 TB if the Artemis CSV does not already reach 2024-11-25: `gaps` prints its MIN) | 2024-12-10 | 2025-12-09 (already past: forms at the first build after the fill) | **2026-12-09**, or the first build after the fill | Not enabled: billing off, `BIGQUERY_BILLING.billing_enabled` False. Built, dry-run ready: `python bigquery_backfill.py plan` / `dry-run`. |

Reading the table: (ii) is free and is the only path that moves the NRR in December 2026 (to 2026-12-28 instead of
2027-02-18). On the free tier the key waits for 2027-03 under S1 (iii) against 2026-12-09 for the billed fill.
INFERRED, not seen: that every day 2025-12-29..2026-02-18 is the unread stretch. `python near_p2p_history.py gaps` on
Jake's store prints the actual days, the count the cost rests on, and the first stored day, which settles it.

## 6. The 2026-02-19 break: what is seen, what is inferred

- SEEN in the repo: NEAR settlement is not a stored series; it is DEX (DefiLlama) + P2P (BigQuery) per day, a day only where
  both hold (`build_workbook._rebuilt_settlement_view`); a P2P day is stored only with NEAR's same-day `price_usd`
  (`NearBigQuery._value`). The break rule in `reader._settlement_forms_on` is only "a missing day". The fixture dump of
  2026-10-13 holds the DEX leg and `price_usd` on every day 2025-09-12..2026-10-12 (no hole), and the P2P leg from 2026-04-01.
  The DEX leg starts 2024-12-10 (NEAR Intents' first day); neither leg begins on 2026-02-19.
- SEEN in the workbook (Jake): r0 = 2026-02-19.
- INFERRED: the DEX leg and the price are whole around that date (fixture), so the missing leg on 2026-02-18 is P2P. The
  likeliest cause is the daily backfill's frontier: it reads newest first, one chunk of up to 31 contiguous days a run, and
  holds a chunk that the month's room after the top-up reserve cannot take (a 31-day chunk at 7.54 GB = 234 GB; R20 noted
  November had ~74 GB left). Then the series starts on 2026-02-19 and SPEC R18.N1's "held from 2025-12-29" was never true
  of the store (the plan said it was unchecked). The alternative, a single missing day inside a longer run, is shown by
  `gaps` as holes INSIDE the run; either way the R23 top-up reads the days when the ledger allows.
