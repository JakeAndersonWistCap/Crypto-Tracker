-- NEAR settlement route (a): IS bigquery-public-data.crypto_near_mainnet_us STILL BEING UPDATED?
-- Run FIRST, in console.cloud.google.com/bigquery. Both queries are cheap: the first reads table
-- metadata only (no bytes billed); the second scans one DATE column per table.
-- Why it matters (near/docs@c0686549 data-infrastructure/big-query.mdx:38-40, added 2026-05-07):
--   "NEAR Lake (the AWS S3 source) was deprecated on March 24, 2026 and no longer indexes new blocks.
--    The NEAR Public Lakehouse / BigQuery dataset itself remains available"
-- and the only ingestion code (near/near-public-lakehouse, last commit 2025-03-21) still reads
-- s3a://near-lake-data-mainnet/. If MAX(block_date) is ~2026-03-24, the dataset is FROZEN: report the
-- date and stop — route (a) cannot give a current series.

-- 1. which tables exist, their size, and when each was last modified
SELECT table_id, row_count, ROUND(size_bytes / 1e12, 3) AS size_tb,
       TIMESTAMP_MILLIS(last_modified_time) AS last_modified
FROM `bigquery-public-data.crypto_near_mainnet_us.__TABLES__`
ORDER BY table_id;

-- 2. the newest day each table actually holds
SELECT 'blocks' AS t, MAX(block_date) AS last_day FROM `bigquery-public-data.crypto_near_mainnet_us.blocks`
UNION ALL SELECT 'receipt_actions', MAX(block_date) FROM `bigquery-public-data.crypto_near_mainnet_us.receipt_actions`
UNION ALL SELECT 'execution_outcomes', MAX(block_date) FROM `bigquery-public-data.crypto_near_mainnet_us.execution_outcomes`;
-- (if query 1 lists ft_events, add:  UNION ALL SELECT 'ft_events', MAX(block_date) FROM `...ft_events`)
