-- ft_balances_daily IS NOT FUNGIBLE-TOKEN BALANCES. NEAR's writer fills it from
-- silver_accounts_daily_ft_balances with: epoch_date, epoch_block_height, account_id, liquid,
-- storage_usage, unstaked_not_liquid, staked, reward, lockup_account_id, lockup_liquid,
-- lockup_unstaked_not_liquid, lockup_staked, lockup_reward (BQ Writer Stream.py:304-320) — each
-- account's NATIVE NEAR, split liquid / staked / locked, per epoch. So it gives the HISTORY of the
-- Intents revenue wallets' native-NEAR balance (what buyback_fund_balance reads live), NOT their
-- wNEAR (an FT) and NOT the inflow actual_buyback_tokens measures. Partitioned on epoch_date and
-- clustered on account_id; the whole table is ~5 GB, and three accounts over a year prune to far
-- less. Not wired: Jake decides (config Near.near_bigquery.balances).
SELECT epoch_date, account_id, liquid, staked, unstaked_not_liquid
  FROM `bigquery-public-data.crypto_near_mainnet_us.ft_balances_daily`
 WHERE epoch_date BETWEEN @d0 AND @d1
   AND account_id IN ('fefundsadmin.sputnik-dao.near', '1csfundsadmin.sputnik-dao.near',
                      'buybacks.multisignature.near')
 ORDER BY epoch_date, account_id
