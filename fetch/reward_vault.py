"""
fetch/reward_vault.py — tier 2: Chainlink staking v0.2 reward EMISSION RATES, read from the
RewardVault. Added 2026-09-29.

WHY. The reward-vault outflow scan measures CLAIMS, which are lumpy: 30d 107,916 LINK read
~3.1%/yr, Q0 607,353 read ~5.8%, against Chainlink's published 4.32% (community) and 4.5% base
(operator). Claims are not accrual. The vault itself holds the rate: getRewardBuckets() returns
three buckets (operatorBase, communityBase, operatorDelegated), each

    struct RewardBucket { uint80 emissionRate;          // Juels per second, pool aggregate
                          uint80 rewardDurationEndsAt;   // unix seconds
                          uint80 vestedRewardPerToken; }

Source: RewardVault.sol ('RewardVault 1.0.0') in code-423n4/2023-08-chainlink, src/rewards,
read 2026-09-29. The deployed vault's typeAndVersion() is read first and anything that is not a
RewardVault is refused — a newer vault with a different layout must not be decoded as this one.

Stored:
    reward_emission_rate_annual  (stock, LINK/yr) = sum of ACTIVE buckets' emissionRate x
                                  31,557,600 / 1e18 (a bucket whose rewardDurationEndsAt has
                                  passed emits nothing)
    reward_rate_ends_unix        (stock) = the earliest ACTIVE bucket's rewardDurationEndsAt (the
                                  latest bucket end once none is active). Every bucket ends
                                  2026-11-27 (Jake's probe, 2026-09-29). A LATER end, or a higher
                                  rate, than the last stored reading is a TOP-UP: flagged to the
                                  Review Queue (reward_vault_topup) and logged.
    emissions_tokens             (flow, dated the run day) = that annual rate / 365.25
The claim outflow stays a separate series, emissions_claimed_tokens (log_scans).
"""
from __future__ import annotations

import logging

from .base import LogEntry, point, today

log = logging.getLogger("token_metrics.fetch.reward_vault")

SOURCE = "rewardvault"
TIER = 2


def _selector(signature: str) -> str:
    """The 4-byte selector, computed from the signature — never hand-written."""
    from eth_utils import keccak
    return "0x" + keccak(text=signature).hex()[:8]


SEL_GET_REWARD_BUCKETS = _selector("getRewardBuckets()")
SEL_TYPE_AND_VERSION = _selector("typeAndVersion()")
BUCKETS = ("operatorBase", "communityBase", "operatorDelegated")
SECONDS_PER_YEAR = 31_557_600             # 365.25 days
JUELS = 10 ** 18


def decode_buckets(hexdata: str) -> dict:
    """{bucket: (emissionRate, rewardDurationEndsAt, vestedRewardPerToken)} from the 9 words."""
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    words = [int(h[i:i + 64], 16) for i in range(0, 64 * 9, 64)]
    if len(words) != 9:
        raise ValueError(f"getRewardBuckets returned {len(h) // 64} word(s), expected 9")
    return {b: tuple(words[3 * i: 3 * i + 3]) for i, b in enumerate(BUCKETS)}


def _utc(ts) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).strftime("%Y-%m-%d")


def decode_string(hexdata: str) -> str:
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    n = int(h[64:128], 16)
    return bytes.fromhex(h[128:128 + 2 * n]).decode("utf-8", "replace")


class RewardVaultRates:
    """Reads getRewardBuckets() for every project declaring a `reward_vault_rates` block."""

    def __init__(self, reader=None, prior_values: dict | None = None):
        if reader is None:
            from .chain import ChainReader
            reader = ChainReader()
        self.reader = reader
        self.prior_values = prior_values or {}

    def run(self, projects: list[dict], window_days, out):
        when = today()
        for p in projects:
            spec = p.get("reward_vault_rates")
            if not spec:
                continue
            name = p["name"]
            try:
                w3 = self.reader.web3(spec["chain"])
                addr = self.reader.checksum(spec["address"])
                tv = decode_string(w3.eth.call({"to": addr, "data": SEL_TYPE_AND_VERSION}).hex())
                if not tv.startswith("RewardVault"):
                    raise ValueError(f"typeAndVersion() is {tv!r}, not a RewardVault — the bucket "
                                     f"layout is not known for it")
                block = w3.eth.get_block("latest")
                raw = w3.eth.call({"to": addr, "data": SEL_GET_REWARD_BUCKETS}, block["number"])
                buckets = decode_buckets(raw.hex())
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"reward_emission_rate_annual: {e}", TIER)
                out.gap(name, "reward_emission_rate_annual",
                        reason=f"the RewardVault's getRewardBuckets() read failed: {e}",
                        tiers_attempted="2",
                        suggestion="Check the Ethereum RPC; a non-RewardVault typeAndVersion means "
                                   "the vault was replaced and its source must be read again.")
                continue
            now_ts = int(block["timestamp"])
            active = {b: v for b, v in buckets.items() if v[1] > now_ts}
            annual = sum(v[0] for v in active.values()) * SECONDS_PER_YEAR / JUELS
            detail = "; ".join(
                f"{b} {v[0] * SECONDS_PER_YEAR / JUELS:,.0f} LINK/yr"
                + ("" if b in active else " (ENDED)") for b, v in buckets.items())
            src = f"{SOURCE}:getRewardBuckets"
            out.add(point(name, "reward_emission_rate_annual", annual, src, TIER, when), SOURCE, name,
                    f"reward_emission_rate_annual={annual:,.2f} LINK/yr ({tv}, block "
                    f"{block['number']:,}): {detail}", TIER)
            out.add(point(name, "emissions_tokens", annual / 365.25, f"{src}:rate_x_time", TIER, when),
                    SOURCE, name, f"emissions_tokens={annual / 365.25:,.2f} LINK for the day "
                                  f"(annual rate / 365.25) — accrual, not claims", TIER)
            out.log.append(LogEntry(SOURCE, name, 0, "ok", f"RewardVault buckets: {detail}", TIER))
            ends = min(v[1] for v in active.values()) if active else max(v[1] for v in buckets.values())
            out.add(point(name, "reward_rate_ends_unix", float(ends), src, TIER, when), SOURCE, name,
                    f"reward_rate_ends_unix={ends} ({_utc(ends)})"
                    + ("" if active else " — EVERY BUCKET HAS ENDED; the rate is 0"), TIER)
            # A TOP-UP extends the end or raises the rate. Flagged, never silently absorbed.
            p_end = self.prior_values.get((name, "reward_rate_ends_unix"))
            p_rate = self.prior_values.get((name, "reward_emission_rate_annual"))
            grew = [w for w, now_, before in (("end", ends, p_end), ("rate", annual, p_rate))
                    if before is not None and now_ > before * (1 + 1e-9) + (86_400 if w == "end" else 0)]
            if grew:
                out.review_item(name, "reward_rate_ends_unix" if "end" in grew else "reward_emission_rate_annual",
                                "reward_vault_topup", "stored_flagged",
                                value=float(ends if "end" in grew else annual),
                                prior_value=float(p_end if "end" in grew else p_rate),
                                date=when, source=src, tier=TIER)
                out.log.append(LogEntry(SOURCE, name, 0, "ok",
                                        f"TOP-UP OBSERVED: {', '.join(grew)} grew — end {_utc(p_end) if p_end else '?'} "
                                        f"-> {_utc(ends)}, rate {p_rate or 0:,.0f} -> {annual:,.0f} LINK/yr", TIER))


def read_at(w3, address: str, block: int) -> tuple[dict, int]:
    """(buckets, block timestamp) at `block`; refuses anything that is not a RewardVault there."""
    tv = decode_string(w3.eth.call({"to": address, "data": SEL_TYPE_AND_VERSION}, block).hex())
    if not tv.startswith("RewardVault"):
        raise ValueError(f"typeAndVersion() at block {block:,} is {tv!r}, not a RewardVault")
    raw = w3.eth.call({"to": address, "data": SEL_GET_REWARD_BUCKETS}, block)
    return decode_buckets(raw.hex()), int(w3.eth.get_block(block)["timestamp"])


def history_rows(project: dict, w3, day_blocks, days: int, have: set, head: int,
                 deadline: float | None = None) -> tuple[list, str]:
    """Past days' emissions_tokens and reward_emission_rate_annual from getRewardBuckets at each
    day's FIRST block (Jake, 2026-09-29) — the rate x time series the live read writes one day of,
    back through the Ethereum archive, the same way archive_backfill pins every state read.

    Newest first; a day already stored is skipped (never an overwrite). Stops at the first day
    the vault is not readable (not deployed, or not a RewardVault then) and says so."""
    import time
    import config
    import pandas as pd
    spec = project["reward_vault_rates"]
    name, addr = project["name"], w3.to_checksum_address(spec["address"])
    src = config.mark_source(f"{SOURCE}:getRewardBuckets", "archive")
    rows, why = [], "reached the start of the window"
    for i in range(1, days + 1):
        if deadline is not None and time.monotonic() > deadline:
            why = "time budget spent; re-run to continue"
            break
        day = today() - pd.Timedelta(days=i)
        if str(day.date()) in have:
            continue
        try:
            block = day_blocks.at(day, head)
            buckets, ts = read_at(w3, addr, block)
        except Exception as e:  # noqa: BLE001
            why = f"stopped at {day.date()}: the vault is not readable there ({str(e)[:160]})"
            break
        annual = sum(v[0] for v in buckets.values() if v[1] > ts) * SECONDS_PER_YEAR / JUELS
        rows.append(point(name, "reward_emission_rate_annual", annual, src, TIER, day).iloc[0])
        rows.append(point(name, "emissions_tokens", annual / 365.25,
                          config.mark_source(f"{SOURCE}:getRewardBuckets:rate_x_time", "archive"),
                          TIER, day).iloc[0])
    return rows, why
