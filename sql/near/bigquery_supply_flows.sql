-- NEAR's daily BURN and ISSUANCE from block-header total_supply (overnight 2026-10-06, B1).
-- nearcore: total_supply = prev + minted (an epoch's FIRST block only) - burn
--   core/primitives/src/block.rs:195-199 @6bc06092; minted only when is_next_block_epoch_start
--   (chain/client/src/client.rs:1112-1116). So inside an epoch the supply only falls (the burn), and the
--   first block of an epoch rises by (minted - that block's burn).
-- The table has no epoch_id (near/near-public-lakehouse@76e0b2cd BQ Writer Stream.py:37-52): an epoch start
-- is a block whose supply ROSE — a mint is tens of thousands of NEAR, one block's burn is tiny. No column gives
-- one block's burn, so the burn of each epoch-first block is filled with that day's mean burn per block
-- (about 1 block in 43,000), and both raw parts are returned beside the totals.
-- Reads 3 columns (block_date, block_height, total_supply: FLOAT64 yoctoNEAR), partition-pruned on block_date.
-- Parameters: @d0, @d1 DATE — days [@d0, @d1] inclusive.
-- CTE NAMES NEVER EQUAL A COLUMN NAME (Jake's run 2026-10-07: "ORDER BY does not support ..."): the per-day CTE was
-- called `day` and also had a column `day`, so the final ORDER BY day resolved to the TABLE alias — a STRUCT of the
-- whole row — which BigQuery cannot order. The CTE is `per_day` now (tests check every sql/near file for this).
WITH b AS (
  SELECT block_height, ANY_VALUE(block_date) AS block_date, ANY_VALUE(total_supply) AS total_supply
    FROM `bigquery-public-data.crypto_near_mainnet_us.blocks`
   WHERE block_date >= DATE_SUB(@d0, INTERVAL 1 DAY) AND block_date <= @d1
   GROUP BY block_height                                   -- guards against duplicate streamed rows
),
d AS (
  SELECT block_date,
         (total_supply - LAG(total_supply) OVER (ORDER BY block_height)) / 1e24 AS delta_near
    FROM b
),
per_day AS (
  SELECT block_date AS day,
         COUNT(*) AS n_blocks,
         COUNTIF(delta_near > 0) AS n_epoch_starts,
         SUM(IF(delta_near > 0, 0, -delta_near)) AS burn_ex_boundary_near,
         SUM(IF(delta_near > 0, delta_near, 0)) AS net_mint_near,
         SUM(delta_near) AS supply_change_near
    FROM d
   WHERE block_date >= @d0 AND delta_near IS NOT NULL
   GROUP BY day
)
SELECT day, n_blocks, n_epoch_starts,
       burn_ex_boundary_near
         + n_epoch_starts * IFNULL(SAFE_DIVIDE(burn_ex_boundary_near, n_blocks - n_epoch_starts), 0) AS burn_near,
       net_mint_near
         + n_epoch_starts * IFNULL(SAFE_DIVIDE(burn_ex_boundary_near, n_blocks - n_epoch_starts), 0) AS issuance_near,
       burn_ex_boundary_near, net_mint_near, supply_change_near
  FROM per_day
 ORDER BY day
