# Round 19, item 5 — done_check lines that block only on old history

Written 2026-11-24. Research only; no code, config or test changed.

**Short answer: very few lines qualify.** On the data held here, only the settlement-volume cells of Near (and, once
its current gap is filled, Ethereum) block because history older than the held window is missing. Almost every other
open line blocks because a current figure is missing, a route has not run on the store, or a decision is open.

## What this was read from

- The 15 fixture dumps (as of 2026-10-13), built at the Round 18 code: 14 projects (Plume left out as instructed).
- The four exports of 2026-11-20 (Aerodrome, Ether.fi, Maple, Morpho): all four read READY, so they have no line.
- **Not the live store.** The store's list may differ; the switch below should be keyed on the reason, not on this list.

## The lines that qualify

| # | Project | done_check line | Why it blocks today | History it needs | Forms on (fixture) | Forms on (store, per Round 18) |
|---|---|---|---|---|---|---|
| 1 | Near | audit A1: NRR ON SETTLEMENT VOLUME (market cap ÷ trailing-365-day settlement) | The 365-day sum needs every day; the P2P leg holds 195 of 365 | 12 months | 2027-03-31 | about 2026-12-28, or the run after the December P2P chunk |
| 2 | Near | Summary: Archetype key (settlement per token, 1-year change) | Needs two 365-day sums | 24 months | 2028-03-30 | 2027-11-30 with no backfill; 2027-05-31 with one chunk a month |
| 3 | Near | Summary: Rank in archetype | Follows the key | 24 months | 2028-03-30 | as the key |
| 4 | Ethereum | Summary: Archetype key | Needs two 365-day sums of settlement | 24 months | not dated (the series is stopped) | see note below |
| 5 | Ethereum | Summary: Rank in archetype | Follows the key | 24 months | not dated | see note below |
| 6 | Ethereum | audit A1: NRR ON SETTLEMENT VOLUME | Needs one 365-day sum | 12 months | not dated | see note below |

Lines 2 and 3 already do not block: since Round 17 their reason carries the date they form, and done_check accepts a
dated history reason. They are listed because they are the same cells a "history filling" label would cover.

**Ethereum is not history-only today.** Its settlement series stops on 2026-08-25 because Artemis stopped publishing.
The missing days since then are current data, not old history. Lines 4–6 become history-only once the rebuild
(DefiLlama DEX and NFT, BigQuery P2P) has filled 2026-08-26 to today. How much older history Ethereum then still owes
is in `docs/r19_bigquery_plan.md`.

## What the workbook would show with "history filling"

| Cell | Today | With the switch |
|---|---|---|
| Near NRR on settlement | "n/a — BLOCKED … holds 195 of the 365 days", counted as a blocker | "history filling — 195 of 365 days held, forms on <date>", grey, not counted |
| Near key and rank | "n/a" and "unranked", dated, not counted | "history filling — forms on <date>"; rank stays "unranked" (a project with no measured key is never ranked on a guess) |
| Ethereum key, rank, NRR | "n/a", "unranked", MISSING, counted | the same label once the gap since 2026-08-26 is filled; until then they still block |

Effect on done_check on the fixture: Near 12 lines → 11. Nothing else moves. No frozen project is touched: none of
the nine has a line of this kind on the dumps or the exports.

If Jake also adopts a shorter labelled window for the key (item 4), the key cell would show that figure with its label
instead of "history filling", and the project could be ranked on it. That is a separate decision.

## Lines that look like history but are not

These clear by themselves on a date, or are not about old history. None should get the label.

| Project | Line | What it really is |
|---|---|---|
| Aethir | Net holder yield (Summary, Comparison, audit CP) | A 90-day window part-held: one outflow series is stored from 2026-08-13. It formed by itself on 2026-11-10. On the store this should already be gone. |
| Near | Subsidy cover, Leakage (ii) | No rows on the fixture; the reader stores the whole published history on its first run. A store-run item. |
| Near | Float, total dilution, buying pressure, insider share, T-bill | Series the fixture predates. Store-run items. |
| Sky | Net supply change (two cells) | Circulating is not measured on one basis across the window (a basis question), plus the T-bill series the fixture lacks. |
| Hyperliquid, Sky, Morpho, Chainlink, GEODNET, Pendle | "Δ3m / Δ6m / Δ9m" and flow-opening cells marked NEEDS HISTORY | Real history gaps of 3–12 months, each with a "valid from" date. They are on the audit tabs and are not done_check blockers. |
| Ether.fi | Four STALE lines, key blocked | The fixture's buyback rows end 2026-09-02. Current data, not history. READY on the 20 Nov export. |

## Everything else, by cause (fixture, 14 projects)

| Cause | Projects and lines |
|---|---|
| The fixture predates a series the store holds (clears on a store run) | Ether.fi 10, Fluid 7, Maple 6, Morpho 1, Pendle 9, Sky 1, Chainlink 5, Near 9, GEODNET 1, Ethereum 2 |
| A decision is open | GEODNET uniformity; Ethereum's four structural cells (no supply-side incentive, no unlock set, no buyback) and buying pressure; Aethir's four revenue-split cells, insider list, three structural cells |
| A current figure is wrong or missing | Ethereum burn_30d (ours 2,635.52 against 2,458.91 ETH); Ethereum settlement since 2026-08-26 |
| READY or parked | Hyperliquid, Uniswap READY; Aerodrome parked on the fixture (READY on the export) |

## Decision for Jake

**How to switch it on next round.**
- (a) By reason: any Summary, Comparison, key or headline cell whose only reason is "NEEDS HISTORY" with a window of
  12 months or more and a dated "forms on" reads "history filling", grey, not counted. Covers the store's own list
  without a hand-kept table.
- (b) By this list: the six lines above, named in config.
- **Recommendation: (a)**, with two guards: the series must still be read (its last day within 7 days of the as-of),
  and a window shorter than 12 months keeps today's behaviour. That keeps a stopped series such as Ethereum's from
  hiding behind the label.

## Could not verify

- The live store's done_check lines. This list is from the fixture and four exports.
- The store dates in the last column: they are Round 18's estimates, not reads.
