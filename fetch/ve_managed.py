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
            if p.get("foundation_buyback"):
                self._foundation(p, p["foundation_buyback"], out)
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


def foundation_rows(name: str, spec: dict, day, got: dict, archive: bool = False) -> tuple[list, str]:
    """[(frame)] for one day's Foundation lock read (check_offline_items.aerodrome_foundation_locks), and the log line."""
    import config                                          # noqa: PLC0415
    src = f"{SOURCE}:foundation_locks"
    src = config.mark_source(src, "archive") if archive else src
    frames = [tidy([(day, got["locked"])], name, spec["stock_metric"], src, TIER),
              tidy([(day, got["claimable"])], name, spec["claimable_metric"], src, TIER)]
    msg = (f"{spec['stock_metric']} = {got['locked']:,.0f} AERO locked by the Foundation's buyback wallets ("
           + "; ".join(f"{spec['owners'][o]} {v:,.0f}" for o, v in got["by_owner"].items())
           + f"; {got['nfts']} veNFT(s), {got['owned_managed']} owned managed veNFT(s) not counted); rebase claimable "
             f"{got['claimable']:,.0f}")
    return frames, msg


def _foundation_reader(owners, block="latest"):
    import check_offline_items as coi                      # noqa: PLC0415 — one read, shared with the probe and seed
    return coi.aerodrome_foundation_locks(list(owners), block=block)


def _foundation(self, p, spec, out):
    """THE FOUNDATION'S BUY-AND-LOCK, FROM STATE (external audit 2026-10-09, item 1): once a day, the AERO locked by
    the buyback wallets and their rebase claimable; the buyback is their daily change (build_workbook.
    _foundation_buyback_views)."""
    name, metric = p["name"], spec["stock_metric"]
    day = str(today().date())
    key = f"ve_managed:{name}:{metric}"
    if not self.daily.due(key, day):
        out.mark_current(SOURCE, name, metric, f"{metric}: read today already", TIER)
        return
    try:
        got = (getattr(self, "foundation_reader", None) or _foundation_reader)(spec["owners"])
    except Exception as e:  # noqa: BLE001
        from .chain import redact_urls
        out.fail(SOURCE, name, f"{metric}: {redact_urls(e)}", TIER)
        return
    if got.get("error"):
        out.fail(SOURCE, name, f"{metric}: {got['error']}", TIER)
        return
    frames, msg = foundation_rows(name, spec, today(), got)
    for f in frames:
        out.add(f, SOURCE, name, msg, TIER)
    self.daily.done(key, day)


VeManaged._foundation = _foundation


def seed_foundation(projects: list[dict], days: int = 100, stored=None, say=print, reader=None) -> list:
    """`token_metrics.py --seed aero_buyback`: the Foundation lock stock at each UTC day start over the last `days`
    days, at that day's first block (archive eth_call through Multicall3), only for days with no row. Frames out."""
    import check_offline_items as coi                      # noqa: PLC0415
    import pandas as pd                                    # noqa: PLC0415
    reader = reader or _foundation_reader
    frames = []
    for p in projects:
        spec = p.get("foundation_buyback")
        if not spec:
            continue
        name = p["name"]
        end = today()
        for k in range(days, 0, -1):
            d = (end - pd.Timedelta(days=k)).normalize()
            if stored and stored(name, spec["stock_metric"], str(d.date())):
                continue
            b = coi.rpc_block_at("base", int(d.tz_localize("UTC").timestamp()) if d.tzinfo is None
                                 else int(d.timestamp()), say=say)
            if b is None:
                say(f"  {d.date()}: no block for the day start — skipped")
                continue
            got = reader(spec["owners"], block=hex(b))
            if got.get("error"):
                say(f"  {d.date()}: {got['error']} — skipped")
                continue
            fs, msg = foundation_rows(name, spec, d, got, archive=True)
            frames += fs
            say(f"  {d.date()} (block {b:,}): {msg}")
    return frames
