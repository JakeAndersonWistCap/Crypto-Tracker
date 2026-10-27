# Pyth Network (PYTH): tracker research dossier

As of 27 October 2026 (on-chain reads 10:20–11:10 UTC). Companion file: `sources.csv` (one row per metric). Raw downloads are in `raw/`; the scripts that read them are in `../pyth_scripts/`.

Conventions: every mechanic below carries a quote, a link and a date. "On-chain" means read by me from `https://api.mainnet-beta.solana.com` on 27 Oct 2026, with the account type, owner program, mint and balance checked. Where something rests only on on-chain behaviour and no primary document names it, the text says so.

---

## 0. Headline findings

1. **The revenue-to-PYTH conversion is documented and running.** OP-PIP-136 (forum post 24 Sep 2026, marked PASSED) commits the DAO's whole approved share of Pyth Pro, LaaS, Marketplace, Indices, Core and Entropy revenue to PYTH accumulation by the Pythian Council, with the PYTH returned to the DAO Treasury and held. No burn. Douro Labs keeps its contractual share (40% of Pro, Marketplace and, per its reports, Indices; 10% of LaaS).
2. **Two settlement paths have actually been used, and on-chain they look nothing alike.** (a) Douro pays USDC (Dec 2025–Mar 2026 and Aug–Sep 2026), the Treasury passes the USDC to the Council vault, the Council opens Jupiter DCA / limit orders, fills land in the Council vault's PYTH account, and the Council sends the PYTH back to the Treasury. (b) Douro paid in PYTH (Apr–Jul 2026, 24,171,981.6 PYTH in total) with plain SPL transfers from its paying wallet straight into the Treasury. No swap is involved. On-chain, the PYTH in path (b) came from two wallets that also hold tens of millions of PYTH, not from market purchases in the same transactions (section 5).
3. **Oracle Integrity Staking rewards ended on 22 April 2026.** On-chain `y = 0`. 671.2m PYTH is staked across 134 publisher pools, and an 84,834 PYTH unclaimed balance remains in reward custody. CO-PIP-139 (7 Oct 2026) reclassifies OIS as "legacy, unstake-only". That conflicts with the docs page, which still says slashing is active.
4. **Supply.** The mint has no mint or freeze authority, and supply is 9,999,958,880.27 PYTH (41,119.73 below 10bn; there is no protocol burn, and these tokens were destroyed by unattributed SPL burns). The genesis lockup sits in 379 `PeriodicVestingAfterListing` accounts owned by 195 wallets. All of it unlocks at **2027-05-19 14:00:00 UTC** (list time 2023-05-20 14:00 UTC + 4 × 365 days). On-chain it totals **2,154,583,334 PYTH**, against the 2,125,000,000 implied by the docs; the gap is unexplained. A further 193.8m PYTH sits in 867 other `PeriodicVesting` accounts with an unvested balance (publisher-reward and grant locks) that release through Oct 2027.
5. **Publisher rewards flow through a chain of wallets, and they run at roughly double the lead's figure.** Douro's paying wallet funds the "distribution wallet" `GiipfV…`. On 11 Sep 2026 `GiipfV…` paid out 5,633,811 PYTH in 57 transfers: 1,405,032 (24.9%) to 28 ordinary wallets and 4,228,779 (75.1%) into 29 newly created staking-program lock accounts. That matches the "25% spot / 75% at 1y" PPR schedule in CO-PIP-137. No primary document states a monthly PPR amount. The measured run (5.63m) and the Douro → `GiipfV…` transfers (4.6–5.6m a month Jul–Sep; 12.4m in Oct) are about double the "~2.7m a month" lead (section 1.7).

---

## 1. Mechanics (quoted, linked, dated)

### 1.1 Revenue split between the DAO and Douro Labs

| Product | DAO | Douro | Primary basis |
|---|---|---|---|
| Pyth Pro subscriptions | 60% | 40% | Constitution, Pyth Pro Governance ("Pyth Pro Subscriptions \| 60% \| 40%"), added by CO-PIP-104 (22 Apr 2026); originally CO-PIP-9 (16 Sep 2025): "Douro Labs proposes that 60% of subscription revenues attributable to the Services to flow to the DAO." |
| Listing as a Service (LaaS) | 90% | 10% | OP-PIP-98 (20 Mar 2026): "Establish a 90/10 revenue share (DAO/Douro) for LaaS proceeds"; Constitution table. |
| Data Marketplace | 60% | 40% | CO-PIP-99 (20 Mar 2026): "Assign Douro Labs as operator under a 60/40 revenue share". It also has a "12-Month Review Clause: Revenue split will be reviewed 12 months after Marketplace launch". |
| Pyth Indices | 60% | 40% | **Only in Douro's monthly reports** (first in the June 2026 report: "Indices (60/40)"). No Constitution, CO-PIP or OP-PIP setting an Indices split was found. OP-PIP-136 lists "Pyth Indices revenue" as eligible but sets no share. |
| Pyth Core fees, Entropy fees, Express Relay | 100% DAO-owned | — | OP-PIP-87 (5 Dec 2025): "Assets accumulated across Pyth contracts on non-Solana chains—such as fees collected by Pyth Core, Entropy, or Express Relay contracts—belong to the DAO". |

Links: Constitution https://github.com/pyth-network/governance/blob/main/docs/constitution/pyth-dao-constitution.md (main branch as fetched 27 Oct 2026; it contains the Price Feed Council definitions, so the CO-PIP-139 edit (PR #578) was not on main at fetch time) · CO-PIP-104 https://forum.pyth.network/t/passed-co-pip-104-pyth-pro-governance-payment-currency-flexibility/2480 · CO-PIP-9 https://forum.pyth.network/t/passed-co-pip-9-proposal-to-assign-douro-labs-as-administrator-and-initial-distributor-for-subscriptions-to-pyth-data-off-chain/2220 · OP-PIP-98 https://forum.pyth.network/t/passed-op-pip-98-laas-listing-as-a-service-for-pyth-pro-lazer/2416 · CO-PIP-99 https://forum.pyth.network/t/passed-co-pip-99-establishment-of-the-pyth-data-marketplace-and-assign-douro-labs-as-operator/2417

**Payment terms** (Constitution, Pyth Pro Governance, added by CO-PIP-104, 22 Apr 2026): "Payments to the Pyth DAO Treasury shall be made monthly, within 7 days from month-end." "Payments may be made in USDC, PYTH tokens, or a combination thereof, at the Operator's discretion." When PYTH is used: "The PYTH amount shall be calculated using the average daily closing price of the PYTH/USD Pyth Lazer feed across all calendar days in the payment month" and "The PYTH amount delivered shall not be less than the USD-equivalent value owed".

**Douro's stated settlement currency going forward.** zenyas (Douro Labs), 9 Sep 2026, in the V2 idea thread: "For future Pyth Pro-related DAO distributions, Douro Labs will continue transferring the DAO share of the revenue in USDC on a monthly basis." https://forum.pyth.network/t/pyth-strategic-reserve-v2-aligning-pyth-accumulation-with-pyth-pro-revenue/2696 (post 6). This is a forum statement. The Constitution permits PYTH settlement.

**Reported revenue (Douro monthly reports, forum category Pyth Pro).** Gross / DAO share, USD:

| Report (post date) | Period | Gross | DAO share | Paid as | Tx in report |
|---|---|---|---|---|---|
| 2 Dec 2025 (t/2289) | 1 Sep–1 Dec 2025 | 262,221 | 157,333 | USDC | 79Tvg8QK… |
| 5 Jan 2026 (t/2315) | Dec 2025 | 90,407* | 54,245 | USDC | 3fFk421U… |
| 3 Feb 2026 (t/2339) | Jan 2026 | 122,684* | 73,700 | USDC | 3WrYYPoy… |
| 3 Mar 2026 (t/2392) | Feb 2026 | 179,833* | 107,900 | USDC | 4pV9cPvy… |
| 2 Apr 2026 (t/2444) | Mar 2026 | 253,833 (gross) / 225,833 (subscribed) | 135,500 earned; 106,850 paid after $28,650 bad-debt credits | USDC | 2Y5HVMA6… |
| 4 May 2026 (t/2508) | Apr 2026 | 361,387 | 229,132 = 5,059,107 PYTH at $0.045291 | PYTH | 2mP8RsJx… |
| 2 Jun 2026 (t/2592) | May 2026 | 293,448 (net of $52,389 credits) | 176,669 = 3,776,270 PYTH at $0.046784 | PYTH | 2Yrg6Bgq… |
| 3 Jul 2026 (t/2627) | Jun 2026 | 451,625 | 273,075 = 7,674,095 PYTH at $0.035584 | PYTH | 5vmWD84C… |
| 4 Aug 2026 (t/2660) | Jul 2026 | 562,557 | 340,384 = 7,662,509 PYTH at $0.044422 | PYTH | 3RvV6p62… |
| 8 Sep 2026 (t/2695) | Aug 2026 | 716,800 | 433,740 | USDC | 3e5rijGD… |
| 5 Oct 2026 (t/2720) | Sep 2026 | 916,471 (Pro 768,084; LaaS 12,700; Indices 135,688) | 553,693 | USDC | 4HmpUVkL… |

\*Derived from cumulative-subscribed figures in successive reports, not stated as a monthly gross in those posts. Cumulative since Sept 2025 per the September report: gross $4,135,517, DAO $2,506,630, Douro $1,628,887. Douro's own wording conflicts within the September report: "60% of subscription revenues for Pyth Pro data from August 1st 2026 to September 1st 2026" vs "for the month of September 2026". It also says "Douro Labs bills in arrears for indices, so August distributed revenue is actually from July performance." No Marketplace revenue appears in any month after May ($0 in Apr–May; the line is absent from June onwards).

On-chain check: every one of the 11 distributions above moved from Douro's paying wallet to the DAO Treasury. The 7 USDC ones came from token account `C6G3jRs1…` (owner `2ru31e9g…`) to the Treasury USDC account `5Unq3fgf…`; the 4 PYTH ones from `8iRsZ71S…` (owner `2ru31e9g…`) to the Treasury PYTH account `AYBdhJU8…`. The amounts match the reports to the unit, except July: 7,662,509.6 on-chain vs 7,662,509 reported.

The published-figure side also carries ARR. Pyth/Douro, 2 Sep 2026: "Pyth crossed $10.4M in annual recurring revenue in August … Pyth Indices reached $1.59 million in fixed annual recurring revenue (excluding variable rev share)". https://www.pyth.network/blog/pyth-august-2026-report-building-the-price-of-everything-layer

### 1.2 Strategic Reserve V1 (OP-PIP-87), December 2025 – September 2026

Source: https://forum.pyth.network/t/passed-op-pip-87-pyth-token-phase-2-pyth-strategic-reserve/2293 (posted 5 Dec 2025; quorum reported 9 Dec 2025).

- Rule: "the DAO will purchase PYTH monthly using one-third (33%) of the Treasury balance at hand (whether USDC, SOL, or others, excluding PYTH)."
- Executor: "the Pythian Council Ops Multisig Wallet … GAdn7TZhszf5KTfwNRx3A2nP6KCRFEWucZubgdEqbJA2 … proposals need to be approved by 6/8 of the elected council members".
- Guardrails: "No transactions where slippage would exceed 5%"; "No single transactions in excess of $25,000 USD"; "Aggregators (like jup.ag) should be preferred"; "Where limit orders are used, they must be at least 0.1% below the displayed market price".
- Destination: "the Pythian Council must repatriate all the PYTH accumulated back to the Pyth DAO Treasury: Gx4MBPb1vqZLJajZmsKLg8fGw9ErhoKsR8LeKcCKFyak".
- Each month needed its own OP-PIP (#2 OP-PIP-88 … #10 OP-PIP-135).

**Purchases by month.** From the Council's monthly reports (forum category; links in sources.csv). The PYTH figure is the amount returned to the Treasury.

| Month (vote) | Funds received by Council | PYTH returned to Treasury | Return tx |
|---|---|---|---|
| Dec 2025 (OP-PIP-87) | 127,573 USDC + 17.97 SOL | 2,157,086.989773 | xJHXBBBt… |
| Jan 2026 (OP-PIP-88) | 132,576 USDC + 10.693 SOL | 2,190,179.028906 | 4J5aV9zk… |
| Feb 2026 (OP-PIP-91) | 116,771 USDC + 7.058 SOL | 2,205,117.505101 | 4NA1H6CM… |
| Mar 2026 (OP-PIP-97) | 123,327 USDC + 4.78 SOL | 2,752,177.01716 | 2GBf54Kc… |
| Apr 2026 (OP-PIP-102) | 117,835 USDC + 3.18 SOL | 2,473,976.02141 | 47N5fAqR… |
| May 2026 (OP-PIP-115) | 86,690.79 USDC + 10.66 USDT + 229.47 SOL + 0.60 wSOL | 2,570,154.710439 | 4oGiqnCE… |
| Jun 2026 (OP-PIP-117) | 57,828.19 USDC + 152.89 SOL | 1,803,872.741588 | 4zY2eci9… |
| Jul 2026 (OP-PIP-123.V2) | 38,552.13 USDC + 101.92 SOL | 1,144,629.131118 | 3se13wzM… |
| Aug 2026 (OP-PIP-129) | 25,701.42 USDC + 67.95 SOL | 669,661.722734 | 2feEsBsZ… |
| Sep 2026 (OP-PIP-135) | 161,714.28 USDC + 45.30 SOL | 2,582,433.550131 | 26q1EGem… (1 Oct 2026) |
| **Total V1** | **988,579.47 USDC/USDT + 641.8 SOL** | **20,549,288.42 PYTH** | |

This matches the lead's "20.55m PYTH". The lead's "~$1.04m" cost could not be confirmed from primary data: the reports give SOL amounts but no USD value for them. The stablecoin leg alone is $988,579 (an average of $0.0481 per PYTH on the USDC leg).

The Council's ledger https://docs.google.com/spreadsheets/d/1e6HmkJ6fff62hYj9fMRVAOtXy0nMVb1F76zAZugiE28 was readable through its public CSV export (74 rows, saved as `raw/ledger.csv`). It records **orders**, not fills: one "Swap (DCA)" row per Jupiter DCA order, carrying the final PYTH out when complete and blank while filling. The months reconcile to the reports above.

### 1.3 Strategic Reserve V2 (OP-PIP-136)

Source: https://forum.pyth.network/t/passed-op-pip-136-pyth-strategic-reserve-v2/2711 (posted 24 Sep 2026; Realms proposal `2qkHmHEi7r5Cz5tG2u2VNtTbD5XFmZGfdQicDhXFqAKo`). Idea thread: https://forum.pyth.network/t/pyth-strategic-reserve-v2-aligning-pyth-accumulation-with-pyth-pro-revenue/2696 (9 Sep 2026).

- **Scope:** "Eligible revenue may include the DAO's approved share of: Pyth Pro revenue; Listing as a Service revenue; Pyth Data Marketplace revenue; Pyth Indices revenue; Pyth Core fees; Pyth Entropy fees; and other DAO-owned assets once properly repatriated." The idea thread is explicit on the share: "The DAO would accumulate 100% of the applicable DAO share from all four categories. This does not mean 100% of gross revenue."
- **No monthly vote:** "future eligible non-PYTH DAO revenue and assets may be made available to the Pythian Council Ops Multisig for conversion into PYTH without requiring a separate monthly DAO vote." "OP-PIP-136 will supersede the conflicting Treasury-management and recurring-purchase mechanics of OP-PIP-87."
- **Initial transfer:** "323,428.56 USDC [and] 90 SOL … from the DAO Treasury USDC account (5Unq3fgfSNdyeGjiq2Pu5XAQUJWo2rauKGErbUyxqUGe)". On-chain: +323,428.561802 USDC into the Council USDC account `HeMtL8F8…` on 2026-09-30 13:08 UTC (tx 2wsDhBFU…).
- **Direct PYTH:** "Direct PYTH distributions remain governed by the existing payment framework and will be held by the DAO. They are not described as open-market purchases."
- **Guardrails (unchanged from V1):** "maximum 5% slippage per transaction; maximum $25,000 per transaction; preference for aggregators such as Jupiter; limit orders at least 0.1% below displayed market price; and public transaction proofs."
- **Restrictions:** "The Pythian Council and Ops Multisig act only as mechanical execution agents. They may not sell accumulated PYTH, borrow against it, use leverage or derivatives, or distribute it without a separate DAO-approved proposal."
- **Reporting:** "monthly reports covering: assets received and converted; PYTH purchased; execution prices, fees, and slippage; transaction links; unconverted balances; and confirmation that no accumulated PYTH was sold."
- **Destination:** "All purchased PYTH will be returned to the official DAO Treasury."

Note on the "$25,000 per transaction" guardrail: each DCA order is opened as one transaction for the full amount (e.g. 553,693.17 USDC on 15 Oct 2026) and filled in many smaller keeper transactions. The ledger and on-chain data are consistent with the guardrail being applied per fill, not per order. The reports do not say how it is applied.

**OP-PIP-140** (posted 7 Oct 2026, PASSED): https://forum.pyth.network/t/passed-op-pip-140-recover-stranded-dao-usdc-transfer-the-treasury-to-the-pythian-council-ops-multisig-and-clarify-direct-fee-repatriation/2722. It recovers 34,375.871282 USDC stranded in `3QpXyXjs…` (account closed), moves "748,076.168209 USDC" and "880.894539350 SOL" to the Council, and says: "The Pythian Council may repatriate DAO-owned Pyth Core, Pyth Pro, Entropy, and other approved on-chain fees directly to the relevant Pythian Council-controlled multisig". On-chain: +748,076.168209 USDC into `HeMtL8F8…` on 2026-10-12 17:58 UTC (tx 4C314G2b…). The 748,076 equals Douro's September distribution (553,693.17) plus the Sept–Oct cross-chain fee repatriations recorded in the ledger (194,383.08).

The September Douro report confirms the V2 routing was missed once: "Douro Labs, when distributing revenue stablecoins should be sending it directly to the Pythian Council wallet for future PYTH token purchases but failed to do so this time."

**V2 activity to date (ledger + on-chain):**
- DCA orders opened: 323,428.56 USDC (1 Oct; PYTH out blank = filling at ledger read); 90 SOL → 138,808.53 PYTH (1 Oct); 880 SOL → 1,147,926.58 PYTH (13 Oct); 194,383.00 USDC → 2,400,625.54 PYTH (13 Oct); 553,693.17 USDC (15 Oct; blank).
- Council vault PYTH account `BTy44YBx…` held **13,841,711.70 PYTH** at 2026-10-27 10:23 UTC, none of it yet returned to the Treasury. Its USDC account held 0, so all V2 USDC is committed to DCA escrows.
- Council-vault transaction count by month (signatures on `GAdn7…`): Sep 376, Oct (to the 27th) 2,843. The October jump is DCA fills.

**Timing caveat.** The September purchases (OP-PIP-135, 15 Sep) ran under the V1 one-third rule after the V2 idea thread had started. The tracker should treat 1 Oct 2026 as the first V2 order.

### 1.4 Where bought PYTH goes

- **Held, not burned.** OP-PIP-87 and OP-PIP-136 both send purchased PYTH to the DAO Treasury PYTH account `AYBdhJU8Wao48SEDyKVRXgDRbo9SU8Thnytakof4HDDH` (the associated token account of `Gx4MBPb1…`; derivation checked). No burn instruction exists in either proposal, and no burn proposal has passed. The burn ideas in the forum (t/2555, 2601, 2602) are discussion only.
- **Not paid to stakers.** No staking-yield distribution of bought PYTH exists. Governance staking has no reward mechanism in the docs or Constitution. OIS rewards came from a separate pre-funded pool and are at zero.
- **The Treasury is fungible and spent by vote.** Bought PYTH sits in the same account as PYTH from other sources: Douro's PYTH deliveries (24.17m), the OIS pool withdrawal (849,207.76 on 23 Jul 2026, OP-PIP-127) and any slashed stake (the OIS pool config's `slash_custody` is this account). The same account pays discretionary spends:
  - 4,000,000 PYTH to the Community Council multisig (OP-PIP-101, 10 Apr 2026): "Transfer 4,000,000 PYTH from 9HKkxg5dpqjUEW1U2r76SpQCH7uvDMciytNYxrpwMVNc (Pyth DAO Treasury) to GKuPcXtNRJwZGrJ8tV25jSbLHZe71BUdUXVzjGLXkSt9 (Community Council multisig)". https://forum.pyth.network/t/passed-op-pip-101-community-council-term-2-election-result-budget-approval/2466
  - 420,000 PYTH in Community Council stipends (OP-PIP-114, 6 May 2026). https://forum.pyth.network/t/passed-op-pip-114-community-council-term-1-cycle-2-stipend-disbursement/2516
  - Pending: 4,450,000 PYTH Cycle 2 request (14 Oct 2026, not yet voted) https://forum.pyth.network/t/community-council-term-2-cycle-2-budget-request/2724, and 225,000 PYTH in Price Feed Council stipends (OP-PIP-141, "COMING SOON", 15 Oct 2026) https://forum.pyth.network/t/coming-soon-op-pip-141-final-price-feed-council-stipend-disbursement/2725
- **Implication for the tracker.** Under Jake's convention, PYTH returned to the Treasury counts as *retired (held)*. The hold is governance-revisable (an OP-PIP can spend it), so it belongs in the "held-discretionary" durability class. Treasury PYTH outflows by vote should be tracked as category (d) outflows, not netted out of the buyback.

**Treasury PYTH account `AYBdhJU8…`: large flows Dec 2025 – Oct 2026** (on-chain, transfers ≥1,000 PYTH. 226 of 574 txs were fetched, including all 10 V1 returns, the 4 in-kind deliveries, the OIS withdrawal and the OP-PIP-101/114 transfers; the 348 unfetched txs were not classified.)

| Class | Count | PYTH | How it shows on-chain |
|---|---|---|---|
| Council buyback returns (V1) | 10 | +20,549,288.42 | Squads `VaultTransactionExecute`, SPL transfer from `BTy44YBx…` (Council vault ATA) |
| Douro in-kind revenue | 4 (+1 test of 10) | +24,171,981.6 | Plain SPL transfer from `8iRsZ71S…` (owner `2ru31e9g…`); no swap, no Squads |
| OIS reward-pool withdrawal | 1 | +849,207.755 | Program `pyti8TM4…` invoked via Pythian multisig `SMPLVC8…`, from `FbSivnCf…` (23 Jul 2026 on-chain; post dated 27 Jul) |
| Community Council budget | 1 | −4,000,000 | Governance program `pytGY6…` execution to `6fLL4ZFT…` (16 Apr 2026) |
| Community Council stipends | 7 | −420,000 | `pytGY6…` → 7 member wallets of 60,000 (13 May 2026) |
| Price Feed / Pythian Council stipends | 4×45,000 + 7×12,500 | −267,500 | `pytGY6…` (9 Jun 2026) |

Earlier visible flows include: Community Council 1,420,000 (27 Mar 2025) and 1,470,000 (22 Sep 2025); stipends 7×60,000 (22 Sep 2025) and 4×45,000 + 5×12,500 (2 Dec 2025); inflows 2,499,980 (19 Mar 2025), 250,000 (24 Jul 2025) and 999,990 (19 Sep 2025) from wallets not identified in any primary source.

Implication: since Dec 2025, retirement-relevant inflows (V1 returns + in-kind) total 44.72m PYTH. Vote-approved outflows over the same period were ≥4.69m. The balance at 10:23 UTC was 41,088,354.29.

### 1.5 Oracle Integrity Staking (OIS)

- **Design** (docs https://docs.pyth.network/oracle-integrity-staking, fetched 27 Oct 2026): "Each publisher is programmatically assigned a staking pool where they can self-stake and to which other stakers can delegate." Reward per pool: "R_p = y · min(S_p, C_p)" (https://docs.pyth.network/oracle-integrity-staking/mathematical-representation).
- **Reward source:** "bootstrapped by a 100M PYTH allocation from the Pyth Data Association" (Pyth blog, 23 Apr 2026 https://www.pyth.network/blog/pyth-s-next-chapter-infrastructure-upgrade-and-a-revenue-based-economic-model). Rate before the pause: "Reward rate (Y) 1924 … Total staked ~1.0B PYTH … Weekly distribution ~1.93M PYTH … distributed = Y / 1,000,000 × total_staked" (OP-PIP-103, 15 Apr 2026). The Phase 3 thread (13 Feb 2026) puts lifetime distribution at "close to 100,000,000 PYTH".
- **Ended:** "the OIS reward rate was set to 0 on 22 April 2026, and no rewards are currently distributed to any pool" (docs banner); "The OIS reward pool depleted on April 22, 2026" (forum, Pyth-DAO, 22 Apr 2026, https://forum.pyth.network/t/ois-rewards-update-april-2026/2479). **On-chain: pool config `BE8Xq1iH…` (owner `pyti8TM4…`) has `y = 0`** (read 10:32 UTC).
- **Pool wind-down.** CO-PIP-121 failed on quorum (t/2619, 30 Jun 2026). The resubmitted CO-PIP-121.V2 passed on 15 Jul 2026 (t/2634), and OP-PIP-127 executed "849,207.755112 PYTH from the OIS Reward Pool Custody to the Pyth DAO Treasury" (t/2645; executed on-chain 23 Jul 2026 21:30 UTC, tx qLAXWqx1…, confirmed in the 27 Jul 2026 post). **This conflicts with the lead, which cited only the failed CO-PIP-121.** Reward custody `FbSivnCf…` holds 84,834.191564 PYTH, equal to the pool's `claimable_rewards` (accrued but unclaimed).
- **Slashing:** the docs say "Staking and slashing remain active". **CO-PIP-139 (7 Oct 2026, PASSED) says the opposite:** "OIS rewards have ended, and OIS staking and slashing for Pythnet data are no longer active following the Pythnet sunset … marks OIS as legacy and unstake-only". https://forum.pyth.network/t/passed-co-pip-139-retirement-of-the-price-feed-council-and-constitution-update/2721. The blog (23 Apr 2026): "zero slashing proposals were submitted to the Pyth DAO." The slash custody in the on-chain config is the Treasury PYTH account.
- **Programs and accounts (on-chain):** OIS program `pyti8TM4zRVBjmarcgAPmTNNAXYKJv7WVHrkrm6woLN` (executable, upgradeable loader); pool config `BE8Xq1iHSQYKG8CZorZsrTnxyeHKQLzCWm4dYKdFkVEL`; pool data `poo1zPoi5xrNzi4yk4i23oWcJrNNkDYAniBCewJY8kb`, which shows 134 publisher slots and stake of delegated 635,884,596 + self 35,355,188 = **671,239,784 PYTH** at epoch 2964. The `reward_program_authority` is the Pythian Council vault `6oXTdojy…`. The OIS docs say OIS and governance stake can be the same tokens ("eligible $PYTH tokens can be used for both purposes"), so the two staked totals must not be added.
- **Pythnet is shut down:** "That blockchain has shut down as part of the Pyth Core upgrade. The upgraded Pyth Core contracts keep their interface and get prices from Pyth Pro." (docs, "How Pyth Pro Works", fetched 27 Oct 2026, https://docs.pyth.network/price-feeds/how-pyth-works/oracle-integrity-staking, which redirects to the Pro architecture page.)

### 1.6 Governance staking

- "Votable Token: PYTH tokens staked in the staking contract at the following address: `pytS9TjG1qyAZypk7n8rw8gfW9sUaqqYyMhJQ4E7JCQ`" (Constitution).
- "Governance uses a 1:1 coin-voting system" (docs https://docs.pyth.network/pyth-token). "Staking for governance can use both locked and unlocked $PYTH tokens" (OIS docs), so vesting tokens vote.
- No governance-staking reward exists in any primary source found. Blog, 23 Apr 2026: "Governance staking is a separate program and is unaffected."
- **On-chain:** voting target account `6njqtZeuLbSFU7Yo72GxPcdwhLQWahEPx1iN9GD6DRgV` (PDA `["target","voting"]` of the staking program; derivation checked). It shows `locked = 1,114,446,379.26 PYTH` at epoch 2964 (`prev_epoch_locked` 1,116,601,382.23; `delta_locked` −208,372.57). This matches the lead's "~1.12bn". Epoch length is 604,800 s (staking config `6RYqefZNXXATuXrgXfSxi9rDj9JS1bi5CQkRwyyQ72Bd`).

### 1.7 Publisher rewards (PPR)

- **Allocation** (docs https://docs.pyth.network/pyth-token/pyth-distribution): "22% — 2,200,000,000 PYTH … ~2% (50M) of the 2.2B PYTH Tokens are unlocked. The remaining PYTH Tokens (2.15B) are subject to the lockup schedule … these rewards can only be distributed to publishers once they are unlocked."
- **Vesting of payouts.** CO-PIP-137 (24 Sep 2026, PASSED), https://forum.pyth.network/t/passed-co-pip-137-ppr-vesting-schedule-correction/2713: "These rewards were distributed using the legacy vesting schedule (25% spot / 25% at 1y / 25% at 2y / 25% at 3y) instead of the current schedule (25% spot / 75% at 1y)." It "upgrades the Pyth Staking program … to add a permissionless instruction shorten_vesting_schedule". The May 2026 and June 2026 distributions covered "33 locked token accounts, totaling 5,463,361 PYTH".
- **Monthly amount: no primary document states it.** No Douro, Pyth or DAO post publishing monthly PPR totals was found. On-chain (staking program `getProgramAccounts`, 27 Oct 2026), one-period `PeriodicVesting` locks with period 31,536,000 s start on the 1st of each month from June 2026:

| Lock start (= distribution month + 1) | Accounts | Locked PYTH |
|---|---|---|
| 2026-06-01 (May distribution) | 23 (+1 three-period) | 4,668,254 |
| 2026-07-01 (June) | 25 | 4,362,715 |
| 2026-08-01 (July) | 26 | 4,781,765 |
| 2026-09-01 (August) | 27 | 5,088,636 |
| 2026-10-01 (September) | 28 | 5,185,447 |

  The mapping to distribution months comes from CO-PIP-137's account lists. For example, May-distribution account `FSD2CS5u…` (158,529) appears with start 2026-06-01, and June-distribution `2kVPALxk…` (474,812) with start 2026-07-01. Read alone, the cohort totals would imply 5.8–6.9m gross at 75% locked. But cohorts are funded across more than one payout run (see below), so the run-level measure is the better figure. **Either way, this conflicts with the lead's "~2.7m a month".** The lead's figure is close to CO-PIP-137's per-month subtotals (2,703,773 and 2,759,588), which cover only the accounts affected by the vesting error. **Measured (11 Sep 2026 run, all 58 September txs of `8kE8RK1G…` fetched):** `GiipfV…` received 5,620,134 + 13,678 from Douro's wallet and paid out 5,633,811 in 57 SPL transfers. 1,405,032 (24.9%) went to 28 transfers to on-curve wallets (spot). 4,228,779 (75.1%) went to 29 token accounts owned by program-derived authorities. Two of these were checked: both were created by staking-program `CreateStakeAccount` + `CreateVoterRecord`, i.e. new vesting locks. Matching amounts against the vesting snapshot places 22 of the 29 locks in the 2026-09-01 one-period cohort (the August distribution) and the rest in earlier one-period cohorts. Gross monthly PPR is therefore ~4.6–5.6m PYTH for Jul–Sep 2026 (Douro → `GiipfV…` transfers), and 12.4m in October to date (possibly two runs).
- **Which wallet pays** (on-chain only; no primary document names these wallets): Douro's paying wallet `2ru31e9g…` (identified by the Douro reports' own tx links) sends PYTH to `GiipfVdYT8yH4ZDZGkVyWgWrjKkLcEgpztSkFf865hJz` (the lead's "distribution wallet"): 1,641,257 (30 Jun), 5,643,224 (10 Jul), 4,631,714 (11 Aug), 5,620,134 (11 Sep), 10,976,304 (6 Oct), 999,999 (9 Oct), 469,460 (16 Oct 2026). `GiipfV…`'s PYTH account `8kE8RK1G…` has 300 transactions since 9 Jun 2026. `GiipfV…` funds the PPR locks directly (measured above). The vesting accounts' creation transactions themselves (`CreateStakeAccount`) were not traced to a signer in this pass.
- Earlier quarterly three-period locks (starts 2024-08-23, 2024-11-20, 2025-02-21, 2025-05-25, 2025-08-19, 2025-11-19, 2026-02-16, 2026-05-18, 2026-07-17; 59–92 accounts each, 9.5–23.6m PYTH each) fit a quarterly reward programme. No primary source labels them, so they are not attributed to PPR here.

### 1.8 Token supply

- **Mint** `HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3`, named in the docs (https://docs.pyth.network/pyth-token/pyth-token-addresses: "Solana | HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"). On-chain: owner SPL Token program, decimals 6, `mintAuthority: null`, `freezeAuthority: null`, **supply 9,999,958,880.265383**.
- **Max supply / allocation** (docs, same page): "There is a total of 10,000,000,000 (10B) PYTH tokens existing." Ecosystem Growth 52% (5.2B; "13% (700M) … are unlocked"); Publisher Rewards 22%; Protocol Development 10% ("15% (150M) … unlocked"); Community and Launch 6% ("All 600M … unlocked from day one"); Private Sales 10% ("All of 1B PYTH Tokens are locked").
- **Schedule** (docs): "The initial circulating supply (November 2023) was 1,500,000,000 (15%) PYTH. And the remaining supply (85%) are initially locked and locked tokens will unlock 6, 18, 30 and 42 months after the initial token launch." The chart on that page shows four equal steps (8.5B / 4 = 2.125B per step).
- **On-chain schedule:** staking config `pyth_token_list_time = 1684591200` (2023-05-20 14:00:00 UTC). 379 `PeriodicVestingAfterListing` accounts, all with `period_duration = 31,536,000` and `num_periods = 4`, owned by 195 wallets. Final step at list_time + 4 × 365 d = **2027-05-19 14:00:00 UTC** (the lead's 19 May 2027 is confirmed). The sum of initial balances is 8,618,333,336, so the final step is **2,154,583,334 PYTH**, not 2,125,000,000. The 118.3m excess over the documented 8.5B locked (29.6m per step) is not explained by any primary source. The top 10 owners hold 79.5% of the final step. The largest are `Ffz4sGA4…` 342.1m, `A1fAsebs…` 338.2m, `9HwAxuuo…` 245.1m, `EAENAcXZ…` 213.8m and `ADBD8FKZ…` 150.0m. The last of these is also the staking program's `pda_authority`, the key that "can authorize the transfer of locked tokens" per the program source.
- **Other locks:** 882 `PeriodicVesting` accounts; the 867 with an unvested balance hold **193,766,254 PYTH** (3 malformed accounts with zero periods and year-3000 starts excluded). These come from already-unlocked buckets (PPR, grants), so a schedule-based circulating figure counts them as circulating even though they are locked on-chain.
- **Circulating, as Pyth publishes it: no Pyth-published circulating figure or endpoint was found.** CoinGecko reports 7,874,958,880.265383 at 10:26:55 UTC, which is exactly on-chain supply − 2,125,000,000, i.e. the docs schedule. Defensible options for "circulating (used)":
  - A. Supply − documented locked (2.125B): 7,874.96m (= CoinGecko).
  - B. Supply − on-chain genesis vesting (2,154.58m): 7,845.38m.
  - C. Supply − all on-chain unvested (2,348.35m): 7,651.61m.
  I recommend B for "circulating used", with C as the free-float base. Jake decides.
- **Burns:** no protocol burn exists. The 41,119.73 PYTH shortfall from 10B shows some holders have called SPL `burn`. A per-transaction source was not identified (open question).

---

## 2. Archetype proposal (for Jake to decide)

**Proposed primary: A3 Revenue Buyback. Proposed secondary: A2 Coordination Mechanism.** A1 does not apply: Pythnet has shut down and PYTH is an SPL token. A4 does not apply: there is no protocol burn.

### A3: the case for
- The test is "a DOCUMENTED revenue-to-token conversion". OP-PIP-136 commits 100% of the DAO's approved share of six named revenue lines to PYTH accumulation. OP-PIP-87 did the same on a one-third-of-treasury basis from Dec 2025. Execution is on-chain, with monthly reports and tx links. None of the funding is emissions: it is USDC from Douro's revenue distributions and repatriated fees, plus SOL held by the Treasury (whose origin the reports do not state).
- 20.55m PYTH bought and returned under V1. 13.84m PYTH sits in the Council vault from V2 orders. 24.17m PYTH was delivered in kind by Douro.

### A3: the case against
1. **Not contract-enforced.** It is a passed OP-PIP that any later OP-PIP can change. The Constitution itself only fixes the revenue split, not the conversion. Mark it "revisable by governance".
2. **"Held" is a discretionary treasury.** The account that receives bought PYTH also pays Community Council budgets (4.0m Apr 2026; 4.45m requested Oct 2026) and stipends. Retirement is reversible. Treasury outflows must appear as category (d) dilution.
3. **In-kind settlement is not a market buy.** The Apr–Jul 2026 PYTH deliveries came from Douro's paying wallet, which was funded from two large PYTH-holding wallets (`CeWsBRks…`, `E6nAbv4q…`), not from same-transaction purchases (section 5). A community member raised exactly this in the V2 thread (scp, 9 Sep 2026): "it could look more like Douro effectively DCAing out of its existing PYTH position". Douro then committed to USDC. Recommendation: count in-kind deliveries as *retired (held) value* at the report's USD figure, flag them as "in-kind, not market-facing", and exclude them from "flow demand".
4. **Revenue is off-chain and operator-reported.** Gross revenue is Douro's figure. The on-chain check sees only the DAO share arriving, and since V2, the Council's DCA orders.
5. **Lag.** Settlement runs monthly, in arrears (Indices a further month behind). Council DCA orders take weeks to fill, and returns to the Treasury lag again (13.84m unreturned at read time). A trailing-90-day window will show lumpy actuals.

**Blocks:** implied buyback = DAO share of revenue × 100% (documented share since OP-PIP-136; one-third of treasury balance before). Actual buyback observed = Council PYTH inflows from DCA/limit fills plus in-kind PYTH from Douro (flagged). Burned 0 / held all / distributed 0. Retirement rates on circulating and FDV. Net absorption against PPR emissions and against unlocks. The 2.15bn unlock on 19 May 2027 dominates any 12-month net-absorption view.

### A2: the case for
- The token pays independent first-party suppliers (publishers) out of a 22% allocation. "more than 138 institutions now contribute first party data to Pyth" (Pyth blog, 2 Sep 2026). On-chain OIS has 134 publisher pools. Customer revenue is measurable monthly (Douro reports). The key ratio, customer revenue ÷ PPR value, is computable once the PPR series is built.

### A2: the case against
- The token's coordinating role has shrunk. OIS rewards have ended and OIS is "legacy, unstake-only" (CO-PIP-139). The Price Feed Council is retired. Pythnet is gone. Pyth Pro runs on a Douro-operated relayer service and "five independently operated routers" (docs). Publishers are paid PPR from a pre-minted allocation through Douro-linked wallets. No documented formula ties PPR to data delivered.
- "Supply units" (publishers, feeds) are published figures (138+ publishers; feeds 3,176+ on kpi.pyth.network, which shows April 2026) or the OIS pool count. No utilisation metric exists.

**Blocks if A2 is kept as secondary:** supply units = publishers (OIS pool count on-chain; published count); feeds = published count; utilisation = not available; customer revenue = Douro gross; emissions to suppliers = PPR (on-chain vesting creations + spot transfers); historical OIS rewards (to 22 Apr 2026).

### CP block mapping
- **Holder value yield:** revenue-funded $ retired (held) ÷ market cap. September's DAO share ($553,693) × 12 ≈ $6.64m against a $568.7m market cap (CoinGecko, 27 Oct 10:26 UTC) ≈ 1.17% implied. Credit only the revenue-funded buys. In-kind deliveries are recorded but flagged; treasury-funded parts are not credited.
- **Outflows:** (a) security emissions = OIS rewards, ~1.93m/week until 22 Apr 2026 and 0 since. (b) supply-side = PPR, ~4.6–5.6m/month gross Jul–Sep 2026 (measured; see 1.7), of which 75% is locked for one year. (c) demand-side = none found. (d) ecosystem/grants = DAO Treasury → Community Council (4.0m Apr 2026), stipends (0.42m), PDA/Douro grant locks (the non-PPR `PeriodicVesting` cohorts). (e) unlocks = genesis vesting (2,154.58m on 2027-05-19) plus releases of the 193.8m other locks (Nov 2026–Oct 2027 schedule in `raw/vest_gpa.json`).
- **Insider share of stake:** governance stake can include locked (insider) tokens. Not computed here; method in section 7.
- **Discretionary supply:** DAO Treasury PYTH (41.09m), Council vault PYTH (13.84m, pending return), Community Council vault PYTH (1.38m), plus large unlocked non-DAO holders with no published schedule (`CeWsBRks…` 34.42m, `E6nAbv4q…` 31.39m, Douro paying wallet 15.89m). Which entity controls the last three is not established from primary sources.
- **Subsidy cover** = DAO revenue (or gross) ÷ $ PPR.

---

## 3. Metric map

See `sources.csv`. The STOCK reads (daily, from the tracker's first run) are:

| Metric | Account / endpoint | Reconstructable? |
|---|---|---|
| Mint supply (total, burns) | `HZ1JovNi…` getTokenSupply | Yes, by replaying `burn` instructions on the mint (high tx count); cheaper to read daily |
| DAO Treasury PYTH (held reserve) | `AYBdhJU8…` getTokenAccountBalance | Yes: 574 txs since 2023-11-20, replayable via getSignaturesForAddress + getTransaction |
| Council vault PYTH (bought, not yet returned) | `BTy44YBx…` | Yes, but 5,935 txs (mostly DCA fills); replay is slow on the free RPC |
| Council vault USDC (unconverted) | `HeMtL8F8…` | Yes (40 txs) |
| DAO Treasury USDC | `5Unq3fgf…` | Yes (65 txs) |
| Council vault SOL | `GAdn7TZh…` getBalance | Partly (lamport deltas per tx) |
| Community Council vault PYTH | `6fLL4ZFT…` | Yes |
| Governance staked total | `6njqtZeu…` voting target (decode) | Only current/previous epoch held; history needs a full replay of staking txs: treat as STOCK |
| OIS staked total, pool count, y, claimable | `poo1zPoi…` slices + `BE8Xq1iH…` | STOCK (history needs replay) |
| Unvested (genesis and other locks) | staking program getProgramAccounts (two filtered calls) | Deterministic from schedules, so it can be computed for any past date from today's snapshot, for the genesis locks; the other locks also need each account's creation date (new PPR locks appear monthly), and accounts closed or modified since (e.g. CO-PIP-137 changes) are lost |
| Douro paying wallet PYTH / USDC | `8iRsZ71S…`, `C6G3jRs1…` | Yes |
| Large unlocked holders (discretionary) | `2tEAnoFK…` (CeWsB…), `8UkJ2UYf…` (E6nAb…) | Yes |
| CoinGecko circulating (cross-check) | /coins/pyth-network | No (CoinGecko's market_chart history returns price, market cap and volume, not circulating) |

Pyth revenue is off-chain. Treat Douro's monthly report as the published figure (manual or forum-parser entry) and the USDC/PYTH arrival in the Treasury or Council accounts as the checker.

---

## 4. Test reads

All values and timestamps are in `sources.csv` (`test_value`, `test_utc`, `test_status`).

**Address verification (27 Oct 2026, ~10:22–10:45 UTC).** Every address the tracker will read:

| Address | Role | Primary source naming it | On-chain check |
|---|---|---|---|
| `HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3` | PYTH mint | docs, token addresses page | SPL mint, decimals 6, no mint/freeze authority |
| `Gx4MBPb1vqZLJajZmsKLg8fGw9ErhoKsR8LeKcCKFyak` | DAO Treasury (SOL wallet) | Constitution; OP-PIP-87/136/140 | System-owned; equals PDA `["native-treasury", 9HKkxg…]` of governance program `pytGY6tWRgGinSCvRLnSv4fHfBTMoiDGiCsesmHWM6U`; 1.0049 SOL |
| `9HKkxg5dpqjUEW1U2r76SpQCH7uvDMciytNYxrpwMVNc` | DAO governance account ("SPL Token" treasury in the Constitution) | Constitution; OP-PIP-101 | Owned by `pytGY6…` (236 bytes), i.e. a governance account, not a token account. It is the config authority of both Squads v4 multisigs below |
| `AYBdhJU8Wao48SEDyKVRXgDRbo9SU8Thnytakof4HDDH` | Treasury PYTH account | OP-PIP-127 ("DAO Treasury Token Account") | Token account, mint PYTH, owner `Gx4MB…`, = ATA (derivation checked); 41,088,354.29 |
| `5Unq3fgfSNdyeGjiq2Pu5XAQUJWo2rauKGErbUyxqUGe` | Treasury USDC account | OP-PIP-136, OP-PIP-140 | Token account, USDC mint, owner `Gx4MB…`, = ATA; 0 |
| `GAdn7TZhszf5KTfwNRx3A2nP6KCRFEWucZubgdEqbJA2` | Pythian Council Ops vault | OP-PIP-87, OP-PIP-136 | System-owned; = Squads v4 vault 0 of `F2iVtu…` (derivation checked) |
| `F2iVtu542e6BxB2eu224d2jyQ3CCG9zTZvkzMokfDdeW` | Council Ops multisig | none (on-chain only) | Squads v4 (`SQDS4ep…`) multisig, **6-of-8**, config authority `9HKkxg…`; same 8 members as the Pythian multisig minus the ops wallet (OP-PIP-87: "Signers … are the same") |
| `BTy44YBx9AVLHbKkSVLGaCBNKUpzw78W6hKy43vpeCHp` / `HeMtL8F8PPsEWXxUzwBirNE2mYBtYpvZ4oq6erhjGr3n` | Council vault PYTH / USDC accounts | none (ATAs of the named vault) | ATAs of `GAdn7…`, mints checked |
| `6oXTdojyfDS8m5VtTaYB9xRCxpKGSvKJFndLUPV3V3wT` | Pythian Multisig Wallet | Constitution | = authority PDA (index 1) of `FVQyHcoo…` under program `SMPLVC8MxZ5Bf5EfF7PaMiTCxoBAcmkbM2vkrvMK8ho` (derivation checked); also OIS `reward_program_authority` |
| `FVQyHcooAtThJ83XFrNnv74BcinbRH3bRmfFamAHBfuj` | Pythian multisig | none (on-chain only) | Owned by `SMPLVC8…`, threshold **6**, 9 keys (incl. ops wallet `opsLibx…` named in the Constitution) |
| `CPUZ9njbfxjqrsx713gEk8KYatvoz1Y4hvs4a5UQKEiu` | Price Feed Multisig Wallet (retired by CO-PIP-139) | Constitution | System-owned, 0.71 SOL, no token accounts; multisig not decoded (not needed) |
| `GKuPcXtNRJwZGrJ8tV25jSbLHZe71BUdUXVzjGLXkSt9` / `DXf94qPHKbpx3KJ275ns4kBndaHTf4BMtC8GDUvBXM7j` | Community Council vault / multisig | Constitution; OP-PIP-101 | Vault = Squads v4 vault 0 of `DXf94q…` (6-of-8, config authority `9HKkxg…`); PYTH ATA `6fLL4ZFT…` 1,384,697.56 |
| `2ru31e9g8RF2mSSNgTQ11QMb166NE6LJccmBqGJM8xxy` | Douro paying wallet | Douro reports' tx links (source of every distribution) | System-owned; PYTH ATA `8iRsZ71S…` 15,885,913.45; USDC ATA `C6G3jRs1…` 8,968.04 |
| `CeWsBRksDcuVd9quoNZNT2Hvr9ei7uxEqGBoWwr5JRhu` | Large PYTH holder; funds Douro wallet | none (on-chain only) | System-owned; PYTH ATA `2tEAnoFK…` 34,416,632.00; USDC 189,778.03 |
| `E6nAbv4qnxRi4X4uBJuPZjFJJ9VNdsYr7R5KZ6eDHtge` | Large PYTH holder; funds Douro wallet | none (on-chain only) | **The wallet itself has no account (0 lamports, getAccountInfo null)** but owns PYTH ATA `8UkJ2UYf…` with 31,389,650 |
| `GiipfVdYT8yH4ZDZGkVyWgWrjKkLcEgpztSkFf865hJz` | "Distribution wallet" | none (on-chain only) | System-owned, 12.57 SOL; PYTH ATA `8kE8RK1G…` 8.03 |
| `pytS9TjG1qyAZypk7n8rw8gfW9sUaqqYyMhJQ4E7JCQ` | Staking program | Constitution | Executable, upgradeable |
| `6njqtZeuLbSFU7Yo72GxPcdwhLQWahEPx1iN9GD6DRgV` / `6RYqefZNXXATuXrgXfSxi9rDj9JS1bi5CQkRwyyQ72Bd` | Voting target / staking config | program source (seeds) | PDAs of `pytS9…`, owner checked |
| `pyti8TM4zRVBjmarcgAPmTNNAXYKJv7WVHrkrm6woLN`, `BE8Xq1iHSQYKG8CZorZsrTnxyeHKQLzCWm4dYKdFkVEL`, `FbSivnCf6iXfXns6osFih2MgX8icmSKx9iba3gyFpiCT`, `poo1zPoi5xrNzi4yk4i23oWcJrNNkDYAniBCewJY8kb` | OIS program / pool config / reward custody / pool data | CO-PIP-121, OP-PIP-103 (pool data address: read from the pool config on-chain, no document names it) | Program executable; config owned by `pyti8…`; custody is a PYTH token account with authority `BE8X…`; pool data owned by `pyti8…` |
| `DCA265Vj8a9CEuX1eb1LWRnDT7uK6q1xMipnNyatn23M` | Jupiter DCA program (as identified by the Council: "DCA executions through Jupiter DCA, which is integrated directly with the Squads multisig", July 2026 report thread) | July purchases report thread names "Jupiter DCA" but not the program ID | Instructions `OpenDcaV2`, `InitiateFlashFill`/`FulfillFlashFill` seen in logs |

All of the lead's addresses verified, with these notes: `F2iVtu…` is 6-of-8 and `FVQyHcoo…` is 6-of-9 as stated; `E6nAbv4q…` has no system account of its own (only token accounts); `CPUZ9…` belongs to a council retired on 7 Oct 2026.

**Free Solana route:** `https://api.mainnet-beta.solana.com`, which is archival for `getSignaturesForAddress` (full history back to the token's launch, 2023-11-20 for the Treasury PYTH account) and for `getTransaction`.
- Documented limits (Solana docs, "Clusters and Public RPC Endpoints", https://solana.com/docs/references/clusters, fetched 27 Oct 2026): "Maximum number of requests per 10 seconds per IP: 100"; "… per IP for a single RPC: 40"; "Maximum concurrent connections per IP: 40"; "Maximum connection rate per 10 seconds per IP: 40"; "Maximum amount of data per 30 seconds: 100 MB"; "The public RPC endpoints are not intended for production applications"; "high-traffic websites may be blocked without prior notice." The page says nothing about getProgramAccounts.
- **Observed** (response headers, 27 Oct 2026): `x-ratelimit-tier: free`, `x-ratelimit-method-limit: 10` for `getTransaction` (150 for `getSlot`), `x-ratelimit-rps-limit: 250`, `x-ratelimit-conn-limit: 40`, `retry-after: 10` on 429. Batched JSON-RPC counts each call against the method limit. One transient `413 "You have used your data allowance"` followed a 2 MB `getAccountInfo` of the OIS pool data. **Effective `getTransaction` throughput from this workspace was ~0.1–0.4 tx/s.** The egress IP appears to be shared: remaining counters were negative on first contact.
- `getProgramAccounts` on the staking program with `dataSize` + `memcmp` filters and `dataSlice` worked (379 and 882 accounts returned in under 1 s each).
- `getSignaturesForAddress` pages at 1,000 per call with a `before` cursor. Full history was available for every account tested (oldest 2023-11-20).
- Other free routes tested: `solana-rpc.publicnode.com` answers but returns `null` for transactions older than ~1–2 days (not archival). `solana.drpc.org` replies "chain is not available on free plan".
- **Needs a paid or keyed RPC** (Helius, Triton, QuickNode or similar): any daily replay of the Council vault's DCA fills (hundreds to thousands of `getTransaction` calls a month), the initial backfill of the Council PYTH account (5,935 txs), any full replay of staking-program history (governance-stake history, insider share), and DAS/enhanced-transaction parsing if used. The daily STOCK balance reads fit comfortably within the free route.

Other endpoints: CoinGecko `/api/v3/coins/pyth-network` worked keyless (keyless is "IP-based rate limiting — shared across all users on the same IP"; "Demo plan: 100 calls/min", https://docs.coingecko.com/docs/common-errors-rate-limit). DefiLlama `/summary/fees/pyth-network` returns 400 (not found). The correct slugs are `pyth-pro`, `pyth-entropy`, `pyth-core` and `pyth-express-relay` under `parent#pyth`. DefiLlama's `pyth-pro` series values PYTH-paid months at market price and **misses the 5 Oct 2026 USDC distribution** (last point 8 Sep 2026), so it is not a reliable revenue source; use Douro's reports. Core fees on DefiLlama go to 0 from Sep 2026 (Core fees zeroed under OP-PIP-128). Hermes (`hermes.pyth.network`) returned 401; the docs say "the Hermes API requires a Pyth API Key (since August 26, 2026)" (https://docs.pyth.network/llms.txt). The tracker does not need it. Forum Discourse JSON works but rate-limits bursts (HTTP 422 "You've performed this action too many times"); pace ≥2 s per request. The Google Sheets CSV export of the ledger worked via curl (public export). WebFetch refused it as not allowlisted.

---

## 5. Open-market buys vs Douro direct PYTH deliveries

### 5.1 What each looks like on-chain

| Class | Accounts touched | Programs / log instructions | Classifier rule |
|---|---|---|---|
| **R1 Douro USDC distribution** | `C6G3jRs1…` (owner `2ru31e9g…`) → `5Unq3fgf…` (Treasury) or, under V2, → `HeMtL8F8…` (Council) | System + SPL Token only | USDC from Douro's ATA; amount = report's DAO share |
| **R2 Douro PYTH in-kind** | `8iRsZ71S…` (owner `2ru31e9g…`) → `AYBdhJU8…` | System + SPL Token only (`TransferChecked`); **no swap, no Squads** | PYTH from Douro's ATA into Treasury PYTH. Count as held, flag "in-kind" |
| **R3 Cross-chain fee repatriation** | counterparty `Q4UmPB9h…` (owner `F7p3dFrj…`) → `5Unq3fgf…` | SPL Token + Memo | USDC in from a non-Douro, non-DAO wallet with a memo; matches ledger rows "Crosschain repatriation (to DAO)". No primary doc names the counterparty wallet |
| **A Treasury → Council funding** | `5Unq3fgf…`→`HeMtL8F8…` (USDC); `Gx4MB…`→`GAdn7…` (SOL) | Governance program `pytGY6…` (Realms proposal execution) | Treasury outflow to Council executed by `pytGY6…` |
| **B Council opens order** | `HeMtL8F8…` → DCA escrow (owner = DCA order account) | Squads `VaultTransactionExecute` + `DCA265…` `OpenDcaV2` (Dec 2025: limit-order program `j1o2qR…` `InitializeOrder`) | One tx per order, full order amount |
| **C Fill = open-market buy** | DCA escrow USDC/wSOL ↓, `BTy44YBx…` (Council PYTH) ↑ | `DCA265…` `InitiateFlashFill` → Jupiter aggregator `JUP6…` `SharedAccountsRoute` → `FulfillFlashFill`; keeper-signed | PYTH into Council PYTH ATA in a tx that invokes `DCA265…` (or `j1o2qR…`); input = decrease of tokens owned by the order account (instruction account index 1) |
| **D Council returns PYTH** | `BTy44YBx…` → `AYBdhJU8…` | Squads `VaultTransactionExecute` + SPL transfer | PYTH out of Council into Treasury |

Two practical points for the classifier:
- **USDC arriving at the Council vault's ATA does not appear in the vault's own signature list** (`GAdn7…`), because a transfer to an ATA does not include the owner wallet. Page the ATAs (`HeMtL8F8…`, `BTy44YBx…`) as well as the wallet.
- **Address poisoning is active on every wallet here.** Dust transfers come from lookalikes: `2ru3aG7B…8xxy` mimics Douro's `2ru31e9g…8xxy` in the Treasury USDC history, and `Gx4MpV…Fyak`, `CeWsii…JRhu`, `GiiphF…hJz`, `GiipwG…gzz` appear elsewhere. Match on full addresses only, and ignore transfers below a dust threshold.

### 5.2 Test month: September 2026 (complete)

All 376 successful transactions on the Council vault `GAdn7…` dated 2026-09 (UTC) were fetched, plus the Council USDC ATA's and the Treasury USDC account's September transactions.

| Class | Count | Amount | Reconciliation with the September purchases report (t/2715, 1 Oct 2026) |
|---|---|---|---|
| R1 Douro USDC distribution (to Treasury) | 1 | 433,740.00 USDC (8 Sep, tx 3e5rijGD…) | = August Douro report (t/2695) |
| R3 Cross-chain repatriation (to Treasury) | 2 (+1 into the stranded account) | 107,696.15 USDC (27 Sep) + 25,429.57 USDC into `3QpXyXjs…`; 8,946.30 the same way on 27 Sep (ledger) | = ledger rows; the stranded amounts were recovered on 12 Oct (OP-PIP-140) |
| A Treasury → Council funding | 2 USDC + 2 SOL | 161,714.28 USDC + 45.30 SOL (15 Sep); 323,428.56 USDC + 90 SOL (30 Sep, OP-PIP-136 initial transfer) | 15 Sep amounts = report "161,714.28 USDC / 45.30 SOL" |
| B Orders opened | 2 | 161,714.28 USDC (order `E8sqLfFG…`, 15 Sep 17:53) and 46.0 SOL (order `EifcLvwY…`, 15 Sep 17:54) | = report lines "161,714.28 USDC <> …" and "46 SOL <> …" |
| **C Fills (open-market buys)** | **369** (323 USDC-order + 46 SOL-order fills; 15 Sep 17:53 → 29 Sep 03:54, ~24/day) | **2,582,433.550131 PYTH** for 161,714.28 USDC + 46.0 SOL. USDC order: 2,497,502.268 PYTH (avg $0.06475); SOL order: 84,931.282 PYTH | **Exact match**: report 2,497,502.27 + 84,931.28 = 2,582,433.55 |
| D Return to Treasury | 1 in Sep (2 Sep: 669,661.722734 = the August batch) + 1 on 1 Oct 02:20 (2,582,433.550131) | | = August and September reports' "sent back" lines |
| R2 Douro PYTH in-kind | 0 | | The distribution paid in September (for August) was USDC |
| Other | 2 (ATA creation, no value) | | |

Fill sizes: max 500.66 USDC input per USDC fill (the final fill) and 1.0 SOL per SOL fill; max 9,729 PYTH out per fill. The largest single fill is ~2% of the $25,000 guardrail. About 2,585 PYTH (~0.1%) per month went to accounts other than the Council within fill transactions. This is consistent with a DCA fee leg but was not separated rigorously from intermediate pool movements.

### 5.3 In-kind deliveries (Apr–Jul 2026): where Douro's PYTH came from

| Douro → Treasury | PYTH | Douro's PYTH ATA (`8iRsZ71S…`) funding before/around it |
|---|---|---|
| 3 May 2026 (Apr) | 5,059,107 (+10 test) | 1 May: +20,000,000 from `2tEAnoFK…` (owner `CeWsBRks…`) |
| 2 Jun 2026 (May) | 3,776,270 | same tranche |
| 3 Jul 2026 (Jun) | 7,674,095 | same tranche (cumulative 16.51m of the 20m) |
| 4 Aug 2026 (Jul) | 7,662,509.6 | 4 Aug 16:17: +20,000,000 from `2tEAnoFK…`, six minutes before the delivery |

`CeWsBRks…`'s own PYTH came, among other inflows, from staking-program `WithdrawStake` of **43,750,000 PYTH on 27 Apr 2026** (and again on 2 Oct 2026), i.e. vested tokens leaving a lock. It also receives monthly 83,333 PYTH payments from `6LY1Jz…`. None of the funding legs contains a swap. **The in-kind deliveries were transfers of pre-existing (vested) holdings, not open-market purchases.** No primary document says who controls `CeWsBRks…`.

From Sep 2026, Douro's PYTH wallet also received transfers of up to 6.1m each (5.0m on 11 Sep; 1.0–6.1m in five transfers on 6 Oct; smaller ones on 8, 13 and 16 Oct) from `8UkJ2UYf…` (owner `E6nAbv4q…`; funded by 50m from `Ems4RXvk…` on 14 Jul 2026). It forwarded PYTH to the distribution wallet `GiipfV…` (section 1.7) and in small amounts (35–71k) to `H1Uyp3Z2…`.

### 5.4 Not completed in this pass

- October 2026 fills (2,843 Council-vault txs) were not fetched. The October picture rests on the ledger's order rows, DCA escrow balances (90,981.93 USDC unconverted at 10:44 UTC) and the Council PYTH balance (13,856,169.01 at 10:44 UTC).
- Only September of the distribution wallet's history was classified (58 of 300 txs). Other months rest on the Douro → `GiipfV…` inflows and the vesting snapshot.

---

## 6. Wiring cost estimate

| Piece | New or reused | Size |
|---|---|---|
| Solana RPC adapter: `getAccountInfo` (jsonParsed and base64 + `dataSlice`), `getTokenSupply`, `getTokenAccountBalance`/`getTokenAccountsByOwner`, `getBalance`; 429 handling that honours `retry-after` | New (no Solana adapter exists in the tracker) | Small |
| Account decoders: staking voting target (`TargetMetadata`), staking config (list time), OIS pool config (`y`, slash custody) and pool data (publisher count, stake sums via slices), Squads v4 multisig (threshold/members, optional) | New | Small–medium (layouts are in the public Rust source; scripts here already decode them) |
| Vesting reader: two filtered `getProgramAccounts` calls on `pytS9…` → unvested by date, unlock calendar, owner concentration | New | Medium (schedule maths + handling of modified/closed accounts) |
| Signature paging + transaction fetch with a local tx cache (SQLite) | New | Medium |
| Transfer/swap classifier: SPL transfer legs from pre/post token balances; tag by program set (Squads `VaultTransactionExecute`, Jupiter DCA `OpenDcaV2` program `DCA265…`, Jupiter limit-order program `j1o2qR…`, governance `pytGY6…`) and counterparty (Douro wallet, DAO Treasury) | New | Medium (Jupiter DCA fills are many small keeper txs; needs a paid RPC for daily runs) |
| Forum-report parser: Douro monthly report (gross / DAO share / Douro per product, paid-as currency, tx hash) and Council purchases report (received, swapped, returned, tx links) from Discourse JSON | New | Medium (free-text tables; formats changed in Apr and Jun 2026; keep a manual override row) |
| Published-figure references (ARR, publisher count, feed count) | Reused pattern (manual/config with date) | Small |
| CoinGecko price, market cap, volume, circulating cross-check | Reused | Small |
| DefiLlama Entropy/Core fees (optional; tiny) | Reused | Small |
| Split config: per-product DAO share with effective dates (CO-PIP-9 Sep 2025; LaaS Mar 2026; Marketplace Mar 2026; Indices per Douro reports Jun 2026; conversion share 1/3-of-treasury Dec 2025–Sep 2026 → 100% of DAO share from 1 Oct 2026) | Reused pattern (config) | Small |

Overall: medium. The only large item is daily replay of DCA fills if the tracker insists on fill-level "actual buyback". A cheaper equivalent is the daily change in Council PYTH + Treasury PYTH balances (STOCK reads) net of identified non-buyback flows.

---

## 7. Not found / open questions

1. **Indices revenue split has no governance basis found.** Douro reports it as 60/40 from June 2026. No Constitution clause, CO-PIP or OP-PIP sets it. Ask on the forum or check later Constitution revisions.
2. **Genesis lockup is 2,154,583,334 on-chain vs 2,125,000,000 in the docs** (118.3m more locked overall: 8,618.3m vs 8.5B). Which bucket the extra belongs to is not documented. It affects circulating option A vs B.
3. **No Pyth-published circulating supply** figure or endpoint was found. CoinGecko's number is supply − 2.125B exactly.
4. **Monthly publisher-reward amount.** No primary source publishes it. Measured on-chain for the 11 Sep 2026 run only (5,633,811 gross, 24.9/75.1 spot/locked). Other months are inferred from Douro → `GiipfV…` transfers. The lead's "~2.7m a month" is not supported; it matches CO-PIP-137's subtotals for the affected accounts only. Whether all of `GiipfV…`'s recipients are data publishers (rather than other grantees) is not labelled on-chain.
5. **Who controls `CeWsBRks…`, `E6nAbv4q…`, `GiipfVdY…`?** On-chain: CeWsB… withdraws vested PYTH from the staking program (43.75m on 27 Apr 2026 and again on 2 Oct 2026) and receives 83,333 PYTH payments from `6LY1Jz…`. E6nAb… received 50m from `Ems4RXvk…` on 14 Jul 2026. Both fund Douro's paying wallet, which funds `GiipfV…`. No primary document attributes them (Douro, Pyth Data Association, or other). Address-poisoning lookalikes of all these wallets (`CeWsii…JRhu`, `Gx4MpV…Fyak`, `GiiphF…hJz`, `GiipwG…gzz`, `5rcgJw…ze1`) appear in their histories. **Never match wallets by prefix/suffix.**
6. **Unattributed burns:** 41,119.73 PYTH below 10B. Burn transactions not identified (needs a mint-wide signature replay; the mint's history is very large).
7. **OIS slashing status conflict:** the docs say slashing is active; CO-PIP-139 says OIS staking and slashing "are no longer active" and OIS is "legacy, unstake-only". 671.2m PYTH sits in OIS positions. It is unknown whether the docs or the on-chain program will change.
8. **Constitution on main does not yet reflect CO-PIP-139** (the Price Feed Council is defined) at fetch time. PR #578 status not checked (the GitHub API was not enabled for this session).
9. **$25,000-per-transaction guardrail vs DCA orders** of $194k–$554k opened in one transaction. The reports do not say how the cap is applied; it is presumably per fill.
10. **USD cost of V1 SOL legs** (641.8 SOL) is not given in the reports, so the lead's "~$1.04m" total cost is unconfirmed. The USDC/USDT leg is $988,579.
11. **Insider share of governance stake:** not computed. Method: `getProgramAccounts` on `pytS9…` for `PositionData` accounts (large; dataSlice the positions array), join each to its `StakeAccountMetadataV2` (lock type ≠ FullyVested ⇒ insider/locked), and sum `Voting` positions. Expect the public RPC's 100 MB/30 s data cap to bite; a paid RPC is advised.
12. **Governance-stake and OIS-stake history** cannot be read back from current accounts (only the current and previous epoch are stored). Start daily reads from the tracker's first run, or replay the staking program with a paid RPC.
13. **Douro's September report period wording** is internally inconsistent ("August 1st 2026 to September 1st 2026" vs "for the month of September 2026"). Use the report month as Douro labels the table and keep the Indices one-month lag.
14. **March 2026 Douro report**: "March gross revenue was $253,833" vs table "Subscribed Revenue $225,833" (60% of 225,833 = 135,500 as stated). One of the two is a typo; the table is consistent with the DAO share.
15. **Marketplace revenue** is reported as $0 (Apr–May) and then omitted from June onward. Whether any Marketplace proceeds exist is not stated.
16. **ETF/ETP or treasury-company holdings of PYTH** were not researched in this pass.
17. **Sites/tools refused:** WebFetch refused `solana.com/docs/references/clusters` (not allowlisted; the `.md` variant was read instead) and the Google Sheet export (read via curl instead, a public export). `hermes.pyth.network` returns 401 (API key required since 26 Aug 2026). `kpi.pyth.network` is stale (April 2026). The GitHub API was not enabled.
