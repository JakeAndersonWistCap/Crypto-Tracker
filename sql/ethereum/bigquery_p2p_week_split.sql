-- ETHEREUM P2P, ONE WEEK, SPLIT (P5-1.9, Jake 2026-11-04). Every transfer the P2P leg counts - the rules of
-- bigquery_p2p_daily.sql exactly (neither side a contract, no self-transfers, no mint/burn address, native ETH top-level
-- transactions that succeeded, the declared ERC-20s only) - classified two ways, nothing dropped:
-- 1. HOW IT WAS MADE (hypothesis (a): does the P2P leg already hold DEX trading?)
--      direct                       native ETH (always a top-level transaction), or an ERC-20 transfer whose transaction
--                                   calls the token contract itself (a plain transfer / transferFrom)
--      contract_call_single_token   the transfer is made inside a call to ANOTHER contract and that transaction moves one
--                                   token only (batch senders, Permit2, payment and vesting contracts)
--      contract_call_multi_token    made inside a call to another contract that moves two or more tokens in the same
--                                   transaction: the shape of a swap, an RFQ / intent fill or an aggregator route
-- 2. INTO A WALLET AN EXCHANGE PUBLISHES (the exchange double count): to_exchange = the receiver's exchange ('' when
--    the receiver is not listed); sender = same_exchange / other_exchange / unlisted ('' when to_exchange is '').
-- Parameters: @d0 DATE, @d1 DATE, @tokens ARRAY<STRING>, @cex_addr ARRAY<STRING>, @cex_name ARRAY<STRING> (same length
-- and order, lower-case). Output: day, token ('ETH' = native, in wei), call_class, to_exchange, sender, amount, n.
WITH contracts AS (
  SELECT DISTINCT LOWER(address) AS address FROM `bigquery-public-data.crypto_ethereum.contracts`
),
cex AS (
  SELECT LOWER(a) AS address, nm AS exchange
    FROM UNNEST(@cex_addr) AS a WITH OFFSET AS i
    JOIN UNNEST(@cex_name) AS nm WITH OFFSET AS j ON i = j
),
tx AS (
  SELECT `hash` AS tx_hash, LOWER(to_address) AS tx_to
    FROM `bigquery-public-data.crypto_ethereum.transactions`
   WHERE block_timestamp >= TIMESTAMP(@d0) AND block_timestamp < TIMESTAMP(DATE_ADD(@d1, INTERVAL 1 DAY))
),
tokens_per_tx AS (
  SELECT transaction_hash AS tx_hash, COUNT(DISTINCT LOWER(token_address)) AS n_tokens
    FROM `bigquery-public-data.crypto_ethereum.token_transfers`
   WHERE block_timestamp >= TIMESTAMP(@d0) AND block_timestamp < TIMESTAMP(DATE_ADD(@d1, INTERVAL 1 DAY))
   GROUP BY 1
),
native AS (
  SELECT DATE(t.block_timestamp) AS day, 'ETH' AS token, 'direct' AS call_class,
         LOWER(t.from_address) AS f, LOWER(t.to_address) AS r, CAST(t.value AS BIGNUMERIC) AS v
    FROM `bigquery-public-data.crypto_ethereum.transactions` t
    LEFT JOIN contracts c ON c.address = LOWER(t.to_address)
   WHERE t.block_timestamp >= TIMESTAMP(@d0) AND t.block_timestamp < TIMESTAMP(DATE_ADD(@d1, INTERVAL 1 DAY))
     AND t.value > 0 AND t.receipt_status = 1 AND t.to_address IS NOT NULL
     AND LOWER(t.from_address) != LOWER(t.to_address)
     AND c.address IS NULL
),
erc20 AS (
  SELECT DATE(tt.block_timestamp) AS day, LOWER(tt.token_address) AS token,
         CASE WHEN x.tx_to = LOWER(tt.token_address) THEN 'direct'
              WHEN COALESCE(k.n_tokens, 1) >= 2 THEN 'contract_call_multi_token'
              ELSE 'contract_call_single_token' END AS call_class,
         LOWER(tt.from_address) AS f, LOWER(tt.to_address) AS r, SAFE_CAST(tt.value AS BIGNUMERIC) AS v
    FROM `bigquery-public-data.crypto_ethereum.token_transfers` tt
    LEFT JOIN contracts c1 ON c1.address = LOWER(tt.from_address)
    LEFT JOIN contracts c2 ON c2.address = LOWER(tt.to_address)
    LEFT JOIN tx x ON x.tx_hash = tt.transaction_hash
    LEFT JOIN tokens_per_tx k ON k.tx_hash = tt.transaction_hash
   WHERE tt.block_timestamp >= TIMESTAMP(@d0) AND tt.block_timestamp < TIMESTAMP(DATE_ADD(@d1, INTERVAL 1 DAY))
     AND LOWER(tt.token_address) IN UNNEST(@tokens)
     AND LOWER(tt.from_address) != LOWER(tt.to_address)
     AND LOWER(tt.from_address) != '0x0000000000000000000000000000000000000000'
     AND LOWER(tt.to_address) NOT IN ('0x0000000000000000000000000000000000000000',
                                      '0x000000000000000000000000000000000000dead')
     AND c1.address IS NULL AND c2.address IS NULL
),
legs AS (
  SELECT * FROM native
  UNION ALL
  SELECT * FROM erc20
)
SELECT l.day, l.token, l.call_class, COALESCE(rx.exchange, '') AS to_exchange,
       CASE WHEN rx.exchange IS NULL THEN ''
            WHEN sx.exchange = rx.exchange THEN 'same_exchange'
            WHEN sx.exchange IS NOT NULL THEN 'other_exchange' ELSE 'unlisted' END AS sender,
       CAST(SUM(l.v) AS STRING) AS amount, COUNT(*) AS n
  FROM legs l
  LEFT JOIN cex rx ON rx.address = l.r
  LEFT JOIN cex sx ON sx.address = l.f
 GROUP BY 1, 2, 3, 4, 5
 ORDER BY 1, 2, 3, 4, 5
