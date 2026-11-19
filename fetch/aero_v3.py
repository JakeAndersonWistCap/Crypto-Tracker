"""
fetch/aero_v3.py — tier 2: Aerodrome AFTER the migration to Aero (launch 2026-10-22 00:00 UTC), read from the new
contracts. Built 2026-10-14 (round F, F1) from the published source, dromos-labs/metadex-public @0058bd9b (the org
aero.xyz names as github.com/Dromos-Labs). No compiled ABI and no Base address is published before launch day, so
every call below is encoded from the signature in that source (CALLS / EVENTS: file and line), and the ADDRESSES come
only from Jake's aero_migration.json (RUNBOOK 11bx) — the reader does nothing until that file is complete
(fetch/validate._migration_switch_missing).

What it stores, under the SAME metric names the legacy reads used (a declared handover per metric at the cutover; the
legacy rows of these metrics are refused from the cutover once the switch is complete — validate.check_migration_cutover):
    gross_issuance_tokens      d(Minter.emissionsMinted)  — AERO minted to emission recipients ("mints on claim":
                               gauge claim -> LeafVoter -> Voter.processRedeem -> Minter.mint, Minter.sol L151-169);
                               the team share is NOT in it (minted to SPLITTER beside the cap)
    issuer_mint_tokens         that delta x teamRate / 1e6  (Minter.sol L162: _splitterShare = amount x teamRate /
                               MAX_PIPS, floor — per mint; summed per day it can differ by < 1 wei a mint)
    emission_rate_current      Minter.emissionRate() x 604,800 / 1e18, AERO a WEEK — the CEILING, not emissions: the rate
                               the emissions CAP accrues at (Minter._accrueEmissionsCap; mint() reverts above the cap).
                               What is minted is gross_issuance_tokens (on claim). 0 before ACTIVATION_TIMESTAMP =
                               epochNext(migrationOpen) (Minter.sol L130 at the DEPLOYED source @8b506b45; @0058bd9b
                               L133 read migrationOpen + 1 week). Read 2026-10-27: 13.489345 AERO/s = 8,158,355.856 a
                               week = 425.4M a year, 20.0% of the 2.127B max — aero.xyz's "annual rate ceiling at
                               launch 20%"; emissionsMinted 2.25M against a cap accrued to 6.68M
    ve_locked_supply_tokens    sAERO.supply()                    (TOKEN held in escrow)
    ve_voting_power_tokens     sAERO.totalVotingPower()          (NOT totalSupply(): on sAERO that is the NFT COUNT,
                               VotingEscrow.sol L583)
    permanent_locked_tokens    sAERO.permanentStakeBalance()
    locked_tokens              Token.balanceOf(sAERO)
    voter_total_weight_tokens  Voter.totalPoint() resolved to the read time exactly as AllocationLogicLibrary._walkPoint
                               (L1295-1316) and _weightOf (L1447): permanent + max(0, bias) after walking the bias down
                               the slope, firing totalSlopeChanges at each Unix-week boundary — includes idle CHAIN0
    voter_rewards_onchain_fees_usd / _bribes_usd / _usd / voter_rewards_unpriced_count / voter_rewards_onchain_apr
                               per COMPLETE epoch (Thursday 00:00 UTC, Unix-week aligned), dated its start: fees =
                               every NotifyFeesAmount the VotingRewardsManagers emitted in the epoch; incentives
                               ("bribes") = each IncentiveStreaming program's rate x its overlap with the epoch (rate is
                               per second x 1e18, IIncentiveStreaming.sol L9) — there is no per-epoch bucket in v3.
                               Priced by DefiLlama's coins API at the epoch's end, as the legacy aero_voter does.
    filing_managed_lock_tokens the Blockworks filing wallets' LOCKED AERO at the supply's block (P2b-1, _filing_locks):
                               legacy managed weights + their sAERO stakes + their Aero Relay principal (PT x
                               totalBacking / PT supply) + pending Relay deposits — circulating subtracts it
NO REBASE: sAERO has none (no RewardsDistributor in V3/src; Minter.mint pays only the recipient and SPLITTER) — the
legacy veAERO rebase keeps its own legacy read (emissions_tokens), which is not replaced.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

import config

from .base import LogEntry, Http, derive_flow_from_cumulative, point, today

log = logging.getLogger("token_metrics.fetch.aero_v3")

SOURCE = "aero_v3"
TIER = 2
REPO = "dromos-labs/metadex-public@0058bd9b1d49b0fb0a6d130dd51886b2c64e3d99"
WEEK = 7 * 86400
WAD = 10 ** 18
# P2a D2 (2026-10-27): what emission_rate_current IS (config metric_labels carries the same text for the workbook)
EMISSION_RATE_LABEL = ("the emissions CAP rate (a ceiling, not AERO emitted): Minter.emissionRate() x 604,800, the "
                       "AERO a week the cap accrues; minted AERO is gross_issuance_tokens")

# name -> (signature, returns, where it is declared at REPO)
CALLS = {
    "emissionsMinted": ("emissionsMinted()", ("uint256",), "V3/src/minter/Minter.sol:82"),
    "emissionRate": ("emissionRate()", ("uint256",), "V3/src/minter/Minter.sol:291"),
    "teamRate": ("teamRate()", ("uint24",), "V3/src/interfaces/minter/IMinter.sol:429"),
    "supply": ("supply()", ("uint128",), "V3/src/core/VotingEscrow.sol:97"),
    "totalVotingPower": ("totalVotingPower()", ("uint256",), "V3/src/core/VotingEscrow.sol:537"),
    "permanentStakeBalance": ("permanentStakeBalance()", ("uint128",), "V3/src/core/VotingEscrow.sol:100"),
    "VOTER": ("VOTER()", ("address",), "V3/src/interfaces/core/IVotingEscrow.sol:411"),
    "totalPoint": ("totalPoint()", ("int128", "int128", "uint48", "uint128"), "V3/src/voter/VoterStorageBase.sol:95"),
    "totalSlopeChanges": ("totalSlopeChanges(uint48)", ("int128",), "V3/src/voter/VoterStorageBase.sol:108"),
    "balanceOf": ("balanceOf(address)", ("uint256",), "V3/src/token/Token.sol:9 (solady ERC20)"),
    "incentiveCount": ("incentiveCount()", ("uint256",), "V3/src/rewards/IncentiveStreaming.sol:24"),
    "incentive": ("incentive(uint256)", ("address", "uint256", "uint256", "uint48", "uint48", "address"),
                  "V3/src/rewards/IncentiveStreaming.sol:94"),
    # G2-D5 (2026-10-18): the supply across the migration
    "totalSupply": ("totalSupply()", ("uint256",), "V3/src/token/Token.sol:9 (solady ERC20)"),
    "MIGRATION": ("MIGRATION()", ("address",), "V3/src/token/Token.sol:17"),
    "VELODROME_MIGRATION": ("VELODROME_MIGRATION()", ("address",), "V3/src/token/Token.sol:20"),
    "V2_TOKEN": ("V2_TOKEN()", ("address",), "V3/src/migration/Migration.sol:32"),
    "V2_ESCROW": ("V2_ESCROW()", ("address",), "V3/src/migration/Migration.sol:38"),
    "MIGRATION_TOKEN_ID": ("MIGRATION_TOKEN_ID()", ("uint256",), "V3/src/migration/Migration.sol:44"),
    "previewConversion": ("previewConversion(uint256)", ("uint256", "uint256", "bool", "bool"),
                          "V3/src/migration/Migration.sol:236"),
    "V3_TOKEN": ("V3_TOKEN()", ("address",), "V3/src/migration/AerodromeMigration.sol:26"),
    "remaining": ("remaining()", ("uint256",), "V3/src/migration/AerodromeMigration.sol:79"),
}
# P2b-1 (2026-10-27): THE FILING WALLETS' LOCKED AERO ACROSS THE MIGRATION (_filing_locks). Transcribed from the
# DEPLOYED source (FILING_REPO, the commit aero_migration.json's addresses come from) and the legacy escrow's
# (LEGACY_REPO); the declaration text of each is in tests/fixtures/aero_v3_filing_abi.json.
# name -> (signature, returns, repo, file, line). RelayToken's totalSupply / balanceOf are the ERC20 calls in CALLS.
FILING_REPO = "Dromos-Labs/metadex-public@8b506b450aaa9d4a9f1a77e264f2d9dd508e9ddf"
LEGACY_REPO = "aerodrome-finance/contracts@1ba30815"
FILING_CALLS = {
    "tokenOfOwnerByIndex": ("tokenOfOwnerByIndex(address,uint256)", ("uint256",), FILING_REPO,
                            "V3/src/core/VotingEscrow.sol", 38),          # OZ ERC721Enumerable
    "staked": ("staked(uint256)", ("uint128", "uint48", "bool"), FILING_REPO, "V3/src/core/VotingEscrow.sol", 523),
    "relayFactories": ("relayFactories()", (), FILING_REPO, "V3/src/relay/RelayFactoryRegistry.sol", 68),
    "allRelaysLength": ("allRelaysLength()", ("uint256",), FILING_REPO, "V3/src/relay/RelayFactory.sol", 228),
    "allRelays": ("allRelays(uint256,uint256)", (), FILING_REPO, "V3/src/relay/RelayFactory.sol", 233),
    "principalToken": ("principalToken()", ("address",), FILING_REPO, "V3/src/relay/RelayBase.sol", 103),
    "totalBacking": ("totalBacking()", ("uint256",), FILING_REPO, "V3/src/relay/RelayBase.sol", 115),
    "depositList": ("depositList()", ("uint40", "uint40", "uint40"), FILING_REPO,
                    "V3/src/interfaces/relay/IRelay.sol", 1068),
    "pendingDeposits": ("pendingDeposits(uint256)", ("address", "uint48", "uint128", "uint128"), FILING_REPO,
                        "V3/src/interfaces/relay/IRelay.sol", 1059),
    "ownerToNFTokenIdList": ("ownerToNFTokenIdList(address,uint256)", ("uint256",), LEGACY_REPO,
                             "contracts/VotingEscrow.sol", 441),
    "idToManaged": ("idToManaged(uint256)", ("uint256",), LEGACY_REPO, "contracts/VotingEscrow.sol", 108),
    "weights": ("weights(uint256,uint256)", ("uint256",), LEGACY_REPO, "contracts/VotingEscrow.sol", 110),
}
# R8 item 2 (2026-11-17): THE SECOND RELAY IMPLEMENTATION HAS NO pendingDeposits(uint256). Jake's 2026-11-16 run:
# "filing_managed_lock_tokens at Base block 58,376,623: execution reverted: Multicall3: call failed". Replayed at that
# block: the RelayFactoryRegistry lists a second factory (0xa5283582951ae0cf72be4bea54da63d199264b78, 9 more Relays);
# its Relays are EIP-1167 proxies of implementation 0x3909f8b833a8beba57cef8c6e713e885b6c359ce, whose dispatch table has
# no selector 0xa7932794 (pendingDeposits(uint256), present in the first implementation 0x3de62ff7...) and has
# 0xb02c43d0 = deposits(uint256) instead, answering the same four words (receiver, queued-at, token id, amount; zero
# past the tail, queued-at 0 once consumed). Two of those Relays held live deposit queues (65 and 180 ids), so every
# pendingDeposits call on them reverted and, with allowFailure false, the whole batch. OBSERVED ON-CHAIN, not transcribed
# from a published source (the fixture of FILING_CALLS is the pinned source's; this getter is kept apart from it).
OBSERVED_CALLS = {
    "deposits": ("deposits(uint256)", ("address", "uint48", "uint128", "uint128"),
                 "Relay implementation 0x3909f8b833a8beba57cef8c6e713e885b6c359ce on Base, selector 0xb02c43d0 "
                 "(read at block 58,376,623, 2026-11-17)"),
}
PENDING_GETTERS = ("pendingDeposits", "deposits")       # tried in this order, per queue entry
MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"
# R8 item 2: THE SUPPLY SERIES (what circulating_supply_onchain is built from), written by the live read and by the
# archive backfill (supply_history). The two aero_* legs are stored only once config.METRICS declares them.
# R13 (Jake 2026-11-19): aero_buyer_liquid_tokens — the Foundation's BUYER wallets' liquid AERO (both tokens) at the
# SAME block: a purchase is off the float from the day it is bought, also while the buyer Safe still holds it. It is
# not an input of supply_once (the buyer wallets are not in the non-circulating set: total and circulating unchanged).
SUPPLY_LEGS = ("total_supply", "noncirculating_holding_tokens", "filing_managed_lock_tokens",
               "aero_issuer_liquid_tokens", "aero_velo_reserve_tokens", "aero_buyer_liquid_tokens")
EPOCH_METRICS = ("voter_rewards_onchain_fees_usd", "voter_rewards_onchain_bribes_usd", "voter_rewards_onchain_usd",
                 "voter_rewards_unpriced_count", "voter_rewards_onchain_apr")
EPOCH_SOURCE = "aero_v3_epoch"   # the tier name of the weekly epoch read (fetch/__init__.py); rows keep SOURCE's prefix
EPOCH_BUDGET_S = 200             # the epoch tier stops ITSELF here (its tier budget is 240 s) with its scan saved
EPOCHS_BACK = 8                  # complete epochs looked at each run, newest first; stored ones cost nothing
LOG_CHUNK, LOG_CHUNK_MIN, LOG_CHUNK_MAX = 5_000, 500, 50_000
LOG_ADDR_MAX = 10                # addresses in one eth_getLogs filter (the public endpoints' limit)
MAX_PENDING_SCAN = 1000          # deposit-queue ids walked per Relay; a longer live queue is a named failure, never 0
# THE LEGACY CONTRACTS the migration must name (config Aerodrome contracts token / ve): AERO v2 and veAERO on Base
LEGACY_TOKEN = "0x940181a94a35a4569e4529a3cdfb74e38fd98631"
LEGACY_ESCROW = "0xebf418fe2512e7e6bd9b87a8f0f294acdc67e6b4"
EVENTS = {
    "NotifyFeesAmount": ("NotifyFeesAmount(address,address,uint256)",
                         "V3/src/interfaces/rewards/IFeeDistribution.sol:29"),
    "VotingRewardsCreated": ("VotingRewardsCreated(address,address)",
                             "V3/src/interfaces/rewards/IVotingRewardsFactory.sol:13"),
}


def _keccak(text: str) -> str:
    from eth_utils import keccak                          # noqa: PLC0415
    return "0x" + keccak(text=text).hex()


def selector(name: str) -> str:
    return _keccak((CALLS.get(name) or FILING_CALLS.get(name) or OBSERVED_CALLS[name])[0])[:10]


def topic0(name: str) -> str:
    return _keccak(EVENTS[name][0])


def decode_words(hexdata: str, types: tuple) -> tuple:
    """Static ABI words -> Python values (uint*, int*, address)."""
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    out = []
    for i, t in enumerate(types):
        w = int(h[64 * i:64 * (i + 1)], 16)
        if t == "address":
            out.append("0x" + f"{w:040x}"[-40:])
        elif t.startswith("int"):
            bits = int(t[3:])
            w &= (1 << bits) - 1
            out.append(w - (1 << bits) if w >> (bits - 1) else w)
        else:
            out.append(w)
    return tuple(out)


def walk_point(bias: int, slope: int, ts: int, permanent: int, now: int, slope_change) -> int:
    """AllocationLogicLibrary._walkPoint + _weightOf (REPO L1295-1316, L1447): the total weight at `now`, in wei."""
    if ts < now:
        cursor, nxt = ts, (ts // WEEK + 1) * WEEK
        while nxt <= now:
            bias = max(bias - slope * (nxt - cursor), 0)
            slope = max(slope - slope_change(nxt), 0)
            cursor, nxt = nxt, nxt + WEEK
        if now > cursor:
            bias = max(bias - slope * (now - cursor), 0)
    return permanent + max(bias, 0)


def epoch_rewards(fee_logs: list, programs: list, start: int, end: int) -> dict:
    """Token amounts (raw) a voter epoch carried: {token: (fees, incentives)} — fees from NotifyFeesAmount logs dated in
    [start, end); incentives as each program's rate (per second x 1e18) x its overlap with the epoch."""
    out: dict = {}
    for lg in fee_logs:
        tok = "0x" + lg["topics"][2][-40:]
        f, i = out.get(tok, (0, 0))
        out[tok] = (f + int(lg["data"], 16), i)
    for tok, _amount, rate, s, e, _creator in programs:
        ov = min(e, end) - max(s, start)
        if ov > 0:
            f, i = out.get(tok.lower(), (0, 0))
            out[tok.lower()] = (f, i + rate * ov // WAD)
    return out


def epoch_issuer_incentives(programs: list, start: int, end: int, issuer: set) -> dict:
    """R9.4: the part of an epoch's incentives CREATED BY AN ISSUER WALLET (IncentiveStreaming.incentive(id).creator in
    `issuer`, lower-case addresses) — {token: raw amount}, each program's rate x its overlap with the epoch, the same
    proration epoch_rewards uses. Foundation-funded incentives are not fees paid by users."""
    out: dict = {}
    for tok, _amount, rate, s, e, creator in programs:
        ov = min(e, end) - max(s, start)
        if ov > 0 and str(creator).lower() in issuer:
            out[tok.lower()] = out.get(tok.lower(), 0) + rate * ov // WAD
    return out


def supply_once(new_total: float, reserve: float, velo_held: float, new_noncirc: float, old_total: float,
                old_noncirc: float, old_held: float, ratio: float) -> dict:
    """EACH AERO ONCE ACROSS THE MIGRATION (round G2, D5; dromos-labs/metadex-public@0058bd9b). New AERO pre-mints the
    whole migration allocation to the migration contract (Token.sol L57-58) — the RESERVE that backs every old AERO not
    yet migrated. Old AERO deposited is HELD (escrow.locked(MIGRATION_TOKEN_ID) via depositLiquid / depositVeNFT,
    Migration.sol L121-123, L156-158), never burned. So the old AERO still out (old − held), converted at `ratio`, is
    counted in place of the reserve that backs it, capped at the reserve: once burnRemaining() burns the reserve
    (AerodromeMigration.sol L44-46) the old term is 0 by itself.
        total       = (new − reserve) + min(ratio × (old − old held), reserve)
        circulating = (new − reserve − Velodrome allocation held − the non-circulating wallets' new AERO)
                      + min(ratio × (old circulating − old held), reserve)
    old circulating = old total − the same wallets' old AERO. Tokens, floats."""
    old_term = min(max(ratio * (old_total - old_held), 0.0), reserve)
    old_circ_term = min(max(ratio * (old_total - old_noncirc - old_held), 0.0), reserve)
    total = (new_total - reserve) + old_term
    circ = (new_total - reserve - velo_held - new_noncirc) + old_circ_term
    return {"total": total, "circulating": circ, "noncirculating": total - circ, "old_term": old_term,
            "old_circ_term": old_circ_term}


def relay_claim(shares: int, backing: int, supply: int) -> int:
    """A Relay holder's AERO (wei): principal shares x totalBacking / principal totalSupply, floored — the price every
    exit settles at (QueueLib.processWithdrawals L439, requireUncoveredHead L508, @8b506b45). PT never moves holder to
    holder and a queued exit's shares stay on the holder's balance until burned (RelayBase L1-20, QueueLib.registerExit),
    so the balance is the whole claim."""
    if not shares:
        return 0
    if not supply:
        raise RuntimeError(f"a Relay principal balance of {shares} wei against a principal totalSupply of 0")
    return shares * backing // supply


def _words_of(hexdata: str) -> list:
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def _address_array(hexdata: str) -> list:
    """An ABI-encoded address[] return (offset, length, items) -> lower-case addresses."""
    w = _words_of(hexdata)
    n = w[1] if len(w) > 1 else 0
    return ["0x" + f"{x:040x}"[-40:] for x in w[2:2 + n]]


def multichain_total(chain_tokens: dict, escrows: list, read_supply, read_balance) -> tuple[float, list, list]:
    """THE UNIFIED AERO ON EVERY LAUNCH CHAIN (Round G6 item 2: Base, Ethereum, OP, Arc, Ink, Robinhood, Arbitrum —
    aero.xyz/aero-upgrade). (sum of the non-Base chains' totalSupply − the bridge escrows' balances, the chains read, the
    chains NOT read with why). Base is counted by supply_once; a token bridged out of Base is held by its escrow there
    (or burned), so each declared escrow balance is subtracted once — never a chain counted twice. A chain with no RPC or
    a failed read is named, never assumed 0."""
    total, read, missing = 0.0, [], []
    for chain, token in sorted(chain_tokens.items()):
        if chain == "base":
            continue
        try:
            total += float(read_supply(chain, token))
            read.append(chain)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{chain}: {str(e)[:80]}")
    for esc in escrows or ():
        try:
            total -= float(read_balance(esc["chain"], chain_tokens[esc["chain"]], esc["address"]))
        except Exception as e:  # noqa: BLE001
            missing.append(f"escrow {esc.get('chain')}:{esc.get('address')}: {str(e)[:80]}")
    return total, read, missing


class EpochBudgetSpent(RuntimeError):
    """The epoch tier's own budget ran out; what it scanned is saved and the next run continues from there."""


def _is_revert(err) -> bool:
    t = str(err).lower()
    return "revert" in t or "call failed" in t


def _slim(lg: dict) -> dict:
    """What a cached log keeps (fetch/logcache): the block, the emitter, the topics and the data."""
    b = lg.get("blockNumber")
    return {"blockNumber": int(b, 16) if isinstance(b, str) else int(b or 0), "address": str(lg.get("address", "")).lower(),
            "topics": list(lg.get("topics") or ()), "data": lg.get("data", "0x")}


class AeroV3:
    """Daily state reads + the supply across the migration (part "state"), and the complete voter epochs not yet stored
    (part "epoch") — TWO TIERS (R8 item 2, fetch/__init__.py aero_v3 / aero_v3_epoch): a tier that times out has its
    stock rows dropped (_drop_stocks), so the weekly epoch scan must never share a tier with the day's supply rows.
    part None runs both in one call (a one-off read; the tests of earlier rounds)."""

    def __init__(self, prior_values: dict | None = None, prior_dates: dict | None = None, rpc=None, prices=None,
                 state: dict | None = None, now: int | None = None, part: str | None = None, stored_long=None,
                 cache=None, clock=time.monotonic, epoch_budget_s: float | None = None, **_ignored):
        self.http = Http(min_interval=0.2)
        self.prior = prior_values or {}
        self.prior_dates = prior_dates or {}
        self._rpc = rpc
        self._prices = prices
        self.state = state
        self.now = now
        self.part = part
        self.stored = stored_long
        self._cache = cache
        self._clock = clock
        self.deadline = None                 # monotonic; set by the epoch tier (EPOCH_BUDGET_S)
        self._ts: dict[int, int] = {}        # block -> timestamp, every header read by this reader
        self._head = None
        self._chunk = LOG_CHUNK
        self._chunk_cap = LOG_CHUNK_MAX
        # R10.A2: the seconds the epoch read may take (the daily tier: EPOCH_BUDGET_S; the one-off backfill: its own)
        self.epoch_budget_s = EPOCH_BUDGET_S if epoch_budget_s is None else float(epoch_budget_s)
        self._refused: dict = {}             # (method, host) -> requests it refused that another endpoint served

    # ---------------------------------------------------------------- transport
    def rpc(self, method: str, params: list):
        if self._rpc is not None:
            return self._rpc(method, params)
        # THE KEYED ENDPOINT FIRST (Round G 2.10): BASE_RPC_URL, then the public list — fetch.chain.rpc_endpoints, as
        # every other Base read (only the host is ever logged)
        from .chain import rpc_endpoints  # noqa: PLC0415
        return self._first_answer(method, rpc_endpoints("base"), lambda url: self.http.post(
            url, json_body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))

    def _first_answer(self, method: str, urls: list, post):
        """The first endpoint's result. R10.A2: an endpoint that TWICE refused a request of this method which another
        endpoint then served (a free tier's 10-block eth_getLogs range, a host answering HTTP 429 / 525 after its
        retries) is not asked again for that method in this run — on Jake's store every log request first waited out
        those refusals. A request NO endpoint serves counts against none (the span is narrowed by the caller)."""
        errs, failed = [], []
        hosts = [(u, u.split("/")[2]) for u in urls]
        live = [(u, h) for u, h in hosts if self._refused.get((method, h), 0) < 2] or hosts
        for url, host in live:
            try:
                r = post(url)
                if "result" in r:
                    for h in failed:
                        self._refused[(method, h)] = self._refused.get((method, h), 0) + 1
                    return r["result"]
                errs.append(f"{host}: {r.get('error')}")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{host}: {str(e)[:100]}")
            failed.append(host)
        raise RuntimeError("; ".join(errs))

    @staticmethod
    def calldata(name: str, *args) -> str:
        return selector(name) + "".join(f"{int(a, 16) if isinstance(a, str) else a:064x}" for a in args)

    def call(self, to: str, name: str, *args, block="latest") -> tuple:
        tag = hex(block) if isinstance(block, int) else block
        return decode_words(self.rpc("eth_call", [{"to": to, "data": self.calldata(name, *args)}, tag]),
                            CALLS[name][1])

    @staticmethod
    def name_call(to: str, name: str, args) -> str:
        """A sub-call as a failure names it: function(arguments) [selector] on address."""
        return f"{name}({', '.join(str(x) for x in args)}) [selector {selector(name)}] on {to}"

    def _aggregate(self, part: list, block: int, allow: bool) -> list:
        from eth_abi import decode, encode                # noqa: PLC0415
        data = _keccak("aggregate3((address,bool,bytes)[])")[:10] + encode(["(address,bool,bytes)[]"], [[
            (to, allow, bytes.fromhex(self.calldata(name, *args)[2:])) for to, name, args in part]]).hex()
        res = self.rpc("eth_call", [{"to": MULTICALL3, "data": data}, hex(block)])
        if not res or res == "0x":
            raise RuntimeError(f"Multicall3 answered nothing at block {block:,}")
        (got,) = decode(["(bool,bytes)[]"], bytes.fromhex(res[2:]))
        if len(got) != len(part):
            raise RuntimeError(f"Multicall3 returned {len(got)} result(s) for {len(part)} call(s)")
        return got

    def multi_try(self, calls: list, block: int, chunk: int = 150) -> list:
        """[(to, name, args)] -> [raw hex return, or None for a sub-call that FAILED], through Multicall3.aggregate3 at
        ONE block. Each batch is asked with allowFailure false first (one call, the usual case); when it reverts it is
        asked again with allowFailure true so the failing sub-call is known by name instead of the whole batch being
        lost behind "Multicall3: call failed" (R8 item 2). A sub-call that answers no data at all (no contract, no such
        function behind a fallback) counts as failed. Never a default: the caller decides what an unread call means."""
        out = []
        for i in range(0, len(calls), chunk):
            part = calls[i:i + chunk]
            try:
                got = self._aggregate(part, block, False)
            except Exception as e:  # noqa: BLE001
                if not _is_revert(e):
                    raise
                try:
                    got = self._aggregate(part, block, True)
                except Exception as e2:  # noqa: BLE001
                    raise RuntimeError(f"a Multicall3 batch of {len(part)} call(s) reverted at block {block:,} "
                                       f"({str(e)[:120]}) and could not be re-read call by call ({str(e2)[:120]})") from e2
            out += [("0x" + ret.hex()) if ok and ret else None for ok, ret in got]
        return out

    def multi(self, calls: list, block: int, chunk: int = 150) -> list:
        """[(to, name, args)] -> [raw hex return] at ONE block; ANY failed sub-call raises, NAMING it (function,
        arguments, selector, address) — a part is never silently missing (P2b-1) and never a zero (R8 item 2)."""
        res = self.multi_try(calls, block, chunk)
        bad = [self.name_call(*c) for c, r in zip(calls, res) if r is None]
        if bad:
            raise RuntimeError(f"{len(bad)} of {len(calls)} Multicall3 sub-call(s) failed at block {block:,}: "
                               + "; ".join(bad[:3]) + (f"; and {len(bad) - 3} more" if len(bad) > 3 else ""))
        return res

    def prices(self, tokens: list, ts: int) -> dict:
        """{token: (price_usd, decimals)} from DefiLlama's coins API at `ts` (base: chain)."""
        if self._prices is not None:
            return self._prices(tokens, ts)
        out = {}
        for i in range(0, len(tokens), 40):
            keys = ",".join(f"base:{t}" for t in tokens[i:i + 40])
            body = self.http.get(f"https://coins.llama.fi/prices/historical/{ts}/{keys}")
            for k, v in ((body or {}).get("coins") or {}).items():
                out[k.split(":", 1)[1].lower()] = (float(v["price"]), int(v.get("decimals") or 18))
        return out

    @property
    def cache(self):
        if self._cache is None:
            from .logcache import LogCache  # noqa: PLC0415
            self._cache = LogCache()
        return self._cache

    # ---------------------------------------------------------------- run
    def run(self, projects: list[dict], window_days, out):
        from .validate import _migration_switch_missing  # noqa: PLC0415
        m = config.AERODROME_MIGRATION
        st = config.aerodrome_migration_state() if self.state is None else self.state
        day = pd.Timestamp(today())
        if day < pd.Timestamp(m["cutover_utc"]) or st.get("launched") is not True:
            return
        src_name = EPOCH_SOURCE if self.part == "epoch" else SOURCE
        missing = _migration_switch_missing(st, day)
        if missing:
            if self.part != "epoch":                       # said once, by the daily tier
                out.unconfigured(SOURCE, m["project"], f"aero_migration.json incomplete: {'; '.join(missing)}", TIER)
            return
        name, a = m["project"], {k: v.lower() for k, v in st["addresses"].items()}
        epoch_ready = bool(st.get("voting_rewards_factory") and st.get("deploy_block"))
        if self.part == "epoch" and epoch_ready and not self._epochs_due(name):
            self._epoch(name, a, st, out)                   # every epoch is stored: marked current, NO chain read
            return
        # EVERY ADDRESS MUST HOLD CODE ON BASE before anything is read from it (Round G 2.10): a well-formed address
        # that is not the deployed contract would answer eth_call with nothing or with another contract's figures
        extra = [(k, st[k]) for k in ("voting_rewards_factory", "relay_factory_registry") if st.get(k)]
        try:
            empty = [k for k, v in [*a.items(), *extra]
                     if str(self.rpc("eth_getCode", [v, "latest"]) or "0x") in ("0x", "0x0")]
        except Exception as e:  # noqa: BLE001
            out.fail(src_name, name, f"aero_migration.json addresses: eth_getCode could not be read — {e}", TIER)
            return
        if empty:
            out.fail(src_name, name, f"aero_migration.json: no contract code on Base at {', '.join(empty)} — nothing "
                                     f"read; check the addresses against Aero's publication", TIER)
            return
        if self.part in (None, "state"):
            try:
                self._state_reads(name, a, out)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"state reads: {e}", TIER)
            try:
                self._supply(name, a, out, st)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"supply across the migration: {e} — total_supply / "
                                       f"noncirculating_holding_tokens NOT written (legacy rows of the day are refused, "
                                       f"so the day reads n/a)", TIER)
        if self.part == "state":
            return
        if epoch_ready:
            if self.part == "epoch":
                self.deadline = self._clock() + self.epoch_budget_s
            try:
                self._epoch(name, a, st, out)
            except Exception as e:  # noqa: BLE001
                out.fail(src_name, name, f"voter epoch: {e}", TIER)
        else:
            out.gap(name, "voter_rewards_onchain_usd", tiers_attempted="2",
                    reason="aero_migration.json has no voting_rewards_factory / deploy_block: the VotingRewardsManagers "
                           "(one per gauge) are found only from VotingRewardsFactory.VotingRewardsCreated logs",
                    suggestion="add both from Aero's published deployment (RUNBOOK 11bx)")

    def _voter_weight(self, a: dict, block="latest", now: int | None = None) -> tuple[str, float]:
        """(Voter address, total weight in AERO) resolved to `now` from Voter.totalPoint() read at `block`."""
        ve = a["voting_escrow"]
        voter = self.call(ve, "VOTER", block=block)[0]
        bias, slope, ts, perm = self.call(voter, "totalPoint", block=block)
        now = now or self.now or int(time.time())
        w = walk_point(bias, slope, ts, perm, now,
                       lambda t: self.call(voter, "totalSlopeChanges", t, block=block)[0]) / WAD
        return voter, w

    def _state_reads(self, name: str, a: dict, out) -> None:
        when = today()
        minted = self.call(a["minter"], "emissionsMinted")[0] / WAD
        team = self.call(a["minter"], "teamRate")[0]
        rate = self.call(a["minter"], "emissionRate")[0]
        src = f"{SOURCE}:minter"
        out.add(point(name, "aero_v3_emissions_minted_cumulative", minted, f"{src}.emissionsMinted", TIER, when),
                SOURCE, name, f"emissionsMinted {minted:,.4f} AERO", TIER)
        flow = derive_flow_from_cumulative(
            minted, self.prior.get((name, "aero_v3_emissions_minted_cumulative")), name, "gross_issuance_tokens",
            config.mark_source(f"{src}.emissionsMinted", "delta"), TIER, when,
            prior_date=self.prior_dates.get((name, "aero_v3_emissions_minted_cumulative")),
            stock_metric="aero_v3_emissions_minted_cumulative", out=out)
        if not flow.empty:
            out.add(flow, SOURCE, name, "gross_issuance_tokens = d(Minter.emissionsMinted)", TIER)
            team_flow = flow.assign(metric="issuer_mint_tokens", value=flow["value"] * team / 1_000_000,
                                    source=config.mark_source(f"{src}.emissionsMinted", "delta")
                                    + f"[x teamRate {team}/1e6, Minter.sol L162]")
            out.add(team_flow, SOURCE, name, f"issuer_mint_tokens = delta x teamRate {team} PIPS", TIER)
        out.add(point(name, "emission_rate_current", rate * WEEK / WAD, f"{src}.emissionRate[x604800]", TIER, when),
                SOURCE, name, f"emission_rate_current = {rate * WEEK / WAD:,.4f} AERO a week (emissionRate {rate} wei/s) "
                              f"— {EMISSION_RATE_LABEL}", TIER)
        ve = a["voting_escrow"]
        for metric, call in (("ve_locked_supply_tokens", "supply"), ("ve_voting_power_tokens", "totalVotingPower"),
                             ("permanent_locked_tokens", "permanentStakeBalance")):
            v = self.call(ve, call)[0] / WAD
            out.add(point(name, metric, v, f"{SOURCE}:sAERO.{call}", TIER, when), SOURCE, name, f"{metric}={v:,.4f}",
                    TIER)
        held = self.call(a["token"], "balanceOf", ve)[0] / WAD
        out.add(point(name, "locked_tokens", held, f"{SOURCE}:token.balanceOf(sAERO)", TIER, when), SOURCE, name,
                f"locked_tokens={held:,.4f}", TIER)
        voter, w = self._voter_weight(a)
        out.add(point(name, "voter_total_weight_tokens", w, f"{SOURCE}:voter.totalPoint[resolved]", TIER, when),
                SOURCE, name, f"voter_total_weight_tokens={w:,.4f}", TIER)
        self.voter, self.weight = voter, w

    # ---------------------------------------------------------------- supply
    def _supply_terms(self, name: str, a: dict, blk: int) -> dict:
        """Every Base term of supply_once read at ONE block `blk` (G2-D5e) — the reads only, nothing written. The
        migration contract is read from the new token (MIGRATION()) and must name the legacy token, the legacy escrow
        and the file's token, hold code, and match an `addresses.migration` in the file when one is given. Any failed
        read raises: no term is ever assumed (a leg that was not read is not stored)."""
        c = lambda to, fn, *args: self.call(to, fn, *args, block=blk)  # noqa: E731
        tok = a["token"]
        mig = c(tok, "MIGRATION")[0].lower()
        velo = c(tok, "VELODROME_MIGRATION")[0].lower()
        if a.get("migration") and a["migration"] != mig:
            raise RuntimeError(f"aero_migration.json addresses.migration {a['migration']} is not the token's "
                               f"MIGRATION() {mig}")
        for k, v in (("migration", mig), ("velodrome_migration", velo)):
            if str(self.rpc("eth_getCode", [v, hex(blk)]) or "0x") in ("0x", "0x0"):
                raise RuntimeError(f"no contract code at the token's {k} {v} at block {blk:,}")
        named = {"V2_TOKEN": c(mig, "V2_TOKEN")[0].lower(), "V2_ESCROW": c(mig, "V2_ESCROW")[0].lower(),
                 "V3_TOKEN": c(mig, "V3_TOKEN")[0].lower()}
        want = {"V2_TOKEN": LEGACY_TOKEN, "V2_ESCROW": LEGACY_ESCROW, "V3_TOKEN": tok}
        bad = [f"{k}() = {named[k]}, expected {want[k]}" for k in want if named[k] != want[k]]
        if bad:
            raise RuntimeError("the migration contract does not name the expected contracts: " + "; ".join(bad))
        tid = c(mig, "MIGRATION_TOKEN_ID")[0]
        basis, converted, _perm, _liquid = c(mig, "previewConversion", tid)
        # THE RATIO FROM THE CONTRACT: previewConversion's converted / basis; with nothing deposited yet (basis 0) the
        # contract's own conversion is the identity (AerodromeMigration.sol L143-145, `return _basis`)
        ratio, ratio_src = ((converted / basis, f"previewConversion({tid}) {converted}/{basis}") if basis else
                            (1.0, "1:1 — nothing deposited yet; AerodromeMigration.sol L143-145 returns the basis"))
        reserve_wei = c(tok, "balanceOf", mig)[0]
        # remaining() is V3_TOKEN.balanceOf(this) (AerodromeMigration.sol L79-80): a different answer at the same block
        # means the contract is not the published source, and nothing is written
        rem_wei = c(mig, "remaining")[0]
        if rem_wei != reserve_wei:
            raise RuntimeError(f"reserve check failed at block {blk:,}: new AERO balanceOf(migration {mig}) = "
                               f"{reserve_wei} wei but remaining() = {rem_wei} wei — not the published contract")
        reserve = reserve_wei / WAD
        held_old = (basis + c(LEGACY_TOKEN, "balanceOf", mig)[0]) / WAD
        new_total = c(tok, "totalSupply")[0] / WAD
        old_total = c(LEGACY_TOKEN, "totalSupply")[0] / WAD
        velo_held = c(tok, "balanceOf", velo)[0] / WAD
        # THE SAME NON-CIRCULATING SET the legacy noncirculating_holding_tokens sums (config contracts noncirc_*)
        wallets = [w["address"].lower() for k, w in (config.PROJECT_BY_NAME[name].get("contracts") or {}).items()
                   if k.startswith("noncirc_") and w.get("chain") == "base"
                   and w.get("metric_override", "noncirculating_holding_tokens") == "noncirculating_holding_tokens"]
        new_nc = sum(c(tok, "balanceOf", w)[0] for w in wallets) / WAD
        old_nc = sum(c(LEGACY_TOKEN, "balanceOf", w)[0] for w in wallets) / WAD
        r = supply_once(new_total, reserve, velo_held, new_nc, old_total, old_nc, held_old, ratio)
        # R13: THE FOUNDATION'S BUYER WALLETS (config foundation_buyback.buyer_wallets) — not in the set above, so a
        # purchase the buyer Safe still held at this block was inside the free float. Their liquid AERO of BOTH tokens
        # (1:1), at the same block as every other term: a wallet that is also in the set above is left out (once).
        buyers = [b.lower() for b in ((config.PROJECT_BY_NAME[name].get("foundation_buyback") or {})
                                      .get("buyer_wallets") or {}) if b.lower() not in wallets]
        buyer_liq = sum(c(t, "balanceOf", w)[0] for w in buyers for t in (tok, LEGACY_TOKEN)) / WAD
        return {"blk": blk, "r": r, "mig": mig, "velo": velo, "ratio": ratio, "ratio_src": ratio_src, "reserve": reserve,
                "held_old": held_old, "new_total": new_total, "old_total": old_total, "velo_held": velo_held,
                "wallets": len(wallets), "new_nc": new_nc, "old_nc": old_nc, "buyers": len(buyers),
                "buyer_liq": buyer_liq, "src": f"{SOURCE}:supply_once[ratio {ratio:g}]"}

    @staticmethod
    def _supply_msg(t: dict) -> str:
        r = t["r"]
        return (f"at Base block {t['blk']:,}: reserve = new AERO balanceOf(migration {t['mig']}) = {t['reserve']:,.4f}, "
                f"read on-chain at that block (remaining() agrees); new {t['new_total']:,.4f} − reserve + "
                f"min({t['ratio']:g} × (old {t['old_total']:,.4f} − held {t['held_old']:,.4f}), reserve) = "
                f"{r['total']:,.4f}; ratio {t['ratio_src']}; circulating {r['circulating']:,.4f} (Velodrome allocation "
                f"held {t['velo_held']:,.4f}, {t['wallets']} wallets new {t['new_nc']:,.4f} / old {t['old_nc']:,.4f})")

    def _supply_frames(self, name: str, t: dict, when, mark: str | None = None) -> list:
        """[(frame, log message)] of one supply reading: total, non-circulating, and the two legs of the non-circulating
        NEW AERO the owner's circulating rule needs (R8 item 2) — the issuer wallets' liquid new AERO (supply_once's
        new_noncirc input) and the unclaimed Velodrome allocation (its velo_held input). The two legs are written only
        once config.METRICS declares them; supply_once's own outputs are unchanged."""
        src = t["src"] if not mark else config.mark_source(t["src"], mark)
        r = t["r"]
        legs = (("aero_issuer_liquid_tokens", t["new_nc"], f"{SOURCE}:token.balanceOf(noncirc wallets)",
                 f"the {t['wallets']} non-circulating wallets' liquid NEW AERO (supply_once new_noncirc)"),
                ("aero_velo_reserve_tokens", t["velo_held"], f"{SOURCE}:token.balanceOf(velodrome_migration)",
                 f"new AERO still held by the token's VELODROME_MIGRATION() {t['velo']} (supply_once velo_held)"))
        if t.get("buyer_liq") is not None:
            legs += (("aero_buyer_liquid_tokens", t["buyer_liq"], f"{SOURCE}:balanceOf(foundation buyer wallets)",
                      f"the {t['buyers']} Foundation buyer wallet(s)' liquid AERO, new + legacy (R13: off the float "
                      f"from the day it is bought; not in supply_once)"),)
        out = [(point(name, "total_supply", r["total"], src, TIER, when), f"total_supply: {self._supply_msg(t)}"),
               (point(name, "noncirculating_holding_tokens", r["noncirculating"], src, TIER, when),
                f"noncirculating_holding_tokens = total − circulating = {r['noncirculating']:,.4f}")]
        for metric, v, leg_src, what in legs:
            if metric in config.METRICS:
                out.append((point(name, metric, v, leg_src if not mark else config.mark_source(leg_src, mark), TIER,
                                  when), f"{metric} at Base block {t['blk']:,} = {v:,.4f}: {what}"))
            else:
                out.append((None, f"{metric} = {v:,.4f} read at Base block {t['blk']:,} but NOT stored: the metric is "
                                  f"not declared in config.METRICS"))
        return out

    def _supply(self, name: str, a: dict, out, st: dict | None = None) -> None:
        """G2-D5: total_supply and noncirculating_holding_tokens with each AERO counted once (supply_once), plus the
        two legs of _supply_frames and the filing wallets' locked AERO — all at ONE block (G2-D5e, Jake 2026-10-18):
        the reserve is new AERO's balanceOf(MIGRATION()) read on-chain at this block, and every other term is read at
        the same block, so the sum is one instant's supply."""
        blk = int(self.rpc("eth_blockNumber", []), 16)
        t = self._supply_terms(name, a, blk)
        r = t["r"]
        when = today()
        st = st or {}
        ct = {k.lower(): v for k, v in (st.get("chain_tokens") or {}).items()}
        if ct:
            extra, read, missing = multichain_total(ct, st.get("bridge_escrows") or [], self._chain_supply,
                                                    self._chain_balance)
            r["total"] += extra
            r["circulating"] += extra
            t["src"] += f"+chains[{','.join(read)}]" + (":PARTIAL" if missing else "")
            out.log.append(LogEntry(SOURCE, name, 0, "ok",
                                    f"total_supply: the unified AERO on {len(read) + 1} chain(s) (base + "
                                    f"{', '.join(read) or 'none'}) adds {extra:,.4f} net of bridge escrows"
                                    + (f"; NOT READ — {'; '.join(missing)} (the total is PARTIAL)" if missing else ""),
                                    TIER))
        for frame, msg in self._supply_frames(name, t, when):
            if frame is None:
                out.log.append(LogEntry(SOURCE, name, 0, "skipped", msg, TIER))
            else:
                out.add(frame, SOURCE, name, msg, TIER)
        # P2b-1: the filing wallets' LOCKED AERO at the SAME block (circulating subtracts it as filing_managed_lock_tokens)
        try:
            self._filing_locks(name, blk, out, st)
        except Exception as e:  # noqa: BLE001 — never a partial or zero figure: the day reads n/a
            out.fail(SOURCE, name, f"filing_managed_lock_tokens at Base block {blk:,}: {str(e)[:600]} — NOT written "
                                   f"(circulating reads n/a that day; unknown, never 0)", TIER)

    def _pending_deposits(self, pend_ids: list, blk: int) -> tuple[list, dict]:
        """The live deposit-queue entries [(relay, id)] -> ([raw return], {getter: entries it read}). Each entry is asked
        through PENDING_GETTERS in order (the first Relay implementation's pendingDeposits(uint256), then the second's
        deposits(uint256) — OBSERVED_CALLS); an entry NO getter answers raises, naming the Relay, the id and every
        selector tried — never a zero amount."""
        res: list = [None] * len(pend_ids)
        used: dict = {}
        for fn in PENDING_GETTERS:
            todo = [k for k, x in enumerate(res) if x is None]
            if not todo:
                break
            got = self.multi_try([(pend_ids[k][0], fn, (pend_ids[k][1],)) for k in todo], blk)
            for k, x in zip(todo, got):
                if x is not None and len(x) >= 2 + 64 * 4:
                    res[k] = x
                    used[fn] = used.get(fn, 0) + 1
        bad = [pend_ids[k] for k, x in enumerate(res) if x is None]
        if bad:
            tried = ", ".join(f"{fn} [selector {selector(fn)}]" for fn in PENDING_GETTERS)
            raise RuntimeError(f"{len(bad)} of {len(pend_ids)} Relay deposit-queue entr(ies) could not be read at block "
                               f"{blk:,} — first: Relay {bad[0][0]} id {bad[0][1]}; tried {tried}; the pending leg is "
                               f"unknown, so no figure is written")
        return res, used

    def _filing_read(self, name: str, blk: int, st: dict) -> tuple[float, str]:
        """P2b-1 (2026-10-27): THE AERO THE BLOCKWORKS FILING WALLETS HOLD LOCKED, across the migration, at Base block
        `blk` (the block _supply reads every other term at) -> (tokens, the log line). Traced at block 53,174,900: the
        wallets withdrew their managed veAERO (weight + locked managed reward, legacy VotingEscrow.withdrawManaged
        L182-204), migrated each veNFT to themselves as a decaying sAERO (AerodromeMigration._settleStake ->
        createStake), then moved those stakes into Aero ProtocolRelays ('Autopilot Internal 001-007') with
        VotingEscrow.rebalanceUnderlying; the Relay owns the sAERO and the wallet holds its principal token (PT). So,
        summed over the filing wallets (config ve_managed_holdings owners):
            legacy  weights(tokenId, idToManaged) on the OLD escrow — managed deposits not yet withdrawn (old AERO,
                    counted inside supply_once's old term)
            direct  sAERO.staked(id).amount for every sAERO the wallet owns (ERC721Enumerable)
            relay   PT.balanceOf(wallet) x totalBacking / PT.totalSupply for every Relay of every factory the
                    RelayFactoryRegistry lists (relay_claim)
            pending live Relay deposit-queue entries naming the wallet (weight moved, PT not yet minted)
        Every read goes through Multicall3 at `blk`; any sub-call that fails raises with its name (multi), so nothing
        is written from a partial read."""
        spec = config.PROJECT_BY_NAME[name].get("ve_managed_holdings") or {}
        labels = {o.lower(): v for o, v in (spec.get("owners") or {}).items()}
        g = self.locked_read(labels, str(spec.get("escrow") or LEGACY_ESCROW).lower(), blk, st)
        by, managed, new_ids, relays, factories = g["by"], g["managed"], g["new_ids"], g["relays"], g["factories"]
        pend_ids, getters, held_in = g["pend_ids"], g["getters"], g["held_in"]
        parts = [sum(v[i] for v in by.values()) / WAD for i in range(4)]
        total = sum(parts)
        return total, (
            f"filing_managed_lock_tokens at Base block {blk:,} = {total:,.4f}: legacy managed veAERO "
            f"{parts[0]:,.4f} ({len(managed)} veNFT(s)) + sAERO stakes {parts[1]:,.4f} ({len(new_ids)} sAERO) + "
            f"Relay principal {parts[2]:,.4f} ({len(relays)} Relay(s) of {len(factories)} factory(ies)) + pending "
            f"Relay deposits {parts[3]:,.4f}"
            + (f" ({len(pend_ids)} queue entr(ies) read: "
               + ", ".join(f"{n} by {fn}" for fn, n in getters.items()) + ")" if pend_ids else "")
            + "; by wallet: " + "; ".join(f"{labels[o]} {sum(v) / WAD:,.2f}" for o, v in by.items() if sum(v))
            + (f"; Relays: {'; '.join(held_in)}" if held_in else ""))

    def locked_read(self, labels: dict, legacy_ve: str, blk: int, st: dict, allow_no_factory: bool = False) -> dict:
        """THE READ BEHIND _filing_read, FOR ANY SET OF WALLETS (R12: the Foundation's buyback wallets read their
        post-migration lock stock through the same code — check_offline_items.aerodrome_foundation_locks_v3). `labels`
        {wallet (lower-case): label}. Returns {"by": {wallet: [legacy managed weights, direct sAERO stakes, Relay
        principal, pending Relay deposits]} (wei), "managed", "new_ids", "relays", "factories", "pend_ids", "getters",
        "held_in", "relay_state": {relay: {"pt", "backing", "supply", "shares": {wallet: PT balance}}}}. Every read
        goes through Multicall3 at `blk`; any sub-call that fails raises with its name — never a partial figure.
        `allow_no_factory`: a registry that ANSWERS an empty factory list is "no Relay exists at this block" (read on
        Base 2026-11-19: relayFactories() = [] at the cutover block 52,918,927 and at 52,950,000, [0xA972232e…] at
        52,962,127) — the Relay legs are then 0 by the registry's own answer. The filing read keeps it an error."""
        owners = list(labels)
        reg = str((st or {}).get("relay_factory_registry") or "").lower()
        ve = str(((st or {}).get("addresses") or {}).get("voting_escrow") or "").lower()
        if not owners or not reg or not ve:
            raise RuntimeError("no filing owners (ve_managed_holdings), no relay_factory_registry or no voting_escrow "
                               "in aero_migration.json — the Relay principal cannot be enumerated")
        W = _words_of
        # round 1: NFT counts on both escrows, the Relay factories
        r1 = self.multi([(ve, "balanceOf", (o,)) for o in owners] + [(legacy_ve, "balanceOf", (o,)) for o in owners]
                        + [(reg, "relayFactories", ())], blk)
        n_new = [W(x)[0] for x in r1[:len(owners)]]
        n_old = [W(x)[0] for x in r1[len(owners):2 * len(owners)]]
        factories = _address_array(r1[-1])
        if not factories and not allow_no_factory:
            raise RuntimeError(f"RelayFactoryRegistry {reg} lists no factory")
        # round 2: token ids, Relay counts
        new_pairs = [(o, i) for o, n in zip(owners, n_new) for i in range(n)]
        old_pairs = [(o, i) for o, n in zip(owners, n_old) for i in range(n)]
        r2 = self.multi([(ve, "tokenOfOwnerByIndex", (o, i)) for o, i in new_pairs]
                        + [(legacy_ve, "ownerToNFTokenIdList", (o, i)) for o, i in old_pairs]
                        + [(f, "allRelaysLength", ()) for f in factories], blk)
        new_ids = [W(x)[0] for x in r2[:len(new_pairs)]]
        old_ids = [W(x)[0] for x in r2[len(new_pairs):len(new_pairs) + len(old_pairs)]]
        n_rel = [W(x)[0] for x in r2[len(new_pairs) + len(old_pairs):]]
        # round 3: stakes, managed links, the Relay lists
        r3 = self.multi([(ve, "staked", (t,)) for t in new_ids] + [(legacy_ve, "idToManaged", (t,)) for t in old_ids]
                        + [(f, "allRelays", (0, n)) for f, n in zip(factories, n_rel)], blk)
        staked = [W(x)[0] for x in r3[:len(new_ids)]]
        mids = [W(x)[0] for x in r3[len(new_ids):len(new_ids) + len(old_ids)]]
        relays = [r for x in r3[len(new_ids) + len(old_ids):] for r in _address_array(x)]
        if len(relays) != sum(n_rel):
            raise RuntimeError(f"the factories list {len(relays)} Relays, allRelaysLength says {sum(n_rel)}")
        # round 4: legacy weights, each Relay's principal token, backing and deposit queue
        managed = [(o, t, m) for (o, _i), t, m in zip(old_pairs, old_ids, mids) if m]
        r4 = self.multi([(legacy_ve, "weights", (t, m)) for _o, t, m in managed]
                        + [(r, fn, ()) for r in relays for fn in ("principalToken", "totalBacking", "depositList")], blk)
        weights = [W(x)[0] for x in r4[:len(managed)]]
        rel = r4[len(managed):]
        pts = ["0x" + f"{W(rel[3 * k])[0]:040x}" for k in range(len(relays))]
        backing = [W(rel[3 * k + 1])[0] for k in range(len(relays))]
        queues = [W(rel[3 * k + 2])[:3] for k in range(len(relays))]
        # round 5: principal supply and the wallets' principal; live deposit-queue entries
        pend_ids = []
        for r, (head, tail, count) in zip(relays, queues):
            if not count:
                continue
            if tail - max(head, 1) + 1 > MAX_PENDING_SCAN:
                raise RuntimeError(f"Relay {r}: deposit queue spans {tail - head + 1} ids (> {MAX_PENDING_SCAN})")
            pend_ids += [(r, i) for i in range(max(head, 1), tail + 1)]
        r5 = self.multi([(pt, "totalSupply", ()) for pt in pts] + [(pt, "balanceOf", (o,)) for pt in pts for o in owners],
                        blk)
        pend, getters = self._pending_deposits(pend_ids, blk)
        sup = [W(x)[0] for x in r5[:len(pts)]]
        bals = r5[len(pts):len(pts) + len(pts) * len(owners)]
        by = {o: [0, 0, 0, 0] for o in owners}                       # legacy, direct, relay, pending (wei)
        for (o, _t, _m), wt in zip(managed, weights):
            by[o][0] += wt
        for (o, _i), amt in zip(new_pairs, staked):
            by[o][1] += amt
        held_in = []
        for k, (r, pt) in enumerate(zip(relays, pts)):
            for j, o in enumerate(owners):
                c = relay_claim(W(bals[k * len(owners) + j])[0], backing[k], sup[k])
                if c:
                    by[o][2] += c
                    held_in.append(f"{r[:10]}… {labels[o]} {c / WAD:,.2f}")
        for (r, _i), x in zip(pend_ids, pend):
            rec, at, _tid, amt = decode_words(x, FILING_CALLS["pendingDeposits"][1])
            if at and rec.lower() in by:
                by[rec.lower()][3] += amt
        relay_state = {r: {"pt": pt, "backing": backing[k], "supply": sup[k],
                           "shares": {o: W(bals[k * len(owners) + j])[0] for j, o in enumerate(owners)}}
                       for k, (r, pt) in enumerate(zip(relays, pts))}
        return {"by": by, "managed": managed, "new_ids": new_ids, "relays": relays, "factories": factories,
                "pend_ids": pend_ids, "getters": getters, "held_in": held_in, "relay_state": relay_state}

    def _filing_locks(self, name: str, blk: int, out, st: dict) -> None:
        total, msg = self._filing_read(name, blk, st)
        out.add(point(name, "filing_managed_lock_tokens", total, f"{SOURCE}:filing_locks", TIER, today()), SOURCE, name,
                msg, TIER)

    def _chain_call(self, chain: str, to: str, data: str) -> int:
        from .chain import rpc_endpoints  # noqa: PLC0415
        urls = rpc_endpoints(chain)
        if not urls:
            raise RuntimeError(f"no RPC endpoint configured for {chain}")
        errs = []
        for url in urls:
            try:
                r = self.http.post(url, json_body={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                                                   "params": [{"to": to, "data": data}, "latest"]})
                if "result" in r:
                    return int(r["result"], 16)
                errs.append(str(r.get("error"))[:80])
            except Exception as e:  # noqa: BLE001
                from .chain import redact_urls  # noqa: PLC0415
                errs.append(redact_urls(e)[:80])
        raise RuntimeError("; ".join(errs))

    def _chain_supply(self, chain: str, token: str) -> float:
        return self._chain_call(chain, token, selector("totalSupply")) / 1e18

    def _chain_balance(self, chain: str, token: str, holder: str) -> float:
        return self._chain_call(chain, token, selector("balanceOf") + holder.lower()[2:].rjust(64, "0")) / 1e18

    # ---------------------------------------------------------------- blocks by timestamp
    def _block_ts(self, n: int) -> int:
        if n not in self._ts:
            blk = self.rpc("eth_getBlockByNumber", [hex(n), False])
            if not blk or "timestamp" not in blk:
                raise RuntimeError(f"Base block {n:,} has no header on this endpoint")
            self._ts[n] = int(blk["timestamp"], 16)
        return self._ts[n]

    def _block_at(self, ts: int) -> int:
        """The FIRST Base block whose timestamp is >= ts — a binary search on the block TIMESTAMPS over the whole chain
        (block 0 to the head), narrowed by every header this reader has already read. R8 item 2: never from a block
        time. The search this replaces looked only 3,456,000 blocks back on a 2 s block time; Base has made 0.2 s
        blocks since about 2026-11-05, so that window was ~8 days and a week-old boundary could fall outside it."""
        if self._head is None:
            self._head = int(self.rpc("eth_blockNumber", []), 16)
        lo, hi = 0, self._head
        if self._block_ts(hi) < ts:
            raise RuntimeError(f"timestamp {ts} is after the head block {hi:,} ({self._block_ts(hi)})")
        for b, t in self._ts.items():
            if t < ts and b + 1 > lo:
                lo = b + 1
            elif t >= ts and b < hi:
                hi = b
        while lo < hi:
            mid = (lo + hi) // 2
            if self._block_ts(mid) >= ts:
                hi = mid
            else:
                lo = mid + 1
        return lo

    # ---------------------------------------------------------------- logs, cached
    def _spent(self) -> bool:
        return self.deadline is not None and self._clock() > self.deadline

    def _logs(self, address, topics, lo: int, hi: int, stream: tuple | None = None) -> list:
        """eth_getLogs over [lo, hi], cached per stream (fetch/logcache.LogCache) so a run asks only for blocks after the
        last one scanned, and saved as it goes so a run cut by the budget continues instead of starting over. The block
        span of a request adapts: it widens while the endpoint answers and narrows when a request is refused."""
        from .logcache import stream_id  # noqa: PLC0415
        if not isinstance(address, str) and len(address) > LOG_ADDR_MAX:
            # R10.A2: public endpoints refuse a log filter of more than 10 addresses (HTTP 403 on Jake's store, every
            # run) — the managers are asked for in groups, each group its own cached stream
            out = []
            for i in range(0, len(address), LOG_ADDR_MAX):
                out += self._logs(list(address[i:i + LOG_ADDR_MAX]), topics, lo, hi)
            return sorted(out, key=lambda lg: (lg["blockNumber"], lg.get("logIndex", 0)))
        sid = stream_id(*(stream or ("base", address if isinstance(address, str) else ",".join(address), topics, lo)))
        cached = self.cache.load(sid)
        events, done = list(cached["events"]), cached["scanned_to"]
        b = lo if done is None else max(lo, int(done) + 1)
        calls = 0
        while b <= hi:
            if self._spent():
                self.cache.save(sid, events, b - 1, b - 1)
                raise EpochBudgetSpent(f"the epoch tier's {self.epoch_budget_s:.0f}s were spent with the log scan at block "
                                       f"{b - 1:,} of {hi:,}; the scan is saved and the next run continues from there")
            e = min(hi, b + self._chunk - 1)
            try:
                got = self.rpc("eth_getLogs", [{"address": address, "topics": topics, "fromBlock": hex(b),
                                                "toBlock": hex(e)}])
            except Exception:  # noqa: BLE001 — a span the endpoint refuses: narrow it, down to LOG_CHUNK_MIN
                if self._chunk <= LOG_CHUNK_MIN:
                    self.cache.save(sid, events, b - 1, b - 1)
                    raise
                self._chunk_cap = max(LOG_CHUNK_MIN, self._chunk // 2)
                self._chunk = max(LOG_CHUNK_MIN, self._chunk // 4)
                continue
            events += [_slim(lg) for lg in got or ()]
            b = e + 1
            calls += 1
            self._chunk = min(self._chunk * 2, self._chunk_cap)
            if calls % 25 == 0:
                self.cache.save(sid, events, b - 1, b - 1)
        if calls or done is None:
            # nothing to reconcile these events against (no balance they must sum to), so what is scanned is "proven"
            self.cache.save(sid, events, max(hi, int(done or 0)), max(hi, int(done or 0)))
        return [lg for lg in events if lo <= lg["blockNumber"] <= hi]

    # ---------------------------------------------------------------- the voter epochs
    def _stored_epochs(self, name: str) -> set:
        """The epoch start dates ('YYYY-MM-DD') whose voter_rewards_onchain_usd row, written by this reader, the store
        already holds. No store handed in (a one-off read) -> none, so the epoch is read."""
        f = self.stored
        if f is None or not len(f):
            return set()
        rows = f[(f["project"] == name) & (f["metric"] == "voter_rewards_onchain_usd")
                 & f["source"].astype(str).str.startswith(SOURCE + ":")]
        return set(pd.to_datetime(rows["date"]).dt.strftime("%Y-%m-%d"))

    def _epochs(self) -> list:
        """[(start, end)] of the last EPOCHS_BACK complete v3 epochs, newest first, none before the cutover."""
        now = self.now or int(time.time())
        cut = int(pd.Timestamp(config.AERODROME_MIGRATION["cutover_utc"]).timestamp())
        last = now // WEEK * WEEK
        return [(e - WEEK, e) for e in (last - k * WEEK for k in range(EPOCHS_BACK)) if e - WEEK >= cut]

    def _epochs_due(self, name: str) -> list:
        have = self._stored_epochs(name)
        return [(s, e) for s, e in self._epochs() if str(pd.Timestamp(s, unit="s").date()) not in have]

    def _programs(self, mgrs: list) -> list:
        """Every IncentiveStreaming program of every manager, in two rounds of Multicall3 batches at one block
        (incentiveCount per manager, then incentive(id) per program) — R8 item 2: this was one eth_call a manager and
        one a program, in sequence. A failed sub-call raises with its name (multi)."""
        if getattr(self, "_progs", None) is None:
            blk = int(self.rpc("eth_blockNumber", []), 16)
            counts = [_words_of(x)[0] for x in self.multi([(mg, "incentiveCount", ()) for mg in mgrs], blk)]
            ids = [(mg, pid) for mg, n in zip(mgrs, counts) for pid in range(1, n + 1)]
            self._progs = [decode_words(x, CALLS["incentive"][1])
                           for x in self.multi([(mg, "incentive", (pid,)) for mg, pid in ids], blk)]
        return self._progs

    def _epoch(self, name: str, a: dict, st: dict, out) -> None:
        """The complete voter epochs the store does not hold yet, newest first. An epoch already stored is skipped
        BEFORE any chain read (R8 item 2: the scan ran again every day of the week for an epoch read on Thursday)."""
        src_name = EPOCH_SOURCE if self.part == "epoch" else SOURCE
        epochs, due = self._epochs(), self._epochs_due(name)
        if not epochs:
            return                                        # no complete v3 epoch yet
        if epochs[0] not in due:
            when = pd.Timestamp(epochs[0][0], unit="s").normalize()
            for metric in EPOCH_METRICS:
                out.mark_current(src_name, name, metric, f"epoch {when.date()} stored already — not scanned again", TIER)
        if not due:
            return
        factory, deploy = st["voting_rewards_factory"], int(st["deploy_block"])
        created = topic0("VotingRewardsCreated")
        newest_end = self._block_at(due[0][1]) - 1
        mgrs = sorted({"0x" + lg["topics"][2][-40:] for lg in self._logs(
            factory, [created], deploy, newest_end, stream=("base", factory, [created], deploy))})
        for k, (start, end) in enumerate(due):
            if self._spent():
                raise EpochBudgetSpent(f"the epoch tier's {self.epoch_budget_s:.0f}s were spent before the epoch of "
                                       f"{pd.Timestamp(start, unit='s').date()}; the next run reads it")
            b0, b1 = self._block_at(start), self._block_at(end) - 1
            fees = self._logs(mgrs, [topic0("NotifyFeesAmount")], b0, b1) if mgrs else []
            progs = self._programs(mgrs) if mgrs else []
            amounts = epoch_rewards(fees, progs, start, end)
            px = self.prices(sorted(set(amounts) | {a["token"]}), end)
            fee_usd = bribe_usd = issuer_usd = 0.0
            unpriced = 0
            own = epoch_issuer_incentives(progs, start, end, set(config.aero_issuer_incentive_creators()))
            for tok, (f, i) in amounts.items():
                if tok not in px:
                    unpriced += 1
                    continue
                p, dec = px[tok]
                fee_usd += f / 10 ** dec * p
                bribe_usd += i / 10 ** dec * p
                issuer_usd += own.get(tok, 0) / 10 ** dec * p
            when = pd.Timestamp(start, unit="s").normalize()
            src = f"{SOURCE}:epoch[{len(mgrs)} managers]"
            for metric, v in (("voter_rewards_onchain_fees_usd", fee_usd),
                              ("voter_rewards_onchain_bribes_usd", bribe_usd),
                              ("voter_rewards_onchain_usd", fee_usd + bribe_usd),
                              # R9.4: the incentives an issuer wallet created — out of FEES / REVENUE / HOLDER REVENUE
                              ("voter_rewards_onchain_bribes_issuer_usd", issuer_usd),
                              ("voter_rewards_unpriced_count", float(unpriced))):
                out.add(point(name, metric, v, src, TIER, when), src_name, name,
                        f"{metric}={v:,.2f} (epoch {when.date()}, Base blocks {b0:,}-{b1:,})", TIER)
            aero_px = px.get(a["token"], (None, 18))[0]
            # the stake the APR is over: the newest epoch against the weight now (as before); an older, missed epoch
            # against Voter.totalPoint at that epoch's last block, resolved to its end
            try:
                w = (getattr(self, "weight", None) or self._voter_weight(a)[1]) if (start, end) == epochs[0] \
                    else self._voter_weight(a, block=b1, now=end)[1]
            except Exception as e:  # noqa: BLE001 — no weight, no APR row (never an APR over a guessed stake)
                w = None
                out.log.append(LogEntry(src_name, name, 0, "failed",
                                        f"voter_rewards_onchain_apr of epoch {when.date()} NOT written: the voter "
                                        f"weight could not be read ({str(e)[:160]})", TIER))
            if aero_px and w:
                apr = (fee_usd + bribe_usd) * 52 / (w * aero_px)
                out.add(point(name, "voter_rewards_onchain_apr", apr, src, TIER, when), src_name, name,
                        f"apr={apr:.4%} (epoch {when.date()})", TIER)


def held_on_the_new_read(stored: list, cutover) -> set:
    """R10.A1a: the days a supply leg counts as HELD for the backfill — [(date, source)] -> {'YYYY-MM-DD'}. A row the
    LEGACY reader wrote on or after the migration cutover (its source is not this module's) is not the leg's value on
    the merged basis: that day is read again from the new contracts and the legacy row is replaced (the declared cut of
    the handover, made in the store). Every other stored day is held and never written again."""
    cut = str(pd.Timestamp(cutover).date())
    return {str(d)[:10] for d, src in stored if str(d)[:10] < cut or str(src).startswith(SOURCE)}


def supply_history(project: dict, have: dict, deadline: float | None = None, reader=None, write=None,
                   state: dict | None = None, clock=time.monotonic) -> tuple[list, str]:
    """R8 item 2: AERODROME'S SUPPLY SERIES ON PAST DAYS (the series stopped on 2026-10-29 — every epoch-scan timeout
    dropped the day's supply rows with it). Each missing day's SUPPLY_LEGS are read exactly as the live read makes
    them, through archive eth_calls at that day's FIRST block (found by timestamp, AeroV3._block_at): the supply terms
    at one block, then the filing wallets' locked AERO at the same block. Newest day first, back to the migration
    cutover; a leg the store already holds for a day is never written again (`have`: {metric: {'YYYY-MM-DD'}}), and a
    day holding every leg costs no call. Stops at `deadline` (monotonic) or at the first day whose supply cannot be
    read (an endpoint without archive state) and says so; a day whose filing leg alone fails gets its other legs and
    is named. Rows carry the live source plus the `archive` marker. `write(rows)` is called per day as it lands."""
    name = project["name"]
    st = state if state is not None else config.aerodrome_migration_state()
    rdr = reader or AeroV3(state=st)
    a = {k: v.lower() for k, v in (st.get("addresses") or {}).items()}
    if st.get("chain_tokens"):
        return [], ("not backfilled: aero_migration.json names AERO on other chains (chain_tokens), whose past supply "
                    "this hook does not read — total_supply of a past day would be partial")
    legs = [m for m in SUPPLY_LEGS if m in config.METRICS]
    cut = pd.Timestamp(config.AERODROME_MIGRATION["cutover_utc"])
    rows, why, unread = [], f"reached the migration cutover ({cut.date()})", []
    day = pd.Timestamp(today()).normalize() - pd.Timedelta(days=1)
    while day >= cut:
        if deadline is not None and clock() > deadline:
            why = "time budget spent; re-run to continue"
            break
        key = str(day.date())
        need = [m for m in legs if key not in (have.get(m) or ())]
        if need:
            try:
                blk = rdr._block_at(int(day.tz_localize("UTC").timestamp()))
                got = []
                if set(need) - {"filing_managed_lock_tokens"}:
                    t = rdr._supply_terms(name, a, blk)
                    got += [f for f, _msg in rdr._supply_frames(name, t, day, mark="archive") if f is not None]
            except Exception as e:  # noqa: BLE001
                why = f"stopped at {key}: the supply is not readable there ({str(e)[:200]})"
                break
            if "filing_managed_lock_tokens" in need and key in (
                    config.AERODROME_MIGRATION.get("filing_unreadable_days") or {}):
                unread.append(f"{key} (DECLARED n/a: {config.AERODROME_MIGRATION['filing_unreadable_days'][key][:90]})")
            elif "filing_managed_lock_tokens" in need:
                try:
                    total, _msg = rdr._filing_read(name, blk, st)
                    got.append(point(name, "filing_managed_lock_tokens", total,
                                     config.mark_source(f"{SOURCE}:filing_locks", "archive"), TIER, day))
                except Exception as e:  # noqa: BLE001 — unknown, never 0: the day keeps no filing row
                    unread.append(f"{key} ({str(e)[:160]})")
            new = [f.iloc[0] for f in got if f["metric"].iloc[0] in need]
            if new:
                rows += new
                if write is not None:
                    write(new)
        day -= pd.Timedelta(days=1)
    if unread:
        why += (f"; filing_managed_lock_tokens NOT read on {len(unread)} day(s), left unwritten: "
                + "; ".join(unread[:3]) + (" …" if len(unread) > 3 else ""))
    return rows, why
