# Round 18 — frozen moves (one table for the round)

**On the 15-dump fixture (what `project_freeze.py --compare all` reads): no frozen cell moved.** All nine frozen
projects read 0 undeclared on the build of the merged round. There is no `frozen_moves_r18.json`: nothing to declare.
The fixture is pre-merger (as of 2026-10-13) and holds no Velodrome rows, so Aerodrome's new treatment does not fire there.

**On Jake's Aerodrome export of 2026-11-20 (with the Round 17 and Round 18 rows added): 105 of 363 cells move.**
These are the moves to expect on the store after the next run. One reason per group:

| Project | Cell (group) | Before | After | Reason |
|---|---|---|---|---|
| Aerodrome | Fees Q0 ($) | 39,265,851 | 38,449,883 | Velodrome's revenue taken out before 2026-10-22 (Jake: Aerodrome only before the merger) |
| Aerodrome | Revenue Q0 ($) | 30,339,669 | 29,559,288 | same |
| Aerodrome | Staking yield | 13.90% | 13.42% | same |
| Aerodrome | Payout ratios, fees / FDV, revenue trends | — | — | same (follow fees and revenue) |
| Aerodrome | Gross issuance Q0 (tokens) | 70,190,338 | 67,092,481 | Velodrome's pre-merger emissions out of every figure |
| Aerodrome | Emissions Q0 (tokens) | 75.88M | 73.13M | same |
| Aerodrome | Total dilution, % of float | 29.92% | 28.83% | same |
| Aerodrome | Subsidy cover (x) | 0.892 | 0.911 | same |
| Aerodrome | A3 key: burned / (emissions + unlocks) | 0.0293 | 0.0304 | same |
| Aerodrome | Net supply change Q0 (tokens) | 66,577,988 | 65,259,775 | Aerodrome-only circulating before 10-22; the 117M merger issuance is on its own line, outside the change |
| Aerodrome | Net supply change, % a year | 12.58% | 12.33% | same |
| Aerodrome | Holder value yield | 9.70% | 9.85% | same (market cap on the Aerodrome-only basis before 10-22) |
| Aerodrome | Net holder yield | -4.33% | -4.19% | same |
| Aerodrome | Flows Q0: opening circulating | 2,079,856,213 | 1,964,174,426 | Velodrome's supply no longer restated into the opening |
| Aerodrome | Flows Q0: + merger issuance (new line) | 0 | 116,999,999 | The AERO minted for VELO conversions, one-off, on its own line |
| Aerodrome | Flows Q0: residual / opening | -0.05% | +0.05% | follows the two lines above |

Source: the Aerodrome agent's single-project builds from the export, before and after. Not seen on the store.
