# Round 20 research — Hyperliquid's Assistance Fund, Uniswap's burn-to-claim, Morpho's fee switch

Research only. No code, config or test was changed. All reads are from 2026-11-23, about 17:00 UTC, unless a row says otherwise. Scripts and downloads are in the scratchpad folder `r20_research_a/`.

## Answers first

| Item | Answer |
|---|---|
| (b) Hyperliquid | **Yes, the Assistance Fund's HYPE can be moved — by a protocol upgrade that validators adopt.** No key holder can move it and nothing ever has. The Hyper Foundation's own words are "irretrievable without a hard fork", and the December 2025 vote is "binding social consensus to never authorize a protocol upgrade to access this address". That is a promise, not a proof. Under Jake's test it is a **held buyback** and A4 stays off. |
| (c) Uniswap | Protocol fees sit in a TokenJar on each chain. Anyone can take the jar's assets by burning a fixed amount of UNI (4,000 on mainnet, 2,000 on the L2s read). It qualifies as **A3 and A4**. The 100M treasury burn is A4 only. |
| (d) Morpho | **Off.** Fee is 0 on all 735 listed markets and the fee recipient is the zero address on Ethereum and Base. No proposal or vote to switch it on exists. The only stated position (June 2025) is to reinvest fees, not distribute them. |

---

## (b) Hyperliquid — the Assistance Fund

### What the repo does today

- `archetypes: [3, 1, 4]`. `burn_mechanism.model = transfer_to_dead_address`, status confirmed. `burn_address` = `0xfefe…fefe`.
- The Fund's balance is read as the cumulative burn (`burn_address_balance` → `gross_burn_tokens`). `a4_burn_metric = gross_burn_tokens` (A4 is the Fund only, Jake 2026-10-12).
- The Fund is taken out of total supply and FDV (SPEC rows S6, G2-D2a) and is out of circulating (SPEC R9.2-HL: "the Assistance Fund (49.03M) is burned").
- `not_applicable.buyback_fund_balance` and `treasury_holding_tokens` both say the Fund is burned, not held.
- The config's stated reason: "The Assistance Fund address has never had a private key, so nothing can leave it", plus "the validator vote of 2025-12-27 (85% of staked weight)" and an SEC-filed exhibit of 2026-05-07.

### What the primary sources say

| Source | Date | What it says |
|---|---|---|
| Hyper Foundation proposal, x.com/HyperFND/status/2001127850754367525 (read through api.fxtwitter.com and cdn.syndication.twimg.com; x.com itself has a login wall) | 2025-12-17 03:10 UTC | "Similar to the zero address, the Assistance Fund system address has never had a private key with control over its funds. Funds are mathematically irretrievable without a hard fork." "No onchain action is required, as the tokens are already in a system address with no private key. This vote is binding social consensus to never authorize a protocol upgrade to access this address." Validators signal by 12-21 04:00 UTC; result on stake weight as of 12-24 04:00 UTC. |
| Hyper Foundation result, x.com/HyperFND/status/2003684257949188396 (api.fxtwitter.com) | 2025-12-24 04:28 UTC | "HYPE in the Assistance Fund system address … has been formally recognized as burned." "85% of stake voting for burning, 7% against, and 8% abstaining." |
| Hyperliquid docs, hyperliquid.gitbook.io/hyperliquid-docs/trading/fees | read 2026-11-23 | "The assistance fund uses the system address `0xfefe…fefe`. It converts trading fees to HYPE in a fully automated manner as part of the L1 execution. HYPE in the assistance fund is burned, removing the tokens permanently from the circulating and total supply." |
| Hyperliquid info API, `tokenDetails` (HYPE) | 2026-11-23 16:57 UTC | totalSupply 998,686,406.64. circulatingSupply 301,408,166.61. The Fund is listed in `nonCirculatingUserBalances` at 49,260,148.08. circulating + listed non-circulating + futureEmissions = totalSupply to within 0.01 HYPE, so **the API's total supply still contains the Fund's HYPE.** |

### What was technically done

Nothing. The proposal says so: "No onchain action is required". The HYPE was not sent to a dead address and the supply was not reduced. It still sits as a spot balance of the account `0xfefe…fefe`.

### On-chain checks (Hyperliquid info API, 2026-11-23)

| Check | Result |
|---|---|
| Fund's HYPE balance (`spotClearinghouseState`) | **49,260,148.08 HYPE** (16:57 UTC). About $3.24bn at mid 65.818. 4.93% of the 1bn max. |
| Fund's whole ledger (`userNonFundingLedgerUpdates`, from 2024-11-29 to today, 3,612 entries, two pages) | **No outgoing entry of any token.** Every `send`, `spotTransfer` and `internalTransfer` has the Fund as destination. The rest are 8 `spotGenesis` credits and 2 zero-size `liquidation` records from December 2024. |
| Fund's recent fills (`userFills`, last 2,000) | All 2,000 are buys of HYPE/USDC (`@107`). No sell. |
| Staking (`delegatorSummary`) | 0 delegated, 0 pending. The Fund's HYPE is not staked and earns nothing. |
| Repo's stored balance series (fixture, 25 readings 2026-09-11..10-13) | Never falls. |
| Other balances | 2,051.98 USDC, 43,220.28 USDH and 31 other small token balances. |

### Verdict

"**No private key, but movable by a hard fork / validator vote**" is what the evidence supports. "Provably unspendable" is not supported.

- No person holds a key. No HYPE has ever left. This is solid.
- The Foundation itself says the funds are retrievable by a hard fork, and describes the protection as "social consensus".
- The account is not inert. The L1 code already spends the Fund's USDC to buy HYPE every day. So the protocol code does debit this account; it only has no rule today that debits its HYPE. INFERRED from the docs line and the fills.
- The HYPE still exists as a balance and is still in the API's total supply. Reversing an Ethereum base-fee burn would need new tokens to be minted. Releasing this HYPE would only need a transfer rule added in an upgrade.
- 7% of stake voted against and 8% abstained. A future validator set is not bound by anything on chain.

### The effect, both ways

Base figures: circulating and float from SPEC R9.2-HL (the frozen dumps, export of 2026-11-16); the Fund and total supply as read today; price 65.818 (API mid). Rates are over 2026-10-13 → 2026-11-23 (fixture reading to today, about 41 days).

| Figure | Burned (today's repo) | Held buyback (Jake's test) | Change |
|---|---|---|---|
| Circulating | 539,758,896 | 589,019,044 | +49.26M (+9.1%) |
| Free float | 99,462,452 | 99,462,452 | none (held tokens are off the float) |
| Float ÷ circulating | 18.4% | 16.9% | |
| Total supply (tokenDetails − Fund, before the 0.32M HyperEVM burn) | 949,426,259 | 998,686,407 | +49.26M |
| FDV at 65.818 | $62.49bn | $65.73bn | +$3.24bn |
| Market cap on our circulating | $35.53bn | $38.77bn | +$3.24bn |
| Burned HYPE, lifetime | 49.26M Fund + 1.31M outside it | 1,313,593 (1bn − totalSupply) | −49.26M |
| A4 | On. Burn = Fund intake, about 30,300 HYPE a day, 11.05M a year, 2.05% of circulating | Off. The only real destruction is about 4,245 HYPE a day (fall in totalSupply), 1.55M a year, 0.26% of circulating | |
| A3 | On, unchanged | On, unchanged. The same purchases, now tagged "held" | |

Every ratio with circulating underneath moves by the same 9.1% (dilution ÷ circulating, holder value yield, insider share). The 30,300 a day includes the Fund spending the AQAv2 USDC lump of early November.

---

## (c) Uniswap — TokenJar and burn-to-claim

### How it works today

| Step | Fact | Source |
|---|---|---|
| Fees collect | Each fee source sends its protocol fee through an adapter to "one immutable contract called the TokenJar" on the same chain. | docs.uniswap.org/contracts/protocol-fee/overview, read 2026-11-23 |
| Who can take them | "Only the active Releaser can withdraw from it." On mainnet the jar's `releaser()` is the Firepit. | docs; eth_call on `0xf38521f1…5f85` |
| The price | `Firepit.release()` is open to anyone. It runs `RESOURCE.safeTransferFrom(msg.sender, RESOURCE_RECIPIENT, threshold)`: the caller's UNI goes straight to `0x…dEaD`. The caller names the jar assets to receive. | Uniswap/protocol-fees `ExchangeReleaser.sol` L45-50; `TokenJar.sol` L29 |
| Threshold | Mainnet **4,000 UNI** (`threshold()` = 4000e18). OP Mainnet, Base, Arbitrum and Unichain releasers: **2,000 UNI** each. | eth_call, 2026-11-23 |
| Who sets it | `thresholdSetter()` and `owner()` of the Firepit, and `owner()` of the jar, are the governance Timelock `0x1a9C8182…35BC`. Governance can change the threshold, and can point the jar at a different releaser (`TokenJar.setReleaser`, onlyOwner). | eth_call; `TokenJar.sol` L40 |
| Why searchers do it | "When the top N assets in the TokenJar are worth slightly more than [the threshold], anyone is incentivized to use the Firepit.release()". | docs, same page |
| Proposal | UNIfication, gov.uniswap.org/t/25881, posted 2025-11-10 by Hayden Adams, Ken Ng and Devin Walsh: "Fees can only be withdrawn from TokenJar if UNI is burned in another smart contract called Firepit." Executed as proposal #93 on 2025-12-27 20:33:11 UTC, block 24,106,378 (already verified in config). | forum JSON, read 2026-11-23 |
| Chains | Mainnet (#93), then OP, Base, Arbitrum (#94), Soneium, X Layer, World Chain, Zora (#95), BNB, Polygon, Celo (#96), Robinhood (#99), v4 fees (#100), Arc (#102), plus Unichain. Not re-read this round; taken from config `UNISWAP_FEE_SWITCH_CHAINS` (read 2026-11-09). |
| Labs and Foundation | Labs set its interface, wallet and API fees to zero. Labs is paid from the treasury instead: "an annual growth budget of 20M UNI, distributed quarterly using a vesting contract starting January 1, 2026". None of the jar's fees go to Labs or the Foundation. | forum post |

### What has been burned

| Figure | UNI | Source |
|---|---|---|
| `0x…dEaD` balance one block before #93 | 49,576.62 | `balanceOf` at block 24,106,377 |
| One-off treasury burn in #93 | 100,000,000 | balance at block 24,106,378 = 100,049,576.62 |
| `0x…dEaD` balance now | 114,511,581.21 | block 26,479,185, 2026-11-23 16:59 UTC |
| **Burn-to-claim since launch** (now − 100M − opening) | **14,462,004.59** | arithmetic. Includes any stray transfers to the address. |
| of which mainnet Firepit | 6,416,000 | `nonce()` 1,604 releases × 4,000 |
| of which Base / Arbitrum / OP / Unichain | 3,424,000 / 1,170,000 / 92,000 / 30,000 | `nonce()` 1,712 / 585 / 46 / 15 × 2,000. Assumes the threshold never changed. Counted at release, before the bridge delay. |
| Remainder (other chains, timing, strays) | 3,330,005 | INFERRED by subtraction, not read |

| Window to 2026-11-23 | UNI to `0x…dEaD` | Per day | Annualised |
|---|---|---|---|
| 7 days | 326,000 | 46,571 | 17.0M |
| 30 days | 1,304,000 | 43,467 | 15.9M |
| 90 days | 5,034,001 | 55,933 | 20.4M |
| 180 days | 9,732,001 | 54,067 | 19.7M |

- The 90-day rate is 2.0% a year of the 1bn genesis supply, 2.3% of supply net of burn (885.5M).
- Value: 5,034,001 UNI is about $25.4M at today's price of $5.04 (DefiLlama coins API). DefiLlama's protocol revenue for the same 90 days is $31.86M, and its holders revenue is the same figure. The two are not like-for-like: the burn is priced at one day's price here, and DefiLlama books fees on the release day. The repo's own stored comparison is burn $33.0M against revenue $29.7M for Q0 (2026-10-11).
- For scale: the Labs growth budget is 20M UNI a year out of the treasury, about the same size as the yearly burn.

### Why it is A3 as well as A4

- **A4:** the UNI goes to `0x…dEaD`. UNI has no burn function and nobody can move that balance. Permanent.
- **A3:** it is a documented programme (proposal #93, the docs, immutable contracts) that turns 100% of protocol revenue into demand for UNI. The protocol does not buy UNI itself. It sells its fee income only for UNI, so whoever claims the fees must first hold or buy that UNI. Jake's definition counts "market buybacks however the tokens end up — burned, held or distributed"; here they end up burned.
- **The 100M treasury burn is A4 only.** It came from the treasury, not from revenue.
- The programme can be redirected by governance (`setReleaser`). It is on today.

---

## (d) Morpho — the fee switch

**Off, with no proposal, vote or dated plan.**

| Check | Result | Source |
|---|---|---|
| Fee on listed markets | **0 on all 735** listed markets ($4.61bn borrowed, $5.55bn supplied) | Morpho API `blue-api.morpho.org/graphql`, `state.fee`, 2026-11-23 |
| Fee on chain, the 8 largest Ethereum / Base markets by borrow (Base cbBTC/USDC $1.42bn; Ethereum cbBTC/USDC, kBTC/RLUSD, PRIME/PYUSD, PT-reUSD/USDC, WBTC/USDC, wstETH/USDT, sUSDe/PYUSD) | **fee = 0** on every one | `market(id)` on Morpho Blue `0xBBBBBbbB…EFFCb`, block 26,479,192 |
| `feeRecipient()` | **zero address** on Ethereum and on Base | eth_call |
| Who can switch it on | `owner()` = `0xcBa28b38…9afa` on both chains. `setFee` and `setFeeRecipient` are onlyOwner. Cap `MAX_FEE = 0.25e18` (25% of borrower interest). | eth_call; morpho-org/morpho-blue `Morpho.sol` L123, L139, `ConstantsLib.sol` L5 |
| Governance docs | "Governance powers include controlling the MORPHO tokens in the governance treasury, activating the fee switch, and whitelisting new LLTVs and IRMs." | docs.morpho.org/learn/governance, read 2026-11-23 |
| Snapshot votes | 142 proposals in `morpho.eth`, 2022-10-15 to 2026-11-19 (latest: MIP 136). **None activates a fee.** The only title with "fee" is MIP 94. | hub.snapshot.org GraphQL |
| Forum | Searches for "fee switch", "protocol fee", "feeRecipient", "buyback": no proposal or RFC to activate it. Mentions are passing ones. | forum.morpho.org/search.json |
| MIP 94 (Morpho Association) | Holding assets in the DAO treasury "could create tax risk for tokenholders… While issues of this kind will need to be comprehensively addressed in relation to future fee switch activation of the Morpho Protocol, work on this topic is not yet complete." | forum.morpho.org/t/1509, 2025-02-11 |
| Stated position | "As the Morpho DAO explores introducing protocol fees to support network growth, the Morpho Association advocates for reinvesting protocol fees rather than distributing them". "All revenue should be reinvested back into growth." | morpho.org/blog/aligning-around-morpho-the-only-asset-for-morpho, published 2025-06-06 |

So A3 is off. If the switch is turned on later, the stated intent is reinvestment, which would still not be A3 unless the fees buy or pay MORPHO. The repo agrees today: archetype [2], `revenue_usd` 0 by design.

---

## Mismatches and rule questions

**1. Hyperliquid: burned or held?** The config says "nothing can leave it" and treats the Fund as burned. The Foundation says it can be reached by a hard fork and that the bar is social consensus.
- Option A — keep it burned (A3, A1, A4). Circulating 539.76M, total supply 949.43M, A4 burn 2.05% a year. Rests on a validator promise.
- Option B — held buyback (A3, A1; A4 off). Circulating 589.02M (+9.1%), float unchanged at 99.46M, total supply 998.69M, FDV +$3.24bn, burned falls to 1.31M.
- **Recommendation: B.** Jake's condition is "only if the Assistance Fund's HYPE can never be moved", on clear primary evidence. The primary source says the opposite in its own words. Note for Jake: a hard fork can undo any burn on any chain, so the working test here is narrower — the HYPE still exists as a balance, in the API's total supply, at an account the protocol code already operates.

**2. If B: does Hyperliquid keep A4 for its other burns?** About 4,245 HYPE a day leaves total supply outside the Fund (0.26% of circulating a year; 1.31M lifetime). A4 is "any permanent destruction, scored on what is actually burned".
- Option A — A4 off, as Jake's tag reads. These burns keep feeding A1's net issuance.
- Option B — A4 on, scored on the non-Fund burns only.
- **Recommendation: A.** It is what the tag says, and the burns are fee burns already counted in A1.

**3. The vote date in config is wrong.** Config says 27 December 2025 in ten places in the Hyperliquid entry (`burn_mechanism.source_date`, `destination_confirmed_date`, the `fee_split` period start, notes). The proposal was 2025-12-17, the stake cut-off 2025-12-24 04:00 UTC and the result 2025-12-24 04:28 UTC. 2025-12-27 is Uniswap's #93 execution date.
- Options: correct to 2025-12-24, or leave.
- **Recommendation: correct to 2025-12-24** and cite the two posts. The 0.99 fee-split period should not start on a date taken from this vote; the vote did not change the split.

**4. The config's source URL is dead.** `hyperliquid.gitbook.io/hyperliquid-docs/hypercore/assistance-fund` returns "Page Not Found" (with HTTP 200). The statement is on `/trading/fees`.
- **Recommendation: re-point** the five uses to `/trading/fees`.

**5. Uniswap is `archetypes: [4]`; Jake's tag is "A4, A3".** `BUYING_PRESSURE_BY_DESIGN` says the burn is "not a market buyback".
- Option A — add A3; the A3 series is the burn-to-claim burn only (14.46M UNI since launch, 5.03M in 90 days); the 100M treasury burn stays A4 only. The same UNI must count once in holder value.
- Option B — add A3 and include the 100M.
- **Recommendation: A.** The 100M was treasury-funded, not revenue. The "by design" wording on the buying-pressure cell then needs Jake's call: it is true that the protocol buys nothing itself, but the new A3 definition counts the demand.

**6. Morpho: would "fee switch on" alone make it A3?** The Association's position is to reinvest fees, not distribute them.
- **Recommendation: no.** A3 needs the revenue to reach the token (a buyback or a payment to holders). Keep A3 off until that is documented, even if the fee goes above 0.

---

## Could not verify

- **The SEC exhibit of 2026-05-07** (Hyperliquid Strategies Inc) that config cites as the second primary source. EDGAR full-text search and the filing pages returned HTTP 403 to this client. Not read. A web search found Hyperliquid Strategies filings that describe the buyback, but none was confirmed to state that the Fund's HYPE is burned or unreachable.
- **The validator governance forum replies** and the per-validator tally. Only the Foundation's two posts were read, through mirrors (api.fxtwitter.com, cdn.syndication.twimg.com). x.com was not read directly.
- **Hyperliquid's node code.** Whether any code path can debit the Fund's HYPE cannot be checked from here; the claim that none exists rests on the Foundation's statement and the empty outflow ledger. Past validator interventions in chain state were not researched this round.
- **Whether the HyperEVM burn (about 0.32M) is inside or outside the 1,313,593 fall in totalSupply.** Not tested; the two were not added together.
- **Uniswap burns on chains other than mainnet, Base, Arbitrum, OP and Unichain.** The 3.33M remainder is by subtraction. Threshold history (whether 4,000 / 2,000 ever changed) was not read, so the per-chain totals assume it did not.
- **The dollar value of fees claimed per release.** Not read from chain. DefiLlama's $31.86M (90 days) is a secondary figure.
- **Morpho Midnight.** Its docs say it "can charge two protocol-level fees on each market" (a settlement fee and a continuous fee capped at 1% a year). Whether either is set above zero, and who receives it, was not read. If it is, "Morpho takes no protocol cut" in config would need a second look. This does not change the Morpho Blue finding.
- **Morpho deployments other than Ethereum and Base** were checked through Morpho's API only (fee 0 on all listed markets), not by eth_call.
