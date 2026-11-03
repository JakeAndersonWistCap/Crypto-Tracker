# P3-6 — Project notes: the columns that move (Jake 2026-11-02, "scale before adding")

How the list was made: the reader built on the committed real dumps (`fixtures/real_*.json`, the build
`tests/test_round_p2e_ranks.py` makes). On A1–A4, every column where at most one project holds a number, leaving out keys,
ranks, the (c) Trajectory block and the generic columns (price, market cap, FDV, circulating, revenue / fees / TVL,
emissions, unlocks, generic yields). Each sparse column was then judged by what it measures, not by its fill alone: a
column moves when it measures one project's own mechanism (the audit column is formed for that project only — every
other row reads "does not apply" or "not applicable to this project"); a generic column that is sparse because of data
holes, or formed today for one project but defined for any, stays.

The moved cell is the archetype tab's own cell for the owning project, unchanged: the same `_data` formula, number
format, fill (confidence) and comment, or the same code with its reason. The audit file is not touched.
`reader.PROJECT_NOTE_COLUMNS` holds the same rows (tests/test_round_p3_6.py checks they agree).

## Moved to Project notes

| Tab | Header | Project | Reason |
|---|---|---|---|
| A1 Infrastructure | Consensus part of yield | Ethereum | Ethereum's validator yield split: the beacon chain (consensus layer) part |
| A1 Infrastructure | Execution part of yield | Ethereum | Ethereum's validator yield split: the execution layer (priority fees and MEV) part |
| A1 Infrastructure | Issuance ÷ protocol maximum | Ethereum | Ethereum's own issuance curve (166.32·√staked, consensus-specs) |
| A1 Infrastructure | Sky Savings Rate | Sky | Sky's own savings rate (sUSDS ssr()); one macro series, shown once under Sky |
| A1 Infrastructure | NRR on settlement, full year | Plume | Plume's incentive season (Points Season 2) left in the NRR; formed for Plume only |
| A2 Coordination | Reserve inflow ÷ aggregator | Chainlink | The LINK Reserve's inflow against Chainlink's fee aggregator |
| A2 Coordination | Of which checker-node rewards | Aethir | Aethir's reward split: checker-node rewards |
| A2 Coordination | Of which cloud host rewards | Aethir | Aethir's reward split: cloud host rewards |
| A2 Coordination | Of which edge rewards | Aethir | Aethir's reward split: edge rewards |
| A2 Coordination | Emissions earned, % a year | Aethir | Aethir's rewards earned and still vesting (a metric formed for Aethir only); released emissions stay |
| A2 Coordination | Of which aggregator payments | Chainlink | Chainlink's revenue split: fee aggregator payments, Direct Requests included |
| A2 Coordination | Of which Direct Requests | Chainlink | Chainlink's revenue split: Direct Requests, a part of the aggregator figure (not added again) |
| A2 Coordination | Of which CCIP fees | Chainlink | Chainlink's revenue split: CCIP fees |
| A2 Coordination | Of which VRF payments | Chainlink | Chainlink's revenue split: VRF payments |
| A2 Coordination | Of which Automation premium | Chainlink | Chainlink's revenue split: Automation premium |
| A2 Coordination | Publisher Conviction ($) | OriginTrail | OriginTrail's Publisher Conviction (pre-purchased demand) |
| A3 Revenue Buyback | Implied purchases, both legs ($) | Sky | Sky's two-leg buyback split (buy and burn) |
| A3 Revenue Buyback | Implied burn ($) | Sky | Sky's two-leg buyback split: the burn leg |
| A3 Revenue Buyback | Implied burn (tokens) | Sky | Sky's two-leg buyback split: the burn leg in tokens |
| A3 Revenue Buyback | Implied minus actual burn (tokens) | Sky | Sky's burn leg against the chain |
| A3 Revenue Buyback | Of which bought on-chain, 90 days | Ether.fi | Ether.fi's top-up Safe: the share bought on DEXs |
| A3 Revenue Buyback | Of which bought on-chain, all time | Ether.fi | Ether.fi's top-up Safe: the share bought on DEXs, all time |
| A3 Revenue Buyback | Voter APR on voting power | Aerodrome | Aerodrome's veAERO voter APR |
| A3 Revenue Buyback | Share paid to virtual balances | Pendle | Pendle's legacy vePENDLE boosts (virtual sPENDLE) |
| A3 Revenue Buyback | Sky Savings Rate | Sky | Sky's own savings rate (sUSDS ssr()) |
| A3 Revenue Buyback | Still in old vePENDLE (tokens) | Pendle | Pendle's vePENDLE to sPENDLE migration |
| A3 Revenue Buyback | sPENDLE + old vePENDLE (tokens) | Pendle | Pendle's vePENDLE to sPENDLE migration |
| A3 Revenue Buyback | Umbrella staked (Aave, $) | Aave | Aave's Umbrella risk cover (not AAVE supply) |

Notes on the list:

- **Sky Savings Rate** is one macro series (`sky_savings_rate`, only_projects "Macro") shown beside the T-bill on A1
  and A3 for every row. Jake named it as Sky's; it moves from both tabs to ONE row in Sky's block, sourced from Sky's
  A3 cell (Sky has no A1 row). Both audit columns map to that row in reader_column_map.csv. The 3-month T-bill stays.
- **Aerodrome** is parked; its veAERO voter APR moves as it is (the parked grey and comment kept). Nothing else about
  the project changes.
- No A4 column moves (see the kept list).
- No headline of the guard sits in a moved column on the fixture; where one does on a live store, the guard reads it at
  its Project notes cell (HEADLINE_LOCATIONS follows the cell).
- No Highlights chip quotes a moved column (the chips read Summary, Comparison, Business, the rank keys and A4
  "Permanent burn yield"), so no chip is re-pointed.

## Sparse but kept (borderline), and why

| Tab | Header | Holds a number (fixture) | Why it stays |
|---|---|---|---|
| A1 Infrastructure | NRR on settlement / NRR headroom (settlement) / Settlement volume, 365 days | Plume | Generic A1 valuation and driver columns, formed for four chains; sparse because of data holes |
| A1 Infrastructure | Validator yield, gross | Plume | A generic yield (gross of commission), formed for any chain whose headline is net |
| A1 Infrastructure | 3-month T-bill, yield-minus-T-bill columns | — | Generic benchmark and yields |
| A2 Coordination | Published unlocks, next 12 months | Aethir | Unlocks (generic) |
| A3 Revenue Buyback | Emissions in force, a year (tokens) | Sky | Emissions (generic); the A3 key reads it where a rate in force is declared |
| A3 Revenue Buyback | Average lock duration (days) | Aerodrome | A generic lock metric (metric not limited to one project) |
| A3 Revenue Buyback | Tokens locked, protocol dashboard / Locked: chain vs dashboard gap | Pendle | A cross-check formed for Sky, Pendle, Aave and Ether.fi |
| A3 Revenue Buyback | Reserve balance, dashboard / Reserve: chain vs dashboard gap | — | A buyback-fund cross-check formed for Chainlink and Maple |
| A3 Revenue Buyback | Quarter change: buyback locked | Chainlink | The change of "Buyback to locked supply", a generic column (Chainlink and Near) |
| A3 Revenue Buyback | Staking APR, as published | — | A generic yield |
| A4 Permanent Burn | Burn split (tokens) | Hyperliquid | Generic: every burn − the A4 burn, for any project with several burn sources |
| A4 Permanent Burn | One-off burns, 12 months (tokens) | Uniswap | Generic burn accounting (an event row for any project); sparse because only Uniswap had an event |
| A4 Permanent Burn | Revenue needed for crossover / Governance lever / Total dilution | GEODNET | Generic A4 drivers; sparse because of data holes |
| A4 Permanent Burn | Net mint, self-reported / Self-reported minus derived | — | Generic supply columns (self-reported where a protocol publishes it) |
| all | text columns (statuses, bases, caveats) and growth columns | — | Every project holds text or a period formula; not one project's mechanism |
