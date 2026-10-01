-- NEAR / BigQuery: HOW THE TABLES ARE LAID OUT — read before any heavy query. Jake's sandbox
-- project near-data-510309 (no billing: it cannot be charged; queries count against the free
-- 1 TB/month). INFORMATION_SCHEMA queries bill a 10 MB minimum each, so these three cost ~30 MB.
-- From NEAR's own writer (near/near-public-lakehouse@76e0b2cd "BQ Writer Stream.py"): every table
-- is DAY-PARTITIONED on block_date (ft_balances_daily on epoch_date); ft_events and nft_events are
-- CLUSTERED on contract_account_id, ft_balances_daily on account_id; receipt_actions is NOT
-- clustered; circulating_supply is neither partitioned nor clustered (1,370 rows). Query (1)
-- confirms that against the live dataset.

-- (1) partitioning and clustering columns
SELECT table_name, column_name, is_partitioning_column, clustering_ordinal_position, data_type
  FROM `bigquery-public-data.crypto_near_mainnet_us`.INFORMATION_SCHEMA.COLUMNS
 WHERE table_name IN ('receipt_actions', 'ft_events', 'ft_balances_daily', 'circulating_supply')
   AND (is_partitioning_column = 'YES' OR clustering_ordinal_position IS NOT NULL)
 ORDER BY table_name, clustering_ordinal_position;

-- (2) rows and ALL-COLUMN bytes per month for the last 13 months (a year's share of each table;
--     the P2P query reads only some columns, so its dry run is smaller than these totals)
SELECT table_name, SUBSTR(partition_id, 1, 6) AS month, SUM(total_rows) AS n_rows,
       ROUND(SUM(total_logical_bytes) / 1e9, 1) AS gb_all_columns
  FROM `bigquery-public-data.crypto_near_mainnet_us`.INFORMATION_SCHEMA.PARTITIONS
 WHERE table_name IN ('receipt_actions', 'ft_events')
   AND partition_id NOT IN ('__NULL__', '__UNPARTITIONED__')
   AND partition_id >= FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 400 DAY))
 GROUP BY 1, 2
 ORDER BY 1, 2;

-- (3) the schemas of circulating_supply and ft_balances_daily
SELECT table_name, column_name, data_type
  FROM `bigquery-public-data.crypto_near_mainnet_us`.INFORMATION_SCHEMA.COLUMNS
 WHERE table_name IN ('circulating_supply', 'ft_balances_daily')
 ORDER BY table_name, ordinal_position;
