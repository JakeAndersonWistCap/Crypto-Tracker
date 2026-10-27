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
MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"
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
    return _keccak((CALLS.get(name) or FILING_CALLS[name])[0])[:10]


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


class AeroV3:
    """Daily state reads + the last complete voter epoch, once the switch file is complete."""

    def __init__(self, prior_values: dict | None = None, prior_dates: dict | None = None, rpc=None, prices=None,
                 state: dict | None = None, now: int | None = None):
        self.http = Http(min_interval=0.2)
        self.prior = prior_values or {}
        self.prior_dates = prior_dates or {}
        self._rpc = rpc
        self._prices = prices
        self.state = state
        self.now = now

    # ---------------------------------------------------------------- transport
    def rpc(self, method: str, params: list):
        if self._rpc is not None:
            return self._rpc(method, params)
        errs = []
        # THE KEYED ENDPOINT FIRST (Round G 2.10): BASE_RPC_URL, then the public list — fetch.chain.rpc_endpoints, as
        # every other Base read (only the host is ever logged)
        from .chain import rpc_endpoints  # noqa: PLC0415
        for url in rpc_endpoints("base"):
            try:
                r = self.http.post(url, json_body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                if "result" in r:
                    return r["result"]
                errs.append(f"{url.split('/')[2]}: {r.get('error')}")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{url.split('/')[2]}: {str(e)[:100]}")
        raise RuntimeError("; ".join(errs))

    @staticmethod
    def calldata(name: str, *args) -> str:
        return selector(name) + "".join(f"{int(a, 16) if isinstance(a, str) else a:064x}" for a in args)

    def call(self, to: str, name: str, *args, block="latest") -> tuple:
        tag = hex(block) if isinstance(block, int) else block
        return decode_words(self.rpc("eth_call", [{"to": to, "data": self.calldata(name, *args)}, tag]),
                            CALLS[name][1])

    def multi(self, calls: list, block: int, chunk: int = 150) -> list:
        """[(to, name, args)] -> [raw hex return], through Multicall3.aggregate3 at ONE block with allowFailure false:
        any call that reverts reverts the whole batch, so a part is never silently missing (P2b-1)."""
        from eth_abi import decode, encode                # noqa: PLC0415
        out = []
        for i in range(0, len(calls), chunk):
            part = calls[i:i + chunk]
            data = _keccak("aggregate3((address,bool,bytes)[])")[:10] + encode(["(address,bool,bytes)[]"], [[
                (to, False, bytes.fromhex(self.calldata(name, *args)[2:])) for to, name, args in part]]).hex()
            res = self.rpc("eth_call", [{"to": MULTICALL3, "data": data}, hex(block)])
            if not res or res == "0x":
                raise RuntimeError(f"Multicall3 answered nothing at block {block:,}")
            (got,) = decode(["(bool,bytes)[]"], bytes.fromhex(res[2:]))
            if len(got) != len(part) or not all(ok for ok, _ in got):
                raise RuntimeError(f"Multicall3 returned {len(got)} result(s) for {len(part)} call(s), or a failure")
            out += ["0x" + ret.hex() for _ok, ret in got]
        return out

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

    # ---------------------------------------------------------------- run
    def run(self, projects: list[dict], window_days, out):
        from .validate import _migration_switch_missing  # noqa: PLC0415
        m = config.AERODROME_MIGRATION
        st = config.aerodrome_migration_state() if self.state is None else self.state
        day = pd.Timestamp(today())
        if day < pd.Timestamp(m["cutover_utc"]) or st.get("launched") is not True:
            return
        missing = _migration_switch_missing(st, day)
        if missing:
            out.unconfigured(SOURCE, m["project"], f"aero_migration.json incomplete: {'; '.join(missing)}", TIER)
            return
        name, a = m["project"], {k: v.lower() for k, v in st["addresses"].items()}
        # EVERY ADDRESS MUST HOLD CODE ON BASE before anything is read from it (Round G 2.10): a well-formed address
        # that is not the deployed contract would answer eth_call with nothing or with another contract's figures
        extra = [(k, st[k]) for k in ("voting_rewards_factory", "relay_factory_registry") if st.get(k)]
        try:
            empty = [k for k, v in [*a.items(), *extra]
                     if str(self.rpc("eth_getCode", [v, "latest"]) or "0x") in ("0x", "0x0")]
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"aero_migration.json addresses: eth_getCode could not be read — {e}", TIER)
            return
        if empty:
            out.fail(SOURCE, name, f"aero_migration.json: no contract code on Base at {', '.join(empty)} — nothing "
                                   f"read; check the addresses against Aero's publication", TIER)
            return
        try:
            self._state_reads(name, a, out)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"state reads: {e}", TIER)
        try:
            self._supply(name, a, out, st)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"supply across the migration: {e} — total_supply / noncirculating_holding_tokens "
                                   f"NOT written (legacy rows of the day are refused, so the day reads n/a)", TIER)
        if st.get("voting_rewards_factory") and st.get("deploy_block"):
            try:
                self._epoch(name, a, st, out)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"voter epoch: {e}", TIER)
        else:
            out.gap(name, "voter_rewards_onchain_usd", tiers_attempted="2",
                    reason="aero_migration.json has no voting_rewards_factory / deploy_block: the VotingRewardsManagers "
                           "(one per gauge) are found only from VotingRewardsFactory.VotingRewardsCreated logs",
                    suggestion="add both from Aero's published deployment (RUNBOOK 11bx)")

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
        voter = self.call(ve, "VOTER")[0]
        bias, slope, ts, perm = self.call(voter, "totalPoint")
        now = self.now or int(time.time())
        w = walk_point(bias, slope, ts, perm, now, lambda t: self.call(voter, "totalSlopeChanges", t)[0]) / WAD
        out.add(point(name, "voter_total_weight_tokens", w, f"{SOURCE}:voter.totalPoint[resolved]", TIER, when),
                SOURCE, name, f"voter_total_weight_tokens={w:,.4f}", TIER)
        self.voter, self.weight = voter, w

    def _supply(self, name: str, a: dict, out, st: dict | None = None) -> None:
        """G2-D5: total_supply and noncirculating_holding_tokens with each AERO counted once (supply_once). The
        migration contract is read from the new token (MIGRATION()) and must name the legacy token, the legacy escrow
        and the file's token, hold code, and match an `addresses.migration` in the file when one is given."""
        # ONE BLOCK FOR EVERY TERM (G2-D5e, Jake 2026-10-18): the reserve is new AERO's balanceOf(MIGRATION()) read
        # on-chain at this block, and every other term is read at the same block, so the sum is one instant's supply
        blk = int(self.rpc("eth_blockNumber", []), 16)
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
        when = today()
        src = f"{SOURCE}:supply_once[ratio {ratio:g}]"
        st = st or {}
        ct = {k.lower(): v for k, v in (st.get("chain_tokens") or {}).items()}
        if ct:
            extra, read, missing = multichain_total(ct, st.get("bridge_escrows") or [], self._chain_supply,
                                                    self._chain_balance)
            r["total"] += extra
            r["circulating"] += extra
            src += f"+chains[{','.join(read)}]" + (":PARTIAL" if missing else "")
            out.log.append(LogEntry(SOURCE, name, 0, "ok",
                                    f"total_supply: the unified AERO on {len(read) + 1} chain(s) (base + "
                                    f"{', '.join(read) or 'none'}) adds {extra:,.4f} net of bridge escrows"
                                    + (f"; NOT READ — {'; '.join(missing)} (the total is PARTIAL)" if missing else ""),
                                    TIER))
        msg = (f"at Base block {blk:,}: reserve = new AERO balanceOf(migration {mig}) = {reserve:,.4f}, read on-chain at "
               f"that block (remaining() agrees); new {new_total:,.4f} − reserve + min({ratio:g} × (old {old_total:,.4f} "
               f"− held {held_old:,.4f}), reserve) = {r['total']:,.4f}; ratio {ratio_src}; circulating "
               f"{r['circulating']:,.4f} (Velodrome allocation held {velo_held:,.4f}, {len(wallets)} wallets new "
               f"{new_nc:,.4f} / old {old_nc:,.4f})")
        out.add(point(name, "total_supply", r["total"], src, TIER, when), SOURCE, name, f"total_supply: {msg}", TIER)
        out.add(point(name, "noncirculating_holding_tokens", r["noncirculating"], src, TIER, when), SOURCE, name,
                f"noncirculating_holding_tokens = total − circulating = {r['noncirculating']:,.4f}", TIER)
        # P2b-1: the filing wallets' LOCKED AERO at the SAME block (circulating subtracts it as filing_managed_lock_tokens)
        try:
            self._filing_locks(name, blk, out, st)
        except Exception as e:  # noqa: BLE001 — never a partial or zero figure: the day reads n/a
            out.fail(SOURCE, name, f"filing_managed_lock_tokens at Base block {blk:,}: {str(e)[:200]} — NOT written "
                                   f"(circulating reads n/a that day; unknown, never 0)", TIER)

    def _filing_locks(self, name: str, blk: int, out, st: dict) -> None:
        """P2b-1 (2026-10-27): THE AERO THE BLOCKWORKS FILING WALLETS HOLD LOCKED, across the migration, at Base block
        `blk` (the block _supply reads every other term at). Traced at block 53,174,900: the wallets withdrew their
        managed veAERO (weight + locked managed reward, legacy VotingEscrow.withdrawManaged L182-204), migrated each
        veNFT to themselves as a decaying sAERO (AerodromeMigration._settleStake -> createStake), then moved those
        stakes into Aero ProtocolRelays ('Autopilot Internal 001-007') with VotingEscrow.rebalanceUnderlying; the Relay
        owns the sAERO and the wallet holds its principal token (PT). So, summed over the filing wallets
        (config ve_managed_holdings owners):
            legacy  weights(tokenId, idToManaged) on the OLD escrow — managed deposits not yet withdrawn (old AERO,
                    counted inside supply_once's old term)
            direct  sAERO.staked(id).amount for every sAERO the wallet owns (ERC721Enumerable)
            relay   PT.balanceOf(wallet) x totalBacking / PT.totalSupply for every Relay of every factory the
                    RelayFactoryRegistry lists (relay_claim)
            pending live Relay deposit-queue entries naming the wallet (weight moved, PT not yet minted)
        Every read goes through Multicall3 at `blk` with allowFailure false; any failure writes nothing."""
        spec = config.PROJECT_BY_NAME[name].get("ve_managed_holdings") or {}
        labels = {o.lower(): v for o, v in (spec.get("owners") or {}).items()}
        owners = list(labels)
        reg = str((st or {}).get("relay_factory_registry") or "").lower()
        ve = str(((st or {}).get("addresses") or {}).get("voting_escrow") or "").lower()
        if not owners or not reg or not ve:
            raise RuntimeError("no filing owners (ve_managed_holdings), no relay_factory_registry or no voting_escrow "
                               "in aero_migration.json — the Relay principal cannot be enumerated")
        legacy_ve = str(spec.get("escrow") or LEGACY_ESCROW).lower()
        W = _words_of
        # round 1: NFT counts on both escrows, the Relay factories
        r1 = self.multi([(ve, "balanceOf", (o,)) for o in owners] + [(legacy_ve, "balanceOf", (o,)) for o in owners]
                        + [(reg, "relayFactories", ())], blk)
        n_new = [W(x)[0] for x in r1[:len(owners)]]
        n_old = [W(x)[0] for x in r1[len(owners):2 * len(owners)]]
        factories = _address_array(r1[-1])
        if not factories:
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
        r5 = self.multi([(pt, "totalSupply", ()) for pt in pts] + [(pt, "balanceOf", (o,)) for pt in pts for o in owners]
                        + [(r, "pendingDeposits", (i,)) for r, i in pend_ids], blk)
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
        for (r, _i), x in zip(pend_ids, r5[len(pts) + len(pts) * len(owners):]):
            rec, at, _tid, amt = decode_words(x, FILING_CALLS["pendingDeposits"][1])
            if at and rec.lower() in by:
                by[rec.lower()][3] += amt
        parts = [sum(v[i] for v in by.values()) / WAD for i in range(4)]
        total = sum(parts)
        when = today()
        out.add(point(name, "filing_managed_lock_tokens", total, f"{SOURCE}:filing_locks", TIER, when), SOURCE, name,
                f"filing_managed_lock_tokens at Base block {blk:,} = {total:,.4f}: legacy managed veAERO "
                f"{parts[0]:,.4f} ({len(managed)} veNFT(s)) + sAERO stakes {parts[1]:,.4f} ({len(new_ids)} sAERO) + "
                f"Relay principal {parts[2]:,.4f} ({len(relays)} Relay(s) of {len(factories)} factory(ies)) + pending "
                f"Relay deposits {parts[3]:,.4f}; by wallet: "
                + "; ".join(f"{labels[o]} {sum(v) / WAD:,.2f}" for o, v in by.items() if sum(v))
                + (f"; Relays: {'; '.join(held_in)}" if held_in else ""), TIER)

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

    def _block_at(self, ts: int) -> int:
        hi = int(self.rpc("eth_blockNumber", []), 16)
        lo = max(0, hi - 40 * 86400)                      # Base: 2 s blocks, 40 days back is enough for an epoch
        while lo < hi:
            mid = (lo + hi) // 2
            if int(self.rpc("eth_getBlockByNumber", [hex(mid), False])["timestamp"], 16) >= ts:
                hi = mid
            else:
                lo = mid + 1
        return lo

    def _logs(self, address, topics, lo: int, hi: int, chunk: int = 5000) -> list:
        got, b = [], lo
        while b <= hi:
            e = min(hi, b + chunk - 1)
            got += self.rpc("eth_getLogs", [{"address": address, "topics": topics, "fromBlock": hex(b),
                                              "toBlock": hex(e)}])
            b = e + 1
        return got

    def _epoch(self, name: str, a: dict, st: dict, out) -> None:
        now = self.now or int(time.time())
        end = now // WEEK * WEEK
        start = end - WEEK
        if start < int(pd.Timestamp(config.AERODROME_MIGRATION["cutover_utc"]).timestamp()):
            return                                        # no complete v3 epoch yet
        b0, b1 = self._block_at(start), self._block_at(end) - 1
        mgrs = ["0x" + lg["topics"][2][-40:] for lg in
                self._logs(st["voting_rewards_factory"], [topic0("VotingRewardsCreated")], int(st["deploy_block"]), b1)]
        fees = self._logs(mgrs, [topic0("NotifyFeesAmount")], b0, b1) if mgrs else []
        progs = []
        for mg in mgrs:
            for pid in range(1, self.call(mg, "incentiveCount")[0] + 1):
                progs.append(self.call(mg, "incentive", pid))
        amounts = epoch_rewards(fees, progs, start, end)
        px = self.prices(sorted(set(amounts) | {a["token"]}), end)
        fee_usd = bribe_usd = 0.0
        unpriced = 0
        for tok, (f, i) in amounts.items():
            if tok not in px:
                unpriced += 1
                continue
            p, dec = px[tok]
            fee_usd += f / 10 ** dec * p
            bribe_usd += i / 10 ** dec * p
        when = pd.Timestamp(start, unit="s").normalize()
        src = f"{SOURCE}:epoch[{len(mgrs)} managers]"
        for metric, v in (("voter_rewards_onchain_fees_usd", fee_usd), ("voter_rewards_onchain_bribes_usd", bribe_usd),
                          ("voter_rewards_onchain_usd", fee_usd + bribe_usd),
                          ("voter_rewards_unpriced_count", float(unpriced))):
            out.add(point(name, metric, v, src, TIER, when), SOURCE, name, f"{metric}={v:,.2f}", TIER)
        aero_px = px.get(a["token"], (None, 18))[0]
        w = getattr(self, "weight", None)
        if aero_px and w:
            apr = (fee_usd + bribe_usd) * 52 / (w * aero_px)
            out.add(point(name, "voter_rewards_onchain_apr", apr, src, TIER, when), SOURCE, name, f"apr={apr:.4%}",
                    TIER)
