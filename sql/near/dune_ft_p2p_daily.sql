-- NEAR settlement route (b) on Dune. Save as a Dune query, put its id in .env as NEAR_DUNE_QUERY_ID,
-- then: python check_offline_items.py near_settlement_routes  (executes it once, reports datapoints).
-- near.ft_transfers (spellbook sources/_base_sources/other/near_base_sources.yml:166-227): RAW
-- delta_amount, no USD column; each transfer appears twice (+ for the receiver, - for the sender) and
-- mints/burns are rows too — so cause = 'ft_transfer' AND delta_amount > 0 counts each transfer once
-- (affected = receiver, involved = sender). Contracts = accounts that ever received DEPLOY_CONTRACT.
-- ~365 rows x (day, token, amount, n) per token kept: the TOP 20 tokens by transfer count.
-- FIRST check freshness:  SELECT MAX(block_date) FROM near.ft_transfers
WITH contracts AS (
  SELECT DISTINCT receipt_receiver_account_id AS a FROM near.actions WHERE action_kind = 'DEPLOY_CONTRACT'),
t AS (
  SELECT f.block_date AS day, f.contract_account_id AS token,
         f.involved_account_id AS sender, f.affected_account_id AS receiver,
         TRY_CAST(f.delta_amount AS double) AS amt_raw
  FROM near.ft_transfers f
  WHERE f.standard = 'nep141' AND f.cause = 'ft_transfer' AND TRY_CAST(f.delta_amount AS double) > 0
    AND f.block_date >= current_date - interval '365' day AND f.block_date < current_date),
p2p AS (
  SELECT t.* FROM t
  WHERE t.sender NOT IN (SELECT a FROM contracts) AND t.receiver NOT IN (SELECT a FROM contracts)
    AND t.sender <> t.receiver),
top AS (SELECT token FROM p2p GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 20)
SELECT p.day, p.token, SUM(p.amt_raw) AS amt_raw, COUNT(*) AS n
FROM p2p p JOIN top USING (token)
GROUP BY 1, 2 ORDER BY 1, 2;
