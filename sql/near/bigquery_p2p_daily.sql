-- NEAR SETTLEMENT VOLUME, P2P LEG — "Artemis method (adapted to NEAR), UNVALIDATED".
-- Artemis counts a transfer as P2P only when NEITHER side is a contract. NEAR has named accounts,
-- not EOAs, so THE RULE USED HERE: an account is a CONTRACT ON A DAY if, that same UTC day, it
-- received a FUNCTION_CALL or DEPLOY_CONTRACT action — a plain account has no code to call, and a
-- contract can only send tokens while executing a call it received. EXCEPT eth-implicit accounts
-- (0x + 40 hex): those are users' wallet contracts (NEP-518) and count as USERS. Per day, so a
-- chunk's answer never depends on how the year was chunked. Known edges: a contract that receives
-- a plain ft_transfer or NEAR on a day nobody called it counts as a user (overstates); a user who
-- is sent ft_transfer_call that day counts as a contract (understates).
--   native NEAR  receipt_actions TRANSFER, deposit from args {"deposit": "<yocto>"}; refunds
--                (predecessor `system`) and self-transfers out. A transfer to a missing account
--                fails and is refunded; the failure is not excluded (no join to execution_outcomes,
--                which would add its bytes) — rare.
--   NEP-141      ft_events cause 'ft_transfer', the RECEIVER's row (delta not negative): affected =
--                receiver, involved = sender; failures are already excluded upstream. Only @tokens
--                (the census's >95%-of-value set) — clustering prunes the rest. RAW amounts: the
--                adapter applies decimals and prices.
-- Parameters: @d0 DATE, @d1 DATE, @tokens ARRAY<STRING>. Output: day, token ('NEAR' = native, in
-- NEAR), amount (string), n.
WITH contracts AS (
  SELECT DISTINCT block_date AS day, receipt_receiver_account_id AS account_id
    FROM `bigquery-public-data.crypto_near_mainnet_us.receipt_actions`
   WHERE block_date BETWEEN @d0 AND @d1
     AND action_kind IN ('FUNCTION_CALL', 'DEPLOY_CONTRACT')
     AND NOT REGEXP_CONTAINS(receipt_receiver_account_id, r'^0x[0-9a-f]{40}$')
),
native AS (
  SELECT ra.block_date AS day, 'NEAR' AS token,
         CAST(SUM(SAFE_CAST(JSON_VALUE(ra.args, '$.deposit') AS BIGNUMERIC)) / 1e24 AS STRING) AS amount,
         COUNT(*) AS n
    FROM `bigquery-public-data.crypto_near_mainnet_us.receipt_actions` ra
    LEFT JOIN contracts c1 ON c1.day = ra.block_date AND c1.account_id = ra.receipt_predecessor_account_id
    LEFT JOIN contracts c2 ON c2.day = ra.block_date AND c2.account_id = ra.receipt_receiver_account_id
   WHERE ra.block_date BETWEEN @d0 AND @d1
     AND ra.action_kind = 'TRANSFER'
     AND ra.receipt_predecessor_account_id != 'system'
     AND ra.receipt_predecessor_account_id != ra.receipt_receiver_account_id
     AND c1.account_id IS NULL AND c2.account_id IS NULL
   GROUP BY 1, 2
),
ft AS (
  SELECT f.block_date AS day, f.contract_account_id AS token,
         CAST(SUM(SAFE_CAST(f.delta_amount AS BIGNUMERIC)) AS STRING) AS amount, COUNT(*) AS n
    FROM `bigquery-public-data.crypto_near_mainnet_us.ft_events` f
    LEFT JOIN contracts c1 ON c1.day = f.block_date AND c1.account_id = f.involved_account_id
    LEFT JOIN contracts c2 ON c2.day = f.block_date AND c2.account_id = f.affected_account_id
   WHERE f.block_date BETWEEN @d0 AND @d1
     AND f.contract_account_id IN UNNEST(@tokens)
     AND f.standard = 'nep141' AND f.cause = 'ft_transfer' AND NOT STARTS_WITH(f.delta_amount, '-')
     AND f.involved_account_id != f.affected_account_id
     AND c1.account_id IS NULL AND c2.account_id IS NULL
   GROUP BY 1, 2
)
SELECT day, token, amount, n FROM native
UNION ALL
SELECT day, token, amount, n FROM ft
ORDER BY day, token
