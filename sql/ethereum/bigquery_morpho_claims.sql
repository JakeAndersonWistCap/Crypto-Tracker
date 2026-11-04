-- MORPHO CLAIMS ON ETHEREUM, ONE ROW PER EVENT (P5-1C.C5, Jake 2026-11-04) - read by morpho_claims.py, which DRY-RUNS
-- this by default (bytes only, no data read, no quota) and runs it only with --apply, under the shared monthly budget.
-- The two distributors' own events in the window:
--   Claimed(address indexed user, address indexed token, uint256 amount)   Merkl's Distributor and the Morpho URD alike
--   RootSet(bytes32 indexed newRoot, bytes32 indexed newIpfsHash)          the URD committing a new rewards tree
-- A claim pays what a root committed, so a URD claim is dated no later than the URD's last RootSet before it.
-- Parameters: @d0 DATE, @d1 DATE, @distributors ARRAY<STRING> (lower-case), @topics ARRAY<STRING> (topic0s, lower-case),
-- @token_topics ARRAY<STRING> (the reward token, left-padded to 32 bytes: a Claimed row is kept only for that token).
-- Output: day, block_number, transaction_hash, log_index, distributor, topic0, topic1, topic2, data.
SELECT DATE(block_timestamp) AS day,
       block_number,
       transaction_hash,
       log_index,
       LOWER(address) AS distributor,
       LOWER(topics[SAFE_OFFSET(0)]) AS topic0,
       LOWER(topics[SAFE_OFFSET(1)]) AS topic1,
       LOWER(topics[SAFE_OFFSET(2)]) AS topic2,
       data
  FROM `bigquery-public-data.crypto_ethereum.logs`
 WHERE block_timestamp >= TIMESTAMP(@d0)
   AND block_timestamp < TIMESTAMP(DATE_ADD(@d1, INTERVAL 1 DAY))
   AND LOWER(address) IN UNNEST(@distributors)
   AND LOWER(topics[SAFE_OFFSET(0)]) IN UNNEST(@topics)
   AND (LOWER(topics[SAFE_OFFSET(0)]) != @topics[OFFSET(0)] OR LOWER(topics[SAFE_OFFSET(2)]) IN UNNEST(@token_topics))
 ORDER BY block_number, log_index
