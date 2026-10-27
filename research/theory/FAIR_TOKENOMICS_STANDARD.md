# The Fair Tokenomics Standard

Version 0.2. Draft for Jake's sign-off, 27 October 2026. Wiston Capital.

## Purpose

The standard is a quantitative test of two things:

- how fairly a token shares the value its network creates with the people who hold and use it;
- whether that is improving.

It runs on the tracker's measured data.

It sits beside LITMUS, which is the qualitative test. The two are separate and distinct:

- LITMUS judges in words what numbers cannot carry: team, security design, regulation, innovation and character.
- The standard judges what can be measured.

Both report on the same band scale, so a band means the same position size in either test. Where they disagree, the disagreement is a finding.

Scope:

- the tracker's portfolio projects, ranked within their primary archetype;
- watchlist names as they are wired.

The standard scores only what the tracker measures from primary sources (SPEC §1 rules 12–13). A figure the tracker cannot support is reported as not scored. It is never estimated.

---

## 1. Principles

Each principle names the decision it rests on (SPEC §1b and §11).

1. **The present, plus a realistic trajectory.**
   - Every scored measure combines its current level with its direction over the last four quarters (T-h, T-n).
   - Business growth that does not yet reach the token is tracked, not discounted, because tokenomics can change.
2. **Tokens are commodities.**
   - Supply and demand are compared in tokens (T-b).
   - Dollars sit beside every token figure. They are the fair comparison of size across projects.
   - Business growth is read at constant prices wherever the free data allows, so that a rising token price cannot pass for growth.
3. **Only delivered value is credited.**
   - Fairness credits what holders actually received: live holder value and the record of sharing.
   - A paused or announced policy, and the most a mechanism could pay, measure potential. They sit on the value leaderboard only (§11 principle 4: a dollar of buyback is not a dollar of value unless it is credibly permanent).
4. **Retirement is burned or held.**
   - Buybacks paid to stakers are staking yield (T-d).
   - Burn, hold and distribute are tracked side by side and never ranked against each other (T-g).
5. **Pay the most important external user.**
   - A low take that leaves more for suppliers and users is not a markdown (§11 principle 1).
   - Retention is judged by where it goes: reinvested in growth, or captured by insiders.
6. **Emissions should buy attributable revenue.** Incentives are judged by whether the activity they fund pays for them (T-j, §11 principle 9).
7. **Locking is support.**
   - Lock terms carry no penalty, and insider allocations are judged by how they are used, not by their existence (T-i).
   - The single scored exception is financial theatre: insiders sending supply to exchanges while a buyback makes the picture look good.
8. **Supply events are calendar entries.** A known, dated unlock is a supply event to size and time around, not a reason to rule a project out (T-s).
9. **Incidents count against a project slightly; the response counts for a lot.** Speed, who bore the loss and what changed are recorded and credited (T-o).
10. **Pauses are judged by intent.** A sensible pause after an outside shock is not marked down much. A failed or unexplained one is (T-p).
11. **Centralisation is a concern only when it holds value back from holders** (T-r).
12. **One date and one basis.** Every cross-project comparison uses:
    - figures from one as-of date;
    - one stated basis per column;
    - peers recomputed together, never copied from separate notes.

---

## 2. Two leaderboards

| Leaderboard | Question | Reads from |
|---|---|---|
| **Fairness** | How fairly does the token share what the network earns, and is that improving? | The eight pillars (§3). Live holder value yield. Record of sharing. Capture state. |
| **Value** | What could the token's capture be worth at today's business? | Policy yield and switch-on ceiling (§4), beside market cap, FDV and the archetype's valuation inputs (NRR, free float ÷ ARR, retirement rates, burn yield) |

- Both rank within the primary archetype; secondary mechanisms add components (T-c).
- A project appears once on each board.
- Until the fairness score is calibrated (§9), the Summary ranks within each archetype by that archetype's crossover ratio: the existing presentation key.

---

## 3. Pillars of the fairness score

The score is built from the LITMUS rows the tracker can measure. Each pillar keeps that row's LITMUS weight, and the eight are rescaled to 100.

The rows that stay in words (§10) are not imported. The two tests remain independent.

| Pillar | LITMUS row (weight) | Weight in the standard | Measures (tracker tab · column) | Kind |
|---|---|---|---|---|
| **P1 Supply discipline** | Tokenomics (15) | 31.3 | Master · Net supply change, annualised % of circulating · CP · TOTAL DILUTION = outflows a+b+c+d+e ÷ tradeable float · CP · Discretionary supply ÷ circulating · CP · Insider share of staked / locked supply · financial-theatre flag (§8) | Grounded |
| **P2 Holder value** | Value extraction (7) | 14.6 | CP · HOLDER VALUE YIELD · CP · NET HOLDER YIELD · record of sharing (§6) · capture state (§5) | Grounded |
| **P3 Take and payout** | Fees (4) | 8.3 | CP · Payout ratio — gross · CP · Payout ratio — the project's own net basis · CP · Supplier payout per $ of fees | Grounded |
| **P4 Business trajectory** | Growth (4) | 8.3 | CP · BUSINESS 1–4 trajectories, at constant prices where available, relative to the sector (§7) | Forward |
| **P5 Market position** | Competition (5) | 10.4 | CP · MARKET SHARE vs its category, and rank movement | Forward |
| **P6 Subsidy efficiency** | Ecosystem (5) | 10.4 | CP · SUBSIDY COVER · CP · Subsidy targeting — fee cover of the emitted pools · CP · LEAKAGE (ii) | Forward |
| **P7 Holding and uniformity** | Utility (4) | 8.3 | CP · Holding demand — lock rate · CP · UNIFORMITY · CP · Non-stakers receive value | Mixed |
| **P8 Economic governance** | Governance (4) | 8.3 | Switch power (capture-status attribute) · vote concentration · greed flag (§8) | Mixed |

**Scoring a measure.** Each measure scores 0 to 1 from two parts:
- **Level:** the Q0 value placed within the archetype's peer set. Yields are also placed against the 3-month T-bill.
- **Trajectory:** the 4Q direction and growth, on the Trajectory tab's method for that row (SPEC §3).

The blend between level and trajectory depends on the pillar's kind:

| Kind | Level | Trajectory |
|---|---|---|
| Grounded (P1–P3) | 70% | 30% |
| Forward (P4–P6) | 30% | 70% |
| Mixed (P7–P8) | 50% | 50% |

The blend sets how much "today" and "where it is heading" each count (principle 1). These splits are starting values and are set at calibration.

**Missing measures.** A pillar averages the measures it has. A pillar with none is reported as not scored, and the total rescales over the pillars present. Every score is published with its coverage: the share of the 100 weight actually measured.

**A modelled leg.** A measure resting on a modelled input is shown but not scored (SPEC §1 rule 11).

---

## 4. The three yields

| Yield | Definition | Leaderboard |
|---|---|---|
| **Live holder value yield** | Revenue-funded $ retired + $ distributed + liquid bribes to voters, annualised, ÷ market cap (CP · HOLDER VALUE YIELD). Treasury- or emissions-funded buybacks are not credited. | Fairness (P2) |
| **Policy yield** | Today's revenue run-rate × the most recent executed policy, on that policy's own share and base, ÷ market cap. A policy that was announced but never specified or voted has no policy yield; it appears only as evidence on the capture state. | Value |
| **Switch-on ceiling** | The base the mechanism takes from (fees or revenue) × the maximum the mechanism allows, ÷ market cap. Beside it, the value implied at switch-on against FDV. | Value |

The base matters. Policy yield applies the policy as it was written, not a policy applied to everything.

Worked cases, figures as at 26–27 October 2026 (to be recomputed together on the calibration date, §9):

**Fluid**
- Live: 0 (tracker, VERIFIED).
- Policy: the executed buyback took 100% of Ethereum revenue (October 2025 to March 2026). Its policy yield is Ethereum revenue ÷ market cap, which is below the all-revenue figure of about 4.7%.
- The grant ($350k a month) is a cost to the DAO, set against revenue in the value flow statement, not against the policy.

**Aave**
- Live: 0 since 19 April 2026.
- Policy: the last executed policy was a fixed budget of $30m a year (Snapshot closed 15 March 2026). That gives min($30m, revenue) ÷ $2.08bn = **1.4%**.
- Aavenomics 3.0 has no specification or vote, so it carries no policy yield.

**Morpho**
- Live: 0. Policy: none.
- Ceiling: the per-market fee is capped at 25% of borrower interest (`MAX_FEE = 0.25e18`, Morpho Blue ConstantsLib). At about $276m of annualised borrower fees, that is about $69m a year, or about 4.7% of market cap.
- The value implied at full switch-on is below the $2.1bn FDV.

**Pyth**
- Live equals policy: OP-PIP-136 sends 100% of the DAO's share of revenue to PYTH purchases.
- On September's DAO share of $553,693, that is about $6.6m a year, or about 1.1% of a $577m market cap.

---

## 5. Capture states

Every project carries one capture state with a dated evidence line and a source.

| State | Definition | Evidence required | Effect on the fairness score |
|---|---|---|---|
| **LIVE** | Revenue reaches holders under a documented mechanism | The mechanism's primary source, plus the measured flow | None. Live yield carries it. |
| **PAUSED — restart signalled** | Paused after an outside shock; a restart has been stated but not yet specified or voted | The pause's primary source, plus the restart statement | Small. The record of sharing carries the credit. |
| **PAUSED — under review** | Paused for a sensible cause; no restart signal yet | The pause's primary source and its stated rationale | Small. The record of sharing carries the credit. |
| **PAUSED — failed or unexplained** | Paused with no reason given, or because the programme failed at its own aim | The pause's primary source | Marked down |
| **LATENT — route defined** | A mechanism exists, switched off, with a named recipient | Contract or governance source naming the recipient | None beyond zero live yield. The ceiling shows on the value board. |
| **LATENT — no route** | A mechanism exists, switched off, with no recipient set or with a stated blocker | Contract state, plus the blocker's source | None beyond zero live yield. Recorded as a blocker. |
| **NONE** | No revenue-to-token mechanism exists | Positive evidence: docs, code or governance | None. Live yield is 0. |

A project with protocol revenue and no holder value reads **0**, with its state beside it. It is never *n/a* (SPEC §1 rule 3).

First assignments, with evidence:

| Project | State | Evidence |
|---|---|---|
| Fluid | PAUSED — under review | Post-mortem, 11 May 2026 (gov.fluid.io/t/1774): "4.1 Pause Buybacks — Immediate halt to all token buybacks. Rationale: Prior buybacks (~1.3% of supply) showed limited effectiveness; Treasury must be preserved and rebuilt." No restart language. The rationale is kept as evidence. Price support is not the standard's test of a buyback. |
| Aave | PAUSED — restart signalled | Pause effective 19 April 2026, ratified by Snapshot 1 May 2026 (governance.aave.com/t/24686). Restart statement: Stani Kulechov, 25 June 2026, quoted on the forum (t/24936): "new Aavenomics 3.0 will have immutable and automated buybacks of AAVE". AIPs 439 and 486 describe stkAAVE being "repurposed into a no-risk staking (dividend yield) contract". No specification or vote. |
| Morpho | LATENT — no route | Morpho Blue `feeRecipient()` = 0x0 (read 12 October 2026). Fee cap 25% (`MAX_FEE`). |
| Pyth | LIVE | OP-PIP-136, passed 24 September 2026 |
| Uniswap, Pendle | LIVE | phase2_attributes (proposal 93; sPENDLE docs) |

The remaining projects map from their recorded attribute (Live, Halted, Latent) at the first scoring pass. Each "Halted" is assigned one of the three PAUSED states with its evidence.

---

## 6. Record of sharing

One number: **cumulative $ returned to holders ÷ cumulative protocol revenue**, from the first day the protocol earned revenue.

- "Returned" uses the live-yield basis: revenue-funded retired plus distributed. Treasury- and emissions-funded buybacks are not credited.
- It measures only what happened. It never measures what was promised.
- It is the fairness credit a paused project keeps (§5).

The start threshold and whether the share rose as the project grew stay in LITMUS words.

---

## 7. Market context

The business rows are read against the business's own sector, so that a cyclical fall shared by the whole sector does not read as decline.

- **Constant-price growth.** Where free token quantities exist, protocol deposits and TVL growth are valued at fixed prices. DefiLlama protocol pages carry daily per-token amounts; Aave V3, for example, from 14 March 2022.
  - Chains have no free per-token data. They stay in dollars, with a price-effect line beside them: the part of the change explained by the native token's price.
- **Sector series.** Fees and revenue for each project's category, on the peer set the market-share columns already use.
- **Relative growth.** The project's change minus the sector's, over the same window.
- **Rank movement.** The project's position in its category, now against four quarters ago.
- **Cycle note.** A dated line in words on where the sector stands.

A leader losing share towards normal mature-market levels while its sector-relative revenue holds is shown as normalising, not as decline.

---

## 8. Event layers

These are dated attributes, kept beside the score.

| Layer | Fields | Use |
|---|---|---|
| **Unlock calendar** | Next dated unlock; size as % of tradeable float; date; source; measured price behaviour after past unlocks (30-day change from the close before each event) | Sizing and timing only; never in the fairness score |
| **Incident log** | Date; loss; who bore it; time to contain; what changed; whether sharing resumed; primary source | A small fixed negative per event in P1/P2, and credit for the response |
| **Governance** | Switch power; top-five share of votes on recent proposals; greed flag (a dated vote that held value back from holders); primary source | P8 |
| **Team funding** | Insider and foundation tokens sent out ÷ holder value returned, same window | Attribute only. The dilution itself is already scored through total dilution and net holder yield. |
| **Financial-theatre flag** | Raised when issuer-side or insider wallets send tokens to exchanges in a window in which the project retires tokens by buyback; size as % of retired tokens | Scored in P1: the one scored exception to principle 7 |

---

## 9. Bands and calibration

| Band | Score | Position meaning |
|---|---|---|
| A | top band (edge set at calibration; 75 on the LITMUS sheets) | Happy to hold a large position |
| B | 70 to the A edge | Roughly 2–3% positions |
| C | 65–70 | Watch list |
| D | 60–65 | Almost the ignore list; a held position scoring D would be shocking; not shorted while still building |
| E | 55–60 | Short, sized by confidence |
| F | below 55 | Short, sized by confidence |

Bands rest on the full pillar score, never on live yield alone. An absolute live-yield band would place almost every name in the book at C or below. That would contradict principle 1 and the band-to-size mapping.

**Calibration.** The standard's score is computed for every project with a trusted LITMUS score, on one as-of date, with peers recomputed together.

- Where the two tests land in different bands, the project's measures are listed with the driving ones named.
- The comparison then says which side is missing something:
  - the numbers miss what the words caught; or
  - the words flatter what the numbers show.
- Starting set: Pyth 72.75 (B), Fluid 71.35 (B), Aave 69.8 (C), Morpho 69.25 (C), Sky 75.4 (B).
- Band edges and the level/trajectory splits (§3) are set from this comparison.
- Price performance against score is tested afterwards, on the same dates.

---

## 10. What the standard does not score

These are LITMUS judgements; the tracker supplies inputs where it has them:

- team quality;
- security design;
- regulation;
- innovation and roadmap;
- addressable market;
- scalability and speed;
- decentralisation beyond switch power;
- funding history;
- development and grants;
- ESG;
- character overall;
- governance execution: what governance has actually shipped.

Portfolio rules sit outside the standard. One example: PYTH is held as a diversifier to LINK, and never in a larger position than LINK.
