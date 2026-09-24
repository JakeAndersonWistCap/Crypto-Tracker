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
-- L0. THE GENESIS SUPPLIES THE VERDICTS REST ON.                          ADDED 2026-09-22
--     One row per project whose genesis supply is ON FILE WITH A SOURCE, mirroring
--     config.genesis_supply. A test asserts these literals equal config's, so the two cannot
--     drift — the SQL cannot run a Python function and a number copied by hand is exactly the
--     kind of thing that goes stale silently.
--
--     UNISWAP, from Uniswap's own governance repository (Uni.sol, fetched 2026-09-22):
--         uint public totalSupply = 1_000_000_000e18; // 1 billion Uni
--     mint() does totalSupply = totalSupply + amount, and there is no _burn — a UNI burn is a
--     transfer to the dead address, which totalSupply still counts. Both facts are needed and
--     both are in that one file.
--
--     GEODNET IS DELIBERATELY ABSENT, and it is the project this rule must NOT be applied to.
--     Its total_supply_gross also reads exactly 1,000,000,000, so the arithmetic would "work" —
--     and the conclusion would be false in the sense the column means. GEOD is entirely
--     pre-minted: emissions are DISTRIBUTION from mining wallets, so the figure would sit at
--     genesis for ever while real tokens reached the market. The inference needs minting to MOVE
--     totalSupply, which for GEOD it does not, and the genesis figure itself is not yet sourced
--     from GEODNET's own documentation. Two independent reasons to leave it out; either is
--     enough. Add it here only once BOTH are settled, from GEODNET's own docs.
--
-- L1. EVERY derived:d_supply+burn ISSUANCE ROW, with a verdict.          VERDICT WIDENED 2026-09-22
--     A row whose gross delta is 0 while the stored issuance is not is a phantom; a row where
--     they agree was right by luck and is still better re-derived.
--
--     ** ONE GROSS READING CAN BE DECISIVE, and the old verdict said it never was. ** Uniswap's
--     220,000 row was marked "only one gross reading — cannot judge, leave it". But the single
--     reading is EXACTLY the genesis supply, and minting is the only thing that moves that
--     figure up while nothing moves it down. So cumulative issuance since deployment is zero —
--     not "unmeasured", zero — and a quantity that is zero over all time is zero over every
--     window inside it. A second reading would add nothing a proof already gives.
WITH genesis(project, tokens) AS (
    VALUES ('Uniswap', 1000000000.0)
),
judged AS (
    SELECT m.date, m.project, m.metric, m.value AS stored_issuance, m.source, m.tier,
           g.value                                                   AS gross_supply_same_day,
           (SELECT p.value FROM metrics p
             WHERE p.project = m.project AND p.metric = 'total_supply_gross'
               AND p.date < m.date ORDER BY p.date DESC LIMIT 1)      AS gross_supply_prior,
           gen.tokens                                                 AS genesis_supply,
           CASE
             -- THE PROOF COMES FIRST, because it settles the rows the delta test cannot reach.
             WHEN gen.tokens IS NOT NULL AND g.value = gen.tokens AND m.value > 0
                  THEN 'PHANTOM — gross supply is EXACTLY the genesis supply, so cumulative '
                    || 'issuance since deployment is zero and any positive figure is spurious'
             WHEN g.value IS NULL THEN 'no gross reading — cannot judge, leave it'
             WHEN (SELECT p.value FROM metrics p
                    WHERE p.project = m.project AND p.metric = 'total_supply_gross'
                      AND p.date < m.date ORDER BY p.date DESC LIMIT 1) IS NULL
                  THEN 'only one gross reading and no genesis on file — cannot judge, leave it'
             WHEN ABS(m.value - (g.value - (SELECT p.value FROM metrics p
                                             WHERE p.project = m.project AND p.metric = 'total_supply_gross'
                                               AND p.date < m.date ORDER BY p.date DESC LIMIT 1))) > 1.0
                  THEN 'PHANTOM — the contract does not agree'
             ELSE 'agrees with the gross delta'
           END                                                        AS verdict
      FROM metrics m
      LEFT JOIN metrics g
        ON g.project = m.project AND g.metric = 'total_supply_gross' AND g.date = m.date
      LEFT JOIN genesis gen ON gen.project = m.project
     WHERE m.metric = 'gross_issuance_tokens'
)
SELECT date, project, stored_issuance, source, tier, gross_supply_same_day, gross_supply_prior,
       genesis_supply,
       gross_supply_same_day - gross_supply_prior AS gross_delta, verdict
  FROM judged
 WHERE source = 'derived:d_supply+burn'
 ORDER BY project, date;

-- L2. WHAT THE WORKBOOK IS SHOWING BECAUSE OF THEM. The 30-day issuance window and the
--     burn/issuance ratio both read this metric, so the size of the error is the size of these
--     values relative to the real (gross) issuance, which for Uniswap is PROVABLY zero.
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
--     schedule, or the new gross delta. Run this before and after L4/L5 and only the counts the
--     deletes name should change.
SELECT source, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date
  FROM metrics
 WHERE metric = 'gross_issuance_tokens'
 GROUP BY source
 ORDER BY source;

-- L3b. THE ROWS THE GENESIS PROOF CONDEMNS, WHATEVER SOURCE THEY CAME FROM.    ADDED 2026-09-22
--      This is the widening, and it is why the proof is worth recording rather than just
--      arguing. L4's delete can only reach rows carrying the retired formula's source string. A
--      positive issuance row for a project whose gross supply has never left genesis is spurious
--      no matter which route wrote it, and this is the list. LOOK FIRST — L5 deletes exactly
--      these rows and nothing else.
--
--      ZERO ROWS ARE LEFT ALONE on purpose: a stored 0 says the same thing the proof says, so
--      there is nothing to correct and deleting it would only remove a true observation.
--
--      AND schedule:config ROWS ARE EXCLUDED, which is not a hedge. A schedule row is not an
--      observation that went wrong — it is config asserting what WOULD be issued, rewritten from
--      config on every run. Deleting it would remove a row that comes straight back and would
--      hide the thing that actually needs fixing: a declared schedule contradicted by a contract
--      that has never minted is a CONFIG ERROR, and L3c reports it as one.
WITH genesis(project, tokens) AS (
    VALUES ('Uniswap', 1000000000.0)
)
SELECT m.date, m.project, m.value AS stored_issuance, m.source, m.tier,
       g.value AS gross_supply_same_day, gen.tokens AS genesis_supply,
       'WOULD DELETE — issuance is provably zero' AS action
  FROM metrics m
  JOIN genesis gen ON gen.project = m.project
  JOIN metrics g ON g.project = m.project AND g.metric = 'total_supply_gross'
                AND g.date = m.date AND g.value = gen.tokens
 WHERE m.metric = 'gross_issuance_tokens'
   AND m.value > 0
   AND m.source <> 'schedule:config'
 ORDER BY m.project, m.date;

-- L3c. THE CONTRADICTION THAT IS A CONFIG FIX, NOT A DELETE.                   ADDED 2026-09-22
--      A declared issuance schedule producing positive figures for a project whose gross supply
--      has never left genesis. Nothing here should be deleted: the rows regenerate from config
--      on the next run, so the only durable remedy is in config. Expected to be empty today —
--      Uniswap declares no issuance_schedule — and it exists so that adding one without
--      noticing this cannot pass silently.
WITH genesis(project, tokens) AS (
    VALUES ('Uniswap', 1000000000.0)
)
SELECT m.project, COUNT(*) AS schedule_rows, MIN(m.date) AS first_date, MAX(m.date) AS last_date,
       SUM(m.value) AS claimed_by_schedule,
       'FIX CONFIG — the schedule says minted, the contract says never' AS action
  FROM metrics m
  JOIN genesis gen ON gen.project = m.project
  JOIN metrics g ON g.project = m.project AND g.metric = 'total_supply_gross'
                AND g.date = m.date AND g.value = gen.tokens
 WHERE m.metric = 'gross_issuance_tokens'
   AND m.value > 0
   AND m.source = 'schedule:config'
 GROUP BY m.project;

-- L4. THE DELETE for the retired formula. Scoped to the metric AND the retired source string, so
--     nothing measured and nothing derived by the surviving route can be caught by it. Rows for
--     a project with no second gross reading are deleted too and that is deliberate: the formula
--     that produced them is retired, so the value has no route back to a source we would defend.
--     The metric re-derives from d(total_supply_gross) on the next run for every project that has
--     two gross readings, and correctly reports a gap for any that does not.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE metric = 'gross_issuance_tokens'
--    AND source = 'derived:d_supply+burn';
-- COMMIT;

-- L5. THE DELETE the genesis proof licenses — the rows L3b listed, from ANY source. Run L3b
--     first and read it; this deletes precisely what it printed.
--
--     THE EXISTS IS THE SCOPE. A row only qualifies if its project has a genesis figure on file
--     AND a total_supply_gross reading ON THE SAME DATE that is exactly equal to it. No
--     tolerance: both sides are whole-token integers, so a tolerance could only let a real mint
--     through.
--
--     THE GENESIS FIGURE IS A CASE RATHER THAN THE CTE L3b USES, and the reason is the preview,
--     not taste. run_sql previews a DELETE by reusing its WHERE clause verbatim against the
--     table the statement names — a CTE defined above the DELETE would not travel with that
--     clause, the preview would fail on a missing table, and the delete would be refused. This
--     form is one table and one clause, so what is printed is exactly what goes. A project not
--     named in the CASE yields NULL, and `g.value = NULL` is never true, so it is untouched.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE metric = 'gross_issuance_tokens'
--    AND value > 0
--    AND source <> 'schedule:config'
--    AND EXISTS (SELECT 1 FROM metrics g
--                 WHERE g.project = metrics.project
--                   AND g.metric = 'total_supply_gross'
--                   AND g.date = metrics.date
--                   AND g.value = CASE metrics.project WHEN 'Uniswap' THEN 1000000000.0 END);
-- COMMIT;

-- L6. VERIFY — L1 returns nothing, L3b returns nothing, and L3 no longer lists
--     derived:d_supply+burn.
-- SELECT COUNT(*) AS should_be_zero FROM metrics
--  WHERE metric = 'gross_issuance_tokens' AND source = 'derived:d_supply+burn';


-- ========================================================================================
-- M. THE SAME-DAY RE-RUN ROWS — a differenced flow anchored on its own earlier row.  2026-09-22
--    M1-M3 LOOK. M4 REBUILDS the flow rows from the balances, which are untouched — it is a
--    utility, rederive.py, not a DELETE. Nothing in this section deletes anything.
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
-- clearing them so the column comes back, not for making the number safe — and clearing them
-- means REBUILDING them from the balances (M4), which recovers the daily shape rather than
-- collapsing the span into one figure.
--
-- M1. THE AFFECTED DATES — how many runs wrote on each date.            CORRECTED 2026-09-22
--
--     ** THE FIRST VERSION OF THIS QUERY COULD NEVER RETURN A ROW. ** It grouped `metrics` by
--     (project, metric, date) and kept the groups with COUNT(DISTINCT fetched_at) > 1. That is
--     the table's PRIMARY KEY, so every group holds exactly one row and the count is 1 by
--     construction. It returned "(no rows)", which read as "no date was written twice" — the
--     opposite of the truth, on the section whose entire premise is that four runs landed on
--     2026-09-21.
--
--     THE UPSERT IS WHY, AND IT IS THE SAME FACT THE SECTION IS ABOUT: a second run on one date
--     overwrites the first, so `metrics` cannot hold the evidence of a re-run. Asking it to is
--     asking the wrong table. run_log keeps one row per run per source per project and does
--     still have it.
--
--     Dates before the store had a run_log simply do not appear. That is honest — the log does
--     not reach back — and it is why the rebuild in M4 reads the balances rather than this.
SELECT substr(r.ts, 1, 10)        AS date,
       r.project,
       r.source,
       COUNT(DISTINCT r.run_id)   AS runs_on_this_date,
       MIN(r.ts)                  AS first_run,
       MAX(r.ts)                  AS last_run
  FROM run_log r
 WHERE r.status = 'ok'
 GROUP BY date, r.project, r.source
HAVING COUNT(DISTINCT r.run_id) > 1
 ORDER BY date DESC, r.project;

-- M2. THE TELESCOPING IDENTITY, PER PROJECT.                          REWRITTEN 2026-09-22
--     The first version returned a blank stock_at_start for five of six projects — GEODNET,
--     Hyperliquid, PancakeSwap, Tron and Venice AI — with only Uniswap computing.
--
--     ** THE FIRST DIAGNOSIS OF THAT WAS WRONG AND IS CORRECTED HERE. ** It was written up as a
--     query fault of the J3 class — a correlated subquery nested two levels inside a GROUP BY,
--     resolving to NULL. It is not. The old query was run against a store seeded with all six
--     real source shapes and it resolved the anchor for every one of them. Nothing about the
--     nesting was broken.
--
--     WHAT THE BLANKS ACTUALLY MEAN: for those five projects the store holds NO
--     burn_address_balance reading dated strictly earlier than their first differenced flow row.
--     There is no anchor to find, so the identity cannot be checked at all. Reproduced by
--     truncating one project's stock series in the seeded store: that project blanks and the
--     others still compute, which is exactly the shape that was observed.
--
--     THAT IS NOT A NULL RESULT — IT IS THE FINDING. A delta is computed from a reading STRICTLY
--     EARLIER than itself, so at the moment each of these flow rows was written its anchor
--     existed. Two things can put the store in this state and they have different remedies:
--       (a) the stock rows were deleted, by an earlier cleanup or a rebuild — the flows are
--           still checkable against whatever stock history remains further forward; or
--       (b) the flow series REACHES BACK FURTHER than the stock series, because the early flow
--           rows came from a different adapter or a backfill that never wrote a balance.
--     first_stock below distinguishes them: under (b) the stock series simply starts later.
--
--     SO THE REWRITE IS NOT A FIX FOR A BROKEN LOOKUP. It is CTEs plus a `diagnostic` column, so
--     that the answer "this could not be checked, and here is which step could not resolve"
--     stops arriving as an empty cell indistinguishable from "checked, nothing wrong". That
--     confusion is the actual defect, and it is the same one as J3 even though the cause is not.
WITH flow AS (
    -- Only DIFFERENCED rows telescope. GEODNET's series is stitched — a monthly Dune backfill
    -- under a live chain delta — and the Dune rows are period figures, not differences, so they
    -- are excluded here and the anchor is taken from the first DELTA rather than the first row.
    SELECT project,
           MIN(date)  AS first_flow,
           MAX(date)  AS last_flow,
           COUNT(*)   AS flow_rows,
           SUM(value) AS flows_recorded
      FROM metrics
     WHERE metric = 'gross_burn_tokens'
       AND source LIKE '%:delta%'          -- NOT '%:delta': a source can carry a trailing
                                            -- marker (:recurring-only) or a bracketed
                                            -- annotation, and an anchored LIKE misses both
     GROUP BY project
),
stock_span AS (
    SELECT project, MIN(date) AS first_stock, MAX(date) AS last_stock, COUNT(*) AS stock_rows
      FROM metrics WHERE metric = 'burn_address_balance' GROUP BY project
),
stock_end AS (
    SELECT f.project, b.value AS stock_at_end, b.date AS end_date
      FROM flow f
      JOIN metrics b ON b.project = f.project AND b.metric = 'burn_address_balance'
                    AND b.date = (SELECT MAX(e.date) FROM metrics e
                                   WHERE e.project = f.project
                                     AND e.metric = 'burn_address_balance'
                                     AND e.date <= f.last_flow)
),
stock_start AS (
    SELECT f.project, b.value AS stock_at_start, b.date AS start_date
      FROM flow f
      JOIN metrics b ON b.project = f.project AND b.metric = 'burn_address_balance'
                    AND b.date = (SELECT MAX(e.date) FROM metrics e
                                   WHERE e.project = f.project
                                     AND e.metric = 'burn_address_balance'
                                     AND e.date < f.first_flow)
)
SELECT f.project, f.first_flow, f.last_flow, f.flow_rows, f.flows_recorded,
       p.first_stock, p.stock_rows,
       s.start_date, s.stock_at_start, e.end_date, e.stock_at_end,
       e.stock_at_end - s.stock_at_start                        AS stock_moved,
       (e.stock_at_end - s.stock_at_start) - f.flows_recorded   AS residual,
       CASE
         WHEN p.first_stock IS NULL
              THEN 'NO STOCK SERIES AT ALL — the flows were differenced from something that is '
                || 'not in the store under this name'
         WHEN s.stock_at_start IS NULL AND p.first_stock >= f.first_flow
              THEN 'CANNOT BE CHECKED — the stock series starts ' || p.first_stock
                || ', on or after the first differenced flow ' || f.first_flow || '. Every delta '
                || 'was computed from a reading STRICTLY EARLIER than itself, so the anchor '
                || 'existed when the flow was written. Either those stock rows were deleted, or '
                || 'the early flow rows came from an adapter or backfill that wrote no balance.'
         WHEN s.stock_at_start IS NULL
              THEN 'CANNOT BE CHECKED — no stock reading before ' || f.first_flow
                || ' although the series starts ' || p.first_stock || '; the anchor row is '
                || 'missing from inside the span'
         WHEN e.stock_at_end IS NULL
              THEN 'CANNOT BE CHECKED — no stock reading at or before ' || f.last_flow
                || ': the stock series ends before the flow series does'
         WHEN ABS((e.stock_at_end - s.stock_at_start) - f.flows_recorded) < 0.000001
              THEN 'RECONCILES'
         ELSE 'DOES NOT RECONCILE — the flows are short by the residual shown'
       END                                                      AS diagnostic
  FROM flow f
  LEFT JOIN stock_span  p ON p.project = f.project
  LEFT JOIN stock_start s ON s.project = f.project
  LEFT JOIN stock_end   e ON e.project = f.project
 ORDER BY f.project;
--     ** THE PYTHON HAD A REAL FAULT OF ITS OWN, and it was found by checking rather than
--     assumed from the SQL. ** build_workbook resolved a flow's stock through a hand-written
--     literal dict that had already gone stale: Chainlink's Reserve inflow and Sky's two
--     decomposed burn legs had no entry, so the telescoping check never ran for them AT ALL —
--     no answer, silently. It is derived from config now (config.stock_for_flow), and a check
--     that cannot run says why instead of skipping. That one WAS a lookup returning nothing.

-- M3. THE ROWS THAT WOULD BE REPLACED. Scoped to the :delta source, so a burn figure from Dune
--     or a dashboard — a real period total, not a difference — is never caught by it.
SELECT date, project, metric, value, source, tier, fetched_at, 'WOULD BE REBUILT' AS action
  FROM metrics
 WHERE project = 'Hyperliquid'
   AND metric = 'gross_burn_tokens'
   AND source LIKE 'hypercore_info:%:delta%'
 ORDER BY date;

-- M4. THE REMEDY IS A REBUILD, NOT A DELETE.                           REPLACED 2026-09-22
--
--     This section proposed deleting all eight differenced rows and letting the next run
--     difference the current balance against the last stored one, recovering the span as a
--     single figure. That works and it throws the history away: one number across ten days
--     instead of ten daily numbers, and the daily shape never comes back. It was described here
--     as "the honest outcome with no route to a better one". There is a better one.
--
--     THE BUG CORRUPTED THE FLOW ROWS. THE BALANCES ARE SOUND. burn_address_balance holds one
--     surviving reading per date — the last run of each day — and those readings were never
--     differenced, so nothing that went wrong touched them. A flow derived from a stock is
--     redundant information: delta(d) = stock(d) - stock(previous stored d). The whole series
--     rebuilds from readings that were never wrong, and the daily shape is recovered.
--
--     IT IS NOT SQL, because the refusals are not expressible as one. A pair yields no row if
--     the measuring point changed between the two readings (Uniswap's Firepit) or if the
--     cumulative fell, and each refusal has to be REPORTED per pair rather than dropped from a
--     result set. So it is a utility, general over (project, flow_metric, stock_metric):
--
--         python rederive.py Hyperliquid gross_burn_tokens            -- look: prints the plan
--         python rederive.py Hyperliquid gross_burn_tokens --apply    -- replace, one transaction
--
--     Plain mode writes nothing. It prints every stored row against its rebuilt value, the
--     change, the run count for that date, and every refusal with its reason. --apply takes a
--     typed REPLACE and swaps the differenced rows for the rebuilt ones in ONE transaction;
--     non-differenced rows are never touched. The stock metric defaults to whatever config
--     declares the flow was differenced from, so a flow cannot be rebuilt from a cumulative it
--     never came from.
--
--     EXPECTED, against the balances as at 2026-09-21: 47,387,407 less the 2026-09-11 reading,
--     which is roughly 231,934 HYPE across the span. Read the printed plan before applying.
--
--     NO DELETE IS SHIPPED HERE ANY MORE, commented out or otherwise. The rebuild replaces the
--     rows itself, and leaving a delete in the file next to it invites doing both.

-- M5. VERIFY — after the rebuild, the rows carry the :rederived marker and the balance series is
--     untouched. Also re-run M2: the telescoping identity is what the rebuild is FOR, so it must
--     now reconcile rather than merely look better.
-- SELECT metric, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date,
--        SUM(value) AS total
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


-- ========================================================================================
-- Q. staked_tokens RETIRED — MERGED INTO locked_tokens. THE ROWS MOVE.               2026-09-23
--    Q1-Q3 LOOK. Q4 is an UPDATE, not a DELETE: the readings were correct, the column was
--    duplicated. Nothing here destroys a figure.
-- ========================================================================================
-- WHAT HAPPENED: staked_tokens (archetypes 1,2,3) and locked_tokens (3) were the same quantity
-- under two names. Every chain read of a stake or an escrow writes locked_tokens — the kinds
-- ve_total_supply, stake_principal and stake_underlying all land there — so staked_tokens had no
-- route at all. Its only wiring was tier-4 Dune slots, every one of them with query_id None. A
-- permanently empty column, and one Gap Report row per project per run for a figure already on
-- the sheet under the other name.
--
-- THE SURVIVOR IS locked_tokens BECAUSE IT IS THE WIRED ONE, widened to [1,2,3] so nothing is
-- lost: that is exactly staked_tokens' old coverage. Ethereum's beacon-chain deposits and
-- Morpho's staking are archetype 1 and 2 and would have been dropped by retiring the wider name.
--
-- Q1. WHAT IS STORED UNDER THE RETIRED NAME. Expected to be small or empty — the column never
--     had a working source — but "expected" is not "checked", and a Dune query that was wired
--     once and later unwired would have left real readings here.
SELECT project, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date,
       GROUP_CONCAT(DISTINCT source) AS sources
  FROM metrics WHERE metric = 'staked_tokens'
 GROUP BY project ORDER BY project;

-- Q2. THE COLLISIONS, AND THEY DECIDE WHETHER THE MOVE IS SAFE. `metrics` is keyed
--     (date, project, metric), so an UPDATE that renames staked_tokens to locked_tokens fails
--     on any (project, date) that already holds a locked_tokens row — and a failed UPDATE inside
--     a transaction rolls the whole move back rather than half-applying it.
--
--     A COLLISION IS ALSO A FINDING, not just an obstacle: the same project holding two
--     different numbers for the same quantity on the same day means the two names were being fed
--     by sources that disagree, and which one is right is a question, not a merge conflict to
--     resolve by picking the newer row. READ THIS BEFORE RUNNING Q4.
SELECT s.project, s.date, s.value AS staked_value, s.source AS staked_source,
       l.value AS locked_value, l.source AS locked_source,
       s.value - l.value AS difference
  FROM metrics s
  JOIN metrics l ON l.project = s.project AND l.date = s.date AND l.metric = 'locked_tokens'
 WHERE s.metric = 'staked_tokens'
 ORDER BY s.project, s.date;

-- Q3. WHAT WOULD MOVE — every staked_tokens row with no locked_tokens row on the same date.
--     If Q2 returned nothing, this is all of Q1.
SELECT s.date, s.project, s.value, s.source, s.tier, 'WOULD MOVE to locked_tokens' AS action
  FROM metrics s
 WHERE s.metric = 'staked_tokens'
   AND NOT EXISTS (SELECT 1 FROM metrics l
                    WHERE l.project = s.project AND l.date = s.date
                      AND l.metric = 'locked_tokens')
 ORDER BY s.project, s.date;

-- Q4. THE MOVE. An UPDATE, because the readings were correct and only the column was duplicated
--     — the same treatment as section I's Ether.fi re-attribution, and for the same reason: a
--     history is evidence and deleting it to tidy a rename throws away the only record of what
--     the series used to say.
--
--     SCOPED TO NON-COLLIDING ROWS so it cannot fail on the primary key. Anything Q2 listed is
--     LEFT WHERE IT IS, deliberately: two disagreeing numbers for one quantity need a decision
--     about which source is right, and this file does not make that decision silently. Re-run Q1
--     afterwards — what remains is exactly the collision set, and it is the to-do.
-- BEGIN;
-- UPDATE metrics SET metric = 'locked_tokens'
--  WHERE metric = 'staked_tokens'
--    AND NOT EXISTS (SELECT 1 FROM metrics l
--                     WHERE l.project = metrics.project AND l.date = metrics.date
--                       AND l.metric = 'locked_tokens');
-- COMMIT;

-- Q5. THE GAP ROWS UNDER THE RETIRED NAME, which section P3 also covers. Listed here so the
--     retirement is finished in one place rather than half-done across two sections.
SELECT COUNT(*) AS gap_rows_for_retired_metric
  FROM gap_report WHERE metric = 'staked_tokens';

-- Q6. VERIFY — Q1 returns only the collision set (or nothing), and locked_tokens has grown by
--     exactly what Q3 listed.
-- SELECT metric, COUNT(*) AS rows, COUNT(DISTINCT project) AS projects
--   FROM metrics WHERE metric IN ('staked_tokens', 'locked_tokens') GROUP BY metric;


-- ========================================================================================
-- R. rwa_xyz_usd RETIRED — A CROSS-CHECK THAT COULD NEVER RUN.                     2026-09-23
--    R1-R2 LOOK. R3 deletes, and only because there is nothing to keep.
-- ========================================================================================
-- WHAT HAPPENED: rwa_xyz_usd was a deliberate SECOND OPINION on rwa_defillama_usd — the right
-- instinct, and the pattern this book uses everywhere it works. Here it never could: all eleven
-- sources.yaml entries were disabled stubs with `url: null` and the note "RWA.xyz has no API on
-- our tier". A cross-check that cannot run is not a cross-check; it is a gap row per chain per
-- run for a number nobody can fetch, and a permanently empty column on the sheet.
--
-- RE-CREATING IT MEANS WRITING THE DOM SCRAPER FIRST, not re-adding the metric. That is the work
-- the stub was standing in for and never became.
--
-- R1. IS THERE ANY DATA AT ALL? Expected to be none — the entries were never enabled — but a
--     metric can acquire rows from a manual override or a one-off backfill, and "expected" is
--     not "checked". IF THIS RETURNS ROWS, STOP: they are a measurement that was made, and they
--     belong in section I's treatment (moved to a surviving metric) rather than deleted.
SELECT project, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date,
       GROUP_CONCAT(DISTINCT source) AS sources
  FROM metrics WHERE metric = 'rwa_xyz_usd'
 GROUP BY project ORDER BY project;

-- R2. THE GAP AND REVIEW ROWS UNDER THE RETIRED NAME. These are the ones that actually exist,
--     and they are report artefacts rather than measurements.
SELECT 'gap_report' AS tbl, COUNT(*) AS rows, COUNT(DISTINCT project) AS projects
  FROM gap_report WHERE metric = 'rwa_xyz_usd'
 UNION ALL
SELECT 'review_queue', COUNT(*), COUNT(DISTINCT project)
  FROM review_queue WHERE metric = 'rwa_xyz_usd';

-- R3. THE DELETE — report artefacts only. RUN R1 FIRST AND READ IT: if R1 returned any metrics
--     rows, do not run this half of it, and decide what those rows should become instead.
--     Section P4 already sweeps non-current run_ids from both tables; this is scoped to the
--     retired METRIC so it catches the current run's rows too, which P4 deliberately does not.
-- BEGIN;
-- DELETE FROM gap_report   WHERE metric = 'rwa_xyz_usd';
-- DELETE FROM review_queue WHERE metric = 'rwa_xyz_usd';
-- COMMIT;

-- R4. VERIFY — R1 and R2 both return nothing.


-- ========================================================================================
-- S. THE PARTIAL-DAY HYPOTHESIS — LOOK ONLY. No delete, and none proposed yet.     2026-09-23
-- ========================================================================================
-- THE OBSERVATION: GEODNET volume on 2026-09-14 reads $385,770 against $12.45M on 09-21; World
-- Mobile $491,448 against $13.86M. Both about 3% of normal, on a date the tool ran intraday.
-- The proposed mechanism was that CoinGecko's incremental request starts AFTER the latest stored
-- date, so an intraday capture is written once and never overwritten.
--
-- ** THAT MECHANISM DOES NOT EXIST, and checking the code settles half the question. ** The
-- request is `market_chart?days=30&interval=daily` (fetch/coingecko.py) — a TRAILING WINDOW
-- counted back from now, not anchored to anything in the store. Every run since 09-14 has asked
-- for a range that contains 09-14 and would have upserted over it. So the value is not frozen
-- for want of being re-requested.
--
-- WHAT IS TRUE, AND IS A DIFFERENT PROBLEM: the partial CURRENT day is excluded from the CHANGE
-- CHECK (config.PROVIDERS_WITH_INCOMPLETE_CURRENT_PERIOD) but is still WRITTEN to the store. So
-- a partial figure does reach the sheet and does feed the 30-day window sums, for at least the
-- rest of that day. It should be corrected by the next run, and the queries below say whether it
-- is.
--
-- NOTHING IS FIXED HERE UNTIL THESE ARE READ. A re-pull would be the remedy if S2 shows the
-- value is stuck; a write-time drop of the current day would be the remedy if S3 shows it keeps
-- coming back. They are different changes and the data decides which.
--
-- S1. THE SUSPECT DAYS — every daily provider row that is under 20% of its own series' trailing
--     median. Not "under 3%", which would only find the two already known: the question is
--     whether this is a pattern and which dates it lands on.
WITH med AS (
    SELECT project, metric, AVG(value) AS typical
      FROM (SELECT project, metric, value,
                   ROW_NUMBER() OVER (PARTITION BY project, metric ORDER BY value) AS rn,
                   COUNT(*) OVER (PARTITION BY project, metric) AS n
              FROM metrics
             WHERE metric IN ('volume_usd', 'fees_usd', 'revenue_usd')
               AND date >= date('now', '-60 day'))
     WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
     GROUP BY project, metric
)
SELECT m.date, m.project, m.metric, m.value, med.typical,
       ROUND(m.value * 100.0 / NULLIF(med.typical, 0), 2) AS pct_of_typical,
       m.source, m.fetched_at
  FROM metrics m JOIN med ON med.project = m.project AND med.metric = m.metric
 WHERE m.date >= date('now', '-60 day')
   AND med.typical > 0
   AND m.value < med.typical * 0.20
 ORDER BY m.date DESC, m.project;

-- S2. IS IT STUCK, OR DID IT HEAL? fetched_at is per-write. A suspect day whose fetched_at is
--     its OWN date was written once and never revisited — which would be the frozen case. One
--     whose fetched_at is LATER has been re-written since and is the provider's own figure.
--     ** THIS IS THE QUERY THAT DECIDES WHICH FIX IS RIGHT. **
SELECT date, project, metric, value, fetched_at,
       CASE WHEN substr(fetched_at, 1, 10) <= date
            THEN 'WRITTEN ON THE DAY AND NEVER REVISITED — the frozen case'
            ELSE 'RE-WRITTEN on ' || substr(fetched_at, 1, 10) || ' — this IS the provider''s figure'
       END AS verdict
  FROM metrics
 WHERE metric IN ('volume_usd', 'fees_usd', 'revenue_usd')
   AND date >= date('now', '-60 day')
 ORDER BY date DESC, project
 LIMIT 200;

-- S3. THE RUN DATES, so a suspect day can be read against whether a run even happened after it.
--     A gap in this list is a day nothing was written, and a value cannot be corrected by a run
--     that did not occur.
SELECT substr(ts, 1, 10) AS day, COUNT(DISTINCT run_id) AS runs,
       MIN(substr(ts, 12, 5)) AS first_run, MAX(substr(ts, 12, 5)) AS last_run
  FROM run_log WHERE source = 'coingecko'
 GROUP BY day ORDER BY day DESC LIMIT 30;

-- S4. MORPHO'S FEES, the separate finding in the same review: $2.13 and $157 on individual days
--     against a stored 30-day total of ~$16.5M. Three orders of magnitude apart, which is a
--     different shape from a partial day and points at the slug or the series rather than timing.
SELECT date, value, source, tier, fetched_at
  FROM metrics WHERE project = 'Morpho' AND metric IN ('fees_usd', 'revenue_usd')
   AND date >= date('now', '-45 day')
 ORDER BY metric, date;


-- ========================================================================================
-- T. PARTIAL CURRENT-DAY FLOW ROWS — written before the day was over.              2026-09-23
--    T1-T2 LOOK. T3 deletes, and only rows a completed day has already outlived.
-- ========================================================================================
-- WHAT HAPPENED: a provider publishes the current day from the moment it starts, and the figure
-- is near-empty rather than proportional — Sky's fees read $7,586 against a typical $909,801 at
-- 12:40 UTC, past the halfway point; Morpho's read $139. Those rows sat inside every trailing-
-- window figure until the next run replaced them.
--
-- FIXED AT WRITE TIME on 2026-09-23: a daily FLOW from DefiLlama or CoinGecko dated today is no
-- longer stored at all (config.drop_current_day). Stocks are unaffected — a price or a balance
-- is correct at any hour, and dropping it would throw away the only reading of the day.
--
-- ** THIS SECTION IS FOR ROWS ALREADY IN THE STORE, AND MOST OF THEM DO NOT NEED IT. ** The
-- providers are called with a trailing window, so an ordinary run re-requests the whole span and
-- upserts a complete figure over yesterday's partial one. Section S2 confirmed that: every
-- 2026-09-21 row for the sixteen was re-written on 09-22. What survives is a partial day that
-- NOTHING RE-FETCHED — a project parked before the next run, or a date that fell out of the
-- trailing window before a run covered it.
--
-- T1. PARTIAL DAYS THAT NEVER HEALED. fetched_at is per-write: a row whose fetched_at is its own
--     date was written during that day and never revisited. For a daily provider flow that is
--     exactly the partial-day signature. A row re-written later IS the provider's own complete
--     figure and must be left alone.
SELECT m.date, m.project, m.metric, m.value, m.source, m.fetched_at,
       ROUND(julianday(substr(m.fetched_at, 1, 10)) - julianday(m.date), 0) AS days_to_rewrite
  FROM metrics m
 WHERE m.metric IN ('fees_usd', 'revenue_usd', 'holders_revenue_usd', 'volume_usd',
                    'customer_revenue_usd')
   AND (m.source LIKE 'defillama%' OR m.source LIKE 'coingecko%')
   AND substr(m.fetched_at, 1, 10) <= m.date
   AND m.date < date('now')
 ORDER BY m.date DESC, m.project;

-- T2. HOW WRONG THEY ARE, so the delete is a judgement about size rather than about dates. A
--     partial day is not merely low, it is near-empty: the observed cases were 0.0%-17.5% of
--     typical. A row at 80% of its neighbours is a quiet day, not a partial one, and deleting it
--     would remove a real observation.
WITH neighbours AS (
    SELECT m.date, m.project, m.metric, m.value,
           (SELECT AVG(n.value) FROM metrics n
             WHERE n.project = m.project AND n.metric = m.metric
               AND n.date BETWEEN date(m.date, '-7 day') AND date(m.date, '-1 day')) AS typical
      FROM metrics m
     WHERE m.metric IN ('fees_usd', 'revenue_usd', 'holders_revenue_usd', 'volume_usd',
                        'customer_revenue_usd')
       AND (m.source LIKE 'defillama%' OR m.source LIKE 'coingecko%')
       AND substr(m.fetched_at, 1, 10) <= m.date
       AND m.date < date('now')
)
SELECT date, project, metric, value, typical,
       ROUND(value * 100.0 / NULLIF(typical, 0), 2) AS pct_of_prior_week,
       CASE WHEN typical IS NULL OR typical = 0 THEN 'NO NEIGHBOURS — cannot judge, leave it'
            WHEN value < typical * 0.25 THEN 'PARTIAL — near-empty against its own prior week'
            ELSE 'A QUIET DAY, not a partial one — leave it'
       END AS verdict
  FROM neighbours
 ORDER BY date DESC, project;

-- T3. THE DELETE. Scoped by all three conditions together — a daily provider flow, never
--     re-written after its own date, and under a quarter of its own prior week. Any one of them
--     alone would take real observations with it.
--
--     DELETED RATHER THAN RE-FETCHED, because there is nothing to re-fetch: these are dates the
--     trailing window no longer reaches. A gap in a flow series is honest; a near-empty day
--     presented as a measurement is not, and it is the one that feeds the 30-day sums.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE metric IN ('fees_usd', 'revenue_usd', 'holders_revenue_usd', 'volume_usd',
--                   'customer_revenue_usd')
--    AND (source LIKE 'defillama%' OR source LIKE 'coingecko%')
--    AND substr(fetched_at, 1, 10) <= date
--    AND date < date('now')
--    AND value < 0.25 * (SELECT AVG(n.value) FROM metrics n
--                         WHERE n.project = metrics.project AND n.metric = metrics.metric
--                           AND n.date BETWEEN date(metrics.date, '-7 day')
--                                          AND date(metrics.date, '-1 day'))
--    AND (SELECT AVG(n.value) FROM metrics n
--          WHERE n.project = metrics.project AND n.metric = metrics.metric
--            AND n.date BETWEEN date(metrics.date, '-7 day')
--                           AND date(metrics.date, '-1 day')) > 0;
-- COMMIT;

-- T4. VERIFY — T2 returns nothing verdicted PARTIAL.

-- ========================================================================================
-- U. MORPHO'S PARENT-RESIDUAL fees_usd ROWS — the post-break days morpho-blue does not cover.
--    U1-U2 LOOK. U3 deletes. U4 verifies.                              2026-09-23T21:26Z
-- ========================================================================================
-- ** RE-SCOPED 2026-09-23. THE FIRST VERSION WOULD HAVE DELETED THE ONLY GOOD POST-BREAK DATA. **
-- As committed on 2026-09-22 (f18f607) this section selected `date >= '2026-09-12'` — right
-- that day, when every row from the break onward was the parent residual. morpho-blue then
-- recovered from 2026-09-21 and the recovery wrote 09-21 (704,123) and 09-22 (713,684) into the
-- same range. The section was never narrowed, so its preview listed those two real days beside
-- the nine residual ones and its DELETE would have removed them. An upper bound fixed at write
-- time goes stale the moment the thing it describes moves.
--
-- ** SO IT IS SCOPED BY WHAT IT TARGETS, NOT BY AN END DATE. ** The residual rows are exactly
-- the post-break days morpho-blue does not cover — the `uncovered` set that
-- fetch/llama._fees_with_restructure_guard computes and deliberately does not store. That set
-- is visible in the store without recomputing it, because of two facts:
--   (1) the recovery branch re-pulls the FULL history every run and upserts every day it
--       stores — never window_days — and
--   (2) store.py's upsert sets fetched_at = excluded.fetched_at, so every row the recovery
--       wrote carries the latest recovery run's timestamp.
-- A post-break row OLDER than the newest fees_usd write for Morpho is one the latest recovery
-- did not write: a day morpho-blue does not cover. Nothing in that rule needs keeping up to
-- date. When DefiLlama backfills a day, the next run rewrites it with a fresh timestamp and it
-- leaves U1 on its own.
--
-- TWO FIXED BOUNDS REMAIN, AND NEITHER CAN MOVE:
--   date >= '2026-09-12'  THE BREAK DATE (config defillama_restructure.break_date) — a
--                         historical fact, not a moving edge. AND IT IS LOAD-BEARING: Morpho's
--                         fees_usd runs back to 2021, before either child listing existed, so
--                         old parent-slug rows are ALSO never rewritten by recovery and would
--                         match the timestamp rule on its own. The floor keeps that history out.
--   value < 10000         A MAGNITUDE INTERLOCK on the DELETE, not the targeting rule. The
--                         residual is Morpho Midnight's 0..~200/day; Blue's fees are ~500,000+.
--                         If the timestamp rule ever picks a real-sized row, the interlock
--                         refuses it and U1's looks_like column shows the disagreement.
--
-- ** RUN THIS AFTER A RUN THAT REACHED MORPHO. ** If the newest write is not a recovery run the
-- rule has nothing to compare against — U2 shows which rows the newest write covered; if it
-- does not list 09-21 onward, stop.

-- U1. THE TARGETS. As of 2026-09-23 expect nine rows, 2026-09-12..2026-09-20, all
--     residual-sized and all from one earlier write.
SELECT date, value, source, tier, fetched_at,
       CASE WHEN value < 10000 THEN 'residual-sized'
            ELSE 'REAL-SIZED - NOT A TARGET' END AS looks_like
  FROM metrics
 WHERE project = 'Morpho'
   AND metric = 'fees_usd'
   AND source LIKE 'defillama%'
   AND date >= '2026-09-12'
   AND fetched_at < (SELECT MAX(fetched_at) FROM metrics
                      WHERE project = 'Morpho' AND metric = 'fees_usd')
 ORDER BY date;

-- U2. WHAT SURVIVES — every post-break row U1 does NOT select, which must be exactly the days
--     morpho-blue reports (2026-09-21 onward as of 2026-09-23). Then the pre-break count. Run
--     before and after U3; neither should move.
SELECT date, value, fetched_at
  FROM metrics
 WHERE project = 'Morpho'
   AND metric = 'fees_usd'
   AND date >= '2026-09-12'
   AND fetched_at >= (SELECT MAX(fetched_at) FROM metrics
                       WHERE project = 'Morpho' AND metric = 'fees_usd')
 ORDER BY date;

SELECT COUNT(*) AS pre_break_rows, MIN(date) AS first_date, MAX(date) AS last_date
  FROM metrics
 WHERE project = 'Morpho' AND metric = 'fees_usd' AND date < '2026-09-12';

-- U3. THE DELETE. The same rule as U1 plus the magnitude interlock. Deleted rather than left:
--     a near-empty residual beside good history feeds every trailing window as though it were
--     an observation, and there is no better number for these dates until DefiLlama backfills
--     morpho-blue for them. When it does, recovery stores the real sum for that day with no
--     human step — so this does not need re-running afterwards. It is not a date to update.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'Morpho'
--    AND metric = 'fees_usd'
--    AND source LIKE 'defillama%'
--    AND date >= '2026-09-12'
--    AND value < 10000
--    AND fetched_at < (SELECT MAX(fetched_at) FROM metrics
--                       WHERE project = 'Morpho' AND metric = 'fees_usd');
-- COMMIT;

-- U4. VERIFY — U1 returns nothing; U2's rows and the pre-break count are unchanged.
--     The level-break check then sees only real post-break days. It is NOT EVALUATED until
--     five of the seven window days are stored (fewer than the 60% floor), then evaluates clean
--     — see config's sheet_trace for why that is the right outcome and not a silence.
SELECT COUNT(*) AS residual_rows_left
  FROM metrics
 WHERE project = 'Morpho'
   AND metric = 'fees_usd'
   AND date >= '2026-09-12'
   AND value < 10000;

-- ========================================================================================
-- V. GEODNET actual_buyback_usd ROWS WRITTEN BY THE DERIVATION.                     2026-09-22
--    V1-V2 LOOK. V3 deletes, scoped to exactly the source string V1 shows.
-- ========================================================================================
-- WHAT HAPPENED: GEODNET's actual_buyback_usd is SOURCED — Dune query 8683175, usd_burned +
-- sol_usd_burned. _derive_buyback also computes the column (actual_buyback_tokens x price_usd)
-- and stood down only when the sourced rows were in the SAME RUN's frame. Tier 4 is a backfill
-- and does not run every day, so on every other day the derivation wrote into a column that
-- already had a measurement. The column then held two sources, and the build blanked it as
-- MEASURING_POINT_CHANGED — which is the guard working correctly on rows that should never have
-- been written.
--
-- FIXED AT WRITE TIME on 2026-09-22 (fetch/__init__.py, _derive_buyback): the derivation now
-- asks config.dune_query_declared, which is a fact about config rather than about which tiers
-- ran this morning, and stands down for good. THIS SECTION IS FOR ROWS ALREADY WRITTEN.
--
-- V1. THE DERIVED ROWS. Expect GEODNET actual_buyback_usd with source 'derived:tokens*price'
--     only. Scoped to the exact source string so a sourced row can never be swept up.
SELECT date, project, metric, value, source, tier, fetched_at
  FROM metrics
 WHERE project = 'GEODNET'
   AND metric = 'actual_buyback_usd'
   AND source LIKE 'derived:%'
 ORDER BY date;

-- V2. WHAT SURVIVES — the sourced series, which this section never touches. Run before and
--     after V3; this one must not move.
SELECT COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date
  FROM metrics
 WHERE project = 'GEODNET'
   AND metric = 'actual_buyback_usd'
   AND source NOT LIKE 'derived:%';

-- V2b. ** IF V2 RETURNS ZERO ROWS, DO NOT RUN V3. ** That would mean the derived rows are the
--      only ones there, and deleting them empties the column rather than un-blanking it. The
--      answer then is to run tier 4 for GEODNET first and re-check.

-- V3. THE DELETE. Deleted rather than left: they are a second measuring point on a column that
--     has a measurement, and while they sit there the build correctly refuses to show either.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'GEODNET'
--    AND metric = 'actual_buyback_usd'
--    AND source LIKE 'derived:%';
-- COMMIT;

-- V4. VERIFY — V1 returns nothing, and V2's count/dates are unchanged from before V3 ran.

-- ========================================================================================
-- W. ETHEREUM'S DERIVED BURN — IS THE 50-70 ETH/DAY DISAGREEMENT REAL, OR RECENCY?
--    ** ANSWERED 2026-09-23: RECENCY. ** The review row is retired. LOOK ONLY throughout.
-- ========================================================================================
-- THE ANSWER, and it is the one W was built to distinguish. Monthly mean ETH/day, from W1:
--
--   2025-09 110.4   2025-12  19.8   2026-03  28.1   2026-06  57.2   2026-09  32.5
--   2025-10 141.7   2026-01  33.7   2026-04 126.1   2026-07  36.2
--   2025-11  78.1   2026-02  54.4   2026-05  75.6   2026-08  36.8
--
-- ** MAY 2026 AVERAGED 75.6 — ABOVE THE BAND'S OWN CEILING — AND IT HAS DECLINED SINCE. ** So
-- the 50-70 reference was right about the period it described and that period has passed. The
-- late-September ~36/day is recency. config's expect_daily_tokens is now None: REMOVED, not
-- widened, because the series ran 141.7 in October and 19.8 two months later and a band that
-- never fires on that has to span 20-142, which catches nothing.
--
-- W2 CORROBORATES BY A DIFFERENT ROUTE: revenue/fees falls SMOOTHLY from ~0.88 (2021) to ~0.20
-- (2026) as priority fees came to dominate a shrinking base fee. The smoothness is the evidence
-- — a FLAT ratio would mean a hardcoded constant rather than a measurement, which is exactly
-- what caught the seeded fixture. Keep W2 as a methodology sanity series, not as a metric.
--
-- W3 CONFIRMED THE SOURCES ARE REAL: defillama and coingecko throughout, no FIXTURE-prefixed
-- rows among the contributors. That check is why the monthly series can be believed at all.
--
-- ** THE LIMIT, RECORDED SO NOBODY "FIXES" IT: price_usd has 377 rows starting 2025-09-12 while
-- ** fees/revenue run from 2015. ** The derived burn cannot precede 2025-09-12 — every earlier
-- day has a numerator and no denominator. A decade of revenue beside an empty burn column reads
-- as a fetch that failed, and the fix somebody reaches for is the latest price, which is the one
-- thing the derivation refuses. See chain_burn_from_revenue.derived_series_floor in config.
--
-- THE QUERIES FOLLOW UNCHANGED, as the record of what was run.
-- ========================================================================================
-- THE DISAGREEMENT AS STATED: the derived burn came out at ~36 ETH/day against a researched
-- 50-70. But the ~36 was computed from a 30-day revenue TOTAL divided by ONE price snapshot,
-- and the 50-70 is MAY 2026 reporting while the stored window ends in September. Two different
-- periods and a price that moved between them is enough to produce that gap with nothing wrong.
--
-- ** THIS IS THE TEST: per-day prices, monthly medians, across the whole stored window. **
-- A DECLINE from ~50 in May toward ~36 in September explains the gap as recency, and the review
-- row should be retired. FLAT at ~36 back through May means the disagreement is real and the
-- row stays. Do not average the two figures; they are measurements of different months.
--
-- W1. THE MONTHLY MEDIAN OF revenue_usd / price_usd — the implied ETH burned per day. Each day
--     is priced on ITS OWN price, which is the whole point: a single snapshot applied to a
--     month of revenue reports what the burn WOULD have cost today, not what it was.
SELECT substr(r.date, 1, 7)                      AS month,
       COUNT(*)                                  AS days,
       ROUND(AVG(r.value / p.value), 1)          AS mean_eth_per_day,
       ROUND(MIN(r.value / p.value), 1)          AS min_eth,
       ROUND(MAX(r.value / p.value), 1)          AS max_eth,
       ROUND(AVG(p.value), 2)                    AS mean_price
  FROM metrics r
  JOIN metrics p
    ON p.project = r.project AND p.date = r.date AND p.metric = 'price_usd'
 WHERE r.project = 'Ethereum' AND r.metric = 'revenue_usd'
 GROUP BY month
 ORDER BY month;

-- W2. THE REVENUE / FEES RATIO BY MONTH. Ethereum's Revenue is base + blob fees and its Fees
--     adds priority fees, so this ratio SHOULD move — it is the share of fees that is burned.
--     A ratio that is pinned to a constant is a sign the series is not what it claims to be.
SELECT substr(r.date, 1, 7)                      AS month,
       ROUND(AVG(r.value / f.value), 4)          AS mean_revenue_over_fees,
       ROUND(MIN(r.value / f.value), 4)          AS min_ratio,
       ROUND(MAX(r.value / f.value), 4)          AS max_ratio
  FROM metrics r
  JOIN metrics f
    ON f.project = r.project AND f.date = r.date AND f.metric = 'fees_usd'
 WHERE r.project = 'Ethereum' AND r.metric = 'revenue_usd'
 GROUP BY month
 ORDER BY month;

-- W3. AND WHAT THE SOURCES ACTUALLY ARE. Run this first if anything above looks synthetic —
--     a source beginning 'FIXTURE:' means the store is a seeded test fixture and NONE of the
--     numbers above are Ethereum's.
SELECT metric, source, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date
  FROM metrics
 WHERE project = 'Ethereum' AND metric IN ('revenue_usd', 'fees_usd', 'price_usd')
 GROUP BY metric, source
 ORDER BY metric, rows DESC;

-- ========================================================================================
-- X. SKY'S RETIRED governance_burn_balance.               CHECKED EMPTY 2026-09-23 — CLOSED.
--    NOTHING TO RUN. Kept as the record that it was checked, not as work outstanding.
-- ========================================================================================
-- ** X1 RETURNED NO ROWS. ** The decomposed burn read has been 403ing since it was written, so
-- the metric was never produced — which X1 was written to find out rather than assume, and the
-- answer is the one the section flagged as possible: there is nothing to clean up.
--
-- ** DO NOT RUN X3. ** It would delete nothing, and a DELETE executed against an empty set is
-- not harmless here: it leaves a transaction in the log that reads, months later, as evidence
-- that rows once existed and were removed. They never existed.
--
-- THIS SECTION IS CLOSED AND STAYS HERE. Deleting it would mean the next person who notices the
-- retired metric name re-derives the whole question from scratch and re-runs X1 looking for
-- stragglers. The cheapest thing to leave behind is the answer.
--
--   X1  no rows          governance_burn_balance / governance_burn_tokens on Sky
--   X2  not re-run       nothing moved, so there is nothing to compare against
--   X3  NOT RUN, and not to be
--   X4  not applicable
--
-- THE ORIGINAL SECTION FOLLOWS, UNCHANGED, as the record of what was looked for.
-- ========================================================================================
-- WHAT HAPPENED: the burn decomposition split Pause Proxy burns off as "governance" — a one-off
-- executive action, explicitly not to be annualised — and looked for a separate Stage 2 burner.
-- There is not one. Sky's executive of 2026-09-11 burns "SKY from the Pause Proxy balance", so
-- those burns ARE the recurring 5%-of-NPS leg, and the old label had them as the opposite.
--
-- ** READING IS ALREADY SAFE WITHOUT THIS SECTION. ** burn_logs no longer serves
-- governance_burn_balance, so config.withdrawn_contract_keys returns ['burn_logs'] for any such
-- row and the build blanks it. The rows cannot render. This section is about not keeping them.
--
-- AND THEY MAY NOT EXIST AT ALL: the decomposed read has been 403ing since it was written, so
-- the metric may never have been produced. X1 is how you find out — it is not assumed either way.
--
-- X1. THE ROWS, if any. Expect NOTHING if the burn scan has never succeeded.
SELECT date, project, metric, value, source, tier, fetched_at
  FROM metrics
 WHERE project = 'Sky'
   AND metric IN ('governance_burn_balance', 'governance_burn_tokens')
 ORDER BY date;

-- X2. WHAT SURVIVES — the two series that ARE served. Run before and after X3; this must not
--     move. If it is empty too, the scan has never run and X3 has nothing to do.
SELECT metric, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date
  FROM metrics
 WHERE project = 'Sky' AND metric IN ('burn_address_balance', 'other_burn_balance')
 GROUP BY metric;

-- X3. THE DELETE. Only if X1 returned rows. They are not a lower-confidence figure — they are
--     the Stage 2 burn filed under a name that says "do not annualise this", which is the
--     opposite of what it is.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'Sky'
--    AND metric IN ('governance_burn_balance', 'governance_burn_tokens');
-- COMMIT;

-- X4. VERIFY — X1 returns nothing, and X2 is unchanged from before X3 ran.

-- ========================================================================================
-- Y. sETHFI'S ACCRUAL JUMPED 15x — WHICH MECHANISM?
--    LOOK ONLY. No deletes in this section; it exists to answer a question.    2026-09-23
-- ========================================================================================
-- THE OBSERVATION. lock_assets_per_share ran:
--     2026-09-14  1.238612   block 25,982,077
--     2026-09-21  1.239700   +0.0879% over 7 days = 0.0126%/day  (~4.7%/yr)
--     2026-09-23  1.244475   block 26,039,143
--                            +0.3852% over 2 days = 0.1924%/day  (~101.7%/yr)
-- Every step is an INCREASE, so the direction check is silent by design. A rate guard is now
-- declared at 25%/yr (lock_ratio.max_annualised_accrual) and will raise a row on the second
-- window — but a flag says LOOK, it does not say WHY. This section is the why.
--
-- ** FIRST, A CORRECTION TO THE PROPOSED TEST, because it does not separate what it was meant
-- ** to. ** The test as put was: "a batch deposit shows as a step in ETHFI.balanceOf(sETHFI)
-- with sETHFI.totalSupply() flat; organic accrual moves them together."
--
-- In an ERC-4626-shaped vault, organic accrual does NOT move them together — that is precisely
-- why the ratio rises at all. Rewards arriving move ASSETS ALONE and leave SHARES flat, whether
-- they arrive as one batch or as a continuous stream. What moves the two together is USER FLOW:
-- a deposit mints shares at the prevailing ratio and a withdrawal burns them, and both leave the
-- ratio unchanged. So "assets step, shares flat" is the signature of REWARDS-versus-FLOW, not of
-- LUMPY-versus-SMOOTH. Both candidate explanations for the acceleration sit on the rewards side
-- of that line, and this test cannot tell them apart.
--
-- ** WHAT SEPARATES LUMPY FROM SMOOTH IS GRANULARITY, NOT PATTERN. ** A step needs a day on
-- which it happened. With observations only on 09-14, 09-21 and 09-23, each window is ONE
-- interval and an interval has no shape. Y3 is how you find out whether the daily rows exist;
-- if they do not, more observations are the only route and no query here will substitute.
--
-- ** BUT THE DECOMPOSITION IS WORTH RUNNING ANYWAY, because it can catch a THIRD mechanism
-- ** that nobody has listed and that the ratio hides completely. ** If shares fall faster than
-- assets — holders exiting at a discount, an exit fee accruing to those who stay, or a share
-- burn — the ratio RISES with no reward arriving at all. That is indistinguishable from accrual
-- in the ratio and obvious in the two series. It is also the one candidate with a different
-- meaning for the sheet: a rising ratio would then be holders leaving, not rewards compounding.
--
-- READ Y2's `residual` AS: the assets that appeared beyond what the share change accounts for.
--     residual > 0, d_shares ~ 0          rewards arrived. Magnitude is the residual.
--     residual > 0, d_shares strongly < 0 shares left faster than assets — EXIT/FEE/BURN, and
--                                         the ratio rise is not accrual at all.
--     residual ~ 0                        the ratio moved with no reward and no exit, which
--                                         means one of the two readings is wrong. Check the
--                                         sources and blocks before anything else.
--
-- Y1. THE TWO SERIES SIDE BY SIDE, with their sources and blocks. Anything whose source differs
--     between rows is a measuring-point change and must be settled before the deltas mean
--     anything — a Dune daily aggregate against a point-in-time contract read are not the same
--     measurement, and differencing across the join produces a number with no referent.
SELECT date,
       MAX(CASE WHEN metric = 'locked_tokens'            THEN value END) AS shares,
       MAX(CASE WHEN metric = 'locked_tokens_underlying' THEN value END) AS assets,
       MAX(CASE WHEN metric = 'lock_assets_per_share'    THEN value END) AS ratio,
       MAX(CASE WHEN metric = 'locked_tokens'            THEN source END) AS shares_source,
       MAX(CASE WHEN metric = 'locked_tokens_underlying' THEN source END) AS assets_source
  FROM metrics
 WHERE project = 'Ether.fi'
   AND metric IN ('locked_tokens', 'locked_tokens_underlying', 'lock_assets_per_share')
   AND date >= '2026-09-01'
 GROUP BY date
 ORDER BY date;

-- Y2. THE DECOMPOSITION, window by window, WITH THE VERDICT AS A COLUMN.
--
-- ** THE VERDICT IS COMPUTED, NOT LEFT TO THE READER. ** The third mechanism — shares falling
-- faster than assets, so the ratio rises with NO reward arriving — is the one that changes what
-- the sheet MEANS, and it is invisible in the ratio and only implicit in two numeric columns.
-- Asking a reader at 9am to compare d_shares against residual and draw the inference is how a
-- finding that matters gets missed by the person who ran the query.
--
-- `flow_assets` is what the share change accounts for at the PREVIOUS ratio; `residual` is
-- everything else, which is the reward-like inflow. The verdict reads them together:
--
--   EXIT / FEE / SHARE BURN     shares fell and the ratio ROSE. Holders left at a discount, or
--                               an exit fee accrued to those who stayed. ** THE RISING RATIO IS
--                               NOT ACCRUAL, and reading it as a yield is backwards. **
--   REWARDS ARRIVED             shares roughly flat, residual positive. The ordinary case, and
--                               the magnitude is the residual.
--   REWARDS + INFLOW            shares grew AND residual is positive. Both happening; the ratio
--                               move is still the residual's doing.
--   NO REWARD, NO EXIT          residual is ~0 while the ratio moved. One of the two readings
--                               is wrong — check sources and blocks before anything else.
--
-- The 1% band on `shares roughly flat` is a READING AID, not a measurement tolerance: it decides
-- which sentence is printed, never which number is stored. Nothing here writes anything.
WITH s AS (
  SELECT date,
         MAX(CASE WHEN metric = 'locked_tokens'            THEN value END) AS shares,
         MAX(CASE WHEN metric = 'locked_tokens_underlying' THEN value END) AS assets
    FROM metrics
   WHERE project = 'Ether.fi'
     AND metric IN ('locked_tokens', 'locked_tokens_underlying')
     AND date >= '2026-09-01'
   GROUP BY date
), w AS (
  SELECT date, shares, assets,
         LAG(date)   OVER (ORDER BY date) AS prev_date,
         LAG(shares) OVER (ORDER BY date) AS prev_shares,
         LAG(assets) OVER (ORDER BY date) AS prev_assets
    FROM s
   WHERE shares IS NOT NULL AND assets IS NOT NULL
)
SELECT prev_date, date,
       CAST(julianday(date) - julianday(prev_date) AS INT)      AS days,
       shares - prev_shares                                     AS d_shares,
       assets - prev_assets                                     AS d_assets,
       (shares - prev_shares) * (prev_assets / prev_shares)      AS flow_assets,
       (assets - prev_assets)
         - (shares - prev_shares) * (prev_assets / prev_shares)  AS residual,
       ((assets - prev_assets)
         - (shares - prev_shares) * (prev_assets / prev_shares))
         / prev_assets * 100.0                                   AS residual_pct_of_assets,
       (assets / shares) / (prev_assets / prev_shares) - 1.0      AS ratio_change,
       CASE
         WHEN (assets / shares) <= (prev_assets / prev_shares)
           THEN 'RATIO FELL — the direction check owns this, not the rate guard'
         WHEN (shares - prev_shares) / prev_shares < -0.01
           THEN 'EXIT / FEE / SHARE BURN — shares fell '
                || CAST(ROUND((prev_shares - shares) / prev_shares * 100.0, 2) AS TEXT)
                || '% while the ratio ROSE. The rise is NOT accrual: holders left at a discount '
                || 'or an exit fee accrued to those who stayed. Do not read it as a yield.'
         WHEN ABS((assets - prev_assets)
                  - (shares - prev_shares) * (prev_assets / prev_shares))
              / prev_assets < 0.0001
           THEN 'NO REWARD, NO EXIT — the ratio moved with nothing behind it. One of the two '
                || 'readings is wrong. Check sources and blocks before anything else.'
         WHEN ABS((shares - prev_shares) / prev_shares) <= 0.01
           THEN 'REWARDS ARRIVED — shares flat, residual positive. Ordinary accrual, and the '
                || 'magnitude is the residual.'
         ELSE 'REWARDS + INFLOW — shares grew and the residual is positive. Both happened, and '
              || 'the ratio move is the residual''s doing, not the inflow''s.'
       END                                                        AS verdict
  FROM w
 WHERE prev_date IS NOT NULL
 ORDER BY date;

-- Y3. IS THERE DAILY GRANULARITY AT ALL? One row per OBSERVATION DAY across the window. If this
--     returns three rows, the lumpy-versus-smooth question is unanswerable from the store and
--     the only route is more observations — say so rather than reading shape into two points.
SELECT date, COUNT(DISTINCT metric) AS metrics_on_that_day
  FROM metrics
 WHERE project = 'Ether.fi'
   AND metric IN ('locked_tokens', 'locked_tokens_underlying')
   AND date >= '2026-08-15'
 GROUP BY date
 ORDER BY date;

-- Y4. THE SAME DECOMPOSITION FOR PENDLE, which has exactly one point today and will not answer
--     yet. Run it once sPENDLE has two, so the question is not re-derived from scratch there.
SELECT date, metric, value, source
  FROM metrics
 WHERE project = 'Pendle'
   AND metric IN ('locked_tokens', 'locked_tokens_shares', 'lock_assets_per_share')
 ORDER BY metric, date;

-- ========================================================================================
-- Z. PENDLE locked_tokens IS ONE SERIES, NOT THREE — AND ALL OF IT IS SHARES.
--    Z1-Z3 LOOK. Z4 MOVES (re-attribution, not deletion). Z5 VERIFIES.       2026-09-23
-- ========================================================================================
-- ** THE THREE-REGIME READING WAS WRONG, AND ESTABLISHING THAT CAME FIRST. ** The report was:
--     2026-09-11..09-17  34,153,771 .. 34,162,882   ASSETS
--     2026-09-18..09-23  29,995,170 .. 30,310,807   SHARES (a silent switch)
-- with the 09-18 drop of -12.20% attributed to the read changing. It was not. Checked against
-- git rather than inferred from the numbers:
--
--   * NO CONFIG CHANGE. contracts.spendle is byte-identical at f63bfd5 (09-17 13:36) and
--     08aa467 / 7ee15f2 / b56453e (09-18): kind ve_total_supply, read_method erc20_total_supply.
--     It has carried that read_method continuously since 216428e (09-11 16:11) — and before
--     that it was None, which METHOD_REQUIRED_KINDS REFUSES, which is why the series starts on
--     09-11 at all.
--   * NO ADAPTER CHANGE. The escrow-vs-totalSupply dispatch condition in fetch/chain.py is
--     identical either side. b56453e is the only chain.py commit that day and it added
--     metric_override and call_arg, neither of which touches this path.
--   * NOTHING ELSE COULD HAVE WRITTEN IT. dune_queries.locked_tokens has query_id None (never
--     fetched). The only Pendle scrape targets locked_tokens_dashboard, a different metric, and
--     is robots-blocked. No manual override mentions Pendle. No vePENDLE contract exists.
--     Exactly ONE contract mapped to locked_tokens on both dates, and it read totalSupply().
--
-- ** SO THE MEASURING POINT NEVER MOVED, AND EVERY ROW FROM 09-11 TO 09-23 IS THE SHARE COUNT.
-- ** The -12.20% is a change in the measured quantity, not in the reader. The source string
-- being identical across the boundary is a TRUE observation with the opposite meaning to the
-- one drawn from it: it is identical because nothing changed.
--
-- THE ARITHMETIC AGREES. Today's pair is internally consistent to 271 tokens
-- (30,310,807.38 shares x 1.1731 = 35,557,608 against a measured 35,557,337). Read regime 1 as
-- shares and assets fell ~40,076,477 -> 35,557,337, which is -11.3% and matches the -12.2% fall
-- in shares. Read it as assets and the share count RISES 4.1% across a week in which the stored
-- series FELL 12% — the two would have to move in opposite directions through the same event.
--
-- ** SO THE MOVE IS THE WHOLE SERIES, NOT THE SECOND REGIME. ** Moving only 09-18..09-23 would
-- split one homogeneous share series across two metrics AND leave 34,162,882 standing in
-- locked_tokens labelled as assets — a wrong number in the assets column, which is the outcome
-- this whole exercise exists to prevent. locked_tokens then legitimately has NO history: the
-- first post-fix reading is its first observation, so no measuring-point guard fires and there
-- is no hole to declare.
--
-- Z1. THE WHOLE SERIES WITH ITS SOURCES. Every row must read chain:ethereum:spendle. Any row
--     with a different source is NOT covered by the reasoning above and must be looked at
--     before anything moves.
SELECT date, value, source, tier, fetched_at
  FROM metrics
 WHERE project = 'Pendle' AND metric = 'locked_tokens'
 ORDER BY date;

-- Z2. THE SOURCE CENSUS — one row expected. More than one means the premise is wrong.
SELECT source, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date,
       MIN(value) AS min_value, MAX(value) AS max_value
  FROM metrics
 WHERE project = 'Pendle' AND metric = 'locked_tokens'
 GROUP BY source
 ORDER BY rows DESC;

-- Z3. THE COLLISION CHECK. locked_tokens_shares already holds 2026-09-23 (written by the same
--     contract once metric_override was set). The store is keyed (date, project, metric), so a
--     bare UPDATE would violate that key on any date present in BOTH. Expect exactly one row:
--     2026-09-23. Anything else and Z4 must be narrowed before it is run.
SELECT s.date, s.value AS shares_value, l.value AS locked_value, s.source
  FROM metrics s
  JOIN metrics l ON l.date = s.date AND l.project = s.project
 WHERE s.project = 'Pendle' AND s.metric = 'locked_tokens_shares'
   AND l.metric = 'locked_tokens'
 ORDER BY s.date;

-- Z4. THE MOVE. Re-attribution, not deletion — the readings were right and the column was
--     wrong. Colliding dates are DROPPED from the source side rather than overwritten: both
--     rows are the same read of the same contract on the same day, so the one already under
--     locked_tokens_shares is kept and its duplicate discarded. Nothing is lost.
--     Commented out deliberately. Run Z1-Z3 first.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'Pendle' AND metric = 'locked_tokens'
--    AND date IN (SELECT date FROM metrics
--                  WHERE project = 'Pendle' AND metric = 'locked_tokens_shares');
-- UPDATE metrics
--    SET metric = 'locked_tokens_shares'
--  WHERE project = 'Pendle' AND metric = 'locked_tokens';
-- COMMIT;

-- Z5. VERIFY. locked_tokens returns NOTHING (it has no history until the first post-fix run);
--     locked_tokens_shares runs continuously 2026-09-11..2026-09-23 from one source.
SELECT metric, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date,
       COUNT(DISTINCT source) AS sources
  FROM metrics
 WHERE project = 'Pendle' AND metric IN ('locked_tokens', 'locked_tokens_shares')
 GROUP BY metric;

-- Z6. THE INCOMING SANITY CHECK, for the first run after the fix. locked_tokens will be
--     PENDLE.balanceOf(sPENDLE) — the ASSETS — and there is no assets history to compare it
--     against, so the continuity statement is arithmetic rather than a trend:
--
--       expected  ~35,557,337   (= the 2026-09-23 direct read, and = shares x 1.1731 to 271)
--       tolerance  a few tenths of a percent for accrual and flow since that block
--
--     ** IF THE FIRST READING COMES BACK NEAR 30.3M, THE FIX DID NOT TAKE ** — that is the
--     share count, and spendle_underlying is being read as a totalSupply again. If it comes
--     back near 40M, the 09-17 level, something re-derived the old regime and must be traced
--     before the row is trusted. Either way, stop and report rather than accepting the number.
SELECT date, metric, value, source
  FROM metrics
 WHERE project = 'Pendle'
   AND metric IN ('locked_tokens', 'locked_tokens_shares', 'lock_assets_per_share')
 ORDER BY metric, date DESC;

-- ========================================================================================
-- AA. UNISWAP'S `withdrawn` ROW — buyback_fund_balance, ORPHANED BY AN ARCHETYPE CHANGE.
--     AA1-AA2 LOOK. AA3 deletes, scoped. AA4 verifies.                        2026-09-23
-- ========================================================================================
-- ** WHICH METRIC AND WHY, established from config rather than from the sheet. **
-- buyback_fund_balance requires archetype 3. Uniswap's archetypes are now [4] ALONE, because
-- its buyback destination is `burn` — the repurchased UNI is destroyed, so there is no fund to
-- hold a balance. The metric is therefore not applicable to the project.
--
-- TWO CONTRACTS STILL DECLARE THAT KIND: contracts.token_jar and contracts.v3_fee_adapter, both
-- kind buyback_fund_balance. Any row they wrote before the archetype narrowed is still in the
-- store, pointing at a column the project cannot have — which is what renders as `withdrawn`.
--
-- ** THE ORPHAN CHECK DOES NOT SEE THIS CASE, AND THAT IS THE MORE USEFUL FINDING. **
-- config.orphaned_contract_keys asks whether a contract serves the metric a row claims. It does
-- not ask whether the PROJECT can have that metric at all, so a contract that serves a metric
-- its own project has been narrowed out of passes cleanly. An audit across all projects finds
-- exactly four such contracts:
--
--     GEODNET    buyback_wallet_polygon_historical -> buyback_fund_balance
--     Uniswap    token_jar                         -> buyback_fund_balance
--     Uniswap    v3_fee_adapter                    -> buyback_fund_balance
--     Aerodrome  minter                            -> gross_issuance_tokens
--
-- Only Uniswap's are cleaned here. The other two projects are NOT touched: GEODNET's key is
-- named `historical` and may be deliberate retention, and Aerodrome's minter may be suppressed
-- rather than inapplicable. Each needs its own look, and a sweep that assumed they were all the
-- same mistake would be the kind of unilateral cleanup this file exists to prevent.
--
-- AA1. THE ROWS, if any. Expect rows only if a run wrote them before the archetype narrowed.
SELECT date, metric, value, source, tier, fetched_at
  FROM metrics
 WHERE project = 'Uniswap' AND metric = 'buyback_fund_balance'
 ORDER BY date;

-- AA2. WHAT SURVIVES — the burn series that IS the story for an archetype-4 project. Run before
--      and after AA3; this must not move.
SELECT metric, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date
  FROM metrics
 WHERE project = 'Uniswap'
   AND metric IN ('gross_burn_tokens', 'burn_address_balance', 'actual_buyback_tokens')
 GROUP BY metric;

-- AA3. THE DELETE. Only if AA1 returned rows. They are not a lower-confidence figure — they are
--      a balance for a fund that does not exist, because the tokens were destroyed rather than
--      held. There is no metric to MOVE them to: this is not a mislabelling like Pendle's
--      shares, it is a quantity with no referent for this project.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'Uniswap' AND metric = 'buyback_fund_balance';
-- COMMIT;

-- AA4. VERIFY — AA1 returns nothing and AA2 is unchanged.
--      THEN THE CONFIG SIDE, which the delete does not fix and which will re-create the rows:
--      contracts.token_jar and contracts.v3_fee_adapter still declare kind
--      buyback_fund_balance. Leave them if they are wanted as reference contracts (the Fire Pit
--      threshold read lives on one of them), but they must not serve a metric the project
--      cannot have. config.metric_labels and config.non_comparable also still carry
--      buyback_fund_balance entries for Uniswap — dead text describing a column that cannot
--      exist. Neither is deleted here: retiring them is a config decision, not a store cleanup.
SELECT metric, COUNT(*) AS rows
  FROM metrics
 WHERE project = 'Uniswap' AND metric = 'buyback_fund_balance'
 GROUP BY metric;

-- ========================================================================================
-- AB. MORPHO fees_usd — THE DAILY SERIES, WHICH IS THE ONLY THING THAT SETTLES THIS.
--     AB1-AB4 ARE ALL SELECTS. NOTHING HERE WRITES.                           2026-09-23
-- ========================================================================================
-- The 09-23 verdict (an unbackfilled hole inside the 7-day lookback) made a one-step
-- prediction for 09-24 and the prediction FAILED: fees_usd reads $13,302,020.60, roughly
-- 22x the pre-break level rather than a return to it. A hole cannot push a median ABOVE
-- the healthy level, so that explanation is ruled out.
--
-- ** DO NOT WRITE A FIX BEFORE RUNNING THESE. ** Three causes are still live — a cumulative
-- series, a one-day catch-up dump, and a second restructure — and they need different
-- handling. Two of the three would be made worse by guessing.

-- AB1. THE LAST 10 DAYS, PLAINLY. This is the headline: is $13.3m one point or a level?
SELECT date, value, source, tier, fetched_at
  FROM metrics
 WHERE project = 'Morpho' AND metric = 'fees_usd'
 ORDER BY date DESC
 LIMIT 10;

-- AB2. THE SHAPE EITHER SIDE OF THE BREAK, to see whether the series is a level or a spike
--      and whether the 09-12..09-20 hole is still absent.
--      EXPECTED IF IT IS AN AGGREGATE IN A DAILY COLUMN: exactly one outsized point, with
--      the days around it at Blue's ordinary ~$500-650k, and the hole still empty.
--      EXPECTED IF IT IS A SECOND RESTRUCTURE: a sustained shift, not one point.
SELECT date, value, source
  FROM metrics
 WHERE project = 'Morpho' AND metric = 'fees_usd'
   AND date >= date('2026-09-05')
 ORDER BY date;

-- AB3. ** IS IT CUMULATIVE? ** The decisive test, and it needs no judgement: a cumulative
--      series never decreases. If prev_value is never above value across the recovered
--      span, _chart is returning a running total and the daily column is wrong from the
--      recovery date onward — not just on the newest point.
SELECT date, value,
       LAG(value) OVER (ORDER BY date) AS prev_value,
       value - LAG(value) OVER (ORDER BY date) AS step,
       CASE WHEN value < LAG(value) OVER (ORDER BY date) THEN 'DECREASES -> not cumulative'
            ELSE 'non-decreasing' END AS verdict
  FROM metrics
 WHERE project = 'Morpho' AND metric = 'fees_usd'
   AND date >= date('2026-09-21')
 ORDER BY date;

-- AB4. DUPLICATE DATES. The recovery branch does by_date[day] += v, keyed on .date(), so two
--      points stamped on one UTC day are summed silently — and that branch had never run
--      against real data before this week. The store upserts on (date, project, metric), so
--      a duplicate will NOT show here as two rows; this checks the store is not itself the
--      place the doubling happened, which would point the investigation the other way.
SELECT date, COUNT(*) AS rows
  FROM metrics
 WHERE project = 'Morpho' AND metric = 'fees_usd'
 GROUP BY date
HAVING COUNT(*) > 1;

-- ========================================================================================
-- AC. MORPHO utilisation_pct / supply_units — THE DefiLlama ROWS THAT PIN measuring_point_changed.
--     AC1-AC2 LOOK. AC3 is the proposed delete, commented out.                  2026-09-23
-- ========================================================================================
-- utilisation_pct renders n/a with status measuring_point_changed and source
-- morpho_api:markets. The flag is CORRECT and it is STUCK: the stored series carries rows from
-- two sources — DefiLlama's borrowed/(tvl+borrowed) (0.3231, collateral in the denominator)
-- and morpho_api's sum(borrow)/sum(supply) over listed markets (0.8802) — and case 5 in
-- build_workbook blanks a series read from two places unless a handover is declared.
--
-- ** IT IS NOT A HANDOVER, SO NONE IS DECLARED. ** A handover says two legs are ONE series
-- measured at two points in time. These are two different quantities: the DefiLlama figure
-- has borrower collateral in its denominator and the morpho_api figure does not. Stitching
-- them and calling the result continuous would be the same mistake as summing the two
-- Pendle regimes. The store upserts and never deletes, so this does not clear by itself.
--
-- ** WHAT THE ROWS ARE. ** Not wrong readings of utilisation — correct readings of a
-- different ratio, stored under this column's name before the exact route existed. The
-- caveat that described them (config.is_non_comparable, "THE DENOMINATOR INCLUDES
-- COLLATERAL") still travels with any row sourced from defillama, so they were never
-- displayed as clean; they are simply no longer the figure this column carries.

-- AC1. THE ROWS. Expect utilisation_pct only — DefiLlama never stored supply_units for
--      Morpho (the adapter refused that pair from the start); the supply_units clause is
--      here so a row that did land is seen rather than assumed absent.
SELECT date, metric, value, source, tier, fetched_at
  FROM metrics
 WHERE project = 'Morpho'
   AND metric IN ('utilisation_pct', 'supply_units')
   AND source LIKE 'defillama%'
 ORDER BY metric, date;

-- AC2. WHAT SURVIVES — the morpho_api rows, which this section never touches. Run before
--      and after; this must not move. (supply_units will be EMPTY until the run after the
--      per-project sanity bound landed on 2026-09-23 — it was rejected at validation, not
--      stored and then lost.)
SELECT metric, COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date,
       MIN(value) AS min_value, MAX(value) AS max_value
  FROM metrics
 WHERE project = 'Morpho'
   AND metric IN ('utilisation_pct', 'supply_units')
   AND source LIKE 'morpho_api%'
 GROUP BY metric;

-- AC3. THE PROPOSED DELETE. Delete rather than move: there is no metric these rows are a
--      correct reading OF that this tool carries — "utilisation with collateral in the
--      denominator" is not a column, and inventing one to keep a known-biased series would
--      be keeping a number for its own sake. The DefiLlama route itself stays on record in
--      config (non_comparable, utilisation_pct_blocked) so what was measured, and why it was
--      replaced, is not lost with the rows.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'Morpho'
--    AND metric IN ('utilisation_pct', 'supply_units')
--    AND source LIKE 'defillama%';
-- COMMIT;

-- AC4. VERIFY — AC1 returns nothing, AC2 is unchanged. On the next build utilisation_pct's
--      status changes from measuring_point_changed to ok with source morpho_api:markets;
--      if it does not, a THIRD source is in the series and AC1's filter needs widening —
--      look before widening it.
SELECT DISTINCT source
  FROM metrics
 WHERE project = 'Morpho' AND metric = 'utilisation_pct';

-- ========================================================================================
-- AD. GEODNET buyback_wallet_polygon_historical — RETIRED 2026-09-23; ANY ROWS IT WROTE.
--     AD1 LOOKS. AD2 is the proposed delete, commented out.                    2026-09-23
-- ========================================================================================
-- The contract entry is gone from config (kept as retired_contracts on the GEODNET entry).
-- It was kind buyback_fund_balance on a project whose buyback BURNS, so the metric it served
-- is not one GEODNET can have — the same shape section AA found on Uniswap — and it was
-- model-knowledge with no GEODNET-authored source and no reference from the burn query.
-- The read was refused as unverified from the start, so AD1 is EXPECTED EMPTY; it is here so
-- that is seen rather than assumed, and because any row that did land is now orphaned.

-- ** RUN BY JAKE 2026-09-24: AD1 RETURNED NO ROWS. ** The retired wallet never wrote anything,
-- so there is nothing to clean and AD2 is not to be run. Kept as the record that it was checked.
-- (AD is GEODNET's retired wallet. Ether.fi's Dune 8683038 retirement is section AF.)

-- AD1. THE ROWS, if any. Expect none: the read was refused while the entry existed.
SELECT date, metric, value, source, tier, fetched_at
  FROM metrics
 WHERE project = 'GEODNET'
   AND source LIKE '%buyback_wallet_polygon_historical%'
 ORDER BY date;

-- AD2. THE PROPOSED DELETE. Only if AD1 returned rows.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'GEODNET'
--    AND source LIKE '%buyback_wallet_polygon_historical%';
-- COMMIT;

-- ========================================================================================
-- AE. GEODNET gross_burn_tokens — FIND THE ZEROS AND SAY WHICH KIND. READ-ONLY.  2026-09-24
--     AE1-AE3 LOOK. There is no delete: a zero is evidence, not debris.
-- ========================================================================================
-- ** THE FIRST VERSION OF THIS SECTION COULD NOT ANSWER ITS OWN QUESTION. ** Its AE1 filtered
-- value > 0, so "no zeros in the window" was the filter, not the data. What it DID show refuted
-- the premise it was written for: seven consecutive readings 09-14..09-23, each exactly 35,000 x
-- days_since_prev (Jake, 2026-09-24). GEODNET burns ~35,000 GEOD per DAY; the "weekly burn read
-- daily" explanation was wrong and is corrected in LUMPY_FLOWS. On a daily burn a zero is an
-- ANOMALY, and these three queries say which one.

-- AE1. THE WHOLE LIVE SERIES, ZEROS INCLUDED. per_day should sit at ~35,000 on every row; a zero
--      shows per_day 0, and next_value / days_to_next say whether the following read caught up.
WITH s AS (
  SELECT date, value, fetched_at,
         CAST(julianday(date) - julianday(LAG(date) OVER (ORDER BY date)) AS INTEGER) AS days_since_prev,
         LEAD(value) OVER (ORDER BY date) AS next_value,
         CAST(julianday(LEAD(date) OVER (ORDER BY date)) - julianday(date) AS INTEGER) AS days_to_next
    FROM metrics
   WHERE project = 'GEODNET' AND metric = 'gross_burn_tokens' AND source LIKE 'chain:polygon%'
)
SELECT date, value, days_since_prev,
       ROUND(value / NULLIF(days_since_prev, 0)) AS per_day,
       next_value, days_to_next, fetched_at
  FROM s
 ORDER BY date;

-- AE2. EACH ZERO AGAINST THE TWO BALANCE READS IT WAS DIFFERENCED FROM. A zero is only ever
--      stored from two SUCCESSFUL reads that returned the same balance, so it is never a missing
--      read. What separates the three kinds is the wall-clock gap between those reads and whether
--      the next reading caught up:
--        hours_between_reads < 24, next reading ~70,000/day-1  -> TIMING: two reads inside one
--                                                                burn interval. Not an anomaly.
--        hours_between_reads >= 24, next reading catches up   -> THE READ DID NOT ADVANCE: a
--                                                                stale balance (lagging node).
--        no catch-up (next reading ~35,000 x its own days)   -> THE BURN DID NOT HAPPEN that day.
WITH b AS (
  SELECT date, value, fetched_at,
         LAG(date)       OVER (ORDER BY date) AS prev_date,
         LAG(value)      OVER (ORDER BY date) AS prev_balance,
         LAG(fetched_at) OVER (ORDER BY date) AS prev_fetched_at
    FROM metrics
   WHERE project = 'GEODNET' AND metric = 'burn_address_balance' AND source LIKE 'chain:polygon%'
),
z AS (
  SELECT date FROM metrics
   WHERE project = 'GEODNET' AND metric = 'gross_burn_tokens'
     AND source LIKE 'chain:polygon%' AND value = 0
)
SELECT z.date AS zero_date, b.prev_date, b.prev_balance, b.value AS balance,
       b.prev_fetched_at, b.fetched_at,
       ROUND((julianday(b.fetched_at) - julianday(b.prev_fetched_at)) * 24, 1) AS hours_between_reads
  FROM z JOIN b ON b.date = z.date
 ORDER BY z.date;

-- AE3. EVERY RUN THAT RAISED THE DECISION ROW. The row is not stored in config: the chain read
--      raises it on any run whose differenced value is 0, so this is its complete history. If the
--      newest run_id here is the LATEST run, the zero is current and the row is live; if not, it
--      is historical and the latest report does not carry it.
SELECT run_id, ts, metric
  FROM gap_report
 WHERE project = 'GEODNET' AND metric LIKE '[data] gross_burn_tokens is ZERO%'
 ORDER BY ts;
SELECT run_id, ts, date, value, prior_value, action
  FROM review_queue
 WHERE project = 'GEODNET' AND metric = 'gross_burn_tokens' AND reason = 'unattributable_zero'
 ORDER BY ts;

-- ========================================================================================
-- AF. Ether.fi — ROWS FROM THE RETIRED DUNE QUERY 8683038. (Part C1 of the 2026-09-24 round.)
--     AF1 LOOKS. AF2 is the proposed delete, commented out.                    2026-09-24
-- ========================================================================================
-- Dune 8683038 is retired for Ether.fi (HTTP 402 on all three metrics, and its staked_supply
-- exceeded what the staking contract holds). Its three metrics are CLOSED in config, but a
-- closure does not hide rows already stored: until these go, locked_tokens_dashboard,
-- lock_rate_pct and staker_count keep rendering the last Dune values as if current.

-- AF1. THE ROWS. Expect the three metrics, daily, up to the last successful pull.
SELECT metric, COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last,
       MIN(value) AS min_value, MAX(value) AS max_value
  FROM metrics
 WHERE project = 'Ether.fi'
   AND source LIKE 'dune:8683038%'
 GROUP BY metric
 ORDER BY metric;

-- AF2. THE PROPOSED DELETE. Only after reading AF1 — and note the history is not recoverable
--      once the query is gone: this is a decision that the retired figure should not render,
--      not a cleanup of noise.
-- BEGIN;
-- DELETE FROM metrics
--  WHERE project = 'Ether.fi'
--    AND source LIKE 'dune:8683038%';
-- COMMIT;

