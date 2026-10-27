# SPEC theory additions: the LITMUS lens (27 October 2026)

This is the working file for Jake's sign-off. Nothing here is for Claude Code yet.

- Section 1 is ready to paste into SPEC §1b.
- Section 2 is written in §12 style. Jake's decisions of 2026-10-27 are recorded under each item.
- The companion document is `FAIR_TOKENOMICS_STANDARD.md`.

Wiring status is from Jake's build of **2026-10-27 08:53 UTC** (`coverage_audit.csv`, `scoreboard.json`).

---

## 1. New SPEC §1b rows

| # | Decision (Jake) | Date | Already partly covered | Implies (column / label) |
|---|---|---|---|---|
| T-n | Score the present with realistic growth and trajectory on top. Forward-looking: growth, addressable market, innovation, competition, ecosystem. Grounded in what is live: security and tokenomics, still crediting credible ability and intent to improve (Aave, Morpho). Tokens as "liquid VC": market cap read against stage. Shorthand only. | 2026-10-26 | T-h; §3 trajectory methods | Every scored measure blends level (Q0) and trajectory (Standard §3: grounded 70/30, forward 30/70, mixed 50/50, set at calibration). Intent to improve is a capture-state evidence line, never a discount. |
| T-o | An exploit or failure counts against a project slightly; the quality of the response counts for a lot (speed, who bore the loss, what changed). Fluid's and Aave's 2026 exploits weigh roughly the same. | 2026-10-26 | — | Incident log (P-H): a small fixed negative per event, plus response credit |
| T-p | Paused buybacks are judged by intent. **Counts against:** no explanation, or paused because it failed (Helium-type). **Not marked down much:** a sensible, sustainable pause after something outside the project's control, with intent to restart. A clear restart sign (a vote or a stated share) is still wanted. When the kind of pause is unclear, ask Jake. Aave is not marked down for Aavenomics 3.0 not being out yet. | 2026-10-26 | §1 rule 3 (Fluid holder value 0 after 2026-05-11); CP · CAPTURE STATUS (Live / Halted / Latent) | Capture states (P-A) |
| T-q | Weigh fees and revenue together: fees show usage, revenue shows capture. Early protocols may keep more to grow and share more later (Maple raised its buybacks as it grew). A low take by design is not a markdown (§11 principle 1). Judge the team's philosophy on paying users and holders. Read market context: cyclicality, sector-wide fees, share and rank. A leader's share falling towards mature-market levels is not necessarily bad (Aave). Reference shares (Pyth 60%, GEODNET 80%, Aerodrome 100%) are context, not targets. | 2026-10-26 | §11 principle 1; CP payout ratios; CP market share | Payout read against stage (P-D); market-context layer (P-E) |
| T-r | Centralisation is fine for speed and for a lab controlling code and design. A good sign is revenue and fee-sharing decisions left to the community. The red flag is greed: decisions that hold value back from holders (Solana's fee-burn proposal voted down by stakers). Design choices Jake cannot judge technically are not marked down until examined or discussed with the team (Pyth's 3-of-5 router keys). | 2026-10-26 | capture_status `switch_power` | Greed flag (P-I). Design choices stay in LITMUS. |
| T-s | A known, dated unlock is a supply event to trade around, not a reason to rule a project out. The fairness test is the use of released tokens: sustainable use for the project, or corporate greed. Undisclosed holders or opaque tranches are a slight markdown only. Watch for financial theatre. | 2026-10-26 | T-i; §6 (e) unlocks; CP insider share; CP discretionary supply | Unlock calendar (P-G); financial-theatre flag (P-F). Opacity is LITMUS-only. |
| T-t | Back clever teams and good founders. Governance gets credit for execution, not only structure (Morpho's governance score raised for this). | 2026-10-26 | — | None. Stays in LITMUS (Standard §10). |
| T-u | PYTH is a diversifier to LINK; never hold more PYTH than LINK. A known unlock (Pyth 2027-05-19) can be traded around. | 2026-10-26 | — | None. A portfolio rule outside the standard, recorded so it is not read as a score input. |
| T-v | Bands are position sizes: A = large position; B = roughly 2–3%; C = watch list; D = almost ignore, a held D would be shocking, not shorted while building; E and F = shorts sized by confidence. Any tracker scoring is calibrated so its bands mean the same. Sky's 75.4 is a B (Jake 2026-10-27); the exact A/B edge is set at calibration. | 2026-10-26 / 2026-10-27 | config LITMUS labels (T-m) | Standard §9 band table |
| T-w | The tracker and LITMUS are two separate and distinct tests: the tracker quantitative, LITMUS qualitative. The tracker's score uses the LITMUS row weights for the rows it measures, rescaled to 100, and does not import LITMUS word scores. | 2026-10-27 | — | Standard §3. A disagreement between the two tests is reported as a finding. |
| T-x | Two leaderboards, fairness and value. Policy yield and switch-on ceiling sit on the value leaderboard only. | 2026-10-27 | T-c (primary archetype decides the leaderboard) | Standard §2 and §4 |

---

## 2. Proposals (§12 style)

**P-A. Capture states with intent.** The three recorded values (Live, Halted, Latent) become seven states, each with a dated evidence line and a source:
- LIVE;
- PAUSED — restart signalled;
- PAUSED — under review;
- PAUSED — failed or unexplained;
- LATENT — route defined;
- LATENT — no route;
- NONE.

The two sensible PAUSED states are not marked down much (T-p); the failed one is. Definitions and first assignments are in Standard §5.
- *Jake 2026-10-27:* **Fluid = PAUSED — under review.** The post-mortem's own rationale ("Prior buybacks (~1.3% of supply) showed limited effectiveness; Treasury must be preserved and rebuilt", gov.fluid.io/t/1774, 11 May 2026) is kept as the evidence line.
- The other states and assignments are PROPOSED.

**P-B. Three yields.**
- Live holder value yield, as built.
- Policy yield: the most recent executed policy, on its own share and base.
- Switch-on ceiling: the mechanism's maximum, with the value implied at switch-on against FDV.
- *Jake 2026-10-27:* **ACCEPTED.** Policy yield and ceiling sit on the value leaderboard only; fairness credits live yield and the record of sharing.
- PROPOSED: an announced but unspecified policy (Aavenomics 3.0) has no policy yield. It is a capture-state evidence line.

**P-C. Record of sharing.**
- *Jake 2026-10-27:* **ACCEPTED as one number**, "only if it doesn't overcomplicate things": cumulative revenue-funded $ returned to holders ÷ cumulative protocol revenue, from the first revenue day. It is compatible with T-l because it measures delivery only.
- The start threshold and the rising-share test stay in LITMUS words.
- It is the fairness credit a paused project keeps (*Jake 2026-10-27*, answering Part 4 question 1: "Record of sharing").

**P-D. Payout ratio by stage.**
- Payout ratio is read with supplier payout beside it; a low take that pays the main external user is not marked down.
- The value flow statement splits "treasury (the rest)" into treasury and insider-controlled lines. The insider line needs the issuer wallet lists, which are MISSING E today.
- PROPOSED.

**P-E. Market context.**
- *Jake 2026-10-27:* **constant-price growth for protocols**, from free DefiLlama token quantities. Chains stay in USD with a price-effect line.
- PROPOSED:
  - sector fee and revenue series on the market-share peer sets;
  - relative growth (project minus sector, same window);
  - rank movement against four quarters ago;
  - a dated cycle note in words.

**P-F. Who funds the team.**
- *Jake 2026-10-27:* the ratio (insider and foundation tokens out ÷ holder value returned) is an **attribute**. A **financial-theatre flag** is **scored**: issuer-side or insider sends to exchanges in a window where buyback tokens are retired.
- This amends T-i with a single exception.
- Blocker: identifying exchange deposit addresses is a §10 NAMED BLOCKER (paid labels). Free routes to test:
  - exchange-published proof-of-reserve addresses;
  - Open Labels via Blockscout (coverage unverified).

**P-G. Unlock calendar.**
- The next dated unlock as % of tradeable float, with its date and source.
- Measured 30-day price change after past unlocks, from the close before each event. Example: PYTH −32%, −36% and −19% after the 2024–2026 cliffs, per Jake's LITMUS; to be recomputed on the calibration date.
- Feeds sizing, not the fairness score. PROPOSED.

**P-H. Incident log.**
- Fields: date, loss, who bore it, time to contain, what changed, whether sharing resumed, primary source.
- A small fixed negative, plus response credit. PROPOSED.

**P-I. Governance.**
- Switch power (built).
- Top-five vote share on recent proposals.
- A dated greed flag.
- Execution credit stays in LITMUS. PROPOSED.

**P-J. What stays in words.** Standard §10. PROPOSED.

**P-K. Composite and calibration.**
- *Jake 2026-10-27:* **the tracker scores the LITMUS rows it measures on their LITMUS weights, rescaled to 100, as a separate test** (T-w).
- PROPOSED:
  - measure scoring by level within the archetype peer set plus trajectory (Standard §3);
  - coverage published with every score;
  - calibration as in section 4 below.

**P-L. Data discipline.**
- For every cross-project comparison: one as-of date; one stated basis per column; peers recomputed together.
- The basis goes in a units/basis line under the header and in the header comment. That keeps P1's 35-character header rule.
- PROPOSED.

**P-M. Zero, not n/a, for unshared revenue.** A project with protocol revenue and no holder value reads 0 with its capture state, never n/a.
- Today Morpho reads "N/A BY DESIGN — no buyback programme" on CP · HOLDER VALUE YIELD, although its fee mechanism exists and is switched off (LATENT).
- Aethir and Plume also read N/A BY DESIGN. Each needs its capture state set from evidence (NONE needs positive evidence; SPEC §1 rule 13).
- PROPOSED.

---

## 3. LITMUS rows → tracker concepts, against today's wiring

Counts are across the 16 tracked projects ("with a value" = VERIFIED, VERIFIED (derived) or LIVE-UNVERIFIED).

| LITMUS row (weight) | Tracker concepts | Wired today | Not built |
|---|---|---|---|
| Tokenomics (15) | Net supply change %; total dilution ÷ float; discretionary supply ÷ circulating; insider share of stake | Net supply change 12 with a value, 4 N/A. Total dilution 13 with a value, 3 N/A. Discretionary supply 11 with a value, 1 BROKEN (Chainlink), 4 N/A. Insider share 4 with a value, 9 MISSING E. | Unlock calendar (only A2 · PUBLISHED UNLOCK SCHEDULE, 1 of 5 A2 projects); financial-theatre flag |
| Value extraction (7) | HVY; NHY; capture state | HVY and NHY: 11 with a value (10 VERIFIED), 4 N/A, 1 MISSING (World Mobile). Capture state: 16 attributes, three values only | Record of sharing; seven capture states; policy yield and ceiling (value board) |
| Fees (4) | Payout gross and net; supplier payout | Payout ratios 11 with a value; supplier payout 15 | Payout-by-stage reading (words) |
| Growth (4) | BUSINESS 1–4 with trajectories | BUSINESS 1: 15 VERIFIED | Constant-price basis; sector-relative growth |
| Competition (5) | Market share vs category | 12 VERIFIED, 4 N/A (LINK, ATH and GEOD named blockers) | Rank movement |
| Ecosystem (5) | Subsidy cover; subsidy targeting; leakage (ii) | Subsidy cover 7 with a value, 9 N/A. Subsidy targeting 1 with a value plus Pendle's first epoch closing 2026-11-03. Leakage (ii) 7. | — |
| Utility (4) | Lock rate; uniformity; non-stakers receive value | Lock rate 11. Uniformity 7 with a value plus 2 MODELLED. Non-stakers 12. | — |
| Governance (4) | Switch power | In the capture-status attribute (16) | Vote concentration; greed flag |
| Source of capital (5) | Who funds the team | — | Attribute only (P-F) |
| Decentralisation (5) | — | — | Stays in words |
| Team, Innovation, Scalability, Security, Speed, Addressable market, Regulation, Dev/Grants, ESG (42) | — | — | Stay in words |

The measured rows carry **48 of the 100 LITMUS points**, not "well over half". The draft table reached 53 only by counting Decentralisation, which has no tracker concept. The standard rescales the 48 to 100.

---

## 4. Calibration plan

**Names and trusted scores:**

| Name | Score | Band |
|---|---|---|
| Pyth | 72.75 | B |
| Fluid | 71.35 | B |
| Aave | 69.8 | C |
| Morpho | 69.25 | C |
| Sky | 75.4 | B |

Further names join as fresh LITMUS re-scores arrive.

**Date.** One as-of date for everything: the tracker build of **2026-11-02 (UTC)**.
- Pyth and Aave figures are read by hand the same day from the routes in `research/pyth/sources.csv` and `research/aave/sources.csv`.
- The LITMUS figures that feed the comparison (market caps, revenue run-rates, holder-revenue peers) are recomputed for that date, never carried from 19 or 26 October.

**Figures per name:**
- every measure in pillars P1–P8;
- the three yields;
- the capture state with its evidence line;
- the record of sharing;
- each pillar's coverage.

**Method.**
1. Compute the standard's score and band.
2. For each name, set the two tests side by side.
3. Where the bands differ, or the scores differ by more than 3 points, list the measures driving the gap. Say which side misses what: the numbers missing something the words caught, or the words flattering something the numbers show.
4. Set the A/B edge and the level/trajectory splits from the result.
5. Test score against price performance on the same dates afterwards.

### New data the standard needs, in order

**Order of rounds:**
1. D4, in progress.
2. P1 Stage 1.
3. Pyth stock reads.
4. D5.
5. P1 Stage 2.
6. **T1**: derived inputs; nothing new is fetched.
7. **T2**: new series.
8. Calibration on 2026-11-02, or the first clean build after T1 if later.
9. Full Pyth and Aave wiring, once the 16 projects are signed off.

| Item | Free route | Round |
|---|---|---|
| Pyth stock reads (supply, Treasury, Council vault, open DCA escrow, governance and OIS stake, unvested, Douro and funding wallets) | Public Solana RPC, daily; no free history for most | Right after P1 Stage 1: urgent |
| Capture states and evidence lines | phase2_attributes plus primary sources (forum posts, contract reads) | T1 |
| Zero-not-n/a for unshared revenue (P-M) | Existing series | T1 |
| Policy yield | config fee_split windows (share and base) × revenue run-rate | T1 |
| Switch-on ceiling | Mechanism maximum in config with its source (Morpho `MAX_FEE` 25%) | T1 |
| Record of sharing | Store's retired and distributed $ plus DefiLlama full revenue history | T1, with the earliest-history gaps listed per project |
| Sector fee and revenue series | DefiLlama fees overview by category, on the existing market-share peer sets | T2 |
| Constant-price TVL (protocols) | DefiLlama `/protocol/{child}` daily token quantities (aave-v3 from 2022-03-14, checked) | T2 |
| Price-effect line (chains) | Chain TVL USD and native price, already stored | T2 |
| Unlock calendar | Projects' published schedules plus on-chain vesting (Pyth: 379 genesis vesting accounts) | T2 |
| Past-unlock price behaviour | Daily closes from Binance's public klines endpoint; CoinGecko's public API stops at 365 days | T2 |
| Incident log | Projects' post-mortems (primary), entered by hand with dates | T2 |
| Vote concentration | Snapshot GraphQL (free) | T2 |
| Greed flags | By hand, from governance records | T2 |
| Financial-theatre flag | Existing outflow scans plus exchange addresses: proof-of-reserve lists or Open Labels (to test) | T2, partly blocked (P-F) |
| Insider wallet lists (unblocks P1 insider share, P-D's insider line, leakage (i)) | Project teams (Jake's outreach) | Whenever they arrive |
| Pyth and Aave full wiring | The two dossiers | After the 16 are signed off |

---

## 5. Conflicts: both versions side by side

| # | Version A | Version B | Status |
|---|---|---|---|
| 1 | Theory note B: policy yield "Fluid about 4.7% (2.4% after the grant). Aave 2.9–3.6%." | These apply 100% of **all** revenue. Fluid's executed buyback took 100% of **Ethereum** revenue (Jake's Fluid LITMUS); Aave's last executed policy was a **fixed $30m a year** (Snapshot closed 2026-03-15). | Standard §4 uses the executed share and base: Aave 1.4%; Fluid to compute from Ethereum revenue. Fluid's Ethereum-only base to be confirmed against its IGP at calibration. |
| 2 | Theory note J: the mapped rows "carry well over half the LITMUS weight" | Measured rows total 48; 53 only with Decentralisation, which has no tracker concept | Section 3 |
| 3 | SPEC §10: "Chains' TVL in native units — NOT BUILT BY DESIGN … USD ÷ the native price only … would restate price" | Theory note (Aave): growth "has to be read in native units (T-b and tvl_native)" | Jake 2026-10-27: constant-price for protocols (free token quantities exist); chains keep the SPEC §10 ruling plus a price-effect line. The §10 row needs a protocol carve-out. |
| 4 | Theory note B: "the two-leaderboard idea agreed on 12 Oct" | Not found in SPEC, in this chat, or in a search of past chats | Recorded as T-x, dated 2026-10-27 |
| 5 | T-i: insider rows "never a penalty" | P-F: financial-theatre flag scored | Jake 2026-10-27: the single scored exception; T-i to be amended |
| 6 | SPEC §1 rule 3: "0 IS NOT n/a" | CP · HOLDER VALUE YIELD reads N/A BY DESIGN for Morpho (and Aethir, Plume) | P-M |
| 7 | Theory note K and Jake's LITMUS list: "Sky 75.4 (B)" | LITMUS sheet band key: "75+ \| A" | Jake 2026-10-27: Sky is B; edge set at calibration |
| 8 | P1 presentation rule: headers ≤ 35 characters | Theory note L: "the basis in the column header" | P-L: basis on a units/basis line and in the header comment |
| 9 | Theory note and Pyth LITMUS: DAO receives 60% of "Pro, Indices and Marketplace" | Primary sources: 60/40 for Pro (CO-PIP-9, CO-PIP-104) and Marketplace (CO-PIP-99); 90/10 for LaaS (OP-PIP-98). **Indices 60/40 appears only in Douro's monthly reports**; no governance vote sets it. | Correct the LITMUS wording. Put the Indices split to the DAO or Douro. |
| 10 | Pyth LITMUS: publishers receive "about 2.7m PYTH a month"; revenue-funded buying "runs at nearly three times publisher issuance" | Pyth dossier, on-chain: the 11 Sep 2026 publisher run paid 5,633,811 PYTH. Douro → distribution-wallet transfers ran 4.6–5.6m a month from July to September. September's DAO share ($553,693 ≈ 7.6m PYTH at $0.0732) is about **1.3–1.6×** publisher issuance, not ~3×. | Material to Pyth's tokenomics row: re-score |
| 11 | Pyth LITMUS: final unlock 2.125bn | On-chain genesis vesting: 2,154,583,334 PYTH at 2027-05-19 14:00 UTC; the gap from the docs is unexplained | Use the on-chain figure, with the gap noted |
| 12 | Pyth LITMUS: Strategic Reserve buying described as revenue-funded open-market buying | April–July 2026 DAO shares (24.17m PYTH) were delivered **in kind** from Douro's paying wallet, funded from a wallet that had withdrawn vested PYTH: existing holdings, not purchases. Open-market fills: 20.55m (V1) | Decision for Jake: does in-kind PYTH count as retirement? It is revenue owed, paid in tokens, with no buying pressure. |
| 13 | Theory note and Aave LITMUS: "bought 250,368 AAVE for $45.4m" (TokenLogic) | Aave's own funding post (28 Feb 2026): "$42M has been allocated to acquiring 205,000 AAVE over the 10-month period". On-chain about 236.8k across the Finance Committee and Collector at the pause. TokenLogic's dashboard is behind a login. | Use primary and on-chain figures |
| 14 | Aave LITMUS: "The DAO controls about 901k AAVE" | Verified on-chain: 868,722 (Ecosystem Reserve + Finance Committee + Collector + liquidity Safe) | Use 868.7k; the 32k gap is unexplained |
| 15 | Theory note (Fluid): post-mortem "reportedly" judged buybacks ineffective | Primary, gov.fluid.io/t/1774 (11 May 2026): "Rationale: Prior buybacks (~1.3% of supply) showed limited effectiveness; Treasury must be preserved and rebuilt". No restart language. | Confirmed. Jake 2026-10-27: PAUSED — under review, with this as evidence. |
| 16 | P1 Summary: rank within archetype by the crossover ratio | Standard §2: the fairness leaderboard ranks by the pillar score | The crossover key stands until calibration, then the Summary takes the fairness rank |
