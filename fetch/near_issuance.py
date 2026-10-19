"""
fetch/near_issuance.py — tier 1: NEAR'S GROSS ISSUANCE, MEASURED AT EACH EPOCH'S MINT (Round G6 item 3, Jake 2026-10-19).

NEAR mints once an epoch, in the epoch's FIRST block. Within the block the supply also falls by the gas burnt, so

    minted = total_supply(first block) - total_supply(its parent) + balance_burnt of the chunks INCLUDED in it

(block header total_supply; chunk header balance_burnt where height_included == the block). Read 2026-10-19 on
rpc.mainnet.near.org: block 220,636,334 (09:00:25 UTC) minted 26,721.4286 NEAR — 2.499%/yr of 1,309,349,144 over the
25,771.5 s epoch; treasury.near received 2,675.0369 of it (10.01%, the documented 90/10 split).

Epochs are walked back from the current one: validators(null).epoch_start_height, then for each epoch its first block,
its parent, and the parent's epoch (validators by epoch_id — the archival node refuses it by block). Stored per UTC
day as gross_issuance_tokens, source near_rpc:epoch_mint[N epochs]: only COMPLETE days (every epoch start that day
read), never today. Walks back to the last stored day (or `days` on the first pass), on the paced archival RPC
(fetch/archive.NearArchive), so a pass the budget stops keeps every complete day it reached.

THE RATE CHANGE IS A DECLARED STEP, NOT A BREAK (Jake): max_inflation_rate [1,20] -> [1,40] at block 170,351,519
(2025-10-29 11:29:03.97 UTC, protocol 80 -> 81; archival-rpc.mainnet.near.org EXPERIMENTAL_protocol_config at 170,351,518
and 170,351,519, read 2026-10-19). The measure is the same either side of it; config.issuance_rate_declared records it.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.near_issuance")

SOURCE = "near_issuance"
TIER = 1
YOCTO = 10 ** 24
FIRST_PASS_DAYS = 7
BACKFILL_DAYS = 365


def epoch_mint(block: dict, parent: dict) -> float:
    """NEAR minted in an epoch's first block: the supply's rise over its parent plus the gas burnt in it."""
    h = int(block["header"]["height"])
    burnt = sum(int(c.get("balance_burnt") or 0) for c in block.get("chunks") or ()
                if int(c.get("height_included", -1)) == h)
    return (int(block["header"]["total_supply"]) - int(parent["header"]["total_supply"]) + burnt) / YOCTO


def _day(block: dict) -> pd.Timestamp:
    return pd.Timestamp(int(block["header"]["timestamp_nanosec"]), unit="ns").normalize()


def walk(rpc, floor: pd.Timestamp, deadline: float | None = None, clock=time.monotonic, start: int | None = None):
    """Epochs newest first from `start` (default: the current epoch) back to `floor`. Returns ([(day, minted, start
    height)] of COMPLETE days only — every epoch start on them read —, the epoch start height to continue from, and
    whether `floor` was reached). At the floor, the continuation is the first epoch start before it (the latest one on
    the day before, so that day is read whole). Stopped by the deadline, the oldest day reached is incomplete: its
    epochs are dropped and the continuation is that day's latest epoch start."""
    start = int(rpc("validators", [None])["epoch_start_height"]) if start is None else int(start)
    out = []
    while True:
        if deadline is not None and clock() > deadline:
            if not out:
                return [], start, False
            last_day = out[-1][0]
            resume = max(e[2] for e in out if e[0] == last_day)
            return [e for e in out if e[0] > last_day], resume, False
        blk = rpc("block", {"block_id": start})
        d = _day(blk)
        if d < floor:
            return out, start, True
        parent = rpc("block", {"block_id": blk["header"]["prev_hash"]})
        out.append((d, epoch_mint(blk, parent), start))
        start = int(rpc("validators", {"epoch_id": parent["header"]["epoch_id"]})["epoch_start_height"])


class NearIssuance:
    """Two passes a run: the TOP-UP from the current epoch back to the last stored day, then — with the budget left —
    the BACKFILL from where the last backfill stopped towards BACKFILL_DAYS ago. State in <cache>/near-issuance.json."""

    def __init__(self, archive=None, deadline_s: float = 100.0, root=None, **_ignored):
        self.archive = archive
        self.deadline_s = deadline_s
        from .archive import cache_root
        self.file = (root or cache_root()) / "near-issuance.json"

    def _state(self) -> dict:
        import json
        try:
            return json.loads(self.file.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, st: dict) -> None:
        import json
        from .logcache import atomic_write_text
        self.file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.file, json.dumps(st, sort_keys=True))

    def run(self, projects: list[dict], window_days, out):
        if not any(p["name"] == "Near" for p in projects):
            return
        if self.archive is None:
            from .archive import NearArchive
            self.archive = NearArchive()
        st, now = self._state(), today()
        deadline = time.monotonic() + self.deadline_s
        target = now - pd.Timedelta(days=BACKFILL_DAYS)
        epochs = []
        try:
            # 1. THE TOP-UP: the last stored day is read again (a later epoch may have completed it). A top-up the budget
            #    stops keeps its complete days but not `through`, so the next run reads the gap again.
            first = not st.get("through")
            through = now - pd.Timedelta(days=FIRST_PASS_DAYS) if first else pd.Timestamp(st["through"])
            top, cont, reached = walk(self.archive.rpc, through, deadline)
            epochs += top
            if top and (reached or first):
                st["through"] = str(max(e[0] for e in top).date())
            if first and (top or reached):
                st["back_next"] = cont          # the backfill continues below where the first pass stopped
            # 2. THE BACKFILL, with what is left, towards BACKFILL_DAYS ago
            if st.get("back_next") and not st.get("back_done") and time.monotonic() < deadline:
                back, cont, reached = walk(self.archive.rpc, target, deadline, start=st["back_next"])
                epochs += back
                st["back_next"], st["back_done"] = (None, True) if reached else (cont, False)
        except Exception as e:  # noqa: BLE001
            from .chain import redact_urls
            out.fail(SOURCE, "Near", f"gross_issuance_tokens: the epoch walk failed — {redact_urls(e)[:200]}", TIER)
        finally:
            for fn in (self.archive.save, lambda: self._save(st)):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
        frame = pd.DataFrame([e[:2] for e in epochs], columns=["date", "v"])
        frame = frame[frame["date"] < now]
        if frame.empty:
            out.mark_current(SOURCE, "Near", "gross_issuance_tokens", "gross_issuance_tokens: no new complete day", TIER)
            return
        per_day = frame.groupby("date")["v"].agg(["sum", "count"])
        pts = list(zip(per_day.index, per_day["sum"]))
        f = tidy(pts, "Near", "gross_issuance_tokens", f"{SOURCE}:near_rpc:epoch_mint", TIER)
        f["source"] = [f"{SOURCE}:near_rpc:epoch_mint[{int(n)} epochs]" for n in per_day["count"]]
        out.add(f, SOURCE, "Near", f"gross_issuance_tokens: {len(pts)} complete day(s) "
                                   f"{per_day.index.min().date()}..{per_day.index.max().date()} from "
                                   f"{int(per_day['count'].sum())} epoch mints (total supply rise at each epoch's first "
                                   f"block + its burnt gas); last day {pts[-1][1]:,.2f} NEAR; backfill "
                                   f"{'done' if st.get('back_done') else 'resumes at block ' + str(st.get('back_next'))}",
                TIER)
