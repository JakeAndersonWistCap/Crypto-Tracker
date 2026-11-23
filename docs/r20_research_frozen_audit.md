# Round 20, Part 5e / 5f — the nine frozen projects: how each token works today against our config

Research only. No code, config or test was changed. Every source was read on **2026-11-23** unless a row says otherwise.
"Config" means `config.py` at the working tree's head. "Fixture" means the frozen baseline cells in
`tests/fixtures/frozen_<project>.json` (the 15-dump build, so prices and stocks there are several weeks old). Figures
worked out here from two sourced numbers are marked INFERRED.

## Answers first

1. **Pendle — confirmed, with four mismatches.** sPENDLE went live 2026-01-20 and the algorithmic incentive model
   started 2026-01-29. 80% of yield and swap fees buy PENDLE; the bought PENDLE is paid to sPENDLE holders **as sPENDLE**
   (already staked). 22 epochs, 3,301,920 PENDLE bought to date. Emissions are now formula-driven with no weekly budget;
   they run at about 0.1% of supply a year, not the 2% our config still declares as the rate.
2. **Maple — confirmed, with one date and one history error.** MIP-021's vote ran 13–17 July 2026 (13 July is the vote
   opening, not the first buyback). It passed 61.30M to 0.02M. Bought SYRUP "will live within the SSF": held, not burned,
   not distributed. **The framework applies for six months only**; our config has no end date. Config also records the
   earlier MIP-019 period as "a flat 25% to buybacks". That is wrong: 25% went to the SSF and the buyback part was
   discretionary.
3. **Sky — the 7.37M burn was 12 October, not November.** Burns on chain: 2,860,943.76 SKY (2026-09-13), 7,372,288 SKY
   (2026-10-12), and a third, 7,770,211 SKY, today (2026-11-23). The emergency buyback is separate: announced
   2026-11-19, 750,000 USDS a day for 7 days (5.25M USDS), executed on chain the same evening, to be burned. It changes
   the Splitter's burn setting from 55% to 90.81% for the week. Our config's fixed split (18.2% burned / 81.8% to
   stakers) is wrong for those seven days.
4. **Fluid — paused, confirmed; no restart condition exists.** The governance post of 2026-05-11 is headed "Pause
   Buybacks" and says "Immediate halt to all token buybacks". It gives no date or condition to resume. Fluid's own API
   still shows 125 executions, the last on 2026-03-20, and $0 in the last 30 days.
5. **Ether.fi — "paused" is not what the sources show.** No pause was ever announced. The treasury buyback (up to $50M
   while ETHFI is under $3) is still authorised and its price condition is met ($0.51), but it has bought nothing since
   2026-04-01. The newer programmatic buyback **is buying today** on CoW, but that ETHFI goes to card users as cashback,
   not to holders.
6. **Hyperliquid, Morpho, Uniswap, Aerodrome** — supply checks agree with config. Nothing new found on emissions,
   unlocks or staking. See `docs/r20_research_hl_uni_morpho.md` and `docs/r19_aerodrome_research.md`.
7. **Eight rule questions** follow in Part 5f. The two that move the most: whether buybacks paid to stakers count as
   buying in net float pressure (Pendle, Sky), and whether Sky's staker rewards paid out of bought SKY are dilution.

---

## 1. Pendle

**What config assumes today.** Archetype A3. 80% of V2 yield and swap fees go to buybacks (`fee_split.share_to_buyback`
0.8, active, programmed). 100% of bought PENDLE is distributed to sPENDLE holders (`buyback_destination` distribute,
effect `yield_payout`; nothing burned or held). Holder revenue is Pendle's own per-epoch distribution, spread over 14
days. Emissions are the gauge payouts on nine chains, counted as incentive spend (b); gross issuance is 0 because
`totalSupply` has been flat since 2025-03-24. A declared rate of 2% a year "terminal inflation" sits beside it. Unlocks
(e) are the falls of four issuer wallets. Circulating 245,655,386; sPENDLE and vePENDLE are in circulating and off the
float. In-kind airdrops are excluded. The buyback contract address is recorded as "not found".

**How it works today.** Pendle docs (github.com/pendle-finance/documentation, master, `sPENDLE.md`, `Incentives.md`,
`Tokenomics.md`) and Pendle's API (`api-v2.pendle.finance/core/v1/spendle/data`).

| Config assumes | Primary source shows | Effect |
|---|---|---|
| Buyback distributed to stakers; treated as a yield payout that returns to the float | "Up to 100% of repurchased PENDLE will be distributed to active sPENDLE holders **in the form of sPENDLE**" (`sPENDLE.md`). Rewards arrive staked. Leaving takes 14 days or a 5% fee | Free float: the paid tokens stay off the float under Jake's rule. Last four full epochs (09-22 to 11-03): 387,700 PENDLE in 56 days, about 2.53M a year, about 1.2% of the fixture's 206.5M float (INFERRED). Net float pressure: counted or not depends on rule F2 below. Holder value yield unchanged (fixture 1.11%) |
| Declared emission rate 2% a year (`issuance_rate_declared`) | The Algorithmic Incentive Model pays each pool an absolute amount from its own liquidity, fees and depth: "there is no fixed weekly PENDLE budget". Conversion floor $1.25 caps issuance (`Incentives.md`) | Dilution: the measured gauge payouts are the right figure. Jake's 11-13 build read 65,450 PENDLE for the quarter, about 262,000 a year, about 0.1% of supply (INFERRED), against 5.6M a year at 2%. Any cell that uses the 2% rate overstates dilution about 20-fold. The 2% still accrues as mintable, unminted supply (9,837,599 PENDLE on 2026-10-13, config `LATENT_SUPPLY`): not new supply under the rule until minted |
| Notes and labels date the change "April 2026 tokenomics"; destination shares apply from the start of history (`from: None`) | sPENDLE live 2026-01-20; vePENDLE locks paused and the new incentives began 2026-01-29 00:00 UTC. April 2026 was only the old schedule's switch to the 2% tail | Labels only for the current quarter. Any buyback read before 2026-01-29 would be mislabelled (before then fees went to vePENDLE voters, not a buyback). Size: none in the last three quarters |
| Buyback contract "NOT FOUND", so tokens bought are derived ($ ÷ same-day price) | Docs now name it: `0x9e08C5499f953C6297A7755BcBcEd383b606896b`; purchases run as an hourly TWAP over the week after each two-week harvest | No effect on the nets. It makes the purchases measurable on chain (execution price and day), replacing the derived figure |
| In-kind airdrops excluded from holder revenue | Still paid in kind to sPENDLE holders. API `airdropInUSDs`: $1,703,964 across five epochs (2026-03-10 to 2026-06-02); $0 in every epoch since 2026-06-16 | Holder value yield: 0 today. Under Jake's A3 wording ("incl. in other tokens") these belong in. See rule F6 |

Confirmed, one line each: 80% share and two-week cadence; 14-day cooldown; latest full epoch (2026-11-03) 110,966
PENDLE for $153,000; staked 29,372,576 sPENDLE plus 158,271,162 virtual sPENDLE from old vePENDLE locks, so about 84% of
each distribution goes to legacy lockers (INFERRED); total supply and the five non-circulating addresses as config.

## 2. Maple

**What config assumes today.** Archetype A3. Buyback share history: none documented before October 2025; "MIP-019, a
flat 25% of net revenue" from 2025-10-01 to 2026-07-31 (status confirmed); MIP-021 tiers from 2026-08-01 with no end:
10% under $1.5M monthly revenue, 20% from $1.5M to $2M, 30% above. All bought SYRUP is held (`hold` 1.0) in the Syrup
Strategic Fund (SSF), in circulating and off the float; the stock is Maple's own SSF chart. No burn. Staking ended
November 2025, so no staking yield. Emissions 0; new supply is two treasury mints (44.4M in the last 12 months, schedule
ended) and unlocks are treasury and registry wallet falls. Whether the SSF sells SYRUP back is recorded as open.

**How it works today.** Snapshot, space `mapledao.eth`, proposal
`0xce5c0b3e759cd071a5412d7935b2408db9def2ecc4f754764f5c2f08055ff3a5` (read through `hub.snapshot.org/graphql`).
Maple's forum returned HTTP 403, so the Snapshot text is the source. Maple's article "What SYRUP Holders Actually
Hold" (maple.finance/insights, dated 2026-07-08).

| Config assumes | Primary source shows | Effect |
|---|---|---|
| MIP-021 in force from 2026-08-01; Jake's note says "from 13 Jul 2026" | Vote opened 2026-07-13 12:59 UTC, closed 2026-07-17; For 61,300,404.59, Against 20,256.90. "All SSF buybacks will be executed after each month-end by the 15th, once the revenue figure is finalised." The text names no first month | None on the current quarter. Config's July 2026 buyback of $136,768 is labelled "MIP-019 flat 25%". It looks like the 10% tier on about $1.37M of July revenue (INFERRED, not verified), so MIP-021 probably governed July's revenue and the first buyback fell by 2026-08-15 |
| MIP-021 has no end date (`to: None`) | "The framework applies for six months." | Forward-looking: the rule lapses around January–February 2027 unless renewed. Holder value yield after that is not committed. Size not established |
| Bought SYRUP held 100% | "SYRUP purchased through these buybacks will live within the SSF." The SSF's jobs: "pursuing strategic opportunities, supporting token liquidity, and holding capital reserves and buybacks." The July article: bought SYRUP is held in the Maple Treasury; "Tokens that enter the Treasury come out of circulation." | Agrees. Not burned, not paid to stakers. Free float: off when bought. The SSF's mandate lets SYRUP leave again, and config already records falls (79.2M to 74.9M, 24–29 Sept 2026). That return to the float is not measured as a named movement yet. Size not established |
| MIP-019 period: flat 25% of net revenue to buybacks, "confirmed" | MIP-021 "replaces **discretionary** buyback allocation". The July article: the SSF receives 25% of monthly revenue, which covers buybacks and reserves | History only (October 2025 to July 2026): implied buyback for those months is overstated wherever the 25% is applied. The 90-day cells are not affected (fixture: implied cells n/a; actual 90-day buyback $658,866 / 4,029,134 SYRUP; holder value yield 0.92%) |
| Tiers sourced to the insights article | The article gives no tiers. The tiers are in the Snapshot proposal | Citation only |

Confirmed, one line each: the three tiers exactly as config; buyback details published on the Transparency page; no
burn; no staking rewards. The article also gives 8M SYRUP bought in 2025 and 2.5M in 2026 to 8 July.

## 3. Sky

**What config assumes today.** Archetypes A3 and A4. Stage 2 since 2026-08-17: 50% of monthly Net Protocol Surplus,
split 22.5% SKY bought for SKY stakers, 22.5% USDS to stakers, 5% SKY bought and burned. Holder revenue is the Smart
Burn Engine's buying plus the USDS paid to stakers. Of SKY bought, 18.18% is burned and 81.82% distributed, fixed from
2026-08-17 (`BUYBACK_DESTINATION_SHARES`). Everything bought lands in the Pause Proxy first: a held buyback, in
circulating and off the float, until a spell burns it or a vest pays it to stakers. A4 counts only SKY actually burned
by spell. Staker rewards are emissions (c) in every period. Gross issuance 0. No unlocks.

**How it works today.** On chain: SKY transfers out of the Pause Proxy `0xBE8E3e36…` (eth.blockscout.com API). Sky
forum thread 28439, "Immediate one-off SKY buyback" (forum.sky.money, 2026-11-19).

| Config assumes | Primary source shows | Effect |
|---|---|---|
| The 7.37M burn is described in Jake's note as a November event | Burn of **7,372,288.00 SKY on 2026-10-12 14:01 UTC**, tx `0x93389082b75a9e278b6b2a2d7dde53618c38214b5d71699fd60461dbe1e10275`. First burn 2,860,943.76 SKY on 2026-09-13, tx `0xcd57535f…e192c3`. The fixture's 90-day burn of 10,233,231.8 is exactly these two | None: config already measures both. Date correction only |
| No burn after 2026-10-12 on file | Third burn: **7,770,211.00 SKY on 2026-11-23 14:11 UTC**, tx `0x4babaf4be2f91bdcf19e8c7213144ed0481d05ac468bb27aae24de272a644e29` | A4: 90-day burn to today is 18,003,443 SKY (INFERRED sum), up 76% on the fixture. The daily read should pick it up |
| Split of SKY bought fixed at 18.18% burned / 81.82% to stakers | One-off buyback: 750,000 USDS a day for 7 days, 5.25M USDS, on top of the regular buying. "The SKY bought will be burned." Splitter set to `hop` 550 s and `burn` 90.81% for 7 days; executed 2026-11-19 ~20:08 UTC, tx `0xebb6efefb6baca843d29052ee9005b5424d68494aff84544f732196e12e5be1c`. Regular flow before: 105,874 USDS a day to SKY, 86,625 USDS a day to stakers | For the seven days the true mix of SKY bought is about 89.9% for burning, 10.1% for stakers (INFERRED: (19,250 + 750,000) ÷ 855,925). Config would label about $4.3M of the week's buying "paid to stakers" that is meant for the burn. The total bought is unaffected |
| Holder value: buying credited as it happens | 5.25M USDS in one week against a market cap of about $944M today (23.31B × $0.0405, INFERRED) | Holder value yield: +0.56% of market cap for the quarter as a one-off; annualised inside a 90-day window it adds about 2.3 points to a fixture figure of 2.45%. See rule F5 |
| Held buyback off the float when bought | The SKY bought goes to the Pause Proxy until a spell burns it. At about $0.042 the one-off buys about 125M SKY (INFERRED; fills not read) | Free float: about −2.1% of the fixture's 5.92B float in one week. Net float pressure: a spike of the same size. A4: nothing until the burn spell, then a burn about 7× the last 90 days' total |
| Staker rewards counted as emissions (dilution) in every period | Weekly vest to the staking farm: 11.07M SKY a week from 2026-09-20, 7.70M a week since 2026-10-19 (about 400M a year; the fixture's "emissions in force" 403.6M agrees). The forum: staking rewards unchanged by the one-off | Dilution: agrees with config's measure. Whether a reward paid from bought SKY is dilution at all is rule F3 |

Confirmed, one line each: Smart Burn Engine receiver is the Pause Proxy; 55/45 regular split ("burn of 55%" set by the
October 8 spell); USDS still paid to stakers; the one-off needed no Executive Vote (Atlas A.2.3.1.5 and A.3.5.2.4,
confirmed on the thread by Core GovOps and the Core Facilitator).

## 4. Fluid

**What config assumes today.** Archetype A3. Buyback ran October 2025 to March 2026 and is HALTED from 2026-05-11:
share 0 from that date, holder revenue a measured 0 from 2026-03-21. While it ran, 100% of bought FLUID went to the
governance treasury (held; no burn, no staking). The reader shows "halted since 2026-05-11". Emissions are FLUID claimed
from the reward distributors (b), with a discretionary ceiling of 0.5% of supply a month recorded as a cap, not a rate.
Vesting ended in 2025. Config says the post-mortem "was not found on gov.fluid.io" and is on file as supplied by Jake.

| Config assumes | Primary source shows | Effect |
|---|---|---|
| Post-mortem not found on Fluid's forum | It is there: gov.fluid.io/t/1774, "Post-Mortem, Treasury Actions, and Forward Strategy Following Resolv Incident", posted 2026-05-11 by DMH. Section 4.1 "Pause Buybacks — Immediate halt to all token buybacks. Rationale: prior buybacks (~1.3% of supply) showed limited effectiveness; Treasury must be preserved and rebuilt." | Source upgrade only. Agrees with config's date, reason and 1.3% |
| Reader label "halted"; Jake's Round 20 label "paused" | The heading says "Pause"; the text says "halt". No date, threshold or condition to resume. No later forum topic restarts it (latest topics read; the newest is 2026-08-02) | Label only. "Stated intent to restart" is not evidenced. See rule F4 |
| Buyback still at 0 | `api.fluid.io/v2/fluid-token/buybacks`: 125 executions, last 2026-03-20 10:43 UTC; total $4,745,304.89 / 1,306,296.83 FLUID; last 30 days $0 | Agrees. Holder value yield 0; net holder yield = −dilution (fixture: 1.45% of float a year); net float pressure = −new supply |
| The same post "records a significant reduction in emissions" | Section 4.2: "Significant reduction / minimization of emissions" | Agrees. Dilution is small and measured (fixture: 137,930 FLUID claimed in 90 days) |

Confirmed, one line each: bought FLUID was held in the treasury, never burned; the last purchase (2026-03-20) predates
the Resolv incident (22 March) and the pause post by seven weeks.

## 5. Ether.fi

**What config assumes today.** Archetype A3 (rounds R14–R16, decided 2026-11-19/20). Two programmes. The old one bought
ETHFI for sETHFI stakers and has been dormant since its last purchase on 2026-04-01. The new "Programmatic Buybacks"
(Snapshot vote closed 2026-09-03) buys ETHFI on CoW with Labs' USDC and pays it to card users on Optimism as cashback:
not holder value (decision 5). Holder revenue is $0. The Foundation's top-ups to sETHFI stakers come from its own stock
and count as dilution (e). Cashback is dilution (c) only where purchases do not cover it. Circulating equals total
supply; issuer wallets, the unvested team leg and staked ETHFI are off the float. A theatre flag records issuer ETHFI
sent to exchange-tagged addresses. The reader shows "active (measured last 90 days)".

| Config assumes | Primary source shows | Effect |
|---|---|---|
| Jake's Round 20 label: "paused" | No pause announcement exists. The newest Snapshot proposal in `etherfi-dao.eth` is the Programmatic Buybacks vote (2026-08-30 to 09-03, 1,141,999 For, 0 Against). The treasury programme (vote 2025-10-30 to 11-03) runs "until (a) the cap is reached, (b) the Foundation deems the program complete, or (c) a subsequent governance vote". None of the three has been published | Label only. The staker-directed buying stopped without a statement. See rule F4 |
| Old programme dormant since 2026-04-01 | The $50M programme buys "when the token spot price is strictly below US $3.00". ETHFI is $0.51 today (coins.llama.fi). The old wallet `0x2f5301a3…` shows no purchase since; its last ETHFI movement was 2026-09-10 | Agrees. Authorised, condition met, not executing. Holder value yield 0 |
| Programmatic buyback active; all of it paid on to card users, none held | Still buying today: CoW fills into the Safe `0x43F4cA92…` on 2026-11-22 and 2026-11-23 (4,000–6,100 ETHFI each). The proposal says the 30% "Buyback" leg is "held in the Foundation treasury" and the 30% "User cashback" leg goes to users; the vote text allows "held in the Foundation treasury **or** distributed as user rewards" | Agrees with config's on-chain finding. The proposal's "held" leg is not happening on chain as far as config's read to 2026-10-19 shows. Free float: buying and paying out roughly cancel. Under the Round 20 wording of A3 ("market buybacks however the tokens end up") this buying would count; under R14 decision 5 it does not. See rule F1. Size: fixture 493,032 ETHFI in 90 days; 1,813,548 ETHFI bought to 2026-10-19 (config) |
| Theatre flag read 2026-11-19: 7,100,020 ETHFI through the FalconX-tagged forwarder | The top-up Safe `0x3fb6784e…` sent a further 1,000,000 ETHFI to that forwarder `0x80381dc3…` on 2026-11-20, after the read, and 5,000,000 to another Safe on 2026-11-18 | Free float: a named outflow about 1M larger than on file. The daily read should pick it up |
| Staker top-ups come from Foundation stock | Top-up Safe to the sETHFI vault: 50,000 ETHFI on 2026-11-02 and on 2026-11-16 | Agrees. Emissions-funded yield; nets out of holder value; counts as dilution |

Confirmed, one line each: fixed 1bn supply, no minting ("No new ETHFI is minted", proposal text); a 20M ETHFI buffer
from Foundation holdings is authorised for rewards; the fixture's dilution is 12.1% of float a year, nearly all unlocks.

## 6. Hyperliquid, Morpho, Uniswap, Aerodrome

Only emissions, unlocks and staking were checked here. The Assistance Fund, burn-to-claim and the fee switch are in
`docs/r20_research_hl_uni_morpho.md`; Aerodrome is in `docs/r19_aerodrome_research.md`.

| Project | Config assumes | Checked today | Result |
|---|---|---|---|
| Hyperliquid | Staking rewards are security emissions (a) paid from the pre-minted Future Emissions pool, only the share paid to stake inside circulating; team, Foundation and Grants releases are unlocks (e); bought HYPE is burned | Hyperliquid API `tokenDetails`: circulating 301,408,109; future emissions pool 409,683,976; total 998,686,389; non-circulating team wallet 238,332,328 and Assistance Fund 49,260,300 | Agrees with the fixture (circulating 301.67M). No mismatch found. The reward formula and unlock ledgers were not re-derived |
| Morpho | Fixed supply; rewards are Merkl claims from pre-minted stock (b); a relock schedule releases to 2027-11-21; fee switch off, holder value 0 | MORPHO `totalSupply` 999,999,999.80 (eth.blockscout.com) | Agrees: nothing minted. Campaigns and the relock schedule were not re-read |
| Uniswap | No emissions; the 2% a year mint the token allows has never been used; the growth budget (20M UNI a year from the Timelock) is outflow (d) | UNI `totalSupply` 1,000,000,000 (eth.blockscout.com) | Agrees: no mint. The burn sends UNI to `0x…dEaD`, which `totalSupply` still counts; our supply nets it by rule, not by the contract |
| Aerodrome | Minted emissions to gauges (b), rebase to lockers (c), Foundation buyback held unless burned, fees and liquid bribes to voters credited | Not re-read; the Round 19 doc is five days old | See r19 doc: 503,340.91 AERO "burned" on the site is still held; the other seven chains' voter revenue is unread (15–22% of the total) |

---

## Rules or definitions still wrong or unclear (Part 5f)

Each item: the evidence, the options, one recommendation. Jake decides.

### F1. Does a buyback count in A3 when the bought tokens go to people who are not holders?

Evidence: Ether.fi buys ETHFI on the market with Labs' revenue every day and ships it to card users as cashback. The
Round 20 wording of A3 counts "market buybacks however the tokens end up (burned, held or distributed)". R14 decision 5
says this is not holder value, and the workbook shows $0. The two rules disagree on about 493,000 ETHFI a quarter.

- (a) Count every revenue-funded market purchase in holder value and in net float pressure, whoever receives it.
- (b) Count it as buying in net float pressure, show the payout to users as a named movement back to the float, and
  keep holder value yield at 0.
- (c) Count it nowhere.

**Recommend (b).** The purchase is real demand and the payout is real supply; they roughly cancel in the float. Nothing
reaches a holder, so holder value stays 0. The A3 wording then needs four words added: "distributed **to token holders
or stakers**".

### F2. Do buybacks paid to stakers count as buying in net float pressure?

Evidence: the rule reads "revenue-funded buying (burned or held)". Pendle distributes 100% of what it buys, so read
literally its buying is 0 although about 2.5M PENDLE a year is bought on the market and arrives staked. Sky pays 81.8%
of the SKY it buys to stakers as liquid rewards.

- (a) Literal: only burned or held purchases count. Pendle's net float pressure is then minus its new supply.
- (b) Every revenue-funded purchase counts when bought. A payout that arrives staked stays off the float (Pendle). A
  payout that arrives liquid returns to the float as a named movement (Sky).

**Recommend (b).** It is the held-buyback rule Jake already wrote, applied to the third destination. One rule for
burned, held and distributed. For Pendle it adds about 1.2% of float a year of buying (INFERRED from the last four
epochs).

### F3. Is a staking reward paid out of bought-back tokens dilution?

Evidence: since 2026-08-17 Sky's staker rewards (about 400M SKY a year) are vested out of the Pause Proxy, which the
Smart Burn Engine refills with bought SKY. Config credits the buying as holder revenue and also counts the whole vest
as emissions, which is nearly all of Sky's 744M-a-year dilution in the fixture. At today's rates the staker leg of the
buying (about 86,600 USDS a day) buys more SKY than the vest pays out (INFERRED). Ether.fi's cashback already uses the
other treatment: only the part not covered by purchases is dilution.

- (a) Keep: every staker reward is emissions and nets out, whatever funds it.
- (b) A reward covered by purchases is the payout of a held buyback (a named movement back to the float), not new
  supply. Only the uncovered part is emissions.

**Recommend (b).** "Staking yield paid from emissions nets out" should mean emissions. Bought tokens are not new supply.
Effect: Sky's dilution falls by up to the whole vest and its net holder yield (fixture −0.24%) turns positive. Size
needs the store.

### F4. What does "paused" mean, and do Fluid and Ether.fi qualify?

Evidence: Fluid's own post is headed "Pause Buybacks" but states no condition or date to resume, and the reason given
("limited effectiveness") reads closer to an ending. Ether.fi never announced anything: one programme went quiet while
still authorised, and another is buying daily for non-holders.

- (a) Keep "paused" for both.
- (b) Define by evidence. "Paused" = a first-party statement that it is paused. Add "no restart condition stated" where
  that is so. "Not executing" = authorised, no statement, no purchases. "Off" = never switched on.
- (c) Require a stated restart condition for "paused"; otherwise "stopped".

**Recommend (b).** Fluid: "paused 2026-05-11 (no restart condition stated)". Ether.fi: "not executing since 2026-04-01
(authorised; programme buys go to card users)". Morpho: "off". Jake's phrase "paused with stated intent to restart" is
not evidenced for either.

### F5. Are one-off buybacks annualised?

Evidence: Sky's 5.25M USDS in seven days is called "one-off, extraordinary" by its own authors. Annualised in a 90-day
window it adds about 2.3 points to holder value yield and shows about 2% of float bought in a week.

- (a) Treat it like any buying; it rolls through the 90-day rates.
- (b) Count the tokens off the float and the dollars in the quarter's total, but show it as a named one-off and leave
  it out of annualised rates and the A3 key.

**Recommend (b).** The float is a fact; a yield is a rate people extrapolate. Burns already have a one-off list.

### F6. Revenue paid to stakers in other tokens: are Pendle's in-kind airdrops in?

Evidence: A3 counts "revenue paid directly to stakers incl. in other tokens". Config excludes Pendle's points-asset
airdrops because they are not a buyback. Pendle's API values them at $1,703,964 over five epochs (2026-03-10 to 2026-06-02) and $0
since 2026-06-16. Sky's USDS and Aerodrome's fee tokens are already counted.

- (a) Include them as holder revenue paid in other tokens.
- (b) Keep them out.

**Recommend (a).** They are fee income the protocol passes to stakers. No effect on the last two quarters.

### F7. When a held buyback's stock falls, is that a return to the float without proof of a sale?

Evidence: Maple's SSF is allowed to support "token liquidity". Config records falls in its SYRUP stock and an open
question about selling. The rule says held buybacks "return as a named movement if sold or paid out liquid".

- (a) Any net fall in the held stock, after the month's purchases, returns to the float on its day, cause unstated.
- (b) Only identified sales return.

**Recommend (a).** The float rule is about where tokens are, not why they moved. It also covers Ether.fi's and
Aerodrome's fund wallets with no new rule.

### F8. Supply that is authorised but not issued: where does it show?

Evidence: Pendle has 9.84M PENDLE mintable and unminted, growing about 2% a year. MIP-021 expires in six months. Uniswap
can mint 2% a year and never has. Fluid's incentive ceiling is 6M a year. None is "created or released", so none is
dilution under the rule. Jake's principle is that dilution ahead matters most.

- (a) Leave them out everywhere.
- (b) One memo column, "authorised, not issued", beside dilution; in neither net.

**Recommend (b).** Also record an end date for Maple's MIP-021 so the status changes on the day it lapses.

---

## Could not verify

- **Maple.** The forum (community.maple.finance) returned HTTP 403; the MIP-021 text was read from Snapshot instead.
  Which month's revenue MIP-021 first applied to is not stated. The Transparency page was not fetched (robots.txt
  disallows it), so no monthly buyback amount after July 2026 was read here. Whether the SSF has sold SYRUP is still
  open.
- **Sky.** The Executive spell texts behind the three burns were not read; the amounts and dates are from the token
  transfers. The one-off's actual fills, the SKY bought so far and the date it will be burned were not read. The
  Splitter's live `burn` value was taken from the forum and the execution transaction, not from an `eth_call`. x.com was
  not opened.
- **Fluid.** Whether the 2026-05-11 proposal went to a vote. Whether any restart has been discussed off the forum.
- **Ether.fi.** Why the staker-directed buying stopped in April 2026. The DAO voted a 5,000 ETH treasury contribution
  for rsETH on 2026-04-23 to 04-27; a link is plausible but INFERRED, not stated. Whether any of the programmatic
  purchases since 2026-10-19 were held instead of paid on.
- **Pendle.** No first-party total for weekly emissions under the new model exists; the 65,450 figure is from Jake's
  build, not re-read. The 84% legacy share is worked from two API fields. Purchases at the buyback contract were not
  read on chain.
- **Hyperliquid, Morpho, Uniswap, Aerodrome.** Only supply totals were re-read. Reward formulas, campaign lists,
  unlock ledgers and the growth budget were taken from config as they stand.
- **Sizes.** Every "fixture" figure is from the frozen baselines on the 15-dump build, not Jake's store. Prices have
  moved a lot since (SYRUP $0.088, SKY $0.040, ETHFI $0.51 today), so dollar effects are rough.
