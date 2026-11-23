# Round 19, item 3 — Aerodrome / Aero: the 911,157 "bought and then burned", and the staker-revenue gap

Research only. No code, config or test was changed. All reads are from 2026-11-23 (UTC) unless a row says otherwise.
Scratch scripts and raw outputs: `/tmp/claude-0/-home-user-Crypto-Tracker/e84ff2c6-8546-5133-82ff-742643596bbf/scratchpad/r19_aerodrome/`.

## Answers first

1. **The site's 911,157 is two parts right and one part unmatched.** 407,815.83 AERO (174,600.64 + 233,215.20) was
   bought and burned: matched to two burn transactions on Base. The other 503,340.91 was **not burned on any chain**.
   It sits liquid in the Fund wallet `0x834C0DA0…` today. It is not "pending" in any stated sense: no Aero page describes
   a pending or scheduled burn, and the site's own data row says "burned" and dates it 2026-11-20.
2. **Why the site says so (INFERRED).** The site's daily feed books the 503,341 burn on 2026-11-20. The only burn on
   chain that day is the 10,000,000 AERO of minted surplus (R18.A4). The feed appears to count part of that burn against
   the last purchase. On chain the purchased tokens never moved to the escrow, and AERO can only be burned through it.
3. **AERO exists on Base only.** The other chains hold a receipt token, xAERO, not AERO. There is nothing to burn there.
   There are now **eight** Aero chains, not seven: Elysium joined on 2026-11-08.
4. **No burn since the Round 18 read.** Still exactly three burns of the new token, to Base block 61,258,573.
5. **The revenue gap is the other chains.** Our stored on-chain read covers Base only. Base agrees with DefiLlama's Base
   figure within −3.2% to +1.3% and with aero.xyz's within −3.6% to +1.7%. The seven other chains pay $203k–$397k a
   week (15%–22% of the total) and we read none of it. A scratch read of those chains with our own method closes the gap
   from about −20% to about −3.5%.
6. **Holder value yield should use the all-chain figure**, read on chain, third-party liquid incentives only. Until the
   other chains are stored, DefiLlama's `holders_revenue_usd` is the closest stand-in. What the build credits today
   (DefiLlama *revenue* plus our Base bribes) counts the bribes twice and includes legacy fees that reach no holder
   (INFERRED from the config and build code; the build was not run).

---

## A. The eight chains, the token on each, and every burn

Sources: Dromos-Labs/metadex-public `deployment-addresses/*.json` at commit `2ce1816b` (2026-11-04, cloned today);
app.aero.xyz/docs/security ("AERO token (Token) — Base"); aero.xyz `llms-full.txt` ("8 Chains Reporting … Base,
Ethereum, Robinhood, ARC, OP Mainnet, Arbitrum, Ink, Elysium"); live RPC reads of each chain.

| Chain (id) | AERO token there? | What is there | xAERO supply today | Burns of AERO |
|---|---|---|---|---|
| Base (8453) — root | **Yes**: `0x3f5bF3Ed…a4Ee` | Token, escrow, Minter, Voter | 0.0000 (xAERO `0x6CdDa0E7…`) | 3 (below) |
| Ethereum (1) | No (no code at the Base address) | xAERO `0x9e148C57…` | 218,951.00 | none possible |
| Robinhood (4663) | No | xAERO `0x9e148C57…` | 396,921.25 | none possible |
| Arbitrum (42161) | No | xAERO `0x436a085B…` | 31,053.06 | none possible |
| OP Mainnet (10) | No | xAERO `0xbdEaeA52…` | 30,911.62 | none possible |
| Ink (57073) | No | xAERO `0x9e148C57…` | 21,340.35 | none possible |
| Arc (5042) | No | xAERO `0x20077cC0…` | 17,400.54 | none possible |
| Elysium (99901) — since 11-08 | No | xAERO `0x9e148C57…` (INFERRED: same address and bytecode size; not in the repo's files) | 45,105.09 | none possible |

How burns and bridge moves were told apart:

- **AERO does not bridge.** The verified source (`V3/src/token/Token.sol`) has no bridge mint. `mint` is the Minter only.
  `burn` can be called only by the escrow (sAERO) or the two migration contracts (`CallerNotBurner` otherwise).
- **xAERO is a receipt, not AERO.** `ReceiptToken.sol`: "Leaf chain receipt token minted and burned by the local
  LeafVoter". LPs on another chain earn xAERO. Redeeming burns the xAERO there (`LeafAllocationLibrary.sol` L437) and
  `Voter.processRedeem` mints AERO on Base. So an xAERO burn is paired with an AERO **mint**. It removes no supply.
- A third-party bridged copy of AERO, if one exists, would lock AERO on Base and leave Base's `totalSupply` unchanged.
  None is published by Aero. Not searched for.

So "check all seven chains for the 18 Nov purchase" has a simple answer: the purchase was on Base, the token only
exists on Base, and it is still there.

### Every burn of the new AERO (Transfer → 0x0, Base, blocks 52,880,070 – 61,258,573)

| Date (UTC) | AERO | From | Tx | What |
|---|---|---|---|---|
| 2026-10-28 | 174,600.64 | sAERO escrow | `0xe00ba4ce…6a33` | bought the same day, staked, burned |
| 2026-10-30 | 233,215.20 | sAERO escrow | `0xf800fc31…caf8` | bought the same day, staked, burned |
| 2026-11-20 | 10,000,000.00 | sAERO escrow (stake #27243 of the buyer Safe) | `0x439ba89d…361e` | the surplus minted 11-19 (R18.A4) |

No burn after 2026-11-20. Scan re-run today from block 61,224,886 to 61,258,573: zero.

### Where the purchases are today (Base block 61,258,573, 13:54 UTC)

| Purchase | Token | AERO | Path | Today |
|---|---|---|---|---|
| 2026-10-28 | new | 174,600.64 | buyer Safe → escrow → 0x0 | burned |
| 2026-10-30 | new | 233,215.20 | buyer Safe → escrow → 0x0 | burned |
| 2026-11-04 | **legacy** | 243,612.23 | buyer Safe → Fund wallet (`0x9a75c06a…7704`) | liquid in the Fund wallet |
| 2026-11-11 | **legacy** | 294,003.10 | buyer Safe → Fund wallet (`0x4e76d2a2…04f2`) | liquid in the Fund wallet |
| 2026-11-18 | new | 503,340.91 | buyer Safe → Fund wallet (`0x6f2c63fa…810f`, block 59,102,538) | liquid in the Fund wallet |

- Fund wallet `0x834C0DA026d5F933C2c18Fa9F8Ba7f1f792fDa52`: **new AERO 2,147,815.65; legacy AERO 537,615.33**.
- Buyer Safe `0x10b42e92…`: 0 of both.
- The Fund wallet's new-AERO balance reconciles to the token: in 1,728,692.45 + 1,164,053.44 + 503,340.91, out
  1,248,271.14 (six transfers), = 2,147,815.65. The only outflow after 11-18 is 23,966.00 to `0x101a124c…`. None of its
  outflows goes to the escrow or to 0x0.
- Legacy AERO: the Fund wallet has had **no legacy outflow at all** since 2026-11-04 (blocks 53,400,000 – 61,255,000).
  So the 537,615.33 has not been migrated, staked or burned.
- No stake of 243,612 / 294,003 / 503,341 AERO by anyone appears among the 45 stakes of ≥100,000 AERO since 11-03.

## B. What Aero and the Momentum Fund say about burn timing

| Source (read today) | What it says |
|---|---|
| aero.xyz/economics/metrics, page data (`econ-data-supply` → `buybacks.days`, pulled 2026-11-23T07:18:52Z) | Per-day rows. `momentumFund`: 10-28 174,601; 10-30 233,215; 11-18 503,341. `momentumFundBurned`: 10-28 174,601; 10-30 233,215; **11-20 503,341**. Nothing on 11-04 or 11-11. |
| Same page, dataset note (`buybacksQuarterly`) | "momentumFundBurned (Buyback and Burns) is the part of Momentum Fund that was bought and then burned, **counted on the burn day**: already inside momentumFund and total, never added to them." |
| aero.xyz/articles/the-aero-economy | "AERO the Fund buys back **can** be burned." The Fund is "market-aware", not on "a fixed schedule". No pending or scheduled burn is described anywhere on the site. |
| Announcements (SECONDARY — x.com has a login wall; syndication returned 429, xcancel 451, nitter is down). Read instead: BSCN via KuCoin (2026-11-11), CoinMarketCap's summary (2026-11-19), CryptoNewsNet via edgeX (2026-11-04) | 243K "staked" (4 Nov), 294K "bought and staked" (11 Nov), 503K "acquired and staked" (19 Nov). None says burned. None mentions a later burn. |

Two things follow:

- The site's 911,157 is built from **per-day rows**, summed by quarter. The third row is a specific claim: 503,341
  burned on 2026-11-20.
- The site's Momentum Fund series leaves out the two legacy purchases (537,615.33). Chain total bought this quarter is
  1,448,772.08; the site shows 911,157.

## C. Verdict and reconciliation

| Leg | Site says | Chain says | Verdict |
|---|---|---|---|
| 174,600.64 (10-28) | bought, burned 10-28 | bought, burned 10-28 | **Right** |
| 233,215.20 (10-30) | bought, burned 10-30 | bought, burned 10-30 | **Right** |
| 503,340.91 (11-18) | bought 11-18, burned 11-20 | bought 11-18, held liquid by the Fund; in no burn | **Unmatched** |
| 243,612.23 (11-04) | not in the series | bought (legacy), held liquid | site omits |
| 294,003.10 (11-11) | not in the series | bought (legacy), held liquid | site omits |
| 10,000,000 (11-20) | not shown as a burn | minted 11-19, burned 11-20 | not a buyback (R18.A4) |
| **Total "bought and burned"** | **911,157** | **407,815.83** | 503,340.91 over |

In plain words: the supply fell by 407,815.83 AERO because of buybacks. The 10,000,000 mint and burn net to zero. The
503,340.91 still exists and the Foundation holds it. One can read 11-20 as "the Safe burned 503,341 of bought AERO and
9,496,659 of surplus" only by also saying the Foundation kept 503,341 of freshly minted surplus. Either way 503,341 AERO
is in a Foundation wallet, not destroyed.

The announcements say the three November purchases were "staked". On chain they are not staked from the Fund wallet
either. A stake by another route in smaller pieces is not excluded.

## D. The staker-revenue gap

### What each source counts

| | Ours (stored) | DefiLlama `aero-protocol` holders revenue | aero.xyz "fees + liquidity payments" |
|---|---|---|---|
| Chains | **Base only** | 8 Aero chains + legacy Velodrome chains | 8 Aero chains + legacy |
| Fees | `NotifyFeesAmount` on the 317 Base reward managers, when fees are pushed to stakers | growth of each gauge's fee balance + what the gauge claimed (accrual), Basic and Slipstream pools | "trading fee revenue distributed per weekly epoch" |
| Incentives (bribes) | each programme's rate × overlap with the epoch, all creators | same, less seconds with no allocation; "Programs funded by Aero itself count too" | "liquidity payments" |
| Pricing | DefiLlama coins API at epoch end; 13–19 tokens a week unpriced, left out | DefiLlama prices; an unpriceable fee token is converted through the pool's other token | not stated |
| Legacy pools | separate tiny legacy row ($1.4k–$4.0k a week) | $0 to holders after 10-22 (their fees are "locked gauge fees", in revenue only) | $1.4k–$4.0k a week |

Adapter source read: DefiLlama/dimension-adapters at `8f26873a` (2026-11-23): `dexs/aero-v2/index.ts`,
`dexs/aero-v2/utils.ts`, `dexs/aero-lite/index.ts`, `dexs/aerodrome/index.ts` + `utils.ts`. There is no
`fees/aerodrome*` or `fees/aero*` file; the fee logic lives in the `dexs/` adapters.

### Weekly totals (USD)

| Week of | Ours (Base) | DefiLlama total | aero.xyz total | Gap to aero.xyz |
|---|---|---|---|---|
| 2026-10-22 | 1,159,296 | 1,357,626 | 1,384,220 | −224,924 (−16.2%) |
| 2026-10-29 | 1,426,324 | 1,804,467 | 1,788,159 | −361,835 (−20.2%) |
| 2026-11-05 | 1,463,746 | 1,838,828 | 1,842,920 | −379,174 (−20.6%) |
| 2026-11-12 | 1,641,087 | 2,060,837 | 2,065,570 | −424,483 (−20.6%) |

Ours splits: fees 928,352 / 1,365,993 / 1,439,735 / 1,624,523; incentives 230,944 / 60,331 / 24,010 / 16,564 (of which
created by issuer wallets 198,350 / 12,301 / 0 / 0); unpriced tokens 17 / 19 / 13 / 17.
aero.xyz splits: fees 1,090,125 / 1,693,357 / 1,787,928 / 2,036,448; incentives 294,095 / 94,802 / 54,991 / 29,122.

### Base against Base

| Week of | Ours | DefiLlama Base | aero.xyz Base | Ours vs DefiLlama | Ours vs aero.xyz |
|---|---|---|---|---|---|
| 2026-10-22 | 1,159,296 | 1,154,251 | 1,175,911 | +0.4% | −1.4% |
| 2026-10-29 | 1,426,324 | 1,407,581 | 1,402,477 | +1.3% | +1.7% |
| 2026-11-05 | 1,463,746 | 1,512,297 | 1,518,667 | −3.2% | −3.6% |
| 2026-11-12 | 1,641,087 | 1,686,782 | 1,698,909 | −2.7% | −3.4% |

Our Base read is sound. The gap is not Slipstream against Basic pools (we read both), and not incentives.

### The missing chains, two full weeks

"Our method, scratch" = the same events and programme proration as `fetch/aero_v3.py`, pointed at each chain's reward
managers (found through the chain's FactoryRegistry), priced by DefiLlama's coins API at epoch end. Read today.

| Chain | Wk 11-05: scratch | DefiLlama | aero.xyz | Wk 11-12: scratch | DefiLlama | aero.xyz |
|---|---|---|---|---|---|---|
| Robinhood | 122,877 | 131,219 | 129,722 | 171,401 | 178,108 | 172,787 |
| Ethereum | 105,092 | 108,274 | 105,369 | 94,891 | 108,030 | 96,282 |
| Arbitrum | 39,051 | 34,017 | 33,533 | 23,038 | 25,322 | 31,002 |
| OP Mainnet | 22,839 | 22,323 | 24,089 | 21,599 | 23,032 | 21,882 |
| Elysium | 18,957 | 20,430 | 19,120 | 26,192 | 28,861 | 27,867 |
| Ink | 6,539 | 6,069 | 7,130 | 4,805 | 4,915 | 4,956 |
| Arc | 5,615 | 4,198 | 3,939 | 5,834 | 5,786 | 7,930 |
| **Seven chains** | **320,970** | **326,531** | **322,902** | **347,760** | **374,055** | **362,707** |

Reward managers found: Robinhood 81, Ethereum 62, Arbitrum 56, OP 32, Ink 28, Arc 8, Elysium 8. Unpriced tokens: 0 on
six chains, 1 on Elysium.

### The gap, decomposed (ours against aero.xyz)

| Component | Wk 11-05 | Wk 11-12 |
|---|---|---|
| Seven chains we do not read | 322,902 (85%) | 362,707 (85%) |
| Base: ours below the site's Base figure | 54,921 (14%) | 57,822 (14%) |
| Legacy voter rewards (stored separately by us) | 1,351 (<1%) | 3,953 (1%) |
| **Total gap** | **379,174** | **424,483** |

With the scratch read added, all chains on chain = 1,784,716 (wk 11-05) and 1,988,847 (wk 11-12): −3.2% and −3.7%
against aero.xyz, −2.9% and −3.5% against DefiLlama.

The Base residual of about 3% is not decomposed. Candidates: the 13–17 unpriced tokens; fees accrued to a gauge but
not yet pushed at the epoch boundary (we count on push, DefiLlama on accrual); the price point.

### Which series holder value yield should use

Jake's rule: holder value = revenue-funded value reaching holders; liquid bribes to voters are credited (decision 4,
2026-10-12).

What the build credits today (read from `config.HOLDER_VALUE["Aerodrome"]` and `build_workbook.py` L7038–7105; INFERRED,
the build was not run): DefiLlama **revenue** (`revenue_usd`) + our Base incentives (`voter_rewards_onchain_bribes_usd`).
Two problems since 2026-10-22:

- DefiLlama's revenue already contains the incentives (adapter: `addBribes` adds to `dailyRevenue`). Adding ours counts
  Base incentives twice.
- DefiLlama's revenue contains legacy-pool fees that reach no holder ("locked gauge fees"): revenue − holders revenue
  = 138,232 / 78,551 / 52,039 / 32,581 a week.

| Week of | Credited today (INFERRED) | Paid to stakers (aero.xyz) | Over |
|---|---|---|---|
| 2026-10-22 | 1,726,802 | 1,384,220 | +24.7% |
| 2026-10-29 | 1,943,349 | 1,788,159 | +8.7% |
| 2026-11-05 | 1,914,877 | 1,842,920 | +3.9% |
| 2026-11-12 | 2,109,982 | 2,065,570 | +2.2% |

Options and what each gives for the week of 11-12:

| Option | Series | Wk 11-12 | For | Against |
|---|---|---|---|---|
| 1 | Our stored on-chain read (Base only) | 1,641,087 | primary | misses 18% that does reach stakers |
| 2 | DefiLlama `holders_revenue_usd` | 2,060,837 | all chains; within 2% of aero.xyz every week | secondary; includes issuer-funded and illiquid incentives |
| 3 | aero.xyz fees + liquidity payments | 2,065,570 | the issuer's own figure | not a chain read; split is "approximated" on some chains |
| 4 | On-chain read on all eight chains, less issuer-created and illiquid incentives | ~1,988,847 (scratch) | primary; applies decision 4 and R9.4 exactly | needs seven more chain reads wired; about 3% under the references |

Recommendation: **option 4** as the source of record, with **option 2 in its place until the other chains are stored**
and as the cross-check after. In both cases stop adding incentives on top of a series that already holds them.

---

## Decisions for Jake

**1. The 503,340.91 AERO the site calls burned.**
- (a) Not burned: bought, held liquid by the Fund, off the float as R13 already treats it. Burned buybacks this
  quarter = 407,815.83.
- (b) Follow the site: burned buybacks = 911,157.
- **Recommend (a).** The chain shows no burn of it and the token can only be burned through the escrow. Record the
  site's row as "not matching", as R18.A5 does, now with its date (11-20) and the inferred cause.

**2. How much of the 10,000,000 burn of 11-20 counts as a burned buyback.**
- (a) 0 — it was minted surplus (R18.A4); the bought tokens are still in the wallet.
- (b) 503,341 — the site's reading.
- (c) 1,818,679.81 — today's 8.18M cap (purchases since 2026-08-25 not yet burned).
- **Recommend (a).** Purchases bought and still held: 1,040,956.24 (537,615.33 legacy + 503,340.91 new).

**3. The series holder value yield uses for Aerodrome.**
- Options 1–4 in the table above (wk 11-12: 1.641M / 2.061M / 2.066M / ~1.989M; credited today ~2.110M).
- **Recommend option 4 (all-chain on-chain read, third-party liquid incentives only); option 2 (DefiLlama
  `holders_revenue_usd`) until the seven other chains are stored.** Drop the separate bribe add-on from 2026-10-22 when
  the base series already contains incentives.

**4. Issuer-created incentives in holder value.**
- R9.4 takes them out of FEES / REVENUE / HOLDER REVENUE. Holder value (decision 4) adds gross liquid bribes.
  Issuer-created on Base: 198,350 (wk 10-22), 12,301 (wk 10-29), 0 since.
- (a) take them out of holder value too; (b) leave them in.
- **Recommend (a).** They are Foundation spend, not revenue. The other chains' creators were not read.

**5. The chain list.**
- `AERODROME_MIGRATION["launch_chains"]` holds seven. Elysium (99901) is the eighth since 2026-11-08: $19k–$29k a week
  to stakers.
- **Recommend** keeping "launch chains" as seven and recording Elysium as a chain added after launch, so any all-chain
  read includes it.

## Could not verify

- **x.com/aeroxyz and the Foundation's own posts.** Login wall; syndication 429; xcancel 451; nitter down. The
  "staked" wording for the 243K, 294K and 503K purchases is from secondary reports only.
- **Why the site books 503,341 as burned on 11-20.** The cause given above is INFERRED from the date and the amounts.
  No Aero source explains it.
- **Whether the November purchases were staked by another route.** Not from the Fund wallet, and not as one stake of
  the purchase amount. Smaller stakes from other wallets were not traced.
- **Any plan to burn the held purchases.** No page read states one.
- **Elysium's deployment file.** Not in metadex-public at `2ce1816b`. Its xAERO address is taken from the same address
  and bytecode size as other chains, and its reward managers from the on-chain FactoryRegistry.
- **The ~3% Base residual** between our read and both references. Not decomposed.
- **What the build credits today.** Read from config and code, not from a build. Which of the two stored rows per
  epoch (the v3 row or the tiny legacy `aero_voter` row) the build picks for the bribe add-on was not checked.
- **Issuer-created incentives on the seven other chains.** Programme creators there were not read.
- **Third-party bridged AERO.** Not searched for. It would not change Base's total supply.
- **Scratch leaf reads use `latest` state for the programme list** (Arbitrum's public node keeps no history). Programmes
  are fixed records, so this should not move the figures; not proven.
