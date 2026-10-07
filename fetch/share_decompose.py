"""
fetch/share_decompose.py — WHY a share vault's assets-per-share moved, transaction by transaction (Jake, 2026-10-06:
Ether.fi's realised share-price yield 10.01% vs the top-up-based 4.18%).

A vault's assets A and shares S change only by Transfer events: the asset token into / out of the vault, and the
share token minted (from address(0)) / burned (to address(0)). With the full history of both (the sETHFI scan
already holds it, reconciled to the wei), A and S are known EXACTLY after every transaction: A = sum(in) - sum(out),
S = sum(minted) - sum(burned). So the change in A/S across each transaction is exact, and its class is the
transaction's own shape:

    topup_identified     asset in, no shares minted or burned, every sender named in sender_labels
    topup_unclassified   the same, from a sender not named — ETHFI arrived without minting, source unknown
    burn_no_outflow      shares burned and NO asset out — an unstake fee / penalty left with the holders
    withdrawal_fee       shares burned with SOME asset out, A/S rose: the exit paid less than A/S per share
    deposit_fee          shares minted against assets in, A/S moved (rounding, or a deposit fee)
    asset_outflow        asset out with no shares burned — A/S fell (a fee taken out, a rescue)
    mixed                anything else (mint and burn in one tx, ...)
    bridge               (Jake's probes16) a Veda BoringVault has no exit penalty: shares burned with NO asset out
                         are shares BRIDGED to another chain (LayerZeroTellerWithRateLimiting.bridge), shares minted
                         with NO asset in are shares bridged BACK — cross-chain share moves, the ETHFI stays put
    strategy             (Jake's probes16) vault assets out to / back from a declared strategy position manager
    oft                  (Jake's probes16) vault assets out to / back from a declared OFT lockbox (token bridging)

The last three are excluded from the reward: from the daily reward tokens and from the REWARD-ONLY assets-per-share
walk (aps_reward_eod), which ADDS every other class's change in A/S and holds still across those three. (probes15's
"withdrawal_queue" / "circular" classes were a misreading of the same flows, retracted 2026-10-07 20:57.)
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

ZERO = "0x0000000000000000000000000000000000000000"
CLASSES = ("topup_identified", "topup_unclassified", "burn_no_outflow", "withdrawal_fee", "deposit_fee",
           "asset_outflow", "mixed", "bridge", "strategy", "oft")
NOT_REWARD = ("bridge", "strategy", "oft")


def _addr(topic: str) -> str:
    return "0x" + str(topic)[-40:].lower()


def _amt(e: dict) -> int:
    d = str(e.get("data") or "0x")
    return int(d[2:66] or "0", 16) if len(d) > 2 else 0


def decompose(ins: list, outs: list, mints: list, burns: list, since_ts: int, labels: set,
              asset_decimals: int = 18, share_decimals: int = 18, bridge_shapes: bool = False,
              strategy=frozenset(), oft=frozenset()) -> dict:
    """{aps_start, aps_end, by_class {cls: {"aps": d, "tokens": t, "txs": n}}, daily {day: tokens}, txs_in_window}.
    ins/outs: asset Transfer logs into / out of the vault; mints/burns: share Transfer logs from / to address(0).
    Every list must be the FULL history (A and S are rebuilt from zero)."""
    strategy, oft = {q.lower() for q in strategy}, {c.lower() for c in oft}
    tx: dict = defaultdict(lambda: {"in": 0, "out": 0, "mint": 0, "burn": 0, "senders": set(), "burners": set(),
                                    "payees": set(), "block": 0, "idx": 0, "ts": 0})
    for kind, evs in (("in", ins), ("out", outs), ("mint", mints), ("burn", burns)):
        for e in evs:
            h = str(e["transactionHash"]).lower()
            t = tx[h]
            t[kind] += _amt(e)
            li = e.get("logIndex") or 0
            li = int(li, 16) if isinstance(li, str) and li.startswith("0x") else int(li)
            blk = int(e.get("blockNumber") or 0)
            if t["block"] == 0 or (blk, li) < (t["block"], t["idx"]):
                t["block"], t["idx"] = blk, li
            t["ts"] = max(t["ts"], int(e.get("timeStamp") or 0))
            if kind == "in":
                t["senders"].add(_addr(e["topics"][1]))
            elif kind == "burn":
                t["burners"].add(_addr(e["topics"][1]))
            elif kind == "out" and len(e.get("topics") or ()) > 2:
                t["payees"].add(_addr(e["topics"][2]))
    sa, ss = 10 ** asset_decimals, 10 ** share_decimals
    a = s = 0
    aps_start = None
    by = {c: {"aps": 0.0, "tokens": 0.0, "txs": 0} for c in CLASSES}
    daily: dict = defaultdict(float)
    aps_eod: dict = {}                       # assets-per-share at each day's last transaction (the window only)
    aps_reward_eod: dict = {}                # the same walk with the NOT_REWARD classes held still
    aps_r = None
    n = 0
    for h, t in sorted(tx.items(), key=lambda kv: (kv[1]["block"], kv[1]["idx"])):
        a0, s0 = a, s
        a += t["in"] - t["out"]
        s += t["mint"] - t["burn"]
        if t["ts"] < since_ts:
            continue
        if aps_start is None:
            aps_start = (a0 / sa) / (s0 / ss) if s0 > 0 else None
        if s0 <= 0 or s <= 0:
            continue
        before, after = (a0 / sa) / (s0 / ss), (a / sa) / (s / ss)
        if aps_r is None:
            aps_r = before
        d = after - before
        moves = t["senders"] | t["payees"]
        if t["mint"] == 0 and t["burn"] == 0 and moves & strategy:
            cls = "strategy"
        elif t["mint"] == 0 and t["burn"] == 0 and moves & oft:
            cls = "oft"
        elif bridge_shapes and ((t["burn"] > 0 and t["out"] == 0 and t["mint"] == 0 and t["in"] == 0)
                                or (t["mint"] > 0 and t["in"] == 0 and t["burn"] == 0 and t["out"] == 0)):
            cls = "bridge"
        elif t["mint"] == 0 and t["burn"] == 0 and t["in"] > 0 and t["out"] == 0:
            cls = "topup_identified" if t["senders"] and t["senders"] <= labels else "topup_unclassified"
        elif t["burn"] > 0 and t["mint"] == 0 and t["in"] == 0:
            cls = "burn_no_outflow" if t["out"] == 0 else "withdrawal_fee"
        elif t["mint"] > 0 and t["burn"] == 0 and t["out"] == 0:
            cls = "deposit_fee"
        elif t["out"] > 0 and t["mint"] == 0 and t["burn"] == 0 and t["in"] == 0:
            cls = "asset_outflow"
        else:
            cls = "mixed"
        tok = d * (s / ss)
        by[cls]["aps"] += d
        by[cls]["tokens"] += tok
        by[cls]["txs"] += 1
        day = pd.Timestamp(t["ts"], unit="s").normalize()
        if cls not in NOT_REWARD:
            daily[day] += tok
            aps_r += d                       # additive: each reward's tokens per share, never rescaled by a round trip
        else:
            daily.setdefault(day, 0.0)
        aps_eod[day] = after
        aps_reward_eod[day] = aps_r
        n += 1
    aps_end = (a / sa) / (s / ss) if s > 0 else None
    if aps_start is None:
        aps_start = aps_end
    return {"aps_start": aps_start, "aps_end": aps_end, "by_class": by, "daily": dict(daily), "aps_eod": aps_eod,
            "aps_reward_eod": aps_reward_eod, "aps_reward_end": aps_r if aps_r is not None else aps_end,
            "txs_in_window": n,
            "assets_now": a / sa, "shares_now": s / ss}
