-- NEAR settlement route (a), ONLY IF bigquery_freshness.sql shows current data.
-- Artemis's P2P definition adapted to NEAR's named accounts: a transfer counts only when NEITHER
-- side is a contract. A CONTRACT here = an account that has ever received a DEPLOY_CONTRACT action
-- (it has code). Artemis's own NEAR rule is stricter on the sender ("an EOA is a transaction signer
-- that never receives a function call", Artemis dbt macros/wallets/distinct_eoa_addresses.sql:21-26)
-- — swap the `contracts` CTE for  action_kind = 'FUNCTION_CALL'  to apply it exactly.
-- Output: per UTC day, native NEAR moved P2P (refunds from `system` excluded), and per token the RAW
-- nep141 amount moved P2P. Prices and decimals are applied OUTSIDE BigQuery (the dataset has
-- neither): native NEAR at the same-day price in our store; tokens from the token list the output
-- names (the top tokens by value, enough to cover >95% of it).
-- BYTES: dry-run first (the console shows "This query will process N GB"); the full-history
-- contracts scan reads 2 columns of receipt_actions, the rest one year of partitions (block_date).
-- Free tier: 1 TB/month. Schema: near/near-public-lakehouse@76e0b2cd BQ Writer Stream.py.
DECLARE d0 DATE DEFAULT DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY);
DECLARE d1 DATE DEFAULT DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY);

CREATE TEMP TABLE contracts AS
SELECT DISTINCT receipt_receiver_account_id AS account_id
FROM `bigquery-public-data.crypto_near_mainnet_us.receipt_actions`
WHERE action_kind = 'DEPLOY_CONTRACT';

-- native NEAR, P2P, per day (yoctoNEAR / 1e24)
SELECT ra.block_date AS day, 'NEAR' AS token,
       SUM(SAFE_CAST(JSON_VALUE(ra.args, '$.deposit') AS BIGNUMERIC) / 1e24) AS amount, COUNT(*) AS n
FROM `bigquery-public-data.crypto_near_mainnet_us.receipt_actions` ra
JOIN `bigquery-public-data.crypto_near_mainnet_us.execution_outcomes` eo
  ON eo.receipt_id = ra.receipt_id AND eo.block_date BETWEEN d0 AND d1
LEFT JOIN contracts c1 ON c1.account_id = ra.receipt_predecessor_account_id
LEFT JOIN contracts c2 ON c2.account_id = ra.receipt_receiver_account_id
WHERE ra.block_date BETWEEN d0 AND d1 AND ra.action_kind = 'TRANSFER'
  AND ra.receipt_predecessor_account_id <> 'system'
  AND ra.receipt_predecessor_account_id <> ra.receipt_receiver_account_id
  AND c1.account_id IS NULL AND c2.account_id IS NULL
  AND eo.status NOT LIKE '%FAILURE%'                       -- status text format: unverified
GROUP BY 1, 2

UNION ALL

-- nep141 ft_transfer, P2P, per day and token (RAW units — divide by the token's decimals)
SELECT eo.block_date AS day, eo.executor_account_id AS token,
       SUM(SAFE_CAST(JSON_VALUE(d, '$.amount') AS BIGNUMERIC)) AS amount, COUNT(*) AS n
FROM `bigquery-public-data.crypto_near_mainnet_us.execution_outcomes` eo,
     UNNEST(eo.logs) AS log,
     UNNEST(JSON_QUERY_ARRAY(SAFE.PARSE_JSON(SUBSTR(log, 12)), '$.data')) AS d
LEFT JOIN contracts c1 ON c1.account_id = JSON_VALUE(d, '$.old_owner_id')
LEFT JOIN contracts c2 ON c2.account_id = JSON_VALUE(d, '$.new_owner_id')
WHERE eo.block_date BETWEEN d0 AND d1 AND STARTS_WITH(log, 'EVENT_JSON:')
  AND JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(log, 12)), '$.standard') = 'nep141'
  AND JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(log, 12)), '$.event') = 'ft_transfer'
  AND eo.status NOT LIKE '%FAILURE%'
  AND c1.account_id IS NULL AND c2.account_id IS NULL
GROUP BY 1, 2
ORDER BY day, token;
-- Export as CSV into data/near/ (never committed). Delivery: run monthly as a saved query, OR put a
-- service-account key in .env (GOOGLE_APPLICATION_CREDENTIALS) and the probe runs it.
