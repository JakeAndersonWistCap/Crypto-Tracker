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
DELETE FROM metrics
 WHERE project = 'Maple'
   AND metric  = 'treasury_holding_tokens';

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
