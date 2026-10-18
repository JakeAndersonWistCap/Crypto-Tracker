-- STAGE 1 OF THE THREE REVENUE WALLETS' ALL-TIME INFLOW (G2 addendum A6, Jake's dry run 2026-10-18: the one-stage query
-- read 2,001.57 GB, over the 400 GB per-query cap). receipt_actions is day-partitioned on block_date and NOT clustered
-- (sql/near/bigquery_metadata.sql), so naming three accounts prunes nothing: the native leg read the whole `args`
-- column — every TRANSFER's JSON — on every day since 2025-09-22. This stage reads only narrow columns (block_date,
-- action_kind, receipt_receiver_account_id, receipt_predecessor_account_id) and returns the DAYS on which any of the
-- three received a native TRANSFER; stage 2 (bigquery_wallet_inflows.sql) reads `args` on those partitions only.
-- Same filters as stage 2. Parameters: @d0 DATE, @d1 DATE. Output: day, account_id, n.
SELECT block_date AS day, receipt_receiver_account_id AS account_id, COUNT(*) AS n
  FROM `bigquery-public-data.crypto_near_mainnet_us.receipt_actions`
 WHERE block_date BETWEEN @d0 AND @d1
   AND action_kind = 'TRANSFER'
   AND receipt_receiver_account_id IN ('fefundsadmin.sputnik-dao.near', 'buybacks.multisignature.near',
                                       '1csfundsadmin.sputnik-dao.near')
   AND receipt_predecessor_account_id != 'system'
   AND receipt_predecessor_account_id != receipt_receiver_account_id
 GROUP BY 1, 2
 ORDER BY 1, 2
