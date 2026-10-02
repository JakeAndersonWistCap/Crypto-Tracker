-- NEAR transactions per UTC day, and how many were SIGNED by a Kai-Ching account (*.kaiching).
-- Jake's probes7 (2026-10-02): the March->April 2026 drop is Kai-Ching — hotwallet.kaiching signed
-- 15,107,069 transactions in 2026-03-09..15 and none in 2026-04-06..12 (93.7% of the drop), users.kaiching
-- 828,287 -> 0 (5.1%). n - kaiching_n is the ex-Kai-Ching activity that reads across the break.
-- Partitioned by block_date: scans block_date + signer_account_id for the requested days only.
-- Parameters: @d0, @d1 DATE (inclusive).
SELECT block_date AS day,
       COUNT(*) AS n,
       COUNTIF(ENDS_WITH(signer_account_id, '.kaiching')) AS kaiching_n
  FROM `bigquery-public-data.crypto_near_mainnet_us.transactions`
 WHERE block_date BETWEEN @d0 AND @d1
 GROUP BY day
 ORDER BY day
