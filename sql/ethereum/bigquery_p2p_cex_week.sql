-- ETHEREUM P2P INTO EXCHANGE WALLETS - the exchange double count, SIZED (P5-1.9, Jake 2026-11-04). NOT subtracted.
-- The P2P rules of bigquery_p2p_daily.sql exactly (neither side a contract, no self-transfers, no mint/burn address,
-- native ETH top-level transactions that succeeded, the declared ERC-20s only), restricted to transfers whose RECEIVER
-- is a wallet an exchange publishes in its own proof-of-reserves list, split by the SENDER:
--   same_exchange   the sender is in the same exchange's list (its own internal moves)
--   other_exchange  the sender is in another listed exchange's list
--   unlisted        any other account: deposit-address sweeps, plus a user sending straight to a listed wallet
-- Parameters: @d0 DATE, @d1 DATE, @tokens ARRAY<STRING>, @cex_addr ARRAY<STRING> and @cex_name ARRAY<STRING> (the same
-- length and order, lower-case; an address listed by two exchanges is dropped before the query). Output: day, token
-- ('ETH' = native, in wei), exchange, sender, amount (string), n.
WITH contracts AS (
  SELECT DISTINCT LOWER(address) AS address FROM `bigquery-public-data.crypto_ethereum.contracts`
),
cex AS (
  SELECT LOWER(a) AS address, nm AS exchange
    FROM UNNEST(@cex_addr) AS a WITH OFFSET AS i
    JOIN UNNEST(@cex_name) AS nm WITH OFFSET AS j ON i = j
),
native AS (
  SELECT DATE(t.block_timestamp) AS day, 'ETH' AS token, r.exchange AS exchange,
         CASE WHEN s.exchange = r.exchange THEN 'same_exchange'
              WHEN s.exchange IS NOT NULL THEN 'other_exchange' ELSE 'unlisted' END AS sender,
         CAST(SUM(t.value) AS STRING) AS amount, COUNT(*) AS n
    FROM `bigquery-public-data.crypto_ethereum.transactions` t
    JOIN cex r ON r.address = LOWER(t.to_address)
    LEFT JOIN cex s ON s.address = LOWER(t.from_address)
    LEFT JOIN contracts c ON c.address = LOWER(t.to_address)
   WHERE t.block_timestamp >= TIMESTAMP(@d0) AND t.block_timestamp < TIMESTAMP(DATE_ADD(@d1, INTERVAL 1 DAY))
     AND t.value > 0 AND t.receipt_status = 1 AND t.to_address IS NOT NULL
     AND LOWER(t.from_address) != LOWER(t.to_address)
     AND c.address IS NULL
   GROUP BY 1, 2, 3, 4
),
erc20 AS (
  SELECT DATE(tt.block_timestamp) AS day, LOWER(tt.token_address) AS token, r.exchange AS exchange,
         CASE WHEN s.exchange = r.exchange THEN 'same_exchange'
              WHEN s.exchange IS NOT NULL THEN 'other_exchange' ELSE 'unlisted' END AS sender,
         CAST(SUM(SAFE_CAST(tt.value AS BIGNUMERIC)) AS STRING) AS amount, COUNT(*) AS n
    FROM `bigquery-public-data.crypto_ethereum.token_transfers` tt
    JOIN cex r ON r.address = LOWER(tt.to_address)
    LEFT JOIN cex s ON s.address = LOWER(tt.from_address)
    LEFT JOIN contracts c1 ON c1.address = LOWER(tt.from_address)
    LEFT JOIN contracts c2 ON c2.address = LOWER(tt.to_address)
   WHERE tt.block_timestamp >= TIMESTAMP(@d0) AND tt.block_timestamp < TIMESTAMP(DATE_ADD(@d1, INTERVAL 1 DAY))
     AND LOWER(tt.token_address) IN UNNEST(@tokens)
     AND LOWER(tt.from_address) != LOWER(tt.to_address)
     AND LOWER(tt.from_address) != '0x0000000000000000000000000000000000000000'
     AND LOWER(tt.to_address) NOT IN ('0x0000000000000000000000000000000000000000',
                                      '0x000000000000000000000000000000000000dead')
     AND c1.address IS NULL AND c2.address IS NULL
   GROUP BY 1, 2, 3, 4
)
SELECT day, token, exchange, sender, amount, n FROM native
UNION ALL
SELECT day, token, exchange, sender, amount, n FROM erc20
ORDER BY day, token, exchange, sender
