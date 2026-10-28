# P2f-5b — the A1 key blocks on a change of measuring point inside its one-year span (PROPOSED, not merged)

Status: **do not merge.** Jake's condition (SPEC P2f-5b): if Ethereum's A1 key would block, the rule is not merged and the
break's date and source are reported. On the committed fixture dumps (Jake's 10-13 store) it **would block**. Nothing in
`reader.py` changed on this branch; the rule is a patch to apply later (or never).

Files: `a1_break_check.py` (read-only check, committed), `tests/test_a1_break_check.py` (committed),
`docs/p2f_a1_key_break_rule.patch` (the proposed rule + its tests; `git apply --check` passes on this commit).

## The rule

`reader.settlement_key` forms (S365 ÷ circulating, at the series' last date `end`) ÷ (the same at `then` = `end` − 365 days) − 1.
Proposed: if `trajectory.basis_breaks` (the function `settlement_key_trajectory` already uses; it follows each input's own
inputs and honours declared handovers and `SERIES_BREAK_NOTES`) reports a change of measuring point of
`settlement_volume_365d_usd` or `circulating_supply_used` dated after `then` and on or before `end`, the key reads
**blocked** with the date, series and the two bases. A key that cannot form for lack of history still says that reason first.
A change dated exactly `then` is not in the span (both values are on the new basis). Each trajectory quarter is judged on its
own span (the key's function, its end moved back).

## What the patch changes (reader.py only, plus tests)

* `a1_key_breaks(groups, project, then, end)` — new, a few lines around `trajectory.basis_breaks`.
* `settlement_key` — after the history checks, returns `(None, "blocked — a change of measuring point inside the key's
  one-year span (<then> to <end>): <date> <metric>: <before> -> <after>")`.
* The three places that wrote the literal code `"n/a"` for a key that is None (Summary key, A1 tab key, A1 key quarters)
  now write `code_of(reason)`: the cell reads `blocked` (red) when the reason starts "blocked —", `n/a` otherwise. Summary
  does it for archetype 1 only.
* `tests/test_round_p2e2.py::test_a1_a_change_of_measuring_point_inside_the_span_blocks_the_change`: the old expectation
  (`_quarter_change` returns "blocked") becomes: Q0 and Q-1 are None with a `blocked —` reason (their spans hold the change),
  Q-2 (ends before it) still forms, the quarter change reads "n/a" (no value to difference).
* `tests/test_a1_key_break_rule.py` — fakes: inside the span blocks (settlement and circulating input), before the span does
  not and the number is unchanged, the day `then` is outside, per-quarter spans, a recognised declared handover does not block,
  a history shortfall keeps its own reason, the script and the rule agree, and the fixture.

Apply: `git apply docs/p2f_a1_key_break_rule.patch`, then `python -m pytest tests/test_a1_key_break_rule.py
tests/test_a1_break_check.py tests/test_round_p2e2.py`.

## Cells that change on the fixture build: none

Both workbooks were built twice from `fixtures/real_*.json` into temp dirs (reader.py as committed, and with the patch) and
every cell value and comment of both files compared: **1 of 1,006,625 cells differs, the "workbook built … UTC" time on Run
Log.** No A1 project holds a two-year trailing-365-day settlement series on the fixture, so every A1 key (Summary key, A1-tab
key, its four quarters, the quarter change; rank "unranked") is `n/a` with its own history reason before and after, and the
history reason is checked before the break. The rule changes **no fixture cell**; the fixture cannot show it working.

What it would change on Jake's store (derived from the code, not built; his store is not here): SPEC P2b-4 says at most
Ethereum can form the key there. Ethereum's span holds the 2026-09-29 break below, so its Summary key, A1-tab key and the Q0 key
quarter would go from a number to `blocked`, its rank to `unranked` (A1 would rank nobody), the change vs previous quarter to
`n/a`, and About's "blocked" coverage count would rise by one. Q-1 and earlier end before 2026-09-29 and are unaffected.
(What-if on the fixture groups with a synthetic Ethereum 365-day series injected: `settlement_key` Q0 returns
`blocked — … 2026-09-29 total_supply_protocol: ultrasound:supply-over-time -> etherscan:ethsupply2.supply`; without the rule it
returns a number.)

## Ethereum (fixture, `a1_break_check.py`)

* The break: **2026-09-29, `total_supply_protocol`** — an input of `circulating_supply_onchain` (config.circulating_onchain
  Ethereum: total = total_supply_protocol, nothing subtracted), which `circulating_supply_used` reads. The input affected is
  **circulating_supply_used** (through its total supply), not the 365-day settlement series.
* Stored sources: through 2026-09-28 `ultrasound:supply-over-time[next-day 00:00Z]` (387 rows, the declared history leg
  `total_supply_ultrasound`, config.SUPPLY_HISTORY); from 2026-09-29 `etherscan:ethsupply2.supply` (12 rows to 10-13).
* Inside the key's span (fixture: 2025-10-13 to 2026-10-13; on Jake's store the span ends later and holds 09-29 until
  2027-09-29).
* **It is a declared handover that the Trajectory code does not recognise.** `Ethereum.series_handover["total_supply_protocol"]`
  (declared 2026-10-13) lists `ordered_points` `ultrasound:supply-over-time[next-day 00:00Z]` -> `etherscan:ethsupply2.supply`
  with `composition_change: "none (PROPOSED): Etherscan reads 4,118–4,298 ETH (0.0035%) above ultrasound on the 8-day overlap,
  a constant level from in-flight deposits"`. `build_workbook` (lines 700, 908) and `measuring_guard` normalise the points with
  `_measuring_point` (brackets stripped), so they treat it as declared. `trajectory._own_breaks` (line 431) compares the raw
  strings with the stripped basis, so `[next-day 00:00Z]` never matches and the change is reported as a break.
  What-if on the fixture (every series, `_own_breaks` since 2020, ordered_points normalised the same way): 17 breaks -> 16,
  and the one that goes is exactly this one. The one-line fix (`pts = {basis_of(metric, x) for x in decl["ordered_points"]}`)
  would let Ethereum's key form, but it also removes that break from Ethereum's Trajectory rows, so it changes workbook cells and is
  **not made here**.
* Options for Jake: (1) leave the rule unmerged; (2) merge the rule and accept Ethereum's key blocked until 2027-09-29;
  (3) merge the rule with the normalisation fix, which says the 0.0035% constant offset is not a change of measuring point (the
  declaration is still marked "PROPOSED").

## Fixture result for every A1 project (the projects the reader's A1 tab lists)

| Project | Key today (fixture) | Change of measuring point inside the span | Verdict |
|---|---|---|---|
| Bitcoin, Solana, Tron, Canton, Injective, Zcash | n/a, no 365-day settlement series; no circulating series stored | none | OK (nothing to block) |
| Ethereum | n/a, no 365-day settlement series (the Artemis CSV holds 352 days; the rebuild's P2P leg is not in the dump) | 2026-09-29 `total_supply_protocol` ultrasound -> etherscan (declared, not recognised: above) | WOULD BLOCK |
| Near | n/a (the sum forms 2027-03-31) | 2026-10-11 `circulating_supply_lockup_rule`: "first_party; no lockup read in force" -> "total_supply − LOCKED − lockup.near − contributors.near, lockups read" (the weekly lockup read began; no declaration). Declared and recognised: `total_supply` near_rpc -> coingecko 2026-09-11. | WOULD BLOCK |
| Plume | n/a, settlement held from 2026-05-01 (two years needed) | 2026-01-01 `circulating_supply_used`: `circulating_supply` -> `circulating_supply_first_party` (no declaration). Declared and recognised: coingecko mcap/price -> coingecko 2026-09-11; mirror_of_supply.plume.org -> scrape:supply.plume.org 2026-10-07. | WOULD BLOCK |
| Chainlink | n/a, no 365-day settlement series | none | OK |
| Hyperliquid (secondary) | n/a, no 365-day settlement series | 2026-09-30 `circulating_supply_used` `circulating_supply` -> `circulating_supply_first_party` (declared 2026-10-21 as a STEP, "CoinGecko's count vs Hyperliquid's own … a different basis": reported by design); 2026-10-12 `circulating_supply_first_party` tokenDetails.circulatingSupply -> …-exclude_wallets (declared as a STEP, "the stock narrows": reported by design) | WOULD BLOCK |

The only key that can form on Jake's store is Ethereum's; the other WOULD BLOCK rows are verdicts on keys that are n/a today for
another reason, so no cell changes there (for Near the sum forms 2027-03-31, for Plume in 2028).

Something the rule as worded cannot see (the script prints it as a NOTE): the 365-day sum reads the DAILY settlement series for
two years, but `build_workbook._nrr_rows` labels every day of the sum with the last day's source (`derived:sum365(<last
source>)`), so Ethereum's declared Artemis-CSV -> rebuild stitch at 2026-08-26 ("a DIFFERENT MEASURING POINT", G10 item 4) is
invisible to a break check on `settlement_volume_365d_usd`. Extending the rule to the daily leg is a definition question and is
not in the patch.

## For Jake: one line on the live store

`python a1_break_check.py --store metrics.db` — read-only (sqlite `mode=ro`), no network, exits 0; prints per A1 project the
key's two end dates, each input's measuring points over the span, every change inside it with its date, the stored `source` before
and after and the declaration, and the verdict (`--project Ethereum` for one).
