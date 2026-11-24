# Round 21 — who controls the two wallets that got 65M MORPHO on 23 Nov

Read 2026-11-24 (Ethereum block ~26,486,189). Research only: no code, config or test changed.
Sources: `eth_call` / receipts / logs on rpc.mevblocker.io (ethereum-rpc.publicnode.com as fallback);
eth.blockscout.com API v2 and its etherscan-style API; hub.snapshot.org GraphQL; forum.morpho.org JSON; docs.morpho.org.
Scripts and raw reads: `scratchpad/r21_morpho/`.

## Answer

**Neither the DAO nor a third party. Both wallets belong to Paul Frambot, Morpho Labs' co-founder and CEO.**
The DAO sent nothing on 23 Nov. The two wallets **claimed their own founder vesting** from Morpho's
vesting contract, the "Vester" (`0x229AD370…0bfc`). That contract pulls legacy MORPHO out of the DAO Safe
under an allowance. The vesting finished on **2025-06-11**. The tokens became claimable then, not on 23 Nov.

So this is not Morpho's treasury moving funds (it is not an issuer wallet). It is also not a new unlock.
The founders' 15.2% is already in the build: `MORPHO_RELOCK` models it as locked and releases it daily.
Before the claim, these tokens were subtracted **twice**: once as part of the DAO's legacy balance and
once by the relock model. The claim removed one of the two. Counting the 65M as dilution counts the
founders' unlock a second time.

| | 0xd9e0f068…dd91 | 0xb6914e90…13ed |
|---|---|---|
| What it is | Gnosis Safe L2 v1.3.0 (masterCopy `0x3E5c6364…D36E`), **2-of-2** | EOA, nonce 21 |
| Owners | `0x3dcb1951bb87df11b3ffcd3a1fdbc9c9041cf3df`, `0x73a5de3d503f2fb537b58845a570812198f307eb` | — |
| Owners shared with a Morpho Safe | **None** (compared with the DAO, Association Master and Ops, Rewards, Contributor Grants 1 and 2, Operative 1 and 2) | — |
| Created / first funded | 2022-03-06 by 0x3dcb1951… (0.015 ETH) | 2022-09-10 (0.099 ETH from 0xdfd5293d…) |
| Link to the founder | **Its ENS reverse record is `paulframbot.eth`**, set by its own Safe tx 0x335efc5e… (2022-03-07) | Renewed `paulframbot.eth` (tx 0xbb83ad35…, 2023-05-23). Its vesting award was **moved to `paulframbot.eth`'s address, claimed 1M there, and moved back 11 minutes later** (2024-04-02, txs 0xa8f47403…, 0x1b7081b9…, 0xc4d81499…). Delegates its Snapshot vote to `paulframbot.eth` |
| `paulframbot.eth` | resolves to `0x2286f24f5662d3a0e4806a0262b69da0db1ee51d`; text records: twitter "PaulFrambot", description "Co-founder & CEO @MorphoLabs" | same |
| Vester award | **#17**: 55,000,000 legacy MORPHO, restricted, created 2023-05-12 | **#18**: 16,000,000, restricted, same tx |
| Opted in to the founders' MECT→MORPHO conversion (MIP1) | yes, called the Association's opt-in contract 0x6327d36f… on 2022-09-10 | yes, same contract, same day |
| Claimed 23 Nov | 0.01 + 4,000,000.00 + 48,499,999.99 = 52,500,000.00 | 12,500,000.00 |
| Holds now | 55,449,999.00 new MORPHO (all wrapped) | 13,000,000.00 new MORPHO |

The two wallets also act together. Both called `restrict()` on their awards on 2024-03-31, three minutes
apart. Both claimed 2.5M on 2023-12-05, ten minutes apart. Both claimed on 23 Nov 2026.

## How the 23 Nov transfers happened (primary: tx receipts)

| UTC | Tx | Sent by | Call | Paid |
|---|---|---|---|---|
| 09:10:35 | 0x3e94b955…ff62b | EOA 0xb6914e90… | `Vester.vest(18, 1)` | 1 wei (a test) |
| 09:11:59 | 0x2e2f3f56…c6e4 | EOA 0xb6914e90… | `Vester.vest(18, 12.5M)` | 12,500,000.00 |
| 09:30:35 | 0x3341de34…a22a | Safe owner 0x3dcb1951…, via the Safe's `execTransaction` | `Vester.vest(17, 0.01)` | 0.01 |
| 09:41:11 | 0x686b5fe0…be5c | Safe owner 0x73a5de3d… | `Vester.vest(17, 4M)` | 4,000,000.00 |
| 15:37:11 | 0xe1054052…f3bb | Safe owner 0x73a5de3d… | `Vester.vest(17)` (all that was left) | 48,499,999.99 |

Each receipt shows the legacy-token Transfer from the DAO `0xcBa28b38…9AFa` and the Vester's `Vest(id, amt)` event.
**The DAO Safe signed nothing.** None of the five txs is sent to the DAO or carries a DAO `execTransaction`. Within minutes each wallet wrapped the
legacy tokens into new MORPHO through `0x9d03bb20…5123` (`depositFor`).

There is no governance approval to find because none was needed. Snapshot `morpho.eth` has no proposal
since MIP 123 (2025-11-27) about vesting, founders or a 65M transfer. The November proposals are MIP 135–137
(Midnight LLTVs and adapter). Forum searches for "vester", "frambot", "65M" and "relock" find nothing from 2026.

## The Vester (primary: verified source + on-chain state)

- `0x229AD3703b7962a9E1C9B6C4Dbaa1852bf770bfc`, Blockscout-verified as `Vester is DssVestTransferrable`.
  `czar` (who pays) = the DAO `0xcBa28b38…`. `gem` = legacy MORPHO `0x9994E35D…0999`. Deployed 2023-05-04.
- `wards(DAO) = 1`. The DAO is the admin and could revoke unblessed awards. #17 and #18 are fully vested
  (`fin` 2025-06-11), so nothing is left to revoke. The DAO could still cut its token allowance to the Vester,
  which is now 101,500,000.01.
- `mgr` of every award = the Association Master Safe `0x6ABfd613…`.
- The vesting rule matches **MIP1** (forum.morpho.org/t/94, 2022-08-19): the DAO deploys a DSSVest for early
  core contributors, "15.8% for the founding team, 7.9% for Morpho Labs, 5.2% for independent researchers &
  advisors", with a 6-month lockup then 30 months linear. That is `bgn` 2022-12-24 to `fin` 2025-06-11.
- 23 awards, 279,882,199.73 in total, 229,525,631.72 claimed. **50,356,568.01 is vested, unclaimed, and still
  sitting in the DAO's legacy balance.** That includes 32,000,000 on awards #14 and #15 (`0xafd6158e…`),
  12,375,229.10 on #16 (the Contributor Grants Safe `0x53051Ef9…`, already in our set), and 500,000 on #12
  (the Association).
- **INFERRED:** the six restricted awards #10, #13, #14, #15, #17 and #18 add up to **exactly 152,000,000**.
  That is the founders' 15.2% in Morpho's docs. They are probably the founders' awards. Only #17 and #18
  are tied to a named person (above).

Other founder-award claims in the last 90 days pulled the DAO's balance down in the same way.
These **INFERRED** founder claims are:

| Date | Award | Claimed | Tx |
|---|---|---|---|
| 2026-04-28 | #15 | 1,500,000.00 | 0xec326d03… |
| 2026-11-06 | #10 | 3,264,486.30 | 0xebd0c2d0… |
| 2026-11-11 | #13 | 17,015,150.98 | 0x76666ed9… |
| 2026-11-23 | #17, #18 | 65,000,000.00 | above |

## Effect on Morpho's total dilution

R20 baseline (the 20 Nov export): float 461.0M, numerator 207.7M = **45.06%**. The build annualises the fall in
`noncirculating_holding_tokens` × 365/90, so the 65M becomes 263.6M a year.

| Treatment | Numerator (tokens/yr) | Float | Total dilution |
|---|---|---|---|
| A. Third party: a real unlock (the build as it stands) | 207.7 + 263.6 = 471.3M | 461.0 + 65.0 = 526.0M | **89.6%** |
| B. Morpho-controlled: issuer set, off the float | 207.7M (the DAO's fall is offset by the wallets' rise) | 461.0M | **45.1%** |
| C. A founder's vested tokens, already covered by the relock model (the finding) | 207.7M (the claim is not a release) | 526.0M (in circulating once, minus the modelled relock) | **39.5%** |
| C, also removing the 6 and 11 Nov founder claims if they sit in the 20 Nov window (**INFERRED**, check the window) | 207.7 − 82.2 = 125.4M | 526.0M | **23.8%** |

Under C, the founders' real release is the relock model's daily linear vest. That model left 75.6M of the
founders' 152M (and 33.3M of Cohort 3's 67M) locked on 2026-11-23, and that leg is already in the numerator.
B gives the right number for the wrong reason: the wallets are not the issuer's.

## Decision for Jake

**Options**
- **A. Keep it as an unlock (89.6%).** This is wrong. It counts the founders' release twice: once by the
  relock model and again when they claim.
- **B. Add the wallets to the issuer set (45.1%).** Not supported. They are a founder's personal wallets with
  no Morpho signer.
- **C. Founder Vester claims are not releases (39.5%; 23.8% if the November claims are in the window).**
  Supported by on-chain primary evidence.

**Recommendation: C.** Treat founders' Vester awards as founders' tokens (the relock model), not as DAO
treasury. A claim is then a change of location, not a release.

Config entries for the lead (none is applied):

- **C (recommended).** This needs a new entry plus a small reader or build change. No existing structure
  excludes a single fall: `UNLOCK_LEGACY_CUT` cuts a whole leg, and `ONE_OFF_DILUTION_NOTES` keeps the release.
  ```python
  MORPHO_FOUNDER_VESTER = {
      "address": "0x229AD3703b7962a9E1C9B6C4Dbaa1852bf770bfc", "chain": "ethereum", "gem": "token_legacy",
      "czar": "0xcBa28b38103307Ec8dA98377ffF9816C164f9AFa",
      "award_ids": (10, 13, 14, 15, 17, 18),     # INFERRED founders: sum 152,000,000 = 15.2%; #17/#18 = paulframbot.eth
      "leg": "noncirculating_holding_tokens",
      # the DAO leg is read as the DAO's legacy balance − sum(Vester.unpaid(id) for award_ids), so a claim moves nothing
      # (32,000,000.00 unpaid on 2026-11-24; 117,279,637.28 before 2026-11-06). Founders' locked supply stays MORPHO_RELOCK's.
      "why": "founders' MIP1 DSSVest awards, fully vested 2025-06-11, pulled from the DAO by the holders; the relock "
             "agreement is modelled by MORPHO_RELOCK, so the DAO's copy is a double count (docs/r21_morpho_wallets.md)",
      "decided": "<Jake>"}
  ```
  If a reader is not wanted, use dated steps for the same leg instead: `(("2026-04-28", 1_500_000.00),
  ("2026-11-06", 3_264_486.30), ("2026-11-11", 17_015_150.98), ("2026-11-23", 65_000_000.00))`.
  This restates history: circulating rises by the founders' unpaid amount (117.3M before 6 Nov), and the float
  baseline moves with it. The relock subtraction is unchanged.
  The other unpaid awards (#4, #9, #23: 5.48M to third parties; #16 to Contributor Grants; #12 to the
  Association) raise the same question. They are left for a later decision.
- **B.** Uses existing structures, no code:
  ```python
  _NONCIRC_WALLETS_FIRST_PARTY["Morpho"] += [
      ("founder_safe_17", "0xd9e0f068C5D61C14F758cdaaB8F76e9b5dd0dD91", "ethereum", "MORPHO",
       "https://eth.blockscout.com/address/0x229AD3703b7962a9E1C9B6C4Dbaa1852bf770bfc", "2026-11-24",
       "Vester award #17 holder (Safe 2-of-2, ENS reverse paulframbot.eth)"),
      ("founder_eoa_18", "0xb6914E908536e345939090A4CbCb1501F1aA13eD", "ethereum", "MORPHO",
       "https://eth.blockscout.com/address/0x229AD3703b7962a9E1C9B6C4Dbaa1852bf770bfc", "2026-11-24",
       "Vester award #18 holder (EOA; award moved to/from paulframbot.eth 2024-04-02)")]
  ```
  This must be placed before the `for _name, _rows in _NONCIRC_WALLETS_FIRST_PARTY.items()` loop
  (config.py ~L19433). Both wallets hold 0 legacy MORPHO, so no `_legacy` row is needed.
- **A.** No change. Optionally add a note in the existing structure:
  `ONE_OFF_DILUTION_NOTES["Morpho"] = {"category": "e", "holder": "the DAO 0xcBa28b38…", "component":
  "noncirc_dao_treasury_legacy", "set_metric": "noncirculating_holding_tokens", "what": "founder (paulframbot.eth)
  Vester claims #17/#18, vested 2025-06-11", "rest_of_set": "…", "from": "2026-11-23", "to": "2026-11-23",
  "tokens": 65_000_000, "carriers": ("outflow_e_tokens", "unlock_release_vesting_tokens", "unlock_release_tokens"),
  "decided": "<Jake>"}`.

## Could not verify

- **That the owner keys are Paul Frambot's personally.** ENS reverse records and text records are
  self-asserted by whoever holds the keys. They are strong evidence, not proof. The Safe's owners
  0x3dcb1951… and 0x73a5de3d… have no ENS name and no Blockscout tags. Etherscan labels were not read.
- **That #10, #13, #14 and #15 are founders'.** INFERRED from the exact 152,000,000 sum. No award is labelled.
- **Whether the relock agreement binds these on-chain tokens.** The Vester has no relock: everything was
  claimable from 2025-06-11. The relock is an off-chain agreement. Docs: "all co-founders have agreed to relock
  … 100% is vested by 17 May 2028 at the latest". Whether Paul Frambot's 65M is inside its schedule cannot
  be read on chain.
- **Whether the 6 and 11 Nov claims (20.28M) are inside the 20 Nov export's 90-day window** and so inside the
  207.7M numerator. Not checked against the build.
- **The Vester is not listed on docs.morpho.org.** The addresses and organization pages' server-rendered HTML
  does not contain it, nor either wallet.
- **Who created the awards.** Awards #17 and #18 were created 2023-05-12 (tx 0xafeff34b…) by a 1-of-2 Safe,
  `0x2d543670…45c0`. Its signer 0x264c86db… also signs the DAO and every Morpho Safe. That Safe is not a ward
  today, and its role then was not read.
