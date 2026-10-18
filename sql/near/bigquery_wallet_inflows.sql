-- ALL-TIME INFLOW of the three NEAR Intents revenue wallets (Round G 2.6 — the references fefundsadmin /
-- buybacks multisig / 1csfundsadmin _alltime_inflow, revenue.near.org, 2026-10-13). Fee inflows reach these
-- accounts as RECEIPTS (intents.near's withdrawals), not as transactions the account appears in, so NearBlocks'
-- account txns cannot sum them (fefundsadmin's newest "transaction" there is 2026-03-27 while it received fees
-- daily) — receipt_actions can.
--   native NEAR  receipt_actions TRANSFER to the wallet, deposit from args {"deposit": "<yocto>"}; refunds
--                (predecessor `system`) and self-transfers out (same exclusions as bigquery_p2p_daily.sql). A
--                transfer that failed is refunded and is not excluded here (no join to execution_outcomes) — rare.
--   wNEAR        ft_events of wrap.near with the wallet as affected account and a positive delta (received).
-- From @d0 (the oldest wallet's creation day; NearBlocks: fefundsadmin created 2025-09-22) to @d1 inclusive.
-- STAGE 2 (G2 addendum A6: the one-stage query dry-ran at 2,001.57 GB): the native leg reads receipt_actions only on
-- @days, the days stage 1 (bigquery_wallet_inflow_days.sql) found a TRANSFER to one of the three — receipt_actions is
-- not clustered, so only partitions prune its `args` column. The wNEAR leg prunes on ft_events' clustering
-- (contract_account_id = 'wrap.near') over @d0..@d1. Parameters: @d0 DATE, @d1 DATE, @days ARRAY<DATE>.
-- Output: account_id, token ('NEAR' native | 'wrap.near'), amount_near (string), n, first_day, last_day.
-- DRY-RUN FIRST (check_offline_items near_settlement_routes prints both stages' bytes).
WITH native AS (
  SELECT receipt_receiver_account_id AS account_id, 'NEAR' AS token,
         SUM(SAFE_CAST(JSON_VALUE(args, '$.deposit') AS BIGNUMERIC)) / 1e24 AS amount_near,
         COUNT(*) AS n, MIN(block_date) AS first_day, MAX(block_date) AS last_day
    FROM `bigquery-public-data.crypto_near_mainnet_us.receipt_actions`
   WHERE block_date IN UNNEST(@days)
     AND action_kind = 'TRANSFER'
     AND receipt_receiver_account_id IN ('fefundsadmin.sputnik-dao.near', 'buybacks.multisignature.near',
                                         '1csfundsadmin.sputnik-dao.near')
     AND receipt_predecessor_account_id != 'system'
     AND receipt_predecessor_account_id != receipt_receiver_account_id
   GROUP BY 1, 2
),
wnear AS (
  SELECT affected_account_id AS account_id, 'wrap.near' AS token,
         SUM(SAFE_CAST(delta_amount AS BIGNUMERIC)) / 1e24 AS amount_near,
         COUNT(*) AS n, MIN(block_date) AS first_day, MAX(block_date) AS last_day
    FROM `bigquery-public-data.crypto_near_mainnet_us.ft_events`
   WHERE block_date BETWEEN @d0 AND @d1
     AND contract_account_id = 'wrap.near'
     AND affected_account_id IN ('fefundsadmin.sputnik-dao.near', 'buybacks.multisignature.near',
                                 '1csfundsadmin.sputnik-dao.near')
     AND SAFE_CAST(delta_amount AS BIGNUMERIC) > 0
   GROUP BY 1, 2
)
SELECT account_id, token, CAST(amount_near AS STRING) AS amount_near, n, first_day, last_day FROM native
UNION ALL
SELECT account_id, token, CAST(amount_near AS STRING) AS amount_near, n, first_day, last_day FROM wnear
ORDER BY account_id, token
