-- orphan_cleanup.sql — REVIEW BEFORE RUNNING. Nothing here is destructive until step 3.
--
-- The store upserts and never deletes, so a contract removed from config.py stops producing new
-- rows while every row it already wrote stays exactly where it is. Two known cases:
--
--   Sky/burn_zero        removed when the Flapper mechanism was refuted. Its rows survived and
--                        rendered as a measured zero on a refuted mechanism, at full confidence.
--   Uniswap/fire_pit     re-pointed to the dead address. The next delta spanned the change and
--                        reported 111,337,581 UNI burned in a month against a real 100-134k/day.
--
-- Run steps 1 and 2 first. They only LOOK. Read what they print, then run step 3.

-- ---------------------------------------------------------------------------------------------
-- STEP 1 — every distinct source in the store, with its span. LOOK ONLY.
-- Compare each against config.py: any source naming a contract key that is no longer there is an
-- orphan. `python -c "import config; print(sorted(config.PROJECT_BY_NAME['Sky']['contracts']))"`
-- ---------------------------------------------------------------------------------------------
SELECT project, metric, source, COUNT(*) AS rows,
       MIN(date) AS first_seen, MAX(date) AS last_seen,
       MIN(value) AS min_value, MAX(value) AS max_value
FROM   metrics
GROUP  BY project, metric, source
ORDER  BY project, metric, first_seen;

-- ---------------------------------------------------------------------------------------------
-- STEP 2 — the two known orphans specifically, and what deleting them would remove. LOOK ONLY.
-- ---------------------------------------------------------------------------------------------
SELECT 'WOULD DELETE' AS action, project, metric, source, date, value
FROM   metrics
WHERE  (project = 'Sky'     AND source LIKE 'chain:%burn_zero%')
   OR  (project = 'Uniswap' AND source LIKE 'chain:%fire_pit%')
ORDER  BY project, metric, date;

-- Any metric that would be left with NO rows at all afterwards. Expect Sky's burn metrics to
-- appear here — that is correct and intended: Sky has no burn figure, and an empty metric is the
-- honest state. Anything ELSE appearing here is a surprise and should stop you.
SELECT project, metric, COUNT(*) AS rows_remaining
FROM   metrics
WHERE  NOT ((project = 'Sky'     AND source LIKE 'chain:%burn_zero%')
        OR  (project = 'Uniswap' AND source LIKE 'chain:%fire_pit%'))
  AND  (project, metric) IN (SELECT project, metric FROM metrics
                             WHERE (project = 'Sky'     AND source LIKE 'chain:%burn_zero%')
                                OR (project = 'Uniswap' AND source LIKE 'chain:%fire_pit%'))
GROUP  BY project, metric;

-- ---------------------------------------------------------------------------------------------
-- STEP 3 — THE DELETE. Back up first; it cannot be undone.
--     cp metrics.db metrics.db.bak-$(date +%Y%m%d)
-- Then uncomment and run.
-- ---------------------------------------------------------------------------------------------
-- BEGIN;
-- DELETE FROM metrics WHERE project = 'Sky'     AND source LIKE 'chain:%burn_zero%';
-- DELETE FROM metrics WHERE project = 'Uniswap' AND source LIKE 'chain:%fire_pit%';
-- COMMIT;

-- ---------------------------------------------------------------------------------------------
-- STEP 4 — verify. Sky's burn metrics should be GONE (correct). Uniswap's burn should be left
-- only with burn_dead rows, and its gross_burn_tokens flow will rebuild from the next run.
-- ---------------------------------------------------------------------------------------------
-- SELECT project, metric, source, COUNT(*), MIN(date), MAX(date) FROM metrics
--  WHERE project IN ('Sky','Uniswap') GROUP BY project, metric, source ORDER BY project, metric;

-- ---------------------------------------------------------------------------------------
-- 2026-09-14 — MAPLE treasury_holding_tokens: 0.51253570332391 SYRUP
--
-- NOT AN ORPHAN. The contract key `treasury` is still in config; what changed is that its
-- ROLE is now marked destination_status "disputed", so future runs stage the observation as
-- evidence and store no metric. But THE STORE UPSERTS AND NEVER DELETES, so the 0.51 row
-- already written stays as the latest value for this metric and keeps rendering.
--
-- The read was correct and the address was wrong: 0xa9466EaBd096449d650D5AEB0dD3dA6F52FD0B19
-- is Maple's v2 PROTOCOL FEE treasury, which takes fees in pool assets, not the wallet holding
-- repurchased SYRUP. Delete the row rather than leaving a real-but-meaningless figure in place.
--
-- Check first:
--   SELECT date, value, source FROM metrics
--    WHERE project='Maple' AND metric='treasury_holding_tokens' ORDER BY date;
--
-- Then delete:
-- ALREADY RUN. DO NOT RE-RUN THIS STATEMENT — commented out 2026-09-18.
-- Maple's treasury address has since been RESOLVED (0xd6d4Bcde6c816F17889f1Dd3000aF0261B03a196,
-- daoMultisig / "Maple Finance: DAO") and is producing LEGITIMATE treasury_holding_tokens rows
-- again. An unscoped DELETE left live here would destroy that real data on any future blanket
-- re-run of this file. Left as a record of what was done, not as a statement to run again.
-- DELETE FROM metrics
--  WHERE project = 'Maple'
--    AND metric  = 'treasury_holding_tokens';

-- =======================================================================================
-- 2026-09-15 — STALE-JUDGEMENT AUDIT
--
-- Every mechanism in this project is evaluated in one of two places, and that decides
-- whether changing config re-judges data already in the store:
--
--   READ-TIME  (build_workbook.py: aggregate / confidence_for) — runs over the WHOLE store
--              on every build, so a config change takes effect immediately. Self-correcting.
--   WRITE-TIME (fetch/*) — runs only over the rows fetched THIS run, so a config change
--              affects the next write and nothing already stored. Needs a manual clear.
--
-- Everything below is the write-time set, audited against what this session changed.
-- SELECT ONLY. Review each before deleting anything — same as every previous cleanup.
-- =======================================================================================

-- ---------------------------------------------------------------------------------------
-- (a) REFUTED BURN MECHANISMS
--
-- Sky is the ONLY project whose burn_mechanism status is "refuted"
-- (model amm_swap_to_receiver), and its rows were cleared earlier in this session. Nothing
-- else in the universe carries that status, so there is no second case to chase.
--
-- This category is now SELF-ANNOUNCING rather than silent: as of today confidence_for
-- returns RED for a refuted mechanism instead of GREEN, so any row that survived would
-- light up on the next build rather than passing clean. Run this to confirm none did.
SELECT date, project, metric, value, source, tier
  FROM metrics
 WHERE project = 'Sky'
   AND metric IN ('gross_burn_tokens', 'burn_address_balance', 'burn_revenue_funded')
 ORDER BY date;
-- Expect ZERO rows. Any row returned is a survivor of the earlier clear and should go.

-- ---------------------------------------------------------------------------------------
-- (b) ROWS THAT WOULD NOW FAIL SANITY BOUNDS TIGHTENED THIS SESSION
--
-- validate_frame applies bounds to the FETCH frame only. Tightening a bound never revisits
-- a stored row, so anything written under the old bound stays exactly where it was.
-- Three bounds were tightened this session:
--
--   Maple / treasury_holding_tokens          min 1,000,000   (was 0)
--   Maple / buyback_fund_balance_dashboard   min 1,000,000   (was 0)
--   Chainlink / locked_tokens                max 60,000,000  (was 1e15)
--
-- Chainlink / locked_tokens_principal (max 60,000,000) is new this session and has no rows
-- yet, so it cannot have a violation; it is listed here only so the set is complete.
SELECT date, project, metric, value, source, tier,
       'below the 1,000,000 floor added 2026-09-14' AS why
  FROM metrics
 WHERE project = 'Maple'
   AND metric IN ('treasury_holding_tokens', 'buyback_fund_balance_dashboard')
   AND value < 1000000
UNION ALL
SELECT date, project, metric, value, source, tier,
       'above the 60,000,000 cap added 2026-09-14 (v0.2 programme is 45m LINK)' AS why
  FROM metrics
 WHERE project = 'Chainlink'
   AND metric IN ('locked_tokens', 'locked_tokens_principal')
   AND value > 60000000
 ORDER BY project, metric, date;
-- KNOWN OFFENDER: Maple/treasury_holding_tokens = 0.5125357033239131. It is already covered
-- by the DELETE above, and is now ALSO suppressed at read time by the disputed status — so
-- the workbook is correct either way. The row is still worth removing.

-- ---------------------------------------------------------------------------------------
-- (c) PARTIAL FIGURES WRITTEN BEFORE supply_is_partial WAS SET
--
-- The ":PARTIAL" marker is not read from config at build time — it is baked into the SOURCE
-- STRING when the row is written. A row written before the flag existed carries no marker
-- and renders as a COMPLETE figure, however partial it actually is.
--
-- One correction to the list as briefed: Ether.fi's supply_is_partial sits on the `sethfi`
-- contract, which is kind ve_total_supply and therefore serves LOCKED_TOKENS, not
-- total_supply. Ether.fi has no partial total_supply to clean.
--
-- PancakeSwap also carries two partial contracts, but those predate this session and are
-- outside the sixteen — listed separately below rather than mixed in.
SELECT date, project, metric, value, source, tier,
       'partial figure with no :PARTIAL marker — written before the flag was set' AS why
  FROM metrics
 WHERE ( (project = 'World Mobile' AND metric = 'total_supply')
      OR (project = 'GEODNET'      AND metric = 'total_supply')
      OR (project = 'Aethir'       AND metric = 'total_supply')
      OR (project = 'Ether.fi'     AND metric = 'locked_tokens') )
   AND source NOT LIKE '%:PARTIAL%'
 ORDER BY project, date;

-- Same question for PancakeSwap, whose partial flags predate this session. Included for
-- completeness; not part of the sixteen and not expected to need action.
SELECT date, project, metric, value, source, tier
  FROM metrics
 WHERE project = 'PancakeSwap' AND metric = 'total_supply'
   AND source NOT LIKE '%:PARTIAL%'
 ORDER BY date;

-- ---------------------------------------------------------------------------------------
-- (d) TIER 4 — STRUCTURALLY FROZEN, PLUS ONE PRESENT CONSEQUENCE
--
-- Dune series are skipped once the store holds history for them, so they are never
-- re-fetched and their write-time checks (sanity bounds, cross-checks, reference values,
-- impossible relations) NEVER RUN AGAIN on that data. That is a STANDING RISK, not a
-- present bug: no judgement behind the four wired series changed this session.
--
--   GEODNET  gross_burn_tokens   query 8683175   mechanism still "assumed" — unchanged
--   Ether.fi locked_tokens       query 8683038
--   Ether.fi lock_rate_pct       query 8683038
--   Ether.fi staker_count        query 8683038
--
-- THE ONE PRESENT CONSEQUENCE is Ether.fi locked_tokens, and it is not stale judgement —
-- it is a genuine second measuring point created by this session. Adding the `sethfi`
-- tier-2 contract gives that metric a second source, and the two measure DIFFERENT THINGS:
-- the Dune series is staked sETHFI (share supply, 141,470,107.5 at 2026-09-10), the tier-2
-- read is ETHFI.balanceOf(sETHFI) (assets). Tier 2 outranks tier 4, so new rows arrive from
-- the contract while the Dune history stays.
--
-- confidence_for will therefore force RED with "MEASURING POINT CHANGED" on the next build.
-- THAT IS CORRECT AND SHOULD NOT BE SUPPRESSED — a window spanning the two reports the gap
-- between share supply and assets as though it were a flow.
--
-- The DECISION is which series Ether.fi's lock rate should be, and it is Jake's to make:
--   keep the contract read  -> clear the Dune history for locked_tokens (this SELECT), or
--   keep the Dune series    -> remove `sethfi` from config and clear nothing.
-- Do NOT clear both.
SELECT source, tier, COUNT(*) AS rows, MIN(date) AS first, MAX(date) AS last,
       MIN(value) AS min_value, MAX(value) AS max_value
  FROM metrics
 WHERE project = 'Ether.fi' AND metric = 'locked_tokens'
 GROUP BY source, tier
 ORDER BY first;
-- Two source groups means the split above is live. One means it has not landed yet.

-- And the general frozen-tier-4 inventory, to see what has never been re-validated:
SELECT project, metric, source, tier, COUNT(*) AS rows,
       MIN(date) AS first, MAX(date) AS last
  FROM metrics
 WHERE tier = 4
 GROUP BY project, metric, source, tier
 ORDER BY project, metric;

-- =======================================================================================
-- 2026-09-15 — ETHER.FI locked_tokens SPLIT: RE-ATTRIBUTE, DO NOT DELETE
--
-- The `sethfi` contract changed kind from ve_total_supply to stake_underlying, so it now
-- feeds locked_tokens_underlying instead of locked_tokens. locked_tokens goes back to being
-- the Dune series alone — staked sETHFI, a SHARE supply, 794 days of history, untouched.
--
-- NOTHING NEEDS DELETING. Any rows the contract wrote under the old metric name are correct
-- readings that were filed under the wrong column, so they MOVE rather than go. The metrics
-- table is keyed (date, project, metric), and locked_tokens_underlying is brand new, so an
-- UPDATE of the metric name cannot collide with an existing row.
--
-- The Dune rows must NOT move. They are matched out by source below: only rows whose source
-- names the sethfi contract are re-attributed.
--
-- 1. LOOK FIRST — what is actually there, split by source:
SELECT source, tier, COUNT(*) AS rows, MIN(date) AS first, MAX(date) AS last,
       MIN(value) AS min_value, MAX(value) AS max_value
  FROM metrics
 WHERE project = 'Ether.fi' AND metric = 'locked_tokens'
 GROUP BY source, tier
 ORDER BY first;
-- Expect: one group sourced 'dune:8683038' (keep, do not touch), and EITHER a second group
-- sourced 'chain:ethereum:sethfi...' (move it, step 2) or nothing else at all, meaning the
-- contract never wrote under the old name and there is nothing to do.

-- 2. THE EXACT ROWS THAT WOULD MOVE — review this list before running step 3:
SELECT date, value, source, tier
  FROM metrics
 WHERE project = 'Ether.fi' AND metric = 'locked_tokens'
   AND source LIKE '%sethfi%'
 ORDER BY date;

-- 3. THE MOVE. Re-attribution, not deletion — the reading was right, the column was wrong.
--    Run only after reviewing step 2. Commented out deliberately; uncomment to run.
-- UPDATE metrics
--    SET metric = 'locked_tokens_underlying'
--  WHERE project = 'Ether.fi' AND metric = 'locked_tokens'
--    AND source LIKE '%sethfi%';

-- 4. CONFIRM — locked_tokens should be single-source again, which is what clears the
--    MEASURING POINT CHANGED red flag:
SELECT metric, source, COUNT(*) AS rows, MIN(date) AS first, MAX(date) AS last
  FROM metrics
 WHERE project = 'Ether.fi' AND metric IN ('locked_tokens', 'locked_tokens_underlying')
 GROUP BY metric, source
 ORDER BY metric, first;


-- =====================================================================================
-- 2026-09-15 (b) — TWO STALE ROWS FOUND ON A LIVE RUN, run 20260915T120711Z.
--
-- Both are the SAME BUG: a config change that guarded the WRITE path and could not touch
-- what was already stored. The read-time guards added alongside this file now blank both
-- in the workbook whatever the store holds, so THESE DELETES ARE HOUSEKEEPING, NOT THE
-- FIX. Nothing renders wrongly while they sit here unreviewed.
--
-- SELECT ONLY. Review before anything is deleted, same as every previous cleanup.
-- =====================================================================================

-- ---------------------------------------------------------------------------------
-- A. ETHER.FI locked_tokens — rows written by sethfi BEFORE its kind changed.
--
--    A1 IS A DIAGNOSTIC, NOT A CLEANUP, AND IT ANSWERS A QUESTION I COULD NOT ANSWER
--    FROM HERE: is the newest sethfi-sourced row dated TODAY or EARLIER?
--      dated EARLIER  -> the fix is on Jake's branch, the row is simply stale. Expected.
--      dated TODAY    -> commit 672ae03 is NOT in the working copy that ran, and the
--                        adapter is still writing locked_tokens from sethfi. Then the
--                        cleanup below is premature — pull first.
--    Run this one FIRST and read the date before doing anything else.
SELECT MAX(date) AS newest_sethfi_row,
       (SELECT MAX(date) FROM metrics) AS newest_row_in_store
  FROM metrics
 WHERE project = 'Ether.fi' AND metric = 'locked_tokens' AND source LIKE '%sethfi%';

-- A2. The full picture: which sources feed locked_tokens, and over what spans.
--     Expect the Dune series to hold the history and sethfi to hold only recent dates.
SELECT source, COUNT(*) AS rows, MIN(date) AS first, MAX(date) AS last,
       MIN(value) AS min_value, MAX(value) AS max_value
  FROM metrics
 WHERE project = 'Ether.fi' AND metric = 'locked_tokens'
 GROUP BY source
 ORDER BY first;

-- A3. THE ROWS THEMSELVES. These are the ones the UPDATE in the previous section
--     (2026-09-14, "3. THE MOVE") was written to re-attribute — it is still commented
--     out and has never been run, which is why the contamination is still here.
--     RE-ATTRIBUTE, DO NOT DELETE: the reading was correct, the column was wrong.
SELECT date, value, source, tier, fetched_at
  FROM metrics
 WHERE project = 'Ether.fi' AND metric = 'locked_tokens' AND source LIKE '%sethfi%'
 ORDER BY date;

-- A4. Check the destination is clear before moving anything into it. The UPDATE would
--     collide on the (date, project, metric) primary key if a date already exists on
--     both sides. If this returns rows, resolve them before running the move.
SELECT a.date, a.value AS locked_tokens_value, b.value AS underlying_value
  FROM metrics a
  JOIN metrics b
    ON b.date = a.date AND b.project = a.project
   AND b.metric = 'locked_tokens_underlying'
 WHERE a.project = 'Ether.fi' AND a.metric = 'locked_tokens'
   AND a.source LIKE '%sethfi%'
 ORDER BY a.date;

-- ---------------------------------------------------------------------------------
-- B. GEODNET gross_issuance_tokens — the derived zero from before the suppression.
--
--    DELETE, DO NOT RE-ATTRIBUTE: unlike Ether.fi's, this reading was never right for
--    any column. Issuance is schedule-based and per-miner and is not recoverable from a
--    supply delta at any sampling interval, so the zero is not a figure in the wrong
--    place — it is not a figure.
SELECT date, value, source, tier, fetched_at
  FROM metrics
 WHERE project = 'GEODNET' AND metric = 'gross_issuance_tokens'
 ORDER BY date;

-- B2. Scope check before deleting: is every row derived, or did a real source ever write
--     here? A non-derived row would be a measured figure and must NOT be swept up.
SELECT source, COUNT(*) AS rows, MIN(date) AS first, MAX(date) AS last,
       SUM(CASE WHEN value = 0 THEN 1 ELSE 0 END) AS zero_rows
  FROM metrics
 WHERE project = 'GEODNET' AND metric = 'gross_issuance_tokens'
 GROUP BY source
 ORDER BY first;

-- B3. THE DELETE. Run only after reviewing B and B2. Commented out deliberately.
--     Scoped to derived sources so a measured figure, if one ever lands, is not swept up.
-- DELETE FROM metrics
--  WHERE project = 'GEODNET' AND metric = 'gross_issuance_tokens'
--    AND source LIKE 'derived:%';

-- ---------------------------------------------------------------------------------
-- C. THE SAME QUESTION ASKED EVERYWHERE ELSE — is either pattern hiding on other
--    projects? Neither of these was looked for until it was reported by hand.
--
-- C1. Every stored row whose source names a contract key. Cross-check the key against
--     config.PROJECT_BY_NAME[project]['contracts'][key]['kind'] and its KIND_METRIC
--     mapping: any row whose metric does not match is the Ether.fi pattern again.
--     (The read-time guard now catches these automatically; this finds them for cleanup.)
SELECT project, metric, source, COUNT(*) AS rows, MAX(date) AS last
  FROM metrics
 WHERE source LIKE 'chain:%'
 GROUP BY project, metric, source
 ORDER BY project, metric;

-- C2. Every derived row still in the store, so a future suppression has a list to check
--     against rather than waiting for someone to spot a zero on a workbook tab.
SELECT project, metric, source, COUNT(*) AS rows, MAX(date) AS last,
       SUM(CASE WHEN value = 0 THEN 1 ELSE 0 END) AS zero_rows
  FROM metrics
 WHERE source LIKE 'derived:%'
 GROUP BY project, metric, source
 ORDER BY zero_rows DESC, project;

-- =======================================================================================
-- 2026-09-18 — GEODNET treasury_holding_tokens: MEASURING POINT CHANGED
--
-- Three treasury wallets (mining_polygon, mining_distribution_polygon, ecosystem_polygon)
-- are summed into treasury_holding_tokens. mining_distribution_polygon was REJECTED on
-- live runs before 2026-09-18 (eth_getCode empty, kind-wide default wrongly required
-- bytecode for a wallet-not-a-contract) and fixed with a targeted holder_has_code=False
-- override — see config.py's block comment on GEODNET's contracts.mining_distribution_polygon.
--
-- So rows written BEFORE that fix summed only TWO components (mining_polygon +
-- ecosystem_polygon, source "chain:sum(mining_polygon+ecosystem_polygon)" or, if
-- ecosystem_polygon was also still failing at some point, a single-component
-- "chain:mining_polygon"); rows written AFTER sum THREE ("chain:sum(mining_polygon+
-- mining_distribution_polygon+ecosystem_polygon)"). build_workbook.py's
-- measuring_point_changed guard (WITHHELD_STATUSES) correctly detects this — a series
-- spanning the change reports the addition of a wallet as though it were a balance
-- change — and blanks the row RED. THIS IS THE GUARD WORKING, NOT A BUG: the fix is a
-- store cleanup, not a config bypass (there is no per-project escape hatch for
-- measuring_point_changed, and there should not be one).
--
-- D1. LOOK ONLY — every distinct source treasury_holding_tokens has actually been
--     written from, oldest first. Confirm there really are two-or-more distinct
--     _measuring_point() values (composition strings ignoring :PARTIAL/:delta) before
--     touching anything.
SELECT date, value, source, tier
  FROM metrics
 WHERE project = 'GEODNET' AND metric = 'treasury_holding_tokens'
 ORDER BY date;

-- D2. Grouped by the SAME normalisation build_workbook.py uses (fetch.base._measuring_point
--     strips only :PARTIAL and :delta, so a source is grouped as printed here).
SELECT source, COUNT(*) AS rows, MIN(date) AS first_seen, MAX(date) AS last_seen,
       MIN(value) AS min_value, MAX(value) AS max_value
  FROM metrics
 WHERE project = 'GEODNET' AND metric = 'treasury_holding_tokens'
 GROUP BY source
 ORDER BY first_seen;
-- Expect to see the OLD (two-component, or single-component) composition strings ending
-- before 2026-09-18, and the NEW three-component sum starting on or after it. If only one
-- composition ever appears, the guard should not be firing at all — stop and re-check
-- rather than deleting anything.

-- D3. THE ROW, NAMED.                                                     answered 2026-09-22
--     The placeholder source strings that used to sit in D4 are gone: the live run of
--     2026-09-21 identified the offending row exactly — ONE row, dated 2026-09-17, carrying the
--     two-wallet PARTIAL composition written while mining_distribution_polygon was still being
--     refused by the existence check. Every row from 2026-09-18 onward carries the full
--     three-wallet sum. So the delete is scoped by DATE, which is exact, with the PARTIAL marker
--     as a second condition so a mis-typed date cannot reach a complete row.
--
--     RUN THIS FIRST AND READ IT. One row, dated 2026-09-17, source containing PARTIAL. If it
--     returns anything else — two rows, a different date, a source without PARTIAL — the store
--     does not match what this section was written against. Stop and report rather than deleting.
SELECT date, project, metric, value, source, tier, fetched_at,
       CASE WHEN date = '2026-09-17' AND source LIKE '%PARTIAL%'
            THEN 'WOULD DELETE — the two-wallet PARTIAL row'
            ELSE 'KEPT — the three-wallet composition' END AS action
  FROM metrics
 WHERE project = 'GEODNET' AND metric = 'treasury_holding_tokens'
 ORDER BY date;

-- D4. THE DELETE. One row. Scoped by date AND by the PARTIAL marker, so it cannot reach the
--     complete three-wallet rows this fix exists to keep — that is the outcome D3's old
--     placeholder wildcard was written to warn against.
--
--     WHAT IT RECOVERS: with one composition left in the history, measuring_point_changed stops
--     firing and treasury_holding_tokens renders as an ordinary series again. The 2026-09-17
--     figure itself is NOT recoverable — it measured two of three wallets and there is no route
--     back to what the third held that day. The series starts on 2026-09-18 and says so.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'GEODNET' AND metric = 'treasury_holding_tokens'
--    AND date = '2026-09-17'
--    AND source LIKE '%PARTIAL%';
-- COMMIT;

-- D5. Verify — one composition, first date 2026-09-18, and the next build renders
--     treasury_holding_tokens as an ordinary GREEN/AMBER series rather than
--     RED/measuring_point_changed.
-- SELECT source, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date
--   FROM metrics
--  WHERE project = 'GEODNET' AND metric = 'treasury_holding_tokens' GROUP BY source;

-- =======================================================================================
-- 2026-09-21 — UNISWAP gross_burn_tokens: ONE DELTA ROW HOLDING ~111.3M
--
-- FOUND BY THE AUDIT OF RUN 20260921T100546Z:
--   gross_burn_tokens "now" (trailing 30d sum)   111,941,581
--   burn_address_balance (cumulative)            111,953,581
-- The 30-day figure is 99.99% of every UNI ever burned. The real rate is 100-200k/day, so
-- thirty days should be ~3-6M.
--
-- WHY THE EARLIER CLEANUP MISSED IT. The row was written on 2026-09-14, when the read moved
-- from fire_pit to the dead address: the delta spanned two different addresses and recorded the
-- step between them as one day's burn. The orphan cleanup deleted rows whose SOURCE named
-- fire_pit — but this row carries the NEW source (chain:ethereum:burn_dead...:delta), because it
-- was written by the burn_dead read. Deleting the fire_pit rows removed the EVIDENCE of the
-- measuring-point change and left the CONSEQUENCE behind, which is also why
-- build_workbook's measuring_point_changed guard (two distinct sources in the history) cannot
-- see it: there is only one source left.
--
-- A read-time arithmetic guard now catches this class without needing the provenance —
-- build_workbook.withheld_for case 6, "one observation is >25% of the cumulative". Until this
-- row is deleted, Uniswap's burn metrics render BLANK and RED with that reason, which is
-- correct but not a substitute for removing the row.
--
-- E1. LOOK ONLY — every Uniswap burn-flow row, largest first. Expect exactly one enormous row
--     around 2026-09-14 and the rest in the 1e5 range.
SELECT date, value, source, tier
  FROM metrics
 WHERE project = 'Uniswap' AND metric = 'gross_burn_tokens'
 ORDER BY value DESC;

-- E2. LOOK ONLY — the specific rows the DELETE below would remove, with the cumulative balance
--     of the same date beside them for scale. Read this before running E3.
SELECT f.date, f.value AS flow_value, f.source,
       (SELECT s.value FROM metrics s
         WHERE s.project = 'Uniswap' AND s.metric = 'burn_address_balance'
           AND s.date <= f.date ORDER BY s.date DESC LIMIT 1) AS cumulative_then,
       'WOULD DELETE' AS action
  FROM metrics f
 WHERE f.project = 'Uniswap' AND f.metric = 'gross_burn_tokens'
   AND f.value > 1000000
 ORDER BY f.date;
-- Every row returned should be a measuring-point artefact, not a burn. If ANY row here looks
-- like a real (if large) day, stop and re-check before deleting.

-- E3. THE DELETE. Back up first: cp metrics.db metrics.db.bak-$(date +%Y%m%d)
--     Threshold is 1,000,000: an order of magnitude above the largest plausible day
--     (~200k) and two below the artefact (~111.3M), so it cannot catch a real reading.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'Uniswap' AND metric = 'gross_burn_tokens' AND value > 1000000;
-- COMMIT;

-- E4. VERIFY. gross_burn_tokens should now sum to a few hundred thousand over the window, and
--     the next build should render it GREEN/AMBER rather than RED/implausible_delta.
-- SELECT COUNT(*) AS rows_left, MIN(value), MAX(value), SUM(value)
--   FROM metrics WHERE project='Uniswap' AND metric='gross_burn_tokens';

-- =======================================================================================
-- 2026-09-21 — ISSUANCE ROWS DERIVED UNDER THE NET-OF-BURN MISTAKE
--
-- Every gross_issuance_tokens row written by the OLD formula for a TRANSFER-BURN project
-- understates issuance by exactly that period's burn. The formula keyed on the burn mechanism
-- (a transfer burn does not reduce the contract's totalSupply, so issuance = d(supply)) while
-- the stored supply came from CoinGecko, which SUBTRACTS the dead-address balance. Confirmed:
--   GEODNET  1,000,000,000.00 - 961,518,067.62 = 38,481,932.38 = burn balance, exactly
--   Uniswap  1,000,000,000 - 888,114,418.92 = 111,885,581 ~= burn balance 111,953,581
-- For a non-minting token the result is approximately -burn, which is why Uniswap reported
-- -242,000 and tripped negative_derived_issuance.
--
-- AFFECTED: the four transfer-burn projects — Uniswap, GEODNET, PancakeSwap, Venice AI.
-- GEODNET's derivation is separately suppressed, so it should have no derived rows at all.
-- PancakeSwap and Venice now REFUSE to derive (convention untested), so their old rows are
-- orphaned by a formula that no longer runs.
--
-- F1. LOOK ONLY — every derived issuance row on the four, with its source. A source of
--     'derived:d_supply' on a transfer-burn project is a row written under the old formula.
SELECT project, source, COUNT(*) AS rows, MIN(date) AS first_seen, MAX(date) AS last_seen,
       MIN(value) AS min_value, MAX(value) AS max_value
  FROM metrics
 WHERE metric = 'gross_issuance_tokens'
   AND project IN ('Uniswap', 'GEODNET', 'PancakeSwap', 'Venice AI')
 GROUP BY project, source
 ORDER BY project, first_seen;

-- F2. LOOK ONLY — the rows themselves, negatives first. A negative issuance row should not
--     exist at all (the derivation rejects them), so any that appear here predate that guard.
SELECT date, project, value, source, tier, 'WOULD DELETE' AS action
  FROM metrics
 WHERE metric = 'gross_issuance_tokens'
   AND project IN ('Uniswap', 'GEODNET', 'PancakeSwap', 'Venice AI')
   AND source LIKE 'derived:%'
 ORDER BY value, project, date;

-- F3. THE DELETE. Scoped to DERIVED rows only, so a measured issuance figure — if one ever
--     arrives from a real source — is never swept up with them.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE metric = 'gross_issuance_tokens'
--    AND project IN ('Uniswap', 'GEODNET', 'PancakeSwap', 'Venice AI')
--    AND source LIKE 'derived:%';
-- COMMIT;

-- F4. VERIFY, and expect DIFFERENT outcomes per project rather than a clean sweep:
--     Uniswap      rebuilds from the next run under d(total_supply_gross) — NOT d(supply)+burn,
--                  which was retired on 2026-09-22 after it invented 219,999.99 of UNI issuance
--                  out of provider read timing. See section L, which is the narrow version of
--                  this delete and the one to run if F has already been applied once. Zero is a
--                  normal answer for a day when the only supply movement was the burn.
--     GEODNET      stays empty — its derivation is suppressed on separate grounds.
--     PancakeSwap  stays empty until total_supply_convention is tested and declared.
--     Venice AI    same.
-- SELECT project, COUNT(*) FROM metrics WHERE metric='gross_issuance_tokens'
--  GROUP BY project ORDER BY project;


-- ========================================================================================
-- G. AERODROME'S EMISSION AND REBASE ROWS — a weekly figure stored daily, and, for the
--    emission leg, the wrong quantity entirely.                          added 2026-09-21
-- ========================================================================================
-- TWO FAULTS IN THE SAME ROWS, and each alone would justify clearing them.
--
-- (1) GRANULARITY. Both reads return ONE figure per weekly epoch. They were dated to the RUN,
--     so every daily run laid down another row carrying the same number, and a trailing-30-day
--     SUM adds them up as though each were a separate week's emissions. Roughly 7x, latent only
--     because the series was days old. Both contracts now declare granularity="weekly" and the
--     adapter dates them to the EPOCH START, so re-reads inside one epoch overwrite one key.
--
-- (2) THE EMISSION LEG IS NOT AN EMISSION. Minter.updatePeriod() assigns `weekly` back only on
--     its non-tail branch, so the first epoch where weekly < TAIL_START freezes the variable
--     for good while the real emission becomes totalSupply * tailEmissionRate / MAX_BPS.
--     Replaying the contract's constants puts the freeze at epoch 67, value 8,969,149.540108 —
--     and the stored figure is 8,969,149. These rows are a dead constant, not a rate, and no
--     amount of re-dating makes them right. The rebase leg (emissions_tokens, tokensPerWeek)
--     is a REAL per-week figure and has fault (1) only.
--
-- G1. LOOK ONLY — what is there, and whether the values are flat (the signature of a frozen
--     read) or moving. Flat across many dates on the emission leg confirms the diagnosis.
SELECT metric, source, COUNT(*) AS rows, COUNT(DISTINCT value) AS distinct_values,
       MIN(date) AS first_seen, MAX(date) AS last_seen, MIN(value) AS min_value, MAX(value) AS max_value
  FROM metrics
 WHERE project = 'Aerodrome'
   AND metric IN ('gross_issuance_tokens', 'emissions_tokens')
 GROUP BY metric, source
 ORDER BY metric, first_seen;

-- G2. LOOK ONLY — the rows themselves. Expect one per run day, all carrying 8,969,149 on the
--     emission leg and 481,250 on the rebase leg.
SELECT date, metric, value, source, tier, 'WOULD DELETE' AS action
  FROM metrics
 WHERE project = 'Aerodrome'
   AND metric IN ('gross_issuance_tokens', 'emissions_tokens')
 ORDER BY metric, date;

-- G3. THE DELETE. Scoped to the two metrics on Aerodrome only. Both series rebuild from the
--     next run: the rebase leg with the same figures under epoch dates, the emission leg with
--     whatever the tail formula actually produces — which is NOT 8,969,149 and may be several
--     times it, depending on where tailEmissionRate has been nudged to.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'Aerodrome'
--    AND metric IN ('gross_issuance_tokens', 'emissions_tokens');
-- COMMIT;

-- G4. VERIFY after the next run. Expect ONE row per metric per epoch, dated to a THURSDAY
--     (ve(3,3) epochs flip on the Unix week boundary, which is a Thursday), and the emission
--     leg's source to carry its branch: '[tail@<n>bps]' in tail mode, '[weekly<...=no]' if the
--     protocol is somehow still pre-tail.
-- SELECT date, metric, value, source,
--        CASE CAST(STRFTIME('%w', date) AS INTEGER) WHEN 4 THEN 'Thursday' ELSE 'NOT a Thursday'
--        END AS epoch_boundary
--   FROM metrics
--  WHERE project = 'Aerodrome'
--    AND metric IN ('gross_issuance_tokens', 'emissions_tokens')
--  ORDER BY metric, date;


-- ========================================================================================
-- H. GEODNET gross_burn_tokens — the OTHER half of section D.                2026-09-21
-- ========================================================================================
-- Section D above handles GEODNET's treasury_holding_tokens, where the measuring point moved
-- because a third wallet was added to the sum. The audit of run 20260921T100546Z reports
-- measuring_point_changed on gross_burn_tokens TOO, and D does not cover it. These are the
-- SELECTs for it. D's cleanup has also never been run — run both together.
--
-- ** DO NOT ASSUME THIS IS THE SAME FAULT AS D. ** It probably is not, and the difference
-- decides whether anything should be deleted at all. GEODNET's burn series is fed from two
-- places ON PURPOSE:
--
--   dune:8683175          a MONTHLY historical backfill, aggregating ERC-20 transfers to
--                         0x...dEaD on Polygon and a Solana burn token account
--   chain:...:delta       the LIVE daily figure, differenced from burn_address_balance
--
-- and burn_backfill_spans_chains is True precisely so Polygon-era burns sit in the same series
-- as the Solana ones. build_workbook's measuring_point_changed guard counts DISTINCT sources
-- and cannot tell "history plus live read of one quantity, by design" from "the address moved
-- and the delta across the move is not a flow". So there are two possible readings:
--
--   (i)  BY DESIGN. The two sources measure the same burn over non-overlapping spans, the
--        guard is over-broad here, and deleting either half would destroy real history. The
--        fix is NOT a delete — it is to decide whether a declared backfill source should count
--        as a second measuring point at all.
--   (ii) A REAL CHANGE. The chain read's own composition moved (a burn address added or
--        swapped), exactly as D's treasury read did, and the old composition's rows should go.
--
-- H1 and H2 tell you which. Nothing below deletes anything. The DELETE is deliberately absent
-- rather than commented out, because which rows to remove depends on the answer, and under
-- reading (i) the answer is none.
--
-- H1. LOOK ONLY — every distinct source, with its span. Reading (i) looks like two sources
--     whose date ranges DO NOT OVERLAP (Dune ending where the chain read begins). Reading (ii)
--     looks like two chain:... compositions, and probably overlapping or adjacent.
SELECT source, COUNT(*) AS rows, MIN(date) AS first_seen, MAX(date) AS last_seen,
       MIN(value) AS min_value, MAX(value) AS max_value
  FROM metrics
 WHERE project = 'GEODNET' AND metric = 'gross_burn_tokens'
 GROUP BY source
 ORDER BY first_seen;

-- H2. LOOK ONLY — the handover. If the two sources meet cleanly at a boundary this is reading
--     (i). If they overlap, the overlapping dates are double-counted in every window that
--     spans them, which is a real fault whichever reading applies.
SELECT date, value, source, tier
  FROM metrics
 WHERE project = 'GEODNET' AND metric = 'gross_burn_tokens'
 ORDER BY date;

-- H3. LOOK ONLY — the same question for the cumulative the live flow is differenced from. A
--     composition change HERE is what would produce reading (ii), and it is also what section
--     E's implausible_delta guard now catches at read time on the flow.
SELECT source, COUNT(*) AS rows, MIN(date) AS first_seen, MAX(date) AS last_seen
  FROM metrics
 WHERE project = 'GEODNET' AND metric = 'burn_address_balance'
 GROUP BY source
 ORDER BY first_seen;

-- H4. AND THE OVERLAP, stated as a number rather than eyeballed from H2. Any row here is
--     double-counted by a window spanning it, under either reading.
SELECT date, COUNT(*) AS sources_on_this_date, GROUP_CONCAT(source, ' | ') AS which,
       SUM(value) AS summed_as_if_distinct
  FROM metrics
 WHERE project = 'GEODNET' AND metric = 'gross_burn_tokens'
 GROUP BY date
HAVING COUNT(*) > 1
 ORDER BY date;


-- ========================================================================================
-- I. ETHER.FI locked_tokens — the Dune series replaced by a contract read.   2026-09-22
-- ========================================================================================
-- WHAT CHANGED: locked_tokens was Dune 8683038's staked_supply. It is now
-- sETHFI.totalSupply(), read directly (config contracts.sethfi_shares). The Dune series is
-- KEPT and moves to locked_tokens_dashboard, the cross-check metric.
--
-- ** THIS IS A CORRECTION, NOT A CHANGE OF BASIS, and the number moves a long way. **
--     Dune staked_supply (2026-09-10)   141,470,107.5
--     sETHFI.totalSupply() on-chain      89,748,241.27
--     ETHFI held by the sETHFI contract 111,163,214.70
-- The Dune figure is 1.58x the sETHFI that exists and 30,306,893 more ETHFI than the staking
-- contract holds. A staked figure larger than the entire share supply is not that supply under
-- any convention, and the multi-chain sETHFI deployments (Scroll, Arbitrum, Base — together
-- about $2.2m) are nowhere near large enough to close it. So expect locked_tokens to DROP by
-- roughly 37% on the next run. That is the fault going away.
--
-- WHY THE OLD ROWS MUST GO: they are sourced 'dune:8683038' and the new ones will be sourced
-- 'chain:ethereum:sethfi_shares'. Two distinct sources in one series is exactly what
-- build_workbook's measuring_point_changed guard is for, and it will correctly blank
-- locked_tokens until the old rows are cleared. That is the guard working — the series really
-- does span a change of measuring point — so the fix is the cleanup, not an exemption.
--
-- I1. LOOK ONLY — confirm the series really is Dune-sourced and see what is there.
SELECT source, COUNT(*) AS rows, MIN(date) AS first_seen, MAX(date) AS last_seen,
       MIN(value) AS min_value, MAX(value) AS max_value
  FROM metrics
 WHERE project = 'Ether.fi' AND metric = 'locked_tokens'
 GROUP BY source
 ORDER BY first_seen;

-- I2. LOOK ONLY — the rows themselves. Every one should carry dune:8683038. A row sourced
--     'chain:...' means the new read has already run, and the cleanup is now urgent rather
--     than preparatory: the series is spanning both sources right now.
SELECT date, value, source, tier, 'WOULD MOVE' AS action
  FROM metrics
 WHERE project = 'Ether.fi' AND metric = 'locked_tokens'
 ORDER BY date;

-- I3. THE MOVE, NOT A DELETE. The readings are real Dune output and become the cross-check
--     series; renaming the metric keeps the history and puts the 1.58x divergence on the sheet
--     as a cross-check disagreement instead of discarding the evidence for it.
--     Scoped to dune-sourced rows so a chain read already written is never swept up.
-- BEGIN;
-- UPDATE metrics
--    SET metric = 'locked_tokens_dashboard'
--  WHERE project = 'Ether.fi' AND metric = 'locked_tokens'
--    AND source LIKE 'dune:%';
-- COMMIT;

-- I4. VERIFY after the next run. Expect locked_tokens single-sourced from the chain read at
--     ~89.7m, locked_tokens_dashboard holding the Dune history at ~141.5m, and
--     lock_assets_per_share producing a value at last — around 1.2386, the real
--     assets-per-share, NOT the ~0.79 the Dune denominator would have given.
-- SELECT metric, source, COUNT(*) AS rows, MIN(date) AS first_seen, MAX(date) AS last_seen,
--        MAX(value) AS latest_value
--   FROM metrics
--  WHERE project = 'Ether.fi'
--    AND metric IN ('locked_tokens', 'locked_tokens_dashboard', 'locked_tokens_underlying',
--                   'lock_assets_per_share')
--  GROUP BY metric, source
--  ORDER BY metric, first_seen;


-- ========================================================================================
-- ========================================================================================
-- J. PANCAKESWAP'S 5,931,409 RESIDUAL — RESOLVED 2026-09-22. It is outboundAmount.
--    LOOK ONLY, and now a re-check rather than an investigation.
-- ========================================================================================
-- ** THE FIRST VERSION OF THIS SECTION HAD A FALSE PREMISE. ** It said we sum bsc:token and
-- base:token_base and therefore double-count Base, and predicted the residual would equal
-- Base's totalSupply. WE NEVER SUMMED THEM: contracts.token_base was unverified, the tier-2
-- gate refused it, and J1 across all 20 logged runs shows only a bsc:token component. The
-- premise was read off the contract LIST without checking the GATE that decides which of those
-- contracts is actually read.
--
-- THE ARITHMETIC, on BSC alone, straight from the store:
--     BSC contract totalSupply (2026-09-21)   5,387,735,435.7511
--     CoinGecko total_supply (same date)        330,627,631.6128
--     difference                              5,057,107,804.1383
--     burn_address_balance                    5,051,176,395.1126
--     residual                                    5,931,409.0257
--
-- That residual is outboundAmount: CAKE locked in CakeProxyOFT
-- (0xb274202daBA6AE180c665B4fbE59857b7c3a8091) backing the representations on CAKE's other
-- eight chains — Ethereum, Arbitrum, Aptos, opBNB, zkSync, Base, Linea, Polygon zkEVM. The
-- contract's own accounting says so: circulatingSupply() = totalSupply() - outboundAmount, so
-- BSC's raw totalSupply() carries everything locked cross-chain.
--
-- ** CONCLUSION: CoinGecko nets out BOTH the dead-address burn AND the cross-chain lock. ** That
-- is a more complete convention than GEODNET, Uniswap and Venice AI, whose providers net out the
-- burn alone, and PancakeSwap now carries its own value for it —
-- total_supply_convention = net_of_burn_and_cross_chain_lock. It is not cosmetic: it changes the
-- issuance FORMULA, which needs a third term (d(outbound)) we have no series for, so issuance
-- derivation REFUSES for this project until outboundAmount is wired.
--
-- WHAT THE SECTION IS FOR NOW: re-running the arithmetic after any change, and settling the one
-- confirmation still outstanding (J4). Nothing here is a fix, and no fix is needed on our side —
-- BSC alone was always the right read.
--
-- J1. THE PER-CHAIN BREAKDOWN, from the run log. _emit_parts logs one line per component, so
--     Base's totalSupply is ALREADY STORED and needs no new read. Compare the base:token_base
--     figure against 5,931,409.03.
SELECT run_id, ts, message
  FROM run_log
 WHERE project = 'PancakeSwap'
   AND status = 'ok'
   AND (message LIKE '%component%token%' OR message LIKE '%components:%')
   AND (message LIKE 'total_supply%' OR message LIKE '%total_supply_gross%')
 ORDER BY ts DESC;

-- J2. THE SAME THING FROM THE SUMMED SOURCE STRING, as a cross-check on J1 — the composition
--     is named in the source, so a row here confirms which chains were actually summed even if
--     the log has rotated.
SELECT date, metric, value, source
  FROM metrics
 WHERE project = 'PancakeSwap'
   AND metric IN ('total_supply', 'total_supply_gross')
 ORDER BY date DESC;

-- J3. THE ARITHMETIC, against whatever is in the store right now rather than the figures quoted
--     above, so this stays honest if the numbers have moved.
--
--     ** FIXED 2026-09-22. The first version returned BLANK contract_sum and residual columns —
--     not "no rows", which is a much easier failure to notice, but NULLs, which render as empty
--     cells and look like an absent answer rather than a broken query. **
--     It looked the contract figure up by metric name, 'total_supply_gross'. That metric only
--     exists from 2026-09-22; every historical row, including the audited run this section is
--     about, stored the chain read under 'total_supply' alongside CoinGecko's. So the subquery
--     found nothing, NULL propagated through the subtraction, and the query silently reported
--     nothing at all.
--
--     RESOLVED BY SOURCE INSTEAD, which survives the rename in both directions: the chain read is
--     sourced 'chain:%' whatever metric it lands under, and CoinGecko's is sourced 'coingecko%'.
--     The diagnostic column names which leg is missing when one is, so a blank can never again be
--     mistaken for an answer.
WITH contract_read AS (
    SELECT value, date FROM metrics
     WHERE project = 'PancakeSwap'
       AND metric IN ('total_supply', 'total_supply_gross')
       AND source LIKE 'chain:%'
     ORDER BY date DESC LIMIT 1
), provider_read AS (
    SELECT value, date FROM metrics
     WHERE project = 'PancakeSwap'
       AND metric = 'total_supply'
       AND source LIKE 'coingecko%'
     ORDER BY date DESC LIMIT 1
), burn_read AS (
    SELECT value, date FROM metrics
     WHERE project = 'PancakeSwap'
       AND metric = 'burn_address_balance'
     ORDER BY date DESC LIMIT 1
)
SELECT
    (SELECT value FROM contract_read)                       AS bsc_contract_total,
    (SELECT date  FROM contract_read)                       AS contract_date,
    (SELECT value FROM provider_read)                       AS coingecko_total,
    (SELECT date  FROM provider_read)                       AS coingecko_date,
    (SELECT value FROM burn_read)                           AS burn_cumulative,
      (SELECT value FROM contract_read)
    - (SELECT value FROM provider_read)                     AS difference,
      (SELECT value FROM contract_read)
    - (SELECT value FROM provider_read)
    - (SELECT value FROM burn_read)                         AS residual_is_outbound_amount,
    -- WHICH LEG IS MISSING, if any. A blank arithmetic column is ambiguous between "no data"
    -- and "broken query"; this says which, and that ambiguity is the bug being fixed.
    CASE
      WHEN (SELECT value FROM contract_read) IS NULL
        THEN 'NO CHAIN-SOURCED SUPPLY ROW — is the tier 2 read running? check the Run Log'
      WHEN (SELECT value FROM provider_read) IS NULL
        THEN 'NO COINGECKO SUPPLY ROW — the comparison needs both sides'
      WHEN (SELECT value FROM burn_read) IS NULL
        THEN 'NO burn_address_balance ROW'
      ELSE 'all three legs present — the residual above should be ~5,931,409 = outboundAmount'
    END                                                     AS diagnostic;

-- J4. THE ONE CONFIRMATION STILL OUTSTANDING, and it is now a confirmation rather than a test —
--     the arithmetic has already identified the number. Needs a live BSC read, which the session
--     that wrote this had no egress for:
--
--       CakeProxyOFT.outboundAmount()                                  -- preferred
--       CAKE.balanceOf(0xb274202daBA6AE180c665B4fbE59857b7c3a8091)     -- the locked balance
--
--     Expect the SAME ORDER OF MAGNITUDE, not an exact match: bridging is continuous, so the
--     figure moves between the run that produced 5,931,409.0257 and whenever it is read. A few
--     percent either way confirms it; a different order of magnitude does not.
--
--     outboundAmount is the better of the two — balanceOf also picks up CAKE sent to the proxy
--     directly, which outboundAmount does not count.


-- ========================================================================================
-- K. THE BLOB TIER — numpy integers stored in INTEGER columns.            2026-09-22
--    K1-K3 LOOK ONLY. K4 repairs, and is only needed if you want the old rows readable.
-- ========================================================================================
-- WHAT HAPPENED: sqlite3 stores a numpy integer as a BLOB in an INTEGER column, silently.
-- numpy.float64 subclasses Python float and binds as REAL; numpy.int64 does NOT subclass int,
-- so it falls through to the buffer protocol and 8 little-endian bytes go in without an error:
--
--     np.int64(2)  ->  typeof() = 'blob',  x'0200000000000000'
--
-- It surfaced one step from the end of the run, three layers from the cause: build_workbook's
-- int(b'\x02\x00...') in write_review_queue, after a clean fetch of 6,967 rows.
--
-- THE CAUSE was fetch/validate.py's change_threshold branch, rewritten on 2026-09-22 for the
-- S7 partial-day fix. It moved from itertuples() — which hands back Python ints — to
-- groupby/.iloc[], where a Series element off a mixed-dtype frame is a numpy scalar. Only rows
-- with reason='change_threshold' are affected; the out_of_bounds branch still used itertuples
-- and is clean. FIXED at the site AND at the store boundary (store._int_or_none), so no write
-- path can do this again without stopping the run and naming the column.
--
-- ** YOU PROBABLY DO NOT NEED K4. ** review_queue is rebuilt per run and read for the LATEST
-- run only (store.review_queue defaults to it, since 2026-09-22), so one clean run leaves the
-- poisoned rows behind as history that nothing reads. K4 is for tidiness or if you want the
-- older runs to stay queryable.
--
-- K1. THE ROW(S), exactly as asked — which ones, what wrote them, when.
SELECT rowid, run_id, ts, project, metric, reason, action, source,
       typeof(tier) AS tier_type, tier AS tier_raw
  FROM review_queue
 WHERE typeof(tier) = 'blob'
 ORDER BY ts, rowid;

-- K2. THE SAME EXPOSURE ANYWHERE ELSE. Every integer column across every table that takes one,
--     because the fix was applied to all of them and the audit should be too. Expect zero rows
--     outside review_queue.tier — and if any appear, say which table before assuming otherwise.
SELECT 'review_queue.tier' AS column_name, COUNT(*) AS blob_rows FROM review_queue WHERE typeof(tier) = 'blob'
UNION ALL SELECT 'staging.tier',            COUNT(*) FROM staging     WHERE typeof(tier) = 'blob'
UNION ALL SELECT 'metrics.tier',            COUNT(*) FROM metrics     WHERE typeof(tier) = 'blob'
UNION ALL SELECT 'run_log.tier',            COUNT(*) FROM run_log     WHERE typeof(tier) = 'blob'
UNION ALL SELECT 'run_log.rows',            COUNT(*) FROM run_log     WHERE typeof(rows) = 'blob'
UNION ALL SELECT 'gap_report.priority',     COUNT(*) FROM gap_report  WHERE typeof(priority) = 'blob'
UNION ALL SELECT 'fetch_status.last_rows',  COUNT(*) FROM fetch_status WHERE typeof(last_rows) = 'blob';

-- K3. CONFIRM THE DIAGNOSIS BEFORE REPAIRING. Every blob should be 8 bytes and decode to a
--     plausible tier (1-4). hex() makes the little-endian layout visible: x'0200000000000000'
--     is 2. Anything that is NOT 8 bytes, or decodes outside 1-4, is a different fault — stop
--     and report it rather than running K4 over it.
SELECT rowid, project, metric, reason,
       length(tier)                                          AS byte_length,
       hex(tier)                                             AS hex_layout,
       -- THE SAME EXPRESSION K4 WRITES, so this row previews the repair rather than merely
       -- describing it. CAST(blob AS INTEGER) would read 0 here and look like a decode failure.
       unicode(substr(CAST(tier AS TEXT), 1, 1))             AS would_become,
       CASE WHEN length(tier) = 8
             AND unicode(substr(CAST(tier AS TEXT), 1, 1)) BETWEEN 1 AND 4
            THEN 'K4 will repair this row'
            ELSE 'NOT an 8-byte tier in 1-4 — a different fault, do not run K4 over it'
       END                                                   AS verdict
  FROM review_queue
 WHERE typeof(tier) = 'blob'
 ORDER BY rowid;

-- K4. THE REPAIR, only for rows K3 confirmed are 8-byte little-endian tiers in 1-4. It reads the
--     low byte, which is the whole value for any tier that small, and leaves anything else alone.
--     Not needed for the workbook to build — see the note above.
-- BEGIN;
-- UPDATE review_queue
--    SET tier = unicode(substr(CAST(tier AS TEXT), 1, 1))
--  WHERE typeof(tier) = 'blob'
--    AND length(tier) = 8
--    AND unicode(substr(CAST(tier AS TEXT), 1, 1)) BETWEEN 1 AND 4;
-- COMMIT;

-- K5. VERIFY — K1 should return nothing, and the tiers should read as plain integers.
-- SELECT typeof(tier) AS tier_type, tier, COUNT(*) AS rows
--   FROM review_queue GROUP BY typeof(tier), tier ORDER BY tier_type, tier;


-- ========================================================================================
-- L. THE PHANTOM ISSUANCE ROWS — d(net supply) + burn, where the two came from             2026-09-22
--    different providers sampled at different moments. L1-L3 LOOK. L4 deletes.
-- ========================================================================================
-- WHAT HAPPENED: Uniswap's 2026-09-21 run derived gross_issuance_tokens = 219,999.99 while
-- total_supply_gross read EXACTLY 1,000,000,000 at both ends. UNI's contract has minted nothing
-- since 2021. The figure was the gap between CoinGecko's net total_supply and the chain's burn
-- delta — the burn landed inside the window and the provider's net figure had not yet moved to
-- match it, so adding the two counted the burn as a mint.
--
-- THE FIX, already in fetch/__init__.py: where total_supply_gross exists (the net_of_burn
-- projects, which are the only ones the add_burn rule applies to), issuance is derived from
-- d(total_supply_gross) directly — one source, one read, no drift. The net+burn figure is still
-- computed and any disagreement is raised as 'issuance_route_divergence' in the Review Queue,
-- so the cross-check survives the change rather than being dropped with the route.
--
-- SO THESE ROWS ARE HISTORY FROM A FORMULA THE CODE NO LONGER USES. They do not heal on the next
-- run: metrics is keyed (date, project, metric), so a new run writes a NEW date and the old rows
-- stay, feeding the 30-day issuance window and the burn/issuance ratio as if measured.
--
-- L1. EVERY derived:d_supply+burn ISSUANCE ROW, and whether a gross reading for the same project
--     and date disagrees with it. A row whose gross delta is 0 while the stored issuance is not
--     is a phantom; a row where they agree was right by luck and is still better re-derived.
SELECT m.date, m.project, m.value AS stored_issuance, m.source, m.tier,
       g.value                                                   AS gross_supply_same_day,
       (SELECT p.value FROM metrics p
         WHERE p.project = m.project AND p.metric = 'total_supply_gross'
           AND p.date < m.date ORDER BY p.date DESC LIMIT 1)      AS gross_supply_prior,
       g.value - (SELECT p.value FROM metrics p
                   WHERE p.project = m.project AND p.metric = 'total_supply_gross'
                     AND p.date < m.date ORDER BY p.date DESC LIMIT 1)
                                                                  AS gross_delta,
       CASE WHEN g.value IS NULL THEN 'no gross reading — cannot judge, leave it'
            WHEN (SELECT p.value FROM metrics p
                   WHERE p.project = m.project AND p.metric = 'total_supply_gross'
                     AND p.date < m.date ORDER BY p.date DESC LIMIT 1) IS NULL
                 THEN 'only one gross reading — cannot judge, leave it'
            WHEN ABS(m.value - (g.value - (SELECT p.value FROM metrics p
                                            WHERE p.project = m.project AND p.metric = 'total_supply_gross'
                                              AND p.date < m.date ORDER BY p.date DESC LIMIT 1))) > 1.0
                 THEN 'PHANTOM — the contract does not agree'
            ELSE 'agrees with the gross delta'
       END                                                        AS verdict
  FROM metrics m
  LEFT JOIN metrics g
    ON g.project = m.project AND g.metric = 'total_supply_gross' AND g.date = m.date
 WHERE m.metric = 'gross_issuance_tokens'
   AND m.source = 'derived:d_supply+burn'
 ORDER BY m.project, m.date;

-- L2. WHAT THE WORKBOOK IS SHOWING BECAUSE OF THEM. The 30-day issuance window and the
--     burn/issuance ratio both read this metric, so the size of the error is the size of these
--     values relative to the real (gross) issuance, which for Uniswap and GEODNET is zero.
SELECT project,
       COUNT(*)      AS phantom_rows,
       MIN(date)     AS first_date,
       MAX(date)     AS last_date,
       SUM(value)    AS total_issuance_claimed
  FROM metrics
 WHERE metric = 'gross_issuance_tokens'
   AND source = 'derived:d_supply+burn'
 GROUP BY project
 ORDER BY project;

-- L3. WHAT SURVIVES. Every OTHER route to this metric stays — a measured figure, a declared
--     schedule, or the new gross delta. Run this before and after L4 and only the
--     derived:d_supply+burn count should change.
SELECT source, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date
  FROM metrics
 WHERE metric = 'gross_issuance_tokens'
 GROUP BY source
 ORDER BY source;

-- L4. THE DELETE. Scoped to the metric AND the retired source string, so nothing measured and
--     nothing derived by the surviving route can be caught by it. Rows for a project with no
--     second gross reading are deleted too and that is deliberate: the formula that produced
--     them is retired, so the value has no route back to a source we would defend. The metric
--     re-derives from d(total_supply_gross) on the next run for every project that has two
--     gross readings, and correctly reports a gap for any that does not.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE metric = 'gross_issuance_tokens'
--    AND source = 'derived:d_supply+burn';
-- COMMIT;

-- L5. VERIFY — L1 returns nothing, and L3 no longer lists derived:d_supply+burn.
-- SELECT COUNT(*) AS should_be_zero FROM metrics
--  WHERE metric = 'gross_issuance_tokens' AND source = 'derived:d_supply+burn';


-- ========================================================================================
-- M. THE SAME-DAY RE-RUN ROWS — a differenced flow anchored on its own earlier row.  2026-09-22
--    M1-M3 LOOK. M4 deletes the flow rows only; the balances they came from are untouched.
-- ========================================================================================
-- WHAT HAPPENED: Hyperliquid's burn_address_balance moved 231,934.0021 HYPE across 2026-09-21
-- and gross_burn_tokens for that date recorded 83,344.4791 — about a third of it. Four runs
-- landed on that date.
--
-- THE MECHANISM, confirmed in code before anything was changed: fetch/hypercore.py differenced
-- against prior_values, which is store.latest_values() — the newest reading of ANY date, and
-- therefore THIS MORNING'S after the first run of the day — while taking the date that guards
-- the subtraction from prior_dates, which is store.values_before(today). The same-date guard in
-- derive_flow_from_cumulative saw yesterday's date, found an interval, and allowed it. So each
-- run wrote only the increment since the previous run, and because metrics is keyed
-- (date, project, metric) each one OVERWROTE the last. The final increment kept the day's key.
--
-- This is the fault store.values_before was written for — PancakeSwap's 59,857,159.01 burn
-- becoming 0.0123 on 2026-09-14. It was fixed for the chain adapter then and left standing in
-- three others. hypercore.py, tron.py and scrape.py now all take prior_delta, and a test asserts
-- the second argument of every derive_flow_from_cumulative call across the four adapters.
--
-- AND A READ-TIME GUARD NOW CATCHES IT WHEREVER IT ALREADY HAPPENED: build_workbook's
-- unreconciled_flow blanks any differenced flow whose values do not sum to its stock's move
-- across the same span. So these rows are already withheld from the sheet — this section is for
-- clearing them so the column comes back, not for making the number safe.
--
-- M1. THE AFFECTED DATES — every date holding MORE THAN ONE run's worth of readings for a
--     cumulative balance. fetched_at is per-write, so several distinct fetched_at values on one
--     date is exactly the re-run signature. LOOK ONLY.
SELECT project, metric, date, COUNT(DISTINCT fetched_at) AS writes_on_this_date,
       MIN(fetched_at) AS first_write, MAX(fetched_at) AS last_write
  FROM metrics
 WHERE metric IN ('burn_address_balance', 'buyback_fund_balance', 'treasury_holding_tokens')
 GROUP BY project, metric, date
HAVING COUNT(DISTINCT fetched_at) > 1
 ORDER BY project, metric, date;

-- M2. THE TELESCOPING IDENTITY, PER PROJECT, exactly as build_workbook computes it: the flows
--     must sum to the stock's move between the reading before the first flow and the reading on
--     the last. A non-zero residual is tokens the flow column never reported.
SELECT f.project,
       MIN(f.date)   AS first_flow_date,
       MAX(f.date)   AS last_flow_date,
       COUNT(*)      AS flow_rows,
       SUM(f.value)  AS flows_recorded,
       (SELECT e.value FROM metrics e
         WHERE e.project = f.project AND e.metric = 'burn_address_balance'
           AND e.date <= (SELECT MAX(x.date) FROM metrics x
                           WHERE x.project = f.project AND x.metric = 'gross_burn_tokens'
                             AND x.source LIKE '%:delta')
         ORDER BY e.date DESC LIMIT 1)                     AS stock_at_end,
       (SELECT b.value FROM metrics b
         WHERE b.project = f.project AND b.metric = 'burn_address_balance'
           AND b.date <  (SELECT MIN(x.date) FROM metrics x
                           WHERE x.project = f.project AND x.metric = 'gross_burn_tokens'
                             AND x.source LIKE '%:delta')
         ORDER BY b.date DESC LIMIT 1)                     AS stock_at_start
  FROM metrics f
 WHERE f.metric = 'gross_burn_tokens'
   AND f.source LIKE '%:delta'
 GROUP BY f.project
 ORDER BY f.project;
--     Read it as: (stock_at_end - stock_at_start) - flows_recorded. Zero is the only passing
--     answer. Anything else is the residual, and its size is how much the burn column understates.

-- M3. THE ROWS THAT WOULD GO, for the one project confirmed affected. Scoped to the :delta
--     source, so a burn figure from Dune or a dashboard — a real period total, not a difference
--     — is never caught by it.
SELECT date, project, metric, value, source, tier, fetched_at, 'WOULD DELETE' AS action
  FROM metrics
 WHERE project = 'Hyperliquid'
   AND metric = 'gross_burn_tokens'
   AND source LIKE 'hypercore_info:%:delta'
 ORDER BY date;

-- M4. THE DELETE. THE BALANCES ARE NOT TOUCHED — burn_address_balance is a good reading on every
--     one of these dates and is what the flows re-derive from. Deleting only the flows means the
--     next run differences the current balance against the last stored balance and recovers the
--     whole span as one figure on the next run's date.
--
--     WHAT YOU GET BACK IS NOT WHAT WAS LOST. The per-day split across 2026-09-21 cannot be
--     recovered — the intermediate readings were overwritten and are gone. One correct figure
--     spanning the gap replaces several wrong ones; the 30-day total becomes right and the daily
--     shape inside it does not come back. That is the honest outcome and there is no route to a
--     better one short of a transfer-history source.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'Hyperliquid'
--    AND metric = 'gross_burn_tokens'
--    AND source LIKE 'hypercore_info:%:delta';
-- COMMIT;

-- M5. VERIFY — M3 returns nothing, and the balance series is intact.
-- SELECT metric, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date
--   FROM metrics WHERE project = 'Hyperliquid'
--    AND metric IN ('burn_address_balance', 'gross_burn_tokens')
--  GROUP BY metric;


-- ========================================================================================
-- N. THE GEODNET BURN HANDOVER — where the backfill stops and the live read starts.  2026-09-22
--    LOOK ONLY. Nothing here deletes anything; the point is to read off the seam.
-- ========================================================================================
-- gross_burn_tokens is read from two places BY DESIGN: Dune 8683175 backfills the monthly
-- history from before this tool existed, and chain:polygon:burn_polygon's delta carries it from
-- the first live run onward. build_workbook's measuring_point_changed blanked the column for it,
-- which is the guard doing exactly what it was built for on the shape it was built for —
-- Uniswap's Firepit-to-dead-address move — and the wrong answer for a handover.
--
-- config now declares the pair (GEODNET's series_handover). THE DECLARATION NAMES THE PAIR AND
-- THE ORDER AND NOTHING ELSE: the no-overlap is re-checked against these stored dates on every
-- build, and any third source blanks the column again. So this section is what you run to see
-- what the guard is checking.
--
-- N1. THE SEAM. Expect the Dune leg to end BEFORE the chain leg begins, with a clear gap — the
--     Dune query sets drop_current_period, so its last month is a completed one.
--     If last_date of the Dune leg >= first_date of the chain leg, the legs OVERLAP: the same
--     burn is counted twice and the workbook will correctly blank the column again. The fix for
--     that is deleting the overlapping Dune rows, NOT widening the declaration.
SELECT source, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date,
       MIN(value) AS min_value, MAX(value) AS max_value, SUM(value) AS total
  FROM metrics
 WHERE project = 'GEODNET' AND metric = 'gross_burn_tokens'
 GROUP BY source
 ORDER BY first_date;

-- N2. THE COMPOSITION STEP, WHICH THE HANDOVER DECLARATION DOES NOT SETTLE.
--     The Dune leg sums POLYGON AND SOLANA burns (value_cols tokens_burned + sol_tokens_burned);
--     the live leg reads chain:polygon:burn_polygon ALONE, because the Solana burn destination
--     is a token account and no Solana adapter exists. So the series NARROWS at the seam, and a
--     month-on-month comparison across it understates the later month by the Solana burn rate.
--
--     This query cannot separate the two columns — they were summed before storage — so it gives
--     the monthly totals either side of the seam instead. Read the Dune query's own
--     sol_tokens_burned column to size the step before comparing across it.
SELECT date, value, source,
       CASE WHEN source LIKE 'dune:%' THEN 'Polygon + Solana' ELSE 'Polygon only' END AS covers
  FROM metrics
 WHERE project = 'GEODNET' AND metric = 'gross_burn_tokens'
 ORDER BY date DESC
 LIMIT 12;

-- N3. ANY THIRD SOURCE, which blanks the column whatever the declaration says. Expect exactly
--     the two declared points. A Solana read appearing here is GOOD NEWS for the composition
--     step above and still has to be added to series_handover before the column comes back.
SELECT DISTINCT source FROM metrics
 WHERE project = 'GEODNET' AND metric = 'gross_burn_tokens'
 ORDER BY source;


-- ========================================================================================
-- O. A RETIRED METRIC'S ROWS — treasury_holding_tokens_chain_crosscheck.            2026-09-22
--    O1-O2 LOOK. O3 deletes. O4 checks nothing else was retired and left behind.
-- ========================================================================================
-- THE METRIC LIVED THREE DAYS. Created 2026-09-18, when Maple's transparency page was promoted
-- to the primary metric name and the daoMultisig chain read was demoted onto this one by
-- metric_override. Left unfed 2026-09-21, when maple.finance/robots.txt turned out to disallow
-- that page — which we respect rather than route around — so the page never fetched, the primary
-- went blank, and the chain read was restored to treasury_holding_tokens.
--
-- IT WAS KEPT FOR A DAY ON THE ARGUMENT THAT RE-APPLYING THE DEMOTION WOULD BE ONE LINE. That is
-- still true and it was the wrong trade: an unfed metric is a permanent Gap Report row, a
-- permanent not_applicable explanation to maintain, and a column on the sheet that can only ever
-- be empty — all of it standing by for a page this tool does not fetch. Retired 2026-09-22:
-- config entry, sanity bound, label, gap-report kind mapping and these rows go together.
--
-- WHAT IS NOT LOST. The chain read's HISTORY is not in these rows alone — the same daoMultisig
-- balance is written to treasury_holding_tokens before 2026-09-18 and again from 2026-09-21.
-- What goes is the three-day window when it was stored under the other name. O2 shows whether
-- those three days are also covered under the primary name before anything is deleted.
--
-- O1. THE ROWS. Expect a handful, all Maple, all dated 2026-09-18 to 2026-09-20, all sourced
--     from the chain read. Anything outside that is not what this section was written against —
--     stop and report rather than deleting.
SELECT date, project, metric, value, source, tier, fetched_at, 'WOULD DELETE' AS action
  FROM metrics
 WHERE metric = 'treasury_holding_tokens_chain_crosscheck'
 ORDER BY project, date;

-- O2. WHAT THE PRIMARY NAME HOLDS OVER THE SAME DATES, so the loss is known before it is taken.
--     A date present on the left and absent on the right is a day that disappears from the
--     treasury series entirely. That is acceptable — it is three days of a metric whose figure
--     is disputed against Maple's own publication by ~3x anyway — but it should be a decision,
--     not a surprise.
SELECT x.date,
       x.value  AS crosscheck_value,
       p.value  AS primary_value,
       CASE WHEN p.value IS NULL THEN 'this date disappears from treasury_holding_tokens'
            ELSE 'already covered under the primary name' END AS effect
  FROM metrics x
  LEFT JOIN metrics p
    ON p.project = x.project AND p.date = x.date AND p.metric = 'treasury_holding_tokens'
 WHERE x.metric = 'treasury_holding_tokens_chain_crosscheck'
 ORDER BY x.date;

-- O3. THE DELETE. Scoped to the metric name, which is exact: nothing else was ever written under
--     it, and the name no longer exists in config so nothing can write it again.
-- BEGIN;
-- DELETE FROM metrics WHERE metric = 'treasury_holding_tokens_chain_crosscheck';
-- DELETE FROM gap_report WHERE metric = 'treasury_holding_tokens_chain_crosscheck';
-- DELETE FROM review_queue WHERE metric = 'treasury_holding_tokens_chain_crosscheck';
-- COMMIT;

-- O4. THE SAME QUESTION ASKED GENERALLY — every metric name in the store that config no longer
--     declares. A retired metric that keeps its rows is invisible: nothing renders it, so nothing
--     reports it, and it sits in the store being counted by every "how much history do we have"
--     query. Cross-check the names this returns against config.METRICS before deleting any of
--     them: a metric that is merely PROJECT-SCOPED (only_projects) is still declared and must not
--     appear here, and if it does, read the list rather than the query.
SELECT metric, COUNT(*) AS rows, COUNT(DISTINCT project) AS projects,
       MIN(date) AS first_date, MAX(date) AS last_date
  FROM metrics
 GROUP BY metric
 ORDER BY metric;

-- O5. VERIFY — O1 returns nothing.
-- SELECT COUNT(*) AS should_be_zero FROM metrics
--  WHERE metric = 'treasury_holding_tokens_chain_crosscheck';


-- ========================================================================================
-- P. STALE GAP REPORT AND REVIEW QUEUE ROWS — history that nothing reads.            2026-09-22
--    P1-P3 LOOK. P4 deletes rows from runs that are not the latest.
-- ========================================================================================
-- BOTH TABLES ARE REBUILT PER RUN and both are read for the LATEST run only — store.gap_report
-- and store.review_queue default to latest_run_id, fixed 2026-09-22 after the workbook was found
-- showing rows from whichever run happened to sort last. So every row carrying an older run_id is
-- inert: nothing renders it, nothing counts it, and it is not a stale FIGURE in the sense the
-- other sections of this file deal with. It is old rows in a table that is meant to be current.
--
-- WHY CLEAR THEM AT ALL, then. Two reasons and neither is the workbook:
--   1. "How many open gaps are there" is a question people ask of the STORE, and every such
--      query has to know to filter by run_id or it counts the same gap once per run it was open.
--   2. Two metrics were CLOSED PERMANENTLY on 2026-09-22 — Ethereum's gross_issuance_tokens and
--      net_mint_monthly, see config.py UNAVAILABLE. Their rows stop being generated from the
--      next run, and the old ones would otherwise sit here reading as open work that was
--      answered.
--
-- P1. HOW MUCH OF EACH TABLE IS CURRENT. Expect one run_id to hold nearly everything and the
--     rest to be a long tail. LOOK ONLY.
SELECT 'gap_report' AS tbl, run_id, MIN(ts) AS first_written, COUNT(*) AS rows,
       CASE WHEN run_id = (SELECT run_id FROM gap_report ORDER BY ts DESC, rowid DESC LIMIT 1)
            THEN 'CURRENT — this is what the workbook reads' ELSE 'superseded' END AS status
  FROM gap_report GROUP BY run_id
UNION ALL
SELECT 'review_queue', run_id, MIN(ts), COUNT(*),
       CASE WHEN run_id = (SELECT run_id FROM review_queue ORDER BY ts DESC, rowid DESC LIMIT 1)
            THEN 'CURRENT — this is what the workbook reads' ELSE 'superseded' END
  FROM review_queue GROUP BY run_id
 ORDER BY tbl, first_written;

-- P2. THE PERMANENTLY-CLOSED METRICS, so their rows are seen before they go. These two are the
--     reason this section exists now rather than at some tidier moment: a closed item whose rows
--     stay on file reads as open work that was answered.
SELECT run_id, project, metric, reason, suggestion
  FROM gap_report
 WHERE project = 'Ethereum'
   AND metric IN ('gross_issuance_tokens', 'net_mint_monthly')
 ORDER BY ts;

-- P3. ANY GAP ROW WHOSE METRIC CONFIG NO LONGER DECLARES. The companion to section O4: a metric
--     that was retired leaves gap rows behind exactly as it leaves metric rows behind, and
--     nothing sweeps them. Cross-check what this returns against config.METRICS before deleting
--     — a PROJECT-SCOPED metric (only_projects) is still declared and must not appear here.
SELECT metric, COUNT(*) AS rows, COUNT(DISTINCT project) AS projects, MAX(ts) AS last_written
  FROM gap_report GROUP BY metric ORDER BY metric;

-- P4. THE DELETE. Everything that is not the current run, in both tables. Scoped by run_id so
--     the current run is untouched whatever else is in there, and deliberately NOT scoped by
--     date: "older than N days" would delete the current run on a store that has not been run
--     for N days, which is precisely when someone is most likely to be looking at it.
-- BEGIN;
-- DELETE FROM gap_report
--  WHERE run_id <> (SELECT run_id FROM gap_report ORDER BY ts DESC, rowid DESC LIMIT 1);
-- DELETE FROM review_queue
--  WHERE run_id <> (SELECT run_id FROM review_queue ORDER BY ts DESC, rowid DESC LIMIT 1);
-- COMMIT;

-- P5. VERIFY — P1 shows one run_id per table, both CURRENT.
