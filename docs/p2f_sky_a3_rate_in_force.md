# P2f-5a — Sky A3 key in past quarters: the emission rate in force, read time-weighted (RECORDED ONLY, nothing built)

Decision (Jake 2026-10-29, SPEC P2f-5a): a stored rate in force is read time-weighted by days in force, from the primary source
(the DssVest history). Nothing is built before Friday. This is a read-only investigation: no code changed, no value changed.

**The most important finding first.** The history reader already exists. `fetch/sky_mkr.py` `SkyMkr._vest_history` (commit
e2250e5, 2026-10-18, SPEC G2.3c/G2.3e) rebuilds `emissions_rate_current_annual_tokens` for past days from the DssVest
`awards(id)` records, up to 455 days back, on the first run after the series' first stored row. The committed Sky dump is dated
2026-10-13, five days *before* that code, which is why the fixture (and P2e-2's report) shows the series from 2026-10-12 only.
Jake's store, run on any build since 10-18 (his 10-28 build was on 9c48c31, which has it), most likely **already holds
the history**, in which case Sky's A3 past quarters are already numbers there. His store is not here, so this is unverified; the
check is one line (below).

## 1. How the engine reads a stored rate in past quarters today

* The metric is a `stock` (config.py:484, `emissions_rate_current_annual_tokens`). `build_workbook.aggregate`, stock branch:
  `row["now"]` = the latest stored value (build_workbook.py:5278) and, for each of the five 90-day quarters,
  `row[q] = _window_mean(s, asof − 90(i+1), asof − 90 i)` (build_workbook.py:5284; `_window_mean`, line 237) = the plain arithmetic
  mean of the stored rows dated in (start, end]. A window holding no row is blank ("n/a — Data: no value in this window").
* The A3 key (`_retire_vs_emissions_col`, build_workbook.py:7393) divides retired buyback tokens per covered day by the rate
  `R.D(r, "emissions_rate_current_annual_tokens", "now") / days_per_year` (`CURRENT_EMISSION_RATE`, config.py:19319). For past
  quarters the Trajectory engine re-runs the same builder shifted k quarters back (`_shift_col`, build_workbook.py:5649):
  a stock's `now` becomes `q{k}`. So **Q0 reads the latest value; Q-1..Q-3 read that quarter's mean of stored rows**; the
  reader shows them as "Retired ÷ emissions (x), −1/−2/−3 qtrs" and "Key: change vs previous quarter".
* The rate is not a leg of the key's `_coverage_guard` (only the buyback leg is), so a thin rate window does not block.

## 2. The time-weighted reading, and how it differs

For a quarter window W = (asof − 90(k+1), asof − 90k] and the awards i paying the distribution, each with annual rate
`a_i = tot_i × 31,536,000 ÷ (fin_i − bgn_i)` (fin_i = the yank time if yanked, tot_i = what had accrued):

    rate(W) = Σ_i a_i × n_i ÷ Σ_i n_i,     n_i = days award i is in force inside W (the higher id where two overlap)

with n_i counted in whole UTC days (the award in force at the day's close, as `vest_rate_history` does) or, exactly, in seconds,
n_i = |[bgn_i, fin_i) ∩ W| ÷ 86,400. The key's denominator is `rate(W) ÷ 365`. Equivalent on a step function: the mean over every
calendar day of W of the rate in force on it, **carried forward** over days with no stored row.

How it differs from `_window_mean`:
1. **Same number when the rows are one per day with no gaps** (what `_vest_history` writes, and what a daily run writes). It
   differs where rows are missing: `_window_mean` weights each *present row* equally and ignores a day with no row (a missed run
   day, a sleeping laptop); time-weighting holds the rate through it. It also differs for any series stored on change dates only.
2. Intra-day: the day-close rows give a switch day entirely to the new award. Vest 17 began 2026-09-13 12:42 UTC, so the old
   rate is under-weighted by ~0.53 day (580.8M vs 393.0M/yr: ~1.1M/yr on a 90-day mean, 0.2%). Seconds-exact weighting removes it.
3. **Days no award covers** (before the first award, between awards): they are left out of both readings, so the mean is over
   covered days only while the numerator (retired per covered day of the *buyback* series) is over its own covered days. The
   decision to take is whether such a quarter reads a partial-window rate, or n/a below RATE_MIN_DAYS (7) / below full coverage.
   Not decided here.
4. **Q0 stays the latest value** under the current code, so the key's change vs the previous quarter compares a latest-rate Q0
   with an averaged Q-1. Worked on the what-if below: Q0 uses 403.6M/yr (vest 18) where the Q0 time-weighted rate is 693.2M/yr,
   so Q0 would be 0.1995 instead of 0.3426 on a time-weighted basis, and the "+38%" quarter change would be a decline. Whether
   Q0 should be time-weighted too is Jake's call; the P2f-5a wording covers past quarters only.

## 3. The primary source

* **Contracts (Ethereum).** `VestedRewardsDistribution` REWARDS_DIST_LSSKY_SKY **0x675671A8756dDb69F7254AFB030865388Ef699Ee** —
  `vestId()` (the award it pays now) and `dssVest()`; the DssVest it reads is MCD_VEST_SKY_TREASURY
  **0x67eaDb3288cceDe034cE95b0511DCc65cf630bB6**, a `DssVestTransferrable` (config.py:14708-14712, 14754-14775; spells-mainnet
  2025-10-30 spell L163-200 @c1ce7e14, dss-vest src/DssVest.sol L475-498). Addresses: config.py:19305-19319
  (`PROJECT_BY_NAME["Sky"]["mkr_backing"]`).
* **Method.** `DssVest.awards(uint256 id)` → (usr, bgn, clf, fin, mgr, res, tot, rxd). The record keeps bgn/fin/tot after an award
  ends or is yanked (yank sets fin to the yank time and tot to what had accrued), so the whole rate history is a state read at
  `latest`, no archive node and no log scan (fetch/sky_mkr.py:67 `vest_awards`, :92 `vest_rate_history`, :109
  `VEST_HISTORY_DAYS = 455`, :122 `_vest_history`; SPEC G2.3c). Source string:
  `sky_mkr:dss_vest.awards[vest N: bgn..fin, tot SKY]:record`; `record` is a SOURCE_MARKERS entry (config.py:1961), the same
  measuring point as the live read, so it is no series break. Scheduled for the daily run: fetch/__init__.py:207.
* **Events.** The code uses no DssVest events (Init / Vest / Yank in dss-vest; not re-read here). The per-spell record of each
  step is in config.py near line 14738 (Sky `issuance_schedule.steps`, the executive spell dates; the comment above it lists each vest reset) and SPEC §6 S1 / config.REWARD_FUNDING.
* **Archive kind.** `fetch.archive.archivable(Sky, "emissions_rate_current_annual_tokens", {"ethereum"}, ...)` returns
  `(False, "no contract read serves it")`: it is a tier-2 fetcher, not a `contracts` read, so `archive_backfill.py` cannot fill
  it; SPEC line 235 adds that the vest contract "has no code ~400 days back". It does not need to: the award records are the
  history.
* **Public RPC reads made today (no key, 24 calls at most).** `ethereum-rpc.publicnode.com` refused (HTTP 403, policy);
  `mainnet.gateway.tenderly.co` answered `VestedRewardsDistribution.vestId()` = **18** and `dssVest()` =
  0x67eadb3288ccede034ce95b0511dcc65cf630bb6 (equal to the config address), then returned HTTP 429 on `awards(1)` and on a batch of
  `awards(1..18)`. So the award history itself could **not** be re-read here. What I can cite: vests 14-17 = 976.8M, 1,162.8M,
  393.0M, 580.8M SKY/yr (fetch/sky_mkr.py docstring; they match the config schedule steps 2026-06-18, 07-16, 08-13, 09-13), and the
  stored vest 18 row (2026-10-12..2027-01-10, 99,525,882 SKY, 403.6M/yr) in fixtures/real_sky.json.

## 4. Cells that go from n/a to numbers once the history is stored

Measured by a what-if build: the fixture store plus 347 daily rows 2025-10-30..2026-10-11 for Sky built from the CONFIG schedule
steps through the repo's own `vest_rate_history`, in the repo's own source format, then both workbooks built and compared
with the baseline (cached values, every sheet). **The figures are illustrative: the config schedule is not the chain's award
records (unread, see 3), so real numbers will differ slightly. The set of cells is the point.**

* Reader, A3 Revenue Buyback, Sky row (4 cells): "Retired ÷ emissions (x), −1 qtr" n/a → 0.2482; "−2 qtrs" n/a → 0.5261; "−3 qtrs"
  n/a → 0.7863; "Key: change vs previous quarter" n/a → +38.0%. Q0 (0.3426), Summary key, rank (Sky 1) unchanged. "−4 qtrs" is not shown on the reader.
* Audit, Trajectory rows `a3_retire_vs_emissions` and `a3_retire_vs_dilution_now` (Sky): Q-1..Q-3 (0.2482, 0.5261, 0.7863), T12M,
  QoQ, growth label ("NEW since Q-3"), quarters in the growth (1 → 4), quarters improved (1/3), trough, Trend text; and the A3 tab's two
  Trend cells (BQ16, BS16). Q-4 stays n/a (the first award starts 2025-10-30; Q-4 ends 2025-10-18).
* Audit, Data row `Sky|emissions_rate_current_annual_tokens`: m1, q0..q3, y1, rows stored, first date (the Q0 average moves from
  403.6M to 693.2M/yr; the key's Q0 does not read it, see 2.4).
* Audit, Coverage / References: the vest-17 reference check (2026-09-13, 580.8M/yr) goes NOT WIRED → VERIFIED (ours 580,789,594
  vs 580,800,000); Sky NOT WIRED 5 → 4, VERIFIED 73 → 74; the Trajectory CHECK counts for the two rows 1 → 4.
* Nothing else on the reader or Summary changes (858 differing cells in all, 811 of them formula references into the hidden data
  sheet; the value comparison shows the 4 reader cells above and 52 on the audit file).

## 5. Backfill needed

* `archive_backfill.py`: **none, and not possible** for this series (3, archive kind). No project/metric to run it for.
* The history comes from the daily run: `SkyMkr._vest_history` writes days before the series' first stored row, ≤455 days back,
  once a day, and only when it can read the awards (a failed read marks nothing and retries on the next run). On a store that has
  run the code since 10-18 the rows exist; if the first row is still 2026-10-12, the next daily run writes them (the tier-2
  `sky_mkr` fetch), no long job. Days before the first award (the lsSKY→SKY farm vest starts
  2025-10-30) have no rate and stay left out.
* Check on Jake's store (read-only):
  `python -c "import sqlite3;print(sqlite3.connect('file:metrics.db?mode=ro',uri=True).execute(\"select min(date),max(date),count(*) from metrics where project='Sky' and metric='emissions_rate_current_annual_tokens'\").fetchall())"`
  — about 2025-10-30..yesterday and ~360 rows means the history is there; 2026-10-12.. and a handful means it is not.
* To build the time-weighted reading (after Friday): in the stock branch (build_workbook.py:5278-5284), for the metrics named in
  `config.CURRENT_EMISSION_RATE`, replace `_window_mean` by a carried-forward mean over the window's calendar days (or the
  seconds-exact award overlap above), and decide 2.3 and 2.4. Until then the cells in 4 read as the plain mean of the stored rows.

## Not verified

* Jake's store (whether the history is already stored); the chain's award records 1..18 (public RPC rate-limited); the dss-vest
  event signatures (not read). The what-if figures in 4 come from the config schedule.
