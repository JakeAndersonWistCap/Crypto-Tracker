"""
fetch/pendle_latent.py — tier 2: PENDLE ACCRUED BUT NOT YET MINTED, READ LIVE (Round G6 item 3, Jake 2026-10-19).

PENDLE mints lazily: the weekly emissions accrue in the token's own state and are minted only when the liquidity
incentives recipient calls claimLiquidityEmissions() (PENDLE.sol: for each week after lastWeekEmissionSent, ×0.989 a week
to week 259, then totalSupply × terminalInflationRateNumerator / 1e12 — 2% a year — from week 260). This reads the exact
amount a claim would mint NOW: eth_call of claimLiquidityEmissions() FROM liquidityIncentivesRecipient() (any other
caller reverts NOT_INCENTIVES_RECIPIENT) — a simulation, nothing is sent. Read 2026-10-19 (block 26,226,484, week 286):
9,837,598.561055001042566566 PENDLE = weeks 205-259 6,865,217.4860 + weeks 260-286 2,972,381.0750, the same figure
config.LATENT_SUPPLY recorded on 2026-10-13 (week 286 began that day). Stored as latent_supply_tokens, one row a day.
"""
from __future__ import annotations

import logging

from .base import point

log = logging.getLogger("token_metrics.fetch.pendle_latent")

SOURCE = "pendle_latent"
TIER = 2
PENDLE = "0x808507121B80c02388fAd14726482e061B8da827"


def _sel(sig: str) -> str:
    from eth_utils import keccak
    return "0x" + keccak(text=sig).hex()[:8]


class PendleLatent:
    def __init__(self, reader=None):
        self.reader = reader

    def run(self, projects: list[dict], window_days, out):
        if not any(p["name"] == "Pendle" for p in projects):
            return
        if self.reader is None:
            from .chain import ChainReader
            self.reader = ChainReader()
        try:
            w3 = self.reader.web3("ethereum")
            rec_raw = w3.eth.call({"to": PENDLE, "data": _sel("liquidityIncentivesRecipient()")})
            recipient = "0x" + bytes(rec_raw)[-20:].hex()
            raw = w3.eth.call({"to": PENDLE, "from": w3.to_checksum_address(recipient),
                               "data": _sel("claimLiquidityEmissions()")})
            block = int(w3.eth.block_number)
        except Exception as e:  # noqa: BLE001
            from .chain import redact_urls
            out.fail(SOURCE, "Pendle", f"latent_supply_tokens: the claim simulation did not answer — {redact_urls(e)}",
                     TIER)
            return
        tokens = int.from_bytes(bytes(raw)[-32:], "big") / 1e18
        out.add(point("Pendle", "latent_supply_tokens", tokens, f"{SOURCE}:claimLiquidityEmissions()@{block}", TIER),
                SOURCE, "Pendle", f"latent_supply_tokens: {tokens:,.6f} PENDLE accrued, not minted (eth_call of "
                                  f"claimLiquidityEmissions() from the incentives recipient {recipient}, block "
                                  f"{block:,}; nothing sent)", TIER)
