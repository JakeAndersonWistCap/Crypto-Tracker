# R23 A2: Chainlink's 33 non-circulating wallets, classified

Research only. **NOT APPLIED**: nothing here touches config, circulating, fixtures or any workbook. Read 2026-11-25 (UTC), Ethereum mainnet, latest block, keyless public RPC (ethereum-rpc.publicnode.com), `supply.chain.link/supply` and Blockscout's account API for the LINK transfer history. User-Agent `token-metrics-check/1.0`, no keys, nothing personal sent. Scripts were run from a scratchpad and are not committed.

## Answer

- **No wallet is class L.** Not one of the 33 is held under a contract that enforces a schedule. The 27 Safes are plain Safe 1.3.0 proxies (singleton `0xd9Db270c…`), threshold 3 of 5, with **no modules, no guard and no fallback handler** (all three read as empty on every one). The 4 legacy multisigs are 3-of-6 MultiSigWallets and the 2 legacy wallets are EOAs. No vesting contract, timelock, delay module or allowance exists on any of them. The "7% of supply per year" schedule is a published policy (`chain.link/circulating-supply`: "Token release schedule is currently 7% of the total supply per year"); on chain it is kept only by the signers.
- **All 33 are class M** under the rule as written: each can transfer at will (3 signers for a Safe, 3 for a legacy multisig, 1 key for an EOA). None earns anything: `getStakerPrincipal` on Chainlink's community pool and operator pool reads 0 for all 33.
- **U: none.** Every wallet's code, signers and balance were read. The one thing that cannot be read is the signers' intent: whether they will keep to the schedule. That is policy, not evidence of a lock, so it does not make a wallet L.

| Class | Wallets | LINK (2026-11-25) |
|---|---|---|
| L, locked by a contract schedule | 0 | 0.00 |
| M, movable company wallet | 33 | 233,150,029.58 |
| U, undetermined | 0 | 0.00 |
| **Total** | 33 | **233,150,029.58** |

The balances equal the repo's stored figure, 233,150,029.58 (R22 block `CIRC`, `docs/r22_circulating.md`), to the cent, and equal `supply.chain.link/supply`'s 33 balances (233,150,029.575; the same 33 addresses, set-compared). Nine Safes hold 233,150,000.01 of it (233,150,000.00); the other 24 hold 29.56 together.

## What circulating would be (NOT APPLIED)

| | LINK |
|---|---|
| totalSupply (constant; nothing minted, nothing burned) | 1,000,000,000.00 |
| Now, as applied (R22 b): total less the 33 wallets | 766,849,970.42 (config shows 766,850,000; `supply.chain.link/supply` serves `circulatingSupply` 766,849,970.42) |
| Alternative: class M added back (L = 0, so the whole 233,150,029.58 returns) | **1,000,000,000.00** (= total; +30.4%) |
| Alternative, if a reader holds L at 0 and U at 0 | same: 1,000,000,000.00 |

Under the rule as written, circulating would be the full 1bn and the 33 wallets would sit in circulating but off the float, as R15 recommended (decision 1 (a)) and R22 did not take. What would have to be true for any LINK to stay OUT: a contract that enforces the release schedule. None exists. If Jake wants the schedule honoured as a published commitment, that is a (b) reading (an unreleased reserve treated like unminted supply), which is the R22 reading in force, and it rests on Chainlink's statement, not on chain evidence. Nothing is changed here.

## Evidence

1. **Code at each address** (`eth_getCode`): 27 addresses hold 171 bytes of code, the Safe proxy. 4 hold 5,705 or 5,806 bytes (MultiSigWallet). 2 hold none (EOAs).
2. **Safe 1.3.0, read on all 27**: `VERSION()` = 1.3.0, `getThreshold()` = 3, `getOwners()` = 5 (no owner set repeats, per R15), storage slot 0 (singleton) = `0xd9db270c1b5e3bd161e8c8503c55ceabee709552` on all 27, `getModulesPaginated` empty, guard slot and fallback-handler slot both zero. A Safe with no module and no guard is bound by nothing but its 3-of-5 signature check.
3. **Legacy multisigs** (`0x98C63b7B…`, `0x75398564…`, `0x5560d001…`, `0xe0362F74…`): `required()` = 3, `getOwners()` = 6. No time-lock.
4. **Earning**: `getStakerPrincipal(wallet)` on the community pool `0xBc10f2E8…DB5e` and the operator pool `0xA1d76A7c…fDc5` is 0 for every one of the 33.
5. **Transfer history** (Blockscout `tokentx`, LINK, every page, per wallet): below.

## Have any wallets moved tokens out since the schedule says they are locked?

There is no published per-quarter schedule to compare against (only "7% a year"), so an off-schedule send cannot be shown or ruled out. What the chain shows:

- **Movements are routine, not breaches.** 26 of the 33 have sent LINK out. Since mid-2023 the sends are four dated releases a year (R15: 21.0M, 18.75M, 11.25M, 19.0M = 70M, 7%). The latest was **2026-10-09**, from the Safes `0x4a87ecE3…`, `0x35a5dc3F…`, `0xe0b66bFc…` (0.6M), `0xa42D0A18…`, `0x0DFfD343…` (1.25M), `0x8d34d66b…`, `0xfB682b0d…` and `0x37398A32…`.
- **Two Safes released part of a lot** on 2026-10-09 and still hold the rest: `0xe0b66bFc…` (29.4M of its 30.0M left) and `0x0DFfD343…` (28.75M of 30.0M left). That is the signers choosing a slice, which only a movable wallet can do.
- **Seven Safes have never sent anything** (`0x5Eab1966…` 3.0M, `0x7594Eb0c…`, `0x9BBb4663…`, `0x8652Fb67…`, `0x5A8e77bC…`, `0x76287e0F…` 30.0M each, `0x157235A3…` 22.0M; 175.0M in all). They were funded on 2022-06-28 and sit untouched; nothing on chain stops them moving.
- Where the sends went is carried from R15/R19 (not re-traced here): mostly via EOA `0x9b1c1752…` to a Binance-tagged hot wallet, the rest to Safe `0xD50f074E…` (staking and payments path). The Binance label is a Blockscout tag, not proven.
- The six legacy wallets paid their genesis lots (350M and 5 x 50M, 2017) out in June 2022 into the 27 Safes and earlier payees. They now hold 29.48 LINK together.

## The 33 wallets

Balance is the 2026-11-25 on-chain `balanceOf`; the repo stores only the sum (`noncirculating_holding_tokens`, 233,150,029.58 for 2026-11-23/25), and R15's per-wallet table (block 26,455,357) agrees with these for every wallet it lists. Label is from the repo's data (config.py `_NONCIRC_WALLETS`, the 2022-blog block, `docs/r15_chainlink_pack.md`). The label does not drive the class. Source for all rows: `supply.chain.link/supply` lists the address; the class rests on the code and getter reads above.

| # | Address | Label (repo data) | Type | LINK | Out history (LINK) | Class |
|---|---|---|---|---|---|---|
| 1 | `0x5Eab1966D5F61E52C22D0279F06f175e36A7181E` | Etherscan label 'Chainlink: Noncirculating Supply 1' | Safe 1.3.0, 3-of-5 | 3,000,000.00 | never sent LINK out | M |
| 2 | `0x9c17f630DBde24eECe8fd248fAA2E51f690FF79B` | Etherscan label 'Chainlink: Noncirculating Supply 2' | Safe 1.3.0, 3-of-5 | 0.00 | 30,000,000.00 LINK out in 14 transfers, last 2026-04-03 | M |
| 3 | `0x7594Eb0ca0a7f313bEFD59AfE9e95c2201a443e4` | Etherscan label 'Chainlink: Noncirculating Supply 3' | Safe 1.3.0, 3-of-5 | 30,000,000.00 | never sent LINK out | M |
| 4 | `0x959815462EeC5fFf387A2e8a6871d94323D371de` | Etherscan label 'Chainlink: Noncirculating Supply 4' | Safe 1.3.0, 3-of-5 | 0.08 | 8,000,000.00 LINK out in 8 transfers, last 2023-12-15 | M |
| 5 | `0x2a6AB3B0C96377bd20AE47E50ae426A8546A4Ae9` | Etherscan label 'Chainlink: Noncirculating Supply 5' | Safe 1.3.0, 3-of-5 | 0.00 | 7,000,000.00 LINK out in 8 transfers, last 2023-03-03 | M |
| 6 | `0xD48133C96C5FE8d41D0cbD598F65bf4548941e27` | Etherscan label 'Chainlink: Noncirculating Supply 6' | Safe 1.3.0, 3-of-5 | 0.00 | 30,000,000.00 LINK out in 14 transfers, last 2025-06-20 | M |
| 7 | `0x157235A3cc6011d9C26A010875c2550246aAbcCA` | Etherscan label 'Chainlink: Noncirculating Supply 7' | Safe 1.3.0, 3-of-5 | 22,000,000.00 | never sent LINK out | M |
| 8 | `0x9BBb46637A1Df7CADec2AFcA19C2920CdDCc8Db8` | Etherscan label 'Chainlink: Noncirculating Supply 8' | Safe 1.3.0, 3-of-5 | 30,000,000.00 | never sent LINK out | M |
| 9 | `0x57Ec4745258e5A4C73d1A82636dc0FE291e3eE9F` | Etherscan label 'Chainlink: Noncirculating Supply 9' | Safe 1.3.0, 3-of-5 | 0.00 | 7,000,000.00 LINK out in 4 transfers, last 2022-11-22 | M |
| 10 | `0x8652Fb672253607c0061677bDCaFb77a324DE081` | Etherscan label 'Chainlink: Noncirculating Supply 10' | Safe 1.3.0, 3-of-5 | 30,000,000.00 | never sent LINK out | M |
| 11 | `0x4a87ecE3eFffCb012fbE491AA028032e07B6F6cF` | Etherscan label 'Chainlink: Noncirculating Supply 11' | Safe 1.3.0, 3-of-5 | 0.00 | 30,000,000.00 LINK out in 24 transfers, last 2026-10-09 | M |
| 12 | `0x3264225f2Fd3bb8D5DC50587EA7506aA8638B966` | Etherscan label 'Chainlink: Noncirculating Supply 12' | Safe 1.3.0, 3-of-5 | 0.00 | 7,000,000.00 LINK out in 4 transfers, last 2022-11-22 | M |
| 13 | `0x35a5dc3FD1210Fe7173aDD3C01144Cf1693B5E45` | Etherscan label 'Chainlink: Noncirculating Supply 13' | Safe 1.3.0, 3-of-5 | 0.00 | 30,000,000.00 LINK out in 12 transfers, last 2026-10-09 | M |
| 14 | `0xEc640A90e9A30072158115B7C0253f2689ee6547` | Etherscan label 'Chainlink: Noncirculating Supply 14' | Safe 1.3.0, 3-of-5 | 0.00 | 7,000,000.00 LINK out in 4 transfers, last 2022-11-22 | M |
| 15 | `0xD321948212663366503E8dCCDE39cc8e71C267c0` | Etherscan label 'Chainlink: Noncirculating Supply 15' | Safe 1.3.0, 3-of-5 | 0.00 | 30,000,000.00 LINK out in 12 transfers, last 2023-12-15 | M |
| 16 | `0xe0b66bFc7344a80152BfeC954942E2926A6FcA80` | Etherscan label 'Chainlink: Noncirculating Supply 16' | Safe 1.3.0, 3-of-5 | 29,400,000.00 | 600,000.00 LINK out in 2 transfers, last 2026-10-09 | M |
| 17 | `0xa42D0A18B834F52e41bEDdEaA2940165db3DA9a3` | Etherscan label 'Chainlink: Noncirculating Supply 17' | Safe 1.3.0, 3-of-5 | 0.00 | 30,000,000.00 LINK out in 18 transfers, last 2026-10-09 | M |
| 18 | `0x5A8e77bC30948cc9A51aE4E042d96e145648BB4C` | Etherscan label 'Chainlink: Noncirculating Supply 18' | Safe 1.3.0, 3-of-5 | 30,000,000.00 | never sent LINK out | M |
| 19 | `0x55b0ba1994d68C2AB0C01C3332eC9473de296137` | Etherscan label 'Chainlink: Noncirculating Supply 19' | Safe 1.3.0, 3-of-5 | 0.00 | 30,000,000.00 LINK out in 14 transfers, last 2024-12-20 | M |
| 20 | `0x76287e0F7b107d1C9f8f01D5aFac314Ea8461a04` | Etherscan label 'Chainlink: Noncirculating Supply 20' | Safe 1.3.0, 3-of-5 | 30,000,000.00 | never sent LINK out | M |
| 21 | `0x0DFfD343C2D3460a7EAD2797a687304Beb394ce0` | Etherscan label 'Chainlink: Noncirculating Supply 21' | Safe 1.3.0, 3-of-5 | 28,750,000.00 | 1,250,000.00 LINK out in 2 transfers, last 2026-10-09 | M |
| 22 | `0x8d34d66bDb2d1d6ACd788A2d73d68e62282332e7` | Etherscan label 'Chainlink: Noncirculating Supply 22' | Safe 1.3.0, 3-of-5 | 0.00 | 500,000.00 LINK out in 2 transfers, last 2026-10-09 | M |
| 23 | `0x276F695b3B2C7f24E7CF5b9d24e416a7f357aDb7` | Etherscan label 'Chainlink: Noncirculating Supply 23' | Safe 1.3.0, 3-of-5 | 0.00 | 7,000,000.00 LINK out in 6 transfers, last 2023-03-03 | M |
| 24 | `0xfB682b0dE4e0093835EA21cfABb5449cA9ac9e5e` | Etherscan label 'Chainlink: Noncirculating Supply 24' | Safe 1.3.0, 3-of-5 | 0.00 | 30,000,000.00 LINK out in 16 transfers, last 2026-10-09 | M |
| 25 | `0xb9b012cad0A7C1b10CbE33a1B3F623b06fAD1c7C` | Chainlink 2022 blog (7M then) | Safe 1.3.0, 3-of-5 | 0.00 | 7,000,000.00 LINK out in 10 transfers, last 2022-08-26 | M |
| 26 | `0xa71bbBd288a4e288CfDC08bb2E70DCd74Da4486D` | Chainlink 2022 blog (7M then) | Safe 1.3.0, 3-of-5 | 0.00 | 7,000,000.00 LINK out in 2 transfers, last 2022-08-26 | M |
| 27 | `0x37398A324d35c942574650B9eD2987BC640BAD76` | Chainlink 2022 blog (0.4M then) | Safe 1.3.0, 3-of-5 | 0.00 | 400,000.00 LINK out in 2 transfers, last 2026-10-09 | M |
| 28 | `0x98C63b7B319dFBDF3d811530F2ab9DfE4983Af9D` | supply page legacy | multisig (MultiSigWallet), 3-of-6 | 27.00 | 350,000,000.00 LINK out in 30 transfers, last 2022-06-29 | M |
| 29 | `0x75398564ce69B7498dA10a11ab06Fd8fF549001c` | supply page legacy | multisig (MultiSigWallet), 3-of-6 | 0.78 | 50,000,000.00 LINK out in 4 transfers, last 2022-06-29 | M |
| 30 | `0x5560d001f977dF5e49eaD7AB0bDD437c4ee3a99E` | supply page legacy | multisig (MultiSigWallet), 3-of-6 | 0.00 | 50,000,000.00 LINK out in 8 transfers, last 2022-06-29 | M |
| 31 | `0xe0362F7445e3203A496f6f8b3d51CBB413B69Be2` | supply page legacy | multisig (MultiSigWallet), 3-of-6 | 1.70 | 50,000,000.00 LINK out in 86 transfers, last 2022-06-28 | M |
| 32 | `0xbe6977E08D4479C0A6777539Ae0e8fa27BE4e9d6` | supply page legacy | EOA | 0.00 | 50,000,000.00 LINK out in 31 transfers, last 2022-06-28 | M |
| 33 | `0xdAD22a85ef8310eF582b70e4051E543F3153e11F` | supply page legacy | EOA | 0.00 | 50,000,000.00 LINK out in 25 transfers, last 2022-06-28 | M |

Class M on every row means: a movable wallet that holds LINK or can receive it, controlled by its signers with nothing on chain between them and a transfer. Under the rule the M rows are IN circulating and off the float. The 24 wallets other than the nine funded Safes together hold 29.56; they are listed because Chainlink's page lists them.

## Not established

- Whether the signers will keep to 7% a year. The on-chain record since 2022 matches a yearly cycle of 70M, but no contract binds it.
- Who the signers are (the Chainlink Foundation sets the schedule by its own 2022 post, but no on-chain or Chainlink source names the Safe owners).
- The exchange leg after `0x9b1c1752…` is carried over from R15/R19, not re-read.

## Sources

- `https://supply.chain.link/supply` (totalSupply 1e27 wei, circulatingSupply 766,849,970.42, 33 balances), `https://chain.link/circulating-supply` (the 7% sentence).
- `https://ethereum-rpc.publicnode.com`: `eth_getCode`, `eth_call` (`balanceOf`, `getThreshold`, `getOwners`, `VERSION`, `required`, `getModulesPaginated`, `getStakerPrincipal`), `eth_getStorageAt` (singleton, guard, fallback handler).
- Blockscout `https://eth.blockscout.com/api?module=account&action=tokentx` for the LINK token, per wallet.
- Repo: `config.py` (R22 block `CIRC`; `_NONCIRC_WALLETS`), `docs/r22_circulating.md`, `docs/r15_chainlink_pack.md`, `docs/r19_chainlink_research.md`, `fixtures/real_chainlink.json`.
