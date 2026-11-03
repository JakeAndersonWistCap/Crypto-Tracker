# P3-1a — Rule 3 sweep ("0 is not n/a"), 2026-11-02

SPEC.md section 1 rule 3: where a quantity is genuinely zero the cell reads 0 with its reason; a code (n/a, by design …)
stays only where the quantity truly does not exist. This sweep lists every reason the build writes for a store-driven
cell that says a MECHANISM IS ABSENT, with a decision per reason.

**How it was swept (both ways the brief asks for):**

1. `rule3.py` (`SUSPECT_PATTERNS`, `suspects(reader_wb, audit_wb)`) scanned every code cell of both workbooks built on
   the fixture dumps (fixtures/real_*.json, as of 2026-10-13; the build tests/test_round_p2e_ranks.py makes) — reader
   codes by their comment, audit text cells and formulas by their text (a formula is evaluated; one that evaluates to a
   number is not a code). Before P3-1: 1,004 audit + reader hits, 30 distinct reasons. After: see the end.
2. The same patterns over the source (`build_workbook.py`, `reader.py`, `config.py`, `credibility.py`) so a reason not
   rendered on the fixture is covered (rows marked *source*).

Decisions: **0** = measured 0 (the mechanism verifiably does not exist; source named) · **code** = stays a code by
design (the quantity does not exist; why) · **R2** = Ethereum's holder value is its fee burn (a number, not 0) ·
**open** = a 0 candidate outside R1's list, or unknown — not changed in this phase (any other definition change stops
the phase, SPEC P3 preamble).

## Holder value (CP Comparability; reader Summary J / M, Comparison, A3; Trajectory Q0..Q-4 of the same cells)

| # | Reason written | Cells | Projects | Decision |
|---|---|---|---|---|
| 1 | `n/a — no buyback programme and no declared payout to holders` (build_workbook `hvy`; credibility `cp_hvy`) | HOLDER VALUE YIELD; reader "Holder value yield" | Morpho, Aethir, Plume | **0** — no payout mechanism: Morpho fee_split status `off`, share 0.0 (docs.morpho.org, 2026-09-14); Aethir fee_split share 0.0, "no fee distribution, no buy-and-burn, no revenue sharing" (docs.aethir.com/aethir-tokenomics/token-overview, 2026-09-14); Plume fee_split share 0.0, revenue sharing "being explored, not live", PLUME not burned (docs.plume.org, 2026-09-14; not_applicable.gross_burn_tokens). config.HOLDER_VALUE `none` + DECLARED_ZERO (the ended-programme path), held only while `holder_value_absent()` finds the fee_split record agreeing. |
| 1e | same, plus `STALE — last point 2026-09-27` | same | Ethereum | **R2** — holder value = the EIP-1559 fee burn (gross_burn_tokens × price, share 1.0 of revenue); the stale input was the burn re-labelled as a buyback (P3-1c). |
| 1p | same | same | PancakeSwap | **code, reason corrected** — a mechanism EXISTS (the fee-funded product burn) but is NOT MEASURED (BUYBACK_ROUTE_OVERRIDE); the cell now gives that reason instead of "no buyback programme" (unknown is never 0). |
| 1o | same | same | Bitcoin, Zcash, Bittensor, Virtuals, peaq (*not in the fixture dumps*) | **open** — likely 0 (no payout mechanism) but R1 names only Morpho, Aethir, Plume; each needs its fee_split at share 0.0 with a source, then `HOLDER_VALUE[name] = {"none": …}` makes it 0 with no code change. |
| 2 | `n/a — no holder value yield` (`nhy`) | NET HOLDER YIELD; reader "Net holder yield" | as #1 | follows #1: R1 projects now 0 − dilution (a number where the dilution legs are formed); Ethereum R2; PancakeSwap reason corrected; others open. |
| 3 | `by design — <P> returns no revenue-funded value to holders …` (reader `blank_reason`) | Payout ratio (both bases) | as #1 | R1 projects: **0** (0 of revenue paid out) where revenue is stored in the window; with no revenue the ratio is 0 ÷ 0 → **code** ("a payout ratio of 0 ÷ 0 does not exist"). Ethereum: R2, measured (1.0 by definition). Others: follow #1. |
| 4 | `n/a — no holder value` (`non_stakers`) | Non-stakers receive value (yes / no) | as #1 | an attribute (yes / no), not a quantity: R1 projects now read "no — nothing reaches holders: <mechanism, source>"; Ethereum "yes — retired / burned tokens reach every holder". |
| 5 | `0 — no such flow: no holder value on <P> is funded from outside revenue …` | Flows "Holder value RECORDED, NOT CREDITED" | all | **0** already (config.HOLDER_VALUE funding record) — no change. |

## Staked / locked and everything that divides by float (CP; A3 / A4 tabs; reader Summary K / L, Comparison, A3, A4)

| # | Reason written | Cells | Projects | Decision |
|---|---|---|---|---|
| 6 | `n/a — no lock metric` (`_free_float_reason`) | Staked / locked; TRADEABLE FLOAT; float ÷ circulating; every "÷ tradeable float" rate (dilution a–e, burn rate); TOTAL DILUTION ÷ float; A4 unlock / total dilution % of free float | Uniswap | **0** — no UNI staking or lock mechanism: Uni.sol only delegates votes (moves and locks no tokens); the fee switch burns UNI through the Firepit (gov.uniswap.org/t/25881, proposal 93); config Uniswap notes "no distribution leg, no staking yield". `free_float_lock_zero` — the Morpho / Maple declaration — so float = circulating and every ÷ float rate is a number. |
| 7 | `n/a — FLUID STAKING IS NOT DEPLOYED …` (config not_applicable.locked_tokens via `_free_float_reason` / Data) | the cells of #6 + A3 lock rate, Tokens locked (ve), unlock / total dilution % of free float | Fluid | **0** — no FLUID-token staking or lock contract in Instadapp/fluid-contracts-public (config fluid_token_staking_checked, 2026-09-23; contracts/periphery/resolvers/stakingRewards/SPEC.md). `free_float_lock_zero`. |
| 8 | `n/a — locked_tokens (now): not applicable … NO MORPHO STAKING` / `withdrawn` (Maple) | Lock rate (CP "Holding demand", A3), A3 Tokens locked | Morpho, Maple | **0** — the same declaration they already carry (free_float_lock_zero) now reaches the lock-rate cells too (`_lock_inside`), one computation for float and lock rate (rule 1). |
| 9 | `n/a — FLUID staking is not deployed — nothing is staked to be paid` (config `PROTOCOL_YIELD_NOT_APPLICABLE`) | PROTOCOL STAKING YIELD (tokens / $), Staker yield — protocol | Fluid | **code** — a yield per staked token does not exist where nothing can be staked (0 ÷ 0), and nothing is paid. |
| 10 | `n/a — no staker yield on this project (nothing is paid to stakers)` (`uniformity`) | UNIFORMITY = whole-token ÷ staker yield | Aethir, Fluid, GEODNET, Maple, Morpho, Uniswap, … | **code** — a ratio over a staker yield that does not exist. |
| 11 | `n/a — locked_tokens (now): not applicable … ETHEREUM HAS NO LOCK CONTRACT` | Lock rate (CP, Comparison) | Ethereum | **open (not a zero)** — ETH IS staked (CP's staked column reads beacon_chain_eth via CP_STAKE_METRIC); the lock-rate columns read locked_tokens instead, so the same concept reads two ways (rule 1). A number, not a 0; a definition fix for a later phase. **Closed by P4-Q4 (Jake 2026-11-03): staked ETH counts as locked (the exit queue makes it a lock) — the lock rate reads beacon_chain_eth ÷ circulating, the same stake as CP's staked column (`_lock_inside` → `_cp_lock`, config.STAKE_COUNTS_AS_LOCKED).** |
| 12 | `n/a — locked_tokens (now): not applicable …` | Lock: contract vs dashboard divergence | Fluid | **code** — a check between two readings neither of which exists. |

## Buyback fund, destination, incentives, unlocks

| # | Reason written | Cells | Projects | Decision |
|---|---|---|---|---|
| 13 | `n/a — buyback_fund_balance (now): not applicable — NO BUYBACK FUND EXISTS (SEPARATELY)` | A3 Buyback to LOCKED supply, Reserve / contract vs dashboard gap, Effective float's hold leg | Sky, Fluid | **code** — no separate fund: the bought tokens are in the treasury, read ONCE as treasury_holding_tokens; a 0 here would say nothing is held, which is false. |
| 14 | `n/a — No buyback; fee switch off` / `n/a — no buyback or burn; fees collected at 0x6CBb55…` | Destination durability (attribute) | Morpho, Plume | **code** — an attribute of a destination that does not exist. |
| 15 | `n/a — no buyback programme declared (config fee_split)` (`Buyback status`) | Config & Sources; Coverage MISSING / MODELLED rows mirroring it | Aethir, Ethereum, Plume, … | **code** — a status text, not a quantity (the quantity is #1). |
| 16 | `n/a — no supply-side incentive (b) series on file; outflows held: …` (`subsidy`) | SUBSIDY COVER (b), (b + c); LEAKAGE (ii) | Chainlink, Ether.fi, Ethereum, Hyperliquid, Near, Sky, Uniswap, + 15 not in the dumps | Subsidy cover: **code** — revenue ÷ 0 does not exist. Leakage (ii) = max(0, 1 − cover): **open** — 0 where (b) verifiably does not exist, but "no series on file" is not that evidence (rule 13); needs FLOW_LINE_EVIDENCE `no_such_flow` for (b) per project. |
| 17 | `n/a — no non-circulating wallet set on this project (config.unlock_stock_metrics)` | Unlocks Q0 (tokens) | Aethir, Ethereum, Plume, Sky, + 15 | Ethereum: **open (0 candidate)** — no protocol treasury or pre-mint exists (config not_applicable.treasury_holding_tokens), so unlocks are 0 — not in R1's list. Aethir, Plume, Sky: **code (unknown)** — they have insider / foundation wallets; the route is a declared wallet set. |
| 18 | `n/a — Data: not applicable to this project — …` | Data tab rows of locked_tokens (Fluid), buyback_fund_balance (Sky, Fluid) | — | **code** — the Data row of a series that does not exist; its 0 lives in the cells that use it (#7). |
| 19 | `BLOCKED — NOT AN OUTFLOW (SPEC D5-3c) … no buyback entered the SSF …` | Data ssf_net_outflow_tokens | Maple | **code (blocked by design)** — matched on "no buyback" incidentally; not an absent mechanism. |
| 20 | *source* `" · no buyback — fees to voters"` (`_retirement_zero`) | A3 retirement label | Aerodrome (parked) | parked — not touched. |
| 21 | *source* reader chart note `(no holder value yield or no total dilution)` | Charts tab note | — | follows #1 / #6. |

## After P3-1 (fixture build, same scan)

`python rule3.py reader.xlsx audit.xlsx` on the fixture build: 1,004 hits before (126 reader, 878 audit) → 817 after
(89 reader, 728 audit), before the last two wording fixes (Morpho's 0 ÷ 0 payout ratio; PancakeSwap's payout ratio now
names its unmeasured burn), which take out 5 more reader hits. Gone: every R1 / R2 row (#1 Morpho / Aethir / Plume /
Ethereum, #2, #3, #4, #6, #7, #8). Every remaining hit is a row above with decision **code** or **open**; the reader
hits left for the 15 fixture projects are #9 (Fluid staking yield), #11 (Ethereum lock rate), #12 / #13 (Fluid / Sky
fund and lock cross-checks) and #16 (subsidy cover / leakage (ii)).

A paragraph that records a measured zero ("… = 0 — a measured zero (SPEC rule 3) …: <mechanism, source>", the comment
the build now writes on each such 0) is rule 3 obeyed, so `rule3.is_suspect` leaves it out: a code beside it (Morpho's
net holder yield, blocked on the fixture by its emissions leg) is judged by its own reason.

## Fixture vs Jake's store (2026-10-29)

On the fixture dumps (as of 2026-10-13) several R1 net holder yields stay BLOCKED by a dilution leg the dumps do not
cover (Aethir outflow (a) 61 of 90 days; Morpho outflow (b) not formed; Plume outflow (a) not stored) — not by holder
value. Holder value yield itself is 0 for all three; the net figure is 0 − dilution wherever the legs are formed.
