"""
fetch/share_price.py — tier 2: a share vault's OWN share price, read on-chain now and at a block ~Q0 back.

Jake's run 2026-10-06 17:20: Ether.fi's realised staking yield needs an independent on-chain reference. The
credibility row read lock_assets_per_share, a ratio of two SEPARATELY stored series whose share leg was once a
Dune figure larger than the vault's supply, so its Q0 growth (incl. a "101.7%/yr" week) is not a clean
reference. This reads ONE state per block:

    convertToAssets(10**18) on the vault                       (ERC-4626)
    else  asset.balanceOf(vault) / vault.totalSupply()         (the same quantity for a fee-free vault)

at today's head and at a block `days_back` ago (an archive endpoint, fetch/archive.resolve_archive), and
stores each as `metric` dated by its block's day. Both points must come from the SAME method, or neither is
stored. The annualised change between them is sETHFI's realised yield over Q0 (credibility share_price_growth).
Once a day (DailyChecks).
"""
from __future__ import annotations

import logging

import pandas as pd

from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.share_price")

SOURCE = "share_vault"
TIER = 2
_ABI = [{"name": "convertToAssets", "type": "function", "stateMutability": "view",
         "inputs": [{"type": "uint256"}], "outputs": [{"type": "uint256"}]},
        {"name": "totalSupply", "type": "function", "stateMutability": "view", "inputs": [],
         "outputs": [{"type": "uint256"}]},
        {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [],
         "outputs": [{"type": "uint8"}]},
        {"name": "balanceOf", "type": "function", "stateMutability": "view",
         "inputs": [{"type": "address"}], "outputs": [{"type": "uint256"}]}]


class SharePrice:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, reader=None, daily=None, archive=None, **_ignored):
        if reader is None:
            from .chain import ChainReader
            reader = ChainReader()
        self.reader = reader
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self._archive = archive            # tests: (w3, block) for the past point

    @staticmethod
    def price(w3, vault: str, asset: str, block) -> tuple[float, str]:
        """(assets per share, method) at `block`."""
        v = w3.eth.contract(address=vault, abi=_ABI)
        a = w3.eth.contract(address=asset, abi=_ABI)
        dv, da = int(v.functions.decimals().call()), int(a.functions.decimals().call())
        try:
            got = int(v.functions.convertToAssets(10 ** dv).call(block_identifier=block))
            return got / 10 ** da, "convertToAssets"
        except Exception:  # noqa: BLE001 — not an ERC-4626 vault: the same quantity from two reads at one block
            assets = int(a.functions.balanceOf(vault).call(block_identifier=block)) / 10 ** da
            shares = int(v.functions.totalSupply().call(block_identifier=block)) / 10 ** dv
            if shares <= 0:
                raise ValueError(f"totalSupply is 0 at block {block}")
            return assets / shares, "balanceOf/totalSupply"

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("share_price_onchain")
            if not spec:
                continue
            name, metric, chain = p["name"], spec["metric"], spec.get("chain", "ethereum")
            day = str(today().date())
            key = f"share_price:{name}:{metric}"
            if not self.daily.due(key, day):
                out.mark_current(SOURCE, name, metric, f"{metric}: read today already", TIER)
                continue
            try:
                w3 = self.reader.web3(chain)
                vault, asset = self.reader.checksum(spec["vault"]), self.reader.checksum(spec["asset"])
                head = int(self.reader.block_number(chain))
                now_px, how = self.price(w3, vault, asset, head)
                if self._archive is not None:
                    aw, blk = self._archive
                else:
                    from .archive import block_days_back, resolve_archive
                    aw, host = resolve_archive(chain, days=float(spec["days_back"]) + 1)
                    if aw is None:
                        out.fail(SOURCE, name, f"{metric}: no endpoint serves {chain} state {spec['days_back']} "
                                               f"day(s) back — today's point only", TIER)
                        aw = None
                    blk = block_days_back(aw, float(spec["days_back"])) if aw is not None else None
                rows = [(today(), now_px)]
                msg = f"{metric} = {now_px:.6f} ({how}) at block {head:,}"
                if aw is not None:
                    then_px, how_then = self.price(aw, vault, asset, blk)
                    if how_then != how:
                        out.fail(SOURCE, name, f"{metric}: the two blocks answered by different methods ({how} now, "
                                               f"{how_then} at {blk:,}) — NOTHING STORED", TIER)
                        continue
                    ts = int(aw.eth.get_block(blk)["timestamp"])
                    d0 = pd.Timestamp(ts, unit="s").normalize()
                    rows.insert(0, (d0, then_px))
                    span = max((today() - d0).days, 1)
                    msg += (f"; {then_px:.6f} at block {blk:,} ({d0.date()}) — {now_px / then_px - 1:+.3%} over "
                            f"{span} day(s), {(now_px / then_px) ** (365.0 / span) - 1:.2%}/yr realised")
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                from .chain import redact_urls
                out.fail(SOURCE, name, f"{metric}: {redact_urls(e)}", TIER)      # hosts only, never a key
                continue
            out.add(tidy(rows, name, metric, f"{SOURCE}:{spec['vault'][:10]}.{how}", TIER), SOURCE, name, msg, TIER)
            self.daily.done(key, day)
