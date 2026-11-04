-- ETHEREUM STAKING FLOWS PER UTC DAY — the BigQuery rebuild of staked ETH (SPEC P5-1.12, Jake 2026-11-04).
-- Read by staked_eth_rebuild.py only (dry run first, then capped at the dry run; NEAR's quota project and ledger).
--   deposits     every DepositEvent log of the beacon deposit contract 0x00000000219ab540356cBB839Cbe05303d7705Fa
--                (topic0 = keccak("DepositEvent(bytes,bytes,bytes,bytes,bytes)")). EIP-6110: these logs are exactly
--                what the consensus layer parses into deposit requests. The event's data is five ABI `bytes`; the
--                amount is the third, an 8-byte LITTLE-ENDIAN gwei integer at byte 352 (head 5 x 32, pubkey 32 + 64,
--                withdrawal_credentials 32 + 32, the amount's length word at byte 320, which must read 8: counted in
--                bad_layout, and any row with a bad layout refuses the rebuild).
--   withdrawals  every EIP-4895 withdrawal in blocks.withdrawals (amount in gwei): reward skims and full exits.
--   blocks       the count per day, and the newest block time — the rebuild refuses a dataset that ends before the
--                anchor (a day not fully read is never taken as zero flow).
-- Parameters: @d0 DATE (the first day), @anchor TIMESTAMP (the beacon-node read the rebuild is anchored on).
-- Output: one row per UTC day from @d0 to the anchor's day (the last one cut at @anchor): day, deposits_gwei,
--   n_deposits, bad_layout, withdrawals_gwei, n_withdrawals, n_blocks, last_block_ts.
WITH blk AS (
  SELECT DATE(b.timestamp) AS day, COUNT(*) AS n_blocks, MAX(b.timestamp) AS last_block_ts,
         SUM((SELECT SUM(CAST(w.amount AS BIGNUMERIC)) FROM UNNEST(b.withdrawals) w)) AS withdrawals_gwei,
         SUM(ARRAY_LENGTH(b.withdrawals)) AS n_withdrawals
    FROM `bigquery-public-data.crypto_ethereum.blocks` b
   WHERE b.timestamp >= TIMESTAMP(@d0) AND b.timestamp < @anchor
   GROUP BY 1
),
dep AS (
  SELECT DATE(l.block_timestamp) AS day,
         SUM(CAST(CONCAT('0x', TO_HEX(REVERSE(FROM_HEX(SUBSTR(l.data, 707, 16))))) AS BIGNUMERIC)) AS deposits_gwei,
         COUNT(*) AS n_deposits,
         COUNTIF(SUBSTR(l.data, 643, 64) != LPAD('8', 64, '0')) AS bad_layout
    FROM `bigquery-public-data.crypto_ethereum.logs` l
   WHERE l.block_timestamp >= TIMESTAMP(@d0) AND l.block_timestamp < @anchor
     AND LOWER(l.address) = '0x00000000219ab540356cbb839cbe05303d7705fa'
     AND l.topics[SAFE_OFFSET(0)] = '0x649bbc62d0e31342afea4e5cd82d4049e7e1ee912fc0889aa790803be39038c5'
   GROUP BY 1
)
SELECT blk.day,
       CAST(COALESCE(dep.deposits_gwei, 0) AS STRING) AS deposits_gwei,
       COALESCE(dep.n_deposits, 0) AS n_deposits,
       COALESCE(dep.bad_layout, 0) AS bad_layout,
       CAST(COALESCE(blk.withdrawals_gwei, 0) AS STRING) AS withdrawals_gwei,
       COALESCE(blk.n_withdrawals, 0) AS n_withdrawals,
       blk.n_blocks,
       blk.last_block_ts
  FROM blk LEFT JOIN dep USING (day)
 ORDER BY blk.day
