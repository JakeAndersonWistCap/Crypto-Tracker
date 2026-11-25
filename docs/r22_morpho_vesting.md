# Round 22 — Morpho's vesting contracts, on chain

Read 2026-11-25 at Ethereum block 26,492,109. Method: `eth_call` on each contract (`ids()`, `czar()`, `gem()`, and per
award `usr / bgn / clf / fin / tot / rxd / accrued / unpaid`), `allowance(czar, vester)` and `balanceOf` on legacy
MORPHO `0x9994E35D…0999`, and `eth_getLogs` for `Vest(uint256,uint256)`. Public RPC (rpc.mevblocker.io,
ethereum-rpc.publicnode.com), eth.blockscout.com API. User-Agent `token-metrics-check/1.0`. Scripts and raw reads are in
`scratchpad/r22_morpho/` (`vester.py`, `vest2.py`, `allow.py`, `vesters_26492109.json`, `events_labs.json`).

Jake (Round 22, item 3): "the same rule applies to every vesting contract. A claim of tokens that have already vested
is not new supply. Vesting counts as dilution on its schedule. Morpho's docs say every MIP1 award vests through a
contract: at least a 6-month lock, then 30 months linear, from 25 Dec 2022. List every vesting contract on chain with its
end date and the amount still unvested. Show what's left of Morpho's dilution, which should be mostly DAO emissions."

## The answer

| Contract | Pays from (czar) | Awards | Total | End | Claimed | Vested, unclaimed | **Still unvested (2026-11-25)** |
|---|---|---:|---:|---|---:|---:|---:|
| the DAO Vester `0x229AD3703b7962a9E1C9B6C4Dbaa1852bf770bfc` | the DAO `0xcBa28b38…9AFa` | 23 | 279,882,199.73 | **2025-06-11** | 229,525,631.72 | 50,356,568.01 | **0.00** |
| the Labs vester `0xe206A8006669A0913D6D13A781580e7E65524407` | `0x1590e7F4…97Dd` (seatbelt: Morpho Labs; filing: Contributor Grants Safe 2) | 62 | 21,227,398.12 | **2028-10-02** | 11,077,111.92 | 7,110,830.23 | **3,039,455.91** |
| the Association vester `0x60167C23eECe006dF7aDB048AeC4b4558957eB5A` | the Association `0x6ABfd613…f6a2` | 22 | 286,755,555.56 (on paper) | 2025-06-11 | 0.00 | 0.00 payable | **0.00** — never funded |

- All three are `DssVestTransferrable` (morpho-org/morpho-transfervest@7364e829, `src/Vester.sol`). A vester holds no
  tokens. A claim (`vest(id)`) pulls legacy MORPHO from the czar's own wallet under an allowance. The unvested and the
  vested-unclaimed tokens sit in the czar's wallet, and the czar's Safe can move them.
- **The DAO Vester** pays MIP1 (forum.morpho.org/t/94, 2022-08-19): the early contributors' MECT vestings converted
  to MORPHO. It holds the founders' 15.2% (the six restricted awards #10, #13, #14, #15, #17, #18 sum to exactly
  152,000,000; #17 / #18 are Paul Frambot's, docs/r21_morpho_wallets.md; the others are INFERRED). It also holds the
  Contributor Grants Safe's 79M (#16) and the Association's 0.5M (#12). Every award: `bgn` = `clf` = 2022-12-24 13:00 UTC,
  `fin` = 2025-06-11 13:00 UTC, linear, no on-chain cliff. That is the 6-month lock from the token's deployment
  (2022-06-24) followed by ~30 months linear. Four awards were yanked earlier (#8, #19, #20, #22). DAO allowance to it:
  101,500,000.01. **New:** the DAO Safe re-approved 150,000,000 to the Vester on 2026-11-23 13:17 UTC (tx
  0x9ab069b3…, `execTransaction` by 0x2b546994…), between the founders' 4M and 48.5M claims. R21 said "the DAO Safe
  signed nothing". It signed the allowance, though not the claims.
- **Jake's dates vs the chain.** "At least a 6-month lock, then 30 months linear, from 25 Dec 2022" would end on
  2025-06-25 at the earliest. On chain the linear vesting runs from 2022-12-24 to 2025-06-11. Either way it is fully
  vested before Morpho's first stored day of the DAO (2025-07-14).
- **The Labs vester** pays contributors' grants (62 awards, starts 2022-12 to 2025-01, ends to 2028-10-02; some
  `restricted`). Czar `0x1590e7F4…`: allowance 67,922,888.08, legacy balance 11,388,690.76. That covers the 7,110,830.23
  vested-unclaimed plus the 3,039,455.91 still to vest. 1,238,404.62 of the balance is promised to no award. 95 claims
  since 2025-07-14 (2,448,809.58), 124,057.39 in the 90 days to 2026-11-20. On 2026-03-16 four awards were moved to new
  beneficiaries (Move events); #58 was yanked on 2025-09-10.
- **The Association vester** repeats MIP1's list (the same beneficiaries: 0xd9e0f068… 55M, 0xb6914e90… 16M,
  0x1590e7F4… 79M…). `allowance(Association, vester) = 0`, `wards(Association) = 0`, no award ever claimed. It was
  superseded by the DAO Vester (deployed 2023-05-04). No token vests through it.
- **Not a MORPHO contract:** the MECT DssVest on Polygon `0x241C23b39A610952b8a8efBdF86463ddbe593e2d` (MIP1's
  precursor; gem = MECT `0xeb1b9579…`, 63 awards; the first four read: bgn 2022-09-10, fin 2025-02-26, nothing claimed).
  MECT is not MORPHO supply.
- **Searched, nothing found:** DAO approvals of legacy and new MORPHO (only the Vester); the top 50 holders of both
  tokens (no vesting contract: the Wrapper, Safes, EOAs, DeFi pools); Blockscout's verified-contract search ("Vest";
  none tied to MORPHO). **No vesting contract was found for the Strategic Partners' cohorts.** Cohort 3's 67M (docs:
  "2 year linear vest, following a 1 year lockup from 21 November 2024") has no contract or wallet on chain, so it
  stays a MODELLED schedule.

## What the build does now (R22)

1. **A claim is not a release**, for every award of the DAO Vester and the Labs vester (`morpho_vest_claims_tokens`;
   R21b's founders-only leg, generalised and renamed). The claim moves vested tokens from the czar's wallet (issuer
   side, off the float) to the beneficiary. It is a named float movement inside the ± issuer line. It is netted only out
   of a release leg that holds the czar's wallet: the DAO's always, the Labs czar's (`morpho_undistributed_tokens`) only
   while that switch is in force.
2. **Vesting on its schedule is the dilution.** `morpho_vesting_unvested_tokens` is each award's DssVest accrual,
   computed from its on-chain `bgn / clf / fin / tot`. It is a release leg: its daily fall enters (e) on the day it vests.
   The DAO Vester vests 0 on every stored day. The Labs vester vests ~6,800 a day (613,973.95 in the 90 days to
   2026-11-20). The float shows it on "+ unlocks" and takes the same amount back on "± issuer" until it is claimed
   (the tokens are still in the czar's Safe).
3. **Circulating, checked.** Both live contracts' vested-unclaimed and unvested tokens sit in a Safe that can move
   them. The DAO and the Grants / Labs Safes are IN circulating and OFF the float (R9.2, `RULE_IN`). That already
   matches the settled rule, so nothing changes there.
4. **The founders' relock tranche is out.** The relock model (`MORPHO_RELOCK`, PR4C.1) kept the founders' 152M out of
   circulating until 2027-11-21 and released it into (e) daily. The founders' tokens are their MIP1 awards, vested on
   chain on 2025-06-11 and claimable: 86.8M was claimed in 2026, 65M of it on 23 Nov, wrapped and held by Paul Frambot's own wallets. Jake (R21b): "circulating once vested". The
   model now holds Cohort 3's 67M only.

## What is left of Morpho's dilution

The 11-20 export (`morpho-2026-11-20-211123-68d6b74.json.gz`), single-project build, Q0 = (2026-08-22, 2026-11-20]:

| Outflow (tokens, Q0) | Before (R21b) | After (R22) |
|---|---:|---:|
| (a) security emissions | 0 (no such flow) | 0 |
| (b) supply-side incentives — the DAO's MORPHO rewards (Merkl / URD claims, all chains) | 2,212,579.07 | 2,212,579.07 |
| (c) demand side | inside (b) | inside (b) |
| (d) ecosystem / grants | inside (e) | inside (e) |
| (e) unlocks | 28,704,594.85 | **10,454,785.38** |
| — the founders' relock (modelled) | 18,739,726.03 | 0 |
| — Cohort 3's relock (modelled) | 8,260,273.97 | 8,260,273.97 |
| — the Labs vester's vesting on schedule | 0 | 613,973.95 |
| — the Labs vester's claims (were releases) | 124,057.39 | 0 |
| — other falls of the not-yet-released set (Rewards / Grants / distributors), net | 1,580,537.45 | 1,580,537.45 |
| (f) named float outflow | 0 | 0 |
| **Total dilution, tokens a year** | 125,487,139 | **51,474,023** |
| Total dilution, % of free float (CP) | 27.2% | **9.6%** |
| Dilution, % of market cap | 14.3% | **5.3%** |
| Circulating / free float | 890.2M / 461.0M | 966.4M / 537.2M |

**It is not mostly DAO emissions.** (b) is 17% of what is left. Cohort 3's modelled relock is 65% of it. Cohort 3 has
no vesting contract on chain. The docs' schedule (67M, linear to 2027-11-21) is the only source, and PR4C.1 / R9.2
(Jake) keep it unvested and out of circulating. If Cohort 3 also left the model, the quarter would be (b) 2.21M + (e)
2.19M. The 1.58M of other falls in the not-yet-released set is not split by wallet in the store, so whether it is DAO
reward funding is NOT established.

## The contracts, award by award (2026-11-25)

"vested" = tot − still unvested; for the Labs vester read from the contract's own `accrued(id)` at the read block.

### The DAO Vester `0x229AD3703b7962a9E1C9B6C4Dbaa1852bf770bfc` (czar the DAO; end 2025-06-11)

| # | beneficiary (usr) | start (bgn) | cliff (clf) | end (fin) | total | vested | claimed | vested, unclaimed | still unvested |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | `0x33e26910…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 4,750,000.00 | 0.00 | 0.00 |
| 2 | `0x33e26910…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 4,750,000.00 | 0.00 | 0.00 |
| 3 | `0x11380556…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 8,000,000.00 | 8,000,000.00 | 8,000,000.00 | 0.00 | 0.00 |
| 4 | `0x4b7b1a1d…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 2,000,000.00 | 2,750,000.00 | 0.00 |
| 5 | `0xa3657f04…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 6,000,000.00 | 6,000,000.00 | 6,000,000.00 | 0.00 | 0.00 |
| 6 | `0xa3657f04…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 1,000,000.00 | 1,000,000.00 | 1,000,000.00 | 0.00 | 0.00 |
| 7 | `0x97f458a8…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 4,750,000.00 | 0.00 | 0.00 |
| 8 | `0x97f458a8…` | 2022-12-24 | 2022-12-24 | 2023-07-23 | 1,114,761.17 | 1,114,761.17 | 1,114,761.17 | 0.00 | 0.00 |
| 9 | `0x2301af62…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 3,000,000.00 | 3,000,000.00 | 600,000.00 | 2,400,000.00 | 0.00 |
| 10 | `0x45b9dc99…` — INFERRED founder | 2022-12-24 | 2022-12-24 | 2025-06-11 | 15,000,000.00 | 15,000,000.00 | 15,000,000.00 | 0.00 | 0.00 |
| 11 | `0x8aa177e6…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 2,500,000.00 | 2,500,000.00 | 2,500,000.00 | 0.00 | 0.00 |
| 12 | `0x6abfd613…` — the Association Master Safe | 2022-12-24 | 2022-12-24 | 2025-06-11 | 500,000.00 | 500,000.00 | 0.00 | 500,000.00 | 0.00 |
| 13 | `0x2fe3f122…` — INFERRED founder | 2022-12-24 | 2022-12-24 | 2025-06-11 | 28,000,000.00 | 28,000,000.00 | 28,000,000.00 | 0.00 | 0.00 |
| 14 | `0xafd6158e…` — INFERRED founder | 2022-12-24 | 2022-12-24 | 2025-06-11 | 30,000,000.00 | 30,000,000.00 | 4,000,000.00 | 26,000,000.00 | 0.00 |
| 15 | `0xafd6158e…` — INFERRED founder | 2022-12-24 | 2022-12-24 | 2025-06-11 | 8,000,000.00 | 8,000,000.00 | 2,000,000.00 | 6,000,000.00 | 0.00 |
| 16 | `0x53051ef9…` — the Contributor Grants Safe 0x53051Ef9… | 2022-12-24 | 2022-12-24 | 2025-06-11 | 79,000,000.00 | 79,000,000.00 | 66,624,770.90 | 12,375,229.10 | 0.00 |
| 17 | `0xd9e0f068…` — founder (Paul Frambot's Safe, ENS paulframbot.eth) | 2022-12-24 | 2022-12-24 | 2025-06-11 | 55,000,000.00 | 55,000,000.00 | 55,000,000.00 | 0.00 | 0.00 |
| 18 | `0xb6914e90…` — founder (Paul Frambot's EOA) | 2022-12-24 | 2022-12-24 | 2025-06-11 | 16,000,000.00 | 16,000,000.00 | 16,000,000.00 | 0.00 | 0.00 |
| 19 | `0xd250ec91…` | 2022-12-24 | 2022-12-24 | 2023-08-02 | 245,774.06 | 245,774.06 | 245,774.06 | 0.00 | 0.00 |
| 20 | `0x57213dba…` | 2022-12-24 | 2022-12-24 | 2023-08-02 | 122,887.03 | 122,887.03 | 122,887.03 | 0.00 | 0.00 |
| 21 | `0xa3657f04…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 4,750,000.00 | 0.00 | 0.00 |
| 22 | `0x8543bbd9…` | 2022-12-24 | 2022-12-24 | 2024-03-07 | 2,317,438.56 | 2,317,438.56 | 2,317,438.56 | 0.00 | 0.00 |
| 23 | `0x9223dc2b…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 331,338.91 | 331,338.91 | 0.00 | 331,338.91 | 0.00 |
| **all** | | | | | **279,882,199.73** | **279,882,199.73** | **229,525,631.72** | **50,356,568.01** | **0.00** |

### The Labs vester `0xe206A8006669A0913D6D13A781580e7E65524407` (czar `0x1590e7F4…`; end 2028-10-02)

| # | beneficiary (usr) | start (bgn) | cliff (clf) | end (fin) | total | vested | claimed | vested, unclaimed | still unvested |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | `0x1cdb2c26…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,526.25 | 4,526.25 | 4,526.25 | 0.00 | 0.00 |
| 2 | `0xd78df836…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 583,518.50 | 583,518.50 | 583,518.50 | 0.00 | 0.00 |
| 3 | `0x14e7a30b…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 35,354.45 | 35,354.45 | 35,354.45 | 0.00 | 0.00 |
| 4 | `0x14e7a30b…` | 2022-12-24 | 2022-12-24 | 2023-01-25 | 28,632.95 | 28,632.95 | 0.00 | 28,632.95 | 0.00 |
| 5 | `0xddd40652…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 391,250.00 | 391,250.00 | 391,250.00 | 0.00 | 0.00 |
| 6 | `0x97f458a8…` | 2023-02-12 | 2023-02-12 | 2023-11-16 | 431,756.17 | 431,756.17 | 431,756.17 | 0.00 | 0.00 |
| 7 | `0x8c839aef…` | 2023-02-12 | 2023-02-12 | 2023-09-13 | 94,798.31 | 94,798.31 | 94,798.31 | 0.00 | 0.00 |
| 8 | `0xa573ed24…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 15,540.00 | 15,540.00 | 0.00 | 15,540.00 | 0.00 |
| 9 | `0xe1e220c7…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 124,200.00 | 124,200.00 | 124,200.00 | 0.00 | 0.00 |
| 10 | `0xe1e220c7…` | 2022-12-24 | 2022-12-24 | 2022-12-27 | 5,353.22 | 5,353.22 | 5,353.22 | 0.00 | 0.00 |
| 11 | `0xc806dcf3…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 85,000.00 | 85,000.00 | 85,000.00 | 0.00 | 0.00 |
| 12 | `0xea302dea…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 73,212.50 | 73,212.50 | 73,212.50 | 0.00 | 0.00 |
| 13 | `0xb50a8067…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 88,967.14 | 88,967.14 | 46,147.25 | 42,819.89 | 0.00 |
| 14 | `0xb50a8067…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 500,000.00 | 500,000.00 | 259,355.53 | 240,644.47 | 0.00 |
| 15 | `0xc76dd9f5…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 1,629,525.00 | 1,629,525.00 | 1,629,525.00 | 0.00 | 0.00 |
| 16 | `0xbaaf2b68…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 5,364.50 | 5,364.50 | 5,364.50 | 0.00 | 0.00 |
| 17 | `0xbaaf2b68…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 1,200,000.00 | 1,200,000.00 | 1,200,000.00 | 0.00 | 0.00 |
| 18 | `0xa90a51b1…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 120,165.00 | 120,165.00 | 99,067.36 | 21,097.64 | 0.00 |
| 19 | `0x9e5f0543…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 583,518.50 | 583,518.50 | 583,518.50 | 0.00 | 0.00 |
| 20 | `0xb21731d8…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 268,466.00 | 268,466.00 | 268,466.00 | 0.00 | 0.00 |
| 21 | `0xb50a8067…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 300,000.00 | 300,000.00 | 155,610.37 | 144,389.63 | 0.00 |
| 22 | `0xcd4c3605…` | 2023-02-12 | 2023-02-12 | 2025-07-31 | 400,000.00 | 400,000.00 | 400,000.00 | 0.00 | 0.00 |
| 23 | `0x8feebfa4…` | 2023-02-12 | 2023-02-12 | 2023-08-31 | 266,936.34 | 266,936.34 | 266,936.34 | 0.00 | 0.00 |
| 24 | `0xe1e220c7…` | 2022-12-27 | 2022-12-27 | 2023-03-27 | 149,577.33 | 149,577.33 | 149,577.33 | 0.00 | 0.00 |
| 25 | `0xacdd96f0…` | 2023-03-20 | 2023-03-20 | 2025-09-05 | 240,000.00 | 240,000.00 | 100,000.00 | 140,000.00 | 0.00 |
| 26 | `0x53b4b510…` | 2023-02-05 | 2023-02-05 | 2025-07-24 | 460,000.00 | 460,000.00 | 75,110.00 | 384,890.00 | 0.00 |
| 27 | `0xc8ab2f79…` | 2023-03-27 | 2023-03-27 | 2024-02-21 | 147,148.14 | 147,148.14 | 147,148.14 | 0.00 | 0.00 |
| 28 | `0xaa8d6d30…` | 2023-03-13 | 2023-03-13 | 2024-03-22 | 166,685.18 | 166,685.18 | 166,685.18 | 0.00 | 0.00 |
| 29 | `0xce621c7b…` | 2023-02-28 | 2023-02-28 | 2023-04-12 | 21,818.19 | 21,818.19 | 21,818.19 | 0.00 | 0.00 |
| 30 | `0xbaaf2b68…` | 2022-12-23 | 2022-12-23 | 2025-06-10 | 120,000.00 | 120,000.00 | 120,000.00 | 0.00 | 0.00 |
| 31 | `0xc39505d6…` | 2023-10-31 | 2023-10-31 | 2026-06-17 | 33,334.00 | 33,334.00 | 0.00 | 33,334.00 | 0.00 |
| 32 | `0x12bd00c7…` | 2023-08-16 | 2023-08-16 | 2026-04-02 | 1,200,000.00 | 1,200,000.00 | 1,200,000.00 | 0.00 | 0.00 |
| 33 | `0x7c87a02d…` | 2023-11-06 | 2023-11-06 | 2024-03-15 | 134,939.24 | 134,939.24 | 0.00 | 134,939.24 | 0.00 |
| 34 | `0xb50a8067…` | 2023-12-01 | 2023-12-01 | 2026-07-18 | 80,000.00 | 80,000.00 | 10,409.55 | 69,590.45 | 0.00 |
| 35 | `0x53b4b510…` | 2023-12-01 | 2023-12-01 | 2026-07-18 | 460,000.00 | 460,000.00 | 0.00 | 460,000.00 | 0.00 |
| 36 | `0xbaaf2b68…` | 2023-12-01 | 2023-12-01 | 2026-07-18 | 280,000.00 | 280,000.00 | 280,000.00 | 0.00 | 0.00 |
| 37 | `0x6b76000f…` | 2023-10-11 | 2023-10-11 | 2023-10-11 | 9,444.00 | 9,444.00 | 0.00 | 9,444.00 | 0.00 |
| 38 | `0xdf5dbc03…` | 2023-12-20 | 2023-12-20 | 2024-09-06 | 21,833.33 | 21,833.33 | 21,833.33 | 0.00 | 0.00 |
| 39 | `0xd5cc8639…` | 2024-03-01 | 2024-03-01 | 2027-10-12 | 666,667.00 | 504,803.28 | 0.00 | 504,803.28 | 161,863.72 |
| 40 | `0x912411bd…` | 2023-11-29 | 2023-11-29 | 2027-11-08 | 480,000.00 | 364,170.00 | 119,881.29 | 244,288.70 | 115,830.00 |
| 41 | `0x2b4fe534…` | 2023-11-13 | 2023-11-13 | 2027-10-23 | 586,667.00 | 451,615.49 | 428,967.70 | 22,647.79 | 135,051.51 |
| 42 | `0x6c85488a…` | 2024-01-01 | 2024-01-01 | 2027-08-13 | 2,666,667.00 | 2,140,424.77 | 200,000.00 | 1,940,424.77 | 526,242.23 |
| 43 | `0x8493f743…` | 2024-05-15 | 2024-05-15 | 2028-01-15 | 586,667.00 | 404,760.88 | 129,482.17 | 275,278.71 | 181,906.12 |
| 44 | `0x2158b20d…` | 2024-03-15 | 2024-03-15 | 2026-06-23 | 865,060.76 | 865,060.76 | 0.00 | 865,060.76 | 0.00 |
| 45 | `0xbb1023d8…` | 2024-07-25 | 2024-07-25 | 2028-03-25 | 440,000.00 | 280,466.41 | 0.00 | 280,466.41 | 159,533.59 |
| 46 | `0x4b1b5f5b…` | 2024-07-25 | 2024-07-25 | 2028-03-25 | 320,000.00 | 203,975.57 | 25,713.45 | 178,262.12 | 116,024.43 |
| 47 | `0xbc4f98d5…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 180,000.00 | 180,000.00 | 180,000.00 | 0.00 | 0.00 |
| 48 | `0x64f91bac…` | 2024-07-01 | 2024-07-01 | 2028-07-01 | 320,000.00 | 192,199.37 | 136,348.04 | 55,851.33 | 127,800.63 |
| 49 | `0xc690bf65…` | 2024-05-20 | 2024-05-20 | 2024-08-01 | 40,000.00 | 40,000.00 | 40,000.00 | 0.00 | 0.00 |
| 50 | `0x98461c40…` | 2024-11-02 | 2024-11-02 | 2028-07-02 | 378,000.00 | 212,875.14 | 0.00 | 212,875.14 | 165,124.86 |
| 51 | `0x28ad7adb…` | 2024-11-29 | 2024-11-29 | 2028-07-29 | 480,000.00 | 260,631.54 | 206,486.54 | 54,144.99 | 219,368.46 |
| 52 | `0xc92531e3…` | 2024-08-15 | 2024-08-15 | 2025-02-15 | 13,095.00 | 13,095.00 | 13,095.00 | 0.00 | 0.00 |
| 53 | `0x6aa7aa13…` | 2025-01-09 | 2025-01-09 | 2028-09-09 | 310,482.00 | 158,953.43 | 133,428.07 | 25,525.36 | 151,528.57 |
| 54 | `0xe1f04609…` | 2025-01-16 | 2025-01-16 | 2028-09-16 | 613,216.00 | 310,734.52 | 309,073.57 | 1,660.89 | 302,481.48 |
| 55 | `0xed14bbd6…` | 2024-11-08 | 2024-11-08 | 2024-11-08 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| 56 | `0xc690bf65…` | 2024-07-31 | 2024-07-31 | 2024-09-29 | 13,200.00 | 13,200.00 | 13,200.00 | 0.00 | 0.00 |
| 57 | `0x51d4a85c…` | 2024-12-04 | 2024-12-04 | 2025-03-11 | 29,408.74 | 29,408.74 | 29,408.74 | 0.00 | 0.00 |
| 58 | `0xde83b1ec…` | 2025-01-01 | 2025-01-01 | 2025-09-11 | 67,765.36 | 67,765.36 | 67,765.36 | 0.00 | 0.00 |
| 59 | `0xacdd96f0…` | 2024-10-02 | 2024-10-02 | 2028-10-02 | 741,000.00 | 397,428.59 | 0.00 | 397,428.59 | 343,571.41 |
| 60 | `0x18e46e77…` | 2025-01-31 | 2025-01-31 | 2028-09-30 | 659,918.00 | 326,789.12 | 0.00 | 326,789.12 | 333,128.88 |
| 61 | `0xc690bf65…` | 2024-10-01 | 2024-10-01 | 2025-01-01 | 4,760.00 | 4,760.00 | 4,760.00 | 0.00 | 0.00 |
| 62 | `0xc690bf65…` | 2025-01-01 | 2025-01-01 | 2025-03-01 | 3,960.00 | 3,960.00 | 3,960.00 | 0.00 | 0.00 |
| **all** | | | | | **21,227,398.12** | **18,187,942.21** | **11,077,111.92** | **7,110,830.23** | **3,039,455.91** |

### The Association vester `0x60167C23eECe006dF7aDB048AeC4b4558957eB5A` (czar the Association; never funded)

Allowance 0: none of the "vested, unclaimed" below can be paid. It is listed for completeness.

| # | beneficiary (usr) | start (bgn) | cliff (clf) | end (fin) | total | vested | claimed | vested, unclaimed | still unvested |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | `0x33e26910…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 0.00 | 4,750,000.00 | 0.00 |
| 2 | `0x33e26910…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 0.00 | 4,750,000.00 | 0.00 |
| 3 | `0x11380556…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 8,000,000.00 | 8,000,000.00 | 0.00 | 8,000,000.00 | 0.00 |
| 4 | `0x4b7b1a1d…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 0.00 | 4,750,000.00 | 0.00 |
| 5 | `0x338ea51f…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 6,000,000.00 | 6,000,000.00 | 0.00 | 6,000,000.00 | 0.00 |
| 6 | `0x05d268e2…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 0.00 | 4,750,000.00 | 0.00 |
| 7 | `0x05d268e2…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 0.00 | 4,750,000.00 | 0.00 |
| 8 | `0x45b9dc99…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 15,000,000.00 | 15,000,000.00 | 0.00 | 15,000,000.00 | 0.00 |
| 9 | `0x8aa177e6…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 2,500,000.00 | 2,500,000.00 | 0.00 | 2,500,000.00 | 0.00 |
| 10 | `0x8aa177e6…` | 2022-12-24 | 2022-12-24 | 2023-06-24 | 505,555.56 | 505,555.56 | 0.00 | 505,555.56 | 0.00 |
| 11 | `0x2fe3f122…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 28,000,000.00 | 28,000,000.00 | 0.00 | 28,000,000.00 | 0.00 |
| 12 | `0xafd6158e…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 30,000,000.00 | 30,000,000.00 | 0.00 | 30,000,000.00 | 0.00 |
| 13 | `0x551fe767…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 8,000,000.00 | 8,000,000.00 | 0.00 | 8,000,000.00 | 0.00 |
| 14 | `0xd9e0f068…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 55,000,000.00 | 55,000,000.00 | 0.00 | 55,000,000.00 | 0.00 |
| 15 | `0xb6914e90…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 16,000,000.00 | 16,000,000.00 | 0.00 | 16,000,000.00 | 0.00 |
| 16 | `0x1164e3ae…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 1,000,000.00 | 1,000,000.00 | 0.00 | 1,000,000.00 | 0.00 |
| 17 | `0x8543bbd9…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 0.00 | 4,750,000.00 | 0.00 |
| 18 | `0x8543bbd9…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 4,750,000.00 | 4,750,000.00 | 0.00 | 4,750,000.00 | 0.00 |
| 19 | `0x21609378…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 1,000,000.00 | 1,000,000.00 | 0.00 | 1,000,000.00 | 0.00 |
| 20 | `0xe360d396…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 3,000,000.00 | 3,000,000.00 | 0.00 | 3,000,000.00 | 0.00 |
| 21 | `0x658bb68a…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 500,000.00 | 500,000.00 | 0.00 | 500,000.00 | 0.00 |
| 22 | `0x1590e7f4…` | 2022-12-24 | 2022-12-24 | 2025-06-11 | 79,000,000.00 | 79,000,000.00 | 0.00 | 79,000,000.00 | 0.00 |
| **all** | | | | | **286,755,555.56** | **286,755,555.56** | **0.00** | **286,755,555.56** | **0.00** |

## Sources (read 2026-11-25 unless dated)

- On chain: the three contracts' state at block 26,492,109; the DAO Vester's `Vest` events, blocks 22,600,000–26,492,097
  (scratchpad/r21_morpho/vester_vest_since2025.json plus a tail read); the Labs vester's events, blocks 22,600,000–head
  (`events_labs.json`); `allowance` / `balanceOf` on legacy MORPHO; the DAO's `Approval` logs on both tokens
  (eth.blockscout.com `getLogs`); the top 50 holders of both tokens (eth.blockscout.com `/api/v2/tokens/…/holders`).
- docs.morpho.org/learn/governance/morpho-token/ (the distribution and vesting text: Cohorts 1–3, Founders' relock to
  17 May 2028 at the latest, Early Contributors).
- forum.morpho.org/t/94 (MIP1, 2022-08-19): "6 months lock up and 30 months linear vesting, starting at the same block
  for everyone"; 15.8% founding team, 7.9% Morpho Labs, 5.2% researchers and advisors (read 2026-11-24, R21).
- github.com/morpho-org/morpho-transfervest @7364e829 (2022-11-01): `src/Vester.sol` (DssVestTransferrable),
  `script/AdmoVestingCreatorDeployment.s.sol` (ADMO_VESTER 0x60167C23…), `script/MorphoLabsVestingCreatorDeployment.s.sol`
  (MORPHO_LABS_VESTER 0xe206A800…).
- Polygon `0x241C23b3…` (MECT DssVest): `ids()`, `gem()` and the first four awards via polygon-bor-rpc.publicnode.com.
  The rest was rate-limited and not read.

## Could not verify

- Cohort 1 / 2 / 3 holders: no vesting contract or wallet found. Cohort 3 stays modelled.
- The yanked awards' schedules before their yank (a yank overwrites `tot` and `fin`). The model uses the yanked values.
  Every DAO-Vester yank and all but one Labs yank predate the store. The exception is Labs #58, yanked 2025-09-10: before
  that day the model under-states its vesting by at most its 67,765 total.
- Who the Labs vester's 62 beneficiaries are (not labelled); whether `0x1590e7F4…` is Morpho Labs (seatbelt) or a
  Contributor Grants Safe (the filing): both sources name it.
- The founders' relock is an off-chain agreement. Whether it binds tokens the contract has vested cannot be read on chain.
