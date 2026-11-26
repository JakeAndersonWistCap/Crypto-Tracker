## Solana (SOL) — R24, A1 Infrastructure + A4 Permanent burn (A3 does not apply)

Read 2026-11-26 from the keyless public RPC (`api.mainnet-beta.solana.com`, via `fetch/solana.SolanaRPC`, methods added; tier
`solana_rpc`) and DefiLlama. Dump: `fixtures/real_solana.json` (flag `asof_exempt`: a later read than the 2026-10-13 vintage the
frozen projects are pinned to, so it does not set the build's as-of; `tests/real_dump.build_asof`). **Every chain read is
FORWARD-ONLY from 2026-11-26** (the public RPC serves no past supply, stake or rewards); nothing was back-filled or invented.
The burn has a year of history (DefiLlama burned-fee revenue / price).

| Cell | Value | Source | Status |
|---|---|---|---|
| Circulating supply | 592,183,208 SOL | the issuer's own figure = `getSupply.circulating` (Agave non-circulating rule); CoinGecko carries the same number to 7 digits (592,183,197.8 on chain) | measured (issuer figure) |
| Total supply | 638,224,669 SOL | `getSupply.total` | measured, FORWARD-ONLY |
| Non-circulating | 46,041,471 SOL in 3,194 accounts | `getSupply.nonCirculating`, account list (`excludeNonCirculatingAccountsList=false`) | measured, FORWARD-ONLY |
| Staked (all) | 423,675,927 SOL (657 current + 31 delinquent vote accounts) | Σ `activatedStake`, `getVoteAccounts` | measured, FORWARD-ONLY |
| Staked inside the non-circulating accounts | 44,795,667 SOL | `getMultipleAccounts` (jsonParsed), active delegations only | measured, FORWARD-ONLY |
| Staked and counted in circulating | 378,880,260 SOL = 63.98% of circulating | the two lines above, a token comes off once | derived |
| **Free float** | **213,302,948 SOL** = circulating − staked in circulating | `free_float.py`, one series | derived; Foundation leg GAP (below) |
| Inflation rate in force | 3.5397% a year (epoch 1097); governor 8% initial, 15% taper, 1.5% terminal | `getInflationRate`, `getInflationGovernor` | measured |
| Gross issuance | **57,574 SOL/day** (21.0M a year, 3.29% of total) | EpochRewards sysvar `total_rewards` 61,874.88 SOL for epoch 1096 ÷ its measured 1.0747 days (first-block times) — ACTUAL rewards the chain created, not rate × supply | measured, FORWARD-ONLY (1 day stored) |
| Issuance, rate × supply / 365 (the brief's computation) | 61,900 SOL/day | 3.5397% × 638.22M / 365 | computed from the chain's reported rate; NOT stored — the 7% gap is the nominal 400 ms slot the rate assumes against the ~215 ms slots measured |
| Gross burn | 466 SOL/day on 2026-11-25 (715/day mean of the last 90 days: 64,324 SOL) | DefiLlama burned-fee revenue (`fees/solana.ts`: 50% of base fees; priority fee all to the leader since SIMD-0096, 2025-02-12) ÷ the day's price; 364 days (price starts 2025-11-27) | derived from a sourced series; the Ethereum pattern |
| Burn observed on chain | 349 SOL/day | fall of `getSupply.total` over a 45 s window inside one epoch, scaled to a day | **MODELLED** to a day; one window; cross-check only (same order as the 466) |
| Burn ÷ issuance | 0.8% (466 ÷ 57,574); 1.2% on the 90-day burn mean | the two lines above | the workbook cell reads **blocked** (unequal coverage: issuance 1 of 90 days) until the daily reads accumulate |
| Burn share of fees | 11.0% (90 days) | burn ÷ fees_usd | derived |
| Fees / revenue | fees $61.2M, revenue (the burn) $6.7M, 90 days | DefiLlama `solana` chain adapter | measured |
| DEX volume | 1,878 days from 2021-10-02; trailing 365 days $951.2bn | DefiLlama `/overview/dexs/solana` | measured |
| Settlement (A1 key) | — | DEX leg wired; P2P transfer leg has no free source | **GAP: settlement: P2P transfer leg has no free source — Jake decides on billing** (`docs/r24_solana_settlement.md`) |
| Archetype key / rank | n/a / unranked | needs settlement | GAP, as above |
| Net holder yield | 0.047% (= holder value yield; total dilution n/a) | A4 burn yield on market cap | dilution blocked while issuance covers 1 of 90 days |

### The non-circulating set, by what it is (Agave `non_circulating_supply.rs`, anza-xyz/agave master, read 2026-11-26)
The rule: a fixed list of 214 accounts, plus every stake account whose lockup is in force or whose withdrawer is one of 114 listed
"autostake" withdraw authorities. The 3,194 accounts on chain split exactly into three classes (no third-party labels used):

| Class | Accounts | SOL | What it is |
|---|---|---|---|
| A. fixed list | 214 (97 hold SOL) | 2,163,633 | system-named accounts in the client's list; largest `EMAY24Pr…3Wup` 537,553, `5D5NxsNV…hQrhNW` 433,661, `EziVYi3S…VEn7` 427,967, `Br3aeVGa…Mtmt` 401,331, `6o5v1HC7…2Suy` 240,690 |
| B. autostake withdraw authority | 2,242 | 27,387,072 | withdrawer `4ZJhPQAg…kbPY` 669 accounts 27,383,419; `HXdYQ5gi…RWE` 1,572 accounts 3,653; `4e6KwQpy…1EA` 1 account 1 |
| C. lockup in force | 738 | 16,490,766 | custodians `3XdBZcUR…Gxg` 467 accounts 8,994,887 (unlocks monthly 2026-12-07 to 2028-01-07); `APfu848V…5Bh` 228 accounts 5,390,039 (2026-11-28 to 2028-09-13); `BfFYshuf…zo` 10 accounts 2,044,402 (2026-12-11 to 2027-09-11); `8DE8fqPf…jC` 4 accounts 61,234; others 11 accounts |

By Jake's rule: C is unvested/locked and stays OUT (it releases as the lockups expire; each release is new supply when it moves).
A and B (29.55M SOL) are in the issuer's non-circulating figure, and nothing on chain or in a Foundation document identifies them
as Foundation-held and transferable, so they stay out as the issuer states. Decision 2 below.

### Open gaps (never zero)
1. **Foundation liquid holdings off the float**: the Solana Foundation publishes no wallet list (its transparency page is dated 2020, no
   wallet attribution; searched 2026-11-26). Not subtracted from the free float; labelled gap (`config.SOLANA_FOUNDATION_GAP`).
2. **FTX estate unlocks**: no primary source says which lockups are the estate's. On chain, the 738 lockups above unlock monthly to 2028
   (`config.SOLANA_FTX_GAP`); who owns them is not on chain.
3. **Settlement P2P leg** (A1 key): docs/r24_solana_settlement.md.
4. **History of every chain series** (supply, stake, issuance, rate, non-circulating): forward-only from 2026-11-26; Burn ÷ issuance
   and Net issuance after burn are blocked until the issuance covers the window.
5. **Proposals** (label only, nothing wired): SIMD-0411 (30% taper), SIMD-0228, SIMD-547 — none live: the chain's governor taper reads
   0.15 on 2026-11-26; SIMD-0228 failed its March 2025 vote (61.6% < 66.67%); the SIMD repository's own status fields could not be read
   from this host (GitHub API 403), so any status beyond "not enacted" for 0411 and 547 is NOT VERIFIED. SIMD-0096 is live (2025-02-12).
6. Active addresses and transaction count: no free daily source wired (n/a).

### Decisions for Jake
1. **Settlement P2P billing** — recommend authorising ONE free BigQuery dry run to price a day, then deciding; until then the key stays a gap.
2. **A and B (29.55M SOL)**: keep out (issuer's figure) — recommended; if you want Jake's rule applied to them (Foundation-held,
   transferable → circulating, off float) a primary source naming the withdrawers as Foundation-controlled is needed first.
3. **Burn ÷ issuance**: accept "blocked" until ~30 days of issuance accumulate — recommended — versus a MODELLED issuance history
   (rate × supply, from the governor's schedule), which mixes two measuring points and is not recommended.
