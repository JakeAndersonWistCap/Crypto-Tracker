-- NEAR's OWN circulating supply, one row a day (near/near-public-lakehouse@76e0b2cd "Aggregated
-- Circulating Supply Pipeline.py"): total supply at the day's last block MINUS the tokens still
-- locked in every *.lockup.near contract (the indexer-for-explorer lockup arithmetic, served by
-- NEAR's Rust API) MINUS the balances of lockup.near and contributors.near — NEAR's official
-- circulating method (near-indexer-for-explorer circulating-supply/src/main.rs). yoctoNEAR.
-- NOT partitioned: every run scans the whole table (~1,400 rows x 4 columns, ~45 KB), billed at
-- BigQuery's 10 MB minimum. Parameter: @since DATE (the last day already stored).
SELECT block_date, computed_at_block_height, circulating_tokens_supply, total_tokens_supply
  FROM `bigquery-public-data.crypto_near_mainnet_us.circulating_supply`
 WHERE block_date > @since
 ORDER BY block_date
