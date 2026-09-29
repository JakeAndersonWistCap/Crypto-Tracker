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


def decode_string(hexdata: str) -> str:
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    n = int(h[64:128], 16)
    return bytes.fromhex(h[128:128 + 2 * n]).decode("utf-8", "replace")


class RewardVaultRates:
    """Reads getRewardBuckets() for every project declaring a `reward_vault_rates` block."""

    def __init__(self, reader=None):
        if reader is None:
            from .chain import ChainReader
            reader = ChainReader()
        self.reader = reader

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
