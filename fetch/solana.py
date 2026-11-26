"""
fetch/solana.py — one Solana JSON-RPC read: an SPL token account's balance. Added 2026-09-28.

WHY IT EXISTS. GEODNET's burns moved to Solana: its burn token account
5SBfxBdqsCM1SJZGQkf9Y74EFmUfzs8LGDjBZUjZGnED (mint 7JA5...mKHu, owned by the incinerator)
grew ~50-55K GEOD/day while the Polygon dead address sat still, so a Polygon-only burn read
reported burns moving chain as a quiet period. fetch/chain.py sums this balance with the
Polygon one into burn_address_balance.

ONE CALL PER ACCOUNT PER RUN: getTokenAccountBalance, no key. The raw integer amount is divided
by 10^decimals exactly (Decimal), so a 9-decimal balance is not rounded before it is stored.

429 IS RETRIED BRIEFLY, THEN THE NEXT ENDPOINT. The public mainnet-beta endpoint rate-limits
hard (Jake's probe: 5 older transactions unreadable on 429). A short exponential retry (2s, 4s),
a Retry-After longer than SOLANA_MAX_RETRY_WAIT_S is not waited, and then the next endpoint in
config.solana_rpc_endpoints() is tried. Every endpoint failing raises, with each one's error.
"""
from __future__ import annotations

import urllib.parse
from decimal import Decimal

import config
from .base import Http

SOLANA_MAX_RETRY_WAIT_S = 10.0


class SolanaRPC:
    def __init__(self, endpoints: list[str] | None = None, http: Http | None = None):
        self.endpoints = endpoints or config.solana_rpc_endpoints()
        self.http = http or Http(retries=2, max_retry_wait=SOLANA_MAX_RETRY_WAIT_S)

    def token_account_balance(self, address: str) -> tuple[float, int, int, str]:
        """(value, raw amount, decimals, host that answered) for an SPL token account."""
        return self._amount("getTokenAccountBalance", address)

    def token_supply(self, mint: str) -> tuple[float, int, int, str]:
        """(value, raw amount, decimals, host) for an SPL MINT's total supply — getTokenSupply,
        same response shape. Added 2026-09-29 for World Mobile's Solana WMTX leg."""
        return self._amount("getTokenSupply", mint)

    def call(self, method: str, params: list):
        """Any JSON-RPC method, first endpoint that answers; the result, or RuntimeError."""
        errors = []
        for url in self.endpoints:
            host = urllib.parse.urlsplit(url).netloc
            try:
                j = self.http.post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            except Exception as e:  # noqa: BLE001 — try the next endpoint
                errors.append(f"{host}: {e}")
                continue
            if not isinstance(j, dict) or "error" in j or "result" not in j:
                errors.append(f"{host}: {(j or {}).get('error') if isinstance(j, dict) else j}")
                continue
            return j["result"]
        raise RuntimeError(f"no Solana RPC answered {method} — " + "; ".join(errors))

    def _amount(self, method: str, address: str) -> tuple[float, int, int, str]:
        errors = []
        for url in self.endpoints:
            host = urllib.parse.urlsplit(url).netloc
            try:
                j = self.http.post(url, {"jsonrpc": "2.0", "id": 1, "method": method,
                                         "params": [address, {"commitment": "finalized"}]})
            except Exception as e:  # noqa: BLE001 — try the next endpoint
                errors.append(f"{host}: {e}")
                continue
            if not isinstance(j, dict) or "error" in j or "result" not in j:
                errors.append(f"{host}: {(j or {}).get('error') if isinstance(j, dict) else j}")
                continue
            v = (j["result"] or {}).get("value") or {}
            try:
                raw, dec = int(v["amount"]), int(v["decimals"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{host}: unexpected shape {str(v)[:120]}")
                continue
            return float(Decimal(raw) / (Decimal(10) ** dec)), raw, dec, host
        raise RuntimeError(f"no Solana RPC answered {method} — " + "; ".join(errors))


# ===================================================================================================================
# R24 SOLANA (SOL) — the chain's own reads, once a day, keyless. Methods are added to the SolanaRPC reader above
# (no second reader); SolanaChain is the tier that stores them. Every series is FORWARD-ONLY from its first read: the
# public RPC serves no past supply, rewards or stake, and nothing is back-filled or modelled.
#   total_supply_protocol          getSupply total (lamports / 1e9)
#   solana_noncirculating_tokens   getSupply nonCirculating — the issuer's own non-circulating set (Agave rule)
#   solana_staked_tokens           Σ activatedStake over getVoteAccounts current + delinquent
#   solana_noncirc_staked_tokens   Σ delegated stake held INSIDE the non-circulating accounts (already out of
#                                  circulating: taken off the staked figure once)
#   solana_inflation_rate          getInflationRate total (a fraction a year, the epoch in force)
#   gross_issuance_tokens          the EpochRewards sysvar's total_rewards / the epoch's own days (ACTUAL rewards
#                                  created by the chain for the last settled epoch, not rate x supply)
#   solana_burn_observed_tokens_per_day  the fall of getSupply total across a short window inside one epoch,
#                                  per day (a CROSS-CHECK of the DefiLlama-derived burn; MODELLED to a day)
# ===================================================================================================================
import base64
import logging
import struct
import time

from .base import point, today

log = logging.getLogger("token_metrics.fetch.solana")
LAMPORTS = 1_000_000_000
U64_MAX = 18446744073709551615
EPOCH_REWARDS_SYSVAR = "SysvarEpochRewards1111111111111111111111111"
SOURCE = "solana_rpc"
TIER = 1


def _supply(self) -> dict:
    s = self.call("getSupply", [{"excludeNonCirculatingAccountsList": False, "commitment": "finalized"}])
    v = s["value"]
    return {"slot": int(s["context"]["slot"]), "total": int(v["total"]), "circulating": int(v["circulating"]),
            "non_circulating": int(v["nonCirculating"]), "accounts": list(v.get("nonCirculatingAccounts") or [])}


def _inflation(self) -> dict:
    return {"rate": self.call("getInflationRate", []), "governor": self.call("getInflationGovernor", [])}


def _epoch_info(self) -> dict:
    return self.call("getEpochInfo", [{"commitment": "finalized"}])


def _vote_stake(self) -> tuple[int, int, int, int]:
    """(Σ activatedStake current, delinquent, n current, n delinquent) in lamports."""
    v = self.call("getVoteAccounts", [{"commitment": "finalized"}])
    return (sum(int(a["activatedStake"]) for a in v["current"]), sum(int(a["activatedStake"]) for a in v["delinquent"]),
            len(v["current"]), len(v["delinquent"]))


def _epoch_rewards(self) -> dict:
    """The EpochRewards sysvar: lamports the chain created as rewards for the last settled epoch (total_rewards) and
    how many were distributed. Layout: u64 distribution_starting_block_height, u64 num_partitions, 32-byte
    parent_blockhash, u128 total_points, u64 total_rewards, u64 distributed_rewards, bool active."""
    a = self.call("getAccountInfo", [EPOCH_REWARDS_SYSVAR, {"encoding": "base64", "commitment": "finalized"}])
    raw = base64.b64decode(a["value"]["data"][0])
    if len(raw) < 81:
        raise RuntimeError(f"EpochRewards sysvar has {len(raw)} bytes, expected 81")
    total, dist = struct.unpack_from("<QQ", raw, 64)
    return {"total_rewards": total, "distributed": dist, "active": bool(raw[80])}


def _block_time(self, slot: int, tries: int = 6):
    """Unix time of the first slot at or after `slot` that has a block (skipped slots have none)."""
    for k in range(tries):
        try:
            t = self.call("getBlockTime", [slot + k])
        except RuntimeError:
            continue
        if t:
            return int(t), slot + k
    return None, None


def _noncirc_stake(self, accounts: list, pace: float = 0.6) -> tuple[int, int, int]:
    """(Σ lamports in the accounts, Σ ACTIVELY delegated stake lamports among them, accounts not found).
    getMultipleAccounts 100 at a time, jsonParsed; a delegation with a deactivationEpoch is not counted."""
    bal = act = missing = 0
    for i in range(0, len(accounts), 100):
        out = self.call("getMultipleAccounts", [accounts[i:i + 100], {"encoding": "jsonParsed", "commitment": "finalized"}])
        for v in out["value"]:
            if v is None:
                missing += 1
                continue
            bal += int(v["lamports"])
            d = v.get("data")
            if isinstance(d, dict) and d.get("program") == "stake":
                dl = (((d.get("parsed") or {}).get("info") or {}).get("stake") or {}).get("delegation") or {}
                if dl and int(dl.get("deactivationEpoch", 0)) == U64_MAX:
                    act += int(dl["stake"])
        time.sleep(pace)
    return bal, act, missing


SolanaRPC.supply = _supply
SolanaRPC.inflation = _inflation
SolanaRPC.epoch_info = _epoch_info
SolanaRPC.vote_stake = _vote_stake
SolanaRPC.epoch_rewards = _epoch_rewards
SolanaRPC.block_time = _block_time
SolanaRPC.noncirc_stake = _noncirc_stake


class SolanaChain:
    """Tier 1: one run of Solana's chain reads per project declaring a `solana_chain` block (config)."""

    def __init__(self, rpc=None, window_s: float = 45.0):
        self.rpc, self.window_s = rpc or SolanaRPC(), window_s

    def run(self, projects: list, window_days, out):
        when = today()
        for p in projects:
            spec = p.get("solana_chain")
            if not spec:
                continue
            name = p["name"]
            r = self.rpc

            def add(metric, value, how, msg):
                out.add(point(name, metric, float(value), f"{SOURCE}:{how}", TIER, when), SOURCE, name, msg, TIER)

            def fail(metric, e):
                out.fail(SOURCE, name, f"{metric}: Solana RPC read failed — {e}", TIER)
                out.gap(name, metric, reason=f"Solana RPC read failed: {e}", tiers_attempted="1",
                        suggestion="the next run retries; the series is forward-only")
            try:
                s1 = r.supply()
            except Exception as e:  # noqa: BLE001
                for m in (spec["supply_metric"], spec["noncirc_metric"]):
                    fail(m, e)
                s1 = None
            if s1:
                add(spec["supply_metric"], s1["total"] / LAMPORTS, "getSupply.total",
                    f"{spec['supply_metric']}={s1['total'] / LAMPORTS:,.2f} SOL (slot {s1['slot']})")
                add(spec["noncirc_metric"], s1["non_circulating"] / LAMPORTS, "getSupply.nonCirculating",
                    f"{spec['noncirc_metric']}={s1['non_circulating'] / LAMPORTS:,.2f} SOL "
                    f"({len(s1['accounts'])} accounts; circulating {s1['circulating'] / LAMPORTS:,.2f})")
            stake_v = noncirc_act = None
            try:
                cur, dl, nc, nd = r.vote_stake()
                stake_v = (cur + dl) / LAMPORTS
                add(spec["stake_metric"], (cur + dl) / LAMPORTS, "getVoteAccounts.activatedStake",
                    f"{spec['stake_metric']}={(cur + dl) / LAMPORTS:,.2f} SOL = current {cur / LAMPORTS:,.2f} "
                    f"({nc} vote accounts) + delinquent {dl / LAMPORTS:,.2f} ({nd})")
            except Exception as e:  # noqa: BLE001
                fail(spec["stake_metric"], e)
            if s1:
                try:
                    bal, act, miss = r.noncirc_stake(s1["accounts"])
                    noncirc_act = act / LAMPORTS
                    add(spec["noncirc_stake_metric"], act / LAMPORTS, "getMultipleAccounts.delegation.stake",
                        f"{spec['noncirc_stake_metric']}={act / LAMPORTS:,.2f} SOL actively delegated inside the "
                        f"non-circulating accounts (their balance {bal / LAMPORTS:,.2f}; {miss} listed accounts empty)")
                except Exception as e:  # noqa: BLE001
                    fail(spec["noncirc_stake_metric"], e)
            try:
                infl = r.inflation()
                rate = float(infl["rate"]["total"])
                add(spec["rate_metric"], rate, "getInflationRate.total",
                    f"{spec['rate_metric']}={rate:.6f} a year, epoch {infl['rate']['epoch']} "
                    f"(governor {infl['governor']})")
            except Exception as e:  # noqa: BLE001
                fail(spec["rate_metric"], e)
            if stake_v is not None and noncirc_act is not None:
                sc = max(0.0, stake_v - noncirc_act)
                add(spec["stake_circ_metric"], sc, "derived:staked-noncirc_staked",
                    f"{spec['stake_circ_metric']}={sc:,.2f} SOL = staked {stake_v:,.2f} - staked inside the "
                    f"non-circulating accounts {noncirc_act:,.2f} (a token comes off once)")
            days = None
            try:
                ep = r.epoch_info()
                er = r.epoch_rewards()
                first = int(ep["absoluteSlot"]) - int(ep["slotIndex"])
                spe = int(ep["slotsInEpoch"])
                t_now, _ = r.block_time(first)                    # this epoch's first block
                t_prev, _ = r.block_time(first - spe)             # the previous epoch's first block
                if not t_now or not t_prev or t_now <= t_prev:
                    raise RuntimeError("epoch boundary block times unreadable")
                days = (t_now - t_prev) / 86400.0
                tr = er["total_rewards"] / LAMPORTS
                add(spec["issuance_metric"], tr / days, "EpochRewards.total_rewards/epoch_days",
                    f"{spec['issuance_metric']}={tr / days:,.2f} SOL/day = the rewards the chain created for epoch "
                    f"{int(ep['epoch']) - 1} ({tr:,.2f} SOL, {er['distributed'] / LAMPORTS:,.2f} distributed) over its "
                    f"{days:.4f} days (first-block times {t_prev}..{t_now}); an ACTUAL read, not rate x supply")
            except Exception as e:  # noqa: BLE001
                fail(spec["issuance_metric"], e)
            if s1 and days:
                try:
                    time.sleep(self.window_s)
                    s2 = r.supply()
                    e2 = r.epoch_info()
                    ds = s2["slot"] - s1["slot"]
                    fall = (s1["total"] - s2["total"]) / LAMPORTS
                    if ds <= 0 or int(e2["epoch"]) != int(ep["epoch"]) or fall < 0:
                        raise RuntimeError("window crossed an epoch boundary or supply rose — no observation")
                    secs = ds * days * 86400.0 / spe
                    add(spec["burn_obs_metric"], fall / secs * 86400.0, "getSupply.total[window]",
                        f"{spec['burn_obs_metric']}={fall / secs * 86400.0:,.1f} SOL/day (MODELLED to a day: "
                        f"{fall:.4f} SOL fell over {ds} slots = {secs:.1f} s inside one epoch); cross-check only")
                except Exception as e:  # noqa: BLE001
                    out.fail(SOURCE, name, f"{spec['burn_obs_metric']}: no observation — {e}", TIER)
