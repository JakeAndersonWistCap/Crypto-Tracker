# R8 rule audit — the final circulating / free-float rule against the seven frozen projects

Read-only, 2026-11-17. Nothing in the repo was changed.

**Headline.** Applied literally, the rule moves every DAO treasury, issuer multisig and staked team wallet INTO circulating (off free float), because a governance vote or a multisig can move them. Only four things stay out: burns, Hyperliquid's future-emissions reserve, the unaccrued fee part of Sky's converter, and (if Jake accepts a legal or modelled lock) Fluid's IGP-137 wallet and Morpho's relock. Free float does not change for legs that were already out of circulating; it only changes where noted.

**Basis for the numbers.**
- Balances are `balanceOf` reads on Ethereum block 26,434,142 (publicnode), Base and Arbitrum, and the Hyperliquid info API, all 2026-11-17.
- The fixtures' metric rows stop at 2026-10-13 and store only CoinGecko's `circulating_supply` (plus Hyperliquid's `circulating_supply_first_party` on its old basis). The local `metrics.db` is empty. So "current" below is recomputed from the config legs at today's balances, not read from the dump.
- "In force" means the add-back switches (Maple, Morpho, Pendle) are treated as on.
- CoinGecko: `/coins/markets`. CoinMarketCap: `data-api/v3/cryptocurrency/detail`. Both read 2026-11-17 10:25 UTC.

Columns: U = unvested/unreleased; M = somebody can move it; E = earns.

## 1. Uniswap (`total_supply_gross` 1,000,000,000)

| Leg (config key) | Address | Balance | U / M / E | Rule |
|---|---|---|---|---|
| `burn_address_balance` | 0x…dEaD | 114,205,581.21 | no key | BURNED, out of circulating and total |
| `treasury_holding_tokens` (Timelock) | 0x1a9C8182… | 262,247,996.31 | U: no (treasury vesting ended, the four vesting contracts hold 0). M: yes — `admin()` = Governor Bravo 0x408ED635…, 2-day delay. E: no (`delegates()` = 0) | MOVES IN, off float |
| `noncirculating_holding_tokens` (4 treasury vesting contracts) | 0x4750…, 0xe395…, 0x4b4e…, 0x3D30… | 0 each | empty | no effect; drop |
| same metric (merkle distributor) | 0x090D4613… | 216,772.09 | released airdrop; claimants can claim; idle | MOVES IN (on float once claimed; immaterial) |
| already in: Uniswap Foundation | 0x5069A64B… | 13,314,112.21 | foundation holding | in circulating; must be off float (wiring not verified) |

- Current 623,329,650 → rule **885,794,419** (+262.46M).
- Config: `CIRCULATING_ONCHAIN["Uniswap"]["subtract"]` → `("burn_address_balance",)`. Add `treasury_holding_tokens` and the Foundation wallet to an off-float entry (`FLOAT_RELEASED_HELD["Uniswap"]`).
- vs CoinMarketCap 623,546,422: +42.1%, which is the Timelock exactly (CoinMarketCap's total 885,794,419 − 262,247,996).
- vs CoinGecko 607,549,896: +45.8% = Timelock 262.25M + Foundation 13.31M + distributor 0.22M; 2.47M residual not named.

## 2. Fluid (total 100,000,000)

| Leg | Address | Balance | U / M / E | Rule |
|---|---|---|---|---|
| `treasury_holding_tokens` (DSA, holds the bought-back FLUID) | 0x28849D2b… | 16,303,003.18 | M: yes, governance. E: none read (plain balance, no delegate; yield positions not verified) | MOVES IN, off float |
| `noncirculating_holding_tokens`: Team Multisig | 0x4F6F977a… | 579,773.48 | M: yes (Avocado wallet, `owner()` 0xc7810aa3…) | MOVES IN, off float |
| same: Timelock, Team Multisig 2, Foundation | 0x2386…, 0x1e2e…, 0xde03… | 0 | empty | no effect |
| same: 5 Merkle distributors | 0x7060…, 0xbAbB…, 0xB48B…, 0xD833…, 0x2524… | 131,518.70 | claimable by users | MOVES IN |
| `noncirculating_igp137_tokens` | 0xCaBebC7f… | 5,000,000.00 | U: yes (IGP-137 "legally locked … until 2030"). E: no. M: on chain yes — a plain Avocado wallet, `owner()` 0x8ae21fc3…; the lock is legal only | **DECISION 2**: literal → MOVES IN, off float |

- Current 77,985,705 → rule **100,000,000** (95,000,000 if IGP-137 stays out).
- Config: empty Fluid's `subtract`; change `HELD_BUYBACK_FLOAT["Fluid"]` from `how: "outside"` to `how: "subtract", held: "treasury_holding_tokens"`; team multisig, distributors and IGP-137 go to an off-float set.
- vs CoinGecko and CoinMarketCap 83,696,997 (both): +19.5% = the treasury 16,303,003 exactly.

## 3. Maple (total 1,267,875,000)

| Leg | Address | Balance | U / M / E | Rule |
|---|---|---|---|---|
| `treasury_holding_tokens_chain` (DAO multisig) | 0xd6d4Bcde… | 42,290,111.33 | M: yes, Safe threshold 4. E: no (0 stSYRUP) | MOVES IN, off float |
| `noncirculating_holding_tokens`: syrupDrip | 0x509712F3… | 166,610.40 | undistributed rewards; reclaim right not verified | MOVES IN, off float (immaterial) |
| same: treasury registry, Migrator, RecapitalizationModule, governor timelock, two admins | — | 0 each | empty | no effect |
| `maple_ssf_safe_tokens` (added back by the switch) | 0x58BE0049… | 48,624,987.59 | held buyback, Safe threshold 3, 0 stSYRUP | already in, off float — compliant |

- Current 1,225,418,278 (in force) → rule **1,267,875,000**.
- Config: empty `subtract`; keep the Safe add-back as a plain inclusion; add the DAO multisig and syrupDrip to the off-float set.
- vs CoinGecko 1,167,271,959 (identical to its 2026-10-13 value): +8.6% = DAO 42.29M + Safe 48.62M + drip 0.17M; 9.5M residual not named.
- vs CoinMarketCap 1,191,009,307: +6.5%. CoinMarketCap's total (1,244,677,493) is 23.2M under on-chain supply and it publishes no method — not established.

## 4. Morpho (total 999,999,999.80)

| Leg | Address | Balance | U / M / E | Rule |
|---|---|---|---|---|
| `noncirculating_holding_tokens` (DAO: new + legacy + Base) | 0xcBa28b38… | 578,871.10 + 270,140,344.68 + 13,000,000 = 283,719,215.78 | M: yes, Safe 6-of-10. E: no | MOVES IN, off float |
| `morpho_relocked_tokens` (MODELLED, Founders 152M + Cohort 3 67M) | no wallet disclosed | 110,700,000 (369/730 days left) | U: yes per filing. M and E: **not verifiable** — no contract or wallet published, so immovability is asserted by a legal agreement only | **DECISION 3**: literal → MOVES IN, off float as locked |
| `morpho_undistributed_tokens`: Contributor Grants Safes | 0x53051Ef9…, 0x1590e7F4… (legacy) | 41,419,748.87 + 11,391,864.52 | M: yes, Safes 5-of-8 and 4-of-8 | MOVES IN, off float |
| same: Rewards Safe (Ethereum, legacy, Base) | 0xF057afeE… | 1,241,821.98 + 356,158.26 + 968,190.25 | M: yes, 3-of-7 | MOVES IN, off float |
| same: Merkl distributors, URD, campaign funder | 0x3Ef3D8bA…, 0x330eefa8…, 0x30Bf2590… | 1,140,891.01 | claimable / EOA | MOVES IN (Katana legs not read) |
| already in: Association and Operative Safes | 0x6ABfd613… and others | 89,025,263.71 | issuer | in, off float — compliant |

- No Morpho wallet read is staked or in a yield position (plain balances; MORPHO has no staking). The relock holders cannot be checked at all.
- Current 549,062,109 → rule **999,999,999.80** literal; 889,299,999.80 if the relock stays out.
- Config: `subtract` → `()` (or `("morpho_relocked_tokens",)`); remove `subtract_when_complete`; extend `FLOAT_RELEASED_HELD["Morpho"]` to the DAO, Grants and Rewards legs.
- vs CoinGecko 705,647,499: +41.7% = DAO 283.72M; 10.63M residual not named.
- vs CoinMarketCap 566,641,267: +76.5% = DAO 283.72M + relock 110.70M + about 38.9M not established.

## 5. Pendle (total 281,527,448.46)

| Leg | Address | Balance | U / M / E | Rule |
|---|---|---|---|---|
| `noncirculating_holding_tokens`: Ecosystem Fund | 0x399Be606… | 16,120,440.00 | M: yes, Safe 2-of-4. E: no (0 sPENDLE, no vePENDLE position) | MOVES IN, off float |
| same: Governance multisig | 0x8119EC16… | 19,751,622.07 | M: yes, Safe 3-of-5. E: no | MOVES IN, off float |
| `pendle_released_held_tokens` (Team multisig + fee wallet) | 0x918cF6b1…, 0x82704… | 7,733,190.01 + 13,603.37 | released | already in, off float — compliant |

- Current 245,655,386 (in force) → rule **281,527,448**.
- Config: `subtract` → `()`; add the two multisigs to `FLOAT_RELEASED_HELD["Pendle"]`.
- vs CoinGecko and CoinMarketCap 174,163,825 (both): +61.6% = vePENDLE 63,159,346 + Ecosystem 16.12M + Governance 19.75M + Team 7.73M; 0.59M residual.

## 6. Sky (total 23,452,431,915.61)

The MKR_SKY converter 0xA1Ea1bA1… holds 2,023,933,778.79 SKY (`noncirculating_holding_tokens`); the legacy converter holds 0. The tracker adds back `sky_mkr_nonissuer_backing_tokens` (1,883,623,640.59 today: MKR supply 82,664.63, rate 24,000, fee 5%). The 140,310,138 net exclusion splits exactly three ways:

| Part | SKY | Test | Rule |
|---|---|---|---|
| Backing unconverted MKR, net of fee | 1,883,623,640.59 | any MKR holder can call `mkrToSky` | in circulating — compliant |
| Fee on still-unconverted MKR | 99,138,086.35 | unreleased; idle; governance can only `burn(skyAmt)` it (verified source, Sourcify) | STAYS OUT (all three hold) |
| Accrued fee, `take()` | 39,982,641.85 | `collect(to)` is open to wards; Pause Proxy is a ward (`wards` = 1) | MOVES IN, off float |
| Backing of the Pause Proxy's 49.56 MKR | 1,189,410.00 | governance can convert | MOVES IN, off float |

- Pause Proxy 0xBE8E3e36… (35,259,586.38, held buyback), the two endowments (liquid 37,781,122.67; Lockstake about 2.93B) and lsSKY are in circulating and off float — compliant.
- Current 23,312,121,777 → rule **23,353,293,829** (+41.17M).
- Config: add a leg for `take()` plus the issuer MKR backing to `CIRCULATING_ONCHAIN["Sky"]["add"]` and to the off-float set.
- vs CoinGecko 23,417,172,329 and CoinMarketCap 23,417,236,509: −0.27%, inside 2% (both are total − Pause Proxy).

## 7. Hyperliquid (tokenDetails total 998,730,613.05)

| Leg | Address | Balance | U / M / E | Rule |
|---|---|---|---|---|
| Assistance Fund | 0xfefe…fefe | 49,030,908.79 spot, 0 staked | system address, no key | BURNED |
| 0x0 and 0x…dEaD | — | 1,673.79 + 2.72 | — | BURNED |
| `futureEmissions` (no address) | protocol reserve | 409,852,654.15 | unreleased; no wallet; idle (controller not verified) | STAYS OUT |
| `team_wallet_tokens` | 0x43e9abea… | 238,246,928.78 staked across 23 validators; 0.0033 spot; 0 pending withdrawal | U: yes. **E: yes** — `delegatorRewards` 14,232.22 HYPE on the last day, 99,562 in 7 days, 439,864 in 30 days, 9,969,109 since 2024-12-29 | MOVES IN, off float |
| `excluded_wallets_spot_tokens` (Grants spot) | 0xa20fcfa0… | 105,001.00 | idle, but a keyed wallet the Foundation can move | MOVES IN, off float |
| already in: Foundation stake | 0xd57ecca4… | 60,635,074.09 staked (3,609.61/day), 0 spot | staked, earning | in, off float — compliant |
| already in: Grants stake | 0xa20fcfa0… | 2,874,535.38 staked (171.01/day) | staked, earning | in, off float — compliant |

- Current about 301.18M (tokenDetails 301,598,445 − Grants spot − HyperEVM burn of about 0.32M, taken from the 2026-11-16 estimate, not re-read) → rule about **539.53M**.
- Config: `CIRCULATING_ONCHAIN["Hyperliquid"]["locked_excluded_metrics"]` → `[]`; `STAKE_PRORATA["Hyperliquid"]["outside"]` removed; `team` and `spot_metric` in `CIRC_RELEASED_STAKED` treated as released; `UNLOCK_STOCKS["Hyperliquid"]` → `()`. Team and Grants spot go off float.
- vs CoinGecko 237,713,007: +127% = team 238.25M + Foundation 60.64M + Grants 2.98M.
- vs CoinMarketCap 252,567,398: +114% = the same wallets; CoinMarketCap counts 14.85M more than CoinGecko on an unpublished basis.

**Assistance Fund checks.**
- Docs (primary, read today): "The assistance fund uses the system address 0xfefe…fefe … HYPE in the assistance fund is burned, removing the tokens permanently from the circulating and total supply."
- Vote: proposed by the Hyper Foundation on 2025-12-17 (X post, cited by Cointelegraph; the post itself was not opened). Result reported 2025-12-24: 85% of stake for, 7% against, 8% abstaining (crypto.news, Tangem). The Foundation's own result post was not found — the result is **verified from secondary sources only**.
- **Config says 2025-12-27; every source read says 2025-12-24.** The date needs correcting or sourcing.
- Destination: `tokenDetails.nonCirculatingUserBalances` lists only the team wallet, 0xfefe…, 0x0 and 0x…dEaD — no other fund address. The Fund's last 2,000 fills (2026-11-14 to 11-17) are all HYPE buys. Its ledger since 2025-12-27 has 3,082 entries, all inbound, 0 outbound. The fixture's balance series (2026-09-11 to 10-13) never falls.
- Not confirmed: fills before 2026-11-14 (the API serves only recent fills), so "every buyback since December 2025" is confirmed by balance and ledger, not fill by fill.

## Bought-back tokens held, not burned

| Project | Wallet | Balance | Now | Rule requires |
|---|---|---|---|---|
| Uniswap | none (burn goes to 0x…dEaD; TokenJar 0xf38521f1… holds 0) | — | burned | compliant |
| Fluid | Treasury DSA 0x28849D2b… | 16,303,003.18 (whole treasury; bought-back share not separated) | OUT of circulating | in, off float — **change** |
| Maple | SSF Safe 0x58BE0049… | 48,624,987.59 | in, off float | compliant |
| Maple | EOA 0x99f03ca0… (received 27.5M from the Safe; controller unknown) | 2,402,392.11 | in; float subtracts Maple's whole SSF chart (77.66M on 2026-10-12, not reconciled to wallets) | compliant if the chart is right — not verified |
| Morpho, Pendle | none on file | — | — | — |
| Sky | Pause Proxy 0xBE8E3e36… | 35,259,586.38 | in, off float | compliant |
| Hyperliquid | Assistance Fund | 49,030,908.79 | burned | compliant |
| Near | fefundsadmin.sputnik-dao.near | 2,026,707.84 | in, off float (`HELD_BUYBACK_FLOAT`) | compliant |
| Near | buybacks.multisignature.near | 2,318,834.77 | in, off float | compliant |
| Near | 1csfundsadmin.sputnik-dao.near | 270.88 | in, off float | compliant |

Near wallets read by `view_account` on rpc.fastnear.com; staking-pool delegations were not checked.

## Decisions for Jake

1. **Treasuries.** The literal rule reverses the 2026-11-16 test ("undistributed treasury / DAO allocation → not circulating"). It puts Uniswap's Timelock (262.2M), Fluid's treasury (16.3M), Maple's DAO multisig (42.3M), Morpho's DAO (283.7M) and Grants Safes (52.8M), and Pendle's Ecosystem and Governance multisigs (35.9M) into circulating. Our figures would then sit 20–77% above CoinGecko and CoinMarketCap on five projects, each gap named above. The alternative is to read "nobody can move it" as "no holder can move it without a governance release", which keeps today's figures.
2. **Fluid IGP-137 (5M).** The lock is legal, not on-chain; the wallet's owner key can move it. Literal: in, off float (100M). Alternative: out (95M).
3. **Morpho relock (110.7M, modelled).** No wallet exists to test "nobody can move it" or "earns nothing". Literal, and "never invent exclusions": in, off float. Alternative: keep the modelled exclusion.
4. **Hyperliquid team (238.2M).** Unvested but staked and earning about 14.2k HYPE a day, so the rule puts it in. Circulating goes from about 301M to about 540M, more than double CoinGecko's.
5. **Sky converter.** Keep the 99.1M unaccrued fee out, and move the 40.0M accrued `take` in? It is small either way (0.2%).
6. **Hyperliquid vote date.** Config's 2025-12-27 against 2025-12-24 in every source read.

## Sources (all read 2026-11-17)

- Chain reads: ethereum-rpc.publicnode.com (block 26,434,142), base-rpc.publicnode.com, arbitrum-one-rpc.publicnode.com; rpc.fastnear.com
- Hyperliquid info API: https://api.hyperliquid.xyz/info (`tokenDetails`, `spotClearinghouseState`, `delegatorSummary`, `delegations`, `delegatorRewards`, `userFills`, `userNonFundingLedgerUpdates`)
- Hyperliquid docs: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
- Vote proposal: https://cointelegraph.com/news/hyperliquid-validators-vote-assistance-fund-supply (cites https://x.com/hyperfnd/status/2001127850754367525)
- Vote result: https://crypto.news/hyper-foundation-confirms-hype-burn-as-validators-back-assistance-fund-vote/ ; https://tangem.com/en/news/crypto/7454-hyper-foundation-burns-37m-hype-tokens-after-validator-vote/
- MkrSky source: https://sourcify.dev/server/v2/contract/1/0xA1Ea1bA18E88C381C724a75F23a130420C403f9a
- CoinGecko: https://api.coingecko.com/api/v3/coins/markets ; CoinMarketCap: https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail
- Repo: `/home/user/Crypto-Tracker/config.py` (`CIRCULATING_ONCHAIN`, `MORPHO_RELOCK`, `HYPERLIQUID_CIRC_R7`), `/home/user/Crypto-Tracker/SPEC.md` (R7B, PR4C.1)

## Round 9: the audit applied (2026-11-18)

All seven frozen projects now run on the rule (`config.RULE_IN`). The wallets listed above as "MOVES IN" are counted in circulating and taken off the free float on the same days. Every moved frozen cell is declared in `tests/fixtures/frozen_moves_r9.json`, with every earlier declaration kept.

| Project | Circulating on the test dumps, before → after | Tradeable float | Unlocks Q0 | Cells declared |
|---|---|---|---|---|
| Hyperliquid | 301,673,350 → 539,758,896 | 99,462,452, unchanged | 5,476,192, unchanged | 79 (+ 4 Notes cells: the vote date labelled unverified) |
| Morpho | 574,801,147 → 878,800,000 | 426,052,377, unchanged | 27,000,000, unchanged | 20 |
| Uniswap | 612,409,760 → 887,142,419 | 612,409,760, unchanged | 5,057,505, unchanged | 45 |
| Fluid | 77,971,195 → 100,000,000 | 77,971,195, unchanged | 140,838, unchanged | 24 |
| Maple | 1,176,793,290 → 1,267,875,000 | effective float 1,176,793,290, unchanged | 12,000,000, unchanged | 42 |
| Pendle | 237,886,870 → 281,527,448 | 143,108,807, unchanged | 1,675,300, unchanged | 50 |
| Sky | 23,312,121,800 → 23,352,337,700 | 5,880,380,760, unchanged | none (no vesting wallet set) | 56 |

### Decisions applied

- **Fluid, the IGP-137 wallet `0xCaBebC7f76D53582a4be7d9972A2b4F531753fd7` (5,000,000 FLUID): in.** It was carried as a lock outside circulating; that label was wrong.
  - Proposal text (Instadapp/fluid-governance, IGP-137 description): 5,000,000 FLUID (5% of supply) is withdrawn from the Treasury DSA `0x28849D2b…` to the Team Multisig `0x4F6F977a…`, "subsequently moved into a dedicated wallet before distribution to the relevant custodial partners". "These tokens will be legally locked for at least 4 years until 2030."
  - Signers: the wallet is an Avocado smart wallet. `owner()` is `0x8ae21fc31f41791578fecd0509ec0f59781cedfc`, an externally owned account (no code; `getOwners()` and `getThreshold()` do not answer). One key signs. There is no multisig and no on-chain lock or vesting contract.
  - Balance: exactly 5,000,000 at Ethereum block 26,441,359.
  - The lock is a legal undertaking only, so the key can move the tokens and the rule counts them in.
- **Morpho, the relock (110.7M, modelled): stays out.** Findings: no wallet or contract is published for the relock; MORPHO has no staking; none of the disclosed Morpho wallets is staked or in a yield position (read 2026-11-17). No wallet and no earning evidence was found, so nothing changes.
- **Sky.** The accrued converter fee `take()` (39,982,641.85) and the SKY backing the Pause Proxy's 49.56 MKR (1,189,410) are in circulating and off the float. The fee on unconverted non-issuer MKR (99,138,086.35) stays out. Both new legs are computed from series already stored: take = the converter's SKY − MKR outstanding × 24,000 (39,982,659 against the direct read, a 17 SKY difference from the two decimals of the MKR supply); issuer backing = the Pause Proxy's MKR × 24,000.
  - **The float answer.** The Round 8 text said Sky's float rises 41.2M. That was wrong: 41.17M is the rise of circulating. The same amount leaves the float on the same days, so the float does not move (5,880,380,760 before and after on the dumps).
- **Dilution.** Scheduled vesting releases stay the unlock legs even where the wallet is now in circulating (option a). Every unlock figure above is unchanged.

### The token-flow statement

A release from a wallet counted in circulating no longer changes circulating; it dilutes the float. The statement for a rule project is: opening + minted + released from wallets still outside circulating − burned. Per project: Morpho keeps the relock's releases; Hyperliquid counts every staking reward; Maple counts mints to the DAO multisig when minted; Uniswap's 100,000,000 burn from the Timelock (2025-12-27) is a circulating burn. Not on the statement: the Sky conversion fee moving into `take()` (5% of the MKR converted).
