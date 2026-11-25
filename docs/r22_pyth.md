# Round 22 — Pyth (AAVEPYTH lane, branch `claude/aave-pyth`)

Jake 2026-11-25: "PYTH (Solana). A3, held buyback. Not A2: publisher emissions ended." Every on-chain figure below was
read on 2026-11-25 from the keyless public RPC `https://api.mainnet-beta.solana.com` (slot ~473,739,500), User-Agent
`token-metrics-check/1.0`. Earlier research: `research/pyth/dossier.md` (27 Oct 2026). Pyth was a STORE-ONLY project
(SPEC D7-1); it is now a project (config.PROJECTS), read by `fetch/pyth_chain.py` (tier `pyth_chain`).

## 1. Addresses — each checked on chain (account owner, mint, balance)

| Role | Address | On chain 2026-11-25 |
|---|---|---|
| PYTH mint | HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3 | supply 9,999,958,541.892647; no mint / freeze authority |
| Staking program | pytS9TjG1qyAZypk7n8rw8gfW9sUaqqYyMhJQ4E7JCQ | 262,648 PositionData accounts; voting target locked 1,114,802,334.70 |
| OIS program | pyti8TM4zRVBjmarcgAPmTNNAXYKJv7WVHrkrm6woLN | pool delegated 634,523,444.33 + self 35,355,187.56 = 669,878,631.89; y = 0 |
| DAO Treasury (owner) | Gx4MBPb1vqZLJajZmsKLg8fGw9ErhoKsR8LeKcCKFyak | PYTH account AYBdhJU8Wao48SEDyKVRXgDRbo9SU8Thnytakof4HDDH: 51,523,842.59 |
| Council Ops multisig vault | GAdn7TZhszf5KTfwNRx3A2nP6KCRFEWucZubgdEqbJA2 | PYTH account BTy44YBx9AVLHbKkSVLGaCBNKUpzw78W6hKy43vpeCHp: 10,785,571.86; USDC 0; no open DCA |
| Community Council vault | GKuPcXtNRJwZGrJ8tV25jSbLHZe71BUdUXVzjGLXkSt9 | 5,350,342.56 |
| Douro Labs paying wallet | 2ru31e9g8RF2mSSNgTQ11QMb166NE6LJccmBqGJM8xxy | 16,019,810.14 PYTH |
| PPR distribution wallet | GiipfVdYT8yH4ZDZGkVyWgWrjKkLcEgpztSkFf865hJz | PYTH account 8kE8RK1G2jwkZMPMxL7spGh9RPzUchDyejdvvBgr2Dee: 11.03 (pass-through) |
| Funders of Douro's wallet (INFERRED) | CeWsBRksDcuVd9quoNZNT2Hvr9ei7uxEqGBoWwr5JRhu, E6nAbv4qnxRi4X4uBJuPZjFJJ9VNdsYr7R5KZ6eDHtge | 34,416,632.00 and 23,393,581.00 |

Every token account is stored only when its mint AND owner are the declared ones (address poisoning is active on these
wallets — research/pyth/dossier.md §5.1).

## 2. Supply and circulating (Round 22 rule: locked tokens out)

- Genesis lockup: 379 `PeriodicVestingAfterListing` accounts (staking program), initial balances 8,618,333,336, four
  equal steps at list time 2023-05-20 14:00 UTC + 1/2/3/4 × 365 days. Unvested on 2026-11-25: **2,154,583,334.00 =
  the last tranche, 19 May 2027 14:00 UTC** (confirmed on chain; the docs' schedule implies 2,125,000,000 — 29,583,334
  more on chain, unexplained by any primary source). The 19 May 2026 step was the same 2,154,583,334 (the brief's
  "about 2.13B").
- Other periodic vesting locks (PPR / grant locks, tag `PeriodicVesting`): 187,646,221.90 unvested in 896 accounts
  (3 malformed excluded), releasing through ~Oct 2027.
- Ours: 9,999,958,541.89 − 2,342,229,555.90 = **7,657,728,985.99**.
- CoinGecko 7,874,958,541.89; CoinMarketCap 7,874,958,541.89 (both = supply − 2,125,000,000).
- Gap **217,229,555.90 (2.8%)**: the other periodic locks 187,646,221.90 (896 vesting accounts of the staking
  program, PPR 75%-locked payouts and grants) + the genesis lockup's 29,583,334.00 above the docs' figure.
- Off the float: staked inside circulating (§3), DAO Treasury + Community Council (56,874,185.15), Douro + the PPR
  wallet (16,019,821.17), the two INFERRED funders (57,810,213.00), held buybacks (Council vault 10,785,571.86).

## 3. Staking (off float) — measured without double counting

Governance and OIS positions can hold the SAME tokens ("eligible $PYTH tokens can be used for both purposes", OIS docs),
and locked (unvested) tokens can be staked. The reader decodes every PositionData account (state at the current epoch,
`epoch = unix time ÷ 604,800`) and takes, per stake account, max(voting, OIS) less the account's own unvested balance
(its StakeAccountMetadataV2 is the PDA ["stake_metadata", positions]).

Read 2026-11-25 (epoch 2968): Σ LOCKED voting positions **1,114,802,334.696795 = the voting target's locked** to the
micro-PYTH; Σ LOCKED OIS positions **669,878,631.892217 = the OIS pool's delegated + self**. Union (LOCKING / LOCKED /
UNLOCKING) 1,263,417,473.28; unvested inside staked accounts 736,299,950.27 (84 accounts with vesting); **staked inside
circulating = 527,117,523.00** (locked_tokens). Adding governance + OIS naively would have taken 1,785M off a float in
which 736.3M is not even circulating (still vesting).

OIS: rewards ended (y = 0 on chain; "The OIS reward pool depleted on April 22, 2026", forum t/2479; OP-PIP-103 set the
end); 84,834.19 PYTH unclaimed sits in reward custody FbSivnCf…. CO-PIP-139 (7 Oct 2026) makes OIS "legacy,
unstake-only".

## 4. The buyback (A3, held)

- **OP-PIP-136 text** (https://forum.pyth.network/t/passed-op-pip-136-pyth-strategic-reserve-v2/2711, posted 24 Sep 2026,
  marked PASSED; read 2026-11-25): "Eligible revenue may include the DAO's approved share of: Pyth Pro revenue; Listing
  as a Service revenue; Pyth Data Marketplace revenue; Pyth Indices revenue; Pyth Core fees; Pyth Entropy fees; and
  other DAO-owned assets once properly repatriated." It replaces "the recurring one-third-of-Treasury purchase
  mechanism"; the Council and Ops multisig "act only as mechanical execution agents" and "may not sell accumulated PYTH,
  borrow against it, use leverage or derivatives"; "return all purchased PYTH to the official DAO Treasury". The page
  states no percentage; the idea thread (t/2696, 9 Sep 2026) says "The DAO would accumulate 100% of the applicable DAO
  share … This does not mean 100% of gross revenue." No effective date is written; the first V2 transfer reached the
  Council on 2026-09-30 13:08 UTC (323,428.56 USDC, dossier §1.3).
- **On chain** (fetch/pyth_chain.py scans every transaction of the Council's PYTH account BTy44…): 7,109 signatures
  2025-12-05 .. 2026-11-25. A rise in a transaction that invokes Jupiter DCA / aggregator / limit orders is a buy; a fall
  into the Treasury's PYTH account is a return (held). Sampled 40 transactions: 35 DCA fills, the June return
  (1,803,872.741588 on 2026-07-06 — **equal to the June report's figure**), the rest no PYTH change.
- **Reconciliation to the monthly "PYTH Purchases Report" posts**: config.PYTH_CHAIN["reported_returns"] holds the ten V1
  returns (Dec 2025–Sep 2026, 20,549,288.42 PYTH; dossier §1.2 from the reports); `fetch.pyth_chain.reconcile_returns`
  matches each to the scanned day it landed. The full scan (~70 min on the public RPC, resumable) is the store run's job:
  see the report's commands. The forum reports' own tx signatures are only in the dossier as prefixes — matching is by
  amount and month, not by signature.
- Held: bought PYTH returns to the Treasury and stays there (spent only by OP-PIP votes). No burn.

## 5. Revenue

DefiLlama: `pyth-network` answers HTTP 400 on /summary/fees; the slug is the parent **`pyth`** (children pyth-pro,
pyth-entropy, pyth-core, pyth-express-relay). Read 2026-11-25: fees all time $5.76M (30 d $1.04M), revenue all time
$3.61M (30 d $0.62M). **Pyth Pro is covered** (child `pyth-pro`, from 2025-12-02) — but the adapter
(dimension-adapters fees/pyth-pro/index.ts @ dfbf631) counts Douro's USDC / PYTH transfers to the DAO (to the Council
after "PIP_136 = 2026-09-25") and grosses them up at 60/40 for everything, so LaaS (90/10), the Marketplace and Indices
are inside it at the Pro split. Cross-check against the Treasury's USDC / SOL inflows: NOT done this round (a gap).

## 6. Research answers

- **How publishers are paid now:** Publisher Rewards (PPR) from the 22% Publisher Rewards allocation, paid monthly by
  Douro Labs' paying wallet through the distribution wallet GiipfV… — 25% spot, 75% locked one year in new staking-program
  vesting accounts (CO-PIP-137, 24 Sep 2026, https://forum.pyth.network/t/passed-co-pip-137-ppr-vesting-schedule-correction/2713;
  measured 5.63M on 11 Sep 2026, dossier §1.7). No primary document states a monthly amount. OIS rewards are 0. So
  "publisher emissions ended" holds for OIS only; PPR continues, from already-minted supply (issuer side → float), which
  is why the "not A2" tag is a decision for Jake rather than a fact.
- **The May 2027 tranche:** 2,154,583,334.00 PYTH at 2027-05-19 14:00 UTC (on chain, §2).
- **Pyth Core / Pythnet sunset (OP-PIP-100, OP-PIP-128):** not re-read this round; dossier §1.5 quotes the docs
  ("That blockchain has shut down as part of the Pyth Core upgrade") and CO-PIP-139.
