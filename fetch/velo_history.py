"""
fetch/velo_history.py — tier 2: Velodrome's VELO supply and locks on Optimism on PAST days (R11.2c, Jake 2026-11-18).

Aerodrome and Velodrome merged on 2026-10-22 00:00 UTC: each VELO converts to 0.044 new AERO (VelodromeMigration.sol
_RATIO_PIPS = 44_000; the allocation, 117,000,000 AERO, is fixed in the new token's constructor). VELO's total supply at
the merger block was 2,648,026,538.38 -> x 0.044 = 116,513,167.69 AERO: existing supply converted, not new supply. So the
merged entity's supply BEFORE the merger is Aerodrome's AERO + Velodrome's VELO x 0.044, and Aerodrome's series before
10-22 is restated on that basis at read time (build_workbook._velo_merged_views) from the rows this module writes:

    velo_total_supply_tokens       VELO.totalSupply()
    velo_locked_tokens             VELO.balanceOf(VotingEscrow)      (like Aerodrome's locked_tokens: tokens the escrow holds)
    velo_ve_locked_supply_tokens   VotingEscrow.supply()
    velo_permanent_locked_tokens   VotingEscrow.permanentLockBalance()
    velo_ve_voting_power_tokens    VotingEscrow.totalSupply()        (veVELO voting power at that block)
    velo_tail_emission_rate_bps    Minter.tailEmissionRate()         (basis points of the supply minted to gauges a week)
    velo_minter_active_period      Minter.activePeriod()             (unix time of the latest flip)
    velo_minter_weekly_tokens      Minter.weekly()                   (below TAIL_START the tail rate applies)

all in VELO (the 0.044 is applied by the view, never stored). Each day is read at its FIRST block; Optimism has produced
one block every 2 s since Bedrock, so the block follows from the timestamp, and its timestamp is checked before any value
is used. Newest day first, back from the day before the cutover; a stored day is never written again; the pass stops at
the first day that cannot be read and says so (unknown, never 0).
"""
from __future__ import annotations

import time

import pandas as pd

from .base import point

SOURCE = "velo_history"
TIER = 2
BEDROCK_BLOCK, BEDROCK_TS, BLOCK_S = 105_235_063, 1_686_068_903, 2
READS = (("velo_total_supply_tokens", "token", "totalSupply()", False, "VELO.totalSupply", 1e18),
         ("velo_locked_tokens", "token", "balanceOf(address)", True, "VELO.balanceOf(VotingEscrow)", 1e18),
         ("velo_ve_locked_supply_tokens", "escrow", "supply()", False, "VotingEscrow.supply", 1e18),
         ("velo_permanent_locked_tokens", "escrow", "permanentLockBalance()", False, "VotingEscrow.permanentLockBalance",
          1e18),
         ("velo_ve_voting_power_tokens", "escrow", "totalSupply()", False, "VotingEscrow.totalSupply", 1e18),
         # R12: the Minter's own terms — the weekly gauge emission of the tail phase is totalSupply x tailEmissionRate
         # / 10,000 (Minter.updatePeriod; checked against the Mint event of the 2026-10-15 flip: 7,919,782.725 VELO =
         # 2,639,927,575.04 x 30 bps), and activePeriod says which flips have happened by this block
         ("velo_tail_emission_rate_bps", "minter", "tailEmissionRate()", False, "Minter.tailEmissionRate", 1.0),
         ("velo_minter_active_period", "minter", "activePeriod()", False, "Minter.activePeriod", 1.0),
         ("velo_minter_weekly_tokens", "minter", "weekly()", False, "Minter.weekly", 1e18))
# R12: the liquid VELO of Velodrome's issuer-linked wallets (spec["issuer_wallets"]: VELO.balanceOf each, summed — every
# wallet at the same block or no row)
ISSUER_LIQUID = "velo_issuer_liquid_tokens"
METRICS = tuple(r[0] for r in READS) + (ISSUER_LIQUID,)


def block_at(ts: int) -> int:
    """Optimism's FIRST block at or after unix time `ts` (Bedrock onward: one block per 2 s from block 105,235,063 at
    1,686,068,903 — an odd second, so a midnight's first block is stamped one second after it)."""
    if ts < BEDROCK_TS:
        raise ValueError("before Bedrock: the block does not follow from the timestamp")
    return BEDROCK_BLOCK + -(-(int(ts) - BEDROCK_TS) // BLOCK_S)


def _chain_reader():
    import check_offline_items as coi                      # noqa: PLC0415 — the probes' eth_call, shared

    def call(to, sig, arg, block):
        data = coi._selx(sig) + (arg.lower()[2:].rjust(64, "0") if arg else "")
        h, why = coi.eth_call(to, data, block=hex(block), chain="optimism")
        if h is None:
            raise RuntimeError(f"{to[:10]} {sig}: {why}")
        return int(h, 16)

    def block_ts(block):
        errs = []
        for url in coi._rpcs_for("optimism"):
            try:
                return int(coi.rpc(url, "eth_getBlockByNumber", [hex(block), False])["result"]["timestamp"], 16)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{coi._rpc_host(url)}: {str(e)[:80]}")
        raise RuntimeError("; ".join(errs))
    return call, block_ts


def _balance_of(call, token: str, wallet: str, blk: int) -> int:
    # `call` appends ONE address argument (spec['escrow'] for READS); a wallet's balance goes through the same shape
    return call(token, "balanceOf(address)", wallet, blk)


def history(project: str, spec: dict, have: dict, first_day=None, deadline: float | None = None, call=None,
            block_ts=None, write=None, clock=time.monotonic) -> tuple[list, str]:
    """The VELO reads (READS) on each day from the day before `spec['until']` back to `first_day` (default: spec['from']),
    newest first. `have`: {metric: {'YYYY-MM-DD'}} — a day holding every one costs no call; a day's reads are
    made at one block and written together. Returns (rows, why it ended)."""
    import config                                          # noqa: PLC0415
    if call is None or block_ts is None:
        c, b = _chain_reader()
        call, block_ts = call or c, block_ts or b
    until = pd.Timestamp(spec["until"]).normalize()
    lo = pd.Timestamp(first_day or spec["from"]).normalize()
    rows, why = [], f"reached {lo.date()}"
    day = until - pd.Timedelta(days=1)
    while day >= lo:
        if deadline is not None and clock() > deadline:
            why = "time budget spent; re-run to continue"
            break
        key = str(day.date())
        if not all(key in (have.get(m) or ()) for m in METRICS):
            ts = int(day.tz_localize("UTC").timestamp())
            try:
                blk = block_at(ts)
                got = block_ts(blk)
                if not ts <= got < ts + BLOCK_S:
                    why = (f"stopped at {key}: block {blk:,} is stamped {got}, not the day's first 2 s ({ts}) — the "
                           f"block rule does not hold there")
                    break
                vals = [(m, call(spec[where], sig, spec["escrow"] if arg else None, blk) / scale, label)
                        for m, where, sig, arg, label, scale in READS]
                wallets = sorted(spec.get("issuer_wallets") or ())
                if wallets:
                    held = sum(_balance_of(call, spec["token"], w, blk) for w in wallets) / 1e18
                    vals.append((ISSUER_LIQUID, held, f"VELO.balanceOf({len(wallets)} issuer-linked wallets)"))
            except Exception as e:  # noqa: BLE001 — unknown, never 0: the day keeps no row
                from .chain import redact_urls             # noqa: PLC0415
                why = f"stopped at {key}: VELO is not readable there ({str(redact_urls(e))[:200]})"
                break
            new = [point(project, m, float(v), config.mark_source(f"{SOURCE}:{label}", "archive"), TIER, day).iloc[0]
                   for m, v, label in vals if key not in (have.get(m) or ())]
            rows += new
            if write is not None and new:
                write(new)
        day -= pd.Timedelta(days=1)
    return rows, why
