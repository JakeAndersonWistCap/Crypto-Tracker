-- WHICH NEP-141 TOKENS CARRY THE VALUE: gross ft_transfer amounts per token over @d0..@d1 (30 days),
-- the 500 busiest by count. fetch/near_bigquery.py prices them (DefiLlama, near:<contract>, with
-- decimals) and keeps the smallest set covering >95% of the value; the P2P query then reads only
-- those tokens, which ft_events' clustering on contract_account_id lets BigQuery prune.
-- Reads 5 columns of ft_events for 30 day-partitions (dry-run first).
SELECT contract_account_id AS token,
       CAST(SUM(SAFE_CAST(delta_amount AS BIGNUMERIC)) AS STRING) AS amount,
       COUNT(*) AS n
  FROM `bigquery-public-data.crypto_near_mainnet_us.ft_events`
 WHERE block_date BETWEEN @d0 AND @d1
   AND standard = 'nep141' AND cause = 'ft_transfer' AND NOT STARTS_WITH(delta_amount, '-')
 GROUP BY 1
 ORDER BY n DESC
 LIMIT 500
