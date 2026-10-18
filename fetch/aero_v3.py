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
    emission_rate_current      Minter.emissionRate() x 604,800 / 1e18 (per-second rate, 0 before ACTIVATION_TIMESTAMP)
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
NO REBASE: sAERO has none (no RewardsDistributor in V3/src; Minter.mint pays only the recipient and SPLITTER) — the
legacy veAERO rebase keeps its own legacy read (emissions_tokens), which is not replaced.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

import config

from .base import Http, derive_flow_from_cumulative, point, today

log = logging.getLogger("token_metrics.fetch.aero_v3")

SOURCE = "aero_v3"
TIER = 2
REPO = "dromos-labs/metadex-public@0058bd9b1d49b0fb0a6d130dd51886b2c64e3d99"
WEEK = 7 * 86400
WAD = 10 ** 18

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
}
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
    return _keccak(CALLS[name][0])[:10]


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

    def call(self, to: str, name: str, *args) -> tuple:
        data = selector(name) + "".join(f"{int(a, 16) if isinstance(a, str) else a:064x}" for a in args)
        return decode_words(self.rpc("eth_call", [{"to": to, "data": data}, "latest"]), CALLS[name][1])

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
        fac = st.get("voting_rewards_factory")
        try:
            empty = [k for k, v in [*a.items(), *((("voting_rewards_factory", fac),) if fac else ())]
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
                SOURCE, name, f"emissionRate {rate} wei/s", TIER)
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
