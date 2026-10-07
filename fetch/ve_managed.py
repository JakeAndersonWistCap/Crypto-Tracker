"""
fetch/ve_managed.py — tier 2: AERO that named wallets have DEPOSITED into managed veNFTs (Jake's probes16, 2026-10-07).

Aerodrome's filing wallets own ~50 veNFTs whose locked() reads 0: their AERO was moved into MANAGED veNFTs with
VotingEscrow.depositManaged, which zeroes locked[tokenId] and records weights[tokenId][mTokenId] — the amount each
normal NFT put in (aerodrome-finance/contracts@1ba30815 VotingEscrow.sol). Per owner:

    n = balanceOf(owner);  tokenId = ownerToNFTokenIdList(owner, i);  mTokenId = idToManaged(tokenId)
    deposited += weights(tokenId, mTokenId)            (for every tokenId with mTokenId != 0)

summed over the owners and stored as `metric` (AERO, the escrow's underlying decimals). Jake's probe read Team
175,788,092 / Public Goods 174,772,260 / Flight School 66,991,134 / Velodrome Foundation airdrop 26,725,510. These are
team / foundation holdings — out of circulating on our convention even though locked. Once a day (DailyChecks).
"""
from __future__ import annotations

import logging

from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.ve_managed")

SOURCE = "ve_managed"
TIER = 2
_ABI = [{"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}],
         "outputs": [{"type": "uint256"}]},
        {"name": "ownerToNFTokenIdList", "type": "function", "stateMutability": "view",
         "inputs": [{"type": "address"}, {"type": "uint256"}], "outputs": [{"type": "uint256"}]},
        {"name": "idToManaged", "type": "function", "stateMutability": "view", "inputs": [{"type": "uint256"}],
         "outputs": [{"type": "uint256"}]},
        {"name": "weights", "type": "function", "stateMutability": "view",
         "inputs": [{"type": "uint256"}, {"type": "uint256"}], "outputs": [{"type": "uint256"}]}]


def deposits(ve, owner: str, block) -> tuple[float, int, int]:
    """(raw AERO deposited into managed NFTs, NFTs owned, of which in a managed NFT) for one owner at `block`."""
    n = int(ve.functions.balanceOf(owner).call(block_identifier=block))
    tot = held = 0
    for i in range(n):
        tid = int(ve.functions.ownerToNFTokenIdList(owner, i).call(block_identifier=block))
        mid = int(ve.functions.idToManaged(tid).call(block_identifier=block))
        if mid:
            tot += int(ve.functions.weights(tid, mid).call(block_identifier=block))
            held += 1
    return tot, n, held


class VeManaged:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, reader=None, daily=None, **_ignored):
        if reader is None:
            from .chain import ChainReader
            reader = ChainReader()
        self.reader = reader
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("ve_managed_holdings")
            if not spec:
                continue
            name, metric, chain = p["name"], spec["metric"], spec.get("chain", "base")
            day = str(today().date())
            key = f"ve_managed:{name}:{metric}"
            if not self.daily.due(key, day):
                out.mark_current(SOURCE, name, metric, f"{metric}: read today already", TIER)
                continue
            try:
                w3 = self.reader.web3(chain)
                ve = w3.eth.contract(address=self.reader.checksum(spec["escrow"]), abi=_ABI)
                head = int(self.reader.block_number(chain))
                scale = 10 ** int(spec.get("decimals", 18))
                total, parts = 0, []
                for owner, label in spec["owners"].items():
                    raw, n, held = deposits(ve, self.reader.checksum(owner), head)
                    total += raw
                    parts.append(f"{label} {raw / scale:,.0f} ({held} of {n} veNFT(s) in a managed NFT)")
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                from .chain import redact_urls
                out.fail(SOURCE, name, f"{metric}: {redact_urls(e)}", TIER)      # hosts only, never a key
                continue
            out.add(tidy([(today(), total / scale)], name, metric, f"{SOURCE}:{spec['escrow'][:10]}.weights", TIER),
                    SOURCE, name, f"{metric} = {total / scale:,.0f} at block {head:,}: " + "; ".join(parts), TIER)
            self.daily.done(key, day)
