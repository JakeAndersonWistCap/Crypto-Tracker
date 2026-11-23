# Round 19 research: Chainlink — the Binance deposit address, what Chainlink says, and the theatre flag

Read 2026-11-23, 13:35–14:10 UTC. Research only: no code, config or test changed.

- Chain reads: Blockscout (eth.blockscout.com `/api/v2/addresses/…` and `/api?module=account&action=tokentx`), and `eth_call` on ethereum-rpc.publicnode.com at **block 26,478,255 (2026-11-23 13:52:59 UTC)**.
- Chainlink pages were fetched from chain.link directly and quoted from the page text.
- PUBLISHED = in a Chainlink document. INFERRED = not stated anywhere; the evidence is given. Labels are Blockscout's address metadata: evidence, not proof.

## Answers first

1. **The address is a Binance-operated deposit address used only by Chainlink's non-circulating wallets.** `0x9B1c1752B6B6eAF62a8c491A8681B32C1F7ecDE9` has received LINK from 19 senders, all 19 among the 33 published wallets, and from nobody else, ever. Its gas was paid by Binance's deposit-funding wallet. It sweeps to Binance's hot wallet within minutes. **Whose Binance account it credits cannot be read on chain.** INFERRED: an account opened for, and used only by, whoever signs for the non-circulating Safes (the Chainlink Foundation sets the release schedule, by its own 2022 post).
2. **Chainlink has never said what the exchange leg is for.** It publishes the rate ("7% of the total supply per year") and a 2022 list of what releases fund (oracle rewards, staking rewards, new services, grants, research). No Chainlink page read says LINK is sold, names Binance, an OTC desk, a market maker, or operating costs paid from token sales.
3. **RAISED at 11.4x is a fair reading of the rule as written, and the number is right.** But the cell should say three things it does not say today: the sends are the scheduled release programme, they started three years before the Reserve and did not change when it launched, and the Reserve offsets 9% of them.
4. **Three open calls:** count 1.35M of the 2.08M second path as node payments (the other 0.73M went to 532 other addresses and is not node payments); move the exchange leg to category (f); make A1's float the same float as CP.

## 1. The deposit address

| Fact | Value | Source |
|---|---|---|
| Address | `0x9B1c1752B6B6eAF62a8c491A8681B32C1F7ecDE9` | config `_CL_FWD_EXCHANGE` |
| Type | EOA: no code, nonce 59 | `eth_getCode`, `eth_getTransactionCount`, block 26,478,255 |
| LINK held now | 0 | `balanceOf`, same block |
| ETH held now | 0.0413 (left-over gas) | `eth_getBalance`, same block |
| LINK ever received | **253,800,000.00** in 138 transfers (69 are 1-LINK tests) | Blockscout token transfers, all 201 rows |
| Senders | **19 addresses, all 19 among the 33 published non-circulating wallets**; no other sender | same; checked against `supply.chain.link/supply` list in config |
| LINK ever sent | 253,800,000.00 in 59 transfers, every one to `0x28C6c06298d514Db089934071355E5743bf21d60` | same |
| Other tokens ever received | none | same |
| ETH ever received | two payments: 0.02 ETH on 2022-08-26 14:00:01 (tx `0x7723b6a2…769d30`) and 0.04 ETH on 2023-12-16 00:30:11 (tx `0x38e8b08a…9790f6`), **both from `0x9430801EBAf509Ad49202aaBC5F5Bc6fd8A3dAf8`** | Blockscout transactions, all 61 rows |
| Label of the gas payer | "Binance: Deposit Funder 1", "Binance Gas Supplier 1", "Fee Provider" | Blockscout metadata |
| Label of the address | "Binance: Deposit Address" | Blockscout token-transfer metadata (R18 read; the address page itself shows no tag) |
| Label of the destination | "Binance: Hot Wallet", "Binance 14" | Blockscout metadata |
| First activity | 2022-08-26 13:19:30, a 1-LINK test from one of the 33 | first transfer, tx `0x540c7288…a40a2c` |
| Sweep timing | same day, usually within the hour and at most about three hours after a deposit; many sweeps land at hh:00:11 or hh:30:11 (a timed batch job). One exception: 8.2M received 2023-12-15 21:33 was swept 2023-12-16 01:00, after the second gas payment | timestamps of the 197 LINK transfers |
| Junk | three zero-value LINK transfers and one fake "LINK" token to look-alike addresses `0x28c6238C…1D60` and `0x28C6A120…1D60` (2026-10-10..18). Address-poisoning spam; not real flows | same |

What this supports:

- **The key is Binance's, not Chainlink's (INFERRED, strong).** Binance pays the gas, the address does nothing but `LINK.transfer` to Binance's hot wallet, and it does it on a timer. That is how a Binance customer deposit address behaves.
- **It serves one customer (INFERRED, strong).** A deposit address belongs to one account. In four years nothing but the non-circulating wallets has paid into it, and it was first used on the first release day of the current scheme.
- **Who that customer is: not established.** It could be an account of the Chainlink Foundation or Chainlink Labs. It could be an account of a third party (a trading firm or OTC desk) that Chainlink pays directly from the Safes. The chain cannot tell these apart. Chainlink has not said.
- **What happens next: not established.** At the hot wallet the LINK is mixed with all Binance customer LINK. Sale, custody, collateral or withdrawal to other chains cannot be read.

LINK the 33 wallets have ever sent out (all destinations, Blockscout `tokentx` for all 33, transfers among the 33 excluded):

| Destination | LINK | When | What |
|---|---|---|---|
| `0x9B1c1752…` (Binance deposit address) | 253,800,000 | 2022: 32.05M; 2023: 47.95M; 2024: 63.45M; 2025: 60.725M; 2026 to date: 49.625M | the exchange leg |
| `0xD50f074E…` (Safe 4-of-7) | 42,450,000 | 2022–2026 | staking rewards and node payments path |
| `0x6781a24c…` (Safe) | 54,500,000 | 2021 | older distribution Safe |
| `0x1f9e26f1…` (EOA) | 12,600,000 | 2019 | older distribution |
| `0x9d6a86fa…` (Safe 3-of-8) | 3,500,000 | 2022 | node payments wallet |

- **No other exchange deposit address is fed by the 33.** The five rows above are every destination.
- The 2019 and 2021 legs (67.1M) were not traced further. They are outside the "now" window.
- The pack's figure of 253.8M since 2022-08-26 is confirmed to the token.

## 2. What Chainlink says

| Source, date | Exact words | What it covers |
|---|---|---|
| chain.link/circulating-supply (read 2026-11-23) | "The Chainlink circulating supply refers to the number of LINK tokens that are circulating in the market. Token release schedule is currently 7% of the total supply per year. The total supply is capped at 1,000,000,000 LINK." | The rate. No destination, no purpose |
| chain.link/blog/sustainably-growing-chainlink (2022-06-27) | "the Chainlink Foundation has been created to lead an increasingly complex set of growth initiatives, many of which are based on deploying additional LINK tokens." Its first goal: "Setting a LINK token release schedule to support key growth initiatives, including oracle rewards for node operators and staking rewards for stakers." | Who sets the schedule: the Foundation |
| same | "only 50 million LINK are available to enter circulating supply over the next nine months … to be used for various purposes, including oracle network rewards, the initial staking rewards and the initiatives described below, among others." | Stated uses. "among others" leaves it open |
| same | Uses listed: "Oracle Rewards to lower initial user costs…", "Staking Rewards…", "Expand existing Chainlink services…", "Support additional blockchains…", "Develop new Chainlink oracle services…", "Other ecosystem growth initiatives … Chainlink Grants, hackathon sponsorships, research and development, code audits, and bug bounty programs." | The only published list of uses. Development and research are on it. Selling is not mentioned |
| chain.link/faqs (read 2026-11-23, R18 copy) | "the LINK token funds “oracle rewards” to bootstrap new decentralized oracle networks before fee-driven sustainability is achieved, similar to how blockchain networks use “block rewards” as a form of network incentive." | Node subsidies are paid in LINK |
| chain.link/blog/payment-abstraction-svr-fee-conversion (2025-03-31) | "By using the Reserves contract on Ethereum mainnet introduced by Payment Abstraction, node operators can accrue oracle rewards on a single blockchain (Ethereum) for their participation in DONs across the multi-chain economy, starting with CCIP." | **PUBLISHED: node operators are paid oracle rewards through the Reserves contract** (`0x5680681E…` is "Reserves 1.0.0"). The 63 addresses are still not named |
| chain.link/blog/chainlink-reserve-strategic-link-reserve (2025-08-07) | "We do not expect any withdrawals from the Reserve for multiple years and thus it is expected to grow over time." | The Reserve is held. The post points to the circulating-supply page; it does not mention the releases |
| same | "50% of fees from staking-secured SVR services—originally planned to be used to cover the existing oracle rewards paid to node operators—is now planned to be used to help fund the Chainlink Reserve" | Revenue once meant to pay node operators now goes to the Reserve; node operators stay on released LINK |
| chain.link/economics (read 2026-11-23, R18 copy) | "767 M+ Circulating supply", "5 M+ LINK in Reserve"; pillars "Revenue growth", "Operating cost reductions", "Cryptoeconomic security" | "Operating cost" here means the cost of running oracle networks, not the company's costs |
| Quarterly reviews Q3 2025 (2025-10-17), Q1 2026 (2026-04-28), Q2 2026 (2026-07-24), Q3 2026 (2026-10-21) | Reserve accumulation figures each quarter | **Nothing on releases, the non-circulating wallets, Binance or sales** (searched each page for: non-circulating, release, sell, sold, Binance, OTC, market maker, treasury, subsidy) |
| X, @ChainLinkGod (Zach Rynes, Chainlink community liaison), 2022-06-27, read through api.fxtwitter.com | ".@chainlink will be migrating non-circulating $LINK tokens from the initial six genesis wallets into 27 new multisignature wallets. This migration does not represent an increase in the circulating supply, but an improvement in on-chain security" | The migration only |

What is NOT said, anywhere I could read:

- That LINK is sold, or how much, or at what price.
- Binance, any exchange, any OTC desk or market maker.
- That token sales fund Chainlink Labs' operating costs or payroll. (It is the obvious reading: the 2022 list includes building new services and research. It is INFERRED, not stated.)
- A per-quarter schedule. Only "7% a year".
- Any link between the releases and the Reserve. The Reserve is presented alone.

Secondary only, not verified: press reports (Lookonchain on X 2023-09-17, read through api.fxtwitter.com; BeInCrypto, AMBCrypto, The Daily Hodl 2023–2026) describe each release as "sent to Binance". One 2023 article says holders had been told the non-circulating supply "would not be sold". I found no Chainlink sentence saying that. Token allocation at launch (35% sale, 35% node operators, 30% company) is from Glassnode and press, not re-read from the 2017 documents.

**Disclosed use, in one line:** node-operator rewards and staking rewards are disclosed and visible on chain (the 13% that goes to `0xD50f074E…`). For the 87% that goes to Binance there is no disclosed use.

## 3. Is RAISED at 11.4x fair?

The rule (FAIR_TOKENOMICS_STANDARD §8): "Raised when issuer-side or insider wallets send tokens to exchanges in a window in which the project retires tokens by buyback; size as % of retired tokens."

The numbers, since the Reserve's first inflow (2025-08-07):

| | LINK | $ (day's price) |
|---|---|---|
| Released by the 33 wallets (five release days) | 88,750,000 | |
| of which to the Binance deposit address | **77,600,000 (87.4%)** | about $925M |
| of which to `0xD50f074E…` (staking and node path) | 11,150,000 (12.6%) | |
| Bought by the Reserve (to 2026-11-19) | **6,804,737** | about $76M (Chainlink's own cost basis $11.2044 × 6,804,760 = $76.2M) |
| **Size: sent ÷ bought** | **11.40x** | 12.2x |
| Reserve as a share of the exchange leg | 8.8% | |
| Net issuer-side flow to the exchange | 70.8M out = 7.1% of supply, 10.1% of the free float (703.6M) | |

$ values use DefiLlama's daily CoinGecko price (secondary for price). Sends by day: 2025-10-10 18.75M at $17.35; 2025-12-19 9.225M at $12.53; 2026-04-03 14.375M at $8.66; 2026-06-19 18.375M at $7.93; 2026-10-09 16.875M at $12.70.

By window, to show the ratio is not an artefact of the start date:

| Window to 2026-11-23 | Sent to exchange | Reserve bought | Size |
|---|---|---|---|
| since 2025-08-07 | 77.60M | 6.80M | 11.4x |
| last 12 months | 58.85M | 5.92M | 9.9x |
| last 9 months | 49.63M | 4.63M | 10.7x |
| last 6 months | 35.25M | 3.03M | 11.7x |
| last 3 months | 16.88M | 1.22M | 13.8x |

Where released LINK was paid out in the same window (2025-08-07 on):

| Use | LINK | Source |
|---|---|---|
| Binance deposit address | 77,600,000 | this read |
| Staking RewardVault (five tranches of 607,500) | 3,037,500 | Blockscout, Safe `0x7e10A625…`, this read |
| Node operators through the Reserves contract | about 5.1M (config's quarterly figures from 2025 Q3; the part of 2025 Q3 before 08-07, at most 80,512, is not split out) | config `CHAINLINK_NODE_PAYMENTS` |
| Safe `0x77dd1a9b…` direct payments (2025 Q3 on) | about 1.6M | this read, section 4 |
| Still in the path wallets | 9.82M in `0xD50f074E…`, 0.90M in `0x9d6a86fa…` | R18 read |

The exchange leg is about 89% of everything paid out. Node operators and stakers together got about 9.7M, a little more than the Reserve bought.

**The case that the flag is unfair:**

- The sends are a programme, not a reaction. One day a quarter since 2022-08-26, three years before the Reserve.
- The Reserve did not change them. Four releases before the Reserve (2024-09 to 2025-06): 61.5M to the exchange. The latest four (2025-12 to 2026-10): 58.85M.
- The rate is published (7% a year), the wallets are published, and Chainlink's own circulating figure rises with each release. Nothing is hidden about the amount.
- The Reserve is funded by revenue, not by the proceeds (no LINK reaches it except converter output).
- The dilution is already scored once, in total dilution (P1). The flag scores the same tokens again.

**The case that it is fair:**

- The rule is met word for word: published issuer wallets, an exchange-tagged address, a buyback running in the same window.
- "Theatre" is about the picture. Chainlink's economics page shows "5 M+ LINK in Reserve" and a Reserve dashboard. It shows nothing about the 77.6M that went the other way. For each LINK the Reserve bought, 11.4 went to Binance.
- The purpose of 87% of every release is undisclosed. A disclosed rate is not a disclosed use.
- Revenue that was planned to pay node operators (50% of SVR fees) was redirected to the Reserve, while node operators and stakers are still paid from released supply. The buyback is partly funded by keeping the subsidy on the release.
- The size is stable in every window (9.9x to 13.8x), in tokens and in dollars.

**Verdict: RAISED is fair. 11.4x is correct.** The flag asks one narrow question and the answer is yes by a wide margin. What is unfair is the bare cell: it reads as if Chainlink started dumping when it started buying. The cell should carry the context, and say plainly that sale is not established.

Candidate wordings (cell, then comment):

**W1 (recommended).**
- Cell: "RAISED — 11.4x. Since the Reserve began (2025-08-07) Chainlink's published non-circulating wallets sent 77.6M LINK to a Binance deposit address; the Reserve bought 6.8M (9% of that). The sends are the scheduled 7%-a-year release, quarterly since Aug 2022 and unchanged by the Reserve. Chainlink has not said what the exchange leg is for."
- Comment: "Sent: 33 wallets listed at supply.chain.link → 0x9B1c1752… (Blockscout: 'Binance: Deposit Address'; gas paid by 'Binance: Deposit Funder 1'; receives from these wallets only) → 0x28C6c062… ('Binance 14'). Five days: 2025-10-10 18.75M, 2025-12-19 9.225M, 2026-04-03 14.375M, 2026-06-19 18.375M, 2026-10-09 16.875M = 87% of the 88.75M released. Bought: 6,804,737 LINK into the Reserve from revenue, held. Whose Binance account, and whether the LINK is sold, is not established. Labels are evidence, not proof. The same tokens are in total dilution. Next release by the pattern: about 2026-12-18, 11.25M."

**W2 (shorter).**
- Cell: "RAISED — 11.4x: 77.6M LINK of scheduled releases to a Binance deposit address against 6.8M bought by the Reserve since 2025-08-07; purpose of the exchange leg not disclosed."
- Comment: as W1.

**W3 (not recommended; needs a rule change).**
- Cell: "NOT RAISED — scheduled release (7% a year, published); 77.6M to a Binance deposit address vs 6.8M bought is shown under dilution."
- Why not: the rule has no exemption for a published schedule, the rules are frozen, and the purpose is undisclosed.

## 4. The three open calls

### 4.1 The second payment path: Safe `0x77dd1a9b170e2f8976c20c10c8d9c27886181077`

All 3,281 LINK transfers of the Safe (Blockscout `tokentx`, 2025-05-14 to 2026-11-04):

| | LINK | Detail |
|---|---|---|
| In | 2,762,902 | 2,412,435 (87%) from `0x9d6a86fa…`; the rest from four other addresses |
| Out to the Reserves contract `0x5680681E…` | 613,880 | already in outflow (b) |
| Out direct, to **43 of the 63** recipients of the Reserves contract | **1,351,928** | 428 payments, median 2,617 LINK. 2025 Q2 198,767; Q3 346,886; Q4 499,736; 2026 Q1 200,004; Q2 106,532; Q3 2. Last sizeable payment 2026-05-29 |
| Out direct, to **532 other addresses** | **730,964** | 2,751 payments, median 40 LINK, on 66 batch days. 2025 Q2 270,441; Q3 374,156; then 86,367 in all since |
| Left in the Safe | 66,130 | by difference |

- **Correction to Round 18:** the 2,082,892 did not all go "to the same recipients". 65% went to 43 of the 63. 35% went to 532 other addresses in small amounts. INFERRED: grants, bounties, hackathon or community rewards (the 2022 post lists these as uses). Not node payments.
- **Now:** the path is winding down. To the 63: 666,763 in the last 12 months, 48,437 in the last 6, 0 in the last 3. To the others: 72,267, 35,193 and 4,225.

Options:

- (a) Count the 1,351,928 paid to the 63 in outflow (b), out of (e) by the same FIFO. Leave the 730,964 in (e), noted as "other issuer payments, purpose not established".
- (b) Count all 2,082,892 in (b).
- (c) Leave both out of (b), with the note corrected.

**Recommend (a).** Same recipients, same funder, same kind of payment as the stream already in (b). It raises (b) by about 15% over the last 12 months (666,763 on roughly 4.5M) and by nothing going forward. The 0.73M is not a supply-side incentive on the evidence, so it should not be in (b).

### 4.2 The exchange leg's category

| | (e) unlocks | (f) named float outflow | Total dilution |
|---|---|---|---|
| (a) As now: stays in (e), memo line only | 77.6M inside it | none | unchanged |
| (b) Move to (f), as Ether.fi (R15) | loses 77.6M since 2025-08-07 (58.85M in the last 12 months, 84% of the 70M released) | 77.6M | unchanged |

- The evidence that this is an exchange send is stronger than Ether.fi's: here the deposit address itself carries the label and Binance pays its gas; there the forwarders had no label.
- The senders are all inside the release stock (the 33), so no sender has to be left out, unlike Ether.fi's deployer EOA.
- After the move (e) holds only what stays issuer-side on the release day; (a) staking and (b) node payments already come out of it.

**Recommend (b): move it to (f).** One treatment for the same thing across projects. It also shows on the face of the workbook that 84–87% of each "unlock" is a send to an exchange. It changes no total.

### 4.3 The issuer stock in A1's float

| | A1 "float after staking" | CP / A2 / A3 float |
|---|---|---|
| Now (on the dump) | 957.5M = 1B − 42.5M staked | 718.1M |
| Live, 2026-11-23 (block 26,477,750, R18) | 957.4M | 703.6M |

- A1's row is "Resulting float = circulating − staked inside circulating" (`build_workbook.py`, the A1 column list). Its code comment says "THE SAME FLOAT AS A2 AND CP". Since circulating became total supply under the rule, it is not: it still contains the 33 wallets (233.15M), the Reserve (6.80M), the vault (3.09M) and the path wallets (10.75M).
- Options: (a) leave it and relabel it "circulating less staked (issuer stock still in)"; (b) take the rule's off-float stock out, so A1 = CP = 718.1M on the dump (−25.0%).

**Recommend (b).** One float, one basis (standard, principle 12). A 957M float for LINK is wrong by a quarter. The fix is generic: it will move the A1 row of every project that has issuer stock inside circulating under the rule, so it needs a declared move for frozen projects. Which other A1 rows move was not checked here.

## Decisions for Jake

| # | Decision | Options | Numbers | Recommend |
|---|---|---|---|---|
| 1 | The flag's state | (a) RAISED 11.4x, as scored; (b) NOT RAISED, exempt a published schedule (rule change) | 77.6M sent, 6.80M bought; 9.9x–13.8x in every window | **(a)** |
| 2 | The cell's wording | W1 full context; W2 short; W3 not raised | — | **W1** |
| 3 | Second payment path | (a) 1,351,928 to the 63 into (b); (b) all 2,082,892; (c) none | +666,763 in the last 12 months, 0 in the last 3 | **(a)**, and correct the R18 note: 730,964 went to 532 other addresses |
| 4 | Exchange leg category | (a) stay in (e); (b) move to (f) | 77.6M since 2025-08-07; 58.85M last 12 months; total dilution unchanged | **(b)** |
| 5 | A1 float | (a) relabel; (b) take issuer stock out | 957.5M → 718.1M on the dump | **(b)**, as a generic fix with declared moves |
| 6 | Node-operator label (new) | (a) keep "INFERRED"; (b) "path PUBLISHED (Payment Abstraction post, 2025-03-31), addresses not named" | 63 recipients, 5.13M LINK | **(b)** |
| 7 | Deposit address label (new) | (a) keep "Binance-tagged deposit address"; (b) add "gas paid by Binance's deposit funder; receives only from the 33" | 2 gas payments, 19 senders, 253.8M | **(b)** |

## Could not verify

- **Who owns the Binance account** behind `0x9B1c1752…`. Not readable on chain; not stated by Chainlink or Binance.
- **What happens to the LINK at Binance** (sale, OTC, custody, collateral, withdrawal to other chains). It is mixed in the hot wallet.
- **That Binance holds the key.** INFERRED from the gas payer's label and the sweep pattern. The labels are Blockscout's; Etherscan was not read (bot check, as in Round 15).
- **X posts.** x.com is behind a login wall. I read two posts through api.fxtwitter.com (@ChainLinkGod 2022-06-27, @lookonchain 2023-09-17). I could not search the official @chainlink or Sergey Nazarov's accounts. Web search found no statement from either on the transfers.
- **Interviews and AMAs.** No transcript was read. Web search (several queries) found no Sergey Nazarov statement on selling LINK, the Binance transfers, OTC or market makers. Absence in a search is not proof that none exists.
- **Q4 2025 quarterly review:** chain.link/blog/quarterly-review-q4-2025 returns 404 (as in Round 18).
- **The 2017 allocation** (35 / 35 / 30) and any 2017 statement on the company's use of its tokens: secondary sources only.
- **The 532 other recipients** of Safe `0x77dd1a9b…` and the four small funders of that Safe: not identified.
- **Node payments since 2025-08-07** were taken from config's quarterly figures, not re-read. The 43-of-63 match uses the Round 18 recipient list (`pal_recipients.json`, 63 addresses).
- **Dollar values** use DefiLlama's daily CoinGecko price, not a primary price source.
- **The 2019 and 2021 outflows** of the legacy wallets (12.6M and 54.5M) were not traced to their end.
- **Which other projects' A1 float rows move** under decision 5.
- **The next release** (about 2026-12-18, 11.25M, of which 8.2M–10.6M went to the exchange in the last three Decembers) is INFERRED from the pattern. Chainlink publishes no dated schedule.

## Sources (read 2026-11-23)

- Chain: ethereum-rpc.publicnode.com, block 26,478,255. Blockscout: `https://eth.blockscout.com/api/v2/addresses/0x9B1c1752…` (`/transactions`, `/token-transfers`, `/internal-transactions`, `/counters`); `https://eth.blockscout.com/api?module=account&action=tokentx` for the 33 wallets, `0x77dd1a9b…` and `0x7e10A625…`.
- Chainlink: https://chain.link/circulating-supply ; https://supply.chain.link/supply ; https://chain.link/blog/sustainably-growing-chainlink ; https://chain.link/blog/payment-abstraction-svr-fee-conversion ; https://chain.link/blog/chainlink-reserve-strategic-link-reserve ; https://chain.link/blog/sustainable-oracle-economics ; https://chain.link/blog/chainlink-staking-v0-2-overview ; https://chain.link/blog/quarterly-review-q3-2025 , -q1-2026, -q2-2026, -q3-2026 ; https://chain.link/economics and https://chain.link/faqs (Round 18 copies, same day).
- X through a mirror: https://api.fxtwitter.com/chainlinkgod/status/1541458175299158017 ; https://api.fxtwitter.com/lookonchain/status/1703346219538682364
- Prices: https://coins.llama.fi/chart/coingecko:chainlink
- Repo: `docs/r15_chainlink_pack.md`, `SPEC.md` "Round 18 — Chainlink", `config.py` (`THEATRE_FLAG`, `CHAINLINK_NODE_PAYMENTS`, `RELEASE_PAID_LATER`, `CHAINLINK_RULE_READ`), `build_workbook.py` (A1 "Resulting float"; `theatre_flag_text`), `research/theory/FAIR_TOKENOMICS_STANDARD.md` §8, `docs/HANDOVER_LOCAL.md` §9.
- Scratch: /tmp/claude-0/-home-user-Crypto-Tracker/e84ff2c6-8546-5133-82ff-742643596bbf/scratchpad/r19_chainlink/
