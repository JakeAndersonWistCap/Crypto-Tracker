# Round 21 research — what Hyperliquid actually burns

Research only. No code, config or test was changed. Reads were taken on 2026-11-24 between 16:20 and 16:35 UTC unless a row says otherwise. Scratch files are in the scratchpad folder `r21_hl/`. This builds on `docs/r20_research_hl_uni_morpho.md` section (b).

## Answer first

Hyperliquid has four channels that take HYPE out of use. Two of them destroy HYPE outright. One sends it to the zero address. One, the Assistance Fund, only holds it.

| Channel | What happens | Provably unspendable? | Lifetime | Rate now | Verdict |
|---|---|---|---|---|---|
| **HyperCore burns** (deploy-gas auctions, gossip and order priority fees, other Core fee burns) | HYPE is removed from the ledger. `tokenDetails.totalSupply` falls. | **Yes.** No balance exists anywhere. Getting it back would need new HYPE to be minted. | **1,319,521** (1bn − totalSupply) | **~4,314 a day** (42 days); 1.57M a year | **True burn.** Item 2, A4 on. |
| **HyperEVM base fees** (EIP-1559) | Removed from the EVM supply. No address receives them. | **Yes.** Destroyed on the EVM. The matching Core backing at `0x2222…` can never be claimed (see below). | ~96,000 (INFERRED: ASXN 92,870 to 2026-10-13, plus 42 days at its rate) | ~83 a day (ASXN, secondary); 36–83 a day | **True burn.** Item 2, A4 on. |
| **HyperEVM priority fees** | Credited to the EVM zero address `0x0…0`. | **Yes, on the standard burn-sink test**: no key, no code at the address, no protocol process sends from it, and it has never fallen in 596 daily readings. It still exists as a balance. | **321,141** | **~278 a day** (90 days) | **True burn** (the convention the book uses for `0x…dEaD` elsewhere). Item 2, A4 on. |
| **Assistance Fund** `0xfefe…fefe` | Trading fees buy HYPE, which stays as a spot balance. | **No.** The Foundation's own words: retrievable by a hard fork; protected by "binding social consensus". The L1 code operates this account every day. | **49,291,705** held | **~30,520 a day** bought | **Held buyback.** Circulating, off the float, A4 off. |

**Recommendation:** keep the Assistance Fund as a held buyback (Jake's default). Count the other three channels as true burns under item 2 and turn A4 on, scored on them only: about **4,675 HYPE a day, 1.71M a year, 1.72% of the free float a year, 0.17× the staking issuance.**

---

## The primary sources

| Source | Read | What it says |
|---|---|---|
| Docs, `for-developers/hyperevm` (hyperliquid.gitbook.io, the `.md` version) | 2026-11-24 | "EIP-1559 is enabled on the HyperEVM. Base fees are burned as usual, implemented in the standard way where the burned fees are removed from the total EVM supply. … priority fees are also burned … The burned priority fees are sent to the zero address's EVM balance." |
| Docs, `for-developers/api/priority-fees` | 2026-11-24 | Gossip priority auctions: "Similar to token, spot, and perp deployment auctions, fees come out of spot balance are burned." Order priority: "Similar to gossip priority auctions, order priority fees are burned." |
| Docs, HIP-1 | 2026-11-24 | `maxSupply`: "the maximum and initial supply. The supply may decrease over time due to spot order book fees or future burn mechanisms." Deploy gas is a 31-hour Dutch auction in HYPE. "The portion of base token fees that is not redirected to the deployer is burned." Dust below one lot is burned. |
| Docs, HIP-3 | 2026-11-24 | "the slashed stake by the deployer is burned". |
| Docs, `…/hypercore-less-than-greater-than-hyperevm-transfers` | 2026-11-24 | HYPE's system address is `0x2222…2222`. Core and EVM move HYPE only by transfers to that address. |
| Docs, `trading/fees` | 2026-11-24 | "HYPE in the assistance fund is burned, removing the tokens permanently from the circulating and total supply." (The API says otherwise; see below.) |
| Hyper Foundation, proposal (x.com/HyperFND/status/2001127850754367525, read through **api.fxtwitter.com**) | 2025-12-17 03:10 UTC; read 2026-11-24 | "Similar to the zero address, the Assistance Fund system address has never had a private key … Funds are mathematically irretrievable without a hard fork." "No onchain action is required … This vote is binding social consensus to never authorize a protocol upgrade to access this address." |
| Hyper Foundation, result (x.com/HyperFND/status/2003684257949188396, **api.fxtwitter.com**) | 2025-12-24 04:28 UTC | "formally recognized as burned." 85% of stake for, 7% against, 8% abstaining. |

## The on-chain reads

| Read | Value | Source |
|---|---|---|
| `tokenDetails` HYPE, totalSupply | 998,680,478.70 (16:34 UTC) | api.hyperliquid.xyz/info |
| circulatingSupply | 301,382,853.37 | same |
| futureEmissions | 409,657,663.81 | same |
| nonCirculatingUserBalances | team `0x43e9…` 238,346,580.21; Assistance Fund 49,291,415.36; Core `0x0…0` 1,673.79; Core `0x…dead` 2.72 | same |
| circulating + non-circulating + futureEmissions − totalSupply | −0.0000002 | computed. **The Assistance Fund is still inside totalSupply.** |
| Assistance Fund HYPE (`spotClearinghouseState`) | 49,291,704.80 (16:34); 49,260,148.08 on 2026-11-23 16:57 (R20) | info API |
| Core `0x2222…` HYPE | 55,279,191.18 | info API |
| EVM `0x2222…` native HYPE | 944,720,820.18 | rpc.hyperliquid.xyz/evm, latest |
| Core + EVM `0x2222…` | **1,000,000,011.36**, the same to the cent at 16:20 and 16:34 | computed: a fixed identity |
| EVM `0x0…0` native HYPE | 321,141.35; no code (`eth_getCode` = `0x`) | rpc.hyperliquid.xyz/evm |
| EVM `0x0…0` history | 230.40 (2025-02-25) → 296,089.90 (2026-08-26) → 308,438.13 (2026-10-13). **Never fell** in 596 daily readings. | repo fixture `fixtures/real_hyperliquid.json`, `hyperevm_zero_address_balance` (archive eth_getBalance via rpc.purroofgroup.com) |
| Core totalSupply history | 998,910,781.05 (2026-09-24) → 998,861,738.03 (2026-10-13) → 998,680,478.70 (today) | same fixture, `total_supply_gross`; today live |
| 200 EVM blocks, 52,135,039–52,135,238 (1,936 txs, receipts) | base fees 0.1783 HYPE; priority fees 1.3576; **zero-address change 1.3576, equal to the priority fees exactly** | rpc.purroofgroup.com (archive), read 2026-11-24 |
| Live auctions | perp deploy: started 1,123.97 (twice the last clearing price, so the last cleared at ~562); spot deploy: 500 (the last one failed); gossip slots: restarted at 12.93 / 1.10 / 0.10 (10× the last winners: ~1.29 / ~0.11 / minimum) | info API `perpDeployAuctionStatus`, `spotDeployState`, `gossipPriorityAuctionStatus` |

**Note on the public RPC:** `rpc.hyperliquid.xyz/evm` ignores the block tag and returns inconsistent values for past blocks. Every historical EVM figure here comes from the archive endpoint rpc.purroofgroup.com, as the repo's own `fetch/hyperevm_burn.py` already does.

## Channel by channel

### 1. HyperCore burns: true burn

The docs say deploy-gas auctions, gossip-priority auctions, order-priority fees, HYPE base-token fees with no deployer share, dust and slashed HIP-3 stake are burned. The API confirms that something leaves the supply: totalSupply started at exactly 1bn (`maxSupply`) and has fallen 1,319,521. The HyperEVM cannot cause this fall (see channel 2), and the Assistance Fund's growth does not change it. So the fall is the Core burns.

| Window | Fall in totalSupply | Per day |
|---|---|---|
| 2026-09-24 → 2026-11-24 (61 days) | 230,302 | 3,775 |
| 2026-10-13 → 2026-11-24 (42 days) | 181,259 | **4,316** |
| 2026-11-23 16:57 → 11-24 16:34 (23.6 hours) | 5,928 | ~6,000 |

The split by channel cannot be read from a primary source. From one reading of the live auctions (INFERRED): gossip priority about 700 a day (480 auctions a day at ~1.4 HYPE); perp deploys at most about 435 a day (one per 31 hours at ~562); spot deploys about 0 now (the last auction failed). The other ~3,000 a day is order priority fees, HYPE spot fees and dust, not split.

Provably unspendable: yes. These tokens have no balance anywhere. Reversing it means changing the supply itself, which is the same test as Ethereum's base-fee burn.

### 2. HyperEVM base fees: true burn

The 200-block test shows the docs are right: the zero address gained exactly the priority fees, and the base fees went nowhere. They leave the EVM supply.

They do **not** leave `tokenDetails`. HyperCore tracks HYPE on the EVM as one balance at `0x2222…`. The read shows Core `0x2222…` + EVM `0x2222…` is a fixed 1,000,000,011.36: a bridge transfer moves the same amount on both sides, and an EVM burn moves neither. So the Core balance at `0x2222…` (55.28M) now exceeds the HYPE that exists on the EVM by the base fees burned. Nobody can bridge that excess back, because it would take EVM HYPE that no longer exists. tokenDetails counts that excess as circulating, because `0x2222…` is not on its non-circulating list.

- **Amount:** ASXN's `base_fees` series (in the repo fixture, secondary) sums to 92,870 from 2025-02-18 to 2026-10-13, and 7,485 in the 90 days to 2026-10-13 (83 a day). My primary sample gives a base-to-priority ratio of 0.13, which at the zero address's 278 a day is 36 a day. One 200-block sample is too small to replace ASXN's figure, so the range is 36–83 a day. Lifetime to today: about 96,000 (INFERRED).
- **A finding for the build:** the repo takes the zero address (0.32M) out of circulating but not the base-fee burn. Circulating and the float are overstated by about 0.1M under every treatment (0.1% of the float). INFERRED that this HYPE sits in the float: it is in tokenDetails' circulating, and nothing takes it off.

### 3. HyperEVM priority fees at the zero address: true burn

321,141 HYPE at EVM `0x0…0`. The 90-day rise (2026-08-26 → 11-24) is 25,051, or **278 a day**. The 42-day rise is 302 a day. The figure also includes anything users send to `0x0` themselves. The biggest single days (+8,266 on 2026-04-09, +7,386 on 2025-10-11) were not traced.

Why this is a true burn and the Assistance Fund is not:
- Nobody holds a key to `0x0`, and there is no code at it. No protocol process sends from it. It has never gone down.
- It is the industry's standard burn sink. The book already counts transfers to `0x…dEaD` as burns (Uniswap, R9.2-UN).
- **The honest caveat:** it still exists as a balance, and the Foundation itself says the Assistance Fund is "similar to the zero address". If Jake wants the strictest test, which is "the supply itself fell", this channel moves to held. That changes the burn by 278 a day (6% of the true burn, 0.1% of the float a year). The Core `0x0`/`0x…dead` balances (1,676 HYPE, already non-circulating) follow the same test.

### 4. The Assistance Fund: held

Nothing new changes R20's verdict. Today's reads:
- 49,291,705 HYPE. Up 31,557 since the R20 read (23.6 hours). Up 1,282,110 since the fixture's 2026-10-13 reading (48,009,594.74), which is **30,526 a day** over 42 days. That includes the AQAv2 USDC the Fund spent in early November.
- Still inside `tokenDetails.totalSupply` (the identity closes to 0.0000002). The docs' statement that it is removed "from the circulating and total supply" is not what the API does.
- The Foundation calls the protection "binding social consensus" and says the HYPE is retrievable by a hard fork. 15% of stake did not vote for it.
- The L1 code debits this account every day (it spends its USDC). The rule that would also move its HYPE is missing, but nothing forbids adding it. Neither `0x0` nor a destroyed base fee has a protocol process that operates on it.

Not provably unspendable. **Held buyback:** circulating, off the float, A4 off. It remains A3.

## The headline, both ways

Bases: circulating and free float are from the frozen dumps (SPEC R9.2-HL, export 2026-11-16). Balances and rates are from today's reads above. Staking issuance is the fall in futureEmissions, 410,783,132.80 (2026-10-13 fixture) → 409,657,663.81 = **26,797 a day**. Option-C issuance adds the repo's Unlocks Q0 of 5,476,192 per 90 days (60,847 a day; repo figure, not re-read) for **87,644 a day**.

| Figure | Repo today (Fund burned) | Jake's default (Fund held, A4 off, no burn line) | **Recommended** (Fund held; Core + EVM burns true) |
|---|---|---|---|
| Circulating | 539,758,896 | 589,050,311 (+ the Fund 49,291,415) | ~588,954,000 (− the EVM base-fee burn ~96k, INFERRED) |
| Free float | 99,462,452 | 99,462,452 | ~99,366,000 (− ~96k, INFERRED) |
| Burned, lifetime | Fund 49.29M + Core 1.32M (+ EVM `0x0` 0.32M, out of circulating but not in `total_burn_tokens`) | — | **~1.74M**: Core 1,319,521 + EVM `0x0` 321,141 + EVM base ~96,000 + Core `0x0`/dead 1,676 |
| A4 burn per day | 30,526 (the Fund) | off | **~4,677** (Core 4,316 + `0x0` 278 + base ~83) |
| Burns per year | 11.14M (A4); 12.72M with Core (`total_burn_tokens`) | — | **1.71M** |
| Burns per year ÷ free float | 11.2% (A4); 12.8% with Core | — | **1.72%** |
| A4 burn ÷ issuance, staking basis | 1.14× | n/a | **0.17×** |
| A4 burn ÷ issuance, option-C basis | 0.35× | n/a | **0.053×** |
| A3 | On (the Fund's buys) | On, unchanged | On, unchanged |

Without the base-fee leg (if Jake takes only what is measured on chain), the A4 burn is 4,594 a day, 1.68M a year, 1.69% of the float and 0.17× issuance. The headline does not change.

---

## Decision for Jake

**Hyperliquid's burns under item 2 and A4.**
- Option A: keep the full held treatment. The Fund is held and A4 is off. The ~1.7M a year of real destruction does not appear as a burn line.
- Option B: the Fund is held. Core burns, HyperEVM base fees and HyperEVM zero-address fees count as true burns under item 2, and A4 is on, scored on them: 4,677 a day, 1.71M a year, 1.72% of the float, 0.17× issuance. Also subtract the base-fee burn (~0.1M) from circulating and the float.
- Option C: option B without the zero address (the strictest test: only HYPE whose supply has fallen). 4,399 a day, 1.61M a year, 1.62% of the float, 0.16× issuance.
- Option D: go back to treating the Fund as burned. A4 11.14M a year, 11.2% of the float, 1.14× issuance. Not supported: the issuer itself says the Fund is reachable by a hard fork.
- **Recommendation: B.** Core burns and EVM base fees are destroyed, and the arithmetic proves it. The zero address passes the same keyless-sink test the book already applies to `0x…dEaD` everywhere else, and its size makes no material difference either way. The Fund stays held.

## Could not verify

- **The split of the Core burn by channel.** No primary endpoint gives totals for deploy auctions, gossip auctions, order priority fees or HYPE spot-fee burns. The auction figures above are from one live reading (INFERRED). ASXN's `burn_auction` series is browser-only and absent from the fixture.
- **The cumulative HyperEVM base-fee burn.** It has no address. Summing every block's base fee was not done (rate limits on rpc.purroofgroup.com). The ~96,000 rests on ASXN (secondary) plus extrapolation. ASXN's split looks unreliable: its base + priority total (314,850 to 2026-10-13) roughly matches the zero address's rise (308,208), although the zero address takes priority fees only. Either ASXN's split or the base-fee total is off. INFERRED that the base-fee figure is the uncertain one.
- **The 11.36 HYPE by which Core + EVM `0x2222…` exceeds 1bn.** Stable across reads, not explained.
- **Whether any Core code path can debit Core `0x0…0` (1,673.79) or `0x…dead` (2.72).** `spotClearinghouseState` returns null for those addresses. Their history was not read.
- **Whether HyperEVM code can debit EVM `0x0`.** Node code was not read. The claim rests on the docs, no code at the address and 596 readings with no fall.
- **The dates of the Core totalSupply fixture readings.** The repo stores the day, not the hour. Per-day rates are ±2%.
- **Option-C issuance.** The Unlocks Q0 figure (5,476,192) is the repo's own and was not re-read. The team wallet rose since SPEC's read (238.25M → 238.35M), so its unlocks do not show as a fall.
- **x.com directly.** It has a login wall. Both Foundation posts were read through api.fxtwitter.com. Validator forum replies were not read.
