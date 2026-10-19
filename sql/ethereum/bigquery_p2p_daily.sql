-- ETHEREUM SETTLEMENT VOLUME, P2P LEG — Artemis method, REBUILT (Round G7 item 8, Jake 2026-10-19).
-- Artemis counts a transfer as P2P only when NEITHER side is a contract (every CREATE/CREATE2), no self-transfers, no
-- mint/burn address. Here:
--   native ETH  top-level transactions with value > 0 that succeeded, the receiver not a contract (a transaction's
--               sender is an externally owned account); internal transfers are contract-sent, so out by the rule.
--   ERC-20      token_transfers of @tokens only (stablecoins + WETH, config SETTLEMENT_REBUILD.ethereum_rebuild),
--               neither side in crypto_ethereum.contracts, not from 0x0, not to 0x0 / 0x…dEaD. RAW amounts: the adapter
--               applies decimals and prices. Artemis's "other tokens in the top-250 DEX pairs" are NOT counted — a
--               known understatement the validation measures.
-- Parameters: @d0 DATE, @d1 DATE, @tokens ARRAY<STRING> (lower-case addresses). Output: day, token ('ETH' = native, in
-- wei), amount (string), n. Partition filters on block_timestamp, so only the days asked for are scanned.
WITH contracts AS (
  SELECT DISTINCT LOWER(address) AS address FROM `bigquery-public-data.crypto_ethereum.contracts`
),
native AS (
  SELECT DATE(t.block_timestamp) AS day, 'ETH' AS token, CAST(SUM(t.value) AS STRING) AS amount, COUNT(*) AS n
    FROM `bigquery-public-data.crypto_ethereum.transactions` t
    LEFT JOIN contracts c ON c.address = LOWER(t.to_address)
   WHERE t.block_timestamp >= TIMESTAMP(@d0) AND t.block_timestamp < TIMESTAMP(DATE_ADD(@d1, INTERVAL 1 DAY))
     AND t.value > 0 AND t.receipt_status = 1 AND t.to_address IS NOT NULL
     AND LOWER(t.from_address) != LOWER(t.to_address)
     AND c.address IS NULL
   GROUP BY 1, 2
),
erc20 AS (
  SELECT DATE(tt.block_timestamp) AS day, LOWER(tt.token_address) AS token,
         CAST(SUM(SAFE_CAST(tt.value AS BIGNUMERIC)) AS STRING) AS amount, COUNT(*) AS n
    FROM `bigquery-public-data.crypto_ethereum.token_transfers` tt
    LEFT JOIN contracts c1 ON c1.address = LOWER(tt.from_address)
    LEFT JOIN contracts c2 ON c2.address = LOWER(tt.to_address)
   WHERE tt.block_timestamp >= TIMESTAMP(@d0) AND tt.block_timestamp < TIMESTAMP(DATE_ADD(@d1, INTERVAL 1 DAY))
     AND LOWER(tt.token_address) IN UNNEST(@tokens)
     AND LOWER(tt.from_address) != LOWER(tt.to_address)
     AND LOWER(tt.from_address) != '0x0000000000000000000000000000000000000000'
     AND LOWER(tt.to_address) NOT IN ('0x0000000000000000000000000000000000000000',
                                      '0x000000000000000000000000000000000000dead')
     AND c1.address IS NULL AND c2.address IS NULL
   GROUP BY 1, 2
)
SELECT day, token, amount, n FROM native
UNION ALL
SELECT day, token, amount, n FROM erc20
ORDER BY day, token
