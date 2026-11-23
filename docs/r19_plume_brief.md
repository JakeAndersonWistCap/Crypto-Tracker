# Round 19 — Plume brief for the call

Read 2026-11-23. Research only; nothing built. Every figure below was read that day unless a date is given.

**The short answer.** Nothing reaches PLUME holders. The vaults do charge fees, about $86k in the last 30 days, and those fees go to plain wallets that nobody labels. Against that, about 212M PLUME ($2.3M at today's price) unlocks every month until January 2028.

| Today | Figure | Source |
|---|---|---|
| PLUME price | $0.01086 | coins.llama.fi/prices/current/coingecko:plume |
| Circulating | 7,030,374,396 of 10,000,000,000 (70.3%) | supply.plume.org/supply |
| Market cap / fully diluted | $76.4M / $108.6M | circulating and total × price |
| Staked | 112,203,226 PLUME (1.6% of circulating), 4.9966% gross a year | rpc.plume.org, staking contract 0x30c791E4654EdAc575FA1700eD8633CB2FEDE871, `totalAmountStaked()`, `getRewardRate()` |

Circulating is 7.03bn, not 6.82bn: the monthly step landed about 21 November. Staked PLUME is down from 134.0M on 2026-09-30 (config.py).

## a. Does anything reach PLUME holders?

No. Nothing found in any Plume source.

- **Token docs** (docs.plume.org/plume/tokens/plume-usdplume.md): PLUME is for gas, governance, staking and ecosystem incentives. No fee share, buyback, burn or distribution.
- **Tokenomics post** (plume.org/blog/tokenomics): the same four uses.
- **Whole docs export** (docs.plume.org/plume/llms-full.txt): "buyback", "revenue share" and "fee share" do not appear. "Burn" appears once, about bridging.
- **Q3 2026 report** (plume.org/blog/q3report, 2026-11-11): does not mention the PLUME token at all. It reports vault growth only.
- **Staking yield** is paid from the pre-minted supply, not from fees. At today's stake it costs about 5.6M PLUME a year (about $61k).
- **Gas** was 153,721 PLUME in the last 30 days, about $1,670 (explorer.plume.org stats service, `txnsFee`). It goes to the chain owner's contract 0x60be7a0c…f7fd, not to holders (config.py, read on chain 2026-10-20).
- **Vault fees** do not touch PLUME (section b).

Nest Points season one: the repo's earlier read of Plume's blog found dates only and no amount, so the "1% of supply" is not confirmed from a primary source.

## b. Vault fees and who receives them

Plume publishes no fee schedule page. The docs say only that the accountant contract "accrues management fees" and that redemption fees "vary by vault" (app.plume.org/docs, architecture and liquidity pages). The rates below come from Plume's own vault API (api.nest.credit/v1) and from the accountant contracts on Plume.

| Vault | Deposits | Management fee a year | Performance fee | Redemption fee, standard / instant | Fees paid to |
|---|---|---|---|---|---|
| BlackOpal (nOPAL) | $111.9M | 0.50% | 5% above an 8% hurdle | 0.015% / 0.15% | 0x36192f07…b8e4 |
| FalconX (nFXCF) | $15.9M | 0 | 0 | 0 / 0.15% | 0xa08a0dc4…a982 |
| FACTOR | $13.5M | 0 | 0 | 0.5% or 1.5% / 2.0% | 0xaf200ebb…7901 |
| Plume PRIME (nPRIME) | $13.3M | 0.20% | 0 | 0 / 0.15% | 0xd7f8d99b…5807 |
| Alpha (nALPHA) | $12.8M | 0.50% | 0 | 0.015% / 0.15% | 0x88bc8480…c253 |
| AXI (nAXI) | $5.6M | 0 | 0 | 0 / 0.15% | 0xc28e1cdf…716e |
| nTBILL | $3.8M | 0.10% | 0 | 0 / 0.15% | 0x20bf117e…48c8 |

Sources: `getAccountantState()` and `getPerformanceFeeConfig()` on each vault's accountant (rates in parts per million); `/vaults/{slug}/redemption-fees`.

- **Who receives them:** each vault pays a different address. Five of the seven are plain wallets with no name or label on explorer.plume.org. 0xa08a0dc4…a982 is the vault owner multisig named in Plume's docs. No Plume source says whether a fee goes to Plume, the asset manager, or a distribution partner.
- **How much:** fees accrued across all vaults were $86,342 in the 30 days to 2026-11-22 and $258,950 in the 90 days (`/vaults/fees`, USD). That is about $1.05M a year at the current rate. BlackOpal is about 75% of it.
- **By type, last 30 days:** management $55,655, performance $12,095, redemption $18,593.
- **Claimed so far:** $195,123 since July, in monthly claims.

## c. Unlocks ahead, through 2028

2.97bn PLUME is still to unlock: 42% on top of today's circulating, $32.3M at today's price. It finishes in January 2028. Nothing unlocks after that.

Plume publishes the schedule as a chart only (plume.org/blog/tokenomics, revised 2025-10-11), with no table. The chart ends at 10bn in January 2028. The table is INFERRED from Plume's own circulating figure, which has risen by exactly 212,116,114.6 about the 21st of each month. Fourteen more steps of that size reach 10bn to the token.

| Step (about the 21st) | Tokens unlocked | Cumulative circulating | % of 10bn |
|---|---|---|---|
| Nov 2026 (done) | 212,116,115 | 7,030,374,396 | 70.3% |
| Dec 2026 | 212,116,115 | 7,242,490,511 | 72.4% |
| Jan 2027 | 212,116,115 | 7,454,606,625 | 74.5% |
| Feb 2027 | 212,116,115 | 7,666,722,740 | 76.7% |
| Mar 2027 | 212,116,115 | 7,878,838,854 | 78.8% |
| Apr 2027 | 212,116,115 | 8,090,954,969 | 80.9% |
| May 2027 | 212,116,115 | 8,303,071,084 | 83.0% |
| Jun 2027 | 212,116,115 | 8,515,187,198 | 85.2% |
| Jul 2027 | 212,116,115 | 8,727,303,313 | 87.3% |
| Aug 2027 | 212,116,115 | 8,939,419,427 | 89.4% |
| Sep 2027 | 212,116,115 | 9,151,535,542 | 91.5% |
| Oct 2027 | 212,116,115 | 9,363,651,657 | 93.6% |
| Nov 2027 | 212,116,115 | 9,575,767,771 | 95.8% |
| Dec 2027 | 212,116,115 | 9,787,883,886 | 97.9% |
| Jan 2028 | 212,116,115 | 10,000,000,000 | 100.0% |
| Feb–Dec 2028 | 0 | 10,000,000,000 | 100.0% |

**Who gets each month's 212M (SECONDARY, cross-check only).** DefiLlama's reading of Plume's chart (defillama-datasets.llama.fi/emissions/plume-mainnet) gives, per month on average:

| Recipient | Tokens a month | Share |
|---|---|---|
| Early Backers | about 58.5M | 28% |
| Core Contributors | about 55.8M | 26% |
| Community & Ecosystem | about 65.8M | 31% |
| Foundation | about 31.9M | 15% |

DefiLlama's total for 2026-11-21 is 7,031.0M against Plume's 7,030.4M, and both end at 10bn on 2028-01-21. So about 54% of every month's unlock goes to insiders.

## d. Vault deposits today

| Measure | Figure | What it is |
|---|---|---|
| Plume's own vaults | $178.75M | 14 active vaults, all chains (`/vaults/stats`; the page's vault list sums to the same) |
| Partner vaults shown on plume.org/vaults | $71.7M | Binance $24.5M, Bybit $9.7M, Ether.fi $37.5M |
| All vaults, every status | $226.5M | `/vaults/stats?status=all`; this is the "$223M" in Plume's Q3 report |
| BlackOpal vault (nOPAL) | $111.9M | down $5.6M in 30 days |
| Actually in BlackOpal's receivables fund | $99.4M | nOPAL's holding of the LiquidStone II token (`/vaults/nest-opal-vault/positions`) |
| Assets after removing vaults that hold other vaults | about $158M | my sum of every vault's positions |

Jake's figures (~$185M, ~$85M, ~$165M) are each lower today. I could not reproduce $165M for BlackOpal on any measure.

**The partner money is mostly not additional.** Do not add $72M to $179M.

- **Ether.fi** ($37.5M) holds Plume's own vault tokens: nFXCF $15.3M, nPRIME $11.6M, FACTOR $6.5M, nCLO $1.4M. That is 96% of the FalconX vault and 87% of the PRIME vault.
- **Bybit 3** ($7.6M) holds nOPAL.
- **Binance** ($24.5M) is nOPAL held in Binance Wallet on Ethereum and BNB Chain, already inside the $111.9M.
- **Alpha** holds $10.1M of nOPAL.
- In total $62.3M of the $226.5M is one vault holding another.

**Reconciling with DefiLlama's $42.6M.** DefiLlama's yields feed lists $178.4M for Plume Vaults across all chains, matching Plume's $178.75M. Its $42.6M is the part whose vault tokens sit on Plume chain: 24%.

| Chain where the vault tokens sit | Deposits | Share |
|---|---|---|
| Ethereum | $66.6M | 37% |
| Plume | $42.6M | 24% |
| Solana | $38.4M | 22% |
| BNB Chain | $25.2M | 14% |
| Avalanche, Base | $5.5M | 3% |

Source: yields.llama.fi/pools, project `plume-vaults` (secondary).

## Questions for the call, most important first

1. **Who receives the vault fees?** Name the owner of each payout wallet in section b. Is it Plume Foundation, a Plume company, BlackOpal, or a partner? What is the split on the BlackOpal vault?
2. **Is there any plan for PLUME holders to share in vault fees**, by buyback, burn or payment to stakers? If so, what decides it and when? If not, what is PLUME for besides gas?
3. **Why would vault growth help PLUME?** 76% of deposits sit on other chains, and 30 days of gas came to about $1,700. What makes a depositor on Ethereum or Solana need PLUME?
4. **The unlocks.** 212M a month to January 2028, about 54% to backers and team. Are those tokens being sold, staked or held? Is any lock-up extension being discussed? Will Plume publish the wallets?
5. **The Ecosystem and Foundation tokens**, about 98M a month. What are they spent on, and who decides?
6. **Why is staking falling?** 134M in September, 112M now, and only 1.6% of circulating.
7. **Partner deposits.** Confirm that Ether.fi, Bybit and Binance money is inside the $179M, not on top of it. What do partners get paid, and out of which fee?
8. **BlackOpal concentration.** One fund is 63% of deposits and about 75% of fees, and it shrank $5.6M this month. What is the capacity, and what happens to fees if it leaves?
9. **Zero-fee vaults.** FalconX, FACTOR and AXI charge no management fee. Is that a launch offer, and what is the intended rate?
10. **Does Plume report revenue anywhere?** The Q3 report gives none.

## Decisions for Jake

1. **How to carry Plume while it is on hold.**
   - Option A: leave it as it is. No holder value, revenue is gas only ($1,670 in 30 days).
   - Option B: after the call, add the vault fees ($86k in 30 days) as Plume revenue if Plume confirms it receives them.
   - Recommendation: A until the call answers question 1. The fee recipients are unlabelled, so the fees cannot be called Plume's yet.
2. **Which deposit figure to quote.**
   - Option A: $178.75M, Plume's own active-vault total, all chains.
   - Option B: $226.5M, Plume's headline, which counts partner vaults again.
   - Option C: $42.6M, Plume chain only.
   - Recommendation: A, with C beside it as the share on Plume's own chain.

## Could not verify

- **Plume's X account.** x.com is behind a login wall. api.fxtwitter.com returned "User not found" and syndication.twitter.com returned "Rate limit exceeded". Nothing was read from X.
- **Governance.** No forum or voting site found. forum, gov, governance and vote on plume.org did not connect. Whether token votes have ever happened is unknown.
- **"Revenue sharing being explored".** The repo recorded this from docs.plume.org/plume/plume-portal/stake on 2026-09-14. That page no longer serves staking content, so the line could not be re-read.
- **Who owns the fee wallets.** Unlabelled on the explorer. Unknown.
- **Unlock dates and split.** Plume publishes a chart, not a table. The monthly step is inferred from its circulating figure; the per-recipient split is DefiLlama's reading.
- **Nest Points season one at 1% of supply.** No primary source found for the amount.
- **Jake's ~$165M BlackOpal figure.** Not reproduced.
- **Fee units.** The API says fees are in USD. One day (nTBILL, 2026-06-12, $28.3M) is plainly a data error and is left out of the totals.
- **Third-party trackers** (DefiLlama's token page, tokenomics.com) also show no buyback, burn or fee switch. Secondary; seen in search results only.
